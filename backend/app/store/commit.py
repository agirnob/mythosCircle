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

import json
from collections.abc import Sequence
from types import MappingProxyType
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core import ids, time
from app.store import models
from app.store.db import session_scope
from app.store.read import entity_live_edges, latest_revision

#: Closed edge vocabulary (AD-5): directed, per-type counter semantics
#: (AD-23). Extensible by adding a type, never by free text. The
#: 2026-09-13 expansion (owner verdict: "add more relation vocabulary")
#: added the six role-bearing types — bases_at, controls, employs,
#: worships, hails_from, protects — whose meanings the d7 world forced
#: into the catch-all ``relationship`` (100 of 114 rows).
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
        "bases_at",
        "controls",
        "employs",
        "worships",
        "hails_from",
        "protects",
    }
)

#: Per-type counter semantic (AD-23): debt = amount, grudge/loyalty =
#: score, ally/enemy = intensity. The map is the code contract the
#: pipeline assigns counters against and Phase 3 consumes; members
#: without an entry carry the neutral default (counter informational).
#: Immutable by contract (2.2 defer, hardened in 2.3): the pipeline
#: imports the map into prompts, so a mutable dict would let one module
#: change the prompt contract for everyone.
EdgeCounterSemantic = Literal["amount", "score", "intensity", "neutral"]

EDGE_COUNTER_SEMANTICS: MappingProxyType[str, EdgeCounterSemantic] = MappingProxyType(
    {
        "debt": "amount",
        "grudge": "score",
        "loyalty": "score",
        "ally_of": "intensity",
        "enemy_of": "intensity",
        "controls": "intensity",
        "worships": "score",
        "protects": "intensity",
    }
)

DEFAULT_EDGE_COUNTER_SEMANTIC: EdgeCounterSemantic = "neutral"


def edge_counter_semantic(edge_type: str) -> EdgeCounterSemantic:
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


class InvalidEntityRecordError(StoreError):
    """A hand edit (spec-3.6 PATCH) would break the AR24 shape of a
    shape-valid record — or violates the name/text wire contracts. The
    owner's conditional-validation rule (2026-09-05): only records that
    already satisfy the character shape must keep satisfying it after
    the merge (they stay re-rollable and exportable); bare records are
    written unconstrained. 422, zero revisions."""


class EntityEditConflictError(StoreError):
    """A regenerate-entity accept would overwrite a DM hand edit that
    landed on the target since staging: the staged ``entity_base_data``
    no longer matches the committed record (or is NULL — a pre-3.6 row
    that cannot be verified). Fail closed (spec-3.6): never a silent
    merge — the caller surfaces the three-way escape (re-roll / accept
    anyway with explicit confirm / cancel)."""

    def __init__(self, entity_id: str) -> None:
        super().__init__(
            f"entity {entity_id} changed since this candidate was generated — "
            "re-roll (rebase), accept anyway (overwrite), or cancel (reject)"
        )
        self.target_id = entity_id


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


class OrphanEntityError(StoreError):
    """A newly created entity participates in zero edges (FR2, AD-23).

    The store-level backstop for commits that bypass the pipeline's
    ``_validate_subgraph``: every *new* entity must carry at least one
    edge whose other endpoint is staged or already committed.
    Self-loop edges never count — they are rejected outright and are
    not an edge "into existing world state".
    """

    def __init__(self, orphans: list[tuple[str, str]]) -> None:
        named = ", ".join(f"{name!r} ({entity_id})" for entity_id, name in orphans)
        super().__init__(
            f"newly created entity with no edges (FR2): {named} — every new "
            "entity needs at least one edge into staged or existing state"
        )
        self.orphans = orphans


class UnknownEntityError(StoreError):
    """A delete target does not exist in this campaign's world (FR4)."""

    def __init__(self, entity_id: str) -> None:
        super().__init__(f"unknown entity: {entity_id}")
        self.entity_id = entity_id


class UnknownEdgeError(StoreError):
    """A delete/update target edge does not exist in this campaign's world,
    or names another campaign's edge (the two are indistinguishable, AD-9 —
    no oracle)."""

    def __init__(self, edge_id: str) -> None:
        super().__init__(f"unknown edge: {edge_id}")
        self.edge_id = edge_id


