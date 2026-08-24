---
id: SPEC-mythosCircle
companions:
  - ../../planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md
  - architecture-diagrams.md
  - stack.md
  - conventions.md
  - metrics.md
  - glossary.md
sources:
  - ../../planning-artifacts/prds/prd-mythosCircle-2026-08-23/prd.md
---

# mythosCircle — Living-World NPC & World Generator for TTRPG DMs

## Why

Lazy-but-invested TTRPG DMs run long campaigns with a world that lives in their head and scattered notes. NPC generators produce fast but isolated entities (the tavern you generate has no ties to your factions); world builders are rich but manual (no generation, no reactivity). No DM-first product combines both into a *living*, interconnected world. mythosCircle claims that wedge: every NPC, faction, or place added becomes a thread in the lore, and the world can react to what the players do. Guardrail that shapes every decision: **a tool for the DM, not an AI DM** — the DM always keeps the wheel (regenerate, pick from candidates, hand-write).

## Capabilities

- **CAP-1 — World state** *(Phase 1)*
  - **intent:** the DM grows a versioned graph of entities (characters, factions, places) and typed directed edges, starting from a guided build-in of her existing world plus notes.
  - **success:** new entities commit with their edges in one transaction; deleting a referenced entity triggers the cascade confirmation listing affected entities; full world state (entities + edges + counters) is exportable as JSON + Markdown at any time.
- **CAP-2 — Entity generation** *(Phase 1)*
  - **intent:** a plain-language ask (e.g. "a barkeep in Greymarch who owes the guild money") returns 2–3 candidates woven into the existing world; the DM picks, tweaks, regenerates (whole or per-field), and wires relations inline — the DM is the final author.
  - **success:** each candidate ships name, role, personality, the secret/rumor/party-hook triple, a 5e stat block, and ≥1 edge into existing world state; acceptance commits the subgraph atomically; only an accepted candidate's subgraph commits and regenerate never mutates accepted state.
- **CAP-3 — Media generation** *(Phase 1)*
  - **intent:** the DM gets a portrait image for an entity (and a fast-tier short video during beta) attached to it.
  - **success:** every accepted entity ships a portrait; media is reclaimed on entity deletion; exports validate media references.
- **CAP-4 — Relationship graph** *(Phase 2)*
  - **intent:** the DM browses a node-link visualizer around any entity ("barkeep → thieves' guild → mayor") — surface only; the graph model already exists and is editable in Phase 1.
  - **success:** the web around any entity renders from world state without the view owning state.
- **CAP-5 — Export & VTT** *(Phase 1)*
  - **intent:** the DM exports entities to table-ready artifacts — Markdown, Owlbear, MapTool (RPGToken JSON + portrait), Fantasy Grounds — carrying the generated stat block and portrait.
  - **success:** export yields a format-validated artifact per entity with stat block and portrait present; validation failure is logged as an export-failure event.
- **CAP-6 — Session logger** *(Phase 3)*
  - **intent:** the DM logs player actions as a typed timeline (actors, targets, campaign time, note); later transcribed from session recordings.
  - **success:** a typed action lands in the world event store with a closed `action_type` set.
- **CAP-7 — Simulate History** *(Phase 3)*
  - **intent:** the world reacts to logged player actions — the graph state machine computes deterministic deltas (revenge, succession, warrants); the LLM only narrates. Rule authoring requires the Phase-1 edge-type schema and the action-log schema to exist first.
  - **success:** a logged action produces a deterministic delta set — same state + same action never diverges; the LLM narration varies, the state does not.

## Constraints

