---
status: draft
updated: 2026-10-18
---

# Review the four-screen redesign

The owner confirmed: keep Forge as it is; redesign Your Worlds, Add Character and Guided Build; give Generic characters a separate private Library. The latest Guided Build exploration separates the familiar initial generator from a persistent World workspace with nested browsing, a secondary graph and scoped expansion planning.

All four interactive concepts are on the [shared Superdesign canvas](https://superdesign.dev/teams/5340a30a-1c5b-4ece-80a7-48c84c8997a9/projects/49dc6237-ff17-4b26-9c70-de2a01f28ca4). Current versions were refetched and checked after import. Original branding is present, and no empty shared-shell component tags remain.

| Concept | Canvas preview | Local interactive HTML | What to try |
|---|---|---|---|
| Your Worlds · v2 | [Open](https://p.superdesign.dev/draft/9c0054be-871e-40fb-aa92-f214cbd68149) | [Your Worlds](mockups/worlds-proposal.html) | Search the campaign cards; open New world; write in Free writing and History; switch chapters; read the combined lore. |
| Add Character · v2 | [Open](https://p.superdesign.dev/draft/1c84dc2e-d2f4-4d1b-b502-8fe341113163) | [Add Character](mockups/add-character-proposal.html) | Enter a name/background, switch sections, add a second character, inspect required-field links in Review, and add mechanics/relations. |
| Guided Build · v6 | [Open](https://p.superdesign.dev/draft/dff4ef13-a3af-4d0c-bb98-f6ab8352c26b) | [Guided Build](mockups/guided-build-proposal.html) | Explore a sample world; add a place directly under Harbor, add figures inside that planned place, open Move/connect/guidance, try removal Undo, then generate additions. |
| Library · v2 | [Open](https://p.superdesign.dev/draft/8f36f0b8-c14f-4edb-9aff-d21497256b77) | [Library](mockups/library-proposal.html) | Search and open character details; choose Move to world; check destination gating, Cancel, and the local demonstration transfer. |

These are design prototypes. AI planning uses a fixed sample proposal; no real model request runs. World creation/deletion, character submission, generation and transfers are local demonstrations. No application route, database or production backend changed. The authored Add Character concept checks field completeness, not the full canonical validator. Navigation placeholders represent future integration.

## Evidence

- Actual Your Worlds and Add Character captures used isolated synthetic data: [Your Worlds before](mockups/your-worlds-before.png), [Add Character before](mockups/add-character-before.png). The existing development servers were left alone; screenshot services used separate ports.
- Prototype inline JavaScript passed syntax checks.
- Lore survives chapter switches and appears in the combined text with its authored content intact.
- Authored character name and background survive section switches.
- Guided Build starts with ten stable-ID nodes. A rename updates graph text; proposing two harbor characters leaves the draft at ten nodes; explicit application changes cards and graph to twelve. No content generation occurs on approval.
- Library confirmation requires a destination. Cancel preserves three sample characters; the explicit demo move leaves two, with a prototype-only status message.
- All four layouts were rendered at 1440px and 390px. The tested 390px layouts had no horizontal document overflow. These checks do not certify every keyboard, contrast, zoom or consequential-state path.

## Visual references

| Screen/state | Desktop | Mobile |
|---|---|---|
| Your Worlds collection | [Capture](mockups/worlds-concept-desktop.png) | [Capture](mockups/worlds-concept-mobile.png) |
| World creation / lore | [Capture](mockups/lore-concept-desktop.png) | [Capture](mockups/lore-concept-mobile.png) |
| Authored character entry | [Capture](mockups/add-character-concept-desktop.png) | [Capture](mockups/add-character-concept-mobile.png) |
| Previous combined planner (v3) | [Capture](mockups/guided-build-concept-desktop.png) | [Capture](mockups/guided-build-concept-mobile.png) |
| Private character Library | [Capture](mockups/library-concept-desktop.png) | [Capture](mockups/library-concept-mobile.png) |

## Implementation boundaries

Read [the confirmed design brief](NEW-REDESIGN-BRIEF.md) and [the feasibility audit](nested-build-feasibility.md) before implementing. A safe AI structural planner requires a typed validation/versioning layer; current global generation counts do not assign characters to individual places. The plan must preserve stable identities, validate relationships and expose possible existing-name merges. Plan approval is distinct from existing direct-commit content generation.

Library remains characters only. Existing transfers move a character rather than keeping a reusable copy, and must not be described as atomic. Lore section editing must preserve existing freeform text; optional chapter labels in these mockups remain proposed wording.

Superdesign generation completed the current-page baselines and initial planner/Library concepts, then returned insufficient generation credits for the remaining refinements. Your Worlds and Add Character were authored from the captured context using the tool's supported HTML import workflow. The planner's static graph was replaced with synchronized local interactions; the Library dialog/search/transfer demonstration was corrected. All four final imports completed without warnings and were refetched successfully.

The guide pair remains draft pending owner layout review. Production implementation, complete accessibility/state checks and required repository checks/PRs are subsequent work.


## Guided Build parity correction · version 4

The owner identified missing generation and authorship controls in version 3. Version 4 restores per-kind **additional** counts, named entries, paste-list entry, shaped place/faction fields, authored figure groups, explicit relation mandates, saved seed context and 2,000-character free-form build notes. All coexist with the nested plan and secondary graph. The guides now specify full retention of the existing shared character editor, registry choices, relation target tiers, scoped draft recovery and job states.

Twelve local interaction assertions passed: initial cards/graph agreement; counts independent from named anchors; pasted nested places; shape fields retained; authored history preserved byte-for-byte through reparenting; synchronized rename; proposal leaves draft unchanged; explicit apply adds two nodes; notes/count retention; group-total limit; valid generation gating; prototype submission summary includes counts and seed context. The tested 390px viewport had no horizontal document overflow. Inline JavaScript syntax passed. Imported version 4 was refetched and its HTML matched the local artifact.

The authored mechanics section uses a limited JSON object demonstration, not the production structured controls. Archetype/dial values and named relation controls are illustrative. Complete canonical validation, registry loading, existing/draft target selection, crash/retry recovery, live AI and generation are specified implementation requirements, not verified prototype behavior. Current prototype submission requires a visible name; production must retain the existing authored-seed name-generation path.

[Updated desktop controls](mockups/guided-build-controls-desktop.png) · [Updated mobile controls](mockups/guided-build-controls-mobile.png). Earlier nested-plan screenshots above document version 3.


## Current Guided Build concept · version 5

The owner asked for a different direction and authorized exploring the researched patterns in [REFERENCE-PATTERNS.md](REFERENCE-PATTERNS.md). This version replaces the combined form/planner composition with two sections:

1. **Guided Build:** familiar counts, named/list/shaped entries, partial authored figures, saved seed context and free-form build notes. The local demo generates an initial foundation and opens the second section.
2. **World workspace:** actual sample entities form a nested outline and readable cards. Select Harbor to see its existing characters and request scoped additions. Suggestions are reviewed before entering the unfinished plan; names, place assignments, faction membership and guidance are editable. A secondary graph reflects existing and planned entries using labels and border patterns.

Choose **Explore a sample world** to reach the second section immediately. Then try **Suggest structure → Add to plan → Generate planned additions**. Generation promotes the local planned entries to Existing and clears that completed plan. Select Market and repeat. Existing entries are preserved through both demo expansions.

Seventeen flow assertions passed: initial/empty states; note retention; foundation generation; Harbor content; non-mutating suggestions; eight Harbor-scoped additions; graph states; synchronized renaming; assignment to a planned place; explicit faction membership; existing-name collision gating; generation preserving existing records; stable IDs/links; consumed-build protection; a second scoped expansion; model-named manual additions. Three additional assertions checked authored/shaped/list retention and section switching. Syntax passed. Both sections had no horizontal document overflow at 390px and 320px. The graph intentionally scrolls horizontally inside its own container; outline/cards remain an equivalent readable task path. These are bounded checks, not comprehensive accessibility certification.

Prototype limitations: generation and AI are fixed local demonstrations; reload resets state. No real jobs, persistent drafts, authentication changes, database writes or production routes were implemented. Authored mechanics still use limited JSON object demonstrations; production must reuse the shared structured editor and canonical validation. Full relation tiers, scoped result-ID reconciliation, live revision/conflict handling and partial failure/retry behavior are guide requirements. The sample collision check is simpler than the backend's canonical normalizer. Completed items are cleared together only because the demo always succeeds; production must retain unfinished items after partial success.

| Current section | Desktop | Mobile |
|---|---|---|
| Initial Guided Build | [Capture](mockups/guided-foundation-desktop.png) | [Capture](mockups/guided-foundation-mobile.png) |
| Persistent world with planned Harbor additions | [Capture](mockups/world-workspace-desktop.png) | [Capture](mockups/world-workspace-mobile.png) |

Versions 3–4 and their evidence above are historical. Version 5 records the earlier two-section exploration. Version 6 is the current planning-control refinement. The owner accepted it as good enough for now, with backend changes explicitly in scope.


## Planning-control refinement · version 6

The owner accepted the two-section direction and identified **adding and arranging** as awkward. The first Guided Build section and its rendering helpers remain byte-for-byte identical to version 5. Workspace changes:

- Direct + Place / + Faction / + Character actions on selected existing places and on planned places; new rows focus their name immediately.
- Recursively nested planned rows with explicit placement and membership summaries. Move, connect & guide discloses the parent, faction and guidance fields. Open editors survive unrelated additions.
- Planned entries appear in the shared outline as well as the graph. Renames update labels, placement summaries and target options without changing IDs.
- Generate planned additions and the full scope summary move to the workspace toolbar. AI suggestions and bulk counts/named lists are optional disclosed tools; they are no longer prerequisites for a single manual addition.
- Leaf removal offers Undo without discarding later additions or edits. Connected parents require reassignment before removal.

Fifteen targeted interaction assertions passed: scoped direct addition/focus; adding a character beneath a planned place; collapsed details; connected-parent protection; open-editor retention; separate membership/containment; synchronized rename; move preserving guidance/membership; removal/Undo availability; Undo preserving intervening additions; shared outline; graph names; stale-suggestion handling; generation preserving original records and clearing completed additions; preserved placement and membership after generation. Both 390px and 320px layouts had no document overflow. Syntax and HTML anchor checks passed. Initial Guided Build HTML/rendering helper equality was checked against fetched version 5.

[Desktop controls](mockups/world-planning-controls-desktop.png) · [Mobile controls](mockups/world-planning-controls-mobile.png). The prototype still uses local fixed sample generation; persistence, live AI, full typed validation and partial-job recovery remain implementation work. Version 5 screenshots are retained as historical evidence.

Version 6 was refetched and matched the local HTML. A bulk request at the 100-character boundary produced 100 draft entries without applying them or changing existing world state.

Owner acceptance: version 6 is sufficient to carry into implementation planning. Backend changes are required, as recorded in [BACKEND-HANDOFF.md](BACKEND-HANDOFF.md). No further layout polishing is requested for this pass.
