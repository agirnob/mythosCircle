"""The hybrid authorship path (spec: hybrid authorship, path 1).

Pins tasks 4-7 of the spec's I/O matrix:

- STRING_LEGACY semantics (covered exhaustively by
  ``test_build_in_pipeline.py``; here one mixed-payload sanity row);
- AUTHORED_DESCRIPTION / AUTHORED_RECORD: authored fields are ground
  truth — model drift is REVERTED by the backfill, never re-repaired;
- ROLE_PIN (identity.role vs the pinned role, reject-only);
- HYBRID_AUTHORED_BLOCK_VALID (byte-identical, ZERO repair calls) /
  _INVALID (fail naming entry + violations BEFORE any repair call, zero
  commits — the provider call count proves the repair was never offered);
- the MANDATE: unseeded/uncommitted ``target_name`` joins the roster,
  ONE bounded re-emit on the ASSEMBLED roster, second miss -> zero
  commits;
- the THREE-TIER declared-edge contract: Tier-1 ULID binding, Tier-2
  staged-key atomic pair, Tier-3 exact-one name match / ambiguous
  rejection;
- F4: SAME_NAME_SAME_BUILD (two same-name seeds -> two fresh ULIDs) and
  STRUCTURED_REBUILD (place delta merge on the same row).

Deterministic — the provider is a pure function of the prompt.
"""

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from app.core.settings import LLMSettings
from app.pipeline.worker import run_next_job
from app.store import (
    app_db_url,
    create_campaign,
    enqueue_job,
    init_db,
    job_status,
    models,
)
from app.store.db import session_scope
from app.store.read import latest_revision, world_entities, world_state

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")


def _owner_id() -> str:
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-hybrid-{new_id()}@example.com", "password123").id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'hybrid.db'}")
    try:
        yield create_campaign(
            _owner_id(),
            title="Hybrid World",
            description="",
            theme="High Fantasy",
            custom_lore="",
        ).id
    finally:
        init_db(previous)


def _enqueue(world_id: str, max_llm_calls: int | None = None, **payload: Any) -> str:
    return enqueue_job(world_id, "build_in", payload, max_llm_calls=max_llm_calls).id


# ---------------------------------------------------------------------------
# Fixtures: records and the fake model
# ---------------------------------------------------------------------------

_STAT_BLOCK: dict[str, Any] = {
    "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter", "alignment": "LG"},
    "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
    "combat": {"ac": 16, "hp": 66},
    "skills": [{"name": "Athletics", "bonus": 5}],
    "actions": [
        {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 4d10+5 slashing"}
    ],
}


def _record(name: str, role: str = "NPC", **stat_overrides: Any) -> dict[str, Any]:
    block = json.loads(json.dumps(_STAT_BLOCK))
    block.update(stat_overrides)
    record = {
        "name": name,
        "role": role,
        "level_cr": "level 5" if role != "Monster" else "CR 5",
        "race_type": "Human",
        "class_profession": "Fighter",
        "alignment": "LG",
        "personality": "Generated personality.",
        "secret": "Generated secret.",
        "rumor": "Generated rumor.",
        "party_hook": "Generated hook.",
        "appearance": "Generated appearance.",
        "background": "Generated background.",
        "goals": "Generated goals.",
        "relationships": "Generated relationships.",
        "voice_style": "Generated voice.",
        "catchphrases": "Generated catchphrases.",
        "world_integration": {
            "reputation": "Generated reputation.",
            "factions": "Generated factions.",
            "current_location": "Generated location.",
            "reaction_matrix": "C0: watches.",
            "on_defeat": "Generated defeat.",
        },
        "stat_block": block,
    }
    if role in ("BBEG", "Monster"):
        record["boss"] = {
            "lair_actions": "None.",
            "legendary_actions": "One per round.",
            "immunities": "None.",
            "vulnerabilities": "None.",
        }
    return record


_SECTION_HEADER = re.compile(r"^(places|factions|key_figures) \(\d+\):")
_ENTRY_LINE = re.compile(r"^- (.+)$")
_MANDATE_LINE = re.compile(r"^- E(\d+) \(mandate\): (.+?) — kind: (\w+)$")


