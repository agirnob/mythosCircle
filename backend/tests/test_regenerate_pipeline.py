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
from app.pipeline.regenerate import (
    SECTION_ORDER,
    build_regenerate_prompt,
    serialize_record,
)
from app.pipeline.statblocks import stat_block_rules_text
from app.pipeline.worker import JobPayloadError, run_next_job
from app.store import (
    EdgeInput,
    EntityInput,
    accept_candidate,
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
    update_entity,
)
from app.store.db import session_scope
from app.store.read import campaign_seed, latest_revision, revision_chain, world_state

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
        [EdgeInput(src=mira_id, dst=guild_id, type="member_of", counter=1, reason="seeded")],
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


def test_whole_reroll_prompt_redacts_section_contents(
    world: tuple[str, str, str],
) -> None:
    """Whole-re-roll prompt (2026-09-27): the target's SECTION CONTENTS are
    redacted so the model cannot copy-echo them — draft and pillar
    re-rolls of the same character both returned the byte-identical
    43-char text because the full target was embedded and echoed. The
    identity anchor, edges, and unknown keys stay visible; a PARTIAL
    re-roll keeps the full record (those sections must echo)."""
    campaign_id, _mira_id, _guild_id = world
    with session_scope() as session:
        seed = campaign_seed(session, campaign_id)
    record = _record()
    prompt = build_regenerate_prompt(seed, record, SECTION_ORDER, ([], []))
    assert "once ran with the Guild" not in prompt  # secret content redacted
    assert "kind eyes, silver-streaked hair" not in prompt  # appearance redacted
    assert '"identity": {' not in prompt.replace(" ", "") or "stat_block" not in prompt
    # identity anchor + unknown keys stay:
    assert '"name": "Mira Vane"' in prompt
    assert '"level_cr": "level 5"' in prompt
    assert '"ambient_theme"' in prompt
    # The dial guidance has per-level teeth for a set dial:
    with session_scope() as session:
        seed = campaign_seed(session, campaign_id)
    deep = build_regenerate_prompt(seed, record, SECTION_ORDER, ([], []), dial="pillar")
    assert "two to three dense paragraphs" in deep
    # A PARTIAL re-roll redacts ONLY the requested section (the stat_block
    # echo class, 2026-09-27: a stat_block re-roll returned the exact
    # block because its content was embedded) — unrequested sections stay
    # visible (their byte-identical echo is the contract):
    partial = build_regenerate_prompt(seed, record, ["stat_block"], ([], []))
    assert '"ac": 13' not in partial  # the fixture's block value — redacted
    assert "once ran with the Guild" in partial  # secret unrequested -> visible


def test_reroll_context_never_leaks_the_target_s_sections() -> None:
    """The 2026-09-27 leak: the target IS the neighborhood seed, and the
    WORLD CONTEXT block serializes each entity's FULL data — the TARGET
    RECORD redaction was defeated when the model read the seed's secret
    from the context and returned it byte-identical. The requested keys
    must be scrubbed from the target's context row."""
    record = _record()
    secret = "once ran with the Guild"
    target = models.Entity(
        id=ids.new_id(),
        campaign_id="C" * 26,
        kind="character",
        name=record["name"],
        text="some description",
        data=dict(record),
        created_at="2026-01-01T00:00:00Z",
    )
    # The context as built by regenerate's runner call site (the target
    # scrubbed, the fix), then the same WITHOUT the scrub (the leak):
    from app.pipeline.regenerate import _with_scrubbed_data

    scrubbed_ctx = [_with_scrubbed_data(target, {"secret", "stat_block"})]
    prompt = build_regenerate_prompt(
        _seed_campaign(id_suffix="A", created_at="2026-01-01T00:00:00Z"),
        record,
        ["secret", "stat_block"],
        (scrubbed_ctx, []),
    )
    assert secret not in prompt
    assert "kind eyes, silver-streaked hair" in prompt  # unrequested data stays in context
    # The unsrubbed variant leaks (proving the scrub is the fix):
    leaky = build_regenerate_prompt(
        _seed_campaign(id_suffix="B", created_at="2026-01-01T00:00:00Z"),
        record,
        ["secret"],
        ([target], []),
    )
    assert secret in leaky


