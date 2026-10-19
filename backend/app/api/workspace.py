"""Private persistent Guided Build workspace."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.api.jobs import JobResponse, _require_campaign, _to_response
from app.store import StoreError, job_status, models
from app.store.workspace import (
    WorkspaceDraft,
    WorkspaceSave,
    WorkspaceSubmit,
    get_workspace,
    save_workspace,
    submit_workspace,
)

router = APIRouter(prefix="/api/campaigns/{campaign_id}/workspace")


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_version: int
    world_version: str | None
    draft: WorkspaceDraft
    receipts: list[dict[str, Any]]


@router.get("")
def get(
    campaign_id: str, current: Annotated[models.Account, Depends(get_current_account)]
) -> WorkspaceResponse:
    _require_campaign(current.id, campaign_id)
    try:
        return WorkspaceResponse.model_validate(get_workspace(campaign_id))
    except StoreError as exc:
        store_error_as_http(exc)


@router.put("")
def put(
    campaign_id: str,
    body: WorkspaceSave,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> WorkspaceResponse:
    _require_campaign(current.id, campaign_id)
    try:
        return WorkspaceResponse.model_validate(save_workspace(campaign_id, body))
    except StoreError as exc:
        store_error_as_http(exc)


def _submit(
    campaign_id: str, current: models.Account, body: WorkspaceSubmit, kind: str
) -> JobResponse:
    _require_campaign(current.id, campaign_id)
    try:
        job = submit_workspace(campaign_id, kind, body)
        job, position = job_status(job.id)
        return _to_response(job, position)
    except StoreError as exc:
        store_error_as_http(exc)


@router.post("/plan")
def plan(
    campaign_id: str,
    body: WorkspaceSubmit,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> JobResponse:
    return _submit(campaign_id, current, body, "plan_expansion")


@router.post("/generate")
def generate(
    campaign_id: str,
    body: WorkspaceSubmit,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> JobResponse:
    return _submit(campaign_id, current, body, "expand_world")