def _parse_roster(prompt: str) -> list[tuple[str, str, str | None]]:
    """The roster the single-call prompt names, in order: ``(section,
    entry, mandate_kind)`` — mandate rows carry their demanded kind."""
    rows: list[tuple[str, str, str | None]] = []
    section: str | None = None
    in_sections = False
    for line in prompt.splitlines():
        if line == "SUBMITTED SECTIONS":
            in_sections = True
            continue
        if not in_sections:
            continue
        # The sections block ends at the notes/task blocks; the mandate
        # demand block (any "- E<n> (mandate): ..." line) is parsed
        # wherever it appears — the fake is obedient to every demand.
        if line == "TASK" or line.startswith("DM NOTES"):
            in_sections = False
            continue
        mandate = _MANDATE_LINE.match(line)
        if mandate is not None:
            rows.append(("mandate", mandate.group(2), mandate.group(3)))
            continue
        header = _SECTION_HEADER.match(line)
        if header is not None:
            section = header.group(1)
            continue
        entry = _ENTRY_LINE.match(line)
        if entry is not None and section is not None:
            rows.append((section, entry.group(1), None))
    return rows


def _fake_wave1(
    prompt: str,
    *,
    role_for: Callable[[str], str] | None = None,
    block_role_for: Callable[[str], str] | None = None,
    skip_mandate: bool = False,
) -> dict[str, Any]:
    """A pure fake wave-1 response: one entity per roster line, in order —
    places/factions flat, characters with a full (generated) record. The
    model DELIBERATELY drifts every authored value (the backfill must
    revert it) and honors no pin unless ``role_for`` says otherwise."""
    entities: list[dict[str, Any]] = []
    position = 0
    for section, entry, mandate_kind in _parse_roster(prompt):
        if skip_mandate and section == "mandate":
            continue  # the model "forgot" the demand — the mandate must catch it
        if section == "mandate":
            entities.append(
                {
                    "ref": f"E{position}",
                    "kind": mandate_kind or "place",
                    "name": entry,
                    "text": "generated",
                }
            )
            position += 1
            continue
        if section in ("places", "factions"):
            entities.append(
                {
                    "ref": f"E{position}",
                    "kind": "place" if section == "places" else "faction",
                    "name": entry,
                    "text": "DRIFTED description",
                }
            )
            position += 1
            continue
        role = role_for(entry) if role_for else "NPC"
        data = _record(entry, role=role)
        block_role = block_role_for(entry) if block_role_for else role
        data["stat_block"]["identity"]["role"] = block_role
        entities.append(
            {
                "ref": f"E{position}",
                "kind": "character",
                "name": entry,
                "data": data,
            }
        )
        position += 1
    return {"entities": entities, "edges": []}


def _run(
    world_id: str,
    provider: Callable[..., str],
) -> tuple[models.Job, Any]:
    job_id = _enqueue(world_id)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    return job, (job.result or {})


def _world(world_id: str) -> tuple[list[models.Entity], list[models.Edge]]:
    with session_scope() as session:
        entities, edges = world_state(session, world_id)
        return list(entities), list(edges)


def _rows(world_id: str) -> list[models.Entity]:
    with session_scope() as session:
        return list(world_entities(session, world_id))


def _committed_place(world_id: str, name: str) -> str:
    from app.store import commit_subgraph

    with session_scope() as session:
        head = latest_revision(session, world_id)
        head_id = head.id if head is not None else None
    commit_subgraph(
        world_id,
        entities=[models.EntityInput(kind="place", name=name, text=None, data={})],
        base_revision=head_id,
        allow_orphans=True,
    )
    with session_scope() as session:
        rows = [e for e in world_entities(session, world_id) if e.name == name]
        return rows[0].id


# ---------------------------------------------------------------------------
# STRING_LEGACY + authored backfill
# ---------------------------------------------------------------------------


