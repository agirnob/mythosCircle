---
title: "mythosCircle — Living-World NPC & World Generator for TTRPG DMs"
status: final
created: 2026-08-23
updated: 2026-08-23
---

# mythosCircle — Product Requirements Document

> Working doc — built section by section on the coaching path. All content sections locked 2026-08-23; Finalize pass (reconciliation, reviewer gate, triage) complete.

## 1. Vision & Positioning
**One-liner.** A tool that lets a TTRPG DM grow an interconnected, believable, *living* world — every NPC, faction, or place they add becomes a thread in the lore, and the world can react to what the players do.

**Who.** Lazy-but-invested DMs running campaigns who don't want to hand-craft every NPC and place from scratch.

**The job.** Generate NPCs/BBEGs/factions/places that are *connected* to the world already being run, so the table feels alive without the prep burden.

**What's different.** NPC generators exist — fast but isolated: the tavern you generate has no ties to your factions. World builders exist — rich but manual: no generation, no reactivity. A few AI-DM platforms and open-source projects feed structured worlds into play, but they run the table instead of the DM. No hosted, DM-first product combines both into a living, interconnected world where each new entity adds to the lore, shows you the relationship graph, and lets the world simulate what happens over time — revenge, succession, warrants — with image and video for every entity. *(Full competitive analysis: `addendum.md`.)*

**Guardrail.** This is *a tool for the DM, not an AI DM*. The DM always keeps the wheel: regenerate, pick from multiple candidates, or hand-write.

## 2. The Journey
Wren: six years behind the screen, running a long 5e sandbox campaign. A world that lives in her head and in scattered notes.

1. **Bring the world in** *(Phase 1)* — Wren builds her world inside mythosCircle: a guided world-building flow plus her own notes, digested by the LLM. Later: import from Obsidian, World Anvil, Kanka.
   *She's not creating a world; she's digitizing one she already believes in.*
2. **The ask** *(Phase 1)* — Prep night. She needs a barkeep for the tavern and a BBEG who'll hunt the party. Plain language: *"a barkeep in Greymarch who owes the guild money."*
3. **Candidates — pick, tweak, rewrite** *(Phase 1)* — 2–3 candidates, each woven into her lore, each shipping with a **secret, a rumor, and a hook tied to the party**, 5e-ready. She picks B, edits one detail, regenerates the name, and wires the relations: *works at the tavern, owes the thieves' guild a debt, sister of the mayor's scribe*. Nothing is final until she says it is.
4. **See the web** *(Phase 2)* — The relationship graph: barkeep → thieves' guild → mayor. The moment it blows users away.
5. **Give them a face** *(Phase 1)* — Portrait for the barkeep; short video for the BBEG reveal slide.
6. **Take it to the table** *(Phase 1)* — Wren exports the barkeep, the BBEG, and their portraits to her VTT — Owlbear, MapTool, Fantasy Grounds, or plain Markdown — so the session starts with them already there.
7. **Session night** *(context, not a feature)* — The BBEG who was supposed to be a one-off is now hunting the group. The barkeep's secret pays off in act three.
8. **Log what the players did** *(Phase 3)* — *"Party assassinated the Ashen Hand's lord, day 14."* Typed by hand, or transcribed from the session recording.
9. **The world reacts — Simulate History** *(Phase 3)* — The graph state machine computes the consequences; the LLM only narrates: the rival guild seizes the territory, survivors put a warrant on the party, the barkeep starts hoarding rumors. Next session, the world has moved.

## 3. Capabilities & Requirements
**A. World state — the foundation** *(Phase 1 core, Phase 2 surface)*
- The world is a graph: nodes (characters, factions, places) and edges (relationships, debts, grudges, loyalties).
- Every generated entity becomes a node woven into existing edges — the world grows with each use.
- Edge types use a closed, directional vocabulary (relationship, debt, grudge, loyalty) defined in Phase 1 — the P3 state machine consumes it; free-form labels are not persisted.
- Deleting an entity with live edges requires an explicit cascade confirmation listing affected entities; the graph never holds dangling edges.
- **Acceptance (A):** new entities commit with their edges in one transaction; deleting a referenced entity triggers the cascade confirmation; world state is exportable as JSON + Markdown at any time.
- Built inside the tool: a guided world-building flow (name, key places, factions, key figures) plus her own notes, digested by the LLM into entities.

