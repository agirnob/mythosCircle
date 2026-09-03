"""The generate runner (spec-3.1): plain-language ask -> 2-3 candidates.

Covers the I/O matrix rows HAPPY_PATH, ASK_EMPTY_WORLD, FEWER_THAN_TWO,
BAD_EDGE, INVALID_STATS, BUDGET_EXCEEDED and DETERMINISM — with fake
providers, no live LLM — plus the store contract: generate payload
validation, the widened ``ck_job_kind`` CHECK (+ the pre-3.1 job-table
rebuild), proposed-candidate staging (all-or-nothing, edge-endpoint
backstop), pagination, and AR7 invisibility (world reads and the
revision chain never see a staged candidate).
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.core import ids
from app.core.pagination import encode_cursor
from app.core.settings import LLMSettings
from app.pipeline.generate import build_generate_prompt
from app.pipeline.retrieval import retrieve_neighborhood
from app.pipeline.statblocks import stat_block_rules_text
from app.pipeline.worker import run_next_job
from app.store import (
    DuplicateJobError,
    EdgeInput,
    EntityInput,
    InvalidCandidateError,
    InvalidJobInputError,
    JobNotFoundError,
    UnknownCampaignError,
    app_db_url,
    campaign_seed,
    cancel_job,
    commit_subgraph,
    create_campaign,
    delete_campaign,
    enqueue_job,
    init_db,
    job_status,
    list_candidates,
    models,
    register_account,
    report_progress,
    stage_candidates,
    undo,
)
from app.store.db import get_engine, session_scope
from app.store.read import latest_revision, world_edges, world_entities

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")


def _owner_id() -> str:
    """One owner account per scratch DB for campaign creation (spec-1.6)."""
    return register_account(f"owner-generate-{ids.new_id()}@example.com", "password123").id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one EMPTY campaign; yields its id.

    Tests commit their own world via ``_commit_world`` so the
    empty-world vs committed-world distinction stays explicit.
    """
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'world.db'}")
    try:
        yield create_campaign(
            _owner_id(), title="Test World", description="", theme="High Fantasy", custom_lore=""
        ).id
    finally:
        init_db(previous)


#: A valid AR25 minimal stat block (spec-2.4's Mira Vane shape).
_VALID_STAT_BLOCK: dict[str, Any] = {
    "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter", "alignment": "LG"},
    "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
    "combat": {"ac": 16, "hp": 44},
    "skills": [{"name": "Athletics", "bonus": 5}],
    "actions": [
        {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 1d8+2 slashing"}
    ],
}


def _commit_world(campaign_id: str) -> tuple[list[models.Entity], list[models.Edge]]:
    """Commit a small world: a faction and a character wired by one typed
    edge. Returns the materialized rows (world rowid order)."""
    bar_id, mira_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            EntityInput(kind="faction", name="The Gilded Bar", text="smoke and coin", id=bar_id),
            EntityInput(
                kind="character",
                name="Mira Vane",
                data={"stat_block": _VALID_STAT_BLOCK},
                id=mira_id,
            ),
        ],
        [EdgeInput(src=bar_id, dst=mira_id, type="member_of", counter=1)],
    )
    with session_scope() as session:
        return list(world_entities(session, campaign_id)), list(world_edges(session, campaign_id))


def _generate_output(total: int = 3) -> dict[str, Any]:
    """A valid generate output: ``total`` candidates, each anchored to the
    committed context (C0/C1)."""
    names = ["Corvin Ashe", "Sister Yeva", "The Tallyman"]
    return {
        "candidates": [
            {
                "name": names[index % len(names)],
                "role": "NPC",
                "personality": "dry, watchful",
                "secret": "owes the Guild a debt",
                "rumor": "seen at the docks at night",
                "party_hook": "hires the party to guard a shipment",
                "stat_block": _VALID_STAT_BLOCK,
                "edges": [
                    {"endpoint": "C0", "direction": "outbound", "type": "rival_of", "counter": 1},
                    {"endpoint": "C1", "direction": "inbound", "type": "member_of"},
                ],
            }
            for index in range(total)
        ]
    }


def _enqueue(world: str, *, max_llm_calls: int | None = None, ask: str = "a rival for Mira") -> str:
    """Enqueue one generate job; returns its id."""
    return enqueue_job(
        world, "generate", {"ask": "a rival for Mira"}, max_llm_calls=max_llm_calls
    ).id


