"""UTC ISO-8601 timestamp convention."""

import re
from datetime import UTC, datetime, timedelta

from app.core import time as app_time

ISO_8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


def test_now_is_utc_iso8601() -> None:
    value = app_time.now()
    assert ISO_8601_UTC_RE.match(value)
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() == timedelta(0)


def test_now_is_current_instant() -> None:
    parsed = datetime.fromisoformat(app_time.now())
    drift = abs((datetime.now(UTC) - parsed).total_seconds())
    assert drift < 5


def test_to_utc_iso_renders_fixed_instant() -> None:
    moment = datetime(2026, 8, 25, 12, 34, 56, 789012, tzinfo=UTC)
    assert app_time.to_utc_iso(moment) == "2026-08-25T12:34:56.789012Z"


def test_to_utc_iso_normalizes_naive_utc() -> None:
    moment = datetime(2026, 8, 25, 12, 34, 56)
    assert app_time.to_utc_iso(moment) == "2026-08-25T12:34:56Z"
