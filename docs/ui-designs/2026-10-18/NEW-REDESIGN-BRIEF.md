---
status: draft
updated: 2026-10-18
scope: design-and-interaction-proposals
---

# Your Worlds, Guided Build, Add Character and Library

## Confirmed direction

Forge stays as it is for now. Overview, Tonight and World remain the visual references. The active redesign targets are Guided Build, Add Character and Your Worlds. A new private Library holds Generic characters only; places, factions, starters and reusable connected collections are outside its initial scope.

The current Guided Build exploration uses **a familiar first-build form followed by a persistent World workspace**, with nested cards, a secondary graph and scoped expansion planning. This is a new interaction and planning contract, not merely recoloring the existing form. The [feasibility audit](nested-build-feasibility.md) separates available generation features from missing planning behavior.

The [visual guide](DESIGN.md), [experience guide](EXPERIENCE.md) and [delivery plan](DESIGN-PLAN.md) remain the shared contracts. This brief records the new screen decisions and acceptance examples. Palette, typography and the existing original brand come from the established application, not from a new theme.

## Your Worlds

**Primary task:** find and continue a real campaign. **Secondary task:** begin a new one.

Proposed composition: a clear Your Worlds heading and New world action, a searchable responsive grid of campaign cards, and a separate Library link. Each card uses available information: title, theme, description and a clear Continue action. Keep deletion secondary with a named confirmation. Do not fabricate last-played dates, session counts, cover art or activity summaries that the current data does not provide. An empty list invites creation; no search results offers clearing the search.

Generic is not displayed as another campaign card. Library has its own destination and private character collection. Separating navigation must preserve access controls and existing stored data.

### A more inviting world-creation space

Keep world name, theme and short pitch. Give custom lore a generous writing space rather than a small leftover textbox. Proposed optional chapter prompts are Premise, Places, History, People & factions, Magic & beliefs, and Tone & boundaries. These labels are suggestions for the mockup, not new required fields.

Use expandable chapter writing areas or editable headings with ample room. Let the DM start with one idea, skip chapters, paste existing prose and write freely. Show what has been written without turning world creation into a compulsory questionnaire. Existing unstructured custom lore must remain intact and editable. Adding chapter prompts is explicit; never silently split, summarize, rewrite or discard the DM's text.

The current stored contract is one `custom_lore` string. A sectioned presentation can retain it as lossless authored text with explicit headings; persistent independently structured chapters need a later contract decision. No automatic lore generation, knowledge extraction, timeline inference or invented rules is part of the initial design.

Acceptance examples:

- Paste a long existing setting description; visit another chapter and return: all prose remains.
- Start with only a world name, theme and one short premise: optional chapter prompts do not block creation beyond current requirements.
- Cancel creation: return to the campaign collection without creating a world; draft handling is clearly stated and never falsely promises persistence.
- Search for a campaign and Continue: arrive in that campaign's Overview. Library characters never appear in this list.

## Guided Build, then keep developing the world

### Guided Build and persistent World workspace — current direction

The owner approved exploring a two-section flow after rejecting the combined seed-and-planning form. **Guided Build generates the initial foundation; the World workspace then supports ongoing expansion from actual world content.** The workspace is available again on later visits. It is not an obsolete snapshot of the original generation request.

The sequence is **familiar Guided Build inputs → initial content generation → actual world workspace → select an existing place → propose/manual-plan additions → edit structure → generate only those additions → refresh actual world and clear completed planned items**. Nested cards and a keyboard-readable outline are primary; the synchronized graph is a secondary view of existing entities and unfinished additions. Existing content carries a solid border and explicit Existing label; additions carry a dashed border and explicit Planned label. Color alone does not distinguish them.

AI structure suggestions operate inside the expansion section. Requesting or applying a suggestion changes no committed content and enqueues no content-generation job. Suggestions bind to the selected existing target, world revision and current plan revision; late responses cannot replace intervening edits. Manual counts produce explicit scoped slots, which can remain model-named. Names, place assignments and faction memberships stay editable before generation. Names remain labels; identity uses draft keys or existing entity IDs. Containment and membership are distinct typed edges.

The first build retains its current per-kind additional counts, named/list/shaped entries, partial authored figures and lore/notes generation context. Returning to the first section retains useful inputs, but must not silently resubmit a consumed build. Existing campaigns can enter the World workspace directly. An empty workspace offers initial Guided Build; it does not invent existing content.

