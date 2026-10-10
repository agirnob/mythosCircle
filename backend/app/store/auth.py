"""Account and session primitives (AR14/AR29, spec-1.5).

The store is the single writer (AD-13): accounts and sessions live here,
created through ``session_scope`` like every other table. Auth is NOT
world graph — no revisions, no events.

Security invariants:
- argon2id password hashing only; plaintext never stored or logged.
- Sessions are opaque tokens (``secrets.token_urlsafe(32)``); the DB
  stores only their SHA-256 — a leaked database never yields tokens.
- `verify_login` runs a full argon2 verify even for unknown emails
  (against a fixed dummy hash) so response timing does not reveal
  whether an account exists (AR29, no user enumeration).
"""

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select

from app.core import ids, time
from app.core.settings import session_ttl_days
from app.store import models
from app.store.commit import StoreError
from app.store.db import session_scope

_hasher = PasswordHasher()

#: A fixed dummy hash verified against for unknown emails — keeps verify
#: timing indistinguishable from a real account (AR29).
_DUMMY_HASH = _hasher.hash("mythoscircle-dummy-password")

#: Far-future timestamp used when the session TTL is disabled (0 days).
_NEVER_EXPIRE_DAYS = 36_500 * 30  # ~year 9999


class InactiveAccountError(StoreError):
    """A session cannot be created for a missing or suspended identity."""


class EmailTakenError(StoreError):
    """An account with this (normalized) email already exists."""

    def __init__(self, email: str) -> None:
        super().__init__(f"account already exists: {email}")
        self.email = email


def register_account(email: str, password: str) -> models.Account:
    """Create an account with an argon2id-hashed password.

    Rejects with ``EmailTakenError`` (409) if the normalized email exists;
    nothing is written on rejection.
    """
    normalized = _normalize_email(email)
    with session_scope() as session:
        existing = session.scalars(
            select(models.Account).where(models.Account.email == normalized)
        ).first()
        if existing is not None:
            raise EmailTakenError(normalized)
        account = models.Account(
            id=ids.new_id(),
            email=normalized,
            password_hash=_hasher.hash(password),
            created_at=time.now(),
        )
        session.add(account)
        return account


def verify_login(email: str, password: str) -> models.Account | None:
    """Return the account on valid credentials, else None (generic 401).

    Unknown emails run a dummy argon2 verify so timing does not reveal
    existence (AR29). The caller decides the response — always the same
    generic 401 body.
    """
    normalized = _normalize_email(email)
    with session_scope() as session:
        account = session.scalars(
            select(models.Account).where(models.Account.email == normalized)
        ).first()
        hash_to_check = account.password_hash if account is not None else _DUMMY_HASH
        try:
            _hasher.verify(hash_to_check, password)
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            # A corrupt/tampered stored hash is indistinguishable from a
            # wrong password — a 500 on a known account would leak
            # existence (review round 1, AR29).
            return None
        return account if account is not None and account.disabled_at is None else None


def create_session(account_id: str) -> tuple[str, models.LoginSession]:
    """Create a session; returns (raw_token, session_row).

    The raw token goes to the cookie; only its SHA-256 is stored.
    """
    token = secrets.token_urlsafe(32)
    token_hash = _sha256(token)
    ttl_days = session_ttl_days()
    expires_at = (
        time.now_plus_days(ttl_days) if ttl_days else time.now_plus_days(_NEVER_EXPIRE_DAYS)
    )
    with session_scope() as session:
        account = session.get(models.Account, account_id)
        if account is None or account.disabled_at is not None:
            raise InactiveAccountError("Invalid credentials.")
        session_row = models.LoginSession(
            id=ids.new_id(),
            account_id=account_id,
            token_hash=token_hash,
            created_at=time.now(),
            expires_at=expires_at,
            revoked_at=None,
        )
        session.add(session_row)
        return token, session_row


def get_session_account(token: str) -> models.Account | None:
    """Resolve a raw token to its account, or None (invalid/expired/revoked).

    Expired or revoked sessions are rejected (AR29) — a rejected session
    is not deleted (the row stays for audit; expiry is a pure read).
    """
    token_hash = _sha256(token)
    with session_scope() as session:
        session_row = session.scalars(
            select(models.LoginSession).where(models.LoginSession.token_hash == token_hash)
        ).first()
        if session_row is None:
            return None
        if session_row.revoked_at is not None:
            return None
        if _is_expired(session_row.expires_at):
            return None
        account = session.get(models.Account, session_row.account_id)
        return account if account is not None and account.disabled_at is None else None


def revoke_session(token: str) -> None:
    """Revoke a session immediately (idempotent — unknown tokens are fine)."""
    token_hash = _sha256(token)
    with session_scope() as session:
        session_row = session.scalars(
            select(models.LoginSession).where(models.LoginSession.token_hash == token_hash)
        ).first()
        if session_row is not None and session_row.revoked_at is None:
            session_row.revoked_at = time.now()


def _normalize_email(email: str) -> str:
    """Lowercase + trim — one canonical form per inbox (AR15 conventions)."""
    return email.strip().lower()


def _sha256(token: str) -> str:
    """The stored form of a session token: SHA-256 hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_expired(expires_at: str) -> bool:
    """True when an ISO-8601 expiry has passed.

    Lexicographic comparison of ISO strings is unsafe at the exact-second
    boundary (``...T12:00:00Z`` vs ``...T12:00:00.123Z``: ``'Z'`` sorts
    after ``'.'``, so a just-expired session can look valid) — parse both
    sides to datetime and compare (review round 1).
    """
    from datetime import UTC, datetime

    def _parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)

    try:
        return _parse(expires_at) <= _parse(time.now())
    except ValueError:
        # An unparseable stored timestamp is treated as expired — never
        # a session that can never expire.
        return True
