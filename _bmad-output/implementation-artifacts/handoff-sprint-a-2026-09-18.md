# Handoff — Sprint A (2026-09-18): step-0 gates, engine hardening, forge UI, media bundle

Session window: 2026-09-18. Six commits on `main` (`771f42b`, `7fbdd23`,
`aa45d84`, `65fe0e8`, `4053ea2`, `390e0fb`), api hub service `mythos-api`
restarted on the latest. Full verification: **1394 backend pytest / ruff /
mypy (61 files) / ruff format --check clean; 243 frontend vitest / eslint
/ vue-tsc clean**. Nothing open in the working tree.

## 0. Read this first

- Repo conventions + dev-stack facts: unchanged from prior handoffs.
- The live fasiha regenerate candidate from the 09-18 handoff was ALREADY
  accepted at 06:02 (job `01M2SHQP…`, candidate `01M2SHR8…`) — the
  committed block is clean (no emoji, Void Collapse 8d8/36, routine named
  by prose). Gate 1 needed zero action.

## 1. Step 0 — owner gates (all closed)

1. **fasiha block**: verified clean (see above).
2. **Verdicts (owner: approve all)**: `spec-hybrid-authorship.md` status
   `draft → done`; sprint-status.yaml gains the 2026-09-15..18 rows
   (hybrid-authorship-two-paths, character-forge-ui-round-2,
   generic-character-library, structured-statblock-editor,
   statblock-power-accounting-saga) as done; `last_updated` 09-18.
3. **Counter-bounds ruling (owner: enforce per-semantic, reject invalid)**:
   `EDGE_COUNTER_RANGES` — amount (debt) 0..1_000_000, score
   (grudge/loyalty) 1..10, intensity (ally_of/enemy_of/controls/protects)
   1..10, neutral shape-only. Enforced at store commit
   (`InvalidEdgeCounterError` names the bound), generate stage
   (`_valid_edge` drops), build-in wave edges (`_resolve_edges` +
   declared relations raise named `JobPayloadError`,
   `_edge_row_usable` pre-filter drops). Closes the 2.3 + 3.1 deferrals.
   NOTE: the pre-existing `test_edge_counter_valid_rows_stored_verbatim`
   pinned the OLD shape-not-range policy (grudge `2**63-1` stored) — it
   was updated to the ruling, not suppressed (handoff 09-18 §3 warning).

## 2. Sprint A shipped

- **Model-output regression corpus** (`backend/tests/fixtures/model_outputs/`
  + `test_model_output_corpus.py`, 6 tests): four VERBATIM gemma-4-26B
  stat blocks captured from `data/llm-journal/` — fasiha original (wave1)
  and accepted regenerate, fatima (wave1), ashen-pilgrim (wave1) — each
  carrying the raw count-vs-dice contradiction. The tests pin the raw
  fixture is STILL contradictory, then assert the gate heals it (8d8/8/36
  etc.) and the fasiha level-20 NPC audits on-target (25,84 → dpr 40.5).
  **The corpus caught a NEW bug the same day it was built**: the
  prose-damage fold (`_fold_prose_damage`) typecast every rebuilt part to
  the first part's type, so ashen-pilgrim's "7 (2d6) necrotic" committed
  as bludgeoning. Fixed (7fbdd23): each prose idiom now matches its part
  by DICE identity (the b99105f rule) and keeps the part's type — the
  fold's documented contract, finally honored. 468 adjacent stat-block
  tests green.
