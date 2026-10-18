---
status: draft
updated: '2026-10-18'
topic: 'mythosCircle website cohesion — extend the approved current UI'
name: mythosCircle
description: 'A calm, modern workspace for Game Masters to prepare, run, and author a living world.'
sources:
  - .memlog.md
  - .working/history/DESIGN-2026-09-25.md
  - .working/history/EXPERIENCE-2026-09-25.md
  - ../../../../frontend/src/styles/tokens.css
  - ../../../../frontend/src/components/shell/AppShell.vue
  - ../../../../frontend/src/router.ts
  - ../../../../.superdesign/design-system.md
  - manifest.json
colors:
  app: '#080d16'
  app-raised: '#0b1320'
  surface: '#0e1724'
  surface-raised: '#131e2e'
  surface-hover: '#17253a'
  input: '#0a1421'
  border: '#24344a'
  border-bright: '#334966'
  glow-violet: 'rgba(125, 92, 255, 0.24)'
  glow-cyan: 'rgba(53, 190, 205, 0.18)'
  text-primary: '#eef1fb'
  text-secondary: '#b2bdd0'
  text-muted: '#74839a'
  text-faint: '#52627a'
  interactive: '#8b6cff'
  interactive-bright: '#a994ff'
  canonical: '#5ed0c1'
  proposal: '#c09cff'
  warning: '#e7b866'
  danger: '#ed777d'
  success: '#75d39e'
typography:
  display: 'Georgia, "Times New Roman", serif'
  body: '"Avenir Next", "Segoe UI", sans-serif'
  page-title: 'clamp(2rem, 4vw, 3rem)'
  section-title: '1.125rem'
  entity-name: '1rem'
  meta: '0.72rem'
  body-size: '0.95rem'
spacing:
  gap: '1rem'
  gap-sm: '0.5rem'
  shell-width: '1440px'
  sidebar-width: '236px'
rounded:
  default: '12px'
  small: '7px'
components:
  shell:
    background: '{colors.app}'
    sidebar-width: '{spacing.sidebar-width}'
  page-header:
    title: '{typography.page-title}'
    description: '{colors.text-secondary}'
  panel:
    background: '{colors.surface}'
    border: '{colors.border}'
    radius: '{rounded.default}'
  button:
    accent: '{colors.interactive}'
    foreground-on-accent: '{colors.app}'
    focus: '{colors.interactive-bright}'
    radius: '{rounded.small}'
  field:
    background: '{colors.input}'
    boundary: '{colors.text-muted}'
    label: '{colors.text-primary}'
    help: '{colors.text-secondary}'
    radius: '{rounded.small}'
  section-nav:
    selected: '{colors.text-primary}'
    marker: '{colors.interactive-bright}'
  context-summary:
    background: '{colors.surface-raised}'
    foreground: '{colors.text-secondary}'
  entity-row:
    name: '{colors.text-primary}'
    background: '{colors.surface}'
  proposal-review:
    marker: '{colors.proposal}'
    background: '{colors.surface-raised}'
  activity:
    foreground: '{colors.text-secondary}'
    error: '{colors.danger}'
  feedback:
    foreground: '{colors.text-secondary}'
    error: '{colors.danger}'
  dialog:
    background: '{colors.surface-raised}'
    radius: '{rounded.default}'
  graph:
    background: '{colors.app-raised}'
    canonical: '{colors.canonical}'
---

# DESIGN.md — website cohesion

This draft updates the exact website-wide guide pair. The owner identifies **Overview, Tonight, and World** as the modern visual anchors; the rest of the website needs to belong to the same product. **Latest owner direction:** leave Forge as it is and redesign **Guided Build, Add Character and Your Worlds**. World creation needs a larger, more inviting custom-lore space; continuing a world needs a better layout. A dedicated private Library for **Generic characters only for now** is selected; its page/route is not implemented. The earlier Forge/World/Overview set remains reference evidence, not the current redesign target. Their current navy/violet language replaces the September espresso/terracotta direction. This is a cohesion pass, with proposed layouts still requiring visual review. No new UI has been implemented by this document update.

[EXPERIENCE.md](EXPERIENCE.md) owns behavior, route coverage, delivery sequence, and acceptance. [DESIGN-PLAN.md](DESIGN-PLAN.md) holds concrete screen briefs and the tool workflow. This pair governs proposals when a mock or older import conflicts. The [September DESIGN snapshot](.working/history/DESIGN-2026-09-25.md) and [EXPERIENCE snapshot](.working/history/EXPERIENCE-2026-09-25.md) preserve prior decisions unchanged; deferred ambitions remain explicit in EXPERIENCE.

