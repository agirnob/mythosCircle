---
stepsCompleted: ["step-01", "step-02", "step-03", "step-04"]
inputDocuments:
  - prds/prd-mythosCircle-2026-08-23/prd.md
  - architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md
---

# mythosCircle - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for mythosCircle, decomposing the requirements from the PRD and Architecture requirements into implementable stories. Scope: Phase 1 (beta) as the buildable core; Phase 2/3 capabilities carried at boundary level per the spine. Epics follow the user-value journey (Wren's arc); Epic 6 is the explicit **beta launch gate**.

**Positioning:** mythosCircle is a hosted website for the DM who runs the online table — and everyone who plays in it. End users reach it over TLS; the operator hosts it (own rig or GPU VPS). In Phase 1 there is one owner per campaign (NFR6); players get the world through the DM's export and the VTT — no player accounts. "Believable" is defined by the invariant set, not prose quality: zero dangling edges; counters change only via commits; cross-section regeneration consistency (AR26); KnowledgeProvider constraint enforcement (AR25); versioned state with per-transaction undo (AR4). Competitive field: one-shot character generators (CharGen, Roll Up a Character), hand-authored lore managers (World Anvil, Campfire, Kanka, Urdr), markdown catch-alls (Obsidian, Notion), and **Tabletop Arc — a full campaign platform: session builder (NPCs/quests/dungeons with Regenerate/Customize), a canon ledger of typed entities claiming "no drift, no contradictions," a living wiki, session audio → lore extraction with timestamped evidence, AI images (credit-metered), maps, and public campaign pages with player collaboration (free: 30 audio min, 10 image credits, unlimited campaigns; Pro $9/mo; Legend $19/mo with priority queue)**. What Arc ships that we don't: the session layer (FR19 is parity, not frontier) and player-facing public pages. What we ship that Arc doesn't (per its own page): **game mechanics as first-class** — validated 5e stat blocks, typed edges with game counters (their ledger is narrative entities with confidence; ours is game state); **the passport, not the home** — a quick NPC generated, exported to Owlbear/MapTool/Markdown in **under a minute**, and the full world → VTT in ten minutes (Arc keeps the campaign on its platform: their lock-in is our export); and **the DM as author** — staged candidates, DM edits beat the LLM, per-transaction undo/rebase (their AI maintains the ledger; ours the DM commits). Operator-hosted, zero per-token, no credits. **Product philosophy (the Obsidian principle):** open, portable, extractable in seconds is why Obsidian beat every note app — so the DM *uses* mythosCircle as needed (populate the world, generate, export) and doesn't *live* in it; the living world in the site is the reason to come back (generate more NPCs off the world, Simulate History), never a wall.

## Requirements Inventory

### Functional Requirements

```
FR1:  [A] The world is a graph: nodes (characters, factions, places) and directed typed edges (relationship, debt, grudge, loyalty, + starter set).
FR2:  [A] Every generated entity becomes a node woven into existing edges — the world grows with each use.
FR3:  [A] Edge types use a closed, directional vocabulary defined in Phase 1 (relationship, debt, grudge, loyalty, member_of, located_in, rival_of, kin_of, ally_of, enemy_of — extensible by adding a type, never free text); free-form labels are not persisted.
FR4:  [A] Deleting an entity with live edges requires an explicit cascade confirmation listing affected entities; the graph never holds dangling edges.
FR5:  [A] World state is exportable as JSON + Markdown at any time.
FR6:  [A] Guided world-build-in flow (name, key places, factions, key figures) plus the DM's own notes, digested by the LLM into entities.
FR7:  [B] A plain-language ask + world state returns 2–3 candidates.
FR8:  [B] Each candidate: name, role, personality, lore connections, the secret / rumor / party-hook triple, and a generated 5e stat block (level, AC, HP, abilities, key skills) — immediate playability.
FR9:  [B] Inline relation editing — while creating or editing a character, the DM adds, edits, or deletes relations to any entity (places, factions, other characters).
FR10: [B] Regenerate — whole or per-field; hand-edit; the DM is the final author.
FR11: [B] Candidate lifecycle: proposed → accepted / rejected; only an accepted candidate's subgraph commits; regenerate creates a new candidate and never mutates accepted state.
FR12: [C] Portrait image for a generated entity.
FR13: [C] Short video (BBEG reveal) — local fast tier only during beta; not a launch priority.
FR14: [C] Media attached to an entity is reclaimed on entity deletion; exports validate media references.
FR15: [D, Phase 2] Node-link relationship visualizer around any entity ("barkeep → thieves' guild → mayor").
FR16: [E] Characters, places, and factions export to: Markdown, Owlbear, MapTool (RPGToken JSON + portrait), Fantasy Grounds. Roll20 in Phase 2.
FR17: [E] Exports carry the generated 5e stat block and the generated portrait — the entity lands table-ready.
FR18: [E] Export format validation failure is logged as an export-failure event.
FR19: [F, Phase 3] Session logger: typed timeline of player actions (actors, targets, campaign time, note); later transcribed from session recordings.
FR20: [G, Phase 3] Simulate History: the graph state machine computes deterministic deltas from logged actions; the LLM only narrates; DM-authored rules over the closed Phase-1 edge vocabulary + a fixed starter rulebook.
```

### NonFunctional Requirements

