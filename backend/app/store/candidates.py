"""Proposed-candidate staging (AR7, AR19; spec-3.1).

A ``generate`` job stages its 2-3 surviving candidates here — never in
the entity/edge tables (AR7), so retrieval, export, and every world read
stay untouched. The staging function is NOT the commit path: it produces
no revision and no event; AD-1's single-writer rule is honored by routing
the write through this store module (never raw SQL in ``pipeline/``).
The accept/reject lifecycle and the commit path are story 3.2.
"""

import json
from typing import Any

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from app.core import ids, time
from app.core.pagination import anchor_rowid, paging
from app.store import models
from app.store.commit import (
    EDGE_TYPES,
    StoreError,
    UnknownCampaignError,
)
from app.store.db import session_scope
from app.store.jobs import (
    DEFAULT_LIST_LIMIT,
    InvalidJobInputError,
    JobNotFoundError,
)
from app.store.models import PROPOSAL_KIND, STATUS_PROPOSED
from app.store.read import world_state

#: The staged edge record keys (the runner resolves each C-ref to a
#: committed entity ULID before staging): the candidate sits on one side,
#: ``endpoint`` is the committed far endpoint, ``direction`` says whether
#: the edge points from the candidate to the endpoint (``outbound``) or
#: from the endpoint to the candidate (``inbound``).
EDGE_DIRECTIONS: frozenset[str] = frozenset({"outbound", "inbound"})


class InvalidCandidateError(StoreError):
    """A staged candidate's edge endpoints do not resolve to committed
    world state (or an edge type is outside the closed vocabulary).

    Generation-time backstop for the STAGING WINDOW: a delete landing
    between the runner's validation and the staging write surfaces here —
    the job fails with a structured error, never a crash, and no staged
    edge dangles at the moment of staging. Deletes AFTER staging are out
    of scope here: payload edges can point at dead ids until story 3.2's
    accept-time commit-path orphan backstop rejects them.
    """


