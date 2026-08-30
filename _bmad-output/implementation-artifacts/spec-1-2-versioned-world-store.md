---
title: 'Versioned world store: atomic subgraph commits and per-transaction undo'
type: 'feature'
created: '2026-08-26'
status: 'done'
review_loop_iteration: 0
baseline_commit: '896ff8d073bc942f62d06e540d991519d0f406cf'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/specs/spec-mythosCircle/conventions.md'
---

## Intent

<!-- What is broken or missing, and why it matters. Then the high-level approach — the "what", not the "how". -->

**Problem:** The world store is an empty seam (`backend/app/store/__init__.py`). Nothing persists world state, nothing versions it, and nothing protects the DM's edits from partial writes or silent overwrites — the substrate every later epic (build-in, generation, exports) commits through.

**Approach:** Implement the store layer per AD-1/AD-2/AD-13: an append-only event log plus a materialized latest revision per campaign (SQLite, WAL, SQLAlchemy 2). One transactional commit path takes a staged subgraph (entities + typed directed edges with counters), validates it against the base revision, and produces exactly one new revision, all-or-nothing. Undo is one compensating commit that reverts a revision's state deltas. A commit whose base revision is stale conflicts (rebase-or-reject, never silent overwrite).

## Boundaries & Constraints

<!-- Three tiers: Always = invariant rules. Ask First = human-gated decisions. Never = out of scope + forbidden approaches. -->

**Always:**
- Exactly one new revision per commit; a partial failure rolls back the whole subgraph (one database transaction) (AR3, AD-1).
- Undo restores the pre-commit materialized state; the entity's ULID stays stable; inbound edges and media-manifest rows survive (AR4).
- Zero dangling edges after every commit: edge endpoints must exist in the current revision or in the same staged subgraph (AD-5, AD-23).
- The store is the sole writer of world state — no raw SQL or direct table writes outside `backend/app/store/` (AD-13).
- Edge types come from the closed Phase-1 vocabulary (relationship, debt, grudge, loyalty, member_of, located_in, rival_of, kin_of, ally_of); edges carry a per-type counter (AD-5, AD-23).
- IDs are ULIDs; timestamps UTC ISO-8601 (conventions.md).

**Ask First:** adding an edge type beyond the Phase-1 vocabulary; changing the revision/event table shapes once other stories read them.

**Never:**
- HTTP routes or a public API in this story (campaign CRUD is 1.6; job endpoints are 1.3).
- Entity deletion/cascade, media writes, candidate/staging tables, queue, auth, or retrieval — later stories.
- Deleting or rewriting event-log rows; undo never rewrites history (event-sourced).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| COMMIT_NEW_SUBGRAPH | campaign at latest revision; subgraph = new entities + edges between them and existing ones | exactly one new revision; materialized state reflects the subgraph; one event per change, each tagged with the new revision id | N/A |
| COMMIT_REGENERATION | subgraph re-uses an existing entity ULID with new content | entity content replaced in place; inbound edges and media refs untouched; one revision | N/A |
| COMMIT_ROLLBACK | subgraph whose edge endpoint does not exist (dangling) | whole subgraph rejected, zero new revision, state unchanged | structured dangling-edge error naming the offending edge/endpoints |
| UNDO_LATEST | a committed latest revision | state equals the previous revision; entity ULIDs stable; inbound edges and media rows survive; new undo revision + events appended | error if revision is not the latest |
| COMMIT_STALE_BASE | subgraph staged against an older revision than latest | commit rejected, latest revision id surfaced | conflict error (409 shape) — rebase-or-reject, never silent overwrite |

## Code Map

<!-- Agent-populated during planning. Annotated paths prevent blind codebase searching. -->

- `backend/app/store/__init__.py` -- the empty seam this story fills; keeps its sole-writer docstring.
- `backend/app/core/ids.py` -- `new_id()` ULID factory; reuse for campaigns, entities, edges, revisions, events, media rows.
- `backend/app/core/errors.py` -- 409 `conflict` envelope code + `_envelope()` exist; the store raises plain exceptions, FastAPI mapping is a later story's concern.
- `backend/app/main.py` -- app factory; DB initialization hook lands here (engine/session setup in the store package, wired in `create_app`).
- `backend/tests/` -- `test_api.py` sets the pytest fixture style; store tests go in `backend/tests/test_store.py`.
- `backend/pyproject.toml` -- add `sqlalchemy` (2.x); `ulid` already present.

## Tasks & Acceptance

<!-- Tasks: backtick-quoted file path -- action -- rationale. Prefer one task per file; group tightly-coupled changes when splitting would be artificial. -->
<!-- If an I/O Matrix is present, include a task to unit-test its edge cases. -->

