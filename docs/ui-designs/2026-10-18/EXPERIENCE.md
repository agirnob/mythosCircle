---
status: draft
updated: '2026-10-18'
topic: 'mythosCircle website cohesion — route and state coverage'
sources:
  - DESIGN.md
  - .memlog.md
  - .working/history/DESIGN-2026-09-25.md
  - .working/history/EXPERIENCE-2026-09-25.md
  - ../../../../frontend/src/router.ts
  - ../../../../frontend/src/components/shell/AppShell.vue
  - ../../../../frontend/src/views/TonightView.vue
  - manifest.json
---

# EXPERIENCE.md — website cohesion

## Foundation

Vue 3, TypeScript and Pinia web application for a Game Master. [DESIGN.md](DESIGN.md) owns visual identity and token definitions. The owner’s current direction is to keep Overview, Tonight and World as the modern anchors and make the remaining surfaces equally coherent and finished. **Latest owner direction supersedes the earlier first-draft selection:** Forge stays as it is; redesign Guided Build, Add Character and Your Worlds. Make custom lore more inviting and reconsider the one-by-one resume list. A dedicated private Library for **Generic characters only for now** is selected; its page/route is not implemented. The current exploration separates initial Guided Build from a persistent World workspace; nested cards and a secondary graph support AI-assisted planning for subsequent additions. Library scope is selected as private Generic characters only for now. This is a **draft design plan**, not a report of shipped UI changes or approved new layouts.

Current route/source inspection establishes the inventory below. Desktop/mobile screenshots now show the actual Vue application under a read-only synthetic fixture account/API: [Overview desktop](mockups/overview-current-desktop.png) / [mobile](mockups/overview-current-mobile.png), [World desktop](mockups/world-current-desktop.png) / [mobile](mockups/world-current-mobile.png), and [Forge desktop](mockups/forge-current-desktop.png) / [mobile](mockups/forge-current-mobile.png). These use sample data with no production/private campaigns or database/world writes. They verify current layout under fixtures, not real authentication integration. Consequential states and full interaction/accessibility QA remain pending before finalizing layout decisions. [DESIGN-PLAN.md](DESIGN-PLAN.md) gives screen briefs and the tool-led next steps.

Preserve current backend contracts, private campaign access and DM authorship. Reading, generating a staged candidate/proposal, accepting it, editing and deleting are distinct actions. Current Ask stages candidates; a separate read-only answer transport is not implemented or authorized by this cohesion pass. No visual mock authorizes a new endpoint, public campaign, persistence guarantee, automatic image generation or background world mutation. World-state writes remain the backend store’s responsibility.

## Information Architecture

Current AppShell separates Campaign (Overview, Tonight, World), Create (Ask the World, Add character, Guided Build), and Tools (Graph); outside a campaign it exposes Forge. Admin is conditional. Keep these distinctions for the cohesion pass; proposed reorganization needs a separate explicit decision. Campaign library entries currently open Overview. The September plan to replace Overview with Tonight and collapse creation into One Create is historical intent, not today’s implemented IA.

### Complete active-route inventory

`:id` denotes the campaign and `:entityId` the entity. Every row needs its listed artifact plus the common state coverage described below.

