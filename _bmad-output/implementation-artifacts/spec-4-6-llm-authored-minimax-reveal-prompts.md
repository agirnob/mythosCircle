---
title: '4-6 LLM-Authored MiniMax Reveal Prompts (two-phase)'
type: 'feature'
created: '2026-09-08'
status: 'done'
baseline_commit: '833c7975f93c863010d5890547a294e8000db80e'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The reveal-video prompt is built mechanically (`bbeg_video_prompt`: appearance + boss lines + name — role + a static "slow cinematic BBEG reveal" framing). MiniMax-H3 generates **native audio** alongside the video (the AAC track / `VAEDecodeAudio` in the workflow). With no soundscape/music/dialogue directive in the prompt, the model hallucinated speech ("sorry Esteya") — the guide is explicit that a proper I2VA prompt carries `integrated_multimodal_description` + `overall_soundscape` + `non_diegetic_music` (or `N/A` for silence).

**Approach:** Two phases, because the 3090 can't hold the LLM (gemma-4-12B) and MiniMax at once (they alternate — owner-confirmed decision 2026-09-08):

1. **Draft** (new `video_prompt` job kind) — gemma authors a MiniMax-H3 **I2VA-compliant** prompt from the entity's committed AR24 data + the in-repo writing guide. The DM reviews/edits it.
2. **Render** (existing `video` job) — uses the approved/edited prompt instead of `bbeg_video_prompt`. `run_video` accepts the prompted payload; the draft is what the DM sees before a ~5-min render, so "sorry Esteya" never ships silently.

The MiniMax guide ships in-repo at `deploy/guides/VIDEO_PROMPT_WRITING_GUIDE_base_en.md` (already added) and is loaded into the draft-generation prompt.

## Boundaries & Constraints

