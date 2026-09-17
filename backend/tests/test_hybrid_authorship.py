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


def test_role_pin_enforced_against_a_block_that_breaks_the_pin(world: str) -> None:
    """ROLE_PIN (nudge contract): a seed pins role BBEG; the generated
    block says NPC — model drift on a derived slot. The pin is ENFORCED
    deterministically: the committed block says BBEG, no repair pass was
    needed, the job succeeds."""
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
    assert job.state == "succeeded"
    ashen = {row.name: row for row in _rows(world)}["The Ashen King"]
    # the pin WON: the committed block carries the DM's role
    assert ashen.data["stat_block"]["identity"]["role"] == "BBEG"
    assert ashen.data["role"] == "BBEG"
    # no repair was needed — the fold is deterministic
    assert set((job.result or {})["llm_calls"]["by_label"]) == {"wave1"}


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
    """HYBRID_AUTHORED_BLOCK_INVALID: the authored block carries spells
    with no identity.class — a CROSS-SUBSECTION canonical breach the
    enqueue shape screen cannot see — so the job fails at the run-time
    gate naming the entry + the violation, the provider call count
    proves NO repair was ever offered, and zero entities commit.
    (Pure shape breaches — a bad dice pattern, out-of-range ints — are
    caught earlier: the enqueue screen 422s them with zero job rows.)"""
    authored_block = json.loads(json.dumps(_STAT_BLOCK))
    authored_block["spells"] = ["Magic Missile"]
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
    assert "spell" in job.error
    assert len(calls) == 1  # only the wave-1 call — the repair was never offered
    assert _rows(world) == []  # zero commits


def test_hybrid_authored_shape_breach_is_an_enqueue_422(world: str) -> None:
    """A pure SHAPE breach in an authored block (dice pattern) is a 422
    at the enqueue gate — zero job rows, the direct-path precedent."""
    import pytest as _pytest

    from app.store import InvalidJobInputError, enqueue_job

    authored_block = json.loads(json.dumps(_STAT_BLOCK))
    authored_block["actions"][0]["damage"] = "1d6x+2"
    with _pytest.raises(InvalidJobInputError, match="dice"):
        enqueue_job(
            world,
            "build_in",
            {"key_figures": [{"name": "Ferdinand", "record": {"stat_block": authored_block}}]},
        )


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


# ---------------------------------------------------------------------------
# The nudge contract: PARTIAL authored stat blocks merge with the generated
# ones (authored subsections verbatim, generator fills only the blanks)
# ---------------------------------------------------------------------------


def test_merge_authored_stat_block_semantics() -> None:
    """The merge helper: scalar subsections merge per field, list
    subsections merge BY NAME (authored bytes win on match, unmatched
    authored append), spells union-dedupe — and the generated parts the
    DM never authored survive untouched."""
    from app.pipeline.build_in import _merge_authored_stat_block

    generated = {
        "identity": {"role": "NPC", "race": "Human", "level": 5},
        "attributes": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "combat": {"ac": 12, "hp": 30},
        "skills": [{"name": "Arcana", "bonus": 6}],
        "actions": [{"name": "Longsword", "description": "Generated slash.", "to_hit": 5}],
        "spells": ["Shield"],
    }
    authored = {
        "identity": {"race": "Tiefling"},
        "actions": [
            # matches the generated Longsword — authored bytes win
            {"name": "longsword ", "description": "DM-authored action.", "damage": "3d6"},
            # no generated match — appended
            {"name": "Acid Flask", "description": "DM thrower.", "damage": "2d6+3"},
        ],
        "spells": ["shield", "Magic Missile"],
    }
    merged = _merge_authored_stat_block(authored, generated)
    # identity: authored race verbatim, generated role/level kept
    assert merged["identity"]["race"] == "Tiefling"
    assert merged["identity"]["role"] == "NPC"
    assert merged["identity"]["level"] == 5
    # attributes/combat: untouched by the authored subset
    assert merged["attributes"]["str"] == 10
    assert merged["combat"]["hp"] == 30
    # actions: matched entry carries the authored description + damage
    # verbatim (the generated to_hit survives); unmatched appended
    by_name = {action["name"].strip().lower(): action for action in merged["actions"]}
    assert by_name["longsword"]["description"] == "DM-authored action."
    assert by_name["longsword"]["damage"] == [{"dice": "3d6", "bonus": 0}]
    assert by_name["acid flask"]["description"] == "DM thrower."
    assert len(merged["actions"]) == 2
    # skills: not authored — generated list survives
    assert merged["skills"] == [{"name": "Arcana", "bonus": 6}]
    # spells: union, case-insensitive dedupe, generated entry kept
    assert merged["spells"] == ["Shield", "Magic Missile"]
    # the generated input was not mutated
    assert generated["identity"]["race"] == "Human"


