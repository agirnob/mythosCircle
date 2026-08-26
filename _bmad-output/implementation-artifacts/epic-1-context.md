# Epic 1 Context: The Forge — Foundation & Substrate

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 1 lays the substrate everything else in mythosCircle is built on: a versioned world store with atomic subgraph commits and per-transaction undo, a single persistent generation queue that never blocks the user, a config-swappable OpenAI-compatible inference adapter, DM auth, and private campaign CRUD around the world-seed model (title, description, theme, custom lore). By its end, the operator's DM can log in to a hosted site over TLS and create a private world on a working, consistent codebase with a green test harness — later epics add generation on top of this without rework.

## Stories

- Story 1.1: Repo scaffold with conventions and green test harness
- Story 1.2: Versioned world store
- Story 1.3: Persistent generation queue
- Story 1.4: OpenAI-compatible inference adapter
- Story 1.5: DM authentication
- Story 1.6: Private world creation (campaign CRUD with world seed)
- Story 1.7: Deploy to the operator's host

## Requirements & Constraints

- Single-machine deployment: one FastAPI process, one SQLite file, one media directory, a local LLM server, Caddy TLS in front; end users reach the site over HTTPS; zero per-token LLM cost (local model).
- World-state integrity: state is versioned; every change commits as one atomic subgraph transaction; per-transaction undo; a concurrent DM edit forces visible rebase-or-reject; no silent corruption of entities or edges.
- Throughput ceiling: one persistent FIFO generation queue — exactly one job at a time on the GPU, queue position visible, no bypass path.
- Swappable inference: no hard single-vendor dependency; local llama.cpp at MVP.
- Long-running generation runs asynchronously with progress, never blocking the UI.
- Accounts: email + password identity, per-campaign ownership, campaigns private, TLS end to end.
- Host LLM: abliterated 14–20B model, 24GB VRAM cap, Q4/Q5 quantization, served as an OpenAI-compatible HTTP server.
- Testing: store and pipeline carry unit tests with deterministic fixtures; no UI e2e in beta — the owner dogfoods the UI.

## Technical Decisions

- Stack: Python 3.12, FastAPI, SQLAlchemy 2 + SQLite (WAL), Pydantic v2; Vue 3.5 + Vite + TypeScript + Pinia; llama.cpp `llama-server`; Caddy 2.
- One SQLite (WAL) database holds current revision tables, the append-only event log, the job queue, the media manifest, and accounts. The store layer is the sole writer — no raw SQL outside it.
- Layering: `api/` orchestrates; `pipeline/` proposes; `store/` commits; `providers/` are leaves (no inbound deps except from pipeline/media); nothing else writes world state.
- Commit semantics: an atomic subgraph commits against the latest revision and produces exactly one new revision; all-or-nothing (partial failure rolls back the whole subgraph); per-transaction undo restores the previous revision; regenerating an existing entity preserves its ULID (inbound edges, media, references survive); a DM edit landing during a pending commit forces visible rebase-or-reject.
- Queue: one persistent FIFO; exactly one job at a time; position and state via REST + WebSocket; cancel API emits a `job_cancelled` message and frees the slot; per-campaign cap on in-flight + queued jobs; jobs idempotent by job-id; the pending queue survives process restart (restored from the store).
- Inference: all generation goes through OpenAI-compatible HTTP adapters; the endpoint is a config change (llama-server, LM Studio, Ollama); each job declares a max LLM/media call budget from config — exceeding it fails the job.
- Wire contract: backend Pydantic schemas are the source of truth; frontend TS types are generated from OpenAPI; entity/edge/job/event IDs are ULIDs; UTC ISO-8601 timestamps; error envelope `{code, message, details?}`; cursor pagination; 4xx = user error (never a state change), 5xx = server error; WebSocket messages `{type: job_progress|job_done|job_failed|queue_changed, job_id, state, queue_position?, progress?}`.
- Auth: email + password; argon2id hashing; httpOnly Secure SameSite=Lax session cookie on path `/api`; the API binds loopback-only behind Caddy; a single generic 401 (no user enumeration); login rate-limit with lockout; session expiry + revocation.
- World-seed model: a campaign is created with title, description, theme (open, user-extendable list; seed: High Fantasy, Grimdark, Steampunk, Planar), a custom lore/rules string, created_at, and owner; theme + custom lore are persisted to flow into later build-in and generation prompts.
- Config: one `deploy/config.toml` plus environment variables for secrets; no config in the client build.
- Naming: snake_case Python, kebab-case API routes, camelCase TS/Vue.

## UX & Interaction Patterns

- Nothing on the path of play blocks on generation: jobs are backgrounded with a job ID, visible queue position, and completion notification — the UI never spins waiting on a generation.

## Cross-Story Dependencies

- 1.1 lands first: the conventions and green test harness every other story builds on.
- 1.2 (store) precedes 1.3 and 1.6 — the queue and campaign data both live in the store.
- 1.4 (inference adapter) builds on 1.3 — generation jobs run through the queue.
- 1.5 (auth) gates 1.6 — campaign CRUD requires the DM's identity.
- 1.7 (deploy) lands last — wires the backend behind Caddy + systemd + backup cron.
- Epic 2 consumes 1.6's world-seed fields (build-in pre-fills title, description, theme, custom lore) and the queue.
