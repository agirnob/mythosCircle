"""Entity deletion REST surface (FR4, AD-5; spec-2.5).

One route mirroring the campaigns DELETE confirm-body pattern: the body
is read via ``request`` because a DELETE body is parsed leniently by
hand (a missing body is legal here — an entity with zero live edges
deletes without confirmation), but anything that IS present must be a
JSON object: malformed JSON is a 400 and a non-dict JSON body (list,
number, string) is a 400. Ownership is checked FIRST: a foreign
campaign id is always 404, even without a body.

Confirmation contract (AD-5 — required only "with live edges"):
- an entity with zero live edges deletes with no body at all;
- live edges without ``cascade`` ⇒ 409 whose envelope ``details`` list
  the affected neighbor entities (id + name);
- ``{"cascade": true}`` requires ``{"confirm": true}`` — the destructive
  option, like AR20's campaign delete, is gated on explicit confirmation;
- ``{"confirm": true, "cascade": true}`` removes the entity plus every
  edge touching it in one revision; neighbors survive;
- ``cascade`` and ``confirm`` must be JSON booleans when present —
  any other type is a 400, never silently ignored.
"""

import json as _json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.store import StoreError, get_campaign, models
from app.store.commit import delete_entity as store_delete_entity

router = APIRouter()


@router.delete(
    "/api/campaigns/{campaign_id}/entities/{entity_id}",
    status_code=204,
)
async def delete_entity(
    campaign_id: str,
    entity_id: str,
    request: Request,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> None:
    """FR4/AD-5: delete one entity through the store's commit path."""
    if get_campaign(current.id, campaign_id) is None:
        # Foreign or unknown — indistinguishable 404 even without a body
        # (matrix row DELETE_UNKNOWN mirrors campaigns DELETE_FOREIGN).
        raise HTTPException(status_code=404, detail="Campaign not found.")
    body = await request.body()
    if not body:
        # An absent body is legal: an entity with zero live edges
        # deletes without confirmation (DELETE_EDGELESS, AD-5).
        payload: Any = {}
    else:
        try:
            payload = _json.loads(body)
        except (ValueError, _json.JSONDecodeError):
            # A malformed body is a client error, not an absent one —
            # mirroring the campaigns DELETE pattern (owner decision
            # 2026-09-03).
            raise HTTPException(
                status_code=400, detail="Request body must be valid JSON."
            ) from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    cascade = payload.get("cascade")
    confirm = payload.get("confirm")
    if (
        cascade is not None
        and not isinstance(cascade, bool)
        or (confirm is not None and not isinstance(confirm, bool))
    ):
        raise HTTPException(
            status_code=400, detail="'cascade' and 'confirm' must be booleans when present."
        )
    cascade = bool(cascade)
    confirm = bool(confirm)
    if cascade and not confirm:
        # The destructive option requires the explicit confirmation, same
        # gate as AR20's campaign delete — a bare {"cascade": true} is a
        # 400, never a deletion.
        raise HTTPException(status_code=400, detail="Cascade deletion requires {'confirm': true}.")
    base_revision = payload.get("base_revision")
    if base_revision is not None and not isinstance(base_revision, str):
        raise HTTPException(status_code=400, detail="base_revision must be a revision id string.")
    try:
        store_delete_entity(campaign_id, entity_id, cascade=cascade, base_revision=base_revision)
    except StoreError as exc:
        store_error_as_http(exc)


__all__ = ["router"]
