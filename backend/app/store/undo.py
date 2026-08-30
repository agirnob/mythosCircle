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

Media-manifest rows are untouched by undo; reclamation happens with the
media stories (AD-10, Epic 4).
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

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
    if latest is None or latest.id != revision_id:
        raise StaleRevisionError(latest.id if latest is not None else None)

    events = session.scalars(
        select(models.Event)
        .where(
            models.Event.campaign_id == campaign_id,
            models.Event.revision_id == revision_id,
        )
        .order_by(literal_column("rowid"))
    ).all()

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
        is_entity = event.type.startswith("entity")
        row = (
            session.get(models.Entity, payload["id"])
            if is_entity
            else session.get(models.Edge, payload["id"])
        )
        if event.type in ("entity_created", "entity_updated", "edge_created", "edge_updated"):
            # The inverse deletes/updates this row; it must exist.
            if row is None:
                raise CorruptEventError(event.id, "row missing for inverse")
        elif event.type in ("entity_deleted", "edge_deleted"):
            # The inverse recreates this row; it must be absent (redo path).
            if row is not None:
                raise CorruptEventError(event.id, "row already exists for inverse recreate")
        else:
            raise CorruptEventError(event.id, f"unknown event type {event.type!r}")


def _apply_inverse(
    session: Session,
    campaign_id: str,
    revision_id: str,
    created_at: str,
    event: models.Event,
) -> None:
    payload = event.payload
    if event.type == "entity_created":
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
    else:
        # Unreachable — _preflight rejects unknown types before any apply.
        raise CorruptEventError(event.id, f"unknown event type {event.type!r}")


_ENTITY_KEYS = ("kind", "name", "text", "data", "created_at")
_EDGE_KEYS = ("src", "dst", "type", "counter", "created_at")
#: Required payload snapshot fields per known event type (AD-1).
_REQUIRED: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "entity_created": (("after", _ENTITY_KEYS),),
    "entity_updated": (("before", _ENTITY_KEYS), ("after", _ENTITY_KEYS)),
    "entity_deleted": (("before", _ENTITY_KEYS),),
    "edge_created": (("after", _EDGE_KEYS),),
    "edge_updated": (("before", _EDGE_KEYS), ("after", _EDGE_KEYS)),
    "edge_deleted": (("before", _EDGE_KEYS),),
}


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
    _add_event(
        session,
        campaign_id,
        revision_id,
        "edge_updated",
        {"id": payload["id"], "before": payload["after"], "after": before},
        created_at,
    )
