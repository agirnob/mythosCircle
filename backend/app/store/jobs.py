"""Persistent generation queue (AD-3, AR11, AR28).

The second substrate piece after the world store (Story 1.2): one FIFO
across all campaigns, persisted in the store — no in-memory queue, so
the pending work survives a restart (AR11). Issuance order is SQLite
``rowid`` — the same monotonic clock as revisions/events, never ULID
string order (ties on the random suffix).

Invariants:
- Exactly one job runs at a time, enforced at claim time (AD-3):
  ``claim_next_job`` refuses while any job is ``running``; concurrent
  claims serialize on the ``BEGIN IMMEDIATE`` begin-listener
  (``store.db``) — no separate lock table.
- Jobs are NOT world graph (AD-1 governs the graph only): job rows and
  transitions never touch the event log or revisions.
- Per-campaign pending cap (AR28): enqueue is rejected when the
  campaign's pending (queued + running) count is at the cap — zero rows
  written. The cap and the per-job call budgets come from
  ``app.core.settings`` (env, defaults per the 1.3 spec); budget
  *enforcement* is Story 1.4 (AR21) — here they are only stored.
- Caller-supplied ``job_id`` must be a ULID and is idempotent by
  job-id: a duplicate is a structured rejection, never a double-enqueue.

Every mutation runs inside ``session_scope`` (BEGIN IMMEDIATE). One
change-listener (registered by the API layer) is notified after each
committed transition with an AD-17 event name — the store knows nothing
about websockets; 1.4's worker drives the same primitives.
"""

import json
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from app.core import ids, time
from app.core.ids import CROCKFORD_ALPHABET
from app.core.settings import queue_settings
from app.store import models
from app.store.commit import StoreError, UnknownCampaignError
from app.store.db import session_scope

#: Closed job-kind set (AD-3 runner split: text -> pipeline, image/video
#: -> media). ``build-in`` lands as a text-kind payload variant (later
#: stories), not as a new kind.
JOB_KINDS: frozenset[str] = frozenset({"text", "image", "video"})

#: Closed job-state set; transitions are only ever driven by the store
#: primitives below.
JOB_STATES: frozenset[str] = frozenset({"queued", "running", "succeeded", "failed", "cancelled"})

#: Non-terminal states: these occupy the per-campaign pending cap (AR28).
PENDING_STATES: frozenset[str] = frozenset({"queued", "running"})

#: Terminal states: never transition again — complete/fail/cancel reject them.
TERMINAL_STATES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

#: Default page size for ``list_jobs``.
DEFAULT_LIST_LIMIT = 50

#: Change-listener event names — the AD-17 WS types the API layer maps 1:1.
EVENT_QUEUE_CHANGED = "queue_changed"
EVENT_JOB_PROGRESS = "job_progress"
EVENT_JOB_DONE = "job_done"
EVENT_JOB_FAILED = "job_failed"
EVENT_JOB_CANCELLED = "job_cancelled"


# ---------------------------------------------------------------------------
# Structured job errors (FastAPI mapping lives in app.api.jobs)
# ---------------------------------------------------------------------------


class JobNotFoundError(StoreError):
    """Unknown job id (-> 404)."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"unknown job: {job_id}")
        self.job_id = job_id


class JobStateConflictError(StoreError):
    """A transition was attempted from a state that forbids it (-> 409)."""

    def __init__(self, job_id: str, state: str) -> None:
        super().__init__(f"job {job_id}: cannot transition from state {state!r}")
        self.job_id = job_id
        self.state = state


class DuplicateJobError(StoreError):
    """A caller-supplied job_id already exists — rejected, never double-enqueued
    (-> 409, idempotent by job-id)."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"job already exists: {job_id}")
        self.job_id = job_id


class QueueFullError(StoreError):
    """The campaign's pending (queued + running) count is at the cap
    (AR28, -> 409); zero rows written."""

    def __init__(self, campaign_id: str, max_pending: int) -> None:
        super().__init__(f"campaign {campaign_id}: pending jobs at the cap ({max_pending})")
        self.campaign_id = campaign_id
        self.max_pending = max_pending


class InvalidJobInputError(StoreError):
    """Malformed job input — rejected with zero rows written (-> 422)."""


# ---------------------------------------------------------------------------
# Change listener (the WebSocket seam; the store never knows websockets exist)
# ---------------------------------------------------------------------------

