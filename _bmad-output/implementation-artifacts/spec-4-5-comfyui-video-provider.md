---
title: '4-5 ComfyUI Video Provider (Local Dev Backend)'
type: 'feature'
created: '2026-09-08'
status: 'done' # draft | ready-for-dev | in-progress | in-review | done
review_loop_iteration: 0
baseline_commit: f4bfb1a3f49f2c58f36a4bfa42cc9fbf29b9073c
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The BBEG reveal video (spec-4.2) ships an OpenAI-compatible `videos/generations` adapter, but the only working local video backend is ComfyUI running the MiniMax H3 image-to-video workflow (`video_minimax_h3_i2v_sage.json`) — an i2v workflow that animates a source image, not a text-to-video server. 4-2's OpenAI adapter can't reach it, so no real reveal video can render locally.

**Approach:** A sibling `comfyui_video_generation` provider mirroring the 4-4 ComfyUI image adapter's discipline (leaf, `ProviderError` family, injectable transport, kwargs-only `settings`): loads the operator's workflow JSON, injects the reveal prompt into the MiniMax prompt node and the entity's portrait as the i2v `first_frame`, submits, polls `/history` until the SaveVideo output is ready, fetches mp4 bytes from `/view`. A new `[video] backend = "openai" | "comfyui"` switch (mirroring 4-4's `[image] backend`) routes the worker call. The OpenAI path stays default; ComfyUI is opt-in. Both workflow JSONs (Krea2 image + MiniMax video) move into the repo at `deploy/workflows/` — they are operator-host-specific (node ids / model weights) but belong in the project so they can't be lost from `~/Desktop`.

## Boundaries & Constraints

**Always:**
- Mirror the 4-4 provider discipline exactly: leaf module, `Callable[..., bytes]`, `ProviderError` family from `app.providers.llm`, injectable `transport` kwarg, kwargs-only `settings`. No new error categories; the worker / `run_video` treat every provider alike.
- The provider returns **mp4 bytes** (ComfyUI SaveVideo output is ISO-BMFF). The mp4-signature check sits at BOTH the provider return and the file write — matrix row NON_MP4_BYTES is the provider-side twin, rejecting garbage before `run_video` ever sees it (review round 1); "single" refers to the WRITE boundary only: `run_video`'s existing `MP4_SIGNATURE` guard remains the one authoritative check at the file write, never weakened, never moved.
- Workflow JSON loaded by path from config (`[comfyui_video] workflow_path`); reloaded from disk on EVERY call (a stale workflow must never be served — 4-4 rule). Workflows live in the repo at `deploy/workflows/` (Krea2 image + MiniMax video); the shipped config's `workflow_path` is RELATIVE to the config file's directory (`workflows/…json` — machine-independent across checkouts, review round 1) and resolves to an absolute path at config load; env/defaults are absolute.
- The i2v `first_frame` is the entity's newest portrait (kind=image manifest row). Under `video_backend="comfyui"` ONLY, `run_video` resolves it and passes the path to the provider as a `first_frame` kwarg; the OpenAI video provider (4-2) gains an ignored optional `first_frame: str | None = None` so both backends share one call shape. A boss-tier entity with no portrait fails the video job (you can't i2v without a source frame). REVIEW-ROUND-1 SCOPE FIX: the portrait resolution AND the no-portrait fail are comfyui-SCOPED — the openai path is text-to-video and runs exactly as spec-4.2 (no portrait resolution, `first_frame=None`; acceptance "the 4-2 path runs unchanged").
- The provider stages the portrait into ComfyUI's input directory (a configured `input_dir`) before submit and cleans it up after — LoadImage reads from ComfyUI's input dir, not the media dir.
- Settings read follows `env > config > default` like every adapter; api_key (if any) is environment-only (AD-22). One provider call = one generation; `timeout` bounds the ENTIRE call; `poll_interval` is a hardcoded code default.
- Cancel-race poll (`_job_still_running`) stays in `run_video`; the provider has no job knowledge.

**Ask First:**
- None (the workflow file is now in-repo at `deploy/workflows/`; the 4-4 "document path only" stance is superseded by the repo-home decision).