| Route / source | Job and proposed deliverable | Particular states to capture |
|---|---|---|
| `/` — CampaignsView | Your Worlds resume/create entry; redesigned layout and generous freeform-lore treatment. Searchable responsive resume cards are proposed, not selected. | Empty/populated, loading/list failure, create pending/failure, delete confirmation/cancel/failure, admin-denied notice. |
| `/login` — LoginView | Sign in; aligned auth form and narrow variant. | Empty, validation, pending, rejected credentials/network failure, return destination. |
| `/register` — RegisterView | Create account; companion auth form. | Empty, validation, pending, rejected request/network failure, authenticated redirect. |
| `/admin` — AdminView | Restricted user administration; targeted table/form consistency pass. | Loading/empty/populated, denied access, mutation pending/failure; actual supported operations only. |
| `/campaigns/:id/overview` — NewWorldView | Campaign synthesis; frozen anchor capture with populated and empty data. | Loading/failure, empty sections, partial media, long world title. |
| `/campaigns/:id/tonight` — TonightView | Current session journal, timeline and changes; frozen anchor and state annotations. | No session/events, session selection/create/edit, composer failure, reference notes, recovery, pagination and section failure. |
| `/campaigns/:id/world` — NewWorldView directory mode | Complete world directory; frozen anchor and filter/list patterns. | Empty world/filter, loading/failure, long records, missing portraits, exports where supported. |
| `/campaigns/:id/entities/kind/:kind` — EntitySectionView | Kind collection; consistent filter/list/header reference. | Empty kind/search, invalid kind, loading/failure, long lists and entity navigation. |
| `/campaigns/:id/entities/:entityId` — NewEntityView | Read/edit entity; kind-specific reading layout and structured editor variants. | Character/place/faction, sparse/complete data, loading/missing/denied, save/regenerate failure and conflict where supported, portrait/export/delete states. |
| `/campaigns/:id/ask` — NewAskView | Generate and review staged candidates/proposals; composer/status/review draft set. | No question/candidates, queued/running/failure, empty result where supported, multiple proposals, selected/resolved/conflicting/stale proposal. |
| `/campaigns/:id/graph` — GraphView | Read relationships and focused entity context; toolbar/canvas/inspector and readable edge-list draft. | Empty/dense graph, focus query, selected entity, filters/no matches, load/renderer failure, keyboard and narrow view. |
| `/campaigns/:id/build-in` — BuildInView | Guided city/world plan; nested cards primary, synchronized graph secondary, with AI-assisted structural planning, explicit typed edges and draft/existing identity. | Initial, valid/invalid, relation/context selection, pending/running/failed/complete, input preserved on failure. |
| `/campaigns/:id/add-character` — AddCharacterView | Zero-LLM authored character entry; grouped fields, batch navigation and review preserving actual required semantics. | Blank/partial/complete, required validation, stat/relation edits, pending/failure, returned committed entity. |
| `/forge` — CharacterForgeView | Global character Forge with destination selection; leave as is and retain as a reference. | No destination, world/Generic choice, quick/detailed/mechanics, draft restore, pending/failure/history. |
| `/campaigns/:id/forge` — CharacterForgeView | Campaign-scoped Forge; leave as is and retain campaign context/reference. | Bound destination, draft/mode changes, pending/failure/history, valid return to campaign. |
| `/campaigns/:id/candidates` — compatibility redirect | Redirect to current Ask review; navigation check rather than new screen design. | Saved deep link arrives in correct campaign Ask; login return follows redirect safely. |

WorldView.vue and CandidatesView.vue remain in source but are **dormant**, not active screen targets. There is no active `/create/capture`, `/create/author`, `/create/generate`, archive, or separate Generic-library route in the router. Generic is a Forge destination, not a promise of a new library screen. Do not design duplicate candidate queues or revive legacy WorldView by mistake.

## Voice and Tone

Calm, direct, useful. A field label names what the DM supplies; a short description explains the outcome. Keep the distinction between a proposal and a committed entity visible. Details about worker isolation, request schemas, endpoint names, AD numbers and transaction internals do not help the DM complete the task; move useful diagnostics to an expandable error detail.

| Context | Proposed copy direction |
|---|---|
| Ask | Name the current world and staged proposals clearly. Explain that acceptance changes the world according to the existing candidate contract; do not imply a separate read-only answer. |
| Authored character | “Write what you know.” State required material and generated behavior only as the current contract supports. |
| Pending generation | Actual queued/running state; no fabricated time estimate or percent. |
| Failed save/build | What failed, whether input is preserved, and the valid retry/edit action. |
| Destructive action | Name the target and actual consequence before confirmation. |
| Scope | Name the current world or Generic destination; explain whether canon will change. |

Avoid hiding actionable failure information behind “Something went wrong.” Do not promise that all drafts persist or every action is undoable without checking the implementation.

## Component Patterns

Visual anatomy is in DESIGN; shared pattern names are identical here. Patterns are a design catalog, not a list of newly implemented components.

