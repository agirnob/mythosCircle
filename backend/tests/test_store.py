"""World store: atomic subgraph commits, per-transaction undo, rebase-or-reject.

Covers the I/O matrix from the story spec
(``_bmad-output/implementation-artifacts/spec-1-2-versioned-world-store.md``):
COMMIT_NEW_SUBGRAPH, COMMIT_REGENERATION, COMMIT_ROLLBACK, UNDO_LATEST,
COMMIT_STALE_BASE — plus the acceptance criteria (AR3, AR4, AD-1, AD-2,
AD-5, AD-10, AD-13, AD-23).

Fixtures are deterministic: fixed input graphs, ULIDs pre-minted with the
convention factory, no wall-clock dependence in assertions.
"""

import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.core import ids, time
from app.store import (
    DEFAULT_EDGE_COUNTER_SEMANTIC,
    EDGE_COUNTER_SEMANTICS,
    EDGE_TYPES,
    BlankEdgeReasonError,
    CorruptEventError,
    CrossCampaignConflictError,
    DanglingEdgeError,
    DuplicateEdgeError,
    DuplicateEntityError,
    EdgeKindViolationError,
    EdgeRetargetError,
    EmptySubgraphError,
    InvalidEdgeCounterError,
    InvalidEdgeTypeError,
    InvalidRunStateError,
    InvalidUlidError,
    LiveEdgesError,
    OrphanEntityError,
    SelfLoopEdgeError,
    StaleRevisionError,
    UnknownCampaignError,
    UnknownEdgeError,
    UnknownEntityError,
    add_media,
    app_db_url,
    commit_knowledge_toggle,
    commit_run_state,
    commit_session_verb,
    commit_subgraph,
    create_campaign,
    delete_edge,
    delete_entity,
    edge_counter_semantic,
    entity_live_edges,
    init_db,
    list_media,
    models,
    session_scope,
    undo,
    update_entity,
)
from app.store.read import latest_revision, revision_chain, revision_events, world_state


def _owner_id() -> str:
    """One owner account per scratch DB for campaign creation (spec-1.6)."""
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-store-{new_id()}@example.com", "password123").id


ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

#: A ULID that can never have been minted (all zero).
MISSING_ID = "0" * 26

#: _state row shapes: entities (kind, name, text, data, created_at);
#: edges (src, dst, type, counter, created_at).
EntityRow = tuple[str, str, str | None, dict[str, Any], str]
EdgeRow = tuple[str, str, str, int, str]


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'world.db'}")
    try:
        yield create_campaign(
            _owner_id(), title="Test World", description="", theme="High Fantasy", custom_lore=""
        ).id
    finally:
        init_db(previous)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_world(campaign_id: str) -> tuple[str, str]:
    """Commit the fixture world (The Gilded Bar + Mira Vane).

    Returns (bar_id, mira_id).
    """
    bar_id, mira_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            models.EntityInput(kind="character", name="Mira Vane", id=mira_id),
        ],
        [
            models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1, reason="seeded"),
            models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=3, reason="seeded"),
        ],
        base_revision=None,
    )
    return bar_id, mira_id


def _head(campaign_id: str) -> str | None:
    """The campaign's current head revision id (None on an empty world)."""
    with session_scope() as session:
        head = latest_revision(session, campaign_id)
        return head.id if head is not None else None


def _state(
    campaign_id: str,
) -> tuple[dict[str, EntityRow], dict[str, EdgeRow]]:
    """Materialized latest state: entities and edges by id, full tuples
    including timestamps so undo equivalence is exact."""
    with session_scope() as session:
        entities, edges = world_state(session, campaign_id)
        return (
            {e.id: (e.kind, e.name, e.text, dict(e.data), e.created_at) for e in entities},
            {g.id: (g.src, g.dst, g.type, g.counter, g.created_at) for g in edges},
        )


def _edge_id(campaign_id: str, src: str, dst: str, type_: str) -> str:
    """The ULID of an edge of the given (src, dst, type) in the campaign."""
    with session_scope() as session:
        edge = session.scalars(
            select(models.Edge).where(
                models.Edge.campaign_id == campaign_id,
                models.Edge.src == src,
                models.Edge.dst == dst,
                models.Edge.type == type_,
            )
        ).first()
        assert edge is not None
        return edge.id


def test_sqlite_pragmas_foreign_keys_and_wal(world: str) -> None:
    """m2: the engine's pooled connections enforce foreign keys, WAL mode
    (PRAGMA foreign_keys defaults to OFF), busy_timeout, and BEGIN
    IMMEDIATE on every transaction (AD-2)."""
    from sqlalchemy import text

    from app.store import get_engine

    with get_engine().connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar_one()
        mode = conn.execute(text("PRAGMA journal_mode")).scalar_one()
        busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar_one()
    assert fk == 1
    assert mode == "wal"
    assert busy_timeout == 5000
    # AD-2: every session_scope transaction begins with BEGIN IMMEDIATE —
    # the write lock is taken before the first statement (even a bare
    # SELECT), so check-then-act is atomic with the write.
    with session_scope() as session:
        raw = session.connection().connection.driver_connection
        assert raw is not None
        session.execute(text("SELECT 1"))
        assert raw.in_transaction


# ---------------------------------------------------------------------------
# COMMIT_NEW_SUBGRAPH
# ---------------------------------------------------------------------------


def test_commit_new_subgraph_one_revision(world: str) -> None:
    """Exactly one new revision; state reflects the subgraph; one event per
    change, each tagged with the new revision id."""
    bar_id, mira_id, kellan_id = ids.new_id(), ids.new_id(), ids.new_id()
    revision = commit_subgraph(
        world,
        [
            models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            models.EntityInput(kind="character", name="Mira Vane", id=mira_id),
            models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id),
        ],
        [
            models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1, reason="seeded"),
            models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=3, reason="seeded"),
            models.EdgeInput(src=kellan_id, dst=bar_id, type="ally_of", counter=1, reason="seeded"),
        ],
        base_revision=None,
    )
    with session_scope() as session:
        chain = list(revision_chain(session, world))
        events = list(revision_events(session, world, revision.id))
        entities, edges = world_state(session, world)

    assert len(chain) == 1  # exactly one new revision
    assert chain[0].id == revision.id
    assert chain[0].base_revision is None  # first commit on an empty world
    assert {e.name for e in entities} == {"The Gilded Bar", "Mira Vane", "Kellan Ash"}
    assert len(edges) == 3
    assert len(events) == 6  # one event per change
    assert all(ev.revision_id == revision.id for ev in events)
    assert sorted(ev.type for ev in events) == [
        "edge_created",
        "edge_created",
        "edge_created",
        "entity_created",
        "entity_created",
        "entity_created",
    ]


def test_commit_ids_and_timestamps_follow_conventions(world: str) -> None:
    """ULID ids and UTC ISO-8601 ``Z`` timestamps everywhere (AD-13, AR4)."""
    bar_id, mira_id = ids.new_id(), ids.new_id()
    revision = commit_subgraph(
        world,
        [
            models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            models.EntityInput(kind="character", name="Mira Vane", id=mira_id),
        ],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1, reason="seeded")],
        base_revision=None,
    )
    with session_scope() as session:
        entity = next(session.scalars(select(models.Entity)))
        event = next(session.scalars(select(models.Event)))

    assert ULID_RE.fullmatch(revision.id)
    assert ULID_RE.fullmatch(entity.id)
    assert ULID_RE.fullmatch(event.id)
    assert ISO_Z_RE.fullmatch(revision.created_at)
    assert ISO_Z_RE.fullmatch(entity.created_at)
    assert ISO_Z_RE.fullmatch(event.created_at)


def test_edge_between_staged_and_existing(world: str) -> None:
    """A staged edge may point at an entity staged in the same subgraph
    (zero dangling edges after every commit, AD-23)."""
    _bar_id, mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id)],
        [models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=1, reason="seeded")],
        base_revision=_head(world),
    )
    _entities, edges = _state(world)
    assert (mira_id, kellan_id, "rival_of", 1) in {(g[0], g[1], g[2], g[3]) for g in edges.values()}


# ---------------------------------------------------------------------------
# COMMIT_REGENERATION
# ---------------------------------------------------------------------------


def test_regeneration_replaces_in_place_keeps_ulid_and_inbound_edges(world: str) -> None:
    """Re-using an existing ULID replaces content in place: the entity ULID
    and inbound edges survive; one new revision (AD-2, AR4)."""
    _bar_id, mira_id = _seed_world(world)
    _before, before_edges = _state(world)

    revision = commit_subgraph(
        world,
        [
            models.EntityInput(
                kind="character",
                name="Mira Vane, the Unbroken",
                data={"goals": ["survive the season"]},
                id=mira_id,
            )
        ],
        [],
        base_revision=_head(world),
    )
    entities, edges = _state(world)
    assert mira_id in entities  # ULID stable
    assert entities[mira_id][1] == "Mira Vane, the Unbroken"
    assert entities[mira_id][3] == {"goals": ["survive the season"]}
    # Inbound edges into Mira survive untouched (same row ids and tuples).
    assert set(edges) == set(before_edges)
    for edge_id, before_tuple in before_edges.items():
        assert edges[edge_id] == before_tuple

    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
        chain = list(revision_chain(session, world))
    assert len(chain) == 2
    assert len(events) == 1
    assert events[0].type == "entity_updated"
    assert events[0].payload["before"]["name"] == "Mira Vane"
    assert events[0].payload["after"]["name"] == "Mira Vane, the Unbroken"


# ---------------------------------------------------------------------------
# COMMIT_ROLLBACK (dangling edge, all-or-nothing, AR3)
# ---------------------------------------------------------------------------


def test_dangling_edge_rejects_whole_subgraph(world: str) -> None:
    """A subgraph with a dangling edge is rejected in full: zero new
    revision, state unchanged, structured error naming the endpoint."""
    _bar_id, _mira_id = _seed_world(world)
    before = _state(world)
    ghost_id = ids.new_id()
    with pytest.raises(DanglingEdgeError) as excinfo:
        commit_subgraph(
            world,
            [models.EntityInput(kind="character", name="The Ghost", id=ghost_id)],
            [models.EdgeInput(src=MISSING_ID, dst=ghost_id, type="ally_of", reason="seeded")],
            base_revision=_head(world),
        )
    assert excinfo.value.missing_endpoint == MISSING_ID
    assert _state(world) == before  # all-or-nothing: nothing landed
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1  # seed only


def test_dangling_edge_missing_destination_rejected(world: str) -> None:
    """An edge whose destination exists nowhere is rejected (AR3)."""
    _bar_id, mira_id = _seed_world(world)
    with pytest.raises(DanglingEdgeError) as excinfo:
        commit_subgraph(
            world,
            [],
            [models.EdgeInput(src=mira_id, dst=MISSING_ID, type="kin_of", reason="seeded")],
            base_revision=_head(world),
        )
    assert excinfo.value.missing_endpoint == MISSING_ID


def test_invalid_edge_type_rejected(world: str) -> None:
    """Edge types outside the closed Phase-1 vocabulary are rejected (AD-5)."""
    bar_id, mira_id = _seed_world(world)
    before = _state(world)
    with pytest.raises(InvalidEdgeTypeError) as excinfo:
        commit_subgraph(
            world,
            [],
            [models.EdgeInput(src=bar_id, dst=mira_id, type="sworn_pact", reason="seeded")],
            base_revision=_head(world),
        )
    assert "sworn_pact" in str(excinfo.value)
    assert _state(world) == before


def test_empty_subgraph_rejected(world: str) -> None:
    with pytest.raises(EmptySubgraphError):
        commit_subgraph(world, [], [], base_revision=_head(world))


def test_duplicate_entity_rejected(world: str) -> None:
    bar_id, _mira_id = _seed_world(world)
    with pytest.raises(DuplicateEntityError):
        commit_subgraph(
            world,
            [
                models.EntityInput(kind="faction", name="A", id=bar_id),
                models.EntityInput(kind="faction", name="B", id=bar_id),
            ],
            [],
            base_revision=_head(world),
        )


