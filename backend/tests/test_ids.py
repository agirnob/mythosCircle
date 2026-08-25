"""ULID identifier convention: 26-character Crockford strings."""

import re

from app.core.ids import CROCKFORD_ALPHABET, new_id

ULID_RE = re.compile(rf"^[{''.join(sorted(CROCKFORD_ALPHABET))}]{{26}}$")


def test_new_id_is_26_char_crockford() -> None:
    value = new_id()
    assert len(value) == 26
    assert ULID_RE.match(value)


def test_new_ids_are_unique() -> None:
    assert len({new_id() for _ in range(1000)}) == 1000
