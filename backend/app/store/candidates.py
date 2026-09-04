"""Proposed-candidate staging (AR7, AR19; spec-3.1).

A ``generate`` job stages its 2-3 surviving candidates here — never in
the entity/edge tables (AR7), so retrieval, export, and every world read
stay untouched. The staging function is NOT the commit path: it produces
no revision and no event; AD-1's single-writer rule is honored by routing
the write through this store module (never raw SQL in ``pipeline/``).
The accept/reject lifecycle lives here too (spec-3.2): ``accept_candidate``
commits the staged subgraph and flips the row to ``accepted`` inside ONE
transaction; ``reject_candidate`` settles the row without touching the
world. Terminal statuses are final — rows are kept for the audit trail.
"""

import json
from typing import Any

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from app.core import ids, time
from app.core.pagination import anchor_rowid, paging

# AD-15: accept must flip the candidate's status and commit its subgraph
# as ONE unit of work, so it calls the internal session-taking ``_commit``
# — not the public ``commit_subgraph``, which owns its session — inside
# the same ``session_scope`` transaction. One call site justifies the
# private import over widening the store's public API.
from app.store import models
from app.store.commit import (
    EDGE_TYPES,
    StoreError,
    UnknownCampaignError,
    _commit,
)
from app.store.db import session_scope
from app.store.jobs import (
    DEFAULT_LIST_LIMIT,
    InvalidJobInputError,
    JobNotFoundError,
)
from app.store.models import (
    PROPOSAL_KIND,
    PROPOSAL_STATUS,
    STATUS_ACCEPTED,
    STATUS_PROPOSED,
    STATUS_REJECTED,
)
from app.store.read import latest_revision, world_state

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


class CandidateNotFoundError(StoreError):
    """Unknown candidate id — or one of another campaign; the two are
    the same indistinguishable error (-> 404, no oracle)."""

    def __init__(self, candidate_id: str) -> None:
        super().__init__(f"unknown candidate: {candidate_id}")
        self.candidate_id = candidate_id


