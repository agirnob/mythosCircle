"""The fully-authored character path (spec: hybrid authorship, path 2).

Pins the three-layer gate's API half and the zero-LLM runner:

- the owner-mandated ACCEPTANCE rows first: a valid, an invalid, and an
  incomplete fully-authored character each prove ZERO LLM invocations
  (the counting provider raises if ever called) — only the valid one
  commits;
- the 202/422 semantics of the synchronous enqueue gate (zero job rows
  on any violation);
- the run-time backstop: STRUCTURAL_VALIDATION_FAILURE on world drift
  (F5), byte-identical commit (no conform/stamp/fold — ``power``
  untouched, DIRECT_OVER_POWERED commits), fresh ULIDs (F4 —
  DIRECT_SAME_NAME), and the declared-edge tiers (1/2) with kind and
  duplicate rules.

Deterministic — the provider is injected and asserted silent; the
add_character runner takes NO provider parameter by construction.
"""

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.settings import LLMSettings
from app.pipeline.worker import run_next_job
from app.store import (
    app_db_url,
    claim_next_job,
    create_campaign,
    enqueue_job,
    init_db,
    job_status,
    models,
)
from app.store.db import session_scope
from app.store.direct import validate_add_character_payload
from app.store.read import latest_revision, world_state

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _owner_id() -> str:
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-direct-{new_id()}@example.com", "password123").id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'direct.db'}")
    try:
        yield create_campaign(
            _owner_id(),
            title="Direct Character World",
            description="",
            theme="High Fantasy",
            custom_lore="",
        ).id
    finally:
        init_db(previous)


@pytest.fixture(autouse=True)
def _reset_limiters() -> None:
    """Register-quota isolation (the edges/API convention): the register
    limiter is module-global and keyed on the TestClient's fixed host —
    _authed_campaign registers per test (incl. the second-account
    ownership pin, spec-6-4)."""
    from app.api.auth import _login_limiter, _register_limiter

    _login_limiter.reset()
    _register_limiter.reset()
    yield
    _login_limiter.reset()
    _register_limiter.reset()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    from app.main import create_app

    application = create_app()
    with TestClient(application) as test_client:
        yield test_client


def _authed_campaign(client: TestClient) -> str:
    """Register via the API (sets the session cookie) and create a
    campaign owned by that account."""
    from app.core import ids

    email = f"direct-{ids.new_id()}@example.com"
    body = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert body.status_code == 201
    return create_campaign(
        body.json()["id"],
        title="Direct API World",
        description="",
        theme="High Fantasy",
        custom_lore="",
    ).id


# ---------------------------------------------------------------------------
# Fixtures: the canonical sheet
# ---------------------------------------------------------------------------


def _stat_block(**overrides: object) -> dict:
    """A valid canonical NPC block (the direct template form: skill text
    blocks, dice-string damage)."""
    block: dict = {
        "identity": {
            "role": "NPC",
            "race": "Human",
            "level": 5,
            "class": "Paladin",
            "alignment": "LG",
        },
        "attributes": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 13, "cha": 15},
        "combat": {"ac": 18, "hp": 44, "hit_dice": "5d10 + 10"},
        "skills": [{"name": "Athletics", "description": "Competent climber and wrestler"}],
        "actions": [
            {
                "name": "Longsword",
                "description": "Melee Weapon Attack: +5 to hit. Hit: 7 (1d8+2) slashing damage.",
                "damage": "1d8+2",
            }
        ],
    }
    block.update(overrides)
    return block


def _record(name: str = "Seraphine", **overrides: object) -> dict:
    """One complete AR24 record with the canonical stat block."""
    record: dict = {
        "name": name,
        "role": "NPC",
        "level_cr": "level 5",
        "race_type": "Human",
        "class_profession": "Paladin",
        "alignment": "LG",
        "personality": "Warm, patient, relentless about promises.",
        "secret": "She buried her oath-ring under the chapel floor.",
        "rumor": "They say her blessing once stopped a bleeding wall.",
        "party_hook": "She hires the party to escort a tithe wagon.",
        "appearance": "Silver-streaked braid, sunburnt hands, dented cuirass.",
        "background": "Raised in the chapel-walls of Gallorb's poorest parish.",
        "goals": "Rebuild the parish granary before the frost.",
        "relationships": "Widowed; her brother captains the river watch.",
        "voice_style": "Measured, homely metaphors, never swears.",
        "catchphrases": '"Bread first. Blades after."',
        "world_integration": {
            "reputation": "The granary saint of the low quarter.",
            "factions": "Loosely bound to the Order of the Sun.",
            "current_location": "Gallorb, the Low Parish.",
            "reaction_matrix": "Asks after the party's wounds before their names.",
            "on_defeat": "The parish scatters; the tithe rots unguarded.",
        },
        "stat_block": _stat_block(),
    }
    record.update(overrides)
    return record