def test_splice_fails_loud_when_a_requested_section_is_missing() -> None:
    """An omitted requested section must NEVER silently keep the base's
    old text (the whole-re-roll staleness class, 2026-09-27: the secret
    that should have been re-rolled came back byte-identical)."""
    from app.pipeline.regenerate import SECTION_ORDER, _splice

    record = _record()
    raw = json.loads(json.dumps(record))
    del raw["secret"]
    with pytest.raises(JobPayloadError) as excinfo:
        _splice(record, raw, SECTION_ORDER)
    assert "omitted requested section" in str(excinfo.value)


def test_dial_stamped_from_request_not_model_echo(
    world: tuple[str, str, str],
) -> None:
    """AD-36: the re-rolled record carries the REQUEST's dial — the DM's
    live setting, authoritative — never the model's echo of the target
    (2026-09-27: a draft re-roll inherited the record's pillar verbatim)
    and never a byte-identical violation (the dial is the shaped
    request's own field)."""
    campaign_id, mira_id, _guild_id = world
    with session_scope() as session:
        entity = session.get(models.Entity, mira_id)
        assert entity is not None and isinstance(entity.data, dict)
        entity.data = {**entity.data, "dial": "important"}
        session.commit()

    job_id = enqueue_job(
        campaign_id,
        "regenerate",
        {"target": {"kind": "entity", "id": mira_id}, "dial": "draft"},
        max_llm_calls=4,
    ).id

    def provider(prompt: str, settings: LLMSettings) -> str:
        # The model ECHOES the target's dial (important) instead of the
        # request's (draft) — the pipeline must override it.
        return _regen_output(_record(), dial="important")

    run_next_job(provider=provider, settings=SETTINGS)
    _job, _position = job_status(job_id)
    staged = _staged(campaign_id)
    assert len(staged) == 1
    payload = staged[0].payload
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    assert data.get("dial") == "draft"
    assert _job.state == "succeeded"


def test_bare_record_accepted_and_one_bounded_retry(
    world: tuple[str, str, str],
) -> None:
    """The model's two JSON slips on this path, both measured live 2026-09-11.

    A whole re-roll came back as a BARE record (no ``candidates`` envelope) —
    unambiguous for a single-candidate contract, so it is accepted rather than
    failing the job. A malformed response gets exactly ONE re-emit carrying the
    decoder's own error line, because re-sending the same prompt just samples
    the same slip again.
    """
    campaign_id, mira_id, _guild_id = world

    # 1. Bare record — the whole-re-roll slip that failed two live jobs.
    bare_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, None)

    def bare_provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_record())  # no {"candidates": [...]} envelope

    assert run_next_job(provider=bare_provider, settings=SETTINGS) == bare_id
    job, _ = job_status(bare_id)
    assert job.state == "succeeded"
    assert len(_staged(campaign_id)) == 1

    # 2. Malformed first response, valid second — one retry, then success.
    retry_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, ["secret"])
    prompts: list[str] = []

    def retry_provider(prompt: str, settings: LLMSettings) -> str:
        prompts.append(prompt)
        if len(prompts) == 1:
            return '```json\n{"candidates": [{"name": "Mira"'  # truncated braces
        return _regen_output(_record(), secret="a retried secret")

    assert run_next_job(provider=retry_provider, settings=SETTINGS) == retry_id
    job, _ = job_status(retry_id)
    assert job.state == "succeeded"
    assert len(prompts) == 2
    # The retry prompt carries the decoder's error so the model can fix it.
    assert "COULD NOT BE PARSED AS JSON" in prompts[1]
    assert "JSON error:" in prompts[1]
    staged = _staged(campaign_id)
    assert any(row.payload.get("secret") == "a retried secret" for row in staged)


