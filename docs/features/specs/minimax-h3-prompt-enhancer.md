---
title: Image-grounded MiniMax H3 prompt enhancement
type: feature
created: 2026-10-20
status: done
baseline_commit: 48758592ed96f25aac853319188caac3480584cb
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="User requested implementation directly; no additional approval checkpoint">

## Intent

The reveal prompt drafter currently sees appearance text, not the actual image. Upgrade it to enhance the user's written direction using the selected portrait and MiniMax H3's official image-to-video prompting guide. Those two inputs are the only narrative sources. Preserve review/edit before rendering.

## Boundaries & Constraints

Always preserve campaign privacy, boss-tier eligibility, queue/cancellation/call budgets, normal administrator-selected LLM routing, and store-only database writes. Work exclusively in `/tmp/mythos-world-workspace` on its existing feature branch. Use `PYTHONPATH=/tmp/mythos-world-workspace/backend` for Python because the shared venv otherwise imports the primary checkout. Use Node 22. No commits, pushes, service restarts or unrelated edits during implementation. Report distilled outcomes.

Never silently substitute appearance/lore for image-aware enhancement, change routing or API keys, introduce public media access, or alter frozen design archives. Existing manual video prompts remain usable. An image-bound enhancement must render with its exact source image even if a newer portrait appears.

Ask first only for a new product decision outside this goal; routine implementation choices are authorized.

## I/O & Edge-Case Matrix

| Scenario | Input/state | Expected behavior | Error handling |
|---|---|---|---|
| Enhance | Owned boss portrait + nonblank direction | Actual image bytes and direction sent to vision-capable LLM; H3 draft returned with image ID | No world/media writes |
| Source invalid | Missing, foreign, deleted, unsafe or unreadable image | No provider call | Clear validation/runtime failure |
| Direction invalid | Blank, wrong type, over limit | No queued work | Validation error |
| Provider unavailable | Connection, unsupported vision, blank/malformed output | No appearance fallback; user retains direction/edit | Failed job with clear error |
| Render | Approved prompt and pinned image ID; newer portrait exists | Approved text verbatim and original image | Deleted/foreign source fails |
| Legacy | Existing manual prompt/render path | Remains usable without enhancement | Existing validation preserved |

</frozen-after-approval>

## Code Map

- `backend/app/media/service.py:724` `run_video_prompt` and helper currently appearance-only with offline fallback. `run_video:478` picks newest portrait. Add validated image-bound mode; legacy queued entity-only jobs may retain compatibility, but new UI enhancement always requires image and direction. New enqueue requests should require the new fields; update tests accordingly.
- `backend/app/store/jobs.py:949` video and video_prompt validators enforce owner/campaign via entity. New draft payload exactly `{entity_id,image_id,prompt}` (direction max 8000 chars). Render optionally accepts `image_id`; require approved prompt when supplied. Recheck image belongs to same campaign/entity and is kind image at enqueue and execution.
- `backend/app/core/settings.py:88` frozen `LLMSettings`, `backend/app/providers/llm.py:128` OpenAI-compatible user message. Add optional per-call `image_data_url` (repr=False), default absent; send text+image_url blocks only when attached. Preserve normal text-only shape. `backend/app/core/llm_routing.py` fallback must carry image; `core/journal.py` explicit settings allowlist must never persist base64.
- `backend/app/pipeline/worker.py:329` pass configured media directory to drafter. Read owned image from media directory with containment, bounded size and supported PNG/JPEG verification; existing portraits are PNG. No arbitrary URLs/paths from client.
- `frontend/src/components/ui/EntityMediaActions.vue` primary media UI currently has editable automatic appearance prompt but no enhance button. Add labeled direction field, visible source portrait, enhance action/status/error, editable resulting prompt. Keep manual prompt usable and persistent; do not auto-render. Preserve pinned source across refresh/edit and async job completion, session/entity switches and failures. Avoid overwriting edits with stale jobs.
- `frontend/src/stores/jobs.ts:279` draft submit currently entity-only. Accept direction + image ID; render accepts optional image ID. `frontend/src/views/WorldView.vue:613` legacy caller also needs adapting (it has its own draft textarea/status); ensure no broken callsites.
- `backend/tests/test_video_prompt_service.py`, `test_jobs.py`, `test_media_api.py`, provider/routing/journal tests and frontend media/jobs/WorldView tests contain existing contracts. Update meaningfully rather than removing coverage.

## Tasks & Acceptance

- [x] Extend per-call provider vision support and image-preserving fallback with wire-level and privacy tests.
- [x] Implement owned-image drafting, H3 instructions/format validation, payload validation and pinned rendering with source/error tests.
- [x] Update both UI callers and store; cover enhance/review/render, failure and portrait-change behavior.
- [x] Update feature documentation; run relevant tests, then backend suite, frontend suite/typecheck/build on Node 22. Report any preexisting issues separately.

Given an image with visual details differing from entity appearance/lore, when enhanced, then only the image and direction enter narrative context. Given success, when user edits and renders, then edited text is used verbatim and the same image supplies frame zero. Given a missing provider, when enhancement fails, then its error is visible and inputs remain editable.

## Design Notes

Primary reference: https://huggingface.co/MiniMaxAI/MiniMax-H3/raw/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md

Use the I2VA alignment line referencing `<Picture 1>` and `[Shot 1]`, followed by `integrated_multimodal_description`, `overall_soundscape`, `non_diegetic_music` separated by blank lines. Anchor frame zero to actual image composition/style/identity, then concrete actions/camera changes and result. Single continuous shot by default, no invented lore/dialogue; explicit user direction controls action/audio. Respect requested silence/no music as N/A. Current configured FastVideo workflow duration is 15 seconds; avoid arbitrary timed beats extending past actual duration (untimed temporal progression is acceptable). Model output may use strict JSON for three fields, then deterministic assembly. Explain format to user briefly. Prompt guide is a reference, not an extra narrative input.

## Spec Change Log

## Verification

Run relevant backend tests from `backend` with `.venv/bin/pytest -q` and explicit PYTHONPATH; socket/TestClient tests require approved escalation. Run `npm test -- --run`, `npm run typecheck`, `npm run build` from `frontend` with Node 22. Inspect resulting draft wire shape using provider mocks; do not claim live vision inference unless actually observed.

## Suggested Review Order

**Image enhancement**

- Build an H3 draft from image bytes and user direction.
  [service.py:918](../../../backend/app/media/service.py#L918)

**Ownership and source binding**

- Validate private source identity before enqueue.
  [jobs.py:1034](../../../backend/app/store/jobs.py#L1034)

**Provider vision input**

- Attach image only for this call; preserve text-only requests.
  [llm.py:101](../../../backend/app/providers/llm.py#L101)

**Review and render**

- Keep edits, source binding and queued work durable.
  [EntityMediaActions.vue:415](../../../frontend/src/components/ui/EntityMediaActions.vue#L415)

**Legacy caller**

- Preserve image binding through the older World interface.
  [WorldView.vue:774](../../../frontend/src/views/WorldView.vue#L774)

**Verification**

- Exercise ownership, malformed outputs, limits and pinned rendering.
  [test_video_prompt_service.py:1](../../../backend/tests/test_video_prompt_service.py#L1)

Final verification: 1,904 backend tests and 551 frontend tests passed. Node 22 typecheck/build/lint and offline dependency lock validation passed; lint has 12 preexisting warnings. Browser integration used a deterministic vision test double, with live vision unavailable because the configured provider key was absent.
