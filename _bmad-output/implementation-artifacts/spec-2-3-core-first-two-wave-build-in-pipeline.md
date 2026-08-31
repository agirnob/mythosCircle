---
title: 'Core-first two-wave build-in pipeline: deterministic retrieval, budgeted waves, direct commit'
type: 'feature'
created: '2026-08-31'
status: 'done'
review_loop_iteration: 0
baseline_commit: '786dd81'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/specs/spec-mythosCircle/conventions.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/spec-2-1-guided-build-in-flow.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/spec-2-2-typed-edge-vocabulary-on-commit.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** build_in jobs enqueue and validate (2.1), but the worker hard-fails every one — `worker.py:81-82` raises `"the build-in runner lands in Story 2.3"` — so a submitted world never materializes. There is no pipeline code (no wave scheduling, no retrieval, AR6/AD-16), and 2.1's build-in ACs end at the wire + terminal state.

**Approach:** land the build-in runner as one job, two internal waves. Wave 1 digests the named sections (key figures, places, factions) into a subgraph of `character`/`faction`/`place` entities + typed edges and commits it atomically (one revision). Wave 2 digests `notes` into a second subgraph, every new entity wired by ≥1 typed edge into the committed core, committed against wave 1's revision. Prompts are pure, byte-deterministic functions of state (AD-16); the AR21 budget gates every LLM call; malformed or invalid output fails the job with an error event and zero (or committed-core-only) world damage.

## Boundaries & Constraints