def _run(world: str, provider: Any, *, max_llm_calls: int | None = None) -> str:
    """Enqueue one generate job and drain it with ``provider``."""
    job_id = enqueue_job(
        world, "generate", {"ask": "a rival for Mira"}, max_llm_calls=max_llm_calls
    ).id
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    return job_id


def _staged(world: str) -> list[models.ProposedCandidate]:
    rows, _cursor = list_candidates(world)
    return rows


def _revision_count(campaign_id: str) -> int:
    with session_scope() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(models.Revision)
                .where(models.Revision.campaign_id == campaign_id)
            )
            or 0
        )


# ---------------------------------------------------------------------------
# HAPPY_PATH + AR7 invisibility
# ---------------------------------------------------------------------------


def test_happy_path_stages_three_candidates(world: str) -> None:
    """3 valid candidates -> 3 ProposedCandidate rows with the full AR19
    shape and edges resolved to committed endpoint ids; the job result
    names the staged ids; progress 1.0; the world graph untouched."""
    before_entities, before_edges = _commit_world(world)
    before_revision_count = _revision_count(world)
    job_id = _run(world, lambda prompt, settings: json.dumps(_generate_output()))

    job, _position = job_status(job_id)
    assert job.state == "succeeded" and job.progress == 1.0
    assert job.result is not None
    assert len(job.result["candidate_ids"]) == 3
    assert job.result["candidate_count"] == 3

    rows = _staged(world)
    assert [row.id for row in rows] == job.result["candidate_ids"]
    committed = {entity.id for entity in before_entities}
    for row in rows:
        assert row.campaign_id == world and row.job_id == job_id
        assert row.kind == "entity" and row.status == "proposed"
        payload = row.payload
        assert set(payload) >= {
            "name",
            "role",
            "personality",
            "secret",
            "rumor",
            "party_hook",
            "stat_block",
            "edges",
        }
        assert payload["role"] in {"NPC", "BBEG", "Monster"}
        assert payload["stat_block"] == _VALID_STAT_BLOCK
        assert len(payload["edges"]) == 2
        for edge in payload["edges"]:
            assert edge["endpoint"] in committed  # far endpoint is committed world state
            assert edge["type"] in {"rival_of", "member_of"}
            assert edge["direction"] in {"outbound", "inbound"}
            assert edge["counter"] == 1
    # AR7: the committed world is exactly as before the job ran.
    with session_scope() as session:
        entities = list(world_entities(session, world))
        edges = list(world_edges(session, world))
    assert [e.id for e in entities] == [e.id for e in before_entities]
    assert [e.id for e in edges] == [e.id for e in before_edges]
    assert _revision_count(world) == before_revision_count


def test_candidate_extra_keys_pass_through(world: str) -> None:
    """AR24 forward compatibility: unknown candidate keys pass through
    unvalidated into the staged payload."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][0]["ambient_detail"] = {"cloak": "grey"}
    _run(world, lambda prompt, settings: json.dumps(output))
    rows = _staged(world)
    assert len(rows) == 3
    assert rows[0].payload["ambient_detail"] == {"cloak": "grey"}


# ---------------------------------------------------------------------------
# ASK_EMPTY_WORLD + payload validation + kind CHECK/migration
# ---------------------------------------------------------------------------


def test_empty_world_submit_rejected_no_job(world: str) -> None:
    """ASK_EMPTY_WORLD: a generate ask on a campaign with zero committed
    entities is a 422-family rejection with zero rows written."""
    with pytest.raises(InvalidJobInputError, match="committed entity"):
        enqueue_job(world, "generate", {"ask": "give me a rival"})
    with session_scope() as session:
        count = session.scalar(select(func.count()).select_from(models.Job))
    assert count == 0


def test_generate_payload_validation(world: str) -> None:
    """The payload is exactly {'ask': str}: non-blank, length-capped."""
    _commit_world(world)
    bad_payloads: list[dict[str, Any]] = [
        {"ask": "   "},
        {"ask": 42},
        {"ask": "valid", "extra": "key"},
        {},
        {"ask": "x" * 2001},
    ]
    for payload in bad_payloads:
        with pytest.raises(InvalidJobInputError):
            enqueue_job(world, "generate", payload)
    with session_scope() as session:
        count = session.scalar(select(func.count()).select_from(models.Job))
    assert count == 0
    # The cap boundary: exactly 2000 trimmed chars is accepted.
    job = enqueue_job(world, "generate", {"ask": "a" * 2000})
    assert job.kind == "generate" and job.state == "queued"


def test_job_kind_check_admits_generate(world: str) -> None:
    """The widened CHECK admits ``generate`` and still rejects unknown
    kinds at the DB level."""
    _commit_world(world)
    job = enqueue_job(world, "generate", {"ask": "an ask"})
    assert job.kind == "generate"
    db_file = str(get_engine().url).removeprefix("sqlite:///")
    raw = sqlite3.connect(db_file)
    try:
        ddl = raw.execute("SELECT sql FROM sqlite_master WHERE name='job'").fetchone()[0]
        assert "'generate'" in ddl
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO job (id, campaign_id, kind, payload, state, progress,"
                " max_llm_calls, max_media_calls, created_at)"
                " VALUES ('x', ?, 'bogus', '{}', 'queued', 0.0, 3, 3, 'now')",
                (world,),
            )
    finally:
        raw.close()


@pytest.mark.parametrize(
    ("legacy_kinds", "legacy_label"),
    [
        ("('text','image','video')", "pre-2.1 3-kind list"),
        ("('text','image','video','build_in')", "2.1 list missing generate"),
    ],
)
def test_migrate_job_kind_adds_generate(
    tmp_path: Path, legacy_kinds: str, legacy_label: str
) -> None:
    """Both legacy job-kind CHECK shapes (spec-2.1's 3-kind list and the
    2.1 list missing ``generate``) are rebuilt by ONE pass to admit every
    current kind; rows and indexes survive; re-init is idempotent."""
    db_path = tmp_path / f"old-schema-{legacy_label.replace(' ', '-')}.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        f"""
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
            CONSTRAINT ck_job_kind CHECK (kind IN {legacy_kinds})
        );
        CREATE INDEX ix_job_state ON job (state);
        CREATE INDEX ix_job_campaign_id ON job (campaign_id);
        """
    )
    raw.commit()
    raw.close()

    previous = app_db_url()
    init_db(f"sqlite:///{db_path}")
    try:
        campaign = create_campaign(
            _owner_id(),
            title="Migrated World",
            description="",
            theme="High Fantasy",
            custom_lore="",
        ).id
        # A committed world so generate submissions pass the empty-world gate.
        _commit_world(campaign)
        job = enqueue_job(campaign, "generate", {"ask": "a rival"})
        assert job.kind == "generate" and job.state == "queued"
        with session_scope() as session:
            ddl = session.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name='job'")
            ).scalar_one()
            assert "'generate'" in ddl
            index_names = set(
                session.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='job'")
                )
                .scalars()
                .all()
            )
        assert {"ix_job_state", "ix_job_campaign_id"} <= index_names
        init_db(f"sqlite:///{db_path}")  # idempotent: re-init leaves it untouched
        with session_scope() as session:
            count = session.scalar(
                select(func.count())
                .select_from(models.Job)
                .where(models.Job.campaign_id == campaign)
            )
        assert count == 1  # the generate job survived the re-init
    finally:
        init_db(previous)


# ---------------------------------------------------------------------------
# FEWER_THAN_TWO / BAD_EDGE / malformed output
# ---------------------------------------------------------------------------


def test_fewer_than_two_fails_naming_count(world: str) -> None:
    """FEWER_THAN_TWO: only one candidate arrives -> the job fails with a
    structured error naming the count; nothing staged."""
    _commit_world(world)
    job_id = _run(world, lambda prompt, settings: json.dumps(_generate_output(1)))
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "1 valid candidate(s) survived validation, need 2" in (job.error or "")
    assert _staged(world) == []


def test_fewer_than_two_after_shape_violations(world: str) -> None:
    """Two of three candidates miss required AR19 fields -> only one
    survives -> job fails naming the dropped candidates; nothing staged."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][0]["secret"] = ""  # blank triple field
    del output["candidates"][1]["personality"]
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "1 valid candidate(s) survived validation, need 2" in (job.error or "")
    assert "E0" in (job.error or "") and "E1" in (job.error or "")
    assert _staged(world) == []


def test_bad_edge_candidate_dropped_two_staged(world: str) -> None:
    """BAD_EDGE: one candidate's edges all target non-existent entities ->
    that candidate is invalid; the other two stage (2 remain valid)."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][1]["edges"] = [
        {"endpoint": "C99", "direction": "outbound", "type": "rival_of"}
    ]
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = _staged(world)
    assert [row.payload["name"] for row in rows] == ["Corvin Ashe", "The Tallyman"]


def test_bad_edge_all_candidates_fails(world: str) -> None:
    """BAD_EDGE with < 2 survivors: every candidate's edges are bad ->
    job fails with a structured error; nothing staged."""
    _commit_world(world)
    output = _generate_output()
    for candidate in output["candidates"]:
        candidate["edges"] = [{"endpoint": "C99", "direction": "outbound", "type": "rival_of"}]
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "0 valid candidate(s) survived validation, need 2" in (job.error or "")
    assert "BAD_EDGE" in (job.error or "")
    assert _staged(world) == []


def test_uninvented_edge_type_dropped(world: str) -> None:
    """An edge type outside the closed vocabulary is dropped; a candidate
    left with no valid edge is invalid (BAD_EDGE semantics)."""
    _commit_world(world)
    output = _generate_output()
    for candidate in output["candidates"]:
        candidate["edges"] = [{"endpoint": "C0", "direction": "outbound", "type": "teleports"}]
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "failed"  # no candidate keeps a valid edge
    assert "BAD_EDGE" in (job.error or "")


def test_more_than_three_candidates_sliced(world: str) -> None:
    """The prompt requests exactly 3; a model that returns 4 has only its
    first 3 validated (validation keeps 2-3, deterministically)."""
    _commit_world(world)
    output = _generate_output(4)
    output["candidates"][3] = "not an object"  # would fail validation if parsed
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert len(_staged(world)) == 3


def test_malformed_output_fails_zero_staged(world: str) -> None:
    """Non-JSON or shape-malformed output fails the job; nothing staged."""
    _commit_world(world)
    for bad in ("not json", '{"waves": []}', '{"candidates": "nope"}'):
        job_id = _run(world, lambda prompt, settings, _bad=bad: _bad)
        job, _position = job_status(job_id)
        assert job.state == "failed"
        assert _staged(world) == []


def test_malformed_repair_output_fails(world: str) -> None:
    """A repair response that is not parseable stat blocks fails the job
    (the repair pass is bounded; its output is not retried)."""
    _commit_world(world)
    output = _generate_output()
    bad_block = json.loads(json.dumps(_VALID_STAT_BLOCK))
    bad_block["attributes"]["str"] = 40
    output["candidates"][0]["stat_block"] = bad_block
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(output) if len(calls) == 1 else "garbage"

    job_id = _run(world, provider)
    assert len(calls) == 2
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert _staged(world) == []


# ---------------------------------------------------------------------------
# INVALID_STATS (one bounded repair pass) + BUDGET_EXCEEDED
# ---------------------------------------------------------------------------


def test_invalid_stat_block_repaired_in_one_pass(world: str) -> None:
    """A constraint-violating block gets exactly one repair call; the
    repaired block is what stages; two provider calls total."""
    _commit_world(world)
    output = _generate_output()
    bad_block = json.loads(json.dumps(_VALID_STAT_BLOCK))
    bad_block["attributes"]["str"] = 40
    output["candidates"][0]["stat_block"] = bad_block
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(output)
        return json.dumps({"stat_blocks": [{"ref": "E0", "stat_block": _VALID_STAT_BLOCK}]})

    job_id = _run(world, provider)
    assert len(calls) == 2  # generate + exactly one bounded repair pass
    assert "VIOLATIONS TO FIX" in calls[1]
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = _staged(world)
    assert len(rows) == 3
    assert rows[0].payload["stat_block"]["attributes"]["str"] == 14


def test_still_invalid_stats_dropped_two_survive(world: str) -> None:
    """INVALID_STATS: a block still invalid after the repair drops that
    candidate; with two clean ones remaining the job still succeeds."""
    _commit_world(world)
    output = _generate_output()
    bad_block = json.loads(json.dumps(_VALID_STAT_BLOCK))
    bad_block["attributes"]["str"] = 40
    output["candidates"][0]["stat_block"] = bad_block
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(output)
        # The repair fails to fix E0: still invalid afterwards.
        return json.dumps({"stat_blocks": [{"ref": "E0", "stat_block": bad_block}]})

    job_id = _run(world, provider)
    assert len(calls) == 2
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = _staged(world)
    assert [row.payload["name"] for row in rows] == ["Sister Yeva", "The Tallyman"]
    assert job.result is not None
    dropped = job.result["dropped"]
    assert [entry["ref"] for entry in dropped] == ["E0"]
    assert dropped[0]["name"] == "Corvin Ashe"


def test_still_invalid_stats_below_two_fails(world: str) -> None:
    """INVALID_STATS + FEWER_THAN_TWO: two of three blocks still invalid
    after the repair -> job fails, stat-failure detail included."""
    _commit_world(world)
    output = _generate_output()
    bad_block = json.loads(json.dumps(_VALID_STAT_BLOCK))
    bad_block["attributes"]["str"] = 40
    output["candidates"][0]["stat_block"] = bad_block
    output["candidates"][1]["stat_block"] = bad_block
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(output)
        return json.dumps(
            {
                "stat_blocks": [
                    {"ref": "E0", "stat_block": bad_block},
                    {"ref": "E1", "stat_block": bad_block},
                ]
            }
        )

    job_id = _run(world, provider)
    assert len(calls) == 2
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "1 valid candidate(s) survived validation, need 2" in (job.error or "")
    assert "stat block(s) still invalid after the repair pass" in (job.error or "")
    assert _staged(world) == []


def test_missing_stat_block_repaired(world: str) -> None:
    """A candidate without a stat_block at all is a repairable violation:
    the one bounded pass fills it in and the candidate stages."""
    _commit_world(world)
    output = _generate_output()
    del output["candidates"][2]["stat_block"]

    def provider(prompt: str, settings: LLMSettings) -> str:
        if "VIOLATIONS TO FIX" in prompt:
            return json.dumps({"stat_blocks": [{"ref": "E2", "stat_block": _VALID_STAT_BLOCK}]})
        return json.dumps(output)

    job_id = _run(world, provider)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = _staged(world)
    assert len(rows) == 3
    assert rows[2].payload["stat_block"] == _VALID_STAT_BLOCK


def test_budget_exceeded_fails_before_repair_http(world: str) -> None:
    """BUDGET_EXCEEDED: max_llm_calls=1 with an invalid block — the repair
    call is refused BEFORE the HTTP request; the job fails; nothing staged."""
    _commit_world(world)
    output = _generate_output()
    bad_block = json.loads(json.dumps(_VALID_STAT_BLOCK))
    bad_block["attributes"]["str"] = 40
    output["candidates"][0]["stat_block"] = bad_block
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        return json.dumps(output)

    job_id = _run(world, provider, max_llm_calls=1)
    assert len(calls) == 1
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "budget" in (job.error or "").lower()
    assert _staged(world) == []


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


def _fresh_context() -> tuple[list[models.Entity], list[models.Edge]]:
    bar = models.Entity(
        id=ids.new_id(),
        campaign_id="C" * 26,
        kind="faction",
        name="The Gilded Bar",
        text="smoke and coin",
        data={"economy": {"level": 3}},
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


def test_build_generate_prompt_deterministic() -> None:
    """Same seed fields + ask + context -> byte-identical prompts across
    calls, fresh ULIDs/timestamps, and key order; no ids leak (AR6/AD-16)."""
    seed_a = _seed_campaign(id_suffix="A", created_at="2026-01-01T00:00:00Z")
    context = _fresh_context()
    first = build_generate_prompt(seed_a, "  a rival for Mira  ", context)
    assert build_generate_prompt(seed_a, "a rival for Mira", context) == first
    assert build_generate_prompt(seed_a, "a rival for Mira", _fresh_context()) == first
    # A different campaign id / created_at must not change the prompt.
    seed_b = _seed_campaign(id_suffix="B", created_at="2099-12-31T23:59:59Z")
    assert build_generate_prompt(seed_b, "a rival for Mira", context) == first
    # The serialized context survives; ids and timestamps never leak.
    assert context[0][0].id not in first and context[0][1].id not in first
    assert seed_a.id not in first and seed_a.created_at not in first
    assert "WORLD CONTEXT" in first and "entity[0]" in first
    assert "EDGE VOCABULARY" in first and "rival_of" in first and "debt: amount" in first
    assert "COUNTER SEMANTICS" in first
    assert stat_block_rules_text() in first and "SRD SPELLS BY CLASS" in first
    assert '"candidates"' in first and "party_hook" in first
    assert '"C<index>"' in first


# ---------------------------------------------------------------------------
# Staging store contract: all-or-nothing + pagination
# ---------------------------------------------------------------------------


def _candidate_record(world_id: str, name: str = "Corvin Ashe") -> dict[str, Any]:
    with session_scope() as session:
        entity = next(iter(world_entities(session, world_id)))
        return {
            "name": name,
            "role": "NPC",
            "personality": "dry",
            "secret": "s",
            "rumor": "r",
            "party_hook": "p",
            "stat_block": _VALID_STAT_BLOCK,
            "edges": [
                {"endpoint": entity.id, "direction": "outbound", "type": "rival_of", "counter": 1}
            ],
        }


def test_stage_candidates_all_or_nothing(world: str) -> None:
    """One bad edge in the batch -> InvalidCandidateError and ZERO rows
    staged (all-or-nothing); a delete landing mid-job surfaces here."""
    _commit_world(world)
    good = _candidate_record(world)
    stray_ulid = ids.new_id()  # well-formed ULID, not committed world state
    bad_endpoint = json.loads(json.dumps(good))
    bad_endpoint["edges"][0]["endpoint"] = stray_ulid
    bad_type = json.loads(json.dumps(good))
    bad_type["edges"][0]["type"] = "teleports"
    job_id = enqueue_job(world, "generate", {"ask": "stage me"}).id
    with pytest.raises(InvalidCandidateError):
        stage_candidates(world, job_id, [good, bad_endpoint])
    assert _staged(world) == []
    with pytest.raises(InvalidCandidateError):
        stage_candidates(world, job_id, [good, bad_type])
    assert _staged(world) == []


def test_stage_candidates_requires_campaign_and_job(world: str) -> None:
    """Staging rejects an unknown campaign or job before writing."""
    _commit_world(world)
    payload = _candidate_record(world)
    with pytest.raises(UnknownCampaignError):
        stage_candidates(ids.new_id(), ids.new_id(), [payload])
    with pytest.raises(JobNotFoundError):
        stage_candidates(world, ids.new_id(), [payload])
    assert _staged(world) == []


def test_list_candidates_pagination_and_cursor_guards(world: str) -> None:
    """Oldest-first rowid order; limit+cursor slices exhaust cleanly; a
    fabricated or cross-campaign cursor is a 422-family rejection."""
    _commit_world(world)
    job = enqueue_job(world, "generate", {"ask": "candidates"})
    stage_candidates(world, job.id, [_candidate_record(world, f"Candidate {i}") for i in range(3)])

    page, cursor = list_candidates(world, limit=2)
    assert [row.payload["name"] for row in page] == ["Candidate 0", "Candidate 1"]
    rest, exhausted = list_candidates(world, cursor, limit=2)
    assert [row.payload["name"] for row in rest] == ["Candidate 2"]
    assert exhausted is None
    # A fabricated cursor must never silently reset the page.
    with pytest.raises(InvalidJobInputError):
        list_candidates(world, encode_cursor(ids.new_id()))
    # A cursor naming another campaign's candidate is a user error too.
    other = create_campaign(
        _owner_id(), title="Other", description="", theme="High Fantasy", custom_lore=""
    ).id
    _commit_world(other)
    other_job = enqueue_job(other, "generate", {"ask": "candidates"})
    other_staged = stage_candidates(other, other_job.id, [_candidate_record(other)])
    with pytest.raises(InvalidJobInputError):
        list_candidates(world, encode_cursor(other_staged[0].id))


# ---------------------------------------------------------------------------
# Review round 1 pins: cancel races, staging idempotency, AR20 cascade,
# retry-vs-empty-world, prompt ref pin, CHECK enforcement
# ---------------------------------------------------------------------------


def test_cancel_before_staging_is_noop(world: str) -> None:
    """CANCEL_BEFORE_STAGING: a cancel landing inside the provider call
    (before the post-validation poll) is a no-op — no rows staged, the
    job stays cancelled, no exception escapes run_next_job."""
    _commit_world(world)
    job_id = _enqueue(world)

    def provider(prompt: str, settings: LLMSettings) -> str:
        cancel_job(job_id)  # lands during the generate call, before the poll
        return json.dumps(_generate_output())

    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "cancelled"  # never failed/succeeded
    assert _staged(world) == []


def test_cancel_after_staging_discards_ghost_rows(
    world: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CANCEL_VS_STAGE: a cancel landing between the staging commit and
    the terminal write leaves ghost rows — the runner discards them and
    propagates the conflict; the job stays cancelled and GET candidates
    would serve nothing."""
    from app.pipeline import generate as generate_module

    _commit_world(world)
    job_id = _enqueue(world)
    real_progress = report_progress

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_generate_output())

    def cancel_then_progress(job_id_: str, progress: float) -> Any:
        cancel_job(job_id_)  # lands after staging, before the terminal write
        real_progress(job_id_, progress)  # raises the conflict

    monkeypatch.setattr(generate_module, "report_progress", cancel_then_progress)
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id
    job, _position = job_status(job_id)
    assert job.state == "cancelled"  # never failed/succeeded
    assert _staged(world) == []  # the ghost rows are gone


def test_stage_candidates_idempotent_per_job(world: str) -> None:
    """A crash between the staging commit and complete_job re-queues the
    job (recover_stale_running); the re-run stages NO duplicates — the
    existing rows come back unchanged."""
    _commit_world(world)
    job = enqueue_job(world, "generate", {"ask": "stage me"})
    first = stage_candidates(world, job.id, [_candidate_record(world, "Only")])
    second = stage_candidates(
        world, job.id, [_candidate_record(world, "Only"), _candidate_record(world, "Extra")]
    )
    assert [row.id for row in second] == [row.id for row in first]
    assert [row.payload["name"] for row in second] == ["Only"]
    assert len(list_candidates(world)[0]) == 1


def test_delete_campaign_cascades_staged_candidates(world: str) -> None:
    """AR20: a campaign with staged candidates hard-deletes — the
    staging rows (FKs to campaign AND job) cascade before the job rows."""
    owner = _owner_id()
    campaign = create_campaign(
        owner, title="Doomed World", description="", theme="High Fantasy", custom_lore=""
    ).id
    _commit_world(campaign)
    job = enqueue_job(campaign, "generate", {"ask": "delete me"})
    stage_candidates(campaign, job.id, [_candidate_record(campaign)])
    assert delete_campaign(owner, campaign) is True
    with session_scope() as session:
        assert session.get(models.Campaign, campaign) is None
        assert session.scalar(select(func.count()).select_from(models.ProposedCandidate)) == 0
        assert session.scalar(select(func.count()).select_from(models.Job)) == 0


def test_retry_after_undo_is_duplicate_not_422(world: str) -> None:
    """An idempotent retry (same job_id) whose world was emptied by undo
    is the documented 409 DuplicateJobError — the duplicate check
    precedes the generate empty-world gate."""
    _commit_world(world)
    job_id = ids.new_id()
    first = enqueue_job(world, "generate", {"ask": "retry"}, job_id=job_id)
    assert first.state == "queued"
    with session_scope() as session:
        latest = latest_revision(session, world)
    assert latest is not None
    undo(world, latest.id)  # the world is empty again
    with pytest.raises(DuplicateJobError):
        enqueue_job(world, "generate", {"ask": "retry"}, job_id=job_id)


def test_candidate_kind_status_check_constraints(world: str) -> None:
    """The staged table pins its closed kind/status sets at the DB level
    (mirroring ck_job_kind/ck_job_state): anything outside is an
    IntegrityError."""
    _commit_world(world)
    job = enqueue_job(world, "generate", {"ask": "check me"})
    stage_candidates(world, job.id, [_candidate_record(world)])
    db_file = str(get_engine().url).removeprefix("sqlite:///")
    raw = sqlite3.connect(db_file)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO proposed_candidate (id, campaign_id, job_id, kind,"
                " status, payload, created_at) VALUES (?, ?, ?, 'faction',"
                " 'proposed', '{}', 'now')",
                (ids.new_id(), world, job.id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO proposed_candidate (id, campaign_id, job_id, kind,"
                " status, payload, created_at) VALUES (?, ?, ?, 'entity',"
                " 'accepted', '{}', 'now')",
                (ids.new_id(), world, job.id),
            )
    finally:
        raw.close()


def test_prompt_pins_context_refs(world: str) -> None:
    """The prompt states the positional C<index> <-> entity[<index>]
    mapping explicitly, mirroring wave 2's range pin."""
    _commit_world(world)
    context_entities, context_edges = retrieve_neighborhood(world, seed_ids=None)
    with session_scope() as session:
        seed = campaign_seed(session, world)
    assert seed is not None
    prompt = build_generate_prompt(seed, "an ask", (context_entities, context_edges))
    assert "CONTEXT REFS" in prompt
    assert "C0..C1" in prompt and "entity[<i>] = C<i>" in prompt


def test_unhashable_edge_type_dropped_cleanly(world: str) -> None:
    """A non-string edge type (list/dict — unhashable for the frozenset
    membership test) drops the edge cleanly; a candidate left with a
    valid edge still stages; all-bad edges fail with BAD_EDGE."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][1]["edges"] = [
        {"endpoint": "C0", "direction": "outbound", "type": ["rival_of"]},
        {"endpoint": "C1", "direction": "inbound", "type": "rival_of"},
    ]
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"  # the malformed edge dropped, anchor kept
    rows = _staged(world)
    assert len(rows) == 3
    yeva = next(row for row in rows if row.payload["name"] == "Sister Yeva")
    # The malformed edge dropped; the well-formed anchor edge survived.
    assert [edge["type"] for edge in yeva.payload["edges"]] == ["rival_of"]


def test_drop_reasons_carry_names_and_one_numbering(world: str) -> None:
    """Drop reasons use ONE E-ref numbering (the parsed index — the same
    refs the repair prompt flags) and always name the candidate: E<n>
    can never refer to two different candidates in one job.error."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][0]["secret"] = ""  # shape drop
    bad_block = json.loads(json.dumps(_VALID_STAT_BLOCK))
    bad_block["attributes"]["str"] = 40
    output["candidates"][1]["stat_block"] = bad_block
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(output)
        # The repair "fails" E1 (the parsed index): still invalid.
        return json.dumps({"stat_blocks": [{"ref": "E1", "stat_block": bad_block}]})

    job_id = _run(world, provider)
    assert len(calls) == 2
    assert "E1" in calls[1]  # the repair prompt flags the parsed index
    job, _position = job_status(job_id)
    assert job.state == "failed"  # only E2 survives: 1 < 2
    error = job.error or ""
    assert "1 valid candidate(s) survived validation, need 2" in error
    assert "E0 ('Corvin Ashe'): secret must be a non-blank string" in error
    assert "E1 ('Sister Yeva'): stat block(s) still invalid after the repair pass" in error
    assert _staged(world) == []


def test_dropped_summary_on_partial_success(world: str) -> None:
    """P9: when 2 of 3 survive, the dropped summary (ref, name, reason)
    rides on the successful result for the DM's accept screen."""
    _commit_world(world)
    output = _generate_output()
    bad_block = json.loads(json.dumps(_VALID_STAT_BLOCK))
    bad_block["attributes"]["str"] = 40
    output["candidates"][0]["stat_block"] = bad_block
    calls: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(output)
        return json.dumps({"stat_blocks": [{"ref": "E0", "stat_block": bad_block}]})

    job_id = _run(world, provider)
    job, _position = job_status(job_id)
    assert job.state == "succeeded"  # E1/E2 survive
    result = job.result
    assert result is not None
    assert len(result["candidate_ids"]) == 2
    dropped = result["dropped"]
    assert len(dropped) == 1
    assert dropped[0]["ref"] == "E0"
    assert dropped[0]["name"] == "Corvin Ashe"
    assert "stat block(s) still invalid after the repair pass" in dropped[0]["reason"]


def test_dropped_summary_absent_when_all_valid(world: str) -> None:
    """A clean 3-of-3 run stages with an empty dropped list."""
    _commit_world(world)
    job_id = _run(world, lambda prompt, settings: json.dumps(_generate_output()))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["dropped"] == []
