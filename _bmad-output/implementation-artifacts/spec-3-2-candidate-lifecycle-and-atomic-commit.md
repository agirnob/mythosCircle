---
title: 'Candidate Lifecycle and Atomic Commit'
type: 'feature'
created: '2026-09-04'
status: 'done'
baseline_commit: '50216fb98ebc25841eec75a73ff23e6dc28ef227'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 3.1 stages 2–3 candidate rows that commit nothing — the DM has no way to make a candidate real or drop it. Story 3.2 delivers the lifecycle: accept commits the candidate's subgraph as exactly one atomic transaction; reject leaves the world untouched; a candidate never mutates accepted state (FR11, AR7, AD-15).

**Approach:** Two synchronous, owner-only POST routes on the candidates resource. Accept translates the staged AR19 payload into one `EntityInput` + `EdgeInput`s and commits them inside ONE store transaction that also flips the row's status to `accepted` — all-or-nothing. Reject flips the status to `rejected`. No new job kind: accept/reject involve no inference, so the queue is untouched (AR5).

## Boundaries & Constraints

**Always:**
- Accept/reject are store-layer functions in `store/candidates.py`, written through the store only (AD-1); the API layer stays thin, mirroring `delete_entity` (`entities.py`).
- Accept atomicity: the subgraph commit and the `accepted` status flip share ONE session/transaction (FR11, AD-15); any `StoreError` rolls back both — a failed accept leaves status `proposed` and zero new revisions.
- Accept commits a NEW entity: `EntityInput(id=None)` — the store assigns a fresh ULID. The accept path never builds `EntityInput(id=<existing>)`, so existing entities are structurally never mutated (FR11, AR4); no edge re-targeting.
- Accept translation: `EntityInput(kind="character", name=payload["name"], data=<payload minus "edges">)` — `stat_block` lands at `data["stat_block"]` matching the entity-data convention (`generate.py:162`); each staged edge becomes `EdgeInput`: `outbound` → `(new_entity, endpoint)`, `inbound` → `(endpoint, new_entity)`, `type`/`counter` pass through.
- `base_revision` = latest revision id read inside the accept transaction (`latest_revision(session, campaign_id)`; `_check_base` requires it when the world is non-empty, `commit.py:581`). Accept calls `commit._commit(session, ...)` — the internal session-taking variant — not `commit_subgraph` (own session): single-transaction atomicity needs one unit of work. The private import gets an explanatory comment.
- The commit path is the accept-time authority for dead endpoints: a staged endpoint deleted since staging fails with `DanglingEdgeError` → no state change, status stays `proposed` (visible rebase-or-reject, AD-2).
- Reject: one transaction flipping `proposed → rejected`; no revision, no event, no world read; verified by an unchanged revision count.
- Status stays a closed set on the DB: widen `ck_proposed_candidate_status` to `('proposed','accepted','rejected')` via constants `STATUS_PROPOSED / STATUS_ACCEPTED / STATUS_REJECTED` + a `PROPOSAL_STATUS` tuple, plus `_migrate_proposed_candidate_status` (rebuild recipe, list derived from the constants — mirror `_migrate_job_kind`, `db.py:118`). No persistent deploy DB exists in the repo, so the migration is belt-and-braces for developer DBs (3-1 `ck_job_kind` precedent).
- GET candidates gains a `status` query param defaulting to `proposed` (closed-set validated, junk → 422): settled rows must not regress the accept-screen read. Job/batch scoping stays deferred (round-3 ledger, story 3.3).
- Errors: `CandidateNotFoundError` (404) and `CandidateSettledError` (409) defined in `store/candidates.py`, mapped in `api/common.py`; a candidate id of another campaign is the same indistinguishable 404 as unknown one (no oracle).

**Ask First:** none expected. If any refactoring beyond the listed files, or a change to the event/revision model, HALT and ask before proceeding.

