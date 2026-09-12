"""Build-in pipeline (spec-2.3 + spec-2.4): the core-first two-wave runner.

Covers the 2.3 rows WAVE1_ONLY, TWO_WAVES, MALFORMED_OUTPUT, BAD_VOCAB,
ORPHAN, BUDGET_EXCEEDED, DETERMINISM, CANCEL_MIDRUN, STALE_BASE,
RETRIEVAL_CAP, UNKNOWN_CURSOR and the 2.4 stat-block rows STAT_OK,
STAT_INVALID_REPAIRED, STAT_MISSING_REPAIRED, STAT_STILL_INVALID,
STAT_REPAIR_BUDGET, STAT_REPAIR_BAD_REF, STAT_REPAIR_FENCE,
WAVE2_CHARACTERS_UNREQUIRED, STAT_CANCEL_BEFORE_REPAIR — with fake
providers, no live LLM. Each test runs its own scratch DB and campaign
so commits, queue positions, and revisions are deterministic.
"""

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.core import ids
from app.core.settings import LLMSettings
from app.pipeline import combat
from app.pipeline.build_in import (
    REPAIR_SEED,
    REPAIR_TEMPERATURE,
    WAVE2_MAX_EDGES,
    WAVE2_MAX_ENTITIES,
    _build_record_repair_prompt,
    _collect_record_issues,
    _log_stat_repair_scope_breaches,
    _OrphanRetryError,
    _repair_retry_prompt,
    _validate_subgraph,
    _wave1_chunks,
    _wave1_roster,
    build_anchor_repair_schema,
    build_wave1_prompt,
    build_wave2_prompt,
    build_wave_schema,
    canonicalize_entity_kind,
    edge_kind_ok,
    normalize_entity_name,
)
from app.pipeline.fencing import json_error
from app.pipeline.knowledge import validate_stat_block
from app.pipeline.retrieval import retrieve_neighborhood, serialize_context
from app.pipeline.statblocks import (
    StatIssue,
    build_stat_repair_schema,
    canonicalize_action_damage,
    conform_power,
    is_conformable,
    spells_reference_text,
    stat_block_rules_text,
)
from app.pipeline.worker import run_next_job
from app.providers.llm import ProviderError
from app.store import (
    EDGE_TYPES,
    InvalidJobInputError,
    app_db_url,
    campaign_seed,
    cancel_job,
    commit_subgraph,
    create_campaign,
    enqueue_job,
    init_db,
    job_status,
    latest_revision,
    list_jobs,
    models,
    register_account,
    revision_chain,
    session_scope,
    set_change_listener,
    world_edges,
    world_entities,
)

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")


def _owner_id() -> str:
    """One owner account per scratch DB for campaign creation (spec-1.6)."""
    return register_account(f"owner-buildin-{ids.new_id()}@example.com", "password123").id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'build-in.db'}")
    try:
        yield create_campaign(
            _owner_id(),
            title="Build-In World",
            description="",
            theme="High Fantasy",
            custom_lore="",
        ).id
    finally:
        init_db(previous)


def _enqueue(world: str, max_llm_calls: int | None = None, **payload: Any) -> str:
    return enqueue_job(world, "build_in", payload, max_llm_calls=max_llm_calls).id


#: A valid AR25 minimal stat block for the wave-1 key figure Mira Vane
#: (spec-2.4): NPC, level 5 Human Fighter — passes every constraint, so
#: the wave commits without a repair pass. Power-floor compliant: 27 DPR
#: inside the level-5 band and hp 66 at the frail line.
_MIRA_STAT_BLOCK: dict[str, Any] = {
    "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter", "alignment": "LG"},
    "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
    "combat": {"ac": 16, "hp": 66},
    "skills": [{"name": "Athletics", "bonus": 5}],
    "actions": [
        {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 4d10+5 slashing"}
    ],
}

#: The conditional boss section (AR24) for Monster/BBEG records.
_BOSS_SECTION: dict[str, Any] = {
    "lair_actions": "On initiative 20, the harbor fog rises.",
    "legendary_actions": "One legendary whirlpool stride per round.",
    "immunities": "None.",
    "vulnerabilities": "Piercing damage from ranged weapons.",
}


def _character_record(name: str, **overrides: Any) -> dict[str, Any]:
    """A complete AR24 character record (dogfood fix 2026-09-09): build-in
    characters now commit the full sectioned record — the same shape
    ``store.candidates.payload_section_violations`` guards on the generate
    path. Pass stat_block=/boss=/role= overrides to vary it."""
    record: dict[str, Any] = {
        "name": name,
        "role": "NPC",
        "level_cr": "level 5",
        "race_type": "Human",
        "class_profession": "Fighter",
        "alignment": "LG",
        "personality": "Courteous, watchful, quietly furious.",
        "secret": "She drowned her brother's claim in the harbor.",
        "rumor": "The Guild's ledgers miss a year of her name.",
        "party_hook": "She hires the party to carry a sealed ledger out of the city.",
        "appearance": "A lean woman with a wet-sand braid and a brow scar.",
        "background": "A former harbor clerk, now deep with the Gilded Bar.",
        "goals": "Buy back her family's house before the tide turns.",
        "relationships": "Owes the Guild; trusts no one else.",
        "voice_style": "Low, precise, unhurried.",
        "catchphrases": "Tides take, tides give.",
        "world_integration": {
            "reputation": "Known to the Guild, trusted by no one.",
            "factions": "The Gilded Bar (member).",
            "current_location": "The Gilded Bar, back room.",
            "reaction_matrix": "Greets strangers flatly; pays debts early.",
            "on_defeat": "Flees to the harbor with the ledger.",
        },
    }
    record.update(overrides)
    return record


def _wave1_output() -> dict[str, Any]:
    """A valid wave-1 output: two core entities wired by typed edges; the
    character (key figure) carries the full AR24 record + an AR25-valid
    minimal stat block (spec-2.4, dogfood fix 2026-09-09) so the wave
    commits without a repair pass."""
    return {
        "entities": [
            {"ref": "E0", "kind": "faction", "name": "The Gilded Bar", "text": "smoke and coin"},
            {
                "ref": "E1",
                "kind": "character",
                "name": "Mira Vane",
                "data": {
                    **_character_record("Mira Vane"),
                    "goal": "tea house",
                    "stat_block": _MIRA_STAT_BLOCK,
                },
            },
        ],
        "edges": [
            {"src": "E0", "dst": "E1", "type": "member_of", "counter": 1},
            {"src": "E1", "dst": "E0", "type": "debt", "counter": 3},
        ],
    }


def _wave1_output_edgeless() -> dict[str, Any]:
    """A wave-1 output whose last two entities carry no edge at all — the
    live 2026-09-11 shape (a wired core with unwired factions/places
    appended after it); wave 1 commits it edgeless (owner verdict
    2026-09-11 — the DM prunes)."""
    output = _wave1_output()
    output["entities"].extend(
        [
            {"ref": "E2", "kind": "faction", "name": "Myconid Colony"},
            {"ref": "E3", "kind": "place", "name": "Grymforge"},
        ]
    )
    return output


def _wave2_output() -> dict[str, Any]:
    """A valid wave-2 output: two new entities (N refs) each anchored to a
    core (C) entity, plus an internal relationship."""
    return {
        "entities": [
            {"ref": "N0", "kind": "place", "name": "The Drowned Rat", "text": "a dockside inn"},
            {
                "ref": "N1",
                "kind": "character",
                "name": "Captain Harlow",
                "data": {**_character_record("Captain Harlow"), "stat_block": _MIRA_STAT_BLOCK},
            },
        ],
        "edges": [
            {"src": "N0", "dst": "C0", "type": "relationship"},
            {"src": "N1", "dst": "C1", "type": "ally_of", "counter": 2},
            {"src": "N0", "dst": "N1", "type": "relationship"},
        ],
    }


def _wave2_output_orphan() -> dict[str, Any]:
    """A wave-2 output whose third entity only edges to wave peers — the
    core-anchor rule (no orphans) must reject it."""
    return {
        "entities": [
            {"ref": "N0", "kind": "place", "name": "The Drowned Rat"},
            {
                "ref": "N1",
                "kind": "character",
                "name": "Captain Harlow",
                "data": {**_character_record("Captain Harlow"), "stat_block": _MIRA_STAT_BLOCK},
            },
            {
                "ref": "N2",
                "kind": "character",
                "name": "Nowhere Man",
                "data": {**_character_record("Nowhere Man"), "stat_block": _MIRA_STAT_BLOCK},
            },
        ],
        "edges": [
            {"src": "N0", "dst": "C0", "type": "relationship"},
            {"src": "N1", "dst": "C1", "type": "ally_of", "counter": 2},
            {"src": "N2", "dst": "N1", "type": "relationship"},
        ],
    }


# ---------------------------------------------------------------------------
# WAVE1_ONLY / TWO_WAVES
# ---------------------------------------------------------------------------


def test_wave1_only_commits_core_and_succeeds(world: str) -> None:
    """notes blank: one revision, entities + typed edges committed, progress
    0.5, job succeeded, result carries wave 1 only."""
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"], notes="")

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_wave1_output())

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.progress == 0.5
    assert job.result is not None
    assert len(job.result["waves"]) == 1
    wave = job.result["waves"][0]
    assert wave["wave"] == 1
    assert wave["entities"] == 2 and wave["edges"] == 2
    assert len(wave["entity_ids"]) == 2 and all(len(eid) == 26 for eid in wave["entity_ids"])
    assert job.result["entity_count"] == 2 and job.result["edge_count"] == 2
    with session_scope() as session:
        revisions = revision_chain(session, world)
        entities, edges = world_entities(session, world), world_edges(session, world)
    assert len(revisions) == 1
    assert [e.name for e in entities] == ["The Gilded Bar", "Mira Vane"]
    assert {e.kind for e in entities} == {"character", "faction"}
    assert {e.type for e in edges} == {"member_of", "debt"}
    # Fully networked at the commit: every entity is an edge endpoint.
    endpoints = {e.src for e in edges} | {e.dst for e in edges}
    assert {e.id for e in entities} == endpoints


def test_two_waves_commit_core_first_then_notes(world: str) -> None:
    """notes non-blank: wave 1 commits first (AR5), wave 2 commits on
    base=revision1, every wave-2 entity edges into the core."""
    responses = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]

    def provider(prompt: str, settings: LLMSettings) -> str:
        return responses.pop(0)

    job_id = _enqueue(world, notes="the docks teem with the Drowned Rat and Captain Harlow")
    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.progress == 1.0
    assert job.result is not None
    assert [w["wave"] for w in job.result["waves"]] == [1, 2]
    assert job.result["entity_count"] == 4 and job.result["edge_count"] == 5
    rev1_id = job.result["waves"][0]["revision_id"]
    rev2_id = job.result["waves"][1]["revision_id"]
    wave1_ids = set(job.result["waves"][0]["entity_ids"])
    wave2_ids = set(job.result["waves"][1]["entity_ids"])
    assert wave1_ids.isdisjoint(wave2_ids)

    with session_scope() as session:
        revisions = revision_chain(session, world)
        rev2 = session.get(models.Revision, rev2_id)
        edges = world_edges(session, world)
    assert len(revisions) == 2
    assert revisions[0].base_revision is None  # first commit on an empty world
    assert rev2 is not None and rev2.base_revision == rev1_id  # wave 2 stages on wave 1
    edge_pairs = [(e.src, e.dst) for e in edges]
    for entity_id in wave2_ids:
        assert any(
            (src == entity_id and dst in wave1_ids) or (dst == entity_id and src in wave1_ids)
            for src, dst in edge_pairs
        ), f"wave-2 entity {entity_id} must edge into the core"


def test_whitespace_only_notes_skip_wave2(world: str) -> None:
    """notes that are blank after trimming skip wave 2 entirely: a single
    provider call, one wave in the result, progress 0.5."""
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], notes="   \n\t ")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(_wave1_output())

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 1  # wave 2 never ran
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.progress == 0.5
    assert job.result is not None and len(job.result["waves"]) == 1


def test_progress_and_success_broadcasts(world: str) -> None:
    """A successful two-wave build emits job_progress (0.5 — the AD-17
    intermediate wave boundary) then job_done + queue_changed."""
    events: list[tuple[str, str, float]] = []
    set_change_listener(lambda event, job, _position: events.append((event, job.id, job.progress)))
    try:
        responses = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]

        def provider(prompt: str, settings: LLMSettings) -> str:
            return responses.pop(0)

        job_id = _enqueue(world, notes="the docks")
        run_next_job(provider=provider, settings=SETTINGS)
        job, _position = job_status(job_id)
        assert job.state == "succeeded"
        assert ("job_progress", job_id, 0.5) in events
        assert ("job_done", job_id, 1.0) in events
        assert ("queue_changed", job_id, 1.0) in events
    finally:
        set_change_listener(None)


# ---------------------------------------------------------------------------
# MALFORMED_OUTPUT / BAD_VOCAB
# ---------------------------------------------------------------------------


def test_malformed_output_fails_zero_commits(world: str) -> None:
    """non-JSON output fails the job naming the wave; job_failed +
    queue_changed emitted; zero commits."""
    events: list[tuple[str, str]] = []
    set_change_listener(lambda event, job, _position: events.append((event, job.id)))
    try:
        job_id = _enqueue(world, places=["Greymarch"])
        processed = run_next_job(
            provider=lambda prompt, settings: "this is not json", settings=SETTINGS
        )
        assert processed == job_id
        job, _position = job_status(job_id)
        assert job.state == "failed"
        assert "wave 1" in (job.error or "")
        assert ("job_failed", job_id) in events
        assert ("queue_changed", job_id) in events
        with session_scope() as session:
            assert revision_chain(session, world) == []
            assert world_entities(session, world) == []
    finally:
        set_change_listener(None)


def test_fence_wrapped_valid_json_is_stripped_and_succeeds(world: str) -> None:
    """A markdown-fenced wave-1 output is stripped before parsing (the
    fence is optional decoration, not malformed output)."""
    wrapped = "```json\n" + json.dumps(_wave1_output()) + "\n```"
    job_id = _enqueue(world, places=["Greymarch"])
    processed = run_next_job(provider=lambda prompt, settings: wrapped, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_count"] == 2


def test_wave_calls_carry_envelope_schema(world: str) -> None:
    """WAVE_CARRIES_SCHEMA: the wave call carries the envelope schema via a
    settings copy (zero double churn — the fake keeps its
    ``(prompt, settings)`` shape); the job result is identical to today."""
    expected = build_wave_schema(1, refs=["E0"])  # one place in the roster
    seen: list[Any] = []

    def fake(prompt: str, settings: LLMSettings) -> str:
        seen.append(settings.response_format)
        return json.dumps(_wave1_output())

    job_id = _enqueue(world, places=["Greymarch"])
    processed = run_next_job(provider=fake, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_count"] == 2
    assert seen == [expected]


def test_wave2_and_anchor_repair_carry_schemas(world: str) -> None:
    """WAVE2_AND_ANCHOR_CARRY: wave 2 carries the envelope schema while the
    anchor repair carries the edges-only schema — reverting either call
    site to plain settings (or to the wave schema) fails this (the
    wave-1-only test never executes those lines). The repair half runs on
    wave 2: wave 1 commits edgeless and never repairs (spec: edgeless
    repair scope)."""
    expected = build_wave_schema(0)  # notes-only job: empty roster, no ref enum
    seen: list[Any] = []
    responses = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]

    def fake(prompt: str, settings: LLMSettings) -> str:
        seen.append(settings.response_format)
        return responses.pop(0)

    job_id = _enqueue(world, notes="the docks teem with the Drowned Rat and Captain Harlow")
    processed = run_next_job(provider=fake, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    wave2_schema = build_wave_schema(max_entities=WAVE2_MAX_ENTITIES, max_edges=WAVE2_MAX_EDGES)
    assert seen == [expected, wave2_schema]  # wave 1 pinned, wave 2 count-free

    seen_repair: list[Any] = []
    responses_repair = [
        json.dumps(_wave1_output()),
        json.dumps(_wave2_output_orphan()),
        json.dumps({"edges": [{"src": "N2", "dst": "C0", "type": "relationship"}]}),
    ]

    def fake_repair(prompt: str, settings: LLMSettings) -> str:
        seen_repair.append(settings.response_format)
        return responses_repair.pop(0)

    job_id2 = _enqueue(world, notes="more world")
    processed2 = run_next_job(provider=fake_repair, settings=SETTINGS)
    assert processed2 == job_id2
    job2, _position = job_status(job_id2)
    assert job2.state == "succeeded"
    # The orphan output has N0..N2 and the wave-1 core is two entities, so
    # the repair schema is pinned to exactly those refs — a repair reusing
    # the wave envelope (or plain settings) fails this line.
    expected_repair = build_anchor_repair_schema(["N0", "N1", "N2"], ["C0", "C1"])
    assert seen_repair == [expected, wave2_schema, expected_repair]


def test_wave_envelope_schema_pins_wire_literals() -> None:
    """The envelope's load-bearing literals are pinned by value — the
    carry test compares the builder to itself, so a flipped literal
    (additionalProperties True, a dropped required key, a widened edge
    type) would stay green without this pin."""
    schema = build_wave_schema()
    assert schema["type"] == "json_schema"
    named = schema["json_schema"]
    assert named["name"] == "build_wave" and named["strict"] is True
    envelope = named["schema"]
    assert envelope["required"] == ["entities", "edges"]
    assert envelope["additionalProperties"] is False
    entities = envelope["properties"]["entities"]["items"]
    assert entities["required"] == ["ref", "kind", "name"]
    assert entities["additionalProperties"] is False
    edges = envelope["properties"]["edges"]["items"]
    assert edges["required"] == ["src", "dst", "type"]
    assert edges["additionalProperties"] is False
    # BAD_TYPE_WIRE: the edge type is an enum single-sourced from the
    # store vocabulary — an invented type is unemittable on
    # grammar-enforcing backends. Pinned by value so a widened type
    # (or a stale copy of the vocabulary) fails here, not live.
    assert edges["properties"]["type"] == {"enum": sorted(EDGE_TYPES)}
    # COUNT_PIN (ladder rung 50): the entities array carries no count keys
    # by default; with a roster count it pins exactly that many items.
    assert "minItems" not in envelope["properties"]["entities"]
    assert "maxItems" not in envelope["properties"]["entities"]
    pinned = build_wave_schema(50)["json_schema"]["schema"]["properties"]["entities"]
    assert pinned["minItems"] == 50 and pinned["maxItems"] == 50
    # E1 tightening (the 100-entity cut): kind enum, name minLength, a
    # chunk's ref enum, wave-2 caps. E2 (present-key data constraints) was
    # TRIED AND REVERTED on live evidence: ladder attempt d1 (Qwen3.8,
    # rung 10) showed the described-key bias — with only stat_block
    # described inside data, the model shipped characters carrying
    # data.stat_block and NO record at all, a repair storm the open schema
    # never produced. ``data`` stays an open object; identity garbage
    # belongs to the validator and the merge guards.
    assert entities["properties"]["kind"] == {"enum": ["character", "faction", "place"]}
    assert entities["properties"]["name"] == {"type": "string", "minLength": 1}
    assert entities["properties"]["data"] == {"type": "object"}
    chunk = build_wave_schema(2, refs=["E24", "E25"])["json_schema"]["schema"]["properties"]
    assert chunk["entities"]["items"]["properties"]["ref"] == {"enum": ["E24", "E25"]}
    capped = build_wave_schema(max_entities=24, max_edges=256)["json_schema"]["schema"]
    assert capped["properties"]["entities"]["maxItems"] == 24
    assert "minItems" not in capped["properties"]["entities"]
    assert capped["properties"]["edges"]["maxItems"] == 256


def test_anchor_repair_schema_pins_wire_literals() -> None:
    """The anchor-repair schema's load-bearing literals are pinned by value:
    an edges-only envelope (no entity list — renames/drops
    unrepresentable), src/dst enums of exactly the known N-refs plus
    C-labels, the edge type single-sourced from the store vocabulary, and
    additionalProperties false at both levels."""
    schema = build_anchor_repair_schema(["N0", "N2", "N1"], ["C1", "C0"])
    assert schema["type"] == "json_schema"
    named = schema["json_schema"]
    assert named["name"] == "build_anchor_repair" and named["strict"] is True
    envelope = named["schema"]
    assert envelope["required"] == ["edges"]
    assert envelope["additionalProperties"] is False
    assert "entities" not in envelope["properties"]
    edges = envelope["properties"]["edges"]["items"]
    assert edges["required"] == ["src", "dst", "type"]
    assert edges["additionalProperties"] is False
    assert edges["properties"]["src"] == {"enum": ["C0", "C1", "N0", "N1", "N2"]}
    assert edges["properties"]["dst"] == {"enum": ["C0", "C1", "N0", "N1", "N2"]}
    assert edges["properties"]["type"] == {"enum": sorted(EDGE_TYPES)}


def test_stat_repair_calls_carry_repair_schema(world: str) -> None:
    """REPAIR_SCHEMA_CARRY (spec: edgeless repair scope): the wave call
    carries the wave envelope while the stat-repair call carries the
    strict repair-response schema — passing the wave settings into the
    gate would constrain a {"stat_blocks": [...]} response to the wave
    envelope undetected, and plain settings would leave the E4-class
    shapes emittable."""
    output = _wave1_output()
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output["entities"][1]["data"] = {
        **_character_record("Mira Vane"),
        "stat_block": bad_block,
    }
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]
    seen: list[Any] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        seen.append(settings.response_format)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(seen) == 2  # wave 1 + exactly one repair pass
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert seen[0] == build_wave_schema(2, refs=["E0", "E1"])  # roster pinned
    assert seen[1] == build_stat_repair_schema()


def test_wave2_stat_repair_carries_repair_schema(world: str) -> None:
    """WAVE2_REPAIR_CARRY: the wave-2 stat-repair call carries the strict
    repair-response schema too — the wave-1-only carry test never executes
    the wave-2 gate lines, so dropping the kwarg there stayed green."""
    wave2 = _wave2_output()
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    wave2["entities"][1]["data"] = {
        **_character_record("Captain Harlow"),
        "stat_block": bad_block,
    }
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(wave2),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]
    seen: list[Any] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"], notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        seen.append(settings.response_format)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert seen == [
        build_wave_schema(2, refs=["E0", "E1"]),
        build_wave_schema(max_entities=WAVE2_MAX_ENTITIES, max_edges=WAVE2_MAX_EDGES),
        build_stat_repair_schema(),
    ]


