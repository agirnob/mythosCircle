"""Real startup upgrade and verified database snapshot/restore preserve Tonight history."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select

from app.core.backup import DB_FILENAME, run_snapshot
from app.core.restore import apply_restore, verify_snapshot
from app.store import models, register_account
from app.store.campaigns import create_campaign
from app.store.commit import commit_session_verb, commit_subgraph
from app.store.db import app_db_url, init_db, session_scope
from app.store.journal import (
    activate_session,
    create_entry,
    create_session,
    get_entry,
    snapshot,
    update_entry,
)


@pytest.fixture
def persistent_world(tmp_path: Path):
    previous = app_db_url()
    data = tmp_path / "data"
    data.mkdir()
    url = f"sqlite:///{data / DB_FILENAME}"
    init_db(url)
    owner = register_account("journal-backup@example.com", "password123")
    campaign = create_campaign(
        owner.id,
        title="Journal world",
        description="Original world",
        theme="High Fantasy",
        custom_lore="Keep the original lore",
    )
    commit_subgraph(
        campaign.id, [models.EntityInput(kind="character", name="Míra")], allow_orphans=True
    )
    with session_scope() as db:
        actor = db.scalar(select(models.Entity).where(models.Entity.campaign_id == campaign.id))
        actor_id = actor.id
    commit_session_verb(
        campaign.id, actor_id, update={"notes": "Original @notes 🐲"}, expected_notes=""
    )
    try:
        yield campaign, actor_id, data, url
    finally:
        init_db(previous)


def _table_images():
    with session_scope() as db:
        return {
            model.__tablename__: [
                snapshot(row) for row in db.scalars(select(model).order_by(model.id))
            ]
            for model in (
                models.Campaign,
                models.Entity,
                models.EntitySessionState,
                models.PlaySession,
                models.JournalEntry,
                models.JournalRequest,
                models.Revision,
                models.Event,
            )
        }


def test_upgrade_adds_active_session_column_without_converting_legacy_notes(
    persistent_world,
    tmp_path: Path,
):
    campaign, actor_id, data, url = persistent_world
    with session_scope() as db:
        original_campaign = snapshot(db.get(models.Campaign, campaign.id))
        original_state = snapshot(db.scalar(select(models.EntitySessionState)))
        revisions = db.scalar(select(func.count()).select_from(models.Revision))
        events = db.scalar(select(func.count()).select_from(models.Event))
    # Build the actual pre-journal shape from a populated database. Dispose the
    # live engine first so SQLite's schema edits cannot race a pooled connection.
    init_db(f"sqlite:///{tmp_path / 'elsewhere.db'}")
    with sqlite3.connect(data / DB_FILENAME) as old:
        old.execute("DROP TABLE journal_entry")
        old.execute("DROP TABLE journal_request")
        old.execute("DROP TABLE play_session")
        old.execute("ALTER TABLE campaign DROP COLUMN active_session_id")
        assert "active_session_id" not in {
            row[1] for row in old.execute("PRAGMA table_info(campaign)")
        }
    engine = init_db(url)
    assert {"play_session", "journal_entry", "journal_request"} <= set(
        inspect(engine).get_table_names()
    )
    assert "active_session_id" in {
        column["name"] for column in inspect(engine).get_columns("campaign")
    }
    with session_scope() as db:
        assert snapshot(db.get(models.Campaign, campaign.id)) == original_campaign
        assert db.get(models.Campaign, campaign.id).active_session_id is None
        state = db.scalar(select(models.EntitySessionState))
        assert state.entity_id == actor_id and snapshot(state) == original_state
        assert state.data["notes"] == "Original @notes 🐲"
        assert db.scalar(select(func.count()).select_from(models.Revision)) == revisions
        assert db.scalar(select(func.count()).select_from(models.Event)) == events
        for model in (models.PlaySession, models.JournalEntry, models.JournalRequest):
            assert db.scalar(select(func.count()).select_from(model)) == 0
    # A second genuine startup reruns the additive migration safely.
    init_db(f"sqlite:///{tmp_path / 'elsewhere.db'}")
    init_db(url)
    with session_scope() as db:
        assert snapshot(db.scalar(select(models.EntitySessionState))) == original_state
        assert db.scalar(select(func.count()).select_from(models.JournalEntry)) == 0


def test_verified_backup_restore_preserves_journal_and_original_retry_receipts(
    persistent_world,
    tmp_path: Path,
):
    campaign, actor_id, data, url = persistent_world
    play = create_session(
        campaign.id,
        title="A night in Arlea",
        play_date="2026-10-04",
        request_key="session-original",
    )
    activate_session(campaign.id, play.id)
    refs = [
        {
            "entity_id": actor_id,
            "label": "Míra",
            "field": "context",
            "token": "@Míra",
            "start": 2,
            "end": 7,
        }
    ]
    action_args = {
        "update": {"defeated": True},
        "session_id": play.id,
        "headline": "The battle ended",
        "context": "🐲 @Míra yielded",
        "references": refs,
        "request_key": "action-original",
    }
    action = commit_session_verb(campaign.id, actor_id, **action_args)
    prose_args = {
        "session_id": play.id,
        "headline": "The party returned",
        "context": "Original prose",
        "request_key": "prose-original",
    }
    prose = create_entry(campaign.id, **prose_args)
    update_entry(campaign.id, prose.id, version=1, context="Later authored context")
    expected = _table_images()
    assert len(expected["journal_request"]) == 3
    # Exercise the same SQLite online backup path used by the production wrappers
    # while the SQLAlchemy engine remains live and the database uses WAL.
    snapshot_dir = run_snapshot(data)
    verify_snapshot(snapshot_dir)
    activate_session(campaign.id, None)
    update_entry(campaign.id, prose.id, version=2, context="After the snapshot")
    commit_session_verb(
        campaign.id, actor_id, update={"notes": "New notes"}, expected_notes="Original @notes 🐲"
    )
    assert _table_images() != expected
    init_db(f"sqlite:///{tmp_path / 'elsewhere.db'}")
    apply_restore(snapshot_dir, data_dir=data)
    init_db(url)
    assert _table_images() == expected
    with session_scope() as db:
        assert db.get(models.Campaign, campaign.id).active_session_id == play.id
        linked = db.scalar(
            select(models.JournalEntry).where(models.JournalEntry.action_revision_id == action.id)
        )
        assert linked.references == refs
        source = db.get(models.Event, linked.source_event_id)
        assert source.revision_id == action.id and source.payload["id"] == actor_id
        assert db.scalar(select(models.EntitySessionState)).data == {
            "notes": "Original @notes 🐲",
            "defeated": True,
        }
    # Receipts survive the complete restore and still return the original
    # creation response, even though the actual prose row has been edited.
    original_retry = create_entry(campaign.id, **prose_args)
    assert original_retry.id == prose.id and original_retry.context == "Original prose"
    assert original_retry.version == 1
    assert get_entry(campaign.id, prose.id).context == "Later authored context"
    assert commit_session_verb(campaign.id, actor_id, **action_args).id == action.id
    assert (
        create_session(
            campaign.id,
            title="A night in Arlea",
            play_date="2026-10-04",
            request_key="session-original",
        ).id
        == play.id
    )
    assert _table_images() == expected
