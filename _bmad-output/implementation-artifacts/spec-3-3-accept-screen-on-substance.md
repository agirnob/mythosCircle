---
title: 'Accept Screen on Substance'
type: 'feature'
created: '2026-09-04'
status: 'done'
review_loop_iteration: 0
baseline_commit: '1e70cf885b8a248aecd479714b6af7776da30fd1'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Candidates are backend-only (3.1/3.2): there is no UI to review, edit, accept, or reject them, and the staged payload carries only the AR19 core (name, role, personality, secret/rumor/party_hook, stat_block, edges) — the DM cannot accept on substance because the AR24 sections (appearance, voice, goals, background, world-integration) are never requested from the model.

**Approach:** Extend the generate prompt contract + validation so every staged candidate carries the full AR24 sectioned profile (boss section conditional on role), then build the accept screen: a Candidates view with an ask box, sectioned-profile cards, and Reject / Edit / Accept at equal tap depth (inversion guarantee #2). Edit = inline per-section editing before accept; the edited payload is what commits. Post-accept hand editing stays story 3.6.

## Boundaries & Constraints

**Always:**
- AR24 section set in the staged payload: identity anchor (name, role, level/CR, race/type, class/profession, alignment), narrative-lore (appearance, personality, background, goals, relationships prose, hooks, voice style, catchphrases, secret), mechanics (AR25 stat_block), conditional boss section (role BBEG/Monster), world-integration block (reputation, factions, current location, reaction matrix, on_defeat). Keep existing AR19 keys unchanged; add, never rename.
- Prompt stays byte-deterministic (AR6/AD-16): new sections are part of the OUTPUT CONTRACT and `_candidate_payload`'s deterministic assembly — no free passthrough of model-invented top-level sections beyond today's behavior.
- Accept with an edited payload remains exactly one atomic transaction (AD-15); the override must carry the staged `edges` verbatim (relation editing is 3.4) or the accept is a 409/422, never a partial commit.
- Frontend TS types derive from regenerated OpenAPI (`npm run gen:api`); reuse `StatBlock.vue` and WorldView's card/section conventions; error envelope handling via `apiFetch`.
- Missing/invalid AR24 sections fail validation like other shape violations — a candidate missing substance must not reach the accept screen silent-empty.
- Forward compat (AR24): the UI renders known sections, skips unknown ones, never fails on extra keys.

**Ask First:**
- If guaranteeing all AR24 sections needs >1 extra LLM call or breaks the <2-valid-survivors budget math, halt and present the tradeoff.

**Never:**
- No post-accept editing, no rebase-or-reject (3.6), no inline relation editing (3.4), no portraits (4.1).
- No UI e2e (AR22); vitest unit tests only.
- No new edge vocabulary, no payload keys for fields outside AR24's section list.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| SECTIONED_STAGING | generate job on a world with entities | Each staged payload carries all required AR24 sections (boss present iff role is BBEG/Monster) | Missing section → shape violation; job fails per existing <2-survivors rule |
| EDIT_THEN_ACCEPT | DM edits "appearance" text, accepts | Commit lands with edited section, staged edges verbatim; one revision | Override with altered/missing `edges` → 4xx, candidate stays `proposed` |
| ACCEPT_UNEDITED | DM accepts without editing | Identical behavior to 3.2 accept (no body) | N/A |
| REJECT_TAP | DM rejects | Same tap depth as accept; world untouched | N/A |
| EMPTY_QUEUE | No proposed candidates | Screen shows empty state + ask box (job kind `generate`, payload `{"ask": ...}`) | Job errors surface via existing jobs WS/REST states |
| UNKNOWN_SECTION | Payload contains extra keys | UI skips them silently; rest renders | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/generate.py` — `build_generate_prompt` :306-378 (OUTPUT CONTRACT at :353-358 — extend with AR24 sections); `_candidate_violations` :416-439 (add required-section checks); `_candidate_payload` :489-506 (deterministic assembly; AR24 passthrough loop at :502-505); role/repair logic :186-214. Prompt-determinism tests nearby.
- `backend/app/store/candidates.py` — `accept_candidate` :145-235 (one transaction; payload-minus-edges → entity.data). Add optional payload-override parameter here; `stage_candidates` :78-126.
- `backend/app/api/candidates.py` — accept route :110-128 (accept optional body `{payload?}`); reject :133-; GET list :70- (payload passes through verbatim — no shape change needed).
- `backend/app/store/models.py:204-228` — `ProposedCandidate.payload` JSON column; doc comment lists AR19 shape — update to AR24 shape.
- `backend/tests/test_generate_api.py` — fake provider fixture :146- returns AR19 candidates → extend fixture payload with AR24 sections; REST candidate coverage :212-300.
- `backend/tests/test_candidate_lifecycle.py` — accept mapping tests :145-180 → add override + edges-mismatch cases.
- `frontend/src/api/schema.ts` — regenerate via `npm run gen:api` (needs backend running); `CandidateResponse` appears after regen.
- `frontend/src/stores/jobs.ts` — store pattern to copy: schema types, monotonic upsert :52-63, syncList :77-92.
- `frontend/src/views/WorldView.vue:90-118,169-200` — card/section CSS + relations-line conventions to reuse.
- `frontend/src/components/StatBlock.vue` — reuse for mechanics section.
- `frontend/src/router.ts:7-27` — add `/campaigns/:id/candidates` route; `frontend/src/api/client.ts:45-70` — apiFetch envelope handling.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/pipeline/generate.py` — extend OUTPUT CONTRACT + `_candidate_violations` + `_candidate_payload` for the AR24 section set (boss conditional on role); keep byte-determinism.
- [x] `backend/app/store/candidates.py` — `accept_candidate(..., payload_override=None)`: validate override is a dict carrying staged `edges` verbatim, else raise; commit override-minus-edges.
- [x] `backend/app/api/candidates.py` + `backend/app/store/models.py` — optional accept body `{payload?}`; update payload doc comment.
- [x] `backend/tests/test_generate_api.py` + `backend/tests/test_candidate_lifecycle.py` — AR24 fixture/staging violations, prompt-determinism, override accept, edges-mismatch rejection.
- [x] `frontend/src/api/schema.ts` — regenerate; `frontend/src/stores/candidates.ts` — new Pinia store (list sync, accept/reject actions).
- [x] `frontend/src/views/CandidatesView.vue` + router entry — ask box; proposed-candidate cards rendering full sectioned profile (unknown keys skipped); Reject / Edit / Accept equal prominence; Edit toggles per-section inline textareas (edges section read-only, shown as relation lines); accept sends edited payload when edited.

**Acceptance Criteria:**
- Given a generate job completes, when the DM opens the accept screen, then each proposed candidate shows the full AR24 sectioned profile — appearance, personality, voice, secret, mechanics, world-integration (AR24).
- Given the accept screen, when the DM acts, then reject, edit, and accept are one tap at equal visual prominence — accept is not the path of least resistance.
- Given a DM edit before accept, when she accepts, then the edited sections commit in the same single transaction and the staged edges commit verbatim.

## Spec Change Log

## Design Notes

- Edit-before-accept is client-side mutation of the candidate payload + the accept override; 3.6's committed-state hand editing (always beats LLM, rebase-or-reject) is a different surface and stays out.
- Boss section conditionality: required when role is BBEG or Monster, absent otherwise — do not emit empty boss objects.
- Keep the ask box on the same view: the DM asks, jobs run via existing WS progress, new candidates appear on sync — no navigation.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — expected: all green incl. new AR24/override tests.
- `make lint && make typecheck` — expected: clean.
- `cd frontend && npm test -- --run` — expected: vitest green incl. CandidatesView tests.

**Manual checks (if no CLI):**
- With backend up: submit ask → wait for job_done → Candidates view shows sectioned profiles → edit one section → accept → WorldView (after refetch) shows the entity with edited section; reject another → it disappears from `proposed` list.

## Suggested Review Order

**The AR24 section contract — one source of truth**

- Field lists + role set live in the store; pipeline and accept path both consume them.
  [`candidates.py:83`](../../backend/app/store/candidates.py#L83)

- `payload_section_violations` — the required-section shape shared by staging and overrides.
  [`candidates.py:134`](../../backend/app/store/candidates.py#L134)

- Pipeline `_candidate_violations` now delegates to the store validator (no duplicate literal).
  [`generate.py:438`](../../backend/app/pipeline/generate.py#L438)

**The prompt contract**

- OUTPUT CONTRACT: full sectioned profile, boss conditionality annotated inline.
  [`generate.py:361`](../../backend/app/pipeline/generate.py#L361)

- Substring pins: world_integration, boss rule, both level_cr formats — prompt regression is caught.
  [`test_generate_pipeline.py:1211`](../../backend/tests/test_generate_pipeline.py#L1211)

**Staging + deterministic payload assembly**

- Deterministic assembly: trimmed sections, world_integration re-assembled, boss iff BBEG/Monster.
  [`generate.py:508`](../../backend/app/pipeline/generate.py#L508)

- Happy path asserts the full section set incl. a BBEG staging with boss present.
  [`test_generate_pipeline.py:196`](../../backend/tests/test_generate_pipeline.py#L196)

**Edit-before-accept — the override transaction**

- `accept_candidate(..., payload_override)`: edges verbatim + full section shape, else 422 row-proposed.
  [`candidates.py:291`](../../backend/app/store/candidates.py#L291)

- Accept route: optional body, explicit `{"payload": null}` rejected; only omitted body = unedited accept.
  [`candidates.py:123`](../../backend/app/api/candidates.py#L123)

**Frontend store**

- syncList prunes server-discarded proposed rows; settled rows stay cached; id-ordered.
  [`candidates.ts:53`](../../frontend/src/stores/candidates.ts#L53)

**The accept screen**

- Mid-edit payload swap visibly discards the draft — never a silent merge.
  [`CandidatesView.vue:112`](../../frontend/src/views/CandidatesView.vue#L112)

- Counter rendered whenever present — forward-compat edge types don't hide counters.
  [`CandidatesView.vue:266`](../../frontend/src/views/CandidatesView.vue#L266)

- Reject / Edit / Accept as three equal-class buttons in one row (inversion guarantee #2).
  [`CandidatesView.vue:540`](../../frontend/src/views/CandidatesView.vue#L540)

**Peripherals**

- Route + WorldView cross-link; router-name wiring is pinned by a real-router test.
  [`router.ts:23`](../../frontend/src/router.ts#L23)

- Override/edges-mismatch/AR24-shape store + API tests; failure-path UI tests.
  [`test_candidate_lifecycle.py:216`](../../backend/tests/test_candidate_lifecycle.py#L216)