| Pattern | Behavioral contract |
|---|---|
| Shell | Reflect current campaign and route, preserve permission gating, provide keyboard navigation and narrow-screen access; changing routes alone never commits world state. |
| Page header | State the current task and context; actions lead to existing destinations or are explicitly marked proposed. |
| Panel | Group a meaningful reading/editing task; heading and reading order remain clear without styling. |
| Button | Pending and disabled states explain availability; prevent repeated submission where necessary; await real result before showing completion. |
| Field | Persistent label; errors associated with the input; retain entered values on supported failure/retry paths. Choice tiles expose checked state with appropriate checkbox/radio semantics and keyboard operation. Editable list rows preserve entry identity and offer reachable edit/remove controls; linked Entity rows remain navigation. Do not silently discard unknown structured content. |
| Section navigation | Classify each instance in its screen brief. Section links remain links. Tabs use tablist/tab/tabpanel roles, selected state and arrow-key navigation; specify automatic/manual activation and focus behavior. Steps communicate current step and existing availability constraints without tab roles or invented wizard gates. Preserve edits when navigating as supported. Disclosures use a button or native summary, expose expanded state, toggle with Enter/Space and keep their revealed content in reading order. |
| Context summary | State destination, scope and meaningful entered choices; mirror the editor, do not invent generated content. |
| Entity row | Navigate to the entity predictably; keep nested actions independently accessible and avoid duplicate activation. |
| Proposal review | Label staged candidates/proposals and their current review state; accept/reject/regenerate actions retain their real contract, conflict and stale-state behavior. No separate answer transport is added. |
| Activity | Display received job states; reconcile reconnects using existing stores; failure leaves a clear retry or edit path. |
| Feedback | Loading, empty, failed and denied are different states; retry only when meaningful; partial failures do not erase unaffected content. |
| Dialog | Name consequence and target; trap focus, return focus to trigger, support Cancel/Esc where appropriate, and avoid stacked dialogs. |
| Graph | Keep current read/focus behavior, make selection and controls keyboard reachable, and provide a readable relation alternative; no invented edge-writing affordance. |

## State Patterns

For each inventory row, deliver the applicable **cold loading, empty, normal populated, pending, validation/error, permission/missing, keyboard focus and narrow-screen** states. Inapplicable states must be labeled with a reason in the screen brief, rather than silently omitted.

| Cross-cutting condition | Expected treatment |
|---|---|
| Network/read failure | Preserve available content, locate error near the affected task, provide a real retry or navigation action. |
| Offline / dropped live updates | Explain stale/pending truth where the current store supports it; check reconnect behavior; do not imply offline editing or automatic replay. |
| Form validation | Associate errors with fields; retain values, identify next correction, and move focus to a useful error location. |
| Job queued/running/failed/complete | Textual truth, preserved input/context where supported, no invented progress, separate proposal completion from world commit. |
| Conflict / stale proposal | Show current conflict handling and refresh/review path; no optimistic canon update. |
| No portrait / sparse record | Useful layout and honest empty sections without fake lore or automatically generated images. |
| Long content / dense lists | Wrap titles and labels, bound prose comfortably, keep actions accessible; use actual pagination contracts. |
| Signed out / expired session | Existing login guard and safe return route; do not expose private content or confusing admin controls. |
| Unsupported / denied target | Clear missing/denied message and safe path back; no public fallback. |
| Unsaved edits and destination change | Audit existing behavior before promising survival; proposal must state whether edits persist, prompt, or reset. |

## Interaction Primitives

All tasks work with keyboard and visible focus; links navigate and buttons act. Esc closes the top eligible overlay, not arbitrary editor content. Prefer progressive grouping to a large required-field wall, while preserving actual validation and payload semantics. Do not implement one-click capture, autofill, zero-LLM commit or autosave merely because a mock depicts it.

Acceptance waits for the server/store result on world mutations. The UI should preserve input on failure and distinguish retries from duplicate submissions. Image generation stays explicit. Graph views and dense tables need a non-visual reading path. Narrow-screen layouts must retain the complete task instead of hiding the review or submit action.

## Accessibility Floor

Target WCAG 2.2 AA. Use `{colors.text-primary}` and `{colors.text-secondary}` for task-bearing text, with real contrast checks from DESIGN. Proposed filled-action labels use `{colors.app}` on `{colors.interactive}` (5.27:1 opaque); white and text-primary fail for normal-size text on that fill. Proposed opaque input boundaries use `{colors.text-muted}` on `{colors.input}` (4.81:1); border and border-bright alone are insufficient. Verify actual composited states, including the skip link, rather than treating these proposed contracts as rendered fixes. `colors.text-faint` is not a label color. Focus uses `{colors.interactive-bright}` with sufficient visible geometry; selected and error states use text/semantics as well as color.

