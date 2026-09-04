"""The regenerate runner (spec-3.5): whole-entity and per-section re-rolls.

Covers the I/O matrix's job-time behavior with fake providers: whole
entity regen (one new proposed row with ``regenerates_entity_id`` and
the committed world untouched), per-section candidate regen (same row
replaced in place), byte-identical preservation + unknown-key
pass-through by construction, MODEL_SHAPE_VIOLATION (disallowed-section
edit / broken AR24 shape), BUDGET_EXCEEDED (zero rows), cancel races
(no-op before the write; ghost discard after staging), prompt
determinism, and the runner's fresh re-resolution of the target at claim
time (settled/deleted target -> job fails, nothing staged).
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.core import ids
from app.core.settings import LLMSettings
from app.pipeline.regenerate import SECTION_ORDER, build_regenerate_prompt, serialize_record
from app.pipeline.statblocks import stat_block_rules_text
from app.pipeline.worker import run_next_job
from app.store import (
    EdgeInput,
    EntityInput,
    app_db_url,
    cancel_job,
    claim_next_job,
    commit_subgraph,
    complete_job,
    create_campaign,
    delete_entity,
    enqueue_job,
    init_db,
    job_status,
    list_candidates,
    models,
    register_account,
    reject_candidate,
    report_progress,
    stage_candidates,
)
from app.store.db import session_scope
from app.store.read import revision_chain

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")


def _owner_id() -> str:
    return register_account(f"owner-regen-{ids.new_id()}@example.com", "password123").id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[tuple[str, str, str]]:
    """A fresh scratch database, one campaign, and the committed fixture
    world (Mira + The Guild); yields (campaign_id, mira_id, guild_id)."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'regen.db'}")
    try:
        campaign_id = create_campaign(
            _owner_id(), title="Test World", description="", theme="High Fantasy", custom_lore=""
        ).id
        mira_id, guild_id = _commit_world(campaign_id)
        yield campaign_id, mira_id, guild_id
    finally:
        init_db(previous)


def _record(name: str = "Mira Vane", role: str = "NPC") -> dict[str, Any]:
    """A full AR24 sectioned record (spec-3.3) for the fixture world."""
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
        # An AR24 forward-compat unknown key: must pass through untouched.
        "ambient_theme": {"motif": "copper coins"},
    }


def _commit_world(campaign_id: str) -> tuple[str, str]:
    """Commit Mira (full AR24 record) + The Guild; returns (mira, guild)."""
    mira_id, guild_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            EntityInput(kind="character", name="Mira Vane", data=_record(), id=mira_id),
            EntityInput(kind="faction", name="The Guild", id=guild_id),
        ],
        [EdgeInput(src=mira_id, dst=guild_id, type="member_of", counter=1)],
        base_revision=None,
    )
    return mira_id, guild_id


def _enqueue(
    campaign_id: str,
    target: dict[str, Any],
    sections: list[str] | None = None,
    *,
    max_llm_calls: int | None = None,
) -> str:
    payload: dict[str, Any] = {"target": target}
    if sections is not None:
        payload["sections"] = sections
    return enqueue_job(campaign_id, "regenerate", payload, max_llm_calls=max_llm_calls).id


def _regen_output(record: dict[str, Any], **overrides: Any) -> str:
    """The fake provider's output: the record with ``overrides`` applied."""
    out = json.loads(json.dumps(record))
    out.update(overrides)
    return json.dumps({"candidates": [out]})


def _staged(campaign_id: str) -> list[models.ProposedCandidate]:
    rows, _cursor = list_candidates(campaign_id)
    return rows


def _revision_count(campaign_id: str) -> int:
    with session_scope() as session:
        return len(list(revision_chain(session, campaign_id)))


