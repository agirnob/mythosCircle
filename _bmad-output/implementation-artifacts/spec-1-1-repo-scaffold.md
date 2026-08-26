---
title: 'Repo scaffold with conventions and green test harness (Epic 1, Story 1.1)'
type: 'feature'
created: '2026-08-25'
status: 'done'
baseline_commit: '0e45156aaa1f9399c640598c17bbc0d1859c809b'
review_loop_iteration: 1
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The repo contains only BMAD planning artifacts — no code. Every later Epic 1 story (store, queue, inference, auth, campaign CRUD, deploy) must land on a runnable, convention-consistent codebase with a green unit-test harness (AR1, AR22).

**Approach:** Scaffold a monorepo: a uv-managed Python 3.12 FastAPI backend under `backend/` with a minimal health endpoint plus the shared convention primitives (ULID IDs, UTC ISO-8601, error envelope, cursor pagination); a Vue 3.5 + Vite + TypeScript + Pinia frontend under `frontend/`; deploy assets under `deploy/` (config, Caddyfile, systemd unit, backup cron + restore script); and a green harness — backend pytest, frontend vitest — with deterministic fixtures.

## Boundaries & Constraints

**Always:**
- Conventions: ULID IDs; UTC ISO-8601 timestamps; error envelope `{code, message, details?}`; cursor pagination; 4xx = user error (never a state change), 5xx = server error; `snake_case` Python, `kebab-case` API routes, `camelCase` TS/Vue.
- Python 3.12 pinned via uv (host python is 3.14); stack per `stack.md`: FastAPI 0.14x, Pydantic v2, SQLAlchemy 2, Vue 3.5, Vite 8.x, TypeScript 7.x, Pinia 4.x.
- Tooling is the project standard (documented in root README + Makefile): backend ruff (lint+format) + mypy (typecheck); frontend eslint + prettier (lint+format) + vue-tsc (typecheck); tests = backend pytest + frontend vitest.
- Layering seams exist but are empty: `api/` orchestrates, `pipeline/` proposes, `store/` is the sole writer, `providers/` + `media/` are leaves. No generation, queue, auth, or campaign logic yet.

**Ask First:**
- Any dependency beyond the scaffold minimum named in the Code Map.
- Any deviation from the versions pinned in `stack.md`.
- Any secret placed in `deploy/config.toml` or the client build (env vars only, AD-22).

