---
title: 'Guided build-in: frontend foundation + the build_in job kind'
type: 'feature'
created: '2026-08-30'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'b087b84'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/specs/spec-mythosCircle/conventions.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegottates">

## Intent

**Problem:** The DM's world exists as a campaign row (1.6) but there is no surface to *bring it in*: the frontend is a Vite counter scaffold with no router, no API client, no auth wiring, and no job progress channel. Epic 2's first move is the guided build-in — the DM walks her world seed plus key places, factions, key figures, and free-form notes into a queued generation job (FR6, AD-19), and the job's position + progress stay visible without blocking the screen (AR5, inversion guarantee #3).

**Approach:** Land the frontend foundation the rest of the product builds on (vue-router, OpenAPI-derived typed API client, error-envelope handling, auth wiring, WS client, Pinia stores) *and* the build-in flow itself: a campaign-gated screen that pre-fills the AR27 seed fields (title, description, theme, custom lore) read-only from the campaign, collects the four build-in sections, and enqueues a new `build_in` job kind whose lifecycle renders live (queued → running → terminal, queue_position + progress via REST + WS, AD-17). The `build_in` runner itself is Story 2.3's Core-First Two-Wave pipeline — 2.1 ships the kind, the enqueue contract, and the full observable job lifecycle, mirroring how 1.3 shipped the queue before 1.4's worker and how 1.4 fails media kinds until Epic 4.

## Boundaries & Constraints