- [x] `backend/pyproject.toml` -- add `sqlalchemy>=2` -- spine stack (AD-13). Already present from story 1.1 (`sqlalchemy>=2.0,<3`); no change needed.
- [x] `backend/app/store/` -- models + commit path: campaign, revision, entity, edge, event, media tables (WAL, single writer); `commit_subgraph(campaign_id, entities, edges, base_revision)` with dangling-edge validation and one-transaction all-or-nothing apply; `undo(campaign_id, revision_id)` as a compensating commit; latest-revision read helpers -- AD-1/AD-2/AD-13.
- [x] `backend/tests/test_store.py` -- deterministic-fixture unit tests covering every I/O matrix row: one-revision-per-commit, rollback of a failing subgraph, ULID-stable regeneration, dangling-edge rejection, undo restores prior state with edges + media rows intact, stale-base conflict -- conventions.md testing row.

**Acceptance Criteria:**
- Given a campaign graph, when a subgraph is committed, then exactly one new revision exists and the commit is all-or-nothing (AR3, AD-1).
- Given a committed latest transaction, when the DM undoes it, then the previous revision's state is restored with the entity ULID stable and inbound edges and media references surviving (AR4).
- Given a pending commit staged on a stale base, when it lands after a concurrent commit, then the store rejects it with a conflict naming the latest revision (AD-2).
- Given any commit, when world state is written, then only the store layer writes it and zero dangling edges remain (AD-13, AD-23).

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries.
     Each entry records: what finding triggered the change, what was amended, what known-bad state
     the amendment avoids, and any KEEP instructions (what worked well and must survive re-derivation).
     Empty until the first bad_spec loopback. -->

## Design Notes

<!-- If the approach is straightforward, DELETE THIS ENTIRE SECTION. Do not write "N/A" or "None". -->
<!-- Design rationale and golden examples only when non-obvious. Keep examples to 5–10 lines. -->

Event-sourced graph-of-record: the append-only `events` table is the truth; `entities`/`edges`
rows are a materialized view of the latest revision, so reads are simple and history never
mutates. Each event carries the `revision_id` that owns it (spine: "the revision owns its events").

Undo is not a pointer flip: it is one compensating commit — replaying the inverse of the
revision's deltas — which appends new events and produces its own revision. The graph state
matches the previous revision, but the log still answers "what happened in order".

Conflict rule: `commit_subgraph` validates `base_revision` against the campaign's latest before
applying. Stale base → conflict error carrying the latest revision id. The pipeline (1.3/2.x)
decides rebase-or-reject from that error; the store itself never merges.

Edge vocabulary: the store implements the spine's (AD-5) closed Phase-1 set —
relationship, debt, grudge, loyalty, member_of, located_in, rival_of, kin_of,
ally_of, **enemy_of**. The Always list above restates the vocabulary without
`enemy_of`; the architecture spine is the source of truth, so `enemy_of` is
included and validated like any other type.

## Verification

<!-- If no build, test, or lint commands apply, DELETE THIS ENTIRE SECTION. Do not write "N/A" or "None". -->
<!-- How the agent confirms its own work. Prefer CLI commands. When no CLI check applies, state what to inspect manually. -->

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all store tests pass (deterministic, no live LLM).
- `make lint && make typecheck` -- expected: ruff, mypy, eslint, prettier, vue-tsc clean.

## Suggested Review Order

1. `../../backend/app/store/models.py` -- schema (campaign, revision, entity, edge, event, media) + frozen input dataclasses. Everything else builds on these shapes.
2. `../../backend/app/store/db.py` -- engine, WAL listener, `session_scope` (the single-writer seam, AR3 all-or-nothing).
3. `../../backend/app/store/commit.py` -- the commit path: base-revision check, edge vocabulary, dangling-edge validation, per-change events. The heart of the story.
4. `../../backend/app/store/undo.py` -- compensating-commit undo incl. the redo branches (undoing an undo).
5. `../../backend/app/store/read.py` -- latest-revision ordering (SQLite `rowid`), revision chain, per-revision events, materialized state.
6. `../../backend/app/store/__init__.py` -- public re-export surface (sole writer of world state, AD-13).
7. `../../backend/app/main.py` -- `init_app_db()` wiring in the app factory.
8. `../../backend/tests/test_store.py` -- the verification: one test per I/O matrix row plus the acceptance criteria.
9. `../../backend/tests/conftest.py` -- scratch-DB env pin so the app's store init never writes in-tree.

### Review Findings

Review of the full 0e45156..HEAD diff (Story 1.1 scaffold + Story 1.2 store) — run 2026-08-30, four parallel layers (blind-hunter, edge-case-hunter, verification-gap, acceptance-auditor). All layers completed.

**decision-needed**

- [x] [Review][Decision] DB path contract: `DEFAULT_DB_URL` (`backend/app/store/db.py:23`, CWD-relative `data/mythosCircle.db`), `deploy/config.toml:15` (`/var/lib/mythoscircle/mythoscircle.db`, unconsumed — nothing reads config.toml), `backup.sh:9`/`restore.sh:12` (`/var/lib/mythoscircle/mythoscircle.db`), and `mythoscircle.service` (EnvironmentFile commented secrets-only, so no `MYTHOSCIRCLE_DB` is set) disagree. Default deploy: app writes `/opt/mythoscircle/backend/data/mythosCircle.db` while nightly backup looks at `/var/lib/mythoscircle/mythoscircle.db` → backup silently skips every night; restore cannot target the live DB. Fix belongs to Story 1.7 wiring, but nothing verifies the composition today.