## Brand & Style

A modern, dark Game Master workspace with a quiet fantasy voice. Navy surfaces support readable material; violet signals interaction and creation, turquoise signals committed world material. Serif names and meaningful portraits provide character. Layout, typography, and editing quality must carry the experience when images are absent.

Keep the existing Overview, Tonight journal, and World directory as reference screens. Extract their useful anatomy before changing other pages: clear page headers, readable descriptions, deliberate content groups, restrained borders, and consistent actions. Preserve their distinct jobs; cohesion does not require identical page layouts.

Current findings combine source inspection with desktop/mobile captures of the actual Vue app using a read-only synthetic fixture account/API: [Overview desktop](mockups/overview-current-desktop.png) / [mobile](mockups/overview-current-mobile.png), [World desktop](mockups/world-current-desktop.png) / [mobile](mockups/world-current-mobile.png), and [Forge desktop](mockups/forge-current-desktop.png) / [mobile](mockups/forge-current-mobile.png). These use sample data, with no production/private campaigns or database/world writes; real authentication integration, consequential states and full interaction/accessibility QA remain unverified. Some creation wrappers and nested editors still use old hardcoded surfaces, unstructured headings, and technical explanatory copy. Admin and the campaign library already use current primitives and need targeted review rather than an assumed total rebuild. Forge is an existing design investment; latest direction leaves its entire current design unchanged. Use it as a reference rather than a redesign target.

## Colors

Frontmatter values mirror [tokens.css](../../../frontend/src/styles/tokens.css) exactly. Each key maps to the same suffix under `--mc-`, except `app` → `--mc-app-bg`, `app-raised` → `--mc-app-bg-raised`. `colors.text-primary` maps to `--mc-text-primary`, for example. Implement using CSS variables; this table records current values rather than authorizing duplicates in components.

| Role | Treatment |
|---|---|
| Canvas | `{colors.app}` with existing restrained atmospheric background; do not add new glows behind every panel. |
| Content | `{colors.surface}` for grouped material; `{colors.surface-raised}` for supporting summaries and floating layers; `{colors.surface-hover}` for hover/selection treatment. |
| Action | `{colors.interactive}` with `{colors.interactive-bright}` for focus and selected markers. Proposed normal-size filled-action labels use `{colors.app}` on `{colors.interactive}` (5.27:1 opaque contrast). White (3.69:1) and text-primary (3.27:1) fail on this fill. Audit the skip link as well as buttons when implementing; verify gradient/hover/disabled composites at their weakest point. |
| World status | `{colors.canonical}` for committed material, `{colors.proposal}` for staged material; always pair with words. |
| Text | `{colors.text-primary}` for names, labels, actions and prose; `{colors.text-secondary}` for supporting copy. |
| Low-emphasis tokens | `{colors.text-muted}` and `{colors.text-faint}` exist in code; neither is a blanket license for small labels. Reserve faint for nonessential decoration. |
| Feedback | `{colors.warning}`, `{colors.danger}`, `{colors.success}` in compact messages and markers, with explicit text. |

WCAG 2.2 AA is the acceptance target: 4.5:1 for normal text, 3:1 for large text and meaningful non-text boundaries/focus indicators. Check every real combination, including selected, hover and disabled contexts. These are targets, not a claim that current pages already pass. For the proposed default input boundary, use opaque `{colors.text-muted}` against `{colors.input}` (4.81:1); this is a control boundary, not a label color recommendation. Existing border (1.47:1) and border-bright (2.01:1) cannot alone identify inputs at 3:1. Keep decorative separators distinct from control boundaries. Render default, focus, error and selected states; focus alone cannot repair a weak resting boundary. Required labels, navigation, help needed to complete a form, and error explanations must remain readable. Do not lower whole-card opacity to create hierarchy.

## Typography

Use `{typography.body}` for UI and reading. Use `{typography.display}` for page display titles and entity/campaign names where the anchor screens do; labels, field instructions, and buttons stay sans serif. The current ramp is `{typography.page-title}`, `{typography.section-title}`, `{typography.entity-name}`, and `{typography.body-size}`; `{typography.meta}` is for nonessential metadata, not task instructions.

Maintain one h1 per page, meaningful h2 groups, and visible form labels. Keep page descriptions short and useful: what can the DM do here, and what happens next? Avoid explanatory paragraphs about validators, endpoint payloads, worker transactions, or revision machinery inside creation flows. Technical detail belongs in an expandable error diagnostic when useful.

