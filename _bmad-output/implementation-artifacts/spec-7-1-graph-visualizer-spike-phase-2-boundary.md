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
- **The whole world renders, always**: every committed entity is a node and every committed edge is drawn (labels hidden), whatever the count — no entity cap in the view. AR6's depth/entity caps remain the backend retrieval/generation contract (untouched); they no longer bound the visualizer's render scope (owner verdict 2026-09-19).
- Layout is a deterministic **concentric ring per kind in SEMANTIC order** (owner verdict 2026-09-19): characters innermost, factions middle, places outermost, unknown kinds trailing; ring radius = count × pitch / 2π enforced outward-monotonic (a dense inner ring pushes sparse outer rings out — rings never invert); world fit on mount; the layout is STABLE — focusing never moves or re-lays-out any node.
- **Focus semantics:** clicking a node (or a deep link `?focus=<eid>`) highlights the entity + its 1-hop relations (both directions — committed src/dst orientation) with edge labels; every other node/edge dims (clearly readable as greyed, still visible). **The focused entity stays exactly where it is** — focus is highlight/dim only, never a reposition (owner verdict 2026-09-19). Clicking another node refocuses. **Clicking empty canvas clears the focus**: URL drops `?focus=`, the whole world returns to full opacity and unhighlighted — "like it started".
- Edge labels are OFF by default; the toolbar Labels toggle shows all of them.
- **Relationship semantics are the committed types, verbatim.** Labels use `edgeLabel(type, counter)` (counter suffix only on the 8 `COUNTER_TYPES`; others bare). Never remap, normalize, or invent a type.
- Direction legibility: arrowheads sized/contrasted, stopping outside node borders; reciprocal and parallel edges render as separate curved paths; hover/selection highlights the full edge and shows source → type(counter) → target.
- Focus treatment is node-attached — stronger border + restrained halo — never a layout-wide ring or boundary shape.
- Entity types distinguishable without reading the badge: kind glyph/icon, portrait or initial avatar (portrait when `media` exists), color as reinforcement only.
- Visible controls: zoom in/out, fit view, reset view, entity-type filter, relationship-type filter, labels toggle — operable with mouse, keyboard, and touch; no hover- or wheel-only affordances.
- Visible breadcrumb (Campaigns / `<world>` / Graph / [`<entity>`]) — human-readable; raw ULIDs never displayed, even though the URL keeps `?focus=<ulid>`.
- Counts are user-facing: the HUD shows the actual rendered web ("N entities · N relationships").
- A/B equivalence is historical (research-7-1): the shipped view renders with the winner only.
- Route is auth-guarded (existing `beforeEach` pattern); view styling is scoped CSS consistent with current views — no bespoke design system Epic 8 must redo.
- Runs before the Epic 6 beta gate (owner swap 2026-09-19); epic/story IDs stay stable.

**Ask First:** If the spike surfaces a fork that the scoring criteria do not resolve (e.g. layout plugin choice materially changes the verdict), HALT and ask. The library decision itself is the story's AC, not a human gate.

**Never:**
- No store writes, no revision, no event (AD-1) — visualization is read-only.
- No graph-model changes; no new entity/edge shape fields.
- Never invent, normalize, or remap relationship types/semantics; never fabricate counters or directions.
- Never display raw ULIDs in the UI (breadcrumb is human-readable; `?focus=` stays internal).
- No entity cap or truncation in the view — the whole committed world renders.
- No re-layout on focus change — the concentric layout is computed once per world and stays stable.
- No hover- or wheel-only interactions; controls work with keyboard and touch.
- Do NOT ship both libraries in the final bundle: the loser's component + its test are removed (git history + research-7-1 keep the evidence).
- No UI e2e (AR22): owner dogfoods; verification is a live browser demo + unit tests.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | committed world, `?focus=<eid>` exists | canvas shows EVERY entity + EVERY committed edge (unlabelled); the focus entity + its 1-hop relations (both directions) highlighted with labels; everything else dimmed; click another node refocuses (URL `?focus=` replaces) | N/A |
| NO FOCUS | `/graph` without `?focus` | the WHOLE world renders, no highlight, no dimming — "like it started" | N/A |
| EMPTY-SPACE CLICK | focus active, user clicks empty canvas | focus cleared: URL drops `?focus`, all nodes/edges return to full opacity, unhighlighted | N/A |
| MISSING FOCUS | `?focus=<ulid>` not in the world | whole world renders unfocused + a non-blocking "focus not found" notice | notice, not an error |
| EMPTY / NOT FOUND / LOADING | no entities; `store.notFound`; fetch in flight | existing WorldView empty/notFound/loader state patterns | N/A |
| RECIPROCAL / PARALLEL | two nodes with opposite or duplicate edge types | separate curved paths, visually distinct; both highlight/dim together under focus | N/A |
| FILTERED EMPTY | type/relationship filter excludes every visible element | filtered-empty message + reset hint; no crash | handled in view |
| RENDERER ERROR | candidate render throws | error state with retry, not a blank canvas | handled in view |

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
- [x] `_bmad-output/implementation-artifacts/mockup-7-1-graph-view.html` — layout mockup v2 (historical; superseded by the whole-world design).
- [x] `frontend/src/components/graph/graphModel.ts` — full-world model (supersedes the capped neighborhood): `buildWorldGraph(world, focusId|null)` → every entity as a node + every committed edge, `focusMissing` for unknown ids, and a `oneHop` set ({nodes, edges} 1 hop both directions from the focus) that drives highlight/dim; `concentricLayoutByKind(nodes)` → deterministic ring-per-kind positions (characters inner / factions middle / places outer; radius from count; stable). Label/curve machinery (`edgeLabel`-based labels, `parallelSlots`, curvature, avatar/kind style) preserved.
- [x] `frontend/src/components/graph/denseFixture.ts` — repurposed: still the deterministic dense A/B/test world (29 entities / 32 edges fixture), truncation constants removed.
- [x] `frontend/src/components/graph/GraphView.vue` — view shell: store-only reads; focus from `?focus=` (deep link) or node click (router.replace); empty-space click clears focus (URL drops `?focus=`); states loader / notFound / error / empty-world / missing-focus-notice / filtered-empty / renderer-error; breadcrumb; toolbar (zoom/fit/reset, kind + relationship filters over the FULL world, labels toggle — default OFF).
- [x] `frontend/src/components/graph/VueFlowGraph.vue` (winner, @vue-flow/core) — renders the full world: concentric stable positions, node cards for every entity, edge labels hidden by default, focus = highlighted 1-hop (arrows + labels, full opacity) with the rest dimmed (grey, still visible), click node refocus, pane-click clears focus, hover tooltip source → type(counter) → target, drag-guarded refocus, touch pan/pinch.
- [x] `frontend/src/router.ts` — auth-guarded `graph` route (+`?focus` passthrough) — already shipped.
- [x] `frontend/src/views/WorldView.vue` — "See web" / "Open graph" entry points — already shipped.
- [x] Tests — `graphModel.test.ts`: full-world nodes/edges, oneHop both directions, missing focus, determinism of the concentric layout, label/curve rows preserved; `GraphView.test.ts`: deep-link focus, refocus on node click, empty-click clears + URL drops focus, cold-load with/without focus, states, HUD full counts, keyboard/zoom, labels toggle; `VueFlowGraph.test.ts`: all entities render, dim/highlight classing under focus, pane-click clear, labels off by default, drag-guard, hover tooltip.
- [x] `_bmad-output/implementation-artifacts/research-7-1-graph-visualizer.md` — decision record (historical, keep) + whole-world amendment note.

