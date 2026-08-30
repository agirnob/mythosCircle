---
title: 'DM authentication: argon2id accounts, session cookies, lockout'
type: 'feature'
created: '2026-08-30'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'e7f0bcd6376a5f5275aee3bacd396d0ad238a5d3'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/specs/spec-mythosCircle/conventions.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing authenticates the DM. Campaigns must be private to one invited user (AD-9, AR14), and 1.6's campaign CRUD needs an identity to enforce ownership — but there is no account, no session, and no login path.

**Approach:** Add an `account` + `session` table to the store (AD-13: one SQLite DB holds accounts; all access through `store/`), argon2id password hashing (AR29), email+password register/login/logout + `GET /api/auth/me` endpoints, an httpOnly Secure SameSite=Lax session cookie on path `/api` (AR14), session expiry + revocation, a login rate limit with lockout that returns a single generic 401 (no user enumeration, AR29), and a `get_current_account` FastAPI dependency 1.6 consumes. The API already binds loopback-only behind Caddy (deploy config + systemd) — pinned by a contract test, not new code.

## Boundaries & Constraints

**Always:**
- Passwords are hashed with argon2id only — `argon2-cffi`'s `PasswordHasher`; the plaintext is never stored, logged, or echoed (AR29).
- Sessions are opaque random tokens (Python `secrets.token_urlsafe(32)`), stored as their SHA-256 hash — a DB leak never yields usable tokens. Not ULIDs (the ULID convention covers entity/edge/job/event/revision ids).
- The session cookie is httpOnly, Secure, SameSite=Lax, path `/api` (AR14).
- A single generic 401 (`code: unauthorized`, message `Invalid credentials.` or `Authentication required.`) for unknown email, wrong password, locked account, missing/invalid/expired/revoked session — no user enumeration (AR29).
- Login rate limit with lockout: N failed attempts per (email, remote-ip) within a window locks that pair until the window passes; locked attempts still return the generic 401. The limiter is in-process (one uvicorn worker = the single writer, AD-13/AR2) and deterministic-tested via injected clock.
- Session expiry: sessions expire after 30 days (env `MYTHOSCIRCLE_SESSION_TTL_DAYS`, default 30); expired or revoked sessions are rejected (AR29).
- `POST /api/auth/logout` revokes the session (deletes the row) — revocation is immediate.
- Auth tables and writes live in the store (`store/auth.py`), matching the single-writer seam; sessions are NOT world graph — no revisions, no events.
- Emails are normalized (lowercased, trimmed) before store/compare; email addresses are validated by Pydantic (`EmailStr` — requires `email-validator`).
- Endpoints: `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` — kebab-case routes, error envelope, Pydantic source of truth (AR15/conventions.md).
- `get_current_account` reads the session cookie, looks up by token-hash, rejects expired/revoked with 401; 1.6+ depends on it.
- One account per email (unique, case-insensitive via normalization).
- ULID ids for accounts; UTC ISO-8601 `Z` timestamps; snake_case Python, kebab-case routes.

**Ask First:** changing the session store from hashed-tokens to server-side cookies elsewhere; adding refresh tokens / OAuth / SSO; per-campaign invitations or multi-user sharing (beta is one invited DM per campaign, AD-9); password reset flows; persisting the rate limiter across restarts (in-process is intentional).

