---
title: '4-3 Reclaim-on-Delete and Export Media Validation'
type: 'feature'
created: '2026-09-07'
status: 'done' # draft | ready-for-dev | in-progress | in-review | done
review_loop_iteration: 0
baseline_commit: 652067b34e81aa217f0164b9dcfcabbb4a0ac629
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Media is never reclaimed — deleting an entity or campaign leaves files and manifest rows on disk/DB, so dead entities keep media (FR14/AR12/AD-10 require reclaim + export reference validation).

**Approach:** Store deletes media rows inside the existing entity-delete and campaign-delete transactions; the API layer removes the files via media-service reclaim helpers. JSON/Markdown exports embed per-entity manifest references with an `available` flag resolved against disk, so broken references are flagged, never shipped silently.

## Boundaries & Constraints

**Always:**
- The store is the ONLY writer of `media` rows (AD-1/AD-13): rows are deleted inside `_delete_entity` and `delete_campaign`, never by the media service or API.
- File reclaim (AD-10) runs AFTER the store transaction commits; rows-first ordering means a crash between commit and reclaim leaves files but no dangling reference. A failed file delete never re-enters the DB.
- Media rows are NOT world graph: no events on reclaim; undo does NOT restore them (`store/undo.py:18` "reclamation happens with the media stories"). Restoration = regeneration.
- File deletion is best-effort: an `OSError` logs a warning and never turns a successful 204 into an error.
- Export validation is a pure read: no store writes, no revision/event; disk existence checked only for manifest rows — a missing file => `available: false`, never dropped (FR14).
- Media lists stay in manifest `rowid` (insertion) order.

