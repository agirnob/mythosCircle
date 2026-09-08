"""ComfyUI video-generation provider (spec-4.5) — the local-dev reveal
backend.

The OpenAI-compatible ``videos/generations`` shape (spec-4.2) targets a
server that does not exist on the dev workstation; the working local
backend is ComfyUI running the MiniMax H3 image-to-video workflow
(``video_minimax_h3_i2v_sage.json``), whose protocol is poll-based:
submit the workflow JSON to ``/prompt`` (with the reveal prompt injected
into the MiniMax node's ``inputs.prompt`` and the entity's portrait —
staged into ComfyUI's input directory — wired to the LoadImage node's
``inputs.image``), poll ``/history/{prompt_id}`` until a SaveVideo node
has output, then fetch the mp4 bytes from ``/view``.

The sibling provider mirrors the 4-4 image adapter's discipline — leaf
of the dependency graph, ``Callable[..., bytes]``, ``ProviderError``
family, injectable ``transport``, kwargs-only ``settings`` — and returns
mp4 bytes, so ``run_video``'s ``MP4_SIGNATURE`` guard stays the single
write boundary for both backends (spec-4.5 Always list). Backend-only:
the worker picks this provider when ``[video] backend = "comfyui"``;
the OpenAI path stays the default.

Leaf of the dependency graph, exactly like ``providers.comfyui`` /
``video``: depends on nothing else in ``app/`` except settings.
``ProviderError`` is the shared provider-failure family from
``app.providers.llm`` — the worker's error vocabulary treats every
provider alike.
"""

import contextlib
import copy
import json
import re
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.core.settings import ComfyUIVideoSettings
from app.providers.llm import ProviderError

#: A provider call: one prompt in (+ the portrait source frame), mp4
#: bytes out. ``...`` accepts the keyword-only ``transport`` kwarg
#: injected by tests.
ComfyUIVideoGeneration = Callable[..., bytes]

#: Relative paths appended under the endpoint base — httpx joins a
#: relative path under ``base_url`` (``http://host:7896`` +
#: ``prompt`` -> ``http://host:7896/prompt``). ComfyUI's API is
#: bare-host, no version mount.
_PROMPT_PATH = "prompt"
_VIEW_PATH = "view"

#: Seconds between ``/history`` polls — a hardcoded code default, NOT an
#: operator knob (spec-4.5 Always list). The ``timeout`` setting bounds
#: the ENTIRE call, never the cadence.
_POLL_INTERVAL = 1.0

#: The mp4 (ISO-BMFF) box magic a ``/view`` 200 must carry bytes 4-8 of:
#: ComfyUI's SaveVideo output is always mp4, so a non-ISO-BMFF body (an
#: HTML error page wrapped in a 200, a truncated body) is garbage and is
#: rejected PROVIDER-SIDE (spec-4.5 matrix row NON_MP4_BYTES) —
#: ``run_video``'s ``MP4_SIGNATURE`` write-boundary guard in
#: media/service.py stays the single authoritative check at the file
#: write (the PNG twin's contract, spec-4.4).
_MP4_SIGNATURE = b"ftyp"

#: The smallest plausible mp4: an ISO-BMFF file opens with a box whose
#: size+type occupy bytes 0-7, so anything shorter cannot carry ``ftyp``.
_MIN_MP4_LEN = 8