def _sheet(
    record: dict | None = None,
    *,
    key: str | None = None,
    relations: list | None = None,
) -> dict:
    sheet: dict = {"record": record if record is not None else _record()}
    if key is not None:
        sheet["key"] = key
    if relations is not None:
        sheet["relations"] = relations
    return sheet


def _payload(world_id: str, characters: list) -> dict:
    return {"campaign_id": world_id, "characters": characters}


def _head_id(world_id: str) -> str | None:
    with session_scope() as session:
        head = latest_revision(session, world_id)
        return head.id if head is not None else None


def _committed_entity(world_id: str, kind: str = "place") -> str:
    """One committed entity for Tier-1 target pins."""
    from app.store import commit_subgraph

    commit_subgraph(
        world_id,
        entities=[models.EntityInput(kind=kind, name="Gallorb", text=None, data={})],
        base_revision=_head_id(world_id),
        allow_orphans=True,
    )
    with session_scope() as session:
        rows = session.scalars(
            select(models.Entity).where(models.Entity.campaign_id == world_id)
        ).all()
        return rows[-1].id


# ---------------------------------------------------------------------------
# The owner-mandated zero-LLM acceptance (spec task 3, non-negotiable)
# ---------------------------------------------------------------------------


def _counting_provider(calls: list) -> Callable[..., str]:
    """A provider that RECORDS every invocation and raises — if the
    direct path ever calls it, the acceptance test fails loudly."""

    def _provider(prompt: str, settings: object = None) -> str:
        calls.append(prompt)
        raise AssertionError("the fully-authored path invoked the LLM provider")

    return _provider


def test_zero_llm_acceptance_valid_invalid_incomplete(world: str) -> None:
    """ACCEPTANCE (owner mandate): a valid, an invalid, and an incomplete
    fully-authored character EACH prove ZERO LLM invocations — counting
    provider raises if called — and only the valid one commits."""
    calls: list = []
    provider = _counting_provider(calls)

    valid_job = enqueue_job(world, "add_character", {"characters": [_sheet()]})
    from app.store import InvalidJobInputError

    with pytest.raises(InvalidJobInputError):
        enqueue_job(
            world,
            "add_character",
            {
                "characters": [
                    _sheet(
                        _record(
                            "Broken",
                            stat_block=_stat_block(
                                actions=[
                                    {
                                        "name": "Swipe",
                                        "description": "A swipe.",
                                        "damage": "1d6x+2",
                                    },
                                ]
                            ),
                        )
                    )
                ]
            },
        )
    with pytest.raises(InvalidJobInputError):
        enqueue_job(
            world,
            "add_character",
            {"characters": [_sheet(_record("Hollow", personality=""))]},
        )

    # The valid job runs to completion with the provider asserted silent.
    assert run_next_job(provider=provider, settings=SETTINGS) == valid_job.id
    job, _position = job_status(valid_job.id)
    assert job.state == "succeeded"
    assert calls == []

    # Only the valid character committed: exactly one character entity,
    # byte-identical to the submission.
    entities, _edges = _world(world)
    characters = [entity for entity in entities if entity.kind == "character"]
    assert len(characters) == 1
    assert characters[0].data == _record()


def test_zero_llm_acceptance_via_worker_dispatch(world: str) -> None:
    """The same mandate through the real dispatch: run_next_job's
    add_character arm receives NO provider — the counting provider rides
    along unused, the journal writes no file, and the result reports
    ``llm_calls: {}``."""
    calls: list = []
    provider = _counting_provider(calls)
    job = enqueue_job(world, "add_character", {"characters": [_sheet(key="a")]})
    assert job.max_llm_calls == 0
    assert run_next_job(provider=provider, settings=SETTINGS) == job.id
    job, _position = job_status(job.id)
    assert job.state == "succeeded"
    assert job.result is not None
    assert job.result["llm_calls"] == {}
    assert calls == []


def test_cancelled_claimed_job_cannot_commit_characters(world: str) -> None:
    from app.pipeline.direct import run_add_character
    from app.store import JobStateConflictError, cancel_job

    queued = enqueue_job(world, "add_character", {"characters": [_sheet()]})
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == queued.id
    cancel_job(claimed.id)
    assert claimed.state == "running"  # the detached worker snapshot is stale
    with pytest.raises(JobStateConflictError):
        run_add_character(claimed)
    with session_scope() as session:
        assert world_state(session, world) == ([], [])
        assert latest_revision(session, world) is None
    stored, _ = job_status(queued.id)
    assert stored.state == "cancelled"


