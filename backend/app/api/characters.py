"""The fully-authored character submission surface (spec: hybrid
authorship — the synchronous enqueue gate of the three-layer F3 gate).

``POST /api/characters`` is a THIN gate that enqueues one
``add_character`` job (the runner commits; jobs are not world graph):
202 Accepted + ``job_id`` when the canonical schema passes, 422 with the
exact schema-path violations BEFORE any job row exists when it does not
(DIRECT_INVALID_STAT / DIRECT_INCOMPLETE / DIRECT_UNKNOWN_KEY /
DIRECT_RELATION_MALFORMED — zero jobs spawned, zero LLM). The canonical
schema is the single source of truth (``store.direct``) — this route
adds NO second definition (the Request-body precedent, like edges: a
pydantic body model would be a mirror that can drift).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.core.errors import StoreHTTPException
from app.store import InvalidJobInputError, StoreError, enqueue_job, get_campaign, job_status

router = APIRouter()


def _require_campaign(current_id: str, campaign_id: str) -> None:
    """Ownership-404-first (the edges/entities precedent): a foreign or
    unknown campaign is the single 404 — the payload shape is never the
    first answer a stranger gets."""
    if get_campaign(current_id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")


async def _object_body(request: Request) -> dict[str, Any]:
    """The request body as a JSON object (the edges/entities precedent):
    malformed JSON or a non-object body is a 422 before any store call."""
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - malformed JSON is a user error
        raise HTTPException(status_code=422, detail="Body must be a JSON object.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object.")
    return payload


@router.post("/api/characters", status_code=202)
async def create_characters(
    request: Request,
    current: Annotated[Any, Depends(get_current_account)],
) -> dict[str, Any]:
    """Submit one or more fully-authored character sheets (202 + job_id).

    The body is ``{"campaign_id": <ulid>, "characters": [<sheet>, ...]}``
    validated against the canonical JSON Schema (``store.direct``) —
    completeness, stat-block structure incl. the dice pattern, well-formed
    declared relations, closed key sets, and the ``target_name`` ban
    (path 2 has no mandate access). A violation is a 422 whose
    ``details.violations`` carry the exact schema paths; zero job rows are
    written on any failure. The enqueued job's ``max_llm_calls`` is 0 —
    the zero-LLM property is structural.
    """
    payload = await _object_body(request)
    campaign_id = payload.get("campaign_id")
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise HTTPException(status_code=422, detail="campaign_id must be a non-blank string.")
    _require_campaign(current.id, campaign_id)
    try:
        job = enqueue_job(campaign_id, "add_character", {"characters": payload.get("characters")})
        job, _position = job_status(job.id)
    except StoreError as exc:
        _gate_error(exc)
    return {"job_id": job.id, "state": job.state, "max_llm_calls": job.max_llm_calls}


def _gate_error(exc: StoreError) -> None:
    """Re-raise a store rejection as the gate's envelope.

    A canonical-schema rejection (422 ``InvalidJobInputError``) splits its
    message back into ``details.violations`` so a client renders
    field-level errors; every other store rejection keeps its documented
    mapping (409 queue-full, 404 unknown campaign).
    """
    if isinstance(exc, InvalidJobInputError):
        message = str(exc)
        prefix = "add_character payload invalid: "
        violations = message[len(prefix) :].split("; ") if message.startswith(prefix) else [message]
        raise StoreHTTPException(422, message, {"violations": violations}) from exc
    store_error_as_http(exc)


__all__ = ["router"]
