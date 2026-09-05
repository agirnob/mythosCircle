"""Media manifest store (AD-10, spec-4.1).

The store is the ONLY writer of the ``media`` table (AD-1 — the media
service and the API never write it directly): ``add_media`` is the single
write seam, and the runner orders the FILE write before it, so a manifest
row never dangles over a missing file. The row is the index of record for
a generated portrait; the file lives under
``media_dir/{campaign_id}/{entity_id}/{filename}``.

Media rows are NOT world graph: no revision, no event (AD-1 governs the
graph; manifest rows are an index, not state the export projects). Reads
are campaign-scoped — a foreign campaign is the indistinguishable 404 the
API layer maps (AD-9).
"""

from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import literal_column, select

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
