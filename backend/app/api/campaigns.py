"""Private campaign CRUD with the AR27 world seed (spec-1.6).

The ``campaign``/``campaigns`` routes require a valid session (1.5's
``get_current_account``) and are owner-scoped: a foreign or unknown
campaign id maps to the same 404 (NFR6, AD-9 — no oracle). Delete
requires an explicit confirmation body and is the AR20 total hard delete
(cascades revisions/events/entities/edges/jobs/media rows), with the
campaign's media directory reclaimed post-commit by the media service
(spec-4.3, AD-10). Store
rejections map through the shared ``store_error_as_http`` (epic-1 retro
item 3: campaigns errors are ``StoreError`` subclasses; the cursor-miss
family — ``InvalidCursorError`` from a deleted/fabricated cursor — rides
the same mapper, retro item 2). ``POST /{campaign_id}/undo`` is the
AD-2 compensating-commit surface: one undo revision, latest-or-named
target, no body on success.
"""

import json as _json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.core.settings import configured_media_dir
from app.media.service import reclaim_campaign_media, reclaim_entity_media
from app.store import StoreError, models
from app.store.campaigns import (
    create_campaign,
    delete_campaign,
    ensure_generic_campaign,
    get_campaign,
    list_campaigns,
    seed_themes,
    update_campaign,
)
from app.store.db import session_scope
from app.store.read import latest_revision, revision_events
from app.store.undo import undo as store_undo

logger = logging.getLogger(__name__)

router = APIRouter()


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    theme: str
    custom_lore: str = Field(default="", max_length=20_000)


class CampaignUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    theme: str | None = None
    custom_lore: str | None = Field(default=None, max_length=20_000)


class CampaignResponse(BaseModel):
    id: str
    owner_id: str
    title: str
    description: str
    theme: str
    custom_lore: str
    is_generic: bool
    created_at: str


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignResponse]
    next_cursor: str | None


class ThemesResponse(BaseModel):
    """The AR27 theme seed list (config ``campaigns.themes``) — the
    create/update forms choose from this; free text is a 422 (dogfood
    fix 2026-09-09: the store always validated, the form never showed)."""

    themes: list[str]


def _to_response(campaign: models.Campaign) -> CampaignResponse:
    return CampaignResponse(
        id=campaign.id,
        owner_id=campaign.owner_id,
        title=campaign.title,
        description=campaign.description,
        theme=campaign.theme,
        custom_lore=campaign.custom_lore,
        is_generic=campaign.is_generic,
        created_at=campaign.created_at,
    )


