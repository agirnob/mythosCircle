"""WebSocket hub for job state broadcasts (AD-17).

One FastAPI process == single writer (AD-8, AD-13): the hub is an
in-process registry of campaign_id -> connected sockets. The store's
change-listener (registered at app-lifespan start) pushes every job
transition to the campaign's subscribers; the store never knows
websockets exist.

The listener may fire from any thread (REST handlers run in the
threadpool, 1.4's worker runs its own), so transitions cross threads
through a thread-safe queue drained by the event loop. The store passes
the queue_position already computed at transition time, so the drain
loop performs NO database work — it cannot block on the write lock,
tear down mid-broadcast, or deadlock against a transitioning writer.
The drain loop also never dies: a failed broadcast is logged and the
affected socket dropped, but the loop keeps delivering subsequent
events.

Auth gate (AD-9): the socket is a private channel — the handshake
resolves the session cookie and checks campaign ownership before
accepting. Any failure closes with 4401
(fatal, no reconnects) BEFORE the socket joins the hub; only an
authenticated owner is ever registered, so the hub never broadcasts to
or leaks existence for a foreign campaign.
"""

import asyncio
import contextlib
import logging
import queue
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.api.auth import COOKIE_NAME
from app.store import get_campaign, get_session_account, models
from app.store.jobs import (
    EVENT_JOB_PROGRESS,
    EVENT_QUEUE_CHANGED,
    set_change_listener,
)

logger = logging.getLogger(__name__)

router = APIRouter()
_SOCKET_TRANSPORT_TIMEOUT_SECONDS = 1.0


def _build_message(event: str, job: models.Job, queue_position: int | None) -> dict[str, Any]:
    """The AD-17 wire shape: {type, job_id, state, queue_position?, progress?}.

    ``queue_position`` was snapshotted at transition time by the store.
    """
    message: dict[str, Any] = {"type": event, "job_id": job.id, "state": job.state}
    if event == EVENT_JOB_PROGRESS:
        message["progress"] = job.progress
    if event in (EVENT_JOB_PROGRESS, EVENT_QUEUE_CHANGED) and queue_position is not None:
        message["queue_position"] = queue_position
    return message


