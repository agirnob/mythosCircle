"""Per-transaction undo (AD-2): one compensating commit.

Undo is not a pointer flip. It re-plays the inverse of the target
revision's state deltas inside a single transaction, appends the inverse
delta events, and produces its own revision — the append-only log is
never rewritten. Because the inverse deltas are ordinary delta events,
undoing the undo revision re-applies the original deltas (redo falls out
of the same mechanism).

Undo only applies to the latest revision; anything else is a stale
target and is rejected (``StaleRevisionError``) — no silent overwrite.
All events of the target revision are validated *before* any state is
touched (preflight) — all-or-nothing structurally, matching the commit
path's validate-then-mutate discipline. State/log inconsistencies that
would make the inverse impossible raise ``CorruptEventError`` (reject,
never guess) and roll back the whole undo.

Media-manifest rows are untouched by undo in the RESTORE direction — no
media events exist to invert, so undo never restores reclaimed media
(spec-4.3; undoing an entity-delete revision recreates the entity but
its reclaimed rows/files stay gone — regeneration is the recovery). The
DELETE direction reclaims: an entity-delete revision reclaims its
manifest rows in the same transaction, and the inverse of an
entity-CREATION revision — which deletes the entity — now reclaims the
entity's manifest rows in this transaction too (spec-4.3 deferral
closed, epic-4 retro item 12/13 bundle; rows in the store transaction,
files post-commit by the API layer, mirroring ``_delete_entity``). A
media delete is likewise not undoable. The HTTP surface is ``POST
/api/campaigns/{campaign_id}/undo`` (spec-4.3 follow-up).
"""

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core import ids, time
from app.store import models
from app.store.commit import (
    CorruptEventError,
    StaleRevisionError,
    UnknownCampaignError,
    _add_event,
)
from app.store.db import session_scope
from app.store.read import latest_revision


def undo(campaign_id: str, revision_id: str) -> models.Revision:
    """Revert the latest revision's deltas; returns the new undo revision.

    Raises ``UnknownCampaignError`` or ``StaleRevisionError`` (carrying
    the latest revision id) — in which case no state is changed.
    """
    with session_scope() as session:
        return _undo(session, campaign_id, revision_id)


def _undo(session: Session, campaign_id: str, revision_id: str) -> models.Revision:
    if session.get(models.Campaign, campaign_id) is None:
        raise UnknownCampaignError(campaign_id)

    latest = latest_revision(session, campaign_id)
    if latest is None:
        raise StaleRevisionError(None)

    events = session.scalars(
        select(models.Event)
        .where(
            models.Event.campaign_id == campaign_id,
            models.Event.revision_id == revision_id,
        )
        .order_by(literal_column("rowid"))
    ).all()
    if not events:
        raise CorruptEventError(revision_id, "target revision has no events")

    # AD-27 take-back: a verb/toggle revision is taken back SURGICALLY
    # even when a later commit is the head — the inverse appends onto
    # CURRENT state, so a same-field later edit is preserved
    # arithmetically, never overwritten (the scar edit between the
    # defeat and its take-back stands). Anything else still requires
    # the head: an image-inverting undo of a non-head revision would
    # silently rewind later canon edits.
    if latest.id != revision_id and (
        not events
        or any(
            not event.type.startswith(("session_state_", "knowledge_state_")) for event in events
        )
    ):
        raise StaleRevisionError(latest.id)

    # Preflight before any mutation: payload shape AND expected row state.
    # A corrupt log is rejected with zero state touched; the undo revision
    # is only created after every event has been validated.
    _preflight(session, events)

    now = time.now()
    revision = models.Revision(
        id=ids.new_id(),
        campaign_id=campaign_id,
        base_revision=latest.id,
        created_at=now,
    )
    session.add(revision)

    for event in reversed(events):
        _apply_inverse(session, campaign_id, revision.id, now, event)

    return revision


