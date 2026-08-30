"""REST surface of the persistent generation queue (AD-17).

Submission and status are REST; progress/queue position also stream
over the WebSocket hub (``app.api.ws``). Pydantic schemas are the wire
contract; store rejections map to the error envelope — 4xx = user error,
never a state change.
"""

from typing import Any, Literal, NoReturn

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.store import (
    DuplicateJobError,
    InvalidJobInputError,
    JobNotFoundError,
    JobStateConflictError,
    QueueFullError,
    StoreError,
    UnknownCampaignError,
    cancel_job,
    enqueue_job,
    job_status,
    list_jobs,
    models,
)

router = APIRouter()


class JobCreate(BaseModel):
    """POST /api/jobs body — the job-submission wire contract."""

    campaign_id: str
    kind: Literal["text", "image", "video"]
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


def _store_error_as_http(exc: Exception) -> NoReturn:
    """Map a store rejection to its envelope HTTPException (4xx = user error).

    The state machine rejects are 409/404; malformed input is 422 — the
    same codes the I/O matrix pins, with machine-readable envelope codes
    from app.core.errors.
    """
    if isinstance(exc, (JobNotFoundError, UnknownCampaignError)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (JobStateConflictError, DuplicateJobError, QueueFullError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, InvalidJobInputError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


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


@router.post("/api/jobs", status_code=201)
def create_job(payload: JobCreate) -> JobResponse:
    """Enqueue a job at the FIFO tail; idempotent by job_id (AD-3, AR28)."""
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
        _store_error_as_http(exc)
    return _to_response(job, position)


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> JobResponse:
    """One job plus its current queue position (STATUS_POSITION)."""
    try:
        job, position = job_status(job_id)
    except JobNotFoundError as exc:
        _store_error_as_http(exc)
    return _to_response(job, position)


@router.get("/api/jobs")
def list_campaign_jobs(
    campaign_id: str = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> JobListResponse:
    """Campaign-scoped FIFO list, oldest first, cursor-paginated (AD-9)."""
    after = None
    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        jobs, next_cursor = list_jobs(campaign_id, after, limit)
    except StoreError as exc:
        _store_error_as_http(exc)
    return JobListResponse(
        jobs=[_to_response(job, position) for job, position in jobs],
        next_cursor=encode_cursor(next_cursor) if next_cursor is not None else None,
    )


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job_route(job_id: str) -> JobResponse:
    """Cancel a queued or running job; frees the queue slot (AR28)."""
    try:
        job = cancel_job(job_id)
        job, position = job_status(job.id)
    except StoreError as exc:
        _store_error_as_http(exc)
    return _to_response(job, position)
