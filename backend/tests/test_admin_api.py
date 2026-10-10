"""Private administrative API contract and suspension boundaries."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.ids import new_id
from app.store import app_db_url, create_campaign, init_db
from app.store.admin import set_disabled
from app.store.auth import create_session, get_session_account, register_account, verify_login


@pytest.fixture()
def admin_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, str, str]]:
    from app.api.auth import _login_limiter, _register_limiter
    from app.main import create_app

    previous = app_db_url()
    url = f"sqlite:///{tmp_path / 'api.db'}"
    monkeypatch.setenv("MYTHOSCIRCLE_DB", url)
    init_db(url)
    admin = register_account("admin@example.com", "password123")
    target = register_account("target@example.com", "password123")
    monkeypatch.setenv("MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS", admin.id)
    _login_limiter.reset()
    _register_limiter.reset()
    try:
        with TestClient(
            create_app(), base_url="https://testserver", raise_server_exceptions=False
        ) as client:
            client.cookies.set("mythoscircle_session", create_session(admin.id)[0], path="/api")
            yield client, admin.id, target.id
    finally:
        _login_limiter.reset()
        _register_limiter.reset()
        init_db(previous)


def test_metadata_and_owner_scope(admin_api: tuple[TestClient, str, str]) -> None:
    client, admin, target = admin_api
    campaign = create_campaign(
        target, title="Secret world", description="", theme="High Fantasy", custom_lore=""
    )
    response = client.get("/api/admin/users", params={"limit": 1})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["users"][0]["id"] == admin
    next_page = client.get("/api/admin/users", params={"cursor": response.json()["next_cursor"]})
    user = next_page.json()["users"][0]
    assert set(user) == {"id", "email", "is_admin", "disabled_at", "created_at", "campaign_count"}
    assert user["campaign_count"] == 1 and next_page.json()["next_cursor"] is None
    assert client.get(f"/api/campaigns/{campaign.id}").status_code == 404
    assert client.get("/api/auth/me").json()["is_admin"] is True


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"q": "x" * 321}, {"cursor": "bad"}, {"cursor": ""}]
)
def test_invalid_query(admin_api: tuple[TestClient, str, str], params: dict[str, Any]) -> None:
    response = admin_api[0].get("/api/admin/users", params=params)
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"disabled": 1},
        {"disabled": "true"},
        {"disabled": None},
        {"disabled": True, "is_admin": True},
    ],
)
def test_strict_body(admin_api: tuple[TestClient, str, str], body: dict[str, Any]) -> None:
    client, _, target = admin_api
    assert client.patch(f"/api/admin/users/{target}", json=body).status_code == 422


def test_authorization_and_target_errors(admin_api: tuple[TestClient, str, str]) -> None:
    client, admin, target = admin_api
    assert client.patch(f"/api/admin/users/{admin}", json={"disabled": True}).status_code == 409
    assert client.patch(f"/api/admin/users/{new_id()}", json={"disabled": False}).status_code == 404
    assert client.patch("/api/admin/users/bad", json={"disabled": False}).status_code == 422
    assert client.patch(f"/api/admin/users/{'Z' * 26}", json={"disabled": False}).status_code == 422
    client.cookies.set("mythoscircle_session", create_session(target)[0], path="/api")
    assert client.get("/api/auth/me").json()["is_admin"] is False
    assert client.get("/api/admin/users").status_code == 403
    assert client.patch(f"/api/admin/users/{target}", json={"disabled": True}).status_code == 403
    client.cookies.clear()
    response = client.get("/api/admin/users")
    assert response.status_code == 401 and response.headers["cache-control"] == "no-store"


def test_disable_retry_cleanup_failure_restore(
    admin_api: tuple[TestClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.ws import hub

    client, _, target = admin_api
    old_token = create_session(target)[0]
    original = hub.invalidate_account

    async def fail(account_id: str) -> None:
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(hub, "invalidate_account", fail)
    response = client.patch(f"/api/admin/users/{target}", json={"disabled": True})
    assert response.status_code == 500 and response.headers["cache-control"] == "no-store"
    assert response.json()["message"] == "Internal server error."
    timestamp = client.get("/api/admin/users", params={"q": "target"}).json()["users"][0][
        "disabled_at"
    ]
    assert timestamp and get_session_account(old_token) is None
    monkeypatch.setattr(hub, "invalidate_account", original)
    assert (
        client.patch(f"/api/admin/users/{target}", json={"disabled": True}).json()["disabled_at"]
        == timestamp
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": "target@example.com", "password": "password123"}
        ).status_code
        == 401
    )
    assert client.patch(f"/api/admin/users/{target}", json={"disabled": False}).status_code == 200
    assert get_session_account(old_token) is None
    assert (
        client.post(
            "/api/auth/login", json={"email": "target@example.com", "password": "password123"}
        ).status_code
        == 200
    )


def test_login_rechecks_after_verification(
    admin_api: tuple[TestClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import auth

    client, _, target = admin_api
    verify = verify_login

    def suspend_after_verify(email: str, password: str) -> Any:
        account = verify(email, password)
        set_disabled(target, True, admin_ids=frozenset())
        return account

    monkeypatch.setattr(auth, "verify_login", suspend_after_verify)
    response = client.post(
        "/api/auth/login", json={"email": "target@example.com", "password": "password123"}
    )
    assert response.status_code == 401 and "set-cookie" not in response.headers


def test_unexpected_list_failure_private(
    admin_api: tuple[TestClient, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.store import admin

    def fail(**kwargs: Any) -> Any:
        raise RuntimeError("private internals")

    monkeypatch.setattr(admin, "list_users", fail)
    response = admin_api[0].get("/api/admin/users")
    assert response.status_code == 500 and response.headers["cache-control"] == "no-store"
    assert "private internals" not in response.text


def test_disabled_caller_and_open_registration_remain_account_scoped(
    admin_api: tuple[TestClient, str, str],
) -> None:
    client, _, target = admin_api
    campaign = create_campaign(
        target, title="Suspended world", description="", theme="High Fantasy", custom_lore=""
    )
    token = create_session(target)[0]
    client.patch(f"/api/admin/users/{target}", json={"disabled": True})
    client.cookies.set("mythoscircle_session", token, path="/api")
    assert client.get("/api/admin/users").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    response = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "password123"},
    )
    assert response.status_code == 201 and response.json()["is_admin"] is False
    assert response.json()["id"] != target
    assert client.get(f"/api/campaigns/{campaign.id}").status_code == 404
    assert client.get("/api/campaigns").json()["campaigns"] == []


def test_database_failure_does_not_evict(
    admin_api: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.ws import hub
    from app.store import admin

    client, _, target = admin_api
    calls: list[str] = []

    async def cleanup(account_id: str) -> None:
        calls.append(account_id)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database failure")

    token = create_session(target)[0]
    monkeypatch.setattr(admin, "set_disabled", fail)
    monkeypatch.setattr(hub, "invalidate_account", cleanup)
    response = client.patch(f"/api/admin/users/{target}", json={"disabled": True})
    assert response.status_code == 500 and calls == []
    assert get_session_account(token) is not None


def test_registration_rejects_privilege_flags(admin_api: tuple[TestClient, str, str]) -> None:
    response = admin_api[0].post(
        "/api/auth/register",
        json={
            "email": "privilege@example.com",
            "password": "password123",
            "is_admin": True,
        },
    )
    assert response.status_code == 422


def test_cursor_scope_anchor_and_encoding(admin_api: tuple[TestClient, str, str]) -> None:
    from app.core.pagination import encode_cursor

    client, admin, _ = admin_api
    for params in [
        {"cursor": encode_cursor(new_id())},
        {"cursor": encode_cursor(admin), "q": "target"},
        {"cursor": encode_cursor(admin) + "!!!"},
    ]:
        assert client.get("/api/admin/users", params=params).status_code == 422


def test_disable_closes_all_registered_campaign_sockets(
    admin_api: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from starlette.websockets import WebSocketDisconnect

    from app.api.ws import hub

    client, admin, target = admin_api
    campaigns = [
        create_campaign(
            target, title=f"World {index}", description="", theme="High Fantasy", custom_lore=""
        )
        for index in range(2)
    ]
    registered = threading.Event()
    original = hub.register
    count = 0

    def register(*args: Any, **kwargs: Any) -> None:
        nonlocal count
        original(*args, **kwargs)
        count += 1
        if count == 2:
            registered.set()

    monkeypatch.setattr(hub, "register", register)
    client.cookies.set("mythoscircle_session", create_session(target)[0], path="/api")
    with (
        client.websocket_connect(f"/api/ws/jobs?campaign_id={campaigns[0].id}") as first,
        client.websocket_connect(f"/api/ws/jobs?campaign_id={campaigns[1].id}") as second,
    ):
        assert registered.wait(timeout=5)
        client.cookies.set("mythoscircle_session", create_session(admin)[0], path="/api")
        assert (
            client.patch(f"/api/admin/users/{target}", json={"disabled": True}).status_code == 200
        )
        for connection in (first, second):
            with pytest.raises(WebSocketDisconnect) as disconnected:
                connection.receive_json()
            assert disconnected.value.code == 4401


def test_session_committed_before_disable_is_revoked_even_before_login_returns(
    admin_api: tuple[TestClient, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import auth

    client, _, target = admin_api
    original = create_session

    def create_then_suspend(account_id: str) -> Any:
        result = original(account_id)
        set_disabled(target, True, admin_ids=frozenset())
        return result

    monkeypatch.setattr(auth, "create_session", create_then_suspend)
    response = client.post(
        "/api/auth/login", json={"email": "target@example.com", "password": "password123"}
    )
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
