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
from typing import Any, cast

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from app.core import ids, time
from app.core.pagination import anchor_rowid, paging
from app.core.settings import queue_settings
from app.store import models
from app.store.commit import StoreError, UnknownCampaignError, UnknownEntityError
from app.store.db import session_scope

#: stages 2-3 proposed candidates, committing nothing. ``regenerate`` is
#: spec-3.5's re-roll kind: its runner re-rolls one entity (whole) or one
#: proposed candidate (whole or per-section), staging/replacing zero or
#: one proposed row and committing nothing.
JOB_KINDS: frozenset[str] = frozenset(
    {
        "text",
        "image",
        "video",
        "video_prompt",
        "build_in",
        "generate",
        "regenerate",
        "add_character",
    }
)

#: Generate payload contract (spec-3.1): exactly one plain-language ask.
GENERIC_BUILD_MAX_FIGURES = 4
GENERATE_MAX_ASK_LENGTH = 2000

#: Build-in payload contract (spec-2.1): free-form section caps.
BUILD_IN_MAX_ENTRIES = 100
BUILD_IN_MAX_ENTRY_LENGTH = 2000
BUILD_IN_MAX_NOTE_LENGTH = 2000


#: The hybrid-authorship spec's canonical schema lives in
#: ``store.direct``; that import is FUNCTION-LOCAL (below) because
#: ``store.direct`` reads the candidates shape constants and
#: ``candidates`` imports this module — a module-level import would
#: close the candidates->jobs->direct->candidates cycle at import time.


#: Build-in budget ladder (improvement plan D): the flat settings default
#: starved big rosters — rung-50 attempt 5 died AT the flat 64 while the
#: model was still converging. Floor, per-figure stat-block calls, wave-2
#: chunk size, fixed wave/repair overhead — see ``_build_in_budget``.
BUILD_IN_MIN_LLM_CALLS = 64
BUILD_IN_LLM_CALLS_PER_FIGURE = 3
BUILD_IN_LLM_CHUNK_ENTRIES = 8
BUILD_IN_LLM_CALLS_OVERHEAD = 32

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
    ``InvalidJobInputError`` (422: kind outside the closed set,
    non-ULID ``job_id``, negative budgets, non-dict payload, a
    ``build_in`` payload violating the spec-2.1 contract, a ``generate``
    payload that is not exactly ``{"ask": str}`` or a campaign with zero
    committed entities — spec-3.1 ASK_EMPTY_WORLD, an ``image`` payload
    that is not exactly ``{"entity_id": <ULID>}`` for a committed entity
    of this campaign with a non-blank AR24 appearance — spec-4.1
    NO_APPEARANCE, a ``video`` payload naming a committed boss-tier
    entity that does not carry a usable prompt source (or supplies a
    blank approved ``prompt`` — spec-4.2/4.6 NO_VIDEO_PROMPT and
    RENDER_BLANK_PROMPT rows), or a ``video_prompt`` payload naming a
    committed boss-tier entity without a non-blank AR24 appearance —
    spec-4.6 DRAFT_NO_BOSS/DRAFT_NO_SOURCE rows), ``UnknownEntityError``
    (404 — an image payload naming a missing or foreign entity),
    ``DuplicateJobError`` (409, idempotent by job-id), or
    ``QueueFullError`` (409, AR28 pending cap). Budgets default from
    env — except ``build_in``, whose LLM ceiling scales with the roster
    when the caller supplies none (improvement plan D,
    ``_build_in_budget``); enforcement is Story 1.4 (AR21).
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


