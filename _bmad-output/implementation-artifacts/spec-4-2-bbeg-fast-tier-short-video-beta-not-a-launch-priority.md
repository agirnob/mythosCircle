---
title: '4-2 BBEG Fast-Tier Short Video (Beta, Not a Launch Priority)'
type: 'feature'
created: '2026-09-06'
status: 'done'
review_loop_iteration: 0
context: []
baseline_commit: d4220a86404795101c561a33f5ea8d7daf49954c
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Boss-tier entities (role BBEG/Monster) carry a committed boss section but no reveal moment — the DM has a portrait and stat block, nothing cinematic. `kind='video'` is legal in the queue (`JobCreate`, `JOB_KINDS`, `ck_job_kind`) but the worker rejects it, the manifest accepts video rows, and nothing generates one.

**Approach:** A `video` generation job on the existing FIFO queue — the 4-1 portrait pipeline mirrored: OpenAI-compatible video adapter, `VideoSettings` + `[video]` config (owner-decided server/model, documented placeholders until confirmed), a `run_video` runner whose prompt is a projection of the committed AR24 record (appearance + boss + identity, never free text), atomic `.mp4` write, manifest row via the store, and a kind-aware file serve. The DM triggers it from a "Generate reveal video" button on boss-tier entity cards; the clip renders inline in a `<video>` block. Fast tier only — premium/OpenRouter video is out of scope (FR13, beta-not-launch-priority).

## Boundaries & Constraints

**Always:**
- Mirror the 4-1 media discipline exactly: prompt built ONLY from the committed entity's AR24 record at run time; `MediaCallBudget` on `job.max_media_calls` (AR21); file write (temp + rename) BEFORE the manifest row via `add_media` (AD-1 — store is the sole `media` writer); no world-state changes, no revision/event (media is not graph).
- Enqueue gate and run-time check run the SAME prompt builder (`bbeg_video_prompt`), so they can never disagree — the 4-1 `appearance_prompt` pattern.
- Every media read stays ownership-404-first (AD-9); the file route keeps row + disk checks (ROW_WITHOUT_FILE → 404).
- One job = one video call; provider failure / budget / missing entity / non-mp4 data fail the job cleanly — no file, no row left behind.
- Cancel-race poll (`_job_still_running`) before the provider call; terminal-write race keeps file+row (4-1 semantics).

**Ask First:**
- Dev video server + model choice (AD-6/NFR8: owner decision at build). Needed to set `[video]` defaults and the end-to-end smoke target. Until confirmed, defaults are documented placeholders and tests inject a mock provider — the 4-1 image pattern.

