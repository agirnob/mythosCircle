"""The single commit path to world state (AD-1).

Every world-state change — build-in, generation accept, DM edit, cascade
delete, future import — lands here: one database transaction, exactly one
new revision, all-or-nothing (AR3). Nothing outside the store writes
world state (AD-13).

Concurrency (AD-2): a commit is staged against a ``base_revision`` — the
head it was read from. If the head moved, the store rejects with
``StaleRevisionError`` carrying the latest revision id; the caller
(pipeline, 1.3/2.x) decides rebase-or-reject. The store itself never
merges — no silent overwrite. The write lock is taken by ``BEGIN
IMMEDIATE`` (see ``store.db._begin_immediate``) before the base check, so
check-then-act is atomic with the write even under concurrency.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import ids, time
from app.store import models
from app.store.db import session_scope
from app.store.read import latest_revision

#: Closed Phase-1 edge vocabulary (AD-5): directed, per-type counter
#: semantics (AD-23). Extensible by adding a type, never by free text.
EDGE_TYPES: frozenset[str] = frozenset(
    {
        "relationship",
        "debt",
        "grudge",
        "loyalty",
        "member_of",
        "located_in",
        "rival_of",
        "kin_of",
        "ally_of",
        "enemy_of",
    }
)

#: Per-type counter semantic (AD-23): debt = amount, grudge/loyalty =
#: score, ally/enemy = intensity. The map is the code contract the
#: pipeline assigns counters against and Phase 3 consumes; members
#: without an entry carry the neutral default (counter informational).
EDGE_COUNTER_SEMANTICS: dict[str, str] = {
    "debt": "amount",
    "grudge": "score",
    "loyalty": "score",
    "ally_of": "intensity",
    "enemy_of": "intensity",
}

DEFAULT_EDGE_COUNTER_SEMANTIC = "neutral"


def edge_counter_semantic(edge_type: str) -> str:
    """The AD-23 counter semantic for an edge type (neutral when unassigned)."""
    return EDGE_COUNTER_SEMANTICS.get(edge_type, DEFAULT_EDGE_COUNTER_SEMANTIC)


#: SQLite's INTEGER is signed 64-bit; representability is part of the
#: store's shape contract (the driver otherwise raises a raw
#: ``OverflowError`` at flush — outside the StoreError/422 family).
#: Semantic ranges (how big a score, whether a debt is negative) stay
#: pipeline-owned.
_SQLITE_INT_MIN = -(2**63)
_SQLITE_INT_MAX = 2**63 - 1


# ---------------------------------------------------------------------------
# Structured store errors (plain exceptions; FastAPI mapping is a later story)
# ---------------------------------------------------------------------------


class StoreError(Exception):
    """Base for structured store rejections — no state is changed."""


class UnknownCampaignError(StoreError):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(f"unknown campaign: {campaign_id}")
        self.campaign_id = campaign_id


class StaleRevisionError(StoreError):
    """Staged against a revision that is no longer the head (AD-2)."""

    def __init__(self, latest_revision_id: str | None) -> None:
        super().__init__("stale base revision — rebase or reject against the latest revision")
        self.latest_revision_id = latest_revision_id


class DanglingEdgeError(StoreError):
    """An edge endpoint is in neither the current revision nor the staged subgraph."""

    def __init__(self, edge: models.EdgeInput, missing_endpoint: str) -> None:
        super().__init__(
            f"dangling edge: {edge.src} --{edge.type}--> {edge.dst} "
            f"(endpoint {missing_endpoint} does not exist)"
        )
        self.edge = edge
        self.missing_endpoint = missing_endpoint


class InvalidEdgeTypeError(StoreError):
    """An edge type outside the closed Phase-1 vocabulary (AD-5)."""

    def __init__(self, edge: models.EdgeInput) -> None:
        super().__init__(f"edge type not in vocabulary: {edge.type!r}")
        self.edge = edge


class InvalidEdgeCounterError(StoreError):
    """An edge counter that is not an integer SQLite can store — shape
    rejection at the store boundary (AD-23; SQLite does not enforce the
    Integer column, and larger magnitudes overflow the driver at flush)."""

    def __init__(self, edge: models.EdgeInput) -> None:
        super().__init__(
            f"edge counter must be an integer in SQLite's signed 64-bit "
            f"range: {edge.counter!r} ({edge.src} --{edge.type}--> {edge.dst})"
        )
        self.edge = edge


class DuplicateEntityError(StoreError):
    """The same entity ULID staged twice in one subgraph."""

    def __init__(self, entity_id: str) -> None:
        super().__init__(f"entity staged twice: {entity_id}")
        self.entity_id = entity_id


class CrossCampaignConflictError(StoreError):
    """A staged ULID belongs to another campaign's world."""

    def __init__(self, ulid: str) -> None:
        super().__init__(f"staged id belongs to another campaign: {ulid}")
        self.ulid = ulid


