"""ULID identifiers.

All entity/edge/job/event IDs are ULIDs, not UUIDs (conventions.md):
26 characters from the Crockford alphabet, time-ordered.
"""

from ulid import ULID

#: Crockford base32 alphabet (no I, L, O, U).
CROCKFORD_ALPHABET = frozenset("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def new_id() -> str:
    """Return a new ULID as a 26-character Crockford string."""
    return str(ULID())


def is_valid_ulid(value: str) -> bool:
    """True iff ``value`` is a 26-character Crockford ULID (conventions.md)."""
    return len(value) == 26 and all(ch in CROCKFORD_ALPHABET for ch in value)