**Never:**
- No frontend changes; no queue changes; no store changes; no api/media changes. The video job kind / payload / manifest / serve behavior is spec-4.2 — frozen.
- No workflow JSON outside `deploy/workflows/` (the repo is the single home; `~/Desktop` copies are removed).
- No automatic backend selection — `video_backend` is a static config field; no fallback chain.
- No OpenAI-shape adapter on the ComfyUI side; no ComfyUI-shape adapter on the OpenAI side. One provider function per backend.
- No per-job runtime knobs (length, resolution, aspect) — fixed per workflow; the workflow's own nodes own them.
- No state caching across calls. Each call reloads the workflow JSON and re-stages the first_frame.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH_OPENAI | `video_backend="openai"` (default) | `video_generation` runs as today (spec-4.2) | N/A |
| HAPPY_PATH_COMFYUI | `video_backend="comfyui"`, prompt + portrait, workflow_path set, server live | Submit → poll `/history` until SaveVideo output → fetch `/view` → mp4 bytes | N/A |
| NO_FIRST_FRAME | boss entity with no portrait | `run_video` fails the job (no source frame for i2v), no file/row | JobPayloadError |
| SUBMIT_HTTP_ERROR | ComfyUI `/prompt` 4xx/5xx | `ProviderError("http", status_code)` | Worker fails job |
| SUBMIT_CONN_ERROR | ComfyUI unreachable | `ProviderError("connection")` | Worker fails job |
| POLL_TIMEOUT | `/history` never produces output before `timeout` | `ProviderError("timeout")` | Worker fails job |
| MALFORMED_SUBMIT | `/prompt` 200, non-JSON / no prompt_id | `ProviderError("http", 200)` | Worker fails job |
| NO_VIDEO_OUTPUT | `/history` 200, no SaveVideo output | `ProviderError("http", 200)` | Worker fails job |
| VIEW_FETCH_FAIL | `/view` non-200 | `ProviderError("http", status_code)` | Worker fails job |
| NON_MP4_BYTES | `/view` returns non-mp4 bytes | `ProviderError("http", 200)` (provider-side, before write) | Worker fails job |
| WORKFLOW_PATH_MISSING | configured path doesn't exist | `ProviderError("connection")` at first call | Worker fails job; operator misconfig |
| INVALID_WORKFLOW_JSON | path exists, JSON malformed / missing prompt node | `ProviderError("connection")` | Worker fails job |
| FIRST_FRAME_STAGE_FAIL | portrait can't be copied into ComfyUI input dir | `ProviderError("connection")` | Worker fails job |
| CANCEL_MID_RENDER | job cancelled while the MiniMax render is running server-side | accepted behavior (review round 1): the render keeps running on ComfyUI up to the whole-call timeout with no abort; propose-to-fetch completes file+row, then `complete_job` raises `JobStateConflictError` (the runner keeps file+row — real content, no dangling row; the cancelled job never reports success) | N/A — no ComfyUI prompt abort in this story |

## Code Map

