"""Campaign CRUD with the AR27 world-seed model (spec-1.6).

Campaigns are private: every operation is owner-scoped (AD-9) and the
foreign-owner id is indistinguishable from an unknown id — ``None``/``False``
upstream, a single 404 down at the API (NFR6, no oracle).

Deletion is the AR20 total hard delete: one transaction that removes the
campaign row AND cascades revisions, events, entities, edges, jobs, and
media-manifest rows. Any failure rolls the whole delete back. This is the
only place in the codebase that deletes world rows — the commit path is
append-only; delete is the confirmed, campaign-scoped "history ends"
boundary.
"""

from collections.abc import Sequence

from sqlalchemy import delete, literal_column, select

from app.core import ids, time
from app.core.config import DEFAULT_THEMES
from app.core.pagination import InvalidCursorError, anchor_rowid, paging
from app.core.settings import configured_themes
from app.store import models
from app.store.commit import StoreError
from app.store.db import session_scope


#: The themes validated against — the config-resolved list (spec-1.7),
#: falling back to ``app.core.config.DEFAULT_THEMES`` when the config ships
#: an empty list. This module holds no copy of the seed (epic-1 retro item
#: 4: one canonical code seed; an empty config falls back to it).
def seed_themes() -> list[str]:
    """The theme seed list in config order (an empty config falls back to
    the code seed — epic-1 retro item 4: one canonical seed). The API
    serves this for the create-form picker; validation uses the set."""
    themes = configured_themes()
    return list(themes) if themes else list(DEFAULT_THEMES)


def configured_seed_themes() -> frozenset[str]:
    """The themes actually validated against — config list, code seed fallback."""
    return frozenset(seed_themes())


DEFAULT_LIST_LIMIT = 50


class CampaignInputError(StoreError):
    """Malformed campaign input — rejected with no state change (-> 422)."""


class InvalidThemeError(StoreError):
    """A theme outside the seed list."""


def normalize_theme(theme: str) -> str:
    """Canonical form of a theme (case-insensitive match, config-driven)."""
    theme = theme.strip()
    for candidate in configured_seed_themes():
        if candidate.lower() == theme.lower():
            return candidate
    raise InvalidThemeError(
        f"theme {theme!r} is not in the seed list: {sorted(configured_seed_themes())}"
    )


def _require_non_blank(value: str, field: str) -> str:
    """Trim and reject a blank field (spec-1.6 CREATE_BLANK contract)."""
    trimmed = value.strip()
    if not trimmed:
        raise CampaignInputError(f"{field} must not be blank")
    return trimmed


def create_campaign(
    owner_id: str,
    *,
    title: str,
    description: str,
    theme: str,
    custom_lore: str,
) -> models.Campaign:
    """Create one private world owned by ``owner_id`` (AR27, AD-9).

    Blank-after-trim title/theme is rejected; a legacy blank lore is
    accepted (custom_lore defaults empty in the fixture contract).
    """
    normalized_theme = normalize_theme(theme)
    with session_scope() as session:
        campaign = models.Campaign(
            id=ids.new_id(),
            owner_id=owner_id,
            title=_require_non_blank(title, "title"),
            description=description.strip(),
            theme=normalized_theme,
            custom_lore=custom_lore.strip(),
            created_at=time.now(),
        )
        session.add(campaign)
        return campaign


def list_campaigns(
    owner_id: str, cursor: str | None = None, limit: int = DEFAULT_LIST_LIMIT
) -> tuple[Sequence[models.Campaign], str | None]:
    """The caller's campaigns, oldest first (rowid), cursor-paginated.

    Never another owner's campaigns (AD-9/NFR6). Returns
    ``(campaigns, next_cursor_ulid_or_None)``.
    """
    if limit < 1:
        raise CampaignInputError("limit must be >= 1")
    with session_scope() as session:
        query = select(models.Campaign).where(models.Campaign.owner_id == owner_id)
        if cursor is not None:
            # Cursor semantics (epic-1 retro item 2): a cursor naming no
            # campaign (fabricated or deleted) is InvalidCursorError; a
            # cursor naming another owner's existing campaign is
            # CampaignInputError — neither acts as a silent page reset or
            # an existence oracle (NFR6). The anchor resolves first (it
            # owns the missing-row family), then the owner check.
            anchor = anchor_rowid(
                session,
                models.Campaign,
                cursor,
                missing_error=InvalidCursorError(f"cursor names no campaign: {cursor}"),
            )
            owner_of_cursor = session.scalar(
                select(models.Campaign.owner_id).where(models.Campaign.id == cursor)
            )
            if owner_of_cursor != owner_id:
                raise CampaignInputError("cursor names a campaign you do not own")
            query = query.where(literal_column("rowid") > anchor)
        rows = session.scalars(query.order_by(literal_column("rowid")).limit(limit + 1)).all()
        page, next_cursor = paging(rows, limit)
        return page, next_cursor


