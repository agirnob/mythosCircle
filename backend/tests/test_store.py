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
    EDGE_TYPES,
    CorruptEventError,
    CrossCampaignConflictError,
    DanglingEdgeError,
    DuplicateEdgeError,
    DuplicateEntityError,
    EdgeRetargetError,
    EmptySubgraphError,
    InvalidEdgeTypeError,
    InvalidUlidError,
    StaleRevisionError,
    UnknownCampaignError,
    app_db_url,
    commit_subgraph,
    create_campaign,
    init_db,
    models,
    session_scope,
    undo,
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
            models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1),
            models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=3),
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
            models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1),
            models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=3),
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
    assert len(edges) == 2
    assert len(events) == 5  # one event per change
    assert all(ev.revision_id == revision.id for ev in events)
    assert sorted(ev.type for ev in events) == [
        "edge_created",
        "edge_created",
        "entity_created",
        "entity_created",
        "entity_created",
    ]


def test_commit_ids_and_timestamps_follow_conventions(world: str) -> None:
    """ULID ids and UTC ISO-8601 ``Z`` timestamps everywhere (AD-13, AR4)."""
    bar_id = ids.new_id()
    revision = commit_subgraph(
        world,
        [models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id)],
        [],
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
        [models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=1)],
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
            [models.EdgeInput(src=MISSING_ID, dst=ghost_id, type="ally_of")],
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
            [models.EdgeInput(src=mira_id, dst=MISSING_ID, type="kin_of")],
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
            [models.EdgeInput(src=bar_id, dst=mira_id, type="sworn_pact")],
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
    _bar_id, _mira_id = _seed_world(world)
    first = _head(world)
    assert first is not None
    # A concurrent commit moves the head.
    stranger_id = ids.new_id()
    commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="A Stranger", id=stranger_id)],
        [],
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


def test_undo_restores_prior_state_with_stable_ulids_and_media(world: str) -> None:
    """Undoing the latest revision restores the previous state exactly:
    entity ULIDs stable, inbound edges and media rows survive, a new undo
    revision with its own events is appended."""
    _bar_id, mira_id = _seed_world(world)
    before = _state(world)

    kellan_id = ids.new_id()
    revision = commit_subgraph(
        world,
        [
            models.EntityInput(kind="character", name="Mira Vane, the Unbroken", id=mira_id),
            models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id),
        ],
        [models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=1)],
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
        # Undo must not touch the media manifest (reclamation is Epic 4, AD-10).
        assert [m.entity_id for m in media_rows] == [kellan_id]

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
        [models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=1)],
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
    _bar_id, _mira_id = _seed_world(world)
    first = _head(world)
    assert first is not None
    stranger_id = ids.new_id()
    commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="A Stranger", id=stranger_id)],
        [],
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
# AD-23 / AD-5: zero dangling edges after every commit
# ---------------------------------------------------------------------------


def test_no_dangling_edges_after_mixed_commits(world: str) -> None:
    _bar_id, mira_id = _seed_world(world)
    kellan_id = ids.new_id()
    r1 = commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan Ash", id=kellan_id)],
        [
            models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=1),
            models.EdgeInput(src=kellan_id, dst=mira_id, type="grudge", counter=5),
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
            [models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=9)],
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
                models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=1),
                models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=2),
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
    rev = commit_subgraph(
        world,
        [models.EntityInput(kind="character", name="Kellan")],
        [],
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
    _seed_world(world)
    head = _head(world)
    assert head is not None
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def _commit(label: str) -> None:
        barrier.wait()  # both threads race for the write lock together
        try:
            rev = commit_subgraph(
                world,
                [models.EntityInput(kind="character", name=label)],
                [],
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
            models.EdgeInput(src=mira_id, dst=kellan_id, type="rival_of", counter=1),
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


@pytest.mark.parametrize("edge_type", sorted(EDGE_TYPES))
def test_closed_edge_vocabulary_members_committable(world: str, edge_type: str) -> None:
    """Every member of the closed Phase-1 vocabulary (AD-5) is accepted by
    a commit — the contract is pinned from the acceptance side, not just
    the rejection side."""
    bar_id, mira_id = _seed_world(world)
    revision = commit_subgraph(
        world,
        [],
        [models.EdgeInput(src=bar_id, dst=mira_id, type=edge_type, counter=1)],
        base_revision=_head(world),
    )
    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
    assert [e.type for e in events] == ["edge_created"]


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
        [models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1)],
        base_revision=None,
    )
    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
        row = session.get(models.Revision, revision.id)
    assert row is not None
    assert len(events) == 4
    assert all(ev.created_at == row.created_at for ev in events)


def test_explicit_id_new_edge_duplicate_relationship_rejected(world: str) -> None:
    """A brand-new edge carrying an explicit id must still respect the
    (src, dst, type) uniqueness invariant (AD-23) — a structured
    DuplicateEdgeError, never a raw unique-constraint IntegrityError at
    flush."""
    bar_id, mira_id = _seed_world(world)
    head = _head(world)
    state_before = _state(world)
    with pytest.raises(DuplicateEdgeError):
        commit_subgraph(
            world,
            [],
            [
                models.EdgeInput(
                    src=mira_id,
                    dst=bar_id,
                    type="debt",  # already seeded (mira -> bar, debt)
                    counter=1,
                    id=ids.new_id(),
                )
            ],
            base_revision=head,
        )
    assert _state(world) == state_before
