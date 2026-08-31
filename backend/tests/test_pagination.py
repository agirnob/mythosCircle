"""Cursor pagination: encode/decode round-trip, malformed input, and the
shared page/anchor scaffold (epic-1 retro item 2).
"""

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.ids import new_id
from app.core.pagination import (
    InvalidCursorError,
    anchor_rowid,
    decode_cursor,
    encode_cursor,
    paging,
)
from app.store import (
    app_db_url,
    create_campaign,
    init_db,
    models,
    register_account,
    session_scope,
)


class _MissingRowError(Exception):
    """The missing_error probe: the anchor must raise exactly the
    exception instance the caller supplied."""


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


# ---------------------------------------------------------------------------
# paging: the page / next-cursor split
# ---------------------------------------------------------------------------


def test_paging_slices_page_and_next_cursor() -> None:
    items: list[Any] = [SimpleNamespace(id=f"id-{i}") for i in range(5)]
    page, next_cursor = paging(items, 2)
    assert [item.id for item in page] == ["id-0", "id-1"]
    assert next_cursor == "id-1"


def test_paging_exact_page_has_no_next_cursor() -> None:
    items: list[Any] = [SimpleNamespace(id=f"id-{i}") for i in range(2)]
    page, next_cursor = paging(items, 2)
    assert [item.id for item in page] == ["id-0", "id-1"]
    assert next_cursor is None


def test_paging_fewer_items_than_limit() -> None:
    page, next_cursor = paging([SimpleNamespace(id="only")], 5)
    assert [item.id for item in page] == ["only"]
    assert next_cursor is None


def test_paging_empty_page() -> None:
    items: list[Any] = []
    page, next_cursor = paging(items, 1)
    assert page == []
    assert next_cursor is None


# ---------------------------------------------------------------------------
# anchor_rowid: cursor ULID -> rowid, fabricated cursors raise
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[None]:
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'pagination.db'}")
    try:
        yield
    finally:
        init_db(previous)


def test_anchor_rowid_resolves_and_raises_on_missing(db: Iterator[None]) -> None:
    owner = register_account(f"owner-page-{new_id()}@example.com", "password123").id
    campaign = create_campaign(
        owner, title="Page World", description="", theme="High Fantasy", custom_lore=""
    )
    with session_scope() as session:
        assert anchor_rowid(session, models.Campaign, None, missing_error=_MissingRowError()) == -1
        # A real row resolves to its SQLite rowid (>= 1 for a committed row).
        anchored = anchor_rowid(
            session, models.Campaign, campaign.id, missing_error=_MissingRowError()
        )
        assert anchored >= 1
    # A fabricated cursor (valid ULID, no such row) raises the caller's
    # missing_error — the page-reset trap (retro item 2).
    with session_scope() as session, pytest.raises(_MissingRowError):
        anchor_rowid(session, models.Campaign, new_id(), missing_error=_MissingRowError())
