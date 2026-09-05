# Epic 4 Context: Give Them a Face — Media

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Every accepted entity arrives with a painter-grade portrait, and the BBEG reveal gets a fast-tier short video during beta. Media is owned by the entity: images and video are written through the media manifest at `media/{campaign_id}/{entity_id}/`, tracked in the DB manifest, reclaimed when the entity is deleted, and reference-validated on export so nothing dangles or leaks. The world gets a face the moment it's real — and that face stays consistent when it leaves the tool.

## Stories

- Story 4.1: Portrait Generation for Accepted Entities
- Story 4.2: BBEG Fast-Tier Short Video (Beta, Not a Launch Priority)
- Story 4.3: Reclaim-on-Delete and Export Media Validation

## Requirements & Constraints

- Every accepted entity ships a portrait; the committed AR24 appearance section (painter-grade: face, body, clothing, scars, marks) is the prompt source — a portrait is a projection of the committed character, not a free-text image prompt (FR12).
- Portrait/video media live at `media/{campaign_id}/{entity_id}/`, are tracked in the DB media manifest, are written only by the media service, and are reclaimed — files plus manifest rows — when the entity is deleted (AR12/AD-10).
- Export validates media references: broken references are flagged rather than shipped silently (FR14). Media ownership semantics extend to whole-campaign deletion (manifest rows + media directory).
- The BBEG reveal video is fast-tier and local only during beta, and is explicitly not a launch priority; if the fast tier disappoints during beta, video moves to Phase 2. Premium-tier video is out of scope (FR13).
- All image and video generation runs asynchronously with visible progress; it must never block the DM's other work (NFR9).
- Text, image, and video jobs share one persistent FIFO queue with exactly one job running at a time on the 3090 — no concurrent inference, no bypass path; queue position visible via REST + WebSocket (AD-3/AR15).
- Each generation job declares a max LLM/media call budget from config; exceeding it fails the job (AR21/AD-22).
- Media generation goes through OpenAI-compatible provider adapters (llm/image/video) — the local image server is the MVP provider, and the image/video model + server choice is an owner decision at build (AD-6/NFR8).

## Technical Decisions

- The media service (`backend/app/media/`) owns write/reclaim/validate; the store owns the manifest rows in the single SQLite WAL store; provider adapters (`backend/app/providers/`) are leaves speaking OpenAI-compatible HTTP — a local→hosted provider swap is a config change, not a rewrite.
- Single-machine deployment: one FastAPI process, one SQLite file, one media directory, local model servers (AD-8).
- Portrait generation runs only for accepted (committed) entities — staged candidates carry no media; the AR24 record committed at accept is the prompt's ground truth, with forward-compatible tolerance of unknown keys.
- Manifest write is the index of record: file writes and manifest rows must stay consistent, and media paths are derived from campaign/entity identity so references are stable for export and snapshot consumers.
- Fast/premium tiering: fast = cheaper, rougher, faster; premium is post-beta (OpenRouter) and not built now.

## UX & Interaction Patterns

- A completed portrait renders on the entity's screen; generation is asynchronous with queue position and progress surfaced so the DM can keep prepping other work while a portrait renders.
- Deletion reclaims media invisibly to the DM — no dangling images or stale references after an entity (or campaign) is removed.

## Cross-Story Dependencies

- Story 4.1 consumes the AR24 sectioned records shipped by Epic 3 (accept commits the full sectioned profile) — portrait prompts read the committed appearance section. This makes portrait prompts the first real consumer of the committed AR24 record beyond rendering.
- Story 4.2 builds on 4.1's manifest write path to attach the video; video jobs share the same queue and budget machinery as text/image jobs.
- Story 4.3's manifest and reclaim semantics are the contract Epic 5's exports embed (portrait references) and Epic 6's nightly snapshots and deletion include (media manifest + media directory).