class CandidateSettledError(StoreError):
    """A lifecycle transition was attempted on an already-settled
    candidate — terminal statuses are final (-> 409)."""

    def __init__(self, candidate_id: str, status: str) -> None:
        super().__init__(f"candidate {candidate_id} is already {status}")
        self.candidate_id = candidate_id
        self.status = status


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
        job = session.get(models.Job, job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.campaign_id != campaign_id:
            raise InvalidCandidateError(
                f"job {job_id} belongs to campaign {job.campaign_id}, not {campaign_id}"
            )
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
            except (TypeError, ValueError, RecursionError) as exc:
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
    """Delete every still-proposed staged row of one job — the
    cancel-race cleanup: a cancel landing between the staging commit and
    the terminal write must not leave ghost rows behind. Returns the
    number of rows removed. Not the lifecycle mechanism — terminal
    statuses are final and ``accept_candidate``/``reject_candidate`` own
    those transitions; settled (accepted/rejected) rows are audit-trail
    facts and are NEVER deleted here, only a job's own uncommitted rows."""
    with session_scope() as session:
        rows = session.scalars(
            select(models.ProposedCandidate).where(
                models.ProposedCandidate.job_id == job_id,
                models.ProposedCandidate.status == STATUS_PROPOSED,
            )
        ).all()
        for row in rows:
            session.delete(row)
        return len(rows)


def accept_candidate(
    campaign_id: str,
    candidate_id: str,
) -> tuple[models.ProposedCandidate, models.Revision]:
    """Make a staged candidate real: commit its subgraph, settle the row.

    ONE transaction (FR11, AD-15): the subgraph commit and the
    ``accepted`` status flip share the same ``session_scope`` — any
    ``StoreError`` rolls back both, leaving the row ``proposed`` and
    zero new revisions. The new entity is a fresh ULID the staged edges
    are wired against; it is not any existing entity's id, so the commit
    path structurally cannot mutate accepted state (FR11, AR4) — and no
    edge is re-targeted. The commit path is the accept-time authority
    for endpoints deleted since staging: a dead endpoint raises
    ``DanglingEdgeError`` (422, rebase-or-reject, AD-2) with no state
    change. ``base_revision`` is the head read inside THIS transaction
    (``None`` on an empty world — the accept becomes revision 1).
    Returns ``(candidate row, new revision)``. Raises
    ``CandidateNotFoundError`` (unknown or foreign-campaign id),
    ``CandidateSettledError`` (already accepted/rejected),
    ``InvalidCandidateError`` (payload shape the staging contract does
    not guarantee: non-dict payload, non-str name, non-int or missing
    edge counter, edge outside the closed vocabulary — and a staged row
    with zero edges commits nothing and fails with the commit path's
    ``OrphanEntityError``), or whatever else the commit path rejects
    with (``InvalidEdgeCounterError``/``DanglingEdgeError``/...).
    """
    with session_scope() as session:
        candidate = session.get(models.ProposedCandidate, candidate_id)
        if candidate is None or candidate.campaign_id != campaign_id:
            raise CandidateNotFoundError(candidate_id)
        if candidate.status != STATUS_PROPOSED:
            raise CandidateSettledError(candidate_id, candidate.status)
        if candidate.kind != PROPOSAL_KIND:
            # A future staging kind or a corrupt row must never silently
            # commit as a character — the closed kind set is enforced at
            # staging and in the DB; this guards rows that bypass both.
            raise InvalidCandidateError(
                f"candidate {candidate_id}: kind must be {PROPOSAL_KIND!r}, got {candidate.kind!r}"
            )
        if not isinstance(candidate.payload, dict):
            raise InvalidCandidateError(f"candidate {candidate_id}: payload must be an object")
        payload = dict(candidate.payload)
        name = payload.get("name")
        if not isinstance(name, str):
            raise InvalidCandidateError(f"candidate {candidate_id}: payload has no name")
        # A fresh ULID minted up front so the staged edges can name the
        # new entity: the commit path needs concrete ids to wire edges,
        # and this id is not any existing entity's — ``_commit`` finds no
        # row for it and creates (never updates). The spec's "id=None"
        # wording describes the invariant (fresh ULID, never an existing
        # entity), not the minting site.
        new_entity_id = ids.new_id()
        entity = models.EntityInput(
            kind="character",
            name=name,
            data={key: value for key, value in payload.items() if key != "edges"},
            id=new_entity_id,
        )
        edges = [
            _accept_edge(new_entity_id, candidate_id, edge) for edge in _candidate_edges(payload)
        ]
        latest = latest_revision(session, campaign_id)
        revision = _commit(
            session,
            campaign_id,
            [entity],
            edges,
            latest.id if latest is not None else None,
        )
        candidate.status = STATUS_ACCEPTED
    return candidate, revision


def _accept_edge(new_entity_id: str, candidate_id: str, edge: Any) -> models.EdgeInput:
    """One staged edge record -> ``EdgeInput`` wired against the new
    entity: ``outbound`` puts the candidate on the source side, ``inbound``
    on the destination side. ``type`` passes through; ``counter`` must be
    present and an int — the staging contract always writes one, and a
    row outside that contract must not silently default to 1 (a missing
    or non-int counter is an ``InvalidCandidateError``, 422)."""
    if not isinstance(edge, dict):
        raise InvalidCandidateError(
            f"candidate {candidate_id}: edge must be an object, got {edge!r}"
        )
    endpoint = edge.get("endpoint")
    direction = edge.get("direction")
    edge_type = edge.get("type")
    if (
        not isinstance(endpoint, str)
        or not isinstance(direction, str)
        or direction not in EDGE_DIRECTIONS
        or not isinstance(edge_type, str)
    ):
        raise InvalidCandidateError(
            f"candidate {candidate_id}: edge does not resolve to committed world "
            f"state or is outside the closed vocabulary: {edge!r}"
        )
    counter = edge.get("counter")
    if type(counter) is not int:
        raise InvalidCandidateError(
            f"candidate {candidate_id}: edge counter must be an int, got {counter!r}"
        )
    if direction == "outbound":
        return models.EdgeInput(src=new_entity_id, dst=endpoint, type=edge_type, counter=counter)
    return models.EdgeInput(src=endpoint, dst=new_entity_id, type=edge_type, counter=counter)


def reject_candidate(campaign_id: str, candidate_id: str) -> models.ProposedCandidate:
    """Settle a candidate ``rejected`` — the world is untouched (AR7).

    One transaction flipping ``proposed -> rejected``: no revision, no
    event, no world read. The row is kept for the audit trail (never
    deleted); terminal statuses are final.
    """
    with session_scope() as session:
        candidate = session.get(models.ProposedCandidate, candidate_id)
        if candidate is None or candidate.campaign_id != campaign_id:
            raise CandidateNotFoundError(candidate_id)
        if candidate.status != STATUS_PROPOSED:
            raise CandidateSettledError(candidate_id, candidate.status)
        candidate.status = STATUS_REJECTED
    return candidate


def list_candidates(
    campaign_id: str,
    cursor: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    status: str = STATUS_PROPOSED,
) -> tuple[list[models.ProposedCandidate], str | None]:
    """Campaign-scoped staged candidates, oldest first (rowid order).

    ``status`` filters the lifecycle set — the API layer validates it
    against ``PROPOSAL_STATUS`` and defaults to ``proposed`` so the
    accept screen never regresses into settled rows.

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
                models.ProposedCandidate.status == status,
                literal_column("rowid") > _after_rowid(session, campaign_id, cursor),
            )
            .order_by(literal_column("rowid"))
            .limit(limit + 1)
        ).all()
        page, next_cursor = paging(rows, limit)
        return list(page), next_cursor


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
    "PROPOSAL_STATUS",
    "STATUS_ACCEPTED",
    "STATUS_PROPOSED",
    "STATUS_REJECTED",
    "CandidateNotFoundError",
    "CandidateSettledError",
    "InvalidCandidateError",
    "accept_candidate",
    "discard_candidates",
    "list_candidates",
    "reject_candidate",
    "stage_candidates",
]
