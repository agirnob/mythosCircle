"""Campaign store tests: owner-scoped CRUD + the AR20 cascade delete
(spec-1.6). Deterministic fixtures, one scratch DB per test.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core import ids, time
from app.store import (
    CampaignInputError,
    InvalidThemeError,
    app_db_url,
    commit_subgraph,
    create_campaign,
    delete_campaign,
    enqueue_job,
    get_campaign,
    init_db,
    list_campaigns,
    models,
    register_account,
    session_scope,
    update_campaign,
)

MISSING_ID = "0" * 26


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[None]:
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'campaigns.db'}")
    try:
        yield
    finally:
        init_db(previous)


def _owner(tag: str = "a") -> str:
    return register_account(f"owner-{tag}@example.com", "password123").id


def _create(owner: str, title: str = "Aetheria") -> str:
    return create_campaign(
        owner, title=title, description="A living world", theme="High Fantasy", custom_lore="spicy"
    ).id


def _seed_graph(campaign_id: str) -> None:
    """A committed subgraph + a queue job + a media row for the cascade."""
    bar_id, mira_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            models.EntityInput(kind="character", name="Mira Vane", id=mira_id),
        ],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1)],
        base_revision=None,
    )
    enqueue_job(campaign_id, "text", {"prompt": "build"})
    with session_scope() as s:
        s.add(
            models.Media(
                id=ids.new_id(),
                campaign_id=campaign_id,
                entity_id=bar_id,
                filename="bar.png",
                kind="image",
                created_at=time.now(),
            )
        )


def _counts(campaign_id: str) -> dict[str, int]:
    with session_scope() as s:
        counts = {
            "revisions": len(
                s.scalars(
                    select(models.Revision).where(models.Revision.campaign_id == campaign_id)
                ).all()
            ),
            "events": len(
                s.scalars(select(models.Event).where(models.Event.campaign_id == campaign_id)).all()
            ),
            "entities": len(
                s.scalars(
                    select(models.Entity).where(models.Entity.campaign_id == campaign_id)
                ).all()
            ),
            "edges": len(
                s.scalars(select(models.Edge).where(models.Edge.campaign_id == campaign_id)).all()
            ),
            "jobs": len(
                s.scalars(select(models.Job).where(models.Job.campaign_id == campaign_id)).all()
            ),
            "media": len(
                s.scalars(select(models.Media).where(models.Media.campaign_id == campaign_id)).all()
            ),
        }
    return counts


def test_create_campaign_seed_fields(db: None) -> None:
    owner = _owner()
    campaign = create_campaign(
        owner,
        title="  Grim Peaks  ",
        description=" deep valley ",
        theme="  gRiMdArK  ",  # case-insensitive theme
        custom_lore=" the old gods stir ",
    )
    assert campaign.owner_id == owner
    assert campaign.title == "Grim Peaks"  # trimmed
    assert campaign.description == "deep valley"
    assert campaign.theme == "Grimdark"  # canonical seed form
    assert campaign.custom_lore == "the old gods stir"


def test_create_bad_theme_rejected(db: None) -> None:
    with pytest.raises(InvalidThemeError):
        create_campaign(_owner(), title="X", description="", theme="Cyberpunk", custom_lore="")


def test_get_own_and_foreign(db: None) -> None:
    owner_a, owner_b = _owner("a"), _owner("b")
    a_id = _create(owner_a)
    assert get_campaign(owner_a, a_id) is not None  # own
    assert get_campaign(owner_b, a_id) is None  # foreign — same as unknown
    assert get_campaign(owner_a, MISSING_ID) is None  # unknown


def test_list_only_own(db: None) -> None:
    owner_a, owner_b = _owner("a"), _owner("b")
    _create(owner_a, "A1")
    _create(owner_a, "A2")
    _create(owner_b, "B1")
    mine, _next = list_campaigns(owner_a, limit=10)
    assert [c.title for c in mine] == ["A1", "A2"]
    theirs, _next = list_campaigns(owner_b, limit=10)
    assert [c.title for c in theirs] == ["B1"]


def test_update_own_and_foreign(db: None) -> None:
    owner_a, owner_b = _owner("a"), _owner("b")
    a_id = _create(owner_a)
    updated = update_campaign(owner_a, a_id, title="New Title", theme="Steampunk")
    assert updated is not None and updated.title == "New Title" and updated.theme == "Steampunk"
    # Partial update leaves other fields intact.
    updated2 = update_campaign(owner_a, a_id, custom_lore="new lore")
    assert updated2 is not None
    assert updated2.title == "New Title"  # untouched
    assert updated2.custom_lore == "new lore"
    # Foreign update is a no-op.
    assert update_campaign(owner_b, a_id, title="Hijack") is None
    still_own = get_campaign(owner_a, a_id)
    assert still_own is not None
    assert still_own.title == "New Title"  # unchanged


def test_delete_foreign_and_unknown_noop(db: None) -> None:
    owner_a, owner_b = _owner("a"), _owner("b")
    a_id = _create(owner_a)
    assert delete_campaign(owner_b, a_id) is False
    assert delete_campaign(owner_a, MISSING_ID) is False
    assert get_campaign(owner_a, a_id) is not None  # still there


def test_delete_cascades_all_world_rows(db: None) -> None:
    """AR20: one transaction removes the campaign AND its revisions,
    events, entities, edges, jobs, and media rows."""
    owner = _owner()
    campaign_id = _create(owner)
    _seed_graph(campaign_id)
    before = _counts(campaign_id)
    assert before["revisions"] >= 1 and before["jobs"] == 1 and before["media"] == 1

    assert delete_campaign(owner, campaign_id) is True
    assert get_campaign(owner, campaign_id) is None
    assert _counts(campaign_id) == {
        "revisions": 0,
        "events": 0,
        "entities": 0,
        "edges": 0,
        "jobs": 0,
        "media": 0,
    }


def test_list_pagination_cursor(db: None) -> None:
    owner = _owner()
    ids_list = [_create(owner, f"T{i}") for i in range(5)]
    page1, next_cursor = list_campaigns(owner, limit=2)
    assert [c.id for c in page1] == ids_list[:2]
    assert next_cursor == ids_list[1]
    page2, next_cursor2 = list_campaigns(owner, cursor=next_cursor, limit=2)
    assert [c.id for c in page2] == ids_list[2:4]
    page3, next_cursor3 = list_campaigns(owner, cursor=next_cursor2, limit=2)
    assert [c.id for c in page3] == ids_list[4:]
    assert next_cursor3 is None


def test_list_cursor_foreign_and_deleted_rejected(db: None) -> None:
    """A cursor naming another owner's or a deleted campaign is a
    ValueError (upstream 422), never a silent page reset or an existence
    oracle (NFR6, review round 1)."""
    owner_a, owner_b = _owner("a"), _owner("b")
    a_id = _create(owner_a)
    b_id = _create(owner_b)
    with pytest.raises(CampaignInputError):
        list_campaigns(owner_a, cursor=b_id)  # foreign owner
    delete_campaign(owner_a, a_id)
    with pytest.raises(CampaignInputError):
        list_campaigns(owner_a, cursor=a_id)  # deleted


def test_configured_seed_themes_falls_back_on_empty_config(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty config theme list falls back to the canonical code seed
    (app.core.config.DEFAULT_THEMES) — one canonical seed, never an empty
    allow-list that would reject every theme (epic-1 retro item 4)."""
    import app.store.campaigns as campaigns_mod
    from app.core.config import DEFAULT_THEMES

    monkeypatch.setattr(campaigns_mod, "configured_themes", lambda: [])
    assert campaigns_mod.configured_seed_themes() == set(DEFAULT_THEMES)