def test_gate_repair_breach_logs_through_run(caplog: pytest.LogCaptureFixture, world: str) -> None:
    """BREACH_GATE_WIRING + STRIP (spec: strip drift): a repair that drifts
    outside its EDIT SCOPE on a live job emits the scope-breach warning
    through run_next_job — and the drift never lands. The attempt-8 shape:
    converging numbers (str 40 fixed to 14) riding with a rider section
    (traits added on an attributes-only issue) commits the numbers, drops
    the riders, still logs the breach paths."""
    output = _wave1_output()
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output["entities"][1]["data"] = {
        **_character_record("Mira Vane"),
        "stat_block": bad_block,
    }
    drifted_block = {
        **_MIRA_STAT_BLOCK,
        "traits": [{"name": "Sneak Attack", "description": "once per turn"}],
    }
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": drifted_block}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        return responses.pop(0)

    with caplog.at_level("WARNING", logger="app.pipeline.build_in"):
        processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert "scope breach" in caplog.text and "traits" in caplog.text
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"] == _MIRA_STAT_BLOCK  # numbers merged, riders dropped
    assert "traits" not in mira.data["stat_block"]


def test_stat_repair_scope_breach_logs_log_only(caplog: pytest.LogCaptureFixture) -> None:
    """SCOPE_BREACH_LOG: a repair that rewrites a section outside its EDIT
    SCOPE logs a warning naming the paths — and nothing else happens
    (log-only: never a new failure class, never merge rejection; the
    re-audit stays authoritative)."""
    before = dict(_MIRA_STAT_BLOCK)
    entity = models.EntityInput(
        kind="character",
        name="Mira Vane",
        data={**_character_record("Mira Vane"), "stat_block": before},
    )
    issue = StatIssue(1, entity, ("attributes.str must be an integer in [1, 30]",))
    # In-scope edit (attributes only) → quiet.
    fixed = {**before, "attributes": {**before["attributes"], "str": 14}}
    with caplog.at_level("WARNING", logger="app.pipeline.build_in"):
        _log_stat_repair_scope_breaches(
            [issue], {1: before}, {1: fixed}, job_id="job-1", attempt=1, wave=1
        )
    assert "scope breach" not in caplog.text
    # Out-of-scope edit (traits rewritten on an attributes-only issue) → warning.
    caplog.clear()
    drifted = {**fixed, "traits": [{"name": "Sneak Attack", "description": "once"}]}
    with caplog.at_level("WARNING", logger="app.pipeline.build_in"):
        _log_stat_repair_scope_breaches(
            [issue], {1: before}, {1: drifted}, job_id="job-1", attempt=1, wave=1
        )
    assert "scope breach" in caplog.text and "traits" in caplog.text
    assert "job-1" in caplog.text and "wave 1" in caplog.text and "attempt 1" in caplog.text
    # Nested drift under an unchanged top-level key (the E8-class bonus
    # rewrite) → warning naming the path, not silence.
    caplog.clear()
    part = {"dice": "1d8", "count": 1, "sides": 8, "bonus": 4, "average": 8.5, "type": "slashing"}
    old_armed = {**before, "actions": [{**before["actions"][0], "damage": [part]}]}
    new_armed = {
        **old_armed,
        "actions": [{**old_armed["actions"][0], "damage": [{**part, "bonus": 69}]}],
    }
    with caplog.at_level("WARNING", logger="app.pipeline.build_in"):
        _log_stat_repair_scope_breaches(
            [issue], {1: old_armed}, {1: new_armed}, job_id="job-1", attempt=2, wave=1
        )
    assert "scope breach" in caplog.text and "actions[0].damage[0].bonus" in caplog.text