# ---------------------------------------------------------------------------
# The synchronous enqueue gate (202 / 422 semantics, zero job rows)
# ---------------------------------------------------------------------------


def test_gate_valid_enqueues_202_with_zero_budget(client: TestClient) -> None:
    world_id = _authed_campaign(client)
    body = client.post("/api/characters", json=_payload(world_id, [_sheet()]))
    assert body.status_code == 202
    data = body.json()
    assert ULID_RE.fullmatch(data["job_id"])
    assert data["max_llm_calls"] == 0
    stored, _position = job_status(data["job_id"])
    assert stored.kind == "add_character"
    assert stored.state == "queued"
    assert stored.max_llm_calls == 0
    assert stored.max_media_calls == 0
    # The conftest client fixture shares one DB across the module: drain
    # the queue so no leftover job leaks into another module's
    # run_next_job claim (queue is a global FIFO, AD-3).
    from app.pipeline.direct import STRUCTURAL_VALIDATION_FAILURE  # noqa: F401
    from app.store import cancel_job

    while (claimed := claim_next_job()) is not None:
        cancel_job(claimed.id)


def test_gate_incomplete_422_zero_rows(client: TestClient) -> None:
    world_id = _authed_campaign(client)
    body = client.post(
        "/api/characters", json=_payload(world_id, [_sheet(_record("Hollow", secret=""))])
    )
    assert body.status_code == 422
    violations = body.json()["details"]["violations"]
    assert any("secret" in violation for violation in violations)
    assert _job_count(world_id) == 0


def test_gate_invalid_stat_422_names_schema_path(client: TestClient) -> None:
    world_id = _authed_campaign(client)
    record = _record(
        "Dicebreaker",
        stat_block=_stat_block(
            actions=[{"name": "Swipe", "description": "A swipe.", "damage": "1d6x+2"}]
        ),
    )
    body = client.post("/api/characters", json=_payload(world_id, [_sheet(record)]))
    assert body.status_code == 422
    violations = body.json()["details"]["violations"]
    assert any("actions[0].damage" in violation for violation in violations)
    assert _job_count(world_id) == 0


def test_gate_unknown_key_422(client: TestClient) -> None:
    world_id = _authed_campaign(client)
    record = _record("Keyed", equipment="a borrowed mule")
    body = client.post("/api/characters", json=_payload(world_id, [_sheet(record)]))
    assert body.status_code == 422
    violations = body.json()["details"]["violations"]
    assert any("unknown key" in violation and "equipment" in violation for violation in violations)
    assert _job_count(world_id) == 0


def test_gate_target_name_banned_422(client: TestClient) -> None:
    world_id = _authed_campaign(client)
    sheet = _sheet(relations=[{"type": "protects", "target_name": "The Shadow Queen"}])
    body = client.post("/api/characters", json=_payload(world_id, [sheet]))
    assert body.status_code == 422
    violations = body.json()["details"]["violations"]
    assert any("target_name" in violation for violation in violations)
    assert _job_count(world_id) == 0


def test_gate_relation_malformed_422(client: TestClient) -> None:
    world_id = _authed_campaign(client)
    sheet = _sheet(relations=[{"type": "protects", "counter": "many", "target_id": "01H8X"}])
    body = client.post("/api/characters", json=_payload(world_id, [sheet]))
    assert body.status_code == 422
    assert _job_count(world_id) == 0


def test_gate_target_id_unknown_entity_422(client: TestClient) -> None:
    world_id = _authed_campaign(client)
    from app.core import ids

    sheet = _sheet(relations=[{"type": "located_in", "target_id": ids.new_id()}])
    body = client.post("/api/characters", json=_payload(world_id, [sheet]))
    assert body.status_code == 422
    violations = body.json()["details"]["violations"]
    assert any("names no committed entity" in violation for violation in violations)
    assert _job_count(world_id) == 0


def test_gate_target_id_kind_breach_422(client: TestClient) -> None:
    world_id = _authed_campaign(client)
    entity_id = _committed_entity(world_id, kind="character")
    sheet = _sheet(relations=[{"type": "located_in", "target_id": entity_id}])
    body = client.post("/api/characters", json=_payload(world_id, [sheet]))
    assert body.status_code == 422
    violations = body.json()["details"]["violations"]
    assert any("located_in" in violation for violation in violations)
    assert _job_count(world_id) == 0