```
NFR1:  Compute & cost. Generation runs on the operator's host — the developer's own machine or a GPU VPS; end users reach it as a website over TLS. Zero per-token cost at MVP (no cloud LLM API). After the initial phase: API route via OpenRouter; metered, bounded multi-calls, per-campaign caps (values set at migration). DECISION 2026-08-23: mythosCircle is a hosted website, not a local tool — locality is the operator's, not the end user's.
NFR2:  World-state integrity. The graph is the single source of truth; generation never silently corrupts existing entities or edges; the DM's edit always beats the LLM's. Verifiable invariants: zero dangling edges; entity identity stable across regeneration; retrieval-relevance proxy — % of generated candidates whose top-k retrieved entities appear in final lore connections.
NFR3:  Commit semantics. World state is versioned; each generation commits as one atomic subgraph transaction against the latest revision; undo is per-transaction; a DM edit landing during a pending commit forces a visible rebase-or-reject.
NFR4:  Beta throughput. Single-generation FIFO queue — no concurrent inference on one GPU; queue position visible; serial RTX 3090 throughput is the beta ceiling.
NFR5:  Backup. World state + media manifest snapshotted automatically (nightly, local); restore tested before beta launch. Export is ownership, not backup.
NFR6:  Accounts. Invited-user identity, per-campaign ownership, TLS. Campaigns are private at beta; sharing is a post-beta experiment.
NFR7:  Content capability. MVP runs an abliterated (uncensored) local model so grimdark content generates without refusal. The OpenRouter migration must select non-over-censored endpoints.
NFR8:  Provider abstraction. The inference endpoint is swappable: local server (MVP) → OpenRouter (later). No hard-coded single-vendor dependency.
NFR9:  Long-running operations. Generation, image, and video run asynchronously with progress.
NFR10: Data ownership. World state always fully exportable (JSON + Markdown) independent of VTT targets; deleting a campaign actually deletes it.
NFR11: Scale. Long-campaign capable: hundreds of entities, thousands of edges. Only lore relevant to the request enters generation — retrieval over the graph, never the whole world in context.
NFR12: Stack. Python/FastAPI backend, Vue frontend. Graph-visualizer candidates: Cytoscape.js / Vue Flow (Phase 2).
```

### Additional Requirements

```
AR1:  [Epic 1] Greenfield scaffolding per the spine's minimal tree: backend/app/{api, core, store, pipeline, providers, media} + tests/, frontend/src/, deploy/ (config.toml, caddy, systemd units, backup cron + restore script).
AR2:  Single-machine deployment: one FastAPI process, one SQLite file, one media directory, local model servers, Caddy in front (AD-8, NFR 1).
AR3:  One event-sourced graph-of-record per campaign; every world-state change lands through the store's transactional commit path producing exactly one new revision; no component outside store/ writes world state (AD-1).
AR4:  Atomic subgraph commits; per-transaction undo; regenerating an existing entity preserves its ULID (inbound edges, media, references survive; edge re-targeting forbidden); visible rebase-or-reject on concurrent DM edit (AD-2).
AR5:  Single persistent FIFO queue for all generation jobs; exactly one job at a time on the 3090; queue position visible via REST + WebSocket; no bypass path; jobs idempotent by job-id (AD-3).
AR6:  Retrieval over the graph only: bounded neighborhood traversal of typed edges (depth cap, entity cap — seed 24 entities); no embeddings, similarity, or full-text search; same state + same request ⇒ same prompt (AD-4, AD-16).
AR7:  Candidates staged as proposed DB rows, invisible to retrieval and export until accepted; each proposal all-or-nothing (partial failure rolls back the whole candidate) (AD-15).
AR8:  Closed, directed edge vocabulary; each edge carries a per-type counter (debt = amount, grudge/loyalty = score, ally/enemy = intensity); counters change only via commits; zero dangling edges after every commit (AD-5, AD-23).
AR9:  All LLM/image/video generation through provider adapters speaking OpenAI-compatible HTTP; local→OpenRouter is a config change; local engines include llama-server and LM Studio (AD-6, NFR 8).
AR10: Host LLM: abliterated 14–20B (24GB VRAM cap, Q4/Q5) in an on-host OpenAI-compatible server (seed llama-server; LM Studio or any OpenAI-compatible engine); pipeline talks HTTP only (AD-14, NFR 7). Host = developer machine or GPU VPS.
AR11: One SQLite database (WAL) holds current revision tables, append-only event log, job queue, media manifest, and accounts; single writer (API process); no raw SQL outside the store layer (AD-13). DECISION 2026-08-23: Postgres rejected — the audit/versioning history requirement is satisfied by the event-sourced revision log; single-machine cost argues against a second database.
AR12: Media at media/{campaign_id}/{entity_id}/, tracked in the DB manifest, written only by the media service, reclaimed on entity deletion, reference-validated on export (AD-10).
AR13: Nightly snapshot via the SQLite backup API (VACUUM INTO or wal_checkpoint + copy — never a raw WAL-file copy) + media manifest to a second local location; restore script exercised before beta launch (AD-12, NFR 5).
AR14: Phase-1 auth: local email + password, httpOnly session cookie (Secure, SameSite=Lax, path /api); every campaign belongs to one invited user and is private (AD-9, NFR 6).
AR15: Wire contract: backend Pydantic schemas are the single source of truth; frontend derives TS types from generated OpenAPI; ULID IDs (prefixed in logs only); UTC ISO-8601; error envelope {code, message, details?}; 4xx = user error (never a state change), 5xx = server error; WebSocket progress messages {type: job_progress|job_done|job_failed|queue_changed, job_id, state, queue_position?, progress?} (AD-17).
AR16: Frontend: Vue 3 + Vite + TypeScript + Pinia; views per capability; world-state in Pinia stores; Phase-2 visualizer reads Pinia only — no direct API calls, no private caches (AD-20, NFR 12).
AR17: Campaign time: each campaign declares a time granularity (day/month/year/century) + calendar label; no wall-clock value enters simulation input (AD-18).
AR18: Export is read-only from the latest revision; stat blocks follow the SRD 5.1 field set (level for NPCs, CR for monsters); Owlbear export = image URL + stat-block text (AD-11).
AR19: Candidate shape contract: every generated entity candidate carries name, role, personality, secret/rumor/party-hook triple, 5e stat block, and ≥1 typed edge into existing world state; acceptance validates the existing-world edge before commit (AD-24).
AR20: Whole-campaign deletion is a hard delete through the store with explicit confirmation — revisions, event log, queue entries, media-manifest rows, and the campaign's media directory (AD-25, NFR 10).
AR21: Config: one deploy/config.toml + environment variables for secrets; no config in the client build; each generation job declares a max LLM/media call budget from config — exceeding it fails the job (AD-22).
AR22: Testing: store + pipeline carry unit tests for commit/undo/retrieval with deterministic fixtures; no UI e2e in beta (owner dogfoods).
AR24: [New 2026-08-23] Canonical sectioned character model. Identity anchor (name; role NPC|BBEG|Monster; level/CR; race/type; class/profession; alignment), narrative-lore sections (appearance — painter-grade: face, body, clothing, scars, marks; personality; background; goals; relationships; hooks; voice style; catchphrases; hidden knowledge), mechanics block (attributes, combat stats, proficiencies, equipment, spells, actions, traits), conditional boss section (lair actions, legendary actions, immunities, vulnerabilities), world-integration block (reputation, factions, current location, reaction matrix, on_defeat). Sections are the regeneration unit; the canonical record is stored as structured JSON keyed by section so the shape can evolve. **Forward compatibility:** every consumer (export, UI, KnowledgeProvider) tolerates unknown sections — render known, skip unknown, never fail. All sections live in the structured canonical entity record in the SQLite store (AR11); the candidate shape contract (AR19) is the minimum admission, AR24 the full canonical form.
AR25: [New 2026-08-23] KnowledgeProvider + 5e reference data. Local reference tables (classes, races, spells, SRD 5.1 stat caps) queried by the pipeline; constraint enforcement (e.g. role=Wizard limits spells to the wizard list or relevant subset); invalid generated stats are flagged and get exactly one bounded repair pass (repair passes count against the AR21 job budget).

AR28: [Hardening 2026-08-23] Job volume control: per-campaign cap on in-flight + queued generation jobs; bounded queue depth; cancel API with a matching `job_cancelled` WebSocket message (extends AD-3, AR5, AR15).
AR29: [Hardening 2026-08-23] Auth hardening: argon2id password hashing; login rate limit with lockout; a single generic 401 (no user enumeration); session expiry + revocation; the API binds loopback-only behind Caddy (extends AR14, AR2).
AR30: [Hardening 2026-08-23] Backup integrity: checksum written at snapshot creation; restore is verify-then-apply; gate criterion — a corrupted snapshot must fail loudly, a clean snapshot must verify against its checksum (extends AR13; Epic 6 gate).
AR26: [New 2026-08-23] Section-level regeneration. Any AR24 section can be regenerated independently; the system sends the *rest* of the character's sections as context to keep the new section consistent (deterministic-prompt invariant AR6 applies); each regeneration is one transaction — the previous revision is the undo (AR4, NFR3). Regenerating one section never rewrites the others.
AR27: [New 2026-08-23] World seed model. A world/campaign is created with title, description (how it behaves, its history), theme chosen from an open, user-extendable list (seed: High Fantasy, Grimdark, Steampunk, Planar), custom lore/rules string, created_at, owner; all entities belong to exactly one world. Theme + custom lore flow into build-in and generation prompts.
```