**Never:**
- No candidate UI, accept screen, or frontend changes (story 3.3).
- No job/queue involvement for accept or reject; no new WS frame types; no new job kind.
- No row deletion as the lifecycle mechanism — terminal statuses only (audit trail).
- No committed revisions from reject; no entity updates with existing ids anywhere in this story.
- No per-section regeneration (3.5), no hand-edit (3.6), no timeout/auto-GC of stale proposals (DM-driven lifecycle only).
- Counter-range enforcement against `EDGE_COUNTER_SEMANTICS` stays deferred (ledger; the commit path already guards int shape → `InvalidEdgeCounterError`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ACCEPT_HAPPY | Owner POST accept; staged `proposed` candidate; all endpoints committed | One new revision; entity + edges committed; row → `accepted`; zero dangling edges | N/A |
| ACCEPT_STALE_ENDPOINT | Endpoint entity deleted/undone since staging | Accept rejected; no revision; status stays `proposed` | `DanglingEdgeError` → 422 (rebase-or-reject) |
| ACCEPT_BAD_COUNTER | Staged edge counter not int | No state change; `proposed` | `InvalidEdgeCounterError` → 422 |
| ACCEPT_ALREADY_SETTLED | Candidate already `accepted`/`rejected` | No state change | `CandidateSettledError` → 409 |
| ACCEPT_UNKNOWN | Unknown or foreign-campaign id | Single 404 | `CandidateNotFoundError` → 404 |
| REJECT_STANDALONE | `candidate rejected` | Row → `rejected`; world untouched | N/A |
| REJECT_ALREADY_SETTLED | Already settled | No state change | `CandidateSettledError` → 409 |
| FOREIGN_OWNER | Unauthed / foreign campaign | 401 / 404 | Auth + ownership-first |
| STATUS_FILTER_BAD | `?status=junk` | 422 validation | 422 envelope |
| UNDO_AFTER_DONE | DM undoes accept revision | World reverts; row stays `accepted` (lifecycle fact, not world state) | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/store/models.py:199-228` — `ProposedCandidate` (kind/status CHECKs at :219-227); constants at :36-43; `Entity`/`Edge` at :86-138; `EntityInput`/`EdgeInput` at :284-313.
- `backend/app/store/commit.py:266-282` — `commit_subgraph` (own session); `_commit(session, campaign_id, entities, edges, base_revision)` ; `_check_base` :581; rejection set incl. `OrphanEntityError`/`DuplicateEdgeError`/`SelfLoopEdgeError`; per-row events at ~:486-537.
- `backend/app/store/candidates.py` — `stage_candidates` :90, `_check_edge` :142-148, `discard_candidates` :155 (proposed-only filter), `accept_candidate` :175 (+ `_accept_edge` :252), `reject_candidate` :286, `list_candidates` :303, `EDGE_DIRECTIONS` :60. Accept/reject landed here.
- `backend/app/store/db.py:91-95,171-220` — migration sequence + `_migrate_proposed_candidate_status`; `_migrate_job_kind` :118 rebuild recipe — its template.
- `backend/app/store/__init__.py:39-56` — re-export block (`accept_candidate`/`reject_candidate` + errors + status constants); `__all__` at :141.
- `backend/app/api/candidates.py:70-157` — GET list (ownership-first 404 + cursor + closed-set `status` param), POST accept :111, POST reject :134; `api/common.py:57-72` mapping table (404: `CandidateNotFoundError`/`JobNotFoundError`/`UnknownCampaignError`/`UnknownEntityError`; 409: `CandidateSettledError`/`JobStateConflictError`…); `api/entities.py:37-92` owner-first pattern; `api/auth.py:113` `get_current_account`.
- `backend/app/pipeline/generate.py:470-504` — staged payload: `{name, role, personality, secret, rumor, party_hook, stat_block, edges: [{endpoint, direction, type, counter}], +AR24 extras}`; :160-162 kind="character" + `data={"stat_block": ...}`.
- `backend/tests/conftest.py:26-48` — `client` fixture, scratch DB (`MYTHOSCIRCLE_DB`), worker disabled; `test_generate_api.py:33-165` — `job_api`/`_register`/`_owned_campaign`/`_commit_world`/`_drain`/`_post_job`; `test_store.py:181/389/1284/1468` — atomicity/stale-base examples.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/models.py` — add `STATUS_ACCEPTED`/`STATUS_REJECTED` + `PROPOSAL_STATUS` tuple; widen `ck_proposed_candidate_status` — closed lifecycle set.
- [x] `backend/app/store/candidates.py` — `accept_candidate(campaign_id, candidate_id)` + `reject_candidate(campaign_id, candidate_id)` + `CandidateNotFoundError`/`CandidateSettledError` (see ## Code Map) + `__all__`.
- [x] `backend/app/store/db.py` — `_migrate_proposed_candidate_status` (constants-derived rebuild) + call in `init_db`.
- [x] `backend/app/store/__init__.py` — re-export the new functions + errors.
- [x] `backend/app/api/common.py` — `CandidateNotFoundError` → 404, `CandidateSettledError` → 409.
- [x] `backend/app/api/candidates.py` — POST accept/reject routes + GET `status` param.
- [x] `backend/tests/test_candidate_lifecycle.py` + `test_generate_api.py` additions — matrix + AC + non-mutation regression.

**Acceptance:**
- Given a `proposed` candidate accepted, then commit is exactly one atomic transaction — partial failure rolls back the whole candidate (status `proposed`, zero revisions) (FR11, AD-15).
- Rejecting a candidate leaves the world untouched (no new revision).
- Accepting a fresh candidate for an existing entity creates a NEW entity (fresh ULID) and never mutates the accepted one (FR11).

## Design Notes

- Accept is synchronous: it commits existing data, no inference. The "nothing blocks on generation" rule is about LLM calls, not store commits.
- Default `proposed` GET filter keeps the accept screen uncluttered and prevents regressing the 3.1 read; job/batch scoping stays story 3.3.
- Private `_commit` import over new public in-session wrapper: avoids adding store API surface for one call site; comment justifies AD-15 atomicity.
- Retained terminal rows make "only what I accept became real" auditable and stay AR7-invisible (never leave the candidates table).
- Accept pre-mints the new entity's ULID (`ids.new_id()` passed as the `EntityInput` id) because the staged edges must name the new entity before `_commit` runs — the frozen "id=None" wording describes the invariant (fresh ULID, never an existing entity), not the minting site; the minted id can never collide with an existing entity, so `_commit` structurally creates (never updates).
- The cancel-race cleanup (`discard_candidates`) filters `status == 'proposed'`: settled rows are audit-trail facts and survive a job cancel that lands after a lifecycle transition.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — expected: all green (459 existing + new).
- `make lint && make typecheck` — expected: clean.

**Manual checks (if no CLI):**
- With backend up: `generate` → list → accept candidate 1 → `world_state` shows it, revision chain +1; reject candidate 2 → list shows only `proposed` by default.

## Suggested Review Order

**The accept transaction (story core)**

- One session: row guard -> payload translation -> commit -> status flip; any StoreError rolls back both halves.
  [`candidates.py:175`](../../backend/app/store/candidates.py#L175)

- Staged edge -> EdgeInput: outbound/inbound wiring, closed vocab, counter required (no silent default).
  [`candidates.py:249`](../../backend/app/store/candidates.py#L249)

- Reject is the pure flip: no revision, no event, world untouched.
  [`candidates.py:283`](../../backend/app/store/candidates.py#L283)

**Schema + lifecycle set**

- STATUS_ACCEPTED/REJECTED + PROPOSAL_STATUS tuple; CHECK widened from the constants.
  [`models.py:41`](../../backend/app/store/models.py#L41)

- Table-rebuild migration: sorted list from models, idempotent no-op, indexes re-created.
  [`db.py:171`](../../backend/app/store/db.py#L171)

**API surface**

- Owner-first 404 POSTs returning the settled CandidateResponse.
  [`candidates.py:110`](../../backend/app/api/candidates.py#L110)

- GET status filter (default proposed, closed-set 422) keeps settled rows off the accept-screen read.
  [`candidates.py:47`](../../backend/app/api/candidates.py#L47)

- 404/409/422 mappings for the new errors.
  [`common.py:58`](../../backend/app/api/common.py#L58)

**Cancel-race safety (AR7 boundary)**

- discard_candidates now proposed-only: settled rows survive cancel cleanup.
  [`candidates.py:155`](../../backend/app/store/candidates.py#L155)

**Tests (peripherals)**

- Matrix happy path: one revision, edges wired, row settled.
  [`test_candidate_lifecycle.py:145`](../../backend/tests/test_candidate_lifecycle.py#L145)

- Atomicity both halves: mid-commit failure and status-flip failure each roll back everything.
  [`test_candidate_lifecycle.py:377`](../../backend/tests/test_candidate_lifecycle.py#L377)

- Migration pinned: legacy single-status DDL widened, rows survive, idempotent.
  [`test_candidate_lifecycle.py:622`](../../backend/tests/test_candidate_lifecycle.py#L622)

- Wire-level 422s: bad counter and malformed payload through the POST routes.
  [`test_generate_api.py:560`](../../backend/tests/test_generate_api.py#L560)

- Status filter x cursor: settled anchor ignored, paging continues.
  [`test_generate_api.py:641`](../../backend/tests/test_generate_api.py#L641)
