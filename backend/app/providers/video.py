"""OpenAI-compatible video-generation provider (AR9, AD-14; spec-4.2).

Reveal-video generation goes through the same OpenAI-compatible HTTP
contract as chat and images: the pipeline/media service talks HTTP only,
never a vendor SDK. Swapping engines is a config change
(``MYTHOSCIRCLE_VIDEO_*`` env), never a code change.

Leaf of the dependency graph, exactly like ``providers.llm`` and
``providers.image``: depends on nothing else in ``app/`` except
settings. ``ProviderError`` is the shared provider-failure family from
``app.providers.llm`` — the worker's error vocabulary treats every
provider alike.

The wire body is ``{model, prompt}`` ONLY (spec-4.2 Never list): no
duration/quality knobs — the fast-tier clip is one fixed shape, and the
prompt is the pipeline's projection of the committed AR24 record.
"""

import base64
from collections.abc import Callable
from typing import Any

import httpx

from app.core.settings import VideoSettings
from app.providers.llm import ProviderError

#: A provider call: one prompt in, mp4 bytes out. ``...`` accepts the
#: keyword-only ``transport`` kwarg injected by tests.
VideoGeneration = Callable[..., bytes]

#: Relative path appended under the endpoint base — httpx joins a
#: relative path under ``base_url`` (``http://host/v1`` +
#: ``videos/generations`` -> ``http://host/v1/videos/generations``). A
#: leading slash would replace the base path and drop the ``/v1`` mount
#: (the ``images/generations`` reasoning, spec-4.1).
_VIDEOS_GENERATIONS_PATH = "videos/generations"


def video_generation(
    prompt: str,
    *,
    settings: VideoSettings,
    first_frame: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Call the configured endpoint and return the video bytes (mp4).

    POSTs ``{model, prompt}`` to ``{endpoint}/videos/generations`` and
    decodes ``data[0].b64_json`` — the OpenAI-compatible contract a
    local video server implements. Base64 keeps the response
    self-contained (no second URL fetch, no URL-validation surface).

    ``first_frame`` is the spec-4.5 shared call shape: ``run_video``
    always resolves the entity's newest portrait and passes its path to
    BOTH backends so one call shape serves two providers — the
    OpenAI-compatible server has no source-frame input, so this adapter
    accepts and ignores it.

    Bare ``httpx`` transport failures (DNS, refused, timeout) ->
    ``ProviderError("connection")``; non-2xx ->
    ``ProviderError("http", status_code=...)``; a 200 whose body is not
    the OpenAI shape (non-JSON, missing/empty/undecodable ``b64_json``)
    is still a provider error, never a raw exception escaping the
    provider's contract (the chat/image adapter precedent). The
    transport is injectable for deterministic tests
    (``httpx.MockTransport``).
    """
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
    body: dict[str, Any] = {
        "model": settings.model,
        "prompt": prompt,
    }
    with client:
        try:
            response = client.post(_VIDEOS_GENERATIONS_PATH, json=body, headers=headers)
        except (httpx.RequestError, httpx.InvalidURL, ValueError) as exc:
            # Request-time URL join failures (missing scheme, garbage
            # endpoint) are connection-class failures, never raw escapes.
            raise ProviderError("connection") from exc
    if response.status_code != 200:
        raise ProviderError("http", status_code=response.status_code)
    try:
        payload = response.json()
        encoded = payload["data"][0]["b64_json"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        # A 200 with a non-JSON body (HTML error page, empty) or a
        # malformed payload is still a provider error, never a raw
        # JSONDecodeError escaping the provider's contract.
        raise ProviderError("http", status_code=response.status_code) from exc
    if not isinstance(encoded, str) or not encoded.strip():
        raise ProviderError("http", status_code=response.status_code)
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        # Invalid base64 is a malformed provider payload — the job must
        # fail, never persist garbage bytes.
        raise ProviderError("http", status_code=response.status_code) from exc
    if not data:
        raise ProviderError("http", status_code=response.status_code)
    return data