def test_unknown_campaign(world: str) -> None:
    with pytest.raises(UnknownCampaignError):
        commit_subgraph(
            MISSING_ID,
            [models.EntityInput(kind="faction", name="A")],
            [],
            base_revision=None,
        )


# ---------------------------------------------------------------------------
# COMMIT_STALE_BASE (rebase-or-reject, AD-2)
# ---------------------------------------------------------------------------


def test_stale_base_rejected_naming_latest(world: str) -> None:
    """A commit staged on a stale base is rejected with the latest revision
    id surfaced — never a silent overwrite."""
    bar_id, _mira_id = _seed_world(world)
    first = _head(world)
    assert first is not None
    # A concurrent commit moves the head.
    stranger_id = ids.new_id()
    commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="A Stranger", id=stranger_id)],
        [models.EdgeInput(src=stranger_id, dst=bar_id, type="ally_of", counter=1, reason="seeded")],
        base_revision=first,
    )
    latest = _head(world)
    assert latest != first

    pending_id = ids.new_id()
    with pytest.raises(StaleRevisionError) as excinfo:
        commit_subgraph(
            world,
            [models.EntityInput(kind="character", name="Pending Edit", id=pending_id)],
            [],
            base_revision=first,  # stale: staged before the concurrent commit
        )
    assert excinfo.value.latest_revision_id == latest
    entities, _edges = _state(world)
    assert "Pending Edit" not in {name for _k, name, *_rest in entities.values()}


def test_empty_world_rejects_staged_base(world: str) -> None:
    """A staged base on an empty world is a stale conflict (latest is None)."""
    with pytest.raises(StaleRevisionError) as excinfo:
        commit_subgraph(
            world,
            [models.EntityInput(kind="faction", name="A")],
            [],
            base_revision="0123456789ABCDEFGHJKMNPQRSTV",
        )
    assert excinfo.value.latest_revision_id is None


# ---------------------------------------------------------------------------
# UNDO_LATEST (per-transaction undo, AD-2)
# ---------------------------------------------------------------------------


def test_undo_restores_prior_state_with_stable_ulids_and_reclaims_media(
    world: str,
) -> None:
    """Undoing the latest revision restores the previous state exactly:
    entity ULIDs stable, inbound edges restored, a new undo revision
    with its own events is appended — and the media row of an entity the
    undo DELETES (Kellan, created by the undone revision) is reclaimed
    in the same transaction (spec-4.3 deferral closed, owner ruling
    2026-09-10)."""
    _bar_id, mira_id = _seed_world(world)
    before = _state(world)

    kellan_id = ids.new_id()
    revision = commit_subgraph(
        world,
        [
            models.EntityInput(kind="character", name="Mira Vane, the Unbroken", id=mira_id),
            models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id),
        ],
        [models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=1, reason="seeded")],
        base_revision=_head(world),
    )
    with session_scope() as session:
        session.add(
            models.Media(
                id=ids.new_id(),
                campaign_id=world,
                entity_id=kellan_id,
                filename="kellan-portrait.png",
                kind="image",
                created_at=time.now(),
            )
        )

    undo_revision = undo(world, revision.id)

    assert _state(world) == before  # previous state restored exactly
    with session_scope() as session:
        chain = list(revision_chain(session, world))
        undo_events = list(revision_events(session, world, undo_revision.id))
        media_rows = list(session.scalars(select(models.Media)))
        # The undo DELETED the entity the undone revision created, so its
        # media rows leave with it (spec-4.3); undo never RESTORES media.
        assert media_rows == []

    assert len(chain) == 3
    assert chain[-1].id == undo_revision.id
    assert undo_revision.id != revision.id
    assert undo_revision.base_revision == revision.id
    assert sorted(ev.type for ev in undo_events) == [
        "edge_deleted",
        "entity_deleted",
        "entity_updated",
    ]
    # The undo events invert the original deltas (reversible log).
    for ev in undo_events:
        if ev.type == "entity_updated":
            assert ev.payload["before"]["name"] == "Mira Vane, the Unbroken"
            assert ev.payload["after"]["name"] == "Mira Vane"


def test_undo_is_redoable(world: str) -> None:
    """Undoing the undo revision re-applies the original deltas: the entity
    returns with its original ULID and created_at (AD-1 event sourcing)."""
    _bar_id, mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    r1 = commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id)],
        [models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=1, reason="seeded")],
        base_revision=_head(world),
    )
    with session_scope() as session:
        row = session.get(models.Entity, kellan_id)
        assert row is not None
        created_at = row.created_at

    r2 = undo(world, r1.id)
    with session_scope() as session:
        assert session.get(models.Entity, kellan_id) is None
        assert (
            session.scalars(
                select(models.Edge).where(
                    models.Edge.src == mira_id,
                    models.Edge.dst == kellan_id,
                    models.Edge.type == "rival_of",
                )
            ).first()
            is None
        )

    r3 = undo(world, r2.id)  # redo
    with session_scope() as session:
        restored = session.get(models.Entity, kellan_id)
        assert restored is not None
        assert restored.name == "Kellan Ash"
        assert restored.created_at == created_at  # original timestamp preserved
        edge = session.scalars(select(models.Edge).where(models.Edge.type == "rival_of")).first()
        assert edge is not None
        assert (edge.src, edge.dst, edge.type) == (mira_id, kellan_id, "rival_of")
    _ = r3


def test_undo_non_latest_raises(world: str) -> None:
    """Undo targets the latest revision only; otherwise stale (AD-2)."""
    bar_id, _mira_id = _seed_world(world)
    first = _head(world)
    assert first is not None
    stranger_id = ids.new_id()
    commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="A Stranger", id=stranger_id)],
        [models.EdgeInput(src=stranger_id, dst=bar_id, type="ally_of", counter=1, reason="seeded")],
        base_revision=first,
    )
    latest = _head(world)
    before = _state(world)
    with pytest.raises(StaleRevisionError) as excinfo:
        undo(world, first)
    assert excinfo.value.latest_revision_id == latest
    assert _state(world) == before  # no state changed


def test_undo_unknown_campaign(world: str) -> None:
    with pytest.raises(UnknownCampaignError):
        undo(MISSING_ID, "0123456789ABCDEFGHJKMNPQRSTV")


# ---------------------------------------------------------------------------
# AD-23: edge counters change only via commits
# ---------------------------------------------------------------------------


def test_edge_counter_update_via_commit(world: str) -> None:
    bar_id, mira_id = _seed_world(world)
    with session_scope() as session:
        debt_id = next(g.id for g in session.scalars(select(models.Edge)) if g.type == "debt")
    revision = commit_subgraph(
        world,
        [],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=7, id=debt_id)],
        base_revision=_head(world),
    )
    _entities, edges = _state(world)
    assert edges[debt_id][3] == 7
    with session_scope() as session:
        event = next(iter(revision_events(session, world, revision.id)))
    assert event.type == "edge_updated"
    assert event.payload["before"]["counter"] == 3
    assert event.payload["after"]["counter"] == 7


# ---------------------------------------------------------------------------
# AD-23 / story 2.2: counter semantics map, counter shape, no free text
# ---------------------------------------------------------------------------


def test_edge_counter_semantics_map_contract() -> None:
    """AD-23's per-type counter semantics are a code contract: every
    vocabulary member resolves to exactly one documented semantic, map
    keys are exactly the semantic-bearing types, the rest are neutral."""
    assert EDGE_COUNTER_SEMANTICS == {
        "debt": "amount",
        "grudge": "score",
        "loyalty": "score",
        "ally_of": "intensity",
        "enemy_of": "intensity",
        "controls": "intensity",
        "worships": "score",
        "protects": "intensity",
    }
    assert set(EDGE_COUNTER_SEMANTICS) == {
        "debt",
        "grudge",
        "loyalty",
        "ally_of",
        "enemy_of",
        "controls",
        "worships",
        "protects",
    }
    assert DEFAULT_EDGE_COUNTER_SEMANTIC == "neutral"
    resolved = {edge_type: edge_counter_semantic(edge_type) for edge_type in EDGE_TYPES}
    assert set(resolved.values()) == {"amount", "score", "intensity", "neutral"}
    assert resolved["debt"] == "amount"
    assert resolved["grudge"] == resolved["loyalty"] == "score"
    assert resolved["ally_of"] == resolved["enemy_of"] == "intensity"
    assert resolved["controls"] == "intensity"
    assert resolved["worships"] == "score"
    assert resolved["protects"] == "intensity"
    assert resolved["relationship"] == "neutral"
    assert resolved["bases_at"] == resolved["employs"] == resolved["hails_from"] == "neutral"
    assert resolved["member_of"] == resolved["located_in"] == "neutral"
    assert resolved["kin_of"] == resolved["rival_of"] == "neutral"


@pytest.mark.parametrize("counter", [1.5, True, "3", 2**63, -(2**63) - 1])
def test_edge_counter_invalid_shape_rejects_subgraph(world: str, counter: Any) -> None:
    """A counter that is not an int — or is an int SQLite cannot
    represent in its signed 64-bit INTEGER — is rejected at the store
    boundary with zero new revision and zero rows: SQLite would store
    the Float/Blob silently, and the larger magnitude would raise a raw
    OverflowError at flush instead of a structured StoreError."""
    bar_id, mira_id = _seed_world(world)
    before = _state(world)
    before_head = _head(world)
    with pytest.raises(InvalidEdgeCounterError) as excinfo:
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id, dst=bar_id, type="debt", counter=counter, reason="seeded"
                )
            ],
            base_revision=before_head,
        )
    assert "debt" in str(excinfo.value)
    assert _state(world) == before
    assert _head(world) == before_head


def test_edge_counter_invalid_shape_rejects_update_path(world: str) -> None:
    """The counter-update route (staged existing edge ULID) walks the
    same shape guard — the primary production counter mutation is
    pinned, not just fresh edge creation."""
    bar_id, mira_id = _seed_world(world)
    debt_id = _edge_id(world, mira_id, bar_id, "debt")
    bad_counter: Any = 1.5
    before = _state(world)
    before_head = _head(world)
    with pytest.raises(InvalidEdgeCounterError):
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id, dst=bar_id, type="debt", counter=bad_counter, id=debt_id
                )
            ],
            base_revision=before_head,
        )
    assert _state(world) == before
    assert _head(world) == before_head


def test_edge_counter_valid_rows_stored_verbatim(world: str) -> None:
    """EDGE_COUNTER_VALID's full row: an omitted counter commits as the
    default 1, semantic boundary values commit verbatim (owner ruling
    2026-09-18: amount 0..1_000_000, score/intensity 1..10 inclusive),
    and a NEUTRAL edge type keeps shape-only storage (no semantic
    range)."""
    bar_id, mira_id = _seed_world(world)
    commit_subgraph(
        world,
        [],
        [
            models.EdgeInput(src=mira_id, dst=bar_id, type="ally_of", reason="seeded"),
            models.EdgeInput(src=bar_id, dst=mira_id, type="kin_of", counter=-5, reason="seeded"),
            models.EdgeInput(src=bar_id, dst=mira_id, type="grudge", counter=10, reason="seeded"),
            models.EdgeInput(
                src=bar_id, dst=mira_id, type="debt", counter=1_000_000, reason="seeded"
            ),
        ],
        base_revision=_head(world),
    )
    _entities, edges = _state(world)
    stored = {(row[2], row[0]): row[3] for row in edges.values()}
    assert stored[("ally_of", mira_id)] == 1  # default when omitted
    assert stored[("kin_of", bar_id)] == -5  # neutral: shape only, no range
    assert stored[("grudge", bar_id)] == 10  # score maximum inclusive
    assert stored[("debt", bar_id)] == 1_000_000  # amount maximum inclusive


