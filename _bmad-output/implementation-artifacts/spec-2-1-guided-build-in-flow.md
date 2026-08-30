---
title: 'Guided build-in: frontend foundation + the build_in job kind'
type: 'feature'
created: '2026-08-30'
status: 'ready-for-dev'
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