Expansion generation submits unfinished additions and their links only, with selected world entities as context/targets. Do not submit existing records as new seeds. Current name-based place/faction matching can update existing records, so the new additions-only contract must resolve normalized name collisions explicitly before submission; the concept blocks them and asks for a rename or use of the existing target. A different explicit edit/update operation remains separate. Global counts alone cannot implement exact per-parent allocations; compile them into keyed draft entries with links, or add a validated allocation adapter.

After generation, refetch the actual resulting entities and edges. Reconcile stable draft keys to committed IDs from confirmed results; remove only confirmed completed plan items. Partial failure or cancellation retains unfinished items and reports committed results accurately. Retry must avoid creating duplicates or replaying completed items. Existing Guided Build commits generated waves directly; structural approval does not introduce a generated-prose acceptance stage or atomic rollback guarantee. All world writes remain through the backend store, and access stays private to the invited account.

The two-section layout is a reviewable design proposal, not shipped behavior. The current prototype uses fixed sample generation and local state. The typed planning service, scoped input/result mapping, name-collision enforcement, recovery and persistent committed-plus-draft workspace require implementation.

### Example to demonstrate

1. Initial Guided Build produces Arlea, Harbor, Market, Old Temple, two factions and four characters.
2. The workspace shows those real records as Existing. Select Harbor and see Lysa and Merek alongside the place description and connections.
3. Request three additional places, one faction and four characters under Harbor, optionally supplying names and guidance. Those counts are local to Harbor.
4. Inspect an AI structural suggestion without changing the world. Apply it to the unfinished plan, rename a place, assign a character to that planned place and choose a faction explicitly. The graph updates from the same state.
5. Generate only those additions. Refresh the world, reconcile returned IDs and clear completed plan items. Keep unfinished items after partial failure.
6. Select Market next and start another expansion. The workspace remains useful; there is no master plan to recreate or discard.

### Acceptance examples

- Switch sections and preserve initial authored text, counts and build notes.
- Open an empty workspace and receive a clear path to the initial generator; open an established campaign and browse actual content immediately.
- Request/apply/discard a suggestion without changing existing world records.
- Plan three places and four characters specifically under Harbor, then assign one character to a planned place and a faction.
- Edit a draft name and see the graph update without breaking placement or membership.
- Block a containment cycle or unresolved target; preserve all draft work while resolving it.
- Detect a normalized place/faction name collision and choose a new name or existing target before generation.
- Successfully generate one expansion; existing content remains intact, completed additions appear as Existing, and the plan is ready for the next expansion.
- Retry after a partial failure; keep incomplete items and never regenerate completed additions blindly.

## Add Character

Keep this as authored entry: no LLM is required to submit a fully authored character. Group the existing fields into Identity, Story, Mechanics, Connections and Review, using the established form vocabulary. Preserve required fields, role-dependent boss controls, structured stat/spell rows, relation targets, validation and multi-character batches.

Proposed composition: a compact destination header, section navigation with completeness/error indicators, one manageable group of fields at a time, and a review summary. Missing required material remains visible through the section indicator and a useful validation summary; collapsing a section does not make its fields optional. User-facing text explains what will be saved, rather than endpoints, schema mirroring or worker transactions.

Acceptance examples: enter a partial character, switch sections and preserve values; navigate to missing required fields from review; add a second authored character and connect them; cancel without saving; retry a failed submission with authored text intact. Do not promise autosave or draft recovery beyond verified behavior.

## Library: Generic characters only

A private collection outside real campaigns. Proposed `/library` navigation is not an existing route. Show searchable character cards or rows, readable identity summaries, a Create character action that uses the existing Forge, and supported per-character actions. Do not introduce places/factions tabs, world templates, credits or collections in this first scope.

The current transfer is **Move to world**, not a reusable copy: name the destination and explain that the character leaves Library after success. Do not offer a keep-in-Library copy option without a new contract. Existing transfer is not an atomic cross-world transaction; failure and duplicate-recovery behavior need to be designed honestly. Library characters remain isolated from one another and do not become generation context for unrelated characters.

Acceptance examples: empty Library points to Forge; searching and opening a character works; choose a private owned campaign as the move destination; cancel changes nothing; successful move refreshes both collection and destination; a transfer failure never falsely reports a completed removal.

## Review and delivery

Produce coordinated concept drafts for the four surfaces, retaining current reference styles. Every visual artifact is a proposal, not a working backend feature. Check desktop/mobile composition, readable labels, contrast, focused controls, retained draft values and the consequential states above. Record selected drafts in the shared Superdesign resume state and link them from the guides. Implement only after the layouts and new planning contract are reviewed, using the repository's branch, check and PR workflow.

