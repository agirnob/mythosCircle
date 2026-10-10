"""Server-authorized, private user access management."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool
from starlette.concurrency import run_in_threadpool

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.api.ws import hub
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.store import admin as store_admin
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