**B. Entity generation** *(Phase 1)*
- Plain-language ask + world state → 2–3 candidates.
- Each candidate: name, role, personality, lore connections, the **secret / rumor / party hook** triple, and a generated **5e stat block** (level, AC, HP, abilities, key skills) — immediate playability, 5e-ready.
- **Inline relation editing** — while creating or editing a character, the DM adds, edits, or deletes relations to any entity (places, factions, other characters): *works at [tavern]*, *owes [guild] a debt*, *rival of [mayor]*.
- Regenerate — whole or per-field. Hand-edit. The user is the final author.
- Candidate lifecycle: *proposed → accepted / rejected*. Only an accepted candidate's subgraph commits; regenerate creates a new candidate and never mutates accepted state.
- **Acceptance (B):** a plain-language ask returns 2–3 candidates, each with a stat block + secret/rumor/hook + at least one edge into existing world state; accept commits atomically; the DM's edit always beats the LLM's output.

**C. Media generation** *(Phase 1)*
- Portrait image for a generated entity.
- Short video (the BBEG reveal slide) — local fast tier only during beta; not a launch priority (§7).
- Media attached to an entity is reclaimed on entity deletion; exports validate media references.
- **Acceptance (C):** every accepted entity ships a portrait (fast-tier video where offered); media reclaimed on delete; export embeds it.

**D. Relationship graph** *(Phase 2)*
- Node-link visualizer around any entity — "barkeep → thieves' guild → mayor".
- Surface only: the graph model already exists from Phase 1 and is editable there.

**E. Export & VTT integration** *(Phase 1)*
- Characters, places, and factions export to: Markdown, Owlbear, MapTool, Fantasy Grounds. Roll20 in Phase 2. *(Foundry deferred: owner doesn't use it and it's a paid license — revisit on customer demand.)*
- Exports carry the generated 5e stat block and the generated portrait, so the entity lands in the VTT table-ready.
- MapTool artifact: RPGToken character (JSON) + portrait — tokens import trivially.
- **Acceptance (E):** export yields a format-validated artifact per entity (Markdown / Owlbear / RPGToken / Fantasy Grounds) with stat block and portrait present; validation failure is logged as an export-failure event.

**F. Session logger** *(Phase 3)*
- Timeline of player actions, typed by the DM. Later: transcribed from session recordings.

**G. World reaction / Simulate History** *(Phase 3)*
- State machine computes deltas from logged actions; the LLM only narrates (revenge, takeover, warrant…). No LLM-driven "what would happen" free-for-all — that's how hallucinations compound. *(Scope note: the original "what would happen in x years" ambition stays a Later item; Phase 3 covers player-action reactions.)*
- Rule model: DM-authored rules over the closed Phase-1 edge vocabulary + a fixed starter rulebook; the LLM narrates, never decides the delta.
- Dependencies (explicit): Phase-1 edge-type schema and Phase-3 action-log schema must exist before P3 rules are authored.
- **Acceptance (G):** a logged action produces a deterministic delta set — same state + action never diverges; the LLM narration varies, the state does not.

