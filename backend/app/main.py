"""Application entry point.

Run locally:
    uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, jobs, ws
from app.core.errors import register_error_handlers
from app.store import init_app_db
from app.store.jobs import recover_stale_running


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Register the queue's WebSocket broadcaster; tear it down on exit."""
    await ws.hub.start()
    yield
    await ws.hub.stop()


def create_app() -> FastAPI:
    """Build the FastAPI application (app factory)."""
    application = FastAPI(title="mythosCircle API", version="0.1.0", lifespan=lifespan)
    register_error_handlers(application)
    init_app_db()  # world store: schema + WAL, idempotent
    recover_stale_running()  # AR11: re-queue a crashed worker's running job
    application.include_router(health.router)
    application.include_router(jobs.router)
    application.include_router(ws.router)
    return application


app = create_app()