@router.post("/api/campaigns", status_code=201)
def create(
    payload: CampaignCreate,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> CampaignResponse:
    """Create a private world owned by the caller (AR27, AD-9)."""
    try:
        campaign = create_campaign(
            current.id,
            title=payload.title,
            description=payload.description,
            theme=payload.theme,
            custom_lore=payload.custom_lore,
        )
    except StoreError as exc:
        store_error_as_http(exc)
    return _to_response(campaign)


@router.get("/api/campaigns")
def list_own(
    current: Annotated[models.Account, Depends(get_current_account)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> CampaignListResponse:
    """The caller's campaigns, oldest first, cursor-paginated."""
    after = None
    if cursor is not None:
        try:
            after = decode_cursor(cursor)
        except InvalidCursorError as exc:
            store_error_as_http(exc)
    try:
        campaigns, next_cursor = list_campaigns(current.id, after, limit)
    except (StoreError, InvalidCursorError) as exc:
        store_error_as_http(exc)
    return CampaignListResponse(
        campaigns=[_to_response(c) for c in campaigns],
        next_cursor=encode_cursor(next_cursor) if next_cursor is not None else None,
    )


@router.get("/api/campaigns/themes", response_model=ThemesResponse)
def list_themes(
    current: Annotated[models.Account, Depends(get_current_account)],
) -> ThemesResponse:
    """The themes a campaign may carry — registered before the
    ``/{campaign_id}`` route so the literal path wins the match."""
    return ThemesResponse(themes=seed_themes())


@router.get("/api/campaigns/{campaign_id}")
def get_one(
    campaign_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> CampaignResponse:
    """One owned campaign — foreign/unknown is a single 404 (no oracle)."""
    campaign = get_campaign(current.id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return _to_response(campaign)


@router.patch("/api/campaigns/{campaign_id}")
def update(
    campaign_id: str,
    payload: CampaignUpdate,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> CampaignResponse:
    """Update seed fields of an owned campaign (PATCH semantics)."""
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail="No campaign fields to update.")
    try:
        campaign = update_campaign(current.id, campaign_id, **fields)
    except StoreError as exc:
        store_error_as_http(exc)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return _to_response(campaign)


@router.delete("/api/campaigns/{campaign_id}", status_code=204)
async def delete(
    campaign_id: str,
    request: Request,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> None:
    """AR20 total hard delete — requires an explicit confirmation body.

    Frozen contract (spec-1.6): ``DELETE`` with a JSON body
    ``{"confirm": true}``; missing/false confirm -> 400, nothing deleted.
    The body is echoed via ``request`` (FastAPI cannot bind a Pydantic
    body to DELETE directly) and malformed JSON is a 400. Ownership is
    checked FIRST: a foreign id is always 404, even without the confirm
    body (matrix row DELETE_FOREIGN).
    """
    if get_campaign(current.id, campaign_id) is None:
        # Foreign or unknown — indistinguishable 404 even without a confirm
        # body (matrix row DELETE_FOREIGN).
        raise HTTPException(status_code=404, detail="Campaign not found.")
    try:
        payload = await request.json()
    except (ValueError, _json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict) or payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="Deletion requires {'confirm': true}.")
    deleted = delete_campaign(current.id, campaign_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    # Rows committed (the store's delete_campaign removed the manifest
    # rows in its transaction) — reclaim the files post-commit (AD-10
    # rows-first ordering, spec-4.3). The helper is best-effort (logs
    # OSError, never raises) and the route guards anyway — a reclaim
    # failure NEVER turns the 204 into an error.
    try:
        reclaim_campaign_media(configured_media_dir(), campaign_id)
    except Exception:  # noqa: BLE001 - reclaim must never fail the 204
        logger.exception("post-delete media reclaim failed for %s", campaign_id)


async def _object_body(request: Request) -> dict[str, Any]:
    """The request body as a JSON object (the entities.py body pattern).

    An absent body is an empty object (the undo route's "undo the latest
    revision" request); malformed JSON or a non-dict body is a client
    400 — this ordering is why the body is hand-parsed instead of
    declared as a pydantic parameter (which would 422 before the
    ownership check and before the 400-vs-422 distinction).
    """
    raw = await request.body()
    if not raw:
        return {}
    try:
        payload = _json.loads(raw)
    except (ValueError, _json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    return payload


@router.post("/api/campaigns/{campaign_id}/undo", status_code=204)
async def undo(
    campaign_id: str,
    request: Request,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> None:
    """AD-2: undo ONE revision as a compensating commit — 204, no body.

    The frozen contract: an absent body undoes the latest revision, a JSON
    object body may name the target with ``{"revision_id": "<ulid>"}``,
    and anything else present is a 400 (malformed JSON or a non-object
    body, the entities.py hand-parse pattern). Ownership is checked FIRST:
    a foreign/unknown campaign is the single indistinguishable 404, even
    with a malformed body. A campaign with no revision at all is a 404
    ("No revision to undo.") — there is nothing to compensate; any other
    revision than the head is the store's ``StaleRevisionError`` -> 409
    through the shared mapper (rebase-or-reject, never a silent
    overwrite), exactly like the other commit routes.

    Undo appends its own revision (the log is never rewritten). Media
    rows are never RESTORED by undo (``store/undo.py``, spec-4.3); when
    the undo DELETES an entity — the inverse of an entity-CREATION
    revision — its manifest rows are reclaimed inside the store
    transaction and the FILES are reclaimed post-commit here, the
    delete-entity route's rows-first pattern (AD-10).
    """
    if get_campaign(current.id, campaign_id) is None:
        # Foreign or unknown — indistinguishable 404, before any body read
        # (the delete_entity ordering).
        raise HTTPException(status_code=404, detail="Campaign not found.")
    payload = await _object_body(request)
    revision_id = payload.get("revision_id")
    if revision_id is not None and not isinstance(revision_id, str):
        # Owner decision 2026-09-11: a malformed body is a client error,
        # never a store-level 409 (the entities.py base_revision guard).
        raise HTTPException(status_code=400, detail="revision_id must be a revision id string.")
    with session_scope() as session:
        latest = latest_revision(session, campaign_id)
    if latest is None:
        raise HTTPException(status_code=404, detail="No revision to undo.")
    try:
        revision = store_undo(campaign_id, revision_id if revision_id is not None else latest.id)
    except StoreError as exc:
        store_error_as_http(exc)
    # The undo revision's entity_deleted events name exactly the entities
    # the undo just deleted (the inverse of entity_created revisions) —
    # their manifest rows are already gone (same store transaction);
    # reclaim the FILES post-commit (AD-10 rows-first ordering, spec-4.3).
    # Best-effort, like the delete routes: a reclaim failure NEVER turns
    # the 204 into an error.
    with session_scope() as session:
        deleted_entities = [
            event.payload["id"]
            for event in revision_events(session, campaign_id, revision.id)
            if event.type == "entity_deleted"
        ]
    for deleted_entity_id in deleted_entities:
        try:
            reclaim_entity_media(configured_media_dir(), campaign_id, deleted_entity_id)
        except Exception:  # noqa: BLE001 - reclaim must never fail the 204
            logger.exception(
                "post-undo media reclaim failed for %s/%s", campaign_id, deleted_entity_id
            )


class GenericWorldCreate(BaseModel):
    theme: str


@router.post("/api/campaigns/generic", status_code=201)
def ensure_generic(
    payload: GenericWorldCreate,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> CampaignResponse:
    """Create-or-get the caller's Generic library world (owner spec,
    2026-09-17): the per-account storage context for characters generated
    without a canon world. Idempotent — the same world returns for every
    call regardless of theme (the theme rides each generation job)."""
    try:
        campaign = ensure_generic_campaign(current.id, payload.theme)
    except StoreError as exc:
        store_error_as_http(exc)
    return _to_response(campaign)


class MoveCharacterIn(BaseModel):
    source_campaign_id: str
    entity_id: str


@router.post("/api/campaigns/{campaign_id}/move", status_code=200)
def move_character(
    campaign_id: str,
    payload: MoveCharacterIn,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> dict[str, str]:
    """Move one character out of the Generic library into this canon
    world: a fresh-ULID copy commits here and the library row leaves
    (its library edges cascade away — they named library rows, not this
    world). Ownership on both campaigns (AD-9); source must be the
    Generic library, target must not be."""
    from app.store.commit import move_character_from_generic  # noqa: PLC0415

    if get_campaign(current.id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    try:
        entity_id, revision_id = move_character_from_generic(
            current.id,
            campaign_id,
            payload.source_campaign_id,
            payload.entity_id,
        )
    except StoreError as exc:
        store_error_as_http(exc)
    return {"entity_id": entity_id, "revision_id": revision_id}
