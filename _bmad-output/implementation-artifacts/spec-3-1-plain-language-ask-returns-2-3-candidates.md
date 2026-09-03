---
title: 'Plain-Language Ask Returns 2–3 Candidates'
type: 'feature'
created: '2026-09-03'
status: 'done'
baseline_commit: '831405b4a0d340e36d857d4190feca65ee78003a'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 2 built the world; the DM still has no way to grow it by asking. Epic 3's core loop starts here: a plain-language ask returns 2–3 candidate entities woven into her existing world (FR7, FR8, AR19).

**Approach:** A new queued `generate` job kind: the pipeline builds a byte-deterministic prompt (campaign seed + ask + AR6 retrieval context), validates 2–3 candidates against the AR19 shape contract and AR25 stat-block rules (one bounded repair pass), and stages them as proposed rows that commit nothing. Accept/reject lifecycle is story 3.2; this story only stages.

## Boundaries & Constraints

**Always:**
- Everything flows through the existing job queue (AR5): submit enqueues, the worker claims, WS frames `job_progress`/`job_done`/`job_failed` fire unchanged — no new frame types.
- Prompt is a pure function of campaign seed + payload + the rowid-ordered retrieved neighborhood (AR6/AD-16): no ids, no timestamps, no wall clock. Pin byte-identity with a test, as build-in does.
- Retrieval via `retrieve_neighborhood` + `serialize_context` reused unchanged (seed = full committed world, bounded by the entity cap); no embeddings, no search (AR6).
- Each candidate carries the AR19 shape: name, role (NPC|BBEG|Monster), personality, the secret/rumor/party-hook triple, an AR25-validated 5e stat block, and ≥1 typed edge from the closed vocabulary whose far endpoint is an existing committed entity. Candidate record stored as structured JSON tolerating extra keys (AR24 forward compat).
- Stat-block validation reuses `collect_stat_issues`/`validate_stat_block` + exactly one bounded repair pass via the existing repair machinery (E-refs); repairs count against the job's `CallBudget` (AR21/AR25).
- Candidates stage as rows in a new proposed-candidates table — never in `entity`/`edge` tables — so retrieval, export, and world reads are untouched (AR7). The runner never calls `commit_subgraph`.
- Generation-time edge validation is runner-level: candidate edge endpoints must resolve to committed `world_state` ids; the commit-path orphan backstop remains the accept-time authority (story 3.2).
- New job kind lands in lockstep in all four places: `store/jobs.py` JOB_KINDS, `models.py` `ck_job_kind` CHECK, `worker.py` dispatch, `api/jobs.py` Literal — plus per-kind payload validation mirroring `_validate_build_in_payload`.
- New REST reads are authed owner-only via `Depends(get_current_account)` + campaign-ownership check, error envelope + cursor pagination per conventions.

**Ask First:**
- Adding auth to the existing `POST /api/jobs` route (pre-existing unauthenticated surface; this story extends it, does not retarget it).
- Any change to the commit path, event/revision model, or `read.world_state` semantics.
- Any new WS frame type or progress semantic beyond the existing runner pattern.
- Dev/staging DBs hit the `ck_job_kind` CHECK change — if a persistent dev DB exists in deploy, decide recreate vs migration before touching it.

**Never:**
- No accept/reject endpoints, no commit of staged candidates, no mutation of accepted state (story 3.2).
- No accept screen or candidate UI (story 3.3); no frontend changes at all in this story.
- No per-section regeneration (3.5), no hand-edit path (3.6), no media (Epic 4).
- No full AR24 sectioned-profile validation — only AR19 fields; unknown keys pass through unvalidated.
- No NFR2 proxy / P95 metrics plumbing — the shared gauge lands with the epic's later stories.
- No budget/LLM-call changes beyond reusing `CallBudget` as-is.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | Authed owner submits kind=`generate` with ask; world has committed entities; model returns 3 valid candidates | Job completes; 3 proposed candidate rows staged with full AR19 shape; job result names staged candidate ids | N/A |
| ASK_EMPTY_WORLD | Ask submitted, campaign has zero committed entities | Rejected at submit | 422 envelope, no job enqueued |
| FEWER_THAN_TWO | Model output: <2 candidates survive shape+stat validation after the one repair pass | Job fails | `job_failed` with structured error naming the count |
| BAD_EDGE | A candidate's edges all target non-existent/uncommitted entities | That candidate is invalid; if <2 remain valid, job fails per FEWER_THAN_TWO | Structured error in job result |
| INVALID_STATS | Candidate stat block violates AR25 | One bounded repair pass; if still invalid, candidate invalid | Per FEWER_THAN_TWO; `stat_failure_message` detail |
| BUDGET_EXCEEDED | Repair pass would exceed `max_llm_calls` | Job fails | `BudgetExceededError` → `job_failed` (existing semantics) |
| DETERMINISM | Same world state + same ask, prompt rebuilt | Byte-identical prompt (AR6) | Pinned by test |
| FOREIGN_OWNER | Non-owner (or unauthed) lists candidates | 401/404 indistinguishable | Envelope per auth conventions (the "submits" half stays unauthenticated per Ask First; submit-side auth is a future story) |

