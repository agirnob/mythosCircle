---
title: 'Hand Editing — the DM Is the Final Author'
type: 'feature'
created: '2026-09-05'
status: 'done'
review_loop_iteration: 1
baseline_commit: 'b334dbc'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The DM cannot edit committed world state by hand. The only committed-entity mutation surfaces are edge add/edit/delete (3-4) and whole-entity regeneration (3-5); WorldView cards render only name/text/stat_block. But "the DM is the final author" (FR10) requires direct field/section edits that persist as committed transactions, and AR4/NFR2 requires that a DM edit colliding with a pending generation never be silently overwritten — today a regenerate-entity accept **silently rebases onto whatever the DM committed** and overwrites the edit (no diff, no 409).

**Approach:** A new synchronous `PATCH /api/campaigns/{id}/entities/{entity_id}` — the store's first direct committed-entity content write — taking partial fields (identity anchor, lore, `stat_block`, `world_integration`, `boss`, `text`) plus optional `base_revision`, merged onto the current record, and committed as exactly one `entity_updated` revision via `EntityInput(id=existing)`. Stale `base_revision` → existing `StaleRevisionError` 409 (rebase-or-reject). **Shape validation is conditional (owner decision 2026-09-05):** a record that already satisfies the AR24 character shape must still satisfy it after the merge (one validator for character-shaped records — they stay re-rollable and exportable); a record that does NOT satisfy the shape (build-in bare characters, factions, places — `kind != character` or data lacking the AR24 core) accepts partial edits unconstrained — the merge lands as-is, no shape forcing, no fabricated promotion. For the pending-commit collision: `ProposedCandidate` gains `entity_base_data` (a snapshot of the regenerated target's committed `data`, captured at staging); accept of a regenerate-entity row compares the current target `data` against it and raises a new `EntityEditConflictError` (409) on mismatch — with a **three-way escape (owner decision 2026-09-05):** Re-roll (rebase), Cancel (reject), or **Accept anyway with explicit confirmation** (the DM chose the generated version over her own edit; the in-place accept then proceeds). Frontend: WorldView renders the full AR24 profile on committed cards with inline per-section editing backed by the PATCH; CandidatesView surfaces the accept conflict as a three-way dialog.

## Boundaries & Constraints