def test_whole_entity_regen_stages_new_row_world_untouched(
    world: tuple[str, str, str],
) -> None:
    """WHOLE_REGEN_ENTITY: sections=null re-rolls every regenerable
    content section into ONE new proposed row carrying
    ``regenerates_entity_id``; the committed Mira is untouched (no new
    revision); edges stage as [] (the existing edges are the anchor)."""
    campaign_id, mira_id, _guild_id = world
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, None)

    def provider(prompt: str, settings: LLMSettings) -> str:
        assert "TARGET RECORD" in prompt and "REQUESTED SECTIONS" in prompt
        return _regen_output(
            _record(),
            personality="new personality",
            secret="new secret",
            appearance="  new appearance  ",
            background="new background",
        )

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "succeeded"
    rows = _staged(campaign_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.regenerates_entity_id == mira_id
    assert row.payload["personality"] == "new personality"
    assert row.payload["appearance"] == "new appearance"  # trimmed
    # Preserved by construction — byte-identical to the target record:
    for key, value in _record().items():
        if key not in ("personality", "secret", "appearance", "background"):
            assert row.payload[key] == value, key
    # Unknown keys pass through (AR24 forward compat):
    assert row.payload["ambient_theme"] == {"motif": "copper coins"}
    assert row.payload["edges"] == []
    # The committed world is untouched — one revision only (the seed).
    assert _revision_count(campaign_id) == 1


def test_section_regen_candidate_replaces_row_in_place(
    world: tuple[str, str, str],
) -> None:
    """SECTION_REGEN_CANDIDATE: re-rolling one section replaces THAT row's
    payload in place — same row id, every other section + unknown keys +
    edges byte-identical, no new rows (one row per intent)."""
    campaign_id, mira_id, guild_id = world
    record = _record(name="Sable Rook")
    record["edges"] = [
        {"endpoint": guild_id, "direction": "outbound", "type": "rival_of", "counter": 2},
        {"endpoint": mira_id, "direction": "inbound", "type": "member_of", "counter": 1},
    ]
    job = enqueue_job(campaign_id, "generate", {"ask": "a rival"})
    candidate = stage_candidates(campaign_id, job.id, [record])[0]
    # Settle the staging job so claim reaches the regen job.
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job.id
    complete_job(job.id)

    regen_job_id = _enqueue(campaign_id, {"kind": "candidate", "id": candidate.id}, ["personality"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        out = json.loads(json.dumps(record))
        out["personality"] = "clipped, cold"
        return json.dumps({"candidates": [out]})

    assert run_next_job(provider=provider, settings=SETTINGS) == regen_job_id
    job2, _ = job_status(regen_job_id)
    assert job2.state == "succeeded"
    rows = _staged(campaign_id)
    assert [row.id for row in rows] == [candidate.id]  # one row per intent
    row = rows[0]
    assert row.job_id == job.id  # the row keeps its ORIGINAL generate job
    assert row.payload["personality"] == "clipped, cold"
    for key, value in record.items():
        if key != "personality":
            assert row.payload[key] == value, key
    assert row.payload["edges"] == record["edges"]  # staged edges byte-identical
    assert row.payload["ambient_theme"] == {"motif": "copper coins"}


def test_disallowed_section_edit_fails_job_zero_rows(
    world: tuple[str, str, str],
) -> None:
    """MODEL_SHAPE_VIOLATION: a model that edits a non-requested section
    (the identity anchor here) fails the job; nothing staged, no new
    revision."""
    campaign_id, mira_id, _guild_id = world
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, ["personality"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        return _regen_output(_record(), name="Renamed Mira")  # name must not change

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "failed"
    assert "byte-identical" in (job.error or "")
    assert _staged(campaign_id) == []
    assert _revision_count(campaign_id) == 1


def test_off_contract_output_fails_job(
    world: tuple[str, str, str],
) -> None:
    """Malformed, empty, or shape-violating output fails the job with
    zero staged rows."""
    campaign_id, mira_id, _guild_id = world
    bad_outputs = (
        "not json",
        '{"candidates": []}',
        '{"candidates": [{"name": "x"}]}',
        json.dumps({"candidates": [{**_record(), "personality": None}]}),
    )
    for bad in bad_outputs:
        job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, ["personality"])

        def provider(prompt: str, settings: LLMSettings, bad: str = bad) -> str:
            return bad

        assert run_next_job(provider=provider, settings=SETTINGS) == job_id
        job, _ = job_status(job_id)
        assert job.state == "failed"
        assert _staged(campaign_id) == []


def test_budget_exceeded_fails_zero_rows(
    world: tuple[str, str, str],
) -> None:
    """BUDGET_EXCEEDED (AR21): max_llm_calls=0 refuses the single provider
    call BEFORE any HTTP request; the job fails, nothing stages."""
    campaign_id, mira_id, _guild_id = world
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, None, max_llm_calls=0)

    def provider(prompt: str, settings: LLMSettings) -> str:
        raise AssertionError("the call must be refused")

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "failed"
    assert "budget" in (job.error or "").lower()
    assert _staged(campaign_id) == []


def test_cancel_before_write_is_noop(
    world: tuple[str, str, str],
) -> None:
    """A cancel landing inside the provider call is a no-op: no rows, no
    replacement, the job stays cancelled (never failed/succeeded)."""
    campaign_id, mira_id, _guild_id = world
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, ["personality"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        cancel_job(job_id)
        return _regen_output(_record(), personality="changed")

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "cancelled"
    assert _staged(campaign_id) == []


def test_cancel_after_staging_discards_ghost_rows(
    world: tuple[str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancel racing the terminal write after staging leaves ghost
    rows — the runner discards them (generate's pattern); the job stays
    cancelled and the accept screen would serve nothing."""
    from app.pipeline import regenerate as regen_module

    campaign_id, mira_id, _guild_id = world
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, None)
    real_progress = report_progress

    def provider(prompt: str, settings: LLMSettings) -> str:
        return _regen_output(_record(), personality="new")

    def cancel_then_progress(job_id_: str, progress: float) -> Any:
        cancel_job(job_id_)
        real_progress(job_id_, progress)  # raises the conflict

    monkeypatch.setattr(regen_module, "report_progress", cancel_then_progress)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "cancelled"
    assert _staged(campaign_id) == []  # the ghost rows are gone


def test_section_regen_entity_new_row_sections_regenerated_rest_identical(
    world: tuple[str, str, str],
) -> None:
    """SECTION_REGEN_ENTITY: an entity target with
    ``sections=["personality","stat_block"]`` stages a NEW row with
    ``regenerates_entity_id`` set; exactly the two requested sections are
    regenerated (overridden), every other section is byte-identical to
    the committed record, edges are empty; the committed entity is
    untouched (no new revision)."""
    campaign_id, mira_id, _guild_id = world
    record = _record()
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, ["personality", "stat_block"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        out = json.loads(json.dumps(record))
        out["personality"] = "re-rolled personality"
        out["stat_block"] = {
            "identity": {"role": "NPC", "level": 6, "race": "Human", "alignment": "NG"},
            "attributes": {"str": 13, "dex": 12, "con": 13, "int": 13, "wis": 14, "cha": 15},
            "combat": {"ac": 14, "hp": 31},
        }
        return json.dumps({"candidates": [out]})

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "succeeded"
    rows = _staged(campaign_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.regenerates_entity_id == mira_id
    assert row.payload["personality"] == "re-rolled personality"
    assert row.payload["stat_block"]["combat"] == {"ac": 14, "hp": 31}
    for key, value in record.items():
        if key in ("personality", "stat_block"):
            continue
        assert row.payload[key] == value, key
    assert row.payload["edges"] == []
    assert _revision_count(campaign_id) == 1


def test_settled_candidate_target_fails_job(
    world: tuple[str, str, str],
) -> None:
    """The runner re-resolves the target FRESH: a candidate settled after
    enqueue fails the job with zero changes (enqueue-time validation is
    not trusted across queue delay)."""
    campaign_id, mira_id, guild_id = world
    record = _record(name="Sable Rook")
    record["edges"] = [
        {"endpoint": guild_id, "direction": "outbound", "type": "rival_of", "counter": 2},
    ]
    job = enqueue_job(campaign_id, "generate", {"ask": "a rival"})
    candidate = stage_candidates(campaign_id, job.id, [record])[0]
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job.id
    complete_job(job.id)
    regen_job_id = _enqueue(campaign_id, {"kind": "candidate", "id": candidate.id}, ["personality"])
    # Settle AFTER enqueue: the enqueue-time validation requires a
    # proposed row; the runner's fresh re-resolution sees the rejection.
    reject_candidate(campaign_id, candidate.id)

    def provider(prompt: str, settings: LLMSettings) -> str:
        raise AssertionError("a settled target must never reach the call")

    assert run_next_job(provider=provider, settings=SETTINGS) == regen_job_id
    job2, _ = job_status(regen_job_id)
    assert job2.state == "failed"
    assert "rejected" in (job2.error or "")
    assert _staged(campaign_id) == []  # rejected rows are not proposed


def test_deleted_entity_target_fails_job(
    world: tuple[str, str, str],
) -> None:
    """An entity deleted after enqueue fails the job fresh — regeneration
    never operates on a ghost."""
    campaign_id, mira_id, _guild_id = world
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, None)
    delete_entity(campaign_id, mira_id, cascade=True)

    def provider(prompt: str, settings: LLMSettings) -> str:
        raise AssertionError("a deleted target must never reach the call")

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "failed"
    assert "no longer" in (job.error or "")
    assert _staged(campaign_id) == []


# ---------------------------------------------------------------------------
# DETERMINISM (AR6/AD-16)
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


def _context() -> tuple[list[models.Entity], list[models.Edge]]:
    guild = models.Entity(
        id=ids.new_id(),
        campaign_id="C" * 26,
        kind="faction",
        name="The Guild",
        text=None,
        data={"economy": {"level": 3}},
        created_at="2026-01-01T00:00:00Z",
    )
    edge = models.Edge(
        id=ids.new_id(),
        campaign_id="C" * 26,
        src=guild.id,
        dst=guild.id,
        type="member_of",
        counter=1,
        created_at="2026-01-01T00:00:00Z",
    )
    return [guild], [edge]


def test_build_regenerate_prompt_deterministic() -> None:
    """Same seed fields + record + requested sections + context ->
    byte-identical prompts across calls and key orders; no ids or
    timestamps leak; the requested-set order is canonical (sorted)."""
    seed_a = _seed_campaign(id_suffix="A", created_at="2026-01-01T00:00:00Z")
    record = _record()
    context = _context()
    first = build_regenerate_prompt(seed_a, record, ["personality", "background"], context)
    assert build_regenerate_prompt(seed_a, record, ["background", "personality"], context) == first
    assert (
        build_regenerate_prompt(seed_a, record, ["personality", "background"], _context()) == first
    )
    # A different campaign id / created_at must not change the prompt.
    seed_b = _seed_campaign(id_suffix="B", created_at="2099-12-31T23:59:59Z")
    assert build_regenerate_prompt(seed_b, record, ["personality", "background"], context) == first
    # Serialization is canonical; ids and timestamps never leak.
    assert context[0][0].id not in first
    assert seed_a.id not in first and seed_a.created_at not in first
    assert "TARGET RECORD" in first and serialize_record(record) in first
    assert "background, personality" in first
    assert "CAMPAIGN SEED" in first and "COMMITTED WORLD CONTEXT" in first
    assert "EDGE VOCABULARY" in first and "debt: amount" in first
    assert '"candidates"' in first and "byte-identical" in first
    # A personality-only re-roll does NOT embed the stat-block machinery
    # (the byte-identical stat block is already in the target record) —
    # but requesting the stat_block does.
    assert "STAT BLOCKS" not in first
    assert stat_block_rules_text() not in first
    stat_prompt = build_regenerate_prompt(seed_a, record, ["stat_block"], context)
    assert "STAT BLOCKS" in stat_prompt and stat_block_rules_text() in stat_prompt
    assert "SRD SPELLS BY CLASS" in stat_prompt
    # The canonical order is the sorted REGEN_SECTIONS order.
    assert tuple(sorted(SECTION_ORDER)) == SECTION_ORDER
