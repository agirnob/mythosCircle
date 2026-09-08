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
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.core.settings import configured_media_dir
from app.media.service import reclaim_entity_media
from app.store import StoreError, get_campaign, models
from app.store.commit import delete_entity as store_delete_entity
from app.store.commit import update_entity as store_update_entity

logger = logging.getLogger(__name__)

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
    # Rows committed — now reclaim the files (AD-10 rows-first ordering,
    # spec-4.3): the store removed the manifest rows inside the delete
    # transaction; the files go after the commit. The helper is
    # best-effort (logs OSError, never raises) and the route guards
    # anyway — a reclaim failure NEVER turns the 204 into an error.
    try:
        reclaim_entity_media(configured_media_dir(), campaign_id, entity_id)
    except Exception:  # noqa: BLE001 - reclaim must never fail the 204
        logger.exception("post-delete media reclaim failed for %s/%s", campaign_id, entity_id)


@router.patch(
    "/api/campaigns/{campaign_id}/entities/{entity_id}",
    status_code=204,
)
async def update_entity(
    campaign_id: str,
    entity_id: str,
    request: Request,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> None:
    """FR10/spec-3.6: hand-edit one committed entity through the store's
    commit path — the DM is the final author.

    Partial fields (identity anchor, lore sections, ``stat_block``,
    ``world_integration``, ``boss``, ``text``, unknown keys) merge onto
    the current record and commit as exactly one ``entity_updated``
    revision; a value-identical PATCH commits none (204, idempotent).
    ``base_revision`` is opt-in optimistic concurrency (omitted targets
    the current head, resolved inside the store call; a moved head is a
    409 ``StaleRevisionError`` — rebase-or-reject). Shape validation is
    conditional (owner decision 2026-09-05): shape-valid records must
    stay shape-valid (422 naming the break, zero revisions); bare
    records merge unconstrained.

    Ownership-404-first, mirroring ``delete_entity``: the campaign check
    runs BEFORE any body is read, so a foreign/unknown campaign is the
    single indistinguishable 404 even with a malformed body. The body is
    therefore hand-parsed (pydantic body parameters would validate — and
    422 — before the handler ran); malformed/non-object/absent body is a
    400, ``base_revision`` non-string is a 400, and a body whose only
    key is ``base_revision`` is a 400 (at least one content key is
    required). 204 body-less, the DELETE precedent.
    """
    if get_campaign(current.id, campaign_id) is None:
        # Foreign or unknown — indistinguishable 404 even without a body.
        raise HTTPException(status_code=404, detail="Campaign not found.")
    payload = await _object_body(request)
    if not payload:
        raise HTTPException(
            status_code=400,
            detail="Request body must be a JSON object with at least one content key.",
        )
    if not any(key != "base_revision" for key in payload):
        raise HTTPException(
            status_code=400,
            detail=(
                "A PATCH needs at least one content key (a body of only base_revision is a 400)."
            ),
        )
    base_revision = payload.get("base_revision")
    if base_revision is not None and not isinstance(base_revision, str):
        raise HTTPException(status_code=400, detail="base_revision must be a revision id string.")
    try:
        store_update_entity(campaign_id, entity_id, patch=payload, base_revision=base_revision)
    except StoreError as exc:
        store_error_as_http(exc)


async def _object_body(request: Request) -> dict[str, Any]:
    """The request body as a JSON object (edges.py body precedent).

    An absent body is an empty object (which the PATCH then rejects as
    keyless — a PATCH must carry content); malformed JSON or a non-dict
    body is a client 400 — this ordering is why the body is hand-parsed
    instead of declared as a pydantic parameter (which would 422 before
    the ownership check and before the 400-vs-422 distinction).
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


__all__ = ["router"]