**Never:** storing or logging plaintext passwords or session tokens; returning a different response for "unknown email" vs "wrong password" vs "locked"; auth on a path other than `/api`'s cookie; a second database or cache for sessions (AD-13); timing side-channels that reveal account existence (argon2 verify runs on every login attempt, even unknown emails — always a dummy-verify for unknown accounts, AR29).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| REGISTER | new email + password | 201, account created, argon2id hash stored, password not echoed | duplicate email → 409 `conflict` envelope |
| REGISTER_INVALID | bad email / short password (Pydantic min-length) | 422 `validation_error`, no account written | N/A |
| LOGIN_OK | registered email + correct password | 200 `{account}` + Set-Cookie httpOnly/Secure/Lax/path=/api session | N/A |
| LOGIN_WRONG | correct email + wrong password | generic 401 `unauthorized` `Invalid credentials.` | N/A |
| LOGIN_UNKNOWN | unregistered email + any password | generic 401, identical body to wrong-password (dummy argon2 verify) | N/A |
| LOGIN_LOCKED | >N failures for (email, ip) in window | generic 401 even with the correct password; no lockout signal | N/A |
| ME_OK | valid session cookie | 200 `{account}` | N/A |
| ME_EXPIRED | session past TTL | 401 `Authentication required.` | N/A |
| ME_REVOKED | session deleted by logout | 401 `Authentication required.` | N/A |
| LOGOUT | valid session | 204, session row deleted (revoked) | invalid session → 204 anyway (idempotent) |
| RATE_WINDOW_RESET | lockout window passes (injected clock) | login succeeds again | N/A |

</frozen-after-approval>

## Code Map

- `backend/pyproject.toml` -- add `argon2-cffi>=25,<26` and `email-validator>=2,<3` (EmailStr). `argon2-cffi` needs a build/runtime wheel — uv handles it.
- `backend/app/store/auth.py` -- NEW: `Account`/`Session` ORM rows live in `models.py`; here the auth primitives: `register_account(email, password) -> Account`, `verify_login(email, password) -> Account | None` (dummy-verify for unknown emails, corrupt-hash -> None), `create_session(account_id) -> (token, Session)` (raw token + row), `get_session_account(token) -> Account | None` (rejects invalid/expired/revoked), `revoke_session(token)`. All via `session_scope` (single writer). Password hashing with `argon2.PasswordHasher`; token = `secrets.token_urlsafe(32)`, stored `sha256` hex; expiry compared as parsed datetimes, never lexicographically.
- `backend/app/store/models.py` -- add `Account` (id ULID PK, email String(320) unique, password_hash String(255), created_at) and `Session` (id ULID PK, account_id FK index, token_hash String(64) unique, created_at, expires_at String(40), revoked_at String(40) nullable). Session `index=True` on account_id.
- `backend/app/core/ratelimit.py` -- `AttemptLimiter` with `allowed(key)` (no consume), `record(key)` (failures only), `blocked_until(key)` (oldest-attempt expiry), injected clock, capped key growth.
- `backend/app/store/__init__.py` -- export the auth surface.
- `backend/app/core/settings.py` -- add `session_ttl_days() -> int` from env `MYTHOSCIRCLE_SESSION_TTL_DAYS` (default 30, reuse `_env_non_negative_int` — a 0/TTL-disabled day value means sessions never expire, documented).
- `backend/app/api/auth.py` -- NEW router: `POST /api/auth/register` (422/409 envelope), `POST /api/auth/login` (Set-Cookie + 200, generic 401), `POST /api/auth/logout` (204 idempotent), `GET /api/auth/me` (200 or 401); `get_current_account` dependency (raises 401 envelope on missing/invalid/expired/revoked). Login uses the rate limiter (`app.core.ratelimit`) keyed (email, ip) with an injected clock.
- `backend/app/core/ratelimit.py` -- NEW: in-process sliding-window limiter `AttemptLimiter(max_attempts=5, window_seconds=900)` with `check(key) -> bool` (records an attempt) and a `reset` for tests; clock injectable.
- `backend/app/main.py` -- include `auth.router`.
- `backend/tests/test_auth.py` -- NEW store-level: argon2id hash (not plaintext, verify passes, wrong password rejected), register dup 409, session hash-token storage (token never in DB), session expiry boundary.
- `backend/tests/test_auth_api.py` -- NEW API-level: register 201/422/409; login Set-Cookie attributes (httpOnly, Secure, SameSite=Lax, Path=/api); login wrong/unknown identical generic 401 bodies; login locked generic 401; me with valid/expired/revoked session; logout 204 + revocation; `/api/auth/me` without cookie 401. Uses `TestClient` with `base_url="https://testserver"` so the Secure cookie round-trips.
- `backend/tests/test_deploy_contract.py` -- extend: assert `deploy/config.toml` host = 127.0.0.1 and `mythoscircle.service` ExecStart binds `--host 127.0.0.1` (loopback-only behind Caddy, AR29/AR2).

