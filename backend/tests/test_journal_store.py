"""Story persistence, retry identity, action pairing and compensating history."""

import pytest
from sqlalchemy import func, select

from app.core.pagination import InvalidCursorError
from app.store import models, register_account
from app.store.campaigns import create_campaign, delete_campaign
from app.store.commit import commit_session_verb, commit_subgraph, delete_entity, update_entity
from app.store.db import app_db_url, init_db, session_scope
from app.store.journal import (
    JournalConflictError,
    JournalInputError,
    JournalNotFoundError,
    activate_session,
    create_entry,
    create_session,
    delete_entry,
    get_entry,
    list_entries,
    take_back_entry,
    update_entry,
    update_session,
)
from app.store.read import latest_revision
from app.store.undo import undo


@pytest.fixture
def world(tmp_path):
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'world.db'}")
    owner = register_account("journal@example.com", "password123")
    campaign = create_campaign(
        owner.id, title="Journal", description="", theme="High Fantasy", custom_lore=""
    )
    other = create_campaign(
        owner.id, title="Other", description="", theme="High Fantasy", custom_lore=""
    )
    try:
        yield campaign, other
    finally:
        init_db(previous)


def head(campaign):
    with session_scope() as db:
        return latest_revision(db, campaign.id).id


def entity(campaign):
    with session_scope() as db:
        latest = latest_revision(db, campaign.id)
    commit_subgraph(
        campaign.id,
        [models.EntityInput(kind="character", name="Míra")],
        base_revision=latest.id if latest else None,
        allow_orphans=True,
    )
    with session_scope() as db:
        return db.scalar(select(models.Entity).where(models.Entity.campaign_id == campaign.id)).id


def test_retry_returns_original_response_after_edit(world):
    campaign, _ = world
    play = create_session(campaign.id, title="First", play_date="2026-10-01", request_key="session")
    update_session(campaign.id, play.id, version=1, title="Changed")
    assert (
        create_session(
            campaign.id, title="First", play_date="2026-10-01", request_key="session"
        ).title
        == "First"
    )
    row = create_entry(campaign.id, session_id=play.id, headline="One", request_key="entry")
    update_entry(campaign.id, row.id, version=1, context="Later")
    retry = create_entry(campaign.id, session_id=play.id, headline="One", request_key="entry")
    assert (retry.id, retry.version, retry.context) == (row.id, 1, "")
    assert get_entry(campaign.id, row.id).context == "Later"
    with pytest.raises(JournalConflictError):
        create_entry(campaign.id, session_id=play.id, headline="Different", request_key="entry")
    with pytest.raises(JournalConflictError):
        update_entry(campaign.id, row.id, version=1, context="Stale")


def test_chronology_unicode_mentions_deletion_and_cursor_scope(world):
    campaign, other = world
    actor = entity(campaign)
    later = create_session(campaign.id, title="Later", play_date="2026-10-02")
    early = create_session(campaign.id, title="Earlier", play_date="2026-10-01")
    text = "🐲 @Míra arrived"
    ref = {
        "entity_id": actor,
        "label": "Míra",
        "token": "@Míra",
        "field": "context",
        "start": 2,
        "end": 7,
    }
    one = create_entry(
        campaign.id,
        session_id=later.id,
        headline="Arrival",
        context=text,
        references=[ref],
        request_key="one",
    )
    two = create_entry(campaign.id, session_id=early.id, headline="Before", request_key="two")
    assert [entry.id for entry in list_entries(campaign.id)[0]] == [two.id, one.id]
    with pytest.raises(InvalidCursorError):
        list_entries(other.id, cursor=one.id)
    with pytest.raises(JournalNotFoundError):
        create_entry(other.id, session_id=later.id, headline="Foreign", request_key="foreign")
    delete_entity(campaign.id, actor)
    edited = update_entry(
        campaign.id,
        one.id,
        version=1,
        context="x" + text,
        references=[{**ref, "start": 3, "end": 8}],
    )
    assert edited.references[0]["label"] == "Míra"
    with pytest.raises(JournalInputError):
        create_entry(
            campaign.id,
            session_id=early.id,
            headline="Missing",
            references=[{"entity_id": actor, "label": "Míra"}],
            request_key="missing",
        )