@pytest.mark.parametrize(
    ("edge_type", "counter"),
    [
        ("debt", -1),  # amount below its minimum
        ("debt", 1_000_001),  # amount above its maximum
        ("grudge", 0),  # score below its minimum
        ("grudge", 11),  # score above its maximum
        ("ally_of", 0),  # intensity below its minimum
        ("enemy_of", 11),  # intensity above its maximum
    ],
)
def test_edge_counter_semantic_range_rejects_subgraph(
    world: str, edge_type: str, counter: int
) -> None:
    """Owner ruling 2026-09-18 (closes the 2.3/3.1 counter-ranges
    deferral): an int OUTSIDE its semantic range is rejected at the
    store boundary with zero new revision and zero rows — an out-of-range
    score would otherwise commit and Phase-3 arithmetic would misread it
    silently."""
    bar_id, mira_id = _seed_world(world)
    before = _state(world)
    before_head = _head(world)
    with pytest.raises(InvalidEdgeCounterError) as excinfo:
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id, dst=bar_id, type=edge_type, counter=counter, reason="seeded"
                )
            ],
            base_revision=before_head,
        )
    assert edge_type in str(excinfo.value)
    assert _state(world) == before
    assert _head(world) == before_head


def test_edge_counter_semantic_range_rejects_update_path(world: str) -> None:
    """The counter-update route (staged existing edge ULID) walks the same
    range guard — the primary production counter mutation is pinned, not
    just fresh edge creation."""
    bar_id, mira_id = _seed_world(world)
    debt_id = _edge_id(world, mira_id, bar_id, "debt")
    before = _state(world)
    before_head = _head(world)
    with pytest.raises(InvalidEdgeCounterError):
        commit_subgraph(
            world,
            [],
            [models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=-1, id=debt_id)],
            base_revision=before_head,
        )
    assert _state(world) == before
    assert _head(world) == before_head


def test_edge_never_carries_free_text_label() -> None:
    """FR3's 'never free text' is structural: the edge table and input
    dataclass carry no label/text field, and EdgeInput rejects one at
    construction (a relation is the typed edge itself, AD-5)."""
    # AD-32 (v3): the edge table gains the ``reason`` column — a SAVED
    # WHY, not free-form labeling: non-blank enforced at the boundary,
    # null-prose denied. The structurally-free-text ban (no label/text)
    # still holds.
    assert list(models.Edge.__table__.columns.keys()) == [
        "id",
        "campaign_id",
        "src",
        "dst",
        "type",
        "counter",
        "reason",
        "created_at",
    ]
    assert set(models.EdgeInput.__dataclass_fields__) == {
        "src",
        "dst",
        "type",
        "counter",
        "reason",
        "id",
    }
    with pytest.raises(TypeError):
        models.EdgeInput(
            src="0" * 26,
            dst="1" * 26,
            type="relationship",
            label="free-form label",
            reason="seeded",
        )  # type: ignore[call-arg]  # no label field exists (FR3)


# ---------------------------------------------------------------------------
# AD-23 / AD-5: zero dangling edges after every commit
# ---------------------------------------------------------------------------


def test_no_dangling_edges_after_mixed_commits(world: str) -> None:
    _bar_id, mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    r1 = commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id)],
        [
            models.EdgeInput(
                src=mira_id, dst=kellan_id, type="rival_of", counter=1, reason="seeded"
            ),
            models.EdgeInput(src=kellan_id, dst=mira_id, type="grudge", counter=5, reason="seeded"),
        ],
        base_revision=_head(world),
    )
    undo(world, r1.id)  # removes Kellan and both edges again
    with session_scope() as session:
        entity_ids = {
            e.id
            for e in session.scalars(
                select(models.Entity).where(models.Entity.campaign_id == world)
            )
        }
        dangling = [
            g.id
            for g in session.scalars(select(models.Edge).where(models.Edge.campaign_id == world))
            if g.src not in entity_ids or g.dst not in entity_ids
        ]
    assert dangling == []


# ---------------------------------------------------------------------------
# Cross-campaign, duplicate, and re-target edge rejections (AD-2, AD-23)
# ---------------------------------------------------------------------------


def test_cross_campaign_edge_id_rejected(world: str) -> None:
    """An explicit edge ULID owned by another campaign is a structured
    rejection (not a raw IntegrityError) and nothing is written."""
    bar_id, mira_id = _seed_world(world)
    other_id = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    _seed_world(other_id)
    other_debt_id = _edge_id(other_id, *_seed_edge_tuple(other_id))

    head = _head(world)
    state_before = _state(world)
    with pytest.raises(CrossCampaignConflictError) as excinfo:
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id,
                    dst=bar_id,
                    type="member_of",
                    counter=1,
                    id=other_debt_id,
                )
            ],
            base_revision=head,
        )
    assert excinfo.value.ulid == other_debt_id
    assert _head(world) == head
    assert _state(world) == state_before


def _seed_edge_tuple(campaign_id: str) -> tuple[str, str, str]:
    with session_scope() as session:
        edge = session.scalars(
            select(models.Edge).where(models.Edge.campaign_id == campaign_id)
        ).first()
        assert edge is not None
        return edge.src, edge.dst, edge.type


def test_duplicate_staged_edge_id_rejected(world: str) -> None:
    """Staging the same explicit edge ULID twice in one subgraph is a
    structured rejection, not a silent double-update."""
    bar_id, mira_id = _seed_world(world)
    debt_id = _edge_id(world, mira_id, bar_id, "debt")
    head = _head(world)
    _, edges = _state(world)

    with pytest.raises(DuplicateEdgeError):
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=5, id=debt_id),
                models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=6, id=debt_id),
            ],
            base_revision=head,
        )

    assert edges[debt_id][3] == 3  # counter untouched
    assert _head(world) == head


def test_new_edge_duplicating_relationship_rejected(world: str) -> None:
    """A new edge reusing an existing (src, dst, type) is rejected (AD-23:
    one row per relationship; stage the existing ULID to change the
    counter)."""
    bar_id, mira_id = _seed_world(world)
    head = _head(world)
    state_before = _state(world)

    with pytest.raises(DuplicateEdgeError):
        commit_subgraph(
            world,
            [],
            [models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=9, reason="seeded")],
            base_revision=head,
        )

    assert _state(world) == state_before


def test_duplicate_staged_relationship_rejected(world: str) -> None:
    """Two NEW edges with the same (src, dst, type) in one subgraph are a
    structured rejection, not a raw unique-constraint IntegrityError."""
    bar_id, mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    head = _head(world)

    with pytest.raises(DuplicateEdgeError):
        commit_subgraph(
            world,
            [models.EntityInput(kind="character", name="Kellan", id=kellan_id)],
            [
                models.EdgeInput(
                    src=mira_id, dst=kellan_id, type="rival_of", counter=1, reason="seeded"
                ),
                models.EdgeInput(
                    src=mira_id, dst=kellan_id, type="rival_of", counter=2, reason="seeded"
                ),
            ],
            base_revision=head,
        )


def test_edge_retarget_rejected(world: str) -> None:
    """An explicit edge id may only change its counter — re-targeting or
    re-typing is forbidden (AD-2)."""
    bar_id, mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    debt_id = _edge_id(world, mira_id, bar_id, "debt")
    head = _head(world)
    state_before = _state(world)

    # Re-target dst.
    with pytest.raises(EdgeRetargetError):
        commit_subgraph(
            world,
            [models.EntityInput(kind="character", name="Kellan", id=kellan_id)],
            [models.EdgeInput(src=mira_id, dst=kellan_id, type="debt", counter=9, id=debt_id)],
            base_revision=head,
        )
    # Re-type.
    with pytest.raises(EdgeRetargetError):
        commit_subgraph(
            world,
            [],
            [models.EdgeInput(src=mira_id, dst=bar_id, type="rival_of", counter=9, id=debt_id)],
            base_revision=head,
        )

    assert _state(world) == state_before


# ---------------------------------------------------------------------------
# Event payload hygiene (m1, m5)
# ---------------------------------------------------------------------------


def test_update_after_snapshot_matches_row(world: str) -> None:
    """Update events carry after-snapshots that match the materialized row
    — created_at is the row's original value, not the commit time."""
    bar_id, mira_id = _seed_world(world)
    debt_id = _edge_id(world, mira_id, bar_id, "debt")
    head = _head(world)
    _, edges = _state(world)
    entities_before = _state(world)[0]

    rev = commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Mira Vane", id=mira_id, text="New line")],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=7, id=debt_id)],
        base_revision=head,
    )

    with session_scope() as session:
        events = revision_events(session, world, rev.id)
        row = session.get(models.Entity, mira_id)
        edge = session.get(models.Edge, debt_id)
    assert row is not None and edge is not None

    entity_event = next(e for e in events if e.type == "entity_updated")
    edge_event = next(e for e in events if e.type == "edge_updated")
    assert entity_event.payload["after"]["created_at"] == row.created_at
    assert entity_event.payload["after"]["name"] == "Mira Vane"
    assert entity_event.payload["after"]["text"] == "New line"
    assert entity_event.payload["before"]["text"] == entities_before[mira_id][2]
    assert edge_event.payload["after"]["created_at"] == edge.created_at
    assert edge_event.payload["after"]["counter"] == 7
    assert edge_event.payload["before"]["counter"] == 3


def test_corrupt_event_payload_rejected(world: str) -> None:
    """A structurally malformed event payload is a structured rejection —
    the undo log is validated before it is re-played."""
    bar_id, mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    rev = commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan", id=kellan_id)],
        [models.EdgeInput(src=kellan_id, dst=bar_id, type="ally_of", counter=1, reason="seeded")],
        base_revision=_head(world),
    )
    with session_scope() as session:
        session.add(
            models.Event(
                id=ids.new_id(),
                campaign_id=world,
                revision_id=rev.id,
                type="entity_updated",
                payload={"id": mira_id, "after": {}},  # before missing
                created_at=time.now(),
            )
        )

    state_before = _state(world)
    with pytest.raises(CorruptEventError):
        undo(world, rev.id)
    assert _state(world) == state_before


# ---------------------------------------------------------------------------
# Undo inverses + redos for updates (m4)
# ---------------------------------------------------------------------------


def test_edge_updated_inverse_and_redo(world: str) -> None:
    """Undoing an edge counter update restores the prior counter; undoing
    the undo (redo) restores the update."""
    bar_id, mira_id = _seed_world(world)
    debt_id = _edge_id(world, mira_id, bar_id, "debt")

    r1 = commit_subgraph(
        world,
        [],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=9, id=debt_id)],
        base_revision=_head(world),
    )
    _, edges = _state(world)
    assert edges[debt_id][3] == 9

    undo(world, r1.id)
    _, edges = _state(world)
    assert edges[debt_id][3] == 3

    # Redo: undo the undo revision.
    with session_scope() as session:
        latest = latest_revision(session, world)
    assert latest is not None
    undo(world, latest.id)
    _, edges = _state(world)
    assert edges[debt_id][3] == 9


def test_entity_updated_redo_restores(world: str) -> None:
    """Undo of an entity update restores kind/name/text/data; undo of the
    undo re-applies the update exactly."""
    bar_id, mira_id = _seed_world(world)

    r1 = commit_subgraph(
        world,
        [
            models.EntityInput(
                kind="character",
                name="Mira Vane",
                id=mira_id,
                text="Renamed text",
                data={"title": "The Vane"},
            )
        ],
        [],
        base_revision=_head(world),
    )
    entities, _ = _state(world)
    assert entities[mira_id] == (
        "character",
        "Mira Vane",
        "Renamed text",
        {"title": "The Vane"},
        entities[mira_id][4],
    )

    undo(world, r1.id)
    entities, _ = _state(world)
    assert entities[mira_id][1] == "Mira Vane"
    assert entities[mira_id][2] is None
    assert entities[mira_id][3] == {}

    with session_scope() as session:
        latest = latest_revision(session, world)
    assert latest is not None
    undo(world, latest.id)
    entities, _ = _state(world)
    assert entities[mira_id][2] == "Renamed text"
    assert entities[mira_id][3] == {"title": "The Vane"}


