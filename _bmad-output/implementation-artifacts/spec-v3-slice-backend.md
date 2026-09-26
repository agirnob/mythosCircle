---
title: 'v3 architecture slice — backend'
type: 'feature'
status: 'done'
baseline_commit: '9e39fd29904f57c11167ccaa5bf7f27cf7bfe9ad'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-09-25/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The v3 UX + architecture (AD-26..AD-38) is final but no code exists: place-employ/control edges are rejected, edges carry no reason, Tier-2 verbs/toggles have no rows or events, and the registry/revisions surfaces are missing.

**Approach:** Implement the slice through the existing store commit path and pipeline machinery — one logbook, strict matrix, code-wins registry — with migrations for live DBs.

## Boundaries & Constraints

**Always:** one logbook (commit path only writer); strict per-kind matrix, no flatten; single-source matrix (prompts/validators/pickers/pin-test follow one edit); take-back renders as `edited`; exports read entity/edge only; no new job kind; backend-only (frontend untouched).

**Ask First:** backfill vs read-time defaults for `dial`/`archetype`/`reason` on pre-field records; revisions default/max tuning (spec says 20/100); `login_session` migration downtime handling on the deployed stack.

**Never:** frontend changes; a second pipeline, log, or writer; light-theme or non-feature tokens; secrets outside env.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Place hires | Greymarch `employs` Roth, reason set | Commits; prompt/validator/picker/pin-test agree | N/A |
| Fort holds valley | High Pass Fort `controls` Greymarch Valley + reason | Commits | N/A |
| Person part-of place | Roth `part_of` Greymarch | Rejected kind violation | BAD_EDGE, repair-or-drop |
| Blank reason (generated) | Valid edge, reason `""`/whitespace/`N/A` | One fill-blank repair (saved JSON back, single-field reply, take-only-X merge), then drop-with-audit; thin candidates fail loud via ≥1-edge floor | N/A |
| Blank reason (authored) | Picker submit, reason empty | Rejected client-side; API 422 | Envelope `{code, message, details?}` |
| Retarget edge | Cult `controls` valley → fort, old reason kept | Rejected until fresh reason supplied | Same as blank |
| Counter bump | Debt 3→4, reason stored | Preserved, no revalidation | N/A |
| Defeat + take-back | Mark Roth defeated, then take back | Two `edited` lines; scar edit between stands | N/A |
| Toggle + bundled edit | Rewrite secret + mark known in one save, then take back | One revision; take-back inverts both halves | N/A |
| Pre-reason rows | Live DB edges with NULL reason | Grandfathered; never block reads/undo | N/A |
| Revisions read | `GET .../revisions?limit=500` | Clamped to max 100, owner-only | 404 foreign campaign |
| Kinds staleness | Picker opened on new walk | Fresh payload with version token, never bundled | Commit live-validates as backstop |

</frozen-after-approval>

## Code Map