**Always:**
- Store is the sole world-state writer (AD-1): the PATCH route delegates to a new `store.commit.update_entity`; one `session_scope` load→merge→validate→`_commit`; exactly one revision when the edit changes anything; `_check_base` unconditional once a world has revisions (edge/delete precedent).
- **Conditional AR24 validation (owner 2026-09-05):** `update_entity` runs `payload_section_violations(current.data)`; only if the CURRENT record already passes (no violations) must the MERGED record also pass — a violation listing is then a 422 naming the break with zero revisions. If the current record already fails the shape, the merged record is written unconstrained (unknown keys pass through, partial edits land; `text` and `name` handled below). This preserves one contract where it matters (character-shaped records stay valid/re-rollable/exportable) and never force-invalidates or force-fabricates.
- `name` edits sync both `data["name"]` and the `Entity.name` column in the same transaction (for records whose data carries name); `text` must be `str` or `null` on the wire — any other type is a 422 with zero revisions (the str|None contract every other write surface guarantees). A PATCH whose merged record would be byte-identical to the current one (including `text`) commits NO revision — 204 success, idempotent, zero history pollution (applies to null-deleting an absent key too).
- `base_revision` optional on the wire; omitted → resolved to the current head inside the store call so the check still guards the race (edges.py `_optional_base`/`_resolved_base` semantics). 4xx never changes state; foreign/unknown campaign or entity is 404 before body validation (Request-based body parse, `entities.py` DELETE precedent); non-object/malformed/empty body is 400; ≥1 content key required (a body of only `base_revision` is a 400).
- Accept-side conflict guard is data-level and precise: `entity_base_data` is captured only for `regenerates_entity_id` rows, inside the staging transaction; the accept compare runs in the accept's `BEGIN IMMEDIATE` transaction (no race). A NULL `entity_base_data` on a LIVE regenerate target (rows staged before the 3-6 migration, or any unverifiable row) fails closed: `EntityEditConflictError` 409 unless the DM explicitly confirms overwrite. Generate-kind rows (fresh ULID, no overwrite risk) get no base and no new check — existing pins (`test_accept_creates_new_entity_never_mutates_existing`) hold.
- Three-way accept conflict (owner 2026-09-05): mismatch and no confirmation → `EntityEditConflictError` 409, row stays `proposed`, zero revisions. Mismatch + explicit `confirm_overwrite: true` on the accept body → the in-place accept proceeds as one `entity_updated` revision; the previous revision is the undo (the DM may still undo). Confirm semantics mirror the destructive-confirmation precedent (campaign delete): the confirm flag alone is NOT enough for the frontend to skip a human confirmation step.
- Regeneration runner preserves DM work across the staging window (owner-approved fix from review): the runner RE-READS the target record at staging and splices preserved sections from that fresh record — a DM hand edit landing between the runner's prompt-build read and staging is carried into the staged payload, never overwritten. For a re-roll of an ENTITY-regen candidate row (target `candidate` whose row has `regenerates_entity_id`): splice preserved sections from the CURRENT target entity record, keep the row's staged `edges` (DM's 3-4 staged edge edits survive), and refresh `entity_base_data` to the current target data (via `replace_candidate_payload`, which gains the base refresh). A re-rolled row is then accept-able after the edit — never stranded.
- Migration follows the additive per-name `ALTER TABLE` provenance pattern (`db.py:291-321`); idempotent.
- Frontend reuses existing patterns: `apiFetch` + `ApiError.code`; `world.ts` store action mirroring `addEdge` (fetch → `fetchSnapshot` on success, propagate ApiError); WorldView renders 409s inline with no auto-refetch (delete/edge `stale base revision` precedent), Reload awaits the refetch before re-enabling the editor; the dialog gates on the edit-conflict error (message "changed since this candidate was generated"), never on bare `code === 'conflict'` (CandidateSettledError must render the card error, not the dialog); the dialog's Re-roll goes through the jobs store's in-flight discipline (`submitRegenerate` + pending-state) so a queued/running regenerate blocks a second enqueue. No new WS event type — the 409 responses ARE the visible rebase-or-reject signal. No UI e2e (AR22); vitest only.

**Ask First:**
- If the boss/role strictness (the single AR24 validator for character-shaped records) blocks a legit DM edit at CHECKPOINT time, the alternative is a relaxed hand-edit validator — but a character-shaped record that fails staging validation can never be re-rolled (3-5) or exported (AR18) — present the tradeoff rather than silently diverging the contracts.

**Never:**
- No entity CREATION and no `kind` edits — the orphan rule stays create-only (spec-2.5 Ask First resolves: 3-6 does NOT need bare-entity creation); edits target existing committed entities only.
- No edge/counter edits here (3-4 owns them), no staged-candidate server-side draft persistence (3-3's edit-via-accept-override remains the candidate path), no new job kind (a hand edit is a sub-second synchronous transaction, not generation — the spinner is the enemy), no `entity_base_data` on generate-kind rows.
- No silent merge, ever: accept never writes a regenerate payload over a changed target without the 409 OR the DM's explicit confirm_overwrite; the PATCH never writes over a moved head without the 409; the confirm flag never skips the frontend confirmation step.
- No shape forcing: a non-AR24 record never gets auto-filled sections to pass validation (the DM's word beats the LLM's — fabricated defaults are the LLM's job, not the store's).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| EDIT_SECTION | `PATCH {personality: "..."}` on AR24-valid entity | One revision, one `entity_updated`; only that field changed; every other field + unknown keys byte-identical; ULID stable; undo restores prior revision | N/A |
| BARE_RECORD | `PATCH {text: "...", location: "..."}` on a faction/place or bare build-in character (data fails shape) | Merge lands unconstrained, one revision — no shape forcing, no fabricated sections; `text` updates the column | N/A |
| EDIT_IDENTITY_NAME | `PATCH {name: "..."}` on AR24-valid entity | `data.name` AND `Entity.name` column updated in one transaction; WorldView header reflects it | blank name → 422 |
| EDIT_ROLE_BOSS | `PATCH {role: "BBEG"}` without `boss` on AR24-valid entity | 422 naming the missing boss fields; zero revisions; entity untouched | 422 (`InvalidEntityRecordError`) |
| EDIT_ROLE_UNBOSS | `PATCH {role: "NPC"}` leaving `boss` present | 422 "boss section is only allowed for BBEG or Monster roles"; removing `boss` (null) in the same PATCH succeeds | 422 |
| EDIT_STATBLOCK | `PATCH {stat_block: {...}}` | Whole section replaced; shape not AR25-audited for hand edits (DM override; staging-time AR25 applies to regeneration only) | non-object stat_block → 422 for AR24-valid records |
| CUSTOM_KEY | `PATCH {notes: "..."}` (unknown key) | Key passes through into `data`; renders in the muted additional-data block; later regen preserves it (3-5 splice) | N/A |
| EDGELESS | PATCH on an entity with zero live edges | Commits normally (orphan rule is create-only) | N/A |
| TEXT_TYPE | `PATCH {"text": 5}` | 422, zero revisions — `text` contract is str or null | 422 (`InvalidEntityRecordError`) |
| NOOP_PATCH | value-identical PATCH, or `{"boss": null}` on a record without boss | 204 success, ZERO revisions (idempotent, history unpolluted) | N/A |
| STALE_BASE | `PATCH {base_revision: <old>}` while head moved | 409 `StaleRevisionError` ("rebase or reject"), zero revisions; retry against head succeeds | 409 |
| OMITTED_BASE | PATCH without `base_revision` | Resolved to current head inside the store call; commits; interleaving edit still 409s | 409 on race |
| FOREIGN | PATCH on foreign/unknown campaign or entity; malformed body | 404 before any body parsing; malformed/non-object/empty/base-only body on OWN campaign → 400 | 404 / 400 |
| ACCEPT_CONFLICT | regenerate-entity staged; DM hand-edit lands on the target; accept without confirm | 409 `EntityEditConflictError`; row stays `proposed`; zero revisions; frontend shows the three-way dialog | 409 |
| ACCEPT_ANYWAY | same state; accept with `confirm_overwrite: true` | In-place accept proceeds: ONE `entity_updated` revision, row `accepted`, provenance recorded; previous revision is the undo | N/A |
| NULL_BASE | regenerate row staged pre-3.6 (base NULL), target alive + edited | Fail closed: `EntityEditConflictError` 409 → three-way dialog (re-roll / accept-anyway / cancel) | 409 |
| MID_CALL_EDIT | DM edit lands between runner prompt-build read and staging | Runner re-reads the target at staging; preserved sections splice from the fresh record; the edit survives in the staged payload; accept proceeds cleanly | N/A |
| RE_ROLL_AFTER_EDIT | entity-regen row hand-edited → conflict → dialog Re-roll | Re-roll targets the CANDIDATE (3-5 in-place path): preserved sections from the CURRENT target, staged `edges` kept, `entity_base_data` refreshed; the row is accept-able after | N/A |
| ACCEPT_CLEAN | regenerate-entity staged; target untouched | In-place accept as today; existing pins green | N/A |
| STAGE_TARGET_GONE | target deleted between runner read and staging | `entity_base_data` stays NULL; existing accept-time deleted-target rejection still fires | job-level `InvalidCandidateError` at accept (3-5 pin) |

</frozen-after-approval>

## Code Map

- `backend/app/store/commit.py` — new `update_entity(campaign_id, entity_id, *, patch, base_revision=None)`: one `session_scope`; `session.get(Entity)` → foreign/absent 404 (`UnknownEntityError`); merge `{**entity.data, **content}` (content = patch minus `text`/`base_revision`; explicit `null` deletes the data key); **conditional validation**: `current_violations = payload_section_violations(entity.data)`; if `current_violations == []` then `payload_section_violations(merged)` must also be `[]` else `InvalidEntityRecordError` (422, zero revisions); if current already fails the shape, merged is written unconstrained. `text` guard: `patch.get("text", entity.text)` must be `str | None` else `InvalidEntityRecordError`. No-op guard: if `merged == entity.data and text_unchanged` → return without `_commit` (route still answers 204). Commit `EntityInput(kind=entity.kind, name=name_or_column, text=…, data=merged, id=entity_id)` via `_commit` with base resolved (None → current head, `_check_base` at :668-672 guards the race). `StaleRevisionError` (:97-103) already carries `latest_revision_id`. Error classes `InvalidEntityRecordError` + `EntityEditConflictError` live in `commit.py` beside `StaleRevisionError`; `UnknownEntityError` already exists.
- `backend/app/api/entities.py` — add `PATCH /api/campaigns/{id}/entities/{entity_id}` mirroring the DELETE route (:35-100): ownership 404 first, Request-based `_object_body` (edges.py:79 pattern — absent/malformed/non-dict → 400, `base_revision` non-str → 400, ≥1 content key required), then `store.update_entity` with `store_error_as_http`. 204 response (body-less; DELETE precedent).
- `backend/app/store/models.py:185-241` — `ProposedCandidate` gains nullable `entity_base_data: JSON` (regenerate rows only); migration `_migrate_proposed_candidate_entity_base` in `db.py` after `create_all`, per-name additive ALTER (provenance pattern `db.py:291-321`).
- `backend/app/store/candidates.py` — `stage_candidates` :230-312: for rows with `target_entity_id`, snapshot `target.data` in-tx (deep copy) → `row.entity_base_data`; target absent → NULL (existing accept-time check owns it). `replace_candidate_payload` :334-382: when the row has `regenerates_entity_id`, ALSO re-snapshot the current target entity's `data` into `entity_base_data` (in the same transaction) so a re-rolled row is never stranded. `accept_candidate` :385-550 gains `confirm_overwrite: bool = False`: after the existing target-exists check, for rows with a live target whose `entity_base_data is None` OR whose `target.data != entity_base_data`: without `confirm_overwrite` raise `EntityEditConflictError(target_id)` before `_commit` (same BEGIN IMMEDIATE tx, zero revisions); with it, proceed (in-place accept, one `entity_updated` revision). Existing clean-accept path (:496-546) otherwise untouched; `_accept_edge` unchanged.
- `backend/app/api/common.py:47-116` — add `InvalidEntityRecordError` to the 422 set, `EntityEditConflictError` to the 409 set (plain `HTTPException(409, str(exc))`, message "…changed since this candidate was generated — re-roll (rebase), accept anyway (overwrite), or cancel (reject)").
- `backend/app/api/candidates.py:35-63` — `CandidateResponse` gains `regenerates_entity_id: str | None = None` (verified absent 2026-09-05; the dialog's re-roll target) wired in `_candidate_response`; `AcceptBody` gains `confirm_overwrite: bool = False` passed to `accept_candidate`. Regenerate `frontend/src/api/schema.ts` via `npm run gen:api` with the :8000 API current (then prettier per repo convention; the PATCH route stays `requestBody?: never` by design — hand-parsed body, same as edges — leave the generated annotation alone).
- `backend/app/pipeline/regenerate.py` — runner re-reads the target entity record immediately before staging (fresh `world_state` entity lookup) and splices preserved sections from THAT record (byte-identical to the record at staging); for a candidate-target re-roll whose row has `regenerates_entity_id`: splice preserved sections from the current target entity, preserve the row's staged `edges` (keep the old payload's `edges` list), and let `replace_candidate_payload` refresh the base. Prompt/retrieval still read at run start (AR6 determinism untouched); only the splice source moves to staging time.
- `frontend/src/views/WorldView.vue` — render the full AR24 profile on committed cards (identity fields, LORE_FIELDS, `stat_block` via `StatBlock.vue`, `world_integration`, `boss` when present, muted additional-data block for unknown `data` keys — resolves the spec-2.7 deferral); "Edit profile" on every card (records fail the conditional shape on the backend for non-AR24 classes — the UI must NOT preempt that: bare cards save text/custom keys and the backend merges unconstrained). Drafts seed from RAW committed values (numbers/objects stringify for the textarea; never blank out a present value); boss-null removal clause stays role-gated (blanking boss on a BBEG 422s correctly — the section is required); Save → `world.updateEntity(id, patch, text, snapshot.revision.id)`; 409 → inline conflict notice with Reload (rebase) / Discard; Reload awaits `world.requestRefetch()` before closing the editor (fast re-edit must not send a stale base_revision); no auto-refetch on error (edge-409 precedent, WorldView.test.ts:410).
- `frontend/src/stores/world.ts` — `updateEntity(campaignId, entityId, patch, baseRevision)` action mirroring `addEdge` :93-99: PATCH `/api/campaigns/{id}/entities/{entityId}` with `{...patch, base_revision?}`, then `fetchSnapshot` on success; propagate ApiError.
- `frontend/src/views/CandidatesView.vue` — accept error branch gates on the edit-conflict MESSAGE (`err.message.includes('changed since this candidate was generated')`), never bare `code === 'conflict'` (CandidateSettledError from a double submit renders the card error); three-way dialog: [Re-roll against latest world] → `jobs.submitRegenerate({kind:'candidate', id: row.id})` (3-5 in-place path — preserves staged edge edits; in-flight discipline via the jobs store pending state, button disabled while pending), [Accept generated version anyway] → TWO-STEP confirm (mirror cascade-delete confirmation; second click sends accept with `confirm_overwrite: true`), [Cancel] → keep row proposed. Dialog target name from `regenerates_entity_id` via the world snapshot.
- `backend/tests/test_entity_edit_api.py` (new) + `backend/tests/test_candidate_lifecycle.py` + `backend/tests/test_regenerate_pipeline.py` — coverage below; store/API fixtures seed BOTH a full-AR24 record (existing `_AR24` shape) and a bare record (`data={}` / `data={stat_block}`).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/commit.py` + `backend/app/api/common.py` — [ ] `update_entity` (conditional validation, name sync, text str|None guard, no-op guard, base resolve) + `InvalidEntityRecordError`/`EntityEditConflictError` (both in `commit.py` beside `StaleRevisionError`) + 422/409 mapping.
- [x] `backend/app/api/entities.py` — [ ] PATCH route (404-first, `_object_body`, content-key requirement, 204).
- [x] `backend/app/store/models.py` + `backend/app/store/db.py` + `backend/app/store/candidates.py` — [ ] `entity_base_data` column + migration + staging snapshot + `replace_candidate_payload` base refresh + accept conflict compare with `confirm_overwrite`.
- [x] `backend/app/api/candidates.py` — [ ] `regenerates_entity_id` on `CandidateResponse`/`_candidate_response` + `AcceptBody.confirm_overwrite`; regen `schema.ts` via `npm run gen:api` (prettier).
- [x] `backend/app/pipeline/regenerate.py` — [ ] staging-time re-read splice + candidate-re-roll edge preservation + base refresh via `replace_candidate_payload`.
- [x] Backend tests — [ ] `test_entity_edit_api.py` (I/O matrix rows incl. BARE_RECORD, TEXT_TYPE, NOOP_PATCH, STALE/OMITTED base, FOREIGN-404-first, malformed-400, role↔boss both directions, custom-key + regen splice round-trip, zero-revision-on-4xx, store parity + undo round-trip); lifecycle (base captured at staging, ACCEPT_CONFLICT 409 row-proposed zero-revisions, ACCEPT_ANYWAY one-revision + undo, NULL_BASE fail-closed, RE_ROLL_AFTER_EDIT accept-able, clean accept + deleted-target + migration pins stay green); regenerate pipeline (MID_CALL_EDIT preserved-sections splice, candidate-re-roll edge preservation).
- [x] `frontend/src/views/WorldView.vue` + `frontend/src/stores/world.ts` — [ ] AR24 profile render + additional-data block + Edit profile (raw-value drafts, bare-record saves) + `updateEntity` action + 409 conflict UI with awaited Reload.
- [x] `frontend/src/views/CandidatesView.vue` — [ ] three-way conflict dialog (message-gated, re-roll in-flight discipline, accept-anyway two-step confirm).
- [x] Frontend vitest — [ ] WorldView render/PATCH body+base_revision/409 no-refetch + bare-record PATCH + awaited-Reload; world store `updateEntity`; CandidatesView conflict dialog (open/reroll-failure/disabled-while-acting/accept-anyway confirm flow) + non-conflict-409 does NOT open dialog; existing suites stay green.

**Acceptance Criteria:**
- Given a committed entity, when the DM hand-edits any field or section (or `text`/name), then the edit persists as exactly one committed revision (a value-identical PATCH commits none), the previous revision is the undo, and every untouched field/section is byte-identical.
- Given a faction/place/bare build-in entity, when the DM edits any field, then the edit commits unconstrained — no 422 loop, no fabricated sections; a character-shaped record that would break its shape still 422s with zero revisions.
- Given a hand edit landing while a regenerate of the same entity is staged, when the accept is then submitted, then the accept is rejected with a 409 conflict and the three-way dialog — Re-roll (in-place candidate re-roll, staged edges preserved), Accept generated version anyway (explicit two-step confirm, one revision, undoable), or Cancel (row stays proposed) — the DM's edit is never silently overwritten.
- Given a hand edit carrying a stale base_revision, when it is submitted, then 409 rebase-or-reject; re-submitting against the current head succeeds and commits one revision.

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries. -->

- 2026-09-05 (intent-gap loopback, review_loop_iteration 0→1): review found the "one AR24 validator" constraint made bare/non-AR24 records (factions, places, build-in characters) an unwinnable 422 loop with no test coverage — frozen block silently assumed character-shaped records. Owner resolved 2026-09-05: **conditional validation** (validate the merged record only when the current record already passes the AR24 shape) and a **three-way accept-conflict dialog** including accept-anyway with explicit two-step confirmation. Also folded in all 11 patch findings from the 3-layer review (NULL-base fail-closed, staging-window re-read splice, re-roll edge preservation + base refresh, no-op revision guard, text type guard, dialog message gate, raw-value drafts, in-flight re-roll discipline, awaited Reload, test-strengthening). KEEP: the PATCH-through-the-sole-commit-path design, `entity_base_data` staging snapshot + in-tx accept compare, 404-first body-less route, `StaleRevisionError` reuse, migration provenance pattern, WS-free conflict signaling — all reviewed as coherent and correct.

## Design Notes

- **Conditional, not relaxed, validation.** "Validate only AR24-valid records" keeps one shared validator for every record that can ever be staged/regenerated/exported (character-shaped), while unblocking the record classes that never could (factions/places/bare). It never lets a valid record become invalid and never invents content for an invalid one — the two failure modes the reviewers pinned.
- **Three-way vs two-way.** Re-roll and Cancel both ultimately discard the generated candidate. Accept-anyway-with-confirm is the DM's explicit "my edit was a mistake / the generated version is better" path — destructive, so it carries the same two-step confirm shape as cascade delete. The previous revision is still the undo (AD-2), so even the confirmed overwrite is recoverable.
- **Staging window closed by construction.** The runner's splice source moves to the staging-time re-read, so everything in the staged payload comes from the record that `entity_base_data` snapshots — accept compare, payload, and base all reference the same moment. A mid-generation hand edit to a preserved section lands in the payload; an edit to a re-rolled section is inherently replaced by the re-roll the DM asked for (visible draft-discard precedent, 3-3).
- **No-op guard is idempotence, not pedantry.** A value-identical PATCH is a legal idempotent retry (REST semantics: 204, no change); without the guard it would mint `entity_updated` events with before==after, polluting the undo chain and replaying no-op events.
- **PATCH is synchronous, not a job.** A hand edit is a sub-second store transaction; routing it through the FIFO queue would make the DM's own edit wait behind generation (the spinner is the enemy, NFR9). The `base_revision` check is the optimistic-concurrency equivalent of the job cancel semantics.
- **Rendering duplication accepted.** WorldView mirrors CandidatesView's section catalogs/labels (as it already duplicates `COUNTER_TYPES`/`EDGE_VOCAB`); extracting a shared profile component from the 934-line tested CandidatesView is a separate refactor story, not part of 3-6.
- **2-7 deferral resolved here:** WorldView's muted additional-data block renders committed `data` keys outside the AR24 set — the "real consumer" the deferral waited for.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — all green incl. new entity-edit API/store + lifecycle conflict + regenerate splice tests.
- `make lint && make typecheck` — clean.
- `cd frontend && npm test -- --run` — vitest green incl. WorldView edit + CandidatesView three-way dialog tests.

**Manual checks (if no CLI):**
- With backend up: WorldView → edit a section on a committed character → card updates, revision increments; edit a faction's text → commits unconstrained; edit a field, then in another tab accept a regenerate of the same entity → 409 dialog appears; Re-roll keeps staged edges; Accept-anyway confirms twice and commits; undo (store-level, no route) restores the prior revision.

## Suggested Review Order

**The hand-edit commit seam**

- The store's first direct committed-entity content write: conditional validation, reserved-key guard, null-delete merge, no-op idempotence.
  [`commit.py:688`](../../backend/app/store/commit.py#L688)

- Optimistic-concurrency base: `_check_base` unconditional the moment a caller names a base — stale + no-op is a 409.
  [`commit.py:871`](../../backend/app/store/commit.py#L871)

- The 404-first, hand-parsed PATCH route — body validation can never mask an ownership 404.
  [`entities.py:99`](../../backend/app/api/entities.py#L99)

**The accept-conflict guard (AR4/NFR2)**

- The conflict compare + `confirm_overwrite` escape in ONE transaction: fail closed on mismatch or NULL base, proceed only on the DM's explicit confirm.
  [`candidates.py:465`](../../backend/app/store/candidates.py#L465)

- The session-taking staging variant: payload, splice source, and base share one committed moment by construction.
  [`candidates.py:277`](../../backend/app/store/candidates.py#L277)

- The regenerated-entity wire contract: `regenerates_entity_id` + the confirm flag on the accept body.
  [`api/candidates.py:60`](../../backend/app/api/candidates.py#L60)

- 409 → envelope migration (the dialog trigger rides the message contract).
  [`api/candidates.py:132`](../../backend/app/api/candidates.py#L132)

**The regeneration runner's staging window**

- Single-transaction splice: `_stage_entity_payload` reads the target fresh and stages with the same record as base — a mid-generation hand edit survives in both.
  [`regenerate.py:211`](../../backend/app/pipeline/regenerate.py#L211)

- The candidate-re-roll same-transaction splice + base refresh (never stranded after a hand edit).
  [`regenerate.py:248`](../../backend/app/pipeline/regenerate.py#L248)

**The UI surfaces**

- WorldView full-profile render + additional-data block (resolves the 2-7 deferral) + raw-value drafts.
  [`WorldView.vue:387`](../../frontend/src/views/WorldView.vue#L387)

- Rebase-after-409 closes only on a successful refetch — a failed Reload keeps the draft and the conflict.
  [`WorldView.vue:494`](../../frontend/src/views/WorldView.vue#L494)

- The three-way conflict dialog, message-gated (never a bare conflict code) with in-flight discipline and two-step confirm.
  [`CandidatesView.vue:558`](../../frontend/src/views/CandidatesView.vue#L558)

**Tests and types (peripherals)**

- Wire-level 409 envelope + confirm_overwrite accept, reserved-key 422s, no-op-stale 409, base-is-the-spliced-record pin.
  [`test_entity_edit_api.py:817`](../../backend/tests/test_entity_edit_api.py#L817)

- Store-level lifecycle: conflict row-proposed, NULL-base fail-closed, re-roll-after-edit accept-able.
  [`test_candidate_lifecycle.py:1377`](../../backend/tests/test_candidate_lifecycle.py#L1377)

- Mid-call edit survives in the staged payload; candidate re-roll preserves staged edges.
  [`test_regenerate_pipeline.py:513`](../../backend/tests/test_regenerate_pipeline.py#L513)

- Regenerated schema: the PATCH operation, `regenerates_entity_id`, `confirm_overwrite`.
  [`schema.ts:478`](../../frontend/src/api/schema.ts#L478)