def test_paired_retry_correction_preserves_later_prose_state_and_undo(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Battle", play_date="2026-10-05")
    args = {
        "update": {"defeated": True},
        "session_id": play.id,
        "headline": "Defeated",
        "request_key": "defeat",
    }
    revision = commit_session_verb(campaign.id, actor, **args)
    row = list_entries(campaign.id)[0][0]
    assert (
        row.action_revision_id == revision.id
        and row.action_entity_id == actor
        and row.source_event_id
    )
    update_entry(campaign.id, row.id, version=1, context="A scar remains")
    commit_session_verb(campaign.id, actor, update={"scar": True})
    assert commit_session_verb(campaign.id, actor, **args).id == revision.id
    corrected = take_back_entry(campaign.id, row.id, version=2)
    assert corrected.corrected and corrected.context == "A scar remains"
    with session_scope() as db:
        assert db.scalar(select(models.EntitySessionState)).data == {"scar": True}
    undo(campaign.id, head(campaign))
    assert not get_entry(campaign.id, row.id).corrected
    with session_scope() as db:
        assert db.scalar(select(models.EntitySessionState)).data == {"scar": True, "defeated": True}


def test_invalid_pair_rolls_back_conflicting_correction_is_atomic(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Battle", play_date="2026-10-05")
    before = head(campaign)
    with pytest.raises(JournalInputError):
        commit_session_verb(
            campaign.id,
            actor,
            update={"defeated": True},
            session_id=play.id,
            headline="",
            request_key="invalid",
        )
    assert head(campaign) == before
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(models.EntitySessionState)) == 0
    commit_session_verb(
        campaign.id,
        actor,
        update={"defeated": True},
        session_id=play.id,
        headline="Defeat",
        request_key="valid",
    )
    row = list_entries(campaign.id)[0][0]
    commit_session_verb(campaign.id, actor, update={"defeated": False})
    before = head(campaign)
    with pytest.raises(JournalConflictError):
        take_back_entry(campaign.id, row.id, version=1)
    assert head(campaign) == before and not get_entry(campaign.id, row.id).corrected


def test_plain_remove_retains_state_and_undo_restores_same_entry(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Battle", play_date="2026-10-05")
    commit_session_verb(
        campaign.id,
        actor,
        update={"defeated": True},
        session_id=play.id,
        headline="Defeat",
        request_key="valid",
    )
    row = list_entries(campaign.id)[0][0]
    delete_entry(campaign.id, row.id, version=1)
    assert not list_entries(campaign.id)[0]
    with session_scope() as db:
        assert db.scalar(select(models.EntitySessionState)).data["defeated"]
    undo(campaign.id, head(campaign))
    assert get_entry(campaign.id, row.id).id == row.id


def test_active_session_total_delete(world):
    campaign, _ = world
    play = create_session(campaign.id, title="First", play_date="2026-10-05")
    assert activate_session(campaign.id, play.id).active_session_id == play.id
    create_entry(campaign.id, session_id=play.id, headline="One", request_key="one")
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(models.JournalRequest)) == 1
    assert delete_campaign(campaign.owner_id, campaign.id)
    with session_scope() as db:
        for model in (models.PlaySession, models.JournalEntry, models.JournalRequest):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_mentions_reject_duplicate_overlap_and_bad_unicode_offsets(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Mentions", play_date="2026-10-05")
    ref = {
        "entity_id": actor,
        "label": "Míra",
        "token": "@Míra",
        "field": "context",
        "start": 2,
        "end": 7,
    }
    for refs in ([ref, ref], [{**ref, "start": 3, "end": 8}]):
        with pytest.raises(JournalInputError):
            create_entry(
                campaign.id,
                session_id=play.id,
                headline="Mentions",
                context="🐲 @Míra",
                references=refs,
                request_key="invalid",
            )
    # Leading whitespace is authored text and offsets continue to refer to it.
    row = create_entry(
        campaign.id,
        session_id=play.id,
        headline="  @Míra",
        references=[{**ref, "field": "headline"}],
        request_key="valid",
    )
    assert row.headline == "  @Míra"


def test_correction_does_not_conflate_boolean_with_integer(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Battle", play_date="2026-10-05")
    commit_session_verb(
        campaign.id,
        actor,
        update={"defeated": True},
        session_id=play.id,
        headline="Defeat",
        request_key="valid",
    )
    row = list_entries(campaign.id)[0][0]
    commit_session_verb(campaign.id, actor, update={"defeated": 1})
    with pytest.raises(JournalConflictError):
        take_back_entry(campaign.id, row.id, version=1)


def test_head_undo_paired_creation_and_redo_restore_state_entry(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Battle", play_date="2026-10-05")
    revision = commit_session_verb(
        campaign.id,
        actor,
        update={"defeated": True},
        session_id=play.id,
        headline="Defeat",
        request_key="valid",
    )
    original = list_entries(campaign.id)[0][0]
    taken_back = undo(campaign.id, revision.id)
    assert not list_entries(campaign.id)[0]
    with session_scope() as db:
        assert db.scalar(select(models.EntitySessionState)) is None
    undo(campaign.id, taken_back.id)
    assert list_entries(campaign.id)[0][0].id == original.id
    with session_scope() as db:
        assert db.scalar(select(models.EntitySessionState)).data == {"defeated": True}


def test_sparse_reorder_filter_and_pagination_use_story_chronology(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Story", play_date="2026-10-05")
    first = create_entry(
        campaign.id,
        session_id=play.id,
        headline="First",
        request_key="first",
        references=[{"entity_id": actor, "label": "Míra"}],
    )
    second = create_entry(campaign.id, session_id=play.id, headline="Second", request_key="second")
    inserted = create_entry(
        campaign.id, session_id=play.id, headline="Inserted", position=1536, request_key="insert"
    )
    page, cursor = list_entries(campaign.id, limit=2)
    assert [row.id for row in page] == [first.id, inserted.id] and cursor == inserted.id
    assert [row.id for row in list_entries(campaign.id, cursor=cursor)[0]] == [second.id]
    update_entry(campaign.id, second.id, version=1, position=512)
    assert [row.id for row in list_entries(campaign.id)[0]] == [second.id, first.id, inserted.id]
    assert [row.id for row in list_entries(campaign.id, entity_id=actor)[0]] == [first.id]
    with pytest.raises(InvalidCursorError):
        list_entries(campaign.id, entity_id=actor, cursor=second.id)


def test_historical_promotion_is_once_without_state_reapplication(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Recall", play_date="2026-10-05")
    revision = commit_session_verb(campaign.id, actor, update={"defeated": True})
    with session_scope() as db:
        source = db.scalar(select(models.Event).where(models.Event.revision_id == revision.id)).id
    row = create_entry(
        campaign.id,
        session_id=play.id,
        headline="A prior defeat",
        source_event_id=source,
        request_key="recall",
    )
    assert row.action_revision_id is None
    with pytest.raises(JournalConflictError):
        create_entry(
            campaign.id,
            session_id=play.id,
            headline="Duplicate",
            source_event_id=source,
            request_key="duplicate",
        )
    with pytest.raises(JournalConflictError):
        take_back_entry(campaign.id, row.id, version=1)
    with session_scope() as db:
        assert db.scalar(select(models.EntitySessionState)).data == {"defeated": True}


def test_active_and_session_head_undo_have_monotonic_versions(world):
    campaign, _ = world
    play = create_session(campaign.id, title="Session", play_date="2026-10-05")
    activate_session(campaign.id, play.id)
    undo(campaign.id, head(campaign))
    with session_scope() as db:
        assert db.get(models.Campaign, campaign.id).active_session_id is None
    update_session(campaign.id, play.id, version=1, title="Edited")
    undo(campaign.id, head(campaign))
    with session_scope() as db:
        current = db.get(models.PlaySession, play.id)
        assert current.title == "Session" and current.version == 3


def test_correction_preserves_unrelated_later_json_type_edit(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Battle", play_date="2026-10-05")
    commit_session_verb(campaign.id, actor, update={"scar": True})
    commit_session_verb(
        campaign.id,
        actor,
        update={"defeated": True},
        session_id=play.id,
        headline="Defeat",
        request_key="valid",
    )
    row = list_entries(campaign.id)[0][0]
    commit_session_verb(campaign.id, actor, update={"scar": 1})
    take_back_entry(campaign.id, row.id, version=1)
    with session_scope() as db:
        current = db.scalar(select(models.EntitySessionState)).data
        assert current == {"scar": 1} and type(current["scar"]) is int


def test_repeated_front_insertions_open_exhausted_slots_in_one_revision(world):
    campaign, _ = world
    play = create_session(campaign.id, title="Crowded story", play_date="2026-10-05")
    first = create_entry(campaign.id, session_id=play.id, headline="First", request_key="first")
    expected = [first.id]
    for index in range(15):
        front = list_entries(campaign.id)[0][0]
        position = max(1, front.position // 2)
        with session_scope() as db:
            before_count = db.scalar(select(func.count()).select_from(models.Revision))
        inserted = create_entry(
            campaign.id,
            session_id=play.id,
            headline=f"Inserted {index}",
            request_key=f"insert-{index}",
            position=position,
        )
        expected.insert(0, inserted.id)
        entries = list_entries(campaign.id)[0]
        assert [row.id for row in entries] == expected
        assert len({row.position for row in entries}) == len(entries)
        with session_scope() as db:
            assert db.scalar(select(func.count()).select_from(models.Revision)) == before_count + 1
    assert get_entry(campaign.id, first.id).version > 1
    with pytest.raises(JournalConflictError):
        update_entry(campaign.id, first.id, version=1, context="Stale after shifts")
    before_ids = expected[1:]
    inverse = undo(campaign.id, head(campaign))
    assert [row.id for row in list_entries(campaign.id)[0]] == before_ids
    undo(campaign.id, inverse.id)
    assert [row.id for row in list_entries(campaign.id)[0]] == expected


def test_move_to_occupied_position_records_shifts_and_undo_with_safe_bounds(world):
    campaign, _ = world
    play = create_session(campaign.id, title="Moves", play_date="2026-10-05")
    first = create_entry(campaign.id, session_id=play.id, headline="First", request_key="first")
    second = create_entry(campaign.id, session_id=play.id, headline="Second", request_key="second")
    moved = update_entry(campaign.id, second.id, version=1, position=first.position)
    assert moved.position == 1024 and moved.version == 2
    shifted = get_entry(campaign.id, first.id)
    assert shifted.position == 2048 and shifted.version == 2
    assert [row.id for row in list_entries(campaign.id)[0]] == [second.id, first.id]
    with pytest.raises(JournalConflictError):
        update_entry(campaign.id, first.id, version=1, context="Stale")
    undo(campaign.id, head(campaign))
    assert [row.id for row in list_entries(campaign.id)[0]] == [first.id, second.id]
    top = create_entry(
        campaign.id,
        session_id=play.id,
        headline="At limit",
        request_key="limit",
        position=2**53 - 1,
    )
    before = head(campaign)
    with pytest.raises(JournalInputError):
        create_entry(
            campaign.id,
            session_id=play.id,
            headline="Cannot shift beyond limit",
            request_key="overflow",
            position=top.position,
        )
    assert head(campaign) == before
    assert get_entry(campaign.id, top.id).position == 2**53 - 1


def test_paired_unchanged_action_rejects_and_cannot_apply_on_later_retry(world):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Battle", play_date="2026-10-05")
    original = commit_session_verb(campaign.id, actor, update={"defeated": True})
    args = {
        "update": {"defeated": True},
        "session_id": play.id,
        "headline": "Context only",
        "context": "Retain this draft",
        "request_key": "unchanged",
    }
    with pytest.raises(JournalConflictError, match="does not change current state"):
        commit_session_verb(campaign.id, actor, **args)
    assert head(campaign) == original.id and list_entries(campaign.id)[0] == []
    # The legacy no-story caller remains a successful no-op.
    assert commit_session_verb(campaign.id, actor, update={"defeated": True}).id == original.id
    later = commit_session_verb(campaign.id, actor, update={"defeated": False})
    with pytest.raises(JournalConflictError, match="does not change current state"):
        commit_session_verb(campaign.id, actor, **args)
    assert head(campaign) == later.id and list_entries(campaign.id)[0] == []
    with session_scope() as db:
        assert db.scalar(select(models.EntitySessionState)).data == {"defeated": False}
        assert db.scalar(select(func.count()).select_from(models.JournalRequest)) == 1
    with pytest.raises(JournalConflictError, match="different request"):
        commit_session_verb(campaign.id, actor, **{**args, "headline": "Changed request"})


@pytest.mark.parametrize("history", ["away_back", "legacy_undo", "correction_undo"])
def test_correction_rejects_later_action_history_even_when_value_returns(world, history):
    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Battle", play_date="2026-10-05")
    commit_session_verb(
        campaign.id,
        actor,
        update={"defeated": True},
        session_id=play.id,
        headline="Original defeat",
        request_key="original",
    )
    row = list_entries(campaign.id)[0][0]
    if history == "away_back":
        commit_session_verb(campaign.id, actor, update={"defeated": False})
        commit_session_verb(campaign.id, actor, update={"defeated": True})
    elif history == "legacy_undo":
        later = commit_session_verb(campaign.id, actor, update={"defeated": False})
        undo(campaign.id, later.id)
    else:
        take_back_entry(campaign.id, row.id, version=1)
        undo(campaign.id, head(campaign))
        row = get_entry(campaign.id, row.id)
    before = head(campaign)
    with pytest.raises(JournalConflictError, match="later committed action"):
        take_back_entry(campaign.id, row.id, version=row.version)
    assert head(campaign) == before and not get_entry(campaign.id, row.id).corrected
    with session_scope() as db:
        assert db.scalar(select(models.EntitySessionState)).data == {"defeated": True}


@pytest.mark.parametrize(
    "surface",
    [
        "session_title",
        "session_key",
        "entry_headline",
        "entry_context",
        "entry_key",
        "edit_headline",
        "edit_context",
        "action_headline",
        "action_context",
        "action_key",
    ],
)
def test_lone_surrogate_rejects_without_any_partial_journal_or_state_write(world, surface):
    from app.store.journal import JournalUnicodeError

    campaign, _ = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Unicode", play_date="2026-10-05")
    entry = create_entry(
        campaign.id, session_id=play.id, headline="Original", request_key="original"
    )
    before = head(campaign)
    with session_scope() as db:
        receipt_count = db.scalar(select(func.count()).select_from(models.JournalRequest))
    invalid = "lone \ud800 surrogate"
    with pytest.raises(JournalUnicodeError):
        if surface == "session_title":
            create_session(campaign.id, title=invalid, play_date="2026-10-05")
        elif surface == "session_key":
            create_session(campaign.id, title="Valid", play_date="2026-10-05", request_key=invalid)
        elif surface.startswith("edit_"):
            update_entry(
                campaign.id, entry.id, version=1, **{surface.removeprefix("edit_"): invalid}
            )
        elif surface.startswith("entry_"):
            args = {
                "session_id": play.id,
                "headline": "Valid",
                "context": "Valid",
                "request_key": "valid",
            }
            args["request_key" if surface == "entry_key" else surface.removeprefix("entry_")] = (
                invalid
            )
            create_entry(campaign.id, **args)
        else:
            args = {
                "update": {"defeated": True},
                "session_id": play.id,
                "headline": "Valid",
                "context": "Valid",
                "request_key": "valid",
            }
            args["request_key" if surface == "action_key" else surface.removeprefix("action_")] = (
                invalid
            )
            commit_session_verb(campaign.id, actor, **args)
    assert head(campaign) == before
    assert get_entry(campaign.id, entry.id).headline == "Original"
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(models.JournalRequest)) == receipt_count
        assert db.scalar(select(func.count()).select_from(models.PlaySession)) == 1
        assert db.scalar(select(func.count()).select_from(models.JournalEntry)) == 1
        assert db.scalar(select(func.count()).select_from(models.EntitySessionState)) == 0


@pytest.mark.parametrize("deleted", [False, True])
def test_promoted_history_has_entity_reference_and_original_request_retry(world, deleted):
    campaign, other = world
    actor = entity(campaign)
    play = create_session(campaign.id, title="Recall", play_date="2026-10-05")
    revision = commit_session_verb(campaign.id, actor, update={"defeated": True})
    with session_scope() as db:
        source = db.scalar(select(models.Event).where(models.Event.revision_id == revision.id)).id
    update_entity(campaign.id, actor, patch={"name": "Captain Míra"})
    if deleted:
        delete_entity(campaign.id, actor)
    original = {
        "session_id": play.id,
        "headline": "A prior defeat",
        "source_event_id": source,
        "references": [],
        "request_key": "recall",
    }
    row = create_entry(campaign.id, **original)
    assert row.action_entity_id == actor
    assert row.references == [{"entity_id": actor, "label": "Míra" if deleted else "Captain Míra"}]
    assert original["references"] == [] and row.action_revision_id is None
    retry = create_entry(campaign.id, **original)
    assert retry.id == row.id and retry.references == row.references
    edited = update_entry(campaign.id, row.id, version=1, context="More of the story")
    assert edited.references == row.references
    with pytest.raises(JournalNotFoundError):
        foreign_session = create_session(other.id, title="Foreign", play_date="2026-10-05")
        create_entry(
            other.id,
            session_id=foreign_session.id,
            headline="Foreign recall",
            source_event_id=source,
            request_key="foreign",
        )
    with session_scope() as db:
        current = db.scalar(
            select(models.EntitySessionState).where(
                models.EntitySessionState.campaign_id == campaign.id,
                models.EntitySessionState.entity_id == actor,
            )
        )
        assert current.data == {"defeated": True}