**Ask First:**
- None (all boundary choices are pinned by AD-10's "reclaimed when their entity is deleted" + FR14's "flagged"; pre-4.3 orphan rows on the operator's dev DB are NOT swept by this story).

**Never:**
- No pruning/retention of a living entity's older clips (keep-latest/N, DM history) — stays a deferred 4.2 note; 4.3 reclaims only on deletion and validates references.
- No sweeps of pre-4.3 already-orphaned rows/files in existing DBs; the guarantee is rows deleted with the entity.
- No new queue/media API routes, no frontend changes, no OpenAPI regeneration (no export frontend consumer — 2.6 deferral).
- No `media` schema change, no FK on `Media.entity_id`, no migration.
- No file deletion while the store transaction could still fail — reclaim is strictly post-commit.
- Campaign reclaim removes `media_dir/{campaign_id}` only — never walks above it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ENTITY_DELETE_MEDIA | entity with `image`+`video` rows+files, DELETE cascade+confirm | 204; rows gone (store reads 404), entity media dir removed | N/A |
| ENTITY_DELETE_NO_MEDIA | no rows | 204; no disk/DB change | N/A |
| ENTITY_DELETE_ROWFILE_GONE | row, file missing | 204; row removed; missing dir is a no-op | N/A |
| ENTITY_DELETE_UNDO | delete then undo | entity+edges restored; `list_media` empty; DM regenerates | N/A |
| CAMPAIGN_DELETE_MEDIA | campaign with media rows+files, DELETE confirm | 204; media rows deleted (txn), `media_dir/{campaign}` dir removed | N/A |
| EXPORT_VALID_REF | rows+files present | JSON `EntityExport.media` all `available: true`; markdown lists them unflagged | N/A |
| EXPORT_BROKEN_REF | row, file missing | `available: false`, still listed; markdown `(broken: file missing on disk)` | N/A |
| EXPORT_NO_MEDIA | no rows | empty array; no markdown Media block | N/A |
| EXPORT_ORPHAN_LEGACY | pre-4.3 orphan row (entity gone) | not shipped (belongs to no listed entity); row persists in DB — outside this story | N/A |

## Code Map

- `backend/app/store/media.py` -- store owner of media rows: `add_media` (no change), campaign-wide `list_media` (rowid order, unchanged), and new `delete_entity_media(session, campaign_id, entity_id)` — the per-entity row-deletion seam used by `_delete_entity` (its docstring names the other row-removal sites: `delete_campaign`'s bulk sweep and undo-of-creation's deferred leave-behind).
- `backend/app/store/commit.py:519` `_delete_entity` -- inside the existing transaction, right before `session.delete(entity)`: `delete_entity_media(session, campaign_id, entity_id)` plus a docstring note; no events, no revision delta.
- `backend/app/store/campaigns.py:164` `delete_campaign` -- already deletes `Media` rows (line 187) — untouched; the API layer adds file reclamation.
- `backend/app/media/service.py` -- new `reclaim_entity_media(media_dir, campaign_id, entity_id)` and `reclaim_campaign_media(media_dir, campaign_id)`: `shutil.rmtree` + a `logger = logging.getLogger(__name__)` warning on OSError; fully idempotent (missing target = no-op).
- `backend/app/api/entities.py:101` DELETE route -- after `store_delete_entity` returns (204), call `reclaim_entity_media(configured_media_dir(), ...)` wrapped in a route-level `try/except Exception` + `logger.exception`: even a helper regression can never turn a committed delete into a 500 (pinned by route-level tests).
- `backend/app/api/campaigns.py:190` DELETE route -- after `delete_campaign` returns True: `reclaim_campaign_media(configured_media_dir(), campaign_id)` under the same guard.
- `backend/app/api/exports.py` -- `EntityExport` gains `media: list[MediaRefExport]` where `MediaRefExport = {id, kind, filename, available}`; media rows are read INSIDE the snapshot session (plain rowid-ordered SELECT on the open session — snapshot-consistent, no nested BEGIN IMMEDIATE) and grouped by entity_id in Python; `_render_markdown` gains a `### Media` block per entity listing `kind: filename` + the broken marker. `available` = `(media_dir/campaign/entity/filename).is_file()` while inside the snapshot session.
- `backend/tests/test_store.py`,`test_media_store.py`,`test_entities_api.py`,`test_campaigns_api.py`,`test_export_api.py` -- matrix-row tests (see Acceptance).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/store/media.py` -- `list_media(..., entity_id=None)` filter + `delete_entity_media(session, campaign_id, entity_id)`.
- [x] `backend/app/store/commit.py` -- hook row deletion into `_delete_entity` txn.
- [x] `backend/app/media/service.py` -- `reclaim_entity_media` / `reclaim_campaign_media`.
- [x] `backend/app/api/entities.py` -- call `reclaim_entity_media` after store delete.
- [x] `backend/app/api/campaigns.py` -- call `reclaim_campaign_media` after store delete.
- [x] `backend/app/api/exports.py` -- `MediaRefExport`, group media into `EntityExport`, markdown Media block + broken flag.
- [x] `backend/tests/{test_store,test_media_store,test_entities_api,test_campaigns_api,test_export_api}.py` -- matrix-row tests.

**Acceptance Criteria:**
- Given a committed entity with rows+files of both kinds, when the DM deletes it (204), then `list_media`/`get_media_file` report none, the entity's media dir is gone from disk, and undoing the delete restores the entity but not its media (regeneration is the recovery).
- Given a campaign with media rows+files, when the DM deletes it (204), then rows are gone from the DB and `{media_dir}/{campaign}` no longer exists.
- Given an export, when a referenced file is missing on disk, then the JSON lists the row with `available: false` and the markdown marks it `(broken: file missing on disk)`; nothing is dropped (FR14).
- Given an unchanged world+media store, repeated exports remain byte-identical (including the new media field).

## Spec Change Log

<!-- Append-only; filled by step-04 review loops. -->
- **2026-09-08 review round 1** -- Findings triaged from blind-hunter/edge-case-hunter/verification-gap; no loopback. Amended (non-frozen): AC typo FR4 -> FR14; Code Map updated to the shipped shape (media read moved INSIDE the export snapshot session — plain SELECT on the open session, snapshot-consistent, killing the added-mid-request skew; `list_media`'s entity_id filter deleted as test-only surface; route-level reclaim guard). Known-bad state avoided: a second `session_scope`/BEGIN IMMEDIATE inside the snapshot session (deadlock) and a stale docstring claiming a single deletion seam. KEEP: rows-first ordering inside the delete transactions; `_media_refs` grouping in Python off one campaign-wide read; the route-level 204 guard; `_rmtree_best_effort` as the single best-effort delete.

## Design Notes

- Row-then-file: a crash between commit and file-reclaim leaves garbage files but no dangling reference; the reverse leaves rows pointing at missing files (ROW_WITHOUT_FILE).
- Undo stops at revisions — the deliverable is "media reclaimed when the entity is deleted"; DM regenerates after undo (documented, not a gap).
- 2-6's "repeated exports are byte-identical" now holds given a fixed world and media store; changing a file changes `available` — that is the flag's purpose.
- Crash-window residue (rows committed, process dies before file reclaim) is unidentifiable garbage by design — no sweep can name it; manual cleanup or a future reconciliation story owns it.
- `frontend/src/api/schema.ts` is now stale for the export response (`EntityExport.media` added; regeneration deliberately out of scope — the existing 2.6 deferral owns it).
- `rowid` ordering across entities and within each entity's list keeps exports deterministic.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. new reclaim/export tests (mock-based; no live image/video server).
- `make lint && make typecheck` -- expected: ruff + mypy clean.

**Manual check:**
- Against a scratch DB: generate one portrait (live provider, ~75s/image — use the config `[image]` default timeout, comfortably above), delete the entity, verify rows gone + `media/{campaign}/{entity}` removed; export a world with a hand-removed portrait file, verify `available:false` in JSON and the broken marker in markdown.

## Suggested Review Order

**Store: rows-first reclamation**

- The design's core: manifest rows leave inside the delete transaction, one line before the entity row.
  [`commit.py:631`](../../backend/app/store/commit.py#L631)
- The single per-entity row-deletion seam; its docstring names the other row-removal sites honestly.
  [`media.py:106`](../../backend/app/store/media.py#L106)
- Settled undo semantics: undo never restores media; regeneration is the recovery.
  [`undo.py:18`](../../backend/app/store/undo.py#L18)

**File reclaim (media service owns it)**

- The one best-effort rmtree both helpers share: missing target = no-op, OSError logged, never raised.
  [`service.py:383`](../../backend/app/media/service.py#L383)
- Entity reclaim: `{media_dir}/{campaign}/{entity}` gone after the delete commits.
  [`service.py:397`](../../backend/app/media/service.py#L397)
- Campaign reclaim: touches only `{media_dir}/{campaign}` — never walks above it.
  [`service.py:412`](../../backend/app/media/service.py#L412)

**API wiring: the 204 stands**

- Route-level guard: even a helper regression can never 500 after the store committed.
  [`entities.py:104`](../../backend/app/api/entities.py#L104)
- Same discipline on campaign delete.
  [`campaigns.py:194`](../../backend/app/api/campaigns.py#L194)

**Export: references ride, broken ones are flagged**

- Media read INSIDE the snapshot session — plain rowid SELECT, no nested BEGIN IMMEDIATE, snapshot-consistent.
  [`exports.py:294`](../../backend/app/api/exports.py#L294)
- Grouping + disk resolution: `available` per row, broken never dropped (FR14).
  [`exports.py:246`](../../backend/app/api/exports.py#L246)
- The wire shape: `{id, kind, filename, available}` per manifest row.
  [`exports.py:64`](../../backend/app/api/exports.py#L64)
- Markdown `### Media` block with the broken marker.
  [`exports.py:216`](../../backend/app/api/exports.py#L216)

**Tests: matrix rows + the 204 pins**

- Store-level: rows gone in-txn, both kinds, neighbor survives, no media events.
  [`test_store.py:1654`](../../backend/tests/test_store.py#L1654)
- Undo restores entity but not media.
  [`test_store.py:1682`](../../backend/tests/test_store.py#L1682)
- API row: 204, rows gone, dir removed, neighbor untouched.
  [`test_entities_api.py:358`](../../backend/tests/test_entities_api.py#L358)
- Route pins: a raising helper still yields 204.
  [`test_entities_api.py:437`](../../backend/tests/test_entities_api.py#L437)
  [`test_campaigns_api.py:247`](../../backend/tests/test_campaigns_api.py#L247)
- Campaign delete reclaims rows + whole media dir.
  [`test_campaigns_api.py:191`](../../backend/tests/test_campaigns_api.py#L191)
- Export flags: valid/broken/no-media/orphan + determinism.
  [`test_export_api.py:498`](../../backend/tests/test_export_api.py#L498)
- Helper discipline: idempotency, OSError swallow, scope.
  [`test_media_service.py:455`](../../backend/tests/test_media_service.py#L455)