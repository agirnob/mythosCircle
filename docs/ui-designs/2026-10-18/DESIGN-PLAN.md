---
status: draft
updated: 2026-10-18
scope: design-planning
---

# Finish the mythosCircle interface

Owner direction: Overview, Tonight, and World look good and modern; the remaining screens feel disconnected and unfinished. Use those three screens as the visual references. The work is to carry their design language through the whole application, with concrete screen designs and interaction states before implementation.

**Latest direction supersedes the earlier first-draft priority:** leave Forge as it is; redesign **Guided Build, Add Character and Your Worlds**. Make custom lore larger and more inviting while preserving freeform writing. Improve the one-by-one world resume layout. A dedicated private Library for **Generic characters only for now** is selected; its page/route is not implemented. The current exploration separates familiar initial Guided Build from a persistent World workspace with nested cards, a secondary graph and scoped AI-assisted expansion planning. Library scope is selected as private Generic characters only for now.

The paired [DESIGN.md](DESIGN.md) and [EXPERIENCE.md](EXPERIENCE.md) own the current visual and behavioral rules. This plan organizes the work. Proposed layouts and priorities below remain reviewable decisions, not approved application changes.

## Evidence and limits

- Inspected current router, shell, tokens, creation pages, entity page, shared editors, authentication pages, and admin components on 2026-10-18.
- Verified Superdesign authentication and exported the existing Forge draft, current version 7, to [a local interactive preview](mockups/forge-superdesign-v7.html). New World and Overview baseline drafts have been generated; these are design artifacts, not application changes.
- Captured the actual Vue Forge, World and Overview pages at 1440px and 390px using a read-only synthetic fixture API. The [Overview desktop](mockups/overview-current-desktop.png)/[mobile](mockups/overview-current-mobile.png), [World desktop](mockups/world-current-desktop.png)/[mobile](mockups/world-current-mobile.png) and [Forge desktop](mockups/forge-current-desktop.png)/[mobile](mockups/forge-current-mobile.png) captures contain sample data, not private campaigns. Fixture-account rendering does not verify real authentication or backend behavior.
- Checked the saved Forge v7 prototype: name and concept survive Quick → Detailed → Quick; mobile document width is 390px at a 390px viewport. Its [desktop](mockups/forge-superdesign-v7-desktop.png) and [mobile](mockups/forge-superdesign-v7-mobile.png) screenshots show different draft states (empty and sample-filled). This limited check does not certify keyboard semantics, contrast or every interaction.
- [Independent consistency/accessibility review](review-consistency-accessibility-2026-10-18.md) identified contrast and interaction-spec gaps. The guides now prescribe passing button/input treatments and measurable target, focus and zoom checks; the application has not yet been changed to meet them.
- Current working files include unrelated regeneration changes. These planning changes do not modify application code.

## Tools and purpose

| Tool | Concrete job | Availability |
|---|---|---|
| Superdesign | Preserve Forge reference; design Guided Build, Add Character and Your Worlds against existing anchors; export selected drafts | Connected, team Personal; existing Forge project and draft verified |
| 12ui | If layout alternatives are needed, improve a captured screen and explicitly select a candidate; use an approved anchor image to extend sibling screens | Installation attempted; version check failed and CLI unavailable. Using connected Superdesign and its supported author/import workflow. |
| agent-browser | Capture current/reference screens, exercise prototypes, inspect keyboard behavior and compare responsive layouts | Available; local login inspected; authenticated pass pending |
| frontend-design | Translate selected drafts into the existing Vue components and shared styles | Skill available; implementation is a later stage |
| bmad-ux | Keep the two guides and decision log aligned with reviewed screen decisions | Active |
| Figma | Optional editable design system and handoff | Found in plugin store; not installed/connected |
| MagicPath | Optional interactive prototype and flow comparisons | Found in plugin store; not installed/connected |

