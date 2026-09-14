"""World-state export: read-only JSON + Markdown + styled HTML projections
(FR5, FR18, AD-11; spec-2.6, spec-4.3, spec-5.1).

Two surfaces over the same latest-revision snapshot, both pure reads via
the store's rowid-ordered read helpers — export never mutates state: no
revision, no event, no store write (AR18, AD-1):

- ``GET /api/campaigns/{id}/export?format=json|markdown|html`` — the full
  world state (all entities, typed edges, counters, stat blocks, media
  refs) plus the latest revision's id+created_at; Markdown as the
  Obsidian-complete document, HTML as a self-contained styled document
  (story 5.1: no embedded binaries at world level — paths + availability).
- ``GET /api/campaigns/{id}/entities/{eid}/export?format=json|markdown|html|owlbear|fg|maptool``
  — the single-entity projection (the engine Epic 5's VTT targets build
  on): the entity with its touching edges and the revision head; the HTML
  sheet embeds the available portrait as a data URI and converts to PDF
  via the browser, and the owlbear/fg/maptool adapters emit the VTT
  artifacts (spec-5.1, spec-5-2, spec-5-3, spec-5-4).

The renderers live in ``export_sheets`` — pure functions of the fetched
snapshot. A renderer raising is a commit-path regression, not a validation
gate (FR18): the projection never re-validates, but the failure is logged
as one ``export_failure`` JSON-lines event before the generic 500.

Only committed state is exported — candidates/proposed entities are
invisible here (AR7). Each entity's media-manifest rows ride along with an
``available`` flag resolved against disk (spec-4.3, FR14): a broken
reference is flagged, never dropped. Repeated exports are byte-identical:
determinism falls out of the read helpers' rowid ordering and stable dict
insertion order, and the ``exported_at`` stamp is the snapshot's own
timestamp — never a wall-clock read.
"""

import json
import logging
import math
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from app.api import export_sheets
from app.api.auth import get_current_account
from app.core.settings import configured_media_dir
from app.store import get_campaign, models
from app.store.db import session_scope
from app.store.read import campaign_seed, latest_revision, world_state

router = APIRouter()

#: FR18: export-failure events land on the JSON-lines log (spec-1.7
#: logging_setup) — export writes no world-state events (AD-1).
_logger = logging.getLogger(__name__)


class CampaignMeta(BaseModel):
    id: str
    title: str
    theme: str
    description: str
    custom_lore: str
    created_at: str


class RevisionMeta(BaseModel):
    id: str
    created_at: str


class MediaRefExport(BaseModel):
    """One manifest row riding an entity's export (spec-4.3, FR14):
    ``available`` is the file's on-disk presence — a broken reference is
    flagged, never dropped or hidden."""

    id: str
    kind: str
    filename: str
    available: bool


class EntityExport(BaseModel):
    id: str
    kind: str
    name: str
    text: str | None
    data: dict[str, Any]
    media: list[MediaRefExport]


class EdgeExport(BaseModel):
    id: str
    src: str
    dst: str
    type: str
    counter: int


class WorldExport(BaseModel):
    campaign: CampaignMeta
    revision: RevisionMeta | None
    entities: list[EntityExport]
    edges: list[EdgeExport]


class EntityExportDetail(BaseModel):
    """The single-entity JSON projection (spec-5.1): the entity, every
    edge touching it (rowid order — the same ordering the world document
    renders), and the revision head the snapshot was taken at."""

    entity: EntityExport
    edges: list[EdgeExport]
    revision: RevisionMeta | None