@pytest.mark.parametrize(
    "wrapped",
    [
        "\n\n```json\n" + json.dumps(_wave1_output()) + "\n```\n\n",  # leading blanks + trailing ws
        "```\n" + json.dumps(_wave1_output()) + "\n```   ",  # bare fence, trailing whitespace
        "```json  \n" + json.dumps(_wave1_output()) + "\n  ```\n",  # ws on the fence lines
    ],
)
def test_tolerant_fence_stripping_succeeds(world: str, wrapped: str) -> None:
    """The fence strip tolerates leading blank lines and trailing
    whitespace on the fence lines (finding 6) — all still parse."""
    job_id = _enqueue(world, places=["Greymarch"])
    processed = run_next_job(provider=lambda prompt, settings: wrapped, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_count"] == 2


def test_unclosed_fence_fails_as_invalid_json(world: str) -> None:
    """A fence opener without a closer is NOT stripped — the JSON error
    names the real problem instead of parsing a fragment."""
    job_id = _enqueue(world, places=["Greymarch"])
    processed = run_next_job(
        provider=lambda prompt, settings: "```json\n" + json.dumps(_wave1_output()),
        settings=SETTINGS,
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "not valid JSON" in (job.error or "")


def test_wrong_shape_fails_naming_wave(world: str) -> None:
    """A non-object/shape-malformed output fails the job naming the wave,
    zero commits, and the queue keeps the processed id."""
    job_id = _enqueue(world, places=["Greymarch"])
    processed = run_next_job(
        provider=lambda prompt, settings: json.dumps({"entities": "nope"}),
        settings=SETTINGS,
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "wave 1" in (job.error or "") and "entities" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []
        assert world_entities(session, world) == []


def test_non_canonical_refs_rejected(world: str) -> None:
    """The ref scheme is canonical: E01/N007-style zero-padded refs are
    rejected wherever they appear (entity refs and edge endpoints)."""
    for tinker in ("entity", "edge"):
        output = _wave1_output()
        if tinker == "entity":
            output["entities"][1]["ref"] = "E01"
        else:
            output["edges"][0]["src"] = "E01"
        job_id = _enqueue(world, places=["Greymarch"])
        processed = run_next_job(
            provider=lambda prompt, settings, output=output: json.dumps(output), settings=SETTINGS
        )
        assert processed == job_id
        job, _position = job_status(job_id)
        assert job.state == "failed"
        assert "E01" in (job.error or "")
        with session_scope() as session:
            assert revision_chain(session, world) == []


def test_self_loop_edges_dropped_at_boundary(world: str) -> None:
    """SELF_LOOP_DROP (ladder rung 50, owner verdict 2026-09-12): a
    self-loop carries zero graph information under the closed vocabulary,
    so the boundary drops it with an info log instead of failing the wave
    — the dedup's sibling. An all-self-loop wave validates to zero edges
    and commits edgeless; a mixed wave keeps its good edges. (The store
    still rejects self-loops outright — FR2 backstop pins live in
    test_store/test_edges_api.)"""
    output = {
        "entities": [
            {"ref": "E0", "kind": "place", "name": "Solo"},
            {"ref": "E1", "kind": "place", "name": "Alone"},
        ],
        "edges": [
            {"src": "E0", "dst": "E0", "type": "ally_of"},
            {"src": "E0", "dst": "E1", "type": "ally_of"},
            {"src": "E1", "dst": "E1", "type": "ally_of"},
        ],
    }
    _entities, edges = _validate_subgraph(1, output)
    assert [(edge.type) for edge in edges] == ["ally_of"]  # only E0 -> E1 survives
    assert edges[0].src != edges[0].dst
    job_id = _enqueue(world, key_figures=["Solo", "Alone"])
    processed = run_next_job(
        provider=lambda prompt, settings: json.dumps(output), settings=SETTINGS
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["edge_count"] == 1


@pytest.mark.parametrize("bad_counter", ["3", 1.5])
def test_bad_counter_type_fails_naming_edge(world: str, bad_counter: Any) -> None:
    """A non-int counter in LLM output fails the job naming the edge;
    zero commits (the store also guards shape, but the pipeline rejects
    before any commit is attempted)."""
    output = _wave1_output()
    output["edges"][0]["counter"] = bad_counter
    job_id = _enqueue(world, places=["Greymarch"])
    processed = run_next_job(
        provider=lambda prompt, settings: json.dumps(output), settings=SETTINGS
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "edge 0" in (job.error or "") and "counter" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_bad_vocab_fails_naming_type_zero_commits(world: str) -> None:
    """BAD_VOCAB: an edge type outside EDGE_TYPES fails the job naming the
    type; wave-1 zero commits."""
    output = _wave1_output()
    output["edges"][0]["type"] = "teleports_to"
    job_id = _enqueue(world, places=["Greymarch"])
    run_next_job(provider=lambda prompt, settings: json.dumps(output), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "teleports_to" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


# ---------------------------------------------------------------------------
# EDGELESS WAVE 1 (spec: edgeless repair scope, owner verdict 2026-09-11 —
# the 2026-09-11 wave-1 re-emit is reversed: wiring the model will not
# invent is not worth a lost build; the DM prunes. Wave-2 anchor tests live
# under WAVE-2 ANCHOR REPAIR below.)
# ---------------------------------------------------------------------------


def test_wave1_edgeless_entities_validate() -> None:
    """EDGELESS_WAVE1 (validator): a wave-1 subgraph that is valid except
    for edgeless tail entities validates as-is — no retry signal, no
    rejection — mapping every entity and edge through."""
    entities, edges = _validate_subgraph(1, _wave1_output_edgeless())
    assert [entity.name for entity in entities] == [
        "The Gilded Bar",
        "Mira Vane",
        "Myconid Colony",
        "Grymforge",
    ]
    assert len(edges) == 2


def test_wave1_edgeless_commits_with_one_wave_call(
    world: str, caplog: pytest.LogCaptureFixture
) -> None:
    """EDGELESS_WAVE1 (job): a wave-1 output whose tail entities carry no
    edge commits as-is with exactly one wave call and no re-emit — the
    entire build used to die here with zero commits."""
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(_wave1_output_edgeless())

    job_id = _enqueue(world, places=["Greymarch"])
    with caplog.at_level("INFO", logger="app.pipeline.build_in"):
        processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 1  # the single wave call — never a re-emit
    assert all("ANCHOR REPAIR" not in call for call in calls)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert "committed edgeless" in caplog.text
    assert "Myconid Colony" in caplog.text and "Grymforge" in caplog.text
    assert job.result is not None and job.result["entity_count"] == 4
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1
        assert {e.name for e in world_entities(session, world)} == {
            "The Gilded Bar",
            "Mira Vane",
            "Myconid Colony",
            "Grymforge",
        }


def test_validate_subgraph_rejects_unknown_wave() -> None:
    """WAVE_GUARD: a wave other than 1 or 2 raises ValueError (programmer
    error, never a JobPayloadError) instead of silently validating as
    wave 2."""
    parsed = {
        "entities": [{"ref": "E0", "kind": "place", "name": "Nowhere"}],
        "edges": [],
    }
    with pytest.raises(ValueError, match="unknown wave"):
        _validate_subgraph(7, parsed)


# ---------------------------------------------------------------------------
# BUDGET_EXCEEDED
# ---------------------------------------------------------------------------


def test_budget_exceeded_fails_before_http_core_stays(world: str) -> None:
    """AR21: max_llm_calls=1 — the wave-2 call is refused before the HTTP
    request; the job fails naming the budget and the core stays."""
    responses = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    job_id = _enqueue(world, notes="more world", max_llm_calls=1)
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "budget" in (job.error or "").lower()
    assert len(calls) == 1  # the second call never reached the provider
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1  # wave-1 core stays
        assert {e.name for e in world_entities(session, world)} == {"The Gilded Bar", "Mira Vane"}


# ---------------------------------------------------------------------------
# DETERMINISM (AD-16)
# ---------------------------------------------------------------------------


def _seed_campaign(*, id_suffix: str, created_at: str) -> models.Campaign:
    return models.Campaign(
        id=f"{'C' * 25}{id_suffix}",
        owner_id="O" * 26,
        title="Grim Peaks",
        description="cold valley",
        theme="Grimdark",
        custom_lore="the old gods stir",
        created_at=created_at,
    )


def test_build_wave1_prompt_deterministic() -> None:
    """Same seed + payload -> byte-identical prompts across calls, key
    orders, and campaign ids/timestamps (no ULIDs or job state leak)."""
    payload = {
        "factions": ["The Guild", "  "],
        "places": ["Greymarch"],
        "key_figures": ["  Mira  "],
    }
    seed_a = _seed_campaign(id_suffix="A", created_at="2026-01-01T00:00:00Z")
    first = build_wave1_prompt(seed_a, payload)
    assert build_wave1_prompt(seed_a, payload) == first
    # A different campaign id / created_at must not change the prompt.
    seed_b = _seed_campaign(id_suffix="B", created_at="2099-12-31T23:59:59Z")
    assert build_wave1_prompt(seed_b, payload) == first
    # Payload key order is irrelevant (sections render in fixed order).
    shuffled = {
        "key_figures": ["  Mira  "],
        "places": ["Greymarch"],
        "factions": ["The Guild", "  "],
    }
    assert build_wave1_prompt(seed_a, shuffled) == first
    # Wave 1 DOES see the notes (2026-09-10): a DM note fixing a key
    # figure's level has to reach the wave that builds that figure, or the
    # record gets an invented level and an unreachable DPR band. Blank
    # notes still render byte-identically (asserted below).
    with_notes = build_wave1_prompt(seed_a, {**payload, "notes": "secret notes"})
    assert with_notes != first
    assert "DM NOTES" in with_notes and "secret notes" in with_notes
    assert build_wave1_prompt(seed_a, {**payload, "notes": "   "}) == first
    # Unknown keys are still ignored.
    assert build_wave1_prompt(seed_a, {**payload, "ambient": "x"}) == first
    # The prompt carries the closed vocabulary + counter semantics (2.2).
    assert "EDGE VOCABULARY" in first and "member_of" in first and "debt: amount" in first
    # spec-2.4: the wave-1 prompt embeds the stat-block contract — the
    # shared rules text and the full spells reference, exactly what the
    # validator enforces (AD-16).
    assert "STAT BLOCKS" in first and stat_block_rules_text() in first
    assert spells_reference_text() in first and "SRD SPELLS BY CLASS" in first
    assert "CHARACTER RECORDS" in first and "stat_block" in first
    assert seed_a.id not in first and seed_a.created_at not in first


def _fresh_context() -> tuple[list[models.Entity], list[models.Edge]]:
    bar = models.Entity(
        id=ids.new_id(),
        campaign_id="C" * 26,
        kind="faction",
        name="The Gilded Bar",
        text="smoke and coin",
        data={"economy": {"level": 3, "resources": ["gold"]}},
        created_at="2026-01-01T00:00:00Z",
    )
    mira = models.Entity(
        id=ids.new_id(),
        campaign_id="C" * 26,
        kind="character",
        name="Mira Vane",
        text=None,
        data={},
        created_at="2026-01-01T00:00:00Z",
    )
    edge = models.Edge(
        id=ids.new_id(),
        campaign_id="C" * 26,
        src=bar.id,
        dst=mira.id,
        type="member_of",
        counter=1,
        created_at="2026-01-01T00:00:00Z",
    )
    return [bar, mira], [edge]


def test_build_wave2_prompt_deterministic() -> None:
    """Same seed + notes + context -> byte-identical prompts; ids and
    timestamps never appear (AD-16)."""
    seed = _seed_campaign(id_suffix="A", created_at="2026-01-01T00:00:00Z")
    notes = "  the docks teem with new characters  "
    context_a = _fresh_context()
    context_b = _fresh_context()  # identical truths, fresh ULIDs/timestamps
    first = build_wave2_prompt(seed, notes, context_a, core_count=2)
    assert build_wave2_prompt(seed, notes, context_a, core_count=2) == first
    assert build_wave2_prompt(seed, notes, context_b, core_count=2) == first
    assert build_wave2_prompt(seed, notes.strip(), context_a, core_count=2) == first
    # The serialized context survives the prompt and never leaks ids.
    assert context_a[0][0].id not in first and context_a[0][1].id not in first
    assert seed.id not in first
    # The record contract + stat rules now gate wave 2 as well (2026-09-09).
    assert "CHARACTER RECORDS" in first and "STAT BLOCKS" in first
    assert "WORLD CONTEXT" in first and "entity[0]" in first
    # The context's edges serialize (member_of — the only context edge),
    # while the vocabulary list separately carries every edge type.
    assert "entity[0] -[member_of counter=1]-> entity[1]" in first
    # Wave-2's new entities use N refs (never wave-1's E labels); the contract
    # shows a worked example rather than a placeholder (2026-09-10).
    # and the prompt names the core anchors as C<index>.
    assert '"ref": "N1"' in first and '"ref": "N0"' in first
    assert "N0" in first and "C0..C1" in first


def test_serialize_context_is_hard_truths_only() -> None:
    entities, edges = _fresh_context()
    context = serialize_context(entities, edges)
    assert "entity[0] kind=faction name='The Gilded Bar'" in context
    assert 'data: {"economy": {"level": 3, "resources": ["gold"]}}' in context
    assert "entity[0] -[member_of counter=1]-> entity[1]" in context
    for entity in entities:
        assert entity.id not in context  # no ids
    assert entities[0].created_at not in context  # no timestamps


def test_cancel_mid_wave1_commits_core_then_stops(world: str) -> None:
    """A cancel fired inside the wave-1 provider call commits the wave-1
    core and then stops (post-commit poll): exactly one revision, the job
    stays cancelled, no wave-2 call, no terminal conflict, no progress."""
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            cancel_job(job_id)  # lands during the wave-1 call
        return json.dumps(_wave1_output())

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "cancelled"  # never failed/succeeded — no terminal conflict
    assert len(calls) == 1  # the wave-2 provider call was never made
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1  # wave-1 core committed
        assert {e.name for e in world_entities(session, world)} == {
            "The Gilded Bar",
            "Mira Vane",
        }
    assert job.progress == 0.0  # the post-commit poll stopped before 0.5


def test_cancel_during_wave2_prevents_wave2_commit(world: str) -> None:
    """A cancel fired inside the wave-2 provider call must NOT commit wave
    2 (the failed wave writes nothing): exactly one revision (core only),
    the job stays cancelled, no terminal conflict."""
    responses = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        if len(responses) == 1:  # the wave-2 call
            cancel_job(job_id)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "cancelled"
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1  # wave 2 NOT committed
        assert {e.name for e in world_entities(session, world)} == {
            "The Gilded Bar",
            "Mira Vane",
        }


def test_stale_base_between_waves_rebases_onto_the_edit(world: str) -> None:
    """M4 (the 100-entity cut): a DM edit landing between the waves makes
    wave-2's staged base stale — the runner REBASES once onto the new head
    instead of dying (a long job's generation window is minutes; the
    pre-rebase runner incinerated the whole wave 2 on a hand edit). The
    DM's edit, the core, and wave 2 all stay: three revisions, no silent
    overwrite (AD-2 — the rebase commits ON TOP of the edit)."""
    responses = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]
    dm_committed = False

    def provider(prompt: str, settings: LLMSettings) -> str:
        nonlocal dm_committed
        if len(responses) == 1 and not dm_committed:  # the wave-2 call
            dm_committed = True
            keep_id = ids.new_id()
            with session_scope() as session:
                head = latest_revision(session, world)
                mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
            # The DM edit is a legal commit: FR2's no-orphan rule (spec-2.5)
            # requires the new entity to carry an edge into existing state.
            commit_subgraph(
                world,
                [models.EntityInput(kind="place", name="DM's New Keep", id=keep_id)],
                # Mira (character) located_in her keep (place) — the AD-5
                # vocabulary reads character -> place.
                [models.EdgeInput(src=mira.id, dst=keep_id, type="located_in")],
                base_revision=head.id if head is not None else None,
            )
        return responses.pop(0)

    job_id = _enqueue(world, notes="more world")
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"  # rebased, not failed
    assert job.result is not None and job.result["entity_count"] == 4
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 3  # wave 1 + DM edit + wave 2
        names = {e.name for e in world_entities(session, world)}
    assert {"The Gilded Bar", "Mira Vane", "DM's New Keep"} <= names


# ---------------------------------------------------------------------------
# RETRIEVAL_CAP
# ---------------------------------------------------------------------------


def test_retrieval_cap_and_determinism(world: str) -> None:
    """RETRIEVAL_CAP: a world of 30 entities yields at most 24 context
    entities, rowid-stable and byte-identical across calls."""
    citizen_ids = [ids.new_id() for _ in range(30)]
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="character", name=f"Citizen {i}", id=citizen_ids[i])
            for i in range(30)
        ],
        [
            models.EdgeInput(src=citizen_ids[i], dst=citizen_ids[i + 1], type="ally_of")
            for i in range(29)
        ],
        base_revision=None,
    )
    seeded = retrieve_neighborhood(world, seed_ids=citizen_ids[:3], depth=1, entity_cap=24)
    seeded_again = retrieve_neighborhood(world, seed_ids=citizen_ids[:3], depth=1, entity_cap=24)
    assert [e.id for e in seeded[0]] == [e.id for e in seeded_again[0]]
    assert [e.id for e in seeded[1]] == [e.id for e in seeded_again[1]]
    assert serialize_context(*seeded) == serialize_context(*seeded_again)
    # Seeded neighborhood: seeds first (rowid order), then one hop.
    assert [e.id for e in seeded[0][:4]] == citizen_ids[:4]


def test_retrieval_guards_reject_bad_params(world: str) -> None:
    """depth/cap guards: a depth below 1 or an entity_cap below 1 is a
    ValueError, never a degenerate traversal (AR6 bounds are 1 hop/24)."""
    with pytest.raises(ValueError):
        retrieve_neighborhood(world, depth=0)
    with pytest.raises(ValueError):
        retrieve_neighborhood(world, entity_cap=0)


def test_retrieval_unknown_seeds_rejected(world: str) -> None:
    """A seed list that resolves to no world entity is rejected naming the
    offending seeds — a silently empty neighborhood must not confuse the
    wave-2 caller downstream."""
    fabricated = ids.new_id()
    with pytest.raises(ValueError) as excinfo:
        retrieve_neighborhood(world, seed_ids=[fabricated])
    assert fabricated in str(excinfo.value)


# ---------------------------------------------------------------------------
# UNKNOWN_CURSOR (retro item 2) + parse unit checks
# ---------------------------------------------------------------------------


def test_list_jobs_fabricated_cursor_rejected(world: str) -> None:
    """UNKNOWN_CURSOR: a decode-valid cursor naming no job is a 422-family
    store rejection, never a silent page-1 reset (epic-1 retro item 2)."""
    _enqueue(world, places=["Greymarch"])
    with pytest.raises(InvalidJobInputError):
        list_jobs(world, cursor=ids.new_id())


def test_real_provider_via_mock_transport(world: str) -> None:
    """The production default path (real ``chat_completion`` through a
    bound MockTransport) runs a build-in job end to end — the seam that
    proves the runner's keyword-``settings`` provider calls land (the
    same regression the text path pins in test_worker)."""
    from functools import partial

    import httpx

    from app.providers.llm import chat_completion

    wave1_json = json.dumps(_wave1_output())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": wave1_json}}]})

    provider = partial(chat_completion, transport=httpx.MockTransport(handler))
    job_id = _enqueue(world, places=["Greymarch"])
    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_count"] == 2


# ---------------------------------------------------------------------------
# STAT BLOCKS (spec-2.4, AR24/AR25)
# ---------------------------------------------------------------------------


def test_valid_stat_block_commits_without_repair(world: str) -> None:
    """STAT_OK: a wave-1 character with a valid data.stat_block commits
    as given (round-trips through Entity.data) with a single provider
    call — no repair pass, job succeeded."""
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(_wave1_output())

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 1  # no repair call for a valid block
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"] == _MIRA_STAT_BLOCK


def test_invalid_stat_block_repaired_in_one_pass(world: str) -> None:
    """STAT_INVALID_REPAIRED: a constraint-violating block (STR 40) gets
    exactly one repair call; the repaired block commits and the job
    succeeds with two provider calls total."""
    output = _wave1_output()
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output["entities"][1]["data"] = {
        **_character_record("Mira Vane"),
        "stat_block": bad_block,
    }
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 2  # wave 1 + exactly one repair pass
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"]["identity"]["level"] == 5  # repaired block landed
    assert mira.data["stat_block"]["attributes"]["str"] == 14


def test_missing_stat_block_repaired(world: str) -> None:
    """STAT_MISSING_REPAIRED: a character without data.stat_block is
    flagged 'stat_block section missing'; the repair pass supplies the
    block and the wave commits."""
    output = _wave1_output()
    output["entities"][1]["data"] = _character_record("Mira Vane")  # drop the block
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 2  # wave 1 + exactly one repair pass
    assert "stat_block section missing" in calls[1]  # the flagged violation
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"] == _MIRA_STAT_BLOCK


def test_still_invalid_stat_block_fails_zero_commits(world: str) -> None:
    """STAT_STILL_INVALID: a block still invalid after all THREE bounded
    repair passes is never committed — the job fails naming the character
    and its violations (fail event, AR25), zero revisions. Each repair
    re-reads the block the previous attempt wrote (a shape violation the
    deterministic conform cannot touch: STR 40 is a hard cap)."""
    output = _wave1_output()
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_block}
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    run_next_job(provider=provider, settings=SETTINGS)
    assert len(calls) == 4  # wave 1 + all three bounded repair passes, never a fourth
    assert "VIOLATIONS STILL UNFIXED" in calls[2]
    assert "THIRD REPAIR PASS" in calls[3]
    assert "attributes.str" in calls[2]
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "still invalid after the repair passes" in (job.error or "")
    assert "Mira Vane" in (job.error or "") and "attributes.str" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_second_stat_repair_pass_heals_the_block(world: str) -> None:
    """STAT_SECOND_PASS (owner decision 2026-09-11): when the first repair
    comes back still invalid, the gate spends ONE more call — the prompt
    re-reads the block that first attempt wrote plus the violations that
    survived it — and the healed second repair commits with no third call
    (the loop breaks once nothing is flagged). The trigger is a SHAPE
    violation: power-only misses never reach the passes anymore (the NPC
    oracle retired NPC/BBEG bands; conform-first owns Monster power misses)."""
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output = _wave1_output()
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_block}
    responses = [
        json.dumps(output),
        # First repair: the model changes nothing that matters — still bad.
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
        # Second repair: the bones of the first attempt, the shape fixed.
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 3  # wave 1 + pass 1 + pass 2 — never a third
    assert "VIOLATIONS TO FIX" in calls[1]
    assert "VIOLATIONS STILL UNFIXED" in calls[2]
    # The second pass names exactly what survived: the still-bad block is
    # re-shown with its surviving shape violation, not the original problem.
    assert "attributes.str" in calls[2]
    assert json.dumps(bad_block, sort_keys=True, separators=(",", ":")) in calls[2]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"] == _MIRA_STAT_BLOCK


def test_third_stat_repair_pass_exhausted_fails(world: str) -> None:
    """STAT_THIRD_PASS_EXHAUSTED (repair-sequence spec step 2): three failed
    repair passes plus the deterministic conform still leave a shape
    violation — the job fails naming it, with no fifth call and zero
    commits. The third prompt re-reads the second attempt's block."""
    output = _wave1_output()
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_block}
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    run_next_job(provider=provider, settings=SETTINGS)
    assert len(calls) == 4  # wave 1 + three bounded passes, never a fourth repair
    assert "THIRD REPAIR PASS" in calls[3]
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "attributes.str" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_third_stat_repair_pass_heals_the_block(world: str) -> None:
    """STAT_THIRD_PASS (repair-sequence spec step 2): two still-invalid
    repairs do not fail the job — the gate spends its third and final call,
    whose prompt re-reads the SECOND attempt's block plus the surviving
    violations, and the healed third repair commits. Shape-violation
    trigger (see the second-pass test for why power misses never reach
    the passes)."""
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output = _wave1_output()
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_block}
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 4  # wave 1 + all three passes
    assert "VIOLATIONS TO FIX" in calls[1]
    assert "SECOND REPAIR PASS" in calls[2]
    assert "THIRD REPAIR PASS" in calls[3]
    # The third pass re-reads the second attempt's block (not the wave's)
    # with the violation that survived it.
    assert "attributes.str" in calls[3]
    assert json.dumps(bad_block, sort_keys=True, separators=(",", ":")) in calls[3]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"] == _MIRA_STAT_BLOCK


def test_stat_repair_calls_carry_one_entity_each(world: str) -> None:
    """STAT_PER_ENTITY (repair-sequence spec step 2): with two failing
    characters — one shape-violating block, one missing block — each repair
    call's prompt names exactly ONE flagged ref (the sibling's block is
    untouched by that call) and both healed blocks merge by ref into the
    committed wave."""
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output = _wave1_output()
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_block}
    output["entities"].append(
        {
            "ref": "E2",
            "kind": "character",
            "name": "Bosun Cobb",
            "data": _character_record("Bosun Cobb"),  # missing stat_block
        }
    )
    responses = [json.dumps(output)]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira", "Cobb"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return responses.pop(0)
        # Per-entity repair: answer exactly the single ref this prompt names.
        assert ("E1 (" in prompt) != ("E2 (" in prompt)
        ref = "E1" if "E1 (" in prompt else "E2"
        return json.dumps({"stat_blocks": [{"ref": ref, "stat_block": _MIRA_STAT_BLOCK}]})

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 3  # wave 1 + one repair call per failing entity
    assert "E2" not in calls[1]  # the E1 call never names its sibling
    assert "E1" not in calls[2]  # nor the E2 call
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        by_name = {e.name: e for e in world_entities(session, world)}
    assert by_name["Mira Vane"].data["stat_block"] == _MIRA_STAT_BLOCK
    assert by_name["Bosun Cobb"].data["stat_block"] == _MIRA_STAT_BLOCK


def test_structured_damage_carries_a_block_without_prose_dice(world: str) -> None:
    """STRUCTURED_DAMAGE end-to-end (spec 2026-09-11): an attack whose
    damage lives in structured parts and whose prose states no dice is a
    valid, in-band block — pre-change the auditor read zero damage from it
    and the gate failed the whole wave. The committed block keeps the
    parts, and no repair call is spent."""
    block = {
        "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter"},
        "attributes": {"str": 18, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 66},
        "skills": [{"name": "Athletics", "bonus": 5}],
        "actions": [
            {
                "name": "Longsword",
                "to_hit": 7,
                "description": "Swings wide, trailing sea-light.",
                "damage": [{"dice": "4d10", "bonus": 5, "type": "slashing"}],
            }
        ],
    }
    output = _wave1_output()
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": block}
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(output)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 1  # the block was valid — no repair pass burned
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    committed = mira.data["stat_block"]
    assert validate_stat_block(committed) == []
    parts = committed["actions"][0]["damage"]
    assert parts[0]["count"] == 4 and parts[0]["sides"] == 10
    assert parts[0]["average"] == 27  # 4 * 5.5 + 5, derived by the canonicalizer
    assert combat.audit_stat_block(committed).band is not None


def test_repaired_block_parts_are_canonicalized(world: str) -> None:
    """A repair response is model output like any other: its damage parts
    are re-canonicalized before the re-check, so the committed block's
    ``average`` always agrees with its own dice (live 2026-09-11: a repair
    shipped 2d6 + 13 with average 16, and the auditor trusts the number).
    The flagged trigger is a MISSING block (whole-block scope), so the
    repaired block lands whole and then canonicalizes."""
    repaired = {
        "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter"},
        "attributes": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 66},
        "actions": [
            {
                "name": "Longsword",
                "to_hit": 6,
                "description": "Melee Weapon Attack: +6 to hit, 35 (4d10 + 5) slashing",
                "damage": [{"dice": "4d10", "count": 4, "sides": 10, "bonus": 5, "average": 16}],
            }
        ],
    }
    output = _wave1_output()
    output["entities"][1]["data"] = _character_record("Mira Vane")  # stat_block missing
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": repaired}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        return responses.pop(0)

    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    part = mira.data["stat_block"]["actions"][0]["damage"][0]
    assert part["average"] == 27  # 4 * 5.5 + 5, not the model's 16
    assert part["count"] == 4 and part["sides"] == 10 and part["bonus"] == 5


