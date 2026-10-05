"""Named play sessions and explicit story prose; all writes are atomic store commits."""

import hashlib
import json
from datetime import date
from typing import Any

from sqlalchemy import exists, func, literal_column, or_, select, tuple_
from sqlalchemy.orm import Session

from app.core import ids, time
from app.core.pagination import InvalidCursorError, paging
from app.store import models
from app.store.commit import StoreError, UnknownCampaignError, _add_event
from app.store.db import session_scope
from app.store.read import latest_revision


class JournalNotFoundError(StoreError):
    """Unknown or foreign session, entry, or source event."""


class JournalInputError(StoreError):
    """Malformed journal input."""


class JournalUnicodeError(JournalInputError):
    """Text contains an unpaired Unicode surrogate (malformed request -> 400)."""


class JournalConflictError(StoreError):
    """Stale version, changed retry, or conflicting later action."""

    def __init__(self, message: str, version: int | None = None):
        self.version = version
        super().__init__(message)


def _campaign(session: Session, campaign_id: str) -> models.Campaign:
    row = session.get(models.Campaign, campaign_id)
    if row is None:
        raise UnknownCampaignError(campaign_id)
    return row


def _session(session: Session, campaign_id: str, session_id: str) -> models.PlaySession:
    row = session.get(models.PlaySession, session_id)
    if row is None or row.campaign_id != campaign_id or row.deleted_at:
        raise JournalNotFoundError("play session not found")
    return row


def _entry(session: Session, campaign_id: str, entry_id: str) -> models.JournalEntry:
    row = session.get(models.JournalEntry, entry_id)
    if row is None or row.campaign_id != campaign_id or row.deleted_at:
        raise JournalNotFoundError("journal entry not found")
    _session(session, campaign_id, row.session_id)
    return row


def _version(row: Any, version: int) -> None:
    if type(version) is not int or row.version != version:
        raise JournalConflictError("version has changed", row.version)


def _text(value: Any, field: str, maximum: int, *, blank: bool = False) -> str:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise JournalUnicodeError(f"{field} contains invalid Unicode") from None
    if not isinstance(value, str) or len(value) > maximum or (not blank and not value.strip()):
        qualifier = "a" if blank else "a non-blank"
        raise JournalInputError(
            f"{field} must be {qualifier} string of at most {maximum} characters"
        )
    return value if blank or field == "headline" else value.strip()


def _date(value: str) -> str:
    _text(value, "play_date", 10)
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (TypeError, ValueError):
        raise JournalInputError("play_date must be YYYY-MM-DD") from None
    return value