def complete_job(job_id: str, result: dict[str, Any] | None = None) -> models.Job:
    """Mark a running job succeeded; frees the slot for the next claim.

    ``result`` (spec-1.4) is the worker's generation output, persisted on
    the job for REST reads — jobs are not world graph, so this never
    creates a revision. Emits ``job_done`` then ``queue_changed``. Wrong
    state (or unknown job) -> ``JobStateConflictError``/``JobNotFoundError``,
    state unchanged.
    """
    with session_scope() as session:
        job = _require_running(session, job_id)
        job.state = "succeeded"
        job.result = result
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
        page, next_cursor = paging(jobs, limit)
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
    if not ids.is_valid_ulid(ulid):
        raise InvalidJobInputError(f"job_id is not a ULID: {ulid!r}")


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
    campaign = session.get(models.Campaign, campaign_id)
    if campaign is None:
        raise UnknownCampaignError(campaign_id)
    if kind == "build_in":
        _validate_build_in_payload(payload)
        if campaign.is_generic:
            # The Generic library's gate (owner spec, 2026-09-17): a
            # library build is CHARACTERS ONLY — the storage world's own
            # state and lore are never generation context, so places/
            # factions/notes (world-shaping sections) and declared
            # relations (they demand world targets) are rejected, and the
            # payload must name the theme whose default seed the runner
            # substitutes for the library's.
            _validate_generic_build_payload(payload, campaign)
    elif kind == "add_character":
        # The hybrid-authorship spec's fully-authored path: the sync
        # enqueue half of the three-layer gate. Violations are the 422's
        # schema-path details; the world-dependent checks (Tier-1 target
        # existence/kind) run in the same transaction — the F5 race is
        # the runner backstop's problem, not this gate's.
        _validate_add_character_payload(payload, session, campaign_id)
    elif kind == "generate":
        _validate_generate_payload(payload)
    elif kind == "image":
        # Spec-4.1: the portrait payload is validated inside the enqueue
        # transaction (like regenerate) — the entity must exist in this
        # campaign and its committed AR24 appearance must be non-blank.
        _validate_image_payload(payload, session, campaign_id)
    elif kind == "video":
        # Spec-4.2/4.6: the reveal-video payload is validated inside the
        # enqueue transaction (the image precedent) — the entity must
        # exist in this campaign and be boss-tier (BBEG/Monster); with a
        # supplied prompt the boss-section projection is relaxed but the
        # source-frame (appearance) + boss gates still apply (4.6).
        _validate_video_payload(payload, session, campaign_id)
    elif kind == "video_prompt":
        # Spec-4.6: the draft payload is validated inside the enqueue
        # transaction (the image precedent) — the entity must exist in
        # this campaign, be boss-tier (BBEG/Monster), and carry a
        # non-blank AR24 appearance (a MiniMax-I2VA draft needs a
        # source-frame description).
        _validate_video_prompt_payload(payload, session, campaign_id)
    if not isinstance(payload, dict):
        raise InvalidJobInputError("job payload must be a JSON object")
    _check_json_serializable(payload)
    if job_id is not None:
        _check_ulid(job_id)
    settings = queue_settings()
    if kind == "add_character":
        # Zero-LLM is STRUCTURAL (spec: hybrid authorship): the runner
        # accepts no provider, and the call ceilings are forced to 0 —
        # an explicit caller budget never overrides the definition.
        max_llm_calls = 0
        max_media_calls = 0
    else:
        if max_llm_calls is None:
            # Build-in rosters scale their own LLM ceiling (improvement
            # plan D) — the flat settings default starved rung-50
            # attempt 5 mid-convergence. An explicit caller-supplied
            # budget never reaches this branch, so it always wins. Runs
            # after _validate_build_in_payload, so the section shape is
            # guaranteed.
            max_llm_calls = (
                _build_in_budget(payload) if kind == "build_in" else settings.max_llm_calls_per_job
            )
        if max_media_calls is None:
            max_media_calls = settings.max_media_calls_per_job
    if max_llm_calls < 0:
        raise InvalidJobInputError(f"max_llm_calls must be >= 0, got {max_llm_calls}")
    if max_media_calls < 0:
        raise InvalidJobInputError(f"max_media_calls must be >= 0, got {max_media_calls}")
    if session.get(models.Campaign, campaign_id) is None:
        raise UnknownCampaignError(campaign_id)
    # The idempotency check precedes the generate empty-world gate: a
    # retry whose world was emptied by undo is the documented 409, never
    # a 422 masquerading as new input (spec-3.1 review round 1).
    if job_id is not None and session.get(models.Job, job_id) is not None:
        raise DuplicateJobError(job_id)
    if kind == "regenerate":
        # Spec-3.5: resolve the target (entity/candidate of this
        # campaign) and validate the closed-section list BEFORE any row
        # — unknown/foreign target is a 404, a shape/section violation a
        # 422, both zero rows. Placed after the duplicate-job gate (a
        # same-job_id retry is the documented 409, never a 422
        # masquerading as new input — the generate empty-world gate's
        # rationale).
        _validate_regenerate_payload(payload, session, campaign_id)
    if kind == "generate":
        # ASK_EMPTY_WORLD (spec-3.1): a plain-language ask needs a world
        # to weave into — zero committed entities is a 422, no job row.
        committed = session.scalar(
            select(func.count())
            .select_from(models.Entity)
            .where(models.Entity.campaign_id == campaign_id)
        )
        if not committed:
            raise InvalidJobInputError(
                "generate requires at least one committed entity in the campaign"
            )
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


