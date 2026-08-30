"""UTC ISO-8601 timestamps.

All persisted and wire timestamps are UTC ISO-8601 (conventions.md),
rendered with the ``Z`` designator, e.g. ``2026-08-25T12:34:56.789012Z``.
"""

from datetime import UTC, datetime, timedelta


def now() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def now_plus_days(days: int) -> str:
    """Return the current UTC time plus ``days`` as an ISO-8601 ``Z`` string."""
    return (datetime.now(UTC) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def to_utc_iso(moment: datetime) -> str:
    """Render any datetime as UTC ISO-8601 with a ``Z`` suffix.

    Naive datetimes are assumed to already be UTC.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
