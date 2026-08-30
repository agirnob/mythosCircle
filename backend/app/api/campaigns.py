"""Private campaign CRUD with the AR27 world seed (spec-1.6).

Every route requires a valid session (1.5's ``get_current_account``) and
is owner-scoped: a foreign or unknown campaign id maps to the same 404
(NFR6, AD-9 — no oracle). Delete requires an explicit confirmation body
and is the AR20 total hard delete (cascades revisions/events/entities/
edges/jobs/media rows).
"""

import json as _json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.auth import get_current_account
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.store import models
from app.store.campaigns import (
    InvalidThemeError,
    create_campaign,
    delete_campaign,
    get_campaign,
    list_campaigns,
    update_campaign,
)

router = APIRouter()


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    theme: str
    custom_lore: str = Field(default="", max_length=20_000)


class CampaignDelete(BaseModel):
    confirm: bool = False


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
    created_at: str


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignResponse]
    next_cursor: str | None


def _to_response(campaign: models.Campaign) -> CampaignResponse:
    return CampaignResponse(
        id=campaign.id,
        owner_id=campaign.owner_id,
        title=campaign.title,
        description=campaign.description,
        theme=campaign.theme,
        custom_lore=campaign.custom_lore,
        created_at=campaign.created_at,
    )


def _theme_error(exc: InvalidThemeError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


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
    except (InvalidThemeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        campaigns, next_cursor = list_campaigns(current.id, after, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CampaignListResponse(
        campaigns=[_to_response(c) for c in campaigns],
        next_cursor=encode_cursor(next_cursor) if next_cursor is not None else None,
    )


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
    except (InvalidThemeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
