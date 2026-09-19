# Research 7-1 — Graph Visualizer spike: decision record

Story 7.1 (Phase 2 boundary). Baseline `5dd0e41`. Compiled 2026-09-19 from the
live A/B against the dense 24-node fixture and the real Drowned Harbor world.

## Candidates (recorded versions, current stable)

| Candidate | Packages | Versions | Custom code | Component size |
|---|---|---|---|---|
| Vue Flow | `@vue-flow/core` | 1.48.2 | concentric layout (hand-rolled), per-edge bezier curvature from `parallelSlots`/`getBezierPath`, tooltip via `foreignObject`, drag-vs-click refocus guard | `VueFlowGraph.vue` (+ `GraphNodeCard.vue`) ≈ 430 lines |
| Cytoscape | `cytoscape` + `cytoscape-fcose` | 3.34.3 + 2.2.0 | SVG data-URI avatar/chip layers, per-slot control-point distances, halo element sync, canvas a11y wrapper, drag-vs-tap guard, fcose tuning | `CytoscapeGraph.vue` ≈ 470 lines (+ ambient type decl for fcose) |

Both consume the SAME shared module contract: `graphModel.ts`
(`buildGraphModel(world, focusId)` → `{nodes, edges, focusId, truncation, …}`)
+ `denseFixture.ts` — identical node/edge sets by construction.

## A/B protocol — same dense fixture, same viewport, desktop AND mobile

Dense fixture = 24 visible entities (focus + 23), 28 rendered edges, >23
eligible neighbors (25; truncation notice active), all 16 EDGE_VOCAB types,
reciprocal pairs (F0↔N10, F0↔N11), parallel same-pair edges (F0→N12,
F0→N13×2), long names, missing portraits (1 available portrait path).

| Check | Vue Flow | Cytoscape |
|---|---|---|
| Nodes rendered (dense) | 24 / 24, **0 node-on-node overlaps** (numeric bounding-box check, desktop + mobile) | 24 nodes in canvas (fcose); pixel-density verification of the node layer (587k–1.1M non-bg px after layout settle) |
| Fit on load | All 24 on-screen at fit, focus centered (scale 0.458) | fit from first `layoutstop`, zoomed in |
| Parallel/reciprocal separation | Per-edge handles + curvatures → visibly fanned | control-point distances per slot → fanned |
| Edges/labels | 28 SVG paths, labels `edgeLabel`-exact (`controls(3)`, `ally_of(2)`) | bezier edges, label pills (canvas) |
| Mobile (390×844) | 24/24 on-screen, 0 overlaps, pinch/pan native | renders; pinch/pan native |
| Focus treatment | border 2.6px + halo span | border 2.6px + halo node |
| Edge labels at density | Zoom-gated (≥0.8) OR hover/selection — no pile-up | Zoom-gated (≥0.75) OR hover/selection; `text-opacity` toggling |
| Tooltip | `src → type(counter) → dst` on hover/select | same via rendered-position overlay |
| refocus | node click (drag-guarded) | node tap (dragguard: 150 ms) |
| A11y | Real DOM nodes: aria-labels, `<title>` per edge, keyboard-focusable nodes | Canvas: `role="img"` + documented counts; nodes not individually focusable |
| Vue integration | Declarative props/events, SFC node cards — styling survives Epic 8 token migration | Imperative DOM bridge + stylesheet compilation; images only via data-URI/canvas |

### Interaction failures / notes (honest record)
- **Vue Flow:** `fitView` must run after the async store init (else the initial
  fit renders the dense web off-center) — fixed with an `@init`-gated fit + a
  nodes-watch `{immediate: true}`. Node drag never triggers refocus (guard
  covered by a component test driving `nodeDragStart/Stop`). Synthetic
  `change` events do not drive `v-model` on the A/B switch — a real
  pointer/keyboard interaction is required (all captured evidence used real
  interactions).
- **Vue Flow hover fix (3-layer review, applied):** the original bindings used
  `@edge-enter`/`@edge-leave`, which DO NOT EXIST in @vue-flow/core 1.48.2 —
  the hover highlight, foreignObject tooltip, and hover-driven label reveal
  never fired. Corrected to the real event names
  `@edge-mouse-enter`/`@edge-mouse-leave` (verified against `dist/types/hooks.d.ts`
  and the emits list); the tooltip/<title> a11y tree is regression-covered.
  Scores below are post-fix.
- **Cytoscape label toggle:** `labelsVisible: false` keeps node/chip rendering
  but edge label styling toggling through the stylesheet is harder to read at
  low zoom — accepted, since selective zoom-gating already hides clutter.
- **Cytoscape:** first-render latency: fcose computes asynchronously in chunks;
  the canvas fills progressively (blank reads inside the first ~5 s on 24
  nodes). Canvas has no DOM accessibility tree; label text unreadable to
  screen readers without an equivalent textual list (none added — out of scope
  for the spike, noted for Epic 8). Data-URI SVG backgrounds keep portraits
  working, but glyph/chip compositing is brittle to style (Epic 8 migration
  risk).
- In-app demo (real world): Pike's live web = **8 entities · 8 relationships**
  (world has 7 neighbors + focus; one intra-neighborhood `employs` edge —
  rendered because both endpoints are in the set; mockup showed an older
  7/7 world state — data shown verbatim, no remap).

