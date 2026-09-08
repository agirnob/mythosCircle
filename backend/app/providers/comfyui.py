"""ComfyUI image-generation provider (spec-4.4) — the local-dev portrait
backend.

The OpenAI-compatible ``images/generations`` shape (spec-4.1) targets a
server that does not exist on the dev workstation; the working local
backend is ComfyUI (the Krea2 Turbo T2I workflow) whose wire protocol
is poll-based: submit the workflow JSON to ``/prompt``, poll
``/history/{prompt_id}`` until a SaveImage node has output, fetch the
PNG bytes from ``/view``. The sibling provider mirrors the 4-1 / 4-2
adapter discipline — leaf of the dependency graph, ``Callable[...,
bytes]``, ``ProviderError`` family, injectable ``transport``, kwargs-only
``settings`` — and returns PNG bytes, so ``run_portrait``'s
``PNG_SIGNATURE`` guard stays the single write boundary for both
backends (spec-4.4 Always list). Backend-only: the worker picks this
provider when ``[image] backend = "comfyui"``; the OpenAI path stays the
default.

Leaf of the dependency graph, exactly like ``providers.llm`` / ``image``
/ ``video``: depends on nothing else in ``app/`` except settings.
``ProviderError`` is the shared provider-failure family from
``app.providers.llm`` — the worker's error vocabulary treats every
provider alike.
"""

import copy
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.core.settings import ComfyUIImageSettings
from app.providers.llm import ProviderError

#: A provider call: one prompt in, PNG bytes out. ``...`` accepts the
#: keyword-only ``transport`` kwarg injected by tests.
ComfyUIImageGeneration = Callable[..., bytes]

#: Relative paths appended under the endpoint base — httpx joins a
#: relative path under ``base_url`` (``http://host:7896`` +
#: ``prompt`` -> ``http://host:7896/prompt``). ComfyUI's API is
#: bare-host, no version mount.
_PROMPT_PATH = "prompt"
_VIEW_PATH = "view"

#: Seconds between ``/history`` polls — a hardcoded code default, NOT an
#: operator knob (spec-4.4 Always list). The ``timeout`` setting bounds
#: the ENTIRE call, never the cadence.
_POLL_INTERVAL = 1.0

#: The PNG magic a ``/view`` 200 must carry: ComfyUI's SaveImage output
#: is always PNG, so a non-PNG body (an HTML error page wrapped in a
#: 200, a truncated body) is garbage and is rejected PROVIDER-SIDE
#: (spec-4.4 matrix row NON_PNG_BYTES) — ``run_portrait``'s
#: ``PNG_SIGNATURE`` write-boundary guard in media/service.py stays the
#: single authoritative check at the file write.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: The PNG IEND terminator (chunk type + its fixed CRC): a /view 200
#: whose body has an intact 8-byte signature but is truncated (no IEND
#: chunk, mid-chunk cut) would be written as a corrupt .png. The
#: provider requires signature + terminator so the write boundary only
#: ever sees a complete PNG (review round 1).
_PNG_IEND = b"IEND\xaeB`\x82"

#: The smallest plausible PNG (signature + one chunk + IEND); anything
#: shorter cannot be a complete image.
_MIN_PNG_LEN = 20

