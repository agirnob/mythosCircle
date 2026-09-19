---
title: '7.1 Graph Visualizer Spike'
type: 'feature'
created: '2026-09-19'
status: 'done'
baseline_commit: '5dd0e41deaf7d65021bfc3aa10c68ee0121909ec'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-7-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The signature demo moment — "barkeep → thieves' guild → mayor" — has no surface. Committed worlds already hold entities and typed edges (Phase 1), but no view renders them as a browsable web, and the library choice (Cytoscape.js vs Vue Flow, NFR12) is undecided.

**Approach:** Spike the visualizer with BOTH candidates rendering the same AR6-bounded neighborhood from Pinia state, score them on concrete criteria, pick one, and ship the winner as a live click-entity → web view (`/campaigns/:id/graph?focus=<eid>`) that reads the world store only. The decision record is the story's deliverable; the working view is the demo moment. `mockup-7-1-graph-view.html` (approved at CHECKPOINT 1) is the layout contract; the review's visual/interaction corrections are baked into the Always/Never tiers below.

## Boundaries & Constraints

**Always:**
- AR16/AD-20: the graph view reads Pinia only — the world store's own snapshot fetch/actions, never raw API calls or private caches. No backend surface is added; no new API.
- Render scope mirrors AR6 seed caps: 1-hop typed-edge neighborhood, ≤24 entities; edges render only when both endpoints are in the set; full-world layout is out of scope (NFR11).
- **Relationship semantics are the committed types, verbatim.** Labels use `edgeLabel(type, counter)` from `components/profile/profile.ts` (counter suffix only on the 8 `COUNTER_TYPES`; others bare). Never remap, normalize, or invent a type — if the committed data is generic (`relationship`), it renders generic (data limitation, recorded, not a defect).
- The committed relation is visible from either endpoint: the neighbor set includes inbound + outbound 1-hop edges (WorldView `RelationLine` precedent); arrows render src→dst as committed — `EdgeExport` has NO direction field, derive it from orientation, never invent one.
- Direction legibility: arrowheads sized/contrasted and stopping outside node borders; reciprocal and parallel edges render as separate curved paths; hover/selection highlights the full edge and shows source → type(counter) → target (tooltip or details panel).
- Focus treatment is node-attached — stronger border + restrained halo/shadow — never an oversized ring or boundary shape through the canvas.
- Edge labels: selective visibility driven by zoom/focus/hover/selection; no permanent label pile-up at density; label collisions avoided; authoritative label always available via tooltip/details.
- Entity types distinguishable without reading the badge: kind glyph/icon, portrait or initial avatar (portrait when `media` exists), color as reinforcement only.
- Visible controls: zoom in/out, fit view, reset, entity-type filter, relationship-type filter, labels toggle — operable with mouse, keyboard, and touch; no hover- or wheel-only affordances.
- Visible breadcrumb (Campaigns / `<world>` / Graph / `<entity>`) — human-readable; raw ULIDs never displayed, even though the URL keeps `?focus=<ulid>`.
- Counts are user-facing: "N entities · N relationships"; the cap is mentioned only when truncation actually occurs.
- Both prototypes consume ONE shared pure module (`graphModel.ts`): given a `WorldExport` + focus id → bounded `{nodes, edges, truncation}` — deterministic truncation in source array order. The client mirrors AR6's *caps*, not `retrieve_neighborhood` (no backend call).
- A/B equivalence: both candidates render the SAME normalized data (incl. the dense 24-node fixture), SAME viewport, SAME interactions, equivalent node/edge content — recorded side by side, on desktop and mobile.
- Route is auth-guarded (existing `beforeEach` pattern); view styling is scoped CSS consistent with current views — no bespoke design system Epic 8 must redo.
- Runs before the Epic 6 beta gate (owner swap 2026-09-19); epic/story IDs stay stable.

**Ask First:** If the spike surfaces a fork that the scoring criteria do not resolve (e.g. layout plugin choice materially changes the verdict), HALT and ask. The library decision itself is the story's AC, not a human gate.

