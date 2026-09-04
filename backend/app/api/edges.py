"""Relation-edge REST surface (FR9; spec-3.4).

Add / edit-counter / delete a single typed, directed edge of the closed
vocabulary from a DM surface — the character screen's inline relation
editing. Every mutation lands through the store commit path: exactly one
revision, all-or-nothing, zero dangling edges after every commit (AR8,
AD-23).

Route shapes:
- POST   /api/campaigns/{campaign_id}/edges  -> create a new edge (201)
- PATCH  /api/campaigns/{campaign_id}/edges/{edge_id} -> counter update
- DELETE /api/campaigns/{campaign_id}/edges/{edge_id} -> delete (204)

Ownership-404-first, mirroring ``delete_entity``: the campaign check
runs BEFORE any body is read, so a foreign or unknown campaign is the
single indistinguishable 404 even with a malformed or absent body (no
oracle). Bodies are therefore hand-parsed from the Request (pydantic
body parameters would validate — and 422 — before the handler runs).
Malformed JSON / a non-object body is a 400; field-level problems
(missing fields, non-str endpoints, a non-int counter) are 422 — the
store remains the authority for vocabulary, duplicates, self-loops,
dangling endpoints, and stale bases.

Edge re-targeting is forbidden (AD-2): PATCH is counter-only; a DM who
wants a new target deletes and re-adds. ``base_revision`` is opt-in
optimistic concurrency (supplied must match the head else 409
``StaleRevisionError``; omitted targets the current head — the DM wire
default). Responses are built from data the route already holds — no
post-commit re-read that a concurrent delete could turn into a
misleading failure.
"""

import json as _json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.store import StoreError, get_campaign, models
from app.store.commit import commit_subgraph, delete_edge
from app.store.db import session_scope
from app.store.read import latest_revision, world_state

router = APIRouter()


class EdgeResponse(BaseModel):
    """The wire shape of one committed edge (mirrors EdgeExport)."""

    id: str
    src: str
    dst: str
    type: str
    counter: int


def _edge_response(edge_id: str, src: str, dst: str, edge_type: str, counter: int) -> EdgeResponse:
    return EdgeResponse(id=edge_id, src=src, dst=dst, type=edge_type, counter=counter)