**Never:**
- Implementing Story 1.2–1.6 logic (store commits, queue, inference, auth, campaign CRUD) — scaffold only.
- UI e2e tests (owner dogfoods in beta, AR22).
- Touching `_bmad-output/`, `_bmad/`, or `.agents/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Health check | `GET /api/health` | 200, JSON body `status: "ok"` | N/A |
| Unknown route | `GET /api/does-not-exist` | 404 in the error envelope | `code` machine-readable, `message` human-readable |
| Server fault | unhandled exception | 5xx in the error envelope | generic message, no stack/SQL internals leaked |

</frozen-after-approval>

## Code Map

- `_bmad-output/specs/spec-mythosCircle/conventions.md` -- canonical cross-cutting conventions (read-only source of truth)
- `_bmad-output/specs/spec-mythosCircle/stack.md` -- pinned stack versions (read-only)
- `_bmad-output/planning-artifacts/epics.md:156-174` -- Story 1.1 acceptance criteria (read-only source)
- `_bmad-output/implementation-artifacts/epic-1-context.md` -- compiled Epic 1 context (read-only)
- `backend/pyproject.toml` + `backend/.python-version` -- uv project: 3.12 pin, deps, inline ruff+mypy+pytest config
- `backend/app/core/{ids,time,errors,pagination}.py` -- convention primitives (tested)
- `backend/app/main.py` + `backend/app/api/` -- app factory, `GET /api/health`
- `Makefile` + `README.md` -- the single standard entry points (setup/test/lint/format/typecheck); `.gitignore` extended for venv, node_modules, dist, caches, `data/`
- `backend/tests/` -- pytest, deterministic fixtures
- `frontend/package.json` + `vite.config.ts` + `tsconfig.json` + eslint/prettier configs -- Vue 3.5 / Vite 8 / TS 7 / Pinia 4 + vitest; scripts: dev, build, test, lint, format, typecheck
- `frontend/src/` -- `main.ts`, `App.vue`, `env.d.ts`, one trivial Pinia store, one vitest test
- `deploy/{config.toml,Caddyfile,mythoscircle.service,backup.sh,restore.sh,backup.cron}` -- deploy starting points (no secrets)


## Tasks & Acceptance

**Execution:**
- [x] `backend/pyproject.toml` + `backend/.python-version` -- uv project, `requires-python = ">=3.12,<3.13"`, deps: fastapi, pydantic, sqlalchemy, uvicorn, python-ulid; dev: pytest, httpx, ruff, mypy -- pins toolchain + conventions per stack.md
- [x] `backend/app/core/{__init__,ids,time,errors,pagination}.py` -- ULID factory; UTC ISO-8601 `now()`; error-envelope model + FastAPI handlers (4xx/5xx, no internal leak); cursor encode/decode helper -- the conventions as tested primitives
- [x] `backend/app/main.py` + `backend/app/api/{__init__,health}.py` -- app factory wiring error handlers; kebab-case `GET /api/health` returning `{"status": "ok"}` -- first runnable HTTP surface
- [x] `backend/app/{store,pipeline,providers,media}/__init__.py` -- empty seams stating the layering rule (store is sole writer) -- structural invariant from day one
- [x] `backend/tests/` -- pytest for ids format, UTC timestamps, pagination round-trip, envelope shape on 404 + forced 500, health endpoint via httpx ASGI transport -- proves harness green + conventions hold
- [x] `frontend/package.json` + `vite.config.ts` + `tsconfig.json` + eslint + prettier configs -- scripts dev/build/test/lint/format/typecheck -- frontend toolchain per AGENTS.md choices
- [x] `frontend/src/{main.ts,App.vue,env.d.ts}` + `frontend/src/stores/` -- minimal mount + one trivial Pinia store + one vitest test -- frontend harness green
- [x] `deploy/{config.toml,Caddyfile,mythoscircle.service,backup.sh,restore.sh,backup.cron}` -- Caddy TLS + loopback proxy; systemd unit; nightly snapshot cron + restore script; no secrets -- AR1 starting points (wired in 1.7, proven in Epic 6)
- [x] `Makefile` + `README.md` + `.gitignore` -- `setup`/`test`/`lint`/`format`/`typecheck` targets fanning out to backend + frontend; gitignore for venv/node_modules/dist/caches/data -- the AC's "project's standard test command"

**Acceptance Criteria:**
- Given a fresh clone with no local state, when `make setup && make test` runs, then backend pytest and frontend vitest both pass.
- Given a fresh clone, when `make lint && make typecheck` run, then ruff + mypy and eslint + prettier + vue-tsc all pass.
- Given the tree, when inspected, then it contains `backend/app/{api,core,store,pipeline,providers,media}`, `backend/tests/`, `frontend/src/`, and `deploy/` (config.toml, Caddyfile, systemd unit, backup cron + restore script).
- Given the convention primitives, when exercised, then ULID IDs are 26-char Crockford, timestamps are UTC ISO-8601, and 4xx/5xx responses match `{code, message, details?}` with 4xx never changing state.

## Spec Change Log

- 2026-08-25 (Ask-First, human-approved): stack.md pins TypeScript 7.x, but TS 7 (native compiler) has a stub JS API — vue-tsc 3.3.11 crashes on it (`ERR_PACKAGE_PATH_NOT_EXPORTED`) and typescript-eslint 8.68 requires peer `typescript <6.1.0` (hard ERESOLVE on a fresh `npm install`), so the spec-mandated lint/typecheck toolchain cannot run on 7.x. Amended: frontend pins `typescript@^6.0.3`, the highest version compatible with the mandated toolchain, as the effective frontend pin per stack.md's own "the code owns this once it exists" note. Avoids: fresh-clone `npm install` / typecheck failure (AC "fresh clone lint + typecheck green"). KEEP: vue-tsc + eslint + prettier as the standard frontend typecheck/lint; revisit the pin if the toolchain gains native-TS-7 support.

## Review (loop 1, 2026-08-25)

**Method:** the three parallel review layers (blind-hunter, edge-case-hunter, verification-gap) were executed inline in-session: subagent spawns failed systemically (local `llama.cpp` provider closed the completion stream before `finish_reason` on 3+ attempts). Context-free property was lost — treat findings as one-reviewer work, not independent lenses.

**Accepted & fixed (all re-verified green):**
- [major] `deploy/restore.sh` deleted the DB but left stale `$DB-wal`/`$DB-shm` sidecars — SQLite could replay a stale WAL onto the restored DB. Fix: `rm -f "$DB" "$DB-wal" "$DB-shm"`.
- [minor] `deploy/backup.sh` failed under `set -e` when the DB did not exist yet (first nightly run before the app creates state). Fix: clean skip.
- [minor] `errors.py` — implicit 4xx branches 408/418/451 fell through to `code="error"` / `"Request failed."`. Fix: machine-readable codes + default messages added.
- [minor] `deploy/Caddyfile` had no SPA fallback — deep links would 404 once routes exist. Fix: `file_server @notApi { try_files {path} /index.html }`.
- [minor] `test_time.py::test_now_is_monotonic_instant` compared two wall-clock reads — flaky under an NTP step. Fix: freshness assertion with 5s tolerance.
- [minor] verification gap: 409/429 envelope codes untested. Fix: 4xx parametrize extended to `[401, 403, 405, 408, 409, 418, 429]` (backend suite 16 → 20).

**Accepted, no action (documented deferrals):** systemd unit hardening (`ProtectSystem`, `PrivateTmp`, …), cron log-dir ownership, `restore.sh` confirmation prompt — deploy starting points wired/proven in Story 1.7; 405 handler drops the allowed-methods list (envelope still convention-valid); no favicon/meta in `index.html` (cosmetic); no `make dev` target (README documents manual commands); app instance built at import (factory exists for future isolation, no state yet). Deploy scripts additionally pass `bash -n` syntax check.

**Re-verification:** `bash -n` both scripts; `make lint` clean (ruff + eslint); `make typecheck` clean (mypy 18 files + vue-tsc); `make test` green — 20 backend + 1 frontend.

## Design Notes

- **Python pin:** host `python3` is 3.14.7; pin 3.12 via `.python-version` + `requires-python = ">=3.12,<3.13"` and run every standard command under `uv run` so the provisioned interpreter is used on any machine (operator rig or GPU VPS).
- **Error envelope:**
  ```python
  class ErrorEnvelope(BaseModel):
      code: str                 # machine-readable, e.g. "not_found"
      message: str              # human-readable
      details: dict | None = None
  ```
  Handlers map FastAPI's 404/RequestValidationError → 4xx envelope; a catch-all handler returns a generic 500 envelope (never stack traces or SQL).

## Verification

**Commands:**
- `make test` -- expected: backend pytest + frontend vitest, all pass
- `make lint && make typecheck` -- expected: zero errors from ruff, mypy, eslint, prettier --check, vue-tsc
- Fresh-clone proof: `TMP=$(mktemp -d) && git archive HEAD | tar -x -C $TMP && make -C $TMP setup && make -C $TMP test` -- expected: pass (proves no untracked local state is required)
- `uv run --directory backend pytest -q` -- expected: all backend tests pass directly

## Suggested Review Order

**Application substrate**

- Entry point: app factory wiring error handlers + health router in one place.
  [`main.py:13`](../../backend/app/main.py#L13)

- First runnable HTTP surface; kebab-case route convention proven on day one.
  [`health.py:5`](../../backend/app/api/health.py#L5)

- Sole-writer invariant declared as an empty seam.
  [`__init__.py:1`](../../backend/app/store/__init__.py#L1)

- Proposes-never-writes seam (AD-1).
  [`__init__.py:1`](../../backend/app/pipeline/__init__.py#L1)

**Convention primitives**

- Machine-readable 4xx code map; loop 1 closed the implicit 408/418/451 branches.
  [`errors.py:19`](../../backend/app/core/errors.py#L19)

- 4xx envelope vs 5xx generic split; no internals leak.
  [`errors.py:75`](../../backend/app/core/errors.py#L75)

- ULID factory: 26-char Crockford, time-ordered.
  [`ids.py:13`](../../backend/app/core/ids.py#L13)

- UTC ISO-8601 rendering with the Z designator.
  [`time.py:10`](../../backend/app/core/time.py#L10)

- Opaque cursor encode/decode; ULID payload validated on decode.
  [`pagination.py:20`](../../backend/app/core/pagination.py#L20)

**Deploy starting points (wired in 1.7)**

- Single loopback FastAPI process behind Caddy (AD-10).
  [`mythoscircle.service:13`](../../deploy/mythoscircle.service#L13)

- SPA fallback + API/WebSocket reverse proxy.
  [`Caddyfile:11`](../../deploy/Caddyfile#L11)

- WAL-safe online snapshot; clean skip when the DB does not exist yet.
  [`backup.sh:14`](../../deploy/backup.sh#L14)

- Stale WAL/SHM sidecars removed before restore (loop 1 major fix).
  [`restore.sh:20`](../../deploy/restore.sh#L20)

- AD-22: no secrets — environment variables only.
  [`config.toml:1`](../../deploy/config.toml#L1)

**Standard entry points**

- setup/test/lint/format/typecheck fanning out to both toolchains.
  [`Makefile:20`](../../Makefile#L20)

- Setup instructions + the standard command table.
  [`README.md:21`](../../README.md#L21)

**Harness & supporting**

- I/O matrix: health, 404 envelope, forced 500 with no internals leaked.
  [`test_api.py:32`](../../backend/tests/test_api.py#L32)

- 4xx envelope parametrize extended by loop 1 (408/409/418/429).
  [`test_api.py:84`](../../backend/tests/test_api.py#L84)

- Loop 1: wall-clock monotonic test replaced with flake-proof freshness check.
  [`test_time.py:18`](../../backend/tests/test_time.py#L18)

- Frontend toolchain scripts; TS 6.0.3 as the effective pin.
  [`package.json:6`](../../frontend/package.json#L6)

- One trivial Pinia store proving vitest is green.
  [`counter.test.ts:5`](../../frontend/src/stores/counter.test.ts#L5)

- venv/node_modules/dist/caches/data never enter git.
  [`.gitignore:5`](../../.gitignore#L5)