#: Acceptable ComfyUI prompt ids — hashes the server emits. A
#: hostile/broken server must not inject path segments into the
#: ``/history/{prompt_id}`` URL (the 4-4 review-round-1 contract).
_SAFE_PROMPT_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def comfyui_video_generation(
    prompt: str,
    *,
    settings: ComfyUIVideoSettings,
    first_frame: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Submit one generation to ComfyUI and return the mp4 bytes.

    Loads the operator's workflow JSON from ``settings.workflow_path``
    (reloaded from disk on EVERY call — a stale workflow must never be
    served), deep-copies it, injects ``prompt`` into the configured
    MiniMax node's ``inputs.prompt`` (NOT ``inputs.value`` — the i2v
    node's text widget), stages the entity's portrait (``first_frame``)
    into the configured ComfyUI input directory and wires its name to
    the LoadImage node's ``inputs.image``, POSTs to
    ``{endpoint}/prompt``, polls ``{endpoint}/history/{prompt_id}``
    every ``_POLL_INTERVAL`` seconds until a SaveVideo node has output —
    or ``settings.timeout`` elapses — then GETs the video bytes from
    ``{endpoint}/view``. The staged portrait copy is removed afterwards
    (no litter in ComfyUI's input dir).

    ``settings.timeout`` bounds the ENTIRE call (submit + poll + fetch):
    every request carries a per-request httpx timeout capped at the
    remaining deadline budget, and the poll loop / view fetch refuse to
    start once the deadline is exhausted.

    Error vocabulary (the 4-4 family): a missing/malformed workflow
    file, a workflow whose prompt node lacks a string ``inputs.prompt``
    or whose first-frame node lacks a string ``inputs.image`` (not the
    MiniMax shape), a missing/unreadable/unstageable first frame, an
    unset ``input_dir`` -> ``ProviderError("connection")``; bare
    ``httpx`` transport failures (DNS, refused) ->
    ``ProviderError("connection")``; any non-2xx ->
    ``ProviderError("http", status_code=...)``; a 2xx whose body is not
    the expected shape (malformed submit, hostile prompt_id, history
    with no SaveVideo output, incomplete/non-mp4 view bytes) is still a
    provider error; the deadline being consumed — by a hung request or
    by polling with no output — -> ``ProviderError("timeout")``.
    """
    # The workflow JSON is the operator's artifact (in-repo at
    # deploy/workflows/, spec-4.5). Missing or malformed is a
    # connection-class failure at first call, surfacing operator
    # misconfig (matrix rows WORKFLOW_PATH_MISSING /
    # INVALID_WORKFLOW_JSON).
    try:
        workflow = json.loads(Path(settings.workflow_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderError("connection") from exc
    node = workflow.get(settings.prompt_node_id) if isinstance(workflow, dict) else None
    node_inputs = node.get("inputs") if isinstance(node, dict) else None
    value = node_inputs.get("prompt") if isinstance(node_inputs, dict) else None
    if not isinstance(value, str):
        # The MiniMax prompt widget (``inputs.prompt``) is the ONLY text
        # field the provider must touch — a workflow that cannot carry
        # it (missing node, non-dict ``inputs``, non-string widget) is
        # not the MiniMax i2v shape and is unusable, not worth
        # submitting (matrix row INVALID_WORKFLOW_JSON, the 4-4 prompt-
        # node discipline moved from ``inputs.value`` to
        # ``inputs.prompt``; review round 1: guarded against a
        # present-but-non-dict ``inputs``).
        raise ProviderError("connection")
    frame_node = workflow.get(settings.first_frame_node_id) if isinstance(workflow, dict) else None
    frame_inputs = frame_node.get("inputs") if isinstance(frame_node, dict) else None
    frame_image = frame_inputs.get("image") if isinstance(frame_inputs, dict) else None
    if not isinstance(frame_image, str):
        # The LoadImage widget (``inputs.image``) is how the workflow
        # names its source frame — a workflow without it (missing node,
        # non-dict ``inputs``) cannot carry the staged portrait, so it
        # fails before submit (matrix row INVALID_WORKFLOW_JSON; review
        # round 1: guarded against a present-but-non-dict ``inputs``).
        raise ProviderError("connection")
    workflow = copy.deepcopy(workflow)
    workflow[settings.prompt_node_id]["inputs"]["prompt"] = prompt
    staged = _stage_first_frame(first_frame, settings)
    workflow[settings.first_frame_node_id]["inputs"]["image"] = staged.name
    try:
        try:
            client = httpx.Client(
                base_url=settings.endpoint,
                timeout=settings.timeout,
                transport=transport,
            )
        except (httpx.InvalidURL, ValueError) as exc:
            # An unparseable configured endpoint (missing scheme, garbage) is a
            # connection-class failure, never a raw exception escaping.
            raise ProviderError("connection") from exc
        headers = (
            {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key is not None else {}
        )
        # The timeout bounds the ENTIRE call (spec-4.5): the deadline gates
        # every request and the poll loop, so a hung request can never run
        # the call past settings.timeout. One ``with client`` for the whole
        # call — an httpx client cannot be re-opened once closed, and one
        # call must be one generation (submit + poll + fetch).
        deadline = time.monotonic() + settings.timeout
        with client:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("timeout")
            try:
                response = client.post(
                    _PROMPT_PATH,
                    json={"prompt": workflow},
                    headers=headers,
                    timeout=_request_timeout(settings.timeout, remaining),
                )
            except (httpx.RequestError, httpx.InvalidURL, ValueError) as exc:
                # Request-time URL join failures are connection-class
                # failures; a request that consumed the whole-call deadline
                # is the "timeout" kind, never a raw escape.
                if time.monotonic() >= deadline:
                    raise ProviderError("timeout") from exc
                raise ProviderError("connection") from exc
            if response.status_code != 200:
                raise ProviderError("http", status_code=response.status_code)
            try:
                prompt_id = response.json()["prompt_id"]
            except (KeyError, TypeError, ValueError) as exc:
                # A 200 with a non-JSON body (HTML error page, empty) or no
                # prompt_id is a malformed submit — the job must fail, never
                # poll a phantom prompt (matrix row MALFORMED_SUBMIT).
                raise ProviderError("http", status_code=response.status_code) from exc
            if not isinstance(prompt_id, str) or not _SAFE_PROMPT_ID.match(prompt_id):
                # A hostile/broken server must not inject path segments into
                # the history URL (the 4-4 review-round-1 contract).
                raise ProviderError("http", status_code=response.status_code)

            video: dict[str, str] | None = None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break  # poll exhaustion -> ProviderError("timeout") below
                try:
                    response = client.get(
                        f"history/{prompt_id}",
                        headers=headers,
                        timeout=_request_timeout(settings.timeout, remaining),
                    )
                except (httpx.RequestError, httpx.InvalidURL, ValueError) as exc:
                    if time.monotonic() >= deadline:
                        raise ProviderError("timeout") from exc
                    raise ProviderError("connection") from exc
                if response.status_code != 200:
                    raise ProviderError("http", status_code=response.status_code)
                try:
                    entry = response.json().get(prompt_id)
                except (AttributeError, ValueError) as exc:
                    # A 200 with a non-JSON body is a malformed history
                    # response, never a raw escape.
                    raise ProviderError("http", status_code=response.status_code) from exc
                if entry is None:
                    # No history entry yet — the prompt is still running; poll
                    # again after the cadence, clamped so the sleep never
                    # overshoots the whole-call deadline.
                    time.sleep(min(_POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
                    continue
                if not isinstance(entry, dict):
                    raise ProviderError("http", status_code=response.status_code)
                # The entry exists: the prompt FINISHED. A completed prompt
                # without a SaveVideo output is NO_VIDEO_OUTPUT — fail, never
                # poll forever (matrix row NO_VIDEO_OUTPUT).
                video = _save_video_output(entry.get("outputs"))
                if video is None:
                    raise ProviderError("http", status_code=response.status_code)
                break
            if video is None:
                # Poll exhaustion: every history poll came up empty before the
                # deadline — the "timeout" kind (matrix row POLL_TIMEOUT).
                raise ProviderError("timeout")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("timeout")
            try:
                response = client.get(
                    _VIEW_PATH,
                    params=video,
                    headers=headers,
                    timeout=_request_timeout(settings.timeout, remaining),
                )
            except (httpx.RequestError, httpx.InvalidURL, ValueError) as exc:
                if time.monotonic() >= deadline:
                    raise ProviderError("timeout") from exc
                raise ProviderError("connection") from exc
            if response.status_code != 200:
                raise ProviderError("http", status_code=response.status_code)
            data = response.content
            if len(data) < _MIN_MP4_LEN or data[4:8] != _MP4_SIGNATURE:
                # A /view 200 that is not a COMPLETE mp4 — an HTML error
                # page, a webm, a truncated body — is rejected provider-side,
                # before run_video ever sees it (matrix row NON_MP4_BYTES;
                # the write-boundary guard stays single).
                raise ProviderError("http", status_code=200)
            return data
    finally:
        _unlink_staged(staged)


def _request_timeout(timeout: float, remaining: float) -> float:
    """A per-request httpx timeout capped at the whole-call deadline.

    ``min(timeout, remaining)`` would let a request run the full
    settings timeout even with seconds left on the deadline — that is
    the 2-3x overshoot this guard exists to prevent. The floor keeps
    httpx from rejecting a zero/negative timeout; the deadline checks
    around each request are the real gate.
    """
    return min(timeout, max(0.01, remaining))


def _stage_first_frame(first_frame: str | None, settings: ComfyUIVideoSettings) -> Path:
    """Copy the entity's portrait into ComfyUI's input directory.

    LoadImage reads from ComfyUI's input dir, never the media dir
    (spec-4.5 Always list), so the provider stages a uniquely named
    copy in before submit; the caller removes it afterwards (no
    litter). Every failure — a missing source portrait, an unset
    ``input_dir``, an FS error on mkdir/copy — is the operator/state
    misconfig class and surfaces as ``ProviderError("connection")``
    (matrix row FIRST_FRAME_STAGE_FAIL).
    """
    if not first_frame:
        # Defensive twin of run_video's no-portrait gate: the runner
        # fails the job before calling without a source frame, but the
        # provider stands on its own contract too.
        raise ProviderError("connection")
    source = Path(first_frame)
    if not source.is_file():
        # The manifest row exists (that's what run_video resolved) but
        # the FILE is gone — a state/drive failure class, never a raw
        # FileNotFoundError escaping the provider's contract.
        raise ProviderError("connection")
    if not settings.input_dir.strip():
        # An unset ComfyUI input dir is operator misconfig, surfaced at
        # the call site instead of staging into the process CWD.
        raise ProviderError("connection")
    staged_dir = Path(settings.input_dir)
    staged = staged_dir / f"first_frame_{uuid.uuid4().hex}.png"
    try:
        staged_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, staged)
    except OSError as exc:
        # A failure mid-copy (disk full, source read error) can leave a
        # PARTIAL staged file behind — remove it so ComfyUI's input dir
        # never litters on the failure path (matrix row
        # FIRST_FRAME_STAGE_FAIL; review round 1).
        with contextlib.suppress(OSError):
            staged.unlink(missing_ok=True)
        raise ProviderError("connection") from exc
    return staged


def _unlink_staged(staged: Path) -> None:
    """Best-effort cleanup of the staged portrait copy (no litter).

    A cleanup failure happens AFTER the call's outcome is settled — it
    never changes it; the worst case is a stray copy in ComfyUI's input
    dir, which the next call's fresh ULID name cannot clobber.
    """
    with contextlib.suppress(OSError):
        staged.unlink(missing_ok=True)


def _save_video_output(outputs: Any) -> dict[str, str] | None:
    """The first video file ``{filename, type, subfolder}`` in a history
    entry's ``outputs`` — or None when no output node produced a video.

    ComfyUI history shapes node outputs as ``{<node_id>: {<label>:
    [{"filename": ..., "type": ..., "subfolder": ...}]}}``. Save-video
    nodes are not one standard: VideoHelperSuite's SaveVideo reports
    under ``gifs``, other packs under ``videos``/``results`` — so the
    scan keys on the CONTRACT (a list of dicts carrying a non-blank
    ``filename``) rather than one label; a node whose output carries
    such a list is the SaveVideo marker, and a finished prompt with no
    such list is NO_VIDEO_OUTPUT (matrix row), never a forever-poll.

    A SaveVideo run can also report non-clip artifacts first (VHS's
    animated-GIF preview alongside the clip): an entry whose filename
    ends ``.mp4`` — the ISO-BMFF clip this provider must fetch — is
    preferred, and the first file-carrying entry is the fallback
    (review round 1; the NON_MP4_BYTES provider-side net still guards
    a wrong pick below this).
    """
    if not isinstance(outputs, dict):
        return None
    fallback: dict[str, str] | None = None
    for output in outputs.values():
        if not isinstance(output, dict):
            continue
        for items in output.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("filename"), str)
                    and item["filename"]
                ):
                    entry = {
                        "filename": item["filename"],
                        "type": item.get("type", "output"),
                        "subfolder": item.get("subfolder", ""),
                    }
                    if item["filename"].lower().endswith(".mp4"):
                        return entry
                    if fallback is None:
                        fallback = entry
    return fallback