def _require_campaign(current_id: str, campaign_id: str) -> None:
    """Ownership-404-first: foreign/unknown campaign is the single 404."""
    if get_campaign(current_id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")


async def _object_body(request: Request) -> dict[str, Any]:
    """The request body as a JSON object (entities.py body precedent).

    An absent body is an empty object (every field then fails its own
    422); malformed JSON or a non-dict body is a client 400 — this
    ordering is why the body is hand-parsed instead of declared as a
    pydantic parameter (which would 422 before the ownership check).
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


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=422, detail=f"'{key}' must be a non-empty string.")
    return value


def _required_counter(payload: dict[str, Any], key: str = "counter") -> int:
    """The strict wire counter: type-strict int, booleans rejected.

    The store's ``type(counter) is not int`` shape guard is authoritative;
    this pre-check keeps JSON coercions (\"3\", 3.0) from ever reaching it
    as a silently-corrected value (spec-2-2: never a silent coercion).
    """
    value = payload.get(key)
    if type(value) is not int:
        raise HTTPException(status_code=422, detail=f"'{key}' must be an integer.")
    return value


def _optional_base(payload: dict[str, Any]) -> str | None:
    value = payload.get("base_revision")
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=422, detail="'base_revision' must be a string.")
    return value


def _resolved_base(body_base: str | None, campaign_id: str) -> str | None:
    """The client-supplied base, or the current head when omitted.

    ``commit_subgraph`` treats ``base_revision=None`` as "empty world
    only" (the accept path passes the actual head); the DM wire default
    "omit base = stage against the current head" is resolved HERE, in
    the route, so the store's optimistic-concurrency check still guards
    the commit (AD-2): a commit landing between this read and the store
    transaction rejects with StaleRevisionError.
    """
    if body_base is not None:
        return body_base
    with session_scope() as session:
        revision = latest_revision(session, campaign_id)
        return revision.id if revision is not None else None


def _live_edge(campaign_id: str, edge_id: str) -> models.Edge:
    """The latest-revision edge, or the indistinguishable-404 (AD-9).

    A read through the store (AD-13); the PATCH needs the edge's
    immutable src/dst/type to stage the counter update (re-targeting is
    forbidden). An edge deleted between this read and the commit
    surfaces as the store's ``UnknownEdgeError`` 404 or a 409 stale
    base — never a silent resurrection (the store rejects explicit-id
    creation, spec-3.4).
    """
    with session_scope() as session:
        _, edges = world_state(session, campaign_id)
        for edge in edges:
            if edge.id == edge_id:
                return edge
    raise HTTPException(status_code=404, detail="Edge not found.")


@router.post("/api/campaigns/{campaign_id}/edges", status_code=201, response_model=EdgeResponse)
async def create_edge(
    campaign_id: str,
    request: Request,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> EdgeResponse:
    """Create a new typed, directed edge in one atomic commit (AR3).

    Thin route: type/vocabulary, self-loop, duplicate-relationship, and
    counter checks are the store's. The no-orphan rule is entity-create-
    only (spec-2.5), so a new edge between committed entities is always
    legal and never orphans anyone. The store assigns the edge's ULID
    (creation is id=None — explicit ids never create).
    """
    _require_campaign(current.id, campaign_id)
    payload = await _object_body(request)
    src = _required_str(payload, "src")
    dst = _required_str(payload, "dst")
    edge_type = _required_str(payload, "type")
    counter = _required_counter(payload) if "counter" in payload else 1
    base_revision = _optional_base(payload)
    try:
        commit_subgraph(
            campaign_id,
            edges=[models.EdgeInput(src=src, dst=dst, type=edge_type, counter=counter)],
            base_revision=_resolved_base(base_revision, campaign_id),
        )
    except StoreError as exc:
        store_error_as_http(exc)
    return _edge_response(*_created_edge(campaign_id, src, dst, edge_type, counter))


@router.patch("/api/campaigns/{campaign_id}/edges/{edge_id}", response_model=EdgeResponse)
async def update_edge(
    campaign_id: str,
    edge_id: str,
    request: Request,
    current: Annotated[models.Account, Depends(get_current_account)],
) -> EdgeResponse:
    """Update one edge's counter in one atomic commit (counter-only, AD-2).

    src/dst/type are immutable; re-targeting is forbidden, so the PATCH
    body carries only the new counter. Unknown/foreign edge id is the
    indistinguishable 404; a stale ``base_revision`` is a 409
    (StaleRevisionError).
    """
    _require_campaign(current.id, campaign_id)
    live = _live_edge(campaign_id, edge_id)
    payload = await _object_body(request)
    counter = _required_counter(payload)
    base_revision = _optional_base(payload)
    try:
        commit_subgraph(
            campaign_id,
            edges=[
                models.EdgeInput(
                    src=live.src, dst=live.dst, type=live.type, counter=counter, id=live.id
                )
            ],
            base_revision=_resolved_base(base_revision, campaign_id),
        )
    except StoreError as exc:
        store_error_as_http(exc)
    # Built from held data — no post-commit re-read that a concurrent
    # delete could turn into a misleading failure.
    return _edge_response(live.id, live.src, live.dst, live.type, counter)


@router.delete("/api/campaigns/{campaign_id}/edges/{edge_id}", status_code=204)
def delete_campaign_edge(
    campaign_id: str,
    edge_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
    base_revision: str | None = None,
) -> None:
    """Delete one edge in one atomic commit (FR9).

    No cascade/confirm gate — deleting a single edge is always
    dangling-safe (never orphans an entity; the store's no-orphan rule is
    entity-create-only). Unknown/foreign edge id is the indistinguishable
    404. ``base_revision`` is query-param opt-in optimistic concurrency
    (omitted targets the current head — ``delete_edge`` implements that
    None-means-head semantics internally, like ``delete_entity``).
    """
    _require_campaign(current.id, campaign_id)
    try:
        delete_edge(campaign_id, edge_id, base_revision=base_revision)
    except StoreError as exc:
        store_error_as_http(exc)


def _created_edge(
    campaign_id: str, src: str, dst: str, edge_type: str, counter: int
) -> tuple[str, str, str, str, int]:
    """Locate the freshly committed edge; the id is the store's answer.

    ``commit_subgraph`` returns only the revision, so the route matches
    the created edge by its (src, dst, type) relationship on the
    materialized state. Through the API the 500 sentinel is unreachable:
    the (src, dst, type) triple was free before this commit and the
    edge's id is unobservable to other clients before this response
    returns, so no concurrent delete/re-add can land in the window.
    """
    with session_scope() as session:
        _, edges = world_state(session, campaign_id)
        for edge in edges:
            if edge.src == src and edge.dst == dst and edge.type == edge_type:
                return edge.id, src, dst, edge_type, edge.counter
    raise HTTPException(status_code=500, detail="Edge committed but not found on re-read.")


__all__ = ["router"]