class InvalidUlidError(StoreError):
    """An explicit staged id is not a 26-char Crockford ULID (conventions.md)."""

    def __init__(self, ulid: str) -> None:
        super().__init__(f"id is not a ULID: {ulid!r}")
        self.ulid = ulid


class EmptySubgraphError(StoreError):
    """An empty subgraph would pollute the revision history."""


class DuplicateEdgeError(StoreError):
    """The same edge ULID is staged twice, or a new edge would duplicate an
    existing (campaign, src, dst, type) relationship (AD-23)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class EdgeRetargetError(StoreError):
    """An explicit edge id changes src/dst/type — edge re-targeting is
    forbidden (AD-2); only the counter may change."""

    def __init__(self, edge: models.EdgeInput, existing: models.Edge) -> None:
        super().__init__(
            f"edge {edge.id}: src/dst/type are immutable "
            f"(existing: {existing.src} --{existing.type}--> {existing.dst}); "
            "only the counter may change (AD-2)"
        )
        self.edge = edge


class CorruptEventError(StoreError):
    """An event payload is structurally malformed — reject, never guess."""

    def __init__(self, event_id: str, missing: str) -> None:
        super().__init__(f"corrupt event {event_id}: {missing}")
        self.event_id = event_id


# ---------------------------------------------------------------------------
# Campaign seed helper (campaign CRUD is story 1.6)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Commit path
# ---------------------------------------------------------------------------


def commit_subgraph(
    campaign_id: str,
    entities: Sequence[models.EntityInput] = (),
    edges: Sequence[models.EdgeInput] = (),
    base_revision: str | None = None,
) -> models.Revision:
    """Commit a staged subgraph atomically; returns the new revision.

    One transaction, exactly one new revision, all-or-nothing (AR3).
    Rejects (no state change) with ``UnknownCampaignError``,
    ``StaleRevisionError``, ``InvalidEdgeTypeError``,
    ``InvalidEdgeCounterError``, ``DanglingEdgeError``,
    ``DuplicateEntityError``, ``DuplicateEdgeError``,
    ``EdgeRetargetError``, ``CrossCampaignConflictError``,
    ``InvalidUlidError``, or ``EmptySubgraphError``.
    """
    with session_scope() as session:
        return _commit(session, campaign_id, list(entities), list(edges), base_revision)


def _commit(
    session: Session,
    campaign_id: str,
    entities: list[models.EntityInput],
    edges: list[models.EdgeInput],
    base_revision: str | None,
) -> models.Revision:
    if session.get(models.Campaign, campaign_id) is None:
        raise UnknownCampaignError(campaign_id)
    if not entities and not edges:
        raise EmptySubgraphError("refusing to commit an empty subgraph")

    latest = latest_revision(session, campaign_id)
    _check_base(latest, base_revision)

    entity_rows = {
        row.id: row
        for row in session.scalars(
            select(models.Entity).where(models.Entity.campaign_id == campaign_id)
        )
    }
    edge_rows = {
        row.id: row
        for row in session.scalars(
            select(models.Edge).where(models.Edge.campaign_id == campaign_id)
        )
    }
    _check_entity_ids(session, campaign_id, entities)
    _check_edge_ids(session, campaign_id, edges)

    staged: list[tuple[str, models.EntityInput, models.Entity | None]] = []
    seen: set[str] = set()
    for entity in entities:
        if entity.id is not None and entity.id in seen:
            raise DuplicateEntityError(entity.id)
        if entity.id is not None:
            seen.add(entity.id)
        existing = entity_rows.get(entity.id) if entity.id is not None else None
        staged.append((entity.id or ids.new_id(), entity, existing))

    known_ids = set(entity_rows) | {staged_id for staged_id, _, _ in staged}
    relationship_rows = {(row.src, row.dst, row.type): row for row in edge_rows.values()}
    seen_edge_ids: set[str] = set()
    staged_relationships: set[tuple[str, str, str]] = set()
    for edge in edges:
        if edge.type not in EDGE_TYPES:
            raise InvalidEdgeTypeError(edge)
        counter = edge.counter
        if type(counter) is not int or not _SQLITE_INT_MIN <= counter <= _SQLITE_INT_MAX:
            raise InvalidEdgeCounterError(edge)
        for endpoint in (edge.src, edge.dst):
            if endpoint not in known_ids:
                raise DanglingEdgeError(edge, endpoint)
        if edge.id is not None:
            if edge.id in seen_edge_ids:
                raise DuplicateEdgeError(f"edge staged twice: {edge.id}")
            seen_edge_ids.add(edge.id)
            existing_edge = edge_rows.get(edge.id)
            if existing_edge is not None and (
                edge.src != existing_edge.src
                or edge.dst != existing_edge.dst
                or edge.type != existing_edge.type
            ):
                raise EdgeRetargetError(edge, existing_edge)
            # An explicit id staging a brand-new edge must still respect the
            # (src, dst, type) uniqueness invariant (AD-23) — otherwise the
            # raw unique-constraint IntegrityError escapes at flush.
            if existing_edge is None:
                _reject_duplicate_relationship(edge, relationship_rows, staged_relationships)
        else:
            _reject_duplicate_relationship(edge, relationship_rows, staged_relationships)

    now = time.now()
    revision = models.Revision(
        id=ids.new_id(),
        campaign_id=campaign_id,
        base_revision=latest.id if latest is not None else None,
        created_at=now,
    )
    session.add(revision)

    for entity_id, entity, existing in staged:
        if existing is None:
            before: dict[str, Any] | None = None
            session.add(
                models.Entity(
                    id=entity_id,
                    campaign_id=campaign_id,
                    kind=entity.kind,
                    name=entity.name,
                    text=entity.text,
                    data=entity.data,
                    created_at=now,
                )
            )
            after = _entity_input_snapshot(entity, now)
            event_type = "entity_created"
        else:
            before = _entity_snapshot(existing)
            existing.kind = entity.kind
            existing.name = entity.name
            existing.text = entity.text
            existing.data = entity.data
            # Snapshot the row after mutation: the payload's created_at must
            # match the materialized row's (the event log is the truth).
            after = _entity_snapshot(existing)
            event_type = "entity_updated"
        _add_event(
            session,
            campaign_id,
            revision.id,
            event_type,
            {"id": entity_id, "before": before, "after": after},
            now,
        )

    for edge in edges:
        existing_edge = edge_rows.get(edge.id) if edge.id is not None else None
        edge_id = edge.id or ids.new_id()
        if existing_edge is None:
            before = None
            session.add(
                models.Edge(
                    id=edge_id,
                    campaign_id=campaign_id,
                    src=edge.src,
                    dst=edge.dst,
                    type=edge.type,
                    counter=edge.counter,
                    created_at=now,
                )
            )
            after = _edge_input_snapshot(edge, now)
            event_type = "edge_created"
        else:
            before = _edge_snapshot(existing_edge)
            existing_edge.counter = edge.counter
            after = _edge_snapshot(existing_edge)
            event_type = "edge_updated"
        _add_event(
            session,
            campaign_id,
            revision.id,
            event_type,
            {"id": edge_id, "before": before, "after": after},
            now,
        )

    return revision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_base(latest: models.Revision | None, base_revision: str | None) -> None:
    if (latest is None) != (base_revision is None):
        raise StaleRevisionError(latest.id if latest is not None else None)
    if latest is not None and latest.id != base_revision:
        raise StaleRevisionError(latest.id)


def _check_entity_ids(
    session: Session, campaign_id: str, entities: list[models.EntityInput]
) -> None:
    """Explicit entity ULIDs must not collide with other campaigns' worlds."""
    for entity in entities:
        if entity.id is None:
            continue
        _check_ulid(entity.id)
        row = session.get(models.Entity, entity.id)
        if row is not None and row.campaign_id != campaign_id:
            raise CrossCampaignConflictError(entity.id)


