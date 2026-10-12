"""Administrative routing persistence, authorization and safe connection tests."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_admin_api import admin_api  # noqa: F401

from app.core.llm_routing import defaults
from app.store.admin import set_disabled
from app.store.auth import create_session
from app.store.db import init_db

# pytest imports this shared fixture for the endpoint matrix.
# ruff: noqa: F811


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/admin/llm-settings"),
        ("put", "/api/admin/llm-settings"),
        ("post", "/api/admin/llm-settings/test"),
    ],
)
def test_authorization(admin_api: tuple[TestClient, str, str], method: str, path: str) -> None:
    client, _, target = admin_api
    body = (
        defaults().model_dump()
        if method == "put"
        else {"provider": "local", "profile": defaults().local.model_dump()}
    )
    client.cookies.set("mythoscircle_session", create_session(target)[0], path="/api")
    result = getattr(client, method)(path, **({"json": body} if method != "get" else {}))
    assert result.status_code == 403 and result.headers["cache-control"] == "no-store"
    set_disabled(target, True, admin_ids=frozenset())
    assert (
        getattr(client, method)(path, **({"json": body} if method != "get" else {})).status_code
        == 401
    )
    client.cookies.clear()
    assert (
        getattr(client, method)(path, **({"json": body} if method != "get" else {})).status_code
        == 401
    )


def test_persistence_and_safe_projection(
    admin_api: tuple[TestClient, str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from app.store import db

    client, _, _ = admin_api
    monkeypatch.setenv("MYTHOSCIRCLE_GEMINI_API_KEY", "never-return-this")
    assert client.get("/api/admin/llm-settings").json()["saved"] is False
    config = defaults().model_dump()
    config.update(primary="gemini", backup="local")
    response = client.put("/api/admin/llm-settings", json=config)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert "never-return-this" not in response.text
    original = str(db._engine.url)
    init_db(f"sqlite:///{tmp_path / 'restart.db'}")
    init_db(original)
    response = client.get("/api/admin/llm-settings")
    assert response.json()["primary"] == "gemini"
    assert response.json()["backup"] == "local"
    assert response.json()["gemini_key_configured"] is True
    assert "api_key" not in response.text


@pytest.mark.parametrize(
    "change",
    [
        {"primary": "gemini"},
        {"backup": "local"},
        {"local": {"endpoint": "http://user:password@host/v1"}},
        {"local": {"endpoint": "http://host/v1?key=secret"}},
        {"local": {"endpoint": "ftp://host/v1"}},
        {"local": {"endpoint": "http://host/v1#fragment"}},
        {"local": {"timeout": 0}},
        {"local": {"max_tokens": 0}},
        {"local": {"temperature": 3}},
        {"local": {"api_key": "secret"}},
        {"gemini": {"endpoint": "https://other.example/v1"}},
    ],
)
def test_invalid_save_is_atomic(
    admin_api: tuple[TestClient, str, str], monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    monkeypatch.delenv("MYTHOSCIRCLE_GEMINI_API_KEY", raising=False)
    client = admin_api[0]
    initial = defaults().model_dump()
    assert client.put("/api/admin/llm-settings", json=initial).status_code == 200
    invalid = defaults().model_dump()
    for key, value in change.items():
        if isinstance(value, dict):
            invalid[key].update(value)
        else:
            invalid[key] = value
    invalid_response = client.put("/api/admin/llm-settings", json=invalid)
    assert invalid_response.status_code == 422
    assert "secret" not in invalid_response.text and "password@" not in invalid_response.text
    result = client.get("/api/admin/llm-settings").json()
    assert {key: result[key] for key in initial} == initial


def test_connection_test_bounded_and_sanitized(
    admin_api: tuple[TestClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import admin
    from app.providers.llm import ProviderError

    calls = []

    def complete(prompt: str, *, settings: Any) -> str:
        calls.append(settings)
        raise ProviderError("http", status_code=429)

    monkeypatch.setattr(admin, "chat_completion", complete)
    client = admin_api[0]
    payload = {"provider": "gemini", "profile": defaults().gemini.model_dump()}
    monkeypatch.delenv("MYTHOSCIRCLE_GEMINI_API_KEY", raising=False)
    assert client.post("/api/admin/llm-settings/test", json=payload).status_code == 422
    assert calls == []
    monkeypatch.setenv("MYTHOSCIRCLE_GEMINI_API_KEY", "private-key")
    result = client.post("/api/admin/llm-settings/test", json=payload)
    assert result.status_code == 200 and result.json()["success"] is False
    assert result.headers["cache-control"] == "no-store"
    assert "private-key" not in result.text and "429" in result.json()["message"]
    assert len(calls) == 1 and calls[0].max_tokens == 2048 and calls[0].timeout == 120
    assert calls[0].routing is None and calls[0].reasoning_effort == "low"


@pytest.mark.parametrize("configured,expected", [(0.5, 0.5), (90, 90), (600, 120)])
def test_gemini_probe_respects_smaller_timeouts_and_caps_long_waits(
    configured: float,
    expected: float,
    admin_api: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import admin

    monkeypatch.setenv("MYTHOSCIRCLE_GEMINI_API_KEY", "private-key")

    def complete(prompt: str, *, settings: Any) -> str:
        assert settings.timeout == expected
        return "OK"

    monkeypatch.setattr(admin, "chat_completion", complete)
    profile = defaults().gemini.model_copy(update={"timeout": configured})
    result = admin_api[0].post(
        "/api/admin/llm-settings/test", json={"provider": "gemini", "profile": profile.model_dump()}
    )
    assert result.status_code == 200 and result.json()["success"] is True


def test_existing_deployment_timeout_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TIMEOUT", "2400")
    assert defaults().local.timeout == 2400


@pytest.mark.parametrize("kind", ["truncated", "empty"])
def test_local_connection_test_reaches_provider_with_viable_budget(
    kind: str, admin_api: tuple[TestClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import admin
    from app.providers.llm import ProviderError

    def complete(prompt: str, *, settings: Any) -> str:
        assert settings.max_tokens == 2048 and settings.timeout <= 15
        assert settings.enable_thinking is True
        raise ProviderError(kind)

    monkeypatch.setattr(admin, "chat_completion", complete)
    profile = defaults().local.model_copy(update={"enable_thinking": True})
    result = admin_api[0].post(
        "/api/admin/llm-settings/test", json={"provider": "local", "profile": profile.model_dump()}
    )
    assert result.status_code == 200 and result.json()["success"] is False
    assert "Provider reached and authenticated" in result.json()["message"]
    assert "output limit" in result.json()["message"]
