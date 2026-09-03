---
title: 'No orphans and cascade delete: FR2 store enforcement + FR4 confirmed entity deletion'
type: 'feature'
created: '2026-09-02'
status: 'done'
review_loop_iteration: 0
baseline_commit: '0de49ca'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/spec-2-4-key-figures-carry-a-minimal-5e-stat-block.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** the store accepts a newly created entity that participates in zero edges (FR2 holds only inside the pipeline's `_validate_subgraph`), and no per-entity delete path exists at all — the DM cannot remove an entity, and nothing guarantees zero dangling edges after a removal (FR4, AD-5, AD-23).

**Approach:** extend the store's commit path with a no-orphan rule (every newly created entity must appear in at least one edge among staged + existing edges), and add a commit-path cascade delete that appends `entity_deleted`/`edge_deleted` events (undo already consumes these inverses) behind an explicit confirmation that lists the affected neighbor entities. One new API route mirrors the campaigns DELETE confirm-body pattern.

## Boundaries & Constraints

**Always:**
- **AD-1 single commit path:** both changes land through `store/` — the orphan rule inside `commit_subgraph` validation, deletion as a new commit-path function that appends events and produces exactly one new revision. No raw SQL deletes of world rows (AR20 campaign delete stays the only exception).
- **FR2 rule scope:** the check applies to *newly created* entities only (staged create, not `entity_updated`); an entity satisfies it with an edge whose other endpoint is staged or already committed — wave-1's first commit (empty world) commits fine. Edge-only and update-only commits are unaffected.
- **Delete semantics:** `cascade=false` (default) with live edges ⇒ no-op failure naming the affected entities; `cascade=true` deletes the entity plus every edge touching it in one revision — neighbors survive, never deleted transitively. An entity with zero live edges deletes without confirmation (AD-5 requires confirmation only "with live edges").
- **Confirmation contract:** live-edge delete without confirm fails with a `LiveEdgesError` carrying the affected entities (neighbor id + name per edge); the API surfaces them in the error envelope `details` so the DM sees the list before confirming (AD-5 "listing affected entities").
- **Event payloads match undo's contract exactly:** `entity_deleted` / `edge_deleted` events carry `id` + `before` snapshots with the key sets `undo.py` `_preflight`/`_apply_inverse` already require — undoing a delete revision must recreate rows with stable ULIDs with zero undo.py changes.
- **Stale-base protection:** delete accepts optional `base_revision` with the same `StaleRevisionError` semantics as commit.
- Error mapping goes through the shared `_store_error_as_http` (new errors: live-edges → 409, unknown entity → 404).

**Ask First:** (1) the orphan rule ships without an opt-out flag — if Epic 3's DM hand-editing (3-6) needs bare-entity creation, that is a new owner decision against this default. (2) newly-orphaned *neighbors* (an entity left with zero edges after a cascade) are reported as affected but not auto-deleted. (3) media reclamation on delete (AD-10) stays deferred to Epic 4.

**Never:**
- No event rewriting/deletion; undo remains a compensating commit (AD-2).
- No media row/file changes (Epic 4 owns reclamation).
- No pipeline changes — `_validate_subgraph` already enforces the FR2 rule for generated waves and stays the first line of defense.
- No schema migrations, no new tables; no frontend/wire changes beyond the new route.
- No transitive/recursive cascade beyond the target's touching edges.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| COMMIT_ORPHAN | staged create with zero staged/existing edges | whole subgraph rejected, zero revisions | `OrphanEntityError` (422) naming the entity |
| COMMIT_CONNECTED | staged create with ≥1 edge into staged∪existing | commits as today | N/A |
| COMMIT_UPDATE_EDGELESS | `entity_updated` on an existing edgeless entity | accepted (rule is create-only) | N/A |
| DELETE_EDGELESS | delete entity with zero live edges, no confirm | one revision: `entity_deleted` (+ `before`) | N/A |
| DELETE_LIVE_NO_CONFIRM | delete entity with live edges, cascade false | no state change; affected entities listed (ids + names) | `LiveEdgesError` (409) |
| DELETE_CASCADE_CONFIRMED | cascade true | one revision: entity + all touching edges deleted; neighbors intact; zero dangling edges | N/A |
| DELETE_UNKNOWN | unknown/foreign-campaign entity | no state change | 404 |
| DELETE_STALE | stale `base_revision` | no state change | `StaleRevisionError` (409) |
| DELETE_UNDO | undo of a cascade-delete revision | entity + edges recreated, stable ULIDs, one compensating revision | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/store/commit.py` -- `commit_subgraph` (L205)/`_commit` (L225); validation order `_check_base` → `_check_entity_ids`/`_check_edge_ids` → per-edge loop (`DanglingEdgeError` covers edge endpoints only). Insert the create-only orphan check after the edge loop: staged-new ids minus participants of staged∪existing edges → `OrphanEntityError`. New delete function belongs here (or a sibling `store/delete.py` reusing `_commit`'s revision/event machinery): `_add_event` with `entity_deleted`/`edge_deleted` + `before` snapshots.
- `backend/app/store/undo.py` -- `_preflight` (L98-184) / `_apply_inverse` already parse `entity_deleted`/`edge_deleted` with `("id", ("before", _ENTITY_KEYS/_EDGE_KEYS))` — the producer MUST match these key sets; `_ENTITY_KEYS`/`_EDGE_KEYS` define the snapshot fields.
- `backend/app/store/read.py` -- add `entity_live_edges(session, campaign_id, entity_id)` (rowid-ordered edges where src or dst = entity_id) — used by delete preflight and the API listing; `world_edges` exists for adjacency building.
- `backend/app/store/models.py` -- Event model ("rows are never deleted or rewritten"), Entity/Edge tables.
- `backend/app/api/campaigns.py` -- DELETE `confirm`-body pattern (L153): ownership first (foreign = 404), body via `await request.json()`, missing/false/malformed → 400; mirror for entities.
- `backend/app/api/common.py` -- `_store_error_as_http` (L36): add `LiveEdgesError` → 409, unknown-entity error → 404; register the new router wherever campaigns' router is registered.
- `backend/app/pipeline/build_in.py` -- `_validate_subgraph` (L332) already rejects wave orphans; read-only reference, unchanged.
- `backend/tests/test_store.py` -- conventions: `test_<behavior>_<outcome>` + AD-referencing docstrings, fixtures `world`/`_seed_world`/`_head`; orphan + delete + delete-undo tests land here.
- `backend/tests/test_campaigns_api.py` -- mirror for a new `test_entities_api.py` (confirm 400, 409 listing, foreign 404, cascade 204/200).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/commit.py` -- `OrphanEntityError` + create-only no-orphan check; commit-path `delete_entity(campaign_id, entity_id, *, cascade, base_revision=None)` emitting `entity_deleted`/`edge_deleted` with undo-compatible `before` snapshots.
- [x] `backend/app/store/read.py` -- `entity_live_edges` helper.
- [x] `backend/app/api/entities.py` -- DELETE `/api/campaigns/{campaign_id}/entities/{entity_id}` mirroring the campaigns confirm pattern; router registration.
- [x] `backend/app/api/common.py` -- error mappings for the new StoreErrors.
- [x] `backend/tests/test_store.py` + `backend/tests/test_entities_api.py` -- I/O-matrix rows (orphan rule, delete semantics, undo of delete, API confirm flow).
- [x] Sprint sync `2-5-no-orphans-and-cascade-delete` → in-progress → review.

**Acceptance Criteria:**
- Given a commit staging a newly created entity with no edge into staged or existing state, when it is committed, then the whole subgraph is rejected and zero revisions are written (FR2).
- Given an entity with live edges, when deletion is requested without confirmation, then nothing changes and the response lists the affected neighbor entities; with confirmation, one revision removes the entity and its touching edges and the graph holds zero dangling edges (FR4, AD-5, AD-23).
- Given a cascade-delete revision, when undone, then the entity and its edges are recreated with stable ULIDs via the existing undo machinery.

## Design Notes

**Why the rule is create-only at the store.** FR2 speaks of *generated* entities; the pipeline already enforces it for every wave (and Epic 3's acceptance will per AD-24). A store-level rule closes the remaining hole — direct commits bypassing the pipeline — without inventing a flag nobody uses. If 3-6's DM hand-editing later needs bare entities, that is an explicit owner renegotiation (Ask First 1).

**Why cascade is one revision, neighbors survive.** Deleting E plus its touching edges cannot dangle anything — every removed edge has E as an endpoint. Auto-deleting neighbors left edgeless would silently destroy world state the DM didn't name; they are listed as affected instead, and the DM deletes them explicitly (Ask First 2).

**Why undo needs zero changes.** `undo.py` already implements the inverse of `entity_deleted`/`edge_deleted` from `before` snapshots — 2.5 only builds the producer. Tests pin the stable-ULID recreation so the contract can't drift.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. the new orphan/delete/undo/API rows (deterministic).
- `make lint && make typecheck` -- expected: ruff + mypy strict clean (frontend untouched).

### Review Findings

<!-- Review ran against a diff that accidentally included the already-committed 2.4/retro work (frontmatter baseline was authored as e8e2cc2, corrected post-review to 0de49ca); 2.4-scope findings were triaged to the ledger, not patched here. -->

- [x] [Review][Patch] Restored the `from app.store.models import (...)` re-export block in `store/__init__.py` — the implementation had removed it while `__all__` still advertised the names; `from app.store import EntityInput` raised ImportError.
- [x] [Review][Patch] Self-loop edges no longer count as connecting a new entity in the no-orphan rule (pipeline forbids self-loops; a self-loop is not an edge "into existing world state"); foreign-world seed in `test_delete_unknown_and_foreign_entity_rejected` re-seeded with a real edge; pinned by `test_commit_self_loop_does_not_connect_new_entity`. [backend/app/store/commit.py]
- [x] [Review][Patch] `StoreHTTPException` moved to `core/errors.py` and re-exported; `handle_http_error` passes envelope `details` only for `StoreHTTPException` (was a generic `getattr` leaking any HTTPException's `details`). [backend/app/core/errors.py, backend/app/api/common.py]
- [x] [Review][Patch] `_store_error_as_http` renamed to public `store_error_as_http` (docstring already claimed the shared public surface); all callers updated, no alias. [backend/app/api/common.py]
- [x] [Review][Patch] Restored the "One transaction, exactly one new revision, all-or-nothing (AR3)." contract line in the `commit_subgraph` docstring.
- [x] [Review][Patch] `test_stale_base_between_waves_fails_core_stays` fixture: anchor looked up by name instead of list position; semantically odd place→faction edge replaced with Mira(character) located_in keep(place). [backend/tests/test_build_in_pipeline.py]
- [x] [Review][Patch] DELETE_STALE wire coverage added — stale `base_revision` → 409 with no state change, current head → 204 (`test_delete_stale_base_revision_409_then_current_head_succeeds`). [backend/tests/test_entities_api.py]
- [x] [Review][Patch] entities-API client fixture aligned with the conftest fixture-scoped `create_app()` convention (epic-1 retro item 7) instead of the module-level app singleton. [backend/tests/test_entities_api.py]
- [Review][Defer ×8] 2.4-scope items (repair spell-reference subsetting, `_check_spells` suppression, cancel-during-repair test, repair ref digit overflow, blank descriptions, prompt heading/"(none listed)" nit) + env-helper boundary/leniency inconsistency + post-delete media reclamation for 4-3 — routed to `deferred-work.md`.
- [Review][Reject] Ask-First-2 "reported as affected" is satisfied: post-cascade edgeless neighbors are a subset of the `LiveEdgesError` affected list; `OrphanEntityError`→422 mapping is a defensive shared-mapper entry (Epic 3 accept path gains API callers); "Suggested Review Order" section is not part of the template.

**Second review pass — 2026-09-03** (4 layers: blind-hunter, edge-case-hunter, verification-gap, acceptance-auditor; post-patch state of 5e655d1)
- [x] [Review][Patch] Malformed/non-dict JSON body on entity DELETE now a 400 (owner decision 2026-09-03: mirror campaigns; missing body still allowed per DELETE_EDGELESS). [backend/app/api/entities.py:46-51]
- [x] [Review][Patch] Store-level commit rejects self-loop edges (`src == dst`) with a structured StoreError → 422 (owner decision 2026-09-03: close the bypass hole outright; neighbor listing still skips self-loops defensively). [backend/app/store/commit.py:312-338]
- [x] [Review][Patch] Self-loop delete lists the entity as its own affected neighbor — `neighbor_id = edge.dst if edge.src == entity_id else edge.src` resolves a self-loop to the target itself, so the AD-5 409 listing names the deleted entity; skip `src == dst` edges in the neighbor loop (cascade deletion itself is unaffected). [blind-hunter+edge-case-hunter+acceptance-auditor] [backend/app/store/commit.py:505-513]
- [x] [Review][Patch] Wrong-typed cascade/confirm JSON values silently ignored — `{"cascade": "true"}` behaves as absent: 409 on live edges (safe) but a silent 204 delete on an edgeless target; reject non-bool cascade/confirm with 400, mirroring campaigns' strict `is not True` gate. [backend/app/api/entities.py:52-53]
- [x] [Review][Patch] AC3's edge-ULID stability is implemented but not pinned — `test_undo_of_cascade_delete_recreates_with_stable_ulids` resolves edge ids via post-undo `_edge_id(...)` lookup, so a regression that re-mints ULIDs on undo would pass; capture edge ids from the pre-delete state (or `edge_deleted` payloads) and assert equality. [acceptance-auditor] [backend/tests/test_store.py:1486-1489]
- [x] [Review][Patch] Entities DELETE body-parsing contract ships unverified — malformed JSON → absent, non-dict body → absent, non-string base_revision → 400 are pinned in the docstring but exercised by none of the 8 API tests (and the mirrored campaigns test only sends well-formed JSON); add the three rows (malformed-body shape depends on the Decision above). [blind-hunter+verification-gap] [backend/tests/test_entities_api.py, backend/app/api/entities.py:49-66]
- [x] [Review][Patch] `OrphanEntityError` docstring omits the self-loop exclusion the implementation enforces (commit.py:351-357) — an implementer working from the docstring alone would accept a self-loop-connected orphan. [backend/app/store/commit.py:196-200]
- [x] [Review][Patch] Deferred-work ledger misattributes six 2.4-scope findings (statblocks/knowledge/build_in/config) to `spec-2-5` — correct the source attribution for traceability. [_bmad-output/implementation-artifacts/deferred-work.md:101-128]
- [x] [Review][Patch] Stale spec anchor: Suggested Review Order pins "edge_deleted per touching edge + entity_deleted" at commit.py:478, which falls inside `delete_entity`'s docstring; the event-append block sits ~L523-541. [spec §Suggested Review Order]
- [x] [Review][Patch] The `details` envelope wire shape is undocumented — `affected_entities` item keys (id/name), rowid ordering, dedup, and the `entity_id` key are defined only by reverse-reading tests; document the schema for 2.7/Epic 3 consumers. [backend/app/api/common.py:64-71, backend/app/api/entities.py]
- [x] [Review][Patch] entities.py propagates a false rationale: "FastAPI cannot bind a Pydantic body to DELETE directly" is inaccurate — FastAPI binds Pydantic DELETE bodies fine; the real motive is lenient manual body parsing; fix the comment in the new file (don't widen the campaigns.py copy). [backend/app/api/entities.py:3-5]
- [x] [Review][Defer] `base_revision` is accepted on the wire but unusable until revision ids are exposed (2.6 export / 2.3 world view own that surface). [backend/app/api/entities.py:52-65] — deferred, pre-existing scope boundary
- [x] [Review][Defer] No delete-side concurrency coverage (delete vs commit race, interleaved base=None semantics). [backend/app/store/commit.py:445-475] — deferred, test hardening
- [x] [Review][Defer] Delete during an in-flight build-in job: the next wave dies on DanglingEdgeError surfaced as a structured job-fail event; no I/O-matrix row — Epic 3 candidate lifecycle revisits. [spec §I/O & Edge-Case Matrix] — deferred, frozen "no pipeline changes"
- [x] [Review][Defer] Affected-listing guarantees (multi-neighbor dedup, rowid ordering, id+name shape) asserted only via the helper, never through the shipped API error payload. [backend/tests/test_entities_api.py:150-170] — deferred, test hardening
- [x] [Review][Defer] Async route runs a synchronous session_scope-backed SQLite transaction on the event loop (blocks all concurrent requests for the txn duration incl. busy-timeout waits) — inherited from the campaigns DELETE pattern, codebase-wide. [backend/app/api/entities.py:35-65] — deferred, pre-existing pattern


## Suggested Review Order

**FR2 — store-level no-orphan rule**

- Entry point: the create-only orphan backstop, after edge validation, before the revision exists.
  [`commit.py:366`](../../backend/app/store/commit.py#L366)
- New error names the orphans (id + name) for the fail path.
  [`commit.py:195`](../../backend/app/store/commit.py#L195)
- Self-loop edges are rejected outright at the store backstop — new and existing entities alike (second-pass review fix, owner decision 2026-09-03).
  [`commit.py:341`](../../backend/app/store/commit.py#L341)
  [`test_store.py:1311`](../../backend/tests/test_store.py#L1311)

**FR4 — commit-path cascade delete**

- Public entry: campaign-scoped fetch, optional base_revision, cascade gate.
  [`commit.py:471`](../../backend/app/store/commit.py#L471)
- One revision: `edge_deleted` per touching edge + `entity_deleted`, `before` snapshots match undo's keys.
  [`commit.py:558`](../../backend/app/store/commit.py#L558)
- Live-edge rejection carries the affected neighbors (ids + names, deduplicated) for AD-5's listing.
  [`commit.py:220`](../../backend/app/store/commit.py#L220)
- Adjacency read helper — rowid-ordered edges touching one entity; delete preflight + API listing share it.
  [`read.py:78`](../../backend/app/store/read.py#L78)

**Wire surface**

- Route mirrors AR20's campaigns confirm-body pattern; cascade-without-confirm is a 400.
  [`entities.py:40`](../../backend/app/api/entities.py#L40)
- `StoreHTTPException` moved to core (no api→core leak); envelope `details` gated on it.
  [`errors.py:53`](../../backend/app/core/errors.py#L53)
- Mapper made public (`store_error_as_http`); new errors: orphan 422, self-loop 422, live-edges 409, unknown-entity 404.
  [`common.py:44`](../../backend/app/api/common.py#L44)

**Undo + tests**

- Undo of a cascade recreates entity + edges with stable ULIDs — zero undo.py changes, pinned.
  [`test_store.py:1486`](../../backend/tests/test_store.py#L1486)
- Matrix rows: orphan rejection, cascade one-revision/zero-dangling, API 409 listing + stale base.
  [`test_store.py:1283`](../../backend/tests/test_store.py#L1283)
  [`test_entities_api.py:126`](../../backend/tests/test_entities_api.py#L126)