## Layout & Spacing

The current shell has a `{spacing.sidebar-width}` sidebar and flexible content; `{spacing.shell-width}` is a token, not a claim that AppShell currently caps its full width. Use `{spacing.gap}` and `{spacing.gap-sm}` for related groups. Retain current shell behavior at 800px while visual variants are evaluated; do not silently replace it with the historical 900px icon rail.

**Proposed composition families:**

- Reading/browsing: page header, focused content sections, optional filters, consistent entity rows. Overview and World remain the reference.
- Creation: page header, compact destination/context, grouped editor, review summary and one primary submit. On wide screens a supporting summary may sit beside the work; on narrow screens it follows the editor. Avoid a narrow form stranded in a wide empty canvas.
- Ask/review: composer followed by job status and staged candidate/proposal review. Current NewAskView stages candidates; it does not render a separate read-only answer. Activity supports the task rather than displacing the composer. Answer-only consultation remains a deferred product/backend decision, not a visual addition.
- Entity detail: name/context/actions, readable kind-specific content, relations and secondary actions. Editing uses the same field language as creation.
- Graph: consistent header and toolbar, a bounded canvas, a readable selection inspector and text alternative.

These families are proposed directions. Each must be rendered with real long content, an empty state, an error and a narrow viewport before acceptance. Do not invent graph editing, autosave, generation, or backend persistence to make a layout attractive.

## Elevation & Depth

Use tonal steps and borders for resting groups. Preserve the restrained atmosphere of the approved anchor screens. Floating dialogs and menus must read above their parent; avoid nested floating layers and excessive shadows. Glows and gradients may support the shell identity already present in code, but must never encode state, reduce contrast, or replace content hierarchy.

## Shapes

Use `{rounded.default}` for panels and `{rounded.small}` for controls. Reuse established chips, icon proportions and control heights. Avoid introducing a competing rounded-card system within a legacy editor. Prefer at least 44×44 CSS px touch targets. Compact controls must meet the WCAG 2.2 AA baseline of 24×24 CSS px or a documented spacing/exception criterion. Include toolbar icons, chips, nested row actions and navigation controls. Check 200% text resizing and 400% browser zoom/reflow at 320 CSS px width; keyboard focus and action/error content must remain reachable and unobscured by sticky bars or overlays. Record genuine two-dimensional graph/table exceptions and retain an equivalent usable reading/task path.

## Components

The names below form the shared visual/behavior catalog with EXPERIENCE. These are design families, not assertions that identically named Vue components already exist.

| Pattern | Visual treatment and proposed deliverable |
|---|---|
| Shell (`{components.shell}`) | Preserve existing navigation grouping and navy context; standardize active, hover, focus and narrow-screen states. Deliver an annotated shell reference. |
| Page header (`{components.page-header}`) | One title, concise purpose, optional world context, grouped actions. Deliver reading, creation, graph and auth variants. |
| Panel (`{components.panel}`) | Tonal group with clear heading and purposeful spacing. Deliver editor, summary, content and empty variants without unnecessary nested cards. |
| Button (`{components.button}`) | One dominant action per local task, normal-size filled labels using app foreground on interactive fill, consistent secondary/quiet/danger variants and visible pending state. Deliver state strip. |
| Field (`{components.field}`) | Persistent readable label, measured boundary, help/error below, required/optional meaning in text. Choice tiles are Field variants with checked/selected state, not navigation. Editable list rows are Field variants; linked entity rows use Entity row. Deliver short input, prose, choice tile, editable row, select, structured stat and relation picker variants. |
| Section navigation (`{components.section-nav}`) | Identify each navigator as section links, tabs or step navigation in its screen brief. Each shows position clearly and reflows without clipping. Preserve Forge’s refined section treatment. Deliver desktop/mobile examples and a disclosure variant with clear expanded/collapsed indicator and readable summary. |
| Context summary (`{components.context-summary}`) | Compact destination/world and meaningful authored summary. Deliver Forge destination and Guided Build review variants; remove implementation narration. |
| Entity row (`{components.entity-row}`) | Legible name, kind, concise distinguishing facts and clear navigation; useful with no portrait. Deliver directory/list and related-entity variants. |
| Proposal review (`{components.proposal-review}`) | Proposed badge, readable proposed content, clear acceptance consequences and separate actions. Deliver single, multiple, conflict and resolved examples. |
| Activity (`{components.activity}`) | Compact status rows and optional diagnostics, with factual queue/running/error text. Deliver no-job, pending, running, complete and failed states. |
| Feedback (`{components.feedback}`) | Task-specific empty/loading/error/permission states; readable explanation and appropriate next action. Deliver shared state catalog. |
| Dialog (`{components.dialog}`) | Named title, consequence text, distinct confirm/cancel and focus ring. Deliver deletion and unsaved-change variants only where supported or proposed explicitly. |
| Graph (`{components.graph}`) | Navy canvas, readable nodes/selection, controls matching shell. Deliver focused, empty, selected and dense examples plus an edge-list alternative. |

