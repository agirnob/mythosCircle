"""Cursor pagination helpers.

Convention (conventions.md): list endpoints paginate by cursor. The
opaque token encodes the ULID of the last item returned; the client
requests the next page with ``cursor=<token>``. ULIDs are time-ordered,
so the cursor also defines a stable sort key.

The page/anchor scaffold (``paging``/``anchor_rowid``) is the single
cursor slice shared by every paginated list (epic-1 retro item 2): a
fabricated cursor ULID is raised through ``missing_error`` instead of a
silent page reset.
"""

import base64
from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from app.core.ids import is_valid_ulid


class InvalidCursorError(ValueError):
    """Raised when a cursor token cannot be decoded to a ULID."""


def encode_cursor(last_id: str) -> str:
    """Encode the last item's ULID into an opaque cursor token."""
    return base64.urlsafe_b64encode(last_id.encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> str:
    """Decode a cursor token back into the last item's ULID.

    Raises:
        InvalidCursorError: if the token is malformed or does not encode a ULID.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        last_id = raw.decode("ascii")
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursorError(f"Malformed cursor: {cursor!r}") from exc
    if not is_valid_ulid(last_id):
        raise InvalidCursorError(f"Cursor does not encode a ULID: {cursor!r}")
    return last_id


class _HasId(Protocol):
    """A store row with a ULID primary key — the cursor source."""

    id: Any


def paging[Item: _HasId](items: Sequence[Item], limit: int) -> tuple[Sequence[Item], str | None]:
    """Split a ``limit + 1`` rowid-ordered fetch into page and next cursor.

    Returns the first ``limit`` items as the page and the last item's
    ULID as the opaque next-cursor when a further page exists (pagination
    convention), else ``None``.
    """
    page = items[:limit]
    has_more = len(items) > limit
    next_cursor = page[-1].id if has_more and page else None
    return page, next_cursor


def anchor_rowid[Item: _HasId](
    session: Session,
    model: type[Item],
    cursor: str | None,
    *,
    missing_error: Exception,
) -> int:
    """Resolve a cursor ULID to its row's SQLite rowid; -1 when no cursor.

    The cursor names the last item of the previous page; its rowid anchors
    ``rowid > anchor`` for the next page. A well-formed ULID that names no
    row is raised as ``missing_error`` (constructed by the caller with the
    cursor in its message) — a fabricated cursor must never silently reset
    the page (epic-1 retro item 2). Ownership and scope checks stay with
    the caller.
    """
    if cursor is None:
        return -1
    rowid = session.scalar(
        select(literal_column("rowid")).select_from(model).where(model.id == cursor)
    )
    if rowid is None:
        raise missing_error
    return rowid
