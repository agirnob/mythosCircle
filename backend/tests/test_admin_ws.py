"""Deterministic suspension/handshake/broadcast races."""

import asyncio
import threading
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import WebSocket

from app.api import ws
from app.store import app_db_url, create_campaign, init_db, models
from app.store.admin import set_disabled
from app.store.auth import create_session, get_session_account, register_account


class Socket:
    def __init__(self, token: str = "") -> None:
        self.cookies = {"mythoscircle_session": token}
        self.messages: list[Any] = []
        self.codes: list[int] = []
        self.closing = asyncio.Event()
        self.release_close = asyncio.Event()
        self.slow = False

    async def accept(self) -> None:
        pass

    async def send_json(self, message: Any) -> None:
        self.messages.append(message)

    async def close(self, code: int = 1000) -> None:
        self.codes.append(code)
        self.closing.set()
        if self.slow:
            await self.release_close.wait()


def test_detachment_precedes_bounded_close_and_blocks_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        hub = ws.JobHub()
        socket = Socket()
        socket.slow = True
        transport = cast(WebSocket, socket)
        hub.register(transport, "campaign", "account")
        job = models.Job(id="job", campaign_id="campaign", state="queued")
        original = ws._build_message

        def snapshot_then_detach(
            event: str, job: models.Job, position: int | None
        ) -> dict[str, Any]:
            hub.unregister(transport, "campaign")
            return original(event, job, position)

        monkeypatch.setattr(ws, "_build_message", snapshot_then_detach)
        await hub._broadcast("queue_changed", job, None)
        assert socket.messages == []
        hub.register(transport, "campaign", "account")
        cleanup = asyncio.create_task(hub.invalidate_account("account"))
        await socket.closing.wait()
        assert transport not in hub._subscribers.get("campaign", ())
        await hub._broadcast("queue_changed", job, None)
        assert socket.messages == []
        # Timeout must complete despite a peer that never finishes close.
        await asyncio.wait_for(cleanup, timeout=2)
        assert socket.codes == [4401]
        assert not hub._identities

    asyncio.run(scenario())


def test_handshake_authenticated_before_disable_cannot_register(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'race.db'}")
    try:
        account = register_account("race@example.com", "password123")
        token, _ = create_session(account.id)
        campaign = create_campaign(
            account.id, title="Race", description="", theme="High Fantasy", custom_lore=""
        )
        authenticated = threading.Event()
        resume = threading.Event()

        def resolve(raw: str) -> Any:
            result = get_session_account(raw)
            if not authenticated.is_set():
                authenticated.set()
                assert resume.wait(timeout=5)
            return result

        monkeypatch.setattr(ws, "get_session_account", resolve)
        hub = ws.JobHub()
        monkeypatch.setattr(ws, "hub", hub)

        async def scenario() -> None:
            socket = Socket(token)
            handshake = asyncio.create_task(ws.jobs_ws(cast(WebSocket, socket), campaign.id))
            assert await asyncio.to_thread(authenticated.wait, 5)
            async with hub.account_lock(account.id):
                await asyncio.to_thread(set_disabled, account.id, True, admin_ids=frozenset())
                await hub.invalidate_account(account.id)
            resume.set()
            await asyncio.wait_for(handshake, 5)
            assert socket.codes == [4401]
            assert not hub._subscribers

        asyncio.run(scenario())
    finally:
        init_db(previous)


