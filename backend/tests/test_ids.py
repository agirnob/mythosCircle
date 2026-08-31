"""ULID identifier convention: 26-character Crockford strings."""

import re

import pytest

from app.core.ids import CROCKFORD_ALPHABET, is_valid_ulid, new_id

ULID_RE = re.compile(rf"^[{''.join(sorted(CROCKFORD_ALPHABET))}]{{26}}$")

#: A ULID that can never have been minted (all zero) — valid Crockford.
ZERO_ULID = "0" * 26


def test_new_id_is_26_char_crockford() -> None:
    value = new_id()
    assert len(value) == 26
    assert ULID_RE.match(value)


def test_new_ids_are_unique() -> None:
    assert len({new_id() for _ in range(1000)}) == 1000


def test_is_valid_ulid_accepts_26_char_crockford() -> None:
    value = new_id()
    assert is_valid_ulid(value) is True


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(new_id()[:-1], id="too-short"),
        pytest.param(new_id() + "A", id="too-long"),
        pytest.param(new_id().lower(), id="lowercase"),
        pytest.param(new_id()[:-1] + "I", id="letter-I-excluded"),
        pytest.param(new_id()[:-1] + "L", id="letter-L-excluded"),
        pytest.param(new_id()[:-1] + "O", id="letter-O-excluded"),
        pytest.param("", id="empty"),
    ],
)
def test_is_valid_ulid_rejects(bad: str) -> None:
    assert is_valid_ulid(bad) is False


def test_is_valid_ulid_accepts_digits_including_zero_one() -> None:
    """0 and 1 ARE valid Crockford digits (CROCKFORD_ALPHABET includes them);
    python-ulid's own 26-char output can contain them."""
    assert "0" in CROCKFORD_ALPHABET and "1" in CROCKFORD_ALPHABET
    assert is_valid_ulid(ZERO_ULID) is True
    assert is_valid_ulid("1" * 26) is True


def test_is_valid_ulid_non_str_raises_type_error() -> None:
    """The predicate is typed for str (value: str); non-str inputs raise
    TypeError rather than returning False."""
    with pytest.raises(TypeError):
        is_valid_ulid(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        is_valid_ulid(12345)  # type: ignore[arg-type]