def snapshot(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _revision(session: Session, campaign_id: str) -> models.Revision:
    head = latest_revision(session, campaign_id)
    row = models.Revision(
        id=ids.new_id(),
        campaign_id=campaign_id,
        base_revision=head.id if head else None,
        created_at=time.now(),
    )
    session.add(row)
    return row


def _event(
    session: Session, revision: models.Revision, kind: str, row: Any, before: dict[str, Any] | None
) -> None:
    _add_event(
        session,
        revision.campaign_id,
        revision.id,
        kind,
        {"id": row.id, "before": before, "after": snapshot(row)},
        revision.created_at,
    )


def _fingerprint(payload: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise JournalInputError("request must be strict JSON") from None
    try:
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        raise JournalUnicodeError("request contains invalid Unicode") from None


def _retry(
    session: Session, campaign_id: str, request_key: str, payload: dict[str, Any], model: Any
) -> Any:
    _text(request_key, "request_key", 128)
    fingerprint = _fingerprint(payload)
    receipt = session.scalar(
        select(models.JournalRequest).where(
            models.JournalRequest.campaign_id == campaign_id,
            models.JournalRequest.request_key == request_key,
        )
    )
    if receipt is None:
        return None
    if receipt.fingerprint != fingerprint:
        raise JournalConflictError("request_key was already used for a different request")
    if receipt.response.get("error") == "unchanged_action":
        raise JournalConflictError(receipt.response["message"])
    return model(**receipt.response)


def _reject_request(
    session: Session, campaign_id: str, request_key: str, payload: dict[str, Any], message: str
) -> JournalConflictError:
    """Remember a rejected no-change action without writing world/story state."""
    session.add(
        models.JournalRequest(
            id=ids.new_id(),
            campaign_id=campaign_id,
            request_key=request_key,
            fingerprint=_fingerprint(payload),
            response={"error": "unchanged_action", "message": message},
        )
    )
    return JournalConflictError(message)


def _receipt(session: Session, row: Any, request_key: str, payload: dict[str, Any]) -> None:
    session.add(
        models.JournalRequest(
            id=ids.new_id(),
            campaign_id=row.campaign_id,
            request_key=request_key,
            fingerprint=_fingerprint(payload),
            response=snapshot(row),
        )
    )


def create_session(
    campaign_id: str, *, title: str, play_date: str, request_key: str | None = None
) -> models.PlaySession:
    payload = {"kind": "session", "title": title, "play_date": play_date}
    with session_scope() as session:
        _campaign(session, campaign_id)
        if request_key is not None:
            retry = _retry(session, campaign_id, request_key, payload, models.PlaySession)
            if retry is not None:
                return retry
        title, play_date = _text(title, "title", 300), _date(play_date)
        sequence = (
            session.scalar(
                select(func.max(models.PlaySession.sequence)).where(
                    models.PlaySession.campaign_id == campaign_id
                )
            )
            or 0
        )
        revision = _revision(session, campaign_id)
        row = models.PlaySession(
            id=ids.new_id(),
            campaign_id=campaign_id,
            title=title,
            play_date=play_date,
            sequence=sequence + 1,
            version=1,
            created_at=revision.created_at,
            updated_at=revision.created_at,
            deleted_at=None,
        )
        session.add(row)
        _event(session, revision, "play_session_created", row, None)
        if request_key is not None:
            _receipt(session, row, request_key, payload)
        return row


def list_sessions(campaign_id: str, cursor: str | None = None, limit: int = 50):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise JournalInputError("limit must be between 1 and 200")
    with session_scope() as session:
        _campaign(session, campaign_id)
        query = select(models.PlaySession).where(
            models.PlaySession.campaign_id == campaign_id, models.PlaySession.deleted_at.is_(None)
        )
        if cursor:
            try:
                anchor = _session(session, campaign_id, cursor)
            except JournalNotFoundError:
                raise InvalidCursorError("cursor names no session in this campaign") from None
            query = query.where(
                tuple_(models.PlaySession.play_date, models.PlaySession.sequence)
                > (anchor.play_date, anchor.sequence)
            )
        return paging(
            session.scalars(
                query.order_by(models.PlaySession.play_date, models.PlaySession.sequence).limit(
                    limit + 1
                )
            ).all(),
            limit,
        )


def update_session(
    campaign_id: str,
    session_id: str,
    *,
    version: int,
    title: str | None = None,
    play_date: str | None = None,
):
    with session_scope() as session:
        _campaign(session, campaign_id)
        row = _session(session, campaign_id, session_id)
        _version(row, version)
        before = snapshot(row)
        if title is not None:
            row.title = _text(title, "title", 300)
        if play_date is not None:
            row.play_date = _date(play_date)
        if snapshot(row) == before:
            return row
        revision = _revision(session, campaign_id)
        row.version += 1
        row.updated_at = revision.created_at
        _event(session, revision, "play_session_updated", row, before)
        return row


def activate_session(campaign_id: str, session_id: str | None):
    with session_scope() as session:
        campaign = _campaign(session, campaign_id)
        if session_id is not None:
            _session(session, campaign_id, session_id)
        if campaign.active_session_id != session_id:
            before = {"active_session_id": campaign.active_session_id}
            revision = _revision(session, campaign_id)
            campaign.active_session_id = session_id
            _add_event(
                session,
                campaign_id,
                revision.id,
                "campaign_active_session_updated",
                {"id": campaign_id, "before": before, "after": {"active_session_id": session_id}},
                revision.created_at,
            )
        return campaign


def delete_session(campaign_id: str, session_id: str, *, version: int):
    with session_scope() as session:
        campaign = _campaign(session, campaign_id)
        row = _session(session, campaign_id, session_id)
        _version(row, version)
        if session.scalar(
            select(models.JournalEntry.id)
            .where(
                models.JournalEntry.session_id == session_id,
                models.JournalEntry.deleted_at.is_(None),
            )
            .limit(1)
        ):
            raise JournalConflictError("remove session entries before removing their session")
        before = snapshot(row)
        revision = _revision(session, campaign_id)
        row.deleted_at = row.updated_at = revision.created_at
        row.version += 1
        _event(session, revision, "play_session_updated", row, before)
        if campaign.active_session_id == session_id:
            campaign.active_session_id = None
            _add_event(
                session,
                campaign_id,
                revision.id,
                "campaign_active_session_updated",
                {
                    "id": campaign_id,
                    "before": {"active_session_id": session_id},
                    "after": {"active_session_id": None},
                },
                revision.created_at,
            )


def _references(
    session: Session,
    campaign_id: str,
    references: Any,
    headline: str,
    context: str,
    existing: list[dict[str, Any]] = (),
):
    if not isinstance(references, (list, tuple)) or len(references) > 100:
        raise JournalInputError("references must be a list of at most 100 items")
    result = []
    for ref in references:
        if (
            not isinstance(ref, dict)
            or not isinstance(ref.get("entity_id"), str)
            or not ids.is_valid_ulid(ref["entity_id"])
        ):
            raise JournalInputError("reference requires an entity ULID")
        _fingerprint(ref)
        entity = session.get(models.Entity, ref["entity_id"])
        historical = next(
            (
                old
                for old in existing
                if all(
                    old.get(key) == ref.get(key) for key in ("entity_id", "label", "token", "field")
                )
            ),
            None,
        )
        if (entity is None or entity.campaign_id != campaign_id) and historical is None:
            raise JournalInputError("reference names no entity in this campaign")
        label = historical["label"] if historical else entity.name
        field = ref.get("field")
        start, end = ref.get("start"), ref.get("end")
        token = ref.get("token", "@" + str(ref.get("label", label)))
        if field is not None:
            if field not in ("headline", "context"):
                raise JournalInputError("reference field must be headline or context")
            body = headline if field == "headline" else context
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(body)
                or body[start:end] != token
            ):
                raise JournalInputError("reference token must match its text occurrence")
            if any(
                old.get("field") == field and start < old["end"] and old["start"] < end
                for old in result
                if old.get("field") is not None
            ):
                raise JournalInputError("reference occurrences may not overlap or duplicate")
        elif start is not None or end is not None:
            raise JournalInputError("reference offsets require a field")
        normalized = {**ref, "label": label}
        result.append(normalized)
    return result


def _position(
    session: Session,
    campaign_id: str,
    session_id: str,
    position: int | None,
    *,
    excluding: str | None = None,
) -> tuple[int, list[models.JournalEntry]]:
    scope = [
        models.JournalEntry.campaign_id == campaign_id,
        models.JournalEntry.session_id == session_id,
        models.JournalEntry.deleted_at.is_(None),
    ]
    if excluding is not None:
        scope.append(models.JournalEntry.id != excluding)
    if position is None:
        last = session.scalar(select(func.max(models.JournalEntry.position)).where(*scope)) or 0
        position = last + 1024
    if type(position) is not int or not 1 <= position <= 2**53 - 1:
        raise JournalInputError("position must be a positive safe integer")
    shifts = []
    if (
        session.scalar(
            select(models.JournalEntry.id)
            .where(*scope, models.JournalEntry.position == position)
            .limit(1)
        )
        is not None
    ):
        shifts = list(
            session.scalars(
                select(models.JournalEntry)
                .where(*scope, models.JournalEntry.position >= position)
                .order_by(models.JournalEntry.position, models.JournalEntry.id)
            )
        )
        if shifts[-1].position > 2**53 - 1 - 1024:
            raise JournalInputError("story positions exceed the safe integer range")
    return position, shifts


def _shift_positions(
    session: Session, revision: models.Revision, rows: list[models.JournalEntry]
) -> None:
    """Open an occupied story slot as part of the requesting gesture's revision."""
    for row in rows:
        before = snapshot(row)
        row.position += 1024
        row.version += 1
        row.updated_at = revision.created_at
        _event(session, revision, "journal_entry_updated", row, before)


def _create_entry(
    session: Session,
    campaign_id: str,
    *,
    session_id: str,
    headline: str,
    context: str = "",
    references=(),
    request_key: str,
    position: int | None = None,
    source_event_id: str | None = None,
    revision: models.Revision | None = None,
    action_entity_id: str | None = None,
    request_payload: dict[str, Any] | None = None,
):
    payload = request_payload or {
        "kind": "entry",
        "session_id": session_id,
        "headline": headline,
        "context": context,
        "references": list(references),
        "position": position,
        "source_event_id": source_event_id,
    }
    retry = _retry(session, campaign_id, request_key, payload, models.JournalEntry)
    if retry is not None:
        return retry
    _session(session, campaign_id, session_id)
    headline, context = (
        _text(headline, "headline", 180),
        _text(context, "context", 20_000, blank=True),
    )
    refs = _references(session, campaign_id, references, headline, context)
    if source_event_id is not None and action_entity_id is None:
        source = session.get(models.Event, source_event_id)
        if (
            source is None
            or source.campaign_id != campaign_id
            or not source.type.startswith(("session_state_", "knowledge_state_"))
        ):
            raise JournalNotFoundError("historical action not found")
        previous = session.scalar(
            select(models.JournalEntry).where(
                models.JournalEntry.campaign_id == campaign_id,
                models.JournalEntry.source_event_id == source_event_id,
            )
        )
        if previous is not None:
            raise JournalConflictError("historical action is already in the journal")
        # Promotion is prose only: no action_revision_id, so correction cannot replay it.
        action_entity_id = source.payload.get("id")
        if not isinstance(action_entity_id, str) or not ids.is_valid_ulid(action_entity_id):
            raise JournalNotFoundError("historical action target is unavailable")
        if not any(ref["entity_id"] == action_entity_id for ref in refs):
            live = session.get(models.Entity, action_entity_id)
            if live is not None and live.campaign_id != campaign_id:
                raise JournalInputError("historical action target belongs to another campaign")
            if live is not None:
                label = live.name
            else:
                anchor = session.scalar(
                    select(literal_column("rowid"))
                    .select_from(models.Event)
                    .where(models.Event.id == source.id)
                )
                historical = session.scalar(
                    select(models.Event)
                    .where(
                        models.Event.campaign_id == campaign_id,
                        models.Event.type.in_(["entity_created", "entity_updated"]),
                        func.json_extract(models.Event.payload, "$.id") == action_entity_id,
                        literal_column("rowid") <= anchor,
                    )
                    .order_by(literal_column("rowid").desc())
                    .limit(1)
                )
                label = (
                    (historical.payload.get("after") or {}).get("name")
                    if historical is not None
                    else None
                ) or "Deleted entity"
            related = {"entity_id": action_entity_id, "label": label}
            _fingerprint(related)
            refs.append(related)
    story_position, shifts = _position(session, campaign_id, session_id, position)
    paired = revision is not None
    revision = revision or _revision(session, campaign_id)
    _shift_positions(session, revision, shifts)
    row = models.JournalEntry(
        id=ids.new_id(),
        campaign_id=campaign_id,
        session_id=session_id,
        headline=headline,
        context=context,
        references=refs,
        position=story_position,
        version=1,
        source_event_id=source_event_id,
        action_revision_id=revision.id if paired else None,
        action_entity_id=action_entity_id,
        corrected=False,
        created_at=revision.created_at,
        updated_at=revision.created_at,
        deleted_at=None,
    )
    session.add(row)
    _event(session, revision, "journal_entry_created", row, None)
    _receipt(session, row, request_key, payload)
    return row


def create_entry(
    campaign_id: str,
    *,
    session_id: str,
    headline: str,
    context: str = "",
    references=(),
    request_key: str,
    position: int | None = None,
    source_event_id: str | None = None,
):
    with session_scope() as session:
        _campaign(session, campaign_id)
        return _create_entry(
            session,
            campaign_id,
            session_id=session_id,
            headline=headline,
            context=context,
            references=references,
            request_key=request_key,
            position=position,
            source_event_id=source_event_id,
        )


def get_entry(campaign_id: str, entry_id: str):
    with session_scope() as session:
        _campaign(session, campaign_id)
        return _entry(session, campaign_id, entry_id)


def list_entries(
    campaign_id: str,
    *,
    session_id: str | None = None,
    entity_id: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise JournalInputError("limit must be between 1 and 200")
    with session_scope() as session:
        _campaign(session, campaign_id)
        if session_id is not None:
            _session(session, campaign_id, session_id)
        query = (
            select(models.JournalEntry)
            .join(models.PlaySession)
            .where(
                models.JournalEntry.campaign_id == campaign_id,
                models.JournalEntry.deleted_at.is_(None),
                models.PlaySession.deleted_at.is_(None),
            )
        )
        if session_id:
            query = query.where(models.JournalEntry.session_id == session_id)
        if entity_id:
            refs = (
                func.json_each(models.JournalEntry.references).table_valued("value").alias("refs")
            )
            query = query.where(
                or_(
                    models.JournalEntry.action_entity_id == entity_id,
                    exists(
                        select(1)
                        .select_from(refs)
                        .where(func.json_extract(refs.c.value, "$.entity_id") == entity_id)
                    ),
                )
            )
        chronology = (
            models.PlaySession.play_date,
            models.PlaySession.sequence,
            models.JournalEntry.position,
            models.JournalEntry.id,
        )
        if cursor:
            anchor = session.scalar(query.where(models.JournalEntry.id == cursor))
            if anchor is None:
                raise InvalidCursorError("cursor names no entry in this journal scope")
            play = _session(session, campaign_id, anchor.session_id)
            query = query.where(
                tuple_(*chronology) > (play.play_date, play.sequence, anchor.position, anchor.id)
            )
        return paging(session.scalars(query.order_by(*chronology).limit(limit + 1)).all(), limit)


def update_entry(
    campaign_id: str,
    entry_id: str,
    *,
    version: int,
    headline: str | None = None,
    context: str | None = None,
    references=None,
    position: int | None = None,
):
    with session_scope() as session:
        _campaign(session, campaign_id)
        row = _entry(session, campaign_id, entry_id)
        _version(row, version)
        before = snapshot(row)
        new_headline = row.headline if headline is None else _text(headline, "headline", 180)
        new_context = (
            row.context if context is None else _text(context, "context", 20_000, blank=True)
        )
        refs = _references(
            session,
            campaign_id,
            row.references if references is None else references,
            new_headline,
            new_context,
            row.references,
        )
        row.headline, row.context, row.references = new_headline, new_context, refs
        shifts = []
        if position is not None:
            row.position, shifts = _position(
                session, campaign_id, row.session_id, position, excluding=row.id
            )
        if before == snapshot(row) and not shifts:
            return row
        revision = _revision(session, campaign_id)
        _shift_positions(session, revision, shifts)
        row.version += 1
        row.updated_at = revision.created_at
        _event(session, revision, "journal_entry_updated", row, before)
        return row


def delete_entry(campaign_id: str, entry_id: str, *, version: int):
    with session_scope() as session:
        _campaign(session, campaign_id)
        row = _entry(session, campaign_id, entry_id)
        _version(row, version)
        before = snapshot(row)
        revision = _revision(session, campaign_id)
        row.deleted_at = row.updated_at = revision.created_at
        row.version += 1
        _event(session, revision, "journal_entry_updated", row, before)


def take_back_entry(campaign_id: str, entry_id: str, *, version: int):
    """Explicitly invert only this action's changed keys, retaining later prose/state."""
    from app.store.undo import _apply_inverse, _preflight

    with session_scope() as session:
        _campaign(session, campaign_id)
        row = _entry(session, campaign_id, entry_id)
        _version(row, version)
        if row.corrected or not row.action_revision_id:
            raise JournalConflictError("entry has no active paired action to take back")
        events = session.scalars(
            select(models.Event).where(
                models.Event.campaign_id == campaign_id,
                models.Event.revision_id == row.action_revision_id,
                models.Event.type.in_(["session_state_created", "session_state_updated"]),
            )
        ).all()
        if not events:
            raise JournalConflictError("paired action is unavailable")
        for event in events:
            state = session.scalar(
                select(models.EntitySessionState).where(
                    models.EntitySessionState.campaign_id == campaign_id,
                    models.EntitySessionState.entity_id == event.payload["id"],
                )
            )
            before_data = (event.payload.get("before") or {}).get("data", {})
            after_data = event.payload["after"]["data"]
            if state is None:
                raise JournalConflictError("action state has changed")
            changed_keys = set()
            for key in set(before_data) | set(after_data):
                original = _fingerprint({key: before_data[key]} if key in before_data else {})
                applied = _fingerprint({key: after_data[key]} if key in after_data else {})
                current = _fingerprint({key: state.data[key]} if key in state.data else {})
                if original != applied:
                    changed_keys.add(key)
                if original != applied and current != applied:
                    raise JournalConflictError(f"later state changed {key}")
            anchor = session.scalar(
                select(literal_column("rowid"))
                .select_from(models.Event)
                .where(models.Event.id == event.id)
            )
            later = session.scalars(
                select(models.Event).where(
                    models.Event.campaign_id == campaign_id,
                    models.Event.type.in_(
                        ["session_state_created", "session_state_updated", "session_state_deleted"]
                    ),
                    func.json_extract(models.Event.payload, "$.id") == event.payload["id"],
                    literal_column("rowid") > anchor,
                )
            ).all()
            for subsequent in later:
                prior = (subsequent.payload.get("before") or {}).get("data", {})
                applied_later = (subsequent.payload.get("after") or {}).get("data", {})
                for key in changed_keys:
                    prior_key = {key: prior[key]} if key in prior else {}
                    later_key = {key: applied_later[key]} if key in applied_later else {}
                    if _fingerprint(prior_key) != _fingerprint(later_key):
                        raise JournalConflictError(f"a later committed action changed {key}")
        _preflight(session, events)
        before = snapshot(row)
        revision = _revision(session, campaign_id)
        for event in reversed(events):
            _apply_inverse(session, campaign_id, revision.id, revision.created_at, event)
        row.corrected = True
        row.version += 1
        row.updated_at = revision.created_at
        _event(session, revision, "journal_entry_updated", row, before)
        return row