- `backend/app/store/commit.py` — commit path: `commit_subgraph:398`, `_commit:436`, `edge_kind_ok:100`, `EDGE_KIND_RULES:68`, `EDGE_TYPES:37`, edge validation in `_commit:482+`, snapshots `_edge_snapshot:1110`/`_edge_input_snapshot:1120`, `delete_edge:752`, `update_entity:824`.
- `backend/app/store/models.py` — `Edge:117` (add `reason`), new state tables, `Session:312` → `login_session`; `Event:144` (type+payload), `Revision:76`, `Job:169` (kinds incl. `regenerate`, no `enrich` — none added).
- `backend/app/store/db.py` — `init_db:63` (`create_all:90` for fresh); `_migrate_job_result:101` / `_migrate_job_kind:160` are the ALTER precedent for reason column + renames.
- `backend/app/store/undo.py` — `undo:50`, `_apply_inverse:120`, `_inverse_edge_update:293`, `_require_keys:197` (new event types must satisfy preflight).
- `backend/app/store/direct.py` — declared-edge application (`EDGE_KIND_RULES` import:50, kind inference:493); AR25 shared validator import:431 is the named store→pipeline exception.
- `backend/app/store/candidates.py` — accept path into commit (reason + state rows ride it).
- `backend/app/pipeline/build_in.py` — vocab block:317 (single-sourced), layer-2 validator:509, edges-only repair `_run_repair:223`/`_build_edge_kind_repair_prompt:590`, anchor repair schema:3872, edge type enum in schemas:3831/3902.
- `backend/app/pipeline/generate.py` — `_valid_edge` direction-fold:731, vocab list:541, stat repair:418.
- `backend/app/pipeline/regenerate.py` — `run_regenerate:140`, prompt builder:513, retry prompt:116, candidate parse:94.
- `backend/app/api/campaigns.py` — ownership + `undo:243` + `_object_body:221` patterns; new reads plug here or a new module registered like the rest.
- `backend/app/api/edges.py` — user-authored edge routes; reason becomes required.
- `backend/tests/test_build_in_pipeline.py:4215` — `test_edge_kind_ok_rules_pin` (update cells); `backend/tests/test_store.py:1277` — closed-vocabulary commit test; `backend/tests/fixtures/model_outputs/` + corpus test — verbatim-model-output fixture convention.
- `backend/app/api/exports.py` -- export reader (must carry new record keys through).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/commit.py` — matrix delta (employs/controls src += place; part_of pairs) + reason validation (create + retarget; blank = empty/whitespace/null-prose deny) + reason in snapshots — single source moves all consumers.
- [x] `backend/app/store/commit.py` (+ `backend/app/api/exports.py`) -- `dial` top-level record key + `archetype` field on place/faction (AD-36); flows through snapshots, validation, export/import/re-roll — RATIONALE: writer/renderer/exporter must agree on depth.
- [x] `backend/app/store/models.py` — `edge.reason` nullable; `entity_session_state` + `entity_knowledge_state` tables; `Session` → `login_session` rename.
- [x] `backend/app/store/db.py` — migrations for reason column, new tables, login rename (live-DB safe; Ask-First on deployed handling).
- [x] `backend/app/store/undo.py` — inverses for verb/toggle event families; rebuild-faithful full-state payloads.
- [x] `backend/app/store/candidates.py` — accept carries reason + state rows through the commit path.
- [x] `backend/app/pipeline/build_in.py` — vocab block + validator follow matrix; fill-blank reason repair (single-field schema, take-only-X merge, denylist) beside existing re-derive repair.
- [x] `backend/app/pipeline/generate.py` + `regenerate.py` — parse/stage reason; enrich = shaped regenerate request (per-kind sections, sections:null, guide, dial); same repair classes.
- [x] `backend/app/api/` — `GET revisions` (default 20/max 100, owner-gated, display-ready summaries) + `GET kinds` (renders matrix + version token); no frontend regen in this slice.
- [x] `backend/app/api/edges.py` — required reason on authored edges.
- [x] `backend/tests/` — pin-test cell updates; matrix/reason/repair/toggle/verb/take-back tests; one verbatim corpus fixture (blank-reason wave output, raw-contradiction assertion).

**Acceptance Criteria:**
- Given Greymarch employs Roth with a reason, when committed, then it persists and survives export/import/re-roll.
- Given a blank-reason generated edge, when the wave completes, then either a filled reason commits after one targeted repair or the edge drops with audit (and thin candidates fail naming the edge).
- Given a defeat followed by a scar edit and a take-back, when reading history, then three `edited` lines show and the scar stands with defeat rewound.
- Given pre-reason live rows, when reading/undoing/exporting, then nothing breaks and NULLs never block.
- Given a stale kinds payload, when accepting, then commit-time live validation is the backstop and pickers re-fetch per walk.

## Spec Change Log

- 2026-09-25: Spec distilled `ready-for-dev`; CHECKPOINT 1 approved (dial clarification: dial = live record setting, not the removed Size slider).
- 2026-09-26: Slice executed end-to-end. All 13 tasks landed; 1494 tests green; ruff lint+format clean; mypy delta zero (4 legacy errors fixed incidentally). Session fixes beyond the tasks: `common.py` 422 mapping extended to the new v3 rejections (blank-reason/kind-violation/run-state — previously re-raised as 500s), `GET /kinds` route moved above the `/{campaign_id}` catch-all (was an unreachable 404; its payload comprehension also bound `src`/`dst` that never existed — never ran until the move), regenerate payload gates' unknown-key check corrected (`>`-superset inverted to set-difference), PATCH edge response now echoes the effective (possibly fresh) reason. Ask-First items resolved with owner verdicts recorded in code: no backfill (NULL grandfathering, doc in `_migrate_edge_reason`); revisions default 20/max 100; `login_session` = startup idempotent rename on the owner's disposable DBs. Verbatim corpus fixture added (`fixtures/model_outputs/blank-reason-wave-2026-09-19.json` — real gemma-4-26B capture, raw-contradiction assertion + end-to-end fill chain test).

## Design Notes

Take-back arithmetic, not images: enrich hp to 35, then take back a −12 verb → 23. Never 40 — later edits are preserved, never overwritten. Toggle bundling follows the save gesture: one save, one revision, one take-back.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green (includes updated pin test + new slice tests).
- ruff + mypy per backend config -- expected: clean (repo AGENTS.md: exact invocations TODO — implementer resolves from repo config).