Use Superdesign as the primary design canvas. Figma and MagicPath are alternatives when editable layers or collaborative prototyping add value; neither is required to start. Avoid maintaining conflicting design systems in multiple tools.

## 1. Capture and freeze the references

Capture Overview, Tonight, and World with representative private test data at 1440px and 390px, plus a 768px layout check. Record shell width, page insets, heading rhythm, section/card spacing, button hierarchy, form treatment, and content density. Include a long world name and a missing portrait. Store sanitized reference images in this workspace's `mockups/` folder.

Deliverable: reference screen pairs and a shared pattern sheet. Forge, World and Overview desktop/mobile samples are captured; Tonight and 768px checks remain additional reference work. Acceptance: a reviewer can point to the source of each visual choice on a redesigned screen. Preserve these references unless a specific accessibility or behavior problem needs correction.

## 2. Define reusable patterns before expanding pages

Design examples for page header, destination/context strip, form section, choice tile, disclosure, primary/secondary/danger action, editable list row, request status, proposal review, empty/error state, and confirmation dialog. Choice tiles are Field variants, disclosures are Section navigation variants, and editable rows are Field groups in the shared catalog. Reuse existing primitives where they fit. List missing primitives as implementation work; a token name or mock is not proof a component exists.

Deliverable: one small pattern catalog with default, focus, selected, disabled, and error states. Acceptance: the same field/button/status looks and behaves consistently on two different screens.

## 3. Retained reference set: Forge, World, Overview

Earlier on 2026-10-18 the owner selected **Forge, World, Overview**; the latest direction supersedes that redesign priority. These artifacts remain reference evidence. Forge now stays as is. Preserve the real World directory and Overview’s endorsed visual language as anchors. The World and Overview drafts are faithful reference baselines first; do not invent a redesign of their already endorsed style.

Deliverable: three linked Superdesign drafts, with responsive behavior and an explicit list of proposed refinements. Forge remains unchanged; its existing Quick/Detailed editor and surrounding design are references. World remains the searchable directory; Overview remains the campaign summary. They keep distinct purposes and real routes.