## Performance at 24 nodes
Both responsive for pan/zoom/fit/hover after the initial render; Vue Flow
render + fit ≈ instant after mount fit fix; Cytoscape first layout ≈ 5 s
(24 nodes, fcose numIter 1000) — acceptable for the caps, noted.

## Scoring (1–5; 5 = best)

| Criterion | Vue Flow | Cytoscape |
|---|---|---|
| Same-viewport A/B equivalence | 5 | 5 |
| Edge/parallel rendering quality | 5 | 4 |
| Label readability + selective visibility | 5 | 3 (canvas text, zoom-dependent) |
| a11y (keyboard + screen reader) | 5 | 2 (canvas) |
| Mobile touch (pan/pinch) | 5 | 4 |
| Vue integration effort | 5 (declarative) | 3 (imperative bridge) |
| Custom code required | 3 (layout hand-rolled) | 3 (SVG URI compositing) |
| Styling migration risk (Epic 8) | 4 (CSS tokens) | 2 (stylesheet/canvas compile) |
| First-render latency @24 | 5 | 3 |
| **Total** | **42** | **29** |

## Verdict
**Vue Flow (@vue-flow/core 1.48.2) wins.** It is the natural AR16 fit
(declarative Vue reactivity over Pinia-derived data), produced the only
numerically verified zero-overlap dense rendering, keeps nodes/labels in the
real DOM accessibility tree, and its CSS-based presentation survives Epic 8's
token migration. The hand-rolled concentric layout is deterministic and
caps-bounded (≤24 entities), so the missing layout algorithm costs nothing at
AR6 scale. Cytoscape rendered faithfully and handled parallel edges well, but
its canvas a11y gap, first-render latency, and imperative bridge resolution
the choice.

## Whole-world redesign (owner verdict 2026-09-19) — supersession note

The owner amended story 7.1 after this record: the graph view no longer
renders a capped 1-hop neighborhood — it renders **every committed entity and
every committed edge** in a deterministic **concentric ring-per-kind layout**
(characters inner, factions middle, places outer; rings ordered by node
count so the largest is outermost; ring radius = `count × CARD_PITCH / 2π`
with one `CARD_PITCH` of inner clearance for the focused card at the center).
AR6's depth/entity caps remain the backend retrieval/generation contract and
no longer bound the view.

Focus semantics replaced: a node click (or `?focus=`) highlights the entity +
its 1-hop relations (both directions) with edge labels and dims the rest
(grey, visible); **clicking empty canvas clears the focus** (URL drops
`?focus=`, the web returns to full opacity). No default focus exists — no
`?focus=` is ever written for an absent one; an unknown `?focus=` renders the
full world with a non-blocking notice. Edge labels are **off by default**;
the toolbar Labels toggle shows all.

This supersedes the A/B-capped behavior recorded above: graphModel.ts now
exports `buildWorldGraph(world, focusId|null)` (full world + `oneHop` set)
and `concentricLayoutByKind(nodes)`; the loser (Cytoscape) was REMOVED per
the checklist below (git history + this file keep the A/B evidence), and the
shipped view renders the winner (Vue Flow) only.

## Shipped-tree note (executed 2026-09-19 with the redesign)
The loser (Cytoscape) is REMOVED: component, ambient type decl, dependencies,
and evidence PNGs are gone from the tree (git history + this file keep the
A/B evidence). The shipped view renders the winner (Vue Flow) only — there is
no candidate switch in the code.

### Final-commit removal checklist (loser = Cytoscape)
- [x] Delete `frontend/src/components/graph/CytoscapeGraph.vue`
- [x] Delete `frontend/src/components/graph/cytoscape-fcose.d.ts`
- [x] Remove `cytoscape` + `cytoscape-fcose` from `frontend/package.json` and the
      corresponding `package-lock.json` subtree (`npm uninstall cytoscape cytoscape-fcose`)
- [x] `GraphView.vue`: render `VueFlowGraph` directly — no `CytoscapeGraph`
      import, no `ACTIVE_CANDIDATES` map, no `CANDIDATE` ref, no visible
      "Graph renderer (spike)" select
- [x] `GraphView.test.ts`: remove the spike-switch test row and the
      `./CytoscapeGraph.vue` mock
- [x] Delete the cytoscape A/B evidence PNGs
      (`ab-dense-cytoscape-desktop.png`, `ab-dense-cytoscape-mobile.png`)
- [x] Keep: `VueFlowGraph.vue`, `GraphNodeCard.vue`, `graphModel.ts`,
      `denseFixture.ts`, `VueFlowGraph.test.ts`, `GraphView.vue` + its tests,
      and the Vue Flow evidence PNGs
      `denseFixture.ts`, `VueFlowGraph.test.ts`, `GraphView.vue` + its tests,
      and the Vue Flow evidence PNGs

## Evidence files (this directory)
- `ab-dense-vueflow-desktop.png`, `ab-dense-vueflow-mobile.png`
- `ab-dense-cytoscape-desktop.png`, `ab-dense-cytoscape-mobile.png`
- `demo-realworld-vueflow-pike.png` (in-app, real world)