## 4. Non-Functional Requirements & Constraints
1. **Compute & cost (local-first).** MVP: all generation runs on the owner's local server (LLM inference on the RTX 3090) — zero per-token cost at MVP; local model quality and throughput are the trade-off. After the initial phase: API route via OpenRouter. Once on API, generation is metered — bounded multi-calls, per-campaign caps (values set at migration).
2. **World-state integrity.** The graph is the single source of truth. Generation must never silently corrupt existing entities or edges; the DM's edit always beats the LLM's. Verifiable invariants: zero dangling edges; entity identity stable across regeneration; retrieval-relevance proxy — % of generated candidates whose top-k retrieved entities appear in final lore connections.
3. **Commit semantics.** World state is versioned; each generation commits as one atomic subgraph transaction against the latest revision; undo is per-transaction; a DM edit landing during a pending commit forces a visible rebase-or-reject.
4. **Beta throughput.** Single-generation FIFO queue — no concurrent inference on one GPU; queue position visible; serial RTX 3090 throughput is the beta ceiling.
5. **Backup.** World state + media manifest snapshotted automatically (nightly, local); restore tested before beta launch. Export is ownership, not backup.
6. **Accounts.** Phase 1 minimum: invited-user identity, per-campaign ownership, TLS. Campaigns are private at beta; sharing is a post-beta experiment (§5).
7. **Content capability.** MVP runs an abliterated (uncensored) local model so grimdark campaign content — violence, warrants, assassinations, faction intrigue — generates without refusal. The OpenRouter migration must select non-over-censored endpoints so content capability does not regress.
8. **Provider abstraction.** The inference endpoint is swappable: local server (MVP) → OpenRouter (later). No hard-coded single-vendor dependency.
9. **Long-running operations.** Generation, image, and video run asynchronously with progress; Wren keeps prepping the next NPC while a portrait renders.
10. **Data ownership.** The world state is always fully exportable (JSON + Markdown) independent of VTT targets; deleting a campaign actually deletes it.
11. **Scale.** Long-campaign capable: hundreds of entities, thousands of edges. Only the lore relevant to the request enters generation — retrieval over the graph, never the whole world in context.
12. **Stack.** Python/FastAPI backend (LLM + image/video generation calls), Vue frontend. Graph-visualizer candidates: Cytoscape.js / Vue Flow (details in `addendum.md`).

## 5. Business Model
- **Closed beta (Phase 1).** Launch as a closed beta: a few invited DMs on the owner's local server. Free — the cost is the owner's compute, not tokens.
- **Token/credit model (post-beta).** No subscription by design — reconsider only if customers demand it. No lifetime buy: LLM usage is ongoing, so pricing must be per-usage.
  - One convertible **credit** unit with published multipliers (values set at OpenRouter migration): character generation (low) < faction/place (mid) < simulate history (mid–high) < image (high) < video (highest — a single premium render can consume the entire balance). Fast-tier video is excluded from free allowances.
  - Free credits granted **weekly** (enough for, e.g., one character plus one image — no video).
  - **Video layering** — two tiers: *fast & rough* (lower quality, cheaper, faster) and *premium* (higher quality, expensive, slower).
- **Open (owner):** credit multipliers + per-operation prices; LLM + image + video endpoint selection on OpenRouter (text: abliterated per §4).
- **Post-beta experiment — shared character cards / gallery.** Exported entities can be published as public character cards (stat block + portrait + lore) — a growth loop and a possible paid tier (e.g., private worlds). *Gate: per-entity share must redact secrets/plot hooks; privacy review before launch. Revisit with token pricing — note the tension with "no subscription by design" (launch constraint). *

## 6. Success Metrics & Counter-Metrics
**Success (beta → launch):**
1. **Activation** — % of invited DMs who create a world and generate their first NPC in week one.
2. **Core loop** — entities generated per active campaign per week.
3. **Wedge validation (the "living world" feel)** — % of generated entities that receive at least one *manual* relation edit; % of entities with a completed in-tool export (format validated). VTT-side import success is out of reach — the export-failure counter covers in-tool failures. The two metrics that prove or kill the positioning.
4. **Retention** — % of campaigns returning the following week. *(Phase 3: % that log session actions after playing.)*
5. **Cost health** — generations per entity and per active campaign; free-credit burn rate per week. *(Metered from day one: free metering in beta, no charge.)*
6. **Measurement note.** In-tool counters: export jobs per entity, format-validation pass rate, manual-relation edit rate, credit burn. Retention is measured per account — gated on the account model (§8). Beta cohort ("a few" DMs) → percentages are directional, not statistical.

