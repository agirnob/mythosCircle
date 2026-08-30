"""Auth store primitives: argon2id hashing, hashed session tokens, expiry,
revocation (AR29, spec-1.5). Deterministic, no network.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.settings import SESSION_TTL_DAYS
from app.store import (
    EmailTakenError,
    app_db_url,
    create_session,
    get_session_account,
    init_db,
    models,
    register_account,
    revoke_session,
    session_scope,
    verify_login,
)


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[None]:
    """A fresh scratch database for auth tests."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'auth.db'}")
    try:
        yield
    finally:
        init_db(previous)


def test_register_hashes_password_argon2id(db: None) -> None:
    """The stored hash is argon2id — never the plaintext (AR29)."""
    account = register_account("Dm@Example.com", "correct horse battery staple")
    assert account.email == "dm@example.com"  # normalized
    assert account.password_hash != "correct horse battery staple"
    assert account.password_hash.startswith("$argon2id$")
    # verify round-trips
    assert verify_login("dm@example.com", "correct horse battery staple") is not None
    assert verify_login("dm@example.com", "wrong") is None


def test_register_duplicate_email_rejected(db: None) -> None:
    register_account("a@b.com", "password123")
    with pytest.raises(EmailTakenError):
        register_account("A@B.com", "password456")  # normalization makes it a dup


def test_verify_unknown_email_dummy_timing(db: None) -> None:
    """An unknown email returns None (generic 401 upstream) — and the
    verify runs a full argon2 pass (dummy hash), so timing does not
    reveal existence (AR29)."""
    assert verify_login("nobody@nowhere.com", "any-password") is None


def test_session_token_never_stored(db: None) -> None:
    """The DB holds only the token's SHA-256 — a leaked DB never yields
    usable tokens (AR29/spec-1.5)."""
    account = register_account("a@b.com", "password123")
    token, session_row = create_session(account.id)
    assert token != session_row.token_hash
    assert session_row.token_hash != ""
    with session_scope() as s:
        stored = s.scalars(__import__("sqlalchemy").select(models.Session)).all()
    assert len(stored) == 1
    assert stored[0].token_hash == session_row.token_hash
    assert token not in {row.token_hash for row in stored}


def test_get_session_account_resolves_valid(db: None) -> None:
    account = register_account("a@b.com", "password123")
    token, _ = create_session(account.id)
    resolved = get_session_account(token)
    assert resolved is not None and resolved.id == account.id


def test_get_session_account_unknown_token(db: None) -> None:
    assert get_session_account("totally-made-up-token") is None


def test_revoke_session_rejects(db: None) -> None:
    account = register_account("a@b.com", "password123")
    token, _ = create_session(account.id)
    assert get_session_account(token) is not None
    revoke_session(token)
    assert get_session_account(token) is None


def test_revoke_unknown_token_idempotent(db: None) -> None:
    revoke_session("no-such-token")  # must not raise


def test_session_expiry_rejects(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A session whose expires_at has passed is rejected (AR29)."""
    from app.core import time as core_time

    account = register_account("a@b.com", "password123")
    monkeypatch.setenv(SESSION_TTL_DAYS, "1")
    token, session_row = create_session(account.id)
    # Advance the clock so the session is now past its TTL.
    monkeypatch.setattr(core_time, "now", lambda: "2999-01-01T00:00:00Z")
    assert session_row is not None
    assert get_session_account(token) is None


def test_session_expiry_honors_configurable_ttl(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The env TTL is what counts — a session created with TTL=2 must be
    rejected at now+3d, NOT at the fixed 30-day default (review round 1)."""
    from app.core import time as core_time

    account = register_account("a@b.com", "password123")
    monkeypatch.setenv(SESSION_TTL_DAYS, "2")
    (token, _session) = create_session(account.id)
    # The real expiry is now+2d; a hard-coded 30d would still be valid here.
    monkeypatch.setattr(core_time, "now", lambda: core_time.now_plus_days(3))
    assert get_session_account(token) is None


def test_session_ttl_zero_never_expires(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL=0 disables expiry: the session resolves even past 30 days
    (review round 1 — the cookie would otherwise be the limiting factor)."""
    from app.core import time as core_time

    account = register_account("a@b.com", "password123")
    monkeypatch.setenv(SESSION_TTL_DAYS, "0")
    (token, _session) = create_session(account.id)
    monkeypatch.setattr(core_time, "now", lambda: core_time.now_plus_days(31))
    assert get_session_account(token) is not None  # never expires


def test_unknown_email_verify_runs_dummy_hash(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The AR29 timing defense is pinned: an unknown email still runs a
    full argon2 verify against the dummy hash (review round 1) — deleting
    the dummy branch must fail this test."""
    from app.store import auth as auth_store

    class Spy:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def verify(self, hash_value: str, password: str) -> None:
            self.calls.append((hash_value, password))

    spy = Spy()
    # PasswordHasher's attributes are read-only — stub the whole module-level
    # hasher (verify_login only needs .verify).
    monkeypatch.setattr(auth_store, "_hasher", spy)
    assert auth_store.verify_login("nobody@nowhere.com", "any-password") is None
    assert spy.calls == [(auth_store._DUMMY_HASH, "any-password")]


def test_corrupt_stored_hash_is_generic_none(db: None) -> None:
    """A tampered/corrupt stored hash returns None (generic 401), never a
    500 — a 500 on a known account would leak existence (review round 1)."""
    account = register_account("a@b.com", "password123")
    with session_scope() as s:
        import sqlalchemy

        row = s.scalars(
            sqlalchemy.select(models.Account).where(models.Account.id == account.id)
        ).first()
        assert row is not None
        row.password_hash = "$argon2id$corrupt$hash$that$cannot$parse"
    assert verify_login("a@b.com", "password123") is None
