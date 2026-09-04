"""Proposed-candidate lifecycle (spec-3.2): accept commits the staged
subgraph as exactly ONE atomic transaction; reject leaves the world
untouched; a candidate never mutates accepted state (FR11, AR7, AD-15).

Covers the spec's I/O matrix — ACCEPT_HAPPY, ACCEPT_STALE_ENDPOINT,
ACCEPT_BAD_COUNTER, ACCEPT_ALREADY_SETTLED, ACCEPT_UNKNOWN,
REJECT_STANDALONE, REJECT_ALREADY_SETTLED, UNDO_AFTER_DONE — plus the
acceptance criteria: partial failure rolls back status AND subgraph
(zero revisions), and accepting for an existing entity creates a NEW
entity (fresh ULID) without ever updating the accepted one.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core import ids
from app.store import (
    BOSS_FIELDS,
    CandidateNotFoundError,
    CandidateSettledError,
    DanglingEdgeError,
    InvalidCandidateError,
    OrphanEntityError,
    accept_candidate,
    app_db_url,
    commit_subgraph,
    create_campaign,
    delete_entity,
    discard_candidates,
    enqueue_job,
    get_engine,
    init_db,
    models,
    reject_candidate,
    session_scope,
    stage_candidates,
    undo,
)
from app.store.read import latest_revision, revision_chain, revision_events, world_state


def _owner_id() -> str:
    """One owner account per scratch DB for campaign creation (spec-1.6)."""
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-cand-{new_id()}@example.com", "password123").id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'candidates.db'}")
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
    """Commit the fixture world (The Gilded Bar + Mira Vane); returns ids."""
    bar_id, mira_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            models.EntityInput(kind="character", name="Mira Vane", id=mira_id),
        ],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1)],
        base_revision=None,
    )
    return bar_id, mira_id


def _payload(mira_id: str, bar_id: str, **overrides: Any) -> dict[str, Any]:
    """A staged AR24-complete payload wired to the seeded world."""
    payload: dict[str, Any] = {
        "name": "Sable Rook",
        "role": "NPC",
        "personality": "dry",
        "secret": "s",
        "rumor": "r",
        "party_hook": "p",
        "level_cr": "level 5",
        "race_type": "Human",
        "class_profession": "Fence",
        "alignment": "NE",
        "appearance": "gaunt, ink-stained fingers",
        "background": "ex-Guild scribe",
        "goals": "buy back her name",
        "relationships": "owes Mira a debt",
        "voice_style": "clipped, low",
        "catchphrases": '"Everything has a price."',
        "stat_block": {"identity": {"role": "NPC"}},
        "world_integration": {
            "reputation": "the fixer of the docks",
            "factions": "The Guild",
            "current_location": "the Gilded Bar",
            "reaction_matrix": "buys drinks, sells favors",
            "on_defeat": "flees, leaving the ledger behind",
        },
        "edges": [
            {"endpoint": mira_id, "direction": "outbound", "type": "rival_of", "counter": 2},
            {"endpoint": bar_id, "direction": "inbound", "type": "member_of", "counter": 1},
        ],
    }
    payload.update(overrides)
    return payload


def _stage(campaign_id: str, payload: dict[str, Any]) -> models.ProposedCandidate:
    """Stage one candidate through the store's staging path."""
    job = enqueue_job(campaign_id, "generate", {"ask": "a rival"})
    (row,) = stage_candidates(campaign_id, job.id, [payload])
    return row


def _head(campaign_id: str) -> str | None:
    with session_scope() as session:
        latest = latest_revision(session, campaign_id)
        return latest.id if latest is not None else None


def _state(campaign_id: str) -> tuple[set[tuple[Any, ...]], set[tuple[Any, ...]]]:
    """(entities, edges) snapshots of the materialized world."""
    with session_scope() as session:
        entities, edges = world_state(session, campaign_id)
        return (
            {
                (e.id, e.kind, e.name, e.text, json.dumps(e.data, sort_keys=True), e.created_at)
                for e in entities
            },
            {(x.id, x.src, x.dst, x.type, x.counter, x.created_at) for x in edges},
        )


def _row(campaign_id: str, candidate_id: str) -> models.ProposedCandidate:
    with session_scope() as session:
        row = session.get(models.ProposedCandidate, candidate_id)
        assert row is not None and row.campaign_id == campaign_id
        return row


# ---------------------------------------------------------------------------
# ACCEPT_HAPPY + acceptance criteria (fresh ULID, non-mutation, one revision)
# ---------------------------------------------------------------------------


