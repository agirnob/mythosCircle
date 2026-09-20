"""DM authentication endpoints (AR14/AR29, spec-1.5).

REST for auth: register, login (sets the httpOnly session cookie),
logout (revokes), and ``/api/auth/me``. ``get_current_account`` is the
FastAPI dependency 1.6's campaign CRUD consumes.

The generic-401 rule is absolute: unknown email, wrong password, locked
account, and missing/invalid/expired/revoked session all produce the
same envelope — no user enumeration (AR29).
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app.core.ratelimit import AttemptLimiter
from app.core.settings import cookie_secure_override, session_ttl_days
from app.store import models
from app.store.auth import (
    EmailTakenError,
    create_session,
    get_session_account,
    register_account,
    revoke_session,
    verify_login,
)

router = APIRouter()

#: Session cookie parameters (AR14).
COOKIE_NAME = "mythoscircle_session"
COOKIE_PATH = "/api"

#: Login attempt limiter: 5 failures per 15 minutes per (normalized email, ip).
_login_limiter = AttemptLimiter(max_attempts=5, window_seconds=900.0)
#: Register attempt limiter: 10 registrations per 15 minutes per ip — open
#: registration would otherwise let an attacker saturate the single worker
#: with argon2 hashing (review round 1, AD-13 single writer).
_register_limiter = AttemptLimiter(max_attempts=10, window_seconds=900.0)

#: The single generic auth failure envelope body (AR29).
_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Invalid credentials.",
)

#: The single generic session-required envelope body (AR29).
_SESSION_REQUIRED = HTTPException(
    status_code=401,
    detail="Authentication required.",
)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class AccountResponse(BaseModel):
    id: str
    email: str


def _account_response(account: models.Account) -> AccountResponse:
    return AccountResponse(id=account.id, email=account.email)


def _client_ip(request: Request) -> str:
    """The client address, honoring the loopback Caddy proxy.

    The service binds loopback-only (AR29); Caddy is the only reverse
    proxy and runs on the same host. When a request arrives from
    127.0.0.1 (or ::1) with an X-Forwarded-For header, that header is
    trusted — otherwise the socket peer is the client (single-tenant
    beta; a broader proxy-trust model is deferred).
    """
    peer = request.client.host if request.client is not None else "unknown"
    if peer in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def _session_max_age() -> int | None:
    """The cookie Max-Age derived from the server TTL — the cookie must
    not outlive (or underlive) the server-side session (review round 1).
    A TTL of 0 (never expire) uses a 10-year cookie lifetime."""
    ttl_days = session_ttl_days()
    if ttl_days == 0:
        return 3650 * 24 * 60 * 60  # ~10 years
    return ttl_days * 24 * 60 * 60


def _set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    """Attach the httpOnly, SameSite=Lax session cookie (AR14).

    ``secure`` starts as the scheme-derived flag: over TLS (Caddy front,
    uvicorn trusts the loopback proxy's X-Forwarded-Proto) the cookie is
    marked Secure; over plain-http dev (the Vite proxy) it is not, so the
    owner's dogfood flow works without a cert (spec-2.1 smoke find).
    ``MYTHOSCIRCLE_COOKIE_SECURE`` overrides it (spec-6-4): every
    deployed topology serves plain HTTP at the origin behind the TLS edge
    and the origin never learns the public scheme, so the operator
    declares the flag explicitly (AD-22, env-only). Unset = current
    behavior; dev stays untouched.
    """
    override = cookie_secure_override()
    if override is not None:
        secure = override
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=_session_max_age(),
        httponly=True,
        secure=secure,
        samesite="lax",
        path=COOKIE_PATH,
    )


def get_current_account(
    session_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> models.Account:
    """FastAPI dependency: resolve the session cookie to an account.

    Missing/invalid/expired/revoked -> the single generic 401 (AR29).
    """
    if session_token is None:
        raise _SESSION_REQUIRED
    account = get_session_account(session_token)
    if account is None:
        raise _SESSION_REQUIRED
    return account


@router.post("/api/auth/register", status_code=201)
def register(payload: RegisterRequest, response: Response, request: Request) -> AccountResponse:
    """Create an account; the password is argon2id-hashed, never stored."""
    if not _register_limiter.allowed(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many registration attempts.")
    try:
        account = register_account(payload.email, payload.password)
    except EmailTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _register_limiter.record(_client_ip(request))  # count successful registrations
    # Sign-up is sign-in: the DM lands authenticated (spec-2.1 dogfood flow).
    token, _session = create_session(account.id)
    _set_session_cookie(response, token, secure=request.url.scheme == "https")
    return _account_response(account)


@router.post("/api/auth/login")
def login(payload: LoginRequest, response: Response, request: Request) -> AccountResponse:
    """Verify credentials, set the session cookie, return the account.

    Every failure — unknown email, wrong password, rate-limit lockout —
    is the same generic 401 (AR29, no user enumeration). The limiter key
    uses the NORMALIZED email (the same form the store compares), so
    case-rotating ``DM@example.com`` / ``dm@example.com`` cannot mint
    fresh attempt budgets (review round 1). Only failures consume quota —
    five legit logins never lock a user out.
    """
    key = f"{payload.email.strip().lower()}|{_client_ip(request)}"
    if not _login_limiter.allowed(key):
        # Locked: still run the full verify so timing does not reveal it,
        # then return the identical generic 401.
        verify_login(payload.email, payload.password)
        raise _UNAUTHORIZED
    account = verify_login(payload.email, payload.password)
    if account is None:
        _login_limiter.record(key)  # failures-only (review round 1)
        raise _UNAUTHORIZED
    token, _session = create_session(account.id)
    _set_session_cookie(response, token, secure=request.url.scheme == "https")
    return _account_response(account)


@router.post("/api/auth/logout", status_code=204)
def logout(
    response: Response,
    session_token: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> None:
    """Revoke the session immediately and clear the cookie; idempotent."""
    if session_token is not None:
        revoke_session(session_token)
    response.delete_cookie(key=COOKIE_NAME, path=COOKIE_PATH)


@router.get("/api/auth/me")
def me(current: Annotated[models.Account, Depends(get_current_account)]) -> AccountResponse:
    """Return the authenticated account."""
    return _account_response(current)
