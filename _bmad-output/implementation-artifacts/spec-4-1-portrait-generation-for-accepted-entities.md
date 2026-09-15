---
title: '4-1 Portrait Generation for Accepted Entities'
type: 'feature'
created: '2026-09-06'
status: 'done'
review_loop_iteration: 0
context: []
baseline_commit: 30c5a0b81c292b139a677fd0b83ddb93a3c83fd3
---

> **Owner renegotiation (2026-09-15):** the portrait is DM-triggered only.
> Accepting a candidate no longer auto-enqueues an image job — the
> WorldView's Generate portrait button (with its per-entity options) is
> the single trigger. `frontend/src/views/CandidatesView.vue` lost the
> `enqueuePortraitAfterAccept` path and its five tests (commit after
> eb9a27f). Everything else in this spec stands.

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Accepted entities have painter-grade AR24 appearance text but no face — the world has no image until the DM leaves the tool. The media manifest table, `image` job kind, and `media_dir` config exist as inert scaffolding; nothing writes media.

**Approach:** Portrait generation is an `image` generation job on the existing FIFO queue. The committed AR24 `appearance` section (face, body, clothing, scars, marks) is the prompt source — never free text. A media service writes the image file under `media/{campaign_id}/{entity_id}/` and records it in the DB manifest, which the entity's screen renders.

## Boundaries & Constraints

**Always:**
- Manifest rows are written only through a store function (AD-1: the store is the only writer of the `media` table); the media service never writes the DB directly.
- Prompt source is the committed entity's AR24 `appearance` section only; `face`/`body`/`clothing`/`scars`/`marks` keys are used when present, unknown keys tolerated (AR24 forward compatibility). A portrait is a projection of the committed character, not a free-text image prompt (FR12).
- Generation runs asynchronously through the FIFO queue: `kind='image'`, exactly one job at a time (AD-3), `max_media_calls` per-job budget (AR21), progress via `report_progress`, never blocks the DM (NFR9).
- File write lands before the manifest row (a row never dangles); filename is a fresh ULID; files live at `media_dir/{campaign_id}/{entity_id}/`.
- Every media read is ownership-checked (AD-9): a foreign campaign is 404, never 403/200.
- No world-state changes: no entity column, no revision/event for media rows (AD-1 governs the graph; the manifest row is the index of record).

**Ask First:**
- Dev image server + model choice (AD-6/NFR8: owner decision at build). Needed to set `[image]` config defaults and the end-to-end smoke target. Until confirmed, defaults are documented placeholders and tests use an injected mock provider.

**Never:**
- No video (story 4.2), no reclaim-on-delete/export media validation (story 4.3), no premium/OpenRouter image path.
- No free-text image prompts; no image upload; no manual editing of media rows.
- No changes to the export projection (`WorldExport` stays media-free; 4.3 owns export validation).
- No candidate-level media: staged candidates never generate portraits (only committed entities).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | Accepted entity with AR24 `appearance` | Portrait file at `media/{campaign}/{entity}/{ulid}.png`, manifest row written, entity card shows the image | N/A |
| NO_APPEARANCE | Entity data lacks non-blank `appearance` | No portrait job enqueued; card shows "No portrait" + generate button still present | Enqueue 422 with envelope code when forced |
| ENTITY_MISSING | Image job claims an entity that no longer exists | Job fails cleanly: no file, no row | `fail_job` with stable message |
| PROVIDER_FAIL | Image endpoint down / HTTP error | Job failed with user-facing message; DM re-triggers via button | `fail_job`; job never retried automatically |
| BUDGET_EXCEEDED | Media calls hit `max_media_calls` | Job failed, no partial file/row | `BudgetExceededError` path |
| FOREIGN_CAMPAIGN | Media GET for another DM's campaign | 404 | store `UnknownCampaignError` |
| ROW_WITHOUT_FILE | Manifest row exists, file removed on disk | 404 on file GET | `MediaNotFoundError` |