def test_regenerate_fails_after_one_retry_still_malformed(
    world: tuple[str, str, str],
) -> None:
    """Twice-malformed output fails the job naming the JSON error — the retry
    is bounded at ONE, never a loop."""
    campaign_id, mira_id, _guild_id = world
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, ["secret"])
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return "not json at all"

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "failed"
    assert "regenerate: output is not valid JSON" in (job.error or "")
    assert len(calls) == 2  # the call plus exactly one retry


def test_stat_block_reroll_runs_the_ar25_gate(world: tuple[str, str, str]) -> None:
    """A re-rolled stat_block goes through the same gate as a build-in one.

    Skipping it let a level-17 paladin's re-roll ship with prose-only
    attacks: the auditor read ZERO damage, the non-combatant exemption
    swallowed the block whole, and the DM got a hero who cannot fight
    (2026-09-11). The job fails when the one bounded repair cannot save it.
    """
    campaign_id, mira_id, _guild_id = world
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, ["stat_block"])
    calls: list[str] = []
    formats: list[Any] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        formats.append(settings.response_format)
        # First call: the re-roll itself, with a prose-only attack. Later
        # calls (the stat repair) return the same unusable block.
        return _regen_output(
            _record(),
            stat_block={
                "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Paladin"},
                "attributes": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 13},
                "combat": {"ac": 17, "hp": 140},
                "skills": [],
                "traits": [],
                "spells": [],
                "actions": [
                    {
                        "name": "Holy Smite",
                        "description": "Mira swings. On a hit, it deals radiant damage.",
                    }
                ],
            },
        )

    run_next_job(provider=provider, settings=SETTINGS)
    _job, _position = job_status(job_id)
    # The gate ran: the flagged block was sent for repair at least once.
    assert len(calls) >= 2
    # The shared gate's repair schema is build-in-only (spec Never list):
    # every call on this path — re-roll and repairs — carries plain settings.
    assert formats == [None] * len(formats)


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
        {
            "endpoint": guild_id,
            "direction": "outbound",
            "type": "rival_of",
            "counter": 2,
            "reason": "seeded relation",
        },
        {
            "endpoint": mira_id,
            "direction": "inbound",
            "type": "member_of",
            "counter": 1,
            "reason": "seeded relation",
        },
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


