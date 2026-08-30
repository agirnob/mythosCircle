"""Provider tests: the OpenAI-compatible chat-completions adapter (spec-1.4).

Deterministic — the httpx transport is injected (MockTransport); the
adapter never touches a real server.
"""

import httpx
import pytest

from app.core.settings import LLMSettings
from app.providers.llm import ProviderError, chat_completion

DEFAULT = LLMSettings()


def test_chat_completion_returns_assistant_text() -> None:
    """A healthy OpenAI-shaped response yields the assistant content."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = request.read().decode()
        assert '"model":"mythos-14b-q5"' in body
        assert '"content":"describe the bar"' in body  # the user prompt rides the request
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "The Gilded Bar."}}]},
        )

    text = chat_completion(
        "describe the bar",
        settings=DEFAULT,
        transport=httpx.MockTransport(handler),
    )
    assert text == "The Gilded Bar."


def test_chat_completion_sends_bearer_key_when_configured() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    chat_completion(
        "hi",
        settings=LLMSettings(api_key="secret-token"),
        transport=httpx.MockTransport(handler),
    )
    assert seen["auth"] == "Bearer secret-token"


def test_chat_completion_no_key_header_when_unset() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    chat_completion("hi", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert seen["auth"] == ""


def test_chat_completion_endpoint_and_model_are_config_swappable() -> None:
    """A different endpoint/model (LM Studio, Ollama, OpenRouter) is a
    config change, never a code change (AR9, NFR8)."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["model"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    chat_completion(
        "hi",
        settings=LLMSettings(endpoint="http://127.0.0.1:1234/v1", model="lm-studio-model"),
        transport=httpx.MockTransport(handler),
    )
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert '"lm-studio-model"' in captured["model"]


def test_chat_completion_http_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="inference exploded")

    with pytest.raises(ProviderError) as excinfo:
        chat_completion("hi", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_chat_completion_connection_error_raises_provider_error() -> None:
    """A connection failure surfaces as kind='connection' — the worker
    fails the job (no retries in 1.4)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderError) as excinfo:
        chat_completion("hi", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "connection"


def test_chat_completion_timeout_raises_connection_error() -> None:
    """A timeout is a connection-class failure (RUN_CONNECTION_ERROR
    includes timeout — review round 1)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(ProviderError) as excinfo:
        chat_completion("hi", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "connection"


def test_chat_completion_invalid_endpoint_raises_connection_error() -> None:
    """An unparseable configured endpoint (missing scheme) is a
    connection-class failure, never a raw httpx.InvalidURL escaping
    (review round 1)."""
    with pytest.raises(ProviderError) as excinfo:
        chat_completion(
            "hi",
            settings=LLMSettings(endpoint="not-a-url"),
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        )
    assert excinfo.value.kind == "connection"


def test_chat_completion_non_json_200_raises_provider_error() -> None:
    """A 200 with a non-JSON body (HTML error page, empty) is still a
    provider error, never a raw JSONDecodeError (review round 1)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Bad Gateway</html>")

    with pytest.raises(ProviderError) as excinfo:
        chat_completion("hi", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"


def test_chat_completion_empty_content_rejected() -> None:
    """A 200 with empty/whitespace assistant content is a provider error
    — a job must never succeed persisting an empty generation (review
    round 1)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})

    with pytest.raises(ProviderError) as excinfo:
        chat_completion("hi", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"


def test_chat_completion_malformed_payload_raises_provider_error() -> None:
    """A 200 without the OpenAI shape is still a provider error, never a
    raw KeyError escaping to the worker."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(ProviderError) as excinfo:
        chat_completion("hi", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "http"
