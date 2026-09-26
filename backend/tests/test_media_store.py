"""Media manifest store tests (spec-4.1, AD-1/AD-10; spec-4.3 delete).

Pins the store-owned write/read invariants: ``add_media`` is the ONLY
media write seam, re-checks campaign + entity existence inside its own
transaction, and validates the ULID filename stem; reads are
campaign-scoped with the indistinguishable store errors the API maps
(FOREIGN_CAMPAIGN's store read, ROW_WITHOUT_FILE's row lookup). Spec-4.3
adds the two deletion seams — ``delete_entity_media`` (all of one
entity's rows, inside the caller's transaction) and
``delete_media_row``/``delete_one_media`` (exactly one named row, the
DM's portrait delete) — both row-only: media are not world graph, so no
revision and no event moves.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core import ids
from app.store import (
    InvalidMediaError,
    MediaNotFoundError,
    UnknownCampaignError,
    UnknownEntityError,
    add_media,
    app_db_url,
    commit_subgraph,
    create_campaign,
    delete_entity_media,
    delete_media_row,
    delete_one_media,
    get_media_file,
    init_db,
    list_media,
    models,
    prune_entity_media,
    session_scope,
    world_entities,
)


def _owner_id() -> str:
    """One owner account per scratch DB for campaign creation (spec-1.6)."""
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-media-{new_id()}@example.com", "password123").id


def _head(campaign_id: str) -> str | None:
    """The campaign's current head revision id (None on an empty world)."""
    from app.store import latest_revision

    with session_scope() as session:
        head = latest_revision(session, campaign_id)
        return head.id if head is not None else None


def _commit_entity(campaign_id: str, name: str = "Mira Vane") -> str:
    """One committed entity into the campaign (FR2: a new entity needs an
    edge, so the subgraph carries a small anchor pair); returns its id."""
    entity_id = ids.new_id()
    anchor_id = ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(kind="character", name=name, id=entity_id),
        ],
        [
            models.EdgeInput(
                src=entity_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
        base_revision=_head(campaign_id),
    )
    return entity_id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'media-store.db'}")
    try:
        yield create_campaign(
            _owner_id(), title="Media World", description="", theme="High Fantasy", custom_lore=""
        ).id
    finally:
        init_db(previous)


def test_add_media_writes_row_and_readers_agree(world: str) -> None:
    """HAPPY_PATH at the store: add_media writes exactly one manifest row
    (ULID id, campaign + entity scoped); list/get/get_media_file all find
    it in rowid (insertion) order."""
    entity_id = _commit_entity(world)
    filename = f"{ids.new_id()}.png"
    row = add_media(world, entity_id, filename, "image")
    assert row.kind == "image" and row.filename == filename
    rows = list_media(world)
    assert [r.id for r in rows] == [row.id]
    assert get_media_file(world, entity_id, filename).id == row.id
    with session_scope() as session:
        # The entity read sees the row too — same store, same DB.
        assert any(e.id == entity_id for e in world_entities(session, world))


def test_add_media_ordering_is_rowid(world: str) -> None:
    """Two rows: list_media returns insertion (rowid) order, AD-16."""
    entity_id = _commit_entity(world)
    first = add_media(world, entity_id, f"{ids.new_id()}.png", "image")
    second = add_media(world, entity_id, f"{ids.new_id()}.png", "image")
    assert [r.id for r in list_media(world)] == [first.id, second.id]


def test_add_media_unknown_campaign(world: str) -> None:
    """Unknown campaign -> UnknownCampaignError (404), zero rows."""
    with pytest.raises(UnknownCampaignError):
        add_media("0" * 26, _commit_entity(world), f"{ids.new_id()}.png", "image")
    assert list_media(world) == []


def test_add_media_unknown_entity(world: str) -> None:
    """Missing entity -> UnknownEntityError (404), zero rows."""
    with pytest.raises(UnknownEntityError):
        add_media(world, "0" * 26, f"{ids.new_id()}.png", "image")
    assert list_media(world) == []


def test_add_media_foreign_entity_rejected(world: str) -> None:
    """An entity of another campaign is the same UnknownEntityError (AD-9
    — no oracle; the media manifest never crosses campaigns)."""
    other = create_campaign(
        _owner_id(), title="Other", description="", theme="Grimdark", custom_lore=""
    ).id
    foreign_entity = _commit_entity(other, "Stranger")
    with pytest.raises(UnknownEntityError):
        add_media(world, foreign_entity, f"{ids.new_id()}.png", "image")
    assert list_media(world) == []


@pytest.mark.parametrize(
    "filename",
    ["", "   ", "not-a-ulid.png", "portrait.png", "01ARZ3NDEKTSV4EEFFX03PYT7U.txt"],
    ids=["blank", "whitespace", "non-ulid-stem", "legacy-name", "wrong-extension"],
)
def test_add_media_requires_ulid_filename(world: str, filename: str) -> None:
    """The filename stem must be a fresh ULID (conventions.md) — the
    runner's contract, enforced at the write boundary (422)."""
    entity_id = _commit_entity(world)
    with pytest.raises(InvalidMediaError):
        add_media(world, entity_id, filename, "image")
    assert list_media(world) == []


def test_reads_are_campaign_scoped(world: str) -> None:
    """FOREIGN_CAMPAIGN's store read: get_media_file on another
    campaign's row (or a fabricated name) -> MediaNotFoundError; the
    unknown-campaign form -> UnknownCampaignError."""
    other = create_campaign(
        _owner_id(), title="Other", description="", theme="Steampunk", custom_lore=""
    ).id
    entity_id = _commit_entity(world)
    row = add_media(world, entity_id, f"{ids.new_id()}.png", "image")
    with pytest.raises(MediaNotFoundError):
        get_media_file(other, entity_id, row.filename)
    with pytest.raises(MediaNotFoundError):
        get_media_file(world, entity_id, "0" * 26 + ".png")
    with pytest.raises(UnknownCampaignError):
        list_media("0" * 26)
    with pytest.raises(UnknownCampaignError):
        get_media_file("0" * 26, entity_id, row.filename)