class LiveEdgesError(StoreError):
    """A delete with live edges was requested without cascade confirm
    (FR4, AD-5). Carries the affected neighbor entities — id + name per
    neighbor — so the DM sees the list before confirming (AD-5)."""

    def __init__(self, entity_id: str, affected: list[dict[str, str]]) -> None:
        named = ", ".join(f"{item['name']!r} ({item['id']})" for item in affected)
        super().__init__(
            f"entity {entity_id} has live edges touching {len(affected)} "
            f"neighbor(s): {named} — confirm cascade deletion to proceed"
        )
        self.entity_id = entity_id
        #: Neighbor entities, rowid-ordered, deduplicated: {"id", "name"}.
        self.affected = affected


class SelfLoopEdgeError(StoreError):
    """An edge whose endpoints are the same entity (FR2, AD-23).

    A self-loop is not an edge "into existing world state" — the
    pipeline's ``_validate_subgraph`` forbids them, and the store is
    the backstop for commits that bypass the pipeline, so it rejects
    them at the same boundary (FR2, owner decision 2026-09-03).
    """

    def __init__(self, edge: models.EdgeInput) -> None:
        super().__init__(
            f"self-loop edge rejected: edge {edge.id or '(new)'} "
            f"({edge.src} -> {edge.dst}, type {edge.type!r}) — an entity "
            "cannot be its own edge endpoint"
        )
        self.edge = edge


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
    *,
    allow_orphans: bool = False,
) -> models.Revision:
    """Commit a staged subgraph atomically; returns the new revision.

    One transaction, exactly one new revision, all-or-nothing (AR3).
    Rejects (no state change) with ``UnknownCampaignError``,
    ``StaleRevisionError``, ``InvalidEdgeTypeError``,
    ``InvalidEdgeCounterError``, ``DanglingEdgeError``,
    ``DuplicateEdgeError``, ``EdgeRetargetError``,
    ``CrossCampaignConflictError``, ``InvalidUlidError``,
    ``UnknownEdgeError`` (an explicit edge id names no edge of this
    campaign — explicit ids update, they never create), or
    ``OrphanEntityError`` (FR2: a newly created entity with zero edges
    into staged or existing state — unless ``allow_orphans``).

    ``allow_orphans`` skips the FR2 create-only check for this commit
    alone. Only the build-in wave-1 runner passes it (owner verdict
    2026-09-11: edgeless wave-1 commits, the DM prunes); every other
    caller — DM edits, wave 2, candidate accepts — keeps the default
    rejection.
    """
    with session_scope() as session:
        return _commit(
            session,
            campaign_id,
            list(entities),
            list(edges),
            base_revision,
            allow_orphans=allow_orphans,
        )