class JobHub:
    """In-process broadcast hub keyed by campaign_id."""

    def __init__(self) -> None:
        # Bounded coordinator stripes: lock first, then store transaction.
        # Transactions always finish before transport I/O.
        self._account_locks = [asyncio.Lock() for _ in range(64)]
        self._identities: dict[WebSocket, tuple[str, str]] = {}
        self._subscribers: dict[str, set[WebSocket]] = defaultdict(set)
        self._pending: queue.SimpleQueue[tuple[str, models.Job, int | None]] = queue.SimpleQueue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wakeup: asyncio.Event | None = None
        self._drain_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Lifespan startup: register the store listener and start the drain task."""
        if self._drain_task is not None:
            return
        self._account_locks = [asyncio.Lock() for _ in range(64)]
        self._loop = asyncio.get_running_loop()
        wakeup = asyncio.Event()
        self._wakeup = wakeup
        self._drain_task = asyncio.create_task(self._drain(wakeup), name="jobs-ws-hub")
        set_change_listener(self._on_store_event)

    async def stop(self) -> None:
        """Lifespan shutdown: unregister the listener, drain remaining events,
        stop the drain task, and close any remaining sockets so clients see a
        clean disconnect."""
        set_change_listener(None)
        task = self._drain_task
        self._drain_task = None
        if task is not None:
            # The drain loop runs forever; cancel it and await its exit.
            # (wait_for against a never-ending task always times out.)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._loop = None
        self._wakeup = None
        self._pending = queue.SimpleQueue()
        for sockets in list(self._subscribers.values()):
            for socket in list(sockets):
                with contextlib.suppress(RuntimeError):
                    await socket.close()
        self._subscribers.clear()
        self._identities.clear()

    def _on_store_event(self, event: str, job: models.Job, queue_position: int | None) -> None:
        self._pending.put((event, job, queue_position))
        loop, wakeup = self._loop, self._wakeup
        if loop is not None and wakeup is not None:
            loop.call_soon_threadsafe(wakeup.set)

    async def _drain(self, wakeup: asyncio.Event) -> None:
        while True:
            await wakeup.wait()
            wakeup.clear()
            while True:
                try:
                    event, job, position = self._pending.get_nowait()
                except queue.Empty:
                    break
                try:
                    await self._broadcast(event, job, position)
                except Exception:
                    # A failed broadcast must never kill the drain task —
                    # one bad socket would silence every subscriber for
                    # the rest of the process.
                    logger.exception("job broadcast failed for job %s", job.id)

    async def _broadcast(self, event: str, job: models.Job, queue_position: int | None) -> None:
        sockets = list(self._subscribers.get(job.campaign_id, ()))
        if not sockets:
            return
        message = _build_message(event, job, queue_position)

        async def _send(socket: WebSocket) -> None:
            # A subscriber that stops reading must not wedge the drain (and
            # through it the store caller, which fires the listener
            # synchronously inside its commit).
            async def eligible_send() -> None:
                # Check inside the scheduled coroutine, immediately before send.
                if socket in self._subscribers.get(job.campaign_id, ()):
                    await socket.send_json(message)

            await asyncio.wait_for(eligible_send(), timeout=10.0)

        results = await asyncio.gather(
            *(_send(socket) for socket in sockets),
            return_exceptions=True,
        )
        for socket, result in zip(sockets, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("dropping unresponsive socket for campaign %s", job.campaign_id)
                self.unregister(socket, job.campaign_id)
                with contextlib.suppress(RuntimeError):
                    await socket.close()

    def account_lock(self, account_id: str) -> asyncio.Lock:
        return self._account_locks[hash(account_id) % len(self._account_locks)]

    async def invalidate_account(self, account_id: str) -> None:
        """Detach synchronously, then attempt bounded transport closure."""
        sockets = [
            (socket, campaign_id)
            for socket, (owner_id, campaign_id) in self._identities.items()
            if owner_id == account_id
        ]
        for socket, campaign_id in sockets:
            self.unregister(socket, campaign_id)

        async def close(socket: WebSocket) -> None:
            try:
                async with asyncio.timeout(_SOCKET_TRANSPORT_TIMEOUT_SECONDS):
                    await socket.close(code=4401)
            except Exception:
                logger.warning("detached job socket transport close failed or timed out")

        await asyncio.gather(*(close(socket) for socket, _ in sockets))

    def register(
        self,
        socket: WebSocket,
        campaign_id: str,
        account_id: str | None = None,
    ) -> None:
        self._subscribers[campaign_id].add(socket)
        if account_id is not None:
            self._identities[socket] = (account_id, campaign_id)

    def unregister(self, socket: WebSocket, campaign_id: str) -> None:
        self._identities.pop(socket, None)
        sockets = self._subscribers.get(campaign_id)
        if sockets is None:
            return
        sockets.discard(socket)
        if not sockets:
            del self._subscribers[campaign_id]


hub = JobHub()


async def _deny_socket(websocket: WebSocket) -> None:
    try:
        async with asyncio.timeout(_SOCKET_TRANSPORT_TIMEOUT_SECONDS):
            await websocket.accept()
            await websocket.close(code=4401)
    except Exception:
        logger.warning("job socket denial transport failed or timed out")


@router.websocket("/api/ws/jobs")
async def jobs_ws(websocket: WebSocket, campaign_id: str = Query(...)) -> None:
    """Subscribe to a campaign's job broadcasts (AD-17).

    Auth resolves before acceptance so a successful handshake is ready
    for broadcasts. Denials accept then close with 4401 without ever
    registering. Authentication is rechecked under the access-transition
    coordinator immediately before accepting and registering.

    The server only broadcasts; inbound frames are drained so clients can
    keep the connection alive with pings. Disconnects are tolerated and
    the socket is removed from the hub (and closed if it failed a send).
    """
    token = websocket.cookies.get(COOKIE_NAME)
    account = await run_in_threadpool(get_session_account, token) if token is not None else None
    if account is None or token is None:
        await _deny_socket(websocket)
        return
    async with hub.account_lock(account.id):
        # Resolve again under the transition lock: a handshake authenticated
        # before suspension must never register after its cleanup.
        current = await run_in_threadpool(get_session_account, token)
        campaign = await run_in_threadpool(get_campaign, account.id, campaign_id)
        if current is None or campaign is None:
            await _deny_socket(websocket)
            return
        # A successful handshake is ready to receive broadcasts immediately.
        try:
            # timeout() runs acceptance in this task, so registration has no
            # extra scheduling gap after the successful transport operation.
            async with asyncio.timeout(_SOCKET_TRANSPORT_TIMEOUT_SECONDS):
                await websocket.accept()
        except Exception:
            logger.warning("job socket acceptance failed or timed out")
            return
        hub.register(websocket, campaign_id, account.id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(websocket, campaign_id)