def _check_edge_ids(session: Session, campaign_id: str, edges: list[models.EdgeInput]) -> None:
    """Explicit edge ULIDs must not belong to other campaigns' worlds.

    The row is fetched by primary key without a campaign filter — the
    campaign-filtered ``edge_rows`` snapshot could never see another
    campaign's edge, which would make the cross-campaign check dead code.
    """
    for edge in edges:
        if edge.id is None:
            continue
        _check_ulid(edge.id)
        row = session.get(models.Edge, edge.id)
        if row is not None and row.campaign_id != campaign_id:
            raise CrossCampaignConflictError(edge.id)


def _check_ulid(ulid: str) -> None:
    """Enforce the ULID convention at the store boundary (conventions.md).

    The store is the last line of defense — no API layer exists yet, and
    SQLite does not enforce ``String(26)`` length. Non-ULID ids would break
    cursor pagination and sortability, so caller-supplied ids are validated
    here, never adopted verbatim.
    """
    if not ids.is_valid_ulid(ulid):
        raise InvalidUlidError(ulid)


def _reject_duplicate_relationship(
    edge: models.EdgeInput,
    relationship_rows: dict[tuple[str, str, str], models.Edge],
    staged_relationships: set[tuple[str, str, str]],
) -> None:
    """Reject a new edge whose (src, dst, type) already exists (AD-23)."""
    relationship = (edge.src, edge.dst, edge.type)
    if relationship in relationship_rows or relationship in staged_relationships:
        raise DuplicateEdgeError(
            f"edge {edge.src} --{edge.type}--> {edge.dst} already exists — "
            "stage its id to update the counter (AD-23)"
        )
    staged_relationships.add(relationship)


