"""Image provider tests (spec-4.1): the OpenAI-compatible images/generations
adapter — deterministic, the httpx transport is injected (MockTransport).

Mirrors the chat-completion adapter's contract tests (test_providers.py):
healthy decode, bearer key, config swappability, http/connection error
classes, and malformed-payload rejection.
"""

import base64

import httpx
import pytest

from app.core.settings import ImageSettings
from app.providers.image import image_generation
from app.providers.llm import ProviderError

DEFAULT = ImageSettings()


def _png_b64() -> str:
    return base64.b64encode(b"\x89PNG\r\n\x1a\npayload").decode()


def test_image_generation_returns_decoded_bytes() -> None:
    """A healthy OpenAI image shape yields the decoded PNG bytes and posts
    the exact OpenAI-compatible body."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/images/generations"
        body = request.read().decode()
        assert '"model":"mythos-portrait-v1"' in body
        assert '"prompt":"a sharp face"' in body
        assert '"response_format":"b64_json"' in body
        return httpx.Response(200, json={"data": [{"b64_json": _png_b64()}]})

    data = image_generation(
        "a sharp face",
        settings=DEFAULT,
        transport=httpx.MockTransport(handler),
    )
    assert data == b"\x89PNG\r\n\x1a\npayload"


def test_image_generation_sends_bearer_key_when_configured() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"data": [{"b64_json": _png_b64()}]})

    image_generation(
        "x",
        settings=ImageSettings(api_key="image-secret"),
        transport=httpx.MockTransport(handler),
    )
    assert seen["auth"] == "Bearer image-secret"


def test_image_generation_no_key_header_when_unset() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"data": [{"b64_json": _png_b64()}]})

    image_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert seen["auth"] == ""


def test_image_generation_endpoint_and_model_are_config_swappable() -> None:
    """A different image endpoint/model (local server, hosted API) is a
    config change, never a code change (AR9, NFR8)."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["model"] = request.read().decode()
        return httpx.Response(200, json={"data": [{"b64_json": _png_b64()}]})

    image_generation(
        "x",
        settings=ImageSettings(endpoint="http://127.0.0.1:8081/v1", model="portrait-v2"),
        transport=httpx.MockTransport(handler),
    )
    assert captured["url"] == "http://127.0.0.1:8081/v1/images/generations"
    assert '"portrait-v2"' in captured["model"]


def test_image_generation_http_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="image service exploded")

    with pytest.raises(ProviderError) as excinfo:
        image_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_image_generation_connection_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderError) as excinfo:
        image_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "connection"


def test_image_generation_timeout_raises_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(ProviderError) as excinfo:
        image_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "connection"


def test_image_generation_invalid_endpoint_raises_connection_error() -> None:
    with pytest.raises(ProviderError) as excinfo:
        image_generation(
            "x",
            settings=ImageSettings(endpoint="not-a-url"),
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
    assert excinfo.value.kind == "connection"


def test_image_generation_non_json_200_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Bad Gateway</html>")

    with pytest.raises(ProviderError) as excinfo:
        image_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"


def test_image_generation_malformed_payload_raises_provider_error() -> None:
    """A 200 without the OpenAI image shape is still a provider error,
    never a raw KeyError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(ProviderError) as excinfo:
        image_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"


def test_image_generation_empty_b64_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": "  "}]})

    with pytest.raises(ProviderError):
        image_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))


def test_image_generation_invalid_b64_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": "!!!not-base64!!!"}]})

    with pytest.raises(ProviderError) as excinfo:
        image_generation("x", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"