def test_restore_waits_for_disable_transport_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from fastapi import Request, Response

    from app.api import admin as admin_api
    from app.store.admin import list_users

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'serialization.db'}")
    try:
        account = register_account("serialized@example.com", "password123")
        hub = ws.JobHub()
        monkeypatch.setattr(admin_api, "hub", hub)
        request = cast(
            Request,
            SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(admin_account_ids=frozenset()))
            ),
        )

        async def scenario() -> None:
            socket = Socket()
            socket.slow = True
            hub.register(cast(WebSocket, socket), "campaign", account.id)
            restore_waiting = asyncio.Event()
            original_lock = hub.account_lock
            calls = 0

            def lock(account_id: str) -> asyncio.Lock:
                nonlocal calls
                calls += 1
                if calls == 2:
                    restore_waiting.set()
                return original_lock(account_id)

            monkeypatch.setattr(hub, "account_lock", lock)
            disable = asyncio.create_task(
                admin_api.set_status(
                    account.id,
                    admin_api.AccountStatusRequest(disabled=True),
                    request,
                    Response(),
                    account,
                )
            )
            await socket.closing.wait()
            restore = asyncio.create_task(
                admin_api.set_status(
                    account.id,
                    admin_api.AccountStatusRequest(disabled=False),
                    request,
                    Response(),
                    account,
                )
            )
            await restore_waiting.wait()
            assert not restore.done()
            users, _ = await asyncio.to_thread(list_users, admin_ids=frozenset())
            assert users[0].disabled_at is not None
            socket.release_close.set()
            disabled, restored = await asyncio.gather(disable, restore)
            assert disabled.disabled_at is not None and restored.disabled_at is None
            users, _ = await asyncio.to_thread(list_users, admin_ids=frozenset())
            assert users[0].disabled_at is None

        asyncio.run(scenario())
    finally:
        init_db(previous)


@pytest.mark.parametrize("phase", ["accept", "deny-accept", "deny-close"])
def test_hung_handshake_transport_releases_coordinator_without_registering(
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    account = models.Account(id="account")
    monkeypatch.setattr(ws, "get_session_account", lambda token: account)
    monkeypatch.setattr(ws, "get_campaign", lambda *args: object() if phase == "accept" else None)
    monkeypatch.setattr(ws, "_SOCKET_TRANSPORT_TIMEOUT_SECONDS", 0.05)
    hub = ws.JobHub()
    monkeypatch.setattr(ws, "hub", hub)

    async def scenario() -> None:
        class HungSocket(Socket):
            def __init__(self) -> None:
                super().__init__("credential-must-not-be-logged")
                self.accepting = asyncio.Event()
                self.forever = asyncio.Event()

            async def accept(self) -> None:
                self.accepting.set()
                if phase in ("accept", "deny-accept"):
                    await self.forever.wait()

            async def close(self, code: int = 1000) -> None:
                self.closing.set()
                await self.forever.wait()

        socket = HungSocket()
        handshake = asyncio.create_task(ws.jobs_ws(cast(WebSocket, socket), "campaign"))
        await socket.accepting.wait()
        if phase == "deny-close":
            await socket.closing.wait()
        assert hub.account_lock(account.id).locked()
        await asyncio.wait_for(handshake, timeout=1)
        assert not hub._subscribers and not hub._identities
        async with asyncio.timeout(0.1), hub.account_lock(account.id):
            pass

    asyncio.run(scenario())
    assert "timed out" in caplog.text
    assert "credential-must-not-be-logged" not in caplog.text


def test_other_account_can_subscribe_and_receive_during_target_cleanup() -> None:
    async def scenario() -> None:
        hub = ws.JobHub()
        target = Socket()
        target.slow = True
        hub.register(cast(WebSocket, target), "campaign", "target")
        other = Socket()
        hub.register(cast(WebSocket, other), "campaign", "other")
        cleanup = asyncio.create_task(hub.invalidate_account("target"))
        await target.closing.wait()
        newcomer = Socket()
        hub.register(cast(WebSocket, newcomer), "campaign", "newcomer")
        await hub._broadcast(
            "queue_changed", models.Job(id="job", campaign_id="campaign", state="queued"), None
        )
        assert target.messages == [] and len(other.messages) == 1
        assert len(newcomer.messages) == 1 and newcomer.codes == []
        assert other.codes == []
        assert cast(WebSocket, other) in hub._subscribers["campaign"]
        assert hub._identities[cast(WebSocket, other)] == ("other", "campaign")
        target.release_close.set()
        await cleanup
        assert other.codes == []
        assert cast(WebSocket, other) in hub._subscribers["campaign"]

    asyncio.run(scenario())
