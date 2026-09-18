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

The ComfyUI wire machinery is SHARED with the video twin: the endpoint
config, workflow prompt build, JSON error mapping, and the
deadline-budgeted submit/poll/fetch core live in
``providers.comfyui_common`` (epic-4 retro item 11) — this module owns
only the format-specific atoms: the PNG completeness check (signature +
IEND), the SaveImage output scan with the rembg node targeting, and the
resolution pinning.

Leaf of the dependency graph, exactly like ``providers.llm`` / ``image``
/ ``video``: depends on nothing else in ``app/`` except settings.
``ProviderError`` is the shared provider-failure family from
``app.providers.llm`` — the worker's error vocabulary treats every
provider alike.
"""

from collections.abc import Callable
from typing import Any

import httpx

from app.core.settings import ComfyUIImageSettings
from app.providers.comfyui_common import (
    build_workflow,
    fetch_generation,
    load_workflow,
    widget,
)
from app.providers.llm import ProviderError

#: A provider call: one prompt in, PNG bytes out. ``...`` accepts the
#: keyword-only ``transport`` kwarg injected by tests.
ComfyUIImageGeneration = Callable[..., bytes]

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


def _valid_png(data: bytes) -> bool:
    """A COMPLETE PNG: signature + IEND terminator and at least the
    smallest plausible size — anything else is garbage the provider
    rejects before the runner sees it (matrix row NON_PNG_BYTES)."""
    return (
        len(data) >= _MIN_PNG_LEN and data.startswith(_PNG_SIGNATURE) and data.endswith(_PNG_IEND)
    )


def comfyui_image_generation(
    prompt: str,
    *,
    settings: ComfyUIImageSettings,
    transport: httpx.BaseTransport | None = None,
    use_rembg: bool = False,
) -> bytes:
    """Submit one generation to ComfyUI and return the PNG bytes.

    Loads the operator's workflow JSON from ``settings.workflow_path``
    (``settings.rembg_workflow_path`` instead when ``use_rembg`` — the
    P1 transparent-background variant, 2026-09-15), reloaded from disk
    on EVERY call (a stale workflow must never be served), deep-copies
    it, injects ``prompt`` into the configured prompt node's
    ``inputs.value`` and the configured generation shape
    (``aspect_ratio`` / ``megapixels``) into the workflow's resolution
    node if it has one, then runs the shared submit -> poll -> fetch
    core (``comfyui_common.fetch_generation``).

    The rembg workflow carries TWO SaveImage nodes (the plain PNG and
    the alpha-joined transparent one); when ``use_rembg`` the provider
    targets ``settings.rembg_output_node_id`` so it always returns the
    transparent PNG, never the plain twin.

    ``settings.timeout`` bounds the ENTIRE call (submit + poll + fetch).

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
    # An unconfigured rembg variant (use_rembg with no path) is the same
    # class: the operator asked for a transparent background without
    # wiring the second workflow.
    path = settings.rembg_workflow_path if use_rembg else settings.workflow_path
    workflow = load_workflow(path)
    if widget(workflow, settings.prompt_node_id, "value") is None:
        # The prompt widget (``inputs.value``) is the ONLY field the
        # provider must touch — a workflow that cannot carry it (missing
        # node, non-dict ``inputs``, non-string widget) is not the Krea2
        # shape and is unusable, not worth submitting (matrix row
        # INVALID_WORKFLOW_JSON, review rounds 1/2: guarded against a
        # present-but-non-dict ``inputs``).
        raise ProviderError("connection")
    workflow = build_workflow(workflow, settings.prompt_node_id, "value", prompt)
    _apply_resolution(workflow, settings)

    return fetch_generation(
        workflow=workflow,
        endpoint=settings.endpoint,
        timeout=settings.timeout,
        api_key=settings.api_key,
        transport=transport,
        extract_output=lambda outputs: _save_image_output(
            outputs, settings.rembg_output_node_id if use_rembg else None
        ),
        valid_bytes=_valid_png,
    )


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


def _save_image_output(outputs: Any, node_id: str | None = None) -> dict[str, str] | None:
    """The SaveImage output ``{filename, type, subfolder}`` for a
    history entry's ``outputs`` — or None when no SaveImage node
    produced an image.

    ComfyUI history shapes node outputs as ``{<node_id>: {"images":
    [{"filename": ..., "type": "output", "subfolder": ...}]}}`` — a
    node whose output carries an ``images`` list is the SaveImage
    marker.

    With ``node_id`` given, ONLY that node's output counts: the rembg
    workflow has two SaveImage nodes (the plain PNG and the alpha-joined
    transparent one) and the caller names the alpha node so the provider
    never returns the wrong twin. Without it, the first SaveImage output
    wins (the base workflow's single SaveImage — existing behavior).
    """
    if not isinstance(outputs, dict):
        return None
    candidates = [outputs.get(node_id)] if node_id is not None else list(outputs.values())
    for output in candidates:
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