**Counter-metrics (look good, mean bad):**
1. **Regenerate rate** — high full/partial regeneration means generation is missing the mark, even if volume looks healthy.
2. **Free-token burnout** — users who exhaust their weekly free tokens and don't return: the allowance is too small or the value dropped.
3. **Premium-video share** *(post-beta only)* — if almost nobody takes the premium tier, the layering is theater.
4. **Export failure rate** — in-tool export/validation failures on the "take it to the table" path (VTT-side import happens outside the tool; we measure what we can see).

## 7. Scope — MVP vs. Later
- **Phase 1 — Launch (MVP):** 5e-first. World state created inside the tool (guided build + notes digested by the LLM). NPC/BBEG generation with 2–3 candidates, generated 5e stat block, secret/rumor/hook, inline relation editing, regenerate, and hand-edit. Image generation; video: local fast tier only, not a launch priority. Export to VTT (Markdown, Owlbear, MapTool RPGToken, Fantasy Grounds).
- **Phase 2:** Relationship graph — the node-link visualizer. The graph data model already exists under Phase 1; this is the surface. Export target: Roll20. *(Foundry deferred — owner cost/usage constraint.)*
- **Phase 3:** Session action logger (typed; later transcribed from recordings) and world reaction / Simulate History (state machine computes deltas, LLM narrates).
- **Later:** Import connectors — Obsidian, World Anvil, Kanka. Additional systems (Pathfinder, then others). Long-horizon history simulation ("what would happen in x years"). Shared character cards / gallery (§5 experiment). Party-shared campaigns.

## 8. Risks & Open Questions
### Competitive
- **LoreKeeper** — closest commercial product: a structured world builder feeding an AI DM, with a free tier. Defense: mythosCircle is DM-first (the DM keeps the wheel), with a graph UI and time simulation LoreKeeper lacks.
- **NarrativeEngine-P / llm_RPG (OSS)** — strongest prior art for "the world lives without you" (nightly faction sims, nemesis system, divergence register). Both are player-facing, self-hosted games; if either ships a DM-prep mode, the Simulate History moat shrinks.
- **open-tabletop-gm + neuralinitiative.ai** — OSS GM framework with a typed relationship graph and a hosted top-up. Fast-follower risk on the graph + hosted axis.
- **Tabletop Arc (Lore Wall)** — "the world becomes canon": a passive version of player-reactivity.

### Open items *(Finalize: closed or deferred with owner + gate)*
- Accounts & campaign sharing — **closed for beta:** private campaigns + invited identity (§4 Accounts). Sharing / shared cards: deferred to post-beta experiment (§5).
- Video — **deferred by priority:** local fast tier only during beta; premium tier post-OpenRouter; if the fast tier disappoints, video moves to Phase 2.
- Token pricing — model decided (§5: one credit unit, published multipliers); multipliers + per-operation prices + per-campaign caps: owner, at OpenRouter migration.
- LLM / image / video endpoint selection on OpenRouter — owner, at migration. Text: abliterated model per §4.
- **Local model selection** — owner, at build: abliterated, 14–20B (24GB VRAM cap); drives quality, throughput, and the §4 content-capability NFR.
- **Shared-card privacy** — a public character card can leak world lore (secrets, plot hooks). Gate: per-entity share with redaction; privacy review before launch (post-beta experiment).

## 9. Glossary
- **Entity** — a node in the world graph: character, faction, or place.
- **Candidate** — a proposed generated entity with its subgraph; states: *proposed → accepted / rejected*.
- **Stat block** — generated 5e data (level, AC, HP, abilities, key skills) attached to a character entity.
- **Credit** — the single convertible unit of account for all generation; multipliers published at OpenRouter migration.
- **Fast / premium tier** — the two video-quality tiers: fast = cheaper, rougher, faster; premium = higher quality, slower, expensive.
- **RPGToken** — MapTool character format: JSON + portrait, trivially importable.
- **Simulate History** — Phase-3 world reaction: the state machine computes deterministic deltas from logged player actions; the LLM only narrates.