# ---------------------------------------------------------------------------
# Concurrency: exactly one commit wins (AD-2)
# ---------------------------------------------------------------------------


def test_concurrent_commits_same_base_exactly_one_wins(world: str) -> None:
    """Two threads commit the same base with a barrier (truly concurrent,
    no sleeps): exactly one revision lands; the loser raises
    StaleRevisionError naming the winner. Never a silent overwrite (AD-2).

    BEGIN IMMEDIATE (store.db._begin_immediate) serializes writers at the
    write lock, so the loser's base check deterministically reads the
    winner's head — the old sleep-based version only exercised the
    sequential-stale case.
    """
    bar_id, _mira_id = _seed_world(world)
    head = _head(world)
    assert head is not None
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def _commit(label: str) -> None:
        barrier.wait()  # both threads race for the write lock together
        entity_id = ids.new_id()
        try:
            rev = commit_subgraph(
                world,
                [models.EntityInput(kind="character", name=label, id=entity_id)],
                [
                    models.EdgeInput(
                        src=entity_id, dst=bar_id, type="ally_of", counter=1, reason="seeded"
                    )
                ],
                base_revision=head,
            )
            results[label] = rev.id
        except StaleRevisionError as exc:
            results[f"{label}_conflict"] = exc.latest_revision_id

    threads = [threading.Thread(target=_commit, args=(label,)) for label in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winner = results.get("A") or results.get("B")
    loser = results.get("A_conflict") or results.get("B_conflict")
    assert winner is not None, f"no commit landed: {results}"
    assert loser is not None, f"no conflict raised: {results}"
    # The loser's conflict names the revision that advanced the head.
    assert loser == winner
    assert winner == _head(world)  # winner is the new head
    entities, _ = _state(world)
    names = {e[1] for e in entities.values()}
    assert len(names & {"A", "B"}) == 1


# ---------------------------------------------------------------------------
# Review-regression tests (2026-08-30 code review)
# ---------------------------------------------------------------------------


def test_explicit_non_ulid_entity_id_rejected(world: str) -> None:
    """A caller-supplied entity id that is not a ULID is a structured
    rejection — non-ULID ids would break cursor pagination and
    sortability (conventions.md)."""
    _bar_id, _mira_id = _seed_world(world)
    head = _head(world)
    state_before = _state(world)
    with pytest.raises(InvalidUlidError) as excinfo:
        commit_subgraph(
            world,
            [models.EntityInput(kind="faction", name="Bad Id", id="not-a-ulid")],
            [],
            base_revision=head,
        )
    assert excinfo.value.ulid == "not-a-ulid"
    assert _head(world) == head
    assert _state(world) == state_before


def test_explicit_non_ulid_edge_id_rejected(world: str) -> None:
    """Edge ids are validated at the store boundary too; short ids and
    non-Crockford strings are rejected before any write."""
    bar_id, mira_id = _seed_world(world)
    head = _head(world)
    with pytest.raises(InvalidUlidError):
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id,
                    dst=bar_id,
                    type="ally_of",
                    counter=1,
                    id="0" * 25,  # one character short of a ULID
                )
            ],
            base_revision=head,
        )
    assert _head(world) == head


def test_cross_campaign_entity_id_rejected(world: str) -> None:
    """An explicit entity ULID owned by another campaign is a structured
    rejection (cross-campaign guard), not an accidental uptake or a raw
    IntegrityError — mirrors the edge-side guard."""
    other_id = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    _other_bar_id, other_entity_id = _seed_world(other_id)
    head = _head(world)
    state_before = _state(world)

    with pytest.raises(CrossCampaignConflictError) as excinfo:
        commit_subgraph(
            world,
            [models.EntityInput(kind="character", name="Sneak", id=other_entity_id)],
            [],
            base_revision=head,
        )
    assert excinfo.value.ulid == other_entity_id
    assert _head(world) == head
    assert _state(world) == state_before


def test_missing_base_on_nonempty_world_rejected(world: str) -> None:
    """A commit with no base on a world that already has a head is a stale
    conflict — a base-less commit must never land on evolving state
    (AD-2)."""
    _bar_id, _mira_id = _seed_world(world)
    head = _head(world)
    state_before = _state(world)
    with pytest.raises(StaleRevisionError) as excinfo:
        commit_subgraph(
            world,
            [models.EntityInput(kind="character", name="No Base")],
            [],
            base_revision=None,
        )
    assert excinfo.value.latest_revision_id == head
    assert _state(world) == state_before


def test_undo_empty_world_rejected(world: str) -> None:
    """Undo on a campaign with zero revisions is a stale target (latest
    is None) — no state is touched."""
    with pytest.raises(StaleRevisionError) as excinfo:
        undo(world, "0123456789ABCDEFGHJKMNPQRSTV")
    assert excinfo.value.latest_revision_id is None


def test_undo_mixed_commit_full_inverse_matrix(world: str) -> None:
    """A single commit touching all four inverse branches (entity create,
    entity update, edge create, edge update interleaved) undoes to the
    exact prior state and redoes to the exact committed state."""
    bar_id, mira_id = _seed_world(world)
    before = _state(world)
    kellan_id = ids.new_id()
    debt_id = _edge_id(world, mira_id, bar_id, "debt")

    r1 = commit_subgraph(
        world,
        [
            models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id),
            models.EntityInput(kind="character", name="Mira Vane, the Unbroken", id=mira_id),
        ],
        [
            models.EdgeInput(
                src=mira_id, dst=kellan_id, type="rival_of", counter=1, reason="seeded"
            ),
            models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=7, id=debt_id),
        ],
        base_revision=_head(world),
    )
    committed = _state(world)
    assert committed != before

    undo_rev = undo(world, r1.id)
    assert _state(world) == before

    undo(world, undo_rev.id)  # redo
    assert _state(world) == committed


@pytest.mark.parametrize(
    ("edge_type", "src_kind", "dst_kind"),
    # One LEGAL kind pair per closed-vocabulary member (AD-5, AD-31) —
    # the acceptance-side pin, per matrix cell. The Gilded Bar is a
    # faction, Mira a character, so a place enters as a fresh row where
    # the cell demands one.
    [
        ("relationship", "faction", "character"),
        ("debt", "faction", "character"),
        ("grudge", "faction", "character"),
        ("loyalty", "faction", "character"),
        ("member_of", "faction", "character"),
        ("located_in", "character", "place"),
        ("part_of", "faction", "faction"),
        ("rival_of", "faction", "character"),
        ("kin_of", "faction", "character"),
        ("ally_of", "faction", "character"),
        ("enemy_of", "faction", "character"),
        ("bases_at", "faction", "place"),
        ("controls", "faction", "place"),
        ("employs", "faction", "character"),
        ("worships", "faction", "character"),
        ("hails_from", "faction", "place"),
        ("protects", "faction", "character"),
    ],
)
def test_closed_edge_vocabulary_members_committable(
    world: str, edge_type: str, src_kind: str, dst_kind: str
) -> None:
    """Every member of the closed vocabulary (AD-5, AD-31) is accepted by
    a commit on a legal kind pair — the contract is pinned from the
    acceptance side, per cell, not one pair for every type."""
    src_id, dst_id = ids.new_id(), ids.new_id()
    revision = commit_subgraph(
        world,
        [
            models.EntityInput(kind=src_kind, name="Src Row", id=src_id),
            models.EntityInput(kind=dst_kind, name="Dst Row", id=dst_id),
        ],
        [models.EdgeInput(src=src_id, dst=dst_id, type=edge_type, counter=1, reason="seeded")],
        base_revision=_head(world),
        allow_orphans=True,
    )
    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
    # The two fresh rows carry their own entity_created events; the
    # pinned contract is that the edge itself commits (AD-5 acceptance).
    assert "edge_created" in [e.type for e in events]


def test_events_share_revision_timestamp(world: str) -> None:
    """All events of one commit carry the owning revision's created_at —
    event timestamps never exceed their revision's, so the log's per-commit
    time is consistent (the revision owns its events)."""
    bar_id, mira_id, kellan_id = ids.new_id(), ids.new_id(), ids.new_id()
    revision = commit_subgraph(
        world,
        [
            models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            models.EntityInput(kind="character", name="Mira Vane", id=mira_id),
            models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id),
        ],
        [
            models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1, reason="seeded"),
            models.EdgeInput(src=kellan_id, dst=bar_id, type="ally_of", counter=1, reason="seeded"),
        ],
        base_revision=None,
    )
    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
        row = session.get(models.Revision, revision.id)
    assert row is not None
    assert len(events) == 5
    assert all(ev.created_at == row.created_at for ev in events)


def test_explicit_id_unknown_edge_rejected(world: str) -> None:
    """Spec-3.4 (strict explicit-id contract): an edge staged with an
    explicit id that names no existing edge of the campaign is an
    ``UnknownEdgeError`` — an explicit id NEVER creates. Allowing it
    would let a PATCH staged against a concurrently deleted edge
    resurrect that edge under the same ULID (review round 1)."""
    bar_id, mira_id = _seed_world(world)
    head = _head(world)
    state_before = _state(world)
    fresh_id = ids.new_id()
    with pytest.raises(UnknownEdgeError) as excinfo:
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id,
                    dst=bar_id,
                    type="debt",
                    counter=1,
                    id=fresh_id,
                )
            ],
            base_revision=head,
        )
    assert excinfo.value.edge_id == fresh_id
    assert _state(world) == state_before


# ---------------------------------------------------------------------------
# Story 2.5: FR2 no-orphans at the store + FR4/AD-5 cascade delete
# ---------------------------------------------------------------------------


def _seed_edgeless_neighbor(campaign_id: str) -> str:
    """Commit kellan connected to the bar, then cascade-delete the bar:
    leaves kellan edgeless (the only way an edgeless committed entity
    arises — the orphan rule forbids creating one directly)."""
    bar_id, mira_id = _seed_world(campaign_id)
    kellan_id = ids.new_id()
    commit_subgraph(
        campaign_id,
        [models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id)],
        [models.EdgeInput(src=kellan_id, dst=bar_id, type="ally_of", counter=1, reason="seeded")],
        base_revision=_head(campaign_id),
    )
    delete_entity(campaign_id, bar_id, cascade=True, base_revision=_head(campaign_id))
    return kellan_id


def test_commit_orphan_entity_rejected_with_zero_revisions(world: str) -> None:
    """COMMIT_ORPHAN: a staged create with zero staged/existing edges is
    rejected in full — zero revisions, zero state change (FR2, AD-23)."""
    _bar_id, _mira_id = _seed_world(world)
    before = _state(world)
    orphan_id = ids.new_id()
    with pytest.raises(OrphanEntityError) as excinfo:
        commit_subgraph(
            world,
            [models.EntityInput(kind="character", name="Kellan the Lost", id=orphan_id)],
            [],
            base_revision=_head(world),
        )
    assert excinfo.value.orphans == [(orphan_id, "Kellan the Lost")]
    assert "Kellan the Lost" in str(excinfo.value)
    assert _state(world) == before
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1  # seed only


def test_commit_orphan_on_empty_world_rejected(world: str) -> None:
    """COMMIT_ORPHAN on an empty world: a bare entity with no edges is
    rejected even when it would be the first commit."""
    with pytest.raises(OrphanEntityError):
        commit_subgraph(world, [models.EntityInput(kind="place", name="Lone Hill")])