def _preflight(session: Session, events: Sequence[models.Event]) -> None:
    """Validate every event of the target revision, none applied yet."""
    for event in events:
        payload = event.payload
        _require_keys(event, payload)
        row = _state_row(session, event, payload)
        if (
            event.type.startswith(("journal_entry_", "play_session_"))
            or event.type == "campaign_active_session_updated"
        ):
            if row is None or (
                isinstance(row, (models.JournalEntry, models.PlaySession))
                and row.campaign_id != event.campaign_id
            ):
                raise CorruptEventError(event.id, "journal row missing for inverse")
            if isinstance(row, models.Campaign) and row.id != event.campaign_id:
                raise CorruptEventError(event.id, "active session event names another campaign")
            if event.type not in (
                "journal_entry_created",
                "journal_entry_updated",
                "play_session_created",
                "play_session_updated",
                "campaign_active_session_updated",
            ):
                raise CorruptEventError(event.id, "unknown journal event type")
            from app.store.journal import snapshot

            current = (
                {"active_session_id": row.active_session_id}
                if isinstance(row, models.Campaign)
                else snapshot(row)
            )
            if json.dumps(current, sort_keys=True) != json.dumps(payload["after"], sort_keys=True):
                raise CorruptEventError(event.id, "journal row differs from its committed event")
            continue
        if event.type in (
            "entity_created",
            "entity_updated",
            "edge_created",
            "edge_updated",
            "session_state_created",
            "session_state_updated",
            "knowledge_state_created",
            "knowledge_state_updated",
        ):
            # The inverse deletes/updates this row; it must exist.
            if row is None:
                raise CorruptEventError(event.id, "row missing for inverse")
        elif event.type in (
            "entity_deleted",
            "edge_deleted",
            "session_state_deleted",
            "knowledge_state_deleted",
        ):
            # The inverse recreates this row; it must be absent (redo path).
            if row is not None:
                raise CorruptEventError(event.id, "row already exists for inverse recreate")
        else:
            raise CorruptEventError(event.id, f"unknown event type {event.type!r}")


def _state_row(session: Session, event: models.Event, payload: dict[str, Any]) -> Any:
    """The materialized row an event's inverse targets, or None."""
    if event.type.startswith("journal_entry_"):
        return session.get(models.JournalEntry, payload["id"])
    if event.type.startswith("play_session_"):
        return session.get(models.PlaySession, payload["id"])
    if event.type == "campaign_active_session_updated":
        return session.get(models.Campaign, payload["id"])
    if event.type.startswith("entity"):
        return session.get(models.Entity, payload["id"])
    if event.type.startswith("edge"):
        return session.get(models.Edge, payload["id"])
    if event.type.startswith("session_state_"):
        return session.scalars(
            select(models.EntitySessionState).where(
                models.EntitySessionState.campaign_id == event.campaign_id,
                models.EntitySessionState.entity_id == payload["id"],
            )
        ).first()
    if event.type.startswith("knowledge_state_"):
        return session.scalars(
            select(models.EntityKnowledgeState).where(
                models.EntityKnowledgeState.campaign_id == event.campaign_id,
                models.EntityKnowledgeState.entity_id == payload["id"],
                models.EntityKnowledgeState.field == payload.get("field"),
            )
        ).first()
    return None