- `backend/app/providers/comfyui.py` -- the 4-4 image adapter (submit/poll/fetch, `ProviderError`, `transport=`, `settings=`). New sibling `providers/comfyui_video.py` mirrors it but: injects into `inputs.prompt` (MiniMax node, NOT `inputs.value`), stages the first_frame into ComfyUI's input dir + sets the LoadImage node's `inputs.image`, polls for a **SaveVideo** output, returns mp4 bytes. Image file untouched.
- `backend/app/core/settings.py` -- `ComfyUIVideoSettings` (endpoint, workflow_path, prompt_node_id, first_frame_node_id, input_dir, timeout, api_key) + `comfyui_video_settings()` + `configured_video_backend()` ("openai"|"comfyui") mirroring the 4-4 image pair. `config.py` + `deploy/config.toml` -- `[comfyui_video]` + `[video] backend` + `MYTHOSCIRCLE_COMFYUI_VIDEO_*` env. `deploy/workflows/` -- the two workflow JSONs (Krea2 image + MiniMax video) now live here; config/env reference the repo-relative absolute path.
- `backend/app/media/service.py:251` `run_video` -- under `[video] backend = "comfyui"` ONLY (review round 1): resolve the entity's newest portrait (kind=image), pass `first_frame=<path>` to the provider, fail when none; the openai path skips resolution and passes `first_frame=None` (4-2 unchanged). `backend/app/providers/video.py` `video_generation` -- gain ignored `first_frame: str | None = None`.
- `backend/app/pipeline/worker.py:227` video dispatch -- mirror the image branch: comfyui backend routes to `comfyui_video_generation` + `comfyui_video_settings`.
- `backend/tests/test_comfyui_video_provider.py`, `test_video_service.py`, `test_worker.py`, `test_config.py` -- matrix rows (mock transport, workflow injection, first_frame staging, mp4 guard, worker dispatch, config precedence). Also changed (review round 1): `test_media_api.py` (the video media-API rows) and `test_deploy_contract.py` (the config/workflow repo-home + shipped-workflow-shape pins).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/providers/comfyui_video.py` -- new ComfyUI video adapter (submit/poll/fetch, mp4 out, ProviderError family, transport injectable, workflow reload, first_frame staging).
- [x] `backend/app/core/config.py; backend/app/core/settings.py; deploy/config.toml` -- `ComfyUIVideoSettings` + `[comfyui_video]` + `[video] backend` + `configured_video_backend()` + env keys.
- [x] `backend/app/media/service.py` -- `run_video` resolves the entity's portrait and passes `first_frame`; fail-no-portrait gate (comfyui-scoped, review round 1).
- [x] `backend/app/providers/video.py` -- `video_generation` accepts ignored `first_frame` kwarg.
- [x] `backend/app/pipeline/worker.py` -- video dispatch: comfyui backend branch.
- [x] `backend/tests/` -- matrix-row tests (adapter under mock transport, first_frame staging, mp4 guard, no-portrait fail, worker dispatch, config precedence). Also changed: `test_media_api.py` (the video media-API rows) and `test_deploy_contract.py` (the config/workflow repo-home + shipped-workflow-shape pins) — noted per review round 1.
- [ ] Live smoke (manual): a boss-tier entity with a portrait renders a real MiniMax reveal clip through ComfyUI.

**Acceptance Criteria:**
- Given `video_backend="comfyui"` and a boss-tier entity with a portrait, when the DM enqueues a video job, then the reveal clip renders as mp4 bytes and lands in the manifest + `media/{campaign}/{entity}/` with no manual refresh.
- Given `video_backend="openai"` (default), then the 4-2 OpenAI path runs unchanged.
- Given a boss-tier entity with no portrait, then the video job fails cleanly (no file, no row) — i2v needs a source frame.
- Given a failed ComfyUI video job (provider, budget, no portrait, non-mp4), then the job ends `failed` with a user-facing message, no file and no manifest row.

## Design Notes

- i2v over t2v: MiniMax H3 animates a source image; the reveal video animates the entity's own portrait — a projection of the committed character (FR12). Hence the first_frame.
- MiniMax's prompt node is `inputs.prompt` (node 105:104), not 4-4's `inputs.value`; the adapter validates the shape it needs and injects there.
- LoadImage reads ComfyUI's input dir, not the media dir — the provider stages a copy in, sets the node, removes it after (no litter).
- `run_video` stays backend-agnostic: under comfyui it resolves the portrait and passes `first_frame`; the OpenAI provider accepts and ignores it. One call shape, two backends. (Review round 1: the RESOLUTION is comfyui-scoped — the openai path never touches the manifest.)
- Cancel-mid-render (review round 1): a MiniMax render runs ~1-2 min server-side, so the pre-existing 4-2 cancel-race deferral has a LONGER window on comfyui — a cancelled job stays running on ComfyUI for up to the whole-call timeout with no abort (this story adds no ComfyUI prompt cancellation). The run still completes file+row, and only `complete_job` raises `JobStateConflictError` (file+row kept — real content, no dangling row, cancelled job never reports success). Documented as ACCEPTED for now; revisit if the operator needs the GPU slot freed on cancel.

## Spec Change Log

### Review round 1 (no loopback)
- SCOPE FIX (P1): the portrait resolution + no-portrait gate in `run_video` are scoped to `[video] backend = "comfyui"` — the openai path runs exactly as spec-4.2 (no portrait resolution, `first_frame=None`). 4-2 tests pass without a portrait again.
- P2: both comfyui providers guard present-but-non-dict `inputs` (AttributeError -> `ProviderError("connection")`).
- P3: `_stage_first_frame` unlinks a partial staged copy when `copyfile` fails mid-transfer (no litter in ComfyUI's input dir on the failure path).
- P4: `_save_video_output` prefers the `.mp4`-suffixed entry over an earlier GIF-preview artifact (VHS output shape); fixture mirrors the real shape.
- P5: workflow_path defaults are computed from the repo's deploy dir; the shipped config's values are repo-relative (`workflows/…json`) and resolve against the active config file's directory — machine-independent across checkouts.
- P6: mp4-guard Always bullet amended (dual check: provider return AND write boundary; "single" = write boundary only).
- P7: `test_deploy_contract` pins that each shipped workflow JSON parses and carries the exact node/widget shape the providers inject into (commit-time renumbering catch).
- P8: `configured_video_backend() -> Literal["openai", "comfyui"]`; media runners type `settings` as a shared `ProviderSettings` protocol — no `type: ignore` needed in the worker's comfyui branches.
- P9: CANCEL_MID_RENDER matrix row + design note (accepted behavior; no ComfyUI prompt abort this story).
- P10: both shipped workflow JSONs' prompt/value widget texts redacted (providers overwrite them) — no dead campaign lore in the repo. `~Desktop` copies removed (byte-identical to the shipped files).
- `test_media_api.py` and `test_deploy_contract.py` noted in the Code Map test inventory.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. new comfyui-video tests (mock-based; no live ComfyUI required).
- `make lint && make typecheck` -- expected: ruff + mypy clean.

**Manual check (live MiniMax smoke):**
- With `video_backend="comfyui"` and `[comfyui_video] workflow_path` pointing at `deploy/workflows/video_minimax_h3_i2v_sage.json`, a boss-tier entity with a portrait enqueues a video job that renders a real clip through ComfyUI's MiniMax H3 workflow (submit/poll/fetch, ~1-2 min), lands mp4 in `media/{campaign}/{entity}/`, and serves `video/mp4` inline. The `[comfyui_video] timeout` default comfortably exceeds the render time.
- **LIVE SMOKE DONE (2026-09-08, 430s)**: BBEG "Vespera Nyx" (hand-edited to role=BBEG + boss section per 3-6) → portrait via Krea2 (~20s) → reveal video via MiniMax i2v (430s) → `01M20HQ7MBWCTHBVDJ2FPSNHVT.mp4` (1.5MB, `ftyp`) → manifest row kind=video → serves `video/mp4` 200 → export `available:true`. Operational note: `[comfyui_video] input_dir` must point at the **Comfy Desktop root** (`/home/main/ComfyUI-Shared/input`), not a stale `/home/main/comfy/ComfyUI/input` — the provider staged correctly once pointed at the real dir (no code change).

## Suggested Review Order

**The i2v provider — MiniMax H3, portrait as first frame**

- The whole story in one file: submit/poll/fetch with prompt injection, first-frame staging, mp4 guard, no-litter finally.
  [`comfyui_video.py:1`](../../backend/app/providers/comfyui_video.py#L1)
- .mp4-first output selection (a VHS GIF preview must not win).
  [`comfyui_video.py:340`](../../backend/app/providers/comfyui_video.py#L340)
- Staging failure unlinks the partial copy — no litter in ComfyUI's input dir.
  [`comfyui_video.py:315`](../../backend/app/providers/comfyui_video.py#L315)

**run_video: the portal-gate is comfyui-scoped**

- The critical fix: portrait resolution + no-portrait gate ONLY under `video_backend="comfyui"` (openai passes `first_frame=None`, 4-2 unchanged).
  [`service.py:318`](../../backend/app/media/service.py#L318)
- The `ProviderSettings` protocol that types `settings` across both backends without ignores.
  [`service.py:60`](../../backend/app/media/service.py#L60)

**Config + dispatch**

- `configured_video_backend() -> Literal[...]` + the comfyui branch.
  [`worker.py:260`](../../backend/app/pipeline/worker.py#L260)
- Repo-relative workflow paths (machine-independent; env override).
  [`config.toml:99`](../../deploy/config.toml#L99)

**Tests**

- The 26 provider matrix rows — happy path, staging, timeout, mp4 guard, node-shape guards.
  [`test_comfyui_video_provider.py:128`](../../backend/tests/test_comfyui_video_provider.py#L128)
- Deploy contract now pins the shipped workflow JSONs' node/widget shape.
  [`test_deploy_contract.py:63`](../../backend/tests/test_deploy_contract.py#L63)
- ComfyUI-scoped no-portrait gate under a comfyui fixture; 4-2 tests portraitless again.
  [`test_video_service.py:30`](../../backend/tests/test_video_service.py#L30)