def _finite_only(value: Any) -> Any:
    """Coerce non-finite floats (NaN/inf can round-trip through the JSON
    column) to null — a read endpoint must never 500 on its data, and
    Starlette's JSONResponse forbids non-finite floats."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite_only(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite_only(item) for item in value]
    return value


def _media_refs(campaign_id: str, rows: Sequence[models.Media]) -> dict[str, list[MediaRefExport]]:
    """Group the campaign's manifest rows by entity, rowid (insertion)
    order preserved within each group (spec-4.3), each resolved against
    disk: ``available`` is the file's presence under
    ``{media_dir}/{campaign}/{entity}/{filename}`` — a missing file is
    flagged, never dropped (FR14). Pre-4.3 orphan rows (their entity is
    gone) belong to no listed entity and are excluded by the grouping
    (the row itself persists — outside this story's scope).
    """
    media_root = Path(configured_media_dir()) / campaign_id
    grouped: dict[str, list[MediaRefExport]] = {}
    for row in rows:
        grouped.setdefault(row.entity_id, []).append(
            MediaRefExport(
                id=row.id,
                kind=row.kind,
                filename=row.filename,
                available=(media_root / row.entity_id / row.filename).is_file(),
            )
        )
    return grouped


def _build_export(
    session: Session, campaign: models.Campaign, campaign_id: str
) -> WorldExport | None:
    """Assemble the latest-revision snapshot on the caller's open session.
    ``None`` when the campaign vanished between the ownership check and
    here — a concurrent delete must still 404, never produce a phantom
    export. The media manifest rides along (spec-4.3, FR14) as a plain
    SELECT on the same snapshot session — snapshot-consistent, and no
    nested session_scope (a second BEGIN IMMEDIATE under this
    transaction's write lock would deadlock). Rowid ordering mirrors
    list_media's documented ordering."""
    if campaign_seed(session, campaign_id) is None:
        return None
    revision = latest_revision(session, campaign_id)
    entities, edges = world_state(session, campaign_id)
    media_rows = session.scalars(
        select(models.Media)
        .where(models.Media.campaign_id == campaign_id)
        .order_by(literal_column("rowid"))
    ).all()
    media_by_entity = _media_refs(campaign_id, media_rows)
    return WorldExport(
        campaign=CampaignMeta(
            id=campaign.id,
            title=campaign.title,
            theme=campaign.theme,
            description=campaign.description,
            custom_lore=campaign.custom_lore,
            created_at=campaign.created_at,
        ),
        revision=RevisionMeta(id=revision.id, created_at=revision.created_at)
        if revision is not None
        else None,
        entities=[
            EntityExport(
                id=entity.id,
                kind=entity.kind,
                name=entity.name,
                text=entity.text,
                data=_finite_only(entity.data),
                media=media_by_entity.get(entity.id, []),
            )
            for entity in entities
        ],
        edges=[
            EdgeExport(
                id=edge.id,
                src=edge.src,
                dst=edge.dst,
                type=edge.type,
                counter=edge.counter,
            )
            for edge in edges
        ],
    )


def _attachment(
    render: Callable[[], str | bytes],
    *,
    filename: str,
    media_type: str,
    campaign_id: str,
    entity_id: str | None,
    fmt: str,
) -> Response:
    """Render and wrap as a download. A render failure is an FR18
    signal — exactly one ``export_failure`` log event (the store-event
    path is world-state-only, AD-1) — then re-raise: the generic 500
    handler owns the response shape, and a format assertion breaking IS
    a commit-path regression, not something to paper over here."""
    try:
        body = render()
    except Exception:
        _logger.error(
            "export_failure campaign_id=%s entity_id=%s format=%s",
            campaign_id,
            entity_id or "-",
            fmt,
            exc_info=True,
        )
        raise
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _download_stem(label: str, ident: str, fallback: str) -> str:
    """Human-readable attachment base name: an ASCII slug of the display
    label plus the short-id discriminator — deterministic, header-safe
    (RFC 6266 forbids raw non-ASCII here), and collision-resistant.
    A label with no ASCII characters at all falls back to the surface
    name; the ULID tail keeps the file unique either way."""
    slug = _SLUG_STRIP.sub("-", label.lower()).strip("-")[:48].rstrip("-")
    return f"{slug}-{ident[-8:]}" if slug else f"{fallback}-{ident[-8:]}"


@router.get("/api/campaigns/{campaign_id}/export", response_model=WorldExport)
def export_world(
    campaign_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
    format: Literal["json", "markdown", "html"] = "json",
) -> WorldExport | Response:
    """The complete latest-revision world state as JSON, Obsidian
    Markdown, or a self-contained styled HTML document."""
    # Ownership first: a foreign or unknown campaign is the single
    # indistinguishable 404 (campaign route pattern, no oracle).
    campaign = get_campaign(current.id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    with session_scope() as session:
        export = _build_export(session, campaign, campaign_id)
        if export is None:
            raise HTTPException(status_code=404, detail="Campaign not found.")
    if format == "json":
        return export
    if format == "markdown":
        stem = _download_stem(export.campaign.title, campaign_id, "world")
        return _attachment(
            lambda: export_sheets.render_world_markdown(export),
            filename=f"{stem}.md",
            media_type="text/markdown",
            campaign_id=campaign_id,
            entity_id=None,
            fmt="markdown",
        )
    stem = _download_stem(export.campaign.title, campaign_id, "world")
    return _attachment(
        lambda: export_sheets.render_world_html(export),
        filename=f"{stem}.html",
        media_type="text/html",
        campaign_id=campaign_id,
        entity_id=None,
        fmt="html",
    )


@router.get(
    "/api/campaigns/{campaign_id}/entities/{entity_id}/export",
    response_model=EntityExportDetail,
)
def export_entity(
    campaign_id: str,
    entity_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
    format: Literal["json", "markdown", "html", "owlbear", "fg", "maptool"] = "json",
) -> EntityExportDetail | Response:
    """One committed entity — edges touching it and the revision head —
    as JSON, Markdown, a print-ready HTML sheet, the Owlbear/Forge
    transfer payload, the Fantasy Grounds Unity 2024-record XML
    (spec-5-3), or the MapTool 1.18.6 ``.rptok`` token ZIP (spec-5-4).
    The entity-level projection is the engine Epic 5's VTT adapters
    consume (spec-5.1, spec-5-2, spec-5.3)."""
    campaign = get_campaign(current.id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    with session_scope() as session:
        export = _build_export(session, campaign, campaign_id)
        if export is None:
            raise HTTPException(status_code=404, detail="Campaign not found.")
    entity = next((e for e in export.entities if e.id == entity_id), None)
    if entity is None:
        # An entity outside the caller's owned world is indistinguishable
        # from a missing one — the same no-oracle rule, one level down.
        raise HTTPException(status_code=404, detail="Entity not found.")
    if format == "json":
        return EntityExportDetail(
            entity=entity,
            edges=[e for e in export.edges if entity_id in (e.src, e.dst)],
            revision=export.revision,
        )
    if format == "markdown":
        stem = _download_stem(export_sheets.name_labels(export)[entity_id], entity_id, "entity")
        return _attachment(
            lambda: export_sheets.render_entity_markdown(export, entity_id),
            filename=f"{stem}.md",
            media_type="text/markdown",
            campaign_id=campaign_id,
            entity_id=entity_id,
            fmt="markdown",
        )
    if format == "owlbear":
        stem = _download_stem(export_sheets.name_labels(export)[entity_id], entity_id, "entity")
        return _attachment(
            lambda: json.dumps(
                export_sheets.render_entity_owlbear(export, entity_id),
                ensure_ascii=False,
                indent=2,
            ),
            filename=f"{stem}.json",
            media_type="application/json",
            campaign_id=campaign_id,
            entity_id=entity_id,
            fmt="owlbear",
        )
    if format == "fg":
        stem = _download_stem(export_sheets.name_labels(export)[entity_id], entity_id, "entity")
        return _attachment(
            lambda: export_sheets.render_entity_fg(export, entity_id),
            filename=f"{stem}.xml",
            media_type="text/xml",
            campaign_id=campaign_id,
            entity_id=entity_id,
            fmt="fg",
        )
    if format == "maptool":
        stem = _download_stem(export_sheets.name_labels(export)[entity_id], entity_id, "entity")
        return _attachment(
            lambda: export_sheets.render_entity_maptool(export, entity_id),
            filename=f"{stem}.rptok",
            media_type="application/zip",
            campaign_id=campaign_id,
            entity_id=entity_id,
            fmt="maptool",
        )
    stem = _download_stem(export_sheets.name_labels(export)[entity_id], entity_id, "entity")
    return _attachment(
        lambda: export_sheets.render_entity_html(export, entity_id),
        filename=f"{stem}.html",
        media_type="text/html",
        campaign_id=campaign_id,
        entity_id=entity_id,
        fmt="html",
    )


__all__ = ["router"]
