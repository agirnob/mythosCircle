"""Private, versioned play sessions and story entries.

Bodies and query strings are validated after campaign and resource ownership,
so malformed requests cannot reveal foreign session or entry existence. All
writes and reference validation belong to the journal store.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.auth import get_current_account
from app.api.common import store_error_as_http
from app.api.entities import _object_body
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from app.store import StoreError, get_campaign, models
from app.store import journal as store
from app.store.db import session_scope

router = APIRouter(prefix="/api/campaigns/{campaign_id}")
Account = Annotated[models.Account, Depends(get_current_account)]


class JournalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class JournalReference(JournalInput):
    entity_id: str
    label: str
    field: Literal["headline", "context"] | None = None
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    token: str | None = None


class PlaySessionCreate(JournalInput):
    title: str = Field(min_length=1, max_length=300)
    play_date: str
    request_key: str | None = Field(default=None, min_length=1, max_length=128)


class PlaySessionUpdate(JournalInput):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    play_date: str | None = None


class JournalEntryCreate(JournalInput):
    session_id: str
    headline: str = Field(min_length=1, max_length=180)
    context: str = Field(default="", max_length=20_000)
    references: list[JournalReference] = Field(default_factory=list)
    request_key: str = Field(min_length=1, max_length=128)
    position: int | None = Field(default=None, ge=1)
    source_event_id: str | None = None


class JournalEntryUpdate(JournalInput):
    version: int = Field(ge=1)
    headline: str | None = Field(default=None, min_length=1, max_length=180)
    context: str | None = Field(default=None, max_length=20_000)
    references: list[JournalReference] | None = None
    position: int | None = Field(default=None, ge=1)


class JournalVersion(JournalInput):
    version: int = Field(ge=1)


class PlaySessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    campaign_id: str
    title: str
    play_date: str
    sequence: int
    version: int
    created_at: str
    updated_at: str


class PlaySessionsResponse(BaseModel):
    sessions: list[PlaySessionResponse]
    active_session_id: str | None
    next_cursor: str | None


class ActiveSessionResponse(BaseModel):
    active_session_id: str | None


class JournalEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    campaign_id: str
    session_id: str
    headline: str
    context: str
    references: list[JournalReference]
    position: int
    version: int
    source_event_id: str | None
    action_revision_id: str | None
    action_entity_id: str | None = None
    corrected: bool
    created_at: str
    updated_at: str


class JournalEntriesResponse(BaseModel):
    entries: list[JournalEntryResponse]
    next_cursor: str | None


def _body_schema(model: type[BaseModel]) -> dict[str, Any]:
    # Manual validation preserves privacy ordering while publishing typed bodies.
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value: Any) -> Any:
        if isinstance(value, dict):
            if "$ref" in value and value["$ref"].startswith("#/$defs/"):
                return inline(definitions[value["$ref"].rsplit("/", 1)[1]])
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value

    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": inline(schema)}},
        }
    }


def _list_schema(*, entries: bool = False) -> dict[str, Any]:
    parameters: list[dict[str, Any]] = [
        {"name": "cursor", "in": "query", "required": False, "schema": {"type": "string"}},
        {
            "name": "limit",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        },
    ]
    if entries:
        parameters.extend(
            {"name": field, "in": "query", "required": False, "schema": {"type": "string"}}
            for field in ("session_id", "entity_id")
        )
    return {"parameters": parameters}


_DELETE_SCHEMA = {
    "parameters": [
        {
            "name": "version",
            "in": "query",
            "required": True,
            "schema": {"type": "integer", "minimum": 1},
        }
    ]
}


def _owned(campaign_id: str, current: models.Account) -> models.Campaign:
    campaign = get_campaign(current.id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return campaign


def _session_owned(campaign_id: str, session_id: str) -> None:
    with session_scope() as db:
        row = db.get(models.PlaySession, session_id)
        if row is None or row.campaign_id != campaign_id or row.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Play session not found.")


def _entry_owned(campaign_id: str, entry_id: str) -> None:
    try:
        store.get_entry(campaign_id, entry_id)
    except StoreError as exc:
        store_error_as_http(exc)


def _validate[Model: BaseModel](model: type[Model], value: Any) -> Model:
    def valid_unicode(item: Any) -> None:
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise HTTPException(
                    status_code=400, detail="Request body must contain valid Unicode text."
                ) from None
        elif isinstance(item, dict):
            for key, nested in item.items():
                valid_unicode(key)
                valid_unicode(nested)
        elif isinstance(item, list):
            for nested in item:
                valid_unicode(nested)

    # JSON escapes can produce lone surrogates. Reject before Pydantic can echo
    # such input into an unserializable validation-error envelope.
    valid_unicode(value)
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


async def _body[Model: BaseModel](model: type[Model], request: Request) -> Model:
    return _validate(model, await _object_body(request))


def _paging(request: Request) -> tuple[str | None, int]:
    cursor = request.query_params.get("cursor")
    try:
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        raise HTTPException(status_code=422, detail="limit must be an integer.") from None
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100.")
    try:
        return decode_cursor(cursor) if cursor is not None else None, limit
    except InvalidCursorError as exc:
        store_error_as_http(exc)


def _version(request: Request) -> int:
    try:
        version = int(request.query_params.get("version", ""))
    except ValueError:
        raise HTTPException(status_code=422, detail="version must be a positive integer.") from None
    if version < 1:
        raise HTTPException(status_code=422, detail="version must be a positive integer.")
    return version


def _entry_response(row: models.JournalEntry) -> JournalEntryResponse:
    response = JournalEntryResponse.model_validate(row)
    return response


@router.get("/play-sessions", response_model=PlaySessionsResponse, openapi_extra=_list_schema())
def list_sessions(campaign_id: str, current: Account, request: Request) -> PlaySessionsResponse:
    campaign = _owned(campaign_id, current)
    cursor, limit = _paging(request)
    try:
        rows, next_id = store.list_sessions(campaign_id, cursor=cursor, limit=limit)
    except (StoreError, InvalidCursorError) as exc:
        store_error_as_http(exc)
    return PlaySessionsResponse(
        sessions=[PlaySessionResponse.model_validate(row) for row in rows],
        active_session_id=campaign.active_session_id,
        next_cursor=encode_cursor(next_id) if next_id is not None else None,
    )


@router.post(
    "/play-sessions",
    response_model=PlaySessionResponse,
    status_code=201,
    openapi_extra=_body_schema(PlaySessionCreate),
)
async def create_session(
    campaign_id: str, current: Account, request: Request
) -> PlaySessionResponse:
    _owned(campaign_id, current)
    payload = await _body(PlaySessionCreate, request)
    try:
        row = store.create_session(campaign_id, **payload.model_dump())
    except StoreError as exc:
        store_error_as_http(exc)
    return PlaySessionResponse.model_validate(row)


@router.patch(
    "/play-sessions/{session_id}",
    response_model=PlaySessionResponse,
    openapi_extra=_body_schema(PlaySessionUpdate),
)
async def update_session(
    campaign_id: str, session_id: str, current: Account, request: Request
) -> PlaySessionResponse:
    _owned(campaign_id, current)
    _session_owned(campaign_id, session_id)
    payload = await _body(PlaySessionUpdate, request)
    try:
        row = store.update_session(campaign_id, session_id, **payload.model_dump())
    except StoreError as exc:
        store_error_as_http(exc)
    return PlaySessionResponse.model_validate(row)


@router.delete("/play-sessions/{session_id}", status_code=204, openapi_extra=_DELETE_SCHEMA)
def delete_session(campaign_id: str, session_id: str, current: Account, request: Request) -> None:
    _owned(campaign_id, current)
    _session_owned(campaign_id, session_id)
    try:
        store.delete_session(campaign_id, session_id, version=_version(request))
    except StoreError as exc:
        store_error_as_http(exc)


@router.post("/play-sessions/{session_id}/activate", response_model=ActiveSessionResponse)
def activate_session(campaign_id: str, session_id: str, current: Account) -> ActiveSessionResponse:
    _owned(campaign_id, current)
    _session_owned(campaign_id, session_id)
    try:
        campaign = store.activate_session(campaign_id, session_id)
    except StoreError as exc:
        store_error_as_http(exc)
    return ActiveSessionResponse(active_session_id=campaign.active_session_id)


@router.get(
    "/journal-entries",
    response_model=JournalEntriesResponse,
    openapi_extra=_list_schema(entries=True),
)
def list_entries(campaign_id: str, current: Account, request: Request) -> JournalEntriesResponse:
    _owned(campaign_id, current)
    cursor, limit = _paging(request)
    session_id = request.query_params.get("session_id")
    entity_id = request.query_params.get("entity_id")
    if session_id is not None:
        _session_owned(campaign_id, session_id)
    try:
        rows, next_id = store.list_entries(
            campaign_id, session_id=session_id, entity_id=entity_id, cursor=cursor, limit=limit
        )
    except (StoreError, InvalidCursorError) as exc:
        store_error_as_http(exc)
    return JournalEntriesResponse(
        entries=[_entry_response(row) for row in rows],
        next_cursor=encode_cursor(next_id) if next_id else None,
    )


@router.get("/journal-entries/{entry_id}", response_model=JournalEntryResponse)
def get_entry(campaign_id: str, entry_id: str, current: Account) -> JournalEntryResponse:
    _owned(campaign_id, current)
    try:
        row = store.get_entry(campaign_id, entry_id)
    except StoreError as exc:
        store_error_as_http(exc)
    return _entry_response(row)


@router.post(
    "/journal-entries",
    response_model=JournalEntryResponse,
    status_code=201,
    openapi_extra=_body_schema(JournalEntryCreate),
)
async def create_entry(
    campaign_id: str, current: Account, request: Request
) -> JournalEntryResponse:
    _owned(campaign_id, current)
    payload = await _body(JournalEntryCreate, request)
    try:
        row = store.create_entry(campaign_id, **payload.model_dump())
    except StoreError as exc:
        store_error_as_http(exc)
    return _entry_response(row)


@router.patch(
    "/journal-entries/{entry_id}",
    response_model=JournalEntryResponse,
    openapi_extra=_body_schema(JournalEntryUpdate),
)
async def update_entry(
    campaign_id: str, entry_id: str, current: Account, request: Request
) -> JournalEntryResponse:
    _owned(campaign_id, current)
    _entry_owned(campaign_id, entry_id)
    payload = await _body(JournalEntryUpdate, request)
    try:
        row = store.update_entry(campaign_id, entry_id, **payload.model_dump())
    except StoreError as exc:
        store_error_as_http(exc)
    return _entry_response(row)


@router.delete("/journal-entries/{entry_id}", status_code=204, openapi_extra=_DELETE_SCHEMA)
def delete_entry(campaign_id: str, entry_id: str, current: Account, request: Request) -> None:
    _owned(campaign_id, current)
    _entry_owned(campaign_id, entry_id)
    try:
        store.delete_entry(campaign_id, entry_id, version=_version(request))
    except StoreError as exc:
        store_error_as_http(exc)


@router.post(
    "/journal-entries/{entry_id}/correct",
    response_model=JournalEntryResponse,
    openapi_extra=_body_schema(JournalVersion),
)
async def correct_entry(
    campaign_id: str, entry_id: str, current: Account, request: Request
) -> JournalEntryResponse:
    _owned(campaign_id, current)
    _entry_owned(campaign_id, entry_id)
    payload = await _body(JournalVersion, request)
    try:
        row = store.take_back_entry(campaign_id, entry_id, version=payload.version)
    except StoreError as exc:
        store_error_as_http(exc)
    return _entry_response(row)
