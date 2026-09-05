"""Entity hand-edit PATCH API tests (spec-3.6, FR10): the DM is the final
author. Covers the I/O matrix rows EDIT_SECTION, BARE_RECORD,
EDIT_IDENTITY_NAME, EDIT_ROLE_BOSS/UNBOSS, EDIT_STATBLOCK, CUSTOM_KEY,
EDGELESS, TEXT_TYPE, NOOP_PATCH, STALE_BASE, OMITTED_BASE, FOREIGN,
UNKNOWN_ENTITY and UNAUTH — plus the store-parity + undo round-trip and
the custom-key regeneration splice round trip. Uses the https TestClient
so the Secure session cookie round-trips (1.5), with a fresh scratch DB
per test (the entities/edges API fixture pattern)."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import ids
from app.store import (
    BOSS_FIELDS,
    EntityEditConflictError,
    commit_subgraph,
    delete_edge,
    models,
    session_scope,
    stage_candidates,
    undo,
    world_entities,
)
from app.store.jobs import enqueue_job
from app.store.read import latest_revision, revision_chain, revision_events


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    """An https TestClient over a per-test app, with a FRESH scratch DB
    per test (same isolation contract as the entities/edges API tests)."""
    from app.main import create_app
    from app.store import app_db_url, init_db

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'entity-edit.db'}")
    try:
        with TestClient(create_app(), base_url="https://testserver") as test_client:
            yield test_client
    finally:
        init_db(previous)


@pytest.fixture(autouse=True)
def _reset_limiters() -> Any:
    from app.api.auth import _login_limiter, _register_limiter

    _login_limiter.reset()
    _register_limiter.reset()
    yield
    _login_limiter.reset()
    _register_limiter.reset()


def _register_login(client: Any, email: str = "dm@example.com") -> None:
    client.post("/api/auth/register", json={"email": email, "password": "correct-battery-horse"})
    response = client.post(
        "/api/auth/login", json={"email": email, "password": "correct-battery-horse"}
    )
    assert response.status_code == 200


def _create_campaign(client: Any) -> Any:
    return client.post(
        "/api/campaigns",
        json={
            "title": "Aetheria",
            "description": "A living world",
            "theme": "High Fantasy",
            "custom_lore": "",
        },
    ).json()


def _ar24_record(name: str = "Mira Vane", role: str = "NPC") -> dict[str, Any]:
    """A full AR24 sectioned record (spec-3.3): the AR19 core, every
    identity/lore section, the world-integration block, a stat block —
    plus one AR24 forward-compat unknown key (``ambient_theme``) that
    must pass through untouched."""
    return {
        "name": name,
        "role": role,
        "personality": "warm, watchful",
        "secret": "once ran with the Guild",
        "rumor": "owes a debt at the bar",
        "party_hook": "knows the tunnels below",
        "level_cr": "level 5",
        "race_type": "Human",
        "class_profession": "Barkeep",
        "alignment": "NG",
        "appearance": "kind eyes, silver-streaked hair",
        "background": "heir to the Gilded Bar",
        "goals": "keep the bar out of Guild hands",
        "relationships": "debt-bound to the Guild",
        "voice_style": "low, unhurried",
        "catchphrases": '"Pour one for the road."',
        "stat_block": {
            "identity": {"role": "NPC", "level": 5, "race": "Human", "alignment": "NG"},
            "attributes": {"str": 12, "dex": 11, "con": 12, "int": 13, "wis": 14, "cha": 15},
            "combat": {"ac": 13, "hp": 27},
        },
        "world_integration": {
            "reputation": "fair keeper of the Gilded Bar",
            "factions": "The Guild (reluctantly)",
            "current_location": "the Gilded Bar",
            "reaction_matrix": "wary of strangers, warm to regulars",
            "on_defeat": "surrenders the ledger",
        },
        "ambient_theme": {"motif": "copper coins"},
    }


def _seed_world(campaign_id: str) -> tuple[str, str]:
    """The Gilded Bar (bare faction, data={}) + Mira Vane (full AR24
    record) with one live edge (store commit path)."""
    bar_id, mira_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            models.EntityInput(kind="character", name="Mira Vane", data=_ar24_record(), id=mira_id),
        ],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1)],
        base_revision=None,
    )
    return bar_id, mira_id


def _head(campaign_id: str) -> str | None:
    with session_scope() as session:
        latest = latest_revision(session, campaign_id)
        return latest.id if latest is not None else None


def _chain(campaign_id: str) -> list[models.Revision]:
    with session_scope() as session:
        return list(revision_chain(session, campaign_id))


def _revision_count(campaign_id: str) -> int:
    return len(_chain(campaign_id))


def _entity(campaign_id: str, entity_id: str) -> models.Entity:
    with session_scope() as session:
        row = session.get(models.Entity, entity_id)
        assert row is not None and row.campaign_id == campaign_id
        return row


def _patch(client: Any, campaign_id: str, entity_id: str, **kwargs: Any) -> Any:
    return client.request("PATCH", f"/api/campaigns/{campaign_id}/entities/{entity_id}", **kwargs)


# ---------------------------------------------------------------------------
# UNAUTH / FOREIGN / UNKNOWN_ENTITY (ownership first, 404 before body parse)
# ---------------------------------------------------------------------------


def test_patch_requires_auth(client: Any) -> None:
    """UNAUTH: no session — 401 before any ownership or body check."""
    response = client.patch(f"/api/campaigns/{'1' * 26}/entities/{'2' * 26}", json={"x": 1})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_patch_foreign_campaign_404_before_body_parse(client: Any) -> None:
    """FOREIGN: ownership is checked FIRST — another DM's campaign (or a
    foreign entity ULID inside an owned campaign) is the single
    indistinguishable 404 even with a malformed, unparseable body."""
    _register_login(client, "dm@example.com")
    mine = _create_campaign(client)
    _bar_id, _mira_id = _seed_world(mine["id"])
    _register_login(client, "other@example.com")
    theirs = _create_campaign(client)
    _other_bar, other_mira = _seed_world(theirs["id"])
    # A malformed body on a foreign campaign is still a 404 — the body is
    # never parsed (the owner check runs first).
    response = _patch(client, mine["id"], "1" * 26, content=b"{oops")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    # A well-formed body on a foreign entity inside the OTHER DM's own
    # campaign works for them…
    response = _patch(client, theirs["id"], other_mira, json={"personality": "x"})
    assert response.status_code == 204
    # …and a foreign entity ULID is invisible inside an owned campaign.
    _register_login(client, "dm@example.com")
    response = _patch(client, mine["id"], other_mira, json={"personality": "x"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_patch_unknown_entity_404_zero_revisions(client: Any) -> None:
    """UNKNOWN_ENTITY: a well-formed PATCH on a ULID that names no entity
    of this campaign is a 404 with zero revisions (4xx never changes
    state)."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, _mira_id = _seed_world(mine["id"])
    chain_before = len(_chain(mine["id"]))
    response = _patch(client, mine["id"], "0" * 26, json={"personality": "x"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert len(_chain(mine["id"])) == chain_before
    with session_scope() as session:
        assert bar_id in {e.id for e in world_entities(session, mine["id"])}


# ---------------------------------------------------------------------------
# Body-validation 400s (malformed / non-object / empty / base-only)
# ---------------------------------------------------------------------------


def test_patch_malformed_body_400(client: Any) -> None:
    """A syntactically invalid body is a client error — 400, never
    silently treated as absent; nothing changes."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    response = _patch(client, mine["id"], mira_id, content=b"{oops")
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"
    assert _entity(mine["id"], mira_id).data["personality"] == "warm, watchful"


def test_patch_non_dict_body_400(client: Any) -> None:
    """A JSON body that is not an object (array, number, string) is a
    400 — a present body the client sent, not an absent one."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    for body in (b"[1, 2]", b"42", b'"personality"'):
        response = _patch(client, mine["id"], mira_id, content=body)
        assert response.status_code == 400
    assert _entity(mine["id"], mira_id).data["personality"] == "warm, watchful"


def test_patch_empty_and_absent_body_400(client: Any) -> None:
    """An empty object (or no body at all) is a 400 — a PATCH must carry
    at least one content key."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    assert _patch(client, mine["id"], mira_id, json={}).status_code == 400
    assert _patch(client, mine["id"], mira_id).status_code == 400
    assert _entity(mine["id"], mira_id).data["personality"] == "warm, watchful"


def test_patch_base_only_body_400(client: Any) -> None:
    """A body whose only key is ``base_revision`` is a 400 — no content
    key, nothing to merge (even with the current head)."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    response = _patch(client, mine["id"], mira_id, json={"base_revision": _head(mine["id"])})
    assert response.status_code == 400
    assert len(_chain(mine["id"])) == 1


def test_patch_non_string_base_revision_400(client: Any) -> None:
    """A non-string ``base_revision`` is a 400 before any store call."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    response = _patch(client, mine["id"], mira_id, json={"personality": "x", "base_revision": 5})
    assert response.status_code == 400
    assert _entity(mine["id"], mira_id).data["personality"] == "warm, watchful"


# ---------------------------------------------------------------------------
# EDIT_SECTION: one revision, byte-identical elsewhere, ULID stable, undo
# ---------------------------------------------------------------------------


def test_patch_section_one_revision_byte_identical_ulid_stable_and_undo(
    client: Any,
) -> None:
    """EDIT_SECTION: a single-section PATCH on a shape-valid character
    commits exactly ONE ``entity_updated`` revision; only that field
    changes (every other data key, unknown keys included, is
    byte-identical in the event snapshots); the entity ULID stays
    stable; undoing the revision restores the prior record
    byte-identical and the undo is itself one revision (AD-2)."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    before = _entity(mine["id"], mira_id)
    before_data = dict(before.data)
    head_before = _head(mine["id"])

    response = _patch(client, mine["id"], mira_id, json={"personality": "edited by the DM's hand"})
    assert response.status_code == 204

    chain = _chain(mine["id"])
    assert len(chain) == 2  # seed + exactly one edit revision
    revision = chain[-1]
    assert revision.base_revision == head_before
    with session_scope() as session:
        events = list(revision_events(session, mine["id"], revision.id))
    assert [event.type for event in events] == ["entity_updated"]
    payload = events[0].payload
    assert payload["id"] == mira_id
    # Only personality changed — every other field byte-identical.
    assert payload["before"]["data"]["personality"] == "warm, watchful"
    assert payload["after"]["data"]["personality"] == "edited by the DM's hand"
    assert payload["before"]["name"] == payload["after"]["name"] == "Mira Vane"
    assert payload["before"]["text"] == payload["after"]["text"] is None
    for key in set(before_data) - {"personality"}:
        assert payload["before"]["data"][key] == payload["after"]["data"][key], key
    assert payload["before"]["data"]["ambient_theme"] == {"motif": "copper coins"}

    after = _entity(mine["id"], mira_id)
    assert after.id == mira_id  # ULID stable
    assert after.data["personality"] == "edited by the DM's hand"
    assert after.data["secret"] == before_data["secret"]

    # The previous revision is the undo: restore and compare byte-identical.
    undo(mine["id"], revision.id)
    restored = _entity(mine["id"], mira_id)
    assert restored.id == mira_id
    assert restored.data == before_data
    assert restored.name == "Mira Vane" and restored.text is None
    assert len(_chain(mine["id"])) == 3  # seed + edit + undo


# ---------------------------------------------------------------------------
# BARE_RECORD: build-in factions/places merge unconstrained
# ---------------------------------------------------------------------------


def test_patch_bare_record_unconstrained_and_text_column(client: Any) -> None:
    """BARE_RECORD: a faction/place record whose data fails the AR24
    shape merges unconstrained — partial edits land as-is, nothing is
    fabricated, no 422 loop (owner decision 2026-09-05); ``text``
    updates the column (and null clears it) in the same single
    revision."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, _mira_id = _seed_world(mine["id"])
    head_before = _head(mine["id"])

    response = _patch(
        client,
        mine["id"],
        bar_id,
        json={"text": "A watering hole with sawdust floors.", "location": "Dockside"},
    )
    assert response.status_code == 204
    bar = _entity(mine["id"], bar_id)
    assert bar.text == "A watering hole with sawdust floors."
    assert bar.data == {"location": "Dockside"}  # merged as-is — no fabrication
    chain = _chain(mine["id"])
    assert len(chain) == 2
    assert chain[-1].base_revision == head_before
    with session_scope() as session:
        events = list(revision_events(session, mine["id"], chain[-1].id))
    assert [event.type for event in events] == ["entity_updated"]
    payload = events[0].payload
    assert payload["before"]["data"] == {}
    assert payload["after"]["data"] == {"location": "Dockside"}
    assert payload["before"]["text"] is None
    assert payload["after"]["text"] == "A watering hole with sawdust floors."

    # text null clears the column; data untouched (one more revision).
    response = _patch(client, mine["id"], bar_id, json={"text": None})
    assert response.status_code == 204
    bar = _entity(mine["id"], bar_id)
    assert bar.text is None
    assert bar.data == {"location": "Dockside"}
    assert len(_chain(mine["id"])) == 3


# ---------------------------------------------------------------------------
# EDIT_IDENTITY_NAME: data.name + column sync, blank 422
# ---------------------------------------------------------------------------


def test_patch_identity_name_syncs_column_and_blank_422(client: Any) -> None:
    """EDIT_IDENTITY_NAME: ``data.name`` and the ``Entity.name`` COLUMN
    update in the same transaction (the WorldView header reads the
    column); a blank name is a 422 with zero revisions (the column is
    non-null)."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    chain_before = len(_chain(mine["id"]))

    response = _patch(client, mine["id"], mira_id, json={"name": "Mira Vane the Elder"})
    assert response.status_code == 204
    mira = _entity(mine["id"], mira_id)
    assert mira.name == "Mira Vane the Elder"  # column
    assert mira.data["name"] == "Mira Vane the Elder"  # data key
    assert len(_chain(mine["id"])) == chain_before + 1

    for blank in ("", "   "):
        response = _patch(client, mine["id"], mira_id, json={"name": blank})
        assert response.status_code == 422
        assert "name" in response.json()["message"].lower()
    assert len(_chain(mine["id"])) == chain_before + 1  # zero revisions on 422
    assert _entity(mine["id"], mira_id).name == "Mira Vane the Elder"


# ---------------------------------------------------------------------------
# EDIT_ROLE_BOSS / EDIT_ROLE_UNBOSS (the conditional boss section)
# ---------------------------------------------------------------------------


def test_patch_role_boss_conditions_422_zero_revisions(client: Any) -> None:
    """EDIT_ROLE_BOSS/UNBOSS: on a shape-valid character, promoting the
    role to BBEG without a boss section is a 422 naming the break;
    demoting to NPC while a boss section is present is a 422; removing
    the boss section (null) in the same PATCH succeeds — zero revisions
    on every 422, one revision on the removal."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    boss = dict.fromkeys(BOSS_FIELDS, "details")

    # role -> BBEG without boss: 422 naming the missing section.
    response = _patch(client, mine["id"], mira_id, json={"role": "BBEG"})
    assert response.status_code == 422
    assert "boss" in response.json()["message"].lower()
    assert len(_chain(mine["id"])) == 1  # untouched

    # A valid Monster promotion adds the boss section (one revision).
    response = _patch(client, mine["id"], mira_id, json={"role": "Monster", "boss": boss})
    assert response.status_code == 204
    assert len(_chain(mine["id"])) == 2
    assert _entity(mine["id"], mira_id).data["role"] == "Monster"
    assert _entity(mine["id"], mira_id).data["boss"] == boss

    # role -> NPC leaving boss present: 422 naming the clause.
    response = _patch(client, mine["id"], mira_id, json={"role": "NPC"})
    assert response.status_code == 422
    assert "boss section is only allowed" in response.json()["message"]
    assert len(_chain(mine["id"])) == 2  # zero revisions

    # Removing the boss section (null) in the SAME PATCH succeeds.
    response = _patch(client, mine["id"], mira_id, json={"role": "NPC", "boss": None})
    assert response.status_code == 204
    mira = _entity(mine["id"], mira_id)
    assert mira.data["role"] == "NPC"
    assert "boss" not in mira.data
    assert len(_chain(mine["id"])) == 3


# ---------------------------------------------------------------------------
# EDIT_STATBLOCK: replacement + non-object 422 (AR24-valid only)
# ---------------------------------------------------------------------------


def test_patch_stat_block_replaced_and_non_object_422(client: Any) -> None:
    """EDIT_STATBLOCK: a whole-section replacement lands in one revision;
    a non-object stat_block is a 422 with zero revisions on an
    AR24-valid record (the section renders through StatBlock.vue) but
    merges unconstrained on a bare record (no AR24 contract there)."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    new_block = {"combat": {"ac": 17, "hp": 42}}

    response = _patch(client, mine["id"], mira_id, json={"stat_block": new_block})
    assert response.status_code == 204
    assert _entity(mine["id"], mira_id).data["stat_block"] == new_block
    assert len(_chain(mine["id"])) == 2

    response = _patch(client, mine["id"], mira_id, json={"stat_block": "nope"})
    assert response.status_code == 422
    assert "stat_block" in response.json()["message"].lower()
    assert len(_chain(mine["id"])) == 2  # zero revisions on the 422

    # The bare faction accepts any stat_block value unconstrained.
    response = _patch(
        client, mine["id"], bar_id, json={"stat_block": "bare nope", "text": "ledge notes"}
    )
    assert response.status_code == 204
    bar = _entity(mine["id"], bar_id)
    assert bar.data == {"stat_block": "bare nope"}
    assert bar.text == "ledge notes"


# ---------------------------------------------------------------------------
# CUSTOM_KEY: unknown keys pass through + the regen splice round trip
# ---------------------------------------------------------------------------


def test_patch_custom_key_round_trips_through_regen_splice(client: Any) -> None:
    """CUSTOM_KEY: an unknown key passes through the merge into ``data``
    with exactly one revision; a later regeneration (3-5 splice)
    preserves it — the staged regenerate payload still carries the key
    and ``entity_base_data`` snapshots the same edited record."""
    from app.core.settings import LLMSettings
    from app.pipeline.worker import run_next_job
    from app.store import enqueue_job, list_candidates

    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    response = _patch(client, mine["id"], mira_id, json={"notes": "handwritten in the margins"})
    assert response.status_code == 204
    assert _entity(mine["id"], mira_id).data["notes"] == "handwritten in the margins"
    assert len(_chain(mine["id"])) == 2  # exactly one revision

    settings = LLMSettings(endpoint="http://test/v1", model="test-model")
    job_id = enqueue_job(
        mine["id"],
        "regenerate",
        {"target": {"kind": "entity", "id": mira_id}, "sections": ["personality"]},
    ).id

    def provider(prompt: str, settings: LLMSettings) -> str:
        # The model echoes the CURRENT record (with the DM's key) and
        # re-rolls only the requested section.
        out = json.loads(json.dumps(_entity(mine["id"], mira_id).data))
        out["personality"] = "re-rolled personality"
        return json.dumps({"candidates": [out]})

    assert run_next_job(provider=provider, settings=settings) == job_id
    (row,) = list_candidates(mine["id"])[0]
    assert row.regenerates_entity_id == mira_id
    assert row.payload["personality"] == "re-rolled personality"
    # The unknown key survives the splice into the staged payload…
    assert row.payload["notes"] == "handwritten in the margins"
    assert row.payload["ambient_theme"] == {"motif": "copper coins"}
    # …and the accept-conflict base snapshots the same edited record.
    assert row.entity_base_data is not None
    assert row.entity_base_data["notes"] == "handwritten in the margins"


# ---------------------------------------------------------------------------
# EDGELESS: the orphan rule is create-only — edits commit
# ---------------------------------------------------------------------------


def test_patch_edgeless_entity_commits(client: Any) -> None:
    """EDGELESS: deleting an entity's last live edge leaves it edgeless,
    and a PATCH on it commits normally — the no-orphans rule is
    entity-create-only, never an edit gate."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    with session_scope() as session:
        from app.store import world_edges

        (edge,) = [e for e in world_edges(session, mine["id"]) if e.src == mira_id]
        edge_id = edge.id
    delete_edge(mine["id"], edge_id)
    assert len(_chain(mine["id"])) == 2  # seed + edge delete

    response = _patch(client, mine["id"], mira_id, json={"personality": "solitary now"})
    assert response.status_code == 204
    assert _entity(mine["id"], mira_id).data["personality"] == "solitary now"
    assert len(_chain(mine["id"])) == 3


# ---------------------------------------------------------------------------
# TEXT_TYPE / NOOP_PATCH
# ---------------------------------------------------------------------------


def test_patch_text_type_422_zero_revisions(client: Any) -> None:
    """TEXT_TYPE: ``text`` is a str-or-null wire contract — any other
    type is a 422 with zero revisions, never a driver error."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    for junk in (5, ["x"], {"x": 1}):
        response = _patch(client, mine["id"], mira_id, json={"text": junk})
        assert response.status_code == 422
        assert "text" in response.json()["message"].lower()
    assert len(_chain(mine["id"])) == 1  # zero revisions


def test_patch_noop_zero_revisions(client: Any) -> None:
    """NOOP_PATCH: a value-identical PATCH and an explicit null on an
    absent key are both a 204 with ZERO revisions — idempotent, history
    unpolluted, a legal REST retry."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    head_before = _head(mine["id"])
    personality = _entity(mine["id"], mira_id).data["personality"]

    # Value-identical content patch.
    assert _patch(client, mine["id"], mira_id, json={"personality": personality}).status_code == 204
    assert _head(mine["id"]) == head_before
    assert len(_chain(mine["id"])) == 1
    # Null-deleting an absent key (no boss on the record).
    assert _patch(client, mine["id"], mira_id, json={"boss": None}).status_code == 204
    assert _head(mine["id"]) == head_before
    # Text identical to the current (None) value.
    assert _patch(client, mine["id"], mira_id, json={"text": None}).status_code == 204
    assert _head(mine["id"]) == head_before
    assert len(_chain(mine["id"])) == 1  # zero revisions across all three


def test_patch_nan_infinity_422_zero_revisions(client: Any) -> None:
    """Strict-JSON write boundary (the staging ``allow_nan=False``
    precedent): ``json.loads`` (the PATCH route's body parser) accepts
    ``NaN``/``Infinity`` literals, but a data payload carrying one would
    500 every later world/snapshot read (Starlette refuses to render
    out-of-range floats). The store rejects with a 422 and ZERO
    revisions; world reads stay healthy."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    # httpx refuses to serialize non-finite floats client-side, so the
    # NaN/Infinity bodies go as raw bytes — json.loads (the route's
    # Request-based body parse) accepts them, the store must not.
    for body in (b'{"notes": NaN}', b'{"notes": Infinity}', b'{"notes": -Infinity}'):
        response = _patch(
            client,
            mine["id"],
            mira_id,
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert "strict json" in response.json()["message"].lower()
    assert len(_chain(mine["id"])) == 1  # zero revisions
    # The poisoned payload never landed: the campaign's world still reads.
    assert client.get(f"/api/campaigns/{mine['id']}").status_code == 200


def test_patch_bool_int_flip_is_not_a_noop(client: Any) -> None:
    """The no-op guard compares canonical JSON, not Python ``==``:
    ``1`` and ``True`` serialize differently on the wire (``1`` vs
    ``true``), so flipping a committed ``1`` to ``true`` is a REAL edit
    that must commit — silently swallowing it would drop the DM's change
    while the UI reports it saved (NOOP_PATCH precision)."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    assert _patch(client, mine["id"], mira_id, json={"hit_points": 1}).status_code == 204
    assert len(_chain(mine["id"])) == 2
    assert _patch(client, mine["id"], mira_id, json={"hit_points": True}).status_code == 204
    assert len(_chain(mine["id"])) == 3  # committed, not swallowed as a no-op
    assert _entity(mine["id"], mira_id).data["hit_points"] is True
    # The reverse direction: ``true`` → ``1`` is equally a real edit.
    assert _patch(client, mine["id"], mira_id, json={"hit_points": 1}).status_code == 204
    assert len(_chain(mine["id"])) == 4
    # And a genuinely value-identical retry is still a no-op.
    assert _patch(client, mine["id"], mira_id, json={"hit_points": 1}).status_code == 204
    assert len(_chain(mine["id"])) == 4


# ---------------------------------------------------------------------------
# STALE_BASE / OMITTED_BASE (optimistic concurrency)
# ---------------------------------------------------------------------------


def test_patch_stale_base_409_then_head_succeeds(client: Any) -> None:
    """STALE_BASE: a PATCH carrying a stale ``base_revision`` is a 409
    (rebase-or-reject, AD-2) with zero revisions; repeating it against
    the current head succeeds with exactly one revision."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    seed_head = _head(mine["id"])
    # Advance the head so the captured seed revision goes stale.
    stale_maker_id = ids.new_id()
    commit_subgraph(
        mine["id"],
        [models.EntityInput(kind="place", name="Stale Maker", id=stale_maker_id)],
        [models.EdgeInput(src=stale_maker_id, dst=bar_id, type="located_in")],
        base_revision=seed_head,
    )
    response = _patch(
        client,
        mine["id"],
        mira_id,
        json={"personality": "new", "base_revision": seed_head},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    assert "stale" in response.json()["message"].lower()
    assert len(_chain(mine["id"])) == 2  # seed + advancer — the 409 wrote nothing
    assert _entity(mine["id"], mira_id).data["personality"] == "warm, watchful"  # untouched

    fresh_head = _head(mine["id"])
    assert (
        _patch(
            client,
            mine["id"],
            mira_id,
            json={"personality": "new", "base_revision": fresh_head},
        ).status_code
        == 204
    )
    assert _entity(mine["id"], mira_id).data["personality"] == "new"
    assert len(_chain(mine["id"])) == 3


def test_patch_omitted_base_resolves_to_current_head(client: Any) -> None:
    """OMITTED_BASE: a PATCH without ``base_revision`` resolves to the
    current head INSIDE the store call — the commit's base is that head,
    so the optimistic-concurrency guard still applies to the resolved
    value (STALE_BASE covers the 409 path)."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    seed_head = _head(mine["id"])

    response = _patch(client, mine["id"], mira_id, json={"personality": "no base given"})
    assert response.status_code == 204
    chain = _chain(mine["id"])
    assert len(chain) == 2
    assert chain[-1].base_revision == seed_head


# ---------------------------------------------------------------------------
# Store parity + undo round-trip
# ---------------------------------------------------------------------------


def test_store_update_entity_parity_and_undo_round_trip(client: Any) -> None:
    """Store parity + undo: the wire PATCH and a direct store
    ``update_entity`` call commit through the same ``_commit`` path — one
    ``entity_updated`` revision each with the same event shape; undoing
    the head revision (AD-2's undo-the-latest rule) restores the prior
    record byte-identical on both paths."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    original = _entity(mine["id"], mira_id)
    original_data = dict(original.data)

    response = _patch(client, mine["id"], mira_id, json={"personality": "wire edit"})
    assert response.status_code == 204
    chain = _chain(mine["id"])
    assert len(chain) == 2
    wire_revision = chain[-1]
    with session_scope() as session:
        events = list(revision_events(session, mine["id"], wire_revision.id))
    assert [event.type for event in events] == ["entity_updated"]

    # Undo the wire edit (it is the head): record restored byte-identical.
    undo(mine["id"], wire_revision.id)
    restored = _entity(mine["id"], mira_id)
    assert restored.id == mira_id
    assert restored.data == original_data
    assert restored.name == "Mira Vane" and restored.text is None

    # Store-level parity: same merge + commit semantics, one revision.
    from app.store import update_entity as store_update

    store_revision = store_update(mine["id"], mira_id, patch={"secret": "store parity"})
    chain = _chain(mine["id"])
    assert len(chain) == 4  # seed + wire + undo + store
    assert chain[-1].id == store_revision.id
    with session_scope() as session:
        events = list(revision_events(session, mine["id"], store_revision.id))
    assert [event.type for event in events] == ["entity_updated"]
    assert events[0].payload["id"] == mira_id
    edited = _entity(mine["id"], mira_id)
    assert edited.data["personality"] == "warm, watchful"  # seed value still present
    assert edited.data["secret"] == "store parity"

    # The store revision's own undo round-trip restores again.
    undo(mine["id"], store_revision.id)
    restored = _entity(mine["id"], mira_id)
    assert restored.data == original_data
    assert len(_chain(mine["id"])) == 5  # seed + wire + undo + store + undo


# ---------------------------------------------------------------------------
# Review-round-2 pins: reserved keys, non-dict patch, no-op stale base,
# stat_block null deletion, wire-level accept-conflict contract.
# ---------------------------------------------------------------------------


def test_patch_reserved_keys_rejected_zero_revisions(client: Any) -> None:
    """Review round 2: ``kind`` and ``edges`` are behavior-bearing
    reserved names — a data-level ``kind`` would shadow the identity
    column and a data-level ``edges`` would be silently replaced by
    regeneration staging. 422 with zero revisions (never unknown keys)."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    before = _revision_count(mine["id"])
    for reserved in ("kind", "edges"):
        response = _patch(
            client, mine["id"], mira_id, json={reserved: "place" if reserved == "kind" else []}
        )
        assert response.status_code == 422
        assert reserved in response.json()["message"]
    assert _revision_count(mine["id"]) == before


def test_store_update_entity_non_dict_patch_rejected(client: Any) -> None:
    """Review round 2: every store boundary guards structured input — a
    non-dict patch is a structured ``InvalidEntityRecordError`` (422),
    never an AttributeError -> 500."""
    from app.store import InvalidEntityRecordError, update_entity

    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    with pytest.raises(InvalidEntityRecordError):
        update_entity(mine["id"], mira_id, patch=["not", "a", "dict"])  # type: ignore[arg-type]


def test_patch_noop_with_explicit_stale_base_409(client: Any) -> None:
    """Review round 2: ``_check_base`` is unconditional when a caller
    names a base — a value-identical PATCH carrying an old
    ``base_revision`` is a 409 (rebase-or-reject), even though nothing
    would be written (the frozen Always bullet's edge/delete
    precedent)."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    stale = _head(mine["id"])
    # Move the head.
    response = _patch(client, mine["id"], mira_id, json={"personality": "rewritten"})
    assert response.status_code == 204
    # A no-op PATCH against the stale base must still 409.
    response = _patch(
        client, mine["id"], mira_id, json={"personality": "rewritten", "base_revision": stale}
    )
    assert response.status_code == 409
    assert "stale base revision" in response.json()["message"]
    assert _revision_count(mine["id"]) == 2  # zero new revisions


def test_patch_stat_block_null_is_explicit_deletion(client: Any) -> None:
    """Review round 2 pin: ``stat_block: null`` is the DELETE semantic
    (matching every other null-deletes-key merge — the EDIT_ROLE_UNBOSS
    boss removal); only a non-null non-object is a shape break. The
    shape contract does not require a stat block to be present, so the
    explicit removal commits one revision."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    assert "stat_block" in _entity(mine["id"], mira_id).data
    response = _patch(client, mine["id"], mira_id, json={"stat_block": None})
    assert response.status_code == 204
    after = _entity(mine["id"], mira_id).data
    assert "stat_block" not in after
    assert _revision_count(mine["id"]) == 2


def _stage_regen_entity_candidate(campaign_id: str, mira_id: str) -> models.ProposedCandidate:
    """Enqueue a regenerate-entity job and stage one row against the
    current committed record (store-level; the wire tests below exercise
    the accept HTTP contract)."""
    job = enqueue_job(
        campaign_id,
        "regenerate",
        {"target": {"kind": "entity", "id": mira_id}, "sections": None},
        job_id=ids.new_id(),
    )
    staged = stage_candidates(
        campaign_id,
        job.id,
        [_ar24_record()],
        target_entity_id=mira_id,
    )
    assert len(staged) == 1
    return staged[0]


def test_wire_accept_conflict_409_envelope_and_message(client: Any) -> None:
    """Review round 2: the accept-conflict 409 wire contract — the HTTP
    envelope carries code 'conflict' and the exact message substring the
    frontend dialog gates on; the row stays proposed; zero revisions."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    row = _stage_regen_entity_candidate(mine["id"], mira_id)
    # The candidate list exposes the re-roll target over the wire.
    page = client.get(f"/api/campaigns/{mine['id']}/candidates").json()
    assert page["candidates"][0]["regenerates_entity_id"] == mira_id
    # DM hand edit lands on the target after staging.
    response = _patch(client, mine["id"], mira_id, json={"personality": "hand-rewritten"})
    assert response.status_code == 204
    before = _revision_count(mine["id"])
    response = client.post(f"/api/campaigns/{mine['id']}/candidates/{row.id}/accept")
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "conflict"
    assert "changed since this candidate was generated" in body["message"]
    assert _revision_count(mine["id"]) == before  # zero revisions
    # The row is still proposed (audit trail intact).
    still = client.get(f"/api/campaigns/{mine['id']}/candidates").json()
    assert still["candidates"][0]["status"] == "proposed"


def test_wire_accept_confirm_overwrite_commits_one_revision(client: Any) -> None:
    """Review round 2: the legal ``{"confirm_overwrite": true}``-only
    body — the three-way escape's second step — commits the in-place
    accept as one revision and settles the row."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    row = _stage_regen_entity_candidate(mine["id"], mira_id)
    response = _patch(client, mine["id"], mira_id, json={"personality": "hand-rewritten"})
    assert response.status_code == 204
    before = _revision_count(mine["id"])
    response = client.post(
        f"/api/campaigns/{mine['id']}/candidates/{row.id}/accept",
        json={"confirm_overwrite": True},
    )
    assert response.status_code == 200, response.text
    accepted = response.json()
    assert accepted["status"] == "accepted"
    assert accepted["accepted_entity_id"] == mira_id
    assert _revision_count(mine["id"]) == before + 1  # exactly one revision


def test_conflict_error_message_contract_matches_frontend_marker(client: Any) -> None:
    """Review round 2: the backend's EntityEditConflictError message
    contains the EXACT substring the frontend dialog gates on
    (CandidatesView EDIT_CONFLICT_MARKER) — a copy-drift-safe contract
    pin for the message-based trigger."""
    err = EntityEditConflictError("E1")
    assert "changed since this candidate was generated" in str(err)


def test_staged_base_is_the_spliced_record(client: Any) -> None:
    """Review round 2 pin: the accept-conflict guard compares against
    the EXACT record staged via ``target_base_data`` (the runner's
    splice source) — a base older than the current committed record
    fails closed at accept, exactly as the runner's single-transaction
    splice would after a mid-staging edit."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    job = enqueue_job(
        mine["id"],
        "regenerate",
        {"target": {"kind": "entity", "id": mira_id}, "sections": None},
        job_id=ids.new_id(),
    )
    # Splice source: the record as it stood BEFORE the hand edit.
    from app.store.candidates import _stage_candidates

    stale_base = _entity(mine["id"], mira_id).data
    with session_scope() as session:
        rows = _stage_candidates(
            session,
            mine["id"],
            job.id,
            [_ar24_record()],
            target_entity_id=mira_id,
            target_base_data=dict(stale_base),
        )
    # Hand edit after staging.
    response = _patch(client, mine["id"], mira_id, json={"personality": "edited after staging"})
    assert response.status_code == 204
    # Accept without confirm -> 409 (the guard uses the spliced base, not
    # the current record).
    before = _revision_count(mine["id"])
    response = client.post(f"/api/campaigns/{mine['id']}/candidates/{rows[0].id}/accept")
    assert response.status_code == 409
    assert _revision_count(mine["id"]) == before
