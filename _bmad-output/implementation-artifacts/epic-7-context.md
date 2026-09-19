# Epic 7 Context: See the Web — Relationship Graph

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Let the DM click any entity and browse its web of relationships rendered as a node-link graph — "barkeep → thieves' guild → mayor" — the product's signature demo moment. The relationship data model already exists from Phase 1 (typed edges, edited inline in the world view); Epic 7 is the surface on top of it. Story 7.1 spikes the visualizer early so the library choice lands by demo time instead of becoming a backlog item.

## Stories

- Story 7.1: Graph Visualizer Spike (Phase 2 boundary)

## Requirements & Constraints

- FR15 (Phase 2): a node-link relationship visualizer around any entity. Given a Phase-1 world, clicking an entity renders its typed-edge web live — within the AR6 depth/entity caps (seed 24 entities). "Renders" means an interactive live demo, not a screenshot.
- AR16 / AD-20: the visualizer reads Pinia only. It makes no direct API calls and holds no private caches — any data it shows must already be in the Pinia stores from Phase-1 views (world state lives in the per-capability Pinia stores; the graph view adds its stores only for view-local concerns, never as a data cache). No backend surface is added for the browser view.
- NFR12: stack stays Python/FastAPI + Vue; the graph-visualizer candidates are Cytoscape.js vs Vue Flow, and the spike must conclude by choosing one — the choice is the deliverable, not a follow-up task.

## Technical Decisions

- Contained view dependency (AD-20): the graph view is a browser-side component over the existing world-state graph; it consumes state, never owns or models it. Vue 3 + Vite + TypeScript + Pinia remain the stack.
- Render scope mirrors retrieval bounds (AR6): a bounded neighborhood over typed edges — depth cap plus entity cap, seed 24 entities. Full-world layout is explicitly out of scope: worlds can hold hundreds of entities and thousands of edges (NFR11), so the caps are what keep the view responsive. The rendered web is the closed typed-edge vocabulary with per-type counters as committed through the store.
- No graph-model work in this epic: entities, directed typed edges, and inline edge editing already exist (Phase 1); the visualizer renders that shape as-is.

## UX & Interaction Patterns

- Entry point: click any entity in a Phase-1 world and its relationship web renders in place, browsable at will — the live "barkeep → thieves' guild → mayor" walk is the demo beat. Rendering must stay within the depth/entity caps so interaction stays responsive.

## Cross-Story Dependencies

- Scheduling (owner decision, 2026-09-19): Epic 7 runs before Epic 6's beta gate — the graph work is not blocked on backup/restore/ownership landing first.
- Epic 8 (post-7, pre-Phase-3 UI uplift) will migrate every view — including the graph view — onto a shared design-token scale and retire per-view scoped-CSS one-offs; the spike should avoid baking in bespoke visual styling that Epic 8 must redo.