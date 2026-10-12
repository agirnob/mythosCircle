"""Server-authorized, private user access management."""

from dataclasses import asdict, replace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool
from starlette.concurrency import run_in_threadpool

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.api.ws import hub
from app.core.llm_routing import (
    LLMRoutingConfig,
    LLMSettingsView,
    LLMTestRequest,
    LLMTestResult,
    resolve_profile,
)
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.providers.llm import ProviderError, chat_completion
from app.store import admin as store_admin
from app.store import llm_settings as store_llm_settings
from app.store import models

router = APIRouter()


class AdminUser(BaseModel):
    id: str
    email: str
    is_admin: bool
    disabled_at: str | None
    created_at: str
    campaign_count: int


class AdminUserList(BaseModel):
    users: list[AdminUser]
    next_cursor: str | None


class AccountStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disabled: StrictBool


def get_admin_account(
    request: Request, current: Annotated[models.Account, Depends(get_current_account)]
) -> models.Account:
    if current.id not in request.app.state.admin_account_ids:
        raise HTTPException(
            status_code=403,
            detail="Administrator access required.",
            headers={"Cache-Control": "no-store"},
        )
    return current


def _decode_cursor(cursor: str) -> str:
    anchor = decode_cursor(cursor)
    if encode_cursor(anchor) != cursor:
        raise InvalidCursorError("Malformed cursor.")
    return anchor


@router.get("/api/admin/users")
def list_users(
    request: Request,
    response: Response,
    current: Annotated[models.Account, Depends(get_admin_account)],
    q: Annotated[str, Query(max_length=320)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> AdminUserList:
    response.headers["Cache-Control"] = "no-store"
    try:
        users, next_cursor = store_admin.list_users(
            admin_ids=request.app.state.admin_account_ids,
            q=q,
            limit=limit,
            cursor=_decode_cursor(cursor) if cursor is not None else None,
        )
    except Exception as exc:
        store_error_as_http(exc)
    return AdminUserList(
        users=[AdminUser(**asdict(user)) for user in users],
        next_cursor=encode_cursor(next_cursor) if next_cursor else None,
    )


@router.patch("/api/admin/users/{account_id}")
async def set_status(
    account_id: str,
    payload: AccountStatusRequest,
    request: Request,
    response: Response,
    current: Annotated[models.Account, Depends(get_admin_account)],
) -> AdminUser:
    response.headers["Cache-Control"] = "no-store"
    async with hub.account_lock(account_id):
        try:
            user = await run_in_threadpool(
                store_admin.set_disabled,
                account_id,
                payload.disabled,
                admin_ids=request.app.state.admin_account_ids,
            )
        except Exception as exc:
            store_error_as_http(exc)
        if payload.disabled:
            await hub.invalidate_account(account_id)
        return AdminUser(**asdict(user))


@router.get("/api/admin/llm-settings")
def get_llm_settings(
    response: Response,
    current: Annotated[models.Account, Depends(get_admin_account)],
) -> LLMSettingsView:
    response.headers["Cache-Control"] = "no-store"
    return store_llm_settings.view()


@router.put("/api/admin/llm-settings")
def put_llm_settings(
    payload: LLMRoutingConfig,
    response: Response,
    current: Annotated[models.Account, Depends(get_admin_account)],
) -> LLMSettingsView:
    response.headers["Cache-Control"] = "no-store"
    try:
        return store_llm_settings.save(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/admin/llm-settings/test")
def test_llm_settings(
    payload: LLMTestRequest,
    response: Response,
    current: Annotated[models.Account, Depends(get_admin_account)],
) -> LLMTestResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        settings = resolve_profile(payload.provider, payload.profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        chat_completion(
            "Reply with OK.",
            settings=replace(
                settings,
                # Free-tier Gemini can take over a minute even for a short
                # probe. Preserve smaller operator limits and bound the wait.
                timeout=min(settings.timeout, 120 if payload.provider == "gemini" else 15),
                max_tokens=2048,
                reasoning_effort="low" if payload.provider == "gemini" else None,
            ),
        )
    except ProviderError as exc:
        if exc.kind in {"truncated", "empty"}:
            return LLMTestResult(
                success=False,
                message=(
                    "Provider reached and authenticated, but the test completion was "
                    "empty or exceeded its output limit. Check model reasoning/output "
                    "settings before using it for generation."
                ),
            )
        return LLMTestResult(success=False, message=f"Connection test failed: {exc}.")
    return LLMTestResult(success=True, message="Connection test succeeded.")