def _apply_inverse(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    payload = event.payload
    if (
        event.type.startswith(("journal_entry_", "play_session_"))
        or event.type == "campaign_active_session_updated"
    ):
        _inverse_journal(session, campaign_id, revision_id, created_at, event)
    elif event.type == "entity_created":
        _inverse_entity_deleted(session, campaign_id, revision_id, created_at, event)
    elif event.type == "entity_updated":
        _inverse_entity_update(session, campaign_id, revision_id, created_at, event)
    elif event.type == "edge_created":
        _inverse_edge_deleted(session, campaign_id, revision_id, created_at, event)
    elif event.type == "edge_updated":
        _inverse_edge_update(session, campaign_id, revision_id, created_at, event)
    elif event.type == "entity_deleted":
        # Redo branch: an undo revision's deletions invert to recreations.
        before = payload["before"]
        session.add(
            models.Entity(
                id=payload["id"],
                campaign_id=campaign_id,
                kind=before["kind"],
                name=before["name"],
                text=before["text"],
                data=before["data"],
                created_at=before["created_at"],
            )
        )
        _add_event(
            session,
            campaign_id,
            revision_id,
            "entity_created",
            {"id": payload["id"], "before": None, "after": before},
            created_at,
        )
    elif event.type == "edge_deleted":
        before = payload["before"]
        session.add(
            models.Edge(
                id=payload["id"],
                campaign_id=campaign_id,
                src=before["src"],
                dst=before["dst"],
                type=before["type"],
                counter=before["counter"],
                reason=before.get("reason"),
                created_at=before["created_at"],
            )
        )
        _add_event(
            session,
            campaign_id,
            revision_id,
            "edge_created",
            {"id": payload["id"], "before": None, "after": before},
            created_at,
        )
    elif event.type == "session_state_created":
        _inverse_session_state_created(session, campaign_id, revision_id, created_at, event)
    elif event.type == "session_state_updated":
        _inverse_session_state_updated(session, campaign_id, revision_id, created_at, event)
    elif event.type == "session_state_deleted":
        # Redo branch: recreate the run-state row from its before image.
        before = payload["before"]
        session.add(
            models.EntitySessionState(
                id=ids.new_id(),
                campaign_id=campaign_id,
                entity_id=payload["id"],
                data=before["data"],
                created_at=created_at,
                updated_at=before["updated_at"],
            )
        )
        _add_event(
            session,
            campaign_id,
            revision_id,
            "session_state_created",
            {"id": payload["id"], "before": None, "after": before},
            created_at,
        )
    elif event.type == "knowledge_state_created":
        _inverse_knowledge_state_created(session, campaign_id, revision_id, created_at, event)
    elif event.type == "knowledge_state_updated":
        _inverse_knowledge_state_updated(session, campaign_id, revision_id, created_at, event)
    elif event.type == "knowledge_state_deleted":
        before = payload["before"]
        session.add(
            models.EntityKnowledgeState(
                id=ids.new_id(),
                campaign_id=campaign_id,
                entity_id=payload["id"],
                field=before["field"],
                known=before["known"],
                created_at=created_at,
                updated_at=before["updated_at"],
            )
        )
        _add_event(
            session,
            campaign_id,
            revision_id,
            "knowledge_state_created",
            {"id": payload["id"], "field": before["field"], "before": None, "after": before},
            created_at,
        )
    else:
        # Unreachable — _preflight rejects unknown types before any apply.
        raise CorruptEventError(event.id, f"unknown event type {event.type!r}")


_ENTITY_KEYS = ("kind", "name", "text", "data", "created_at")
# Legacy snapshots predate the nullable reason column.
_EDGE_KEYS = ("src", "dst", "type", "counter", "created_at")
#: AD-28 run-state snapshot keys (rebuild-faithful, AD-26). The payload
#: ``id`` names the ENTITY; the knowledge payload also carries ``field``.
_SESSION_KEYS = ("data", "updated_at")
_KNOWLEDGE_KEYS = ("field", "known", "updated_at")
#: Required payload snapshot fields per known event type (AD-1).
_REQUIRED: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "play_session_created": (
        (
            "after",
            ("title", "play_date", "sequence", "version", "created_at", "updated_at", "deleted_at"),
        ),
    ),
    "play_session_updated": (
        (
            "before",
            ("title", "play_date", "sequence", "version", "created_at", "updated_at", "deleted_at"),
        ),
        (
            "after",
            ("title", "play_date", "sequence", "version", "created_at", "updated_at", "deleted_at"),
        ),
    ),
    "journal_entry_created": (
        (
            "after",
            (
                "session_id",
                "headline",
                "context",
                "references",
                "position",
                "version",
                "created_at",
                "updated_at",
                "deleted_at",
                "corrected",
                "source_event_id",
                "action_revision_id",
                "action_entity_id",
            ),
        ),
    ),
    "journal_entry_updated": (
        (
            "before",
            (
                "session_id",
                "headline",
                "context",
                "references",
                "position",
                "version",
                "created_at",
                "updated_at",
                "deleted_at",
                "corrected",
                "source_event_id",
                "action_revision_id",
                "action_entity_id",
            ),
        ),
        (
            "after",
            (
                "session_id",
                "headline",
                "context",
                "references",
                "position",
                "version",
                "created_at",
                "updated_at",
                "deleted_at",
                "corrected",
                "source_event_id",
                "action_revision_id",
                "action_entity_id",
            ),
        ),
    ),
    "campaign_active_session_updated": (
        ("before", ("active_session_id",)),
        ("after", ("active_session_id",)),
    ),
    "entity_created": (("after", _ENTITY_KEYS),),
    "entity_updated": (("before", _ENTITY_KEYS), ("after", _ENTITY_KEYS)),
    "entity_deleted": (("before", _ENTITY_KEYS),),
    "edge_created": (("after", _EDGE_KEYS),),
    "edge_updated": (("before", _EDGE_KEYS), ("after", _EDGE_KEYS)),
    "edge_deleted": (("before", _EDGE_KEYS),),
    # AD-26/AD-28 run-state event families join the same preflight
    # contract (reject never guess). The undo of a verb/toggle revision
    # applies the SURGICAL inverse (AD-27 arithmetic), never an image.
    "session_state_created": (("after", _SESSION_KEYS),),
    "session_state_updated": (("before", _SESSION_KEYS), ("after", _SESSION_KEYS)),
    "session_state_deleted": (("before", _SESSION_KEYS),),
    "knowledge_state_created": (("after", _KNOWLEDGE_KEYS),),
    "knowledge_state_updated": (("before", _KNOWLEDGE_KEYS), ("after", _KNOWLEDGE_KEYS)),
    "knowledge_state_deleted": (("before", _KNOWLEDGE_KEYS),),
}