def test_commit_allow_orphans_commits_edgeless(world: str) -> None:
    """EDGELESS_WAVE1 (store): ``allow_orphans=True`` commits a bare entity
    with zero edges — the build-in wave-1 edgeless commit (owner verdict
    2026-09-11). The default stays rejecting (pinned above); only the
    explicit opt-in passes the FR2 backstop."""
    revision = commit_subgraph(
        world, [models.EntityInput(kind="place", name="Lone Hill")], [], allow_orphans=True
    )
    assert revision.id == _head(world)
    entities, edges = _state(world)
    assert [row[1] for row in entities.values()] == ["Lone Hill"]
    assert edges == {}


def test_commit_self_loop_rejected(world: str) -> None:
    """A self-loop edge is rejected outright (FR2, owner decision
    2026-09-03): the pipeline forbids self-loops (spec-2.3) and the
    store is the backstop for commits that bypass the pipeline — for
    new entities and existing entities alike. A self-loop never
    satisfies the no-orphan rule either."""
    loner_id = ids.new_id()
    with pytest.raises(SelfLoopEdgeError):
        commit_subgraph(
            world,
            [models.EntityInput(kind="place", name="The Lonely Hill", id=loner_id)],
            [models.EdgeInput(src=loner_id, dst=loner_id, type="located_in", reason="seeded")],
            base_revision=None,
        )
    # An existing entity cannot loop onto itself either.
    bar_id, _mira_id = _seed_world(world)
    with pytest.raises(SelfLoopEdgeError):
        commit_subgraph(
            world,
            edges=[models.EdgeInput(src=bar_id, dst=bar_id, type="rival_of", reason="seeded")],
            base_revision=_head(world),
        )
    # Nothing was written beyond the seed wave.
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1  # seed wave only


def test_commit_connected_staged_create_accepted(world: str) -> None:
    """COMMIT_CONNECTED: a staged create wired to an existing entity via
    a staged edge commits as today."""
    bar_id, _mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    revision = commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id)],
        [models.EdgeInput(src=kellan_id, dst=bar_id, type="ally_of", counter=1, reason="seeded")],
        base_revision=_head(world),
    )
    entities, edges = _state(world)
    assert kellan_id in entities
    assert edges[_edge_id(world, kellan_id, bar_id, "ally_of")][3] == 1
    assert revision.id == _head(world)


def test_commit_update_of_edgeless_entity_accepted(world: str) -> None:
    """COMMIT_UPDATE_EDGELESS: the orphan rule is create-only — an
    ``entity_updated`` on an existing edgeless entity is accepted."""
    kellan_id = _seed_edgeless_neighbor(world)
    revision = commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan Ash, Remembered", id=kellan_id)],
        [],
        base_revision=_head(world),
    )
    entities, _edges = _state(world)
    assert entities[kellan_id][1] == "Kellan Ash, Remembered"
    assert revision.id == _head(world)


def test_delete_edgeless_no_confirm_one_revision(world: str) -> None:
    """DELETE_EDGELESS: an entity with zero live edges deletes without
    confirmation — one revision carrying ``entity_deleted`` with the
    ``before`` snapshot (AD-5 requires confirmation only with edges)."""
    kellan_id = _seed_edgeless_neighbor(world)
    head_before = _head(world)
    revision = delete_entity(world, kellan_id, cascade=False, base_revision=head_before)
    assert revision.id != head_before
    entities, _edges = _state(world)
    assert kellan_id not in entities
    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
        assert [ev.type for ev in events] == ["entity_deleted"]
        assert events[0].payload["id"] == kellan_id
        assert events[0].payload["before"]["name"] == "Kellan Ash"
        assert set(events[0].payload["before"]) == {"kind", "name", "text", "data", "created_at"}
        assert len(list(revision_chain(session, world))) == 4


def test_delete_live_edges_no_confirm_lists_neighbors(world: str) -> None:
    """DELETE_LIVE_NO_CONFIRM: deleting an entity with live edges without
    cascade is a no-op failure naming the affected neighbor entities
    (ids + names, deduplicated) (FR4, AD-5)."""
    bar_id, mira_id = _seed_world(world)
    before = _state(world)
    head = _head(world)
    with pytest.raises(LiveEdgesError) as excinfo:
        delete_entity(world, mira_id, cascade=False, base_revision=head)
    assert excinfo.value.entity_id == mira_id
    assert excinfo.value.affected == [{"id": bar_id, "name": "The Gilded Bar"}]
    assert "The Gilded Bar" in str(excinfo.value)
    assert _state(world) == before  # no state change
    assert _head(world) == head


def test_delete_cascade_confirmed_one_revision_zero_dangling(world: str) -> None:
    """DELETE_CASCADE_CONFIRMED: one revision removes the entity plus
    every edge touching it; neighbors survive; zero dangling edges."""
    bar_id, mira_id = _seed_world(world)
    # A second edge from the bar keeps the bar alive after mira leaves.
    kellan_id = ids.new_id()
    commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id)],
        [
            models.EdgeInput(src=kellan_id, dst=bar_id, type="ally_of", counter=1, reason="seeded"),
            models.EdgeInput(
                src=mira_id, dst=kellan_id, type="rival_of", counter=2, reason="seeded"
            ),
        ],
        base_revision=_head(world),
    )
    head = _head(world)
    revision = delete_entity(world, mira_id, cascade=True, base_revision=head)
    entities, edges = _state(world)
    assert mira_id not in entities
    assert bar_id in entities and kellan_id in entities  # neighbors survive
    assert all(mira_id not in (src, dst) for src, dst, *_rest in edges.values())
    dangling = [eid for e in edges.values() for eid in (e[0], e[1]) if eid not in entities]
    assert dangling == []  # AD-23
    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
        assert [ev.type for ev in events] == [
            "edge_deleted",
            "edge_deleted",
            "edge_deleted",
            "entity_deleted",
        ]
        for ev in events:
            assert ev.payload["after"] is None
            assert ev.payload["before"]  # undo-compatible before snapshots
        assert len(list(revision_chain(session, world))) == 3  # seed, kellan, delete


def test_delete_unknown_and_foreign_entity_rejected(world: str) -> None:
    """DELETE_UNKNOWN: an unknown or foreign-campaign entity is a
    structured 404-class rejection with no state change."""
    bar_id, _mira_id = _seed_world(world)
    before = _state(world)
    with pytest.raises(UnknownEntityError) as excinfo:
        delete_entity(world, MISSING_ID, base_revision=_head(world))
    assert excinfo.value.entity_id == MISSING_ID
    # Foreign world: a ULID owned by another campaign is invisible.
    other_campaign = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    foreign_tavern_id, foreign_keep_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        other_campaign,
        [
            models.EntityInput(kind="place", name="Foreign Tavern", id=foreign_tavern_id),
            models.EntityInput(kind="place", name="Foreign Keep", id=foreign_keep_id),
        ],
        [
            models.EdgeInput(
                src=foreign_keep_id, dst=foreign_tavern_id, type="located_in", reason="seeded"
            )
        ],
    )
    with pytest.raises(UnknownEntityError):
        delete_entity(world, foreign_tavern_id, base_revision=_head(world))
    assert _state(world) == before


def test_delete_stale_base_rejected_no_state_change(world: str) -> None:
    """DELETE_STALE: a delete staged on a stale base is rejected with
    ``StaleRevisionError`` — same semantics as ``commit_subgraph``."""
    _bar_id, mira_id = _seed_world(world)
    stale_head = _head(world)
    new_place_id = ids.new_id()
    commit_subgraph(
        world,
        [models.EntityInput(kind="place", name="New Place", id=new_place_id)],
        [models.EdgeInput(src=mira_id, dst=new_place_id, type="located_in", reason="seeded")],
        base_revision=_head(world),
    )
    before = _state(world)
    with pytest.raises(StaleRevisionError):
        delete_entity(world, mira_id, cascade=True, base_revision=stale_head)
    assert _state(world) == before


def test_undo_of_cascade_delete_recreates_with_stable_ulids(world: str) -> None:
    """DELETE_UNDO: undoing a cascade-delete revision recreates the
    entity and its edges with stable ULIDs via the existing undo
    machinery — zero undo.py changes (AD-2)."""
    bar_id, mira_id = _seed_world(world)
    # Capture the edge ids BEFORE the delete so the undo assertion pins
    # the stable-ULID contract itself, not a post-undo lookup (AC3).
    member_id_before = _edge_id(world, mira_id, bar_id, "member_of")
    debt_id_before = _edge_id(world, mira_id, bar_id, "debt")
    delete_entity(world, mira_id, cascade=True, base_revision=_head(world))
    entities_after_delete, edges_after_delete = _state(world)
    assert mira_id not in entities_after_delete
    assert member_id_before not in edges_after_delete
    assert debt_id_before not in edges_after_delete
    head = _head(world)
    assert head is not None
    undo_revision = undo(world, head)
    entities, edges = _state(world)
    assert mira_id in entities  # stable ULID
    assert entities[mira_id][1] == "Mira Vane"
    # The recreated edges carry the deleted edges' ULIDs, not fresh ones.
    assert member_id_before in edges
    assert debt_id_before in edges
    assert edges[member_id_before][3] == 1  # stable ULID + counter
    assert edges[debt_id_before][3] == 3
    with session_scope() as session:
        events = list(revision_events(session, world, undo_revision.id))
        assert [ev.type for ev in events] == [
            "entity_created",
            "edge_created",
            "edge_created",
        ]
        assert all(ev.payload["before"] is None for ev in events)
    # And it redoes: undoing the undo re-deletes (existing machinery).
    redo_revision = undo(world, undo_revision.id)
    entities_redone, _edges_redone = _state(world)
    assert mira_id not in entities_redone
    assert redo_revision.id == _head(world)


def test_delete_edge_one_revision_no_confirm(world: str) -> None:
    """Spec-3.4 EDGE_DELETE (store): a single-edge delete needs NO
    cascade/confirm — it is always dangling-safe. One revision, one
    ``edge_deleted`` event with an undo-compatible ``before`` snapshot;
    both neighbor entities survive."""
    bar_id, mira_id = _seed_world(world)
    edge_id = _edge_id(world, mira_id, bar_id, "member_of")
    before = _state(world)
    head = _head(world)
    assert head is not None
    revision = delete_edge(world, edge_id, base_revision=head)
    entities, edges = _state(world)
    assert edge_id not in edges
    assert bar_id in entities and mira_id in entities  # neighbors survive
    dangling = [eid for e in edges.values() for eid in (e[0], e[1]) if eid not in entities]
    assert dangling == []  # AD-23
    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
        assert [ev.type for ev in events] == ["edge_deleted"]
        assert events[0].payload["after"] is None
        assert set(events[0].payload["before"]) == {
            "src",
            "dst",
            "type",
            "counter",
            "reason",
            "created_at",
        }
        assert len(list(revision_chain(session, world))) == 2  # seed + delete
    assert _state(world) != before


def test_undo_of_delete_edge_recreates_with_stable_ulid(world: str) -> None:
    """Spec-3.4 EDGE_DELETE (undo): undoing a standalone edge-delete
    revision recreates the edge with its stable ULID via the existing
    undo machinery — zero undo.py changes."""
    bar_id, mira_id = _seed_world(world)
    member_id = _edge_id(world, mira_id, bar_id, "member_of")
    debt_id = _edge_id(world, mira_id, bar_id, "debt")
    delete_edge(world, member_id, base_revision=_head(world))
    _entities, edges_after = _state(world)
    assert member_id not in edges_after
    assert debt_id in edges_after  # other edges untouched
    head = _head(world)
    assert head is not None
    undo_revision = undo(world, head)
    _entities, edges = _state(world)
    assert member_id in edges  # stable ULID
    assert edges[member_id][:4] == (mira_id, bar_id, "member_of", 1)
    with session_scope() as session:
        events = list(revision_events(session, world, undo_revision.id))
        assert [ev.type for ev in events] == ["edge_created"]
        assert events[0].payload["before"] is None
    # And it redoes: undoing the undo re-deletes (existing machinery).
    redo_revision = undo(world, undo_revision.id)
    _e2, edges_redone = _state(world)
    assert member_id not in edges_redone
    assert redo_revision.id == _head(world)


