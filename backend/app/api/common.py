"""Shared API helpers (AD-17 envelope contract).

``store_error_as_http`` is the single mapping from a store rejection to
its error envelope. Every API router that calls into ``app.store`` routes
its store rejections through this one mapper, so the wire codes stay
consistent across surfaces (spec-1.4, spec-1.6, spec-2.1 retro item 3).
"""

from typing import NoReturn

from fastapi import HTTPException

from app.core.errors import StoreHTTPException
from app.core.pagination import InvalidCursorError
from app.store import (
    CampaignInputError,
    CandidateNotFoundError,
    CandidateSettledError,
    CrossCampaignConflictError,
    DanglingEdgeError,
    DuplicateEdgeError,
    DuplicateEntityError,
    DuplicateJobError,
    EdgeRetargetError,
    EmptySubgraphError,
    EntityEditConflictError,
    InvalidCandidateError,
    InvalidEdgeCounterError,
    InvalidEdgeTypeError,
    InvalidEntityRecordError,
    InvalidJobInputError,
    InvalidMediaError,
    InvalidThemeError,
    InvalidUlidError,
    JobNotFoundError,
    JobStateConflictError,
    LiveEdgesError,
    MediaNotFoundError,
    OrphanEntityError,
    QueueFullError,
    SelfLoopEdgeError,
    StaleRevisionError,
    UnknownCampaignError,
    UnknownEdgeError,
    UnknownEntityError,
)
from app.store.commit import (
    BlankEdgeReasonError,
    EdgeKindViolationError,
    InvalidRunStateError,
)
from app.store.journal import (
    JournalConflictError,
    JournalInputError,
    JournalNotFoundError,
    JournalUnicodeError,
)

__all__ = ["StoreHTTPException", "store_error_as_http"]


def store_error_as_http(exc: Exception) -> NoReturn:
    """Map a store rejection to its envelope HTTPException (4xx = user error).

    The rejections are 404/409/422 — the same codes the I/O matrices pin,
    with machine-readable envelope codes from app.core.errors. Unknown
    exceptions are re-raised (5xx via the catch-all handler; nothing
    internal leaks). ``CorruptEventError`` is deliberately NOT mapped
    here: a structurally malformed event log is internal corruption, not
    user error — re-raising surfaces it as a 500 so it can never be
    mistaken for a recoverable client mistake.
    """
    if isinstance(exc, JournalUnicodeError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            JobNotFoundError,
            CandidateNotFoundError,
            UnknownCampaignError,
            UnknownEntityError,
            UnknownEdgeError,
            MediaNotFoundError,
            JournalNotFoundError,
        ),
    ):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            JobStateConflictError,
            CandidateSettledError,
            DuplicateJobError,
            QueueFullError,
            StaleRevisionError,
            CrossCampaignConflictError,
            DuplicateEdgeError,
            LiveEdgesError,
            EntityEditConflictError,
            JournalConflictError,
        ),
    ):
        if isinstance(exc, LiveEdgesError):
            # AD-5: the DM must see the affected neighbors before
            # confirming — the listing rides the envelope's ``details``.
            # Wire schema (consumed by 2.7's world view and Epic 3's
            # accept path):
            #   details.affected_entities: list of {"id": <ULID>,
            #     "name": <entity name>}, rowid-ordered, deduplicated
            #     per neighbor;
            #   details.entity_id: the delete target's ULID.
            raise StoreHTTPException(
                status_code=409,
                detail=str(exc),
                details={"affected_entities": exc.affected, "entity_id": exc.entity_id},
            ) from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            InvalidJobInputError,
            InvalidCandidateError,
            InvalidThemeError,
            CampaignInputError,
            InvalidCursorError,
            EdgeRetargetError,
            InvalidUlidError,
            InvalidMediaError,
            DanglingEdgeError,
            InvalidEdgeCounterError,
            InvalidEdgeTypeError,
            DuplicateEntityError,
            EmptySubgraphError,
            OrphanEntityError,
            SelfLoopEdgeError,
            InvalidEntityRecordError,
            BlankEdgeReasonError,
            EdgeKindViolationError,
            InvalidRunStateError,
            JournalInputError,
        ),
    ):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc
