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
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text

import app.store as store_module
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
    recover_stale_running,
    register_account,
    report_progress,
    stage_candidates,
    undo,
)
from app.store.db import get_engine, session_scope
from app.store.jobs import GENERATE_MAX_ASK_LENGTH
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
    """A valid AR24 generate output (spec-3.3): ``total`` NPC candidates,
    each carrying the full sectioned profile and anchored to the
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
                "level_cr": "level 5",
                "race_type": "Human",
                "class_profession": "Fence",
                "alignment": "NE",
                "appearance": "gaunt, ink-stained fingers, a coat too fine for the quarter",
                "background": "ex-Guild scribe turned broker of favors",
                "goals": "buy back a name the Guild still owns",
                "relationships": "pays protection to the Gilded Bar; rivals Mira Vane",
                "voice_style": "clipped, low, never repeats an offer",
                "catchphrases": '"Everything has a price."',
                "stat_block": _VALID_STAT_BLOCK,
                "world_integration": {
                    "reputation": "the fixer of the docks",
                    "factions": "The Guild (in good standing, barely)",
                    "current_location": "the back booth of the Gilded Bar",
                    "reaction_matrix": "buys drinks for strangers, sells favors dearer",
                    "on_defeat": "flees, leaving the ledger behind",
                },
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
    return enqueue_job(world, "generate", {"ask": ask}, max_llm_calls=max_llm_calls).id


def _run(
    world: str,
    provider: Any,
    *,
    max_llm_calls: int | None = None,
    ask: str = "a rival for Mira",
) -> str:
    """Enqueue one generate job and drain it with ``provider``."""
    job_id = enqueue_job(world, "generate", {"ask": ask}, max_llm_calls=max_llm_calls).id
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
            "level_cr",
            "race_type",
            "class_profession",
            "alignment",
            "appearance",
            "background",
            "goals",
            "relationships",
            "voice_style",
            "catchphrases",
            "world_integration",
            "stat_block",
            "edges",
        }
        assert payload["role"] == "NPC"  # the fixture's role — no boss section (spec-3.3)
        assert "boss" not in payload
        assert set(payload["world_integration"]) == {
            "reputation",
            "factions",
            "current_location",
            "reaction_matrix",
            "on_defeat",
        }
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
# AR24 sectioned profile (spec-3.3): required sections + boss conditionality
# ---------------------------------------------------------------------------


def test_missing_ar24_section_drops_candidate(world: str) -> None:
    """A candidate missing an AR24 narrative section (appearance) is a
    shape violation: with two clean ones the job still stages 2 and the
    drop summary names the missing section."""
    _commit_world(world)
    output = _generate_output()
    del output["candidates"][0]["appearance"]
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["candidate_count"] == 2
    dropped = job.result["dropped"]
    assert len(dropped) == 1 and "appearance" in dropped[0]["reason"]


def test_missing_ar24_sections_fail_below_two(world: str) -> None:
    """A candidate missing substance must not reach the accept screen
    silent-empty: when every candidate misses an AR24 section the job
    fails per the <2-survivors rule; nothing staged."""
    _commit_world(world)
    output = _generate_output()
    for candidate in output["candidates"]:
        del candidate["goals"]
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "failed" and job.result is None
    assert "goals must be a non-blank string" in (job.error or "")
    assert _staged(world) == []


def test_world_integration_block_validated(world: str) -> None:
    """The world-integration block must be an object with all five
    non-blank fields; a blank or missing field is a shape violation."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][0]["world_integration"]["reaction_matrix"] = "   "
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["candidate_count"] == 2
    reasons = " | ".join(drop["reason"] for drop in job.result["dropped"])
    assert "world_integration.reaction_matrix must be a non-blank string" in reasons


