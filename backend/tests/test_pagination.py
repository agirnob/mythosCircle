"""Cursor pagination: encode/decode round-trip and malformed input."""

import pytest

from app.core.ids import new_id
from app.core.pagination import InvalidCursorError, decode_cursor, encode_cursor


def test_round_trip() -> None:
    last_id = new_id()
    assert decode_cursor(encode_cursor(last_id)) == last_id


def test_malformed_cursor_raises() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor("not a cursor")
    with pytest.raises(InvalidCursorError):
        decode_cursor("YWJj")  # valid base64, decodes to "abc" — not a ULID


def test_invalid_cursor_is_value_error() -> None:
    assert issubclass(InvalidCursorError, ValueError)
