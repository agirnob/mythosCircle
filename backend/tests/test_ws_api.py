"""WebSocket auth gate (AD-9): ``/api/ws/jobs`` is a private channel.

The handshake accepts first, then the session cookie is resolved to an
account and the campaign checked for ownership; any failure sends a 4401
close (fatal — the client must not reconnect) BEFORE the socket is ever
registered in the hub. Only an authenticated campaign owner receives job
broadcasts; a foreign campaign never leaks existence through the hub.

TestClient note: the server's denial close frame is queued BEHIND the
accept frame, so ``websocket_connect``'s ``__enter__`` still succeeds;
the test must ``receive`` to surface the pending close and its code.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.store import app_db_url, init_db


@pytest.fixture()
def ws_api(tmp_path: Path, client: TestClient) -> Iterator[None]:
    """Re-point the app's store at a fresh scratch DB.

    The store engine is module-global (store.db) and the test client's
    endpoints use it at call time, so re-pointing per test keeps auth and
    campaign rows deterministic (register/login + campaign API all land in
    the same scratch DB).
    """
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'ws-api.db'}")
    try:
        yield
    finally:
        init_db(previous)


@pytest.fixture(autouse=True)
def _reset_limiters() -> Any:
    """The auth limiters are module-global and keyed on the TestClient's
    fixed host — reset so registration quotas never leak between tests."""
    from app.api.auth import _login_limiter, _register_limiter

    _login_limiter.reset()
    _register_limiter.reset()
    yield
    _login_limiter.reset()
    _register_limiter.reset()


def _register_login(client: TestClient, email: str = "dm@example.com") -> None:
    client.post("/api/auth/register", json={"email": email, "password": "correct-battery-horse"})
    response = client.post(
        "/api/auth/login", json={"email": email, "password": "correct-battery-horse"}
    )
    assert response.status_code == 200


def _create_campaign(client: TestClient) -> str:
    """Create a campaign as the logged-in user; returns its id."""
    response = client.post(
        "/api/campaigns",
        json={
            "title": "WS Test World",
            "description": "",
            "theme": "High Fantasy",
            "custom_lore": "",
        },
    )
    assert response.status_code == 201
    campaign_id: str = response.json()["id"]
    return campaign_id


def test_ws_jobs_unauth_closed_4401(client: TestClient, ws_api: None) -> None:
    """No session cookie -> the socket is closed with 4401 (fatal, no
    reconnect) — the hub never registers the anonymous client. A fabricated
    campaign id is fine: the account check fails before the campaign check,
    and nothing about the session state may be observable to an anonymous
    client (AD-9)."""
    from app.core import ids

    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        client.websocket_connect(f"/api/ws/jobs?campaign_id={ids.new_id()}") as ws,
    ):
        ws.receive_json()  # surfaces the 4401 close sent right after accept
    assert excinfo.value.code == 4401


def test_ws_jobs_owned_campaign_connects(client: TestClient, ws_api: None) -> None:
    """An authenticated owner connects; a ping text frame does not disconnect
    the socket, and a submitted job still surfaces as a broadcast — proof the
    socket was registered in the hub."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    with client.websocket_connect(f"/api/ws/jobs?campaign_id={campaign_id}") as ws:
        ws.send_text("ping")
        response = client.post(
            "/api/jobs",
            json={"campaign_id": campaign_id, "kind": "text", "payload": {"ask": "who?"}},
        )
        assert response.status_code == 201
        message = ws.receive_json()
        assert message["type"] == "queue_changed"
        assert message["job_id"] == response.json()["id"]


def test_ws_jobs_foreign_campaign_closed_4401(client: TestClient, ws_api: None) -> None:
    """An authenticated client for ANOTHER owner's campaign is closed 4401 —
    ownership is enforced before registration (AD-9; no existence oracle via
    the hub)."""
    _register_login(client, "dm@example.com")
    campaign_id = _create_campaign(client)  # owned by dm
    _register_login(client, "other@example.com")  # other's session now in the jar
    with (
        pytest.raises(WebSocketDisconnect) as excinfo,
        client.websocket_connect(f"/api/ws/jobs?campaign_id={campaign_id}") as ws,
    ):
        ws.receive_json()  # surfaces the 4401 close sent right after accept
    assert excinfo.value.code == 4401
