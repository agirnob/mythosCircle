"""Shared ComfyUI scaffolding (epic-4 retro item 11): the twin atoms of
the image provider (``providers.comfyui``, spec-4.4) and the video
provider (``providers.comfyui_video``, spec-4.5) extracted once so the
two adapters stop carrying the same machinery in parallel.

What lives here — the atoms BOTH providers share verbatim:

* **endpoint config** — the relative wire paths (``/prompt``,
  ``/view``), the poll cadence, and the prompt-id safety pattern, all
  identical across the twins;
* **workflow prompt build** — loading the operator's workflow JSON off
  disk (every call, never cached), the guarded string-widget lookup
  that proves a workflow can carry the provider's prompt, and the
  deep-copy injection that mutates only the one widget;
* **JSON error mapping** — every response/body that is not the expected
  shape maps to the ``ProviderError`` family (connection/http/timeout),
  never a raw exception escaping the provider boundary;
* **retry/warmup** — the deadline-budgeted submit -> poll ``/history``
  -> fetch ``/view`` loop that tolerates ComfyUI's warmup lag (the
  first call after startup may need seconds to load) while bounding the
  ENTIRE call by ``settings.timeout``; ``request_timeout`` caps every
  per-request httpx timeout at the remaining deadline so a hung poll
  can never exceed the whole-call budget.

What stays in each provider: the format-specific atoms (the PNG
signature/IEND/len checks vs the mp4 ``ftyp`` check, the SaveImage
output scan vs the SaveVideo scan — including the rembg node targeting
and the video first-frame staging), ``_apply_resolution``, and the
``comfyui_*_generation`` entry points with their settings types.

Leaf of the dependency graph, exactly like the two providers it
serves: depends on nothing else in ``app/`` except settings and the
shared ``ProviderError`` family from ``app.providers.llm``.
"""

import copy
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.providers.llm import ProviderError

#: Relative paths appended under the endpoint base — httpx joins a
#: relative path under ``base_url`` (``http://host:7896`` +
#: ``prompt`` -> ``http://host:7896/prompt``). ComfyUI's API is
#: bare-host, no version mount.
PROMPT_PATH = "prompt"
VIEW_PATH = "view"

#: Seconds between ``/history`` polls — a hardcoded code default, NOT an
#: operator knob (the spec-4.4/4-5 Always lists). The ``timeout`` setting
#: bounds the ENTIRE call, never the cadence.
POLL_INTERVAL = 1.0

#: Acceptable ComfyUI prompt ids — hashes the server emits. A
#: hostile/broken server must not inject path segments into the
#: ``/history/{prompt_id}`` URL (the 4-4 review-round-1 contract).
SAFE_PROMPT_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def request_timeout(timeout: float, remaining: float) -> float:
    """A per-request httpx timeout capped at the whole-call deadline.

    ``min(timeout, remaining)`` would let a request run the full
    settings timeout even with seconds left on the deadline — that is
    the 2-3x overshoot this guard exists to prevent. The floor keeps
    httpx from rejecting a zero/negative timeout; the deadline checks
    around each request are the real gate.
    """
    return min(timeout, max(0.01, remaining))


def load_workflow(path: str | None) -> dict[str, Any]:
    """Load the operator's workflow JSON off disk — reloaded on EVERY
    call (a stale workflow must never be served, spec-4.4/4-5 Never
    lists).

    A missing/unset path or an unparseable file is a connection-class
    failure (operator/state misconfig surfaced at first call), never a
    raw ``OSError``/``JSONDecodeError`` escaping the provider boundary.
    """
    if not path:
        raise ProviderError("connection")
    try:
        workflow: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderError("connection") from exc
    if not isinstance(workflow, dict):
        # A non-dict workflow JSON (list/string/scalar) is operator
        # misconfiguration — the same connection-class failure the JSON
        # decode raises, never a raw AttributeError downstream.
        raise ProviderError("connection")
    return workflow


def widget(workflow: Any, node_id: str, key: str) -> str | None:
    """The string ``inputs[key]`` widget of a workflow node — or None
    when the workflow cannot carry it (non-dict workflow, missing node,
    non-dict ``inputs``, non-string widget).

    The guarded lookup both providers need before submitting: a
    present-but-non-dict ``inputs`` must be a connection-class failure,
    never a raw ``AttributeError`` (review round 1).
    """
    node = workflow.get(node_id) if isinstance(workflow, dict) else None
    node_inputs = node.get("inputs") if isinstance(node, dict) else None
    value = node_inputs.get(key) if isinstance(node_inputs, dict) else None
    return value if isinstance(value, str) else None


def build_workflow(workflow: dict[str, Any], node_id: str, key: str, value: str) -> dict[str, Any]:
    """Deep-copy the operator's workflow and inject ``value`` into the
    node's ``inputs[key]`` widget — the ONLY mutation the provider makes
    to the artifact (the copy keeps the on-disk file untouched)."""
    built = copy.deepcopy(workflow)
    built[node_id]["inputs"][key] = value
    return built


