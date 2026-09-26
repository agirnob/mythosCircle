---
title: 'v3 Tier-2 wire slice — session verbs, knowledge toggles, run-state read'
type: 'feature'
status: 'done'
baseline_commit: '5a41652'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-09-25/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-v3-slice-backend.md'
  - '{project-root}/_bmad-output/implementation-artifacts/handover-2026-09-26.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The v3 backend slice shipped store + undo + registry/revisions reads but deliberately
left the Tier-2 wire surface unwired: the DM has no route to fire a consequence verb, flip a
knowledge toggle, or read Tonight's run-state — and the entity PATCH route would merge a
`knowledge_flips` key into the record as junk data instead of bundling it (AD-29 one-save-one-revision).

**Approach:** Four route surfaces over the existing store commit functions — each an
ownership-404-first, hand-parsed-body route on the entities/undo precedent — plus two small
store idempotence fixes the wire contracts require. Store/undo/API machinery is already tested;
this slice adds only the wire and the reads the Tier-2 UI needs.

## Boundaries & Constraints

**Always:** one logbook (commit path only writer); routes call the exported store functions
verbatim — no second writer, no vocabulary owned by the API; exports keep reading entity/edge
only (the run-state read is a NEW read surface, AD-28 "chip reads are joins; readers stay
dumb"); read-only reads; owner-gated like every route; 204 body-less mutations, the undo/PATCH
precedent; literal routes before dynamic-segment routes; frontend untouched (no schema regen —
the owner's uncommitted rebuild is out of bounds).

**Ask First:** n/a — semantics fully bound by AD-26..29 + the frozen backend slice.

**Never:** frontend changes; a second pipeline/writer; new verb vocabulary (the session image
stays an open strict-JSON object — the four verbs are UI affordances over an open image, the
take-back tests already use `{"hp": -12}` as a scar); export/entity/edge schema changes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fire a verb | `POST .../session-verb` `{"update": {"defeated": true}}` | 204; one revision, `session_state_*` event; run-state shows the merged image | N/A |
| Double-fire a verb | Same verb twice | Both 204; SECOND commits nothing (store no-op, head returned) | N/A |
| Verb on foreign campaign | Any body, foreign id | 404 before body read (no oracle) | envelope 404 |
| Verb `update` missing/non-object | `{}` / `{"update": 3}` | 422, zero revisions | 422 |
| Verb stale base | Named `base_revision` ≠ head | 409 `StaleRevisionError` | 409 |
| Toggle a secret | `POST .../knowledge-toggle` `{"field": "secret", "known": true}` | 204; row set, `knowledge_state_*` event; record untouched | N/A |
| Double-fire a toggle | Same `{field, known}` again | 204; second commits nothing (new store no-op, mirrors the verb) | N/A |
| Toggle unknown field | `{"field": "favorite_color"}` | Store rejects (closed set, DB CHECK + code) → 422 | 422 |
| Toggle `known` non-bool | `{"field": "secret", "known": "yes"}` | 422, zero revisions | 422 |
| Edit + flip saved together | PATCH body with content keys + `knowledge_flips: [{field, known}]` | 204; ONE revision, entity + knowledge events | N/A |
| PATCH flip-only | PATCH `{"knowledge_flips": [...]}` alone | 204; one knowledge-only revision (AD-29 one save = one undoable step) | N/A |
| PATCH re-save, nothing changed | Identical content + unchanged flips | 204; zero revisions (PATCH idempotence promise holds for flips too) | N/A |
| PATCH `knowledge_flips` malformed | non-list / item non-object / field non-str / known non-bool | 422, zero revisions | 422 |
| Run-state read | `GET .../run-state` | 200 `{session: {eid: image}, knowledge: {eid: {field: bool}}}`; absent = no rows | N/A |
| Run-state foreign/unknown | Foreign campaign id | 404 single indistinguishable | 404 |
| Run-state never leaks | Export after verbs/toggles | Exporter output unchanged (AD-11 reads entity/edge only — pinned by existing export tests) | N/A |

## Code Map

- `backend/app/store/commit.py` — `commit_knowledge_toggle:616` (add same-value no-op, mirror `commit_session_verb:563`); `_update_entity:1279` (value-identical guard ~1397 must not swallow `knowledge_flips`; pass only CHANGED flips to `_commit`); new helper beside `_resolve_state_image:516`.
- `backend/app/api/entities.py` — `_object_body:171` reuse; add `session-verb` + `knowledge-toggle` POSTs; PATCH `update_entity:98` extracts `knowledge_flips`.
- `backend/app/api/campaigns.py` — v3 read section (~`:387`): add `GET /api/campaigns/{campaign_id}/run-state` beside `revisions_history` (registered in the section; no catch-all collision — deeper segment count).
- `backend/app/api/common.py` — no change expected (InvalidRunStateError + StaleRevisionError + Unknown*Errors already mapped by the v3 slice).
- `backend/tests/test_store.py` — new toggle/update idempotence tests beside `test_defeat_scar_take_back_surgical:2246`.
- `backend/tests/test_tier2_api.py` — NEW, the `test_undo_api.py` fresh-scratch-DB-per-test pattern (no shared conftest DB → no queue-drain concern; these tests enqueue no jobs anyway).
- `backend/app/api/exports.py` — untouched (state rows never export; existing tests already pin the entity/edge-only surface).

## Tasks & Acceptance

**Execution:**
- [x] `commit.py` — `commit_knowledge_toggle` same-value flip no-op (returns head; stale-explicit-base behavior mirrors `commit_session_verb`).
- [x] `commit.py` — `_update_entity` no-op guard extended: identical content + unchanged flips → head, zero revisions; changed flips alone → one knowledge-only revision; only changed flips forwarded to `_commit`.
- [x] `entities.py` — `POST .../session-verb` (204; `update` required strict-JSON object; 400 body shape / 422 field-level; ownership-404-first).
- [x] `entities.py` — `POST .../knowledge-toggle` (204; `field` required non-empty string, `known` required bool; closed set left to the store's 422).
- [x] `entities.py` — PATCH entity extracts `knowledge_flips` (list of `{field, known}`) before the record merge; still counts toward the ≥1-content-key guard.
- [x] `campaigns.py` — `GET .../run-state` (owner-gated, response models, dumb joins).
- [x] `tests/test_store.py` + `tests/test_tier2_api.py` — matrix rows pinned (verb fire + no-op + 404/409/422; toggle set + no-op + 422s; PATCH bundle/flip-only/re-save/malformed; run-state read + 404 + record-untouched).
- [x] No frontend regen; no common.py change unless the store surfaces an unmapped rejection.

**Acceptance Criteria:**
- Given a DM fires the same verb twice, when the API is read, then exactly one new revision exists and runs reads the merged image (double-fire gate, AD-26/Flow 6).
- Given an edit+flip save with a repeated unchanged flip, when the PATCH resolves, then exactly ONE revision holds the edit and the changed flip, and unchanged flips emit nothing.
- Given exported world after verbs/toggles, when compared to the pre-verb export, then the export projection is byte-identical for the entity/edge surface (state never leaks).
- Given a foreign campaign id on any route of this slice, when hit with any body, then the single indistinguishable 404 lands before any body read.
- Given run-state on a fresh world, when read, then empty maps render — absent rows are never errors.

## Spec Change Log

- 2026-09-27: Slice executed end-to-end. 1525 backend tests green (1497 baseline + 5 store + 23 API); ruff lint+format clean; mypy zero delta on touched files (56-error legacy baseline untouched). All acceptance criteria verified live on a throwaway server (scratch DB, port 8899) plus TestClient; dev API `mythos-api-run` restarted serving the new routes on :8000. Two store behaviors needed beyond the task list, both surfaced by the wire tests: (1) `_update_entity`'s value-identical guard returned the head BEFORE carrying `knowledge_flips`, silently dropping a flip-only PATCH — fixed by extending the guard AND, when the merge is identical but a flip changed, committing the flip ALONE (no redundant `entity_updated` event for content that did not move); (2) `commit_knowledge_toggle` committed a fresh revision for a same-value flip (history pollution on retry) — now a no-op mirroring `commit_session_verb`, with `_flip_changed` identity-compared (`is not`) so a `1` for `true` still reaches the store's strict-bool 422 instead of reading as unchanged. No schema regen (frontend untouched); PATCH entity keeps its existing 400 for a non-string `base_revision` while the new verb/toggle routes use 422 (edges `_optional_base` precedent) — documented in the route docstrings.

## Design Notes

Wire response is 204 for both writes (the undo/PATCH precedent — the frontend re-reads state
from the feed and run-state; no optimistic echo the refetch can contradict). `update` stays an
open object because the session image IS open (the AD-27 scar `{"hp": -12}` is a verb in the
take-back tests; closing the vocabulary at the wire would reject the store's own test shapes).
The four UX verbs (defeated / allegiance / thread / item) are UI affordances over that image —
they ship in the frontend phase under the existing AD-26 "no silent new verbs" rule.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — expected: full suite green (1497 baseline + new tests).
- `uv run --directory backend ruff check .` + `uv run --directory backend ruff format --check .` — clean.
- mypy delta zero (56-error pre-existing baseline untouched).
- Restart the dev API (`hub` service `mythos-api-run`) and smoke the four surfaces with curl on the live dev DB's scratch copy.