</frozen-after-approval>

## Code Map

- `backend/app/store/models.py` -- `Media` table (id, campaign_id, entity_id, filename, kind, created_at) exists, no writer yet (~line 240).
- `backend/app/store/jobs.py` -- `JOB_KINDS` includes `'image'`/`'video'`; per-kind `_validate_*_payload` pattern (`_validate_generate_payload` etc.); `enqueue_job`; `Job.max_media_calls` column already exists.
- `backend/app/pipeline/worker.py` -- `_run_job` dispatch (`build_in`/`generate`/`regenerate` lazy imports); `image`/`video` currently raise `JobPayloadError("the media service lands in Epic 4")` ~line 109 — add `image` dispatch, keep `video` failing.
- `backend/app/pipeline/budget.py` -- `CallBudget(job)` hardcodes `job.max_llm_calls`; media runner needs the `max_media_calls` budget variant.
- `backend/app/providers/llm.py` -- the OpenAI-compatible adapter pattern to mirror for `providers/image.py`: httpx, `ProviderError(kind, *, status_code)`, injectable `transport`, `settings=` kwarg.
- `backend/app/core/settings.py` + `config.py` + `deploy/config.toml` -- `LLMSettings` pattern; add `ImageSettings` + `[image]` section (endpoint/model/timeout, `MYTHOSCIRCLE_IMAGE_*` env); `config.toml` already has `[world] media_dir = "/var/lib/mythoscircle/media"` — plumb `RuntimeConfig.media_dir` + `MYTHOSCIRCLE_MEDIA_DIR` override.
- `backend/app/store/read.py` -- `world_entities` (rowid order) — the runner reads the committed entity + its `data.appearance` at run time.
- `backend/app/api/jobs.py` -- `JobCreate.kind` already unions `'image'`; wire the image payload validator; `store_error_as_http` in `api/common.py` maps store errors.
- `backend/app/api/candidates.py` -- accept route (`accept_candidate`) — accepts commit the entity only; it never enqueues media (2026-09-15 owner decision).
- `backend/app/main.py` -- router include list; no static media mount — media served via authenticated route (session cookie path is `/api`, same-origin `<img>` works).
- `backend/app/core/ids.py` -- `new_id()` for filenames + media row ids.
- `frontend/src/views/WorldView.vue` -- entity card ~line 619-845; `LORE_FIELDS` includes `appearance` (~line 333); add portrait `<img>` + "Generate portrait" button + job status.
- `frontend/src/views/CandidatesView.vue` -- accept handler — the portrait is NOT auto-enqueued (2026-09-15 owner decision); the WorldView Generate portrait button is the single trigger.
- `frontend/src/stores/jobs.ts` -- `submitBuildIn`/`submitRegenerate` pattern; add `submitPortrait(campaignId, entityId)`; WS dispatch (`handleWsMessage`) already re-syncs on `job_done` — extend the sync to re-fetch media on image jobs.
- `frontend/src/api/schema.ts` -- regenerate via `npm run gen:api` (only against a current api on :8000); `JobCreate.kind` already includes `'image'`; new `MediaResponse` types arrive.
- `frontend/src/stores/world.ts` -- `fetchSnapshot` pulls `/export` JSON; media fetched separately (new media list endpoint), not woven into `WorldExport`.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/providers/image.py` -- new OpenAI-compatible image adapter (images/generations, httpx, ProviderError family, injectable transport, ImageSettings kwarg).
- [x] `backend/app/core/config.py; backend/app/core/settings.py; deploy/config.toml` -- `ImageSettings` dataclass + `[image]` section + env keys; `RuntimeConfig.media_dir` + env override.
- [x] `backend/app/store/media.py` -- store-owned media writes/reads: `add_media` (campaign+entity exist, ULID file name), `list_media`, `get_media`, `MediaNotFoundError`; re-export via `store/__init__.py`.
- [x] `backend/app/media/service.py` -- `run_portrait(job, provider, settings)`: re-read committed entity (missing → fail), build prompt from appearance (dict-known-keys join | verbatim string | blank → fail), budget-guarded image call, atomic file write (temp+rename), `add_media` row, `complete_job` with `{entity_id, filename}`.
- [x] `frontend/src/stores/jobs.ts` -- `submitPortrait` (+ portrait-in-flight discipline mirroring `regenerateInFlight`); WS sync re-fetches media on image `job_done`.
- [x] `backend/app/api/media.py` -- `GET /api/campaigns/{id}/media` (manifest list) + `GET /api/campaigns/{id}/media/{entity_id}/{filename}` (FileResponse, ownership + row + file checks → 404); `main.py` include router.
- [x] `frontend/src/views/WorldView.vue` -- portrait `<img>`, generate button (gated on non-blank appearance), job status line; media list fetch.
- [x] `frontend/src/views/CandidatesView.vue` -- auto-enqueue portrait after successful accept when payload has appearance (REMOVED 2026-09-15 owner decision — DM-triggered only).
- [x] `backend/tests/` -- store media write/read invariants; service runner (prompt build, file+row ordering, budget, missing entity, provider failure, atomic write); adapter httpx mock; API routes (ownership 404, missing file 404, happy 200); worker image dispatch; budget variant.
- [x] `frontend` tests -- jobs store `submitPortrait` + WorldView portrait rendering/status; keep conventions of existing `WorldView.test.ts`.

**Acceptance Criteria:**
- Given an accepted entity with an AR24 appearance section, when the DM triggers portrait generation via the entity card's Generate portrait button, then the image lands
- Given a completed portrait job, then the manifest row renders the image on the entity's card with no manual refresh, and the job's queue position was visible while pending (AR12, AD-3).
- Given an entity without an appearance section, then no portrait is enqueued and the card states "No portrait".
- Given a failed image job (provider error, budget, missing entity), then the job ends `failed` with a user-facing message and the DM can re-trigger via the button.

## Design Notes

Appearance normalization: committed AR24 `appearance` is either a dict (`face`/`body`/`clothing`/`scars`/`marks`, unknown keys tolerated) or a plain string. The prompt builder joins known dict keys (`face: …\nbody: …`) or uses the string verbatim — blank/whitespace-only in either shape is the enqueue 422 / run-fail condition. Already-present job scaffolding means zero migration: `image` is a legal `JobCreate` kind today and the worker's "media service lands in Epic 4" branch is the single dispatch to replace.

Golden flow: DM clicks Generate portrait on the entity card → `POST /api/jobs {kind:'image', payload:{entity_id}}` → `_validate_image_payload` → FIFO → worker claims → run_portrait reads entity (`world_entities`), builds prompt, budget-guarded `images/generations` call, writes `{media_dir}/{campaign}/{entity}/{ulid}.png` via temp+rename, `add_media` row, `complete_job({entity_id, filename})` → WS `job_done` → WorldView re-fetches media → `<img src="/api/campaigns/{id}/media/{entity_id}/{filename}">` renders (same-origin session cookie covers auth).

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. new media tests
- `make lint && make typecheck` -- expected: ruff + mypy clean
- `cd frontend && npm run test` -- expected: frontend suite green (existing test runner conventions)
- Manual dogfood (scratch DB, dev api per session pattern): accept a candidate in a world with `appearance` → job appears with queue position → on `job_done` (mock or live image server) the portrait renders on the entity card; entity without appearance shows "No portrait".
## Suggested Review Order

**Portrait runner**

- The media write path in one function: AR24-prompt build, atomic temp+rename write, PNG-signature guard, file-before-row, cleanup on every failure
  [`service.py:90`](../../backend/app/media/service.py#L90)
- Prompt source contract - dict known-key join | verbatim string | blank -> fail
  [`service.py:67`](../../backend/app/media/service.py#L67)

**Queue integration**

- FIFO `image` kind goes live; lazy-import dispatch replaces the "Epic 4" reject branch
  [`worker.py:156`](../../backend/app/pipeline/worker.py#L156)
- Media-call budget twin of CallBudget, keyed on `job.max_media_calls` (first consumer of the previously dead config)
  [`budget.py:42`](../../backend/app/pipeline/budget.py#L42)
- OpenAI-compatible images/generations adapter with the LLM provider's error/transport/settings shape
  [`image.py:34`](../../backend/app/providers/image.py#L34)
- Enqueue gate: entity exists + non-blank appearance, 422/404 with zero rows
  [`jobs.py:559`](../../backend/app/store/jobs.py#L559)

**Manifest store**

- Store-owned manifest writes (AD-1) - campaign/entity existence, ULID files, rowid ordering
  [`media.py:42`](../../backend/app/store/media.py#L42)
- Row-keyed file read - the traversal guard and ROW_WITHOUT_FILE semantics
  [`media.py:100`](../../backend/app/store/media.py#L100)

**Media API**

- Authenticated file serve: ownership-404 first, row + disk checks, inline disposition so the card `<img>` renders
  [`media.py:87`](../../backend/app/api/media.py#L87)
- Campaign-scoped manifest list
  [`media.py:69`](../../backend/app/api/media.py#L69)

**Config**

- `[image]` section + env keys + `media_dir` plumbing (env > config > default)
  [`config.py:77`](../../backend/app/core/config.py#L77)
- Resolver the service and API share so writes and serves agree on the root
  [`settings.py:140`](../../backend/app/core/settings.py#L140)

**Appearance gate**

- One shared frontend gate - kills the triplicated key list between two views and the backend
  [`appearance.ts:27`](../../frontend/src/lib/appearance.ts#L27)

**Frontend stores**

- Portrait-row selection: newest `kind === 'image'` only - a 4.2 video row can never displace the img
  [`world.ts:121`](../../frontend/src/stores/world.ts#L121)
- WS terminal image frame -> media re-fetch - the no-manual-refresh mechanism
  [`world.ts:282`](../../frontend/src/stores/world.ts#L282)
- Failed media fetch -> 'Portrait list unavailable' hint, not a false 'No portrait.'
  [`world.ts:73`](../../frontend/src/stores/world.ts#L73)
- `submitPortrait` + `portraitInFlight` - the duplicate-generation discipline both trigger paths share
  [`jobs.ts:137`](../../frontend/src/stores/jobs.ts#L137)

**Frontend views**

- Card portrait block: image/status/failure/error states, generate button gated on appearance + in-flight
  [`WorldView.vue:741`](../../frontend/src/views/WorldView.vue#L741)
- Failed re-generation renders over an existing portrait (criterion 4); stale errors cleared on terminal/media change
  [`WorldView.vue:789`](../../frontend/src/views/WorldView.vue#L789)
- DM-triggered portrait (2026-09-15 owner decision — the accept auto-enqueue was removed): appearance gate + in-flight guard on the WorldView button
  [`WorldView.vue:741`](../../frontend/src/views/WorldView.vue#L741)

**Tests & supporting**

- Store invariants, runner failures, provider adapter, config budgets - the matrix rows
  [`test_media_store.py:1`](../../backend/tests/test_media_store.py#L1)
  [`test_media_service.py:1`](../../backend/tests/test_media_service.py#L1)
  [`test_image_provider.py:1`](../../backend/tests/test_image_provider.py#L1)
- API contract: auth/ownership/traversal/file-serve pins
  [`test_media_api.py:1`](../../backend/tests/test_media_api.py#L1)
- Frontend pins: WS media re-fetch, portrait render/status (accept auto-enqueue removed 2026-09-15)
  [`world.test.ts:1`](../../frontend/src/stores/world.test.ts#L1)
  [`WorldView.test.ts:1`](../../frontend/src/views/WorldView.test.ts#L1)
  [`CandidatesView.test.ts:1`](../../frontend/src/views/CandidatesView.test.ts#L1)