def test_boss_section_required_for_bbeg_and_monster(world: str) -> None:
    """BBEG/Monster candidates MUST carry the boss section: a Monster
    with a complete boss stages with it; a BBEG without one is dropped
    (boss must be an object)."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][0]["role"] = "Monster"
    output["candidates"][0]["boss"] = {
        "lair_actions": "none — it hunts",
        "legendary_actions": "3 per round",
        "immunities": "charmed",
        "vulnerabilities": "fire",
    }
    output["candidates"][1]["role"] = "BBEG"
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["candidate_count"] == 2
    rows = _staged(world)
    assert rows[0].payload["role"] == "Monster"
    assert set(rows[0].payload["boss"]) == {
        "lair_actions",
        "legendary_actions",
        "immunities",
        "vulnerabilities",
    }
    assert "boss must be an object" in job.result["dropped"][0]["reason"]


def test_boss_section_partial_fields_violation(world: str) -> None:
    """A boss section with a blank field is a shape violation naming the
    field — substance, not an empty shell."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][0]["role"] = "BBEG"
    output["candidates"][0]["boss"] = {
        "lair_actions": "the hall floods with shadows",
        "legendary_actions": "3 per round",
        "immunities": "charmed",
        "vulnerabilities": "",
    }
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["candidate_count"] == 2
    assert "boss.vulnerabilities must be a non-blank string" in job.result["dropped"][0]["reason"]


def test_bbeg_happy_path_stages_with_boss(world: str) -> None:
    """A BBEG candidate through the whole happy path: staged with the
    complete boss section (and no boss on the NPC rows)."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][0]["role"] = "BBEG"
    output["candidates"][0]["level_cr"] = "level 20"
    output["candidates"][0]["boss"] = {
        "lair_actions": "the hall floods with shadows",
        "legendary_actions": "3 per round",
        "immunities": "charmed, frightened",
        "vulnerabilities": "radiant",
    }
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded" and job.result is not None
    assert job.result["candidate_count"] == 3 and job.result["dropped"] == []
    rows = _staged(world)
    assert rows[0].payload["role"] == "BBEG"
    assert rows[0].payload["level_cr"] == "level 20"
    assert rows[0].payload["boss"] == {
        "lair_actions": "the hall floods with shadows",
        "legendary_actions": "3 per round",
        "immunities": "charmed, frightened",
        "vulnerabilities": "radiant",
    }
    assert "boss" not in rows[1].payload and "boss" not in rows[2].payload


def test_boss_section_forbidden_for_npc(world: str) -> None:
    """An NPC with a boss object is a shape violation — never an empty
    boss section (spec-3.3 Design Notes)."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][0]["boss"] = {
        "lair_actions": "",
        "legendary_actions": "",
        "immunities": "",
        "vulnerabilities": "",
    }
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["candidate_count"] == 2
    assert "boss section is only allowed" in job.result["dropped"][0]["reason"]


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
    # Pre-existing rows — the data the rebuild exists to preserve: a
    # queued text job and a finished text job from the old schema (both
    # kinds admitted by EITHER legacy CHECK — the rebuild cannot create
    # kinds the old schema forbade).
    legacy_account = "A" * 26
    legacy_campaign = "C" * 26
    raw.execute(
        "INSERT INTO account (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (legacy_account, "legacy@example.com", "legacy-hash", "2026-01-01T00:00:00Z"),
    )
    raw.execute(
        "INSERT INTO campaign (id, owner_id, title, description, theme,"
        " custom_lore, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            legacy_campaign,
            legacy_account,
            "Legacy World",
            "",
            "High Fantasy",
            "",
            "2026-01-01T00:00:00Z",
        ),
    )
    raw.executemany(
        "INSERT INTO job (id, campaign_id, kind, payload, state, progress,"
        " max_llm_calls, max_media_calls, error, result, created_at,"
        " started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "J" + "1" * 25,
                legacy_campaign,
                "text",
                '{"prompt": "grow the world"}',
                "queued",
                0.0,
                8,
                0,
                None,
                None,
                "2026-01-01T00:00:00Z",
                None,
                None,
            ),
            (
                "J" + "2" * 25,
                legacy_campaign,
                "text",
                '{"prompt": "hello"}',
                "succeeded",
                1.0,
                5,
                0,
                None,
                '{"text": "hello world"}',
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:01:00Z",
                "2026-01-01T00:02:00Z",
            ),
        ],
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
        with session_scope() as session:
            legacy = {row.id: row for row in session.execute(select(models.Job)).scalars().all()}
        first = legacy["J" + "1" * 25]
        assert first.kind == "text" and first.state == "queued"
        assert first.payload == {"prompt": "grow the world"}
        text_job = legacy["J" + "2" * 25]
        assert text_job.kind == "text" and text_job.state == "succeeded"
        assert text_job.result == {"text": "hello world"}
    finally:
        init_db(previous)


