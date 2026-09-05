"""Portrait media REST surface (spec-4.1, AD-10).

Authed, owner-only READ surface over the media manifest: the campaign's
manifest list and the per-file image GET. Every read is ownership-404-
first (the campaign route pattern — a foreign or unknown campaign is the
single indistinguishable 404, never a 403/200, AD-9); the file route
then checks the manifest row through the store (the FILE_GET matrix
rows) and the file on disk — a row whose file is gone is the same 404
(ROW_WITHOUT_FILE), never a 500.

The API never writes the manifest: media rows are written only through
the store's ``add_media`` (AD-1 — the media service owns generation, the
store owns the row). The image file is served from
``media_dir/{campaign_id}/{entity_id}/{filename}`` over the same-origin
session cookie (the cookie's path is ``/api``, so ``<img>`` GETs
authenticate like any other API read).
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.core.settings import configured_media_dir
from app.store import (
    MediaNotFoundError,
    StoreError,
    get_campaign,
    get_media_file,
    list_media,
    models,
)

router = APIRouter()


class MediaResponse(BaseModel):
    """One media manifest row (AD-10) — the entity card's portrait index."""

    id: str
    campaign_id: str
    entity_id: str
    filename: str
    kind: str
    created_at: str


class MediaListResponse(BaseModel):
    """The campaign's media manifest, rowid (insertion) order."""

    media: list[MediaResponse]


def _media_response(row: models.Media) -> MediaResponse:
    return MediaResponse(
        id=row.id,
        campaign_id=row.campaign_id,
        entity_id=row.entity_id,
        filename=row.filename,
        kind=row.kind,
        created_at=row.created_at,
    )


@router.get("/api/campaigns/{campaign_id}/media", response_model=MediaListResponse)
def list_campaign_media(
    campaign_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> MediaListResponse:
    """The campaign's media manifest (FOREIGN_CAMPAIGN: owner-only, the
    single indistinguishable 404)."""
    # Ownership first: a foreign or unknown campaign is the single
    # 404 (campaign route pattern, no oracle).
    if get_campaign(current.id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    try:
        rows = list_media(campaign_id)
    except StoreError as exc:
        store_error_as_http(exc)
    return MediaListResponse(media=[_media_response(row) for row in rows])


@router.get("/api/campaigns/{campaign_id}/media/{entity_id}/{filename}")
def get_file(
    campaign_id: str,
    entity_id: str,
    filename: str,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> FileResponse:
    """One portrait file — ownership + manifest row + disk checks, all 404.

    The row lookup is store-side (AD-9) and the disk check is the
    ROW_WITHOUT_FILE row: a manifest row whose file was removed is the
    same 404, never a server error. The filename is a manifest row's
    runner-minted ``<ulid>.png``; the row lookup is the traversal guard
    (a non-row name can never reach the filesystem), and separators are
    rejected outright.
    """
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        # Traversal-shaped names never reach the filesystem or the row
        # lookup — the store-family 404 through the shared mapper.
        store_error_as_http(MediaNotFoundError(f"illegal filename: {filename!r}"))
    if get_campaign(current.id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    try:
        get_media_file(campaign_id, entity_id, filename)
    except StoreError as exc:
        store_error_as_http(exc)
    path = Path(configured_media_dir()) / campaign_id / entity_id / filename
    if not path.is_file():
        # ROW_WITHOUT_FILE: a manifest row whose file is gone is the same
        # store-family 404 — through the shared mapper, never a 500.
        store_error_as_http(MediaNotFoundError(f"file {entity_id}/{filename} missing on disk"))
    # ``inline`` disposition (Starlette's FileResponse defaults to
    # ``attachment``): a portrait must render inside the entity card's
    # ``<img>``, never download (acceptance criterion 2).
    return FileResponse(
        path,
        media_type="image/png",
        filename=filename,
        content_disposition_type="inline",
    )


__all__ = ["router"]