def test_gate_target_key_must_name_sibling_sheet(client: TestClient) -> None:
    world_id = _authed_campaign(client)
    orphan = _sheet(relations=[{"type": "protects", "target_key": "ghost"}])
    body = client.post("/api/characters", json=_payload(world_id, [orphan]))
    assert body.status_code == 422
    self_ref = _sheet(key="me", relations=[{"type": "protects", "target_key": "me"}])
    body = client.post("/api/characters", json=_payload(world_id, [self_ref]))
    assert body.status_code == 422
    assert _job_count(world_id) == 0


def test_gate_requires_ownership(client: TestClient) -> None:
    """Ownership first: another DM's (or a fabricated) campaign is a
    single indistinguishable 404 — never a payload verdict."""
    first_campaign = _authed_campaign(client)
    _authed_campaign(client)  # a REAL second account takes the session over

    foreign = client.post(
        "/api/characters",
        json=_payload(first_campaign, [_sheet()]),
    )
    fabricated = client.post(
        "/api/characters",
        json=_payload("1" * 26, [_sheet()]),
    )
    assert foreign.status_code == 404
    assert foreign.json()["code"] == "not_found"
    # CHARACTERS_FOREIGN (spec-6-4): byte-identical bodies — a stranger
    # cannot tell the campaign exists (no oracle).
    assert foreign.json() == fabricated.json()


def _world(world_id: str) -> tuple[list, list]:
    with session_scope() as session:
        entities, edges = world_state(session, world_id)
        return list(entities), list(edges)


def _job_count(world_id: str) -> int:
    from app.store.db import session_scope as _scope

    with _scope() as session:
        return len(
            session.scalars(select(models.Job).where(models.Job.campaign_id == world_id)).all()
        )


def test_runner_valid_commits_byte_identical_with_edge(world: str) -> None:
    """DIRECT_VALID + DIRECT_RELATION_OK: one revision, fresh ULID, the
    record byte-identical (no stamp — ``power`` untouched), the declared
    Tier-1 edge applied deterministically in the same transaction."""
    target_id = _committed_entity(world)
    job = enqueue_job(
        world,
        "add_character",
        {
            "characters": [
                _sheet(relations=[{"type": "located_in", "target_id": target_id, "counter": 2}])
            ]
        },
    )
    assert run_next_job(provider=_counting_provider([]), settings=SETTINGS) == job.id
    stored, _position = job_status(job.id)
    assert stored.state == "succeeded"
    entities, edges = _world(world)
    character = next(entity for entity in entities if entity.kind == "character")
    assert character.data == _record()  # byte-identical, power never stamped
    assert character.data.get("stat_block", {}).get("power") is None
    assert [(edge.src, edge.dst, edge.type, edge.counter) for edge in edges] == [
        (character.id, target_id, "located_in", 2)
    ]
    assert stored.result["entity_ids"] == [character.id]
    assert stored.result["revision_id"]


def test_runner_same_name_commits_fresh_ulid(world: str) -> None:
    """DIRECT_SAME_NAME (F4): a second 'Seraphine' commits a SECOND
    distinct ULID — the old character is untouched, no overwrite."""
    for _round in range(2):
        job = enqueue_job(world, "add_character", {"characters": [_sheet()]})
        assert run_next_job(provider=_counting_provider([]), settings=SETTINGS) == job.id
        assert job_status(job.id)[0].state == "succeeded"
    entities, _edges = _world(world)
    characters = [entity for entity in entities if entity.kind == "character"]
    assert len(characters) == 2
    assert len({character.id for character in characters}) == 2
    assert all(character.data == _record() for character in characters)


def test_runner_world_moved_fails_named_zero_commits(world: str) -> None:
    """DIRECT_WORLD_MOVED (F5): the Tier-1 target is deleted between
    enqueue and run — job_failed with the STRUCTURAL_VALIDATION_FAILURE
    code, zero LLM, zero commits (the character never lands)."""
    from app.store import delete_entity

    target_id = _committed_entity(world)
    job = enqueue_job(
        world,
        "add_character",
        {"characters": [_sheet(relations=[{"type": "located_in", "target_id": target_id}])]},
    )
    delete_entity(world, target_id)
    assert run_next_job(provider=_counting_provider([]), settings=SETTINGS) == job.id
    stored, _position = job_status(job.id)
    assert stored.state == "failed"
    assert stored.error is not None
    assert stored.error.startswith("STRUCTURAL_VALIDATION_FAILURE:")
    entities, _edges = _world(world)
    assert not [entity for entity in entities if entity.kind == "character"]


