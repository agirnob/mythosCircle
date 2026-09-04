---
title: 'Inline Relation Editing'
type: 'feature'
created: '2026-09-04'
status: approved
review_loop_iteration: 0
baseline_commit: '8b4a8bb47dc8fe6997a2b740fce0126842e0729c'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
---

## Intent

**Problem:** Relations are read-only in both surfaces. On the accept screen (3.3) the staged edges are pinned "verbatim" by `accept_candidate` — the DM cannot wire the candidate into the web she wants before committing. On the world view (2.7) committed relations render as inert text — the DM cannot add, edit, or delete any edge of the closed vocabulary. The web of the world is not yet shaped by the DM (FR9).

**Approach:** Two cooperate surfaces, one store story.
1. **Committed edges** — a new relation-edge REST surface hitting the existing `commit_subgraph` add/update path plus a new standalone store `delete_edge`; the WorldView entity cards get inline add/edit/delete of relations into any other existing entity.
2. **Candidate edges** — relax `accept_candidate`'s verbatim-edges rule so the accept override can carry the DM's own edge set (added/edited/deleted staged edges); the CandidatesView relation lines become editable before accept, and the accept commits that edge set as the candidate's subgraph.

Every mutation commits through the store: exactly one revision, all-or-nothing, per-type counters change only via commit (AR8, AD-23), zero dangling edges after every commit.

## Boundaries & Constraints