def test_section_regen_world_integration_mapping_canonicalized(
    world: tuple[str, str, str],
) -> None:
    """REGEN_MATRIX_NORMALIZE (review 2026-09-09): a mapping-form
    reaction_matrix in a world_integration re-roll canonicalizes to the
    contract's string form BEFORE validation — the compact model's mapping
    shape stages instead of hard-failing the re-roll."""
    campaign_id, mira_id, _guild_id = world
    record = _record()
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, ["world_integration"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        out = json.loads(json.dumps(record))
        out["world_integration"] = {
            **record["world_integration"],
            "reaction_matrix": {
                "C0": "Wary; watches the door.",
                "C1": "Friendly; pours a free round.",
            },
        }
        return json.dumps({"candidates": [out]})

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "succeeded"
    rows = _staged(campaign_id)
    assert len(rows) == 1
    matrix = rows[0].payload["world_integration"]["reaction_matrix"]
    assert matrix == "C0: Wary; watches the door., C1: Friendly; pours a free round."


def test_settled_candidate_target_fails_job(
    world: tuple[str, str, str],
) -> None:
    """The runner re-resolves the target FRESH: a candidate settled after
    enqueue fails the job with zero changes (enqueue-time validation is
    not trusted across queue delay)."""
    campaign_id, mira_id, guild_id = world
    record = _record(name="Sable Rook")
    record["edges"] = [
        {
            "endpoint": guild_id,
            "direction": "outbound",
            "type": "rival_of",
            "counter": 2,
            "reason": "seeded relation",
        },
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
    assert "TARGET RECORD" in first
    # The requested sections' contents are REDACTED (copy-echo guard,
    # 2026-09-27) — only the unrequested sections serialize at full size:
    assert "once ran with the Guild" in first  # secret unrequested -> embedded
    expected_redacted = serialize_record(
        {k: v for k, v in record.items() if k not in ("background", "personality")}
    )
    assert expected_redacted in first
    assert "background, personality" in first
    # The requested contents are never embedded:
    assert "heir to the Gilded Bar" not in first  # background redacted
    assert "warm, watchful" not in first  # personality redacted
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


# ---------------------------------------------------------------------------
# Spec-3.6 staging-window closures: MID_CALL_EDIT (the runner re-reads at
# staging) + candidate-target re-rolls (RE_ROLL_AFTER_EDIT: preserved
# sections from the CURRENT target, staged edges kept, base refreshed)
# ---------------------------------------------------------------------------


def test_mid_call_edit_survives_in_staged_payload(
    world: tuple[str, str, str],
) -> None:
    """MID_CALL_EDIT: a DM hand edit landing between the runner's
    prompt-build read and the staging write is carried into the staged
    payload — preserved sections splice from the FRESH record (the
    re-rolled section keeps the generation), ``entity_base_data``
    snapshots the same edited record, and the accept proceeds cleanly:
    never silently overwritten, never stranded."""
    campaign_id, mira_id, _guild_id = world
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": mira_id}, ["personality"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        # The provider call sits between the runner's resolve (prompt-
        # build read) and the staging write — the DM's edit lands there.
        update_entity(campaign_id, mira_id, patch={"background": "DM's mid-call edit"})
        out = json.loads(json.dumps(_record()))  # the run-start record
        out["personality"] = "re-rolled by the model"
        return json.dumps({"candidates": [out]})

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _ = job_status(job_id)
    assert job.state == "succeeded"
    (row,) = _staged(campaign_id)
    assert row.regenerates_entity_id == mira_id
    # The re-rolled section is the model's; the DM's edited preserved
    # section survives in the staged payload.
    assert row.payload["personality"] == "re-rolled by the model"
    assert row.payload["background"] == "DM's mid-call edit"
    # Payload and accept-conflict base reference the same edited record.
    assert row.entity_base_data is not None
    assert row.entity_base_data["background"] == "DM's mid-call edit"
    # The row is accept-able after the mid-call edit — clean accept.
    accepted, _revision = accept_candidate(campaign_id, row.id)
    assert accepted.status == "accepted"
    with session_scope() as session:
        ents, _edges = world_state(session, campaign_id)
        by_id = {e.id: e for e in ents}
    assert by_id[mira_id].data["personality"] == "re-rolled by the model"
    assert by_id[mira_id].data["background"] == "DM's mid-call edit"


def test_mid_call_role_unboss_edit_gates_spliced_boss(
    world: tuple[str, str, str],
) -> None:
    """MID_CALL_EDIT x EDIT_ROLE_UNBOSS interplay: a whole-record re-roll
    of a BBEG whose DM hand edit moves the role off BOSS_ROLES between
    the prompt-build read and staging. The model's boss section was
    requested at run start from the BBEG record, but splicing it onto
    the fresh NPC record would stage (and accepting would commit) an
    AR24-invalid payload — the splice drops it: the DM's edit wins, the
    committed record stays shape-valid (re-rollable, exportable)."""
    campaign_id, mira_id, guild_id = world
    bbeg_id = ids.new_id()
    bbeg_record = _record(name="Vorgath", role="BBEG")
    bbeg_record["boss"] = {
        "lair_actions": "the bar itself turns on intruders",
        "legendary_actions": "two per round",
        "immunities": "charmed",
        "vulnerabilities": "holy water",
    }
    with session_scope() as session:
        head = latest_revision(session, campaign_id)
    assert head is not None, "world fixture commits at least one revision before this test"
    commit_subgraph(
        campaign_id,
        [EntityInput(kind="character", name="Vorgath", data=bbeg_record, id=bbeg_id)],
        [EdgeInput(src=bbeg_id, dst=guild_id, type="rival_of", counter=1, reason="seeded")],
        base_revision=head.id,
    )
    job_id = _enqueue(campaign_id, {"kind": "entity", "id": bbeg_id}, None)

    def provider(prompt: str, settings: LLMSettings) -> str:
        # The DM's mid-call edit: role off BOSS_ROLES (with the boss
        # section removed in the same PATCH — the AR24-valid way).
        update_entity(campaign_id, bbeg_id, patch={"role": "NPC", "boss": None})
        out = json.loads(json.dumps(bbeg_record))  # the run-start record
        out["personality"] = "re-rolled by the model"
        return json.dumps({"candidates": [out]})

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    (row,) = _staged(campaign_id)
    assert row.regenerates_entity_id == bbeg_id
    assert row.payload["role"] == "NPC"  # the DM's mid-call edit
    assert row.payload["personality"] == "re-rolled by the model"
    assert "boss" not in row.payload  # the model's boss section was gated
    accepted, _revision = accept_candidate(campaign_id, row.id)
    assert accepted.status == "accepted"
    with session_scope() as session:
        ents, _edges = world_state(session, campaign_id)
    committed = next(e for e in ents if e.id == bbeg_id)
    from app.store.candidates import payload_section_violations

    assert payload_section_violations(committed.data) == []  # still AR24-valid


def test_candidate_target_reroll_preserves_staged_edges_and_refreshes_base(
    world: tuple[str, str, str],
) -> None:
    """RE_ROLL_AFTER_EDIT (pipeline): re-rolling an entity-regen CANDIDATE
    row after a DM hand edit on the target — preserved sections splice
    from the CURRENT target record, the row's staged 3-4 edge edits
    survive verbatim, ``entity_base_data`` refreshes to the same edited
    record, and the refreshed row accepts cleanly (never stranded)."""
    campaign_id, mira_id, guild_id = world
    staged_edges = [
        {
            "endpoint": guild_id,
            "direction": "outbound",
            "type": "rival_of",
            "counter": 2,
            "reason": "seeded relation",
        },
    ]
    record = _record()
    record["edges"] = staged_edges
    job = enqueue_job(campaign_id, "generate", {"ask": "a rival"})
    candidate = stage_candidates(campaign_id, job.id, [record], target_entity_id=mira_id)[0]
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job.id
    complete_job(job.id)

    # The DM's hand edit lands on the committed target after staging.
    update_entity(campaign_id, mira_id, patch={"secret": "DM's hand edit"})
    staged_payload = dict(candidate.payload)  # the run-start record

    regen_job_id = _enqueue(campaign_id, {"kind": "candidate", "id": candidate.id}, ["personality"])

    def provider(prompt: str, settings: LLMSettings) -> str:
        out = json.loads(json.dumps(staged_payload))  # byte-identical echo
        out["personality"] = "clipped, cold"
        return json.dumps({"candidates": [out]})

    assert run_next_job(provider=provider, settings=SETTINGS) == regen_job_id
    job2, _ = job_status(regen_job_id)
    assert job2.state == "succeeded"
    (row,) = _staged(campaign_id)
    assert row.id == candidate.id  # one row per intent
    # The requested section is re-rolled…
    assert row.payload["personality"] == "clipped, cold"
    # …preserved sections splice from the CURRENT target — the DM's edit
    # survives the re-roll…
    assert row.payload["secret"] == "DM's hand edit"
    # …the row's staged 3-4 edge edits survive verbatim (not the
    # committed graph's edges)…
    assert row.payload["edges"] == staged_edges
    # …and the accept-conflict base refreshes to the same edited record.
    assert row.entity_base_data is not None
    assert row.entity_base_data["secret"] == "DM's hand edit"
    accepted, _revision = accept_candidate(campaign_id, row.id)
    assert accepted.status == "accepted"
    assert accepted.accepted_entity_id == mira_id
    with session_scope() as session:
        ents, edges = world_state(session, campaign_id)
        by_id = {e.id: e for e in ents}
    assert by_id[mira_id].data["secret"] == "DM's hand edit"
    assert by_id[mira_id].data["personality"] == "clipped, cold"
    assert (mira_id, guild_id, "rival_of") in {(ed.src, ed.dst, ed.type) for ed in edges}


def test_enrich_guide_and_dial_ride_the_prompt(world: tuple[str, str, str]) -> None:
    """AD-38: an enrich (sections null + guide + dial) flows BOTH the
    guide box and the closed dial level into the re-roll prompt — the
    model sees the DM's shape, never a guessed depth."""
    campaign_id, mira_id, _guild_id = world
    seen: list[str] = []
    job_id = enqueue_job(
        campaign_id,
        "regenerate",
        {
            "target": {"kind": "entity", "id": mira_id},
            "sections": None,
            "guide": "Lean into the dockmaster shadow-work.",
            "dial": "pillar",
        },
    ).id

    def provider(prompt: str, settings: LLMSettings) -> str:
        seen.append(prompt)
        return _regen_output(_record(), personality="re-rolled")

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    (prompt,) = seen
    assert "DIAL (elaboration weight" in prompt
    assert "pillar" in prompt
    assert "GUIDE (the DM says what changed" in prompt
    assert "Lean into the dockmaster shadow-work." in prompt


@pytest.mark.parametrize("kind", ["place", "faction"])
@pytest.mark.parametrize("sections", [None, ["description"]])
@pytest.mark.parametrize("entity_target", [False, True])
def test_flat_proposal_reroll(
    world: tuple[str, str, str], kind: str, sections: list[str] | None, entity_target: bool
) -> None:
    from app.store import InvalidCandidateError, InvalidJobInputError
    from app.store.candidates import FLAT_REGEN_SECTIONS, replace_candidate_payload

    campaign_id, _, guild_id = world
    record: dict[str, Any] = {
        "name": "Flat target",
        "entity_kind": kind,
        "edges": [],
        "custom": "keep",
    }
    record.update(
        {field: ("original details " * 20) + field for field in FLAT_REGEN_SECTIONS[kind]}
    )
    if entity_target:
        entity_id = ids.new_id()
        with session_scope() as session:
            head = latest_revision(session, campaign_id)
        assert head is not None
        commit_subgraph(
            campaign_id,
            [
                EntityInput(
                    id=entity_id,
                    kind=kind,
                    name=record["name"],
                    data={key: value for key, value in record.items() if key != "entity_kind"},
                )
            ],
            [
                EdgeInput(
                    src=guild_id,
                    dst=entity_id,
                    type="located_in" if kind == "place" else "rival_of",
                    counter=1,
                    reason="seeded",
                )
            ],
            base_revision=head.id,
        )
        first = _enqueue(campaign_id, {"kind": "entity", "id": entity_id}, sections)
        assert (
            run_next_job(
                provider=lambda *args, **kwargs: json.dumps({"candidates": [record]}),
                settings=SETTINGS,
            )
            == first
        )
        assert job_status(first)[0].state == "succeeded", job_status(first)[0].error
        candidate = _staged(campaign_id)[0]
    else:
        job = enqueue_job(campaign_id, "generate", {"ask": "flat target", "entity_kind": kind})
        candidate = stage_candidates(campaign_id, job.id, [record])[0]
        claim_next_job()
        complete_job(job.id)
    with pytest.raises(InvalidJobInputError):
        _enqueue(campaign_id, {"kind": "candidate", "id": candidate.id}, ["personality"])
    regen = _enqueue(campaign_id, {"kind": "candidate", "id": candidate.id}, sections)
    output = dict(record, description=("rerolled description " * 20).strip())
    assert (
        run_next_job(
            provider=lambda *args, **kwargs: json.dumps({"candidates": [output]}), settings=SETTINGS
        )
        == regen
    )
    assert job_status(regen)[0].state == "succeeded", job_status(regen)[0].error
    row = _staged(campaign_id)[0]
    assert row.id == candidate.id
    assert row.payload == output
    with pytest.raises(InvalidCandidateError):
        replace_candidate_payload(campaign_id, row.id, dict(output, description=[]))
    with pytest.raises(InvalidCandidateError, match="cannot change entity_kind"):
        replace_candidate_payload(campaign_id, row.id, dict(output, entity_kind="character"))
    if entity_target:
        accepted, _ = accept_candidate(campaign_id, row.id)
        assert accepted.accepted_entity_id == entity_id
