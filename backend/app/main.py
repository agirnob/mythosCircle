"""Application entry point.

Run locally:
    uv run --directory backend uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from fastapi import FastAPI

from app.api import health
from app.core.errors import register_error_handlers
from app.store import init_app_db


def create_app() -> FastAPI:
    """Build the FastAPI application (app factory)."""
    application = FastAPI(title="mythosCircle API", version="0.1.0")
    register_error_handlers(application)
    init_app_db()  # world store: schema + WAL, idempotent
    application.include_router(health.router)
    return application


app = create_app()