</frozen-after-approval>

## Code Map

- `backend/app/store/jobs.py:48` -- `JOB_KINDS` frozenset; extend with `generate`. `enqueue_job` (:167) + `_validate_build_in_payload` (:451) = pattern for a new `_validate_generate_payload` (ask: non-empty, bounded length).
- `backend/app/store/models.py:131-137` -- `ck_job_kind` SQLite CHECK (schema change: scratch-DB-per-test, deploy DB needs migration decision). Add `ProposedCandidate` table: ULID id, campaign_id, job_id, kind, status, payload JSON, created_at — outside entity/edge tables is the AR7 guarantee.
- `backend/app/pipeline/worker.py:100-131` -- `_run_job` dispatch: add a `generate` branch (lazy import) to the new runner; update the JOB_KINDS docstring (:17).
- `backend/app/pipeline/build_in.py:73-127` -- runner template: `session_scope` → seed/revision, `CallBudget`, fenced parse, validate, repair pass, complete/fail. Reuse the shape, not the code.
- `backend/app/pipeline/retrieval.py:11-13,86` -- `retrieve_neighborhood` (seed_ids=None = full world, entity_cap=24) + `serialize_context`: reuse verbatim; its docstring earmarks this story.
- `backend/app/pipeline/statblocks.py` -- `collect_stat_issues` (:45), `stat_block_rules_text` (:63), `spells_reference_text` (:91), repair trio (:110/:146/:196) with hard-coded `E<position>` refs — keep E-refs for candidates.
- `backend/app/pipeline/knowledge.py:471` -- `validate_stat_block`; reference tables (:30-353). AR19 narrative fields are NOT validated today — new shape validation in the runner.
- `backend/app/pipeline/budget.py` / `fencing.py` -- `CallBudget`, `strip_fence`: reuse as-is.
- `backend/app/api/jobs.py:26,78` -- `JobCreate.kind` Literal (:26) + POST (:78): extend with `generate`. NOTE: this surface is unauthenticated (pre-existing; see Ask First).
- `backend/app/api/auth.py:113` -- `get_current_account`; mirror `exports.py`/`entities.py` ownership pattern for the new authed candidates read.
- `backend/app/api/common.py:24` -- `store_error_as_http`: map any new store error (e.g. invalid generate payload → 422 code).
- `backend/app/store/read.py` -- `world_state` (committed ids for edge validation), `campaign_seed` (AR27 prompt input). No visibility hooks exist — and none may be added: proposed rows stay in their own table.
- `backend/app/providers/llm.py:24,62` -- provider injectable `Callable[..., str]`; fake-provider test pattern from `tests/test_build_in_pipeline.py:151+` (canned responses) and byte-determinism pins (:514,:579).
- `backend/tests/test_jobs.py`, `test_jobs_api.py` -- payload-validation + submit/poll conventions to mirror.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/models.py` -- add `ProposedCandidate` table + extend `ck_job_kind` CHECK with `generate` -- AR7 staging rows; kind-set lockstep.
- [x] `backend/app/store/jobs.py` -- add `generate` to `JOB_KINDS`; add `_validate_generate_payload` (payload is exactly `{ask: str}`, non-empty, length-capped) wired into `enqueue_job` -- queue accepts the new kind with validated payload.
- [x] `backend/app/pipeline/generate.py` -- new runner `run_generate`: deterministic `build_generate_prompt(seed, ask, context)` (embeds AR27 theme/custom-lore, edge vocabulary + counter semantics, AR19 output contract, `stat_block_rules_text` + `spells_reference_text`); fenced parse; AR19 shape validation (name/role/personality/triple/≥1 valid edge into committed world ids); stat validation + one bounded repair pass under `CallBudget`; stage valid candidates as `ProposedCandidate` rows (one transaction, all-or-nothing per batch); `complete_job` with staged ids; <2 valid candidates → structured `job_failed` -- the story's core.
- [x] `backend/app/pipeline/worker.py` -- dispatch branch for `generate` + docstring -- lockstep kind extension.
- [x] `backend/app/api/jobs.py` -- extend `JobCreate.kind` Literal with `generate` -- submit surface.
- [x] `backend/app/api/candidates.py` -- authed `GET /api/campaigns/{campaign_id}/candidates`: owner-only, cursor-paginated proposed-candidate rows; register router; map new errors via `api/common.py` -- observable staging surface (the accept screen consumes it in 3.3).
- [x] `backend/tests/` -- store tests (payload validation, kind CHECK, staging rows + visibility: export/`world_state` unaffected), pipeline tests (fake provider: happy path, FEWER_THAN_TWO, repair pass, budget exhaustion, byte-identical prompt pin, empty-world submit rejection), API tests (submit/poll/list, auth + foreign-campaign 404) -- I/O matrix coverage.

**Acceptance Criteria:**
- Given a committed world and a plain-language ask, when the `generate` job completes, then 2–3 proposed candidates exist, each carrying name, role, personality, secret/rumor/party-hook, an AR25-valid 5e stat block, and ≥1 typed edge to an existing committed entity (FR7, FR8, AR19).
- Given a staged candidate, when retrieval, export, or any world read runs, then the candidate is invisible — proposed rows live only in the candidates table (AR7).
- Given the same world state and the same ask, when the prompt is built twice, then it is byte-identical (AR6).
- Given any invalid scenario in the I/O matrix, when it occurs, then the job fails with a structured error and no committed state changes.

### Review Findings

**Code review of story 3-1 (2026-09-03)** — 3 layers (blind hunter, edge case hunter, verification gap). 18 raw findings, 15 after dedup: 0 decisions, 13 patches, 1 deferred, 1 dismissed. All patches applied and verified (448 tests, `make lint`/`make typecheck` clean).

**Patches:**
- [x] [Review][Patch] AR20 regression: campaign deletion hit an FK violation once candidates were staged — `delete_campaign` now cascades `proposed_candidate` before job/campaign rows [backend/app/store/campaigns.py:182]
- [x] [Review][Patch] `_job_still_running` returned False on status-read errors, wedging the FIFO (job stuck `running`, nothing claimable) — now returns True like build_in [backend/app/pipeline/generate.py:451]
- [x] [Review][Patch] `stage_candidates` not idempotent per job — crash-requeue duplicated rows; now returns existing rows for the job [backend/app/store/candidates.py:54]
- [x] [Review][Patch] Prompt demanded `C<index>` endpoints without stating the `entity[<i>]` mapping — `context_ref_pin` pins it [backend/app/pipeline/generate.py:232]
- [x] [Review][Patch] Unhashable JSON edge `type` raised TypeError aborting the whole job — str-guards drop it as BAD_EDGE [backend/app/pipeline/generate.py:355]
- [x] [Review][Patch] Shape-drop reasons and stat-repair refs used colliding E-numberings and never named candidates — one numbering (repair mirror pads shape-invalid entries) with names in every reason [backend/app/pipeline/generate.py:131]
- [x] [Review][Patch] `_migrate_job_kind` hardcoded the kind list + newest-kind sentinel — both now derive from `JOB_KINDS`; both legacy shapes tested [backend/app/store/db.py:118]
- [x] [Review][Patch] `proposed_candidate` lacked closed-set CHECKs — `ck_proposed_candidate_kind/status` added from the constants [backend/app/store/models.py:218]
- [x] [Review][Patch] Successful jobs were silent about dropped candidates — `complete_job` result carries `dropped: [{ref, name, reason}]` [backend/app/pipeline/generate.py:218]
- [x] [Review][Patch] Cancel between final poll and `complete_job` staged ghost rows for a cancelled job — `discard_candidates` on `JobStateConflictError`; two cancel-race tests added (previously zero coverage) [backend/app/pipeline/generate.py:224]
- [x] [Review][Patch] Generate retry whose world was emptied by undo returned 422 instead of the documented 409 — duplicate check now precedes the empty-world gate [backend/app/store/jobs.py:418]
- [x] [Review][Patch] Docstring overclaims: exactly-3 rationale clause, "never a dangling staged edge" scoped to the staging window, API-test "envelope codes" → scenario names

**Deferred:**
- [ ] [Review][Defer] Worlds beyond the 24-entity retrieval cap silently lose ask-relevant context (spec-2-3 item narrowed for the candidates path) — ledgered in deferred-work.md [backend/app/pipeline/generate.py]

### Review Findings (round 2, 2026-09-04)

**Fresh 3-layer review of the final code (blind hunter, edge case hunter, verification gap)** — 13 raw findings, 5 after dedup: 5 patches applied, 2 deferred, 1 accepted. All 13 round-1 patches independently re-verified applied-and-correct by all three lenses. Full verification: 451 tests, ruff, mypy, eslint, vue-tsc clean.

**Patches:**
- [x] [Review][Patch] Oversized `C<index>` edge refs (>=4301 digits) raised CPython's int-conversion ValueError, aborting the WHOLE job instead of dropping one malformed edge as BAD_EDGE — digit length now bounded (6) before `int()` [backend/app/pipeline/generate.py:368]
- [x] [Review][Patch] Non-finite floats (json.loads accepts NaN/Infinity/1e999→inf) staged through AR24's unvalidated passthrough and 500'd the candidates read (Starlette `allow_nan=False`) — the runner drops such candidates as malformed (dropped summary), and `stage_candidates` repeats the strict-JSON check as the write-boundary backstop [backend/app/pipeline/generate.py:193, backend/app/store/candidates.py:86]
- [x] [Review][Patch] A crash-requeued generate job whose re-run FAILED kept the first run's staged rows under a failed job (served to the accept screen, contradicting FEWER_THAN_TWO "nothing staged") — the worker now discards a generate job's staged rows on any failure before `fail_job`; first-run failures remove zero rows [backend/app/pipeline/worker.py:75]
- [x] [Review][Patch] `test_more_than_three_candidates_sliced` was vacuous (its 4th entry was shape-invalid, so removing the slice changed nothing) — rewritten with 4 VALID candidates asserting `candidate_ids == 3` [backend/tests/test_generate_pipeline.py:464]
- [x] [Review][Patch] `InvalidCandidateError` unmapped in `store_error_as_http` (would 500 if it ever crossed an API boundary) — mapped to 422 alongside `InvalidJobInputError` [backend/app/api/common.py:88]

**Tests added (round 2):** oversized-ref drop, non-finite stat-block candidate drop, store strict-JSON rejection, crash-requeue re-run failure discard, non-vacuous slice pin.

**Deferred (ledgered in deferred-work.md):**
- [ ] [Review][Defer] Hard crash between the staging commit and the conflict-discard leaves a cancelled job's rows listable — story 3.2's reject/timeout lifecycle garbage-collects them
- [ ] [Review][Defer] Failure paths (FEWER_THAN_TWO/BAD_EDGE/INVALID_STATS/BUDGET_EXCEEDED) don't assert the world graph unchanged (AC4); runner-level delete-mid-job composition untested — assertion-strength hardening for a later sweep

**Accepted:** FOREIGN_OWNER matrix row's "submits" half — POST /api/jobs stays unauthenticated by the Ask First constraint (submit-side auth is owned by a future story); the read half is fully covered.

## Design Notes

- Job kind named `generate` (matching the epic's proposal-kind `entity` contract: a `generate` job stages `entity` proposals). Payload: `{ask: str}`.
- The prompt requests exactly 3 candidates (a fixed count, never "2-3" in the ask itself — the runner does not solicit a chosen winner); validation keeps 2–3. Failing at <2 (rather than shipping one) keeps the "2–3 candidates" contract honest and testable; regeneration of the ask is the DM's retry. The runner never picks a winner — selection is the DM's (inversion guarantee #2 shapes 3.3, but the runner already returns candidates undifferentiated).
- Proposed rows are written by the runner through a store-module function (never raw SQL in `pipeline/`) — the store stays the sole writer (AD-1); this staging function is not the commit path and produces no revision/event.
- Delete-during-active-job (deferred-work 2.5 item) is naturally narrowed here: edge endpoints are validated against committed ids at staging time; a delete landing mid-job surfaces as invalid-candidate, not a crash.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all green including new generate/store/candidates tests
- `make lint && make typecheck` -- expected: clean
- (frontend untouched: no npm runs required)

**Manual checks (if no CLI):**
- With backend up + a build-in-committed campaign: POST /api/jobs kind=generate with an ask; poll job to done; GET the candidates endpoint — 3 proposed candidates with stat blocks + edges; export JSON unchanged (candidates invisible).

## Suggested Review Order

**The runner (story core)**

- Orchestration: deterministic prompt → provider → fenced parse → validate → repair → stage; commits nothing.
  [`generate.py:84`](../../backend/app/pipeline/generate.py#L84)
- Byte-deterministic prompt builder; `context_ref_pin` states the C-index ↔ entity mapping.
  [`generate.py:253`](../../backend/app/pipeline/generate.py#L253)
- AR19 shape + edge validation; malformed edges drop individually, never abort the job.
  [`generate.py:355`](../../backend/app/pipeline/generate.py#L355)
- Cancel polls return True on status errors (no FIFO wedge); pre-staging polls are no-ops.
  [`generate.py:451`](../../backend/app/pipeline/generate.py#L451)
- Cancel-vs-stage window: `JobStateConflictError` discards ghost rows before propagating.
  [`generate.py:224`](../../backend/app/pipeline/generate.py#L224)

**Staging store (AR7)**

- `ProposedCandidate` model outside entity/edge tables + closed-set CHECKs.
  [`models.py:199`](../../backend/app/store/models.py#L199)
- Idempotent staging: re-staging a job returns its existing rows.
  [`candidates.py:54`](../../backend/app/store/candidates.py#L54)
- Ghost-row discard path used by the runner's cancel window.
  [`candidates.py:102`](../../backend/app/store/candidates.py#L102)
- AR20 cascade: staged candidates die with their campaign.
  [`campaigns.py:182`](../../backend/app/store/campaigns.py#L182)

**Queue integration (kind lockstep)**

- `generate` accepted with validated `{ask}` payload; duplicate check precedes the empty-world gate (409, not 422).
  [`jobs.py:418`](../../backend/app/store/jobs.py#L418)
- Migration derives list + sentinel from `JOB_KINDS`; both legacy shapes handled in one pass.
  [`db.py:118`](../../backend/app/store/db.py#L118)
- Worker dispatch: lazy import, generate commits nothing.
  [`worker.py:90`](../../backend/app/pipeline/worker.py#L90)

**API surface**

- Submit surface: `generate` in the kind Literal.
  [`jobs.py:32`](../../backend/app/api/jobs.py#L32)
- Authed owner-only, cursor-paginated candidates read.
  [`candidates.py:45`](../../backend/app/api/candidates.py#L45)

**Tests (peripherals)**

- Happy path: 3 valid candidates staged, full AR19 shape.
  [`test_generate_pipeline.py:175`](../../backend/tests/test_generate_pipeline.py#L175)
- Cancel races: pre-staging no-op; post-staging ghost discard.
  [`test_generate_pipeline.py:793`](../../backend/tests/test_generate_pipeline.py#L793)
- Crash-requeue idempotency + retry-after-undo 409 + AR20 cascade.
  [`test_generate_pipeline.py:837`](../../backend/tests/test_generate_pipeline.py#L837)
- Byte-identical prompt pin (AR6).
  [`test_generate_pipeline.py:687`](../../backend/tests/test_generate_pipeline.py#L687)
- AR7 invisibility: export JSON unchanged with staged rows present.
  [`test_generate_api.py:312`](../../backend/tests/test_generate_api.py#L312)