The first set is on the [shared Superdesign canvas](https://superdesign.dev/teams/5340a30a-1c5b-4ece-80a7-48c84c8997a9/projects/49dc6237-ff17-4b26-9c70-de2a01f28ca4):

| Draft | Review link | Scope |
|---|---|---|
| Forge | [Saved v7 interactive reference](mockups/forge-superdesign-v7.html) | Existing refined design, preserved unchanged as a reference; latest direction does not schedule its redesign. |
| World | [World baseline](https://p.superdesign.dev/draft/ad5c99be-2b33-43de-97cd-31bd42580414) · [local HTML](mockups/world-superdesign-baseline.html) | New faithful current-directory baseline using synthetic sample content; source structure checked, full accessibility/interaction review pending. |
| Overview | [Overview baseline](https://p.superdesign.dev/draft/38e0cef3-32a3-4cdf-823f-91f7bfcc2877) · [local HTML](mockups/overview-superdesign-baseline.html) | New faithful current-summary baseline using synthetic sample content; source responsive/focus rules restored to the generated baseline. |

Resume records are saved per route in `.superdesign/resume.json`; the original Forge target remains intact. Baseline generation used Gemini 3.1 Pro after querying the current model catalog. No alternative visual directions were generated.

Rendered local baselines at 1440px and 390px: [Overview desktop](mockups/overview-superdesign-desktop.png)/[mobile](mockups/overview-superdesign-mobile.png), [World desktop](mockups/world-superdesign-desktop.png)/[mobile](mockups/world-superdesign-mobile.png). Both generated documents measured 390px scroll width at a 390px viewport. Overview’s desktop composition was visually compared with its current-app capture. These limited layout checks do not certify all keyboard, contrast or state behavior.

Initial comparison from the captured pages and saved Forge v7 preview:

- World and Overview already share the same display hierarchy, navy surfaces, violet actions and entity cards. Preserve these as the reference language.
- Current Forge still has a plain header, long introductory copy, browser-default destination and submit buttons, and a dense generated-fields summary. Its inner editor already uses the current surface and input system.
- Forge v7 demonstrates a compact destination strip, secondary History action, consistent generation action and shorter summary. It is a design reference to reconcile with the actual editor, not a replacement implementation to paste over it.
- The saved Forge preview shows the global shell; a campaign Forge must retain its campaign navigation/context. Do not copy the preview's global navigation into campaign screens.

## 4. Current redesign packages

| Surface | Confirmed need | Proposed design deliverable | Required review states |
|---|---|---|---|
| Your Worlds — resume | One-by-one list needs a better layout | Separate Continue from Create. Explore searchable responsive world cards with readable title/theme/pitch, Continue primary and management secondary. Card grid is proposed, not selected. | Empty, few/many worlds, long titles, no search matches, loading/error, deletion/cancel, mobile. |
| Your Worlds — create/lore | More inviting, larger, sectioned custom lore while preserving freeform writing | Generous resizable prose editor; optional chapter prompts such as premise, places, people and tensions. Preserve exact `custom_lore` text and a single freewriting path; no required chapter schema, automatic rewrite or truncation. | Empty invitation, long pasted/written lore, prompts open/closed, validation/network failure with text preserved, mobile. |
| Add Character | Redesign authored entry | Group identity, narrative, mechanics and relationships with completeness/review and batch navigation. Preserve existing zero-LLM behavior, authored text and actual required fields; no blank-fill generation promise. | Partial/invalid, second batch member, stat/relation edits, pending/failure, committed result. |
| Guided Build | Plan a city, places x/y/z, factions a/b/c and per-place characters l/m/n/o as relationships | Selected: nested cards main editor, synchronized graph secondary, and AI-assisted structural planning before generation. Provide keyboard-readable cards, explicit typed edges, new/existing badges, stable identity and scope review. | Empty/complex plan, rename/reorder/reassign/remove, duplicate keys/names, unresolved/invalid targets, existing entity updates, direct-commit build/cancellation/partial failure. |
| Library — selected scope | Dedicated private page for Generic characters only for now | Proposed `/library` route, not yet implemented. Exclude Generic from Your Worlds; preserve per-account storage and generation isolation. No place/faction/starter categories or reusable-copy promise. | Empty/populated/search-no-match, loading/error, access boundaries and actual move pending/partial failure. |

Forge is outside the redesign packages. Ask/review and other pages remain follow-on cohesion work, preserving their current staged-candidate and write contracts.

Read [nested-build-feasibility.md](nested-build-feasibility.md) before designing the planner or Library. Named nested plans can serialize to the existing flat roster using unique stable `key`, `target_key` and committed `target_id`; the current GUI adapter needs work. Names are labels, and normalized place/faction duplicates can merge/update. Make these consequences visible before submission. Per-parent generated counts are not supported by current global `generate_counts`; complete draft-key/result mapping is also new work.

A plan preview reviews **input intent**, not generated candidates. Guided Build currently commits generated waves directly; cancellation can leave wave-1 core committed. Never draw a generated-output Accept gate or promise atomic cancellation. Generic is private characters-only storage; current move transfers with a fresh ULID and dropped edges, commits destination then deletes source separately. It is neither atomic nor a reusable copy.

The planner interaction and private Generic-characters-only Library scope are selected. Detailed AI structural planning is a new feature contract, not existing backend behavior. Do not convert broader Library categories into confirmed features.

### Guided Build and persistent World workspace — current direction

The owner approved exploring a two-section flow after rejecting the combined seed-and-planning form. **Guided Build generates the initial foundation; the World workspace then supports ongoing expansion from actual world content.** The workspace is available again on later visits. It is not an obsolete snapshot of the original generation request.

The sequence is **familiar Guided Build inputs → initial content generation → actual world workspace → select an existing place → propose/manual-plan additions → edit structure → generate only those additions → refresh actual world and clear completed planned items**. Nested cards and a keyboard-readable outline are primary; the synchronized graph is a secondary view of existing entities and unfinished additions. Existing content carries a solid border and explicit Existing label; additions carry a dashed border and explicit Planned label. Color alone does not distinguish them.

AI structure suggestions operate inside the expansion section. Requesting or applying a suggestion changes no committed content and enqueues no content-generation job. Suggestions bind to the selected existing target, world revision and current plan revision; late responses cannot replace intervening edits. Manual counts produce explicit scoped slots, which can remain model-named. Names, place assignments and faction memberships stay editable before generation. Names remain labels; identity uses draft keys or existing entity IDs. Containment and membership are distinct typed edges.

The first build retains its current per-kind additional counts, named/list/shaped entries, partial authored figures and lore/notes generation context. Returning to the first section retains useful inputs, but must not silently resubmit a consumed build. Existing campaigns can enter the World workspace directly. An empty workspace offers initial Guided Build; it does not invent existing content.

Expansion generation submits unfinished additions and their links only, with selected world entities as context/targets. Do not submit existing records as new seeds. Current name-based place/faction matching can update existing records, so the new additions-only contract must resolve normalized name collisions explicitly before submission; the concept blocks them and asks for a rename or use of the existing target. A different explicit edit/update operation remains separate. Global counts alone cannot implement exact per-parent allocations; compile them into keyed draft entries with links, or add a validated allocation adapter.

After generation, refetch the actual resulting entities and edges. Reconcile stable draft keys to committed IDs from confirmed results; remove only confirmed completed plan items. Partial failure or cancellation retains unfinished items and reports committed results accurately. Retry must avoid creating duplicates or replaying completed items. Existing Guided Build commits generated waves directly; structural approval does not introduce a generated-prose acceptance stage or atomic rollback guarantee. All world writes remain through the backend store, and access stays private to the invited account.

The two-section layout is a reviewable design proposal, not shipped behavior. The current prototype uses fixed sample generation and local state. The typed planning service, scoped input/result mapping, name-collision enforcement, recovery and persistent committed-plus-draft workspace require implementation.


## 5. Carry the patterns through reading and editing

| Surface | Proposed design brief | Required review states |
|---|---|---|
| Entity detail and editor | Coherent character/place/faction headers; readable story sections; common edit/regenerate/media action hierarchy; replace system terminology in normal copy; bring mechanics and relation editors into the shared form system | Each kind, sparse/flat data, no image, long prose, edit/cancel, validation, regeneration scope, failed/conflicting save, missing entity |
| Graph | Shared header and context, grouped filter toolbar, clear focus breadcrumb, accessible relation list alongside visualization | Dense graph, focused graph, no edges, no filter matches, renderer failure/retry, keyboard navigation, narrow screen |
| Kind collection | Use the same search/filter and entity card/row treatments as World | Empty kind, results/no results, long names, missing images, narrow screen |
| Your Worlds | Active priority described above; align resume/create/delete hierarchy and inviting lore writing | Empty/populated library, create errors, loading, delete confirmation, narrow screen |
| Login/register | Bounded branded form, token-based inputs and buttons, clear help/error treatment using the existing brand | Empty/invalid form, submitting, failed authentication/registration, keyboard focus, narrow screen |
| Admin/settings | Preserve table and configuration density; use the same headers, fields and confirmation patterns | Loading/error, no users, filtered list, pagination, confirmation, denied access, settings failure |

## 6. Produce and review actual designs

For each work package:

1. Capture the current screen and dependencies; record what is working and what must change.
2. Reuse or reproduce the existing screen on Superdesign before exploring alternatives. Carry the reference shell, token context and actual feature constraints into the draft.
3. Show a default state and a consequential state (for example invalid form or proposal conflict), at desktop and mobile widths. Name any states covered only by the guide.
4. Review hierarchy, scanability, labels, draft recovery, action consequences and consistency with the three references. Record the selected direction and any unresolved decisions.
5. Update both guides with the selected visual and behavioral changes. Mark draft proposals as reviewed only when that review occurred.
6. Implement the selected package on a fresh branch from current `develop`, run relevant checks under Node 22, push and open a PR into `develop`; require `CI gate` before merge.

No visual refresh may silently alter whether a flow stages proposals, writes canon, uses an LLM, or commits authored text verbatim. UI mockups use sanitized sample data and do not call production write APIs.

## Ready-to-use target design brief

> Redesign Guided Build, Add Character and Your Worlds in the current mythosCircle navy/violet language anchored by Overview, Tonight and World. Leave Forge as it is. Your Worlds needs a clear Continue versus Create hierarchy: explore searchable responsive cards, and give custom lore a large inviting freewriting area with optional section prompts while preserving exact existing text. Add Character needs grouped authored fields, batch navigation and clear required-field validation; preserve zero-LLM submission. Guided Build needs a city plan with named places, factions and characters assigned by explicit relationships. The two-section exploration retains familiar initial Guided Build, then uses a persistent World workspace with nested cards, secondary graph and scoped expansion planning. Provide a complete keyboard-readable nested-card editor sharing its draft model with the graph. Distinguish new drafts from existing nodes; preserve stable identity across rename, show name collisions and typed edge meaning. Preview intended inputs only: generated build waves currently commit directly. A dedicated private Generic-characters-only Library is selected; `/library` is the proposed, unimplemented route. Your Worlds should list real campaigns only, excluding Generic. Do not invent per-parent generated counts, reusable copies, atomic move/cancellation, or unsupported schemas/features. The selected Library requires a proposed new `/library` route during implementation, not a claim that it already exists. Include mobile, long content and consequential states. List proposed backend/adapter work separately from supported behavior.

## Completion criteria

- Every active route in EXPERIENCE has a design disposition and state checklist.
- Every redesigned screen visibly belongs beside the reference screens; legacy local colors and browser-default actions are removed from the touched area.
- A short first-use view explains the task and next action without a wall of implementation text.
- Long names, long prose and narrow screens remain readable; no horizontal page overflow at 390px or 320px.
- Controls have labels, visible focus and reachable actions; errors explain recovery and retain authored input where the contract supports it.
- Selected proposals remain distinct from committed world content; confirmation and conflict paths remain explicit.
- Browser verification compares implementation with its approved draft. Required local checks and `CI gate` pass before merge.

## Decisions still needed

- Define and review the new AI structural-planning contract and detailed nested-card/secondary-graph layout; presentation direction is selected.
- Review the detailed selected characters-only Library layout and proposed `/library` navigation; broader categories stay outside initial scope.
- Select proposed Your Worlds layout and custom-lore section treatment; the card grid and prompt chapter names are proposals.
- Select actual per-screen mockups before calling their layouts final.
- Decide later whether the separate creation flows should be unified; visual consistency alone does not authorize the old One Create architecture.
- Decide whether an optional Figma or MagicPath connection adds enough value to maintain alongside Superdesign.

The [current four-screen redesign brief](NEW-REDESIGN-BRIEF.md) records the confirmed planner and Library choices, example city structure, lore-writing direction and acceptance cases.

The [four interactive concepts and verification record](CONCEPT-REVIEW.md) are ready for layout review, with canvas links and local previews. They simulate planning/saving and do not implement production behavior.


### Owner acceptance and implementation scope

The owner accepted the current two-section / workspace-control direction as **good enough for now**. Further visual exploration is paused for this pass. Implementation explicitly includes the backend work in [BACKEND-HANDOFF.md](BACKEND-HANDOFF.md): typed planning, scoped/model-named additions, validation and collision rules, committed-ID reconciliation, persistence and partial-failure/idempotent retry behavior. The prototype is not a frontend-only implementation specification.