def _inverse_journal(
    session: Session, campaign_id: str, revision_id: str, created_at: str, event: models.Event
) -> None:
    from app.store.journal import snapshot

    row = _state_row(session, event, event.payload)
    if event.type == "campaign_active_session_updated":
        before = {"active_session_id": row.active_session_id}
        row.active_session_id = event.payload["before"]["active_session_id"]
        _add_event(
            session,
            campaign_id,
            revision_id,
            event.type,
            {"id": row.id, "before": before, "after": {"active_session_id": row.active_session_id}},
            created_at,
        )
        return
    before = snapshot(row)
    if event.type.endswith("_created"):
        # Removal retains the retry receipt and stable source identity.
        row.deleted_at = created_at
    else:
        for key, value in event.payload["before"].items():
            if key not in ("id", "campaign_id", "version", "updated_at"):
                setattr(row, key, value)
    row.version += 1
    row.updated_at = created_at
    _add_event(
        session,
        campaign_id,
        revision_id,
        event.type.rsplit("_", 1)[0] + "_updated",
        {"id": row.id, "before": before, "after": snapshot(row)},
        created_at,
    )


def _require_keys(event: models.Event, payload: dict[str, Any]) -> None:
    """Validate a known event payload's shape (AD-1, reject never guess)."""
    if not isinstance(payload, dict) or "id" not in payload:
        raise CorruptEventError(event.id, "payload missing 'id'")
    for field, keys in _REQUIRED.get(event.type, ()):
        snapshot = payload.get(field)
        if not isinstance(snapshot, dict):
            raise CorruptEventError(event.id, f"payload {field!r} is missing or not a snapshot")
        missing = [key for key in keys if key not in snapshot]
        if missing:
            raise CorruptEventError(
                event.id, f"payload {field!r} missing keys: {', '.join(missing)}"
            )