def test_delete_edge_unknown_and_foreign_rejected(world: str) -> None:
    """Spec-3.4: an unknown or foreign-campaign edge id is an
    ``UnknownEdgeError`` (404-class) with no state change."""
    bar_id, _mira_id = _seed_world(world)
    before = _state(world)
    with pytest.raises(UnknownEdgeError) as excinfo:
        delete_edge(world, MISSING_ID, base_revision=_head(world))
    assert excinfo.value.edge_id == MISSING_ID
    # Foreign world: another campaign's edge ULID is invisible here.
    other_campaign = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    foreign_tavern_id, foreign_keep_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        other_campaign,
        [
            models.EntityInput(kind="place", name="Foreign Tavern", id=foreign_tavern_id),
            models.EntityInput(kind="place", name="Foreign Keep", id=foreign_keep_id),
        ],
        [
            models.EdgeInput(
                src=foreign_keep_id, dst=foreign_tavern_id, type="located_in", reason="seeded"
            )
        ],
    )
    foreign_edge_id = _edge_id(other_campaign, foreign_keep_id, foreign_tavern_id, "located_in")
    with pytest.raises(UnknownEdgeError):
        delete_edge(world, foreign_edge_id, base_revision=_head(world))
    assert _state(world) == before


def test_delete_edge_stale_base_rejected_no_state_change(world: str) -> None:
    """Spec-3.4: a delete staged on a stale base is rejected with
    ``StaleRevisionError`` — same semantics as ``delete_entity``."""
    bar_id, mira_id = _seed_world(world)
    stale_head = _head(world)
    assert stale_head is not None
    new_place_id = ids.new_id()
    commit_subgraph(
        world,
        [models.EntityInput(kind="place", name="New Place", id=new_place_id)],
        [models.EdgeInput(src=mira_id, dst=new_place_id, type="located_in", reason="seeded")],
        base_revision=_head(world),
    )
    before = _state(world)
    member_id = _edge_id(world, mira_id, bar_id, "member_of")
    with pytest.raises(StaleRevisionError):
        delete_edge(world, member_id, base_revision=stale_head)
    assert _state(world) == before


def test_entity_live_edges_helper_rowid_ordered(world: str) -> None:
    """The read helper returns only the edges touching the entity, in
    rowid order, for the delete preflight and the API listing."""
    bar_id, mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id)],
        [models.EdgeInput(src=kellan_id, dst=mira_id, type="rival_of", counter=1, reason="seeded")],
        base_revision=_head(world),
    )
    with session_scope() as session:
        mira_edges = entity_live_edges(session, world, mira_id)
        assert [(e.src, e.dst, e.type) for e in mira_edges] == [
            (mira_id, bar_id, "member_of"),
            (mira_id, bar_id, "debt"),
            (kellan_id, mira_id, "rival_of"),
        ]
        assert list(entity_live_edges(session, world, ids.new_id())) == []


def test_delete_entity_reclaims_media_rows_in_transaction(world: str) -> None:
    """ENTITY_DELETE_MEDIA (store, spec-4.3): the delete transaction
    removes the entity's manifest rows — image and video alike — while
    neighbors' rows survive; the delete revision carries no media events
    (media rows are an index, not graph state, AD-1)."""
    bar_id, mira_id = _seed_world(world)
    mira_portrait = add_media(world, mira_id, f"{ids.new_id()}.png", "image")
    mira_clip = add_media(world, mira_id, f"{ids.new_id()}.mp4", "video")
    bar_portrait = add_media(world, bar_id, f"{ids.new_id()}.png", "image")
    revision = delete_entity(world, mira_id, cascade=True, base_revision=_head(world))
    remaining = list_media(world)
    assert [r.id for r in remaining] == [bar_portrait.id]
    assert mira_portrait.id not in {r.id for r in remaining}
    assert mira_clip.id not in {r.id for r in remaining}
    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
        assert {ev.type for ev in events} == {"entity_deleted", "edge_deleted"}


def test_delete_entity_without_media_unchanged(world: str) -> None:
    """ENTITY_DELETE_NO_MEDIA (store): deleting an entity with no media
    rows works exactly as before — an empty manifest stays empty."""
    _bar_id, mira_id = _seed_world(world)
    delete_entity(world, mira_id, cascade=True, base_revision=_head(world))
    assert list_media(world) == []


def test_undo_of_delete_restores_entity_but_not_media(world: str) -> None:
    """ENTITY_DELETE_UNDO (spec-4.3): undoing a delete revision restores
    the entity and its edges with stable ULIDs, but the manifest rows
    stay reclaimed — undo does not restore media (``store/undo.py``);
    regeneration is the recovery."""
    bar_id, mira_id = _seed_world(world)
    add_media(world, mira_id, f"{ids.new_id()}.png", "image")
    member_id_before = _edge_id(world, mira_id, bar_id, "member_of")
    debt_id_before = _edge_id(world, mira_id, bar_id, "debt")
    delete_entity(world, mira_id, cascade=True, base_revision=_head(world))
    assert list_media(world) == []
    head = _head(world)
    assert head is not None
    undo(world, head)
    entities, edges = _state(world)
    assert mira_id in entities
    assert member_id_before in edges and debt_id_before in edges
    assert list_media(world) == []  # media are NOT restored by undo


def test_undo_of_entity_creation_reclaims_media_rows_and_files(world: str, tmp_path: Path) -> None:
    """ENTITY_CREATE_UNDO_MEDIA (spec-4.3 deferral closed, owner ruling
    2026-09-10): undoing the revision that CREATED an entity deletes the
    entity — its manifest rows leave in the same store transaction (the
    ``_delete_entity`` seam) and the FILES are reclaimed post-commit
    (AD-10 rows-first; the undo route's reclaim half is called directly
    here, mirroring the API layer)."""
    from app.media.service import reclaim_entity_media

    bar_id, mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    creation = commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id)],
        [models.EdgeInput(src=kellan_id, dst=mira_id, type="rival_of", counter=1, reason="seeded")],
        base_revision=_head(world),
    )
    portrait = add_media(world, kellan_id, f"{ids.new_id()}.png", "image")
    clip = add_media(world, kellan_id, f"{ids.new_id()}.mp4", "video")
    media_dir = tmp_path / "media"
    for row in (portrait, clip):
        path = media_dir / world / kellan_id / row.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"payload")
    assert [r.entity_id for r in list_media(world)] == [kellan_id, kellan_id]

    undo_revision = undo(world, creation.id)

    with session_scope() as session:
        events = [ev.type for ev in revision_events(session, world, undo_revision.id)]
    assert sorted(events) == ["edge_deleted", "entity_deleted"]
    assert list_media(world) == []  # both manifest rows reclaimed in the txn
    assert bar_id in _state(world)[0]  # the seed world is untouched
    # Files post-commit (AD-10 rows-first): the API layer's reclaim half.
    reclaim_entity_media(media_dir, world, kellan_id)
    assert not (media_dir / world / kellan_id).exists()  # files gone


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# AD-32/AD-33: the saved edge reason
# ---------------------------------------------------------------------------


def _mira_edge_id(world: str, src: str, dst: str, edge_type: str) -> str:
    """The live edge's ULID — the store rows carry no id in the tuple
    helpers, so read the materialized row directly."""
    with session_scope() as session:
        entities, edges = world_state(session, world)
    for edge in edges:
        if (edge.src, edge.dst, edge.type) == (src, dst, edge_type):
            return edge.id
    raise AssertionError(f"no live edge {src} --{edge_type}--> {dst}")


@pytest.mark.parametrize(
    "blank", ["", "   ", "None", "none", "N/A", "n/a", "unknown", "...", "TBD", "n.a."]
)
def test_new_edge_blank_reason_rejected(world: str, blank: str) -> None:
    """AD-32 create contract: a new edge's reason must be non-blank and
    never null-prose — empty, whitespace-only, and the model's honest
    no-answer markers (``None``, ``N/A``, ``...``) are the same
    rejection. A pre-v3 row's NULL is the ONLY legal null (tested
    below)."""
    bar_id, mira_id = _seed_world(world)
    with pytest.raises(BlankEdgeReasonError):
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id,
                    dst=bar_id,
                    type="ally_of",
                    counter=1,
                    reason=blank,
                )
            ],
            base_revision=_head(world),
        )


def test_new_edge_non_string_reason_rejected(world: str) -> None:
    """A non-string reason is blank, not data."""
    bar_id, mira_id = _seed_world(world)
    with pytest.raises(BlankEdgeReasonError):
        commit_subgraph(
            world,
            [],
            [models.EdgeInput(src=mira_id, dst=bar_id, type="ally_of", reason=7)],  # type: ignore[arg-type]
            base_revision=_head(world),
        )


def test_counter_bump_preserves_stored_reason(world: str) -> None:
    """AD-32 counter-only bump: an absent reason preserves the stored
    value — a bump never re-decides the saved why."""
    bar_id, mira_id = _seed_world(world)
    edge_id = _mira_edge_id(world, mira_id, bar_id, "member_of")
    commit_subgraph(
        world,
        [],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=5, id=edge_id)],
        base_revision=_head(world),
    )
    with session_scope() as session:
        _entities, edges = world_state(session, world)
    (edge,) = [edge for edge in edges if edge.type == "member_of"]
    assert edge.reason == "seeded"


def test_counter_bump_with_fresh_reason_resaves(world: str) -> None:
    """A bump MAY supply a fresh reason — it re-saves verbatim."""
    bar_id, mira_id = _seed_world(world)
    edge_id = _mira_edge_id(world, mira_id, bar_id, "member_of")
    commit_subgraph(
        world,
        [],
        [
            models.EdgeInput(
                src=mira_id,
                dst=bar_id,
                type="member_of",
                counter=2,
                reason="the watch answers to the bar's ledger",
                id=edge_id,
            )
        ],
        base_revision=_head(world),
    )
    with session_scope() as session:
        _entities, edges = world_state(session, world)
    (edge,) = [edge for edge in edges if edge.type == "member_of"]
    assert edge.counter == 2
    assert edge.reason == "the watch answers to the bar's ledger"


def test_counter_bump_fresh_blank_reason_rejected(world: str) -> None:
    """A bump that SUPPLIES a reason must supply a real one — the fresh
    blank is a rejection, never a silent keep-old."""
    bar_id, mira_id = _seed_world(world)
    edge_id = _mira_edge_id(world, mira_id, bar_id, "member_of")
    with pytest.raises(BlankEdgeReasonError):
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id,
                    dst=bar_id,
                    type="member_of",
                    counter=5,
                    reason="N/A",
                    id=edge_id,
                )
            ],
            base_revision=_head(world),
        )


def test_pre_v3_null_reason_bump_never_blocks(world: str) -> None:
    """AC: pre-reason live rows (NULL) are grandfathered — a counter
    bump without a supplied reason never blocks, and the stored NULL
    stays NULL; reads/undo/export never trip on it."""
    bar_id, mira_id = _seed_world(world)
    edge_id = _mira_edge_id(world, mira_id, bar_id, "member_of")
    with session_scope() as session:
        edge = session.get(models.Edge, edge_id)
        assert edge is not None
        edge.reason = None  # simulate the pre-v3 live row (AD-32)
        session.flush()
    commit_subgraph(
        world,
        [],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=9, id=edge_id)],
        base_revision=_head(world),
    )
    with session_scope() as session:
        _entities, edges = world_state(session, world)
    (edge,) = [edge for edge in edges if edge.type == "member_of"]
    assert edge.counter == 9
    assert edge.reason is None  # grandfathered NULL, never invented