**Never:**
- No store writes, no revision, no event (AD-1) — visualization is read-only.
- No graph-model changes; no new entity/edge shape fields.
- Never invent, normalize, or remap relationship types/semantics; never fabricate counters or directions.
- Never display raw ULIDs in the UI (breadcrumb is human-readable; `?focus=` stays internal).
- Not a full-world renderer (NFR11 scale stays behind the caps).
- No permanent full-label rendering at density — selective label visibility only.
- No hover- or wheel-only interactions; controls work with keyboard and touch.
- Do NOT ship both libraries in the final bundle: the loser's component + its test are removed in the final commit (git history + research-7-1 keep the evidence).
- No UI e2e (AR22): owner dogfoods; verification is a live browser demo + unit tests.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | committed world, `?focus=<eid>` exists | canvas shows focus node + 1-hop neighbors (both directions), typed edge arrows labelled with `edgeLabel`, ≤24 nodes; node click refocuses (URL `?focus=` replaces) | N/A |
| NO FOCUS | `/graph` without `?focus` | focus = the world's most-connected entity (max degree over all committed typed edges; rowid tie-break; deterministic), URL gains `?focus` — the demo moment never lands on an edgeless first row | N/A |
| MISSING FOCUS | `?focus=<ulid>` not in the world | empty-state message + hint; no crash | handled in view |
| OVER CAP | focus with >24 one-hop neighbors | deterministic truncation + "showing N of M connected entities, plus the focused entity" | N/A |
| NO RELATIONSHIPS | focus with zero edges | "No relationships" state, focus node still shown | handled in view |
| RECIPROCAL / PARALLEL | two nodes with opposite or duplicate edge types | separate curved paths, visually distinct, both labelled/selectable | N/A |
| FILTERED EMPTY | type/relationship filter excludes every visible element | filtered-empty message + reset hint; no crash | handled in view |
| RENDERER ERROR | candidate render throws | error state with retry, not a blank canvas | handled in view |
| EMPTY / NOT FOUND / LOADING | no entities; `store.notFound`; fetch in flight | existing WorldView empty/notFound/loader state patterns | N/A |

</frozen-after-approval>

## Code Map

- `frontend/src/stores/world.ts` — `useWorldStore`: `byCampaign[campaignId].world: WorldExport | null`, action `fetchSnapshot(campaignId)`; THE sanctioned read path for the view (AR16).
- `frontend/src/api/schema.ts` — generated `EntityExport` `{id, kind, name, text, data, media}` (L896-910), `EdgeExport` `{id, src, dst, type, counter}` (L866-893), `WorldExport` `{campaign, revision, entities, edges}` (L1116-1123) — arrays, not maps.
- `frontend/src/components/profile/profile.ts` — `EDGE_VOCAB` (16), `COUNTER_TYPES` (8), `EDGE_DIRECTIONS`, `edgeLabel(type, counter)` — vocabulary + label home.
- `frontend/src/views/WorldView.vue` — entity cards `<article class="card entity">` (~L1378) with a per-card action button row (the "See web" entry point); relations projection `relationsByEntity`/`relationsFor` (~L111-194) with `outbound: endpoint === edge.src` (~L162); `RouterLink` nav convention (~L1299-1316).
- `frontend/src/router.ts` — 9 routes, `createWebHistory`, auth guard; add `/campaigns/:id/graph` (`name: 'graph'`) + `?focus` query (no entity deep-link exists today — this introduces the query-param convention).
- `frontend/package.json` — NO graph lib installed; vue ^3.5.41, pinia ^4.0.3, vue-router ^5.3.0, vite ^8.2.2, vitest ^4.1.11, TS ^6.0.3; `.npmrc` `legacy-peer-deps=true` (openapi-typescript vs TS6 — codegen only).
- `frontend/vite.config.ts` — `/api` proxy → 127.0.0.1:8000 (ws:true); vitest env `node`.
- `frontend/src/views/WorldView.test.ts` — view-test conventions (happy-dom header, router/ws/client mocks, L1-49); `frontend/src/stores/world.test.ts` — store-test conventions (createPinia + fetch spy).
- `backend/app/pipeline/retrieval.py` — `DEFAULT_DEPTH=1`, `DEFAULT_ENTITY_CAP=24` (L23-25): the caps the client window mirrors (semantics only — no backend call).
- `backend/app/store/commit.py` — `EDGE_TYPES` (16) — matches frontend `EDGE_VOCAB`.

## Tasks & Acceptance