def _entity_snapshot(row: models.Entity) -> dict[str, Any]:
    return {
        "kind": row.kind,
        "name": row.name,
        "text": row.text,
        "data": row.data,
        "created_at": row.created_at,
    }


def _entity_input_snapshot(entity: models.EntityInput, created_at: str) -> dict[str, Any]:
    return {
        "kind": entity.kind,
        "name": entity.name,
        "text": entity.text,
        "data": entity.data,
        "created_at": created_at,
    }


def _edge_snapshot(row: models.Edge) -> dict[str, Any]:
    return {
        "src": row.src,
        "dst": row.dst,
        "type": row.type,
        "counter": row.counter,
        "created_at": row.created_at,
    }


def _edge_input_snapshot(edge: models.EdgeInput, created_at: str) -> dict[str, Any]:
    return {
        "src": edge.src,
        "dst": edge.dst,
        "type": edge.type,
        "counter": edge.counter,
        "created_at": created_at,
    }


def _add_event(
    session: Session,
    campaign_id: str,
    revision_id: str,
    event_type: str,
    payload: dict[str, Any],
    created_at: str,
) -> None:
    session.add(
        models.Event(
            id=ids.new_id(),
            campaign_id=campaign_id,
            revision_id=revision_id,
            type=event_type,
            payload=payload,
            created_at=created_at,
        )
    )