def test_prune_entity_media_keeps_five_newest_rows(world: str) -> None:
    """KEEP-5 at the store seam (epic-4 retro item 13, owner ruling
    2026-09-10): prune_entity_media deletes every row beyond the 5
    newest (rowid order) in its own transaction and returns the deleted
    rows — the runner's file-reclaim source — mixed kinds sharing the
    one bounded history; other entities' rows survive; a second prune is
    a no-op."""
    entity_id = _commit_entity(world)
    other_id = _commit_entity(world, "Other")
    rows = [
        add_media(world, entity_id, f"{ids.new_id()}.png", "image"),
        add_media(world, entity_id, f"{ids.new_id()}.mp4", "video"),
        *[add_media(world, entity_id, f"{ids.new_id()}.png", "image") for _ in range(4)],
    ]
    kept_other = add_media(world, other_id, f"{ids.new_id()}.png", "image")
    assert len(rows) == 6

    pruned = prune_entity_media(world, entity_id)

    assert [r.id for r in pruned] == [rows[0].id]  # exactly the oldest row
    assert [r.id for r in list_media(world)] == [r.id for r in rows[1:]] + [kept_other.id]
    # Idempotent: nothing beyond the 5 newest to prune a second time.
    assert prune_entity_media(world, entity_id) == []


def test_delete_entity_media_deletes_rows_in_callers_session(world: str) -> None:
    """The spec-4.3 deletion seam: delete_entity_media removes exactly the
    entity's rows inside the CALLER's transaction (no session of its own
    — _delete_entity is the only caller); other entities' rows survive."""
    entity_id = _commit_entity(world)
    other_id = _commit_entity(world, "Other")
    gone_one = add_media(world, entity_id, f"{ids.new_id()}.png", "image")
    gone_two = add_media(world, entity_id, f"{ids.new_id()}.mp4", "video")
    kept = add_media(world, other_id, f"{ids.new_id()}.png", "image")
    with session_scope() as session:
        delete_entity_media(session, world, entity_id)
    remaining = list_media(world)
    assert [r.id for r in remaining] == [kept.id]
    assert gone_one.id not in {r.id for r in remaining}
    assert gone_two.id not in {r.id for r in remaining}


def test_delete_media_row_deletes_one_row_in_callers_session(world: str) -> None:
    """The spec-4.3 single-row seam: delete_media_row removes exactly the
    named row inside the CALLER's transaction (no session of its own) and
    returns it — the API's file-reclaim source; the same entity's other
    rows and every other entity's rows survive."""
    entity_id = _commit_entity(world)
    other_id = _commit_entity(world, "Other")
    target = add_media(world, entity_id, f"{ids.new_id()}.png", "image")
    kept_sibling = add_media(world, entity_id, f"{ids.new_id()}.png", "image")
    kept_other = add_media(world, other_id, f"{ids.new_id()}.png", "image")
    with session_scope() as session:
        deleted = delete_media_row(session, world, entity_id, target.id)
    assert deleted is not None
    assert deleted.id == target.id and deleted.filename == target.filename
    assert [r.id for r in list_media(world)] == [kept_sibling.id, kept_other.id]
    # A miss is a None return, not an error — the caller decides (404).
    with session_scope() as session:
        assert delete_media_row(session, world, entity_id, target.id) is None


def test_delete_one_media_removes_exactly_the_named_row(world: str) -> None:
    """The wrapper's happy path: the row is gone and every other row of
    the campaign stays (media rows are not world graph — no revision, no
    event is written by a media delete)."""
    entity_id = _commit_entity(world)
    target = add_media(world, entity_id, f"{ids.new_id()}.png", "image")
    kept = add_media(world, entity_id, f"{ids.new_id()}.mp4", "video")
    head_before = _head(world)

    deleted = delete_one_media(world, entity_id, target.id)

    assert deleted.id == target.id
    assert [r.id for r in list_media(world)] == [kept.id]
    assert _head(world) == head_before  # no revision written


def test_delete_one_media_misses_are_scoped(world: str) -> None:
    """Unknown campaign -> UnknownCampaignError; a fabricated id, another
    entity's id, and a foreign campaign's id are all the same
    MediaNotFoundError with every row left in place (AD-9 — a miss names
    no owner)."""
    other_campaign = create_campaign(
        _owner_id(), title="Other", description="", theme="Grimdark", custom_lore=""
    ).id
    entity_id = _commit_entity(world)
    foreign_entity = _commit_entity(other_campaign, "Stranger")
    mine = add_media(world, entity_id, f"{ids.new_id()}.png", "image")
    foreign = add_media(other_campaign, foreign_entity, f"{ids.new_id()}.png", "image")

    with pytest.raises(UnknownCampaignError):
        delete_one_media("0" * 26, entity_id, mine.id)
    with pytest.raises(MediaNotFoundError):
        delete_one_media(world, entity_id, "0" * 26)
    with pytest.raises(MediaNotFoundError):
        # The row exists, but not for this (campaign, entity) pair.
        delete_one_media(world, foreign_entity, mine.id)
    with pytest.raises(MediaNotFoundError):
        delete_one_media(world, entity_id, foreign.id)
    assert [r.id for r in list_media(world)] == [mine.id]
    assert [r.id for r in list_media(other_campaign)] == [foreign.id]