def test_kind_violation_rejected_at_commit_backstop(world: str) -> None:
    """AC: a stale kinds payload commits into the SAME matrix — the
    commit-time live validation is the backstop (AD-34); a person is
    never part_of a place (AD-31)."""
    bar_id, mira_id = _seed_world(world)
    with pytest.raises(EdgeKindViolationError):
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id,
                    dst=bar_id,
                    type="part_of",
                    counter=1,
                    reason="the bar raised her",
                )
            ],
            base_revision=_head(world),
        )


def test_place_employer_and_place_controller_commit(world: str) -> None:
    """The v3 matrix delta (AD-31) is committable: a place src may
    employ (the city hires its watch-captain) and control (the fort
    holds its valley); part_of nests place-in-place."""
    bar_id, mira_id = _seed_world(world)
    town_id, fort_id = ids.new_id(), ids.new_id()
    rev = commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="Greymarch", id=town_id),
            models.EntityInput(kind="place", name="High Pass Fort", id=fort_id),
        ],
        [
            models.EdgeInput(
                src=town_id, dst=mira_id, type="employs", counter=1, reason="city watch contract"
            ),
            models.EdgeInput(
                src=fort_id,
                dst=town_id,
                type="controls",
                counter=1,
                reason="the fort holds the valley",
            ),
            models.EdgeInput(
                src=town_id,
                dst=fort_id,
                type="part_of",
                counter=1,
                reason="the town answers to the fort",
            ),
        ],
        base_revision=_head(world),
    )
    assert rev is not None
    with session_scope() as session:
        _entities, edges = world_state(session, world)
    assert {(e.type, e.src, e.dst) for e in edges} >= {
        ("employs", town_id, mira_id),
        ("controls", fort_id, town_id),
        ("part_of", town_id, fort_id),
    }


# ---------------------------------------------------------------------------
# AD-26..AD-29: Tonight Tier-2 run-state (verbs, knowledge toggles, take-back)
# ---------------------------------------------------------------------------


def _session_row(world: str, entity_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.scalars(
            select(models.EntitySessionState).where(
                models.EntitySessionState.campaign_id == world,
                models.EntitySessionState.entity_id == entity_id,
            )
        ).first()
        return dict(row.data) if row is not None else None


def _knowledge_rows(world: str, entity_id: str) -> dict[str, bool]:
    with session_scope() as session:
        rows = session.scalars(
            select(models.EntityKnowledgeState).where(
                models.EntityKnowledgeState.campaign_id == world,
                models.EntityKnowledgeState.entity_id == entity_id,
            )
        ).all()
    return {row.field: row.known for row in rows}


def _event_types(world: str, revision_id: str) -> list[str]:
    with session_scope() as session:
        return [event.type for event in revision_events(session, world, revision_id)]


def test_verb_commit_materializes_row_and_event(world: str) -> None:
    """AD-26/AD-28: one consequence verb is ONE committed revision whose
    event is rebuild-faithful (the FULL resulting image) and whose state
    row materializes in the same transaction."""
    _bar_id, mira_id = _seed_world(world)
    revision = commit_session_verb(world, mira_id, update={"defeated": True})
    assert _session_row(world, mira_id) == {"defeated": True}
    assert _event_types(world, revision.id) == ["session_state_created"]
    with session_scope() as session:
        event = revision_events(session, world, revision.id)[0]
    assert event.payload["after"]["data"] == {"defeated": True}


def test_verb_merges_onto_existing_image(world: str) -> None:
    """A second verb merges onto the CURRENT image — hp never erases
    defeated."""
    _bar_id, mira_id = _seed_world(world)
    commit_session_verb(world, mira_id, update={"defeated": True})
    commit_session_verb(world, mira_id, update={"hp": -12})
    assert _session_row(world, mira_id) == {"defeated": True, "hp": -12}


def test_verb_double_fire_commits_nothing(world: str) -> None:
    """The double-fire gate (EXPERIENCE Flow 6): a value-identical second
    fire returns the head unchanged — one revision, one event."""
    _bar_id, mira_id = _seed_world(world)
    first = commit_session_verb(world, mira_id, update={"defeated": True})
    second = commit_session_verb(world, mira_id, update={"defeated": True})
    assert second.id == first.id
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 2  # seed + the one verb
    assert _session_row(world, mira_id) == {"defeated": True}


def test_verb_unknown_entity_rejected(world: str) -> None:
    _bar_id, _mira_id = _seed_world(world)
    with pytest.raises(UnknownEntityError):
        commit_session_verb(world, "0" * 26, update={"defeated": True})


def test_verb_invalid_state_image_rejected(world: str) -> None:
    """AD-26: the image is rebuild-faithful — a non-object or
    non-strict-JSON image is a structured rejection, never a stored
    blob."""
    _bar_id, mira_id = _seed_world(world)
    with pytest.raises(InvalidRunStateError):
        commit_session_verb(world, mira_id, update="defeated")  # type: ignore[arg-type]
    with pytest.raises(InvalidRunStateError):
        commit_session_verb(world, mira_id, update={"flag": object()})


def test_toggle_flip_materializes_row_and_event(world: str) -> None:
    """AD-29: a standalone flip is its own undoable step; the record
    never changes — only the marker moves."""
    _bar_id, mira_id = _seed_world(world)
    revision = commit_knowledge_toggle(world, mira_id, "secret", known=True)
    assert _knowledge_rows(world, mira_id) == {"secret": True}
    assert _event_types(world, revision.id) == ["knowledge_state_created"]
    with session_scope() as session:
        entity = session.get(models.Entity, mira_id)
        assert entity is not None
    assert "known" not in entity.data  # the record is untouched


def test_toggle_unknown_field_rejected(world: str) -> None:
    """The closed toggle set (secret/rumor/party_hook) is a DB CHECK plus
    a code check — anything else is a structured rejection."""
    _bar_id, mira_id = _seed_world(world)
    with pytest.raises(InvalidRunStateError):
        commit_knowledge_toggle(world, mira_id, "favorite_color", known=True)


def test_toggle_unknown_entity_rejected(world: str) -> None:
    _bar_id, _mira_id = _seed_world(world)
    with pytest.raises(UnknownEntityError):
        commit_knowledge_toggle(world, "0" * 26, "secret", known=True)


def test_toggle_same_value_is_a_noop_reverse_is_new_step(world: str) -> None:
    """AD-29 one undoable step each way: a repeated same-value flip is
    NOT a step — the head returns, zero revisions; flipping the OTHER
    way is a fresh step (one new revision)."""
    _bar_id, mira_id = _seed_world(world)
    first = commit_knowledge_toggle(world, mira_id, "secret", known=True)
    assert _event_types(world, first.id) == ["knowledge_state_created"]
    again = commit_knowledge_toggle(world, mira_id, "secret", known=True)
    assert again.id == first.id  # the double-fire commits nothing
    flipped = commit_knowledge_toggle(world, mira_id, "secret", known=False)
    assert flipped.id != first.id
    assert _knowledge_rows(world, mira_id) == {"secret": False}
    with session_scope() as session:
        chain = list(revision_chain(session, world))
    assert [r.id for r in chain[-2:]] == [first.id, flipped.id]


def test_toggle_non_bool_known_rejected_even_on_existing_row(world: str) -> None:
    """The no-op path must never swallow a type violation: ``1`` for
    ``true`` reads as CHANGED (identity-compare) and reaches the store's
    strict bool contract."""
    _bar_id, mira_id = _seed_world(world)
    commit_knowledge_toggle(world, mira_id, "secret", known=True)
    with pytest.raises(InvalidRunStateError):
        commit_knowledge_toggle(world, mira_id, "secret", known=1)  # type: ignore[arg-type]


def test_update_entity_flip_only_bundle_commits_one_revision(world: str) -> None:
    """AD-29: a bundled save whose edit is empty is a legal knowledge-
    only revision — the value-identical guard must NOT swallow the flip
    (a flip-only PATCH is the dedicated toggle route's sibling)."""
    _bar_id, mira_id = _seed_world(world)
    revision = update_entity(
        world,
        mira_id,
        patch={},
        knowledge_flips=[models.KnowledgeFlipInput(entity_id=mira_id, field="secret", known=True)],
    )
    assert _event_types(world, revision.id) == ["knowledge_state_created"]
    assert _knowledge_rows(world, mira_id) == {"secret": True}
    with session_scope() as session:
        chain = list(revision_chain(session, world))
    assert chain[-1].id == revision.id


def test_update_entity_resave_repeats_unchanged_flip_noop(world: str) -> None:
    """PATCH idempotence extends to the bundle: a re-save of the same
    form (identical content + unchanged flips) commits NOTHING — the
    head returns, the flip emits no event."""
    _bar_id, mira_id = _seed_world(world)
    bundle = update_entity(
        world,
        mira_id,
        patch={"appearance": "scarred twice across the cheek"},
        knowledge_flips=[models.KnowledgeFlipInput(entity_id=mira_id, field="secret", known=True)],
    )
    assert _event_types(world, bundle.id) == ["entity_updated", "knowledge_state_created"]
    resave = update_entity(
        world,
        mira_id,
        patch={"appearance": "scarred twice across the cheek"},
        knowledge_flips=[models.KnowledgeFlipInput(entity_id=mira_id, field="secret", known=True)],
    )
    assert resave.id == bundle.id
    with session_scope() as session:
        chain = list(revision_chain(session, world))
    assert chain[-1].id == bundle.id
    assert _knowledge_rows(world, mira_id) == {"secret": True}


def test_update_entity_bundle_only_changed_flips_commit(world: str) -> None:
    """A re-save changing ONE flip of a repeated pair commits only the
    changed one — the unchanged flip emits nothing (filtered, not
    repeated)."""
    _bar_id, mira_id = _seed_world(world)
    bundle = update_entity(
        world,
        mira_id,
        patch={"appearance": "scarred twice across the cheek"},
        knowledge_flips=[
            models.KnowledgeFlipInput(entity_id=mira_id, field="secret", known=True),
            models.KnowledgeFlipInput(entity_id=mira_id, field="rumor", known=True),
        ],
    )
    assert _event_types(world, bundle.id) == [
        "entity_updated",
        "knowledge_state_created",
        "knowledge_state_created",
    ]
    resave = update_entity(
        world,
        mira_id,
        patch={"appearance": "scarred twice across the cheek"},
        knowledge_flips=[
            models.KnowledgeFlipInput(entity_id=mira_id, field="secret", known=True),
            models.KnowledgeFlipInput(entity_id=mira_id, field="rumor", known=False),
        ],
    )
    assert resave.id != bundle.id
    with session_scope() as session:
        events = [e.type for e in revision_events(session, world, resave.id)]
    assert events == ["knowledge_state_updated"]
    assert _knowledge_rows(world, mira_id) == {"secret": True, "rumor": False}


def test_defeat_scar_take_back_surgical(world: str) -> None:
    """AC-3, the AD-27 arithmetic: defeat, then a scar edit on top, then
    take the defeat back — history reads three ``edited`` revisions, the
    scar STANDS, and the defeat rewinds arithmetically (the row is
    deleted by the created-inverse), never overwriting the later edit."""
    _bar_id, mira_id = _seed_world(world)
    defeat = commit_session_verb(world, mira_id, update={"defeated": True})
    scar = update_entity(world, mira_id, patch={"appearance": "scarred twice across the cheek"})
    take_back = undo(world, defeat.id)  # non-head → surgical inverse path

    with session_scope() as session:
        chain = list(revision_chain(session, world))
    assert [r.id for r in chain[-3:]] == [defeat.id, scar.id, take_back.id]
    assert _event_types(world, defeat.id) == ["session_state_created"]
    assert _event_types(world, scar.id) == ["entity_updated"]
    assert _event_types(world, take_back.id) == ["session_state_deleted"]
    # The scar stands; the defeat is rewound (its created row is gone).
    with session_scope() as session:
        entity = session.get(models.Entity, mira_id)
    assert entity is not None
    assert entity.data["appearance"] == "scarred twice across the cheek"
    assert _session_row(world, mira_id) is None


def test_edit_plus_toggle_bundles_one_revision(world: str) -> None:
    """AD-29 bundle: an edit+flip saved together is ONE revision; the
    take-back inverts BOTH halves in the same undo step."""
    _bar_id, mira_id = _seed_world(world)
    bundle = update_entity(
        world,
        mira_id,
        patch={"appearance": "hooded"},
        knowledge_flips=[models.KnowledgeFlipInput(entity_id=mira_id, field="secret", known=True)],
    )
    assert _event_types(world, bundle.id) == ["entity_updated", "knowledge_state_created"]
    assert _knowledge_rows(world, mira_id) == {"secret": True}
    undo_revision = undo(world, bundle.id)
    with session_scope() as session:
        entity = session.get(models.Entity, mira_id)
    assert entity is not None
    assert "appearance" not in entity.data  # the edit half rewound
    assert _knowledge_rows(world, mira_id) == {}  # the flip half rewound
    # AD-27: an undo renders as an edit — the entity half carries
    # ``entity_updated`` (never a distinct undo event); only the toggle
    # half shows its flip-back stream event.
    assert sorted(_event_types(world, undo_revision.id)) == [
        "entity_updated",
        "knowledge_state_deleted",
    ]


def test_run_state_commit_empty_is_rejected(world: str) -> None:
    """commit_run_state with no ops is an empty subgraph — the store's
    EmptySubgraphError, same as any empty commit."""
    _bar_id, _mira_id = _seed_world(world)
    with pytest.raises(EmptySubgraphError):
        commit_run_state(world)


# ---------------------------------------------------------------------------
# AD-28/AD-32 migrations (the pre-v3 live shape)
# ---------------------------------------------------------------------------


def test_migrate_login_session_renames_pre_v3_table(tmp_path: Path) -> None:
    """A pre-v3 database has the login table under ``session`` (AD-28):
    init_db renames it in place, rows and all — the model's
    ``login_session`` SELECTs work and no login is lost."""
    import sqlite3

    db_path = tmp_path / "pre-v3-session.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE account (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            email VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            created_at VARCHAR(40) NOT NULL
        );
        CREATE TABLE session (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            account_id VARCHAR(26) NOT NULL REFERENCES account(id),
            token_hash VARCHAR(64) NOT NULL,
            expires_at VARCHAR(40) NOT NULL,
            revoked_at VARCHAR(40),
            created_at VARCHAR(40) NOT NULL
        );
        """
    )
    raw.execute(
        "INSERT INTO account VALUES "
        "('22222222222222222222222222', 'pre-v3@example.com', 'hash', '2026-01-01')"
    )
    raw.execute(
        "INSERT INTO session VALUES ('11111111111111111111111111', "
        "'22222222222222222222222222', 'deadbeef', "
        "'2099-01-01', NULL, '2026-01-01')"
    )
    raw.commit()
    raw.close()

    previous = app_db_url()
    try:
        init_db(f"sqlite:///{db_path}")
        with session_scope() as session:
            rows = session.execute(select(models.LoginSession)).scalars().all()
        assert len(rows) == 1
        assert rows[0].token_hash == "deadbeef"
        tables = (
            sqlite3.connect(db_path)
            .execute("SELECT name FROM sqlite_master WHERE type='table'")
            .fetchall()
        )
        assert ("session",) not in tables
    finally:
        init_db(previous)


def test_migrate_login_session_merges_when_both_tables_exist(tmp_path: Path) -> None:
    """The damaged live shape (v3 ``create_all`` ran before the rename):
    both tables exist and ``session`` still holds the rows — init_db
    COPIES them into the empty ``login_session`` and drops the old table
    (a pure rename would log everyone out)."""
    import sqlite3

    db_path = tmp_path / "both-session.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE account (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            email VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            created_at VARCHAR(40) NOT NULL
        );
        CREATE TABLE session (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            account_id VARCHAR(26) NOT NULL REFERENCES account(id),
            token_hash VARCHAR(64) NOT NULL,
            expires_at VARCHAR(40) NOT NULL,
            revoked_at VARCHAR(40),
            created_at VARCHAR(40) NOT NULL
        );
        """
    )
    raw.execute(
        "INSERT INTO account VALUES "
        "('22222222222222222222222222', 'pre-v3@example.com', 'hash', '2026-01-01')"
    )
    raw.execute(
        "INSERT INTO session VALUES ('11111111111111111111111111', "
        "'22222222222222222222222222', 'feedface', "
        "'2099-01-01', NULL, '2026-01-01')"
    )
    raw.commit()
    raw.close()

    previous = app_db_url()
    try:
        init_db(f"sqlite:///{db_path}")  # create_all makes empty login_session
        init_db(f"sqlite:///{db_path}")  # second startup runs the merge branch
        with session_scope() as session:
            rows = session.execute(select(models.LoginSession)).scalars().all()
        assert len(rows) == 1
        assert rows[0].token_hash == "feedface"
        tables = (
            sqlite3.connect(db_path)
            .execute("SELECT name FROM sqlite_master WHERE type='table'")
            .fetchall()
        )
        assert ("session",) not in tables
    finally:
        init_db(previous)


