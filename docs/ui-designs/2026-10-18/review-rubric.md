# Spine Pair Review — mythosCircle

## Overall verdict

This is a strong, unusually well-disciplined contract pair. Token continuity
with the 09-21 spines is exact (zero value changes across 146 inherited
tokens), every `{token}` reference in both files resolves, and every memlog
decision from the 09-23/09-24 sessions is carried forward faithfully, with
open points tagged `[ASSUMPTION]` and mirrored to Open Questions. Not flat
"strong" because a downstream consumer will trip on four mediums in the
first build: no contrast targets stated (and at least one as-specified
accent combo fails normal-text AA), no component rows for `kind-tabs` /
`tag-chip` / `state-chip`, a direct 640px-vs-560px stepper breakpoint
contradiction, and no `sources` declaration in either frontmatter.

## 1. Flow coverage — adequate

All six Key Flows were checked for the four structural elements; every flow
has a named protagonist (Yigit), numbered steps, an explicit **Climax:**
beat, and at least one failure path (Flow 1 has two). The flows exercise
the spine's load-bearing decisions well: F1 (Tonight/Tier 1 + ritual +
revisions-down), F2 (Capture→Author, E.1, dial default), F3 (registry walk,
archetypes, offered companions, `located_in`, chips, preview, suggested
next steps), F4 (enrich/lazy render/guide-box/JSON ban), F5 (night thread,
E.2+Q6), F6 (verbs + knowledge 3-state + "Take it back", A.2/A.3/E.3).

### Findings
- **[low]** Requirement families with no journey exercising them: C.2
  atmosphere & specificity fields, C.6 templates-as-starters (prose only),
  F.1 ego-graph, F.5 danger-zone delete, archive restore, and the ⌘K
  palette (the entity picker does appear, Flow 1 failure). (EXPERIENCE.md
  §Key Flows, F1–F6.) *Fix:* fold one beat into existing flows (start Flow 3
  from a template; Flow 4 failure shows a delete confirm; mention the
  ego-graph in Flow 6 step 1) or explicitly accept prose coverage.

## 2. Token completeness — strong

All 217 frontmatter tokens are defined and valued; all 101 DESIGN.md and 36
EXPERIENCE.md `{…}` references resolve (the only unmatched strings are the
literal API-path `{id}` in the revisions-endpoint URLs and the header's
illustrative `{group.name}` — false positives). All 19 color tokens carry
values (`accent.ghost` is a valid `rgba(...)`; hexes for the other 18).
Token continuity with 09-21 verified by diff: zero value changes
(color/type/space/radius/elevation identical); only metadata changed and
`spec:` dropped.