def test_hybrid_partial_actions_merge_through_the_pipeline(world: str) -> None:
    """NUDGE_PARTIAL_MERGE: a seed figure whose stat block authors ONLY
    one action commits with the generated block intact (identity,
    attributes, combat from the generator) and the authored action
    merged verbatim over its generated namesake — the model's deliberate
    drift of the description is reverted."""
    authored_block = {
        "actions": [{"name": "Longsword", "description": "DM-authored action.", "damage": "3d6"}]
    }
    seed_figure = {"name": "Ferdinand", "record": {"stat_block": authored_block}}
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, key_figures=[seed_figure], notes="")
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    # no repair pass fired — the merged block is gate-clean
    assert set((job.result or {})["llm_calls"]["by_label"]) == {"wave1"}
    ferdinand = {row.name: row for row in _rows(world)}["Ferdinand"]
    block = ferdinand.data["stat_block"]
    # generated skeleton survives
    assert block["identity"]["race"] == "Human"
    assert block["attributes"]["con"] == 14
    assert block["combat"]["hp"] == 66
    # the authored action is verbatim on its generated namesake
    longsword = next(a for a in block["actions"] if a["name"].strip().lower() == "longsword")
    assert longsword["description"] == "DM-authored action."
    # the committed form is the canonical parts view of the DM's dice
    # string — same formula, gate-clean shape (this block is a
    # generated-canonical whole with authored content)
    assert longsword["damage"] == [{"dice": "3d6", "bonus": 0}]


def test_hybrid_partial_stat_shape_breach_is_an_enqueue_422(world: str) -> None:
    """A partial authored block's SHAPE breaches are enqueue 422s: a bad
    dice pattern, an out-of-range attribute, and the generated `power`
    stamp are all rejected before any job row exists."""
    import pytest as _pytest

    from app.store import InvalidJobInputError, enqueue_job

    for bad_block, fragment in (
        (
            {"actions": [{"name": "X", "description": "d", "damage": "1d6x"}]},
            "dice",
        ),
        ({"attributes": {"str": 40}}, "must be an integer"),
        ({"power": {"dpr": 9.0}}, "power"),
        ({"combat": {"hp": 0}}, "positive integer"),
    ):
        with _pytest.raises(InvalidJobInputError, match=re.escape(fragment)):
            enqueue_job(
                world,
                "build_in",
                {"key_figures": [{"name": "Ferdinand", "record": {"stat_block": bad_block}}]},
            )


def test_hybrid_partial_identity_role_pin_only_when_authored(world: str) -> None:
    """A partial block WITHOUT identity.role never trips the pin check —
    the generator fills the role; a partial block WITH a disagreeing
    authored role fails the job (reject-only)."""
    seed_figure = {
        "name": "Ferdinand",
        "record": {"stat_block": {"identity": {"race": "Tiefling"}}},
    }

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_wave1(prompt))

    job_id = _enqueue(world, key_figures=[seed_figure], notes="")
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    ferdinand = {row.name: row for row in _rows(world)}["Ferdinand"]
    # authored race verbatim; the generated role stands
    assert ferdinand.data["stat_block"]["identity"]["race"] == "Tiefling"
    assert ferdinand.data["stat_block"]["identity"]["role"] == "NPC"

    disagreeing = {
        "name": "Betrayer",
        "role": "NPC",
        "record": {"stat_block": {"identity": {"role": "BBEG"}}},
    }
    job_id = _enqueue(world, key_figures=[disagreeing], notes="")
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "pinned role" in (job.error or "")
    assert _rows(world)  # Ferdinand committed; only the second build failed