def test_stat_repair_budget_exceeded_fails_before_http(world: str) -> None:
    """STAT_REPAIR_BUDGET: max_llm_calls=1 with an invalid block — the
    repair call is refused before HTTP (AR21); the job fails and nothing
    commits."""
    output = _wave1_output()
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_block}
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"], max_llm_calls=1)
    run_next_job(provider=lambda prompt, settings: json.dumps(output), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "budget" in (job.error or "").lower()
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_stat_repair_bad_ref_fails(world: str) -> None:
    """STAT_REPAIR_BAD_REF: the repair response refs a position that was
    not flagged (or an unknown one) — the job fails, zero commits."""
    output = _wave1_output()
    output["entities"][1]["data"] = _character_record("Mira Vane")  # missing stat_block
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E0", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "was not flagged" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_stat_repair_fence_wrapped_succeeds(world: str) -> None:
    """STAT_REPAIR_FENCE: a markdown-fenced repair output is stripped
    before parsing (same tolerance as wave output)."""
    output = _wave1_output()
    output["entities"][1]["data"] = _character_record("Mira Vane")
    repair = (
        "```json\n"
        + json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]})
        + "\n```"
    )
    responses = [json.dumps(output), repair]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    processed = run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert "stat_block" in mira.data


def test_wave2_characters_require_full_records(world: str) -> None:
    """WAVE2_RECORDS (dogfood fix 2026-09-09): wave-2 characters carry the
    same full AR24 record + stat block as wave 1 — a bare character gets
    exactly one bounded record repair, and one still incomplete after it
    fails the wave naming the entity and the missing section (the wave-1
    core stays), while a complete one commits with the record."""
    bare = _wave2_output()
    bare["entities"][1] = {"ref": "N1", "kind": "character", "name": "Captain Harlow"}
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(bare),
        # one repair pass, still incomplete (personality only) -> fail
        json.dumps({"records": [{"ref": "E1", "data": {"personality": "gruff dockmaster"}}]}),
    ]
    job_id = _enqueue(world, notes="the docks teem with Captain Harlow")
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "wave 2" in (job.error or "")
    assert "Captain Harlow" in (job.error or "") and "appearance" in (job.error or "")
    with session_scope() as session:
        # the seed is not a revision (AD-1 exception): wave 1 alone.
        assert len(list(revision_chain(session, world))) == 1  # wave-1 only

    responses2 = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]
    job_id2 = _enqueue(world, notes="the docks teem with the Drowned Rat and Captain Harlow")
    processed = run_next_job(
        provider=lambda prompt, settings, responses=responses2: responses.pop(0),
        settings=SETTINGS,
    )
    assert processed == job_id2
    job2, _position = job_status(job_id2)
    assert job2.state == "succeeded"
    assert job2.result is not None and job2.result["entity_count"] == 4
    with session_scope() as session:
        harlow = next(e for e in world_entities(session, world) if e.name == "Captain Harlow")
    assert harlow.data["appearance"]  # the record committed…
    assert harlow.data["stat_block"] == _MIRA_STAT_BLOCK  # …with the stat block