### Findings
- **[medium]** No contrast targets are stated for any load-bearing
  combination. EXPERIENCE.md defers ("Visual contrast lives in
  `DESIGN.md`", §Accessibility Floor) but DESIGN.md §Colors never
  quantifies. Computed on the token values: `accent.primary` text on
  `surface.selected` = 3.02:1 (segmented active labels), on
  accent-ghost-over-raised = 3.10:1 (active generate-chip label),
  white on `accent.primary-hover` = 3.70:1 (primary-button hover) — all
  fail AA normal text (4.5:1) — and `text.secondary` on `accent.primary`
  (dial/stepper current-level label if the label renders on the fill) =
  1.76:1. `accent.on-primary` on `accent.primary` at rest = 4.66:1 (passes,
  no margin). (DESIGN.md §Colors, L335–382; §Components dial/segmented-row/
  generate-chip [ASSUMPTION] tags.) *Fix:* commit targets in §Colors —
  "body ≥ 4.5:1 on all resting surfaces; accent-on-selected/ghost text
  combos must meet 4.5:1 (darken the accent or enlarge); hover must not
  drop below resting" — and resolve the `[ASSUMPTION]` accent treatments
  against it. Sibling `review-accessibility.md` F1/F3–F6 carries the
  numeric detail; this is the rubric's "targets stated" requirement.

## 3. Component coverage — adequate

Every `{components.X}` reference in either file resolves to a frontmatter
token (56). Component names are identical across the two files wherever
both specify behavior. Checked against both required homes: the full
One-Create and Tonight/run-loop sets (36 anatomies + 31 behavioral rows)
are present with real rules, not one-word descriptions, and the v2 carried
set is intact.

### Findings
- **[medium]** `kind-tabs` is used as a component — DESIGN.md §Layout &
  Spacing "World roster" (L450), EXPERIENCE.md IA table (L61), §Responsive
  (L560) — but has no row in DESIGN.md.Components and no row in
  EXPERIENCE.md.Component Patterns; the token carries only
  background/border/radius. Its selected-state treatment is even tagged
  `[ASSUMPTION - tab treatment]` (DESIGN.md §Colors L354), and that
  assumption has no Open Questions counterpart. *Fix:* add a "Kind tabs"
  row to both files (anatomy: registry-driven tab set per kind + note slot;
  behavior: tab presence rules, selection semantics, empty-roster
  rendering) and resolve the assumption into OQ.
- **[low]** `tag-chip` (used DESIGN.md L535/579/628, EXPERIENCE.md
  L303) and `state-chip` (DESIGN.md §Colors L377) have tokens but no
  dedicated row in either file; their anatomy and behavior live implicitly
  inside host components (cast strip, archetype card, hook row). *Fix:*
  one row each or a declared "token-only primitive" note — otherwise
  story-dev invents chip state rules (hover, active, removal).
- **[low]** The four inherited button/card tokens (`button-primary`,
  `button-secondary`, `card`, `input`) are never bound to any control: v3's
  primary CTAs ("Build into the world", "Go", "Ask", commit bar) have no
  anatomy row and no named button variant anywhere. *Fix:* bind the CTAs to
  `button-primary` (or state the tokens exist for codegen only).
- **[low]** `segmented-row` and the authored-section markers
  (`authored-filled-marker`, `model-will-write-marker`) have no dedicated
  EXPERIENCE.md rows; their behavior is only reachable via the Interaction
  Primitives `radiogroup` bullet and the Review-rail row. *Fix:* one merged
  row or an explicit covered-by note.

## 4. State coverage — adequate

Twelve state rows cover the surfaces that carry real state risk: the
Create-walks job matrix (queued/running/built/failed × rail behavior,
conflict, stale edit, preserve-on-failure, offline/WS drop, load failure),
dial levels, flat-record, enrich in flight, night-thread lifecycle,
knowledge 3-state, consequence verbs, archive (archived/restored/deleted),
switcher mid-flight, revisions-endpoint down. Permission-denied correctly
N/A (single-user local tool). Tonight's empty-cast and revisions-down cases
are carried by Flow 1 failures.

### Findings
- **[low]** Missing empty/initial states: World roster (empty world, or a
  kind tab with zero rows), Relations (empty graph), Library (empty drafts,
  Generic isolation), kind-tabs (which tabs render for each kind). *Fix:*
  three empty-state rows + a tab-presence rule in State Patterns.

## 5. Visual reference coverage — strong

`imports/lore-weaver-create-city-2026-09-24.md` is linked inline in both
spines at the relevant section (DESIGN §Inspiration & References;
EXPERIENCE §Inspiration & Anti-patterns), each naming what it illustrates;
`.working/archetype-templates.md` likewise (DESIGN Inspiration; EXPERIENCE
registry). Both files exist on disk. Spines-win-on-conflict is stated once
per file (both headers). No mockups/ or wireframes/ directories exist in
this workspace, so nothing is orphaned.

### Findings
- **[low]** The 09-21 mockups are referenced only as a group — "The 09-21
  mockups remain *shape* references only; their IA is superseded" (DESIGN.md
  §Inspiration & References) — without enumerating `mockups/shell-v2.html`,
  `create-v2.html`, `accept-screen.html`, `forge-wizard.html` or naming what
  each illustrates. Given the explicit shape-only demotion and that they
  live in the sibling 09-21 workspace, acceptable; a one-line enumeration
  would make the reference checkable. *Fix:* enumerate or drop the pointer.

## 6. Bloat & overspecification — strong

No pixel specs where tokens cover them (hard pixels — 2px accent bar, 3px
lore rule, 56px icon rail, 64ch, breakpoints, stroke widths — all sit on
properties with no token; they are the spec, not restatement). No source
restatement: no personas/FEs/scope recycled; the Foundation "new backend
surface" inventory is genuinely load-bearing, and the carried pieces say
"carried" instead of restating. DESIGN.md carries editorial voice
(appropriate); EXPERIENCE.md prose stays behavioral — the single ornamental
flourish is the command-palette rationale quote "makes the 108 feel like 3"
(from the memlog, so it is a committed rationale, not decoration).

### Findings
- **[low]** "The nudge IS the feature" appears four times (EXPERIENCE.md
  registry §IA L159, State Patterns L330, Component Patterns enrich-nudge
  row; DESIGN.md Components L615) and "never enqueues, never discards"
  three times (IA, Component Patterns shell row, State Patterns switcher
  row). Each instance lands in a different consumer-facing section, so it
  is defensible redundancy — but one canonical line per file plus a pointer
  would read tighter. *Fix:* keep the first instance per file, shorten the
  rest.

## 7. Inheritance discipline — adequate