#: Listener signature: (AD-17 event name, the committed job row,
#: queue_position at transition time, or None for a terminal job).
ChangeListener = Callable[[str, models.Job, int | None], None]

_change_listener: ChangeListener | None = None


def set_change_listener(listener: ChangeListener | None) -> None:
    """Register (or clear) the single change listener.

    One FastAPI process == single writer (AD-13): exactly one listener.
    Called after every committed job transition with
    ``(event, job, queue_position)`` — the position is snapshotted
    synchronously at transition time, so a broadcast never opens a DB
    transaction. Includes transitions 1.4's worker drives
    (claim/complete/fail/progress).
    """
    global _change_listener
    _change_listener = listener


def _notify(event: str, job: models.Job, position: int | None) -> None:
    """Fire the change listener after the transition committed.

    The queue position is snapshotted by the caller inside the mutation's
    own session — the event describes the state at transition time, and
    the listener needs no DB access of its own (a broadcast never opens
    a transaction, so it cannot block on the write lock or deadlock
    against a concurrent transition). Never raises: a failing listener
    must not corrupt the already-committed job transition.
    """
    listener = _change_listener
    if listener is not None:
        with suppress(Exception):
            listener(event, job, position)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def enqueue_job(
    campaign_id: str,
    kind: str,
    payload: dict[str, Any],
    *,
    job_id: str | None = None,
    max_llm_calls: int | None = None,
    max_media_calls: int | None = None,
) -> models.Job:
    """Enqueue one job at the tail of the global FIFO (AD-3).

    Rejects (zero rows written) with ``UnknownCampaignError`` (404),
    ``InvalidJobInputError`` (422: kind outside {text,image,video},
    non-ULID ``job_id``, negative budgets, non-dict payload),
    ``DuplicateJobError`` (409, idempotent by job-id), or
    ``QueueFullError`` (409, AR28 pending cap). Budgets default from
    env; enforcement is Story 1.4 (AR21).
    """
    with session_scope() as session:
        job = _enqueue(session, campaign_id, kind, payload, job_id, max_llm_calls, max_media_calls)
        position = _queue_position(session, job)
    _notify(EVENT_QUEUE_CHANGED, job, position)
    return job


def claim_next_job() -> models.Job | None:
    """Claim the earliest queued job, or None.

    Exactly one job runs at a time (AD-3): returns None while any job is
    ``running`` and when the queue is empty. The ``BEGIN IMMEDIATE``
    begin-listener serializes concurrent claims — the loser's reads see
    the winner's ``running`` row and return None.
    """
    with session_scope() as session:
        running = session.scalars(
            select(models.Job).where(models.Job.state == "running").limit(1)
        ).first()
        if running is not None:
            return None
        job = session.scalars(
            select(models.Job)
            .where(models.Job.state == "queued")
            .order_by(literal_column("rowid"))
            .limit(1)
        ).first()
        if job is None:
            return None
        job.state = "running"
        job.started_at = time.now()
        position = _queue_position(session, job)
    _notify(EVENT_QUEUE_CHANGED, job, position)
    return job


def complete_job(job_id: str) -> models.Job:
    """Mark a running job succeeded; frees the slot for the next claim.

    Emits ``job_done`` then ``queue_changed``. Wrong state (or unknown
    job) -> ``JobStateConflictError``/``JobNotFoundError``, state unchanged.
    """
    with session_scope() as session:
        job = _require_running(session, job_id)
        job.state = "succeeded"
        job.finished_at = time.now()
        position = _queue_position(session, job)  # terminal: None
    _notify(EVENT_JOB_DONE, job, position)
    _notify(EVENT_QUEUE_CHANGED, job, position)
    return job


def fail_job(job_id: str, error: str) -> models.Job:
    """Mark a running job failed, storing the error message (-> 409/404 otherwise).

    Emits ``job_failed`` then ``queue_changed`` — failing frees the queue
    slot exactly like completing, so same-campaign pending positions shift.
    """
    with session_scope() as session:
        job = _require_running(session, job_id)
        job.state = "failed"
        job.error = error
        job.finished_at = time.now()
        position = _queue_position(session, job)  # terminal: None
    _notify(EVENT_JOB_FAILED, job, position)
    _notify(EVENT_QUEUE_CHANGED, job, position)
    return job


