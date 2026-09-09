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

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.core import ids
from app.core.settings import LLMSettings
from app.pipeline.build_in import (
    build_wave1_prompt,
    build_wave2_prompt,
)
from app.pipeline.retrieval import retrieve_neighborhood, serialize_context
from app.pipeline.statblocks import spells_reference_text, stat_block_rules_text
from app.pipeline.worker import run_next_job
from app.store import (
    InvalidJobInputError,
    app_db_url,
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
#: the wave commits without a repair pass.
_MIRA_STAT_BLOCK: dict[str, Any] = {
    "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter", "alignment": "LG"},
    "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
    "combat": {"ac": 16, "hp": 44},
    "skills": [{"name": "Athletics", "bonus": 5}],
    "actions": [
        {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 1d8+2 slashing"}
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
            {"src": "N0", "dst": "C0", "type": "located_in"},
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
            {"src": "N0", "dst": "C0", "type": "located_in"},
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


def test_self_loop_edges_rejected(world: str) -> None:
    """A self-loop never satisfies the orphan rule: edges must connect
    distinct entities, so an all-self-loop subgraph fails naming the
    edge and commits nothing."""
    output = {
        "entities": [
            {"ref": "E0", "kind": "place", "name": "Solo"},
            {"ref": "E1", "kind": "place", "name": "Alone"},
        ],
        "edges": [
            {"src": "E0", "dst": "E0", "type": "ally_of"},
            {"src": "E1", "dst": "E1", "type": "ally_of"},
        ],
    }
    job_id = _enqueue(world, key_figures=["Solo", "Alone"])
    processed = run_next_job(
        provider=lambda prompt, settings: json.dumps(output), settings=SETTINGS
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "self-loop" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


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
# ORPHAN
# ---------------------------------------------------------------------------


def test_orphan_wave1_fails_naming_entity(world: str) -> None:
    """An entity with no edge within the wave-1 subgraph fails the job
    naming the orphan; that wave zero commits."""
    output = _wave1_output()
    output["entities"].append(
        {
            "ref": "E2",
            "kind": "character",
            "name": "Rootless Stranger",
            "data": {**_character_record("Rootless Stranger"), "stat_block": _MIRA_STAT_BLOCK},
        }
    )
    job_id = _enqueue(world, places=["Greymarch"])
    run_next_job(provider=lambda prompt, settings: json.dumps(output), settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "Rootless Stranger" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


def test_orphan_wave2_fails_but_core_stays(world: str) -> None:
    """A wave-2 entity anchored only to wave peers fails the job naming the
    orphan; the wave-1 core stays committed (documented resilience, no
    compensating undo)."""
    responses = [json.dumps(_wave1_output()), json.dumps(_wave2_output_orphan())]

    def provider(prompt: str, settings: LLMSettings) -> str:
        return responses.pop(0)

    job_id = _enqueue(world, notes="more world")
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "Nowhere Man" in (job.error or "")
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 1  # wave 2 zero commits
        entities = world_entities(session, world)
    assert {e.name for e in entities} == {"The Gilded Bar", "Mira Vane"}  # core intact


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
    # Wave 1 never sees notes; unknown keys are ignored.
    assert build_wave1_prompt(seed_a, {**payload, "notes": "secret notes"}) == first
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
    # Wave-2's new entities use N<index> refs (never wave-1's E labels)
    # and the prompt names the core anchors as C<index>.
    assert '"ref": "N<index>"' in first
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


def test_stale_base_between_waves_fails_core_stays(world: str) -> None:
    """A DM edit landing between the waves makes wave-2's staged base stale:
    StaleRevisionError -> job fails, the DM's edit and the core stay, no
    silent overwrite (AD-2)."""
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
    assert job.state == "failed"
    assert "stale" in (job.error or "").lower()
    with session_scope() as session:
        assert len(revision_chain(session, world)) == 2  # wave 1 + the DM edit
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
    """STAT_STILL_INVALID: a block still invalid after the repair pass is
    never committed — the job fails naming the character and its
    violations (fail event, AR25), zero revisions."""
    output = _wave1_output()
    bad_block = dict(_MIRA_STAT_BLOCK)
    bad_block["attributes"] = {**bad_block["attributes"], "str": 40}
    output["entities"][1]["data"] = {**_character_record("Mira Vane"), "stat_block": bad_block}
    responses = [
        json.dumps(output),
        json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]}),
    ]
    job_id = _enqueue(world, places=["Greymarch"], key_figures=["Mira"])
    run_next_job(
        provider=lambda prompt, settings, responses=responses: responses.pop(0),
        settings=SETTINGS,
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "still invalid after the repair pass" in (job.error or "")
    assert "Mira Vane" in (job.error or "") and "attributes.str" in (job.error or "")
    with session_scope() as session:
        assert revision_chain(session, world) == []


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
        "combat": {"ac": 15, "hp": 7},
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
