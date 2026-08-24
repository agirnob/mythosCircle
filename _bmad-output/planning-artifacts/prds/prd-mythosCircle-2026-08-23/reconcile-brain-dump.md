# Reconciliation — brain-dump input vs. prd-mythosCircle-2026-08-23

Generated 2026-08-23 (bmad-prd Finalize, step 2: input reconciliation).
Input: `original-inputs/brain-dump.md` · Artifacts: `prd.md`, `addendum.md`.

## 1. Coverage table

| # | Input idea (abbreviated) | Landing |
|---|---|---|
| 1 | Target: lazy TTRPG DMs who want a generated NPC/BBEG "that will hunt the group", e.g. random barkeeper connected to the world | **Covered** — prd §1 *Who* / *The job*; §2 *Journey* step 2 "The ask"; §3 B *Entity generation* |
| 2a | "generate inter connected world which feels like its alive" (core of the tool) | **Covered** — prd §1 one-liner (*interconnected, believable, living world*); §6 metric "the 'living world' feel" |
| 2b | "MVP would be basic NPC generation with LLM using the lore provided" | **Covered** — prd §7 *Scope* Phase 1 (MVP) |
| 2c | end tool "should be able to make factions, cities, places, towns, connection between characters … relations to other NPCs" | **Covered** — prd §3 A *World state* (nodes: characters, factions, places; edges: relationships, debts, grudges, loyalties) |
| 2d | "This is not a ai dm but a tool for the dm" | **Covered** — prd §1 *Guardrail* ("a tool for the DM, not an AI DM") |
| 2e | "DM should be able to regen chose from multiple things write themself if they want to" | **Covered** — prd §1 *Guardrail*; §3 B (regenerate whole/per-field, hand-edit) |
| 2f | "after NPC has been generated I want the user to generate image or video" | **Covered** — prd §3 C *Media generation* (Phase 1: portrait image + short video) |
| 2g | "simulate history button so if players do something the world can react" (faction leader killed → rival faction helps/takes over, or puts a warrant on the players) — explicitly "after the mvp" | **Covered** — prd §2 *Journey* step 9 (*Simulate History*, Phase 3); §3 G *World reaction* (state machine computes; LLM narrates) |
| 3 | "there are tools that generate the npc or … world building. But there is no tool that combines both … no tool … that change the world by effects of the players or simulate the history" | **Covered (honest form)** — prd §1 *What's different*; full evidence in addendum *Competitive landscape* + *Wedge verdict* (claim softened per research — see §3 below) |
| 4 | "backend would be python using fastapi for llm and image gen calls, ui can be made by vue" | **DROPPED** — no stack mention in either artifact. Technical how; belongs in addendum (tech) or the architecture doc (see Gaps #1) |
| 5 | "Tackle the 'Simulate History' Feature Carefully … expensive and unpredictable (hallucinations compounding) … treat factions and world simulation like a state machine or graph database … LLM only to narrate the changes rather than compute them" | **Covered** — prd §3 A (graph model), §3 G ("State machine computes deltas … LLM only narrates. No LLM-driven 'what would happen' free-for-all"), NFR *World-state integrity*, §2 *Journey* step 9 |
| F1 | "Relationship Graph Visualizer … visual node-link diagram (using a library like Cytoscape.js or Vue Flow on the frontend) showing how the barkeep connects to the thieves' guild and the mayor will blow users away" | **Covered (function), library suggestion DROPPED** — prd §3 D *Relationship graph* (Phase 2); §2 *Journey* step 4 "See the web … The moment it blows users away"; §7 Phase 2. Cytoscape.js/Vue Flow mention absent (see Gaps #2) |
| F2 | "Lazy DMs don't just want a name and stats; they want immediate playability. Every generated entity should come with a built-in Rumor, a Secret, and a Hook tied to the party" | **Covered** — prd §2 *Journey* step 3 ("secret, a rumor, and a hook tied to the party, 5e-ready"); §3 B ("secret / rumor / party hook triple. 5e-ready"). Rationale "immediate playability" implicit in §1 *The job* (see Gaps #3) |
| F3 | "Player Action Logger (Post-MVP) … simple timeline event logger ('Party assassinated Lord Vane on Day 14') will make triggering world reactions much more accurate" | **Covered** — prd §3 F *Session logger* (Phase 3); §2 *Journey* step 8 ("Log what the players did") |
| F4 | "adding what players did can be made by transcription of the session" | **Covered** — prd §3 F ("Later: transcribed from session recordings"); §2 *Journey* step 8 |

## 2. Gaps — ideas the FR structure silently drops (→ addendum)

1. **Technical stack** — input point 4: *"backend would be python using fastapi for llm and image gen calls, ui can be made by vue."* Neither prd.md nor addendum.md mentions FastAPI or Vue. This is deliberate PRD scope (product doc, not architecture), but the note is now nowhere: it belongs in the addendum as a "technical direction" note (or carried to the bmad-architecture step).
2. **Graph-viz library suggestion** — input F1: *"a visual node-link diagram (using a library like Cytoscape.js or Vue Flow on the frontend)"*. prd §3 D keeps the node-link visualizer but drops the library candidates. Belongs in the addendum (frontend library consideration: Cytoscape.js vs Vue Flow, against the Phase 2 graph surface).
3. **"Immediate playability" rationale** — input F2: *"Lazy DMs don't just want a name and stats; they want immediate playability."* The feature (secret/rumor/hook triple, 5e-ready) survived; the *why* (instant table readiness vs. name+stats) did not. One-line addition to the addendum would preserve the intent behind the triple.
4. **Multi-year simulation ambition** — input point 3: *"simulate the history of what would happen in x years"*. The PRD scopes Simulate History to reacting to logged player actions (§3 G, Phase 3), and the user's own point 5 argues against LLM-driven simulation. Resolved internally, but the original "years of history" ambition is silently narrowed; a note in the addendum ("Simulate History is reaction-driven by design, per input point 5; free-running multi-year simulation deliberately out of scope") would keep the decision visible.

## 3. Conflicts

No genuine unresolved conflicts between the input and the artifacts.

- **Resolved by research (not a conflict):** input point 3 *"There is no tool that combines both"* / *"no tool … that simulate the history"* — disproved: addendum *Wedge verdict* documents LoreKeeper (world builder + AI DM) and NarrativeEngine-P (divergence register, NPC agency). The PRD carries the honest compact form (§1 *What's different*); the research is the resolution, so this is not listed as a conflict.
- **Resolved by the input itself:** point 3's *"simulate the history of what would happen in x years"* vs. the PRD's reaction-scoped Simulate History — the input's own point 5 ("Treat … like a state machine … LLM only to narrate") resolves the tension; see Gaps #4 for the narrowing note.