The [four interactive concepts and verification record](CONCEPT-REVIEW.md) are ready for layout review, with canvas links and local previews. They simulate planning/saving and do not implement production behavior.


### Guided Build: seed controls and capability parity

The owner's screenshot review identified missing functionality in the first nested-plan concept. Nested cards must extend the existing builder, retaining all of its seed paths:

- **Per-kind generation counts:** separate controls for places, factions and key figures. A count requests additional unnamed entities on top of entered names, shaped entries and authored figures; zero is valid. Show entered + model-named totals. Keep the existing limit of 100 per kind, enforce it across all entry modes, and preserve inputs when validation fails. Global counts do not imply a per-city allocation; exact allocations require explicit planned nodes/links or the new validated allocation adapter.
- **Named entries and pasted lists:** support a single name or one name per line for all three kinds. Offer an explicit parent when adding to the nested plan, including unassigned/world level. Pasting adds editable stable-ID nodes without replacing other entries, counts, notes or authored material. Resolve normalized same-kind place/faction names deliberately; the current payload makes shaped entries canonical over duplicate quick names. Do not silently deduplicate characters by name.
- **Shaped places and factions:** an expanded card retains name, description, registry-backed archetype and dial, and explicit relations. A named entry can be expanded into a shaped entry without losing identity or links. Closing details or changing presentation depth never discards previously entered fields. Supported relation target tiers remain existing entity ID, draft key, and mandated target name, with direction and allowed kind pairs validated.
- **Authored key figures:** retain the shared partial character editor, including identity, NPC/BBEG/Monster roles, all story fields, mechanics toggles, structured stats/skills/actions/traits/spells, boss fields, world integration and relations. Supplied fields are authored facts preserved verbatim; blank fields can be generated. A plain named figure is still valid. Retain existing generation-name behavior when authored background/concept supplies enough seed; do not import Add Character's fully authored required-field rules into this hybrid path.
- **Lore and notes:** show the current campaign description, theme and saved custom lore as generation context. Provide inviting free-form build notes with the current 2,000-character limit, retaining text through edits, validation, failures and retries. Build notes seed this generation; they do not overwrite saved custom lore or become authored character background. Keep their source and scope clear.
- **State and submission:** preserve the initial build draft separately from actual committed world content and unfinished expansion additions. Cards and graph share workspace state; applying suggestions changes only the addition plan. Restore existing account/campaign-scoped draft and failed-seed recovery. Review named/model-named totals, affected relations and potential existing-name updates before explicit generation; retain queued/running/failed/cancelled/succeeded behavior. Structural planning itself performs no world write.

The revised interactive concept exposes these controls with working local state. Its authored editor demonstrates text groups and JSON object retention for complex mechanics; production must reuse the existing structured character editor, registry and canonical validation rather than ship those prototype JSON controls. The concept's relation mandate controls are illustrative; full target-tier selection, backend validation, recovery and live generation remain implementation work.


### World workspace: direct planning controls

The owner identified adding and arranging as the part needing refinement. The current exploration puts **+ Place / + Faction / + Character** actions on the selected place and each planned place. Each action adds one stable-ID draft row under that exact parent and focuses its name. A blank name can remain model-named. Planned places render their children recursively, so adding a city section and its people is possible without leaving the plan for a separate global form.

Keep planned rows compact: explicit Planned/kind label, name, readable placement and optional membership, plus removal. **Move, connect & guide** opens the parent selector, faction membership and guidance. Moving a character preserves its authored guidance and independent faction link. Restrict containment parents to places and prevent self/descendant cycles. Open editors survive unrelated additions and re-renders; renames update outline, graph and target-option labels without changing identity.

The workspace toolbar keeps **Generate planned additions** and the full generation scope visible. AI ideas and bulk counts/named lists are optional disclosed tools; neither is required to add one item manually. Suggestions bind to their original scope and plan revision. Removing a connected parent requires reassignment first. Removing a leaf offers Undo that restores only the removed entry, preserving later additions and edits. Actual job/persistence behavior remains governed by the two-section contract above.


### Owner acceptance and implementation scope

The owner accepted the current two-section / workspace-control direction as **good enough for now**. Further visual exploration is paused for this pass. Implementation explicitly includes the backend work in [BACKEND-HANDOFF.md](BACKEND-HANDOFF.md): typed planning, scoped/model-named additions, validation and collision rules, committed-ID reconciliation, persistence and partial-failure/idempotent retry behavior. The prototype is not a frontend-only implementation specification.