### UX Design Requirements

None — no UX design exists for this project (owner dogfoods the UI; see AR22).

### FR Coverage Map

```
FR1:  Epic 2 - world as typed graph (build-in output)
FR2:  Epic 2 - generated entities woven into existing edges
FR3:  Epic 2 - closed directional edge vocabulary enforced on commit
FR4:  Epic 2 - cascade confirmation on entity deletion, zero dangling edges
FR5:  Epic 2 - world state exportable as JSON + Markdown
FR6:  Epic 2 - guided world-build-in flow (theme/custom-lore aware, AR27)
FR7:  Epic 3 - plain-language ask → 2–3 candidates
FR8:  Epic 3 - candidate content incl. 5e stat block (validated per AR25)
FR9:  Epic 3 - inline relation editing
FR10: Epic 3 - regenerate whole or per-section (AR26), hand-edit
FR11: Epic 3 - candidate lifecycle: proposed → accepted/rejected, only accepted commits
FR12: Epic 4 - portrait image per entity
FR13: Epic 4 - fast-tier short video (beta, not launch priority)
FR14: Epic 4 - media reclaimed on delete, export validation
FR15: Epic 7 - node-link visualizer (Phase 2 boundary)
FR16: Epic 5 - VTT exports (Markdown, Owlbear, MapTool, Fantasy Grounds)
FR17: Epic 5 - stat block + portrait embedded, table-ready
FR18: Epic 5 - export validation failure logged as event
FR19: Epic 8 - session logger (Phase 3 boundary)
FR20: Epic 8 - Simulate History (Phase 3 boundary)
```

## Epic List

```
Epic 1: The Forge — Foundation & Substrate
Epic 2: Bring the World In — Guided Build-In
Epic 3: Living Candidates — Generation & Acceptance
Epic 4: Give Them a Face — Media
Epic 5: Take It to the Table — Export & VTT
Epic 6: Protect the Table — Backup, Restore & Ownership (BETA LAUNCH GATE)
Epic 7: See the Web — Relationship Graph (Phase 2, boundary-level)
Epic 8: The World Reacts — Simulate History (Phase 3, boundary-level)
```

## Value Checkpoints

The plan's load-bearing assumptions get gauges, not faith. Each checkpoint is a named story-level gate; a failed checkpoint stops the next epic and names its suspect.

| Checkpoint | Criterion | Suspect on failure |
|---|---|---|
| End of Epic 2 | The build-in world is actually played (Wren runs a scene from it); the NFR2 retrieval-relevance proxy is logged | The build-in prompt contract (AR27 theme/custom-lore flow), not the store |
| End of Epic 3 | Candidate acceptance rate ≥ ~50%; NFR2 proxy re-logged; the DM records a playable-at-the-table verdict (qualitative, DM-owned) alongside the rate | The generation contract (AR19 shape, AR6 retrieval seed, prompt — including AR27 theme/custom-lore weight) — not the UI |
| End of Epic 5 | The ten-minute world→VTT demo closes (Owlbear unit file + portrait) **and a single-character export closes in under a minute, and a second human has watched the world reach the table** (two-eyes rule) | The export path (AR18, target ordering) — the "for online tables" claim otherwise |
| End of Epic 6 | Corrupted-snapshot restore fails loudly; clean restore verifies (AR30) | The backup/restore implementation, not the store |
| End of Epic 2 & 3 | P95 generation job duration logged (core build-in, candidates); perceived-wait gauge — "30 seconds" is the market's promise (Tabletop Arc) | The model/quant decision (AR10) — not the UI; core-first build-in (Epic 2) is the first bandage |