def test_migrate_edge_reason_adds_null_column(tmp_path: Path) -> None:
    """A pre-reason database gains the nullable column; existing rows
    stay NULL (grandfathered, AD-32)."""
    import sqlite3

    db_path = tmp_path / "pre-reason.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE account (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            email VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            created_at VARCHAR(40) NOT NULL
        );
        CREATE TABLE campaign (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            owner_id VARCHAR(26) NOT NULL,
            title VARCHAR(300) NOT NULL,
            description TEXT NOT NULL,
            theme VARCHAR(100) NOT NULL,
            custom_lore TEXT NOT NULL,
            created_at VARCHAR(40) NOT NULL
        );
        CREATE TABLE edge (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            campaign_id VARCHAR(26) NOT NULL REFERENCES campaign(id),
            src VARCHAR(26) NOT NULL,
            dst VARCHAR(26) NOT NULL,
            type VARCHAR(64) NOT NULL,
            counter INTEGER NOT NULL,
            created_at VARCHAR(40) NOT NULL
        );
        """
    )
    raw.execute(
        "INSERT INTO campaign VALUES ('00000000000000000000000000', "
        "'22222222222222222222222222', 'T', '', "
        "'High Fantasy', '', '2026-01-01')"
    )
    raw.execute(
        "INSERT INTO edge VALUES ('11111111111111111111111111', "
        "'00000000000000000000000000', '22222222222222222222222222', "
        "'33333333333333333333333333', 'ally_of', 1, '2026-01-01')"
    )
    raw.commit()
    raw.close()

    previous = app_db_url()
    try:
        init_db(f"sqlite:///{db_path}")
        columns = {
            row[1] for row in sqlite3.connect(db_path).execute("PRAGMA table_info(edge)").fetchall()
        }
        assert "reason" in columns
        with session_scope() as session:
            edge = session.get(models.Edge, "1" * 26)
        assert edge is not None and edge.reason is None
    finally:
        init_db(previous)


@pytest.mark.parametrize("later", [{"hp": 12}, {"defeated": False}])
def test_creation_take_back_preserves_later_session_state(
    world: str, later: dict[str, Any]
) -> None:
    _, entity = _seed_world(world)
    first = commit_session_verb(world, entity, update={"defeated": True})
    commit_session_verb(world, entity, update=later)
    original = _session_row(world, entity)
    assert original is not None
    take_back = undo(world, first.id)
    assert _session_row(world, entity) == later
    undo(world, take_back.id)
    assert _session_row(world, entity) == original


def test_surgical_numeric_take_back_logs_current_image_and_redoes(world: str) -> None:
    _, entity = _seed_world(world)
    commit_session_verb(world, entity, update={"hp": 10})
    target = commit_session_verb(world, entity, update={"hp": 20})
    commit_session_verb(world, entity, update={"hp": 35})
    take_back = undo(world, target.id)
    assert _session_row(world, entity) == {"hp": 25}
    with session_scope() as session:
        event = revision_events(session, world, take_back.id)[0]
        assert event.payload["before"]["data"] == {"hp": 35}
    undo(world, take_back.id)
    assert _session_row(world, entity) == {"hp": 35}


@pytest.mark.parametrize("creation", [False, True])
def test_knowledge_take_back_preserves_later_toggle_and_redo(world: str, creation: bool) -> None:
    _, entity = _seed_world(world)
    if not creation:
        commit_knowledge_toggle(world, entity, "secret", known=False)
    target = commit_knowledge_toggle(world, entity, "secret", known=True)
    later = commit_knowledge_toggle(world, entity, "secret", known=False)
    take_back = undo(world, target.id)
    assert _knowledge_rows(world, entity) == {"secret": False}
    with session_scope() as session:
        previous = revision_events(session, world, later.id)[0].payload["after"]
        inverse = revision_events(session, world, take_back.id)[0]
        assert inverse.payload["before"] == previous
    undo(world, take_back.id)
    assert _knowledge_rows(world, entity) == {"secret": False}


@pytest.mark.parametrize("value", [True, 1.0])
def test_undo_persists_json_type_changes(world: str, value: bool | float) -> None:
    _, entity = _seed_world(world)
    update_entity(world, entity, patch={"custom_flag": 1})
    changed = update_entity(world, entity, patch={"custom_flag": value})
    inverse = undo(world, changed.id)
    with session_scope() as session:
        row = session.get(models.Entity, entity)
        assert row is not None
        assert type(row.data["custom_flag"]) is int
    undo(world, inverse.id)
    with session_scope() as session:
        row = session.get(models.Entity, entity)
        assert row is not None
        assert type(row.data["custom_flag"]) is type(value)


@pytest.mark.parametrize("kind", ["edge_created", "edge_updated", "edge_deleted"])
def test_undo_legacy_edge_snapshots_without_reason(world: str, kind: str) -> None:
    _seed_world(world)
    with session_scope() as session:
        edge = session.scalars(select(models.Edge)).first()
        assert edge is not None
        head = latest_revision(session, world)
        assert head is not None
        snapshot = {
            key: getattr(edge, key) for key in ("src", "dst", "type", "counter", "created_at")
        }
        revision = models.Revision(
            id=ids.new_id(),
            campaign_id=world,
            base_revision=head.id,
            created_at=time.now(),
        )
        session.add(revision)
        session.flush()
        session.add(
            models.Event(
                id=ids.new_id(),
                campaign_id=world,
                revision_id=revision.id,
                type=kind,
                payload={
                    "id": edge.id,
                    "before": snapshot if kind != "edge_created" else None,
                    "after": snapshot if kind != "edge_deleted" else None,
                },
                created_at=time.now(),
            )
        )
        if kind == "edge_deleted":
            session.delete(edge)
        target = revision.id
    inverse = undo(world, target)
    undo(world, inverse.id)


@pytest.mark.parametrize("family", ["session", "knowledge"])
def test_unmodified_state_creation_take_back_deletes_and_redoes(world: str, family: str) -> None:
    _, entity = _seed_world(world)
    if family == "session":
        target = commit_session_verb(world, entity, update={"defeated": True})
    else:
        target = commit_knowledge_toggle(world, entity, "secret", known=True)
    inverse = undo(world, target.id)
    assert _session_row(world, entity) is None
    assert _knowledge_rows(world, entity) == {}
    undo(world, inverse.id)
    if family == "session":
        assert _session_row(world, entity) == {"defeated": True}
    else:
        assert _knowledge_rows(world, entity) == {"secret": True}


def test_notes_compare_is_atomic_under_concurrent_writes(world: str) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, entity = _seed_world(world)
    gate = Barrier(2)

    def save(text: str) -> str:
        gate.wait()
        try:
            commit_session_verb(world, entity, update={"notes": text}, expected_notes="")
            return "saved"
        except StaleRevisionError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["first", "second"]))
    assert sorted(results) == ["conflict", "saved"]
    assert _session_row(world, entity)["notes"] in {"first", "second"}