def _validate_generic_build_payload(payload: dict[str, Any], campaign: models.Campaign) -> None:
    """The Generic library's build-in contract (owner spec, 2026-09-17):
    characters only, theme-seeded, storage-isolated.

    The payload is exactly ``{"key_figures": [...], "theme": str}`` —
    structured seed figures (never legacy strings: a library build is
    always authored-shaped), at most ``GENERIC_BUILD_MAX_FIGURES``, NO
    relations on any entry (a declared target would demand world rows the
    library must not grow), and a theme carrying a default seed. The
    runner substitutes the theme's default description/lore for the
    campaign's own seed — the library's entities and lore are never
    generation context.
    """
    from app.core.config import THEME_DEFAULT_SEEDS  # noqa: PLC0415 - config leaf
    from app.store.direct import seed_entry_violations  # noqa: PLC0415 - import cycle

    unknown = set(payload) - {"key_figures", "theme"}
    if unknown:
        raise InvalidJobInputError(
            "generic build payload carries world-shaping section(s) "
            f"{sorted(unknown)} — the library stores characters only"
        )
    figures = payload.get("key_figures")
    if not isinstance(figures, list) or not figures:
        raise InvalidJobInputError("generic build payload needs a non-empty key_figures list")
    if len(figures) > GENERIC_BUILD_MAX_FIGURES:
        raise InvalidJobInputError(f"generic build exceeds {GENERIC_BUILD_MAX_FIGURES} figures")
    for index, figure in enumerate(figures):
        if isinstance(figure, str):
            raise InvalidJobInputError(
                f"generic build key_figures[{index}] must be an authored entry"
            )
        violations = seed_entry_violations(figure, "key_figures")
        if violations:
            raise InvalidJobInputError(
                f"generic build key_figures[{index}]: " + "; ".join(violations)
            )
        relations = figure.get("relations") if isinstance(figure, dict) else None
        if relations:
            raise InvalidJobInputError(
                f"generic build key_figures[{index}] declares relations — the library "
                "stores characters only; wire relations when you move the character "
                "into a canon world"
            )
    theme = payload.get("theme")
    if not isinstance(theme, str) or theme.strip() not in THEME_DEFAULT_SEEDS:
        raise InvalidJobInputError(
            f"generic build theme must be one of {sorted(THEME_DEFAULT_SEEDS)}"
        )