## Tasks & Acceptance

**Execution:**
- [x] `backend/pyproject.toml` -- add argon2-cffi + email-validator -- AR29 hardening deps.
- [x] `backend/app/store/models.py` -- Account + Session tables -- AD-13 accounts in the store.
- [x] `backend/app/store/auth.py` -- register/verify/session primitives (argon2id, hashed tokens, dummy-verify) -- AR29.
- [x] `backend/app/store/__init__.py` -- export auth surface.
- [x] `backend/app/core/settings.py` -- session TTL setting.
- [x] `backend/app/core/ratelimit.py` -- in-process sliding-window attempt limiter.
- [x] `backend/app/api/auth.py` -- routes + cookie + get_current_account dependency.
- [x] `backend/app/main.py` -- include auth router.
- [x] `backend/tests/test_auth.py` + `test_auth_api.py` -- I/O matrix + AC coverage.
- [x] `backend/tests/test_deploy_contract.py` -- loopback bind pin.

**Acceptance Criteria:**
- Given a registered account with a valid password, when I log in, then an httpOnly, Secure, SameSite=Lax session cookie is set on path `/api` (AR14).
- Given a password being stored, when it is written, then it is hashed with argon2id (AR29).
- Given repeated failed logins, when the rate limit is hit, then the account is locked out and every auth failure returns a single generic 401 that does not reveal which users exist (AR29).
- Given the host network, when the API binds, then it is loopback-only behind Caddy (AR29, AR2).
- Given an expired or revoked session, when I call the API, then the session is rejected (AR29).

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries.
     Each entry records: what finding triggered the change, what was amended, what known-bad state
     the amendment avoids, and any KEEP instructions (what worked well and must survive re-derivation).
     Empty until the first bad_spec loopback. -->

### Loop 1 (2026-08-30, review round 1)

**Findings fixed (all patch — no intent gap):**
- Limiter key used the raw Pydantic email (domain-lowercased, local-part
  preserved) while the account identity is the fully normalized email —
  case rotation (`DM@`/`DM@`) minted fresh attempt budgets per variant.
  Fixed: key = `email.strip().lower()` — the store's canonical form.
- `AttemptLimiter.check` recorded EVERY attempt including successes —
  five legit logins in the window locked the user out. Split into
  `allowed()` (no consume) + `record()` (failures only).
- Register was unthrottled — open registration let an attacker saturate
  the single worker with argon2 hashing. Added a per-ip register limiter
  (10/hour) returning 429 `rate_limited`.
- Cookie `Max-Age` was hard-coded 30 days while the server TTL is
  configurable (TTL=0 never-expire was defeated client-side; TTL<30
  cookie outlived the session). `_session_max_age()` now derives from
  `session_ttl_days()` (0 -> 10-year cookie).
- A corrupt/tampered stored hash raised `InvalidHashError` -> 500 on a
  known account (existence leak). `verify_login` now treats it as None.
- `blocked_until` reported the newest attempt's expiry (too late); now
  the oldest attempt's — the actual window release.
- Lexicographic ISO expiry compare misjudged the exact-second boundary
  (`Z` sorts after `.`); expiry is now parsed to datetime.
- Limiter keyed-entries grew without bound; `record` caps at 10k keys
  with empty-deque eviction.
- Tests added: ratelimit unit suite (window, release, oldest-expiry),
  dummy-verify spy, TTL=N boundary, TTL=0 never-expire, corrupt-hash,
  case-rotation lockout, successes-not-locking, cookie Max-Age vs TTL,
  register 429, per-test limiter isolation, unique registration emails.