**Always:**
- One job = one build-in; wave 1 commits before wave 2; both through `commit_subgraph` (AD-1). Wave 2 passes `base_revision` = wave 1's revision; a DM edit landing between waves ⇒ `StaleRevisionError` ⇒ job fails, never a silent overwrite (AD-2).
- **Commit-path reconciliation (owner-reviewed): build-in commits directly — the DM's submission is the acceptance act.** AR7/AD-15 proposed-subgraph staging is Epic 3's candidate lifecycle; the epic contract ("key figures generate and commit first", 2.7's "as soon as the core wave commits") supersedes the spine's AD-19 "proposed subgraph" wording for build-in. No staging tables in 2.3.
- Deterministic prompts: `build_wave1_prompt(campaign_seed, payload)` and `build_wave2_prompt(campaign_seed, notes, context)` are pure functions; ordering rowid-stable; never embed timestamps, ULIDs, or job state (AD-16); pinned byte-identical by tests.
- Retrieval (AR6): `pipeline/retrieval.py` — bounded deterministic BFS over typed edges, `depth=1` default, `entity_cap=24` (seed), rowid-order frontiers (no randomness, no similarity, no full-text). Wave 2's context = neighborhood of wave-1 entities; serialization carries hard truths only (kind, name, text, data, edges with type + counter).
- Budget (AR21): every provider call goes through `CallBudget`; exceeding ⇒ `BudgetExceededError` ⇒ job fails with an error event. `BudgetExceededError`/`CallBudget` move to `pipeline/budget.py` (worker + build_in share; worker's `_CallBudget` alias is removed — clean cutover, test imports updated).
- LLM output contract: one JSON object per wave — `{"entities":[{"ref","kind","name","text"?,"data"?}],"edges":[{"src","dst","type","counter"?}]}` — parsed after stripping optional markdown fences (malformed ⇒ job fails, never partial). Structural validation before any commit: kinds in `{character,faction,place}`; `type in EDGE_TYPES` (2.2's contract — the prompt carries the vocabulary + `EDGE_COUNTER_SEMANTICS`); counters ints; every src/dst ref resolves (wave 2: `C<idx>` refs into the committed context + wave refs); entity names non-blank. Each entity gets a runner-generated ULID (`ids.new_id()`) so edges wire before commit.
- No orphans (2.5 foundation, FR2/FR4): each wave-1 entity has ≥1 edge within the wave-1 subgraph; each wave-2 entity has ≥1 edge whose other endpoint is a wave-1 (existing) entity. Violation fails the job with a message naming the orphan. The world stays fully networked at every commit.
- Wave 2 runs only when `notes` is non-blank (else skipped). Progress: `report_progress(job.id, 0.5)` after wave 1, `1.0` after wave 2; `complete_job(job.id, result={"waves":[{"wave","revision_id","entities","edges","entity_ids"}...],"entity_count","edge_count"})`.
- Cancel-race poll (same as the text path, worker.py:90-99) before each wave's provider call: a cancelled job is a no-op — no `complete_job`/`fail_job` on a terminal job.
- Retro item 2: cursor-pagination scaffold shared via `core/pagination.py` (`paging(items, limit)`, `anchor_rowid(session, model, cursor, *, missing_error)`); jobs' unknown-cursor miss becomes 422 `InvalidJobInputError` (was silent page-1 reset); campaigns' behavior (foreign/deleted ⇒ `CampaignInputError`) unchanged and stays pinned.
- 2.2 deferred hardening: `EDGE_COUNTER_SEMANTICS` becomes `MappingProxyType`, `edge_counter_semantic` returns `Literal["amount","score","intensity","neutral"]` (contents unchanged — pins stay green); build_in validates type membership via `EDGE_TYPES` before resolving.
- Layering: pipeline→store→core only; nothing outside `store/` writes world state; providers stay leaves (AD-1, AD-13).

**Ask First:** any of the frozen decisions above the owner wants changed at review (wave composition, direct commit, orphan rule, one-job-two-waves, wave-2-failure-keeps-core).

**Never:**
- No staging/proposal tables or candidate lifecycle in 2.3 (Epic 3). No unmatched re-generation/repair passes (2.4 owns AR25 repair). No frontend/wire changes (2.7 renders). No second job for wave 2 (a separate job would let other campaigns' jobs interleave — the FIFO fairness rule stays, AD-3). No LLM retry loops (one call per wave; fail loud). No embeddings, similarity, or full-text search (AD-16). No timestamps/ULIDs/queue state inside prompts. No world-state writes outside `commit_subgraph`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| WAVE1_ONLY | notes blank; provider returns valid wave-1 JSON | one revision; entities+edges committed; progress 0.5; job `succeeded`; result has wave 1 only | N/A |
| TWO_WAVES | notes non-blank | wave 1 commits first; wave 2 commit on base=revision1; every wave-2 entity edges into core; job `succeeded` | N/A |
| MALFORMED_OUTPUT | non-JSON / wrong shape / fence-wrapped | job `failed`, zero commits, `job_failed` + `queue_changed`; FIFO flows | error names wave + reason |
| BAD_VOCAB | edge type outside EDGE_TYPES | job `failed`, wave-1 zero commits | error names the type |
| ORPHAN | entity with no required edge | job `failed`, that wave zero commits; if wave-2 failure after wave 1, committed core stays | error names the entity |
| BUDGET_EXCEEDED | `max_llm_calls=N` | call N+1 refused before HTTP; job `failed`; committed core (if wave 1 landed) stays | error names budget |
| DETERMINISM | same seed+payload / same seed+notes+context | prompts byte-identical across calls | N/A |
| CANCEL_MIDRUN | cancel between waves | no wave-2 call; job stays `cancelled`, no terminal conflict | N/A |
| STALE_BASE | DM edit lands between waves | `StaleRevisionError` ⇒ job `failed`; no silent overwrite (AD-2) | N/A |
| RETRIEVAL_CAP | world > 24 entities | context ≤ 24 entities, rowid-stable, byte-identical across calls | N/A |
| UNKNOWN_CURSOR | fabricated cursor ULID on `list_jobs` | `InvalidJobInputError` (422) — was silent page 1 (retro item 2) | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/worker.py` — replace the build_in branch (L81-82) with a call to `run_build_in(job, provider, settings)`; import `CallBudget`/`BudgetExceededError` from `pipeline/budget.py`; keep `_error_message` (L136-144) and the cancel-race poll pattern (L90-99); `complete_job`/`fail_job`/`report_progress` stay untouched.
- `backend/app/pipeline/budget.py` — NEW: `BudgetExceededError` + `CallBudget` moved verbatim from worker.py (L37-41, L121-133). Worker's `_CallBudget` name removed; `test_worker.py:19-25` import updated.
- `backend/app/pipeline/build_in.py` — NEW: `run_build_in(job, provider, settings)` (seed read, budget, cancel-poll, wave orchestration, commits, progress, result); `build_wave1_prompt`/`build_wave2_prompt` (pure/deterministic — campaign seed via `read.campaign_seed`, payload sections trimmed in order, vocabulary + counter semantics inline); `parse_build_output(text)` (fence-strip → `json.loads` → shape checks → `JobPayloadError` on failure); `_validate_subgraph` (kinds, refs, vocabulary, int counters, orphan rule); `ids.new_id()` per entity.
- `backend/app/pipeline/retrieval.py` — NEW: `retrieve_neighborhood(campaign_id, seed_ids=None, *, depth=1, entity_cap=24) -> (entities, edges)` (rowid-order BFS over `read.world_entities`/`read.world_edges`); `serialize_context(entities, edges) -> str` (hard truths, no ids/timestamps).
- `backend/app/store/read.py` — add session-scoped wrappers: `campaign_seed(campaign_id) -> Campaign | None`; `world_entities(campaign_id) -> Sequence[Entity]`; `world_edges(campaign_id) -> Sequence[Edge]` (reuse `world_state` L57; rowid order is the AD-16 determinism contract).
- `backend/app/store/commit.py` — `EDGE_COUNTER_SEMANTICS` L45-58 → `MappingProxyType` + resolver `-> Literal["amount","score","intensity","neutral"]` (2.2 defer; contents unchanged).
- `backend/app/core/pagination.py` — add `paging(items, limit) -> (page, next_cursor)` and `anchor_rowid(session, model, cursor, *, missing_error) -> int`; `decode_cursor` unchanged.
- `backend/app/store/jobs.py` — `list_jobs` (L356-379) uses `paging`; `_after_rowid` (L466-487): fabricated cursor no longer returns -1 — raise `InvalidJobInputError("cursor names no job: ...")` (retro item 2, 422); foreign-campaign cursor rejection stays pinned.
- `backend/app/store/campaigns.py` — `list_campaigns` (L92-110) uses `paging` + `anchor_rowid`; behavior unchanged (`CampaignInputError` on foreign/deleted — pins at test_campaigns.py:206-217 stay green).
- `backend/tests/test_worker.py` — budget imports from `pipeline.budget`; `test_build_in_job_fails_not_wedges_and_queue_flows` (L215-233) replaced by success-path (fake JSON provider) + malformed-fail-not-wedge tests.
- `backend/tests/test_build_in_pipeline.py` — NEW: WAVE1_ONLY, TWO_WAVES, MALFORMED_OUTPUT, BAD_VOCAB, ORPHAN, BUDGET_EXCEEDED, DETERMINISM, CANCEL_MIDRUN, RETRIEVAL_CAP, UNKNOWN_CURSOR rows (fake provider / `httpx.MockTransport`; no live LLM).
- `backend/tests/test_pagination.py` + `backend/tests/test_jobs.py` — scaffold unit tests; fabricated-cursor pin (test_jobs.py:603-605) flips from page-1 reset to `InvalidJobInputError`.
- `backend/tests/test_store.py` — semantics-map pins unchanged (MappingProxyType equality).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/pipeline/budget.py` -- move `BudgetExceededError` + `CallBudget` verbatim; worker imports + test imports updated. Clean cutover.
- [x] `backend/app/store/read.py` -- add `campaign_seed`, `world_entities`, `world_edges` session-scoped wrappers (rowid order).
- [x] `backend/app/pipeline/retrieval.py` -- bounded deterministic BFS + hard-truth serialization; determinism + cap tests.
- [x] `backend/app/pipeline/build_in.py` -- wave orchestration, deterministic prompts, output parse/validate (vocabulary via store contract, orphan rule), commits, budget gate, cancel poll, progress, result shape.
- [x] `backend/app/pipeline/worker.py` -- dispatch `build_in` to `run_build_in`; drop the Story-2.3 fail-bookmark.
- [x] `backend/app/store/commit.py` -- MappingProxyType + Literal hardening (2.2 defer).
- [x] `backend/app/core/pagination.py` + `backend/app/store/{jobs,campaigns}.py` -- shared scaffold; jobs unknown-cursor 422 (retro item 2).
- [x] `backend/tests/*` -- new pipeline tests + rewritten worker test + pagination pins (I/O matrix rows).
- [x] Sprint sync `2-3-core-first-two-wave-build-in-pipeline` → in-progress → review (after this implementation), retro item 2 → done.

**Acceptance Criteria:**
- Given valid build-in notes, when the job runs, then key figures + their typed edges commit before any second-wave entity (AR5), each wave is one atomic commit, and the world stays fully networked (no orphan entity at any commit — FR2/FR4 foundation).
- Given the same world state + the same request, when a prompt is built, then it is byte-identical (deterministic-prompt invariant, AR6, AD-4, AD-16) — pinned by unit tests across both waves.
- Given a build-in job with a max LLM-call budget, when it would exceed it, then the job fails with an error event before the extra HTTP call (AR21); a wave-2 failure after wave 1 leaves the committed core intact (documented resilience, no compensating undo).
- Given a fabricated or foreign cursor on the jobs list, then it is a 422 user error, never a silent page reset (retro item 2).

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries. -->

## Design Notes

**Why direct commit (reconciliation).** The spine (AD-19/AD-15) stages build-in output as a proposed subgraph; the epic contract commits it ("core wave commits first", 2.7 renders "as soon as the core wave commits", value checkpoint: "the build-in world is actually played"). The DM submitting the build-in IS the acceptance act; a staged-but-invisible world would fail every 2.7 AC and delay playability. Candidate staging (AR7, FR11) is Epic 3's plain-language-ask lifecycle and lands with its proposal tables. The spine's AD-19 wording should gain this exception in a later contract pass (retro item 6 territory — noted here, not edited here).

**Why one job, two waves.** The queue is a single serial FIFO (AD-3/NFR4); two separate jobs would let other campaigns' jobs claim the slot between waves (fairness, but breaks "the rest streams behind" atomicity and the build-in lifecycle). One job, seeded with the AR21 default budget (64 calls), runs both waves back to back; progress 0.5/1.0 marks the wave boundary on the AD-17 wire.

**Why the orphan rule lives here.** 2.5 formalizes cascade delete; its "no orphan" half is a per-commit invariant the store's dangling check already supports (endpoints may live in the same staged subgraph — commit.py:324). Enforcing ≥1 edge per entity at build time makes 2.5's delete confirmation the only remaining piece and keeps the graph connected for Phase-3 counter propagation.

**Wave-2 failure resilience.** Core-first exists so a mid-build failure still leaves a playable world: a wave-2 failure fails the job (error event, queue flows) and the committed core stays. The client (2.7) surfaces the terminal state; the DM resubmits to grow the world. No compensating undo — the failed wave wrote nothing.

**Retrieval seeds now, target-centric in Epic 3.** AR6's traversal is defined "from the target entity"; build-in has no target, so wave 2 seeds the BFS with all wave-1 entities (depth 1, cap 24). The primitive (`retrieve_neighborhood`) takes an explicit seed list, so Epic 3's candidacy reuses it unchanged with seed = the ask's target.

**Rejected alternatives.** Second job for wave 2 (interleaving, above). Auto-generated entity ids at commit (runner must know ids to wire edges — explicit `ids.new_id()`). In-prompt retry loop for malformed output (2.4's bounded repair pass is the AR25 mechanism; 2.3 fails loud and the budget stays a ceiling).

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. the new pipeline/retrieval/pagination tests (deterministic, no live LLM).
- `make lint && make typecheck` -- expected: ruff, mypy strict clean (frontend untouched — no `gen:api`).
- Smoke (owner dogfood, optional until 2.7): launch backend + a scripted fake provider; enqueue a build_in via the API; observe waves commit, `job_done` with result, and a `GET /api/campaigns/{id}/...` world read showing entities + typed edges.

## Suggested Review Order

**Runner orchestration**

- Wave-1→wave-2 sequencing, budget, cancel polls, progress, result — the design's spine
  [`build_in.py:64`](../../backend/app/pipeline/build_in.py#L64)

- Cancel-race poll shared with the text path; cancelled jobs never complete or fail
  [`build_in.py:528`](../../backend/app/pipeline/build_in.py#L528)

- build_in dispatch replaces the 2.3 fail-bookmark; lazy import breaks the import cycle
  [`worker.py:76`](../../backend/app/pipeline/worker.py#L76)

- Per-call AR21 gate moved out of worker so both runners share one guard
  [`budget.py:23`](../../backend/app/pipeline/budget.py#L23)

**Deterministic prompt + output contract**

- Pure function of seed + payload — byte-deterministic wave-1 prompt (AD-16)
  [`build_in.py:150`](../../backend/app/pipeline/build_in.py#L150)

- Wave-2 prompt: notes + bounded core context with the C/N ref scheme
  [`build_in.py:206`](../../backend/app/pipeline/build_in.py#L206)

- Fence-tolerant JSON parse with wave-attributed failures, zero partial commits
  [`build_in.py:288`](../../backend/app/pipeline/build_in.py#L288)

- Canonical refs, vocabulary, int counters, self-loop and orphan rejection
  [`build_in.py:312`](../../backend/app/pipeline/build_in.py#L312)

**Retrieval (AR6)**

- Bounded rowid-order BFS with explicit seeds, depth and entity-cap guards
  [`retrieval.py:28`](../../backend/app/pipeline/retrieval.py#L28)

- Hard-truth serialization — no ids, timestamps, or ULIDs in context
  [`retrieval.py:97`](../../backend/app/pipeline/retrieval.py#L97)

- Session-scoped rowid-order world reads: the AD-16 determinism contract
  [`read.py:58`](../../backend/app/store/read.py#L58)

**2.2 defers closed**

- Counter semantics now an immutable map with a Literal-typed resolver
  [`commit.py:68`](../../backend/app/store/commit.py#L68)

**Pagination convergence (retro item 2)**

- Shared page slice — the single cursor split every list uses
  [`pagination.py:56`](../../backend/app/core/pagination.py#L56)

- Fabricated cursors raise through missing_error, never a silent page reset
  [`pagination.py:69`](../../backend/app/core/pagination.py#L69)

- Jobs list: unknown cursor is now a 422 user error
  [`jobs.py:330`](../../backend/app/store/jobs.py#L330)

- Campaigns list: anchor-first ordering makes missing_error real and messages truthful
  [`campaigns.py:96`](../../backend/app/store/campaigns.py#L96)

**Tests**

- Full I/O matrix incl. cancel world-state, determinism, budget, and caps
  [`test_build_in_pipeline.py:126`](../../backend/tests/test_build_in_pipeline.py#L126)

- Malformed- and fabricated-cursor 422 wire pins, store + API
  [`test_jobs_api.py:312`](../../backend/tests/test_jobs_api.py#L312)