Token continuity claim verified by diff (see §2 — exact). Requirement codes
(A.1–A.3, B.1–B.5, C.1–C.6, E.1–E.5, F.1–F.6, Q1–Q6, owner 09-24 verdicts)
are used verbatim and consistently across both files and match the memlog
content — spot-checked: dial semantics (full section set at every level),
parallel `entity_session_state`/`entity_knowledge_state` tables, revisions
endpoint + registry endpoint, offered-companions softening, one-edge-one-name
(no `based_in`), trimmed place bones (no size/population), suggested
next-steps + explicit-click preview as review-rail/token behaviors.
EXPERIENCE.md token references resolve to DESIGN.md tokens by name (verified
programmatically: 33 unique component refs, all in frontmatter).
Glossary is consistent ("Take it back", "Tonight", "could-go-wrong", the
Night thread).

### Findings
- **[medium]** Neither frontmatter declares `sources`. validate.md requires
  it to resolve; both files name their sources only in prose (`.memlog.md`,
  `LANGUAGE.md (2026-09-21-r2)`, the owner deck of 2026-09-23), and
  DESIGN.md additionally dropped the 09-21 `spec:` URL. A consumer script
  that reads sources from frontmatter gets nothing. *Fix:* add
  `sources: [.memlog.md, LANGUAGE.md, ../ux-mythosCircle-2026-09-21/DESIGN.md, ../ux-mythosCircle-2026-09-21/EXPERIENCE.md]`
  to both files.
- **[low]** "No size slider, no population field" (EXPERIENCE.md Flow 3;
  DESIGN.md Components archetype-card L537–538) is a place-scoped trim, but
  the templates both spines cite as content still carry "**Scope**
  (slider)" for factions (`.working/archetype-templates.md`, faction shared
  bones). A consumer can read the sentence as global. *Fix:* scope the
  sentence to place archetypes or drop the faction Scope slider.

## 8. Shape fit — strong

DESIGN.md: all eight canonical sections present in locked order (Brand &
Style → Colors → Typography → Layout & Spacing → Elevation & Depth →
Shapes → Components → Do's and Don'ts), with only the earned
Inspiration & References tail (required-when-applicable: the Lore-Weaver
import is memlog-evidenced). EXPERIENCE.md: all eight required defaults
present (Foundation, IA, Voice and Tone, Component Patterns, State
Patterns, Interaction Primitives, Accessibility Floor, Key Flows), plus
both required-when-applicable sections (Inspiration & Anti-patterns,
Responsive). No invented sections. The two files split visual-vs-behavioral
cleanly and cross-reference correctly.

### Findings
- **[medium]** The stepper collapse breakpoint contradicts across files:
  DESIGN.md §Components/Stepper says "Below `640px` collapses to 'Step N of
  M — {name}'" (L526) while EXPERIENCE.md §Responsive says "`< 560px` …
  Stepper collapses to 'Step N of M — {name}'" (L561). Same behavior, two
  numbers — a consumer cannot build both. (Sidebar 900px, Create stack
  1020px, walk-grid 560px agree across the files.) *Fix:* pick one (560px
  matches the other narrow-width behaviors) and state it once — ideally as
  a breakpoint token in DESIGN.md so the two files stop each owning the
  number.

## Mechanical notes

- **"dime dials"** typo: EXPERIENCE.md §Accessibility Floor, L370 —
  "pickers, dime dials, verb rows" → "dial(s)".
- DESIGN.md `[ASSUMPTION - tab treatment]` (L354) has no Open Questions
  counterpart; EXPERIENCE.md's Open Questions (9 items) mirror the
  behavioral assumptions (parse, step map, level sets, transport, geometry,
  known-by-party, ego fallback, sandbox, Tier-2 gate) exactly, but the
  visual-call assumptions (tab treatment, dial marker geometry, ego nodes,
  chip active treatment, preview display scale, lg radius) are DESIGN-side
  only. The 09-24 memlog does not decide any of them; treat as open if a
  strict OQ mirror is wanted.
- Mermaid: no diagram blocks in either file; nothing to check.
- Frontmatter metadata is consistent across the pair (`status: draft`,
  `updated: 2026-09-24`, identical topic strings); DESIGN.md carries the
  full token block, EXPERIENCE.md correctly minimal. `sources`/`spec`
  absent (see §7 M-finding).
- The carried-inheritance labels are honest: "carried" items match the
  09-21 frontmatter/components exactly; `preview-card`, `guide-box`,
  `kind-tabs` are genuine v3 additions whose "new" status is not flagged in
  the DESIGN.md Components copy (candidate-card marks its delta, preview
  card does not) — cosmetic, but the §token-continuity parenthetical also
  under-lists them.