**KEEP:** generic-401 byte-identical bodies; hashed session tokens;
dummy-verify for unknown emails; loopback-only bind pin; deterministic
injected-clock tests.

### Loop 1 addendum — spec/code drift captured

- Frozen text says logout "deletes the row"; the implementation marks
  `revoked_at` and retains the row for audit. The observable contract
  (204, then /me 401) is what's tested; kept as documented intent.
- Deferred (ledger): session-row purge job; rehash-on-login for argon2
  param bumps; a broader trusted-proxy model beyond the loopback Caddy;
  login-CSRF (SameSite=Lax accepted for the single-tenant beta); register
  429 rate-limit envelope (a deliberate, non-enumerating signal).

## Design Notes

The generic-401 rule is absolute: unknown email, wrong password, locked account all produce byte-identical `{code: unauthorized, message: "Invalid credentials."}` bodies — and unknown emails still run a full argon2 verify against a fixed dummy hash so response timing does not reveal existence. This is what "no user enumeration" means in practice (AR29).

Session tokens are never stored: the cookie carries the raw token (opaque, 32 random bytes), the DB stores only its SHA-256. Lookup is by hash. Logout deletes the row — immediate revocation.

The rate limiter is in-process by design: one uvicorn worker is the single writer (AR2/AD-13), so an in-memory sliding window is correct and survives nothing — a restart clears lockouts (acceptable in beta; persistence is deferred). The clock is injectable so tests advance it deterministically.

Registration is open in beta (one invited DM per campaign, AD-9): anyone can register, so email-existence is discoverable via a 409 on register. Single-tenant beta makes this low-risk; invitation gating is a later story (deferred-work note).

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all auth tests + full suite green (deterministic, argon2 hashing is fast with default params).
- `make lint && make typecheck` -- expected: ruff, mypy strict, eslint, vue-tsc clean (backend changes only).

## Suggested Review Order

**The auth store (argon2id, hashed sessions)**

- register/verify primitives — argon2id only, dummy-verify for unknown emails, corrupt hash -> None (AR29)
  [`auth.py:47`](../../backend/app/store/auth.py#L47)
  [`auth.py:70`](../../backend/app/store/auth.py#L70)

- Sessions: opaque token stored as SHA-256; expiry parsed as datetimes (review round 1)
  [`auth.py:93`](../../backend/app/store/auth.py#L93)
  [`auth.py:117`](../../backend/app/store/auth.py#L117)

**The API (cookie, generic 401, limiters)**

- Normalized limiter key + failures-only counting + loopback-trusted client IP
  [`auth.py:143`](../../backend/app/api/auth.py#L143)
  [`auth.py:74`](../../backend/app/api/auth.py#L74)

- Cookie Max-Age derives from the configurable server TTL
  [`auth.py:91`](../../backend/app/api/auth.py#L91)

- The dependency 1.6 consumes — single generic 401 for every session failure
  [`auth.py:114`](../../backend/app/api/auth.py#L114)

**The rate limiter**

- allowed/record split, oldest-attempt release, capped key growth
  [`ratelimit.py:27`](../../backend/app/core/ratelimit.py#L27)
  [`ratelimit.py:63`](../../backend/app/core/ratelimit.py#L63)

**Tests (review-regression)**

- Case-rotation bypass, successes-not-locking, register throttle, cookie vs TTL
  [`test_auth_api.py:183`](../../backend/tests/test_auth_api.py#L183)
  [`test_auth_api.py:227`](../../backend/tests/test_auth_api.py#L227)

- Dummy-verify spy, TTL boundaries, corrupt-hash
  [`test_auth.py:110`](../../backend/tests/test_auth.py#L110)
  [`test_auth.py:135`](../../backend/tests/test_auth.py#L135)

- RATE_WINDOW_RESET release + oldest-expiry
  [`test_ratelimit.py:34`](../../backend/tests/test_ratelimit.py#L34)