- **Add-Character form UI** (the hybrid-authorship follow-up): route
  `/campaigns/:id/add-character` → AddCharacterView (batch sheets) +
  AuthorSheetForm (dropdowns, prose only in narrative slots, boss gated
  on role, StatBlockEditor reused with additive `hideIdentity`) +
  RelationTargetPicker (tier-1 searchable committed-entity dropdown,
  tier-2 staged-sibling keys, tier-3 name resolution with "Did you mean
  [Name] (#ULID)?" converting to a tier-1 binding — a `target_name` NEVER
  reaches the wire) + `lib/characterValidation.ts` client-side mirror
  with backend schema-path wording; 24 validation + 6 view pins.
- **Shared AR24 profile module** (epic-3 retro item 2):
  `components/profile/profile.ts` — WorldView (137 lines less) and
  CandidatesView deduped onto it; byte-identical rendering preserved
  (both label spellings pinned). Known cosmetic divergence: `boss` is
  deliberately absent from the shared FIELD_LABELS base (CandidatesView
  renders it raw); WorldView patches it locally — unify when the
  candidates re-roll badge allows.
- **portraitFor unify** (epic-4 retro item 13): frontend portraitFor now
  picks newest AVAILABLE by rowid, mirroring backend
  export_sheets._hero_portrait_src (4 new pins).
- **KEEP-5 retention** (epic-4 retro item 13 ruling): store
  `prune_entity_media` seam (5 newest rows per entity by rowid),
  prune+file-reclaim inside the runner write tail (rows-in-txn AD-10,
  failure logged never fatal). Pins: store seam, 6th-portrait runner
  test, export newest-available.
- **comfyui provider scaffold** (epic-4 retro item 11):
  `providers/comfyui_common.py` (endpoint config, workflow build, JSON
  error mapping, submit/poll/fetch deadline core) — image + video
  providers now build on it; format-specific atoms stayed put.
- **media write-tail extraction** (epic-4 retro item 12):
  `_persist_media_output` — temp+rename file-before-row, add_media,
  cancel-race window, shared by run_portrait/run_video.
- **Undo-of-entity-creation media reclaim** (spec-4.3 deferral closed):
  `_inverse_entity_deleted` reclaims manifest rows in the store txn;
  the undo API route reclaims files post-commit (rows-first, best-effort
  never fails the 204). The prior-state undo test pinned the OLD
  deferred behavior and was updated to the ruling (media never restored;
  regeneration is the recovery).
- **Binary/attachment OpenAPI typings** (2.7 + 5.1 deferrals):
  get_file declares video/mp4 + image/png; export routes declare
  text/markdown + text/html (+ text/xml, application/zip on the entity
  export); schema.ts regenerated. gen:api gotcha recorded below.
- Frontend full gates: vue-tsc + eslint clean (the agents skip
  project-wide checks BY CONTRACT — expect to fix their type/lint
  fallout at integration; this session: 16 typecheck errors + 11 lint
  problems, all in the new UI files).

## 3. Owner-owned / open

- **`spec-retrieval-cap-diagnostic-2026-09-18-draft.md`** (new): generate
  retrieval-cap truncation surfacing + ask-target seeding — the one
  blocked item; needs your review (decision: name-match boost vs rowid
  bias for seeding beyond the 24-row AR6 cap).
- Epic 5 close: **5-5 world→VTT kill-criterion demo** (10-min world→
  Owlbear + <1 min single-character export + second human) — next epic
  gate after sprint A.
- **Epic 6 (beta launch gate)** is entirely backlog (6-1 nightly snapshot,
  6-2 restore verify-then-apply, 6-3 clean campaign deletion, 6-4 privacy,
  6-5 launch gate).
- Open retro items: epic-3 #4 retrieval-cap (now the draft spec above);
  epic-4 #11/#12/#13 are CLOSED by this sprint.

## 4. Behavior notes / gotchas

- Counter bounds now apply to the LIVE wire: PATCH/POST an edge with
  grudge 0 or debt -1 → 422 validation_error naming the range. Existing
  committed data is untouched (only new commits validated).
- `world_integration` and `Additional data` still edit as raw JSON
  (unchanged, owner hasn't asked).
- gen:api: an inline `Model.model_json_schema()` under explicit
  `responses` content produces unresolved `$ref`s and openapi-typescript
  FAILS (`Can't resolve $ref at …/media/items`) — keep response_model and
  declare ONLY the non-JSON media types in `responses`.
- KEEP-5 prune runs on the WRITE path: writing a 6th portrait for an
  entity reclaims the oldest row+file. Undo of an entity-created revision
  now deletes its media rows+files (the old rows-suvive behavior was a
  pinned deferral; updated).
- LLM journal (`data/llm-journal/`, 25 files) is the raw-output corpus
  source — future slivers: capture BEFORE fixing, die-hard rule.

## 5. Verification

```
uv run --directory backend pytest -q            # 1394 passed
cd backend && uv run ruff check app tests && uv run ruff format --check app tests && uv run mypy app
cd frontend && npx vitest run && npm run lint && npm run typecheck   # 243 passed
# corpus quick-check
uv run --directory backend pytest tests/test_model_output_corpus.py -q
```
Restart the api after backend changes: `hub restart mythos-api`.