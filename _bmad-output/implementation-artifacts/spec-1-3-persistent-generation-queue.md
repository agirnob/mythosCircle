---
title: 'Persistent generation queue: FIFO substrate, claim serialization, cancel, caps'
type: 'feature'
created: '2026-08-30'
status: 'done'
review_loop_iteration: 1
baseline_commit: 'c76b9002d57a6c809e0c1be5fe8d855555f14719'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/specs/spec-mythosCircle/conventions.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Generation jobs have no home: nothing queues them, serializes them onto the single GPU (AD-3), survives a restart, or tells the DM where their job stands. Story 1.2 delivered the world store; the queue is the second substrate piece every later generation story commits through.

**Approach:** Add a persistent `job` table to the store (AD-13), one FIFO: enqueue via REST (AD-17), claim serialization that guarantees exactly one job runs at a time (AD-3), per-campaign pending caps (AR28), cancel that frees the slot (AR28), restart recovery from the store (AR11), and REST + WebSocket visibility of state and queue position (AD-17). A `running` job found at store startup is re-queued — its owner died with the process, and the FIFO must never deadlock on a stale `running` row. The actual runner + inference adapter are Story 1.4 — 1.3 ships the substrate and the claim/complete/fail/cancel/progress primitives 1.4's worker calls.

## Boundaries & Constraints

**Always:**
- Exactly one job runs at a time, enforced at claim time in the store: `claim_next_job` refuses while any job is `running` (AD-3); concurrent claims serialize via `BEGIN IMMEDIATE` (store.db begin-listener).
- One persistent FIFO across all campaigns; issuance order = SQLite `rowid` (same monotonic clock as revisions/events). No bypass path around `enqueue_job` (AD-3).
- The queue survives process restart: jobs persist in the store; no in-memory queue (AR11, AD-13). On store initialization, any job in `running` state is re-queued (`queued`, `started_at` cleared) — a `running` row persisting past a process death must not stall the single queue; only a live worker may hold `running`.
- Jobs are NOT world graph: job rows and transitions never touch the event log or revisions; job transitions do not create revisions (AD-1/AR3 applies to graph state only).
- Job IDs are ULIDs (conventions.md); caller-supplied `job_id` must be a ULID and globally unique across the store (the job id is the primary key) — reuse in any campaign → structured `DuplicateJobError` → 409, zero rows written (idempotent by job-id).
- Wire contract (AD-17): REST for submission/status; WS messages shaped `{type: job_progress|job_done|job_failed|job_cancelled|queue_changed, job_id, state, queue_position?, progress?}`; error envelope `{code, message, details?}`; 4xx = user error, never a state change.
- Per-campaign cap: enqueue rejected when the campaign's pending (queued + running) count is at the cap (AR28). Cap comes from env `MYTHOSCIRCLE_MAX_PENDING_PER_CAMPAIGN` (default 10); `deploy/config.toml` consumption stays deferred to Story 1.7 (review finding).
- Every job declares `max_llm_calls` / `max_media_calls` (defaults from env `MYTHOSCIRCLE_MAX_LLM_CALLS_PER_JOB`=64, `MYTHOSCIRCLE_MAX_MEDIA_CALLS_PER_JOB`=8); enforcement is 1.4 (AR21) — 1.3 only stores them, validated `>= 0`.
- Job kinds are the closed set `{text, image, video}` (AD-3 runner split: text→pipeline, image/video→media; `build-in` is a text-kind payload variant fixed in later stories).
- ULID ids; UTC ISO-8601 `Z` timestamps; snake_case Python, kebab-case API routes.

**Ask First:** adding a job kind beyond {text, image, video}; changing the job table shape once 1.4's worker reads it; a second concurrent runner (multi-GPU).