**Always:**
- Media is NOT world graph (AD-1): a prompt draft is a **job result** (JSON `{entity_id, prompt}`), never a new store table or an entity-data mutation. No `media` manifest row for a prompt (manifest holds files: png/mp4).
- Two disjoint job kinds: `video_prompt` (LLM draft) and `video` (render). The video job's payload carries the DM's approved prompt: `{entity_id, prompt}`. A prompt-less `{entity_id}` video payload still works: `run_video` falls back to `bbeg_video_prompt` (backward compatible; the OpenAI t2v path 4-2 is unchanged).
- The draft job reuses the existing LLM adapter `chat_completion` (app.providers.llm) + `LLMSettings`; no new provider. It loads the guide from `deploy/guides/...` by path and the entity's committed `data` (appearance, boss, name, role, background, goals) to build the generation instruction.
- Enqueue gates stay in the store (`store/jobs.py`): `video_prompt` validates `{entity_id}` + entity exists + is boss-tier + has appearance/boss (a MiniMax-I2VA draft needs a source-frame description). `video` keeps the boss-tier gate; when a `prompt` is supplied it relaxes the `bbeg_video_prompt`-non-None check (the DM's prompt is the source of truth), but the source-frame (appearance) + boss gate still apply.
- The runner (`run_video_prompt`) mirrors `run_portrait`'s discipline: re-read committed entity, budget-guarded LLM call, non-blank result, `complete_job({entity_id, prompt})`; every failure fails the job with no leftover.
- The DM's edit is optional-but-expected: the render uses whatever prompt is in the video payload (drafted-and-accepted, drafted-and-edited, or hand-written). The frontend prefills the textarea from the latest draft job result.

**Ask First:**
- Draft durability: defaults to **session-only** — the draft is a job result the frontend holds; a reload loses it (re-draft). Persisting edited drafts to a store is deferred (would be a world-state-adjacent change; AD-1 wants the store to own it). Confirm this is acceptable for beta vs. wanting persisted drafts.
- Guide ingestion: defaults to **embed the full guide text** (~4k tokens, gemma-12b handles it) in the draft instruction. Confirm vs. distilling to key rules.

**Never:**
- No single-job auto-swap; no store schema change; no manifest/migration.
- No automatic render after a draft — the video job is always DM-triggered (two-phase is the point).
- No fallback chains: `video_prompt` always uses the LLM; there is no template fallback if the LLM is down (the job fails, the DM knows).
- No changes to the `video` provider call shape (`provider(prompt, settings, first_frame=...)`) or the mp4 guard.
- The guide file is not edited by code; only read into a prompt.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error |
|----------|--------------|----------|-------|
| DRAFT_OK | boss entity w/ appearance+boss, LLM up | `video_prompt` job succeeds, result `{entity_id, prompt}` = I2VA-structured (3 core fields) | N/A |
| DRAFT_NO_BOSS | not boss-tier | job 422 at enqueue | InvalidJobInputError |
| DRAFT_NO_SOURCE | boss, no appearance | job 422 at enqueue (needs source-frame desc) | InvalidJobInputError |
| DRAFT_LLM_DOWN | LLM refused/timeout | job `failed`, no draft, stable message | JobPayloadError("video prompt generation failed: …") |
| DRAFT_BLANK_RESULT | LLM returns blank | job `failed` | JobPayloadError |
| RENDER_WITH_PROMPT | video payload `{entity_id, prompt}` | `run_video` uses `payload["prompt"]`, renders | N/A |
| RENDER_LEGACY | video payload `{entity_id}` only | falls back to `bbeg_video_prompt` | N/A |
| RENDER_BLANK_PROMPT | `{entity_id, prompt:""}` | 422 (blank supplied prompt rejected; use legacy or re-draft) | InvalidJobInputError |
| RENDER_NOT_BOSS | demoted mid-queue | job fails (existing NOT_BOSS matrix row) | JobPayloadError |
| RENDER_NO_FRAME | comfyui, boss, prompt OK, no portrait | job fails (existing NO_FIRST_FRAME) | JobPayloadError |

</frozen-after-approval>

## Code Map

- `backend/app/media/service.py` — `bbeg_video_prompt(data)` at :142 (appearance + BOSS_PROMPT_KEYS lines + `name — role` + REVEAL_FRAMING at :113). `run_portrait` at :180, `run_video` at :280 — the runner discipline to mirror: run-time re-read via `session_scope()` (`entity = session.get(models.Entity, entity_id)` + `entity.campaign_id != job.campaign_id` → `JobPayloadError`), budget-guarded provider call, `complete_job(job.id, result=...)`. `run_video`'s payload contract check at :292 currently `set(payload) != {"entity_id"}` — MUST become `set(payload) <= {"entity_id", "prompt"}` with entity_id required; prompt read when non-blank string, else `bbeg_video_prompt(data)` fallback. Role re-check `data.get("role") not in BOSS_ROLES` at :313 (BOSS_ROLES imported from `app.store.candidates`). `run_video_prompt(job, llm: ChatCompletion, settings: LLMSettings)` — new sibling: mirror `run_portrait`'s entity re-read, build instruction (guide text + data projections), `CallBudget(job).call(lambda: llm(prompt, settings=settings))` (LLM budget, NOT `MediaCallBudget` — it is an LLM call; `app.pipeline.budget.CallBudget` at budget.py:27), non-blank check, `complete_job(job.id, result={"entity_id": ..., "prompt": ...})`.
- `backend/app/pipeline/worker.py` — `run_next_job` (:48) signature carries `provider: Provider = chat_completion` + `settings: LLMSettings` (the text-road injectables) — the `video_prompt` dispatch reuses them (no new param). `_run_job` (:173) `if job.kind == "video"` branch at :284 — add `if job.kind == "video_prompt": from app.media.service import run_video_prompt; run_video_prompt(job, provider, settings); return` (lazy import, the established circularity pattern). The unsupported-kind error string at :340 lists kinds — extend it. `_error_message` at :333 handles `ProviderError`/`JobPayloadError` already — a `JobPayloadError("video prompt generation failed: …")` from the runner surfaces cleanly with no worker change.
- `backend/app/store/jobs.py` — `JOB_KINDS` frozenset at :49 (`{"text", "image", "video", "build_in", "generate", "regenerate"}`) — add `"video_prompt"`. `_validate_video_payload` at :596 — currently `set(payload) != {"entity_id"}` → 422; update to accept `prompt` (optional non-blank-only-after-trim): `set(payload) <= {"entity_id", "prompt"}`, entity_id ULID+exists+boss-tier gates unchanged, supplied blank prompt → `InvalidJobInputError`. New `_validate_video_prompt_payload(payload, session, campaign_id)` mirroring `_validate_image_payload` at :565 (exact `{"entity_id"}`, ULID, existence, boss-tier, non-blank appearance via `appearance_prompt` at service.py:119 — the draft needs a source-frame description). Dispatch `elif kind == "video_prompt":` in `_enqueue` at :398-413. Error classes `InvalidJobInputError` (:123) / `UnknownEntityError` already exist.
- `backend/app/api/jobs.py` — `JobCreate.kind: Literal[...]` at :26 — add `"video_prompt"` to the union. No other API change (payload is `dict[str, Any]`; enqueue gates own validation; `store_error_as_http` maps StandardError→422/404/409).
- `backend/app/core/settings.py` — `LLMSettings` + `llm_settings()` live HERE (not providers.llm); `app.providers.llm` exports `chat_completion(prompt, *, settings=...)` + `ChatCompletion = Callable[..., str]` + `ProviderError`.
- `frontend/src/stores/jobs.ts` — `submitRevealVideo` at :170 (POST `/api/jobs` `{campaign_id, kind: 'video', payload: {entity_id}}` via `apiFetch<Job>`); `videoInFlight` getter at :78. Add `submitRevealVideoPrompt(campaignId, entityId)` (kind `video_prompt`) and a `videoPromptFor(campaignId, entityId)` getter: latest SUCCEEDED `video_prompt` job's `result.prompt` for the entity (result is `{entity_id, prompt}` — jobs have `result: dict | null`). Jobs WS sync surfaces terminal frames (`job_done`/`job_failed` → re-list; world.ts:294).
- `frontend/src/views/WorldView.vue` — reveal-video block: helpers at :356-458 (`isBossTier` :375, `hasVideoPrompt` :383, `videoJobFor` :408, `videoStatus` :425, `generateRevealVideo` :446 which calls `jobs.submitRevealVideo`), template at :904-953 (button bindings, `videoFor(entity)` clip, failure/status lines). Extend: "Draft reveal prompt" button (disabled while no appearance / not boss / `video_prompt` in-flight — mirror `jobs.videoInFlight`), editable textarea prefilled from `jobs.videoPromptFor(campaignId, entity.id)`, render button sends `{entity_id, prompt}` via updated `submitRevealVideo(campaignId, entityId, prompt?)`.
- `frontend/src/stores/world.ts` — `videoFor(campaignId, entityId)` at :132 picks latest kind=video media row; NO new media row for drafts (keep `videoPromptFor` in jobs.ts).
- `deploy/guides/VIDEO_PROMPT_WRITING_GUIDE_base_en.md` — committed companion, 15.7 KB / 222 lines; I2VA first line at :24 (`For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`), three core fields at :45-56 (`integrated_multimodal_description` / `overall_soundscape` / `non_diegetic_music`). Read by `run_video_prompt` via repo path; never written.
- `backend/tests/` — `test_video_service.py` (604 lines: media-runner patterns, mock provider/transport injection), `test_jobs_api.py` (474: enqueue 422/404/409 matrix via TestClient), `test_worker.py` (dispatch + `_error_message`), `test_deploy_contract.py` (:108, workflow-file pins at `test_shipped_comfyui_workflows_parse_and_carry_provider_shape`) — add the guide-exists-and-non-empty pin here. New `test_video_prompt_service.py` for the draft runner (mock `llm` callable, matrix rows DRAFT_OK/LLM_DOWN/BLANK + gate tests in test_jobs_api).
- `frontend/src/views/WorldView.test.ts` — reveal-video section at :1215+: `bossWorld(dataOverrides)` :1221, `stubVideoApi` :1271, `revealVideoButton` :1282 — extend for draft button + textarea + prompt-carrying render POST.

## Tasks & Acceptance

**Execution:**
- [x] `deploy/guides/VIDEO_PROMPT_WRITING_GUIDE_base_en.md` — commit the guide.
- [x] `backend/app/media/service.py` — `run_video_prompt` + `run_video` optional `prompt`.
- [x] `backend/app/pipeline/worker.py` — `video_prompt` dispatch.
- [x] `backend/app/api/jobs.py`; `backend/app/store/jobs.py` — `video_prompt` kind + validator updates.
- [x] `frontend/src/views/WorldView.vue`; `stores/jobs.ts`; `stores/world.ts` — draft + render surfaces.
- [x] `backend/tests/` — matrix-row tests.
- [x] Live smoke: draft a reveal prompt for Vespera (gemma up), review the prompt (assert it carries the 3 core fields + no dialogue unless desired), then render.

**Acceptance Criteria:**
- Given a boss-tier entity with appearance + boss, when the DM drafts a reveal prompt, then a MiniMax-I2VA-structured prompt results (contains `integrated_multimodal_description`, `overall_soundscape`, `non_diegetic_music`), reviewable/editable before render.
- Given that drafted (or hand-written) prompt, when the DM renders, then the video uses the submitted prompt verbatim (never `bbeg_video_prompt` substitution on a supplied prompt).
- Given a legacy `{entity_id}` video payload in tests/OpenAI path, then `bbeg_video_prompt` fallback still works (4-2 unchanged).
- Given the LLM down, a `video_prompt` job fails cleanly with no draft, no file, no row.

## Spec Change Log

<!-- Append-only; filled by step-04 review loops. -->

### 2026-09-08 — live smoke (draft half)

- Trigger: task-list live smoke for Vespera Nyx, deferred until the operator's Unsloth Studio (gemma-4-12B-it-qat-GGUF) was up.
- What ran: `POST /api/jobs` `kind: video_prompt` for Vespera (`01M20F1TH1SRVXQYT04XP3PEB2`, BBEG, appearance + boss section) against the dev api (`api-verdict` restarted to load the 4-6 code; `MYTHOSCIRCLE_DB=/tmp/mythos-dev/mythoscircle.db`); job `01M20Z76RFKXYTH5SYBBQ7JKW7` → `succeeded` in ~20s.
- Result: the draft leads with the I2VA picture-alignment line, carries all three core fields, no invented dialogue, audio follows the drafted soundscape/music (entity-grounded: Vespera's braid/tunic/half-smile from appearance; the Seraph's radiant light from `boss.lair_actions`). No media row, no revision (AD-1: a draft is a job result only).
- Known-bad state avoided: the pre-4.6 render path shipped whatever MiniMax hallucinated (the "sorry Esteya" failure); no draft + LLM down verifies the clean-fail path via the matrix instead.
- KEEP: `run_video_prompt`'s guide-verbatim embed is fast enough (~20s) — normalization of the instruction is not needed; the draft ran at `MYTHOSCIRCLE_LLM_TIMEOUT=2400` (operator env).
- Remaining: the render half of the smoke needs ComfyUI + MiniMax up (operator swaps VRAM: gemma out, video model in) — draft → render with the approved prompt verbatim.

### 2026-09-08 — live smoke (render half) — DONE

- Trigger: operator opened ComfyUI (127.0.0.1:7896, HTTP 200); the render half of the 4-6 smoke.
- What ran: `POST /api/jobs` `kind: video` with `{entity_id: Vespera, prompt: <drafted text verbatim, 1199 chars>}` → 201 (`01M2151W9TQV552G2GCMW2BXEN`). Worker claimed it, ComfyUI submitted the MiniMax H3 i2v workflow (provider history-polling observed in logs), **succeeded in ~5.3 min** (progress 0→1.0).
- Result: `01M215BGHVBF05SW66WN6Y3X7R.mp4` — 772 KB, valid ISO-MP4; h264 video + **AAC audio**, 13.67s both. Manifest row kind=video added; served `200 video/mp4` inline. Frame at 4s (vision-checked): single woman, chest-up, dim cinematic room, tight braid, high-collared midnight-blue tunic with metallic fastenings, calculating half-smile, camera push-in vibe — matches the committed appearance + drafted `integrated_multimodal_description`; no extra faces, no artifacts, no text. Audio (volumedetect): mean −27.8 dB, max −15.2 dB — present, quiet soundscape-level (fits "low ominous hum + slow cello"), decisively NOT the pre-4.6 hallucinated-speech signature (speech peaks −6…−1 dB).
- KEEP: the two-phase flow is the fix — draft (gemma, ~20s) → DM review → render (MiniMax, ~5.3 min) shipped the audio the DM ordered. The RENDER_WITH_PROMPT verbatim path is live-verified end to end (unit matrix already pinned it at the service layer).

## Design Notes

- The guide's I2VA instruction is the first line (the `<Picture 1>` referenced line); `run_video_prompt` instructs gemma to emit that line + the three core fields, and to use `overall_soundscape: N/A` / `non_diegetic_music: N/A` when the DM wants silence — that's the guardrail against the hallucinated "sorry Esteya" speech.
- `video_prompt` and `video` stay separate because only one model fits the 3090 at a time: draft when the LLM is up, render when MiniMax is up.
- Alignment with the citation in the retro and the `[video]`-backend parity: this story does NOT touch providers, the mp4 guard, or the `[video] backend` switch (4-4/4-5). 

## Verification

**Commands:**
- `uv run --directory backend pytest -q` — full suite green incl. new video_prompt tests (mock LLM; no live gemma).
- `make lint && make typecheck` — ruff + mypy clean.

**Manual (live smoke):**
- LLM up: draft a reveal prompt for Vespera Nyx — assert the result is I2VA-structured and the 3 core fields are present.
- Review/edit the prompt in the UI, then render (MiniMax ~5 min) — the clip's audio follows the drafted soundscape/music.
- LLM down: draft fails cleanly, no leftover.

## Suggested Review Order

**Runner logic — the two-phase heart**

- Entry point: the draft runner — one budget-guarded LLM call, result `{entity_id, prompt}`, no file/row (AD-1).
  [`service.py:478`](../../backend/app/media/service.py#L478)

- The draft instruction: guide verbatim + committed projections + the I2VA compliance demand against hallucinated speech.
  [`service.py:569`](../../backend/app/media/service.py#L569)

- `run_video` prompt branch: DM's prompt used verbatim, never a bbeg substitution; blank-prompt and appearance gates.
  [`service.py:313`](../../backend/app/media/service.py#L313)

**Enqueue gates & wire contract**

- Video payload validator: optional prompt relaxes only the boss-section projection; boss-tier + appearance gates hold.
  [`store/jobs.py:609`](../../backend/app/store/jobs.py#L609)

- New draft validator: boss-tier + non-blank appearance (I2VA source-frame) at the enqueue boundary.
  [`store/jobs.py:665`](../../backend/app/store/jobs.py#L665)

- Closed kind set + API Literal union stay in sync.
  [`store/jobs.py:49`](../../backend/app/store/jobs.py#L49) · [`api/jobs.py:32`](../../backend/app/api/jobs.py#L32)

**Worker dispatch**

- Draft dispatch reuses the existing text-road LLM injectables — no new worker params.
  [`worker.py:281`](../../backend/app/pipeline/worker.py#L281)

**Frontend surface**

- Draft button/textarea/render wiring: local edit wins, fresh draft replaces, in-flight + failure discipline mirrored.
  [`WorldView.vue:429`](../../frontend/src/views/WorldView.vue#L429) · [`WorldView.vue:1019`](../../frontend/src/views/WorldView.vue#L1019)

- Jobs-store getters: in-flight gate + latest succeeded draft per entity (session-only durability).
  [`jobs.ts:91`](../../frontend/src/stores/jobs.ts#L91) · [`jobs.ts:104`](../../frontend/src/stores/jobs.ts#L104) · [`jobs.ts:204`](../../frontend/src/stores/jobs.ts#L204)

**Verification & deploy pin**

- Draft-runner matrix + render-with-prompt/legacy/blank rows.
  [`test_video_prompt_service.py:57`](../../backend/tests/test_video_prompt_service.py#L57) · [`test_jobs.py:379`](../../backend/tests/test_jobs.py#L379)

- Guide ships in-repo, non-empty, carries the I2VA contract.
  [`test_deploy_contract.py:110`](../../backend/tests/test_deploy_contract.py#L110)

- Frontend two-phase flow tests (draft click, prefill, edited-prompt render, failure).
  [`WorldView.test.ts:1441`](../../frontend/src/views/WorldView.test.ts#L1441)