**Acceptance Criteria:**
- Given a Phase-1 world, when the DM opens the graph, then EVERY committed entity and EVERY committed edge renders (no cap) in the concentric ring-by-kind layout; edge labels are hidden; the HUD shows the full "N entities · N relationships".
- Given the graph view, when it renders, then every datum shown comes from Pinia (the world store's snapshot) — no view-local API calls or private caches.
- Given a node, when clicked, then it becomes the focus: the entity + its 1-hop relations (both directions, committed types) highlight with edge labels, everything else dims, and `?focus=` replaces without reload; another node click refocuses; direct nav/refresh restore the focus; back/forward restore prior states.
- Given an active focus, when the DM clicks empty canvas, then the focus clears: URL drops `?focus=`, the whole world returns to full opacity — unhighlighted, as it started.
- Given an edge hover or selection, then a tooltip/details shows source → type(counter) → target alongside the highlight.
- Given the type/relationship filters, then the visible subset updates accordingly and a filtered-empty state renders with a reset hint; labels toggle on/off (default OFF).
- Given keyboard or touch input, then zoom in/out, fit, reset, and pan work without mouse-wheel or hover (nodes are draggable without accidentally triggering refocus, and dragging never mutates world state).
- Given a candidate render failure, then an error state renders with retry — never a blank canvas.
- Given the visible page, then a human-readable breadcrumb (Campaigns / <world> / Graph / [<entity>]) is shown and no raw ULID appears anywhere in the UI.
- Given `?focus=<ulid>` that exists in no world, then the whole world still renders with a non-blocking "focus not found" notice.
- Given a world with no entities, then the empty-world state renders; the layout never re-runs on focus change (stable positions).

## Spec Change Log

- `[owner verdict 2026-09-19]` **Focus never repositions + SEMANTIC ring order:** (1) the focused entity no longer jumps to the graph center — it stays in its ring position; focus is highlight/dim + labels only. (2) Rings are now ordered by kind semantics, not count: **characters innermost → factions middle → places outermost** (unknown kinds trailing, first-seen order); radii stay outward-monotonic, so a dense inner ring pushes sparse outer rings further out (rings never invert). Center slot reservation removed from the layout (nothing sits at (0,0) anymore). _Avoids:_ the jarring focus jump and the count-ordered layout hiding the reference pattern the owner asked for. _KEEP:_ deterministic ring layout, stable positions across focus changes, highlight/dim + labels, `?focus=` channel, whole-world render.
- `[owner verdict 2026-09-19]` **Whole-world render redesign (supersedes the 1-hop neighborhood scope):** the view now ALWAYS renders every committed entity + every committed edge (labels off by default) in a deterministic concentric ring-per-kind layout (characters inner, factions middle, places outer — reference pattern). Focusing a node (click or `?focus=`) highlights the entity + its 1-hop relations (both directions) with labels and dims everything else; clicking empty canvas clears the focus (URL drops `?focus=`) back to the full unhighlighted web. AR6 caps remain the backend retrieval contract only — they no longer bound the view. Hub-default NO FOCUS (26443e7) is superseded: no focus = whole world unfocused. _Avoids:_ a view that hides most of the world behind a 24-entity window. _KEEP:_ committed-type labels, direction truth, stable deterministic layout, `?focus=` deep-linking, AR16 store-only reads, breadcrumb/controls/a11y.
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