**Never:**
- No premium/OpenRouter video path (FR13); no auto-enqueue on accept (video is expensive — DM triggers it); no video for non-boss-tier entities; no free-text video prompts; no duration/quality knobs in the wire body (model+prompt only).
- No reclaim-on-delete or export media validation (story 4.3).
- No changes to `portraitFor`/portrait rendering — the existing guard (video row never displaces the portrait `<img>`) is already tested and must survive.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | Boss-tier entity with appearance + boss section | `.mp4` at `media/{campaign}/{entity}/{ulid}.mp4`, manifest row `kind='video'`, card `<video>` renders | N/A |
| NOT_BOSS | Entity role NPC (forced enqueue) | No job | Enqueue 422, envelope code |
| NO_VIDEO_PROMPT | Appearance or boss section blank/missing | No job | Enqueue 422 / run-fail stable message |
| ENTITY_MISSING | Job claims a deleted entity | Fail cleanly, no file/row | `fail_job` stable message |
| PROVIDER_FAIL | Video endpoint down / HTTP error | Job failed, user-facing message, re-trigger via button | `fail_job`; never auto-retried |
| BUDGET_EXCEEDED | Media calls hit `max_media_calls` | Job failed, no partial file/row | `BudgetExceededError` path |
| NON_MP4 | Provider returns non-ISO-BMFF bytes | Fail before write | `JobPayloadError`, no file/row |
| FOREIGN_CAMPAIGN | Media GET for another DM's campaign | 404 | store `UnknownCampaignError` |
| ROLE_FLIP | Role edited away from BBEG/Monster after a video exists | Video block hides; manifest row persists | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/worker.py:156` -- the `if job.kind != "text"` reject naming "story 4.2" is the single dispatch to replace; add a `video` branch (lazy import `run_video`, mirror the `image` branch's injectable-provider pattern: `video_provider`/`video_settings` params on `run_next_job`/`_run_job`, production defaults `video_generation`/`video_settings()` resolved at dispatch).
- `backend/app/media/service.py` -- `run_portrait` (line 96) is the runner template: payload contract, run-time entity re-read via `session_scope`, budget guard, cancel-race poll, atomic temp+rename, `add_media`-on-success-with-cleanup, `complete_job({'entity_id', 'filename'})`. Reuse `_job_still_running` verbatim; reuse `appearance_prompt` output as part of the video prompt. `PNG_SIGNATURE` guard → mp4 twin: `data[4:8] == b"ftyp"` (ISO-BMFF box magic) checked before write.
- `backend/app/providers/image.py` -- adapter shape to mirror for `providers/video.py`: `Callable[..., bytes]`, httpx client, `ProviderError` family from `providers.llm`, injectable `transport`, `settings=` kwarg, `data[0].b64_json` extraction. Video path: `videos/generations` under the base URL (relative-path join, same `images/generations` reasoning).
- `backend/app/core/settings.py:109` -- `ImageSettings` dataclass + `image_settings()` (env > config > default); add `VideoSettings` + `video_settings()` + `VIDEO_*` env constants (`MYTHOSCIRCLE_VIDEO_ENDPOINT/MODEL/TIMEOUT`, `MYTHOSCIRCLE_VIDEO_API_KEY` key-only-env per AD-22). `configured_media_dir()` reused unchanged.
- `backend/app/core/config.py:73` -- `DEFAULT_IMAGE_*` placeholder block + `RuntimeConfig.image_*` fields; add `DEFAULT_VIDEO_ENDPOINT = "http://127.0.0.1:8082/v1"`, `DEFAULT_VIDEO_MODEL`, `DEFAULT_VIDEO_TIMEOUT`, `video_endpoint/video_model/video_timeout` fields, `[video]` parse in `load_config`. `deploy/config.toml:35` gets the mirrored `[video]` section with placeholder comments.
- `backend/app/store/jobs.py:403` -- `_enqueue` dispatches per-kind validators; add `elif kind == "video": _validate_video_payload(...)`. `:559` `_validate_image_payload` is the template: payload exactly `{"entity_id": <ULID>}`, entity-in-campaign 404, then the prompt-existence gate 422. Video adds the role gate: `entity.data["role"] in ("BBEG", "Monster")`. Function-local import of the prompt builder (circularity precedent).
- `backend/app/store/media.py` -- `add_media(campaign_id, entity_id, filename, kind)` accepts `kind='video'` as-is (ULID-stem + non-blank kind only); `get_media_file` row lookup unchanged. Zero store changes expected — verify with tests only.
- `backend/app/api/media.py:93` -- `get_file` hardcodes `media_type="image/png"`; make it kind-aware: `video/mp4` for a `kind='video'` row, `image/png` otherwise (row is already fetched — use it). `MediaResponse.kind` already on the wire; `schema.ts` regenerates it (`npm run gen:api` against a current api on :8000).
- `frontend/src/stores/jobs.ts:137` -- `submitPortrait` + `portraitInFlight` pattern: add `submitRevealVideo` (kind `'video'`) + `videoInFlight` getter.
- `frontend/src/stores/world.ts:121` -- `portraitFor` filters `kind === 'image'` (do not touch); add `videoFor(campaignId, entityId)` — newest `kind === 'video'` row. `handleJobMessage:282` re-fetches media on terminal `image` frames; extend the branch to `image`/`video`.
- `frontend/src/views/WorldView.vue:736` -- portrait block template: add a sibling video block on boss-tier cards (`BOSS_ROLES` set already at `:450`): `<video controls>` from `videoFor`, "Generate reveal video" button gated on role + in-flight, status/failure lines mirroring the portrait block's rules (failure renders over an existing clip; queue/running only while none exists).
- `backend/tests/test_media_service.py`, `test_image_provider.py`, `test_media_api.py` -- fixture/mock-transport conventions to mirror in video variants; `frontend/src/views/WorldView.test.ts:1180` already pins the video-row-does-not-displace-portrait guard.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/providers/video.py` -- new OpenAI-compatible video adapter (`videos/generations`, mp4 bytes out, ProviderError family, injectable transport, `VideoSettings` kwarg).
- [x] `backend/app/core/config.py; backend/app/core/settings.py; deploy/config.toml` -- `VideoSettings` + `[video]` section + `MYTHOSCIRCLE_VIDEO_*` env keys (placeholder defaults, ask-first documented).
- [x] `backend/app/media/service.py` -- `bbeg_video_prompt(data)` (appearance join + boss section + name/role line; None when insufficient) and `run_video(job, provider, settings, media_dir)` mirroring `run_portrait` with the mp4 signature guard.
- [x] `backend/app/store/jobs.py` -- `_validate_video_payload` (payload/ULID/entity/role/prompt gates) + `_enqueue` dispatch branch.
- [x] `backend/app/pipeline/worker.py` -- replace the video reject branch with the lazy `run_video` dispatch + injectables.
- [x] `backend/app/api/media.py` -- kind-aware `media_type` on `get_file`.
- [x] `frontend/src/stores/jobs.ts; frontend/src/stores/world.ts` -- `submitRevealVideo`, `videoInFlight`, `videoFor`, terminal video frame → `fetchMedia`.
- [x] `frontend/src/views/WorldView.vue` -- boss-tier video block: `<video>` render, gated button, status/failure lines.
- [x] `backend/tests/` + `frontend/` -- matrix-row tests: adapter (mock transport), runner (prompt build, mp4 guard, file+row ordering, budget, missing entity, provider fail), enqueue gates (NOT_BOSS/NO_VIDEO_PROMPT 422), worker dispatch, API (video mime type, ownership 404, ROW_WITHOUT_FILE), frontend pins (button gating, `<video>` render, WS re-fetch, portrait non-displacement).