Provide semantic headings, persistent input labels, error associations, announced asynchronous status, meaningful button/link names, dialog focus management and logical tab order. Section links retain link semantics; tabs expose selected state, documented activation and arrow-key behavior; steps expose current position/availability; disclosures expose expanded state and Enter/Space operation. Respect reduced motion. Prefer 44×44 CSS px touch targets; compact targets meet 24×24 CSS px or a documented WCAG spacing/exception criterion. Verify at 320px, 390px, 768px and 1440px, 200% text resizing and 400% browser zoom/reflow at 320 CSS px width. Check wrapping, target spacing, reachable errors/actions and keyboard focus unobscured by sticky bars or overlays. Document genuine graph/table two-dimensional exceptions and retain a usable equivalent reading/task path. These are verification widths, not a new set of product breakpoints.

## Key Flows

### Yigit prepares tonight in the established workspace

1. Open a world from the library into Overview; confirm the campaign and browse the current synthesis.
2. Open Tonight, select the play session and read the journal; consult World or an entity as needed.
3. **Outcome:** the approved reference screens remain familiar while other surfaces adopt their visual language.

Failure path: an empty session or failed journal read has its own clear state; unrelated world content remains usable. Do not replace the current persisted journal with the historical local-only sheet.

### Yigit asks a question and reviews world changes

1. Open Ask the World from the campaign shell, enter a question and submit.
2. Read the staged candidates/proposals and select one for review.
3. Review one proposal and use its supported action.
4. **Outcome:** Yigit can tell what is still proposed, what was rejected and what was committed.

Failure path: a job failure preserves usable input; conflicts/stale proposals use existing reconciliation. This flow also covers saved candidate-route links arriving at Ask.

### Yigit creates a character without a wall of internals

1. Choose Add character or the existing Forge route according to the task; see the actual destination and commitment behavior.
2. Complete grouped identity/background/mechanics/relation material with clear labels; use refined Forge editing where applicable.
3. Review entered material and submit through the existing contract.
4. **Outcome:** the editor, summary, activity and destination feel like one workspace, and the resulting world/draft state is clear.

Failure path: validation and backend errors remain actionable without discarding input; draft restoration is promised only where verified. Guided Build follows the same context → edit → review → result pattern with its current supported kinds and controls.

### Yigit follows an entity into its relationships

1. Find an entity through World or a kind collection and open its detail.
2. Read kind-specific sections, then enter structured editing or a supported regeneration action.
3. Follow the graph deep link and inspect the relevant relationship context.
4. **Outcome:** reading, editing and graph navigation share coherent controls without obscuring world state.

Failure path: sparse data, missing entity, failed save, dense graph or absent relationships each has a readable recovery/fallback; deletion retains named confirmation and actual consequences.

### Yigit returns to the world library and account tools

1. Return to the library, find or create a world, and confirm its destination.
2. Sign in/register if necessary; admin tools appear only with permission.
3. **Outcome:** outer pages feel part of the same product and preserve clear access boundaries.

Failure path: creation/login failures preserve useful input; admin denial follows the existing guarded redirect and notice; delete cancellation leaves the world intact.

## Concrete Design Milestones

The latest owner selection is **Guided Build, Add Character and Your Worlds**. Forge stays unchanged. The earlier Forge/World/Overview artifacts remain style/reference evidence. The current direction uses familiar Guided Build first, then a persistent World workspace with nested cards, a secondary graph and scoped expansion planning; detailed layouts and planning API are unimplemented, and Library scope is selected as private Generic characters only for now.