### Current redesign briefs — proposed treatments

**Your Worlds.** Separate “Continue a world” from “Create a world” visually so returning to play is easy to scan. Proposed resume treatment: searchable responsive world cards with a clear Continue action, readable title and theme/pitch, and secondary management actions. A card grid is a proposal, not an owner-selected layout. Include empty, many-world, no-search-match and narrow variants; do not invent last-played data or recency sorting without support.

**Inviting custom lore.** Give freeform writing a generous, resizable prose area with a clear label and helpful optional prompts. Proposed optional chapter prompts—premise, places, people, tensions—can organize writing visually without becoming required schema fields. Preserve the exact existing `custom_lore` text and its freeform meaning; switching sections must not parse, rewrite, truncate or silently reserialize the DM’s writing. A large single writing area remains available. Show empty invitation, long authored lore, collapsed/expanded prompts and validation/failure recovery.

**Add Character.** Group the existing authored fields into legible identity, narrative, mechanics and relationship areas, with a compact completeness/review summary and clear batch character navigation. This is the existing zero-LLM authored path; do not style it as Generate or promise that missing required fields will be model-filled. Preserve required semantics, batches and authored text. Choice tiles and disclosed optional detail use the shared Field/Section navigation catalog.

**Guided Build and World workspace.** Keep the familiar initial generator as the first section. Once content exists, open a persistent workspace to browse a city, its places/factions and their characters, then plan additions under a selected place. Nested cards are primary and the graph secondary; AI structural planning applies to subsequent expansion. In either direction provide a complete keyboard-readable outline, distinguish new draft nodes from existing committed entities, and label edge direction and meaning. Nesting must not silently imply membership/control. Use stable node identity when labels change. The plan preview describes intended input, not unseen generated output or an acceptance stage.

**Library — selected scope.** A dedicated private page for **Generic characters only for now** is selected. `/library` is a proposed new route, not active UI. Your Worlds should show real campaigns only and exclude Generic. Design readable character browsing, empty/loading/error states and clear existing move-to-world consequences. Preserve private per-account storage and generation isolation. Places, factions, connected starters and reusable-copy semantics are outside initial scope; do not show them as empty promised categories.

Capability and identity limits are recorded in [nested-build-feasibility.md](nested-build-feasibility.md). The planner presentation, AI-assisted structural-planning direction and private Generic-characters-only Library scope are selected; detailed layouts and new implementation contracts remain proposals; no new application UI or backend behavior is delivered by these guides.

### Guided Build and persistent World workspace — current direction

The owner approved exploring a two-section flow after rejecting the combined seed-and-planning form. **Guided Build generates the initial foundation; the World workspace then supports ongoing expansion from actual world content.** The workspace is available again on later visits. It is not an obsolete snapshot of the original generation request.

The sequence is **familiar Guided Build inputs → initial content generation → actual world workspace → select an existing place → propose/manual-plan additions → edit structure → generate only those additions → refresh actual world and clear completed planned items**. Nested cards and a keyboard-readable outline are primary; the synchronized graph is a secondary view of existing entities and unfinished additions. Existing content carries a solid border and explicit Existing label; additions carry a dashed border and explicit Planned label. Color alone does not distinguish them.

AI structure suggestions operate inside the expansion section. Requesting or applying a suggestion changes no committed content and enqueues no content-generation job. Suggestions bind to the selected existing target, world revision and current plan revision; late responses cannot replace intervening edits. Manual counts produce explicit scoped slots, which can remain model-named. Names, place assignments and faction memberships stay editable before generation. Names remain labels; identity uses draft keys or existing entity IDs. Containment and membership are distinct typed edges.

The first build retains its current per-kind additional counts, named/list/shaped entries, partial authored figures and lore/notes generation context. Returning to the first section retains useful inputs, but must not silently resubmit a consumed build. Existing campaigns can enter the World workspace directly. An empty workspace offers initial Guided Build; it does not invent existing content.