**Execution:**
- [x] `_bmad-output/implementation-artifacts/mockup-7-1-graph-view.html` — static layout mockup v2 (no graph lib; REAL demo data — Pike the Rook's 1-hop web, Drowned Harbor): kind glyph/avatar, directed curved arrows with `edgeLabel` incl. counters, node-attached focus (no ring), breadcrumb, controls toolbar, tooltip exemplar. Approved at CHECKPOINT 1; the layout contract both A/B candidates must match.
- [x] `frontend/src/components/graph/graphModel.ts` — pure bounded-neighborhood module: input `(world: WorldExport, focusId: string)` → `{nodes, edges, truncation}` — 1 hop both directions, cap 24, deterministic array-order truncation, edges with both endpoints in set, focus-unknown → empty set; the single contract both prototypes consume.
- [x] `frontend/src/components/graph/denseFixture.ts` — the A/B + test fixture: 24 visible nodes, >23 eligible neighbors, long names, missing portraits, incoming + outgoing edges, reciprocal pairs, multiple edges between the same pair, same- and cross-type edges, dense labels. Both candidates render THIS fixture, not only the 7-node world.
- [x] `frontend/src/components/graph/GraphView.vue` — view shell: reads world store only (no direct API calls), initializes focus from `?focus` (default first entity), renders the active candidate via a local `CANDIDATE: 'vueflow' | 'cytoscape'` switch (spike-only); node click refocuses (router replace query); states: loader / notFound / empty / no-relationships / filtered-empty / renderer-error; breadcrumb; toolbar (zoom in/out, fit, reset, type + relationship filters, labels toggle) wired to candidate controls.
- [x] `VueFlowGraph.vue` (install @vue-flow/core current stable) and `CytoscapeGraph.vue` (cytoscape + a layout plugin, current stable) — each consumes `graphModel` output: node label = name + kind glyph/avatar (portrait via store media when present), directed arrows with contrast + clear-of-border arrowheads, curved separated reciprocal/parallel edges, `edgeLabel` labels incl. counters, selective label visibility (zoom/hover/selection), hover/selection tooltip or details with source → type(counter) → target, click-to-refocus, touch pan/pinch; record installed versions + custom-code count.
- [x] `frontend/src/router.ts` — add auth-guarded `graph` route (+`?focus` passthrough).
- [x] `frontend/src/views/WorldView.vue` — per-card "See web" action → `router.push({ name: 'graph', params: { id }, query: { focus: eid } })`; "Graph" nav link in the view's section nav.
- [x] Tests — `graphModel.test.ts`: happy path, both-direction inclusion, cap truncation + notice flag (deterministic on the dense fixture), missing focus, dangling-edge exclusion; `GraphView.test.ts`: renders nodes/edges from a store-provided world, refocus on node click + query sync, default-focus, states (empty/notFound/loading/filtered-empty/renderer-error via mocked candidate throw). Winner-only component test.
- [x] `_bmad-output/implementation-artifacts/research-7-1-graph-visualizer.md` — spike decision record: EQUIVALENT A/B on the same dense fixture + real world (same viewport, same interactions, desktop + mobile): screenshots, interaction failures, pan/zoom/pinch behavior, label readability, parallel-edge handling, focus-transition, performance at 24 nodes, Vue integration effort, custom code required, final scores + verdict + rationale. The AC-3 deliverable.

**Acceptance Criteria:**
- Given a Phase-1 world, when the DM clicks an entity's "See web", then `/campaigns/:id/graph?focus=<eid>` renders the typed-edge web within the AR6 caps (1 hop, ≤24 entities) — a live browsable demo, not a screenshot.
- Given the graph view, when it renders, then every datum shown comes from Pinia (the world store's snapshot) — no view-local API calls or private caches.
- Given a rendered node, when clicked, then the view refocuses that entity's neighborhood with `?focus=` replaced (no reload); direct navigation/refresh restores focus, and browser back/forward restore prior focus states.
- Given the same world, when both candidates render it, then both show the identical node/edge set (shared `graphModel` contract).
- Given the dense 24-node fixture, when either candidate renders it, then there is no node-on-node overlap, edge direction stays readable, and labels are selectively hidden rather than permanently colliding (evidence recorded in research-7-1).
- Given an edge hover or selection, then a tooltip/details shows source → type(counter) → target; hovering/selecting highlights the full edge including reciprocal/parallel variants.
- Given the type/relationship filters, then the visible subset updates accordingly and a filtered-empty state renders with a reset hint; labels toggle on/off.
- Given keyboard or touch input, then zoom in/out, fit, reset, and pan work without mouse-wheel or hover (nodes are draggable without accidentally triggering refocus, and dragging never mutates world state).
- Given a candidate render failure, then an error state renders with retry — never a blank canvas.
- Given the visible page, then a human-readable breadcrumb (Campaigns / <world> / Graph / <entity>) is shown and no raw ULID appears anywhere in the UI.
- Given the spike, when it concludes, then the library is chosen with the scored EQUIVALENT A/B record in research-7-1; the loser's component/test are removed from the shipped tree.

## Spec Change Log

- `[owner verdict 2026-09-19]` NO FOCUS now defaults to the world's **most-connected entity** (max degree over all committed typed edges; rowid tie-break; deterministic), not the first rowid entity — the original default degraded the demo moment in real worlds: The Drowned Harbor's first entity (The Rotting Pier) has degree 0 → 1 lonely node; the demo build's first entity (The Guttered Light) shows 2/1. URL still gains `?focus=` for the hub (refresh/back/direct-nav survive). _Avoids:_ a graph landing that reads as broken/empty on real data. _KEEP:_ neighborhood caps, deterministic selection, `focusDefaulted` URL surfacing, the `?focus=` channel for explicit entity entry.

## Design Notes

**Both-then-one spike shape.** The AC is a choice; a single-library implementation would make the verdict faith. One shared pure `graphModel` isolates the library from the data logic, so the A/B is fair and cheap — same node/edge set from the same store. The `mockup-7-1-graph-view.html` (approved at CHECKPOINT 1) is the layout contract: both candidates must reproduce its node/edge presentation, not invent their own. Layout matters for the demo: Vue Flow ships NO layout algorithm (dagre/elk or hand-rolled concentric required); Cytoscape ships cose/fcose. Expected winner on paper: Vue Flow (declarative Vue reactivity is AR16's natural fit; component/CSS styling survives Epic 8's token migration; no imperative DOM bridge). Cytoscape wins if its layout quality visibly beats Vue Flow + plugin on the real demo world — the live A/B must decide and the record must name the deciding criteria.

**Direction has no field.** `EdgeExport = {src, dst, type, counter}`; commit-time direction is already folded into src/dst orientation. Arrows render src→dst — the committed truth; a seed's inbound edges point at it, matching WorldView's relation list.

**Relationship semantics = committed types.** The Drowned Harbor world's real edge mix is across all 16 types (relationship 95, member_of 51, bases_at 37, located_in 18, enemy_of 16, ally_of 12, … debt 3, grudge 1 — the 8 counter types carry `edgeLabel` suffixes like `ally_of(2)`, `loyalty(7)`). A single 1-hop window can still land on generic types (Cistern King's web is 6× relationship + 2× member_of) — that is the data, shown as-is. The visualizer's job is presentation, never semantic repair.

**Both-then-one spike shape.** The AC is a choice; a single-library implementation would make the verdict faith. One shared pure `graphModel` isolates the library from the data logic, so the A/B is fair and cheap — same node/edge set from the same store. The `mockup-7-1-graph-view.html` v2 (approved at CHECKPOINT 1) is the layout contract: both candidates must reproduce its node/edge presentation (focus, arrows, labels, tooltip, controls, breadcrumb), not invent their own. Layout matters for the demo: Vue Flow ships NO layout algorithm (dagre/elk or hand-rolled concentric required); Cytoscape ships cose/fcose. Expected winner on paper: Vue Flow (declarative Vue reactivity is AR16's natural fit; component/CSS styling survives Epic 8's token migration; no imperative DOM bridge). Cytoscape wins if its layout quality visibly beats Vue Flow + plugin on the dense 24-node fixture — the live A/B must decide and the record must name the deciding criteria.

**Entry point.** A route + query param (not an inline drawer): deep-linkable, back-button friendly, and one surface both the card action and the nav link share. "Renders in place" is preserved — same SPA, no full-page nav.

## Verification

**Commands:**
- `npm test` — expected: green incl. graphModel + GraphView rows
- `make lint && make typecheck` — expected: clean (backend untouched)
- Browser demo against the dev stack (mythos-api :8000, web :5173): world view → entity "See web" → graph renders → node click refocuses; screenshot evidence at each step.

**Manual checks — interactions (live browser evidence, both candidates where noted):**
- Click/tap a node refocuses; `?focus=` updates without reload; direct nav + refresh restore focus; back/forward restore prior focus states.
- Mouse drag pan + wheel zoom; touch pan + pinch zoom; keyboard pan/zoom equivalents.
- Node drag does not accidentally trigger refocus and never mutates entities/edges.
- Truncation is deterministic on the dense fixture (`>23 eligible neighbors`).
- Type + relationship filters; filtered-empty state; labels toggle.
- Reciprocal/parallel edges stay distinguishable; hover/selection highlights the full edge with the source → type(counter) → target tooltip.
- All states exercised live: loading, empty, not-found, no-relationships, filtered-empty, renderer-error.
- Performance at 24 nodes stays responsive; core info keyboard/screen-reader accessible (node/edge labels exposed to the a11y tree).

**A/B protocol (research-7-1):**
- Both candidates: same dense 24-node fixture + same real world, same viewport, same interactions, equivalent node/edge content — desktop AND mobile.
- Record: equivalent screenshots, interaction failures, pan/zoom/pinch behavior, label readability, parallel-edge handling, focus-transition, performance observations, Vue integration effort, custom code required → scores + verdict. Then remove the loser + its test and dependency.

## Suggested Review Order

**Data flow & scope**

- The pure bounded web: one model, both-direction 1-hop, cap semantics with truncation flags
  [`graphModel.ts:84`](../../frontend/src/components/graph/graphModel.ts#L84)
- The AR6 cap constant the whole render scope hangs off
  [`graphModel.ts:28`](../../frontend/src/components/graph/graphModel.ts#L28)
- Store-owned portrait convention — the third hand-rolled copy eliminated
  [`world.ts:152`](../../frontend/src/stores/world.ts#L152)

**View shell (GraphView)**

- Cold-load `?focus` watcher — the deep-link/refresh/back-forward contract
  [`GraphView.vue:70`](../../frontend/src/components/graph/GraphView.vue#L70)
- Refocus = one router.replace on the focus channel
  [`GraphView.vue:92`](../../frontend/src/components/graph/GraphView.vue#L92)
- HUD renders the FILTERED web counts, truncation only when unfiltered
  [`GraphView.vue:365`](../../frontend/src/components/graph/GraphView.vue#L365)

**Winner renderer (Vue Flow)**

- Hover tooltip + highlight on the REAL events (edgeMouseEnter/Leave)
  [`VueFlowGraph.vue:289`](../../frontend/src/components/graph/VueFlowGraph.vue#L289)
- Reset restores the captured initial viewport, not a silent fit
  [`VueFlowGraph.vue:211`](../../frontend/src/components/graph/VueFlowGraph.vue#L211)
- Drag-guard so node drag never refocuses
  [`VueFlowGraph.vue:243`](../../frontend/src/components/graph/VueFlowGraph.vue#L243)
- Keyboard-activatable node card (Enter/Space), avatar/portrait + kind chip
  [`GraphNodeCard.vue:57`](../../frontend/src/components/graph/GraphNodeCard.vue#L57)

**Entry points**

- Per-entity "See web" carries `?focus=` into the graph
  [`WorldView.vue:1429`](../../frontend/src/views/WorldView.vue#L1429)
- Section-nav "Open graph" lands on the view
  [`WorldView.vue:1306`](../../frontend/src/views/WorldView.vue#L1306)
- Auth-guarded route with `?focus` passthrough
  [`router.ts:38`](../../frontend/src/router.ts#L38)

**Tests**

- Cap-boundary behavior: 23 stay, 24 truncate (replaces the tautologies)
  [`graphModel.test.ts:211`](../../frontend/src/components/graph/graphModel.test.ts#L211)
- Cold-mount default-focus URL surfacing (the patch-2 regression guard)
  [`GraphView.test.ts:323`](../../frontend/src/components/graph/GraphView.test.ts#L323)
- Dense-fixture determinism + NO FOCUS default
  [`graphModel.test.ts:71`](../../frontend/src/components/graph/graphModel.test.ts#L71)
- Selective label visibility contract
  [`VueFlowGraph.test.ts:76`](../../frontend/src/components/graph/VueFlowGraph.test.ts#L76)

**Spike record**

- The A/B fixture that decided the library
  [`denseFixture.ts:1`](../../frontend/src/components/graph/denseFixture.ts#L1)
- Decision record: scores 42–29, evidence PNGs, loser-removal checklist
  [`research-7-1-graph-visualizer.md:1`](research-7-1-graph-visualizer.md#L1)
- Approved layout contract the renderer matches
  [`mockup-7-1-graph-view.html:1`](mockup-7-1-graph-view.html#L1)