#: Acceptable ComfyUI prompt ids — hashes the server emits. A
#: hostile/broken server must not inject path segments into the
#: ``/history/{prompt_id}`` URL (review round 1).
_SAFE_PROMPT_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def comfyui_image_generation(
    prompt: str,
    *,
    settings: ComfyUIImageSettings,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Submit one generation to ComfyUI and return the PNG bytes.

    Loads the operator's workflow JSON from ``settings.workflow_path``
    (reloaded from disk on EVERY call — a stale workflow must never be
    served), deep-copies it, injects ``prompt`` into the configured
    prompt node's ``inputs.value`` and the configured generation shape
    (``aspect_ratio`` / ``megapixels``) into the workflow's resolution
    node if it has one, POSTs to ``{endpoint}/prompt``, polls
    ``{endpoint}/history/{prompt_id}`` every ``_POLL_INTERVAL`` seconds
    until a SaveImage node has output — or ``settings.timeout`` elapses
    — then GETs the image bytes from ``{endpoint}/view``.

    ``settings.timeout`` bounds the ENTIRE call (submit + poll + fetch):
    every request carries a per-request httpx timeout capped at the
    remaining deadline budget, and the poll loop / view fetch refuse to
    start once the deadline is exhausted.

    Error vocabulary (the 4-1 / 4-2 family plus one new kind): a
    missing/malformed workflow file, a prompt node without a string
    ``inputs.value`` (not the Krea2 shape) -> ``ProviderError(
    "connection")``; bare ``httpx`` transport failures (DNS, refused) ->
    ``ProviderError("connection")``; any non-2xx ->
    ``ProviderError("http", status_code=...)``; a 2xx whose body is not
    the expected shape (malformed submit, hostile prompt_id, history
    with no SaveImage output, incomplete/non-PNG view bytes) is still a
    provider error; the deadline being consumed — by a hung request or
    by polling with no output — -> ``ProviderError("timeout")``, the
    only caller of the new kind (spec-4.4).
    """
    # The workflow JSON is the operator's artifact (documented, never
    # shipped in the repo — spec-4.4 Never list). Missing or malformed
    # is a connection-class failure at first call, surfacing operator
    # misconfig (matrix rows WORKFLOW_PATH_MISSING / INVALID_WORKFLOW_JSON).
    try:
        workflow = json.loads(Path(settings.workflow_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderError("connection") from exc
    node = workflow.get(settings.prompt_node_id) if isinstance(workflow, dict) else None
    node_inputs = node.get("inputs") if isinstance(node, dict) else None
    value = node_inputs.get("value") if isinstance(node_inputs, dict) else None
    if not isinstance(value, str):
        # The prompt widget (``inputs.value``) is the ONLY field the
        # provider must touch — a workflow that cannot carry it (missing
        # node, non-dict ``inputs``, non-string widget) is not the Krea2
        # shape and is unusable, not worth submitting (matrix row
        # INVALID_WORKFLOW_JSON, review rounds 1/2: guarded against a
        # present-but-non-dict ``inputs``).
        raise ProviderError("connection")
    workflow = copy.deepcopy(workflow)
    workflow[settings.prompt_node_id]["inputs"]["value"] = prompt
    _apply_resolution(workflow, settings)

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
    # The timeout bounds the ENTIRE call (spec-4.4): the deadline gates
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
            # the history URL (review round 1).
            raise ProviderError("http", status_code=response.status_code)

        image: dict[str, str] | None = None
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
            # without a SaveImage output is NO_OUTPUT_NODE — fail, never
            # poll forever (matrix row NO_OUTPUT_NODE).
            image = _save_image_output(entry.get("outputs"))
            if image is None:
                raise ProviderError("http", status_code=response.status_code)
            break
        if image is None:
            # Poll exhaustion: every history poll came up empty before the
            # deadline. The ONLY caller of the "timeout" kind (spec-4.4).
            raise ProviderError("timeout")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("timeout")
        try:
            response = client.get(
                _VIEW_PATH,
                params=image,
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
        if (
            len(data) < _MIN_PNG_LEN
            or not data.startswith(_PNG_SIGNATURE)
            or not data.endswith(_PNG_IEND)
        ):
            # A /view 200 that is not a COMPLETE PNG — an HTML error
            # page, a truncated body with an intact signature — is
            # rejected provider-side, before run_portrait ever sees it
            # (matrix rows NON_PNG_BYTES; the write-boundary guard
            # stays single).
            raise ProviderError("http", status_code=200)
        return data


def _request_timeout(timeout: float, remaining: float) -> float:
    """A per-request httpx timeout capped at the whole-call deadline.

    ``min(timeout, remaining)`` would let a request run the full
    settings timeout even with seconds left on the deadline — that is
    the 2-3x overshoot this guard exists to prevent. The floor keeps
    httpx from rejecting a zero/negative timeout; the deadline checks
    around each request are the real gate.
    """
    return min(timeout, max(0.01, remaining))


def _apply_resolution(workflow: dict[str, Any], settings: ComfyUIImageSettings) -> None:
    """Pin the documented generation shape on the workflow's resolution
    node, if it has one.

    The Krea2 Turbo workflow's ResolutionSelector node carries the
    ``aspect_ratio`` / ``megapixels`` widgets; the provider pins them
    from config (spec-4.4: ``aspect_ratio`` / ``megapixels`` are
    config-level defaults — one job = one image, fixed shape per
    config, no per-job knobs). A workflow without such a node keeps its
    fixed shape — tolerated, never a failure (review round 1).
    """
    for candidate in workflow.values():
        if not isinstance(candidate, dict):
            continue
        inputs = candidate.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if candidate.get("class_type") == "ResolutionSelector" or (
            "aspect_ratio" in inputs and "megapixels" in inputs
        ):
            inputs["aspect_ratio"] = settings.aspect_ratio
            inputs["megapixels"] = settings.megapixels
            return


def _save_image_output(outputs: Any) -> dict[str, str] | None:
    """The first SaveImage output ``{filename, type, subfolder}`` in a
    history entry's ``outputs`` — or None when no SaveImage node
    produced an image.

    ComfyUI history shapes node outputs as ``{<node_id>: {"images":
    [{"filename": ..., "type": "output", "subfolder": ...}]}}`` — a
    node whose output carries an ``images`` list is the SaveImage
    marker.
    """
    if not isinstance(outputs, dict):
        return None
    for output in outputs.values():
        if not isinstance(output, dict):
            continue
        images = output.get("images")
        if not isinstance(images, list):
            continue
        for image in images:
            if (
                isinstance(image, dict)
                and isinstance(image.get("filename"), str)
                and image["filename"]
            ):
                return {
                    "filename": image["filename"],
                    "type": image.get("type", "output"),
                    "subfolder": image.get("subfolder", ""),
                }
    return None