**Always:**
- Closed vocabulary only: `EDGE_TYPES` = relationship, debt, grudge, loyalty, member_of, located_in, rival_of, kin_of, ally_of, enemy_of, directed, per-type counter (AD-5). Add/edit/delete of edges uses only these types; no free text. Every edge carries a counter (int; store shape-validates int + SQLite range — semantic bounds stay pipeline/Phase-3 owned per the 2.3 deferral).
- Every mutation goes through the store commit path (AD-1) as exactly one atomic transaction + one revision. Edge add = `EdgeInput(id=None)`; edge counter update = `EdgeInput(id=<existing>)` (src/dst/type immutable — edge re-targeting is forbidden, AD-2); edge delete = new standalone store path appending the same `edge_deleted` event shape undo's `_inverse_edge_deleted` already consumes (cascade delete already writes it) → undo is unchanged.
- Add/update inherits all existing `_commit` guards: `InvalidEdgeTypeError`, `InvalidEdgeCounterError`, `DanglingEdgeError`, `SelfLoopEdgeError`, `DuplicateEdgeError`, `EdgeRetargetError`, `EmptySubgraphError`; edge-only commits are legal (`edges` non-empty, `entities` empty). A new edge to an existing entity is never an orphan (orphan rule is entity-create-only, spec-2.5).
- Standalone edge delete needs **no cascade/confirm**: deleting an edge never dangles (edges are the connectors) and never orphans an entity — unlike `delete_entity`. It is always safe; the commit path writes one `edge_deleted` event, neighbors survive.
- Ownership and 404s follow the campaign route pattern (no oracle): a foreign/unknown campaign OR entity in an edge body / edge id is the single indistinguishable 404, checked before any state change. Foreign campaign id is always 404 even with a malformed/absent body.
- Error envelope + status mapping: 4xx = user error, never a state change (AR15); body validated as strict JSON object (mirror `delete_entity`'s 400-vs-404 ordering).
- `base_revision` is opt-in optimistic concurrency on add/edit/delete (same semantics as `delete_entity` — supplied must match head else `StaleRevisionError` 422; omitted targets current head, the DM wire default). No rebase UI in 3.4 — a stale commit surfaces as a 409/422 the DM retries after refetch (WorldView already refetches on WS frames).
- Frontend TS types derive from regenerated OpenAPI (`npm run gen:api`); reuse WorldView's card/relation-line CSS and the existing counter-rendering convention (counter renders whenever the edge carries one — forward-compat).
- Accept screen: adding/editing a candidate edge targets **any committed world entity** (not another proposed candidate) — the candidate's edge endpoint must resolve to committed world state (AR19 edge into existing world), and the accept commit path enforces it. Staged-edge add/edit/delete is client-side payload mutation that commits only on accept, all-or-nothing (AD-15).

**Ask First:**
- If making candidate edges editable requires touching the 3.3 frozen override contract beyond relaxing the verbatim check (e.g., reordering candidate commit, re-targeting an accepted entity), halt and present the tradeoff.

**Never:**
- No relation editing against a *proposed* (uncommitted) candidate as a distance endpoint — candidates do not exist as world entities until accepted.
- No edge re-targeting of a committed edge (immutable src/dst/type) — the DM deletes and re-adds instead (AD-2).
- No semantic counter-range validation (negative debt, zero/oversized score) — that is the deferred 2.3 boundary decision, outside 3.4.
- No portraits, no exports changes, no rebase-or-reject UI, no undo UI (undo is 3.6 territory on a different surface).
- No UI e2e (AR22); vitest only.
- No new edge vocabulary, no free-text relation labels.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| EDGE_ADD | POST /edges `{src, dst, type, counter}` on a world | New edge commits, one revision, 201 + EdgeResponse(id) | Unknown/foreign campaign → 404 (even with a malformed/absent body); unknown endpoint entity → 422 DanglingEdge (spec-2-2 contract); type outside vocab → 422; self-loop → 422; (src,dst,type) exists → 409 DuplicateEdge; counter non-int (strict — "3"/3.0/bool rejected) → 422; base_revision stale → 409 |
| EDGE_EDIT | PATCH /edges/{id} `{counter}` | Counter updates in one revision, 200 + EdgeResponse; src/dst/type untouched (explicit id NEVER creates — an id-set staging naming no live edge is UnknownEdgeError 404, never a resurrection) | Unknown/foreign campaign or edge id → 404; counter invalid → 422; stale base → 409 |
| EDGE_DELETE | DELETE /edges/{id} (no body) | One `edge_deleted` event, one revision, 204; neighbors survive; no confirm needed | Unknown/foreign campaign or edge id → 404; stale base → 409 |
| CANDIDATE_EDGE_EDIT | Accept override with a modified `edges` list (added/edited/deleted) | Override edges replace staged edges; the commit wires the DM's set to the new entity; ≥1 edge must remain else commit `OrphanEntityError`; one revision | Override edge endpoint not committed / type outside vocab → 422, row stays `proposed`; zero edges → commit OrphanEntityError → 422 |
| STALE_COMMIT | Edge mutation with outdated `base_revision` | Store rejects `StaleRevisionError`, no state change | 409 (envelope precedent, spec-2-5 DELETE_STALE); the head id rides the Python attribute, wire detail is prose |
| FOREIGN_CAMPAIGN | Any edges op on another campaign's id | Single indistinguishable 404, no oracle — checked BEFORE body parsing (entities.py precedent) | N/A |

## Code Map

- `backend/app/store/commit.py` — `commit_subgraph` :266 already handles edge add/update (`EdgeInput` id-None vs id-set; `_commit` edge loop :458-491). Add `delete_edge(campaign_id, edge_id, *, base_revision=None) -> models.Revision` mirroring `_delete_entity` :509-563 (ownership 404, `StaleRevisionError`, append `edge_deleted` with `before` = `_edge_snapshot`); NO cascade/confirm — single-edge delete is always dangling-safe.
- `backend/app/store/undo.py` — `_inverse_edge_deleted` :249 already restores an `edge_deleted` event — no change; verify with a test.
- `backend/app/store/candidates.py` — `accept_candidate` :301-336: relax the override's "edges verbatim" check (:310) to accept an override whose `edges` list (when present) is a list of valid edge dicts; validate via the existing `_accept_edge`/commit path (endpoint→committed world, type→vocab, counter int). `_candidate_edges`/`_check_candidate_edges` stay (reused conceptually). `__all__` unchanged.
- `backend/app/api/edges.py` (new) — router with POST add, PATCH edit, DELETE delete. Mirrors `entities.py`'s body/ownership/404 patterns and `common.store_error_as_http`. Response uses an `EdgeOut` schema (`id, src, dst, type, counter`). Register router in `backend/app/main.py` (or API `__init__`).
- `backend/app/store/models.py` — no new columns. `EdgeInput` docstring stays (add/update); delete is a separate store call.
- `backend/tests/test_edges_api.py` (new) — REST coverage: add/edit/delete happy + ownership 404, vocab/types, self-loop, duplicate, stale base, non-JSON/non-dict body.
- `backend/tests/test_store_commit.py` — `delete_edge` store tests: event shape, undo round-trip, unknown/foreign, stale base, neighbor survival, edge-only add commit.
- `backend/tests/test_candidate_lifecycle.py` — accept-with-edited-edges cases: added edge, deleted staged edge, edited counter, bad-endpoint 422 (row stays proposed), zero-edges OrphanEntity 422.
- `frontend/src/api/schema.ts` — regenerate via `npm run gen:api` (backend on :8000); new edges ops + `EdgeOut`.
- `frontend/src/api/client.ts` — reuse `apiFetch` envelope handling; add edges calls (add/update/delete) as needed (or via a new store action).
- `frontend/src/views/WorldView.vue` — entity cards: edit-relations affordance per card; add-relation form (destination entity picker = other committed entities, type from `EDGE_TYPES`, counter); inline per-edge edit (counter) + delete. Commit → world store refetch (WS already coalesces). Reuse relation-line CSS.
- `frontend/src/views/CandidatesView.vue` — make the read-only staged relation lines editable before accept (add/edit/delete of the candidate's `edges` list); accept sends the (possibly changed) edge list. Reuse the 3-3 edit-before-accept pattern.
- `frontend/src/stores/world.ts` / new `frontend/src/stores/relations.ts` — action(s) calling edges API then refetching; keep store read-only on world state otherwise.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/commit.py` — `delete_edge` (standalone, undo-compatible, owner-404, stale-base); `_commit` hardened: an explicit edge id naming no live edge raises `UnknownEdgeError` (never creates — the PATCH-after-DELETE resurrection vector).
- [x] `backend/app/store/candidates.py` — relax override verbatim-edges to a validated, editable edge set (absent `edges` keeps staged).
- [x] `backend/app/api/edges.py` + register — POST (201)/PATCH/DELETE, Request-based body parsing AFTER the ownership 404 (entities.py precedent), strict wire counter, responses built from held data (no post-commit re-reads).
- [x] Backend tests: `test_edges_api.py` (add/edit/delete, ordering, coercions, no-resurrect race), `test_store.py` (delete_edge + undo + explicit-id strictness), `test_candidate_lifecycle.py` + `test_generate_api.py` (edited edges / invalid / orphan, wire-level).
- [x] `frontend/src/api/schema.ts` regen; `frontend/src/stores/world.ts` edge actions (generated EdgeCreate/EdgeUpdate types).
- [x] `frontend/src/views/WorldView.vue` — inline relation add/edit/delete on entity cards.
- [x] `frontend/src/views/CandidatesView.vue` — staged-edge editing before accept; accept sends updated edges; `resync()` refetches the world snapshot (picker freshness).
- [x] Frontend vitest for both surfaces (75 green).

**Acceptance Criteria:**
- Given a character's screen, when the DM adds a relation, then it is a typed edge from the closed directional vocabulary to any existing entity, committed through the store in one revision (FR9, AR3).
- Given an existing relation, when the DM edits its counter or deletes it, then the change commits through the store, per-type counters change only via commits, and zero dangling edges remain (AR8, AD-23).

## Spec Change Log

- 2026-09-04 (review round 1 corrections): matrix cells aligned with the shipped envelope — DuplicateEdge 422→409, stale-base 422→409 (spec-2-5 DELETE_STALE precedent), dangling endpoint 404→422 (spec-2-2 contract), STALE_COMMIT "carrying latest revision id" dropped (wire detail is prose); EDGE_ADD pinned 201. Implementation hardening from the 3-layer review: ownership-404 now precedes body parsing; strict int counters on the wire; explicit-id edges never create (UnknownEdgeError); PATCH/POST responses built from held data.

## Design Notes
- Edge delete needs no confirm (always dangling-safe) — deliberately unlike `delete_entity`; document this in the store docstring to keep the distinction crisp.
- Add/update reuse `commit_subgraph` with no new store code; only delete is net-new store surface.
- Do NOT intercept the 2.3 counter-semantic deferral: the DM enters a plain int counter; store shape-validates. Range/semantics stay out of 3.4.
- WorldView is the beta "character's screen" for committed entities — no new entity-detail route this story (the dogfood surface is the card).
- The accept override's `edges` relaxation is the 3.3 frozen contract's explicit "relation editing is story 3.4" hand-off; the accept stays one atomic transaction and an invalid override's edge set fails the whole accept (row stays proposed).

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — all green incl. new edges API/store and candidate-edited-edge tests.
- `make lint && make typecheck` — clean.
- `cd frontend && npm test -- --run` — vitest green incl. WorldView + CandidatesView relation-editing tests.

**Manual checks (if no CLI):**
- With backend up: create/world → WorldView card → add relation to an existing entity → appears after refetch → edit counter → delete → gone; WS frames update neighbors. Ask → accept screen → edit a staged edge (add/edit/delete) → accept → world view shows the DM's edge set; a bad endpoint on an edited edge fails accept with the row staying proposed.

## Suggested Review Order

**Store — the single writer's edge surface**

- `delete_edge` mirroring `_delete_entity`; event shape matches undo's `_EDGE_KEYS`.
  [`commit.py`](../../backend/app/store/commit.py)

- Undo `_inverse_edge_deleted` consumes `edge_deleted` already — no change.
  [`undo.py:249`](../../backend/app/store/undo.py#L249)

**Candidate accept — the editable edge override**

- Override edges validated (endpoint→committed world, vocab) instead of verbatim; all-or-nothing.
  [`candidates.py:301`](../../backend/app/store/candidates.py#L301)

**API**

- POST/PATCH/DELETE with owner-404-first and 4xx-never-state-change ordering.
  [`edges.py`](../../backend/app/api/edges.py)

**Frontend**

- WorldView inline add/edit/delete + refetch; CandidatesView staged-edge editing + updated accept payload.
  [`WorldView.vue`](../../frontend/src/views/WorldView.vue), [`CandidatesView.vue`](../../frontend/src/views/CandidatesView.vue)