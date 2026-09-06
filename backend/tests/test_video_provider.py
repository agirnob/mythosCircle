"""Video provider tests (spec-4.2): the OpenAI-compatible
videos/generations contract — base64 mp4 out, injectable transport,
ProviderError family.

Mirrors test_image_provider.py (spec-4.1) case for case: the adapter is
the same shape with ``videos/generations`` under the base URL and a
``{model, prompt}``-only wire body (spec-4.2 Never list — no
duration/quality knobs)."""

import base64

import httpx
import pytest

from app.core.settings import VideoSettings
from app.providers.llm import ProviderError
from app.providers.video import video_generation

DEFAULT = VideoSettings()


def _mp4_b64() -> str:
    # Minimal ISO-BMFF-flavored bytes: box-size longword then "ftyp".
    return base64.b64encode(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00payload").decode()


def test_video_generation_returns_decoded_bytes() -> None:
    """A healthy OpenAI video shape yields the decoded mp4 bytes and posts
    the exact contract body — model + prompt ONLY (no duration/quality
    knobs, spec-4.2 Never list)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/videos/generations"
        body = request.read().decode()
        assert '"model":"mythos-reveal-v1"' in body
        assert '"prompt":"a cinematic reveal"' in body
        assert "response_format" not in body
        assert "seconds" not in body
        return httpx.Response(200, json={"data": [{"b64_json": _mp4_b64()}]})

    data = video_generation(
        "a cinematic reveal",
        settings=DEFAULT,
        transport=httpx.MockTransport(handler),
    )
    assert data == b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00payload"


def test_video_generation_sends_bearer_key_when_configured() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"data": [{"b64_json": _mp4_b64()}]})

    video_generation(
        "x",
        settings=VideoSettings(api_key="video-secret"),
        transport=httpx.MockTransport(handler),
    )
    assert seen["auth"] == "Bearer video-secret"


def test_video_generation_no_key_header_when_unset() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"data": [{"b64_json": _mp4_b64()}]})

    video_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert seen["auth"] == ""


def test_video_generation_endpoint_and_model_are_config_swappable() -> None:
    """A different video endpoint/model (local server, hosted API) is a
    config change, never a code change (AR9, NFR8)."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["model"] = request.read().decode()
        return httpx.Response(200, json={"data": [{"b64_json": _mp4_b64()}]})

    video_generation(
        "x",
        settings=VideoSettings(endpoint="http://127.0.0.1:8082/v1", model="reveal-v2"),
        transport=httpx.MockTransport(handler),
    )
    assert captured["url"] == "http://127.0.0.1:8082/v1/videos/generations"
    assert '"reveal-v2"' in captured["model"]


def test_video_generation_http_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="video service exploded")

    with pytest.raises(ProviderError) as excinfo:
        video_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_video_generation_connection_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderError) as excinfo:
        video_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "connection"


def test_video_generation_timeout_raises_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(ProviderError) as excinfo:
        video_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "connection"


def test_video_generation_invalid_endpoint_raises_connection_error() -> None:
    with pytest.raises(ProviderError) as excinfo:
        video_generation(
            "x",
            settings=VideoSettings(endpoint="not-a-url"),
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
    assert excinfo.value.kind == "connection"


def test_video_generation_non_json_200_raises_provider_error() -> None:
    """A 200 wrapping an HTML error page is a provider error, never bytes
    that could reach the write boundary (spec-4.2 Design Notes)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Bad Gateway</html>")

    with pytest.raises(ProviderError) as excinfo:
        video_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"


def test_video_generation_malformed_payload_raises_provider_error() -> None:
    """A 200 without the OpenAI shape is still a provider error, never a
    raw KeyError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(ProviderError) as excinfo:
        video_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"


def test_video_generation_empty_b64_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": "  "}]})

    with pytest.raises(ProviderError):
        video_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))


def test_video_generation_invalid_b64_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": "!!!not-base64!!!"}]})

    with pytest.raises(ProviderError) as excinfo:
        video_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"
