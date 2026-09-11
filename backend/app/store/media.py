"""Media manifest store (AD-10, spec-4.1).

The store is the ONLY writer of the ``media`` table (AD-1 — the media
service and the API never write it directly): ``add_media`` is the single
write seam (the runner orders the FILE write before it, so a manifest row
never dangles over a missing file) and ``delete_entity_media`` is the
entity-delete seam (used by ``_delete_entity`` inside its transaction;
spec-4.3 reclaims rows with their entity — ``delete_campaign`` bulk-deletes
its campaign's rows in the same spirit, and undo of an entity-CREATION
revision leaves rows behind, deferred). ``delete_media_row`` /
``delete_one_media`` are the single-row seam behind the DM's portrait
delete (spec-4.3) — one row, the file reclaimed after the commit. The row is the index of record
for a generated portrait; the file lives under
``media_dir/{campaign_id}/{entity_id}/{filename}``.

Media rows are NOT world graph: no revision, no event (AD-1 governs the
graph); 4-3 projects them into exports with an on-disk ``available`` flag
but they remain an index undo never restores. Reads
are campaign-scoped — a foreign campaign is the indistinguishable 404 the
API layer maps (AD-9).
"""

from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import delete, literal_column, select
from sqlalchemy.orm import Session

from app.core import ids, time
from app.store import models
from app.store.commit import StoreError, UnknownCampaignError, UnknownEntityError
from app.store.db import session_scope


class MediaNotFoundError(StoreError):
    """No media row for the requested (campaign, entity, filename) — or a
    row whose file is gone from disk (-> 404: FOREIGN_CAMPAIGN's store
    read and ROW_WITHOUT_FILE both surface here)."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"media not found: {detail}")
        self.detail = detail


class InvalidMediaError(StoreError):
    """Malformed media-writer input (non-ULID filename stem) — rejected
    with zero rows written (-> 422, the store-boundary 4xx rule)."""


def add_media(
    campaign_id: str,
    entity_id: str,
    filename: str,
    kind: str = "image",
) -> models.Media:
    """Write one manifest row — the store's ONLY media write (AD-1).

    Rejects (zero rows) with ``UnknownCampaignError`` (404) for an
    unknown campaign, ``UnknownEntityError`` (404) for a missing or
    foreign entity, and ``InvalidMediaError`` (422) for a filename whose
    stem is not a ULID (conventions.md — the runner mints fresh ULIDs)
    or a blank kind. The campaign/entity existence is re-checked inside
    this transaction, so the writer's precondition can never rot between
    the runner's read and this write.
    """
    if not isinstance(filename, str) or not filename.strip():
        raise InvalidMediaError("media filename must be a non-blank string")
    if len(filename) > 500:
        raise InvalidMediaError("media filename exceeds 500 chars")
    stem = Path(filename).stem
    if not ids.is_valid_ulid(stem):
        raise InvalidMediaError(f"media filename stem must be a ULID: {filename!r}")
    if not isinstance(kind, str) or not kind.strip():
        raise InvalidMediaError("media kind must be a non-blank string")
    with session_scope() as session:
        if session.get(models.Campaign, campaign_id) is None:
            raise UnknownCampaignError(campaign_id)
        entity = session.get(models.Entity, entity_id)
        if entity is None or entity.campaign_id != campaign_id:
            raise UnknownEntityError(entity_id)
        row = models.Media(
            id=ids.new_id(),
            campaign_id=campaign_id,
            entity_id=entity_id,
            filename=filename,
            kind=kind,
            created_at=time.now(),
        )
        session.add(row)
        return row


def list_media(campaign_id: str) -> Sequence[models.Media]:
    """All manifest rows of a campaign, rowid (insertion) order —
    deterministic retrieval (AD-16), matching the other read helpers'
    documented ordering. Unknown campaign -> ``UnknownCampaignError``.
    """
    with session_scope() as session:
        if session.get(models.Campaign, campaign_id) is None:
            raise UnknownCampaignError(campaign_id)
        return session.scalars(
            select(models.Media)
            .where(models.Media.campaign_id == campaign_id)
            .order_by(literal_column("rowid"))
        ).all()


def delete_entity_media(session: Session, campaign_id: str, entity_id: str) -> None:
    """Delete every manifest row of one entity INSIDE the caller's
    transaction — the entity-delete seam (AD-1; the caller is
    ``_delete_entity``, spec-4.3). The store's other row deletions are
    ``delete_campaign``'s bulk sweep and undo's leave-behind on
    entity-creation reverts; neither routes through here.

    Media rows are NOT world graph: no events, no revision delta, and
    undo does not restore them (``store/undo.py`` — regeneration is the
    recovery). File reclaim is the API layer's post-commit job (AD-10
    rows-first ordering); this seam only removes rows. The entity's
    existence is the caller's precondition — it has just resolved the
    entity row and is about to delete it.
    """
    session.execute(
        delete(models.Media).where(
            models.Media.campaign_id == campaign_id,
            models.Media.entity_id == entity_id,
        )
    )


def delete_media_row(
    session: Session, campaign_id: str, entity_id: str, media_id: str
) -> models.Media | None:
    """Delete ONE manifest row INSIDE the caller's transaction — the
    single-row twin of ``delete_entity_media`` (the caller is
    ``delete_one_media``; the API layer is the only caller). Returns the
    deleted row — the API's file-reclaim source, ``filename`` — or None
    when no row matches (unknown id, or an id belonging to another entity
    or another campaign: all the same miss, AD-9).

    Media rows are NOT world graph: no events, no revision delta, and undo
    does not restore them (``store/undo.py`` — regeneration is the
    recovery, spec-4.3). File reclaim is the API layer's post-commit job
    (AD-10 rows-first ordering); this seam only removes the row.
    """
    row = session.scalar(
        select(models.Media).where(
            models.Media.campaign_id == campaign_id,
            models.Media.entity_id == entity_id,
            models.Media.id == media_id,
        )
    )
    if row is None:
        return None
    session.delete(row)
    return row


def delete_one_media(campaign_id: str, entity_id: str, media_id: str) -> models.Media:
    """Delete one manifest row in its own transaction; returns it.

    Unknown campaign -> ``UnknownCampaignError``; no row for this
    (campaign, entity, id) -> ``MediaNotFoundError`` — the DELETE's 404
    (the campaign and the row miss are distinct only in code, never on
    the wire beyond the ownership gate).
    """
    with session_scope() as session:
        if session.get(models.Campaign, campaign_id) is None:
            raise UnknownCampaignError(campaign_id)
        row = delete_media_row(session, campaign_id, entity_id, media_id)
        if row is None:
            raise MediaNotFoundError(f"{entity_id}/{media_id}")
        return row


def get_media_file(campaign_id: str, entity_id: str, filename: str) -> models.Media:
    """The manifest row backing a served file: exactly one row per
    (campaign, entity, filename).

    Unknown campaign -> ``UnknownCampaignError``; no such row ->
    ``MediaNotFoundError`` — the file GET's row check (the API then
    re-checks the file on disk for ROW_WITHOUT_FILE).
    """
    with session_scope() as session:
        if session.get(models.Campaign, campaign_id) is None:
            raise UnknownCampaignError(campaign_id)
        row = session.scalar(
            select(models.Media).where(
                models.Media.campaign_id == campaign_id,
                models.Media.entity_id == entity_id,
                models.Media.filename == filename,
            )
        )
        if row is None:
            raise MediaNotFoundError(f"{entity_id}/{filename}")
        return row
