"""Media manifest store tests (spec-4.1, AD-1/AD-10).

Pins the store-owned write/read invariants: ``add_media`` is the ONLY
media write seam, re-checks campaign + entity existence inside its own
transaction, and validates the ULID filename stem; reads are
campaign-scoped with the indistinguishable store errors the API maps
(FOREIGN_CAMPAIGN's store read, ROW_WITHOUT_FILE's row lookup).
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
    get_media_file,
    init_db,
    list_media,
    models,
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
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
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
