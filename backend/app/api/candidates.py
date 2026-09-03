"""Proposed-candidate reads (AR7, AR19; spec-3.1).

One authed, owner-only read over the staging table: the accept screen
(story 3.3) consumes it. Proposed rows are never world state (AR7), so
this surface is independent of export and ``world_state`` reads. A
foreign or unknown campaign is the single indistinguishable 404 (the
campaign route pattern — no oracle, auth conventions).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.store import StoreError, get_campaign, list_candidates, models

router = APIRouter()


class CandidateResponse(BaseModel):
    """One staged proposed-candidate row."""

    id: str
    campaign_id: str
    job_id: str
    kind: str
    status: str
    #: The AR19 candidate record (name, role, personality, secret/rumor/
    #: party_hook, stat_block, edges) — extra keys tolerated (AR24).
    payload: dict[str, Any]
    created_at: str


class CandidateListResponse(BaseModel):
    """Cursor-paginated, campaign-scoped candidate list."""

    candidates: list[CandidateResponse]
    next_cursor: str | None


@router.get("/api/campaigns/{campaign_id}/candidates")
def list_campaign_candidates(
    campaign_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> CandidateListResponse:
    """The campaign's staged proposed candidates, oldest first (AR7).

    Owner-only: unauthenticated is a 401; a foreign or unknown campaign
    is the single indistinguishable 404. Cursor-paginated per the list
    conventions; a fabricated cursor is a 422 (epic-1 retro item 2).
    """
    campaign = get_campaign(current.id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    after = None
    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        candidates, next_cursor = list_candidates(campaign_id, after, limit)
    except StoreError as exc:
        store_error_as_http(exc)
    return CandidateListResponse(
        candidates=[
            CandidateResponse(
                id=candidate.id,
                campaign_id=candidate.campaign_id,
                job_id=candidate.job_id,
                kind=candidate.kind,
                status=candidate.status,
                payload=candidate.payload,
                created_at=candidate.created_at,
            )
            for candidate in candidates
        ],
        next_cursor=encode_cursor(next_cursor) if next_cursor is not None else None,
    )


__all__ = ["router"]