**patch**

- [x] [Review][Patch] Commit path does not actually take the write lock before the base-revision check — `isolation_level="SERIALIZABLE"` maps to `PRAGMA read_uncommitted=0` with a no-op `do_begin` (verified SQLAlchemy 2.0.52 dialect source + live probe: `in_transaction` is False after SELECT, opens only at first INSERT). Two commits on the same base can both pass `_check_base` in autocommit, serialize at flush, and both land with the same `base_revision` — the AD-2/AC3 silent-overwrite guarantee is not implemented; the misleading comment at `db.py:49-58` must be replaced. Fix: explicitly `session.execute(text("BEGIN IMMEDIATE"))` as the first statement in `_commit`/`_undo` before any validation read; fix the comment; strengthen `test_concurrent_commits_same_base_exactly_one_wins` (current `sleep(0.2)` only exercises the sequential-stale case); add `PRAGMA busy_timeout`.
- [x] [Review][Patch] Explicit entity/edge ULIDs are never shape-validated at the store boundary — `EntityInput(id=...)`/`EdgeInput(id=...)` accept any string and persist it; "IDs are ULIDs" (conventions.md Always tier) is enforced only for store-minted ids. Add a ULID-shape check in `_check_entity_ids`/`_check_edge_ids` (26 chars, Crockford) raising a structured error.
- [x] [Review][Patch] Undo corruption guards raise `StaleRevisionError(revision_id)` passing the not-yet-committed undo revision's own id as `latest_revision_id` — misleads downstream rebase logic (`undo.py:160,196` and the update variants). Should raise `CorruptEventError` (module already has it); payload must never name a revision that does not exist.
- [x] [Review][Patch] Undo validates event payloads lazily, mid-mutation (`_undo` creates+adds the undo revision, then `_require_keys` runs inside the replay loop). Validate every event of the target revision before any `session.add`/row mutation — all-or-nothing structurally, matching commit's validate-then-mutate discipline.
- [x] [Review][Patch] Duplicate-relationship check skipped for explicit-id new edges: `DuplicateEdgeError` is only raised in the `edge.id is None` branch; an explicit fresh id duplicating an existing (src,dst,type) hits the raw unique-constraint `IntegrityError` at flush (`commit.py:255-265`).
- [x] [Review][Patch] Untested branches with real behavior: entity-side `_check_entity_ids` rejection (only edge analog tested at `test_store.py:582`); `_check_base` `base_revision=None`-on-non-empty-world; undo on zero-revision campaign; `handle_http_error` 5xx HTTPException branch; exact envelope code strings (only 404 pinned; 400/451 untested); undo of a mixed create+update commit.
- [x] [Review][Patch] Per-event timestamps are inconsistent with the owning revision: `_add_event` calls `time.now()` per event while revision/rows share one `now` (`commit.py:317-334`) — event `created_at` can exceed `revision.created_at` within one commit. Pass the shared `now` through.
- [x] [Review][Patch] No composite index for the primary access pattern: `Event` has separate `campaign_id`/`revision_id` indexes; `revision_events()`/`undo` filter both columns (SQLite uses one). Add `Index('ix_event_campaign_revision', campaign_id, revision_id)`.
- [x] [Review][Patch] `world_state()` returns entities/edges in unspecified order while sibling read helpers document rowid ordering (AD-16 deterministic retrieval) — add `order_by(rowid)`.

**defer**

- [x] [Review][Defer] Undo rejects unknown event types with `CorruptEventError` — forward-compat break when later stories (AD-7 session events, media manifest, queue) append new event types to the shared log; by-design now (reject, never guess), must be extended with each new type [backend/app/store/undo.py:113-117] — deferred, pre-existing
- [x] [Review][Defer] No log-vs-materialized audit/verification surface — no helper replays events or detects drift between `entity`/`edge` rows and the event log; `CorruptEventError` reachable only serendipitously via undo — deferred to ops/later story [backend/app/store/]
- [x] [Review][Defer] Read helpers cannot distinguish "unknown campaign" from "empty world" — `world_state`/`latest_revision`/`revision_chain` return empty for nonexistent campaigns while commit/undo raise `UnknownCampaignError`; Story 1.6 API mapping must duplicate the existence check — deferred, pre-existing (1.6 concern) [backend/app/store/read.py]
- [x] [Review][Defer] Edge counters have no semantic/range validation — AD-23 per-type meaning (debt=amount, grudge/loyalty=score) has no type-to-schema map; ranges are the pipeline's concern once counters are assigned — deferred [backend/app/store/models.py:104-115]
- [x] [Review][Defer] `backup.sh` has no single-instance guard — manual run overlapping the cron run races on `mkdir`/retention sweep; flock + retention that ignores in-progress destinations is a 1.7 ops item — deferred [deploy/backup.sh]
- [x] [Review][Defer] Frontend has no wire-contract foundation (no OpenAPI client generator, no `src/api/`, no envelope/cursor types) — Story 1.6 will build the contract layer; out of scope for 1.1/1.2 — deferred [frontend/src/]