def test_authored_description_and_record_revert_model_drift(world: str) -> None:
    """AUTHORED_DESCRIPTION / AUTHORED_RECORD: the model drifts every
    authored value; the committed entity carries the authored text
    verbatim (the backfill reverts drift deterministically — zero repair
    calls were needed or made)."""
    seed_figure = {
        "name": "Ferdinand",
        "record": {
            "personality": "DM-authored personality.",
            "goals": "DM-authored goals.",
        },
    }
    payload = {
        "places": [{"name": "The Old Mill", "description": "DM-authored mill prose."}],
        "key_figures": [seed_figure],
        "notes": "",
    }
    prompts: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        prompts.append(prompt)
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = {row.name: row for row in _rows(world)}
    assert rows["The Old Mill"].text == "DM-authored mill prose."
    assert rows["Ferdinand"].data["personality"] == "DM-authored personality."
    assert rows["Ferdinand"].data["goals"] == "DM-authored goals."
    # The model's drifted values never reached the world.
    assert "DRIFTED" not in json.dumps(rows["The Old Mill"].text)
    # No repair pass was ever offered: the fake emitted gate-valid output,
    # and the budget's labels show wave1 only.
    assert set((job.result or {})["llm_calls"]["by_label"]) == {"wave1"}


# ---------------------------------------------------------------------------
# ROLE_PIN
# ---------------------------------------------------------------------------


def test_role_pin_rejects_a_block_that_breaks_the_pin(world: str) -> None:
    """ROLE_PIN: a seed pins role BBEG; the generated block says NPC —
    the job fails naming the entry, reject-only, BEFORE any repair call
    (the provider count proves it), zero commits."""
    seed_figure = {"name": "The Ashen King", "role": "BBEG"}

    def provider(prompt: str, settings: LLMSettings) -> str:
        # A gate-valid BBEG record whose BLOCK identity.role says NPC —
        # the pin breach is the ONLY defect.
        return json.dumps(
            _fake_wave1(prompt, role_for=lambda _n: "BBEG", block_role_for=lambda _n: "NPC")
        )

    job_id = _enqueue(world, key_figures=[seed_figure], notes="")
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert job.error is not None
    assert "role pin" in job.error
    assert "The Ashen King" in job.error
    assert _rows(world) == []  # zero commits


def test_role_pin_honored_when_the_model_matches(world: str) -> None:
    seed_figure = {"name": "The Ashen King", "role": "BBEG"}

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_wave1(prompt, role_for=lambda _name: "BBEG"))

    job_id = _enqueue(world, key_figures=[seed_figure], notes="")
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    ferdinand = {row.name: row for row in _rows(world)}["The Ashen King"]
    assert ferdinand.data["role"] == "BBEG"


# ---------------------------------------------------------------------------
# Authored stat blocks: reject-only, byte-identical
# ---------------------------------------------------------------------------


def test_hybrid_authored_block_valid_commits_byte_identical(world: str) -> None:
    """HYBRID_AUTHORED_BLOCK_VALID: the authored block commits
    byte-identical (no stamp, no fold), with ZERO repair calls — the
    provider's labels prove the repair machinery never fired."""
    authored_block = json.loads(json.dumps(_STAT_BLOCK))
    seed_figure = {
        "name": "Ferdinand",
        "record": {"personality": "DM personality.", "stat_block": authored_block},
    }
    payload = {"key_figures": [seed_figure], "notes": ""}

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert set((job.result or {})["llm_calls"]["by_label"]) == {"wave1"}
    ferdinand = {row.name: row for row in _rows(world)}["Ferdinand"]
    assert ferdinand.data["stat_block"] == authored_block
    assert ferdinand.data["personality"] == "DM personality."


def test_hybrid_authored_block_invalid_fails_before_any_repair(world: str) -> None:
    """HYBRID_AUTHORED_BLOCK_INVALID: the authored block breaks the dice
    pattern — the job fails naming the entry + the schema-path violation,
    the provider call count proves NO repair was ever offered, and zero
    entities commit."""
    authored_block = json.loads(json.dumps(_STAT_BLOCK))
    authored_block["actions"][0]["damage"] = "1d6x+2"
    seed_figure = {"name": "Ferdinand", "record": {"stat_block": authored_block}}
    payload = {"key_figures": [seed_figure], "notes": ""}
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert job.error is not None
    assert "reject-only" in job.error
    assert "Ferdinand" in job.error
    assert "actions[0].damage" in job.error
    assert len(calls) == 1  # only the wave-1 call — the repair was never offered
    assert _rows(world) == []  # zero commits


# ---------------------------------------------------------------------------
# The mandate + three-tier declared edges
# ---------------------------------------------------------------------------