| Order | Deliverable | Exit condition |
|---|---|---|
| 0. Preserve references | Existing Forge/World/Overview captures and drafts; add current target captures with sanitized data. | Reference language is retained; no Forge redesign scheduled. |
| 1. Shared catalog | Existing patterns applied to authored groups, lore prose, world resume cards and node/relation editing. | Current navy/violet, readable labels and accessible controls remain consistent. |
| 2. Target designs | Your Worlds resume/create hierarchy and lore writing; Add Character grouped authored/batch flow; Guided Build planning flow. | Desktop/mobile and consequential states reviewed; selected nested-card/secondary-graph planning direction is faithfully represented; no unsupported generation or Library promises. |
| 3. Library — selected scope | Dedicated private Generic-character browsing; proposed `/library` page; Generic excluded from Your Worlds. | Characters-only scope, private isolation and actual move semantics clear; route remains unimplemented until delivery. |
| 4. Follow-on cohesion | Ask/review, entity details/editors, graph, auth and targeted admin improvements. | Current contracts and earlier route/state inventory preserved. |
| 5. Implementation and QA | Selected designs translated to Vue in scoped branches. | Acceptance gate, relevant Node 22 local checks and required CI pass before publishing. |

**Acceptance gate for each screen:** compare against the frozen anchors; every inventory state is captured or justified; review desktop/mobile with real long and sparse content; task labels and input boundaries pass contrast checks; keyboard/focus/validation/pending/failure paths work; no unsupported controls, duplicate proposal queues, new palette, or backend promises; no loss of existing contracts or private campaign protections. Record what was accepted and remaining issues. A single attractive mock is insufficient.

