"""Application entry point.

Run locally:
    uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (
    auth,
    campaigns,
    candidates,
    edges,
    entities,
    exports,
    health,
    jobs,
    media,
    ws,
)
from app.core.errors import register_error_handlers
from app.core.logging_setup import setup_logging
from app.pipeline.worker import worker_loop
from app.store import init_app_db
from app.store.jobs import recover_stale_running

#: Set to "1" in the test environment to disable the background worker.
TESTING_ENV = "MYTHOSCIRCLE_TESTING"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start the queue's WebSocket broadcaster and the queue worker.

    The hub must be up before the worker claims anything — completions
    broadcast immediately. Teardown stops the worker first (an in-flight
    job finishes; the stop event is checked between jobs), then the hub.

    Under ``MYTHOSCIRCLE_TESTING=1`` the worker is NOT started: the
    contract tests drive transitions deterministically themselves, and an
    uncontrolled 0.2s drainer would race their exact-frame assertions
    (review round 1). The worker's behavior is covered by its own tests.
    """
    await ws.hub.start()
    worker_stop = asyncio.Event()
    worker = None
    if os.environ.get(TESTING_ENV) != "1":
        worker = asyncio.create_task(worker_loop(worker_stop), name="jobs-worker")
    try:
        yield
    finally:
        if worker is not None:
            worker_stop.set()
            await asyncio.gather(worker, return_exceptions=True)
        await ws.hub.stop()


def create_app() -> FastAPI:
    """Build the FastAPI application (app factory)."""
    application = FastAPI(title="mythosCircle API", version="0.1.0", lifespan=lifespan)
    setup_logging()  # JSON-lines file logging first — every handler logs structured
    register_error_handlers(application)
    init_app_db()  # world store: schema + WAL, idempotent
    recover_stale_running()  # AR11: re-queue a crashed worker's running job
    application.include_router(auth.router)
    application.include_router(campaigns.router)
    application.include_router(health.router)
    application.include_router(jobs.router)
    application.include_router(ws.router)
    application.include_router(entities.router)
    application.include_router(edges.router)
    application.include_router(exports.router)
    application.include_router(candidates.router)
    application.include_router(media.router)
    return application


app = create_app()