def test_nudge_attributes_only_survives_a_blockless_generation(world: str) -> None:
    """The nudge contract's ordering guarantee: a figure whose authored
    stat block is ONLY attributes, against a model that ships NO stat
    block at all, must NOT die on the pre-gate role pin (the merged
    skeleton has no identity yet) — the stat gate repairs the skeleton,
    and the pin judges the block the gates left. The authored attributes
    commit verbatim over the repaired block."""
    authored_block = {
        "attributes": {"str": 20, "dex": 20, "con": 20, "int": 20, "wis": 20, "cha": 20},
    }
    seed_figure = {"name": "fatima", "record": {"stat_block": authored_block}}

    def provider(prompt: str, settings: LLMSettings) -> str:
        if "stat_blocks" in prompt:
            # the stat repair: a complete, pin-clean block for E0
            repaired = _record("fatima")
            return json.dumps(
                {"stat_blocks": [{"ref": "E0", "stat_block": repaired["stat_block"]}]}
            )
        if "records" in prompt and "stat_blocks" not in prompt:
            # the record repair: the model's record, completed (key: data)
            return json.dumps(
                {"records": [{"ref": "E0", "data": _record("fatima")}]}
            )
        # wave 1: fatima with NO stat_block at all
        rows = json.dumps(
            {
                "entities": [
                    {
                        "ref": "E0",
                        "kind": "character",
                        "name": "fatima",
                        "data": {
                            "name": "fatima",
                            "role": "NPC",
                            "level_cr": "level 5",
                            "race_type": "Half-Elf",
                            "class_profession": "Paladin",
                            "alignment": "CG",
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
                        },
                    }
                ],
                "edges": [],
            }
        )
        return rows

    job_id = _enqueue(world, key_figures=[seed_figure], notes="")
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded", job.error
    fatima = {row.name: row for row in _rows(world)}["fatima"]
    block = fatima.data["stat_block"]
    # the repaired skeleton carries a pin-clean identity...
    assert block["identity"]["role"] == "NPC"
    # ...and the authored attributes are verbatim over it
    assert block["attributes"] == {
        "str": 20,
        "dex": 20,
        "con": 20,
        "int": 20,
        "wis": 20,
        "cha": 20,
    }


def test_nudge_pin_folds_a_repaired_block_that_breaks_the_pin(world: str) -> None:
    """A repaired block whose role STILL disagrees with the pin is folded
    to the pin at the post-gate verdict — the pin is deterministic, not
    dropped and not a rejection (the Role select is authored truth)."""
    authored_block = {"attributes": {"str": 20}}
    seed_figure = {"name": "fatima", "role": "NPC", "record": {"stat_block": authored_block}}

    def provider(prompt: str, settings: LLMSettings) -> str:
        if "stat_blocks" in prompt:
            repaired = _record("fatima")
            repaired["stat_block"]["identity"]["role"] = "BBEG"  # wrong role
            return json.dumps(
                {"stat_blocks": [{"ref": "E0", "stat_block": repaired["stat_block"]}]}
            )
        if "records" in prompt and "stat_blocks" not in prompt:
            return json.dumps(
                {"records": [{"ref": "E0", "data": _record("fatima")}]}
            )
        return json.dumps(
            {
                "entities": [
                    {
                        "ref": "E0",
                        "kind": "character",
                        "name": "fatima",
                        "data": {"name": "fatima", "role": "NPC"},
                    }
                ],
                "edges": [],
            }
        )

    job_id = _enqueue(world, key_figures=[seed_figure], notes="")
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded", job.error
    fatima = {row.name: row for row in _rows(world)}["fatima"]
    # the repaired BBEG block was folded to the pinned role
    assert fatima.data["stat_block"]["identity"]["role"] == "NPC"
    assert fatima.data["role"] == "NPC"
    # the authored attributes are still verbatim over the repaired block
    assert fatima.data["stat_block"]["attributes"]["str"] == 20