def test_wave1_records_repaired_in_one_pass(world: str) -> None:
    """RECORD_REPAIR (2026-09-09 live: gemma shipped a wave-1 subgraph with
    NO records): a character lacking the AR24 record gets exactly one
    bounded repair pass and commits with the merged record. A stat_block in
    the repair patch is discarded — the record gate never touches stats."""
    output = _wave1_output()
    output["entities"][1]["data"] = {"stat_block": _MIRA_STAT_BLOCK}  # no record
    responses = [
        json.dumps(output),
        json.dumps(
            {
                "records": [
                    {
                        "ref": "E1",
                        "data": {**_character_record("Mira Vane"), "stat_block": {"junk": True}},
                    }
                ]
            }
        ),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 2  # wave 1 + the record repair; stat gate stays silent
    assert "CHARACTER RECORDS" in calls[1] and "Mira Vane" in calls[1]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["appearance"]  # the repaired record committed…
    assert mira.data["stat_block"] == _MIRA_STAT_BLOCK  # …untouched by the patch


def test_reaction_matrix_object_canonicalized_on_build_in(world: str) -> None:
    """REACTION_MATRIX_NORMALIZE, build-in (dogfood 2026-09-09): the
    compact model ships ``world_integration.reaction_matrix`` as a
    ``{"C<index>": "<reaction>"}`` mapping even on the build-in path —
    the record is canonicalized to the string form BEFORE the record gate,
    so a wave commits its full AR24 record without burning the repair
    pass; a mapping arriving inside the repair patch is canonicalized on
    the merge the same way."""
    output = _wave1_output()
    mapping = {
        "C0": "Wary; watches the bar for Guild spies.",
        "C1": "Hostile; owes her brother's claim.",
    }
    output["entities"][1]["data"] = {
        **_character_record(
            "Mira Vane",
            world_integration={
                "reputation": "Known to the Guild.",
                "factions": "The Gilded Bar (member).",
                "current_location": "The Gilded Bar, back room.",
                "reaction_matrix": mapping,
                "on_defeat": "Flees to the harbor with the ledger.",
            },
        ),
        "stat_block": _MIRA_STAT_BLOCK,
    }
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(output)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 1  # canonicalized up front: NO record repair pass
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    matrix = mira.data["world_integration"]["reaction_matrix"]
    assert matrix.startswith("C0: Wary; watches the bar for Guild spies., ")
    assert "C1: Hostile; owes her brother's claim." in matrix


def test_missing_entity_name_repaired_in_one_pass(world: str) -> None:
    """NAME_REPAIR (dogfood 2026-09-09 live: gemma shipped wave-1 with a
    fully-detailed character that had NO name field at all — the old
    structural check hard-failed the whole wave on 'entity N name must
    be a non-blank string'). A nameless entity of ANY kind (here a
    faction AND a character) gets exactly one bounded repair pass; the
    repaired name lands on the entity level and — for a character — in
    the record too; the fully-repaired wave commits with no further
    repair calls (record/stat gates stay silent)."""
    output = _wave1_output()
    # The 2026-09-09 gemma shape: no name on the entity NOR in the record.
    del output["entities"][0]["name"]  # faction: The Gilded Bar
    del output["entities"][1]["name"]  # character: Mira Vane
    del output["entities"][1]["data"]["name"]
    responses = [
        json.dumps(output),
        json.dumps(
            {
                "names": [
                    {"ref": "E0", "name": "The Gilded Bar"},
                    {"ref": "E1", "name": "Barkeep Whostbos"},
                ]
            }
        ),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 2  # wave 1 + the name repair; record/stat gates silent
    assert "names" in calls[1] and "E0 (faction)" in calls[1] and "E1 (character)" in calls[1]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        by_name = {e.name: e for e in world_entities(session, world)}
    assert "The Gilded Bar" in by_name  # faction: entity name repaired, no record
    assert "name" not in by_name["The Gilded Bar"].data
    barkeep = by_name["Barkeep Whostbos"]
    assert barkeep.kind == "character"
    assert barkeep.data["name"] == "Barkeep Whostbos"  # record name matches (contract)


def test_missing_entity_name_falls_back_to_record_name(world: str) -> None:
    """NAME_FALLBACK (dogfood 2026-09-09): a character whose entity-level
    name is missing but whose RECORD carries a name (the CHARACTER RECORDS
    contract requires data.name == entity name — the model clearly meant
    it) is recovered WITHOUT a repair call: the record name becomes the
    entity name before any gate runs."""
    output = _wave1_output()
    del output["entities"][1]["name"]  # data (record) still has "name": "Mira Vane"
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(output)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 1  # no repair pass: the record name was recovered
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.kind == "character" and mira.data["name"] == "Mira Vane"


def test_name_repair_blank_or_missing_ref_fails(world: str) -> None:
    """NAME_REPAIR_STRICT: the name repair response must list exactly the
    flagged refs with non-blank string names — a blank name or an empty
    'names' list fails the job, zero commits (same strictness as the
    record repair)."""
    output = _wave1_output()
    del output["entities"][1]["name"]
    del output["entities"][1]["data"]["name"]
    responses = [
        json.dumps(output),
        json.dumps({"names": [{"ref": "E1", "name": "   "}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "name repair: ref E1 name must be a non-blank string" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []

    responses2 = [json.dumps(output), json.dumps({"names": []})]
    job_id2 = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses2: responses.pop(0),
        settings=SETTINGS,
    )
    job2, _position = job_status(job_id2)
    assert job2.state == "failed"
    assert "name repair: missing repaired names for E1" in (job2.error or "")


def test_wave2_nameless_character_repaired(world: str) -> None:
    """NAME_REPAIR_WAVE2: the name gate is parity-gated on wave 2 — a
    wave-2 character with no name gets one bounded repair pass (labelled
    E<position>, the repair convention shared with the record gate) and
    commits with the name; the wave-1 core stays untouched."""
    output2 = _wave2_output()
    del output2["entities"][1]["name"]
    del output2["entities"][1]["data"]["name"]
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(output2),
        json.dumps({"names": [{"ref": "E1", "name": "Captain Harlow"}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, notes="the docks teem with Captain Harlow")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 3  # wave 1 + wave 2 + the name repair
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_count"] == 4
    with session_scope() as session:
        harlow = next(e for e in world_entities(session, world) if e.name == "Captain Harlow")
    assert harlow.kind == "character" and harlow.data["name"] == "Captain Harlow"


def test_record_repair_malformed_json_retried_once(world: str) -> None:
    """RECORD_REPAIR_RETRY (dogfood 2026-09-09 live: the user's build_in
    failed with 'record repair: output is not valid JSON (Expecting ':'
    delimiter ...)' — gemma's repair response embedded an excluded
    stat_block whose traits/actions members were bare strings: invalid
    JSON). A non-parseable repair response gets exactly one retry that
    feeds the invalid text back; the valid retry commits — the merge
    discards any stat_block the retry carries."""
    output = _wave1_output()
    output["entities"][1]["data"] = {"stat_block": _MIRA_STAT_BLOCK}  # no record -> gate fires
    malformed = (
        '{"records": [{"ref": "E1", "data": {'
        '"role": "NPC", "level_cr": "level 5", '
        '"traits": {"Tavern Keep: Can identify poisons.", "Steady Hand: Advantage on checks."}, '
        '"actions": {"Heavy Mug: +4 to hit, 1d6+2 bludgeoning."}, '
        '"world_integration": {"reaction_matrix": "C0: fine"}}}]}'
    )
    responses = [
        json.dumps(output),
        malformed,  # invalid JSON -> retry
        json.dumps({"records": [{"ref": "E1", "data": {**_character_record("Mira Vane")}}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 3  # wave 1 + record repair + the JSON retry
    assert "YOUR PREVIOUS INVALID RESPONSE" in calls[2]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["appearance"]  # the retried record committed…
    assert mira.data["stat_block"] == _MIRA_STAT_BLOCK  # …stat_block untouched by repairs


def test_record_repair_malformed_twice_fails(world: str) -> None:
    """RECORD_REPAIR_RETRY_EXHAUSTED: two consecutive malformed repair
    responses fail the job with a clear message; zero commits."""
    output = _wave1_output()
    output["entities"][1]["data"] = {"stat_block": _MIRA_STAT_BLOCK}
    malformed = '{"records": [{"ref": "E1", "data": {"traits": {"T: x", "S: y"}}}]}'
    responses = [json.dumps(output), malformed, malformed]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "record repair: output was not valid JSON after one retry" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_record_repair_prose_wrapped_json_rescued(world: str) -> None:
    """RECORD_REPAIR_EXTRACT: prose around the repair JSON (the model
    wraps the object in a sentence) is stripped by the balanced-object
    extraction — no retry is burned."""
    output = _wave1_output()
    output["entities"][1]["data"] = {"stat_block": _MIRA_STAT_BLOCK}
    wrapped = (
        "Here is the completed record:\n"
        + json.dumps({"records": [{"ref": "E1", "data": {**_character_record("Mira Vane")}}]})
        + "\nHope this helps!"
    )
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(output)
        return wrapped

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 2  # wave 1 + record repair (no retry)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"


def test_name_repair_malformed_json_retried_once(world: str) -> None:
    """NAME_REPAIR_RETRY: the name gate gets the same one JSON retry."""
    output = _wave1_output()
    del output["entities"][1]["name"]
    del output["entities"][1]["data"]["name"]
    responses = [
        json.dumps(output),
        '{"names": [{"ref": "E1", "name": "Mira" Vane"}]}',  # unescaped quote: invalid
        json.dumps({"names": [{"ref": "E1", "name": "Mira Vane"}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 3  # wave 1 + name repair + the JSON retry
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.kind == "character" and mira.data["name"] == "Mira Vane"


def test_stat_repair_malformed_json_retried_once(world: str) -> None:
    """STAT_REPAIR_RETRY: the stat gate gets the same one JSON retry — a
    malformed stat-repair response (bare-string object members) is
    re-elicited once, then the valid block commits."""
    output = _wave1_output()
    bad_block = {**_MIRA_STAT_BLOCK, "attributes": {**_MIRA_STAT_BLOCK["attributes"], "str": 40}}
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_block}
    responses = [
        json.dumps(output),
        '{"stat_blocks": [{"ref": "E1", "stat_block": {"traits": {"X: y"}}}]}',  # invalid JSON
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 3  # wave 1 + stat repair + the JSON retry
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"] == _MIRA_STAT_BLOCK


def test_record_repair_mapping_matrix_canonicalized_on_merge(world: str) -> None:
    """REPAIR_MERGE_CANONICALIZE (review 2026-09-09): a mapping-form
    reaction_matrix arriving INSIDE the record-repair patch is canonicalized
    on the merge — the re-check sees the string form and the wave commits.
    The upfront canonicalization never runs here (the wave output carries
    no record, so the gate fires); without the merge line this fails.
    Non-C<index> keys are dropped on the merge."""
    output = _wave1_output()
    output["entities"][1]["data"] = {"stat_block": _MIRA_STAT_BLOCK}  # no record -> gate fires
    record = _character_record("Mira Vane")
    record["world_integration"] = {
        **record["world_integration"],
        "reaction_matrix": {"C0": "Wary; watches the bar.", "hello": "not a ref"},
    }
    responses = [
        json.dumps(output),
        json.dumps({"records": [{"ref": "E1", "data": record}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 2  # wave 1 + record repair (parseable: no retry)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["world_integration"]["reaction_matrix"] == "C0: Wary; watches the bar."


def test_name_repair_malformed_twice_fails(world: str) -> None:
    """NAME_REPAIR_RETRY_EXHAUSTED: two consecutive malformed name repairs
    fail the job with a clear message; zero commits."""
    output = _wave1_output()
    del output["entities"][1]["name"]
    del output["entities"][1]["data"]["name"]
    malformed = '{"names": [{"ref": "E1", "name": "Mira" Vane"}]}'  # unescaped quote: invalid
    responses = [json.dumps(output), malformed, malformed]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "name repair: output was not valid JSON after one retry" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_stat_repair_malformed_twice_fails(world: str) -> None:
    """STAT_REPAIR_RETRY_EXHAUSTED: two consecutive malformed stat repairs
    fail the job with a clear message; zero commits (never a silent drop
    on the build-in path)."""
    output = _wave1_output()
    bad_block = {**_MIRA_STAT_BLOCK, "attributes": {**_MIRA_STAT_BLOCK["attributes"], "str": 40}}
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_block}
    malformed = '{"stat_blocks": [{"ref": "E1", "stat_block": {"traits": {"X: y"}}}]}'
    responses = [json.dumps(output), malformed, malformed]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "stat repair: output was not valid JSON after one retry" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_record_repair_huge_ref_fails_as_contract(world: str) -> None:
    """HUGE_REF: a 5000-digit repair ref fails as a contract violation —
    never a raw ValueError escaping the job-error channel as a 500."""
    output = _wave1_output()
    output["entities"][1]["data"] = {"stat_block": _MIRA_STAT_BLOCK}
    huge = "E" + "9" * 5000
    responses = [
        json.dumps(output),
        json.dumps({"records": [{"ref": huge, "data": {**_character_record("Mira Vane")}}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "record repair: ref must be E<position>" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_record_repair_missing_ref_fails(world: str) -> None:
    """RECORD_REPAIR_MISSING_REF: the repair response must list exactly the
    flagged refs — an empty 'records' list fails the job, zero commits."""
    output = _wave1_output()
    output["entities"][1]["data"] = {"stat_block": _MIRA_STAT_BLOCK}
    responses = [json.dumps(output), json.dumps({"records": []})]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "missing repaired records for E1" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []

    responses2 = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]
    job_id2 = _enqueue(world, notes="the docks teem with the Drowned Rat and Captain Harlow")
    processed = run_next_job(
        provider=lambda prompt, settings, responses=responses2: responses.pop(0),
        settings=SETTINGS,
    )
    assert processed == job_id2
    job2, _position = job_status(job_id2)
    assert job2.state == "succeeded"
    assert job2.result is not None and job2.result["entity_count"] == 4
    with session_scope() as session:
        harlow = next(e for e in world_entities(session, world) if e.name == "Captain Harlow")
    assert harlow.data["appearance"]  # the record committed…
    assert harlow.data["stat_block"] == _MIRA_STAT_BLOCK  # …with the stat block


def test_monster_with_level_repaired_in_one_pass(world: str) -> None:
    """STAT_INVALID_REPAIRED, monster-with-level variant of the matrix
    row: a key figure rendered as Monster carrying `level` violates the
    role-limited semantics; one repair pass replaces it with a CR block."""
    bad_monster = {
        "identity": {"role": "Monster", "level": 5, "race": "Goblin", "alignment": "unaligned"},
        "attributes": {"str": 8, "dex": 14, "con": 10, "int": 9, "wis": 11, "cha": 8},
        "combat": {"ac": 15, "hp": 7},
    }
    fixed_monster = {
        "identity": {"role": "Monster", "cr": "1/4", "race": "Goblin", "alignment": "unaligned"},
        "attributes": {"str": 8, "dex": 14, "con": 10, "int": 9, "wis": 11, "cha": 8},
        "combat": {"ac": 15, "hp": 18},
    }
    output = _wave1_output()
    output["entities"][1]["data"] = {
        **_character_record(
            "Mira Vane",
            role="Monster",
            level_cr="CR 5",
            race_type="Goblin",
            class_profession="Goblin warband",
            alignment="NE",
            boss=dict(_BOSS_SECTION),
        ),
        "stat_block": bad_monster,
    }
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": fixed_monster}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 2
    assert "identity.level is not allowed for role Monster" in calls[1]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"]["identity"]["cr"] == "1/4"


def test_classless_spells_repaired_and_frail_tiny_conformed(world: str) -> None:
    """SPELLS_TINY_VALVE_LOOP (spec-stat-repair-spells-tiny review, updated
    for the NPC-oracle cut): Mira keeps evocative spells with no coupling
    class — a SHAPE violation, so the model's one repair call per failing
    entity still owns it (the EDIT SCOPE fix — a cr rewrite riding with the
    repair is drift the gate strips). Boo the hamster (CR 1/4, hp 15, one
    weak bite) trips frail + under-powered — power-only Monster misses go
    to the deterministic conform BEFORE any pass (conform-first), so Boo
    costs ZERO calls and lands in its CR band exactly. Two provider calls
    total: wave 1 plus Mira's repair, its prompt naming only her block."""
    bad_spells = dict(_MIRA_STAT_BLOCK)
    bad_spells["identity"] = {k: v for k, v in _MIRA_STAT_BLOCK["identity"].items() if k != "class"}
    bad_spells["spells"] = ["Fireball", "Magic Missile"]
    fixed_spells = {
        **bad_spells,
        "identity": {**bad_spells["identity"], "class": "Wizard"},
    }
    tiny_attributes = {"str": 8, "dex": 14, "con": 10, "int": 9, "wis": 11, "cha": 8}
    bad_tiny = {
        "identity": {"role": "Monster", "cr": "1/4", "race": "Hamster", "alignment": "unaligned"},
        "attributes": dict(tiny_attributes),
        "combat": {"ac": 13, "hp": 15},
        "actions": [
            {"name": "Bite", "description": "Melee Weapon Attack: +3 to hit, 1d4+1 piercing"}
        ],
    }
    # No fixed_tiny fixture: Boo's power-only miss never reaches a repair
    # call — the deterministic conform lands it (conform-first).
    output = _wave1_output()
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_spells}
    output["entities"].append(
        {
            "ref": "E2",
            "kind": "character",
            "name": "Boo",
            "data": {
                **_character_record(
                    "Boo",
                    role="Monster",
                    level_cr="CR 1/4",
                    race_type="Hamster",
                    class_profession="Cheese thief",
                    alignment="unaligned",
                    boss=dict(_BOSS_SECTION),
                ),
                "stat_block": bad_tiny,
            },
        }
    )
    output["edges"].extend(
        [
            {"src": "E0", "dst": "E2", "type": "member_of", "counter": 1},
            {"src": "E2", "dst": "E0", "type": "debt", "counter": 3},
        ]
    )
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": fixed_spells}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 2  # wave 1 + Mira's shape repair — Boo cost zero calls
    assert "spells require identity.class" in calls[1]
    assert "E2 (" not in calls[1]  # the E1 call never names its sibling
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        by_name = {e.name: e for e in world_entities(session, world)}
    assert by_name["Mira Vane"].data["stat_block"]["identity"]["class"] == "Wizard"
    assert by_name["Mira Vane"].data["stat_block"]["spells"] == ["Fireball", "Magic Missile"]
    boo = by_name["Boo"].data["stat_block"]
    assert boo["identity"]["cr"] == "1/4"  # identity never moves
    assert boo["actions"][0]["name"] == "Bite"  # the model's action survives
    assert validate_stat_block(boo) == []
    hp_band = combat.hp_band(boo["identity"])
    assert hp_band is not None and not combat.is_hp_frail(boo["combat"]["hp"], hp_band)
    audit = combat.audit_stat_block(boo)
    # 3.5 DPR against the CR 1/4 band 4-5 is on-target (0.8x tolerance) —
    # the conform fixed the frail floor, not a band miss that never existed.
    assert audit.verdict == combat.VERDICT_ONTARGET


def test_cancel_before_stat_repair_is_noop(world: str) -> None:
    """STAT_CANCEL_BEFORE_REPAIR: a cancel landing between wave-1
    validation and the repair call is a no-op — no repair call, no wave-1
    commit, the job stays cancelled."""
    output = _wave1_output()
    output["entities"][1]["data"] = _character_record("Mira Vane")  # flagged for repair
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        cancel_job(job_id)  # lands during the wave-1 call, before the poll
        return json.dumps(output)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 1  # the repair call was never made
    job, _position = job_status(job_id)
    assert job.state == "cancelled"  # never failed/succeeded
    with session_scope() as session:
        assert revision_chain(session, world) == []  # no wave-1 commit


def test_faction_stat_block_is_stripped(world: str) -> None:
    """Only characters carry stat blocks (spec-2.4 review decision): a
    faction shipping data.stat_block has the key stripped before commit —
    tolerated, never fatal, so 2.6/2.7 can never see a stats-bearing
    faction."""
    output = _wave1_output()
    output["entities"][0]["data"] = {"stat_block": {"nonsense": True}}
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(output)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 1  # stripping is silent: no repair churn
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        bar = next(e for e in world_entities(session, world) if e.name == "The Gilded Bar")
    assert "stat_block" not in bar.data


def test_flat_record_is_relocated_not_dropped() -> None:
    """A model that writes the record BESIDE ``data`` has still written it.

    Dropping it is what let the record gate re-invent a different character
    entirely (dogfood 2026-09-11: a key figure submitted as "the hero
    paladin sanberi" committed as a level-2 Cartographer, because its wave-1
    record sat one level too high and ``data`` was silently {})."""
    parsed = {
        "entities": [
            {
                "ref": "E0",
                "kind": "character",
                "name": "Sanberi",
                # Every record key written flat, exactly as the model does it.
                "role": "NPC",
                "level_cr": "level 18",
                "race_type": "Human",
                "class_profession": "Paladin",
                "alignment": "LG",
                "personality": "stoic",
                "secret": "fuelled by a shard",
                "stat_block": {"identity": {"role": "NPC", "level": 18}},
                "world_integration": {"reputation": "a symbol of hope"},
            },
            {"ref": "E1", "kind": "place", "name": "City of Gallorb", "text": "soot and copper"},
        ],
        "edges": [{"src": "E0", "dst": "E1", "type": "located_in", "counter": 1}],
    }
    entities, _edges = _validate_subgraph(1, parsed)
    sanberi = entities[0]
    assert sanberi.kind == "character"
    assert sanberi.name == "Sanberi"
    # The record survived the trip instead of vanishing into an empty data.
    assert sanberi.data["class_profession"] == "Paladin"
    assert sanberi.data["level_cr"] == "level 18"
    assert sanberi.data["race_type"] == "Human"
    assert sanberi.data["secret"] == "fuelled by a shard"
    assert sanberi.data["stat_block"] == {"identity": {"role": "NPC", "level": 18}}
    assert sanberi.data["world_integration"] == {"reputation": "a symbol of hope"}
    assert sanberi.data["name"] == "Sanberi"  # record name mirrors the entity
    # A flat place carries no record keys: nothing is invented for it.
    assert entities[1].data == {}


def test_nested_record_wins_over_a_flat_duplicate() -> None:
    """When a model writes both, the nested form is the contract."""
    parsed = {
        "entities": [
            {
                "ref": "E0",
                "kind": "character",
                "name": "Mira",
                "role": "BBEG",
                "data": {"role": "NPC", "level_cr": "level 3"},
            },
            {"ref": "E1", "kind": "place", "name": "Greymarch"},
        ],
        "edges": [{"src": "E0", "dst": "E1", "type": "located_in", "counter": 1}],
    }
    entities, _edges = _validate_subgraph(1, parsed)
    assert entities[0].data["role"] == "NPC"
    assert entities[0].data["level_cr"] == "level 3"


def test_entity_kind_slips_are_canonicalized() -> None:
    """``kind`` carries the contract kind, but models slip two unambiguous
    ways: a case variant of a contract kind, and the ROLE word ("monster" —
    every entry of ROLES is a character-kind entity). Both fold; anything
    else is still rejected. Dogfood 2026-09-10: ``kind: "monster"`` failed
    every build-in at wave 2."""
    assert canonicalize_entity_kind("character") == ("character", None)
    assert canonicalize_entity_kind("faction") == ("faction", None)
    assert canonicalize_entity_kind("Place") == ("place", None)
    assert canonicalize_entity_kind("MONSTER") == ("character", "Monster")
    assert canonicalize_entity_kind("monster") == ("character", "Monster")
    assert canonicalize_entity_kind(" BBEG ") == ("character", "BBEG")
    # Not a kind and not a role — the wave still fails loudly.
    assert canonicalize_entity_kind("dragon") is None
    assert canonicalize_entity_kind(None) is None
    assert canonicalize_entity_kind(7) is None


def test_role_word_kind_seeds_the_record_role() -> None:
    """A role written in ``kind`` is folded to a character AND seeded into
    data.role, so the record the gates read keeps what the model meant."""
    parsed = {
        "entities": [
            {"ref": "E0", "kind": "monster", "name": "The Doom", "text": "a giant"},
            {"ref": "E1", "kind": "place", "name": "Gallorb"},
        ],
        "edges": [{"src": "E0", "dst": "E1", "type": "located_in", "counter": 1}],
    }
    entities, edges = _validate_subgraph(1, parsed)
    assert len(edges) == 1
    doom = entities[0]
    assert doom.kind == "character"
    assert doom.data["role"] == "Monster"


def test_wave_duplicate_edge_rows_collapse_to_one() -> None:
    """EDGE_DEDUP (ladder rung 25): a byte-identical edge row emitted twice
    (model stutter — E24 -> E11 relationship x2 live) collapses to one at
    the boundary instead of failing the wave at the store's AD-23 backstop.
    Same pair with a different type or counter is NOT a duplicate."""
    output = _wave1_output()
    output["edges"].append(dict(output["edges"][0]))
    output["edges"].append({"src": "E0", "dst": "E1", "type": "member_of", "counter": 2})
    _entities, edges = _validate_subgraph(1, output)
    assert [(edge.src, edge.dst, edge.type, edge.counter) for edge in edges] == [
        (edges[0].src, edges[0].dst, "member_of", 1),
        (edges[1].src, edges[1].dst, "debt", 3),
        (edges[2].src, edges[2].dst, "member_of", 2),
    ]


def test_conform_power_fixes_dpr_and_frail_hp() -> None:
    """The deterministic conform repairs both power violations the model's
    repair passes do not converge on: damage under the band and an HP floor
    below half the band low (live 2026-09-10: 5.5 vs 15-20, 50 vs 93-98).
    Monster-only since the NPC oracle (owner verdict 2026-09-12) — an
    NPC/BBEG block is never band-conformed. The model's own action,
    identity and prose survive — only the numbers move."""
    block: dict[str, Any] = {
        "identity": {"role": "Monster", "cr": 2, "race": "Humanoid"},
        "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 14, "hp": 20},
        "skills": [],
        "traits": [],
        "spells": [],
        "actions": [
            {
                "name": "Dagger",
                "description": (
                    "Melee Weapon Attack: +4 to hit. Hit: 5.5 (1d4 + 3) piercing damage."
                ),
            }
        ],
    }
    violations = validate_stat_block(block)
    assert is_conformable(violations)  # a DPR miss AND a frail HP floor
    conformed = conform_power(block)
    assert conformed is not None
    assert validate_stat_block(conformed) == []
    audit = combat.audit_stat_block(conformed)
    assert audit.band is not None and audit.band[0] <= audit.dpr <= audit.band[1]
    hp_band = combat.hp_band(block["identity"])
    assert hp_band is not None and not combat.is_hp_frail(conformed["combat"]["hp"], hp_band)
    assert conformed["actions"][0]["name"] == "Dagger"
    assert conformed["identity"] == block["identity"]
    # Shape problems are never this pass's job — they stay the model's.
    assert not is_conformable(["skills entries must be objects with a 'name'"])
    assert not is_conformable(["combat.hp must be a positive integer"])
    assert not is_conformable([])


def _combat_shape_block(actions: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "identity": {"role": "NPC", "level": 17, "race": "Half-Elf", "class": "Paladin"},
        "attributes": {"cha": 20, "con": 18, "dex": 12, "int": 14, "str": 22, "wis": 16},
        "combat": {"ac": 21, "hp": 285},
        "skills": [],
        "traits": [],
        "spells": [],
        "actions": actions,
    }


def test_a_fighting_block_with_no_readable_damage_is_not_exempt() -> None:
    """The non-combatant exemption is for creatures with NO attacks, not for
    attacks the auditor cannot read. Measured 2026-09-11: a level-17 paladin
    shipped with "makes one melee attack… deals massive radiant damage", the
    audit read ZERO damage, and the whole shallow block passed untouched.
    The readability violation fires for EVERY role; the deterministic arm
    runs for Monsters only — an NPC's prose-only attack belongs to the
    model's repair passes (the conform never lifts a non-Monster block)."""
    prose_only = _combat_shape_block(
        [
            {
                "name": "Multiattack",
                "description": "Sanberi makes four attacks with their Holy Smite.",
            },
            {
                "name": "Holy Smite",
                "description": (
                    "Sanberi makes one melee attack. On a hit, it deals massive radiant damage."
                ),
            },
        ]
    )
    violations = validate_stat_block(prose_only)
    assert violations and violations[0].startswith("no readable damage for level 17")
    assert is_conformable(violations)  # the prefix is conformable in principle
    # NPC oracle: the conform refuses the NPC block — readability is the
    # repair passes' job for a non-Monster.
    assert conform_power(prose_only) is None
    # The same block declared as a Monster arms deterministically.
    monster = {
        **prose_only,
        "identity": {"role": "Monster", "cr": 17, "race": "Half-Elf", "class": "Paladin"},
    }
    monster_violations = validate_stat_block(monster)
    assert monster_violations and monster_violations[0].startswith("no readable damage for CR 17")
    armed = conform_power(monster)
    assert armed is not None
    assert validate_stat_block(armed) == []
    audit = combat.audit_stat_block(armed)
    assert audit.band is not None and audit.band[0] <= audit.dpr <= audit.band[1]
    # Armed, not rewritten: the model's own sentence survives with a damage
    # clause appended, and the routine still counts it.
    assert "deals massive radiant damage plus" in armed["actions"][1]["description"]
    # The scores came up to the row for a CR-17 creature.
    assert armed["attributes"]["cha"] == 26
    assert armed["attributes"]["str"] == 28


def test_a_true_non_combatant_stays_exempt() -> None:
    """A creature with no attack-shaped action keeps the exemption — the
    whole point of it (a scholar must not be force-armed)."""
    scholar = _combat_shape_block(
        [{"name": "Lay on Hands", "description": "Restores 20 hit points to a touched ally."}]
    )
    scholar["identity"] = {"role": "NPC", "level": 5, "race": "Human", "class": "Cleric"}
    assert validate_stat_block(scholar) == []


def test_stray_damage_field_is_folded_into_the_description() -> None:
    """The model sometimes puts the dice in a sibling ``damage`` key the
    auditor never reads — the block then reports ZERO damage, the
    non-combatant exemption swallows it, and it ships with no usable offence
    and no damage on the exported token (measured 2026-09-11, regenerate at
    level 17)."""
    block = {
        "identity": {"role": "NPC", "level": 17, "race": "Half-Elf", "class": "Paladin"},
        "attributes": {"cha": 20, "con": 18, "dex": 12, "int": 14, "str": 22, "wis": 16},
        "combat": {"ac": 21, "hp": 285},
        "skills": [],
        "traits": [],
        "spells": [],
        "actions": [
            {
                "name": "Multiattack",
                "description": "Sanberi makes four attacks with their Holy Smite.",
            },
            {
                "name": "Holy Smite",
                "damage": "5d10+6 radiant",
                "description": (
                    "Sanberi makes one melee attack. On a hit, it deals massive radiant damage."
                ),
            },
        ],
    }
    entity = models.EntityInput(
        kind="character", name="Sanberi", text=None, data={"stat_block": block}, id=None
    )
    out = canonicalize_action_damage([entity])
    action = out[0].data["stat_block"]["actions"][1]
    assert "Hit: 5d10+6 radiant." in action["description"]
    # The auditor now SEES the damage it was blind to.
    assert combat.audit_stat_block(out[0].data["stat_block"]).dpr > 0
    # An action that already carries its own dice is left alone.
    already = models.EntityInput(
        kind="character",
        name="X",
        text=None,
        data={
            "stat_block": {
                "actions": [
                    {
                        "name": "Claw",
                        "damage": "2d6 fire",
                        "description": "Hit: 9 (2d6 + 2) slashing damage.",
                    },
                ]
            }
        },
        id=None,
    )
    kept = canonicalize_action_damage([already])[0].data["stat_block"]["actions"][0]
    assert kept["description"] == "Hit: 9 (2d6 + 2) slashing damage."


def test_conform_power_lifts_scores_and_keeps_the_numbers_consistent() -> None:
    """A CR-17 block written at low magnitudes is lifted to the DMG row for
    its challenge: scores to the tier floor (RANKING preserved — the model
    owns the shape, the table owns the size), AC to the floor, hp to the
    band, and the to-hit / flat damage / stated average rewritten so the
    prose never contradicts the scores it now carries (2026-09-11: a live
    level-17 paladin sat at STR 16 / hp 142 / one 14-damage attack — the
    conform is Monster-only since the NPC oracle)."""
    block = {
        "identity": {"role": "Monster", "cr": 17, "race": "Half-Elf", "class": "Paladin"},
        "attributes": {"cha": 18, "con": 16, "dex": 14, "int": 12, "str": 16, "wis": 14},
        "combat": {"ac": 18, "hp": 142},
        "skills": [{"name": "Perception", "bonus": 5}],
        "actions": [
            {"name": "Multiattack", "description": "Sanberi makes two attacks with Holy Strike."},
            {
                "name": "Holy Strike",
                "description": (
                    "Melee Weapon Attack: +8 to hit, reach 5 ft., one target. "
                    "Hit: 14 (2d8 + 5) radiant damage."
                ),
            },
        ],
        "traits": [],
        "spells": [],
    }
    assert is_conformable(validate_stat_block(block))
    conformed = conform_power(block)
    assert conformed is not None
    attributes = conformed["attributes"]
    # CHA was the model's best score and stays its best — only the size moved.
    assert attributes["cha"] == 28
    assert attributes["str"] == 26
    assert attributes["int"] == 12  # the dump stays the dump
    assert conformed["combat"]["ac"] == 21
    audit = combat.audit_stat_block(conformed)
    assert audit.band is not None and audit.band[0] <= audit.dpr <= audit.band[1]
    assert validate_stat_block(conformed) == []
    strike = conformed["actions"][1]["description"]
    # The lift reached the numbers the same prose states.
    assert "+13 to hit" in strike  # +8 shifted by the STR/DEX modifier delta
    assert "19 (2d8 + 10)" in strike  # average recomputed from its own dice
    # The model's own routine survives untouched.
    assert conformed["actions"][0]["description"].startswith("Sanberi makes two attacks")


def test_conform_power_lifts_scores_without_moving_an_in_band_blocks_damage() -> None:
    """An in-band block keeps its DAMAGE — the flat half is the one that moves
    DPR, and a cosmetic score lift must not push a valid block out of band.
    The scores and the to-hit still move: a CR-5 creature reading STR 14 is
    the complaint this pass exists to answer (Monster-only since the NPC
    oracle)."""
    block = {
        "identity": {"role": "Monster", "cr": 5, "race": "Human", "class": "Fighter"},
        "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 140},
        "actions": [
            {
                "name": "Axe",
                "description": ("Melee Weapon Attack: +5 to hit. Hit: 35 (10d6) slashing damage."),
            }
        ],
        "skills": [],
        "traits": [],
        "spells": [],
    }
    conformed = conform_power(block)
    assert conformed is not None
    # Scores lift to the tier even here (STR 14 -> the 5-8 floor).
    assert conformed["attributes"]["str"] == 20
    assert conformed["attributes"]["cha"] == 9  # the dump stays the dump
    # The damage the block already carried is untouched — the rider is never
    # added to a block that meets its band.
    assert "35 (10d6) slashing damage" in conformed["actions"][0]["description"]
    assert conformed["combat"]["ac"] == 17


def test_authentic_npc_block_commits_stamped_without_repair(world: str) -> None:
    """NPC ORACLE (owner verdict 2026-09-12): an authentic level-5 NPC
    block — 6.5 DPR against the monster table's 33-38, hp below the frail
    line — is NOT a violation. Zero repair calls, the model's own numbers
    commit, stamped under-powered for DM visibility. Pre-verdict this exact
    shape burned 45 of 48 calls on the Qwen rung-50 ladder and committed
    the conform's arithmetic instead of the model's."""
    authentic = {
        "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter"},
        "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 40},
        "skills": [{"name": "Athletics", "bonus": 5}],
        "actions": [
            {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 1d8+2 slashing"}
        ],
    }
    output = _wave1_output()
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": authentic}
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    responses = [json.dumps(output)]

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 1  # wave 1 only — an authentic NPC spends no repair
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    # The model's own numbers, byte for byte, plus the DM-visible stamp.
    assert mira.data["stat_block"] == {
        **authentic,
        "power": {"dpr": 6.5, "band": [33.0, 38.0], "verdict": "under-powered"},
    }


def test_underpowered_monster_conforms_without_repair_calls(world: str) -> None:
    """CONFORM_FIRST: a Monster power-only miss goes straight to the
    deterministic conform — zero LLM repair calls (measured on both ladder
    models: repairs nudge 8.5 -> 13 -> 17 and never reach the band, or
    overshoot it; the conform lands the exact DMG row in one pass)."""
    weak_monster = {
        "identity": {"role": "Monster", "cr": 5, "race": "Humanoid"},
        "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 66},
        "skills": [{"name": "Athletics", "bonus": 5}],
        "actions": [
            {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 1d8+2 slashing"}
        ],
    }
    output = _wave1_output()
    output["entities"][1]["data"] = {
        **_character_record("Mira Vane", role="Monster", level_cr="CR 5", boss=dict(_BOSS_SECTION)),
        "stat_block": weak_monster,
    }
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    responses = [json.dumps(output)]

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 1  # wave 1 only — the conform is deterministic, free
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    conformed = mira.data["stat_block"]
    assert validate_stat_block(conformed) == []
    audit = combat.audit_stat_block(conformed)
    assert audit.band is not None and audit.band[0] <= audit.dpr <= audit.band[1]
    # The model's own action and identity survive; only the numbers moved.
    assert conformed["actions"][0]["name"] == "Longsword"
    assert conformed["identity"] == weak_monster["identity"]


def test_overpowered_stat_block_commits_stamped_without_repair(world: str) -> None:
    """POWER_FLAG (owner verdict 2026-09-12): a 60-DPR level-5 block is not
    a violation — no repair pass is spent on it, the job succeeds, and the
    committed block carries the over-powered power stamp for the DM."""
    over_block = {
        "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter"},
        "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 140},
        "skills": [{"name": "Athletics", "bonus": 5}],
        "actions": [{"name": "Slam", "description": "10d10+5 force"}],
    }

    def wave_with(block: dict[str, Any]) -> dict[str, Any]:
        output = _wave1_output()
        output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": block}
        return output

    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    responses = [json.dumps(wave_with(over_block))]

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 1  # wave 1 only — over-powered spends no repair pass
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"]["power"] == {
        "dpr": 60.0,
        "band": [33.0, 38.0],
        "verdict": "over-powered",
    }


def test_stat_repair_dropping_class_keeps_original(world: str) -> None:
    """CLASS_PRESERVE (ladder attempt 19): a repair that fixes the level
    but drops identity.class must not open a class-link hole — the merge
    restores the valid pre-repair class, so the job converges on the next
    pass instead of dying with `spells require identity.class` after all
    three passes. A deliberately changed class still lands (only a
    dropped/blanked one is restored)."""
    leveled_block = {
        "identity": {"role": "NPC", "level": 25, "race": "Human", "class": "Wizard"},
        "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 140},
        "skills": [{"name": "Athletics", "bonus": 5}],
        "spells": ["Fire Bolt"],
        "actions": [{"name": "Slam", "description": "10d10+5 force"}],
    }
    classless_fix = {
        "identity": {"role": "NPC", "level": 5, "race": "Human"},
        "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 140},
        "skills": [{"name": "Athletics", "bonus": 5}],
        "spells": ["Fire Bolt"],
        "actions": [{"name": "Slam", "description": "6d10+5 fire"}],
    }
    output = _wave1_output()
    output["entities"][1]["data"] = {
        **_character_record("Mira Vane"),
        "stat_block": leveled_block,
    }
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": classless_fix}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"  # no class-link pass 2, no third response needed
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"]["identity"]["class"] == "Wizard"
    assert mira.data["stat_block"]["identity"]["level"] == 5


def test_stat_repair_dropping_level_keeps_original(world: str) -> None:
    """LEVEL_PRESERVE (Qwen3.8 rung-10 attempt 1): a repair that fixes the
    violation but drops identity.level must not leave a level the validator
    cannot read — the merge restores the pre-repair value, so the wave
    converges instead of dying `identity.level must be an integer in [1, 20]`
    across three passes. The live shape: the model nudged damage twice and
    dropped the level both times, and the follow-up repairs (scope
    ['identity']) edited spells instead of the missing field. The trigger
    here is the class-link family (scope {spells, identity}) — since the
    NPC oracle, power misses never reach the passes, so the guard is
    exercised on the shape violations that do. A same-shape level edit
    still lands (see the class test's level-25 -> 5 fix)."""
    weak_block = {
        **_MIRA_STAT_BLOCK,
        "spells": ["Fireball"],  # not on the Fighter list — scope {spells, identity}
    }
    leveled_out_fix = {
        "identity": {"role": "NPC", "race": "Human", "class": "Fighter"},  # level dropped
        "attributes": dict(_MIRA_STAT_BLOCK["attributes"]),
        "combat": {"ac": 16, "hp": 66},
        "skills": [{"name": "Athletics", "bonus": 5}],
        "spells": [],
        "actions": [
            {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 4d10+5 slashing"}
        ],
    }
    output = _wave1_output()
    output["entities"][1]["data"] = {
        **_character_record("Mira Vane"),
        "stat_block": weak_block,
    }
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": leveled_out_fix}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"  # no second/third pass needed
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    assert mira.data["stat_block"]["identity"]["level"] == 5
    assert mira.data["stat_block"]["identity"]["class"] == "Fighter"
    assert mira.data["stat_block"]["spells"] == []


def test_frail_monster_conforms_without_repair_calls(world: str) -> None:
    """CONFORM_FIRST (frail): a Monster whose only miss is the HP floor is
    lifted deterministically — zero repair calls (the model's passes never
    converged on arithmetic; the conform lands the band floor in one exact
    step). An NPC's low HP is never a violation at all — the frail check is
    Monster-only since the NPC oracle (owner verdict 2026-09-12)."""
    frail_monster = {
        "identity": {"role": "Monster", "cr": 5, "race": "Humanoid"},
        "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 40},
        "skills": [{"name": "Athletics", "bonus": 5}],
        "actions": [
            {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 4d10+5 slashing"}
        ],
    }
    output = _wave1_output()
    output["entities"][1]["data"] = {
        **_character_record("Mira Vane", role="Monster", level_cr="CR 5", boss=dict(_BOSS_SECTION)),
        "stat_block": frail_monster,
    }
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    responses = [json.dumps(output)]

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 1  # wave 1 only — the conform is deterministic, free
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        mira = next(e for e in world_entities(session, world) if e.name == "Mira Vane")
    conformed = mira.data["stat_block"]
    assert validate_stat_block(conformed) == []
    hp_band = combat.hp_band(conformed["identity"])
    assert hp_band is not None
    assert conformed["combat"]["hp"] == hp_band[0]  # lifted to the band floor
    assert conformed["actions"][0]["name"] == "Longsword"  # the model's action survives


# ---------------------------------------------------------------------------
# RECORD-REPAIR CHUNKING (spec 2026-09-10)
# ---------------------------------------------------------------------------


def _recordless_wave1_output(count: int) -> dict[str, Any]:
    """A wave-1 output with ``count`` record-less characters (E1..E<count>)
    plus the E0 faction: every character carries a valid stat block and a
    name but no AR24 record, so the record gate flags exactly the
    characters while the name/stat gates stay silent. Every entity is an
    edge endpoint, so validation passes."""
    entities: list[dict[str, Any]] = [
        {"ref": "E0", "kind": "faction", "name": "The Gilded Bar", "text": "smoke and coin"}
    ]
    edges: list[dict[str, Any]] = []
    for position in range(1, count + 1):
        entities.append(
            {
                "ref": f"E{position}",
                "kind": "character",
                "name": f"Hero{position}",
                "data": {"stat_block": _MIRA_STAT_BLOCK},
            }
        )
        edges.append({"src": "E0", "dst": f"E{position}", "type": "member_of", "counter": 1})
        edges.append({"src": f"E{position}", "dst": "E0", "type": "debt", "counter": 3})
    return {"entities": entities, "edges": edges}


def _record_chunk_response(positions: list[int]) -> str:
    """A valid record-repair response covering exactly ``positions``."""
    return json.dumps(
        {
            "records": [
                {"ref": f"E{position}", "data": _character_record(f"Hero{position}")}
                for position in positions
            ]
        }
    )


def test_record_repair_chunk_split(world: str) -> None:
    """CHUNK_SPLIT: 14 flagged records repair in 4 chunk calls (4+4+4+2)
    whose merged patches commit — the old single 14-record response
    stochastically dropped a brace and its same-size retry failed the
    same way."""
    output = _recordless_wave1_output(14)
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(output)
        chunk_index = len(calls) - 2  # 0-based among the repair calls
        first = 1 + chunk_index * 4
        return _record_chunk_response(list(range(first, min(first + 4, 15))))

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 5  # wave 1 + 4 record-repair chunks
    repair_calls = calls[1:]
    expected_chunks = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14]]
    for prompt, expected in zip(repair_calls, expected_chunks, strict=True):
        for position in expected:
            assert f"E{position} ('Hero{position}')" in prompt
        for other in [p for chunk in expected_chunks for p in chunk if p not in expected]:
            assert f"E{other} ('Hero{other}')" not in prompt
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        entities = world_entities(session, world)
    assert len(entities) == 15
    hero7 = next(e for e in entities if e.name == "Hero7")
    assert hero7.data["appearance"] and hero7.data["stat_block"] == _MIRA_STAT_BLOCK


def test_record_repair_chunk_retry_isolated(world: str) -> None:
    """CHUNK_RETRY: a malformed chunk is retried alone — the retry quotes
    the JSON decode error and names only its chunk's refs; the other
    chunk is called exactly once and the wave proceeds."""
    output = _recordless_wave1_output(5)
    malformed = '{"records": [{"ref": "E1", "data": {"role": "NPC"'  # truncated: no close
    responses = [
        json.dumps(output),
        malformed,
        _record_chunk_response([1, 2, 3, 4]),
        _record_chunk_response([5]),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 4  # wave 1 + bad chunk + its retry + the other chunk
    assert "YOUR PREVIOUS INVALID RESPONSE" in calls[2]
    assert "JSON error:" in calls[2]
    assert "E5 ('Hero5')" not in calls[2]  # the retry re-elicits only its chunk
    assert "YOUR PREVIOUS INVALID RESPONSE" not in calls[3]  # no retry burned there
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        hero5 = next(e for e in world_entities(session, world) if e.name == "Hero5")
    assert hero5.data["appearance"]


def test_record_repair_chunk_fail_names_refs(world: str) -> None:
    """CHUNK_FAIL: a chunk malformed twice fails the job naming that
    chunk's refs; zero commits."""
    output = _recordless_wave1_output(5)
    malformed = '{"records": [{"ref": "E5", "data": {"role": "NPC"'  # truncated: no close
    responses = [
        json.dumps(output),
        _record_chunk_response([1, 2, 3, 4]),
        malformed,
        malformed,
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "record chunk E5" in (job.error or "")
    assert "not valid JSON after one retry" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_record_repair_small_wave_single_call(world: str) -> None:
    """SMALL_WAVE: 4 flagged records stay exactly one repair call — the
    chunking boundary changes nothing below it (existing single-record
    ``len(calls) == 2`` pins cover the rest)."""
    output = _recordless_wave1_output(4)
    responses = [json.dumps(output), _record_chunk_response([1, 2, 3, 4])]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 2  # wave 1 + the single record repair
    job, _position = job_status(job_id)
    assert job.state == "succeeded"


def test_repair_retry_prompt_names_json_error() -> None:
    """ERROR_LINE: the shared retry prompt quotes the decoder's error on
    its own line after the rules; ``json_error`` reports the
    fence-stripped failure and None when parseable."""
    assert json_error(json.dumps({"records": []})) is None
    assert json_error("just prose, no braces") == "not a JSON object"
    assert json_error(json.dumps([1, 2])) == "not a JSON object"
    err = json_error('{"records": [{"ref": "E1", "data": {"role": "NPC"')
    assert err is not None and "JSON error:" not in err  # raw decoder text
    prompt = _repair_retry_prompt("BASE", "BAD", "NOTE", err)
    assert f"JSON error: {err}" in prompt
    assert prompt.index(f"JSON error: {err}") > prompt.index("no prose before or after")
    assert "YOUR PREVIOUS INVALID RESPONSE" in prompt
    assert "JSON error:" not in _repair_retry_prompt("BASE", "BAD", "NOTE")


def test_record_repair_chunk_prompts_deterministic() -> None:
    """DETERMINISM: the same flagged records chunk into byte-identical prompts."""

    def chunk_prompts() -> list[str]:
        entities = [
            models.EntityInput(kind="character", name=f"Hero{i}", data={}) for i in range(6)
        ]
        issues = _collect_record_issues(entities)
        assert len(issues) == 6
        return [_build_record_repair_prompt(issues[i : i + 4]) for i in (0, 4)]

    first, second = chunk_prompts(), chunk_prompts()
    assert len(first) == 2  # 4 + 2
    assert first == second


def test_json_error_prefers_inner_candidate() -> None:
    """JSON_ERROR_INNER: a fenced truncated object reports the same error
    as the bare inner text, and a prose-wrapped object with an inner typo
    quotes the inner defect — never the backticks or the prose."""
    inner = '{"records": [{"ref": "E1", "data": {"role": "NPC"'
    fenced = "```json\n" + inner + "\n```"
    assert json_error(fenced) == json_error(inner)
    typo_inner = '{"records": {"a": 1,}}'  # balanced braces, trailing comma
    wrapped = "Here is the completed record:\n" + typo_inner + "\nHope this helps!"
    assert json_error(wrapped) == json_error(typo_inner)
    assert "Here is the completed record" not in (json_error(wrapped) or "")


def test_record_repair_cancel_between_chunks(world: str) -> None:
    """CHUNK_CANCEL: a cancel landing after the first chunk stops the gate
    before the next chunk — no further provider calls, no commit, and the
    job stays cancelled with no terminal conflict."""
    output = _recordless_wave1_output(5)
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(output)
        if len(calls) == 2:
            cancel_job(job_id)  # lands during the first chunk's call
            return _record_chunk_response([1, 2, 3, 4])
        raise AssertionError("no chunk after cancel may reach the provider")

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "cancelled"  # never failed/succeeded — no terminal conflict
    assert len(calls) == 2  # wave 1 + first chunk only
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_record_repair_budget_exhausted_mid_chunks(world: str) -> None:
    """CHUNK_BUDGET: max_llm_calls=2 — wave 1 plus the first chunk exhaust
    the budget, so the second chunk is refused before any HTTP request and
    the job fails naming the budget with zero commits."""
    output = _recordless_wave1_output(5)
    responses = [json.dumps(output), _record_chunk_response([1, 2, 3, 4])]
    calls: list[str] = []
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"], max_llm_calls=2)

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "budget" in (job.error or "").lower()
    assert len(calls) == 2  # the second chunk never reached the provider
    with session_scope() as session:
        assert revision_chain(session, world) == []


# ---------------------------------------------------------------------------
# WAVE-2 ANCHOR REPAIR (spec: repair sequence step 3, edges-only)
# ---------------------------------------------------------------------------


def _anchor_repair_healed_edges() -> dict[str, Any]:
    """The edges-only repair healing the orphan output: one N2->C0 edge so
    every entity anchors. No entity list is emitted — the wave's entities
    stay frozen from the first attempt."""
    return {"edges": [{"src": "N2", "dst": "C0", "type": "relationship"}]}


def test_wave2_anchor_repair_heals_and_commits(world: str) -> None:
    """ANCHOR_HEAL: a wave-2 output with one orphan triggers exactly one
    edges-only repair naming it; the repair wires the orphan to the core
    and the frozen-entity wave commits."""
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(_wave2_output_orphan()),
        json.dumps(_anchor_repair_healed_edges()),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 3  # wave 1 + wave 2 + the one anchor repair
    assert "ANCHOR REPAIR" in calls[2]
    assert "'Nowhere Man' (N2)" in calls[2]
    # Frozen rosters: new entities as ref+name+kind, the core as C-label+name.
    assert "- N2 'Nowhere Man' (character)" in calls[2]
    assert "- C0 'The Gilded Bar'" in calls[2]
    assert '{"edges": [...]}' in calls[2]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_count"] == 5
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 2
        entities = {e.name: e for e in world_entities(session, world)}
    assert set(entities) == {
        "The Gilded Bar",
        "Mira Vane",
        "The Drowned Rat",
        "Captain Harlow",
        "Nowhere Man",
    }
    # The orphan committed anchored: an edge joins it to the core.
    core_ids = {entities["The Gilded Bar"].id, entities["Mira Vane"].id}
    orphan_id = entities["Nowhere Man"].id
    with session_scope() as session:
        edges = world_edges(session, world)
    assert any(
        (e.src == orphan_id and e.dst in core_ids) or (e.dst == orphan_id and e.src in core_ids)
        for e in edges
    )


def test_wave2_anchor_second_miss_fails_core_stays(world: str) -> None:
    """SECOND_MISS: a repair adding no anchor edge fails the job naming the
    orphans; wave 1 stays committed, wave 2 writes nothing."""
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(_wave2_output_orphan()),
        json.dumps({"edges": []}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 3  # exactly one repair — never a third attempt
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "Nowhere Man" in (job.error or "")
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1  # wave 2 zero commits
        entities = world_entities(session, world)
    assert {e.name for e in entities} == {"The Gilded Bar", "Mira Vane"}  # core intact


def test_wave2_clean_wave_makes_no_extra_call(world: str) -> None:
    """CLEAN_WAVE: no orphans — zero extra calls, identical behavior to
    before the repair existed."""
    responses = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 2
    assert all("ANCHOR REPAIR" not in prompt for prompt in calls)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 2


def test_wave2_orphan_message_uses_n_prefix(world: str) -> None:
    """PREFIX: the wave-2 orphan failure names the orphan with its N ref —
    never the wave-1 E prefix."""
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(_wave2_output_orphan()),
        json.dumps({"edges": []}),
    ]
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        return responses.pop(0)

    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "'Nowhere Man' (N2)" in (job.error or "")
    assert "(E2)" not in (job.error or "")


def test_wave2_anchor_repair_ignores_renamed_entities(world: str) -> None:
    """RENAMED_COMMITS (spec step-3 Success): the attempt-8 shape — a repair
    that wires the orphan but smuggles a renamed entity list — commits with
    the frozen first-attempt entities. Rename-class deaths are
    unrepresentable: the repair's entity list is never read."""
    repair = {
        "entities": [
            {"ref": "N0", "kind": "place", "name": "The Drowned Rat"},
            {"ref": "N1", "kind": "character", "name": "Captain Harlow the Renamed"},
            {"ref": "N2", "kind": "character", "name": "Nowhere Man"},
        ],
        "edges": [{"src": "N2", "dst": "C0", "type": "relationship"}],
    }
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(_wave2_output_orphan()),
        json.dumps(repair),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 3  # exactly one repair — never a third attempt
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 2
        entities = world_entities(session, world)
    assert {e.name for e in entities} == {
        "The Gilded Bar",
        "Mira Vane",
        "The Drowned Rat",
        "Captain Harlow",
        "Nowhere Man",
    }  # the smuggled rename never committed


def test_wave2_orphan_message_without_core() -> None:
    """NO_CORE_SUFFIX: with no visible core the wave-2 orphan error never
    renders C0..C-1 — it says so plainly and still names N refs."""
    parsed = {
        "entities": [
            {"ref": "N0", "kind": "place", "name": "The Drowned Rat"},
            {
                "ref": "N1",
                "kind": "character",
                "name": "Captain Harlow",
                "data": {**_character_record("Captain Harlow"), "stat_block": _MIRA_STAT_BLOCK},
            },
        ],
        "edges": [{"src": "N0", "dst": "N1", "type": "relationship"}],
    }
    with pytest.raises(_OrphanRetryError) as excinfo:
        _validate_subgraph(2, parsed, context=(), core_count=0)
    assert excinfo.value.wave == 2
    assert "C0..C-1" not in str(excinfo.value)
    assert "no visible core" in str(excinfo.value)
    assert "'Captain Harlow' (N1)" in str(excinfo.value)


def test_wave2_anchor_repair_budget_exhausted(world: str) -> None:
    """REPAIR_BUDGET: max_llm_calls=2 — wave 1 plus the first wave-2 attempt
    exhaust the budget, so the repair is refused before any HTTP request
    and the job fails naming the budget with the core intact."""
    responses = [json.dumps(_wave1_output()), json.dumps(_wave2_output_orphan())]
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world", max_llm_calls=2)

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "budget" in (job.error or "").lower()
    assert len(calls) == 2  # the repair never reached the provider
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1
        assert {e.name for e in world_entities(session, world)} == {
            "The Gilded Bar",
            "Mira Vane",
        }


def test_wave2_anchor_repair_cancel_skips_repair(world: str) -> None:
    """REPAIR_CANCEL: a cancel landing during the first wave-2 call stops the
    runner before the repair — no further provider calls, no commit beyond
    the core, and the job stays cancelled with no terminal conflict."""
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(_wave1_output())
        cancel_job(job_id)  # lands during the first wave-2 call
        return json.dumps(_wave2_output_orphan())

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "cancelled"  # never failed/succeeded — no terminal conflict
    assert len(calls) == 2  # wave 1 + first wave-2 attempt only
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1  # wave-1 core committed


def test_wave2_nonorphan_failure_gets_no_retry(world: str) -> None:
    """NO_RETRY_NONORPHAN: a wave-2 bad-ref rejection fails immediately —
    exactly 2 calls, no ANCHOR REPAIR marker."""
    bad = _wave2_output()
    bad["entities"][0]["ref"] = "X0"
    responses = [json.dumps(_wave1_output()), json.dumps(bad)]
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert len(calls) == 2
    assert all("ANCHOR REPAIR" not in prompt for prompt in calls)
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1  # wave 2 zero commits


def test_wave2_mixed_orphan_and_bad_edge_gets_no_retry(world: str) -> None:
    """NO_RETRY_MIXED: an orphan plus a bad edge type still fails immediately
    (the edge rejection fires before the orphan check) — exactly 2 calls."""
    mixed = _wave2_output_orphan()
    mixed["edges"][0]["type"] = "hates"
    responses = [json.dumps(_wave1_output()), json.dumps(mixed)]
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "vocabulary" in (job.error or "")  # the edge rejection, not the orphan
    assert len(calls) == 2
    assert all("ANCHOR REPAIR" not in prompt for prompt in calls)
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1  # wave 2 zero commits


def test_wave2_anchor_repair_repaired_by_gates(world: str) -> None:
    """REPAIR_GATES: the merged wave flows through the normal gates — first
    attempt with an incomplete record plus an anchoring repair still gets
    the record repair and commits."""
    orphan = _wave2_output_orphan()
    del orphan["entities"][1]["data"]["appearance"]
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(orphan),
        json.dumps(_anchor_repair_healed_edges()),
        json.dumps({"records": [{"ref": "E1", "data": _character_record("Captain Harlow")}]}),
    ]
    calls: list[str] = []
    job_id = _enqueue(world, notes="more world")

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    assert len(calls) == 4  # wave 1 + wave 2 + repair + the record repair
    assert "ANCHOR REPAIR" in calls[2]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_count"] == 5
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 2
        harlow = next(e for e in world_entities(session, world) if e.name == "Captain Harlow")
    assert harlow.data["appearance"]  # the repaired record committed


# ---------------------------------------------------------------------------
# The 100-entity cut: chunked wave-1 (M1), retry taxonomy (C), window sizing
# (B), two-tier wave-2 context (M2), stale rebase (M4), telemetry (J).
# ---------------------------------------------------------------------------


def _places_chunk_output(
    start: int, count: int, edges: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """One chunk's flat-place response: global refs E<start>.., no records."""
    return {
        "entities": [
            {"ref": f"E{start + index}", "kind": "place", "name": f"Place {start + index}"}
            for index in range(count)
        ],
        "edges": edges or [],
    }


def test_wave1_roster_trims_in_section_order() -> None:
    """The roster is the trimmed payload in fixed section order — the same
    rule the count pin and the enqueue budget share."""
    roster = _wave1_roster(
        {"places": [" P1 ", "", 42, "P2"], "factions": [], "key_figures": ["F1", "   "]}
    )
    assert roster == [("places", "P1"), ("places", "P2"), ("key_figures", "F1")]


def test_wave1_chunks_slice_by_weight_and_cap() -> None:
    """M1 slicing: characters weigh 1.0 (budget 12), flats 0.35, the hard
    cap binds at 16 — global positions stay contiguous, so refs and the
    merge order are a pure function of the roster (AD-16)."""
    chunks = _wave1_chunks([("places", f"P{i}") for i in range(20)])
    assert [len(chunk) for chunk in chunks] == [16, 4]  # the hard cap binds
    assert [pos for chunk in chunks for pos, _s, _e in chunk] == list(range(20))
    chunks = _wave1_chunks([("key_figures", f"F{i}") for i in range(13)])
    assert [len(chunk) for chunk in chunks] == [12, 1]  # the weight binds
    mixed = [("places", "P")] * 3 + [("key_figures", "F")] * 12
    chunks = _wave1_chunks(mixed)
    # 3 x 0.35 + 10 x 1.0 = 11.05; the 11th figure would cross 12 → [13, 2].
    assert [len(chunk) for chunk in chunks] == [13, 2]
    assert _wave1_chunks([]) == []


def test_chunked_wave1_generates_merges_and_wires(world: str) -> None:
    """M1 end to end: a 20-place roster exceeds one chunk (hard cap 16) —
    two pinned chunk calls plus one edges-only wiring pass; entities merge
    in global positional order, the cross-chunk wiring edge commits, each
    call carries its count+ref-pinned schema and sized window (B), and the
    result carries per-label telemetry (J)."""
    places = [f"Place {i}" for i in range(20)]
    calls: list[tuple[str, Any]] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append((prompt, settings))
        if "PART 1 of 2" in prompt:
            return json.dumps(_places_chunk_output(0, 16))
        if "PART 2 of 2" in prompt:
            return json.dumps(_places_chunk_output(16, 4))
        return json.dumps({"edges": [{"src": "E0", "dst": "E19", "type": "located_in"}]})

    job_id = _enqueue(world, places=places)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 3  # chunk0 + chunk1 + wiring — no monolith
    assert calls[0][1].response_format == build_wave_schema(16, refs=[f"E{i}" for i in range(16)])
    assert calls[1][1].response_format == build_wave_schema(
        4, refs=[f"E{i}" for i in range(16, 20)]
    )
    assert calls[2][1].response_format == build_anchor_repair_schema(
        sorted(f"E{i}" for i in range(20)), []
    )
    assert calls[0][1].max_tokens == 900 * 16 + 4096  # TOKENS_PER_ENTITY x pin
    assert calls[2][1].max_tokens == 16384  # EDGES_CALL_MAX_TOKENS
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None
    assert job.result["entity_count"] == 20
    assert job.result["edge_count"] == 1  # the cross-chunk wiring edge
    labels = job.result["llm_calls"]["by_label"]
    assert labels["wave1_chunk0"]["calls"] == 1
    assert labels["wave1_chunk1"]["calls"] == 1
    assert labels["wave1_wiring"]["calls"] == 1
    assert job.result["llm_calls"]["total"] == 3
    with session_scope() as session:
        names = [entity.name for entity in world_entities(session, world)]
    assert names == [f"Place {i}" for i in range(20)]  # global positional order


def test_wiring_failure_degrades_to_chunk_edges(world: str) -> None:
    """M1 best-effort wiring: a wiring pass still malformed after its one
    JSON retry degrades to committing the chunk-internal edges only —
    never a job death over wiring (the edgeless commit is legal, owner
    verdict 2026-09-11)."""
    places = [f"Place {i}" for i in range(20)]
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if "PART 1 of 2" in prompt:
            return json.dumps(
                _places_chunk_output(
                    0, 16, edges=[{"src": "E0", "dst": "E1", "type": "relationship"}]
                )
            )
        if "PART 2 of 2" in prompt:
            return json.dumps(_places_chunk_output(16, 4))
        return "not json at all {"  # the wiring pass, malformed twice

    job_id = _enqueue(world, places=places)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 4  # 2 chunks + wiring + its one JSON retry
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None
    assert job.result["entity_count"] == 20
    assert job.result["edge_count"] == 1  # the chunk-internal edge only


def test_chunk_malformed_json_retries_once_and_recovers(world: str) -> None:
    """C: a chunk whose first response is malformed JSON gets exactly one
    re-elicitation quoting the decoder error, and the job recovers — the
    blast radius is one bounded chunk call, not a 15-minute monolith."""
    places = [f"Place {i}" for i in range(20)]
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if "PART 1 of 2" in prompt:
            if len(calls) == 1:
                return "{entities: [oops"
            return json.dumps(_places_chunk_output(0, 16))
        if "PART 2 of 2" in prompt:
            return json.dumps(_places_chunk_output(16, 4))
        return json.dumps({"edges": []})

    job_id = _enqueue(world, places=places)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 4  # chunk0 bad + retry + chunk1 + wiring
    assert "COULD NOT BE PARSED" in calls[1]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_count"] == 20
    assert job.result["llm_calls"]["by_label"]["wave1_chunk0_json_retry"]["calls"] == 1


def test_json_retry_rolls_the_pinned_seed(world: str) -> None:
    """C+G: with an operator-pinned seed the bounded JSON retry carries
    seed+1 — a deterministic profile never re-calls the identical failing
    sample; the first call keeps the pinned seed."""
    seeded = dataclasses.replace(SETTINGS, seed=7)
    seen_seeds: list[int | None] = []
    responses = ["{not json", json.dumps(_wave1_output())]

    def provider(prompt: str, settings: LLMSettings) -> str:
        seen_seeds.append(settings.seed)
        return responses.pop(0)

    job_id = _enqueue(world, places=["Greymarch"])
    assert run_next_job(provider=provider, settings=seeded) == job_id
    assert seen_seeds == [7, 8]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"


def test_truncated_wave_call_retries_with_doubled_window(world: str) -> None:
    """B/C: finish_reason=length means the WINDOW was too small for this
    roster — the one retry doubles it inside the operator cap instead of
    deterministically truncating again (the naive re-call the pre-taxonomy
    retry would have made)."""
    windows: list[int] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        windows.append(settings.max_tokens)
        if len(windows) == 1:
            raise ProviderError("truncated")
        return json.dumps(_wave1_output())

    job_id = _enqueue(world, places=["Greymarch"])
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    first = 900 * 1 + 4096  # the roster-of-1 sized window
    assert windows == [first, min(first * 2, SETTINGS.max_tokens)]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"


def test_anchor_repair_malformed_json_retries_once(world: str) -> None:
    """C: the anchor repair — ZERO retries before the taxonomy cut — gets
    the same one bounded JSON re-elicitation; a repair recovering on the
    retry commits the wave, and the retry shows in the telemetry."""
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(_wave2_output_orphan()),
        "{edges: [oops",
        json.dumps({"edges": [{"src": "N2", "dst": "C0", "type": "relationship"}]}),
    ]
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return responses.pop(0)

    job_id = _enqueue(world, notes="more world")
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    assert len(calls) == 4
    assert "COULD NOT BE PARSED" in calls[3]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None
    assert job.result["llm_calls"]["by_label"]["anchor_repair_json_retry"]["calls"] == 1


def test_wave2_anchors_into_the_compact_core_tier(world: str) -> None:
    """M2: a 25-place core exceeds the 24-row detail cap — the 25th entity
    rides the COMPACT roster, so a wave-2 edge to C24 validates and anchors
    (pre-M2 it died as 'names no context entity': the ledger's phantom
    orphan at scale)."""
    places = [f"Place {i}" for i in range(25)]
    prompts: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        prompts.append(prompt)
        if "PART 1 of 2" in prompt:
            return json.dumps(_places_chunk_output(0, 16))
        if "PART 2 of 2" in prompt:
            return json.dumps(_places_chunk_output(16, 9))
        if "wiring an already-generated" in prompt:
            return json.dumps({"edges": []})
        return json.dumps(
            {
                "entities": [
                    {"ref": "N0", "kind": "place", "name": "The Late Annex", "text": "past the cap"}
                ],
                "edges": [{"src": "N0", "dst": "C24", "type": "located_in"}],
            }
        )

    job_id = _enqueue(world, places=places, notes="an annex beyond the cap")
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_count"] == 26
    wave2_prompt = prompts[3]
    assert "COMPACT CORE ROSTER" in wave2_prompt
    assert "C24 'Place 24' (place)" in wave2_prompt
    with session_scope() as session:
        by_name = {entity.name: entity for entity in world_entities(session, world)}
        edges = world_edges(session, world)
    annex = by_name["The Late Annex"]
    assert any(
        edge.src == annex.id and edge.dst == by_name["Place 24"].id for edge in edges
    )  # anchored to the compact tier


def test_wave1_commit_rebases_over_a_dm_edit(world: str) -> None:
    """M4: a DM commit landing DURING wave-1 generation makes the job's
    base stale — the commit rebases once onto the new head instead of
    incinerating the generation (at 100-entity scale that window is
    minutes; the pre-rebase runner died here with zero commits)."""
    dm_committed = False

    def provider(prompt: str, settings: LLMSettings) -> str:
        nonlocal dm_committed
        if not dm_committed:
            dm_committed = True
            keep_id = ids.new_id()
            with session_scope() as session:
                head = latest_revision(session, world)
            commit_subgraph(
                world,
                [models.EntityInput(kind="place", name="DM's New Keep", id=keep_id)],
                [],
                base_revision=head.id if head is not None else None,
                allow_orphans=True,
            )
        return json.dumps(_wave1_output())

    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 2  # DM edit + rebased wave 1
        names = {entity.name for entity in world_entities(session, world)}
    assert {"The Gilded Bar", "Mira Vane", "DM's New Keep"} <= names


# ---------------------------------------------------------------------------
# Identity (owner decisions 2026-09-12): the roster-twin gate and the upsert
# ---------------------------------------------------------------------------


def test_normalize_entity_name_pins() -> None:
    """The dedup key is exact-name: casefolded, article-stripped,
    whitespace-collapsed — and deliberately NOT fuzzy (epithet variants
    stay distinct)."""
    assert normalize_entity_name("The Cistern King") == "cistern king"
    assert normalize_entity_name("  cistern   KING. ") == "cistern king"
    assert normalize_entity_name("A Dock") == "dock"
    assert normalize_entity_name("An Inn") == "inn"
    assert normalize_entity_name("Sim (The Drowned)") != normalize_entity_name("Wreck-diver Sim")


def test_wave2_prompt_carries_anti_twin_rule(world: str) -> None:
    """The wave-2 TASK tells the model the roster subjects already exist —
    the prompt half of the twin decision."""
    with session_scope() as session:
        seed = campaign_seed(session, world)
    assert seed is not None
    prompt = build_wave2_prompt(seed, "notes", ([], []), core_count=0)
    assert "never create an entity for one of them" in prompt


def test_wave2_roster_twin_dropped_and_renumbered(world: str) -> None:
    """TWIN GATE: a wave-2 entity whose (kind, normalized name) matches a
    wave-1 roster entry is dropped BEFORE validation; edges that pointed
    at it follow it out; the survivor is renumbered (N-refs are
    positional) and keeps its core anchor."""
    twin = _wave2_output()
    twin["entities"][0] = {"ref": "N0", "kind": "character", "name": "Mira Vane"}
    # N1 (the place) survives as N0; its C0 edge is rewritten, the edge to
    # the dropped twin is gone.
    responses = [json.dumps(_wave1_output()), json.dumps(twin)]
    job_id = _enqueue(world, places=["Greymarch"], notes="Mira Vane returns to the Drowned Rat")
    run_next_job(provider=lambda prompt, settings: responses.pop(0), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    merge = (job.result or {})["merge"]["wave2"]
    assert merge["twins_dropped"] == ["Mira Vane"]
    with session_scope() as session:
        rows = world_entities(session, world)
        edges = world_edges(session, world)
    mira = [e for e in rows if e.name == "Mira Vane"]
    assert len(mira) == 1  # no twin row — the wave-1 record stands
    harlow = next(e for e in rows if e.name == "Captain Harlow")
    mira_row = mira[0]
    assert any(e.src == harlow.id and e.dst == mira_row.id for e in edges)
    # the edge that pointed at the dropped twin is gone with it
    assert not any(e.dst == harlow.id and e.src == mira_row.id for e in edges)


def test_wave2_all_twins_commit_nothing(world: str) -> None:
    """Every wave-2 subject duplicates the roster: the job succeeds with an
    empty wave-2 result and NO second revision (the head stands)."""
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(
            {
                "entities": [
                    {"ref": "N0", "kind": "faction", "name": "The Gilded Bar"},
                    {"ref": "N1", "kind": "character", "name": "Mira Vane"},
                ],
                "edges": [{"src": "N1", "dst": "N0", "type": "member_of"}],
            }
        ),
    ]
    job_id = _enqueue(world, places=["Greymarch"], notes="Mira Vane and the Gilded Bar, again")
    run_next_job(provider=lambda prompt, settings: responses.pop(0), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    result = job.result or {}
    assert result["waves"][1]["entities"] == 0
    assert result["entity_count"] == 2  # the wave-1 roster view only
    assert result["merge"]["wave2"]["twins_dropped"] == ["The Gilded Bar", "Mira Vane"]
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1


def test_upsert_resubmit_updates_rows_not_twins(world: str) -> None:
    """UPSERT (owner decision 2026-09-12): re-submitting the same lists
    updates the committed rows in place — no twins, no duplicate edges, no
    second wave-1 revision — and wave 2 still wires into the ORIGINAL rows
    (the final-ID roster), not into ghosts."""
    responses1 = [json.dumps(_wave1_output())]
    _enqueue(world, places=["Greymarch"], notes="")
    run_next_job(provider=lambda prompt, settings: responses1.pop(0), settings=SETTINGS)
    with session_scope() as session:
        first = {e.name: e.id for e in world_entities(session, world)}
    assert len(first) == 2

    responses2 = [json.dumps(_wave1_output()), json.dumps(_wave2_output())]
    job_id2 = _enqueue(world, notes="the docks teem with Captain Harlow")
    run_next_job(provider=lambda prompt, settings: responses2.pop(0), settings=SETTINGS)
    job2, _position = job_status(job_id2)
    assert job2.state == "succeeded"
    merge = (job2.result or {})["merge"]
    assert [n["name"] for n in merge["wave1"]["unchanged"]] == [
        "The Gilded Bar",
        "Mira Vane",
    ]
    assert merge["wave1"]["merged"] == []
    assert merge["wave2"]["twins_dropped"] == []
    with session_scope() as session:
        rows = {e.name: e.id for e in world_entities(session, world)}
        edges = world_edges(session, world)
        revisions = list(revision_chain(session, world))
    assert set(rows) == {
        "The Gilded Bar",
        "Mira Vane",
        "The Drowned Rat",
        "Captain Harlow",
    }
    assert rows["The Gilded Bar"] == first["The Gilded Bar"]  # updated in place
    assert len(edges) == 5  # wave 1's two + wave 2's three, zero duplicates
    assert len(revisions) == 2  # job1 wave 1 + job2 wave 2; job2 wave 1 no-op
    rat, bar = rows["The Drowned Rat"], rows["The Gilded Bar"]
    assert any(e.src == rat and e.dst == bar for e in edges)


def test_upsert_grown_resubmit_adds_only_new(world: str) -> None:
    """The spec's growth workflow: a grown list keeps the unchanged rows and
    creates only the new ones."""
    responses1 = [json.dumps(_wave1_output())]
    _enqueue(world, places=["Greymarch"], notes="")
    run_next_job(provider=lambda prompt, settings: responses1.pop(0), settings=SETTINGS)
    responses2 = [json.dumps(_wave1_output_edgeless())]  # + Myconid Colony, Grymforge
    job_id2 = _enqueue(world, places=["Greymarch"], notes="")
    run_next_job(provider=lambda prompt, settings: responses2.pop(0), settings=SETTINGS)
    job2, _position = job_status(job_id2)
    assert job2.state == "succeeded"
    merge = (job2.result or {})["merge"]["wave1"]
    assert [n["name"] for n in merge["unchanged"]] == ["The Gilded Bar", "Mira Vane"]
    assert merge["merged"] == []
    with session_scope() as session:
        names = {e.name for e in world_entities(session, world)}
    assert names == {"The Gilded Bar", "Mira Vane", "Myconid Colony", "Grymforge"}


def test_upsert_drops_duplicate_relationships(world: str) -> None:
    """A re-submit whose edges duplicate live relationships drops them (the
    store would reject them as duplicates) and keeps the ORIGINAL counters
    — a merge is not a counter update."""
    responses1 = [json.dumps(_wave1_output())]
    _enqueue(world, places=["Greymarch"], notes="")
    run_next_job(provider=lambda prompt, settings: responses1.pop(0), settings=SETTINGS)
    changed = _wave1_output()
    changed["edges"][0]["counter"] = 9
    responses2 = [json.dumps(changed)]
    job_id2 = _enqueue(world, places=["Greymarch"], notes="")
    run_next_job(provider=lambda prompt, settings: responses2.pop(0), settings=SETTINGS)
    job2, _position = job_status(job_id2)
    assert job2.state == "succeeded"
    merge = (job2.result or {})["merge"]["wave1"]
    assert {d["why"] for d in merge["dropped_edges"]} == {"duplicate relationship"}
    assert len(merge["dropped_edges"]) == 2
    with session_scope() as session:
        edges = world_edges(session, world)
    assert sorted(e.counter for e in edges) == [1, 3]  # originals intact


def test_repair_calls_are_cold_and_seeded(world: str) -> None:
    """K sampling preset (owner verdict 2026-09-12): repair-class calls
    carry temperature 0 + the pinned seed (reproducible patches) while
    wave calls stay warm at the operator default; a malformed repair
    response retries at seed+1 — a cold profile never re-samples the
    identical failing patch."""
    seen: list[LLMSettings] = []
    output = _wave1_output()
    output["entities"][1]["data"] = _character_record("Mira Vane")  # no stat_block
    responses = [
        json.dumps(output),
        "not json at all",  # malformed repair -> one rolled-seed retry
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]

    def provider(prompt: str, settings: LLMSettings) -> str:
        seen.append(settings)
        return responses.pop(0)

    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    wave_call, repair_call, retry_call = seen
    assert (wave_call.temperature, wave_call.seed) == (None, None)  # warm
    assert (repair_call.temperature, repair_call.seed) == (REPAIR_TEMPERATURE, REPAIR_SEED)
    assert retry_call.seed == REPAIR_SEED + 1


def test_repair_sampling_respects_operator_pins(world: str) -> None:
    """An operator-pinned seed/temperature is a deliberate choice — the
    cold preset must not stomp it."""
    pinned = dataclasses.replace(SETTINGS, seed=7, temperature=0.9)
    seen: list[LLMSettings] = []
    output = _wave1_output()
    output["entities"][1]["data"] = _character_record("Mira Vane")
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": _MIRA_STAT_BLOCK}]}),
    ]

    def provider(prompt: str, settings: LLMSettings) -> str:
        seen.append(settings)
        return responses.pop(0)

    _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(provider=provider, settings=pinned)
    assert seen[1].seed == 7 and seen[1].temperature == 0.9


def test_edge_kind_ok_rules_pin() -> None:
    """EDGE_KIND_RULES (owner decision 2026-09-12): the single table both
    layers read. member_of/loyalty never touch a place; located_in must
    point at a place; the catch-alls stay unrestricted."""
    assert edge_kind_ok("member_of", "character", "faction")
    assert edge_kind_ok("member_of", "faction", "faction")
    assert not edge_kind_ok("member_of", "place", "faction")
    assert not edge_kind_ok("member_of", "character", "place")
    assert edge_kind_ok("located_in", "character", "place")
    assert edge_kind_ok("located_in", "place", "place")
    assert not edge_kind_ok("located_in", "character", "character")
    assert not edge_kind_ok("located_in", "place", "faction")
    assert edge_kind_ok("debt", "place", "place")
    assert edge_kind_ok("relationship", "place", "faction")
    assert not edge_kind_ok("loyalty", "place", "character")


def test_prompts_carry_edge_kind_guidance(world: str) -> None:
    """Layer 1: every build prompt prints the kind-compatible vocabulary
    derived from the SAME table the validator enforces — prompt text and
    rules cannot drift (byte-deterministic, AD-16)."""
    with session_scope() as session:
        seed = campaign_seed(session, world)
    assert seed is not None
    prompt = build_wave1_prompt(seed, {"places": ["Greymarch"], "key_figures": ["Mira"]})
    assert "- member_of: character|faction -> character|faction" in prompt
    assert "a place is never a member and never a host" in prompt
    assert "- located_in: any -> place" in prompt
    assert "EDGE VOCABULARY" in prompt and "debt: amount" in prompt
    prompt2 = build_wave2_prompt(seed, "notes", ([], []), core_count=0)
    assert "- member_of: character|faction -> character|faction" in prompt2
    assert "never both A member_of B and B member_of A" in prompt2


def test_wave1_kind_invalid_edges_repaired(world: str) -> None:
    """Layer 2, wave-1 happy path: a kind-invalid edge (faction -> character
    located_in) gets ONE bounded edges-only repair; the corrected edge
    commits, nothing is dropped, the job result carries no audit."""
    output = _wave1_output()
    output["edges"].append({"src": "E0", "dst": "E1", "type": "located_in", "counter": 1})
    responses = [
        json.dumps(output),
        json.dumps({"edges": [{"src": "E0", "dst": "E1", "type": "relationship", "counter": 1}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(provider=lambda prompt, settings: responses.pop(0), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert "edge_kind_dropped" not in (job.result or {}).get("merge", {}).get("wave1", {})
    with session_scope() as session:
        edges = world_edges(session, world)
    committed = {(e.type, e.src, e.dst) for e in edges}
    assert {"member_of", "debt", "relationship"} <= {t for t, _s, _d in committed}
    assert not any(t == "located_in" for t, _s, _d in committed)


def test_wave1_kind_residual_dropped_with_audit(world: str) -> None:
    """A repair that still violates the rules drops the edge AND names it in
    the job result audit — the wave still succeeds (wave-1 edges are
    best-effort)."""
    output = _wave1_output()
    output["edges"].append({"src": "E0", "dst": "E1", "type": "located_in", "counter": 1})
    responses = [
        json.dumps(output),
        json.dumps({"edges": [{"src": "E0", "dst": "E1", "type": "located_in", "counter": 1}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(provider=lambda prompt, settings: responses.pop(0), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    dropped = (job.result or {})["merge"]["wave1"]["edge_kind_dropped"]
    assert len(dropped) == 1
    assert "located_in must point at a place" in dropped[0]["reason"]
    with session_scope() as session:
        edges = world_edges(session, world)
    assert {e.type for e in edges} == {"member_of", "debt"}  # originals only


def test_wave1_kind_repair_failure_degrades_to_drop(world: str) -> None:
    """The repair call itself dies (provider error) — the rejected edges
    drop with the audit, the build never fails over edges."""
    output = _wave1_output()
    output["edges"].append({"src": "E0", "dst": "E1", "type": "located_in", "counter": 1})
    calls = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(output)
        raise ProviderError("llm down mid-repair")

    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert len((job.result or {})["merge"]["wave1"]["edge_kind_dropped"]) == 1
    with session_scope() as session:
        assert {e.type for e in world_edges(session, world)} == {"member_of", "debt"}


def test_wave1_mutual_member_of_repaired_to_single_direction(world: str) -> None:
    """The graph rule: both member_of directions are rejected and the
    repair keeps exactly one — membership is hierarchical."""
    output = _wave1_output()
    output["edges"].append({"src": "E1", "dst": "E0", "type": "member_of", "counter": 2})
    responses = [
        json.dumps(output),
        json.dumps({"edges": [{"src": "E0", "dst": "E1", "type": "member_of", "counter": 1}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(provider=lambda prompt, settings: responses.pop(0), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        edges = list(world_edges(session, world))
    member = [(e.src, e.dst) for e in edges if e.type == "member_of"]
    assert len(member) == 1  # one direction survived


def test_wave2_kind_invalid_edges_repaired(world: str) -> None:
    """Wave-2 happy path: a character -> character located_in gets the one
    repair and commits corrected."""
    wave2 = _wave2_output()
    wave2["edges"][1] = {"src": "N1", "dst": "C1", "type": "located_in"}
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(wave2),
        json.dumps({"edges": [{"src": "N1", "dst": "C1", "type": "ally_of", "counter": 2}]}),
    ]
    job_id = _enqueue(world, notes="Captain Harlow docks at the Rat")
    run_next_job(provider=lambda prompt, settings: responses.pop(0), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    with session_scope() as session:
        rows = {e.name: e.id for e in world_entities(session, world)}
        edges = world_edges(session, world)
    harlow, mira = rows["Captain Harlow"], rows["Mira Vane"]
    assert any(e.src == harlow and e.dst == mira and e.type == "ally_of" for e in edges)
    assert not any(e.type == "located_in" for e in edges)


def test_wave2_kind_invalid_edges_fail_after_repair(world: str) -> None:
    """Wave-2's edges are load-bearing: still-invalid after the ONE repair
    FAILS the job naming the edge — the wave-1 core stays committed."""
    wave2 = _wave2_output()
    wave2["edges"][1] = {"src": "N1", "dst": "C1", "type": "located_in"}
    responses = [
        json.dumps(_wave1_output()),
        json.dumps(wave2),
        json.dumps({"edges": [{"src": "N1", "dst": "C1", "type": "located_in"}]}),
    ]
    job_id = _enqueue(world, notes="Captain Harlow docks at the Rat")
    run_next_job(provider=lambda prompt, settings: responses.pop(0), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "edge-kind rule violations" in (job.error or "")
    assert "located_in" in (job.error or "")
    with session_scope() as session:
        assert len(list(revision_chain(session, world))) == 1  # wave-1 only


def test_wave2_mutual_member_of_against_world_fixed(world: str) -> None:
    """The world side of the hierarchy rule: a wave-2 C-to-C member_of
    contradicting a committed membership is rejected; the repair dropping
    it commits without it."""
    responses1 = [json.dumps(_wave1_output())]
    _enqueue(world, places=["Greymarch"], key_figures=["Mira"], notes="")
    run_next_job(provider=lambda prompt, settings: responses1.pop(0), settings=SETTINGS)
    responses2 = [
        json.dumps(_wave1_output()),
        json.dumps(
            {
                "entities": [
                    {
                        "ref": "N0",
                        "kind": "place",
                        "name": "The Drowned Quay",
                        "text": "silt and moorings",
                    }
                ],
                "edges": [
                    {"src": "C1", "dst": "C0", "type": "member_of"},
                    {"src": "N0", "dst": "C0", "type": "relationship"},
                ],
            }
        ),
        json.dumps({"edges": []}),
    ]
    job_id2 = _enqueue(world, places=["Greymarch"], key_figures=["Mira"], notes="still circles")
    run_next_job(provider=lambda prompt, settings: responses2.pop(0), settings=SETTINGS)
    job2, _position = job_status(job_id2)
    assert job2.state == "succeeded"
    assert (job2.result or {})["entity_count"] == 3  # 2 wave-1 + The Drowned Quay
    with session_scope() as session:
        rows = {e.name: e.id for e in world_entities(session, world)}
        edges = list(world_edges(session, world))
    bar, mira = rows["The Gilded Bar"], rows["Mira Vane"]
    member = [(e.src, e.dst) for e in edges if e.type == "member_of"]
    assert (bar, mira) in member  # the original direction stands
    assert (mira, bar) not in member  # the contradiction never committed
    assert any(
        e.src == rows["The Drowned Quay"] and e.dst == bar and e.type == "relationship"
        for e in edges
    )
