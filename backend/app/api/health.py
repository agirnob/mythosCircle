"""Liveness probe — the first runnable HTTP surface."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, str]:
    """Return the service health status."""
    return {"status": "ok"}