1. **Single machine:** one FastAPI process, one SQLite file, one media directory, local model servers, Caddy in front — all on the owner's RTX-3090 server; no message brokers, object stores, multi-node, or hosted tiers (AD-8, NFR 1).
2. **One graph-of-record, one commit path:** event-sourced versioned graph per campaign; every world-state change (build-in, accept, DM edit, cascade delete, future import) lands through the store's transactional commit and produces exactly one new revision; nothing else writes world state (AD-1, NFR 2–3).
3. **Commit semantics:** a candidate's full subgraph commits atomically against the latest revision; undo is one compensating commit per transaction; regenerating an existing entity preserves its ULID (inbound edges, media, references survive, edge re-targeting forbidden); a DM edit landing during a pending commit forces a visible rebase-or-reject (AD-2).
4. **Single FIFO queue:** all generation jobs (text, image, video, simulate) enter one persistent queue; exactly one job runs at a time on the 3090 — no concurrent inference; queue position visible via REST + WebSocket; no bypass path (AD-3, NFR 4, 9).
5. **Graph retrieval only:** prompts built from a bounded neighborhood traversal of typed edges (depth + entity cap, seed 24 entities) against the latest revision; no embeddings, similarity, or full-text search; the full world never enters a prompt; scale target: hundreds of entities, thousands of edges (AD-4, AD-16, NFR 11).
6. **Staged candidates:** generated candidates exist as proposed subgraphs invisible to retrieval and export until accepted; each proposal is all-or-nothing (a partial generation failure rolls back the whole candidate) (AD-15).
7. **LLM proposes, never decides:** state mutates only via DM-accepted commit or DM edit; relationship counters change only via commits; the Phase-3 simulation reads/writes counters deterministically and the LLM narrates the outcome (AD-5, AD-7, AD-23).
8. **Closed edge vocabulary:** directed edge types from a fixed Phase-1 set (relationship, debt, grudge, loyalty, member_of, located_in, rival_of, kin_of, ally_of, enemy_of — extensible by adding a type, never free text), each with a per-type counter; zero dangling edges after every commit (AD-5, NFR 2).
9. **Provider abstraction:** all LLM/image/video generation goes through adapters speaking OpenAI-compatible HTTP; local→OpenRouter (or any vendor) is a config change, not a rewrite (AD-6, NFR 8).
10. **Content capability:** MVP runs an abliterated 14–20B local model (24GB VRAM cap, Q4/Q5) via a local OpenAI-compatible server (llama-server seed); grimdark content (violence, warrants, intrigue) must generate without refusal, and the OpenRouter migration must select non-over-censored endpoints (AD-14, NFR 7).
11. **One SQLite store:** WAL-mode database holds current revision tables, the append-only event log, job queue, media manifest, and accounts; single writer (API process); no raw SQL outside the store layer (AD-13).
12. **Media owned by entities:** files at `media/{campaign_id}/{entity_id}/`, tracked in the DB manifest, written only by the media service, reclaimed on entity deletion, reference-validated on export (AD-10).
13. **Nightly snapshot + tested restore:** scheduled snapshot of the DB (SQLite backup API — `VACUUM INTO` or `wal_checkpoint` + copy, never a raw WAL-file copy) + media manifest to a second local location; restore script exercised before beta launch (AD-12, NFR 5).
14. **Accounts:** invited local email+password identity carried in an httpOnly session cookie (Secure, SameSite=Lax, path `/api`); every campaign belongs to one user and is private; TLS via Caddy, FastAPI on localhost (AD-9, AD-21, NFR 6).
15. **Wire contract:** backend Pydantic schemas are the single source of truth; the frontend derives types from generated OpenAPI; ULID IDs; UTC ISO-8601; error envelope `{code, message, details?}`; WebSocket progress messages `{type: job_progress|job_done|job_failed|queue_changed, job_id, state, queue_position?, progress?}` (AD-17).
16. **Frontend:** Vue 3 + Vite + TypeScript + Pinia; the Phase-2 visualizer reads world state only from Pinia stores — no direct API calls, no private caches (AD-20, NFR 12).
17. **Campaign time:** each campaign declares a time granularity (day/month/year/century) + calendar label; session events and simulation input reference campaign-time units only — no wall-clock value enters simulation (AD-18).
18. **Export is read-only:** export reads the latest revision, never writes; stat blocks follow the SRD 5.1 field set (level for NPCs, CR for monsters); Owlbear export = image URL + stat-block text (AD-11).
19. **Candidate shape contract:** every generated entity candidate carries name, role, personality, secret/rumor/party-hook triple, 5e stat block, and ≥1 typed edge into existing world state; acceptance validates the existing-world edge before commit (AD-24).
20. **Whole-campaign deletion is total:** hard delete through the store with explicit confirmation — revisions, event log, queue entries, media-manifest rows, and the campaign's media directory (AD-25, NFR 10).
21. **Config:** one `deploy/config.toml` + environment variables for secrets; no config in the client build; each generation job declares a max LLM/media call budget from config — exceeding it fails the job (AD-22).

## Non-goals

- An AI DM that runs the table — the DM always keeps the wheel.
- Hosted/multi-node deployment and scaling beyond the owner's machine (until beta proof).
- Import connectors (Obsidian, World Anvil, Kanka) and third-party/OAuth auth.
- Roll20 export (Phase 2) and Foundry export (owner cost/usage; revisit on demand).
- Non-5e systems (Pathfinder, then others) — 5e-first.
- Long-horizon history simulation ("what would happen in x years") — Phase 3 covers player-action reactions only.
- Shared character cards / gallery, party-shared campaigns, any campaign sharing (post-beta experiment, gated on redaction + privacy review).
- Premium-tier video (post-OpenRouter); video is not a launch priority in Phase 1.
- Session-recording transcription (Phase 3).
- Subscriptions or lifetime buys (no subscription by design; per-usage pricing only at OpenRouter migration).
- i18n (Turkish) — unless beta DMs are non-English.
- UI e2e tests in beta — store + pipeline carry unit tests; the owner dogfoods the UI.

## Success signal

Beta launch demonstration (PRD journey steps 1–6): an invited DM builds her world inside the tool (guided build-in + her notes, digested by the LLM), asks "a barkeep in Greymarch who owes the guild money," picks from 2–3 candidates with stat block + secret/rumor/hook, wires the relations inline, gets a portrait, and exports the barkeep + portrait table-ready — all on the owner's machine, zero per-token cost. Watched counter-metric: a high regenerate rate means generation is missing the mark (see `metrics.md`).

## Assumptions

- SQLite is the single state store (AD-13) — DB engine not explicitly chosen by the owner.
- REST + WebSocket for API + job progress (AD-17) — progress channel not explicitly chosen.
- Candidates staged as DB rows with a `proposed` flag (AD-15) — staging mechanism not explicitly chosen.
- All entry paths (guided build-in, later import connectors) share the same staging→commit pipeline (AD-19).
- Caddy for TLS termination (AD-21) — reverse-proxy choice not explicitly chosen.
- Vue 3 + Vite + TypeScript + Pinia (AD-20) — framework not explicitly chosen.
- One TOML config + env-var secrets (AD-22) — config mechanism not explicitly chosen.

## Open Questions

- Local model selection (abliterated 14–20B, 24GB VRAM cap) — owner, at build; drives quality, throughput, and the content-capability constraint.
- Image/video model + server choice — owner, at build (adapter interface already bound).
- Fast-tier video verdict — if it disappoints during beta, video moves to Phase 2.
- OpenRouter migration: endpoint selection, credit multipliers, per-campaign caps — owner, at migration.
- Phase-2 graph visualizer library (Cytoscape.js vs. Vue Flow) — at Phase-2 build.