**Acceptance Criteria:**
- Given a committed boss-tier entity with appearance + boss section, when the DM clicks "Generate reveal video", then a `video` job enters the FIFO with visible position and the resulting `.mp4` renders inline in the entity card's `<video>` block with no manual refresh.
- Given a non-boss entity or one lacking a usable video prompt, then the enqueue is rejected 422 (button mirrors the gate) and no job row exists.
- Given a failed video job (provider, budget, missing entity, non-mp4), then the job ends `failed` with a user-facing message, no file and no manifest row, and the DM can re-trigger.
- Given a video manifest row, then the file GET serves `video/mp4` inline behind the session cookie, and a foreign campaign gets the indistinguishable 404.

## Design Notes

Prompt shape (deterministic, projection-only): `bbeg_video_prompt` joins `appearance_prompt(...)` output, the boss section's non-blank values (`lair_actions`/`legendary_actions`/`immunities`/`vulnerabilities` joined `key: value`), and an identity line (`name — role`). The reveal framing ("slow cinematic BBEG reveal") is a pipeline constant appended to the prompt — never user-supplied free text, same standing as the stat-block rules text. The enqueue validator and the runner share this one builder.

mp4 over webm: the fast-tier local server is expected to emit ISO-BMFF; the `ftyp` check is the write-boundary guard mirroring `PNG_SIGNATURE` — a provider 200 wrapping an HTML error page must fail the job, never land mislabeled bytes under a `.mp4` name.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. new video tests
- `make lint && make typecheck` -- expected: ruff + mypy clean
- `cd frontend && npm run test` -- expected: frontend suite green
- Manual dogfood (scratch DB, dev api per session pattern): on a boss-tier entity card, generate the reveal video against a mock/live endpoint → queue position visible → on `job_done` the `<video>` renders; NPC entity shows no video button.

## Suggested Review Order

**The runner — one job, one clip, no dangling anything**

- Entry point: the 4-1 portrait discipline mirrored end-to-end, with the run-time role gate added in review.
  [`service.py:251`](../../backend/app/media/service.py#L251)
- The projection-only prompt: appearance + boss + identity + a pipeline-constant reveal framing — never free text.
  [`service.py:113`](../../backend/app/media/service.py#L113)
- The mp4 twin of `PNG_SIGNATURE`: an HTML error page must fail the job, never land mislabeled bytes.
  [`service.py:74`](../../backend/app/media/service.py#L74)

**Queue integration**

- The "story 4.2" reject branch becomes the lazy dispatch, injectables mirroring the image path.
  [`worker.py:193`](../../backend/app/pipeline/worker.py#L193)
- Enqueue gate: payload/ULID/entity 404, NOT_BOSS 422, shared-builder prompt 422 — gates can never disagree.
  [`jobs.py:596`](../../backend/app/store/jobs.py#L596)

**Provider + config (ask-first placeholders)**

- OpenAI-compatible `videos/generations`, same error/transport shape as the image adapter.
  [`video.py:40`](../../backend/app/providers/video.py#L40)
- `VideoSettings` + env keys; api_key stays environment-only (AD-22).
  [`settings.py:155`](../../backend/app/core/settings.py#L155)
- Documented placeholder defaults — inert until the owner resolves the dev video server.
  [`config.py:89`](../../backend/app/core/config.py#L89)
- The mirrored `[video]` section.
  [`config.toml:46`](../../deploy/config.toml#L46)

**Serve + frontend**

- Kind-aware content type: a video row serves `video/mp4`, everything else keeps the 4-1 behavior.
  [`media.py:126`](../../backend/app/api/media.py#L126)
- Separate `videoFor` projection — a video row can never displace the portrait `<img>` (pinned test).
  [`world.ts:132`](../../frontend/src/stores/world.ts#L132)
- The DM-facing trigger: gated button + in-flight discipline, mirroring `submitPortrait`.
  [`jobs.ts:170`](../../frontend/src/stores/jobs.ts#L170)
- The card block: `<video>` render, failure-over-clip, queue/running status.
  [`WorldView.vue:909`](../../frontend/src/views/WorldView.vue#L909)

**Tests (matrix rows + review additions)**

- Runner invariants incl. the review-round demotion-gate test and the dotfile-safe tmp-litter assertion.
  [`test_video_service.py:405`](../../backend/tests/test_video_service.py#L405)
- Adapter contract under a mock transport.
  [`test_video_provider.py:1`](../../backend/tests/test_video_provider.py#L1)
- Enqueue gates + api mime/ownership pins.
  [`test_jobs.py:1`](../../backend/tests/test_jobs.py#L1), [`test_media_api.py:1`](../../backend/tests/test_media_api.py#L1)
- Frontend pins: enabled-state + click-through POST body (review round), WS re-fetch, portrait non-displacement.
  [`WorldView.test.ts:1288`](../../frontend/src/views/WorldView.test.ts#L1288), [`world.test.ts:408`](../../frontend/src/stores/world.test.ts#L408)