## Epic 1: The Forge — Foundation & Substrate

**Value:** the DM logs in to the hosted site and creates a private world with a title, description, theme, and custom lore — the substrate she will build everything on already works: versioned store, persistent queue, swappable inference on the operator's host.
**FRs covered:** (substrate) — NFR1, 8, 9, 12; AR1, 2, 3, 4, 5, 9, 10, 11, 14, 15, 17, 21, 22, 23, 27
**Notes:** Story 1 = scaffolding (AR1). Inference is OpenAI-compatible-HTTP from day one (AR9) — LM Studio or llama-server, swappable by config. Campaign CRUD with the world seed model (AR27). Auth + session cookie (AR14). Conventions (AR23) and unit-test harness (AR22) land here so later epics build on green. **Design rule (inversion guarantee #3):** nothing on the path of play blocks on generation — jobs are backgrounded with job ID, queue position, and completion notification (AR5, AR28); the spinner is the enemy.

### Story 1.1: Repo Scaffold with Conventions and Green Test Harness

As a **operator** deploying on my own rig or a GPU VPS,
I want a runnable repo skeleton with the shared conventions and a green unit-test harness,
So that every later story lands on a working, consistent codebase.

**Acceptance Criteria:**

**Given** a fresh clone
**When** I run the project's standard test command
**Then** the unit-test harness (backend pytest, frontend vitest) runs and passes.

**Given** any new code being added
**When** it is integrated
**Then** the conventions hold: ULID IDs, UTC ISO-8601 timestamps, the error envelope `{code, message, details?}`, 4xx = user error, 5xx = server error (AR23, AR15).

**Given** the scaffold
**When** the tree is inspected
**Then** it contains `backend/app/{api,core,store,pipeline,providers,media}`, `tests/`, `frontend/src/`, and `deploy/` (config.toml, Caddyfile, systemd units, backup cron + restore script) (AR1, AR22).

### Story 1.2: Versioned World Store

As a **DM**,
I want my world state versioned with atomic subgraph commits and per-transaction undo,
So that nothing ever silently corrupts and every edit of mine is recoverable.

**Acceptance Criteria:**

**Given** a campaign graph
**When** a subgraph is committed
**Then** exactly one new revision exists and the commit is all-or-nothing (a partial failure rolls back the whole subgraph) (AR3, AD-1).

**Given** a committed transaction
**When** the DM undoes it
**Then** the previous revision is restored, the entity ULID is stable, and inbound edges and media references survive (AR4).

**Given** a pending commit
**When** a concurrent DM edit lands
**Then** a visible rebase-or-reject occurs and no silent overwrite happens (AD-2).

**Given** any commit
**When** world state is written
**Then** the store layer is the sole writer (AD-1), there is no raw SQL outside the store (AD-13), and zero dangling edges remain (AD-23).

### Story 1.3: Persistent Generation Queue

As a **DM**,
I want to queue generation jobs with a visible queue position and the ability to cancel,
So that I can run the world without babysitting a single GPU.

**Acceptance Criteria:**

**Given** a job submitted via REST
**When** it is queued
**Then** exactly one job runs at a time and its queue position and state are visible via REST + WebSocket (`job_id`, state, queue_position) (AR5, AD-17).

**Given** an in-flight or queued job
**When** the DM cancels it
**Then** a `job_cancelled` message is emitted and the queue slot is freed (AR28).

**Given** a per-campaign cap
**When** in-flight + queued jobs exceed it
**Then** the enqueue is rejected (AR28).

**Given** a server restart
**When** the process returns
**Then** the pending queue is restored from the store and no job is lost (AR11).

### Story 1.4: OpenAI-Compatible Inference Adapter

As a **operator**,
I want generation to speak to any OpenAI-compatible HTTP server (llama-server, LM Studio, Ollama) through configuration,
So that I run on my own rig or a GPU VPS and swap engines without a code change.

**Acceptance Criteria:**

**Given** a config pointing at a local llama-server
**When** a queued generation job runs
**Then** the LLM is called over HTTP only and completion is reported via `job_done` (AD-14, AR9).

**Given** a job that declares a max LLM-call budget
**When** the job would exceed it
**Then** the job fails with an error event rather than running unbounded (AR21).

**Given** a config change swapping the endpoint (e.g. LM Studio or Ollama)
**When** I redeploy
**Then** no code change is required (NFR8, AR9).

### Story 1.5: DM Authentication

As a **DM**,
I want to log in with email + password and be recognized across requests,
So that my campaign stays private to me over TLS.

**Acceptance Criteria:**

**Given** a registered account with a valid password
**When** I log in
**Then** an httpOnly, Secure, SameSite=Lax session cookie is set on path `/api` (AR14).

**Given** a password being stored
**When** it is written
**Then** it is hashed with argon2id (AR29).

**Given** repeated failed logins
**When** the rate limit is hit
**Then** the account is locked out and every auth failure returns a single generic 401 that does not reveal which users exist (AR29).

**Given** the host network
**When** the API binds
**Then** it is loopback-only behind Caddy (AR29, AR2).

**Given** an expired or revoked session
**When** I call the API
**Then** the session is rejected (AR29).

### Story 1.6: Private World Creation (Campaign CRUD with Seed)

As a **DM**,
I want to create a private world with a title, description, theme, and custom lore,
So that I have a home to pour my campaign into.

**Acceptance Criteria:**

**Given** I am logged in
**When** I create a world with a title, description, a theme from the open user-extendable list (seed: High Fantasy, Grimdark, Steampunk, Planar), and a custom-lore string
**Then** the campaign is created and is private to me (AR27, AD-9, NFR6).

**Given** an existing world
**When** I list, update, or delete it
**Then** only my own worlds are visible and deletion is a total hard delete with confirmation (AR20).

**Given** a world with a theme + custom lore
**When** it is saved
**Then** those values are persisted to flow into the Epic 2 build-in and generation prompts (AR27).

### Story 1.7: Deploy to the Operator's Host

As a **operator**,
I want a single-command deploy (Caddy TLS in front, systemd service, backup cron),
So that the DM can reach the site over HTTPS.

**Acceptance Criteria:**

**Given** the deploy config
**When** I run the deploy
**Then** Caddy terminates TLS and the FastAPI service runs under systemd (AD-8, AR1).

**Given** the deployed instance
**When** the DM opens the site in a browser
**Then** the login screen is served over HTTPS and the health check passes (NFR1).

**Given** the deploy
**When** the schedule runs
**Then** the nightly backup cron is registered (AR1; snapshot + restore proof lands in Epic 6).

## Epic 2: Bring the World In — Guided Build-In

**Value:** the DM walks the guided build-in — key places, factions, key figures — plus her notes and custom lore, and the LLM digests it into the versioned graph: typed edges, no orphans, exportable as JSON + Markdown.
**FRs covered:** FR1–FR6
**Notes:** build-in is a queued generation job (AD-19, AR5); **core-first build-in:** key figures + their edges generate and commit first (the world is visible in minutes), the rest of the world streams behind on the same FIFO queue — one world, two waves; **key figures carry a minimal 5e stat block (AR25-validated) so the world is playable at the first visible moment**; retrieval for later generation starts here (AR6); candidates staged invisible until accepted (AR7); edge vocabulary + counters enforced on commit (AR3, AR8); cascade delete (FR4); state export (FR5, AR18-read-only). **Exportable invariant (enforced at commit):** every committed entity satisfies the SRD 5.1 field set + a valid portrait reference (AR25, FR2) — so all later exports are guaranteed projections, not a place where validation happens (FR18). **First visible moment (last story):** the core build-in world renders on screen — nodes, edges, her barkeep, stats ready to roll initiative — this is the "it's real" moment; the story step must name it.

### Story 2.1: Guided Build-In Flow

As a **DM**,
I want a guided build-in that carries my world seed (title, description, theme, custom-lore) plus key places, factions, key figures, and free-form notes,
So that my campaign's shape is captured without me learning a schema.

**Acceptance Criteria:**

**Given** a created world (Story 1.6)
**When** I open the build-in
**Then** the world-seed fields are pre-filled from the campaign (title, description, theme, custom-lore) and I add key places, factions, key figures, and free-form notes (FR6, AR27).

**Given** completed build-in inputs
**When** I submit
**Then** a generation job is enqueued and the screen is not blocked — the job runs in the background with visible position and progress (AD-19, AR5; design rule).

### Story 2.2: Typed Edge Vocabulary on Commit

As a **DM**,
I want every relation in my world to be a typed edge from a closed, directional vocabulary,
So that the world is queryable, game-state-ready, and never free-text mush.

**Acceptance Criteria:**

**Given** a generated edge
**When** it is committed
**Then** its type is from the closed directional set (relationship, debt, grudge, loyalty, member_of, located_in, rival_of, kin_of, ally_of, enemy_of — extensible by adding a type, never free text) (FR3, AR3).

**Given** a free-form relation label
**When** it is persisted
**Then** it is never stored as free text (FR3).

**Given** a per-type counter (debt = amount, grudge/loyalty = score, ally/enemy = intensity)
**When** the counter changes
**Then** it changes only via a commit, and zero dangling edges remain (AR8, AD-23).

### Story 2.3: Core-First Two-Wave Build-In Pipeline

As a **DM**,
I want my key figures and their edges to exist first, with the rest of the world streaming in behind,
So that the world is visible and playable in minutes, not after a long build.

**Acceptance Criteria:**

**Given** the build-in notes
**When** the core wave runs
**Then** key figures + their typed edges generate and commit before any second-wave entity (AR5).

**Given** the same world state + the same request
**When** the prompt is built
**Then** it is byte-identical (deterministic-prompt invariant, AR6, AD-4, AD-16).

**Given** a build-in job that declares a max LLM-call budget
**When** it would exceed the budget
**Then** the job fails with an error event (AR21).

### Story 2.4: Key Figures Carry a Minimal 5e Stat Block

As a **DM**,
I want each key figure to arrive with a minimal, valid 5e stat block,
So that the world is playable at the first visible moment.

**Acceptance Criteria:**

**Given** a key figure
**When** its stat block is generated
**Then** it follows the AR24 section shape and passes AR25 constraint checks (role-limited, SRD 5.1 field set) — or exactly one bounded repair pass.

**Given** a stat block still invalid after the repair pass
**When** it is committed
**Then** it is not committed; a fail event is logged (AR25).

### Story 2.5: No Orphans and Cascade Delete

As a **DM**,
I want every generated entity woven into existing edges, and a safe cascade delete,
So that the world grows coherently and I can remove things without leaving debris.

**Acceptance Criteria:**

**Given** a generated entity
**When** it commits
**Then** it carries at least one typed edge into existing world state — no orphans (FR2).

**Given** an entity with live edges
**When** I delete it
**Then** an explicit cascade confirmation lists the affected entities, and after the commit zero dangling edges remain (FR4, AD-23).

### Story 2.6: World State Export

As a **DM**,
I want my world state exportable as JSON + Markdown at any time,
So that I own it and can carry it anywhere.

**Acceptance Criteria:**

**Given** any world state
**When** I export
**Then** I get JSON + Markdown, read-only from the latest revision (export never mutates state) (FR5, NFR10, AR18).

**Given** the Markdown export
**When** it is inspected
**Then** it matches Obsidian-level completeness — the no-lock-in claim depends on it (Epic 5 dependency).

### Story 2.7: First Visible Moment

As a **DM**,
I want to see my world on screen — nodes, typed edges, my barkeep, stats ready to roll initiative — as soon as the core wave commits,
So that the tool feels real at the first visible moment.

**Acceptance Criteria:**

**Given** a committed core wave
**When** the DM opens the world view
**Then** nodes, typed edges, and the barkeep's stat block render (FR1).

**Given** a second-wave entity that commits
**When** it lands
**Then** it appears in the world view without a reload, via WebSocket progress (NFR9).

## Epic 3: Living Candidates — Generation & Acceptance

**Value:** the DM asks in plain language and gets 2–3 candidates woven into her world; she picks, regenerates any section with consistency context, wires relations inline, and accepts — only what she accepts becomes real.
**FRs covered:** FR7–FR11
**Commitments:** section regeneration (AR26) is a story in this epic, with acceptance criteria: regenerate one section with the rest-of-character data in context; the previous revision is the undo; the other sections are byte-identical. The accept screen renders the full sectioned profile (AR24) — appearance, voice, secret — so acceptance is on substance, not name + stats. **Reject and edit must be at least as easy as accept** — one-tap accept is not the path of least resistance (inversion guarantee #2). KnowledgeProvider + 5e constraint enforcement + bounded repair pass (AR25); deterministic prompt invariant (AR6); counter semantics (AR8); candidate shape (AR19).

### Story 3.1: Plain-Language Ask Returns 2–3 Candidates

As a **DM**,
I want to ask for an entity in plain language and get 2–3 candidates woven into my world,
So that the world grows with each use, not with schema labor.

**Acceptance Criteria:**

**Given** a plain-language ask plus world state
**When** the ask is submitted
**Then** 2–3 candidates return, each carrying the AR19 shape: name, role, personality, secret/rumor/party-hook triple, an AR25-validated 5e stat block, and at least one typed edge into existing world state (FR7, FR8, AR19).

**Given** a candidate
**When** it is staged
**Then** it is a *proposed* DB row, invisible to retrieval and export until accepted (AR7).

**Given** the same world state + the same ask
**When** the prompt is built
**Then** it is byte-identical (AR6).

### Story 3.2: Candidate Lifecycle and Atomic Commit

As a **DM**,
I want candidates to be accepted or rejected, with only what I accept becoming real,
So that accepted state is never mutated by a regenerate or a failed candidate.

**Acceptance Criteria:**

**Given** a proposed candidate
**When** it is accepted
**Then** its subgraph commits as exactly one atomic transaction — a partial failure rolls back the whole candidate (FR11, AR7, AD-15).

**Given** a proposed candidate
**When** it is rejected
**Then** accepted state is untouched and the candidate is dropped.

**Given** an existing accepted entity
**When** a new ask generates a fresh candidate for it
**Then** a new candidate is staged and the accepted entity is never mutated (FR11).

### Story 3.3: Accept Screen on Substance

As a **DM**,
I want the accept screen to show the full sectioned profile and keep reject/edit as easy as accept,
So that I accept on substance, not on name + stats, and the tool never nudges me toward "accept all."

**Acceptance Criteria:**

**Given** a candidate
**When** the accept screen renders
**Then** the full AR24 sectioned profile is shown — appearance, personality, voice, secret, mechanics, world-integration (AR24).

**Given** the accept screen
**When** I act
**Then** reject and edit are at the same tap depth as accept — one-tap accept is not the path of least resistance (inversion guarantee #2).

### Story 3.4: Inline Relation Editing

As a **DM**,
I want to add, edit, or delete relations to any entity from a character's screen,
So that the web of the world is shaped by me, in the closed vocabulary.

**Acceptance Criteria:**

**Given** a character's screen
**When** I add a relation
**Then** it is a typed edge from the closed directional vocabulary to any existing entity (FR9, AR3).

**Given** an existing relation
**When** I edit or delete it
**Then** the change commits through the store, per-type counters change only via commits, and zero dangling edges remain (AR8, AD-23).

### Story 3.5: Regeneration — Whole and Per-Section

As a **DM**,
I want to regenerate a whole character or any single section,
So that I can re-roll what I don't like without disturbing the rest.

**Acceptance Criteria:**

**Given** an accepted character
**When** one section is regenerated
**Then** the rest of the character's sections enter the prompt as context (deterministic, AR6), the previous revision is the undo, and the other sections are byte-identical (AR26, AR4).

**Given** a whole-character regeneration
**When** it completes
**Then** it lands as one transaction and the prior revision is the undo (FR10, AR4).

### Story 3.6: Hand Editing — the DM Is the Final Author

As a **DM**,
I want to hand-edit any field or section directly,
So that my word always beats the LLM's.

**Acceptance Criteria:**

**Given** any field or section
**When** I edit it
**Then** the edit persists as a committed transaction and the DM's edit always beats the LLM's (FR10, NFR2).

**Given** a hand edit landing during a pending commit
**When** the two collide
**Then** a visible rebase-or-reject occurs, and undo restores the prior revision (AR4).

## Epic 4: Give Them a Face — Media

**Value:** every accepted entity has a painter-grade portrait; the BBEG reveal gets a fast-tier short video during beta.
**FRs covered:** FR12–FR14
**Notes:** media service writes through the manifest (AR12); appearance section (AR24) is the portrait prompt source; fast-tier video = not a launch priority (FR13); reclaim-on-delete + export validation (FR14).

### Story 4.1: Portrait Generation for Accepted Entities

As a **DM**,
I want every accepted entity to arrive with a painter-grade portrait,
So that the world has a face the moment it's real.

**Acceptance Criteria:**

**Given** an accepted entity
**When** portrait generation runs
**Then** the AR24 appearance section (face, body, clothing, scars, marks) is the prompt source, and the image is written through the media manifest at `media/{campaign_id}/{entity_id}/` (FR12, AR24, AR12).

**Given** a generated portrait
**When** it completes
**Then** it is tracked in the DB manifest and renders on the entity's screen (AR12).

### Story 4.2: BBEG Fast-Tier Short Video (Beta, Not a Launch Priority)

As a **DM**,
I want a fast-tier short video for the BBEG reveal,
So that the big moment lands with impact during beta.

**Acceptance Criteria:**

**Given** a BBEG-role entity
**When** a reveal video is requested
**Then** a fast-tier local video is generated and attached through the media manifest (FR13, AR12).

**Given** the beta scope
**When** this story is scoped
**Then** the video is fast-tier and local only, and is explicitly not a launch priority (FR13).

### Story 4.3: Reclaim-on-Delete and Export Media Validation

As a **DM**,
I want media reclaimed when an entity is deleted and validated on export,
So that nothing leaks or dangles.

**Acceptance Criteria:**

**Given** an entity with attached media
**When** the entity is deleted
**Then** its media files are reclaimed and the manifest rows removed (FR14, AR12, AD-10).

**Given** an export
**When** media references are checked
**Then** references are validated and any broken reference is flagged (FR14, AR12).

## Epic 5: Take It to the Table — Export & VTT

**Value:** the DM takes a piece of the living world to the table — Markdown, Owlbear, MapTool (RPGToken JSON + portrait), Fantasy Grounds — **one character in under a minute**, table-ready and format-validated. The character is *of* the world: it carries the guild, the grudge, the secret it was woven into. The world stays alive in the site (generate more NPCs off it, run Simulate History) — the export is the bridge, not the goodbye.
**FRs covered:** FR16–FR18
**Notes:** read-only from latest revision (AR18); **export is a pure projection of committed state** — the exportable invariant (SRD 5.1 field set + valid portrait reference, enforced at commit via AR25/FR2) means export carries no validation gate of its own; a format assertion failing at export time = a commit-path regression, logged as an export-failure event (FR18). Full-world JSON+Markdown per AD-11; Markdown at Obsidian-level completeness. **Openness is the moat** (the Obsidian principle — open, portable, extractable in seconds): the DM *uses* the tool as needed, doesn't live in it; every entity is out the door in under a minute, to a VTT or a plain Markdown/PDF sheet. Arc keeps users in the app (gated edits, no VTT export, credit-metered); ours is a tool that *lets them leave*. **Target order (locked):** Owlbear first, Fantasy Grounds second (XML), MapTool/RPGToken third; Roll20 = Phase 2. **Kill criterion:** full world-to-VTT demo (build-in → candidate → accept → export → Owlbear/MapTool screen) closes in under ten minutes, **and a second human has watched the world reach the table** (two-eyes rule, inversion guarantee #1); a single-character export closes in under a minute; if it can't, the "for online tables" claim is withdrawn and Epic 5 moves to Phase 2.

### Story 5.1: Export Engine — Pure Projection

As a **DM**,
I want any entity — or the whole world — exportable as Markdown or JSON,
So that a piece of the living world is out the door in seconds.

**Acceptance Criteria:**

**Given** a committed entity (or the whole world)
**When** it is exported to Markdown or JSON
**Then** the export is a pure projection of committed state — SRD 5.1 field set, stat block, portrait reference, and typed edges render exactly as committed (AR18, AD-11).

**Given** a committed entity
**When** an export format assertion fails
**Then** that is a commit-path regression: an export-failure event is logged — export never mutates state and never re-validates (FR18; the exportable invariant is enforced at commit).

**Given** the Markdown export
**When** it is inspected
**Then** it matches Obsidian-level completeness — the no-lock-in claim depends on it.

### Story 5.2: Owlbear Export (First Target)

As a **DM**,
I want a single character to export to Owlbear as a ready unit,
So that my barkeep lands in the VTT in under a minute.

**Acceptance Criteria:**

**Given** a single committed character
**When** the Owlbear export runs
**Then** a valid JSON "unit collection" + portrait URL is produced in under a minute (FR16, FR17, AR18, AD-11).

**Given** the produced file
**When** it is dropped into Owlbear
**Then** the entity appears table-ready with stat block and portrait.

### Story 5.3: Fantasy Grounds Export (Second Target)

As a **DM**,
I want a character to export to Fantasy Grounds as XML,
So that the world works on a second VTT without re-typing.

**Acceptance Criteria:**

**Given** a committed character
**When** the Fantasy Grounds export runs
**Then** valid XML with stat block + portrait is produced (FR16, FR17, FR18).

### Story 5.4: MapTool / RPGToken Export (Third Target)

As a **DM**,
I want a character to export to MapTool as an RPGToken token,
So that a third VTT is covered.

**Acceptance Criteria:**

**Given** a committed character
**When** the MapTool/RPGToken export runs
**Then** valid RPGToken JSON + portrait is produced (FR16, FR17, FR18).

### Story 5.5: World→VTT Kill-Criterion Demo

As a **DM** — with a second human watching,
I want the full world→VTT path timed end-to-end,
So that the "for online tables" claim is proven, not asserted.

**Acceptance Criteria:**

**Given** the full path (build-in → candidate → accept → export → Owlbear/MapTool screen)
**When** it is timed
**Then** it closes in under ten minutes, and the single-character export in under a minute (kill criterion).

**Given** the two-eyes rule
**When** the demo completes
**Then** a second human has watched the world reach the table (inversion guarantee #1).

**Given** the demo exceeding the time budget
**When** it fails
**Then** the "for online tables" claim is withdrawn and Epic 5 moves to Phase 2.

## Epic 6: Protect the Table — Backup, Restore & Ownership — **BETA LAUNCH GATE**

**Value:** the DM's world is private to her, backed up nightly, and the restore has been *proven*; a campaign deletion is total and clean. Beta does not ship until this epic's restore test passes.
**FRs covered:** NFR5, NFR6 (privacy/TLS half), NFR10
**Notes:** nightly backup-API snapshot (AR13) + tested restore script (gate criterion); total campaign deletion (AR20); per-campaign ownership + TLS (AR14, AD-8 Caddy). Gate: restore exercised against a real campaign snapshot before any beta user touches the tool. Scope: the gate blocks *sharing* (beta), not the owner's own dogfooding — the owner is the first user from the end of Epic 2.

### Story 6.1: Nightly Snapshot with Integrity

As a **DM**,
I want my world snapshotted nightly to a second local location with a checksum,
So that a crash or a bad day never erases my campaign.

**Acceptance Criteria:**

**Given** the schedule
**When** the snapshot runs
**Then** the world state is captured via the SQLite backup API (VACUUM INTO / wal_checkpoint + copy — never a raw WAL-file copy) and the media manifest lands at a second local location, with a checksum written at snapshot creation (AR13, AR30).

### Story 6.2: Restore Proven — Verify-Then-Apply

As a **DM**,
I want restore to verify before it applies, and to fail loudly on a bad snapshot,
So that I never restore into a silently broken world.

**Acceptance Criteria:**

**Given** a corrupted snapshot
**When** restore runs
**Then** it fails loudly with no partial or corrupt state (AR30, NFR5).

**Given** a clean snapshot
**When** restore runs
**Then** it verifies the checksum first, then applies, and restores a fully working world (AR30, NFR5).

### Story 6.3: Total, Clean Campaign Deletion

As a **DM**,
I want deleting a campaign to be total and clean,
So that "deleted" means actually gone.

**Acceptance Criteria:**

**Given** a campaign
**When** it is deleted with explicit confirmation
**Then** revisions, event log, queue entries, media-manifest rows, and the media directory are all hard-deleted through the store — no trace remains (AR20, NFR10).

### Story 6.4: Privacy and Per-Campaign Ownership

As a **DM**,
I want my campaign private to me, over TLS,
So that the world is mine until I choose to share it.

**Acceptance Criteria:**

**Given** a non-owner
**When** they attempt to access a campaign
**Then** access is denied as if it doesn't exist — no user or campaign enumeration (NFR6, AR14).

**Given** the deployed instance
**When** it is reached
**Then** it is served over TLS via Caddy, and one owner per campaign holds at beta (AD-8, NFR6).

### Story 6.5: Beta Launch Gate

As a **DM**,
I want beta to ship only after the restore has been proven on a real snapshot,
So that "beta" means the world is actually safe.

**Acceptance Criteria:**

**Given** the beta launch
**When** the gate is checked
**Then** beta is permitted only if restore has been exercised against a real campaign snapshot, a corrupted snapshot has failed loudly, and a clean restore has verified against its checksum — otherwise it is blocked (gate; AR13, AR30, NFR5).

## Epic 7: See the Web — Relationship Graph *(Phase 2, boundary-level)*

**Value:** the DM browses the web around any entity — "barkeep → thieves' guild → mayor" — the product's signature demo moment.
**FRs covered:** FR15
**Notes:** node-link visualizer reads Pinia only, no direct API calls (AR16); library choice (Cytoscape.js vs Vue Flow) decided at P2 build; spike early — the demo moment, not a backlog item.

### Story 7.1: Graph Visualizer Spike *(Phase 2 boundary)*

As a **DM**,
I want to click any entity and see its web of relationships rendered,
So that "barkeep → thieves' guild → mayor" is a live demo moment, not a screenshot.

**Acceptance Criteria:**

**Given** a Phase-1 world
**When** an entity is clicked
**Then** its typed-edge web renders within the AR6 depth/entity caps (FR15).

**Given** the visualizer
**When** it reads state
**Then** it reads Pinia only — no direct API calls, no private caches (AR16, AD-20).

**Given** the spike
**When** it concludes
**Then** the visualizer library is chosen (Cytoscape.js vs Vue Flow, NFR12) — the demo moment, not a backlog item.

## Epic 8: The World Reacts — Simulate History *(Phase 3, boundary-level)*

**Value:** the barkeep's secret pays off in act three — the world computes deterministic consequences; the LLM only narrates.
**FRs covered:** FR19, FR20
**Notes:** session logger (FR19) is P3 input infrastructure — do not skip it; Simulate History (FR20) is a door with a spec keyhole: requires the P1 edge schema + action-log schema to exist and a Phase-3 spec before stories are written. **Tabletop Arc ships the session layer today, free** (audio → transcript → lore extraction → timestamped facts → player-safe/GM-private recaps): FR19 is parity, not frontier — the differentiators that survive are mechanics + versioning (their ledger is narrative entities with confidence; ours is game state with counters) and the ten-minute world→VTT path (their lock-in is our passport). Watch quarterly: their public campaign pages + player collaboration (post-beta consideration, NFR6 says private at beta).

### Story 8.1: Session Logger — Schema and Commit Path *(Phase 3 boundary, do not skip)*

As a **DM**,
I want a typed timeline of player actions (actors, targets, campaign time, note) committing to the world,
So that what happened at the table flows back into the living world.

**Acceptance Criteria:**

**Given** a logged player action
**When** it commits
**Then** it carries actors, targets, campaign time (the campaign's declared granularity — never wall-clock), and a note (FR19, AD-18).

**Given** the action log
**When** it is written
**Then** it lands through the store's commit path as a new revision (AD-1).

### Story 8.2: Simulate History — A Door with a Spec Keyhole *(Phase 3 boundary)*

As a **DM**,
I want the world to compute deterministic consequences from logged actions, with the LLM only narrating,
So that the barkeep's secret pays off in act three.

**Acceptance Criteria:**

**Given** logged actions
**When** Simulate History runs
**Then** deterministic deltas are computed by the graph state machine — no LLM in the state path — and the LLM only narrates (FR20).

**Given** the DM-authored rules
**When** they apply
**Then** they apply over the closed Phase-1 edge vocabulary + a fixed starter rulebook (FR20).

**Given** the Phase-3 gate
**When** stories for this capability are to be written
**Then** a Phase-3 spec exists first (boundary gate).
