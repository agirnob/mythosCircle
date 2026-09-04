"""Proposed-candidate reads and lifecycle (AR7, AR19; spec-3.1, 3.2).

One authed, owner-only read over the staging table plus the synchronous
owner-only accept/reject POSTs. The accept screen (story 3.3) consumes
it. Proposed rows are never world state (AR7), so the read is
independent of export and ``world_state`` reads. Accept is a thin route
over the store's one-transaction commit (mirroring ``delete_entity``);
reject only settles the row. A foreign or unknown campaign is the
single indistinguishable 404 (the campaign route pattern — no oracle,
auth conventions).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.store import (
    PROPOSAL_STATUS,
    STATUS_PROPOSED,
    StoreError,
    accept_candidate,
    get_campaign,
    list_candidates,
    models,
    reject_candidate,
)

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


def _candidate_response(candidate: models.ProposedCandidate) -> CandidateResponse:
    """The wire shape of one staged row (shared by the list and the
    lifecycle transitions)."""
    return CandidateResponse(
        id=candidate.id,
        campaign_id=candidate.campaign_id,
        job_id=candidate.job_id,
        kind=candidate.kind,
        status=candidate.status,
        payload=candidate.payload,
        created_at=candidate.created_at,
    )


@router.get("/api/campaigns/{campaign_id}/candidates")
def list_campaign_candidates(
    campaign_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    status: str = Query(default=STATUS_PROPOSED),
) -> CandidateListResponse:
    """The campaign's staged proposed candidates, oldest first (AR7).

    Owner-only: unauthenticated is a 401; a foreign or unknown campaign
    is the single indistinguishable 404. Cursor-paginated per the list
    conventions; a fabricated cursor is a 422 (epic-1 retro item 2).
    ``status`` filters the closed lifecycle set and defaults to
    ``proposed`` — settled rows must not regress the accept-screen read.
    """
    if status not in PROPOSAL_STATUS:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {sorted(PROPOSAL_STATUS)}.",
        )
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
        candidates, next_cursor = list_candidates(campaign_id, after, limit, status)
    except StoreError as exc:
        store_error_as_http(exc)
    return CandidateListResponse(
        candidates=[_candidate_response(candidate) for candidate in candidates],
        next_cursor=encode_cursor(next_cursor) if next_cursor is not None else None,
    )


@router.post("/api/campaigns/{campaign_id}/candidates/{candidate_id}/accept")
def accept_campaign_candidate(
    campaign_id: str,
    candidate_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> CandidateResponse:
    """Commit the candidate's subgraph as one atomic transaction and
    settle the row ``accepted`` (spec-3.2, FR11/AD-15).

    Owner-first, mirroring ``delete_entity``: a foreign or unknown
    campaign is the 404 before anything else is touched. Thin route:
    every decision (fresh-ULID entity, staged edges, base revision,
    one-transaction atomicity, dead-endpoint rejection) is the store's.
    """
    if get_campaign(current.id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    try:
        candidate, _revision = accept_candidate(campaign_id, candidate_id)
    except StoreError as exc:
        store_error_as_http(exc)
    return _candidate_response(candidate)


@router.post("/api/campaigns/{campaign_id}/candidates/{candidate_id}/reject")
def reject_campaign_candidate(
    campaign_id: str,
    candidate_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> CandidateResponse:
    """Settle the candidate ``rejected`` — the world is untouched (AR7).

    Owner-first like the accept route; the store flips the status in one
    transaction with no revision, no event, and no world read.
    """
    if get_campaign(current.id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    try:
        candidate = reject_candidate(campaign_id, candidate_id)
    except StoreError as exc:
        store_error_as_http(exc)
    return _candidate_response(candidate)


__all__ = ["router"]
