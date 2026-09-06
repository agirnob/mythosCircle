---
title: '4-4 ComfyUI Image Provider (Local Dev Backend)'
type: 'feature'
created: '2026-09-06'
status: 'done'
review_loop_iteration: 0
context: []
baseline_commit: 338404474a3b34ed7988c450384f0f3a0ecf8823
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `image_generation` (spec-4.1) targets an OpenAI-compatible `/v1/images/generations` endpoint. Local dogfood has no OpenAI-shaped image server — the only working local backend is ComfyUI on `127.0.0.1:7896` running the Krea2 Turbo T2I workflow (`image_krea2_turbo_t2i_int8 (2).json`). Owners testing portraits on their workstation get a connection error, and the config defaults point at an inert placeholder (`http://127.0.0.1:8081/v1`). Backend support for the OpenAI image server is still the production shape (placeholder server, no live endpoint); ComfyUI is an alternative local-dev backend, not a replacement.

**Approach:** A sibling `comfyui_image_generation(prompt, *, settings)` provider mirroring the 4-1 / 4-2 adapter discipline (leaf of dependency graph, `ProviderError` family, injectable transport, kwargs-only `settings`). Polls `/history/{prompt_id}` until the SaveImage output is ready, fetches bytes from `/view`, returns PNG bytes (so `run_portrait`'s existing `PNG_SIGNATURE` guard stays authoritative). Backend-only: no queue / manifest / serve / frontend changes — the worker picks the provider by a new `[image] backend = "openai" | "comfyui"` config field and routes the call. OpenAI path stays default; ComfyUI is opt-in. Tests inject a mock ComfyUI transport; no live ComfyUI is required for the suite to pass.

## Boundaries & Constraints

**Always:**
- Mirror the 4-1 / 4-2 provider discipline exactly: leaf module, `Callable[..., bytes]`, `ProviderError` family from `app.providers.llm`, injectable `transport` kwarg, kwargs-only `settings`. No new error categories; the worker / `run_portrait` treat every provider alike.
- The provider returns **PNG bytes** (ComfyUI's SaveImage output is PNG). `run_portrait`'s `PNG_SIGNATURE` guard is the write boundary — never duplicated, never weakened.
- Workflow JSON is loaded by **absolute path** from config (`[comfyui_image] workflow_path`). Default ships to a missing path (operator must set it; tests inject the bytes inline). No workflow JSON in the repo.
- Settings read follows `env > config > default` precedence like every other adapter; the api_key (if any) is environment-only (AD-22).
- One provider call = one full generation (submit + poll + fetch). The `timeout` setting bounds the **entire** call, not just the submit. `poll_interval` is a hardcoded code default (1.0s) — not operator-tunable.
- Cancel-race poll (`_job_still_running`) belongs to `run_portrait`, not the provider. The provider has no knowledge of jobs.
- Backend-only: no API/frontend changes. `image_backend` is a runtime config switch.

**Ask First:**
- The Krea2 Turbo workflow file lives at `/home/main/Desktop/...` (operator machine). Confirm the deployment posture: do we ship the workflow JSON to the operator host in `deploy/`, or only document the path? (Default plan: document only; tests inject bytes. Reopen if owner wants it shipped.)

**Never:**
- No frontend changes; no queue changes; no store changes; no api/media changes. The image job kind / payload / manifest / serve behavior is spec-4.1 — frozen.
- No workflow JSON in the repo (operator-specific paths, model weights referenced by node IDs differ per host).
- No automatic backend selection at runtime — `image_backend` is a static config field; no fallback chain.
- No OpenAI-shape adapter on the ComfyUI side; no ComfyUI-shape adapter on the OpenAI side. One provider function per backend.
- No premium tier / multi-image / aspect-ratio-per-job runtime knobs. `aspect_ratio` and `megapixels` are config-level defaults; one job = one image, fixed shape per config.
- No state caching across calls. Each provider call reloads the workflow JSON from disk (workflows are small — ~8 KB); a stale workflow must never be served.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH_OPENAI | `image_backend="openai"` (default) | `image_generation` runs as today (spec-4.1) | N/A |
| HAPPY_PATH_COMFYUI | `image_backend="comfyui"`, prompt, workflow_path set, server live | Submit → poll `/history` until output → fetch `/view` → PNG bytes | N/A |
| SUBMIT_HTTP_ERROR | ComfyUI `/prompt` returns 4xx/5xx | `ProviderError("http", status_code)` | Worker fails the job |
| SUBMIT_CONN_ERROR | ComfyUI unreachable (DNS / refused / timeout) | `ProviderError("connection")` | Worker fails the job |
| POLL_TIMEOUT | `/history` never produces an output before `timeout` | `ProviderError("timeout")` (NEW category — the only new one) | Worker fails the job |
| MALFORMED_SUBMIT | `/prompt` 200 with non-JSON / no `prompt_id` | `ProviderError("http", 200)` | Worker fails the job |
| NO_OUTPUT_NODE | `/history` 200 with no SaveImage output for this prompt | `ProviderError("http", 200)` | Worker fails the job |
| VIEW_FETCH_FAIL | `/view` returns non-200 | `ProviderError("http", status_code)` | Worker fails the job |
| NON_PNG_BYTES | `/view` returns non-PNG bytes | `ProviderError("http", 200)` (provider-side, before write) | Worker fails the job (provider never hands garbage to `run_portrait`) |
| WORKFLOW_PATH_MISSING | Configured path doesn't exist | `ProviderError("connection")` at first call | Worker fails the job; surfaces operator misconfig |
| INVALID_WORKFLOW_JSON | Path exists but JSON malformed / missing the prompt node | `ProviderError("connection")` | Worker fails the job |

</frozen-after-approval>

## Code Map

- `backend/app/providers/image.py` -- the OpenAI-compatible adapter (4-1): `Callable[..., bytes]`, `httpx.Client`, `ProviderError` family, `transport=` injectable, `settings=` kwarg. New sibling `providers/comfyui.py` mirrors this discipline but speaks ComfyUI's submit+polling protocol. The OpenAI file stays untouched.
- `backend/app/providers/llm.py:ProviderError` -- shared error vocabulary. Add one new `kind="timeout"` to the existing family (used by no other adapter today; ComfyUI poll exhaustion is the first caller). Dataclass signature is unchanged — `ProviderError(kind, status_code=None)` already supports it.
- `backend/app/core/settings.py:109` -- `ImageSettings` dataclass + `image_settings()` (env > config > default). Add `ComfyUIImageSettings` (endpoint, workflow_path, prompt_node_id, aspect_ratio, megapixels, timeout, api_key) + `comfyui_image_settings()` + `COMFYUI_IMAGE_*` env constants. New function follows the existing `image_settings()` shape line-for-line; only the field set differs (ComfyUI is workflow-driven, not model-driven).
- `backend/app/core/config.py:73` -- `DEFAULT_IMAGE_*` placeholder block + `RuntimeConfig.image_*` fields. Add `image_backend: Literal["openai","comfyui"] = "openai"` (env > config > default; defaults preserve current behavior — opt-in). Add `comfyui_image_endpoint/workflow_path/prompt_node_id/aspect_ratio/megapixels/timeout` fields, `COMFYUI_IMAGE_*` env constants, `[comfyui_image]` parse in `load_config`. New validation: `workflow_path` resolved at read time, not lazily.
- `deploy/config.toml:35` -- `[image]` section gains `backend = "openai"` (default). New `[comfyui_image]` section with documented defaults (`endpoint = "http://127.0.0.1:7896"`, `workflow_path = ""` — operator must fill, `prompt_node_id = "30:19"`, `aspect_ratio = "1:1 (Square)"`, `megapixels = 1.0`, `timeout = 1800` — Krea2 Turbo on local GPU is 30-90s typical). All values are inert placeholders consistent with the 4-1 / 4-2 ask-first comment style; backend switch defaults to OpenAI so nothing about current behavior changes until the operator opts in.
- `backend/app/media/service.py:151` -- `run_portrait` takes `provider: Callable[..., bytes]` and returns PNG bytes; **NO change**. ComfyUI returns PNG bytes; the existing `PNG_SIGNATURE` guard at `service.py:206` is the write boundary. Zero regressions.
- `backend/app/pipeline/worker.py:177` -- the `if job.kind == "image"` branch: add a third pair of injectables `comfyui_image_provider` / `comfyui_image_settings` to `run_next_job` and `_run_job`. Dispatch: if `resolved.image_backend == "comfyui"`, use `comfyui_image_provider or comfyui_image_generation` + `comfyui_image_settings or resolve_comfyui_image_settings()`; else the existing OpenAI path. Production defaults preserve the spec-4.1 behavior exactly when `backend = "openai"` (default).
- `backend/tests/test_image_provider.py` -- the OpenAI adapter contract under `httpx.MockTransport`. New `test_comfyui_image_provider.py` mirrors it: submit-returns-prompt_id, poll-returns-output, view-returns-png-bytes, all error variants from the matrix, plus the `test_config.py` extension for `[comfyui_image]` and `image_backend` (env > config > default precedence; default `"openai"`; config `"comfyui"` honored; env override wins).
- `backend/tests/test_worker.py` -- production-default dispatch test (OpenAI path today): add a sibling test that asserts `image_backend = "comfyui"` resolves the comfyui provider under the same `run_next_job` seam. Mirrors the 4-2 video path's worker dispatch tests.

## Tasks & Acceptance

**Execution:**
- [x] `backend//providers/comfyui.py` -- new `comfyui_image_generation(prompt, *, settings, transport=None)`; submit workflow JSON to `{endpoint}/prompt`, poll `{endpoint}/history/{prompt_id}` every 1.0s until `outputs` contain a SaveImage node, GET `{endpoint}/view?filename=X&type=output&subfolder=Y`, return PNG bytes. `ProviderError("timeout")` on poll exhaustion; `ProviderError("connection")` on submit/transport failures; `ProviderError("http", status_code)` on any non-2xx or malformed JSON.
- [x] `backend//providers/llm.py` -- add `kind="timeout"` to the documented `ProviderError` family. No constructor change; just the docstring.
- [x] `backend//core/config.py` + `backend/app/core/settings.py` -- `image_backend` field (env > config > default, default `"openai"`); `ComfyUIImageSettings` dataclass; `comfyui_image_settings()` resolver; `COMFYUI_IMAGE_*` env constants; `[comfyui_image]` config parser.
- [x] `deploy/config.toml` -- `backend = "openai"` on `[image]`; new `[comfyui_image]` section with documented placeholders + `ask-first` comment.
- [x] `backend//pipeline/worker.py` -- add `comfyui_image_provider` / `comfyui_image_settings` injectables to `run_next_job` / `_run_job`; branch in the `image` kind dispatch by `image_backend`.
- [x] `backend//test_comfyui_image_provider.py` -- matrix-row tests: happy path (submit→poll→view), bearer key, config swappability, submit HTTP error, submit conn error, poll timeout, malformed submit, missing output node, view fetch fail, non-PNG bytes, missing workflow path, invalid workflow JSON.
- [x] `backend//test_config.py` -- `[comfyui_image]` env > config > default precedence; `image_backend` default is `"openai"`, config override `"comfyui"` is honored, env wins over config.
- [x] `backend//test_worker.py` -- `run_next_job` with `image_backend="comfyui"` resolves the comfyui provider and dispatches it; OpenAI path unchanged when `backend="openai"` (default).
- [x] `backend//` smoke: live ComfyUI on `127.0.0.1:7896` reachable + workflow file at `/home/main/Desktop/image_krea2_turbo_t2i_int8 (2).json` -- manual: `run_image_job` against the live backend returns a real PNG. Not in CI; documented in Verification.

**Acceptance Criteria:**
- Given `image_backend="openai"` (default) and the existing 4-1 tests, when `run_next_job` claims an `image` job, then `image_generation` runs as today and the suite stays green.
- Given `image_backend="comfyui"`, a reachable ComfyUI endpoint, and a configured workflow path, when `run_next_job` claims an `image` job, then `comfyui_image_generation` submits → polls → fetches → returns PNG bytes and `run_portrait` writes the file + manifest row unchanged.
- Given any matrix-row failure (submit HTTP, submit conn, poll timeout, missing output, view fetch fail, non-PNG, missing/invalid workflow), when the provider is called, then the worker fails the job with a user-facing message, no file and no manifest row.
- Given `image_backend` is unset / misspelled / `None`, then the resolver falls back to `"openai"` and the existing path runs.

## Design Notes

**Why a sibling provider, not an OpenAI adapter on ComfyUI:** ComfyUI's wire protocol is fundamentally poll-based (submit → poll history → fetch view). Forcing it into the OpenAI synchronous-single-POST shape requires a long-lived request that ComfyUI doesn't support — every existing OpenAI-image adapter assumes the response carries the image bytes. A sibling provider is the boring/safe choice.

**Why `run_portrait` is untouched:** `PNG_SIGNATURE` is the write boundary for both backends because both produce PNG bytes. Adding a second guard inside the provider duplicates the contract; weakening it for ComfyUI (webm/jpeg allowed?) would break the API's kind-aware serve.

**Workflow JSON injection point:** the Krea2 Turbo workflow's prompt entry is node `"30:19"` `inputs.value` (verified against `/home/main/Desktop/image_krea2_turbo_t2i_int8 (2).json`). The provider copies the JSON, mutates only that one field, submits, polls for `outputs` containing any SaveImage node (the workflow has `"29"`). Hardcoding the node IDs in config (`prompt_node_id`) avoids a fragile scan of the workflow graph per call. NOTE: node `30:28` in the same workflow is a switch, not a prompt widget — the injection point must stay the operator's prompt widget.

**`ProviderError("timeout")` is new:** the 4-1 / 4-2 adapters can't time out the way ComfyUI can (poll exhaustion vs. HTTP timeout). Adding the kind is a one-line docstring update; the dataclass is unchanged. The worker's error vocabulary gains one entry.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. new `test_comfyui_image_provider.py` + worker dispatch test (mock transport; no live ComfyUI needed).
- `make lint && make typecheck` -- expected: ruff + mypy clean.
- Manual smoke (NOT in CI): with ComfyUI live on `127.0.0.1:7896` and the operator's workflow JSON at the configured path, `backend/.venv/bin/python -c "from app.providers.comfyui import comfyui_image_generation; ..."` against a real boss-tier prompt. Verify PNG bytes decode to a valid image. Document the exact one-liner in the operator runbook.

**Manual checks (if no CLI):**
- `deploy/config.toml`: `image_backend = "comfyui"` + `[comfyui_image]` section present with `workflow_path` pointing at the operator's Krea2 Turbo JSON; the OpenAI `[image]` section's `endpoint` is irrelevant when ComfyUI is active (config is read but never called).
- `backend/app/pipeline/worker.py`: dispatch branch's OpenAI path is byte-identical to spec-4.1 when `backend = "openai"`.
- `ProviderError("timeout")`: grep-able in `providers/llm.py` docstring; the only caller is `comfyui_image_generation`'s poll exhaustion path.

## Suggested Review Order

**Provider protocol (entry point)**

- The submit→poll→view protocol: whole-call deadline on every request, poll cadence, per-call error vocabulary.
  [`comfyui.py:79`](../../backend/app/providers/comfyui.py#L79)

- The write-boundary twin guard: PNG signature + IEND terminator rejected provider-side.
  [`comfyui.py:60`](../../backend/app/providers/comfyui.py#L60)

- Config-level resolution knobs wired into the workflow's resolution node, absent-tolerant.
  [`comfyui.py:271`](../../backend/app/providers/comfyui.py#L271)

**Error vocabulary**

- The new `"timeout"` kind now renders distinctly — poll exhaustion no longer masquerades as a dead server.
  [`llm.py:48`](../../backend/app/providers/llm.py#L48)

**Settings + config (opt-in shape)**

- `image_backend` defaults `"openai"`; only literal `"comfyui"` opts in, no fallback chain; non-finite env floats fail loudly.
  [`config.py:240`](../../backend/app/core/config.py#L240)

- `ComfyUIImageSettings` resolver: env > config > default, empty env falls through even for numerics, api_key env-only.
  [`settings.py:163`](../../backend/app/core/settings.py#L163)

- The shipped `[comfyui_image]` placeholders + switch — operator fills `workflow_path`.
  [`config.toml:45`](../../deploy/config.toml#L45)

**Worker dispatch**

- The image branch picks the provider by backend; OpenAI path byte-identical when `"openai"` (default).
  [`worker.py:196`](../../backend/app/pipeline/worker.py#L196)

**Tests (matrix rows + review additions)**

- The 18+ adapter-contract tests incl. the slow-server whole-call-timeout proofs and twin-guard pin.
  [`test_comfyui_image_provider.py:1`](../../backend/tests/test_comfyui_image_provider.py#L1)

- Config precedence + env-only opt-in + non-finite rejection + restored video-placeholder test.
  [`test_config.py:1`](../../backend/tests/test_config.py#L1)

- Real-provider seam test and backend dispatch under `run_next_job`.
  [`test_worker.py:1`](../../backend/tests/test_worker.py#L1)

- Shipped-config contract pinned (`backend == "openai"`, placeholders intact).
  [`test_deploy_contract.py:1`](../../backend/tests/test_deploy_contract.py#L1)