def test_tier3_hybrid_mandate_generates_the_target(world: str) -> None:
    """TIER3_HYBRID_MANDATE: the declared target is neither seeded nor
    committed — it joins the roster (kind inferred from the edge-kind
    table: located_in demands a place), generates inside the wave, and
    the declared edge wires to its fresh ULID atomically."""
    seed_figure = {
        "name": "Ferdinand",
        "relations": [{"type": "located_in", "target_name": "Duskmoor"}],
    }
    payload = {"key_figures": [seed_figure], "notes": ""}
    prompts: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        prompts.append(prompt)
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = {row.name: row for row in _rows(world)}
    assert set(rows) == {"Ferdinand", "Duskmoor"}
    assert rows["Duskmoor"].kind == "place"
    assert len(prompts) == 1  # the mandate generated INSIDE the wave call
    _entities, edges = _world(world)
    assert [(edge.src, edge.dst, edge.type) for edge in edges] == [
        (rows["Ferdinand"].id, rows["Duskmoor"].id, "located_in")
    ]


def test_mandate_reemit_recovers_a_skipped_target(world: str) -> None:
    """MANDATE_MISS -> ONE bounded re-emit on the ASSEMBLED roster: the
    wave skipped the demanded target, the re-emit (frozen roster,
    cold+seeded) produces it, and the job succeeds with the declared edge."""
    seed_figure = {
        "name": "Ferdinand",
        "relations": [{"type": "located_in", "target_name": "Duskmoor"}],
    }
    payload = {"key_figures": [seed_figure], "notes": ""}
    prompts: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        prompts.append(prompt)
        if "MISSING MANDATORY TARGETS" in prompt:
            return json.dumps(
                {"entities": [{"ref": "E1", "kind": "place", "name": "Duskmoor"}], "edges": []}
            )
        return json.dumps(_fake_wave1(prompt, skip_mandate=True))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert len(prompts) == 2
    assert "mandate_reemit" in (job.result or {})["llm_calls"]["by_label"]
    rows = {row.name: row for row in _rows(world)}
    assert set(rows) == {"Ferdinand", "Duskmoor"}
    _entities, edges = _world(world)
    assert any(edge.dst == rows["Duskmoor"].id for edge in edges)


def test_mandate_second_miss_fails_with_zero_commits(world: str) -> None:
    """The re-emit misses too — the DM-demanded endpoint is load-bearing:
    the job fails and NOTHING commits (no partial wave-1 core)."""
    seed_figure = {
        "name": "Ferdinand",
        "relations": [{"type": "located_in", "target_name": "Duskmoor"}],
    }
    payload = {"key_figures": [seed_figure], "notes": ""}
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if "MISSING MANDATORY TARGETS" in prompt:
            # The re-emit emits A place — but not the demanded one.
            return json.dumps(
                {"entities": [{"ref": "E1", "kind": "place", "name": "Wrongmoor"}], "edges": []}
            )
        return json.dumps(_fake_wave1(prompt, skip_mandate=True))  # no Duskmoor, ever

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert job.error is not None
    assert "still missing after the mandated re-emit" in job.error
    assert _rows(world) == []  # zero commits


def test_tier1_ulid_target_binds_direct(world: str) -> None:
    """TIER1_ULID_TARGET: a committed ULID binds directly — the matcher
    never runs, no mandate row joins the roster, the edge wires to that
    exact entity."""
    target_id = _committed_place(world, "Gallorb")
    seed_figure = {
        "name": "Ferdinand",
        "relations": [{"type": "located_in", "target_id": target_id}],
    }
    payload = {"key_figures": [seed_figure], "notes": ""}

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = _rows(world)
    assert len([row for row in rows if row.kind == "character"]) == 1
    _entities, edges = _world(world)
    ferdinand = next(row for row in rows if row.kind == "character")
    assert [(edge.src, edge.dst, edge.type) for edge in edges] == [
        (ferdinand.id, target_id, "located_in")
    ]


def test_tier3_name_matching_one_committed_entity(world: str) -> None:
    """TIER3_NAME_MATCHES_ONE: the declared name normalized-matches
    exactly one committed entity — it resolves to that entity, no
    generation."""
    target_id = _committed_place(world, "Duskmoor")
    seed_figure = {
        "name": "Ferdinand",
        "relations": [{"type": "located_in", "target_name": "the duskmoor"}],
    }
    payload = {"key_figures": [seed_figure], "notes": ""}

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = _rows(world)
    assert len(rows) == 2  # no mandated extra
    _entities, edges = _world(world)
    ferdinand = next(row for row in rows if row.kind == "character")
    assert [(edge.src, edge.dst, edge.type) for edge in edges] == [
        (ferdinand.id, target_id, "located_in")
    ]


