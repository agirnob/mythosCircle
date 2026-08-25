"""Cursor pagination helpers.

Convention (conventions.md): list endpoints paginate by cursor. The
opaque token encodes the ULID of the last item returned; the client
requests the next page with ``cursor=<token>``. ULIDs are time-ordered,
so the cursor also defines a stable sort key.
"""

import base64

from app.core.ids import CROCKFORD_ALPHABET

_ULID_LENGTH = 26


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
    if len(last_id) != _ULID_LENGTH or any(ch not in CROCKFORD_ALPHABET for ch in last_id):
        raise InvalidCursorError(f"Cursor does not encode a ULID: {cursor!r}")
    return last_id
