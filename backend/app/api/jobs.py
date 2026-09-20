"""REST surface of the persistent generation queue (AD-17).

Submission and status are REST; progress/queue position also stream
over the WebSocket hub (``app.api.ws``). Pydantic schemas are the wire
contract; store rejections map to the error envelope — 4xx = user error,
never a state change.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.store import (
    JobNotFoundError,
    StoreError,
    cancel_job,
    enqueue_job,
    get_campaign,
    job_status,
    list_jobs,
    models,
)

router = APIRouter()


class JobCreate(BaseModel):
    """POST /api/jobs body — the job-submission wire contract."""

    campaign_id: str
    kind: Literal[
        "text",
        "image",
        "video",
        "video_prompt",
        "build_in",
        "generate",
        "regenerate",
        "add_character",
    ]
    payload: dict[str, Any]
    # Idempotency key (conventions.md): retries reuse the same job_id; a
    # duplicate is rejected with 409, never double-enqueued.
    job_id: str | None = None
    max_llm_calls: int | None = Field(default=None, ge=0)
    max_media_calls: int | None = Field(default=None, ge=0)


class JobResponse(BaseModel):
    """A job row plus its per-campaign queue position."""

    id: str
    campaign_id: str
    kind: str
    payload: dict[str, Any]
    state: str
    progress: float
    max_llm_calls: int
    max_media_calls: int
    error: str | None
    result: dict[str, Any] | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    queue_position: int | None


class JobListResponse(BaseModel):
    """Cursor-paginated, campaign-scoped job list."""

    jobs: list[JobResponse]
    next_cursor: str | None


def _to_response(job: models.Job, position: int | None) -> JobResponse:
    return JobResponse(
        id=job.id,
        campaign_id=job.campaign_id,
        kind=job.kind,
        payload=job.payload,
        state=job.state,
        progress=job.progress,
        max_llm_calls=job.max_llm_calls,
        max_media_calls=job.max_media_calls,
        error=job.error,
        result=job.result,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        queue_position=position,
    )


def _require_campaign(current_id: str, campaign_id: str) -> None:
    """Ownership-404-first (the characters/edges precedent, AD-9): a
    foreign or unknown campaign is the single 404 — no worker is ever
    asked to touch a campaign its caller does not own and no message
    distinguishes the two (no-oracle rule)."""
    if get_campaign(current_id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")


def _require_owned_job(current_id: str, job: models.Job) -> None:
    """The get/cancel twin of ``_require_campaign`` (spec-6-4).

    The store is owner-blind by design — workers claim jobs by id with no
    account (AD-3) — so the route resolves the job first, then gates its
    campaign. A job whose campaign the caller does not own is raised as
    ``JobNotFoundError``: byte-identical to a genuinely unknown job's 404,
    so the endpoint yields no oracle one level down (the same rule
    exports.py applies to entities)."""
    if get_campaign(current_id, job.campaign_id) is None:
        raise JobNotFoundError(job.id)


@router.post("/api/jobs", status_code=201)
def create_job(
    payload: JobCreate,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> JobResponse:
    """Enqueue a job at the FIFO tail; idempotent by job_id (AD-3, AR28).

    Ownership (spec-6-4): the caller's session must own the campaign —
    foreign and unknown campaigns are the same 404 raised BEFORE the
    store ever runs (the characters ``_require_campaign`` precedent)."""
    _require_campaign(current.id, payload.campaign_id)
    try:
        job = enqueue_job(
            payload.campaign_id,
            payload.kind,
            payload.payload,
            job_id=payload.job_id,
            max_llm_calls=payload.max_llm_calls,
            max_media_calls=payload.max_media_calls,
        )
        job, position = job_status(job.id)
    except StoreError as exc:
        store_error_as_http(exc)
    return _to_response(job, position)


@router.get("/api/jobs/{job_id}")
def get_job(
    job_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> JobResponse:
    """One job plus its current queue position (STATUS_POSITION).

    Ownership (spec-6-4): there is no campaign_id to gate on, so the
    route resolves the job first (store is owner-blind, AD-3), then gates
    its campaign — a foreign job's 404 is byte-identical to a genuinely
    unknown job's (``_require_owned_job``, no-oracle rule)."""
    try:
        job, position = job_status(job_id)
        _require_owned_job(current.id, job)
    except StoreError as exc:
        store_error_as_http(exc)
    return _to_response(job, position)


@router.get("/api/jobs")
def list_campaign_jobs(
    current: Annotated[models.Account, Depends(get_current_account)],
    campaign_id: str = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> JobListResponse:
    """Campaign-scoped FIFO list, oldest first, cursor-paginated (AD-9).

    Ownership (spec-6-4): the ownership 404 fires before cursor decoding
    — a stranger learns nothing, not even via the cursor error surface."""
    _require_campaign(current.id, campaign_id)
    after = None
    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        jobs, next_cursor = list_jobs(campaign_id, after, limit)
    except StoreError as exc:
        store_error_as_http(exc)
    return JobListResponse(
        jobs=[_to_response(job, position) for job, position in jobs],
        next_cursor=encode_cursor(next_cursor) if next_cursor is not None else None,
    )


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job_route(
    job_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> JobResponse:
    """Cancel a queued or running job; frees the queue slot (AR28).

    Ownership (spec-6-4): the ownership check runs BEFORE any mutation —
    a foreign cancel is a byte-identical 404 and the job's state and row
    are untouched (resolve-then-gate, then cancel)."""
    try:
        job, _position = job_status(job_id)
        _require_owned_job(current.id, job)
        job = cancel_job(job_id)
        job, position = job_status(job.id)
    except StoreError as exc:
        store_error_as_http(exc)
    return _to_response(job, position)