**Never:** a worker thread or any executor in this story (the runner is 1.4 — 1.3 emits no `job_done`/`job_failed` except through `complete_job`/`fail_job` store primitives); HTTP auth (DM auth is 1.5 — endpoints are unauthenticated, campaign-scoped); campaign CRUD routes (1.6); job retries/requeue; deleting job rows; config.toml parsing; any raw SQL outside `store/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ENQUEUE_FIRST | campaign exists, empty queue | job `queued`, position 1, ULID minted, `created_at` set | N/A |
| ENQUEUE_FIFO | 3 jobs in order | positions 1,2,3; `claim_next_job` yields the earliest (rowid) first | N/A |
| ENQUEUE_CAP | pending count at cap | rejected, zero rows written | structured `QueueFullError` → 409 envelope, no state change |
| ENQUEUE_DUP_ID | `job_id` already exists anywhere in the store | rejected, zero rows written | `DuplicateJobError` → 409 |
| ENQUEUE_BAD_INPUT | kind outside {text,image,video} / non-ULID `job_id` / budgets < 0 / payload not a JSON-serializable dict | rejected, zero rows written | `InvalidJobInputError` → 422, no state change |
| CLAIM_EXACTLY_ONE | queue non-empty, no job running | earliest queued job → `running` + `started_at`; a second claim while running returns `None` | N/A |
| CLAIM_CONCURRENT | two threads claim simultaneously (barrier) | exactly one job claimed; other returns `None` | N/A (BEGIN IMMEDIATE serializes) |
| COMPLETE | a `running` job | → `succeeded`, `finished_at`, slot freed (next claim proceeds); `job_done` + `queue_changed` emitted | wrong state → `JobStateConflictError` → 409 |
| FAIL | a `running` job, error message | → `failed` with error stored; `job_failed` emitted | wrong state → `JobStateConflictError` → 409 |
| PROGRESS | a `running` job, 0 ≤ progress ≤ 1 | progress stored; `job_progress` emitted; state stays `running` | out-of-range → `InvalidJobInputError` → 422 |
| CANCEL_QUEUED | queued job | → `cancelled`; slot freed (claim skips it); `job_cancelled` + `queue_changed` | N/A |
| CANCEL_RUNNING | running job | → `cancelled`; `job_cancelled` + `queue_changed` (1.4's runner polls the state to stop) | N/A |
| CANCEL_TERMINAL | succeeded/failed/cancelled job | rejected, state unchanged | `JobStateConflictError` → 409 |
| STATUS_POSITION | later enqueues and terminal transitions exist | position = 1 + count of same-campaign non-terminal jobs with earlier rowid | unknown job → `JobNotFoundError` → 404 |
| RESTART | process dies, same DB file re-opened | queued jobs restored unchanged; a `running` job is re-queued (state `queued`, `started_at` cleared) so the FIFO flows; positions identical, no loss (AR11) | N/A |
| RESTART_STALE_RUNNING | DB holds a `running` job from a dead process | startup recovery re-queues it; `claim_next_job` proceeds in FIFO order as if it had never started | N/A |
| CROSS_CAMPAIGN | two campaigns, jobs in both | A's list/positions never show B's jobs; the global FIFO claims across campaigns in rowid order | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/store/models.py` -- add `Job` table: id String(26) PK, campaign_id FK index, kind String(64), payload JSON, state String(32) index, progress Float, max_llm_calls / max_media_calls Integer, error Text nullable, created_at / started_at / finished_at String(40) nullable — pattern mirrors `Event`/`Revision` (rowid = order clock).
- `backend/app/store/jobs.py` -- NEW: job errors (`JobNotFoundError`→404, `JobStateConflictError`→409, `DuplicateJobError`→409, `QueueFullError`→409, `InvalidJobInputError`→422) + `enqueue_job`, `claim_next_job`, `complete_job`, `fail_job`, `report_progress`, `cancel_job`, `job_status`, `list_jobs` (cursor), `set_change_listener`; every mutation runs inside `session_scope` (BEGIN IMMEDIATE).
- `backend/app/store/__init__.py` -- export the job API + errors.
- `backend/app/core/settings.py` -- NEW: `queue_settings()` dataclass from env (`MYTHOSCIRCLE_MAX_PENDING_PER_CAMPAIGN`, `MYTHOSCIRCLE_MAX_LLM_CALLS_PER_JOB`, `MYTHOSCIRCLE_MAX_MEDIA_CALLS_PER_JOB`), defaults per Always list.
- `backend/app/api/jobs.py` -- NEW router: `POST /api/jobs`, `GET /api/jobs/{job_id}`, `GET /api/jobs?campaign_id=&cursor=&limit=`, `POST /api/jobs/{job_id}/cancel`; Pydantic schemas; maps store errors → envelope (see I/O matrix); enforces `4xx = no state change`.
- `backend/app/api/ws.py` -- NEW: WebSocket hub keyed by campaign_id + `GET /api/ws/jobs?campaign_id=` route; broadcasts the AD-17 shapes to the campaign's subscribers; registers the store change-listener in `create_app`.
- `backend/app/main.py` -- wire jobs router, WS route, hub lifecycle (FastAPI lifespan), register listener.
- `backend/app/core/errors.py` -- reuse; no changes expected (409/422/404 paths exist).
- `backend/tests/test_jobs.py` -- NEW store tests: one per I/O matrix row, deterministic fixtures (world-fixture style), threading-barrier concurrency.
- `backend/tests/test_jobs_api.py` -- NEW: REST envelope tests + WS message assertions via TestClient `websocket_connect`.
- `backend/tests/conftest.py` -- existing scratch-DB pin already isolates the app; no change expected.
- `deploy/config.toml` -- intentionally untouched (consumption deferred to 1.7, review-deferred).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/models.py` -- add `Job` table (state index; rowid-order FIFO) -- AD-13 substrate.
- [x] `backend/app/store/jobs.py` -- enqueue/claim/complete/fail/progress/cancel/status/list + change-listener -- the queue substrate (AD-3, AR11, AR28).
- [x] `backend/app/store/__init__.py` -- export job surface.
- [x] `backend/app/core/settings.py` -- env-backed queue settings with defaults.
- [x] `backend/app/api/jobs.py` -- REST routes + Pydantic schemas + envelope mapping.
- [x] `backend/app/api/ws.py` -- WS hub, `/api/ws/jobs` route, store-listener registration.
- [x] `backend/app/main.py` -- wire router/WS/lifespan/listener.
- [x] `backend/tests/test_jobs.py` -- I/O matrix coverage incl. concurrent claims and restart persistence.
- [x] `backend/tests/test_jobs_api.py` -- envelope codes, WS message shapes, cancel flow.

**Acceptance Criteria:**
- Given a job submitted via REST, when it is queued, then exactly one job runs at a time (claim invariant) and its state + `queue_position` are visible via REST and WebSocket (job_id, state, queue_position) (AR5, AD-17).
- Given an in-flight or queued job, when the DM cancels it, then a `job_cancelled` message is emitted and the queue slot is freed (AR28).
- Given a per-campaign cap, when in-flight + queued jobs exceed it, then the enqueue is rejected with no state change (AR28).
- Given a server restart, when the process returns, then the pending queue is restored from the store and no job is lost (AR11).


### Loop 1 (2026-08-30)

**Finding:** review round 1 (intent_gap) — a `running` job from a crashed
process deadlocks the whole queue: `claim_next_job` refuses while any job
is `running`, and nothing can ever transition the orphan row again; the
restart test pinned the deadlock as correct.

**Amended (human decision, owner-approved):** the frozen RESTART row,
Approach, and Always list now mandate startup recovery: any `running` job
found at store initialization is re-queued (`queued`, `started_at`
cleared) so the FIFO never stalls on a stale `running` row (AR11). New
I/O row RESTART_STALE_RUNNING pins the behavior. Implementation:
`store/jobs.recover_stale_running()` called from `create_app` after
`init_app_db()`.

**Known-bad state avoided:** permanent global-FIFO deadlock after one
mid-job crash with no recovery path.

**KEEP:** rowid-ordered FIFO issuance; claim-time exactly-one invariant
(no lock table, BEGIN IMMEDIATE serialization); worker-less 1.3 (runner
is 1.4); per-campaign `queue_position` (AD-9); AD-17 WS shape; `job_id`
idempotency; per-campaign pending cap (AR28).

**Clarification (review round 1, same loop):** the frozen wording "job_id
unique per campaign" was ambiguous against an id-as-primary-key schema —
the job id PK makes ids globally unique, and cross-campaign reuse of one
idempotency key is a structured `DuplicateJobError` (409), never a raw
IntegrityError. Always bullet and ENQUEUE_DUP_ID amended to "globally
unique across the store"; ENQUEUE_BAD_INPUT amended to "JSON-serializable
dict" (store rejects non-serializable payloads at the boundary, 422).
Pinned by `test_job_id_globally_unique` and
`test_non_json_serializable_payload_rejected`.

## Design Notes

Exactly-one-at-a-time is a claim-time store invariant, not a worker lock: `claim_next_job` returns `None` while any job is `running`, and concurrent claims serialize on the `BEGIN IMMEDIATE` begin-listener (db.py) — no separate lock table; the invariant lives in the store the queue is part of.

FIFO order is SQLite `rowid` (the same monotonic clock as revisions/events) — never ULID string order (ties on the random suffix).

`queue_position` is per-campaign, not global: `1 + count(same-campaign non-terminal jobs with earlier rowid)`. Privacy-safe (AD-9) — a DM never sees other campaigns' load; it answers "how many of *my* jobs are ahead", not a global ETA. Global FIFO still schedules across campaigns by rowid.

WS emission: `store.jobs` holds one registered change-listener (one FastAPI process = single writer, AD-13). `create_app` registers the API hub broadcaster, so *every* transition — including `claim`/`complete`/`fail`/`progress` called later by 1.4's worker — pushes the AD-17 shapes without store knowledge of websockets.

`job_cancelled` is a WS type beyond AD-17's four, required by AR28. `job_progress`/`job_done`/`job_failed` are emit-ready; 1.4's worker drives progress and completion through the primitives.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — expected: all store + API job tests pass, full suite green (deterministic, no live LLM).
- `make lint && make typecheck` — expected: ruff, mypy strict, eslint, vue-tsc clean (backend changes only).

## Suggested Review Order

**Startup recovery (the loop-1 resolution)**

- Stale-`running` re-queue: the FIFO never deadlocks after a crash
  [`jobs.py:285`](../../backend/app/store/jobs.py#L285)

- Wired into the app factory right after store init
  [`main.py:30`](../../backend/app/main.py#L30)

**Store transitions (claim/complete/fail/cancel + position snapshot)**

- Position snapshotted in-session at transition time — broadcasts never touch the DB
  [`jobs.py:141`](../../backend/app/store/jobs.py#L141)

- Exactly-one claim + per-campaign position semantics
  [`jobs.py:187`](../../backend/app/store/jobs.py#L187)

- fail emits `queue_changed` like complete/cancel (round-1 fix)
  [`jobs.py:232`](../../backend/app/store/jobs.py#L232)

- Cursor pagination is campaign-scoped; unknown campaign → 404
  [`jobs.py:462`](../../backend/app/store/jobs.py#L462)
  [`jobs.py:321`](../../backend/app/store/jobs.py#L321)

- DB-level CHECK constraints on the closed state/kind sets + progress range
  [`models.py:137`](../../backend/app/store/models.py#L137)

**WebSocket hub (drain resilience)**

- Pre-built messages, no DB in the drain; a failed send drops only the socket
  [`ws.py:41`](../../backend/app/api/ws.py#L41)
  [`ws.py:101`](../../backend/app/api/ws.py#L101)

- Stop cancels the run-forever drain via cancel+gather (never wait_for)
  [`ws.py:74`](../../backend/app/api/ws.py#L74)

**API layer**

- List maps store 404/422 into the envelope; cursor decoded once
  [`jobs.py:134`](../../backend/app/api/jobs.py#L134)

**Tests (review-regression)**

- WS tests drive transitions from a worker-shaped background thread (TestClient portal determinism) — the round-1 hang fix
  [`test_jobs_api.py:251`](../../backend/tests/test_jobs_api.py#L251)

- Recovery, terminal-list, cap-race, foreign-cursor, cancel-404
  [`test_jobs.py:597`](../../backend/tests/test_jobs.py#L597)
  [`test_jobs.py:678`](../../backend/tests/test_jobs.py#L678)