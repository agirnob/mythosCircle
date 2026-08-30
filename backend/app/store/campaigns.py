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

from sqlalchemy import delete, select

from app.core import ids, time
from app.store import models
from app.store.db import session_scope

#: The open, user-extendable theme seed list (AR27). Extending it is a
#: config change (deploy/config.toml [campaigns].themes, consumed in 1.7),
#: mirrored here and pinned by a deploy-contract test.
SEED_THEMES: frozenset[str] = frozenset({"High Fantasy", "Grimdark", "Steampunk", "Planar"})

DEFAULT_LIST_LIMIT = 50


class InvalidThemeError(ValueError):
    """A theme outside the seed list."""


def normalize_theme(theme: str) -> str:
    """Canonical form of a theme (case-insensitive match on the seed)."""
    theme = theme.strip()
    for candidate in SEED_THEMES:
        if candidate.lower() == theme.lower():
            return candidate
    raise InvalidThemeError(f"theme {theme!r} is not in the seed list: {sorted(SEED_THEMES)}")


def _require_non_blank(value: str, field: str) -> str:
    """Trim and reject a blank field (spec-1.6 CREATE_BLANK contract)."""
    trimmed = value.strip()
    if not trimmed:
        raise InvalidThemeError(f"{field} must not be blank")
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
        raise ValueError("limit must be >= 1")
    from sqlalchemy import literal_column

    with session_scope() as session:
        query = select(models.Campaign).where(models.Campaign.owner_id == owner_id)
        if cursor is not None:
            # Owner-scoped cursor: a foreign or deleted campaign id must not
            # silently reset the page or act as an existence oracle (NFR6).
            owner_of_cursor = session.scalar(
                select(models.Campaign.owner_id).where(models.Campaign.id == cursor)
            )
            if owner_of_cursor != owner_id:
                raise ValueError("cursor names a campaign you do not own")
            anchor = session.scalar(
                select(literal_column("rowid"))
                .select_from(models.Campaign)
                .where(models.Campaign.id == cursor)
            )
            if anchor is not None:
                query = query.where(literal_column("rowid") > anchor)
        rows = session.scalars(query.order_by(literal_column("rowid")).limit(limit + 1)).all()
        page = rows[:limit]
        has_more = len(rows) > limit
        next_cursor = page[-1].id if has_more and page else None
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
                raise ValueError(f"unknown campaign field: {key}")
            if value is None:
                raise ValueError(f"campaign field {key} must not be null")
            if key in {"title", "theme"}:
                _require_non_blank(value, key)
            setattr(row, key, value.strip())
        return row


def delete_campaign(owner_id: str, campaign_id: str) -> bool:
    """AR20 total hard delete of an owned campaign; False for unknown/foreign.

    One transaction: remove the campaign row and cascade revisions, events,
    entities, edges, jobs, and media-manifest rows. Any failure rolls the
    whole delete back — nothing is half-removed.
    """
    deleted = False
    with session_scope() as session:
        row = session.get(models.Campaign, campaign_id)
        if row is None or row.owner_id != owner_id:
            return False
        session.execute(delete(models.Event).where(models.Event.campaign_id == campaign_id))
        session.execute(delete(models.Revision).where(models.Revision.campaign_id == campaign_id))
        session.execute(delete(models.Entity).where(models.Entity.campaign_id == campaign_id))
        session.execute(delete(models.Edge).where(models.Edge.campaign_id == campaign_id))
        session.execute(delete(models.Job).where(models.Job.campaign_id == campaign_id))
        session.execute(delete(models.Media).where(models.Media.campaign_id == campaign_id))
        session.delete(row)
        deleted = True
    return deleted