def fetch_generation(
    *,
    workflow: dict[str, Any],
    endpoint: str,
    timeout: float,
    api_key: str | None,
    transport: httpx.BaseTransport | None,
    extract_output: Callable[[Any], dict[str, str] | None],
    valid_bytes: Callable[[bytes], bool],
) -> bytes:
    """Submit one generation to ComfyUI and return the provider bytes.

    The shared submit -> poll ``/history/{prompt_id}`` -> fetch
    ``/view`` core: POSTs the already-built ``workflow`` JSON to
    ``{endpoint}/prompt``, polls ``{endpoint}/history/{prompt_id}``
    every ``POLL_INTERVAL`` seconds until ``extract_output`` finds the
    provider's output node (or ``timeout`` elapses — ComfyUI's warmup
    lag is absorbed by the poll loop), then GETs the bytes from
    ``{endpoint}/view`` and admits them only when ``valid_bytes`` says
    the body carries the provider's format (PNG for the image twin, mp4
    for the video twin).

    ``timeout`` bounds the ENTIRE call (submit + poll + fetch): every
    request carries a per-request httpx timeout capped at the remaining
    deadline budget (``request_timeout``), and the poll loop / view
    fetch refuse to start once the deadline is exhausted.

    Error vocabulary (the shared family): bare ``httpx`` transport
    failures (DNS, refused) -> ``ProviderError("connection")``; any
    non-2xx -> ``ProviderError("http", status_code=...)``; a 2xx whose
    body is not the expected shape (malformed submit, hostile
    prompt_id, history with no output, incomplete/format-less view
    bytes) is still a provider error; the deadline being consumed — by
    a hung request or by polling with no output — ->
    ``ProviderError("timeout")``.
    """
    try:
        client = httpx.Client(
            base_url=endpoint,
            timeout=timeout,
            transport=transport,
        )
    except (httpx.InvalidURL, ValueError) as exc:
        # An unparseable configured endpoint (missing scheme, garbage) is a
        # connection-class failure, never a raw exception escaping.
        raise ProviderError("connection") from exc
    headers = {"Authorization": f"Bearer {api_key}"} if api_key is not None else {}
    # The timeout bounds the ENTIRE call (spec-4.4/4-5): the deadline gates
    # every request and the poll loop, so a hung request can never run
    # the call past settings.timeout. One ``with client`` for the whole
    # call — an httpx client cannot be re-opened once closed, and one
    # call must be one generation (submit + poll + fetch).
    deadline = time.monotonic() + timeout
    with client:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("timeout")
        try:
            response = client.post(
                PROMPT_PATH,
                json={"prompt": workflow},
                headers=headers,
                timeout=request_timeout(timeout, remaining),
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
        if not isinstance(prompt_id, str) or not SAFE_PROMPT_ID.match(prompt_id):
            # A hostile/broken server must not inject path segments into
            # the history URL (review round 1).
            raise ProviderError("http", status_code=response.status_code)

        output: dict[str, str] | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break  # poll exhaustion -> ProviderError("timeout") below
            try:
                response = client.get(
                    f"history/{prompt_id}",
                    headers=headers,
                    timeout=request_timeout(timeout, remaining),
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
                time.sleep(min(POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
                continue
            if not isinstance(entry, dict):
                raise ProviderError("http", status_code=response.status_code)
            # The entry exists: the prompt FINISHED. A completed prompt
            # without the provider's output is NO_OUTPUT_NODE / NO_VIDEO
            # _OUTPUT — fail, never poll forever (the matrix rows).
            output = extract_output(entry.get("outputs"))
            if output is None:
                raise ProviderError("http", status_code=response.status_code)
            break
        if output is None:
            # Poll exhaustion: every history poll came up empty before the
            # deadline. The ONLY caller of the "timeout" kind (spec-4.4).
            raise ProviderError("timeout")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("timeout")
        try:
            response = client.get(
                VIEW_PATH,
                params=output,
                headers=headers,
                timeout=request_timeout(timeout, remaining),
            )
        except (httpx.RequestError, httpx.InvalidURL, ValueError) as exc:
            if time.monotonic() >= deadline:
                raise ProviderError("timeout") from exc
            raise ProviderError("connection") from exc
        if response.status_code != 200:
            raise ProviderError("http", status_code=response.status_code)
        data = response.content
        if not valid_bytes(data):
            # A /view 200 that is not a COMPLETE payload in the provider's
            # format — an HTML error page, a truncated body with an intact
            # signature — is rejected provider-side, before the runner
            # ever sees it (matrix rows NON_PNG_BYTES / NON_MP4_BYTES;
            # the write-boundary guard stays single).
            raise ProviderError("http", status_code=200)
        return data
