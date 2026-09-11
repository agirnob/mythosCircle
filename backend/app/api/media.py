"""Portrait media REST surface (spec-4.1, AD-10; spec-5.2 signed URLs).

Authed, owner-only READ surface over the media manifest: the campaign's
manifest list and the per-file image GET. Every read is ownership-404-
first (the campaign route pattern — a foreign or unknown campaign is the
single indistinguishable 404, never a 403/200, AD-9); the file route
then checks the manifest row through the store (the FILE_GET matrix
rows) and the file on disk — a row whose file is gone is the same 404
(ROW_WITHOUT_FILE), never a 500.

Spec-5.2 adds two portrait-for-Forge pieces: a mint route returning an
absolute HMAC-signed expiring URL (the DM pastes it into Forge's
per-unit portrait override), and a signature branch on the file GET so
the URL serves with no session (Forge cannot present the cookie). Every
signed-path failure is the campaign-missing 404 — no oracle. The secret
is env-only (AD-22); the origin comes from ``[server].base_url``.

The API never writes the manifest: media rows are written only through
the store's ``add_media`` (AD-1 — the media service owns generation, the
store owns the row). The image file is served from
``media_dir/{campaign_id}/{entity_id}/{filename}`` over the same-origin
session cookie (the cookie's path is ``/api``, so ``<img>`` GETs
authenticate like any other API read).
"""

import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.auth import COOKIE_NAME, get_current_account
from app.api.common import store_error_as_http
from app.core.config import DEFAULT_BASE_URL
from app.core.settings import configured_base_url, configured_media_dir, media_url_secret
from app.store import (
    MediaNotFoundError,
    StoreError,
    get_campaign,
    get_media_file,
    list_media,
    models,
)
from app.store.auth import get_session_account

router = APIRouter()

#: FR18-style: the mint route's only 500 (unset secret) lands on the
#: JSON-lines log (spec-1.7 logging_setup) before the generic handler.
_logger = logging.getLogger(__name__)


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


class PortraitUrlResponse(BaseModel):
    """A signed, expiring portrait URL for Forge's per-unit portrait
    override (spec-5.2) — absolute, fetchable with no session."""

    url: str
    expires_at: str


#: Signed portrait-URL lifetime, 7 days (spec Ask First — changing this
#: changes both the minted ``exp`` and the DM-facing expiry contract).
_PORTRAIT_URL_TTL_SECONDS = 7 * 24 * 3600


def _media_sig(secret: str, campaign_id: str, entity_id: str, filename: str, exp: int) -> str:
    """HMAC-SHA256 over ``{cid}.{eid}.{filename}.{exp}`` (spec-5.2) —
    compared with ``hmac.compare_digest``, never ``==``."""
    msg = f"{campaign_id}.{entity_id}.{filename}.{exp}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _optional_account(
    session_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> models.Account | None:
    """The session cookie resolved leniently: None when absent, invalid,
    expired, or revoked. The cookie path 401s on None; the signed-URL
    path never consults it (Forge cannot present a cookie)."""
    if session_token is None:
        return None
    return get_session_account(session_token)


def _newest_available_portrait(campaign_id: str, entity_id: str) -> models.Media | None:
    """The newest AVAILABLE image row for the entity (rowid order, last
    match wins) — the ``_hero_portrait_src`` rule (spec-5.1 precedent):
    kind image AND file on disk. A campaign-deleted-mid-request race
    reads as no portrait (the mint route already ownership-checked)."""
    try:
        rows = list_media(campaign_id)
    except StoreError:
        return None
    root = Path(configured_media_dir())
    newest = None
    for row in rows:
        if (
            row.entity_id == entity_id
            and row.kind == "image"
            and (root / campaign_id / entity_id / row.filename).is_file()
        ):
            newest = row
    return newest