**Always:**
- The build-in screen is session-gated: no valid session → the generic 401 envelope and a redirect to `/login`. Every API call carries the httpOnly session cookie (SameSite=Lax, path `/api`) via `credentials: 'same-origin'` — no token storage in the client (AR14, AD-9).
- The four build-in section inputs are free-form (the DM never learns a schema — FR6): `places`, `factions`, `key_figures` (each a non-empty list of 1–2000-char trimmed strings, ≤100 entries) and `notes` (a single string). At least one non-blank entry across all four is required; an all-blank submission is a 422. Whitespace-only entries are trimmed away before validation.
- The AR27 seed fields display pre-filled **read-only** from `GET /api/campaigns/{id}` (title, description, theme, custom lore). Editing the seed is the 1.6 PATCH surface already shipped — the build-in does not duplicate it. Theme + custom lore flow into the eventual prompt from the campaign row, not from this payload (AR27, AD-16's hard-truths rule).
- Enqueue: `POST /api/jobs` with `kind: "build_in"` and the payload above, idempotent by optional `job_id` (409 on duplicate, existing 1.3 contract), budgets defaulting from env (AR21). The queue accepts the kind; the response carries `queue_position` (AD-3).
- **Worker boundary (documented, not a stub):** until Story 2.3 wires the build-in runner, the 1.4 worker fails a claimed `build_in` job with a clear error (`"job kind 'build_in': the build-in runner lands in Story 2.3"`), broadcasting `job_failed` + `queue_changed` so the FIFO keeps flowing and the screen shows a real terminal state with its position — the same fail-not-wedge convention 1.4 applied to media kinds. The AC's observable guarantees (enqueued, screen not blocked, position + progress visible over REST + WS, terminal state reached) all hold in 2.1; digestion-into-graph is 2.3's AC.
- Frontend types derive from the backend's OpenAPI (AD-17): `openapi-typescript` regenerates `frontend/src/api/schema.ts` from the running backend's `/openapi.json` via an npm script; the generated file is committed and regenerated when backend contracts change. No hand-maintained mirror of Pydantic schemas.
- Error envelope `{code, message, details?}` is parsed into a typed `ApiError`; 4xx = never a state change, 5xx = server error (conventions.md). A 401 anywhere clears the auth store and routes to `/login` (single generic 401, AR29).
- Pinia stores own state: `auth` (account, login/register/logout/me), `campaigns` (list + current), `jobs` (per-campaign queue view, upserted by REST list/poll and WS messages). No component fetches API state directly into local component state for these domains (AD-20 pattern).
- WS client: one reconnecting `WebSocket` per open campaign to `/api/ws/jobs?campaign_id=`, dispatching AD-17 message types (`job_progress|job_done|job_failed|queue_changed`) into the jobs store; reconnect with bounded backoff; drop on logout/navigation away.
- Routes: `/login`, `/register`, `/` (campaign list landing), `/campaigns/:id/build-in`. Router guard resolves `/api/auth/me` before entering authed routes; unauthenticated → `/login`.
- Naming: kebab-case routes, camelCase TS/Vue, ULID ids (conventions.md).

**Ask First:** campaign create/edit/delete UI in 2.1 (the 1.6 API exists; the build-in only *consumes* an existing campaign — list view included for navigation, creation surface deferred); a build-in submit that also PATCHes the seed (editing is 1.6's surface); draft persistence of build-in inputs before submit (local UX nicety, not AC'd).

**Never:** storing the seed fields in the `build_in` payload (they live on the campaign — AR27); a second hand-written TS mirror of Pydantic schemas (always `gen:api`); token/session storage in localStorage or the client bundle (AD-9, AR14); blocking the screen on job completion (AR5); a worker that skips `build_in` jobs silently or wedges the FIFO (AD-3); raw SQL or world-state writes outside `store/` (AD-1).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| BUILDIN_OPEN | authed session, own campaign id | 200; seed fields pre-filled read-only, four empty section inputs | 401 no session; 404 unknown/foreign campaign |
| BUILDIN_SUBMIT | ≥1 non-blank section | 201 `{job}` incl. id, kind `build_in`, state `queued`, `queue_position` ≥ 1; lifecycle renders live | N/A |
| BUILDIN_ALL_BLANK | all four sections blank/whitespace | 422 before enqueue; zero rows written | N/A |
| BUILDIN_ENTRY_TOO_LONG | a single entry > 2000 chars | 422; zero rows written | N/A |
| BUILDIN_TOO_MANY | a list > 100 entries | 422; zero rows written | N/A |
| BUILDIN_IDEMPOTENT | same `job_id` twice | 201 then 409 naming the existing job (1.3 contract); one row, never double-enqueued | N/A |
| BUILDIN_UNAUTH | no/invalid session | generic 401 envelope; client redirects to `/login` | N/A |
| BUILDIN_QUEUE_CAP | campaign pending count at cap | 409; client renders the cap error | N/A |
| BUILDIN_LIFECYCLE | claimed by worker before 2.3 | queued → running → `job_failed` with "runner lands in Story 2.3"; WS `job_progress`/`job_failed`/`queue_changed` seen live; queue flows on | N/A |
| WS_RECONNECT | socket drops mid-view | reconnect with backoff; jobs store re-syncs via `GET /api/jobs` on reconnect | N/A |
| WS_UNAUTH | socket without session (edge) | server closes; client treats as auth failure → `/login` | N/A |
| API_401_ANYWHERE | any authed call returns generic 401 | auth store cleared, redirect `/login`, no partial UI state | N/A |
| API_5XX | provider/backend error | error envelope surfaced in-place; screen never blocks | N/A |

## Code Map

- `frontend/package.json` -- add `vue-router` (dependency) and `openapi-typescript` (devDependency); add `"gen:api"` script (fetch `http://127.0.0.1:8000/openapi.json` → `src/api/schema.ts`).
- `frontend/src/api/client.ts` -- NEW: `fetch` wrapper (JSON, `credentials: 'same-origin'`, error envelope → typed `ApiError`, 401 hook); `frontend/src/api/schema.ts` -- generated (committed); type exports for `JobResponse`, `CampaignResponse`, `AccountResponse`, job payload shapes.
- `frontend/src/stores/auth.ts` -- NEW Pinia store: `account`, `login/register/logout/me`, `hydrate()` called by the router guard; `frontend/src/stores/campaigns.ts` -- NEW: list + current campaign; `frontend/src/stores/jobs.ts` -- NEW: per-campaign jobs map, `submitBuildIn()`, `syncList()`, WS message dispatch (AD-17 types).
- `frontend/src/ws.ts` -- NEW: `connectJobSocket(campaignId, onMessage)` reconnecting client with bounded backoff + teardown.
- `frontend/src/router.ts` -- NEW: routes above + the `me`-based guard; `frontend/src/main.ts` -- install router; `frontend/src/App.vue` -- shell with `<RouterView/>`, session-aware nav; remove the counter demo store/view (clean cutover).
- `frontend/src/views/LoginView.vue`, `RegisterView.vue` -- NEW: forms hitting `POST /api/auth/login|register` (1.5 wire), redirect on success.
- `frontend/src/views/CampaignsView.vue` -- NEW: own-campaign list (cursor page 1, 1.6 wire) + "open build-in" per campaign.
- `frontend/src/views/BuildInView.vue` -- NEW: pre-filled seed card (read-only) + four section inputs + submit → jobs store `submitBuildIn()` → live job panel (id, state, `queue_position`, progress, terminal error display).
- `frontend/src/stores/*.test.ts` -- NEW/updated vitest unit tests: auth store (me-401 clears), jobs store (AD-17 message dispatch upserts `queue_position`/progress/state; terminal types), keeping the existing counter test removed with the demo.
- `backend/app/store/jobs.py` -- `_enqueue` kind validation: extend `{text, image, video}` with `build_in`; payload guard for the four-section shape (at least one non-blank, length/count caps) raising `InvalidJobInputError` (422).
- `backend/app/api/jobs.py` -- `JobCreate.kind` Literal gains `"build_in"`.
- `backend/app/pipeline/worker.py` -- `_run_job`: `build_in` → raise `JobPayloadError("job kind 'build_in': the build-in runner lands in Story 2.3")` (fail-not-wedge, mirroring the media branch).
- `backend/tests/test_jobs.py` -- extend: BUILDIN_SUBMIT 201 + position; ALL_BLANK/length/count 422s; idempotency; worker fails `build_in` with the documented message and the queue flows on.
- Retro action items folded in (next-story-dev owned, per epic-1 retro):
  - **Item 1 — ULID predicate:** add `core/ids.is_valid_ulid(s) -> bool`; use it in `commit._check_ulid`, `jobs._check_ulid`, `pagination.decode_cursor` (single Crockford predicate, three error families stay).
  - **Item 3 — campaigns errors → StoreError:** `InvalidThemeError` and the campaigns value-errors subclass `StoreError`; `api/campaigns.py` routes through a shared `_store_error_as_http` (extract the jobs mapper into `api/common.py` or reuse pattern), dropping the inline `(ValueError, ...)` catch.
  - **Item 4 — one canonical theme seed:** drop the dead `SEED_THEMES` fallback in `store/campaigns.py`; `configured_seed_themes()` reads only the config-resolved list (config falls back to `core/config.py DEFAULT_THEMES` today — that is the single code seed); keep the deploy-contract pin test.

## Tasks & Acceptance

**Execution:**
- [ ] Frontend foundation: router + guard, API client + generated schema, error envelope, auth store + login/register views, campaigns list view, jobs store + WS client — per Code Map.
- [ ] Build-in view: read-only seed prefill from the current campaign, four sections, submit → `build_in` job, live lifecycle panel.
- [ ] Remove the Vite counter demo (store, test, App.vue wiring) — clean cutover.
- [ ] Backend: `build_in` kind + payload validation in `_enqueue` + `JobCreate`; worker fail-not-wedge message; retro items 1/3/4.
- [ ] Tests: backend kind/validation/lifecycle + retro-item regressions; frontend auth/jobs store unit tests; `gen:api` regeneration exercised.

**Acceptance Criteria:**
- Given I am a logged-in DM with a campaign (1.6), when I open the build-in, then the world-seed fields are pre-filled from the campaign (title, description, theme, custom-lore) and I can add key places, factions, key figures, and free-form notes (FR6, AR27).
- Given completed build-in inputs, when I submit, then a `build_in` generation job is enqueued and the screen is not blocked — queue_position and state render immediately and update live over REST + WS (AD-19, AR5; design rule). The worker's terminal failure (until 2.3) is a documented, visible state, not a hang or a silent skip.
- Given a blank submission or an oversized entry, when I submit, then 422 with zero rows written.
- Given a dropped WebSocket, when it reconnects, then the jobs view re-syncs from `GET /api/jobs` — no missed terminal state.

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries. -->

### Post-implementation (2026-08-30, owner smoke)

**Smoke findings (implemented + pinned):**
- **Secure cookie over plain-http dev:** the session cookie was hard-coded
  `Secure=True`, so a browser refused it over the Vite dev proxy and the
  dogfood flow could not authenticate. `_set_session_cookie` now derives
  Secure from the request scheme (TLS via Caddy => Secure; plain-http dev
  => not). Tests pin Secure over the https TestClient.
- **Register = sign-in:** `POST /api/auth/register` set no session cookie,
  so a freshly-registered DM was bounced to login. Register now creates a
  session and sets the cookie (standard sign-up-is-sign-in UX).
- **WebSocket library missing:** uvicorn had no `websockets`/`wsproto`,
  so every `/api/ws/jobs` upgrade was rejected (`Unsupported upgrade
  request`) and the live job panel could never update. Added
  `websockets` to `backend/pyproject.toml`; the smoke captured the full
  AD-17 lifecycle live (`queue_changed(queued,1) -> running(1) ->
  job_failed -> queue_changed(failed)`).
- **Vite proxy WS:** `frontend/vite.config.ts` proxied `/ws` (a nonexistent
  path) instead of upgrading `/api`; the socket is now `/api` with
  `ws: true`, matching the real route `/api/ws/jobs`.
- **`frontend/.npmrc`:** `openapitypescript` peers `typescript@^5` but the
  project runs typescript 6; the dep is a build-time codegen CLI, so the
  peer conflict is accepted via `legacy-peer-deps=true` (plain `npm ci`
  in deploy otherwise fails).

**KEEP (survive re-derivation):** the build-in wire + lifecycle flow;
auth-gated build-in screen; read-only seed prefill; fail-not-wedge worker
behavior until 2.3; the shared ULID predicate + campaigns->StoreError.

## Design Notes

**Why 2.1 owns the frontend foundation.** The 1-2 review deferred "frontend wire-contract foundation" into 1.6, but 1.6's final scope was backend-only (campaign CRUD + seed). The deferral is therefore still open and unowned; Epic 2's first two frontend stories (2.1, 2.7) both depend on it. Per the owner's gate decision (2026-08-30), 2.1 absorbs the foundation explicitly: router, OpenAPI-derived client, envelope, auth wiring, WS client, Pinia stores. This is recorded here so later reviews stop re-flagging the deferral as unowned.

**Worker boundary until 2.3.** Enqueuing `build_in` jobs before the runner exists could wedge the FIFO if the worker skipped them; failing them loudly mirrors the exact convention 1.4 established for image/video ("the media service lands in Epic 4"). 2.1's ACs are the enqueue + observable lifecycle; digestion (core-first two-wave, deterministic prompt, budget) is 2.3's AC set. The acceptance verification below proves the full lifecycle end-to-end, including the honest terminal state.

**Build-in commit path (2.3 note, not decided here).** Spine AD-19/AD-15 stage build-in output as a *proposed* subgraph; Epic 2's 2.3/2.7 language has the core wave "commit" directly. That reconciliation is a design note in the 2.3 spec (owner-reviewed), consistent with this story's decision to keep 2.1's scope to the wire + lifecycle.

**Retro items landed with 2.1.** Items 1/3/4 were owned by "next story dev" / "campaigns-touching story" / "config-touching story" — 2.1 is the first story after the retro and touches every one of those files (jobs.py, commit.py, campaigns.py, pagination.py, core/ids.py). Item 2 (cursor-pagination convergence) is owned by the pipeline story dev → 2.3; item 5 (env-parse consolidation) waits for a refactor story; item 6 (spine AD-1 exception) is the owner's contract edit; item 7 (conftest app import) is a test-hardening story.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. new build_in kind/validation/lifecycle + retro-item regressions (deterministic).
- `make lint && make typecheck` -- expected: ruff, mypy strict, eslint, vue-tsc clean.
- `cd frontend && npm run gen:api && npm test` -- expected: generated schema regenerates; store unit tests green.
- Smoke (owner dogfood): launch backend (scratch DB, test config) + `npm run dev`; register → login → land on `/` → open a campaign's build-in → seed pre-filled → submit → job panel shows `queued` + position, then `running`, then the documented terminal state live over WS; verify 401 redirect on a fresh tab without a session.

## Suggested Review Order

**Backend (kind + validation + retro items)**

- `build_in` acceptance + payload guard; idempotency preserved
  [`store/jobs.py:204`](../../backend/app/store/jobs.py#L204)

- Worker fail-not-wedge message for `build_in`
  [`pipeline/worker.py:80`](../../backend/app/pipeline/worker.py#L80)

- ULID predicate consolidation + campaigns→StoreError + theme seed dedup
  [`core/ids.py`](../../backend/app/core/ids.py)
  [`store/campaigns.py`](../../backend/app/store/campaigns.py)

**Frontend (foundation + build-in flow)**

- API client: envelope → typed ApiError, 401 hook, generated schema
  [`api/client.ts`](../../frontend/src/api/client.ts)

- Jobs store: AD-17 WS dispatch + REST re-sync; auth store guard
  [`stores/jobs.ts`](../../frontend/src/stores/jobs.ts)
  [`router.ts`](../../frontend/src/router.ts)

- Build-in view: read-only seed prefill, section validation, live panel
  [`views/BuildInView.vue`](../../frontend/src/views/BuildInView.vue)

**Tests**

- Kind matrix (201/422/idempotent/worker-fail) + store unit tests
  [`test_jobs.py`](../../backend/tests/test_jobs.py)
  [`stores/*.test.ts`](../../frontend/src/stores/)

### Review Findings

Review of the full b087b84..HEAD diff (Story 2.1: guided build-in + frontend foundation) — run 2026-08-31, four parallel layers (blind-hunter, edge-case-hunter, verification-gap, acceptance-auditor). All layers completed; findings verified against the working tree.

**decision-needed**

- [x] [Review][Decision] build_in `notes` is unbounded — spec caps every entry (≤100 entries, ≤2000 chars) but `notes` has no length cap in `_validate_build_in_payload` and no `maxlength` in the textarea; a multi-MB notes blob passes validation and flows verbatim into the 2.3 prompt. A cap value must be chosen. [backend/app/store/jobs.py:459-462, frontend/src/views/BuildInView.vue]

**patch**

- [x] [Review][Patch] `/api/ws/jobs` accepts unauthenticated sockets — `jobs_ws` never checks the session cookie; a client that guesses a campaign_id receives that campaign's job broadcasts (payload, error text) — cross-account disclosure (AD-9). The WS_UNAUTH matrix row is unimplemented on both sides. Fix: cookie auth before accept, close unauthenticated sockets with an auth code (4401); client treats it as auth failure → `/login`. [backend/app/api/ws.py:156-172, frontend/src/ws.ts]
- [x] [Review][Patch] API_401_ANYWHERE is dead code — `onUnauthorized` is invoked in `client.ts` but no call site passes it (auth/campaigns/jobs stores, views all call `apiFetch` with two args). An expired session during `syncList`/`fetchOne`/`submitBuildIn` neither clears the auth store nor redirects. Fix: `setUnauthorizedHandler` in the client wired from `main.ts` (clear auth + `/login`). [frontend/src/api/client.ts:32,63-65]
- [x] [Review][Patch] WS_RECONNECT not implemented — socket `onopen` only resets the retry counter; nothing re-syncs from `GET /api/jobs` on reconnect, and `stores/jobs.ts`'s "Full re-sync after reconnect" docstring is false. A drop during the fast queued→failed window leaves the panel on stale `running` — AC4 unmet. Fix: `onReconnect` callback → `syncList`, wired from `BuildInView.vue`. [frontend/src/ws.ts:33-37, stores/jobs.ts]
- [x] [Review][Patch] `ws.ts` teardown leaks a reconnect — a `setTimeout(connect)` scheduled by a prior `onclose` fires after teardown (`connect()` never checks `closed`); no `onerror` handling. Fix: guard `connect()` with `closed`, clear the timer in teardown, treat a 4401 close as fatal (no reconnect). [frontend/src/ws.ts:46-58]
- [x] [Review][Patch] WS frames for uncached jobs are dropped and `syncList` can clobber fresher WS deltas — `handleWsMessage` patches only cached ids (a `job_progress` for a job enqueued in another tab is discarded); the async `queue_changed`/terminal `syncList` can upsert a stale REST snapshot after a newer frame patched the row. Fix: re-sync when the frame's job id is unknown; monotonic merge in upsert (never regress state/progress). [frontend/src/stores/jobs.ts:63-83]
- [x] [Review][Patch] `syncList` is single-page — `/api/jobs` defaults to 50; with more jobs in a campaign, older build-ins never enter the store and `latestBuildIn` can be wrong. Fix: fetch all pages (or raise the limit for the build-in view). [frontend/src/stores/jobs.ts:44-52]
- [x] [Review][Patch] Views have no error handling — `void campaigns.fetchOne(campaignId)` / `void jobs.syncList(campaignId)` / `void campaigns.list()` with no catch: a 404 (unknown/foreign campaign) or 5xx leaves "Loading world seed…" up forever (unhandled rejection) and an empty campaign list renders the misleading "No worlds yet" state. Fix: error state in the campaigns store + per-view error surface. [frontend/src/views/BuildInView.vue:30-33, CampaignsView.vue:18-20, stores/campaigns.ts]
- [x] [Review][Patch] `ck_job_kind` CHECK-constraint widening ships with no migration — `create_all` never alters an existing table, so any 1.3-1.6 database keeps `kind IN ('text','image','video')` and the first build_in INSERT raises an unhandled IntegrityError → 500. Fix: `_migrate_job_kind` (inspect constraint, rebuild the table with the new constraint, copy, drop, rename), matching the existing additive-migration convention. [backend/app/store/models.py:165-167, db.py:88-93]
- [x] [Review][Patch] deploy.sh pip-fallback install path omits `websockets` — hosts without uv deploy a backend lacking WS support; `/api/ws/jobs` regresses to "Unsupported upgrade request" and the live panel dies (AC2). Fix: add `"websockets>=15,<16"` to the pip install line. [deploy/deploy.sh:70-73]
- [x] [Review][Patch] build_in `notes: null` is accepted — spec pins `notes` as "a single string"; `if notes is not None and not isinstance(notes, str)` lets an explicit null through. Fix: require a string when the key is present. [backend/app/store/jobs.py:475-478]
- [x] [Review][Patch] `_store_error_as_http` maps only a subset of `StoreError` subclasses — `InvalidUlidError`, `StaleRevisionError`, `EmptySubgraphError`, `InvalidEdgeTypeError`, `CrossCampaignConflictError`, `DanglingEdgeError` re-raise into 500s through this surface; 2.3's commit path raises exactly these. Fix: map them into the 404/409/422 families (`CorruptEventError` stays internal → 500). [backend/app/api/common.py:22-35]
- [x] [Review][Patch] Retro item 3 is half-done — `api/campaigns.py list_own` still catches `InvalidCursorError` inline instead of routing through the shared mapper; `_require_non_blank` raises `InvalidThemeError` for a blank title (mis-branded class now flowing through the shared mapper). Fix: route the cursor rejection through `_store_error_as_http`; raise `CampaignInputError` for non-theme blank fields. [backend/app/api/campaigns.py, store/campaigns.py:56-61]
- [x] [Review][Patch] Empty themes config list now rejects every theme — retro item 4 removed the empty-list fallback: `configured_seed_themes()` returns an empty set when config declares `themes = []` (old contract: empty → defaults), so every campaign create/update 422s. Fix: `return frozenset(themes) if themes else frozenset(DEFAULT_THEMES)` — one canonical code seed, restored default behavior. [backend/app/store/campaigns.py:29-31]
- [x] [Review][Patch] Router guard aborts navigation on a 5xx from `hydrate()` — a non-401 error rejects the guard's awaited `hydrate()`, leaving a blank RouterView with no surface. Fix: catch in the guard and fall through to the auth check. [frontend/src/router.ts:25-29]
- [x] [Review][Patch] `apiFetch` trusts non-JSON 2xx bodies — a proxy error page / empty 200 parses to `null` via `.catch(() => null)` and is cast to `T` (account/job/campaign silently null). Fix: content-type/parse guard on the success path → ApiError. [frontend/src/api/client.ts:54,68]
- [x] [Review][Patch] Register=sign-in cookie and the plain-http Secure branch are unpinned — no test asserts `POST /api/auth/register` sets the session cookie (deleting `auth.py:146-147` leaves the suite green) and the scheme-derived `Secure` is tested only over the https client; reverting to hard-coded Secure breaks dogfood invisibly. Fix: register Set-Cookie + me round-trip test; plain-http client test asserting no Secure + authenticated me. [backend/tests/test_auth_api.py]
- [x] [Review][Patch] build_in idempotency test missing — the Code Map test bullet lists idempotency but no test exercises same-`job_id` twice → 201 then 409 for the new kind. Fix: add it. [backend/tests/test_jobs.py]
- [x] [Review][Patch] Retro item 1's shared ULID predicate has no direct test — `test_ids.py` covers only `new_id`; a regression in `is_valid_ulid` surfaces only through unrelated suites. Fix: unit tests for the predicate. [backend/tests/test_ids.py]
- [x] [Review][Patch] Frontend tests skip terminal WS dispatch, reconnect re-sync, and blank-submit — `job_done`/`job_failed`/`job_cancelled` frames, the reconnect→syncList AC, and BuildInView's disabled/error states have no coverage (no `ws.ts` test exists). Fix: add after the reconnect/handler wiring fixes. [frontend/src/stores/jobs.test.ts, ws.test.ts]
- [x] [Review][Patch] BuildInView surfaces only the newest build-in — submitting a second job while one runs removes the first job's lifecycle from view; a failing newest job hides an older one still progressing. Fix: render all build-in jobs for the campaign (capped list), not just `latestBuildIn`. [frontend/src/views/BuildInView.vue:85-100]

**resolution**

All 21 findings (1 decision + 20 patches) resolved 2026-08-31 in the patch sweep: notes capped at 2000 chars (decision); WS auth gate (4401 close, AD-9), global 401 handler, WS reconnect re-sync, teardown/leak fixes, monotonic store merge + paginated sync + unknown-job re-sync, view error states, `_migrate_job_kind` constraint rebuild, deploy.sh `websockets` pip fallback, notes-null rejection, full StoreError mapper coverage, retro-3 completion (cursor routing + CampaignInputError for blank fields), empty-themes fallback to DEFAULT_THEMES, auth cookie/secure-branch tests, build_in idempotency + ULID-predicate tests, frontend terminal/reconnect/logout tests, multi-job build-in panel. Verification: backend 264 passed + mypy strict clean, ruff/eslint clean, vitest 20/20, vue-tsc clean.