def report_progress(job_id: str, progress: float) -> models.Job:
    """Store a running job's progress (0 <= progress <= 1); state stays running.

    Out-of-range (incl. NaN) -> ``InvalidJobInputError`` (422); wrong
    state -> ``JobStateConflictError`` (409) — 4xx, never a state change.
    """
    if not 0.0 <= progress <= 1.0:
        raise InvalidJobInputError(f"progress must be in [0, 1], got {progress}")
    with session_scope() as session:
        job = _require_running(session, job_id)
        job.progress = progress
        position = _queue_position(session, job)
    _notify(EVENT_JOB_PROGRESS, job, position)
    return job


def cancel_job(job_id: str) -> models.Job:
    """Cancel a queued or running job; terminal jobs are rejected (409).

    Frees the queue slot (a cancelled job is no longer pending, AR28) and
    tells 1.4's runner to stop (the runner polls the state).
    """
    with session_scope() as session:
        job = session.get(models.Job, job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.state in TERMINAL_STATES:
            raise JobStateConflictError(job_id, job.state)
        job.state = "cancelled"
        job.finished_at = time.now()
        position = _queue_position(session, job)  # terminal: None
    _notify(EVENT_JOB_CANCELLED, job, position)
    _notify(EVENT_QUEUE_CHANGED, job, position)
    return job


def recover_stale_running() -> int:
    """Re-queue every job left in ``running`` state; returns the count.

    A ``running`` row can only mean the worker that claimed it died with
    the process — there is no live worker to complete or fail it. Called
    at store/app startup (AR11, spec RESTART_STALE_RUNNING): the job is
    put back at the head of the FIFO's pending order with ``started_at``
    cleared, so the queue never deadlocks on a stale ``running`` row.
    """
    with session_scope() as session:
        stale = session.scalars(select(models.Job).where(models.Job.state == "running")).all()
        for job in stale:
            job.state = "queued"
            job.started_at = None
        return len(stale)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def job_status(job_id: str) -> tuple[models.Job, int | None]:
    """The job row plus its per-campaign queue position.

    Position = 1 + count of same-campaign non-terminal jobs with earlier
    rowid (STATUS_POSITION); None for terminal jobs (no longer in the
    queue). Unknown job -> ``JobNotFoundError``.
    """
    with session_scope() as session:
        job = session.get(models.Job, job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job, _queue_position(session, job)


def list_jobs(
    campaign_id: str,
    cursor: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> tuple[list[tuple[models.Job, int | None]], str | None]:
    """Campaign-scoped job list, oldest first (rowid order).

    ``cursor`` is the ULID of the last job of the previous page (the API
    layer decodes the opaque token, pagination convention). Returns
    ``(jobs with their per-campaign queue positions, next cursor ULID or
    None)``. Positions are computed in the same transaction — a stable
    snapshot, never a per-page recount.
    """
    if limit < 1:
        raise InvalidJobInputError(f"limit must be >= 1, got {limit}")
    with session_scope() as session:
        if session.get(models.Campaign, campaign_id) is None:
            raise UnknownCampaignError(campaign_id)
        jobs = session.scalars(
            select(models.Job)
            .where(
                models.Job.campaign_id == campaign_id,
                literal_column("rowid") > _after_rowid(session, campaign_id, cursor),
            )
            .order_by(literal_column("rowid"))
            .limit(limit + 1)
        ).all()
        has_more = len(jobs) > limit
        page = jobs[:limit]
        next_cursor = page[-1].id if has_more else None
        pending_ids = session.scalars(
            select(models.Job.id)
            .where(
                models.Job.campaign_id == campaign_id,
                models.Job.state.in_(PENDING_STATES),
            )
            .order_by(literal_column("rowid"))
        ).all()
        positions = {job_id: index + 1 for index, job_id in enumerate(pending_ids)}
        return [(job, positions.get(job.id)) for job in page], next_cursor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enqueue(
    session: Session,
    campaign_id: str,
    kind: str,
    payload: dict[str, Any],
    job_id: str | None,
    max_llm_calls: int | None,
    max_media_calls: int | None,
) -> models.Job:
    if kind not in JOB_KINDS:
        raise InvalidJobInputError(f"job kind must be one of {sorted(JOB_KINDS)}, got {kind!r}")
    if not isinstance(payload, dict):
        raise InvalidJobInputError("job payload must be a JSON object")
    _check_json_serializable(payload)
    if job_id is not None:
        _check_ulid(job_id)
    settings = queue_settings()
    if max_llm_calls is None:
        max_llm_calls = settings.max_llm_calls_per_job
    if max_media_calls is None:
        max_media_calls = settings.max_media_calls_per_job
    if max_llm_calls < 0:
        raise InvalidJobInputError(f"max_llm_calls must be >= 0, got {max_llm_calls}")
    if max_media_calls < 0:
        raise InvalidJobInputError(f"max_media_calls must be >= 0, got {max_media_calls}")
    if session.get(models.Campaign, campaign_id) is None:
        raise UnknownCampaignError(campaign_id)
    if job_id is not None and session.get(models.Job, job_id) is not None:
        raise DuplicateJobError(job_id)
    pending = session.scalar(
        select(func.count())
        .select_from(models.Job)
        .where(
            models.Job.campaign_id == campaign_id,
            models.Job.state.in_(PENDING_STATES),
        )
    )
    if (pending or 0) >= settings.max_pending_per_campaign:
        raise QueueFullError(campaign_id, settings.max_pending_per_campaign)
    job = models.Job(
        id=job_id or ids.new_id(),
        campaign_id=campaign_id,
        kind=kind,
        payload=payload,
        state="queued",
        progress=0.0,
        max_llm_calls=max_llm_calls,
        max_media_calls=max_media_calls,
        error=None,
        created_at=time.now(),
        started_at=None,
        finished_at=None,
    )
    session.add(job)
    return job


def _require_running(session: Session, job_id: str) -> models.Job:
    job = session.get(models.Job, job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    if job.state != "running":
        raise JobStateConflictError(job_id, job.state)
    return job


def _check_ulid(ulid: str) -> None:
    """Caller-supplied job ids must be ULIDs (conventions.md) — enforced at
    the store boundary, like entity ids (commit._check_ulid).
    """
    if len(ulid) != 26 or any(ch not in CROCKFORD_ALPHABET for ch in ulid):
        raise InvalidJobInputError(f"job_id is not a ULID: {ulid!r}")


def _check_json_serializable(payload: dict[str, Any]) -> None:
    """Reject payloads SQLAlchemy's JSON type cannot serialize (422, zero rows).

    ``isinstance(payload, dict)`` alone lets a non-serializable value
    (datetime, custom object) slip through to a raw flush-time TypeError
    → 500. The store is the boundary where 4xx = user error holds.
    """
    try:
        json.dumps(payload)
    except TypeError as exc:
        raise InvalidJobInputError(f"job payload must be JSON-serializable: {exc}") from exc


def _rowid(session: Session, job_id: str) -> int | None:
    """The rowid of a job (None if no such job)."""
    return session.scalar(
        select(literal_column("rowid")).select_from(models.Job).where(models.Job.id == job_id)
    )


def _after_rowid(session: Session, campaign_id: str, cursor: str | None) -> int:
    """Rowid anchor for cursor pagination: list jobs strictly after this one.

    The cursor is the last item's ULID (pagination convention); its
    rowid anchors the next page. A cursor naming another campaign's job
    is a user error (422): it would silently skip this campaign's jobs
    by anchoring on a foreign rowid. A well-formed ULID that names no
    job falls back to the start: jobs are never deleted, so only
    fabricated cursors can miss.
    """
    if cursor is None:
        return -1
    anchor = _rowid(session, cursor)
    if anchor is None:
        return -1
    row = session.get(models.Job, cursor)
    if row is not None and row.campaign_id != campaign_id:
        raise InvalidJobInputError("cursor names a job of another campaign")
    return anchor


def _queue_position(session: Session, job: models.Job) -> int | None:
    """1 + count of same-campaign non-terminal jobs with earlier rowid.

    Per-campaign, not global: a DM sees only their own load (AD-9); the
    global FIFO still schedules across campaigns by rowid.
    """
    if job.state not in PENDING_STATES:
        return None
    rowid = _rowid(session, job.id)
    if rowid is None:
        return None
    earlier = session.scalar(
        select(func.count())
        .select_from(models.Job)
        .where(
            models.Job.campaign_id == job.campaign_id,
            models.Job.state.in_(PENDING_STATES),
            literal_column("rowid") < rowid,
        )
    )
    return (earlier or 0) + 1