Expansion generation submits unfinished additions and their links only, with selected world entities as context/targets. Do not submit existing records as new seeds. Current name-based place/faction matching can update existing records, so the new additions-only contract must resolve normalized name collisions explicitly before submission; the concept blocks them and asks for a rename or use of the existing target. A different explicit edit/update operation remains separate. Global counts alone cannot implement exact per-parent allocations; compile them into keyed draft entries with links, or add a validated allocation adapter.

After generation, refetch the actual resulting entities and edges. Reconcile stable draft keys to committed IDs from confirmed results; remove only confirmed completed plan items. Partial failure or cancellation retains unfinished items and reports committed results accurately. Retry must avoid creating duplicates or replaying completed items. Existing Guided Build commits generated waves directly; structural approval does not introduce a generated-prose acceptance stage or atomic rollback guarantee. All world writes remain through the backend store, and access stays private to the invited account.

The two-section layout is a reviewable design proposal, not shipped behavior. The current prototype uses fixed sample generation and local state. The typed planning service, scoped input/result mapping, name-collision enforcement, recovery and persistent committed-plus-draft workspace require implementation.


## Do's and Don'ts

| Do | Avoid |
|---|---|
| Extend Overview, Tonight and World’s existing visual language | A new palette or isolated restyle per route |
| Preserve refined Forge editing work | Rebuilding it without comparing its saved draft |
| Style nested editors and their wrapper together | Polished header above a legacy input wall |
| Use readable labels and meaningful hierarchy | Faint text, lower opacity, or tiny instructions as decoration |
| Show authored material and proposed changes distinctly | A generic success card that hides whether canon changed |
| Explain user consequences | API schema, AD references, POST paths and worker transactions in main flows |
| Compare realistic states at desktop and mobile sizes | Approving a single beautiful empty screenshot |
| Carry exact current token values into visual tools | Copying generated arbitrary hex values back into Vue |

## References and design tooling

Use Superdesign to compare drafts before implementation. Resume the saved Forge project `49dc6237-ff17-4b26-9c70-de2a01f28ca4`, draft `043058c8-074c-4f93-afb2-15f980728109` (saved current version 7 in [resume.json](manifest.json)); establish freshness against current source before reusing it. It is a refined Forge reference, not website-wide approval. The earlier Forge/World/Overview reference set is retained. Latest direction leaves Forge unchanged and focuses new drafts on Guided Build, Add Character and Your Worlds. Later Ask/Guided Build/Add Character drafts must include the current shell and anchor captures once collected.

12ui can provide optional layout exploration under its own skill workflow. Use frontend-design when translating a chosen draft into Vue; agent-browser supplies actual screenshots and interaction QA. Figma and MagicPath were discovered as possible tools but are not installed or part of the committed workflow. Document edits themselves do not generate visuals; current draft-generation progress and artifacts are recorded in DESIGN-PLAN. An [independent consistency/accessibility review](review-consistency-accessibility-2026-10-18.md) checked the draft; its contrast, target-size and navigation findings informed these proposed contracts. They are not rendered fixes. The [login baseline](mockups/login-baseline-2026-10-18.png) records the unauthenticated screen. The desktop/mobile sample-data captures above establish current layout evidence; consequential-state captures and runtime interaction/accessibility checks remain pending. World and Overview Superdesign baselines are generated and available on the [shared canvas](https://superdesign.dev/teams/5340a30a-1c5b-4ece-80a7-48c84c8997a9/projects/49dc6237-ff17-4b26-9c70-de2a01f28ca4), with local [World](mockups/world-superdesign-baseline.html) and [Overview](mockups/overview-superdesign-baseline.html) previews; Forge reuses inspected version 7. World/Overview local baselines were rendered at 1440px and 390px with no horizontal document overflow at 390px; owner review and full state/accessibility verification remain pending. Independent review findings have been addressed in this contract, while visual and implementation QA remain outstanding. The exported [Forge v7 prototype](mockups/forge-superdesign-v7.html) has [desktop](mockups/forge-superdesign-v7-desktop.png) and [mobile](mockups/forge-superdesign-v7-mobile.png) captures. Limited prototype checks found name/concept preserved through Quick → Detailed → Quick and no horizontal overflow at 390px; these do not verify actual Vue interaction, backend behavior or full accessibility. See [the concrete plan](DESIGN-PLAN.md) for tool availability and next artifacts.

The [Lore-Weaver import](imports/lore-weaver-create-city-2026-09-24.md) and [archetype templates](.working/archetype-templates.md) remain historical inspiration for creation anatomy. Their proposed controls are not an inventory of shipped functionality. Older mockups referenced by the archived spines remain historical, not current visual approval.

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