def test_accept_commits_subgraph_and_settles_row(world: str) -> None:
    """ACCEPT_HAPPY: one new revision; entity + edges committed exactly as
    staged; the row flips to ``accepted``."""
    bar_id, mira_id = _seed_world(world)
    head_before = _head(world)
    assert head_before is not None
    candidate = _stage(world, _payload(mira_id, bar_id))

    accepted, revision = accept_candidate(world, candidate.id)

    assert accepted.status == "accepted"
    assert revision.id == _head(world)
    assert revision.base_revision == head_before
    with session_scope() as session:
        chain = list(revision_chain(session, world))
        events = list(revision_events(session, world, revision.id))
        entities, edges = world_state(session, world)

    assert len(chain) == 2  # seed + the accept revision — nothing else
    assert sorted(ev.type for ev in events) == [
        "edge_created",
        "edge_created",
        "entity_created",
    ]
    sable = next(e for e in entities if e.name == "Sable Rook")
    # Provenance is durable on the row, not just the returned object:
    # the row points at the entity it became and the revision that made
    # it real (spec-3.3 deferred finding).
    accepted_entity_id = accepted.accepted_entity_id
    accept_revision_id = accepted.accept_revision_id
    assert accepted_entity_id == sable.id
    assert accept_revision_id == revision.id
    mrow = _row(world, candidate.id)
    assert mrow.accepted_entity_id == sable.id
    assert mrow.accept_revision_id == revision.id
    payload = candidate.payload
    assert set(sable.data) == set(payload) - {"edges"}
    assert sable.data["stat_block"] == payload["stat_block"]  # entity-data convention
    assert sable.data["personality"] == "dry"
    relationships = {(x.src, x.dst, x.type, x.counter) for x in edges}
    assert (sable.id, mira_id, "rival_of", 2) in relationships  # outbound
    assert (bar_id, sable.id, "member_of", 1) in relationships  # inbound
    # The row is settled in the database, not just in the returned object.
    assert _row(world, candidate.id).status == "accepted"


def test_accept_creates_new_entity_never_mutates_existing(world: str) -> None:
    """FR11/AR4: accepting for an existing entity creates a NEW entity
    (fresh ULID) — the accepted one is byte-identical after, and the
    accept revision carries no ``entity_updated`` events."""
    bar_id, mira_id = _seed_world(world)
    entities_before, _edges_before = _state(world)
    candidate = _stage(world, _payload(mira_id, bar_id))

    _accepted, revision = accept_candidate(world, candidate.id)

    entities_after, _edges_after = _state(world)
    mira_before = next(e for e in entities_before if e[2] == "Mira Vane")
    mira_after = next(e for e in entities_after if e[2] == "Mira Vane")
    assert mira_before == mira_after  # not mutated, not re-keyed
    with session_scope() as session:
        events = list(revision_events(session, world, revision.id))
    assert "entity_updated" not in {ev.type for ev in events}
    assert "edge_updated" not in {ev.type for ev in events}


# ---------------------------------------------------------------------------
# EDIT_THEN_ACCEPT (spec-3.3): payload override
# ---------------------------------------------------------------------------


