---
title: 'Regeneration — Whole and Per-Section'
type: 'feature'
created: '2026-09-04'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'aa47a6b'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
---

<!-- Target: 900–1300 tokens. Above 1600 = high risk of context rot.
     Never over-specify "how" — use boundaries + examples instead.
     Cohesive cross-layer stories (DB+BE+UI) stay in ONE file.
     IMPORTANT: Remove all HTML comments when filling this template. -->

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The DM cannot regenerate anything. On the accept screen (3-3) the only per-section option is manual textarea editing; there is no LLM re-roll (whole or per-section) for a proposed candidate, and no way to regenerate an accepted entity — the accept screen promises "regenerates any part she dislikes without disturbing the rest", and the epic requires regenerating an existing entity to stage a new candidate and never mutate accepted state (FR10, AR4).

**Approach:** A new `regenerate` job kind. Payload `{"target": {"kind": "entity"|"candidate", "id": "<ULID>"}, "sections": ["profile_section", ...] | null}` — `null`/absent = whole character, a non-empty list = exactly those AR24 content sections. The runner seeds retrieval from the target (entity: the entity's ULID; candidate: its staged edge endpoints), builds a byte-deterministic prompt embedding the target record with the to-preserve sections verbatim, and the regenerated profile is spliced so preserved sections are byte-identical *by construction* (runner-overwritten, never model-fidelity-dependent). A re-rolled *proposed* candidate replaces that row's payload in place (one row per intent, edges untouched). A regenerated *accepted entity* stages a brand-new proposed row carrying `regenerates_entity_id`; accepting it commits an in-place `entity_updated` (existing ULID, edges preserved, staged non-duplicate edges added) — the previous revision is the undo. Reject/GC of the staged row leaves the committed entity untouched.

## Boundaries & Constraints

**Always:**
- Store is the sole world-state writer (AD-1): staging writes no revision (AR7); accept is exactly one atomic transaction (AD-15); the regen runner never calls `commit_subgraph`.
- Regenerated record must be a valid full AR24 shape (`payload_section_violations`); preserved sections byte-identical; identical bullets apply to unknown keys (AR24 forward compat: extra keys pass through from the target record untouched).
- Closed regenerable-section set (defined in `store.candidates`, re-exported to the pipeline): the AR24 content sections — `personality, secret, rumor, party_hook, appearance, background, goals, relationships, voice_style, catchphrases, stat_block, world_integration, boss`. Identity anchor (`name, role, level_cr, race_type, class_profession, alignment`) and `edges` are NOT regenerable (hand-edit / 3-4 territory). A section outside the set, or `boss` on a non-BBEG/Monster target role, is rejected (422 at enqueue; zero rows).
- In-place accept preserves the target entity's ULID and every existing committed edge (edge re-targeting forbidden, AD-2); staged edges, when present, must be non-duplicate against the existing graph (DuplicateEdge 422) and resolve to committed endpoints (DanglingEdge 422); an entity-regen candidate may carry `edges: []` (the existing edges are the anchor).
- Prompt stays byte-deterministic (AR6/AD-16): seed fields + target record + preserved sections + retrieved neighborhood only — no ids/timestamps/job state. One LLM call, through `CallBudget` (BudgetExceededError fails the job).
- `JobCreate.kind` literal, `JOB_KINDS`, the DB `ck_job_kind` constraint, and the db.py constraint rebuild all admit `regenerate`; worker dispatch + ghost-discard branch cover it.
- Ownership/404s follow the established pattern: foreign/unknown campaign 404 before anything; unknown or foreign target id is a 404 at enqueue (zero rows); 4xx never changes state (AR15).
- Frontend reuses the WS job-progress loop; a re-roll visibly discards any in-flight manual draft on that candidate (3-3 precedent — never a silent merge). No UI e2e (AR22); vitest only.

**Ask First:**
- If in-place accept or the re-roll replace-in-place needs to alter the 3-3/3-4 frozen accept/override contract beyond the new `regenerates_entity_id` path, halt and present the tradeoff.

**Never:**
- No direct PATCH of committed entity state — regeneration always stages a proposal first; mutating accepted state only ever happens at accept, in one transaction, and is undoable.
- No edge re-targeting, no semantic counter changes, no new edge vocabulary (AD-2/AD-5).
- No per-section storage or merge primitive in the DB — preservation is prompt context + runner splice.
- No UI per-section picker on WorldView (accepted-entity cards) — whole-character re-roll there; per-section re-roll lives on the accept screen where sections render (the staged candidate from a whole re-roll is itself per-section re-rollable).
- No portraits/exports (Epic 4/5), no rebase-or-reject UI (3-6), no steering-ask field (`sections` re-roll is one tap; a fresh ask is the steer).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| WHOLE_REGEN_ENTITY | regenerate `target=entity X, sections=null` | New proposed row `regenerates_entity_id=X`; X untouched; accept → in-place `entity_updated`, ULID + edges preserved, one revision, undo restores X byte-identical | Unknown/foreign X → 404 at enqueue, zero rows; duplicate staged edge vs existing graph → 422 accept |
| SECTION_REGEN_CANDIDATE | regenerate `target=candidate C, sections=["personality"]` | Same row C, payload replaced; every other section + unknown keys + `edges` byte-identical | C settled/unknown/foreign → 404/422, no change |
| SECTION_REGEN_ENTITY | regenerate `target=entity X, sections=["personality","stat_block"]` | New proposed row: those sections regenerated, all others byte-identical from X's committed record; accept → in-place `entity_updated` | Unknown section → 422 at enqueue; `boss` on non-BBEG/Monster → 422 |
| LLM_BUDGET | Regen job reaches max_llm_calls | Job fails `budget exceeded`, no staged rows, no revisions | BudgetExceededError → fail_job; ghost discard on cancel race |
| MODEL_SHAPE_VIOLATION | Regenerated profile fails AR24 shape or disallowed section edited | Job fails; zero rows; committed world untouched | JobPayloadError → fail_job (envelope) |
| RE_ROLL_WITH_DRAFT | DM has unsaved manual edits on C, clicks re-roll | Draft visibly discarded (3-3 precedent), regen job runs, row replaced on job done | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/store/jobs.py` — `JOB_KINDS` :50 + `ck_job_kind` in `models.py:180-183` gain `regenerate`; `enqueue_job` :391-397 dispatch → `_validate_regenerate_payload` (shape + closed-section set) resolves the target (entity/candidate, campaign owner) → NotFound 404 / InvalidJobInput 422 before any row.
- `backend/app/store/models.py:204-228` — `ProposedCandidate` gains nullable `regenerates_entity_id: String(26)`; db.py constraint rebuild already derives from `JOB_KINDS` (`db.py:128-160`); new column needs an ALTER in the migration path.
- `backend/app/store/candidates.py` — add `REGEN_SECTIONS` closed set (re-exported to pipeline, `__all__`); `stage_candidates` :206 gains optional `target_entity_id` (row column, placed on the new row); new `replace_candidate_payload(campaign_id, candidate_id, payload)` (row must be `proposed`, same campaign, payload re-validated via `payload_section_violations`); `accept_candidate` :291 branches on `row.regenerates_entity_id` → in-place `EntityInput(id=target)` + staged edges via existing `_accept_edge`/`_commit`, provenance `accepted_entity_id=target`.
- `backend/app/api/jobs.py:28-38` — `JobCreate.kind` Literal gains `"regenerate"`; no new route (reuses POST /api/jobs).
- `backend/app/pipeline/regenerate.py` (new) — `run_regenerate`: resolve target fresh (entity via `world_state`, candidate via its staged endpoints as retrieval seeds); `build_regenerate_prompt` (seed + target record + preserved-sections instruction + `serialize_context`); `CallBudget`-gated single call; parse 1-candidate envelope (reuse `_parse_candidates`), validate via `payload_section_violations`, splice = regenerated sections trimmed + preserved sections/unknown keys taken verbatim from the target record + `edges` from record (candidate) or `[]` (entity); stage: candidate → `replace_candidate_payload`, entity → `stage_candidates(..., target_entity_id=...)`; ghost-discard on cancel race (mirror `run_generate`).
- `backend/app/pipeline/worker.py:109-140` — dispatch branch + the `run_next_job` exception discard branch (:124) covers `regenerate`.
- `backend/app/pipeline/retrieval.py` — `retrieve_neighborhood(seed_ids=...)` seeded by target ULID (entity) or candidate's committed endpoints.
- `backend/tests/test_regenerate_api.py`, `test_regenerate_pipeline.py`, `test_candidate_lifecycle.py` — coverage below (enqueue validation lives in `test_regenerate_api.py`; there is no `test_store_jobs.py`).
- `frontend/src/api/schema.ts` — regen via `npm run gen:api` (backend on :8000); `frontend/src/stores/jobs.ts` — `submitRegenerate(target, sections|null)` mirroring `submitBuildIn` :66-77; `frontend/src/views/CandidatesView.vue` — per-section Re-roll button (next to each section's Edit toggle) + whole Re-roll; draft-discard on re-roll (3-3 precedent :112); regenerating-candidate rows re-sync through the view's kind-agnostic `onJobMessage` (WS job_done) — no candidates-store change; `frontend/src/views/WorldView.vue` — per-entity-card Regenerate (whole) button staging the job, surfaced in CandidatesView; `frontend/src/stores/world.ts` — no new world writes (regen stages, does not commit).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/jobs.py` + `backend/app/store/models.py` — [x] `regenerate` kind (JOB_KINDS, model literal, db.py rebuild) + `_validate_regenerate_payload` + `regenerates_entity_id` column migration.
- [x] `backend/app/store/candidates.py` — [x] `REGEN_SECTIONS`, `stage_candidates(..., target_entity_id)`, `replace_candidate_payload`, `accept_candidate` in-place branch.
- [x] `backend/app/pipeline/regenerate.py` + `backend/app/pipeline/worker.py` — [x] runner (prompt, splice, staging) + dispatch/discard.
- [x] Backend tests — [x] enqueue validation (shape/sections/boss-role/target 404), prompt determinism, byte-identical preservation + unknown-key pass-through, replace-in-place, in-place accept (ULID/edges preserved, entity_updated, dup-edge 422, undo round-trip), budget exceed, cancel-race ghost discard.
- [x] `frontend/src/api/schema.ts` [x] regen; `frontend/src/stores/jobs.ts` submitRegenerate; CandidatesView re-roll (whole + per-section) with draft-discard; WorldView whole-regen entry.
- [x] Frontend vitest — [x] re-roll payload + WS resync + draft discard; WorldView regenerate stages job; existing suites stay green.

**Acceptance Criteria:**
- Given the accept screen, when the DM re-rolls one section of a proposed candidate, then exactly that section changes (re-rolled by the LLM), every other section and the staged edges are byte-identical, and the job surfaces via WS progress.
- Given an accepted entity, when the DM regenerates it (whole), then a new proposed candidate appears, the committed entity is untouched, and accepting it replaces the entity in place in one revision — ULID and edges preserved, prior content restored by undo.
- Given any regenerate job, when it fails or is cancelled, then zero staged rows remain and no revision is written.

## Design Notes

- **Byte-identical by construction:** the runner never trusts the model to echo. After parse+validation, the staged payload is `dict(target_record)` with only the requested sections overwritten from the model's output (trimmed) — preserved sections, unknown keys, and `edges` are copied from the record. "Byte-identical" is therefore an assembly invariant, tested directly.
- **One row per intent:** a re-rolled *candidate* replaces its own row (no stacking); a regenerated *entity* is a new proposal because the committed entity must remain visible until the DM accepts. `regenerates_entity_id` is set only on entity-targeted staging; the fresh-ULID accept path (3-2/3-3/3-4) is untouched for rows without it.
- **In-place accept is the only legal regen-entity commit:** a fresh ULID would create a second entity, leaving the original's edges dangling (orphan) or duplicating edge pairs (DuplicateEdge) — the commit path's in-place `entity_updated` (already proven by `test_regeneration_replaces_in_place_keeps_ulid_and_inbound_edges`, `test_store.py:267`) is AD-2-compliant.
- **Identity anchor is not re-rollable:** name/role re-rolls would invalidate references and are hand-edit (3-6) territory; a whole re-roll regenerates the content sections only.
- Do NOT intercept the 2.3 counter-semantic deferral; staged edge validation is endpoint/vocab/int only.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — all green incl. new regen API/pipeline/store tests.
- `make lint && make typecheck` — clean.
- `cd frontend && npm test -- --run` — vitest green incl. re-roll/regenerate tests.

**Manual checks (if no CLI):**
- With backend up: accept screen → re-roll one section → row updates with other sections unchanged; WorldView → Regenerate on an entity → new proposed candidate appears → accept → entity card content replaced (ULID stable in URL/export), undo restores; a cancelled regen leaves no proposed rows.

## Suggested Review Order

**The regenerate job contract**

- Entry point: enqueue-time validation resolves the target, gates sections/boss-role, 404/422 before any row.
  [`jobs.py:550`](../../backend/app/store/jobs.py#L550)

- The wire literal admits the new kind alongside `JOB_KINDS`.
  [`jobs.py:32`](../../backend/app/api/jobs.py#L32)

- Follow-up fix: the DB CHECK constraint admits `regenerate` (review round 1).
  [`models.py:180`](../../backend/app/store/models.py#L180)

**The store's in-place accept — the invarianvce seam**

- In-place branch: same ULID, `entity_updated` one revision, kind/text preserved, dup-edge 422.
  [`candidates.py:498`](../../backend/app/store/candidates.py#L498)

- Staging gains the entity-target column; the replace-in-place write for candidate re-rolls.
  [`candidates.py:230`](../../backend/app/store/candidates.py#L230)

- The origin-ULID column migration for upgraded databases.
  [`db.py:291`](../../backend/app/store/db.py#L291)

**The runner — byte-identical by construction**

- Fresh target resolution, budget gate, cancel-race polls, by-construction splice.
  [`regenerate.py:85`](../../backend/app/pipeline/regenerate.py#L85)

- Whole re-roll excludes `boss` for non-BBEG targets (review round 1).
  [`regenerate.py:242`](../../backend/app/pipeline/regenerate.py#L242)

- Deterministic prompt; stat/spells embed only when mechanics are re-rolled.
  [`regenerate.py:305`](../../backend/app/pipeline/regenerate.py#L305)

**The re-roll surfaces**

- Per-section + whole re-roll, draft-discard after success, row buttons gated while rolling.
  [`CandidatesView.vue:505`](../../frontend/src/views/CandidatesView.vue#L505)

- Whole-entity Regenerate gated to AR24 cards.
  [`WorldView.vue:216`](../../frontend/src/views/WorldView.vue#L216)

- The store action posting regenerate jobs.
  [`jobs.ts:85`](../../frontend/src/stores/jobs.ts#L85)

**Peripherals — tests and types**

- Runner coverage: whole/per-section entity + candidate, budget, cancel races, determinism.
  [`test_regenerate_pipeline.py:340`](../../backend/tests/test_regenerate_pipeline.py#L340)

- REST: 201 happy paths, 404/422 branches, positive boss path, zero-row guarantees.
  [`test_regenerate_api.py:195`](../../backend/tests/test_regenerate_api.py#L195)

- Migration-upgrade pin + lifecycle in-place accept cases.
  [`test_candidate_lifecycle.py:853`](../../backend/tests/test_candidate_lifecycle.py#L853)

- Re-roll UI tests incl. WS job_done resync, draft preservation, disable-in-flight.
  [`CandidatesView.test.ts:467`](../../frontend/src/views/CandidatesView.test.ts#L467)

- Regenerated OpenAPI types (regen kind + response shapes).
  [`schema.ts:12`](../../frontend/src/api/schema.ts#L12)