def test_runner_over_powered_commits_unstamped(world: str) -> None:
    """DIRECT_OVER_POWERED (verdict 2026-09-12): an over-band Monster
    commits byte-identical, no stamp, no verdict rejection."""
    record = _record(
        "The Emberjaw",
        role="Monster",
        level_cr="CR 7",
        boss={
            "lair_actions": "None.",
            "legendary_actions": "Three: bite, wing buffets, roar.",
            "immunities": "fire",
            "vulnerabilities": "None.",
        },
        stat_block=_stat_block(
            identity={
                "role": "Monster",
                "race": "Emberwyrm",
                "cr": 3,
                "alignment": "CE",
            },
            combat={"ac": 17, "hp": 108, "hit_dice": "13d10 + 39"},
            actions=[
                {
                    "name": "Gore",
                    "description": "Melee Weapon Attack: +9 to hit. "
                    "Hit: 90 (12d10 + 24) piercing damage.",
                    "damage": "12d10+24",
                }
            ],
        ),
    )
    job = enqueue_job(world, "add_character", {"characters": [_sheet(record, key="m")]})
    assert run_next_job(provider=_counting_provider([]), settings=SETTINGS) == job.id
    stored, _position = job_status(job.id)
    assert stored.state == "succeeded"
    entities, _edges = _world(world)
    monster = next(entity for entity in entities if entity.kind == "character")
    assert monster.data == record
    assert monster.data["stat_block"].get("power") is None


def test_runner_tier2_pair_commits_atomically(world: str) -> None:
    """DIRECT_RELATION_OK via Tier 2: two sheets, the edge wires the two
    fresh ULIDs atomically in the one revision."""
    knight = _sheet(key="knight")
    squire = _sheet(
        record=_record("Perrin"),
        key="squire",
        relations=[{"type": "protects", "target_key": "knight", "counter": 3}],
    )
    job = enqueue_job(world, "add_character", {"characters": [knight, squire]})
    assert run_next_job(provider=_counting_provider([]), settings=SETTINGS) == job.id
    stored, _position = job_status(job.id)
    assert stored.state == "succeeded"
    entities, edges = _world(world)
    by_name = {entity.name: entity for entity in entities if entity.kind == "character"}
    assert set(by_name) == {"Seraphine", "Perrin"}
    assert [(edge.src, edge.dst, edge.type, edge.counter) for edge in edges] == [
        (by_name["Perrin"].id, by_name["Seraphine"].id, "protects", 3)
    ]


def test_runner_declared_duplicate_collapses(world: str) -> None:
    """Two identical declared rows collapse to one — the store's
    duplicate-relationship backstop never crashes the run."""
    target_id = _committed_entity(world)
    relation = {"type": "located_in", "target_id": target_id}
    job = enqueue_job(
        world, "add_character", {"characters": [_sheet(relations=[relation, dict(relation)])]}
    )
    assert run_next_job(provider=_counting_provider([]), settings=SETTINGS) == job.id
    stored, _position = job_status(job.id)
    assert stored.state == "succeeded"
    _entities, edges = _world(world)
    assert len([edge for edge in edges if edge.type == "located_in"]) == 1


def test_canonical_schema_rejects_unknown_top_level_keys(world: str) -> None:
    violations = validate_add_character_payload({"characters": [_sheet()], "notes": "x"})
    assert violations and "unknown key" in violations[0]
    violations = validate_add_character_payload("not an object")
    assert violations == ["add_character payload must be a JSON object"]
    violations = validate_add_character_payload({"characters": []})
    assert violations and "non-empty" in violations[0]


def test_gate_dial_is_an_authorable_record_key(client: TestClient) -> None:
    """AD-36: ``dial`` is the +1 authorable record key — an authored
    record carrying the elaboration level passes the closed-key gate;
    anything else is still DIRECT_UNKNOWN (the sibling pin above)."""
    world_id = _authed_campaign(client)
    record = _record("Dialed", dial="pillar")
    body = client.post("/api/characters", json=_payload(world_id, [_sheet(record)]))
    assert body.status_code == 202, body.text
    assert not any("unknown key" in v for v in body.json().get("details", {}).get("violations", []))
    # The conftest client fixture shares one DB across the module: drain
    # the queue so no leftover job leaks into another module's
    # run_next_job claim (the valid-enqueue precedent).
    from app.store import cancel_job, claim_next_job

    while (claimed := claim_next_job()) is not None:
        cancel_job(claimed.id)