def _validate_build_in_payload(payload: dict[str, Any]) -> None:
    """Enforce the spec-2.1 build-in payload contract (422, zero rows),
    extended by the hybrid-authorship spec: a section entry is a
    ``str`` (STRING_LEGACY — today's semantics byte-for-byte) or a
    SeedEntry object (``store.direct.seed_entry_violations`` owns the
    shape: required non-blank ``name``; figures may pin ``role`` and
    author a ``record``; places/factions may author a ``description``;
    any entry may declare ``relations``).

    The free-form sections are ``places``, ``factions``, ``key_figures``
    (each a list of 1–2000-char entries, <= 100 entries) and ``notes``
    (a single string, trimmed length <= 2000 chars; an explicit
    null/non-string is rejected). At least one non-blank entry across all
    four is required. Unrecognized top-level keys are ignored (the runner
    in 2.3 owns the prompt contract); only the shape that can be
    """
    if not isinstance(payload, dict):
        raise InvalidJobInputError("build_in payload must be a JSON object")
    any_content = False
    from app.store.direct import seed_entry_violations  # noqa: PLC0415 - import cycle

    counts = payload.get("generate_counts", {})
    if not isinstance(counts, dict) or set(counts) - {"places", "factions", "key_figures"}:
        raise InvalidJobInputError(
            "generate_counts must contain only places, factions, and key_figures"
        )

    for section in ("places", "factions", "key_figures"):
        entries = payload.get(section, [])
        if not isinstance(entries, list):
            raise InvalidJobInputError(f"build_in {section} must be a list")
        if len(entries) > BUILD_IN_MAX_ENTRIES:
            raise InvalidJobInputError(f"build_in {section} exceeds {BUILD_IN_MAX_ENTRIES} entries")
        requested = counts.get(section, 0)
        if (
            type(requested) is not int
            or requested < 0
            or requested + len(entries) > BUILD_IN_MAX_ENTRIES
        ):
            raise InvalidJobInputError(
                f"{section}: generated count must be 0–{BUILD_IN_MAX_ENTRIES - len(entries)} "
                "after named entries"
            )
        any_content = any_content or requested > 0
        for i, entry in enumerate(entries):
            if isinstance(entry, str):
                trimmed = entry.strip()
                if len(trimmed) > BUILD_IN_MAX_ENTRY_LENGTH:
                    raise InvalidJobInputError(
                        f"build_in {section}[{i}] exceeds {BUILD_IN_MAX_ENTRY_LENGTH} chars"
                    )
                any_content = any_content or bool(trimmed)
            else:
                violations = seed_entry_violations(entry, section)
                if violations:
                    raise InvalidJobInputError(f"build_in {section}[{i}]: " + "; ".join(violations))
                any_content = True
    # ``notes`` is optional (absent key stays allowed), but an explicit
    # null or any non-string is rejected — the key, when present, must be
    # a string within the cap.
    if "notes" in payload and not isinstance(payload["notes"], str):
        raise InvalidJobInputError("build_in notes must be a string")
    notes = payload.get("notes", "")
    if len(notes.strip()) > BUILD_IN_MAX_NOTE_LENGTH:
        raise InvalidJobInputError(f"build_in notes exceeds {BUILD_IN_MAX_NOTE_LENGTH} chars")
    if notes.strip():
        any_content = True
    if not any_content:
        raise InvalidJobInputError("build_in requires at least one non-blank section entry")


def _seed_entry_name(entry: str | dict[str, Any]) -> str:
    """A section entry's roster name — the trimmed text of a legacy
    string, the ``name`` of a SeedEntry (both already validated)."""
    if isinstance(entry, str):
        return entry.strip()
    name = entry.get("name")
    return name.strip() if isinstance(name, str) else ""


def _declared_target_names(payload: dict[str, Any]) -> set[str]:
    """The normalized names of every ``target_name`` a SeedEntry's
    declared relations demand (the mandate set). Pure function of the
    payload — the enqueue budget counts them WITHOUT a world read (a
    committed-target demand is counted too: the ceiling only needs to be
    an upper bound, and the runner's own mandate check owns the exact
    semantics)."""
    from app.store.direct import normalize_entity_name  # noqa: PLC0415 - import cycle

    names: set[str] = set()
    for section in ("places", "factions", "key_figures"):
        for entry in payload.get(section) or []:
            if not isinstance(entry, dict):
                continue
            relations = entry.get("relations")
            if not isinstance(relations, list):
                continue
            for raw in relations:
                if isinstance(raw, dict) and isinstance(raw.get("target_name"), str):
                    names.add(normalize_entity_name(raw["target_name"]))
    seed_names = {
        normalize_entity_name(_seed_entry_name(entry))
        for section in ("places", "factions", "key_figures")
        for entry in payload.get(section) or []
    }
    return names - seed_names