def _commit(
    session: Session,
    campaign_id: str,
    entities: list[models.EntityInput],
    edges: list[models.EdgeInput],
    base_revision: str | None,
    *,
    allow_orphans: bool = False,
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
        if edge.src == edge.dst:
            # A self-loop is not an edge "into existing world state" —
            # the pipeline forbids them and the store is the backstop
            # for commits that bypass the pipeline (FR2, owner decision
            # 2026-09-03).
            raise SelfLoopEdgeError(edge)
        if edge.id is not None:
            if edge.id in seen_edge_ids:
                raise DuplicateEdgeError(f"edge staged twice: {edge.id}")
            seen_edge_ids.add(edge.id)
            existing_edge = edge_rows.get(edge.id)
            if existing_edge is None:
                # Strict contract (spec-3.4): an explicit edge id MUST name
                # an existing edge of this campaign — creation is
                # id=None only. Allowing an explicit id to create would let
                # a PATCH staged against a concurrently deleted edge
                # silently resurrect it under the same ULID (spec-3-4
                # review round 1). Unknown ids are a structured rejection,
                # never a guessed create.
                raise UnknownEdgeError(edge.id)
            if (
                edge.src != existing_edge.src
                or edge.dst != existing_edge.dst
                or edge.type != existing_edge.type
            ):
                raise EdgeRetargetError(edge, existing_edge)
        else:
            _reject_duplicate_relationship(edge, relationship_rows, staged_relationships)

    # FR2 no-orphans (store-level backstop, spec-2.5): every *newly
    # created* entity must participate in at least one edge whose other
    # endpoint is staged or already committed. Updates and edge-only
    # commits are unaffected; wave-1's first commit (empty world) commits
    # because its edges are staged in the same subgraph. Self-loops never
    # connect: the pipeline forbids them outright and they do not weave
    # the entity into the world. Skipped only under ``allow_orphans``
    # (build-in wave 1, owner verdict 2026-09-11).
    new_entity_ids = {staged_id for staged_id, _entity, existing in staged if existing is None}
    if new_entity_ids and not allow_orphans:
        connected: set[str] = set()
        for edge in edges:
            if edge.src != edge.dst:
                connected.add(edge.src)
                connected.add(edge.dst)
        for row in edge_rows.values():
            if row.src != row.dst:
                connected.add(row.src)
                connected.add(row.dst)
        orphans = [
            (staged_id, entity.name)
            for staged_id, entity, existing in staged
            if existing is None and staged_id not in connected
        ]
        if orphans:
            raise OrphanEntityError(orphans)

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
            # Force the UPDATE even when the new data compares ``==`` to
            # the old (SQLAlchemy skips a flush when the assigned value
            # equals the stored one — ``1 == True``), which would leave
            # the row stale while the event log records the edit; the
            # event log and the materialized row must never diverge.
            flag_modified(existing, "data")
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


def delete_entity(
    campaign_id: str,
    entity_id: str,
    *,
    cascade: bool = False,
    base_revision: str | None = None,
) -> models.Revision:
    """Delete one entity through the commit path (FR4, AD-5, AD-23).

    With live edges and ``cascade=False`` (the default) the delete is a
    no-op failure: ``LiveEdgesError`` carries the affected neighbor
    entities (id + name) so the caller can list them before confirming.
    With ``cascade=True`` the entity plus every edge touching it leaves
    in exactly one revision — neighbors survive, never deleted
    transitively, so the graph holds zero dangling edges (AD-23). An
    entity with zero live edges deletes without confirmation (AD-5
    requires it only "with live edges").

    The revision appends ``entity_deleted``/``edge_deleted`` events with
    ``before`` snapshots matching undo's ``_ENTITY_KEYS``/``_EDGE_KEYS``
    contract exactly — undoing the delete revision recreates the rows
    with stable ULIDs via the existing undo machinery, zero undo changes.
    Media manifest rows leave in the same transaction (AD-10, spec-4.3):
    they are not world graph — no events, no revision delta — and undo
    does NOT restore them (``store/undo.py``); regeneration is the
    recovery. Files are reclaimed post-commit by the API layer, so a
    crash between commit and reclaim leaves files but no dangling row.

    Rejects (no state change) with ``UnknownCampaignError``,
    ``UnknownEntityError`` (unknown or foreign-campaign entity),
    ``StaleRevisionError`` (a supplied ``base_revision`` must match the
    head — the DELETE_STALE row; ``None`` targets the current head, the
    DM wire default), or ``LiveEdgesError``.
    """
    with session_scope() as session:
        return _delete_entity(session, campaign_id, entity_id, cascade, base_revision)


def _delete_entity(
    session: Session,
    campaign_id: str,
    entity_id: str,
    cascade: bool,
    base_revision: str | None,
) -> models.Revision:
    if session.get(models.Campaign, campaign_id) is None:
        raise UnknownCampaignError(campaign_id)
    latest = latest_revision(session, campaign_id)
    if base_revision is not None:
        # Optimistic concurrency is opt-in at the store boundary: an
        # omitted base targets the current head (the DM's DELETE carries
        # none), a supplied base must match it (DELETE_STALE, AD-2).
        _check_base(latest, base_revision)
    entity = session.scalars(
        select(models.Entity).where(
            models.Entity.campaign_id == campaign_id,
            models.Entity.id == entity_id,
        )
    ).first()
    if entity is None:
        # Foreign-campaign ULIDs are invisible to this campaign's world —
        # unknown and foreign are indistinguishable (AD-9).
        raise UnknownEntityError(entity_id)

    live_edges = entity_live_edges(session, campaign_id, entity_id)
    if live_edges and not cascade:
        neighbors: dict[str, str] = {}
        for edge in live_edges:
            if edge.src == edge.dst:
                # Defensive: the commit path rejects self-loops
                # (SelfLoopEdgeError), so a live self-loop cannot exist;
                # if one ever did, the entity is not its own neighbor.
                continue
            neighbor_id = edge.dst if edge.src == entity_id else edge.src
            neighbor = session.get(models.Entity, neighbor_id)
            neighbors.setdefault(neighbor_id, neighbor.name if neighbor else neighbor_id)
        raise LiveEdgesError(
            entity_id, [{"id": nid, "name": name} for nid, name in neighbors.items()]
        )

    now = time.now()
    revision = models.Revision(
        id=ids.new_id(),
        campaign_id=campaign_id,
        base_revision=latest.id if latest is not None else None,
        created_at=now,
    )
    session.add(revision)
    for edge in live_edges:
        _add_event(
            session,
            campaign_id,
            revision.id,
            "edge_deleted",
            {"id": edge.id, "before": _edge_snapshot(edge), "after": None},
            now,
        )
        session.delete(edge)
    _add_event(
        session,
        campaign_id,
        revision.id,
        "entity_deleted",
        {"id": entity_id, "before": _entity_snapshot(entity), "after": None},
        now,
    )
    # Reclaim the entity's media rows inside this transaction (AD-10,
    # spec-4.3) — the store's single media-row deletion seam. No events,
    # no revision delta: manifest rows are an index, not graph state.
    # Function-local import: store.media imports this module's errors.
    from app.store.media import delete_entity_media

    delete_entity_media(session, campaign_id, entity_id)
    session.delete(entity)
    return revision


def delete_edge(
    campaign_id: str,
    edge_id: str,
    *,
    base_revision: str | None = None,
) -> models.Revision:
    """Delete one edge through the commit path (FR9, AD-23).

    Unlike ``delete_entity`` there is NO cascade/confirm gate: deleting a
    single edge never dangles (edges are the connectors) and never orphans
    an entity (the no-orphan rule is entity-create-only) — it is always
    safe, one revision, all-or-nothing. The revision appends one
    ``edge_deleted`` event whose ``before`` snapshot matches undo's
    ``_EDGE_KEYS`` contract exactly — undoing the delete revision
    recreates the edge with a stable ULID via the existing undo machinery
    (already exercised by cascade delete), zero undo changes. Neighbors
    survive.

    Rejects (no state change) with ``UnknownCampaignError``,
    ``UnknownEdgeError`` (unknown or foreign-campaign edge id), or
    ``StaleRevisionError`` (a supplied ``base_revision`` must match the
    head; ``None`` targets the current head, the DM wire default).
    """
    with session_scope() as session:
        return _delete_edge(session, campaign_id, edge_id, base_revision)


def _delete_edge(
    session: Session,
    campaign_id: str,
    edge_id: str,
    base_revision: str | None,
) -> models.Revision:
    if session.get(models.Campaign, campaign_id) is None:
        raise UnknownCampaignError(campaign_id)
    latest = latest_revision(session, campaign_id)
    if base_revision is not None:
        # Optimistic concurrency is opt-in at the store boundary: an
        # omitted base targets the current head (the DM's DELETE carries
        # none), a supplied base must match it (DELETE_STALE, AD-2).
        _check_base(latest, base_revision)
    edge = session.scalars(
        select(models.Edge).where(
            models.Edge.campaign_id == campaign_id,
            models.Edge.id == edge_id,
        )
    ).first()
    if edge is None:
        # Foreign-campaign ULIDs are invisible to this campaign's world —
        # unknown and foreign are indistinguishable (AD-9).
        raise UnknownEdgeError(edge_id)

    now = time.now()
    revision = models.Revision(
        id=ids.new_id(),
        campaign_id=campaign_id,
        base_revision=latest.id if latest is not None else None,
        created_at=now,
    )
    session.add(revision)
    _add_event(
        session,
        campaign_id,
        revision.id,
        "edge_deleted",
        {"id": edge.id, "before": _edge_snapshot(edge), "after": None},
        now,
    )
    session.delete(edge)
    return revision


def update_entity(
    campaign_id: str,
    entity_id: str,
    *,
    patch: dict[str, Any],
    base_revision: str | None = None,
) -> models.Revision:
    """Hand-edit one committed entity through the commit path
    (spec-3.6, FR10) — the store's first direct committed-entity content
    write.

    Partial fields (identity anchor, lore sections, ``stat_block``,
    ``world_integration``, ``boss``, ``text``, unknown keys) are merged
    onto the current record and committed as exactly ONE
    ``entity_updated`` revision via ``EntityInput(id=entity_id)`` — the
    existing in-place machinery (ULID stable, edges untouched, undo
    restores the prior revision). An explicit ``null`` value DELETES
    the data key (removal is the delete semantic); on an absent key that
    is a no-op. ``name`` edits sync both ``data["name"]`` and the
    ``Entity.name`` column in the same transaction.

    Shape validation is CONDITIONAL (owner decision 2026-09-05): only a
    record that already satisfies the AR24 sectioned shape must still
    satisfy it after the merge (``InvalidEntityRecordError`` 422, zero
    revisions, naming the break) — it stays re-rollable and exportable;
    a bare record (faction/place/build-in character, data fails the
    shape) is written unconstrained — no shape forcing, no fabricated
    sections. ``text`` must be ``str`` or ``null`` (the wire contract
    every write surface guarantees) and ``name``, when present, a
    non-blank string. A merge byte-identical to the current record
    (``text`` included) commits NOTHING — the route still answers 204,
    idempotent, zero history pollution (a value-identical PATCH is a
    legal REST retry).

    ``base_revision`` is optimistic concurrency: ``None`` (the DM wire
    default) is resolved to the current head INSIDE the store call, so
    ``_check_base`` still guards the race (a commit landing between the
    resolution and this transaction rejects with ``StaleRevisionError``).
    Rejects (no state change) with ``UnknownCampaignError``,
    ``UnknownEntityError`` (unknown or foreign-campaign entity),
    ``StaleRevisionError``, or ``InvalidEntityRecordError``.
    """
    with session_scope() as session:
        return _update_entity(session, campaign_id, entity_id, patch, base_revision)


def _update_entity(
    session: Session,
    campaign_id: str,
    entity_id: str,
    patch: dict[str, Any],
    base_revision: str | None,
) -> models.Revision:
    if session.get(models.Campaign, campaign_id) is None:
        raise UnknownCampaignError(campaign_id)
    entity = session.scalars(
        select(models.Entity).where(
            models.Entity.campaign_id == campaign_id,
            models.Entity.id == entity_id,
        )
    ).first()
    if entity is None:
        # Foreign-campaign ULIDs are invisible to this campaign's world —
        # unknown and foreign are indistinguishable (AD-9).
        raise UnknownEntityError(entity_id)

    if not isinstance(patch, dict):
        # Every other store boundary guards structured input — a store
        # caller handing update_entity a non-dict patch must get a
        # structured rejection, never an AttributeError (AD-1 discipline).
        raise InvalidEntityRecordError("patch must be an object")

    # ``text`` and ``base_revision`` are column/wire concerns, not data
    # keys: everything else in the patch is content merged onto ``data``.
    content = {key: value for key, value in patch.items() if key not in ("text", "base_revision")}

    # Reserved names (spec-3.6 Never list): ``kind`` is the entity's
    # identity column — a data-level shadow kind would lie next to the
    # real one; ``edges`` are 3-4 territory and a data-level edges key
    # would be silently replaced by regeneration staging. Neither is an
    # unknown key — both are 422 with zero revisions.
    reserved = {"kind", "edges"} & content.keys()
    if reserved:
        raise InvalidEntityRecordError(
            f"patch may not carry the reserved keys: {', '.join(sorted(reserved))}"
        )

    # The str|None text contract every other write surface guarantees: an
    # explicit ``text`` must be a string or null (removal) — anything else
    # is a 422 with zero revisions, never a driver error.
    new_text = patch.get("text", entity.text)
    if new_text is not None and not isinstance(new_text, str):
        raise InvalidEntityRecordError("text must be a string or null")

    # ``name`` edits sync both ``data["name"]`` and the ``Entity.name``
    # column; the column is non-null, so blank/absent names are a 422
    # (EDIT_IDENTITY_NAME, spec-3.6 I/O matrix).
    if "name" in content and (not isinstance(content["name"], str) or not content["name"].strip()):
        raise InvalidEntityRecordError("name must be a non-blank string")

    # Merge: ``{**data, **content}`` with explicit null DELETING the data
    # key — a NOOP_PATCH null-deleting an absent key stays a no-op.
    current_data = entity.data if isinstance(entity.data, dict) else {}
    merged = dict(current_data)
    for key, value in content.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value

    # Strict-JSON write boundary — the same one candidate staging enforces
    # (candidates.py ``allow_nan=False``): ``json.loads`` (the PATCH route's
    # body parser) accepts ``NaN``/``Infinity`` literals, and a data payload
    # carrying one would 500 every later world/snapshot read (Starlette
    # refuses to render out-of-range floats). The store is the last line of
    # defense (AD-1): reject here with zero revisions.
    try:
        json.dumps(merged, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InvalidEntityRecordError(f"data must be strict JSON: {exc}") from exc

    # Conditional AR24 validation (owner decision 2026-09-05): only a
    # record that ALREADY satisfies the character shape must keep
    # satisfying it after the merge (one shared validator for every
    # record that can ever be staged/regenerated/exported); a record
    # that already fails the shape (faction/place/bare build-in) is
    # written unconstrained. Function-local import: candidates.py
    # imports this module, so a module-level import would cycle.
    from app.store.candidates import payload_section_violations

    if payload_section_violations(current_data) == []:
        violations = payload_section_violations(merged)
        # AR24's required-section set does not audit ``stat_block``
        # (AR25 does, and only regeneration is AR25-audited — DM
        # override), but a shape-valid record whose stat block renders
        # through StatBlock.vue must keep an object there: a non-object
        # replacement is a shape break on the record's own contract
        # (EDIT_STATBLOCK, spec-3.6 I/O matrix).
        stat_block = merged.get("stat_block")
        if stat_block is not None and not isinstance(stat_block, dict):
            violations.append("stat_block must be an object")
        if violations:
            raise InvalidEntityRecordError(
                "edit would break the required sectioned shape: " + "; ".join(violations)
            )

    latest = latest_revision(session, campaign_id)
    # Canonical-JSON compare, not Python ``==``: ``==`` conflates
    # ``1 == True == 1.0`` although they serialize differently on the
    # wire — a real ``1`` → ``true`` edit would be silently swallowed
    # here (204, edit dropped). ``allow_nan=False`` cannot raise: the
    # strict-JSON boundary above already rejected non-serializable data.
    if (
        json.dumps(merged, sort_keys=True, allow_nan=False)
        == json.dumps(current_data, sort_keys=True, allow_nan=False)
        and new_text == entity.text
    ):
        # A value-identical PATCH is an idempotent retry: 204, no
        # revision, no history pollution (NOOP_PATCH). An EXPLICIT
        # stale base still rejects — ``_check_base`` is unconditional
        # when a caller names a base (spec-3.6 Always bullet; the
        # omitted-base DM default is a fresh in-tx resolution, so a
        # no-op with no named base is a clean idempotent 204).
        if base_revision is not None:
            _check_base(latest, base_revision)
        # The entity itself was committed, so its world holds >= 1
        # revision — this guard exists only to keep the return type
        # honest (mypy cannot see the invariant).
        if latest is None:
            raise CorruptEventError(entity_id, f"entity {entity_id} exists without any revision")
        return latest

    # Resolve the optimistic-concurrency base before the commit: a None
    # base targets the CURRENT head (the DM wire default), resolved here
    # so ``_commit``'s ``_check_base`` still guards the race — a commit
    # landing between this read and the transaction rejects with
    # StaleRevisionError (edges.py ``_resolved_base`` semantics).
    if base_revision is None:
        base_revision = latest.id if latest is not None else None

    name_value = content.get("name", current_data.get("name"))
    return _commit(
        session,
        campaign_id,
        [
            models.EntityInput(
                kind=entity.kind,
                name=name_value if isinstance(name_value, str) else entity.name,
                text=new_text,
                data=merged,
                id=entity_id,
            )
        ],
        [],
        base_revision,
    )


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