def test_declared_ambiguous_target_rejects_naming_the_matches(world: str) -> None:
    """DECLARED_AMBIGUOUS_TARGET: two committed entities share the name —
    the job rejects naming the matches (the DM targets by ULID), zero
    commits."""
    _committed_place(world, "Duskmoor")
    _committed_place(world, "Duskmoor")
    seed_figure = {
        "name": "Ferdinand",
        "relations": [{"type": "located_in", "target_name": "Duskmoor"}],
    }
    payload = {"key_figures": [seed_figure], "notes": ""}

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert job.error is not None
    assert "matches 2 committed entities" in job.error
    assert len(_rows(world)) == 2  # the two committed places only


def test_tier2_staged_target_commits_pair_atomically(world: str) -> None:
    """TIER2_STAGED_TARGET: the declared key names another staged seed in
    the SAME payload — both entities commit fresh ULIDs and the edge
    wires atomically in the one revision (SAME_NAME_SAME_BUILD wiring)."""
    payload = {
        "key_figures": [
            {
                "name": "Perrin",
                "key": "squire",
                "relations": [{"type": "protects", "target_key": "knight"}],
            },
            {"name": "Seraphine", "key": "knight"},
        ],
        "notes": "",
    }

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = {row.name: row for row in _rows(world)}
    assert set(rows) == {"Perrin", "Seraphine"}
    _entities, edges = _world(world)
    assert [(edge.src, edge.dst, edge.type) for edge in edges] == [
        (rows["Perrin"].id, rows["Seraphine"].id, "protects")
    ]
    with session_scope() as session:
        head = latest_revision(session, world)
        assert head is not None


def test_same_name_same_build_commits_two_entities(world: str) -> None:
    """SAME_NAME_SAME_BUILD pin: two identical-name figure seeds in ONE
    payload — TWO distinct entities, fresh ULIDs; the roster machinery
    must not collapse them (F4)."""
    payload = {"key_figures": ["Seraphine", "Seraphine"], "notes": ""}

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, **payload)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = [row for row in _rows(world) if row.name == "Seraphine"]
    assert len(rows) == 2
    assert len({row.id for row in rows}) == 2


def test_structured_rebuild_is_a_place_delta(world: str) -> None:
    """STRUCTURED_REBUILD (gate 3): a structured re-submit of a committed
    place merges field-level — the SAME row updates (delta), not a twin."""
    payload1 = {"places": [{"name": "The Old Mill", "description": "version one."}], "notes": ""}

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_wave1(prompt))

    job_id1 = _enqueue(world, **payload1)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id1
    assert job_status(job_id1)[0].state == "succeeded"
    first = {row.name: row.id for row in _rows(world)}

    payload2 = {"places": [{"name": "The Old Mill", "description": "version two."}], "notes": ""}
    job_id2 = _enqueue(world, **payload2)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id2
    job2, _position = job_status(job_id2)
    assert job2.state == "succeeded"
    rows = {row.name: row for row in _rows(world)}
    assert rows["The Old Mill"].id == first["The Old Mill"]  # same row, updated
    assert rows["The Old Mill"].text == "version two."
    merge = (job2.result or {})["merge"]["wave1"]
    assert merge["merged"] == [
        {"name": "The Old Mill", "kind": "place", "into": first["The Old Mill"]}
    ]


def test_unknown_seed_key_is_a_422(world: str) -> None:
    from app.store import InvalidJobInputError

    with pytest.raises(InvalidJobInputError) as excinfo:
        _enqueue(world, places=[{"name": "Mill", "quest_hook": "unauthorized"}])
    assert "quest_hook" in str(excinfo.value)


def test_relation_malformed_is_a_422(world: str) -> None:
    from app.store import InvalidJobInputError

    with pytest.raises(InvalidJobInputError) as excinfo:
        _enqueue(
            world,
            key_figures=[{"name": "Ferdinand", "relations": [{"type": "protects"}]}],
        )
    assert "exactly one" in str(excinfo.value)