def _build_in_budget(payload: dict[str, Any]) -> int:
    """Roster-scaled ``max_llm_calls`` for a build_in job (improvement plan D).

    Ladder evidence: rung-50 attempt 5 died AT the flat 64-call default
    while the model was still converging — BudgetExceeded mid-wave after
    a long run. The proven runner formula (``run_rung.py``:
    ``3 * figures + 32``) is generalized with a chunk headroom term —
    every ``BUILD_IN_LLM_CHUNK_ENTRIES`` non-blank roster entries across
    the three sections adds one wave-2 chunk call:

    ``max(64, 3 * figures + ceil(total / 8) + 32)``

    ``figures`` counts ``key_figures`` PLUS the hybrid path's mandated
    relation targets (each declared ``target_name`` that no seed entry
    carries — the mandate may generate a full record + stat block and
    its repairs, so it budgets like a figure); ``total`` counts all three
    sections plus the mandated names. Only non-blank entries count (the
    runner's blank-trim rule), and the ``BUILD_IN_MIN_LLM_CALLS`` floor
    keeps small rosters at the old settings default. Called from
    ``_enqueue`` after ``_validate_build_in_payload``, so every entry is
    a valid string-or-SeedEntry.
    """

    def _count(section: str) -> int:
        return sum(1 for entry in payload.get(section) or [] if _seed_entry_name(entry))

    counts = cast(dict[str, int], payload.get("generate_counts") or {})
    figures = _count("key_figures") + counts.get("key_figures", 0)
    total = (
        figures
        + _count("places")
        + _count("factions")
        + counts.get("places", 0)
        + counts.get("factions", 0)
    )
    mandated = _declared_target_names(payload)
    figures += len(mandated)
    total += len(mandated)
    chunks = -(-total // BUILD_IN_LLM_CHUNK_ENTRIES)  # ceil division
    naming_calls = sum(-(-count // 20) for count in counts.values())
    return max(
        BUILD_IN_MIN_LLM_CALLS,
        BUILD_IN_LLM_CALLS_PER_FIGURE * figures
        + chunks
        + BUILD_IN_LLM_CALLS_OVERHEAD
        + 2 * naming_calls,
    )


def _validate_add_character_payload(
    payload: dict[str, Any], session: Session, campaign_id: str
) -> None:
    """The fully-authored character path's enqueue gate (422, zero rows).

    The canonical schema (``store.direct.validate_add_character_payload``)
    owns completeness, stat-block structure, the dice pattern, relations
    well-formedness, unknown keys, and the ``target_name`` ban — a 422
    whose message lists the exact schema-path violations. The two
    world-dependent checks run HERE, inside the enqueue transaction (the
    image-payload precedent): a Tier-1 ``target_id`` must name a
    committed entity OF this campaign, and the relation's kind pair must
    be legal. The run-time backstop re-resolves both against the fresh
    world (F5) — this gate only keeps a doomed job out of the queue.
    """
    from app.store.commit import edge_kind_ok  # noqa: PLC0415 - import cycle
    from app.store.direct import validate_add_character_payload  # noqa: PLC0415

    violations = validate_add_character_payload(payload)
    if violations:
        raise InvalidJobInputError("add_character payload invalid: " + "; ".join(violations))
    for index, sheet in enumerate(payload["characters"]):
        relations = sheet.get("relations")
        if not isinstance(relations, list):
            continue
        for r_index, raw in enumerate(relations):
            if not isinstance(raw, dict) or "target_id" not in raw:
                continue
            target_id = raw["target_id"]
            entity = session.get(models.Entity, target_id)
            if entity is None or entity.campaign_id != campaign_id:
                raise InvalidJobInputError(
                    f"add_character characters[{index}].relations[{r_index}].target_id "
                    f"{target_id} names no committed entity of this campaign"
                )
            edge_type = raw.get("type")
            if isinstance(edge_type, str) and not edge_kind_ok(edge_type, "character", entity.kind):
                raise InvalidJobInputError(
                    f"add_character characters[{index}].relations[{r_index}]: {edge_type} "
                    f"cannot run from a character to a committed {entity.kind}"
                )


def _validate_generate_payload(payload: dict[str, Any]) -> None:
    """Enforce the spec-3.1 generate payload contract (422, zero rows).

    The payload is exactly ``{"ask": str}`` — the DM's plain-language
    ask, a single non-blank string whose trimmed length is at most
    ``GENERATE_MAX_ASK_LENGTH``. A missing/non-string/blank/over-long
    ask, or any extra key, is rejected before any row is written.
    """
    if not isinstance(payload, dict):
        raise InvalidJobInputError("generate payload must be a JSON object")
    if set(payload) not in ({"ask"}, {"ask", "entity_kind"}):
        raise InvalidJobInputError(
            "generate payload must be {'ask': str, 'entity_kind': 'character'|'faction'|'place'}"
        )
    ask = payload["ask"]
    if not isinstance(ask, str):
        raise InvalidJobInputError("generate ask must be a string")
    if not ask.strip():
        raise InvalidJobInputError("generate ask must be non-blank")
    if len(ask.strip()) > GENERATE_MAX_ASK_LENGTH:
        raise InvalidJobInputError(f"generate ask exceeds {GENERATE_MAX_ASK_LENGTH} chars")
    if "entity_kind" in payload and payload["entity_kind"] not in {"character", "faction", "place"}:
        raise InvalidJobInputError(
            "generate entity_kind must be 'character', 'faction', or 'place'"
        )


def _validate_image_payload(payload: dict[str, Any], session: Session, campaign_id: str) -> None:
    """Enforce the spec-4.1 image payload contract (422/404, zero rows).

    The payload is ``{"entity_id": <ULID>}`` plus the optional P1 knobs
    (``style`` / ``framing`` / ``background`` / ``custom_style``) — the
    committed entity profile selected by kind: character appearance,
    place description, or faction description/doctrine/assets. The entity
    must exist in this campaign (404 ``UnknownEntityError``); missing
    source material is a 422, never a queued job. Options and the prompt
    builder are shared with the runner, so the enqueue gate and run-time fail
    condition can never disagree (function-local import: the media
    service imports this package).
    """
    from app.media.service import entity_artwork_prompt, portrait_options

    try:
        portrait_options(payload, entity_kind=None)
    except ValueError as exc:
        raise InvalidJobInputError(str(exc)) from exc
    entity_id = payload["entity_id"]
    if not isinstance(entity_id, str) or not ids.is_valid_ulid(entity_id):
        raise InvalidJobInputError(f"image payload entity_id is not a ULID: {entity_id!r}")
    entity = session.get(models.Entity, entity_id)
    if entity is None or entity.campaign_id != campaign_id:
        raise UnknownEntityError(entity_id)
    try:
        options = portrait_options(payload, entity_kind=entity.kind)
    except ValueError as exc:
        raise InvalidJobInputError(str(exc)) from exc
    if entity_artwork_prompt(
        entity.kind, entity.name, entity.data or {}, text=entity.text, **options
    ) is None:
        raise InvalidJobInputError(
            f"{entity.kind.title()} artwork needs a non-blank description"
            if entity.kind in {"place", "faction"}
            else (
                f"Character {entity.name!r} needs a non-blank appearance "
                "before generating a portrait"
            )
        )


def _validate_video_payload(payload: dict[str, Any], session: Session, campaign_id: str) -> None:
    """Enforce the spec-4.2/4.6 video payload contract (422/404, zero rows).

    The payload is ``{"entity_id": <ULID>}``, optionally joined by the
    DM's approved ``prompt`` (spec-4.6). The committed entity must exist
    in this campaign (404 ``UnknownEntityError``) and be BOSS-tier: its
    committed ``role`` must be BBEG or Monster (422 — the NOT_BOSS matrix
    row). With a supplied prompt, the DM's prompt is the source of truth;
    without one, ``bbeg_video_prompt`` builds from appearance and reveal
    framing. Both paths need a non-blank appearance and boss-tier role.
    A supplied blank prompt is rejected (422 — the
    RENDER_BLANK_PROMPT matrix row). The prompt-existence check runs the
    SAME builder the runner uses, so the enqueue gate and the run-time
    fail condition can never disagree (function-local import: the media
    service imports this package).
    """
    from app.media.service import appearance_prompt, bbeg_video_prompt
    from app.store.candidates import BOSS_ROLES

    if not isinstance(payload, dict) or not set(payload) <= {"entity_id", "prompt"}:
        raise InvalidJobInputError(
            "video payload must be {'entity_id': <ULID>}, optionally with a non-blank 'prompt'"
        )
    entity_id = payload.get("entity_id")
    if not isinstance(entity_id, str) or not ids.is_valid_ulid(entity_id):
        raise InvalidJobInputError(f"video payload entity_id is not a ULID: {entity_id!r}")
    entity = session.get(models.Entity, entity_id)
    if entity is None or entity.campaign_id != campaign_id:
        raise UnknownEntityError(entity_id)
    data = entity.data or {}
    if data.get("role") not in BOSS_ROLES:
        raise InvalidJobInputError(
            f"video payload entity {entity_id} is not boss-tier (BBEG or Monster)"
        )
    supplied_prompt = payload.get("prompt")
    if supplied_prompt is not None:
        # The DM's approved prompt is the source of truth (spec-4.6): a
        # supplied prompt never relaxes the boss-tier or appearance gates, and
        # a blank supplied prompt is a 422 (RENDER_BLANK_PROMPT).
        if not isinstance(supplied_prompt, str) or not supplied_prompt.strip():
            raise InvalidJobInputError("video payload 'prompt' must be a non-blank string")
        if appearance_prompt(data.get("appearance")) is None:
            raise InvalidJobInputError(
                f"video payload entity {entity_id} has no non-blank AR24 appearance"
            )
        return
    if bbeg_video_prompt(data) is None:
        raise InvalidJobInputError(
            f"video payload entity {entity_id} has no usable reveal prompt "
            "(add a non-blank appearance to render this reveal)"
        )


def _validate_video_prompt_payload(
    payload: dict[str, Any], session: Session, campaign_id: str
) -> None:
    """Enforce the spec-4.6 video_prompt payload contract (422/404, zero rows).

    The payload is exactly ``{"entity_id": <ULID>}`` — the committed
    entity whose AR24 record is the draft source. The entity must exist
    in this campaign (404 ``UnknownEntityError``), be BOSS-tier (422 —
    the DRAFT_NO_BOSS matrix row), and carry a non-blank AR24
    ``appearance`` (422 — the DRAFT_NO_SOURCE matrix row: a MiniMax-I2VA
    draft needs a source-frame description). The appearance check runs
    the SAME ``appearance_prompt`` the runner uses, so the enqueue gate
    and the run-time fail condition can never disagree (function-local
    import: the media service imports this package).
    """
    from app.media.service import appearance_prompt
    from app.store.candidates import BOSS_ROLES

    if not isinstance(payload, dict) or set(payload) != {"entity_id"}:
        raise InvalidJobInputError("video prompt payload must be exactly {'entity_id': <ULID>}")
    entity_id = payload["entity_id"]
    if not isinstance(entity_id, str) or not ids.is_valid_ulid(entity_id):
        raise InvalidJobInputError(f"video prompt payload entity_id is not a ULID: {entity_id!r}")
    entity = session.get(models.Entity, entity_id)
    if entity is None or entity.campaign_id != campaign_id:
        raise UnknownEntityError(entity_id)
    data = entity.data or {}
    if data.get("role") not in BOSS_ROLES:
        raise InvalidJobInputError(
            f"video prompt payload entity {entity_id} is not boss-tier (BBEG or Monster)"
        )
    if appearance_prompt(data.get("appearance")) is None:
        raise InvalidJobInputError(
            f"video prompt payload entity {entity_id} has no non-blank AR24 appearance"
        )


def _validate_regenerate_payload(
    payload: dict[str, Any], session: Session, campaign_id: str
) -> None:
    """Enforce the spec-3.5 regenerate payload contract (422/404, zero rows).

    Payload is ``{"target": {"kind": "entity"|"candidate", "id": <ULID>},
    "sections": [AR24 content section, ...] | null, "guide"?: str,
    "dial"?: <DIAL_LEVELS member>}`` — ``sections``
    absent or null means the whole character; a non-empty list means
    exactly those regenerable content sections (the closed
    ``REGEN_SECTIONS`` set; an empty list is "regenerate nothing" and
    rejected). The AD-38 shaped request rides the same envelope:
    ``guide`` (the DM's box text) and ``dial`` (a closed ``DIAL_LEVELS``
    member — the record's elaboration setting, never invented at run
    time). The target is resolved inside the enqueue transaction:
    unknown or foreign entity/candidate id -> ``UnknownEntityError`` /
    ``CandidateNotFoundError`` (404); a settled candidate -> 422; a
    target without an AR24 sectioned record (``payload_section_violations``
    non-empty — e.g. a build-in entity, which never carries a sectioned
    profile) -> 422; a section outside the closed set -> 422; and
    ``boss`` on a target whose role is not BBEG/Monster -> 422 — all
    before any row is written (function-local import: store.candidates
    imports this module, the db.py-precedented direction).
    """
    from app.store import DIAL_LEVELS
    from app.store.candidates import (
        BOSS_ROLES,
        FLAT_REGEN_SECTIONS,
        REGEN_SECTIONS,
        CandidateNotFoundError,
        payload_section_violations,
    )

    if not isinstance(payload, dict):
        raise InvalidJobInputError("regenerate payload must be a JSON object")
    if set(payload) - {"target", "sections", "guide", "dial"}:
        raise InvalidJobInputError(
            "regenerate payload must be {'target': ..., 'sections': ...|null, "
            "'guide'?: str, 'dial'?: <dial level>}"
        )
    guide = payload.get("guide")
    if guide is not None and (not isinstance(guide, str) or not guide.strip()):
        raise InvalidJobInputError("regenerate guide must be a non-blank string when present")
    dial = payload.get("dial")
    if dial is not None and dial not in DIAL_LEVELS:
        raise InvalidJobInputError(
            f"regenerate dial {dial!r} is outside the closed set: {sorted(DIAL_LEVELS)}"
        )
    target = payload.get("target")
    if not isinstance(target, dict) or set(target) != {"kind", "id"}:
        raise InvalidJobInputError(
            "regenerate target must be exactly {'kind': 'entity'|'candidate', 'id': <ULID>}"
        )
    kind = target.get("kind")
    if kind not in ("entity", "candidate"):
        raise InvalidJobInputError(
            "regenerate target kind must be 'entity' or 'candidate', got {kind!r}"
        )
    target_id = target.get("id")
    if not isinstance(target_id, str) or not ids.is_valid_ulid(target_id):
        raise InvalidJobInputError(f"regenerate target id is not a ULID: {target_id!r}")
    # Whole-character by default: sections absent OR null. A non-empty
    # list is exactly those sections; an empty list regenerates nothing
    # and is rejected.
    sections = payload.get("sections")
    if sections is not None:
        if not isinstance(sections, list) or not sections:
            raise InvalidJobInputError(
                "regenerate sections must be null (whole character) or a non-empty list"
            )
        for section in sections:
            flat_section_names = {
                section_name
                for section_names in FLAT_REGEN_SECTIONS.values()
                for section_name in section_names
            }
            if not isinstance(section, str) or (
                section not in REGEN_SECTIONS and section not in flat_section_names
            ):
                raise InvalidJobInputError(
                    f"regenerate section {section!r} is not regenerable — "
                    f"closed set: {sorted(REGEN_SECTIONS | flat_section_names)}"
                )
    entity_kind: str | None = None
    if kind == "entity":
        entity = session.get(models.Entity, target_id)
        if entity is None or entity.campaign_id != campaign_id:
            raise UnknownEntityError(target_id)
        record = entity.data
        entity_kind = entity.kind
    else:
        candidate = session.get(models.ProposedCandidate, target_id)
        if candidate is None or candidate.campaign_id != campaign_id:
            raise CandidateNotFoundError(target_id)
        if candidate.status != models.STATUS_PROPOSED:
            raise InvalidJobInputError(
                f"regenerate target candidate {target_id} is already "
                f"{candidate.status} — only proposed candidates are re-rollable"
            )
        record = candidate.payload
        entity_kind = (
            record.get("entity_kind", "character") if isinstance(record, dict) else "character"
        )
    flat_sections = FLAT_REGEN_SECTIONS.get(entity_kind or "")
    if flat_sections is not None:
        if not isinstance(record, dict):
            raise InvalidJobInputError(f"regenerate target {target_id!r} has no record object")
        if sections is not None:
            invalid = [section for section in sections if section not in flat_sections]
            if invalid:
                raise InvalidJobInputError(
                    f"regenerate section(s) {sorted(set(invalid))!r} are not valid for "
                    f"{entity_kind}: {list(flat_sections)}"
                )
        return
    if sections is not None:
        invalid = [section for section in sections if section not in REGEN_SECTIONS]
        if invalid:
            raise InvalidJobInputError(
                f"regenerate section(s) {sorted(set(invalid))!r} are not valid for "
                f"character records: {sorted(REGEN_SECTIONS)}"
            )
    # The regeneration unit is the AR24 sectioned record: a target
    # without one (build-in entities, hand-written rows) has no sections
    # to preserve byte-identically — reject up front, never at the job.
    violations = (
        payload_section_violations(record)
        if isinstance(record, dict)
        else ["record must be an object"]
    )
    if violations:
        raise InvalidJobInputError(
            f"regenerate target {target_id!r} has no AR24 sectioned profile: "
            f"{'; '.join(violations)}"
        )
    if sections is not None and "boss" in sections:
        role = record.get("role")
        if not isinstance(role, str) or role not in BOSS_ROLES:
            raise InvalidJobInputError(
                "regenerate section 'boss' is only allowed for a BBEG or Monster target role"
            )


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
    job is a user error too (422): jobs are never deleted, so only
    fabricated cursors can miss — and they must not silently reset the
    page (epic-1 retro item 2).
    """
    if cursor is None:
        return -1
    row = session.get(models.Job, cursor)
    if row is not None and row.campaign_id != campaign_id:
        raise InvalidJobInputError("cursor names a job of another campaign")
    return anchor_rowid(
        session,
        models.Job,
        cursor,
        missing_error=InvalidJobInputError(f"cursor names no job: {cursor}"),
    )


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