def get_campaign(owner_id: str, campaign_id: str) -> models.Campaign | None:
    """One campaign, only if owned by ``owner_id`` (else None — 404)."""
    with session_scope() as session:
        row = session.get(models.Campaign, campaign_id)
        if row is None or row.owner_id != owner_id:
            return None
        return row


def update_campaign(owner_id: str, campaign_id: str, **seed: str) -> models.Campaign | None:
    """Update seed fields of an owned campaign; None for unknown/foreign.

    ``seed`` keys are the AR27 fields (title/description/theme/custom_lore);
    each provided value is validated and applied.
    """
    with session_scope() as session:
        row = session.get(models.Campaign, campaign_id)
        if row is None or row.owner_id != owner_id:
            return None
        if "theme" in seed:
            seed["theme"] = normalize_theme(seed["theme"])
        for key, value in seed.items():
            if key not in {"title", "description", "theme", "custom_lore"}:
                raise CampaignInputError(f"unknown campaign field: {key}")
            if value is None:
                raise CampaignInputError(f"campaign field {key} must not be null")
            if key in {"title", "theme"}:
                _require_non_blank(value, key)
            setattr(row, key, value.strip())
        return row


def delete_campaign(owner_id: str, campaign_id: str) -> bool:
    """AR20 total hard delete of an owned campaign; False for unknown/foreign.

    One transaction: remove the campaign row and cascade revisions, events,
    entities, edges, session and knowledge state, proposed candidates
    (spec-3.1 staging rows carry FKs to campaign and job), jobs, and
    media-manifest rows. Any failure rolls
    the whole delete back — nothing is half-removed.
    """
    deleted = False
    with session_scope() as session:
        row = session.get(models.Campaign, campaign_id)
        if row is None or row.owner_id != owner_id:
            return False
        session.execute(
            delete(models.JournalRequest).where(models.JournalRequest.campaign_id == campaign_id)
        )
        session.execute(
            delete(models.JournalEntry).where(models.JournalEntry.campaign_id == campaign_id)
        )
        session.execute(
            delete(models.PlaySession).where(models.PlaySession.campaign_id == campaign_id)
        )
        session.execute(delete(models.Event).where(models.Event.campaign_id == campaign_id))
        session.execute(delete(models.Revision).where(models.Revision.campaign_id == campaign_id))
        for state_model in (models.EntitySessionState, models.EntityKnowledgeState):
            session.execute(delete(state_model).where(state_model.campaign_id == campaign_id))
        session.execute(delete(models.Entity).where(models.Entity.campaign_id == campaign_id))
        session.execute(delete(models.Edge).where(models.Edge.campaign_id == campaign_id))
        session.execute(
            delete(models.ProposedCandidate).where(
                models.ProposedCandidate.campaign_id == campaign_id
            )
        )
        session.execute(delete(models.Job).where(models.Job.campaign_id == campaign_id))
        session.execute(delete(models.Media).where(models.Media.campaign_id == campaign_id))
        session.delete(row)
        deleted = True
    return deleted


GENERIC_CAMPAIGN_TITLE = "Generic"


def ensure_generic_campaign(owner_id: str, theme: str) -> models.Campaign:
    """The account's Generic library world (owner spec, 2026-09-17):
    create-or-get the one ``is_generic`` campaign owned by ``owner_id``.

    The Generic world is a STORAGE context, never a narrative one: its
    entities and lore are excluded from generation (the build runner
    substitutes the payload theme's default seed), and its rows exist so
    characters generated without a canon world live somewhere queryable.
    The campaign's own seed fields are static library text; the
    generation context rides the job payload's theme.

    Unknown theme -> ``InvalidThemeError`` (422). One generic campaign
    per account regardless of theme — the theme rides each job.
    """
    normalized = normalize_theme(theme)
    with session_scope() as session:
        campaign = (
            session.query(models.Campaign)
            .filter(
                models.Campaign.owner_id == owner_id,
                models.Campaign.is_generic.is_(True),
            )
            .first()
        )
        if campaign is not None:
            session.expunge(campaign)
            return campaign
        fresh = models.Campaign(
            id=ids.new_id(),
            owner_id=owner_id,
            title=GENERIC_CAMPAIGN_TITLE,
            description=(
                "Character library — storage only. Characters here never "
                "influence each other or any generation."
            ),
            theme=normalized,
            custom_lore="",
            is_generic=True,
            created_at=time.now(),
        )
        session.add(fresh)
        session.flush()
        session.expunge(fresh)
        return fresh
