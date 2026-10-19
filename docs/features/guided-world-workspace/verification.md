# Workspace implementation verification

The accepted Guided Build / persistent World workspace v6 is implemented in Vue and the private FastAPI/store pipeline. The exact approved HTML, CSS and JavaScript remain frozen in `docs/ui-designs/2026-10-18`; production code uses real campaign, entity, job and receipt identities.

## Automated evidence

- Full backend regression suite: 1,864 tests pass (one existing Starlette/httpx deprecation warning).

- Node 22.23.3: 535 frontend tests in 47 files, typecheck, production build and ESLint pass. ESLint reports 12 pre-existing warnings.
- 48 dedicated backend workspace checks cover draft persistence, owner-only GET and all mutations, strict schemas, invalid targets/cycles/collisions, optimistic conflicts, idempotency, selected dependencies, graph/receipt rollback, restart recovery, bounded context, typed planning, cancellation, and real generation/repair gates with a deterministic provider.
- The checked-in TypeScript API schema matches regenerated OpenAPI types byte for byte.
- Frozen archive verification passes: 86 files, seven source/extracted sets and Node 22 JavaScript syntax.

## Browser evidence

An isolated backend/database and Vite frontend exercised the actual API, queue, generation pipeline, store commits, receipts and Vue reconciliation. A deterministic provider supplied content; this is not a live-model quality or latency evaluation.

1. Open an established Harbor campaign directly in its workspace.
2. Add Market under Harbor; add Rook under the planned Market. The new name input receives focus.
3. Save and reload; both stable draft identities, names and nested placement return.
4. Submit generation and run the real pipeline with the deterministic provider. Market and Rook become linked existing entries; the draft clears through receipts.
5. Select the generated Market, add Edda, save and generate a second expansion. Harbor, Market and Rook remain unchanged; Edda is linked to Market.
6. Show the synchronized graph and readable relations. Check 1440px desktop columns and 390px/320px mobile layouts: no page overflow or browser errors. The embedded graph uses the existing Graph view’s pan/zoom controls.
7. Reopen the initial Guided Build section: counts, named/shaped entries, authored figures and notes remain available. Detailed authored recovery and initial completion transitions are also covered by component tests.

[Desktop capture](desktop.png) · [Mobile capture](mobile.png)

## Review resolution

Three independent review passes examined the full implementation diff. Applied fixes cover historical proposal ordering/dismissal, planning error feedback, save draining and response fences, selection/Undo receipt promotion, raw authored identity/prose/mechanics/relation recovery, strict planning output schema, expansion-wide guidance, target coverage and structured-context bounds. Missing private mutation, successful proposal application and detailed recovery regression tests were added. Responsive media-query boundaries were restored and checked in the browser.

Generic submission already rejected workspace kinds at its HTTP schema; the public store enqueue path now rejects forged workspace requests as well. No unresolved findings remain from these passes.

## Operational behavior

Generation checkpoints the entire selected scope atomically. Failures before commit retain every selected draft entry; completed receipts prevent replay after a crash. Draft conflicts preserve local edits and require explicit reload. Browser exit protection covers pending saves. Proposal dismissal is scoped to account/campaign in the current browser; server drafts and receipts remain authoritative across browsers.

Deployment must use the normal checked develop-to-main release path. This branch has not been merged or deployed.

## Shared graph correction

The workspace now embeds the same production Graph view, Vue Flow renderer,
layout, node cards and controls as the standalone Graph page. Its adapter only
projects planned nodes and placement/membership edges; it does not draw a graph.
Planned cards carry text labels and dashed borders, and planned edges are dashed.
Existing portraits and canonical relationship labels use the normal graph path.
The readable relationship list remains available as a disclosure.

Browser checks rendered six existing nodes/six edges, then added a nested planned
place and character for eight nodes/eight edges. Clicking a planned card updated
the editor selection without writing URL focus; type filtering showed the correct
four places, and zoom changed the shared viewport. The standalone page retained
six canonical nodes, zero planned nodes, and its normal breadcrumb/navigation.
Fresh mobile mounts at 390px and 320px showed no page overflow.

Added regression checks cover overlay identity collisions and dangling links,
unchanged canonical exports, planned-node focus, renderer status/dashes, and
rename/move/membership/removal synchronization through the shared component.

[Shared graph desktop](shared-graph-desktop.png) · [Shared graph mobile](shared-graph-mobile.png)