@router.get(
    "/api/campaigns/{campaign_id}/entities/{entity_id}/portrait-url",
    response_model=PortraitUrlResponse,
)
def portrait_url(
    campaign_id: str,
    entity_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> PortraitUrlResponse:
    """Mint the Forge portrait URL: owner-only, absolute, expiring.
    Unknown/foreign campaign is the campaign 404; an unknown entity or
    one with no available portrait is the entity 404 (same envelope).
    An unset secret is the generic 500 after one error log line — fail
    closed, never mint unsigned. Read-only (AD-1/AD-11): no revision,
    no event, no store write."""
    if get_campaign(current.id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    secret = media_url_secret()
    if secret is None:
        _logger.error(
            "portrait_url_secret_missing campaign_id=%s entity_id=%s",
            campaign_id,
            entity_id,
        )
        raise RuntimeError("media URL secret is not configured")
    row = _newest_available_portrait(campaign_id, entity_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found.")
    exp = int(time.time()) + _PORTRAIT_URL_TTL_SECONDS
    sig = _media_sig(secret, campaign_id, entity_id, row.filename, exp)
    base = configured_base_url().rstrip("/")
    if base == DEFAULT_BASE_URL:
        # Operator misconfig flag: the minted URL points at the shipped
        # placeholder origin and Forge can never fetch it. Mints are
        # DM-click rare, so one warning line per mint is affordable.
        _logger.warning(
            "portrait_url_placeholder_origin campaign_id=%s entity_id=%s base_url=%s",
            campaign_id,
            entity_id,
            base,
        )
    url = (
        f"{base}/api/campaigns/{quote(campaign_id, safe='')}"
        f"/media/{quote(entity_id, safe='')}/{quote(row.filename, safe='')}"
        f"?exp={exp}&sig={sig}"
    )
    expires_at = datetime.fromtimestamp(exp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return PortraitUrlResponse(url=url, expires_at=expires_at)


def _signed_file(
    campaign_id: str, entity_id: str, filename: str, exp: str | None, sig: str | None
) -> FileResponse:
    """Serve a file on a valid portrait-URL signature — no session.
    Fail closed to the campaign-missing 404 on EVERYTHING: unset
    secret, half-present params, non-integer/expired/tampered values,
    unknown row, missing file, traversal-shaped names. ``exp`` arrives
    raw (a non-integer is a 404, never a 422 — the signed path has no
    typed contract to violate); the compare runs on ASCII bytes (a
    non-ASCII ``sig`` is a 404, never a TypeError 500). Row + disk
    checks mirror the cookie path; only the 404 shape differs."""
    try:
        exp_int = int(exp) if exp is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Campaign not found.") from None
    try:
        sig_bytes = sig.encode("ascii") if sig is not None else None
    except UnicodeEncodeError:
        raise HTTPException(status_code=404, detail="Campaign not found.") from None
    secret = media_url_secret()
    if (
        secret is None
        or exp_int is None
        or sig_bytes is None
        or exp_int < int(time.time())
        or not hmac.compare_digest(
            _media_sig(secret, campaign_id, entity_id, filename, exp_int).encode("ascii"),
            sig_bytes,
        )
    ):
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        raise HTTPException(status_code=404, detail="Campaign not found.")
    try:
        row = get_media_file(campaign_id, entity_id, filename)
    except StoreError:
        raise HTTPException(status_code=404, detail="Campaign not found.") from None
    path = Path(configured_media_dir()) / campaign_id / entity_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return FileResponse(
        path,
        media_type="video/mp4" if row.kind == "video" else "image/png",
        filename=filename,
        content_disposition_type="inline",
        # A signed portrait URL is a 7-day bearer credential — it must
        # never sit in a shared cache (the cookie path stays unmarked:
        # same-origin session responses are private by default).
        headers={"Cache-Control": "private"},
    )


@router.get("/api/campaigns/{campaign_id}/media/{entity_id}/{filename}")
def get_file(
    campaign_id: str,
    entity_id: str,
    filename: str,
    current: Annotated[models.Account | None, Depends(_optional_account)],
    exp: str | None = None,
    sig: str | None = None,
) -> FileResponse:
    """One portrait file — ownership + manifest row + disk checks, all 404.
    The row lookup is store-side (AD-9) and the disk check is the
    ROW_WITHOUT_FILE row: a manifest row whose file was removed is the
    same 404, never a server error. The filename is a manifest row's
    runner-minted ``<ulid>.png``; the row lookup is the traversal guard
    (a non-row name can never reach the filesystem), and separators are
    rejected outright.

    With ``exp``/``sig`` (spec-5.2) a valid signature bypasses the
    session entirely — the portrait URL is pasted into Forge, which
    fetches with no cookie. EVERY signed-path failure (absent secret,
    missing/expired/tampered params, unknown row, missing file) is the
    campaign-missing 404: the signature, the row, and the disk check
    are indistinguishable (no oracle). Without the params the cookie
    path is unchanged, including its 401.
    """
    if exp is not None or sig is not None:
        return _signed_file(campaign_id, entity_id, filename, exp, sig)
    if current is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        # Traversal-shaped names never reach the filesystem or the row
        # lookup — the store-family 404 through the shared mapper.
        store_error_as_http(MediaNotFoundError(f"illegal filename: {filename!r}"))
    if get_campaign(current.id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    try:
        row = get_media_file(campaign_id, entity_id, filename)
    except StoreError as exc:
        store_error_as_http(exc)
    path = Path(configured_media_dir()) / campaign_id / entity_id / filename
    if not path.is_file():
        # ROW_WITHOUT_FILE: a manifest row whose file is gone is the same
        # store-family 404 — through the shared mapper, never a 500.
        store_error_as_http(MediaNotFoundError(f"file {entity_id}/{filename} missing on disk"))
    # ``inline`` disposition (Starlette's FileResponse defaults to
    # ``attachment``): a portrait must render inside the entity card's
    # ``<img>`` and a reveal clip inside its ``<video>`` (spec-4.2), never
    # download (acceptance criterion 2). The media type follows the
    # manifest row's kind — a video row serves ``video/mp4``, everything
    # else the portrait ``image/png``.
    return FileResponse(
        path,
        media_type="video/mp4" if row.kind == "video" else "image/png",
        filename=filename,
        content_disposition_type="inline",
    )


__all__ = ["router"]