def stage_candidates(
    campaign_id: str,
    job_id: str,
    payloads: list[dict[str, Any]],
) -> list[models.ProposedCandidate]:
    """Stage candidate records atomically — one transaction, all-or-nothing.

    Re-validates inside the write transaction (the race backstop): every
    edge endpoint must resolve to a committed ``world_state`` id and
    every edge type must be a string in the closed vocabulary; otherwise
    ``InvalidCandidateError`` and zero rows written. Unknown campaign or
    job is rejected before anything is staged.

    Idempotent per job: a crash between the staging commit and
    ``complete_job`` re-queues the job (``recover_stale_running``), so a
    re-run must not stage duplicates — when rows already exist for the
    job, they are returned unchanged and nothing new is written.
    """
    with session_scope() as session:
        if session.get(models.Campaign, campaign_id) is None:
            raise UnknownCampaignError(campaign_id)
        if session.get(models.Job, job_id) is None:
            raise JobNotFoundError(job_id)
        existing = session.scalars(
            select(models.ProposedCandidate).where(models.ProposedCandidate.job_id == job_id)
        ).all()
        if existing:
            return list(existing)
        entities, _edges = world_state(session, campaign_id)
        committed = {entity.id for entity in entities}
        for index, payload in enumerate(payloads):
            # Strict-JSON backstop (review round 2): Python's json.loads
            # accepts NaN/Infinity and 1e999 overflows to inf, which the
            # SQLAlchemy JSON column stores but Starlette refuses to
            # re-serialize (allow_nan=False) — such a staged payload
            # would 500 the candidates read. The runner already drops
            # these; this is the write-boundary defense for any caller.
            try:
                json.dumps(payload, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise InvalidCandidateError(
                    f"candidate {index}: payload is not strict JSON ({exc})"
                ) from exc
            _check_candidate_edges(index, payload, committed)
        rows = [
            models.ProposedCandidate(
                id=ids.new_id(),
                campaign_id=campaign_id,
                job_id=job_id,
                kind=PROPOSAL_KIND,
                status=STATUS_PROPOSED,
                payload=payload,
                created_at=time.now(),
            )
            for payload in payloads
        ]
        session.add_all(rows)
    return rows


def discard_candidates(job_id: str) -> int:
    """Delete every staged row of one job — the cancel-race cleanup: a
    cancel landing between the staging commit and the terminal write
    must not leave ghost rows behind. Returns the number of rows
    removed. Not the accept/reject lifecycle (story 3.2 owns the
    transitions); this only removes a job's own uncommitted rows."""
    with session_scope() as session:
        rows = session.scalars(
            select(models.ProposedCandidate).where(models.ProposedCandidate.job_id == job_id)
        ).all()
        for row in rows:
            session.delete(row)
        return len(rows)


def _check_edge(endpoint: Any, direction: Any, edge_type: Any, committed: set[str]) -> bool:
    """One staged edge is valid when its endpoint is a committed id, its
    direction is a known member, and its type is a string in the closed
    vocabulary. The isinstance guards keep a non-string JSON value
    (list/dict — unhashable) a clean False, never a TypeError."""
    return (
        isinstance(endpoint, str)
        and endpoint in committed
        and isinstance(direction, str)
        and direction in EDGE_DIRECTIONS
        and isinstance(edge_type, str)
        and edge_type in EDGE_TYPES
    )


def _candidate_edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The staged ``edges`` list of a candidate payload (empty when absent
    or not a list — the caller's own contract governs the shape)."""
    edges = payload.get("edges")
    return edges if isinstance(edges, list) else []


def _check_candidate_edges(index: int, payload: dict[str, Any], committed: set[str]) -> None:
    """Reject the batch when any candidate edge references an endpoint
    that is not committed world state or a type outside the vocabulary."""
    for edge in _candidate_edges(payload):
        if not isinstance(edge, dict):
            raise InvalidCandidateError(f"candidate {index}: edge must be an object, got {edge!r}")
        if not _check_edge(
            edge.get("endpoint"), edge.get("direction"), edge.get("type"), committed
        ):
            raise InvalidCandidateError(
                f"candidate {index}: edge does not resolve to committed world state "
                f"or is outside the closed vocabulary: {edge!r}"
            )


def list_candidates(
    campaign_id: str,
    cursor: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> tuple[list[models.ProposedCandidate], str | None]:
    """Campaign-scoped staged candidates, oldest first (rowid order).

    ``cursor`` is the ULID of the last candidate of the previous page
    (the API layer decodes the opaque token, pagination convention).
    Returns ``(candidates, next cursor ULID or None)``.
    """
    if limit < 1:
        raise InvalidJobInputError(f"limit must be >= 1, got {limit}")
    with session_scope() as session:
        if session.get(models.Campaign, campaign_id) is None:
            raise UnknownCampaignError(campaign_id)
        rows = session.scalars(
            select(models.ProposedCandidate)
            .where(
                models.ProposedCandidate.campaign_id == campaign_id,
                literal_column("rowid") > _after_rowid(session, campaign_id, cursor),
            )
            .order_by(literal_column("rowid"))
            .limit(limit + 1)
        ).all()
        page, next_cursor = paging(rows, limit)
        return list(page), next_cursor


def _after_rowid(session: Session, campaign_id: str, cursor: str | None) -> int:
    """Rowid anchor for cursor pagination: list candidates strictly after
    this one.

    The cursor is the last item's ULID (pagination convention); a
    well-formed ULID that names no candidate, or one of another
    campaign, is a user error (422): a fabricated cursor must never
    silently reset the page (epic-1 retro item 2).
    """
    if cursor is None:
        return -1
    row = session.get(models.ProposedCandidate, cursor)
    if row is not None and row.campaign_id != campaign_id:
        raise InvalidJobInputError("cursor names a candidate of another campaign")
    return anchor_rowid(
        session,
        models.ProposedCandidate,
        cursor,
        missing_error=InvalidJobInputError(f"cursor names no candidate: {cursor}"),
    )


__all__ = [
    "EDGE_DIRECTIONS",
    "PROPOSAL_KIND",
    "STATUS_PROPOSED",
    "InvalidCandidateError",
    "list_candidates",
    "stage_candidates",
]