Use the installed Superdesign draft workflow first, with the exact saved Forge identifiers in DESIGN; branch/reuse safely after source freshness checks. Optional 12ui exploration can supply alternative reference layouts. Use frontend-design for later Vue implementation and agent-browser for authenticated visual/interaction QA. Figma and MagicPath are discovered options, not installed dependencies. The [independent consistency/accessibility review](review-consistency-accessibility-2026-10-18.md) informed contrast and interaction requirements, and its findings are addressed in the proposed contracts. It did not approve layouts or verify rendered fixes; visual and implementation QA remain outstanding. World/Overview Superdesign baseline drafts are generated: [World](https://p.superdesign.dev/draft/ad5c99be-2b33-43de-97cd-31bd42580414) and [Overview](https://p.superdesign.dev/draft/38e0cef3-32a3-4cdf-823f-91f7bfcc2877), with source context saved in resume records. Both local baselines were rendered at desktop/mobile widths and measured no horizontal document overflow at 390px; this is limited layout evidence, not full accessibility or interaction approval. Forge reuses inspected version 7. The exported [Forge v7 prototype](mockups/forge-superdesign-v7.html) has [desktop](mockups/forge-superdesign-v7-desktop.png) and [mobile](mockups/forge-superdesign-v7-mobile.png) captures. Limited prototype checks found name/concept preserved through Quick → Detailed → Quick and no horizontal overflow at 390px; these do not verify actual Vue interaction, backend behavior or full accessibility.

## Target Flow Contracts and Capability Boundaries

**Your Worlds and lore.** Resume and create are separate visual tasks. Searchable responsive cards are the proposed resume layout, with Continue primary and management secondary. Do not claim recency fields or sorting not available in current data. Creation gives `custom_lore` a generous freewriting surface. Optional visual chapter prompts must preserve the existing exact text, remain optional and never turn lore into required structured fields. Keep a single freeform editor available and preserve long writing on validation/network failure. A new sectioned storage schema is outside this design pass.

**Add Character.** This is authored zero-LLM submission with existing required fields and batch behavior. Grouping can reduce the visible wall, but it must not weaken validation, rewrite authored text, or imply missing sections generate automatically. Batch navigation identifies each draft and retains edits; final review identifies which characters will be submitted and any missing required material. Include partial/invalid second characters, stat/relation changes, pending/failure and actual committed result.

**Guided Build and World workspace.** Yigit first generates a foundation using the familiar builder. The persistent workspace then shows actual world content and supports scoped additions to a city, its places/factions and their characters. Nested cards are primary, the synchronized graph secondary, and AI structural planning helps plan later expansions. The nested-card editor provides the complete keyboard-readable textual planning path. Cards and graph share one underlying draft-ID model: edits, selection, rename, relation changes and removal synchronize immediately rather than maintaining independent copies. Draft nodes and existing world targets are distinct choices, with explicit kind, context and edge semantics. Show child place `part_of` city, faction `bases_at` place, character `located_in` place or `member_of` faction only where kind rules permit. Visual grouping is not an implicit world edge.

The [source feasibility audit](nested-build-feasibility.md) establishes these boundaries:

- Backend input already supports a **flat keyed roster**, using stable seed `key`, draft `target_key` and committed `target_id`; named nesting does not require a new nested payload. Current GUI submission loses these stable IDs and uses names, so the proposed planner needs a new adapter. Renaming/reordering must preserve relationships; removing a node explains affected edges and offers reassignment, without silent cascade.
- Validate unique draft keys, valid references and kind pairs before generation. Names are not identities: normalized same-kind place/faction names can update/merge existing entities and duplicates fold together; characters create fresh ULIDs. Show collisions and possible updates explicitly. Distinct same-named places/factions need a backend merge-contract decision, not a prettier picker.
- `generate_counts` is global by kind, not per parent. “Generate four people for this tavern” needs an allocation adapter or contract change; do not offer it as currently supported. Named explicit people and edges can be planned today.
- Pre-generation preview shows intended inputs, counts, typed edges and unresolved references. Current Guided Build **directly commits generated waves**, rather than staging generated output for acceptance. Cancellation after wave 1 can leave committed core content. Never promise no world change until generated-preview acceptance or all-or-nothing cancellation.
- Results should identify created/updated/merged/skipped/failed material, but a complete draft-key-to-final-ULID result map is a new requirement; existing reports are only partial foundations. Keep all world writes in the backend store.

**Library — selected scope.** The owner selected a dedicated private Library for **Generic characters only for now**. `/library` is the proposed new route and is not active in the current router. Your Worlds should show real campaigns only, excluding Generic. Current Generic is private per-account character storage isolated from narrative world context; that storage remains unchanged until implementation. Show empty/populated/search-no-match/loading/error/access states and accurate transfer feedback. Places/factions/starters, connected collections and keep-in-Library copies are outside initial scope. Current move-to-world assigns a fresh ULID and drops source edges; destination commit and source deletion are separate. Do not call it atomic or a reusable copy.

**Yigit’s proposed target journey:** 1. Find a world quickly or open its creation area and write lore freely. 2. Plan the city’s named places/factions/people with explicit relationships. 3. Review input scope, collisions and direct-commit consequences, then submit. 4. Inspect actual build results; add fully authored characters through the separate zero-LLM path when desired. **Outcome:** the plan has comprehensible relationships and authored intent survives. Failure paths cover unresolved/duplicate keys, name collisions, invalid edges, partial build/cancellation, long lore and invalid batch members. This is a proposed journey following the selected planner direction, not shipped interaction.

### Guided Build and persistent World workspace — current direction

The owner approved exploring a two-section flow after rejecting the combined seed-and-planning form. **Guided Build generates the initial foundation; the World workspace then supports ongoing expansion from actual world content.** The workspace is available again on later visits. It is not an obsolete snapshot of the original generation request.

The sequence is **familiar Guided Build inputs → initial content generation → actual world workspace → select an existing place → propose/manual-plan additions → edit structure → generate only those additions → refresh actual world and clear completed planned items**. Nested cards and a keyboard-readable outline are primary; the synchronized graph is a secondary view of existing entities and unfinished additions. Existing content carries a solid border and explicit Existing label; additions carry a dashed border and explicit Planned label. Color alone does not distinguish them.

AI structure suggestions operate inside the expansion section. Requesting or applying a suggestion changes no committed content and enqueues no content-generation job. Suggestions bind to the selected existing target, world revision and current plan revision; late responses cannot replace intervening edits. Manual counts produce explicit scoped slots, which can remain model-named. Names, place assignments and faction memberships stay editable before generation. Names remain labels; identity uses draft keys or existing entity IDs. Containment and membership are distinct typed edges.

The first build retains its current per-kind additional counts, named/list/shaped entries, partial authored figures and lore/notes generation context. Returning to the first section retains useful inputs, but must not silently resubmit a consumed build. Existing campaigns can enter the World workspace directly. An empty workspace offers initial Guided Build; it does not invent existing content.

Expansion generation submits unfinished additions and their links only, with selected world entities as context/targets. Do not submit existing records as new seeds. Current name-based place/faction matching can update existing records, so the new additions-only contract must resolve normalized name collisions explicitly before submission; the concept blocks them and asks for a rename or use of the existing target. A different explicit edit/update operation remains separate. Global counts alone cannot implement exact per-parent allocations; compile them into keyed draft entries with links, or add a validated allocation adapter.

After generation, refetch the actual resulting entities and edges. Reconcile stable draft keys to committed IDs from confirmed results; remove only confirmed completed plan items. Partial failure or cancellation retains unfinished items and reports committed results accurately. Retry must avoid creating duplicates or replaying completed items. Existing Guided Build commits generated waves directly; structural approval does not introduce a generated-prose acceptance stage or atomic rollback guarantee. All world writes remain through the backend store, and access stays private to the invited account.

The two-section layout is a reviewable design proposal, not shipped behavior. The current prototype uses fixed sample generation and local state. The typed planning service, scoped input/result mapping, name-collision enforcement, recovery and persistent committed-plus-draft workspace require implementation.


## Deferred Intent and Open Decisions

The [unchanged September snapshot](.working/history/EXPERIENCE-2026-09-25.md) retains the fuller historical specification, including its assumptions. Preserve its useful ambitions without turning them into claims of current scope:

- One Create / Capture → Author → Generate unification, associated routes and one shared stepper/rail: deferred IA decision; current route separation remains.
- Archive/restore, separate Generic-library browsing and sandbox copies: verify contracts and product value before adding surfaces.
- Global Cmd-K palette and common searchable pickers: candidate shared work, not assumed shipped.
- Saved templates, registry expansion/note-slot citizenship, offered companions/populate and universal dials: verify current implementation and scope independently; do not mint unavailable controls.
- Separate read-only Ask answers: deferred product/backend decision; current NewAskView stages candidates/proposals and the cohesion pass keeps its generation/review contracts.
- Scene-scoped Night ask thread and its ephemeral transport: historical proposal; current Tonight uses play sessions, a journal, timeline, reference notes and changes. Do not overwrite that model.
- “Take it back,” knowledge states, consequence verbs, local ego graphs and export consolidation: retain the intent, inspect current behavior per screen, and separate existing implementation from gaps.
- Historical exclusions of timeline and DM/reference notes are superseded by current Tonight implementation; VTT export, credits, light theme and automatic images are not authorized by this cohesion pass.

Open decisions are the detailed Library layout and implementation, detailed target layouts, and the safe AI structural-planning contract. Nested cards primary and synchronized graph secondary are selected, not open alternatives. The previous Forge/World/Overview first-set priority is superseded; those artifacts remain references. Forge stays unchanged. None blocks this draft or justifies erasing the current approved anchors.

## Inspiration & Anti-patterns

The [Lore-Weaver creation import](imports/lore-weaver-create-city-2026-09-24.md) illustrates grouped creation and preview anatomy; it does not authorize credits, PRO badges or unavailable features. The [archetype templates](.working/archetype-templates.md) remain a historical content reference. The saved refined Forge is the concrete current editing reference.

Avoid a token-only restyle that leaves a raw field wall, polished cards around legacy editors, separate uncoordinated candidate queues, low-contrast decorative labels, or generated imagery substituted for a clear task. Keep source-only observations distinct from fixture-rendered layout evidence and verified interaction defects.

## Responsive & Platform

Desktop web first, complete narrow-screen tasks. Current AppShell reflows at 800px; preserve it pending an explicit evaluated change. Reading layouts can remain single column; creation side summaries stack after the editor. Tabs, graph tools, action bars and long entity names must remain reachable without task clipping. Check actual keyboard, zoom, reduced-motion and touch interactions before final approval.

The [current four-screen redesign brief](NEW-REDESIGN-BRIEF.md) records the confirmed planner and Library choices, example city structure, lore-writing direction and acceptance cases.

The [four interactive concepts and verification record](CONCEPT-REVIEW.md) are ready for layout review, with canvas links and local previews. They simulate planning/saving and do not implement production behavior.


### Guided Build: seed controls and capability parity

The owner's screenshot review identified missing functionality in the first nested-plan concept. Nested cards must extend the existing builder, retaining all of its seed paths:

- **Per-kind generation counts:** separate controls for places, factions and key figures. A count requests additional unnamed entities on top of entered names, shaped entries and authored figures; zero is valid. Show entered + model-named totals. Keep the existing limit of 100 per kind, enforce it across all entry modes, and preserve inputs when validation fails. Global counts do not imply a per-city allocation; exact allocations require explicit planned nodes/links or the new validated allocation adapter.
- **Named entries and pasted lists:** support a single name or one name per line for all three kinds. Offer an explicit parent when adding to the nested plan, including unassigned/world level. Pasting adds editable stable-ID nodes without replacing other entries, counts, notes or authored material. Resolve normalized same-kind place/faction names deliberately; the current payload makes shaped entries canonical over duplicate quick names. Do not silently deduplicate characters by name.
- **Shaped places and factions:** an expanded card retains name, description, registry-backed archetype and dial, and explicit relations. A named entry can be expanded into a shaped entry without losing identity or links. Closing details or changing presentation depth never discards previously entered fields. Supported relation target tiers remain existing entity ID, draft key, and mandated target name, with direction and allowed kind pairs validated.
- **Authored key figures:** retain the shared partial character editor, including identity, NPC/BBEG/Monster roles, all story fields, mechanics toggles, structured stats/skills/actions/traits/spells, boss fields, world integration and relations. Supplied fields are authored facts preserved verbatim; blank fields can be generated. A plain named figure is still valid. Retain existing generation-name behavior when authored background/concept supplies enough seed; do not import Add Character's fully authored required-field rules into this hybrid path.
- **Lore and notes:** show the current campaign description, theme and saved custom lore as generation context. Provide inviting free-form build notes with the current 2,000-character limit, retaining text through edits, validation, failures and retries. Build notes seed this generation; they do not overwrite saved custom lore or become authored character background. Keep their source and scope clear.
- **State and submission:** initial seed inputs retain their own recoverable build draft; the World workspace combines actual committed entities with a separate set of unfinished additions. Cards and graph share that same workspace state. Expansion suggestions never overwrite the initial seed form or committed content. Restore existing account/campaign-scoped draft and failed-seed recovery. Review named/model-named totals, affected relations and potential existing-name updates before explicit generation; retain queued/running/failed/cancelled/succeeded behavior. Structural planning itself performs no world write.

The revised interactive concept exposes these controls with working local state. Its authored editor demonstrates text groups and JSON object retention for complex mechanics; production must reuse the existing structured character editor, registry and canonical validation rather than ship those prototype JSON controls. The concept's relation mandate controls are illustrative; full target-tier selection, backend validation, recovery and live generation remain implementation work.


### World workspace: direct planning controls

The owner identified adding and arranging as the part needing refinement. The current exploration puts **+ Place / + Faction / + Character** actions on the selected place and each planned place. Each action adds one stable-ID draft row under that exact parent and focuses its name. A blank name can remain model-named. Planned places render their children recursively, so adding a city section and its people is possible without leaving the plan for a separate global form.

Keep planned rows compact: explicit Planned/kind label, name, readable placement and optional membership, plus removal. **Move, connect & guide** opens the parent selector, faction membership and guidance. Moving a character preserves its authored guidance and independent faction link. Restrict containment parents to places and prevent self/descendant cycles. Open editors survive unrelated additions and re-renders; renames update outline, graph and target-option labels without changing identity.

The workspace toolbar keeps **Generate planned additions** and the full generation scope visible. AI ideas and bulk counts/named lists are optional disclosed tools; neither is required to add one item manually. Suggestions bind to their original scope and plan revision. Removing a connected parent requires reassignment first. Removing a leaf offers Undo that restores only the removed entry, preserving later additions and edits. Actual job/persistence behavior remains governed by the two-section contract above.


### Owner acceptance and implementation scope

The owner accepted the current two-section / workspace-control direction as **good enough for now**. Further visual exploration is paused for this pass. Implementation explicitly includes the backend work in [BACKEND-HANDOFF.md](BACKEND-HANDOFF.md): typed planning, scoped/model-named additions, validation and collision rules, committed-ID reconciliation, persistence and partial-failure/idempotent retry behavior. The prototype is not a frontend-only implementation specification.
