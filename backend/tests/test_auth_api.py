"""Auth API: register/login/logout/me + the session cookie + generic 401
(AR14/AR29, spec-1.5). Uses an https test server so the Secure cookie
round-trips.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> Any:
    """An https TestClient over the real app (conftest pins env + scratch
    DB). https is required: the session cookie is ``Secure`` (AR14), and
    httpx never sends Secure cookies over plain http."""
    from app.main import app

    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Any:
    """The auth limiters are module-global and keyed on the TestClient's
    fixed host ('testclient') — without a reset, one test's lockout or
    registration count leaks into the next (review round 1 isolation)."""
    from app.api.auth import _login_limiter, _register_limiter

    _login_limiter.reset()
    _register_limiter.reset()
    yield
    _login_limiter.reset()
    _register_limiter.reset()


def _register(
    client: Any, email: str = "dm@example.com", password: str = "correct-battery-horse"
) -> Any:
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )


def test_register_201_normalizes_email(client: Any) -> None:
    response = _register(client, "  Dm@Example.com ")
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "dm@example.com"
    assert "password" not in body  # never echoed
    assert body["id"]


def test_register_duplicate_409(client: Any) -> None:
    _register(client)
    response = _register(client, "DM@example.com")
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "conflict"


def test_register_invalid_422(client: Any) -> None:
    response = client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "short"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_login_sets_session_cookie(client: Any) -> None:
    _register(client)
    response = client.post(
        "/api/auth/login",
        json={"email": "dm@example.com", "password": "correct-battery-horse"},
    )
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "mythoscircle_session=" in set_cookie
    assert "HttpOnly" in set_cookie  # AR14
    assert "Secure" in set_cookie  # AR14
    assert "SameSite=lax" in set_cookie  # AR14 (httpx lowercases)
    assert "Path=/api" in set_cookie  # AR14
    # The cookie round-trips: /api/auth/me is now authenticated.
    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "dm@example.com"


def test_login_wrong_password_generic_401(client: Any) -> None:
    _register(client)
    response = client.post(
        "/api/auth/login",
        json={"email": "dm@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "unauthorized"
    assert body["message"] == "Invalid credentials."


def test_login_unknown_email_same_generic_401(client: Any) -> None:
    """Unknown email yields a byte-identical 401 to a wrong password —
    no user enumeration (AR29)."""
    response = client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
    )
    assert response.status_code == 401
    body = response.json()
    # The envelope may carry details=None; the code/message must be exact.
    assert body["code"] == "unauthorized"
    assert body["message"] == "Invalid credentials."


def test_login_locked_generic_401(client: Any) -> None:
    """5 failures lock the (email, ip) pair; the 6th — even with the
    correct password — is the same generic 401 (AR29)."""
    _register(client)
    for _ in range(5):
        client.post(
            "/api/auth/login",
            json={"email": "dm@example.com", "password": "bad"},
        )
    # The 6th attempt uses the correct password — still locked, same 401.
    response = client.post(
        "/api/auth/login",
        json={"email": "dm@example.com", "password": "correct-battery-horse"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_me_without_cookie_401(client: Any) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_logout_revokes_session(client: Any) -> None:
    _register(client)
    client.post(
        "/api/auth/login",
        json={"email": "dm@example.com", "password": "correct-battery-horse"},
    )
    assert client.get("/api/auth/me").status_code == 200
    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 204
    # The revoked session is rejected.
    assert client.get("/api/auth/me").status_code == 401


def test_logout_without_session_idempotent_204(client: Any) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# Review-regression tests (2026-08-30 review round 1)
# ---------------------------------------------------------------------------


def test_login_case_rotation_cannot_bypass_lockout(client: Any) -> None:
    """The limiter key is the NORMALIZED email — DM@example.com and
    dm@example.com share one attempt budget, so case rotation cannot mint
    fresh attempts (AR29, review round 1)."""
    _register(client)
    for _ in range(5):
        client.post(
            "/api/auth/login",
            json={"email": "DM@example.com", "password": "bad"},
        )
    # The correct password under the OTHER casing is still locked out.
    response = client.post(
        "/api/auth/login",
        json={"email": "dm@example.com", "password": "correct-battery-horse"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_successful_logins_do_not_lock_out(client: Any) -> None:
    """Only failures consume limiter quota — 5 successful logins in a row
    must never lock the user out (review round 1)."""
    _register(client)
    for _ in range(6):
        response = client.post(
            "/api/auth/login",
            json={"email": "dm@example.com", "password": "correct-battery-horse"},
        )
        assert response.status_code == 200
        # Clear the session between attempts so each is a fresh login.
        client.post("/api/auth/logout")


def test_login_cookie_max_age_matches_ttl(client: Any, monkeypatch: Any) -> None:
    """The cookie Max-Age derives from the server TTL — a TTL of 7 days
    yields a 7-day cookie, never the old hard-coded 30 (review round 1)."""
    from app.core.settings import SESSION_TTL_DAYS

    monkeypatch.setenv(SESSION_TTL_DAYS, "7")
    _register(client)
    response = client.post(
        "/api/auth/login",
        json={"email": "dm@example.com", "password": "correct-battery-horse"},
    )
    set_cookie = response.headers.get("set-cookie", "")
    assert "Max-Age=604800" in set_cookie  # 7 * 86400


def test_login_cookie_ttl_zero_long_lived(client: Any, monkeypatch: Any) -> None:
    """TTL=0 (never expire) yields a long-lived cookie — the browser must
    not drop it at 30 days (review round 1)."""
    from app.core.settings import SESSION_TTL_DAYS

    monkeypatch.setenv(SESSION_TTL_DAYS, "0")
    _register(client)
    response = client.post(
        "/api/auth/login",
        json={"email": "dm@example.com", "password": "correct-battery-horse"},
    )
    set_cookie = response.headers.get("set-cookie", "")
    assert "Max-Age=315360000" in set_cookie  # ~10 years


def test_register_rate_limited_per_ip(client: Any) -> None:
    """Registration is throttled per ip — an attacker cannot saturate the
    single worker with argon2 hashing (review round 1)."""
    for i in range(10):
        response = client.post(
            "/api/auth/register",
            json={"email": f"user{i}@example.com", "password": "password123"},
        )
        assert response.status_code == 201
    # 11th registration from the same ip is throttled.
    response = client.post(
        "/api/auth/register",
        json={"email": "user11@example.com", "password": "password123"},
    )
    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"


# ---------------------------------------------------------------------------
# Session cookie + plain-http (spec-2.1 smoke findings)
# ---------------------------------------------------------------------------


def test_register_sets_session_cookie_already_logged_in(client: Any) -> None:
    """Sign-up is sign-in: registration sets the session cookie (HttpOnly,
    Path=/api) and ``/api/auth/me`` succeeds IMMEDIATELY, without a separate
    login (spec-2.1 dogfood flow)."""
    response = _register(client, "signup@example.com")
    assert response.status_code == 201
    set_cookie = response.headers.get("set-cookie", "")
    assert "mythoscircle_session=" in set_cookie
    assert "HttpOnly" in set_cookie  # AR14
    assert "Path=/api" in set_cookie  # AR14
    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "signup@example.com"


def test_login_plain_http_cookie_not_secure() -> None:
    """Over plain http (the Vite dev proxy) the session cookie is NOT marked
    Secure, so the owner's dogfood flow works without a cert (spec-2.1 smoke
    find) — the session still authenticates."""
    from app.main import app

    with TestClient(app, base_url="http://testserver") as plain_client:
        _register(plain_client, "plain@example.com")
        response = plain_client.post(
            "/api/auth/login",
            json={"email": "plain@example.com", "password": "correct-battery-horse"},
        )
        assert response.status_code == 200
        set_cookie = response.headers.get("set-cookie", "")
        assert "mythoscircle_session=" in set_cookie
        assert "Secure" not in set_cookie  # plain http must not mark Secure
        assert plain_client.get("/api/auth/me").status_code == 200


def test_cookie_secure_override_true_over_plain_http(monkeypatch: Any) -> None:
    """COOKIE_TRUE_HTTP (spec-6-4): with MYTHOSCIRCLE_COOKIE_SECURE=true
    the cookie carries Secure even over the plain-http origin — the
    deployed stack behind the TLS edge (Caddy's plain-http listener gives
    uvicorn no public scheme to derive from), declared via the AD-22
    env-only lever, never the client."""
    from app.core.settings import COOKIE_SECURE_ENV

    monkeypatch.setenv(COOKIE_SECURE_ENV, "true")
    from app.main import app

    with TestClient(app, base_url="http://testserver") as plain_client:
        response = _register(plain_client, "secure-over-http@example.com")
        assert response.status_code == 201
        set_cookie = response.headers.get("set-cookie", "")
        assert "mythoscircle_session=" in set_cookie
        assert "Secure" in set_cookie  # the env override beats the http scheme


def test_cookie_secure_override_false_over_https(client: Any, monkeypatch: Any) -> None:
    """COOKIE_FALSE_HTTPS (spec-6-4): MYTHOSCIRCLE_COOKIE_SECURE=false
    suppresses the Secure flag even over TLS — the operator can pin the
    flag off (e.g. an interim LAN origin) without touching code."""
    from app.core.settings import COOKIE_SECURE_ENV

    monkeypatch.setenv(COOKIE_SECURE_ENV, "false")
    _register(client, "no-secure@example.com")
    response = client.post(
        "/api/auth/login",
        json={"email": "no-secure@example.com", "password": "correct-battery-horse"},
    )
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "mythoscircle_session=" in set_cookie
    assert "Secure" not in set_cookie  # the env override beats the https scheme


def test_cookie_secure_garbage_env_fails_boot(monkeypatch: Any) -> None:
    """A typo'd MYTHOSCIRCLE_COOKIE_SECURE fails at app CONSTRUCTION —
    the env_bool_optional ValueError — never as a per-request 500 from
    _set_session_cookie on every login/register (spec-6-4 review round
    1)."""
    from app.core.settings import COOKIE_SECURE_ENV

    monkeypatch.setenv(COOKIE_SECURE_ENV, "banana")
    with pytest.raises(ValueError, match="must be a boolean"):
        from app.main import create_app

        create_app()