def test_migrate_job_kind_handles_sqlalchemy_quoted_ddl(tmp_path: Path) -> None:
    """A database created by ``create_all`` stores the job DDL with a
    QUOTED table name and tab indentation (``CREATE TABLE "job" (`` +
    ``\\n\\t``). That real-world shape wedged every subsequent start when
    the rebuild's regex/rename missed it (found 2026-09-04 on a live dev
    DB): the rewrite must match it, and an unrewritable shape must fail
    loudly instead of re-CREATE-ing the live table."""
    db_path = tmp_path / "quoted-ddl.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE "account" (
\t"id" VARCHAR(26) NOT NULL,
\t"email" VARCHAR(320) NOT NULL UNIQUE,
\t"password_hash" VARCHAR(255) NOT NULL,
\t"created_at" VARCHAR(40) NOT NULL,
\tPRIMARY KEY ("id")
);
        CREATE TABLE "campaign" (
\t"id" VARCHAR(26) NOT NULL,
\t"owner_id" VARCHAR(26) NOT NULL REFERENCES "account" ("id"),
\t"title" VARCHAR(300) NOT NULL,
\t"description" TEXT NOT NULL,
\t"theme" VARCHAR(100) NOT NULL,
\t"custom_lore" TEXT NOT NULL,
\t"created_at" VARCHAR(40) NOT NULL,
\tPRIMARY KEY ("id")
);
        CREATE TABLE "job" (
\t"id" VARCHAR(26) NOT NULL,
\t"campaign_id" VARCHAR(26) NOT NULL REFERENCES "campaign" ("id"),
\t"kind" VARCHAR(64) NOT NULL,
\t"payload" JSON NOT NULL,
\t"state" VARCHAR(32) NOT NULL,
\t"progress" FLOAT NOT NULL,
\t"max_llm_calls" INTEGER NOT NULL,
\t"max_media_calls" INTEGER NOT NULL,
\t"error" TEXT,
\t"result" JSON,
\t"created_at" VARCHAR(40) NOT NULL,
\t"started_at" VARCHAR(40),
\t"finished_at" VARCHAR(40),
\tPRIMARY KEY ("id"),
\tCONSTRAINT "ck_job_kind" CHECK (kind IN ('text','image','video','build_in'))
);
        """
    )
    raw.execute("INSERT INTO account VALUES ('A' || ?, 'q@example.com', 'x', 'now')", ("A" * 25,))
    raw.execute(
        "INSERT INTO campaign VALUES ('C' || ?, 'A' || ?, 'Quoted World', '',"
        " 'High Fantasy', '', 'now')",
        ("C" * 25, "A" * 25),
    )
    raw.commit()
    raw.close()

    previous = app_db_url()
    init_db(f"sqlite:///{db_path}")  # must not raise 'table "job" already exists'
    try:
        with session_scope() as session:
            ddl = session.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name='job'")
            ).scalar_one()
            assert "'generate'" in ddl and '"job_new"' not in ddl
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
    # V6: the shape-drop rides the dropped summary on a PARTIAL success
    # too (the stat-repair drop path was already pinned).
    assert job.result is not None
    dropped = job.result["dropped"]
    assert [d["ref"] for d in dropped] == ["E1"]
    assert dropped[0]["name"] == "Sister Yeva"
    assert "BAD_EDGE" in dropped[0]["reason"]


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
    # A 4th entry that would fail validation if parsed: an unsliced run
    # would drop it in shape validation and still succeed with 3.
    output = _generate_output(4)
    output["candidates"][3] = "not an object"
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    result = job.result
    assert result is not None and len(result["candidate_ids"]) == 3
    # The slice is the only guard between the model contract and the
    # staged 2-3 contract: 4 VALID candidates must still stage exactly
    # the first 3 — this fails if the [:MAX_CANDIDATES] slice is ever
    # removed (E3 would stage and candidate_ids would be 4).
    output = _generate_output(4)
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    result = job.result
    assert result is not None and len(result["candidate_ids"]) == 3


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


def test_stage_candidates_rejects_non_strict_json(world: str) -> None:
    """NaN/Infinity in a staged payload is rejected at the store boundary
    — the strict-JSON backstop (the read surface would 500 them)."""
    _commit_world(world)
    job = enqueue_job(world, "generate", {"ask": "stage me"})
    bad = _candidate_record(world, "Infinity")
    bad["power"] = float("inf")  # json.loads yields this from 1e999/Infinity
    with pytest.raises(InvalidCandidateError):
        stage_candidates(world, job.id, [bad])
    assert _staged(world) == []
    with pytest.raises(InvalidCandidateError):
        stage_candidates(world, job.id, [_candidate_record(world, "NaN") | {"power": float("nan")}])
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
    IntegrityError. Spec-3.2 widened the status set to the full
    lifecycle — ``accepted``/``rejected`` are now legal, junk still is
    not."""
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
        for status in ("proposed", "accepted", "rejected"):
            raw.execute(
                "INSERT INTO proposed_candidate (id, campaign_id, job_id, kind,"
                f" status, payload, created_at) VALUES (?, ?, ?, 'entity',"
                f" '{status}', '{{}}', 'now')",
                (ids.new_id(), world, job.id),
            )
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO proposed_candidate (id, campaign_id, job_id, kind,"
                " status, payload, created_at) VALUES (?, ?, ?, 'entity',"
                " 'settled', '{}', 'now')",
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


def test_prompt_pins_ar24_sections(world: str) -> None:
    """The OUTPUT CONTRACT pins the AR24 sectioned profile: the
    world-integration block, the boss section's conditional rule
    (required for BBEG/Monster, omitted for NPC — annotated inline in
    the template itself, never self-contradictory), and both level/CR
    formats."""
    _commit_world(world)
    context_entities, context_edges = retrieve_neighborhood(world, seed_ids=None)
    with session_scope() as session:
        seed = campaign_seed(session, world)
    assert seed is not None
    prompt = build_generate_prompt(seed, "an ask", (context_entities, context_edges))
    assert '"world_integration": {"reputation": "...", "factions": "...",' in prompt
    assert '"current_location": "...", "reaction_matrix": "...",' in prompt
    assert '"on_defeat": "..."' in prompt
    assert "CONDITIONAL" in prompt
    assert "REQUIRED" in prompt and "when the role is BBEG or Monster" in prompt
    assert "OMITTED entirely for NPC" in prompt
    assert "empty boss object" in prompt
    assert '"level_cr": "level <n>" for NPC/BBEG or "CR <n>" for Monster,' in prompt


#
# Review round 2 pins: adversarial edge refs, non-finite stat values,
# crash-requeue re-run failure cleanup
# ---------------------------------------------------------------------------


def test_oversized_context_ref_drops_edge(world: str) -> None:
    """An edge endpoint ref with thousands of digits (CPython's
    int-conversion cap would raise on int()) drops that edge as BAD_EDGE
    instead of aborting the whole job — the anchor rule stays honest."""
    _commit_world(world)
    output = _generate_output()
    output["candidates"][1]["edges"] = [
        {"endpoint": "C" + "9" * 5000, "direction": "outbound", "type": "rival_of"},
        {"endpoint": "C1", "direction": "inbound", "type": "rival_of"},
    ]
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"  # malformed edge dropped, anchor kept
    rows = _staged(world)
    assert len(rows) == 3
    yeva = next(row for row in rows if row.payload["name"] == "Sister Yeva")
    assert [edge["type"] for edge in yeva.payload["edges"]] == ["rival_of"]


def test_non_finite_stat_value_drops_candidate(world: str) -> None:
    """json.loads turns 1e999/Infinity into inf — AR25's stat validation
    tolerates the unknown key, so the strict-JSON guard must drop the
    candidate (the candidates read would 500 an Infinity payload)."""
    _commit_world(world)
    output = _generate_output()
    # A fresh dict — json.loads turns 1e999 into inf HERE (the shared
    # _VALID_STAT_BLOCK must never be mutated in place).
    output["candidates"][0]["stat_block"] = {**_VALID_STAT_BLOCK, "power": 1e999}
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    rows = _staged(world)
    assert len(rows) == 2  # E0 dropped, E1/E2 survive
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    result = job.result
    assert result is not None
    assert any(drop["ref"] == "E0" and "non-finite" in drop["reason"] for drop in result["dropped"])
    assert all("power" not in row.payload["stat_block"] for row in rows)


def test_failed_requeue_rerun_discards_staged_rows(world: str) -> None:
    """A crash after staging leaves a ``running`` job; the re-queued
    re-run fails (malformed output) — the first run's staged rows must
    not outlive the FAILED job (FEWER_THAN_TWO 'nothing staged')."""
    _commit_world(world)
    # The crash window: staging committed, then the process died before
    # the terminal write — the job row is still 'running'.
    job = enqueue_job(world, "generate", {"ask": "rerun"})
    staged = stage_candidates(
        world, job.id, [_candidate_record(world, f"Candidate {i}") for i in range(3)]
    )
    assert len(staged) == 3
    with session_scope() as session:
        row = session.get(models.Job, job.id)
        assert row is not None
        row.state = "running"
    # Startup recovery re-queues the stale running job (AR11).
    assert recover_stale_running() == 1
    # Run 2: fails on garbage provider output — the worker must discard
    # the run-1 rows so a failed job never serves candidates.
    assert run_next_job(provider=lambda prompt, settings: "bogus", settings=SETTINGS) == job.id
    state, _position = job_status(job.id)
    assert state.state == "failed"
    assert _staged(world) == []


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


#
# Review round 3 pins: runner payload re-validation, runner world-state
# guards, transient status-read errors, duplicate candidates, retrieval
# determinism, adversarial nesting, cross-campaign staging
# ---------------------------------------------------------------------------


def _raw_generate_job(campaign_id: str, ask: str) -> str:
    """Insert a generate job row directly, bypassing the enqueue gate —
    the runner-level re-validation is defense for exactly such rows."""
    job_id = ids.new_id()
    with session_scope() as session:
        session.add(
            models.Job(
                id=job_id,
                campaign_id=campaign_id,
                kind="generate",
                payload={"ask": ask},
                state="queued",
                progress=0.0,
                max_llm_calls=5,
                max_media_calls=0,
                error=None,
                result=None,
                created_at="2026-01-01T00:00:00Z",
                started_at=None,
                finished_at=None,
            )
        )
    return job_id


def test_runner_revalidates_ask_on_claim(world: str) -> None:
    """A generate job row written outside enqueue_job still meets the
    payload contract at claim time: a blank or over-long ask fails the
    job with a structured error, never a prompt with an empty or
    truncated ask section."""
    _commit_world(world)
    blank = _raw_generate_job(world, "   ")
    assert (
        run_next_job(
            provider=lambda prompt, settings: json.dumps(_generate_output()),
            settings=SETTINGS,
        )
        == blank
    )
    job, _position = job_status(blank)
    assert job.state == "failed"
    assert "ask must be non-blank" in (job.error or "")

    overlong = _raw_generate_job(world, "x" * (GENERATE_MAX_ASK_LENGTH + 1))
    assert (
        run_next_job(
            provider=lambda prompt, settings: json.dumps(_generate_output()), settings=SETTINGS
        )
        == overlong
    )
    job, _position = job_status(overlong)
    assert job.state == "failed"
    assert "exceeds" in (job.error or "")


def test_runner_guard_empty_world_at_claim(world: str) -> None:
    """The runner re-checks world state between enqueue and claim (the
    undo race): a world emptied after submit fails the job with a
    structured error, never a crash. (A campaign deleted after submit is
    unreachable on the wire — AR20 cascades its jobs away with it — so
    the seed-missing guard stays defense-only.)"""
    _commit_world(world)
    emptied = enqueue_job(world, "generate", {"ask": "empty"}).id
    with session_scope() as session:
        latest = latest_revision(session, world)
    assert latest is not None
    undo(world, latest.id)
    assert (
        run_next_job(
            provider=lambda prompt, settings: json.dumps(_generate_output()), settings=SETTINGS
        )
        == emptied
    )
    job, _position = job_status(emptied)
    assert job.state == "failed"
    assert "no committed entities" in (job.error or "")


def test_status_read_error_does_not_wedge_job(world: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient job_status read error on a cancel-race poll must not
    wedge the queue: the poll treats it as 'still running' and the job
    reaches a terminal state (round-1 patch, now pinned — flipping the
    branch back to False passes every other test)."""

    _commit_world(world)
    job_id = _enqueue(world)
    real_status: Callable[[str], tuple[models.Job, int | None]] = store_module.job_status
    calls = {"n": 0}

    def flaky_status(job_id_: str) -> tuple[models.Job, int | None]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient status read failure")
        return real_status(job_id_)

    monkeypatch.setattr(store_module, "job_status", flaky_status)
    assert (
        run_next_job(
            provider=lambda prompt, settings: json.dumps(_generate_output()), settings=SETTINGS
        )
        == job_id
    )
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert len(_staged(world)) == 3


def test_duplicate_candidate_stages_once(world: str) -> None:
    """'2-3 candidates' means distinct candidates: an echoed candidate
    stages once and the duplicate lands in the dropped summary naming
    the first occurrence."""
    _commit_world(world)
    output = _generate_output()  # Corvin Ashe, Sister Yeva, The Tallyman
    output["candidates"][2] = json.loads(json.dumps(output["candidates"][1]))
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    rows = _staged(world)
    assert [row.payload["name"] for row in rows] == ["Corvin Ashe", "Sister Yeva"]
    assert job.result is not None
    dropped = job.result["dropped"]
    assert [d["ref"] for d in dropped] == ["E2"]
    assert dropped[0]["name"] == "Sister Yeva"
    assert "duplicate of E1" in dropped[0]["reason"]


def test_all_duplicates_fail_fewer_than_two(world: str) -> None:
    """An all-identical batch has <2 distinct survivors: FEWER_THAN_TWO
    fails the job with the duplicate reason; nothing staged."""
    _commit_world(world)

    output = _generate_output()
    dup = json.loads(json.dumps(output["candidates"][0]))
    output["candidates"] = [dup, dup, dup]
    job_id = _run(world, lambda prompt, settings: json.dumps(output))
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "1 valid candidate(s) survived validation, need 2" in (job.error or "")
    assert "duplicate of E0" in (job.error or "")
    assert _staged(world) == []


def test_retrieval_order_pins_context_refs(world: str) -> None:
    """The prompt is a pure function of the RETRIEVED context: two
    retrievals of the same world give the same entity order (the C<index>
    refs' semantics) and the same prompt bytes; a different ask changes
    the prompt end-to-end."""
    _commit_world(world)
    first = retrieve_neighborhood(world, seed_ids=None)
    second = retrieve_neighborhood(world, seed_ids=None)
    assert [entity.id for entity in first[0]] == [entity.id for entity in second[0]]
    with session_scope() as session:
        seed = campaign_seed(session, world)
    assert seed is not None
    prompt_a = build_generate_prompt(seed, "a rival for Mira", first)
    assert build_generate_prompt(seed, "a rival for Mira", second) == prompt_a
    assert build_generate_prompt(seed, "another ask entirely", first) != prompt_a


def test_pathologically_nested_output_fails_structured(world: str) -> None:
    """An adversarially nested output (beyond CPython's JSON recursion
    limit) fails the job with the structured malformed-output error, not
    a raw RecursionError crash."""
    _commit_world(world)
    job_id = _run(
        world,
        lambda prompt, settings: '{"candidates": ' + "[" * 20000 + "]" * 20000 + "}",
    )
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "output is not valid JSON" in (job.error or "")


def test_stage_candidates_rejects_foreign_job(world: str) -> None:
    """Staging ties rows to a campaign: a job of ANOTHER campaign is
    rejected, never cross-staged."""
    _commit_world(world)
    other = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    _commit_world(other)
    job = enqueue_job(other, "generate", {"ask": "elsewhere"})
    with pytest.raises(InvalidCandidateError, match="belongs to campaign"):
        stage_candidates(world, job.id, [_candidate_record(world)])
