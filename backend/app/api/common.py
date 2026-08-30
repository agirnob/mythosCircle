"""Shared API helpers (AD-17 envelope contract).

``_store_error_as_http`` is the single mapping from a store rejection to
its error envelope. Every API router that calls into ``app.store`` routes
its store rejections through this one mapper, so the wire codes stay
consistent across surfaces (spec-1.4, spec-1.6, spec-2.1 retro item 3).
"""

from typing import NoReturn

from fastapi import HTTPException

from app.core.pagination import InvalidCursorError
from app.store import (
    CampaignInputError,
    DuplicateJobError,
    InvalidJobInputError,
    InvalidThemeError,
    JobNotFoundError,
    JobStateConflictError,
    QueueFullError,
    UnknownCampaignError,
)


def _store_error_as_http(exc: Exception) -> NoReturn:
    """Map a store rejection to its envelope HTTPException (4xx = user error).

    The rejections are 409/404/422 — the same codes the I/O matrices pin,
    with machine-readable envelope codes from app.core.errors. Unknown
    exceptions are re-raised (5xx via the catch-all handler; nothing
    internal leaks).
    """
    if isinstance(exc, (JobNotFoundError, UnknownCampaignError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (JobStateConflictError, DuplicateJobError, QueueFullError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(
        exc, (InvalidJobInputError, InvalidThemeError, CampaignInputError, InvalidCursorError)
    ):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


__all__ = ["_store_error_as_http"]