def _inverse_entity_deleted(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    payload = event.payload
    row = session.get(models.Entity, payload["id"])
    if row is None:
        raise CorruptEventError(event.id, "entity row missing for inverse of entity_created")
    # Reclaim the entity's media rows inside this transaction (AD-10,
    # spec-4.3; the ``_delete_entity`` pattern, deferral closed 2026-09-10):
    # the inverse of an entity_created event DELETES the entity, so its
    # manifest rows leave with it — an index, not graph state, no events,
    # no revision delta, and undo never restores them (regeneration is
    # the recovery). Files are the API layer's post-commit job (AD-10
    # rows-first ordering). Function-local import: store.media imports
    # this module's errors' siblings.
    from app.store.media import delete_entity_media

    delete_entity_media(session, campaign_id, payload["id"])
    session.delete(row)
    _add_event(
        session,
        campaign_id,
        revision_id,
        "entity_deleted",
        {"id": payload["id"], "before": payload["after"], "after": None},
        created_at,
    )


def _inverse_entity_update(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    payload = event.payload
    row = session.get(models.Entity, payload["id"])
    if row is None:
        raise CorruptEventError(event.id, "entity row missing for inverse of entity_updated")
    before = payload["before"]
    row.kind = before["kind"]
    row.name = before["name"]
    row.text = before["text"]
    row.data = before["data"]
    flag_modified(row, "data")
    _add_event(
        session,
        campaign_id,
        revision_id,
        "entity_updated",
        {"id": payload["id"], "before": payload["after"], "after": before},
        created_at,
    )


def _inverse_edge_deleted(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    payload = event.payload
    row = session.get(models.Edge, payload["id"])
    if row is None:
        raise CorruptEventError(event.id, "edge row missing for inverse of edge_created")
    session.delete(row)
    _add_event(
        session,
        campaign_id,
        revision_id,
        "edge_deleted",
        {"id": payload["id"], "before": payload["after"], "after": None},
        created_at,
    )


def _inverse_edge_update(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    payload = event.payload
    row = session.get(models.Edge, payload["id"])
    if row is None:
        raise CorruptEventError(event.id, "edge row missing for inverse of edge_updated")
    before = payload["before"]
    row.src = before["src"]
    row.dst = before["dst"]
    row.type = before["type"]
    row.counter = before["counter"]
    row.reason = before.get("reason")
    _add_event(
        session,
        campaign_id,
        revision_id,
        "edge_updated",
        {"id": payload["id"], "before": payload["after"], "after": before},
        created_at,
    )


def _inverse_session_state_created(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    """Remove the creation delta, preserving subsequent session state."""
    payload = event.payload
    row = session.scalars(
        select(models.EntitySessionState).where(
            models.EntitySessionState.campaign_id == campaign_id,
            models.EntitySessionState.entity_id == payload["id"],
        )
    ).first()
    if row is None:
        raise CorruptEventError(event.id, "run-state row missing for inverse")
    current: dict[str, Any] = {"data": dict(row.data), "updated_at": row.updated_at}
    undone = _surgical_inverse({}, payload["after"]["data"], current["data"])
    if undone:
        row.data = undone
        flag_modified(row, "data")
        row.updated_at = created_at
        event_type = "session_state_updated"
        after = {"data": undone, "updated_at": created_at}
    else:
        session.delete(row)
        event_type = "session_state_deleted"
        after = None
    _add_event(
        session,
        campaign_id,
        revision_id,
        event_type,
        {"id": payload["id"], "before": current, "after": after},
        created_at,
    )


def _inverse_session_state_updated(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    """The SURGICAL inverse of a verb commit (AD-27): the verb's own
    deltas invert arithmetically onto CURRENT state — a same-field later
    edit is preserved, never overwritten (enrich's hp 35 plus a −12
    inverse lands 23, not 40)."""
    payload = event.payload
    row = session.scalars(
        select(models.EntitySessionState).where(
            models.EntitySessionState.campaign_id == campaign_id,
            models.EntitySessionState.entity_id == payload["id"],
        )
    ).first()
    if row is None:
        raise CorruptEventError(event.id, "run-state row missing for inverse")
    before = payload["before"]["data"]
    after = payload["after"]["data"]
    current = dict(row.data) if isinstance(row.data, dict) else {}
    current_image = {"data": current, "updated_at": row.updated_at}
    undone = _surgical_inverse(before, after, current)
    row.data = undone
    flag_modified(row, "data")
    row.updated_at = created_at
    _add_event(
        session,
        campaign_id,
        revision_id,
        "session_state_updated",
        {
            "id": payload["id"],
            "before": current_image,
            "after": {"data": undone, "updated_at": created_at},
        },
        created_at,
    )


def _inverse_knowledge_state_created(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    """Delete the created marker only while its original result still holds."""
    payload = event.payload
    row = session.scalars(
        select(models.EntityKnowledgeState).where(
            models.EntityKnowledgeState.campaign_id == campaign_id,
            models.EntityKnowledgeState.entity_id == payload["id"],
            models.EntityKnowledgeState.field == payload["field"],
        )
    ).first()
    if row is None:
        raise CorruptEventError(event.id, "knowledge row missing for inverse")
    current = {"field": row.field, "known": row.known, "updated_at": row.updated_at}
    if row.known == payload["after"]["known"]:
        session.delete(row)
        event_type = "knowledge_state_deleted"
        after = None
    else:
        # A later toggle owns this marker; retain it and log the actual transition.
        row.updated_at = created_at
        event_type = "knowledge_state_updated"
        after = {**current, "updated_at": created_at}
    _add_event(
        session,
        campaign_id,
        revision_id,
        event_type,
        {"id": payload["id"], "field": row.field, "before": current, "after": after},
        created_at,
    )


def _inverse_knowledge_state_updated(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    """The inverse of a toggle: flip back when the marker still holds
    this transaction's result; a later flip stands (one undoable step
    per transaction, AD-29)."""
    payload = event.payload
    row = session.scalars(
        select(models.EntityKnowledgeState).where(
            models.EntityKnowledgeState.campaign_id == campaign_id,
            models.EntityKnowledgeState.entity_id == payload["id"],
            models.EntityKnowledgeState.field == payload["field"],
        )
    ).first()
    if row is None:
        raise CorruptEventError(event.id, "knowledge row missing for inverse")
    current = {"field": row.field, "known": row.known, "updated_at": row.updated_at}
    before = payload["before"]
    after = payload["after"]
    if row.known == after["known"]:
        # The marker still holds this transaction's result: flip back.
        row.known = before["known"]
        undone = {**after, "known": before["known"], "updated_at": created_at}
    else:
        # A later commit already moved the marker; the take-back leaves
        # it (surgical precedence, AD-27).
        undone = {**after, "known": row.known, "updated_at": created_at}
    row.updated_at = created_at
    _add_event(
        session,
        campaign_id,
        revision_id,
        "knowledge_state_updated",
        {
            "id": payload["id"],
            "field": payload["field"],
            "before": current,
            "after": undone,
        },
        created_at,
    )


def _surgical_inverse(
    before: dict[str, Any], after: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """AD-27 arithmetic: per-key inverse of exactly this transaction's
    delta onto CURRENT state. Numeric keys subtract the verb's delta;
    scalar keys restore the prior value only when the current value
    still holds the verb's result (a later same-field edit stands);
    keys the verb added are removed only when untouched since."""
    result = dict(current)

    def same(left: Any, right: Any) -> bool:
        return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)

    for key in set(before) | set(after):
        if key in before and key in after:
            prev, applied = before[key], after[key]
            if same(prev, applied):
                continue
            cur_value = current.get(key)
            if (
                isinstance(cur_value, (int, float))
                and not isinstance(cur_value, bool)
                and isinstance(prev, (int, float))
                and not isinstance(prev, bool)
                and isinstance(applied, (int, float))
                and not isinstance(applied, bool)
            ):
                # Additive arithmetic: current + (before - applied).
                result[key] = cur_value + (prev - applied)
            elif key in current and same(current[key], after[key]):
                result[key] = before[key]
            # else: a later edit owns the field — it stands.
        elif key in after and key not in before:
            # The verb added this key; the inverse removes it only when
            # untouched since.
            if key in current and same(current[key], after[key]):
                result.pop(key, None)
        elif key in before and key not in after:
            # The verb removed the key; the inverse restores it only
            # when nothing re-added it.
            if key not in current:
                result[key] = before[key]
    return result