def test_accept_override_commits_edited_payload(world: str) -> None:
    """EDIT_THEN_ACCEPT: the override's edited sections commit in the
    same single transaction with the staged edges verbatim; one new
    revision; the row settles ``accepted``."""
    bar_id, mira_id = _seed_world(world)
    head_before = _head(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    override = dict(candidate.payload, personality="edited by the DM's hand")

    accepted, revision = accept_candidate(world, candidate.id, payload_override=override)

    assert accepted.status == "accepted"
    assert revision.base_revision == head_before
    entities, edges = _state(world)
    sable = next(e for e in entities if e[2] == "Sable Rook")
    data = json.loads(sable[4])
    assert data["personality"] == "edited by the DM's hand"
    assert data["secret"] == "s"  # unedited sections pass through
    # The staged edges commit verbatim, counters included (relation editing is 3.4).
    relationships = {(src, dst, type_, counter) for _id, src, dst, type_, counter, _at in edges}
    assert (sable[0], mira_id, "rival_of", 2) in relationships  # outbound, counter intact
    assert (bar_id, sable[0], "member_of", 1) in relationships  # inbound, counter intact
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 2  # seed + accept
    assert _row(world, candidate.id).status == "accepted"


def test_accept_override_edited_edges_commit(world: str) -> None:
    """Spec-3.4: the override may carry the DM's OWN edge set — added,
    edited, and deleted staged edges all commit in the same single
    transaction; one revision; the row settles ``accepted``."""
    bar_id, mira_id = _seed_world(world)
    head_before = _head(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    # The DM's edited set: drop the inbound member_of, edit the rival_of
    # counter 2 -> 7, add a new outbound ally_of to the bar.
    override = dict(
        candidate.payload,
        edges=[
            {"endpoint": mira_id, "direction": "outbound", "type": "rival_of", "counter": 7},
            {"endpoint": bar_id, "direction": "outbound", "type": "ally_of", "counter": 1},
        ],
    )

    accepted, revision = accept_candidate(world, candidate.id, payload_override=override)

    assert accepted.status == "accepted"
    assert revision.base_revision == head_before
    _entities, edges = _state(world)
    sable = next(e for e in _entities if e[2] == "Sable Rook")
    relationships = {(src, dst, type_, counter) for _id, src, dst, type_, counter, _at in edges}
    assert (sable[0], mira_id, "rival_of", 7) in relationships  # edited counter
    assert (sable[0], bar_id, "ally_of", 1) in relationships  # added edge
    assert not any(
        type_ == "member_of" and src == bar_id and dst == sable[0]
        for _id, src, dst, type_, _c, _at in edges
    )  # deleted staged edge
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 2  # seed + accept
    assert _row(world, candidate.id).status == "accepted"


def test_accept_override_bad_edge_endpoint_rejected(world: str) -> None:
    """Spec-3.4: an override edge whose endpoint is not committed world
    state is an ``InvalidCandidateError`` (422): zero revisions, the row
    stays ``proposed`` — never a partial commit."""
    bar_id, mira_id = _seed_world(world)
    state_before = _state(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    override = dict(
        candidate.payload,
        edges=[{"endpoint": "Z" * 26, "direction": "outbound", "type": "ally_of", "counter": 1}],
    )

    with pytest.raises(InvalidCandidateError, match="resolve to committed world"):
        accept_candidate(world, candidate.id, payload_override=override)
    assert _state(world) == state_before
    assert _row(world, candidate.id).status == "proposed"


def test_accept_override_out_of_vocab_edge_rejected(world: str) -> None:
    """Spec-3.4: an override edge type outside the closed vocabulary is an
    ``InvalidCandidateError`` — the row stays ``proposed``."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    override = dict(
        candidate.payload,
        edges=[{"endpoint": mira_id, "direction": "outbound", "type": "friends", "counter": 1}],
    )

    with pytest.raises(InvalidCandidateError, match="closed vocabulary"):
        accept_candidate(world, candidate.id, payload_override=override)

    assert _row(world, candidate.id).status == "proposed"


def test_accept_override_zero_edges_orphan_rejected(world: str) -> None:
    """Spec-3.4: an override that strips every edge commits a fresh entity
    with zero edges — the commit path's ``OrphanEntityError`` (422) rolls
    back the whole accept; the row stays ``proposed``."""
    bar_id, mira_id = _seed_world(world)
    state_before = _state(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    override = dict(candidate.payload, edges=[])

    with pytest.raises(OrphanEntityError):
        accept_candidate(world, candidate.id, payload_override=override)

    assert _state(world) == state_before
    assert _row(world, candidate.id).status == "proposed"


def test_accept_override_missing_edges_keeps_staged(world: str) -> None:
    """Spec-3.4: an override WITHOUT the ``edges`` key keeps the staged
    set (3.3 client compat) — the staged edges commit unchanged."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    override = {key: value for key, value in candidate.payload.items() if key != "edges"}

    accepted, _revision = accept_candidate(world, candidate.id, payload_override=override)

    assert accepted.status == "accepted"
    _entities, edges = _state(world)
    sable = next(e for e in _entities if e[2] == "Sable Rook")
    relationships = {(src, dst, type_, counter) for _id, src, dst, type_, counter, _at in edges}
    assert (sable[0], mira_id, "rival_of", 2) in relationships
    assert (bar_id, sable[0], "member_of", 1) in relationships
    assert _row(world, candidate.id).status == "accepted"


def test_accept_non_dict_override_rejected(world: str) -> None:
    """A non-dict override (a corrupted client) is an
    ``InvalidCandidateError``, never a silent commit."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))

    with pytest.raises(InvalidCandidateError, match="override"):
        accept_candidate(world, candidate.id, payload_override="nope")  # type: ignore[arg-type]

    assert _row(world, candidate.id).status == "proposed"


def test_accept_override_equal_to_staged_accepts(world: str) -> None:
    """An override identical to the staged payload is the edit-screen
    no-op: it commits exactly like the unedited accept."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))

    accepted, _revision = accept_candidate(
        world, candidate.id, payload_override=dict(candidate.payload)
    )

    assert accepted.status == "accepted"
    assert _row(world, candidate.id).status == "accepted"


def test_accept_override_shape_violations_rejected(world: str) -> None:
    """An override must satisfy the same required AR24 section shape as
    staging (spec-3.3): dropping or blanking a section is an
    ``InvalidCandidateError`` — zero revisions, the row stays
    ``proposed``, never a partial commit."""
    bar_id, mira_id = _seed_world(world)
    state_before = _state(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    staged = candidate.payload

    def reject(override: Any, match: str) -> None:
        with pytest.raises(InvalidCandidateError, match=match):
            accept_candidate(world, candidate.id, payload_override=override)
        assert _row(world, candidate.id).status == "proposed"

    # name: non-str (P3) / absent (P3) / blank
    reject(dict(staged, name=42), "name must be a non-blank string")
    reject({k: v for k, v in staged.items() if k != "name"}, "name must be a non-blank string")
    reject(dict(staged, name="   "), "name must be a non-blank string")
    # role / identity + lore sections
    reject(dict(staged, role="Wizard"), "role must be one of")
    reject({k: v for k, v in staged.items() if k != "goals"}, "goals must be a non-blank string")
    reject(dict(staged, appearance=""), "appearance must be a non-blank string")
    # world-integration block
    reject({k: v for k, v in staged.items() if k != "world_integration"}, "world_integration")
    partial = dict(staged, world_integration={"reputation": "dread"})
    reject(partial, "factions must be a non-blank string")
    # boss conditional: NPC must not carry one; Monster must
    reject(dict(staged, boss=dict.fromkeys(BOSS_FIELDS, "x")), "boss section is only allowed")
    reject(
        dict(staged, role="Monster"),
        "boss must be an object",
    )
    reject(
        dict(
            staged,
            role="Monster",
            boss={
                "lair_actions": "",
                "legendary_actions": "x",
                "immunities": "x",
                "vulnerabilities": "x",
            },
        ),
        "boss.lair_actions must be a non-blank string",
    )

    assert _state(world) == state_before


def test_accept_override_boss_stages_for_monster(world: str) -> None:
    """The conditional path through the guard: a Monster override with a
    complete boss section passes the shape and commits it."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    override = dict(
        candidate.payload,
        role="Monster",
        boss=dict.fromkeys(BOSS_FIELDS, "details"),
    )

    accepted, _revision = accept_candidate(world, candidate.id, payload_override=override)

    assert accepted.status == "accepted"
    entities, _edges = _state(world)
    sable = next(e for e in entities if e[2] == "Sable Rook")
    data = json.loads(sable[4])
    assert data["role"] == "Monster"
    assert set(data["boss"]) == set(BOSS_FIELDS)


# ---------------------------------------------------------------------------
# ACCEPT_STALE_ENDPOINT (commit path = accept-time authority, AD-2)
# ---------------------------------------------------------------------------


def test_accept_dead_endpoint_rejected_row_stays_proposed(world: str) -> None:
    """An endpoint deleted since staging fails the accept with
    ``DanglingEdgeError`` — no revision, status stays ``proposed``."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    delete_entity(world, mira_id, cascade=True, base_revision=_head(world))
    state_before = _state(world)

    with pytest.raises(DanglingEdgeError):
        accept_candidate(world, candidate.id)

    assert _row(world, candidate.id).status == "proposed"
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 2  # seed + delete only
    assert _state(world) == state_before


# ---------------------------------------------------------------------------
# ACCEPT_BAD_COUNTER (commit path guards int shape)
# ---------------------------------------------------------------------------


def test_accept_non_int_counter_rejected_row_stays_proposed(world: str) -> None:
    """ACCEPT_BAD_COUNTER: a staged edge whose counter is not an int is
    rejected — no state change (staging itself only validates
    endpoint/direction/type, so such a row can exist). The accept-time
    guard fires before the commit path's ``InvalidEdgeCounterError``;
    either way the wire code is a 422."""
    bar_id, mira_id = _seed_world(world)
    payload = _payload(mira_id, bar_id)
    payload["edges"][0]["counter"] = "three"
    candidate = _stage(world, payload)
    state_before = _state(world)

    with pytest.raises(InvalidCandidateError):
        accept_candidate(world, candidate.id)

    assert _row(world, candidate.id).status == "proposed"
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1
    assert _state(world) == state_before


def test_accept_missing_counter_rejected(world: str) -> None:
    """A staged edge record with NO counter key (only possible outside
    the staging contract) must not silently default to 1 — it is an
    ``InvalidCandidateError`` and nothing is committed."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    with session_scope() as session:
        row = session.get(models.ProposedCandidate, candidate.id)
        assert row is not None
        payload = dict(row.payload)
        stripped = dict(payload["edges"][0])
        del stripped["counter"]
        payload["edges"] = [stripped]
        row.payload = payload
    state_before = _state(world)

    with pytest.raises(InvalidCandidateError):
        accept_candidate(world, candidate.id)

    assert _row(world, candidate.id).status == "proposed"
    assert _state(world) == state_before


def test_accept_malformed_payload_edge_rejected(world: str) -> None:
    """A payload edge outside the staging contract (unknown direction) is
    a structured ``InvalidCandidateError``, never a crash.

    ``stage_candidates`` already validates edge shape, so this backstop
    is exercised by corrupting a staged row's payload directly — the
    guard exists for rows that bypass the staging contract (AR24
    forward-compatibility leftovers, direct DB writes)."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    with session_scope() as session:
        row = session.get(models.ProposedCandidate, candidate.id)
        assert row is not None
        payload = dict(row.payload)
        payload["edges"] = [dict(payload["edges"][0], direction="sideways")]
        row.payload = payload

    with pytest.raises(InvalidCandidateError):
        accept_candidate(world, candidate.id)

    assert _row(world, candidate.id).status == "proposed"
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1


# ---------------------------------------------------------------------------
# ACCEPT_UNKNOWN / ACCEPT_ALREADY_SETTLED
# ---------------------------------------------------------------------------


def test_accept_unknown_or_foreign_candidate_not_found(world: str) -> None:
    """ACCEPT_UNKNOWN: an unknown id and a candidate of another campaign
    are the same indistinguishable ``CandidateNotFoundError``."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    other = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id

    with pytest.raises(CandidateNotFoundError):
        accept_candidate(world, "0" * 26)
    with pytest.raises(CandidateNotFoundError):
        accept_candidate(world, "Z" * 26)
    with pytest.raises(CandidateNotFoundError):
        accept_candidate(other, candidate.id)  # another campaign's row
    # Nothing settled by the failed attempts.
    assert _row(world, candidate.id).status == "proposed"


def test_accept_already_settled_conflict(world: str) -> None:
    """ACCEPT_ALREADY_SETTLED: accepting twice (or accepting a rejected
    row) is ``CandidateSettledError`` — terminal statuses are final."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    accept_candidate(world, candidate.id)
    revisions_after_accept = _head(world)

    with pytest.raises(CandidateSettledError):
        accept_candidate(world, candidate.id)

    with pytest.raises(CandidateSettledError):
        reject_candidate(world, candidate.id)
    assert _head(world) == revisions_after_accept  # no new revision
    assert _row(world, candidate.id).status == "accepted"
    # An accepted row keeps its provenance even after a later conflict.
    mrow = _row(world, candidate.id)
    assert mrow.accepted_entity_id is not None
    assert mrow.accept_revision_id is not None


# ---------------------------------------------------------------------------
# REJECT_STANDALONE / REJECT_ALREADY_SETTLED
# ---------------------------------------------------------------------------


def test_reject_settles_row_world_untouched(world: str) -> None:
    """REJECT_STANDALONE: the row flips to ``rejected`` with no revision,
    no event, and no world change."""
    bar_id, mira_id = _seed_world(world)
    state_before = _state(world)
    candidate = _stage(world, _payload(mira_id, bar_id))

    rejected = reject_candidate(world, candidate.id)

    assert rejected.status == "rejected"
    assert _row(world, candidate.id).status == "rejected"
    # A rejected row produces no world artifact, so it carries no
    # provenance (provenance is only set by acceptance — spec-3.3).
    mrow = _row(world, candidate.id)
    assert mrow.accepted_entity_id is None
    assert mrow.accept_revision_id is None
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1  # seed only
    assert _state(world) == state_before


def test_reject_already_settled_conflict(world: str) -> None:
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    reject_candidate(world, candidate.id)

    with pytest.raises(CandidateSettledError):
        reject_candidate(world, candidate.id)
    with pytest.raises(CandidateSettledError):
        accept_candidate(world, candidate.id)


# ---------------------------------------------------------------------------
# Atomicity: the subgraph commit and the status flip share ONE transaction
# ---------------------------------------------------------------------------


def test_accept_commit_failure_rolls_back_everything(
    world: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AD-15: a failure inside the commit (mid-write, after the revision
    is staged) rolls back the whole accept — status stays ``proposed``
    and zero revisions persist."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    state_before = _state(world)

    import app.store.commit as commit_module

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected mid-commit failure")

    monkeypatch.setattr(commit_module, "_add_event", boom)
    with pytest.raises(RuntimeError):
        accept_candidate(world, candidate.id)

    assert _row(world, candidate.id).status == "proposed"
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1  # seed only
    assert _state(world) == state_before


def test_accept_failed_status_flip_rolls_back_subgraph(
    world: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AD-15, the other half: a flip that violates the status CHECK makes
    the flush fail AFTER the subgraph writes are staged — the shared
    transaction rolls both back. This is what makes accept all-or-nothing
    rather than commit-then-settle."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    state_before = _state(world)

    monkeypatch.setattr("app.store.candidates.STATUS_ACCEPTED", "bogus")
    with pytest.raises(IntegrityError):
        accept_candidate(world, candidate.id)

    assert _row(world, candidate.id).status == "proposed"
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1  # seed only
        entities, edges = world_state(session, world)
    assert not any(e.name == "Sable Rook" for e in entities)
    assert _state(world) == state_before


# ---------------------------------------------------------------------------
# UNDO_AFTER_DONE
# ---------------------------------------------------------------------------


def test_undo_after_accept_reverts_world_row_stays_accepted(world: str) -> None:
    """UNDO_AFTER_DONE: undoing the accept revision reverts the world; the
    row keeps its terminal status — a lifecycle fact, not world state."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    _accepted, revision = accept_candidate(world, candidate.id)

    undo(world, revision.id)

    with session_scope() as session:
        entities, _edges = world_state(session, world)
    assert not any(e.name == "Sable Rook" for e in entities)
    assert _row(world, candidate.id).status == "accepted"


# ---------------------------------------------------------------------------
# Guard backstops (rows outside the staging contract)
# ---------------------------------------------------------------------------


def _corrupt_payload(candidate_id: str, mutate: Any) -> None:
    """Rewrite a staged row's payload directly (bypassing the staging
    contract) so the accept-time guards can be exercised."""
    with session_scope() as session:
        row = session.get(models.ProposedCandidate, candidate_id)
        assert row is not None
        payload = dict(row.payload)
        mutate(payload)
        row.payload = payload


def test_accept_non_dict_payload_rejected(world: str) -> None:
    """A null/non-dict payload must raise the structured
    ``InvalidCandidateError``, never a raw TypeError from dict()."""
    bar_id, mira_id = _seed_world(world)
    junk_values: tuple[Any, ...] = (None, [1, 2], "sable")
    for junk in junk_values:
        candidate = _stage(world, _payload(mira_id, bar_id))
        candidate_id = candidate.id
        with session_scope() as session:
            row = session.get(models.ProposedCandidate, candidate_id)
            assert row is not None
            row.payload = junk
        with pytest.raises(InvalidCandidateError):
            accept_candidate(world, candidate_id)
        assert _row(world, candidate_id).status == "proposed"


def test_accept_non_entity_kind_rejected(world: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A future staging kind or a corrupt row must not silently commit as
    a character — kind != PROPOSAL_KIND is an ``InvalidCandidateError``.
    The DB CHECK already forbids writing a non-'entity' kind, so the
    guard is exercised by pointing the accept path's contract constant at
    a future kind: the staged 'entity' row no longer matches."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    state_before = _state(world)

    monkeypatch.setattr("app.store.candidates.PROPOSAL_KIND", "faction")
    with pytest.raises(InvalidCandidateError):
        accept_candidate(world, candidate.id)

    assert _row(world, candidate.id).status == "proposed"
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1
    assert _state(world) == state_before


def test_accept_edgeless_candidate_orphan_rejected(world: str) -> None:
    """A staged row with ``edges: []`` (reachable only by bypassing
    staging — the no-orphans rule forbids staging a wired candidate for
    an empty world) fails accept with the commit path's
    ``OrphanEntityError``: zero state change, status stays ``proposed``.
    Pinned in the docstring's rejection list."""
    from app.store import OrphanEntityError

    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    _corrupt_payload(candidate.id, lambda payload: payload.update(edges=[]))
    state_before = _state(world)

    with pytest.raises(OrphanEntityError):
        accept_candidate(world, candidate.id)

    assert _row(world, candidate.id).status == "proposed"
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1
    assert _state(world) == state_before


def test_accept_on_empty_world_rejects_with_zero_revisions(world: str) -> None:
    """The base-``None`` branch: on a zero-revision world the head read
    yields ``base_revision=None`` and ``_check_base`` must not misfire as
    ``StaleRevisionError`` — a hand-staged edgeless candidate fails with
    the orphans error instead. (A SUCCESSFUL accept can never have base
    ``None``: its edges must name a committed endpoint, which requires a
    prior revision — staging enforces this, so the row is hand-written
    here.)"""
    with session_scope() as session:
        # enqueue_job refuses a generate job on an entity-less world, so
        # the row (and its job) is written directly — the hand-staged
        # backstop path.
        job = models.Job(
            id=ids.new_id(),
            campaign_id=world,
            kind="generate",
            payload={"ask": "hand-staged"},
            state="succeeded",
            progress=1.0,
            max_llm_calls=0,
            max_media_calls=0,
            error=None,
            result=None,
            created_at="2026-09-04T00:00:00Z",
            started_at=None,
            finished_at=None,
        )
        session.add(job)
        session.add(
            models.ProposedCandidate(
                id=ids.new_id(),
                campaign_id=world,
                job_id=job.id,
                kind="entity",
                status="proposed",
                payload={"name": "Sable Rook", "edges": []},
                created_at="2026-09-04T00:00:00Z",
            )
        )
        row = session.scalars(
            select(models.ProposedCandidate).where(models.ProposedCandidate.campaign_id == world)
        ).one()
        candidate_id = row.id

    from app.store import OrphanEntityError, StaleRevisionError

    with pytest.raises(OrphanEntityError) as excinfo:
        accept_candidate(world, candidate_id)

    assert not isinstance(excinfo.value, StaleRevisionError)
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 0
    assert _row(world, candidate_id).status == "proposed"


# ---------------------------------------------------------------------------
# discard_candidates vs the lifecycle (cancel-race cleanup)
# ---------------------------------------------------------------------------


def test_discard_keeps_settled_rows_and_removes_proposed(world: str) -> None:
    """The cancel-race cleanup must never erase settled rows (the
    accepted entity stays in the world; the audit trail is terminal) —
    only still-proposed rows of the job are removed."""
    bar_id, mira_id = _seed_world(world)
    settled = _stage(world, _payload(mira_id, bar_id))
    accept_candidate(world, settled.id)

    assert discard_candidates(settled.job_id) == 0
    assert _row(world, settled.id).status == "accepted"

    fresh_job = enqueue_job(world, "generate", {"ask": "another"})
    (proposed,) = stage_candidates(world, fresh_job.id, [_payload(mira_id, bar_id)])
    assert discard_candidates(fresh_job.id) == 1
    with session_scope() as session:
        assert session.get(models.ProposedCandidate, proposed.id) is None


def test_discard_then_accept_is_not_found_and_commits_nothing(world: str) -> None:
    """The serialized accept-vs-discard race: a proposed row cleaned
    pre-accept makes the accept a ``CandidateNotFoundError`` that commits
    nothing — no revision, no entity, status never settled."""
    bar_id, mira_id = _seed_world(world)
    candidate = _stage(world, _payload(mira_id, bar_id))
    state_before = _state(world)

    assert discard_candidates(candidate.job_id) == 1
    with pytest.raises(CandidateNotFoundError):
        accept_candidate(world, candidate.id)

    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1  # seed only
        entities, _edges = world_state(session, world)
    assert not any(e.name == "Sable Rook" for e in entities)
    assert _state(world) == state_before


# ---------------------------------------------------------------------------
# _migrate_proposed_candidate_status (pre-3.2 database rebuild)
# ---------------------------------------------------------------------------


def test_migrate_proposed_candidate_status_widens_check(tmp_path: Path) -> None:
    """A pre-3.2 database's status CHECK (only ``proposed``) is rebuilt to
    admit the full lifecycle: rows survive unchanged, the campaign/job
    indexes are re-created, the widened constraint accepts all three
    statuses (and only them), and a second migration pass is a no-op."""
    db_path = tmp_path / "old-schema.db"
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
            owner_id VARCHAR(26) NOT NULL REFERENCES account(id),
            title VARCHAR(300) NOT NULL,
            description TEXT NOT NULL,
            theme VARCHAR(100) NOT NULL,
            custom_lore TEXT NOT NULL,
            created_at VARCHAR(40) NOT NULL
        );
        CREATE TABLE job (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            campaign_id VARCHAR(26) NOT NULL REFERENCES campaign(id),
            kind VARCHAR(64) NOT NULL,
            payload JSON NOT NULL,
            state VARCHAR(32) NOT NULL,
            progress FLOAT NOT NULL,
            max_llm_calls INTEGER NOT NULL,
            max_media_calls INTEGER NOT NULL,
            error TEXT,
            result JSON,
            created_at VARCHAR(40) NOT NULL,
            started_at VARCHAR(40),
            finished_at VARCHAR(40),
            CONSTRAINT ck_job_kind CHECK (kind IN ('text','image','video','build_in','generate'))
        );
        CREATE TABLE proposed_candidate (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            campaign_id VARCHAR(26) NOT NULL REFERENCES campaign(id),
            job_id VARCHAR(26) NOT NULL REFERENCES job(id),
            kind VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL,
            payload JSON NOT NULL,
            created_at VARCHAR(40) NOT NULL,
            CONSTRAINT ck_proposed_candidate_kind CHECK (kind IN ('entity')),
            CONSTRAINT ck_proposed_candidate_status CHECK (status IN ('proposed'))
        );
        CREATE INDEX ix_proposed_candidate_campaign_id
            ON proposed_candidate (campaign_id);
        CREATE INDEX ix_proposed_candidate_job_id ON proposed_candidate (job_id);
        """
    )
    campaign_id, job_id = ids.new_id(), ids.new_id()
    raw.execute(
        "INSERT INTO account VALUES (?, 'dm@example.com', 'x', 'now')",
        (_owner_id(),),
    )
    raw.execute(
        "INSERT INTO campaign VALUES (?, ?, 'Old World', '', 'High Fantasy', '', 'now')",
        (campaign_id, _owner_id()),
    )
    raw.execute(
        "INSERT INTO job VALUES (?, ?, 'generate', '{}', 'succeeded', 0.0, 10, 10,"
        " NULL, NULL, 'now', NULL, NULL)",
        (job_id, campaign_id),
    )
    staged_ids = [ids.new_id() for _ in range(2)]
    for staged_id in staged_ids:
        raw.execute(
            "INSERT INTO proposed_candidate VALUES (?, ?, ?, 'entity', 'proposed', '{}', 'now')",
            (staged_id, campaign_id, job_id),
        )
    raw.commit()
    raw.close()

    previous = app_db_url()
    init_db(f"sqlite:///{db_path}")
    try:
        engine = get_engine()
        with engine.connect() as conn:
            ddl_row = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE name='proposed_candidate'"
            ).fetchone()
            assert ddl_row is not None
            ddl = ddl_row[0]
            indexes = {
                name
                for (name,) in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                    " AND tbl_name='proposed_candidate' AND sql IS NOT NULL"
                ).fetchall()
            }
            survivors = conn.exec_driver_sql(
                "SELECT id, status FROM proposed_candidate ORDER BY rowid"
            ).fetchall()
        assert all(f"'{status}'" in ddl for status in ("proposed", "accepted", "rejected"))
        assert indexes == {"ix_proposed_candidate_campaign_id", "ix_proposed_candidate_job_id"}
        assert [(row[0], row[1]) for row in survivors] == [
            (staged_id, "proposed") for staged_id in staged_ids
        ]
        # The widened constraint admits the terminal statuses...
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO proposed_candidate"
                " (id, campaign_id, job_id, kind, status, payload, created_at) VALUES"
                " (?, ?, ?, 'entity', 'accepted', '{}', 'now')",
                (ids.new_id(), campaign_id, job_id),
            )
        # ...and still rejects junk.
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO proposed_candidate"
                " (id, campaign_id, job_id, kind, status, payload, created_at)"
                " VALUES (?, ?, ?, 'entity', 'settled', '{}', 'now')",
                (ids.new_id(), campaign_id, job_id),
            )
        # A second pass is a no-op: DDL and row count unchanged.
        from app.store.db import _migrate_proposed_candidate_status

        _migrate_proposed_candidate_status(engine)
        with engine.connect() as conn:
            ddl_after_row = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE name='proposed_candidate'"
            ).fetchone()
            assert ddl_after_row is not None
            ddl_after = ddl_after_row[0]
            count_after = conn.exec_driver_sql("SELECT COUNT(*) FROM proposed_candidate").scalar()
        assert ddl_after == ddl
        assert count_after == 3
    finally:
        init_db(previous)
