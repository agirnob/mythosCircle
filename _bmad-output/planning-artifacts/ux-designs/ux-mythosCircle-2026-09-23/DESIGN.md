---
status: 'final'
updated: '2026-09-25'
topic: 'mythosCircle v3 full UX remake'
name: mythosCircle
description: Dark-first TTRPG AI-DM companion — prep tonight, run the table, and keep one living world with a local model.
spec: https://github.com/google-labs-code/design.md
sources:
  - .memlog.md
  - LANGUAGE.md
  - imports/lore-weaver-create-city-2026-09-24.md
  - ../ux-mythosCircle-2026-09-21/DESIGN.md
  - ../ux-mythosCircle-2026-09-21/EXPERIENCE.md
color:
  bg.base: '#161311'
  bg.sunken: '#120f0d'
  surface.raised: '#211c18'
  surface.overlay: '#2b241f'
  surface.selected: '#262b3d'
  border.subtle: '#2c3038'
  border.strong: '#3a3f4b'
  border.input: '#606879'
  text.primary: '#ebdcc9'
  text.secondary: '#a89885'
  text.disabled: '#858585'
  text.link: '#8ab4ff'
  accent.primary: '#b8522b'
  accent.primary-hover: '#a04422'
  accent.secondary: '#c8923c'
  accent.on-primary: '#ffffff'
  accent.ghost: 'rgba(184,82,43,0.14)'
  status.error: '#ff7b72'
  status.success: '#7bd88f'
  status.warning: '#f5b54a'
  status.info: '#8ab4ff'
type:
  family.display: 'Georgia, "Times New Roman", serif'
  family.body: 'system-ui, sans-serif'
  family.mono: 'ui-monospace, monospace'
  scale.display: 32px
  scale.h1: 28px
  scale.h2: 20px
  scale.h3: 16px
  scale.body: 16px
  scale.small: 14px
  scale.caption: 12px
  scale.micro: 11px
  scale.title: 22px
  weight.regular: '400'
  weight.medium: '500'
  weight.semibold: '600'
  weight.bold: '700'
space:
  '1': 4px
  '2': 8px
  '3': 12px
  '4': 16px
  '5': 24px
  '6': 32px
  '7': 48px
  shell-max: 1280px
  shell-pad: 16px
  section-gap: 24px
  sidebar-w: 232px
  rail-w: 340px
radius:
  sm: 6px
  md: 8px
  lg: 12px
  full: 9999px
elevation:
  raised: 'none'
  overlay: '0 8px 28px rgba(0, 0, 0, 0.45)'
  popover: '0 12px 40px rgba(0, 0, 0, 0.55)'
components:
  button-primary:
    background: '{color.accent.primary}'
    foreground: '{color.accent.on-primary}'
    radius: '{radius.sm}'
  button-secondary:
    background: 'transparent'
    foreground: '{color.text.secondary}'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  button-quiet:
    background: 'transparent'
    foreground: '{color.text.secondary}'
    radius: '{radius.sm}'
  button-danger-ghost:
    background: 'transparent'
    foreground: '{color.status.error}'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  card:
    background: '{color.surface.raised}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  input:
    background: '{color.bg.base}'
    border: '{color.border.input}'
    radius: '{radius.sm}'
  card-select-active:
    background: '{color.surface.selected}'
    border: '{color.accent.primary}'
    radius: '{radius.sm}'
  tag-chip:
    background: '{color.accent.ghost}'
    foreground: '{color.text.primary}'
    radius: '{radius.full}'
  state-chip:
    background: '{color.surface.overlay}'
    foreground: '{color.text.secondary}'
    radius: '{radius.full}'
  stepper-active:
    background: '{color.accent.primary}'
    foreground: '{color.accent.on-primary}'
  stepper-done:
    background: 'transparent'
    foreground: '{color.text.secondary}'
    border: '{color.accent.primary}'
  authored-section:
    background: '{color.surface.raised}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  authored-filled-marker:
    foreground: '{color.text.primary}'
  model-will-write-marker:
    foreground: '{color.text.secondary}'
    border: '{color.border.subtle}'
  stat-subsection:
    background: '{color.bg.base}'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  relation-draft-row:
    font: '{type.family.mono}'
    foreground: '{color.text.primary}'
  job-feed-row:
    background: '{color.surface.raised}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  candidate-card:
    background: '{color.surface.raised}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  reroll-scope-label:
    foreground: '{color.text.secondary}'
    font: '{type.scale.small}'
  conflict-dialog:
    background: '{color.surface.overlay}'
    border: '{color.status.warning}'
    radius: '{radius.md}'
    elevation: '{elevation.overlay}'
  stale-edit-notice:
    foreground: '{color.status.error}'
    font: '{type.scale.small}'
  error-envelope:
    foreground: '{color.status.error}'
    font: '{type.family.mono}'
  app-shell:
    background: '{color.bg.base}'
    radius: '{radius.md}'
  sidebar-item:
    background: 'transparent'
    foreground: '{color.text.secondary}'
    radius: '{radius.sm}'
  sidebar-item-active:
    background: '{color.surface.selected}'
    foreground: '{color.text.primary}'
    radius: '{radius.sm}'
  mode-switch:
    background: '{color.bg.sunken}'
    border: '{color.border.subtle}'
    radius: '{radius.full}'
  mode-switch-active:
    background: '{color.surface.selected}'
    foreground: '{color.text.primary}'
    radius: '{radius.full}'
  review-rail:
    background: '{color.surface.raised}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  ask-box:
    background: '{color.bg.base}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  segmented-row:
    background: 'transparent'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  segmented-cell-active:
    background: '{color.surface.selected}'
    foreground: '{color.text.primary}'
    border: '{color.accent.primary}'
  tonight-sheet:
    background: '{color.bg.base}'
  tonight-banner:
    background: '{color.surface.raised}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  sigil:
    background: '{color.accent.ghost}'
    border: '{color.accent.primary}'
    radius: '{radius.full}'
  cast-strip:
    background: 'transparent'
    border: 'none'
  hook-row:
    background: 'transparent'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  change-line:
    foreground: '{color.text.secondary}'
    font: '{type.scale.small}'
  note-block:
    background: '{color.surface.raised}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  thread-line:
    background: 'transparent'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  knowledge-chip:
    background: '{color.surface.overlay}'
    foreground: '{color.text.secondary}'
    radius: '{radius.full}'
  verb-row:
    background: 'transparent'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  preview-card:
    background: '{color.surface.raised}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  archetype-card:
    background: '{color.surface.raised}'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  generate-chip:
    background: '{color.accent.ghost}'
    foreground: '{color.text.primary}'
    radius: '{radius.full}'
  dial:
    background: '{color.bg.sunken}'
    border: '{color.border.subtle}'
    radius: '{radius.full}'
  dial-row:
    background: 'transparent'
    border: 'none'
  guide-box:
    background: '{color.bg.base}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  enrich-nudge:
    background: 'transparent'
    foreground: '{color.text.secondary}'
    radius: '{radius.sm}'
  kind-tabs:
    background: 'transparent'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  entity-picker:
    background: '{color.bg.base}'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
  command-palette:
    background: '{color.surface.overlay}'
    border: '{color.border.strong}'
    radius: '{radius.lg}'
    elevation: '{elevation.popover}'
  archive-group:
    foreground: '{color.text.secondary}'
    font: '{type.scale.caption}'
  confirm-dialog:
    background: '{color.surface.overlay}'
    border: '{color.border.subtle}'
    radius: '{radius.lg}'
    elevation: '{elevation.overlay}'
  danger-zone:
    background: '{color.surface.raised}'
    border: '{color.status.error}'
    radius: '{radius.md}'
  ego-graph:
    background: '{color.bg.sunken}'
    border: '{color.border.subtle}'
    radius: '{radius.md}'
  export-menu:
    background: '{color.surface.overlay}'
    border: '{color.border.subtle}'
    radius: '{radius.sm}'
    elevation: '{elevation.overlay}'
---

# DESIGN.md — mythosCircle v3 full UX remake

Redesign v3. Supersedes the 2026-09-21 unified-Create spines. Scope, from the
owner deck of 2026-09-23: ADD (run loop, personhood, universal authoring) +
CHANGE (creation experience, calm/law compliance).

**Token continuity:** every token name defined in the 09-21 DESIGN.md is kept
with its value — this remake changed none of them; v3 only *adds* tokens for
new surfaces (Tonight sheet, create walk modes, registry pickers, archive,
dial/guide regenerate, ego-graph, export menu).

**Law:** the design language in `LANGUAGE.md` (2026-09-21-r2) governs;
where a memlog decision touches a law point, the owner's later decision is
law. **Owner rule:** images are intent, not inventory — every control below
is priced against the memlog and `frontend/src`, never against mock images;
a control that exists only in a picture is BUILD, not extend.
**Spines win on conflict with any mock, wireframe, or import.**

## Brand & Style

mythosCircle is the DM's table, not a game board: a calm chamberlain's
workbench where the DM preps *tonight* (the Tonight landing), builds and
remembers the living world (One Create, per-kind rich pages), rewinds the
scene ("Take it back"), and consults the world mid-session (the Night ask thread).

Garden-variety modern dark app bound like an old book: deep-espresso
surfaces, serif display names, a terracotta wax-seal sigil per campaign,
thin rules instead of boxes, terracotta for *active / will-fire*, antique
brass for tags and highlights. Mood comes from portraits and words, never
chrome. Light theme is not a thing.

The visual language reconciles the two parents named in v2 — the app's
existing dark tonal steps and the Lore-Weaver inspirations (card-select
grids, live-preview rail, serif display names) — under the owner's Rustic
Dark Grimoire redirect (2026-09-25): deep-espresso surfaces, terracotta
will-fire, antique-brass tags. Where parents and redirect disagree, the
redirect wins; the spine keeps the tonal-step structure and re-hues it warm.
v3 adds one discipline: **kinds are first-class** — a place reads
like a place, a faction like a faction (Doctrine 2), and the registry makes
per-kind walks content, not code.

Creation is a walk for every kind (Doctrine 5) — with Capture as the front
door that *seeds* the walk (owner, 09-24): one line lands in the Author
sheet, never a separate quick-create island and never a gamble queue.
Character-hero doctrine holds: playable truth first, and the generation
dial can never compromise narrative completeness (narrative is always
complete; the dial weighs mechanics). The Tonight landing is print-honest:
it reads like a well-kept index card — calm blocks, thin rules, no
dashboard chrome — and prints through the existing media-free export
machinery.

Non-features are carried from the law: timeline, DM notes, token preview,
VTT export buttons, credits/cost, light theme. They appear in the reference
images; the law does not buy them, and no tokens are minted for them.

## Colors

Rustic Dark Grimoire values (owner redirect 2026-09-25). The v3 stories:

- **Base (`{color.bg.base}` `#161311`)** Deep Espresso — the app canvas and
  the Tonight sheet's resting surface. Never used for cards.
- **Sunken (`{color.bg.sunken}` `#120f0d`)** Charred Well — for wells: the
  `{components.dial}` track, the `{components.mode-switch}` track, code/job
  dumps, the `{components.ego-graph}` canvas, ask/guide-textarea beds.
- **Raised (`{color.surface.raised}` `#211c18`)** Dark Leather — the card
  surface and the Tonight block surface. Hierarchy comes from this one tonal
  step, not shadows.
- **Overlay (`{color.surface.overlay}` `#2b241f`)** Raised Board — for
  floating layers:
  the `{components.command-palette}`, `{components.entity-picker}` results,
  `{components.export-menu}`, `{components.confirm-dialog}`,
  `{components.conflict-dialog}`. Always paired with `{elevation.overlay}`
  or `{elevation.popover}`.
- **Selected (`{color.surface.selected}` `#262b3d`)** fills a chosen
  card-select or archetype card, an active segmented cell, the active
  sidebar item, the active mode-switch half, the active registry kind tab
  [ASSUMPTION - tab treatment], ringed or edged by accent. Never a resting
  surface. Cool-slate carryover from v2 — warm re-tint open, owner call.
- **Borders:** `{color.border.subtle}` `#2c3038` separates everything;
  `{color.border.strong}` `#3a3f4b` marks hover/focus-adjacent edges and
  the palette border. 1px everywhere except the 3px lore-quote rule.
- **Text:** `{color.text.primary}` `#ebdcc9` Aged Ivory body;
  `{color.text.secondary}` `#a89885` Faded Iron Gall for muted/help/queue labels and knowledge states;
  `{color.text.disabled}` `#858585` for disabled controls;
  `{color.text.link}` `#8ab4ff` for inline text links only — never
  buttons, never nav.
- **Accent (`{color.accent.primary}` `#b8522b`)** Terracotta Wax Seal — the
  will-fire voice: primary buttons, stepper current-step,
  `{components.dial}` current level, selected rings, focus rings, active
  `{components.generate-chip}` markers, the Tonight "Go" action, and the
  `{components.sigil}` ring. Hover is `{color.accent.primary-hover}`;
  text on accent is `{color.accent.on-primary}`; the translucent wash (chip
  fills, sigil bed) is `{color.accent.ghost}`.
- **Secondary (`{color.accent.secondary}` `#c8923c`)** Antique Brass — tags,
  pills, and highlights ONLY. Never a primary action, never a second
  will-fire voice: brass text on dark (6.7:1 base / 6.1:1 raised) or
  base-ink labels on brass fills (6.7:1).
- **Status:** `{color.status.error}` `#ff7b72` (unified),
  `{color.status.success}` `#7bd88f`, `{color.status.warning}` `#f5b54a`,
  `{color.status.info}` `#8ab4ff`. Status colors appear as text / icons /
  hairline borders only — never full-surface fills. The
  `{components.danger-zone}` takes the error hairline; terminal
  `{components.state-chip}` / `{components.knowledge-chip}` states take
  status *text*.

Avoid: the retired `#2f6feb` blue anywhere new; gradients; a third chromatic
accent beyond terracotta + brass; colored fills behind body text;
light-theme tokens (dual-theme stays deferred — dark-first single theme).

**Contrast targets (WCAG 2.2 AA, committed):** body text ≥ 4.5:1 on every
resting surface (verified: `{color.text.primary}` 13.7:1 base / 12.5:1
raised; `{color.text.secondary}` 6.6:1 base / 6.0:1 raised). Accent
(`{color.accent.primary}`) is a marker, never a body-text fill — active
labels render `{color.text.primary}`; accent stays for rings, underlines,
markers, and the sigil. `{color.accent.on-primary}` on
`{color.accent.primary}` 4.9:1 at rest, 6.3:1 on
`{color.accent.primary-hover}`. Disabled labels (`{color.text.disabled}`)
4.6:1 on raised and 5.0:1 on base — disabled controls never carry
load-bearing meaning as color alone. Inline links carry a resting underline
(non-color cue, 1.4.1) in addition to `{color.text.link}` (8.9:1 on base).
Input boundaries use `{color.border.input}` (3.3:1 non-text vs
`{color.bg.base}`, 1.4.11); terracotta focus rings ≥ 3.4:1 on all resting
surfaces.

## Typography

Unchanged ramp from 09-21, three voices, no webfont dependency.

- **Display** (`{type.family.display}`, Georgia/serif) speaks only for
  entity, kind, and campaign display names: the `{components.tonight-banner}`
  campaign name, roster rows, `{components.preview-card}` and
  `{components.candidate-card}` headers, rich-page headers — at
  `{type.scale.display}` 32px in the rail and `{type.scale.h1}` 28px on
  card headers [ASSUMPTION]. Nothing else: surfaces, buttons, labels stay
  sans.
- **Title** (`{type.scale.title}` 22px, body family) for surface titles
  ("Create", "Tonight", "World") and dialog titles.
- **Body** (`{type.family.body}`, system-ui) is everything else: base 16px,
  `{type.scale.small}` 14px for help/muted/job metadata and
  `{components.change-line}` text, `{type.scale.caption}` 12px for stepper
  labels and overline eyebrows (all-caps, `0.08em` tracking),
  `{type.scale.micro}` 11px for rail eyebrows, feed timestamps, revision
  metadata.
- **Mono** (`{type.family.mono}`, ui-monospace) stays reserved for machine
  output: job error payloads, IDs, dice strings, relation lines, revision
  ids inside change lines. Never prose, never labels.
- **Weights:** regular 400 default; medium 500 for card-select / archetype
  titles, active segment labels, rail emphasis; semibold 600 for sidebar
  labels and candidate names; bold 700 for the shell brand and display-name
  fallback.
- **Headings:** `h1` 28px, `h2` 20px, `h3` 16px-medium, all `1.3` line
  height; body `1.6`. Stepper step titles are `h2`; Tonight block titles
  `h3`. Rhythm target: 20px baseline cadence — `{space.5}` section breaks
  land on body line-height multiples where practical. Dense, not cramped:
  small text carries `1.5` line height minimum.

## Layout & Spacing

4-based scale unchanged (`{space.1}`–`{space.7}`). Rule of thumb:
micro-gaps `{space.1}`–`{space.2}`, related groups `{space.3}`–`{space.4}`,
section breaks `{space.5}`–`{space.6}`.

- **App shell (`{components.app-shell}`):** sidebar `{space.sidebar-w}`
  232px fixed + content column, content max `{space.shell-max}` 1280px with
  `{space.shell-pad}` 16px sides. Sidebar collapses to an icon rail (56px)
  below `900px`; content goes single-column; the Create work area stacks
  form-over-rail below `1020px`. Shell nav is Create / World / Relations /
  Library — one Create section, three mode routes beneath it.
- **Campaign switcher:** sits at the sidebar top under the brand; lists
  active campaigns, the Generic library, and an
  `{components.archive-group}` of archived campaigns. The switcher owns the
  working context for every surface — One Create carries no picker step.
- **Tonight landing (`{components.tonight-sheet}`):** one scroll column
  over the `{components.tonight-banner}` — ritual blocks in top-to-bottom
  order (recap, what-they-know, open hooks, pick scene, go), then the prep
  blocks (cast strip, recent changes, could-go-wrong) at `{space.5}` gaps,
  then the `{components.thread-line}` thread as the last block. Single
  column at all widths [ASSUMPTION - geometry]. Print is the existing
  media-free printer export — screen tokens do not govern print CSS.
- **Create work area:** form column (flexible, min `0`) + `{components.review-rail}`
  at `{space.rail-w}` 340px, `{space.5}` gutter; form content max ~`64ch`.
  The rail hosts `{components.preview-card}` for *every* kind. Both walks
  (Author and Generate) share this geometry — mode switches never reflow
  the shell.
- **Kind walks:** Concept/Details steps lay out as 2-up card-select grids
  (`1fr 1fr`, `{space.4}` gap, collapsing to 1-up below `560px`):
  `{components.archetype-card}` rows, environment / economy / power-base /
  leadership card selects. Tag inputs wrap; dials are full-width rows;
  `{components.generate-chip}` rows wrap under Details.
- **World roster:** `{components.kind-tabs}` over a *roster list*, not a
  108-card grid (a calm index into rich pages). Rows at `{space.4}` height:
  serif name, kind marker, dial marker, edge count; empty rows get the
  `{components.enrich-nudge}`. Density on demand: `{components.command-palette}`
  + `{components.entity-picker}` — never the 108-wall.
- **Rich pages:** character-page pattern per kind — tabbed stat tiles /
  section accordions at `{space.section-gap}`; `{components.ego-graph}`
  panel embedded at a fixed preview height, full map in Relations; one
  `{components.export-menu}`; a `{components.danger-zone}` at the page
  foot.
- **Vertical rhythm:** `{space.6}` 32px before action bars; feed rows
  compress to `{space.3}` padding — glanced at, not read.

## Elevation & Depth

Unchanged: depth is tonal first, shadow last.

- Resting hierarchy uses fill steps only — base → raised → overlay. No
  shadows on cards, inputs, sidebar, rail, or Tonight blocks
  (`{elevation.raised}` is `none`).
- Floating layers use `{elevation.overlay}` (entity-picker results,
  export menu, confirm/conflict dialogs); the
  `{components.command-palette}` is the one large transient surface and
  takes `{elevation.popover}`. Shadows are neutral black, never
  accent-tinted.
- Lift on hover is a border shift (`subtle` → `strong`), never a shadow.
  Selected is fill + accent ring, never elevation. The dial's current level
  and the sigil are the only accent *fills* outside selected states — both
  mean "this is live".

## Shapes

Radii unchanged: `{radius.sm}` 6px controls and inputs, `{radius.md}` 8px
cards, sheets, and Tonight blocks, `{radius.lg}` 12px for large transient
surfaces (command palette, confirm dialog) [ASSUMPTION]. `{radius.full}`
9999px pills are reserved for chips (tag, state, knowledge, generate, cast),
the sigil, the dial track, and the mode switch — the app's "rounded token"
objects. Nothing between md and full; sharp corners stay for wells.

## Components

Behavioral rules live in `EXPERIENCE.md`; this section is visual anatomy.
Carried components keep their 09-21 anatomy; v3 additions and changes are
marked.

### Shell & global

- **App shell (`{components.app-shell}`)** — sidebar + full-width content.
  Sidebar: brand (bold, primary), campaign switcher with
  `{components.archive-group}` at its foot, nav Create / World / Relations /
  Library (`{components.sidebar-item}`, active =
  `{components.sidebar-item-active}` with a 2px accent left bar), account +
  jobs pinned bottom. Inline SVG, 16px stroke, currentColor — zero external
  assets.
- **Sigil (`{components.sigil}`)** — the campaign's wax-seal mark: accent
  ring on accent-ghost bed, `{radius.full}`. One per campaign, shown in the
  switcher and `{components.tonight-banner}`. Gothic accent, never chrome.
- **Kind tabs (`{components.kind-tabs}`)** — the World roster's horizontal
  tab set, one tab per registry kind + the note slot: `character / place /
  faction / notes`. Registry-driven (only kinds with rows render);
  active tab = `{components.sidebar-item-active}` anatomy (selected fill +
  accent ring), inactive secondary; whole tab keyboard-activatable, arrow-
  key traversal. Empty kinds show no tab [ASSUMPTION - tab treatment,
  see EXPERIENCE.md Open Questions].
- **Tag chip (`{components.tag-chip}`)** and **state chip
  (`{components.state-chip}`)** are token-only primitives: anatomy lives
  in their host components (cast strip, archetype card, hook row,
  generate chips); tags rest on `{color.accent.secondary}` fills with
  base-ink labels, state chips on `{color.surface.overlay}` fills with
  secondary text; terminal state text takes status color. Chips that
  act (add/remove tags, tick companions) are keyboard-activatable with
  ≥ 44px targets; purely decorative chips are `aria-hidden`.
- **Command palette (`{components.command-palette}`)** — global ⌘K:
  overlay surface, strong border, popover elevation; input on top, results
  and commands beneath; active row = selected fill + `{color.text.primary}`
  label (accent ring marks selection) [ASSUMPTION]. Density on demand.
- **Entity picker (`{components.entity-picker}`)** — embedded typeahead
  (the palette's engine, compact): base input, results as an overlay list.
  Replaces every 108-target plain select and relation-target picker.

### One Create

- **Mode switch (`{components.mode-switch}`)** — sunken pill track, now
  THREE halves: Capture / Author / Generate. Active half =
  `{components.mode-switch-active}` (selected fill + primary text + 2px
  accent underline); inactive halves secondary text. One section in nav;
  routes live at `/create/capture|author|generate`.
- **Stepper (`{components.stepper-active}` / `{components.stepper-done}`)**
  — one horizontal stepper under the mode switch, shared by all three
  modes; per-kind step maps. Current step accent-filled dot + primary
  label; done accent-ring dot + check; upcoming subtle-border dot.
  Connector hairlines in `{color.border.subtle}`. Below `560px` collapses
  to "Step N of M — {name}" (matches the `< 560px` responsive tier).
- **Card-select grid (`{components.card-select-active}`)** — 2-up grid of
  `{radius.sm}` options; title medium + secondary small description; hover
  border `strong`; selected fill + 2px accent ring, title stays
  `{color.text.primary}` (accent is the ring, never the label). Carried
  for closed picks (environment, economy, power base…) *and* new for
  `{components.archetype-card}`.
- **Archetype card (`{components.archetype-card}`)** — a card-select option
  in the walk's Concept step: archetype name, one-line character, seed tags
  as `{components.tag-chip}`s, default dial marker. One card per registry
  archetype (10 place, 8 faction); selected = `{components.card-select-active}`.
  Scale is encoded in the archetype (Village vs City vs Fort) — no size
  slider, no population field.
- **Segmented row (`{components.segmented-row}` / `{components.segmented-cell-active}`)**
  — carried for single-pick vocabs (role `NPC`/`BBEG`/`Monster`, level-vs-CR,
  relation existing/new, economy detail) and the walk's Simple/Advanced
  level toggle.
- **Authored sections (`{components.authored-section}`)** — carried:
  label + input per record group; filled fields render with the
  `{components.authored-filled-marker}` (yours, verbatim), blanks with the
  `{components.model-will-write-marker}` (muted, "model will write").
  Place/faction walks use the same blocks for their registry-defined
  sections. The distinction is the contract and MUST be visible before
  submit.
- **Stat subsection (`{components.stat-subsection}`)** — carried toggle-card
  + detail panel (attributes, combat, skills, actions, traits, spells,
  boss non-NPC). Character-only: the dial weights these (`nothing` = no
  stat block; `pillar` = full block + boss + subsections).
- **Relation-draft row (`{components.relation-draft-row}`)** — carried mono
  line `type / mode / target / counter`; the type control now *filters by
  source kind* (per-kind edge availability from the registry) — a place
  cannot pick `member_of`/`control`/`employ`; `located_in` only points at
  places; `part_of` is the one new edge type (no `based_in` alias).
- **Generate chip (`{components.generate-chip}`)** — "what will be
  generated": selectable pill chips bound to the `{components.dial}`.
  Resting accent-ghost fill, primary text; active (will-generate) chip adds
  the accent ring + keeps `{color.text.primary}` labels [ASSUMPTION - active
  treatment]. Chips are the dial's promise made visible; disabled chips
  (below the dial floor) render in `{color.text.disabled}` — and never
  carry load-bearing meaning as color alone (the disabled label states
  "above {level}" in text).
- **Dial (`{components.dial}`)** — the generation-intensity control: sunken
  pill track, five levels `nothing / draft / simple / important / pillar`
  mirrored as a stepped row with dot markers; current level accent-filled,
  labels secondary; value label above in small text [ASSUMPTION - marker
  geometry]. Lives in the record (survives export and re-rolls); per-kind
  semantics: for places/factions every level carries the full per-kind
  section set (elaboration differs), for characters the dial is mechanics
  weight only.
- **Dial row (`{components.dial-row}`)** — the universal standard row of
  three dials (Importance / Stance / Complexity) present on every entity,
  introduced once as a pattern — never bolted per-kind. Level sets are
  open [ASSUMPTION].
- **Preview card (`{components.preview-card}`)** — the rail's live preview,
  now for *every* entity type (E.5): image slot + serif display name, kind
  badge + attribute chips (`{components.tag-chip}`), description text.
  The image slot NEVER auto-generates: resting state is a basic inline-SVG
  placeholder (kind-aware geometric silhouette, zero external assets);
  the DM may click it to generate a portrait, mirroring the explicit-click
  "Generate portrait" discipline elsewhere (owner 09-24 — no implicit
  image cost, no draft-image retention). Shows the entity's existing
  portrait when one is attached. Label line "This is a preview based on
  your inputs." Updates live as the walk fills.
- **Review rail (`{components.review-rail}`)** — carried: sticky
  `{radius.md}` panel speaking one language in all modes — Author/Capture:
  authored-section mirror + relation lines + commit; Generate: ask context
  + selected-candidate summary + queue truth; walks: `{components.preview-card}`
  + generate-chip summary + commit. Mirrors, never gates; never
  cross-writes. After commit, a **suggested next-steps checklist**
  (secondary text rows, "what's next" for what was just built —
  kind + dial + result derived, one link each) appears at the rail foot;
  rows never promise a non-feature.
- **Ask box (`{components.ask-box}`)** — carried textarea + Ask action;
  the box now doubles as the Night-thread composer when on the Tonight
  landing (same component, scene context above it).
- **Guide box (`{components.guide-box}`)** — textarea on guided regenerate
  / enrich: "say what changed or what you want more of". The text rides the
  regenerate request with the dial level; preserved on failure. Sits under
  the re-roll/enrich control, above the feeds.
- **Candidate card (`{components.candidate-card}`)** — carried (name header,
  sections, stat display, mono relation lines, four equal actions). v3:
  renders for every kind with its registry sections; gains the
  `{components.reroll-scope-label}` (carried) plus a guide-box and dial on
  re-roll.
- **Job-feed row (`{components.job-feed-row}`)** — carried queue truth
  (`Queued (position N)` / running / built / failed, mono error verbatim).
- **Conflict dialog (`{components.conflict-dialog}`)**, **stale-edit notice
  (`{components.stale-edit-notice}`)**, **error envelope
  (`{components.error-envelope}`)** — carried unchanged.
- **Enrich nudge (`{components.enrich-nudge}`)** — the quiet inline
  affordance on a section-slot whose record is physically flat: secondary
  small text "Enrich this one." (ghost button). The nudge is the feature —
  flat records stay physically flat until the DM acts.

### Tonight / run loop

- **Tonight sheet (`{components.tonight-sheet}`)** — the campaign landing:
  the `{components.tonight-banner}` plus its blocks on the app canvas.
  One screen: active scene + cast + open hooks + recent changes +
  could-go-wrong; printable. No store writes.
- **Tonight banner (`{components.tonight-banner}`)** — raised strip at the
  top: sigil, serif campaign name, scene line, and the "Take it back" entry
  (the rewind-the-scene power, relabeled undo).
- **Cast strip (`{components.cast-strip}`)** — horizontal row of
  `{components.tag-chip}` cast members (session-scoped, removable, "add"
  opens the entity picker). Local to the night; per-campaign.
- **Hook row (`{components.hook-row}`)** — one open-hook line from the
  entity's `party_hook` / `secret` / `rumor` fields, with its
  `{components.knowledge-chip}`.
- **Change line (`{components.change-line}`)** — one recent-changes line
  (revision meta + event summary) from `GET /api/campaigns/{id}/revisions`;
  mono revision id + small secondary text.
- **Note block (`{components.note-block}`)** — the could-go-wrong free text
  (local, authored-section anatomy).
- **Thread line (`{components.thread-line}`)** — one ask/answer pair in the
  scene-scoped Night thread: question primary, answer secondary small,
  suggest-accept action. The thread is session-lifetime.
- **Knowledge chip (`{components.knowledge-chip}`)** — party-knowledge
  3-state marker on secret/rumor/hook: `hidden / shown-to-me / known-by-party`,
  overlay fill + secondary text; state text takes status color only on
  terminal states [ASSUMPTION]. Carries the reveal action.
- **Verb row (`{components.verb-row}`)** — consequence quick-actions on
  entity state (mark defeated / flip allegiance / resolve thread / spend
  item): quiet buttons in a hairline row, each committing one undoable
  revision. State lives in the parallel session table, never in the record.
- **Ego graph (`{components.ego-graph}`)** — the default 1–2 hop local
  graph on every rich page: sunken canvas, hairline border, focus entity
  centered, accent nodes [ASSUMPTION - node colors]; "Open full map" → the
  Relations surface. The 108-node embed is gone.
- **Export menu (`{components.export-menu}`)** — one trigger
  (`{components.button-quiet}`) + overlay menu listing the export formats
  formerly five links. On rich pages and roster rows.
- **Danger zone (`{components.danger-zone}`)** — page-foot section for
  delete: raised surface, error hairline, secondary text, delete =
  `{components.button-danger-ghost}` opening a **confirm dialog
  (`{components.confirm-dialog}`)** — overlay panel, lg radius, named
  target, Confirm / Cancel; never an on-card delete.
- **Archive group (`{components.archive-group}`)** — the switcher's
  archived-campaigns group label; archived entries render muted, out of the
  active list, still exportable/restorable.

## Do's and Don'ts

| Do | Don't |
|---|---|
| Terracotta will-fire (`{color.accent.primary}`) + Brass tags/pills/highlights only (`{color.accent.secondary}`) | A third chromatic accent; retired `#2f6feb` in new code |
| Tonal hierarchy base → raised → overlay, hairline borders | Shadows for resting hierarchy; gradients anywhere |
| Serif for entity / kind / campaign display names only | Serif body, buttons, or labels |
| Mono for machine output (job errors, IDs, dice, relation lines, revision ids) | Mono prose or UI labels |
| Dial carries the FULL per-kind section set at every level; blank = model will write | Dial levels that strip sections or re-introduce flat "nothing" rows |
| Tonight as a calm, printable index card; ritual top-to-bottom | Dashboard chrome on the landing; celebratory copy |
| Registry-driven walks: archetype cards, generate chips, preview rail for every kind | Per-kind bolt-ons; a size slider or population field (archetype encodes scale) |
| One Export menu; delete in a danger zone with a real confirm | Five export links; delete on cards |
| Ego-graph local (1–2 hops) + open full map | 108-node embeds on entity pages |
| `part_of` added; `located_in`/`bases_at` carry containment | A `based_in` alias — one name per relation |
| Structured edit everywhere; a DM never sees a brace | Raw JSON (world_integration / Additional data) in edit sheets |
| Inline links carry a resting underline (non-color cue) | Underline-less colored links |
| Inputs use `{color.border.input}` (≥ 3:1 non-text boundary) | `border.subtle` inputs |
| Searchable pickers and ⌘K for every 108-wall | Plain 108-target selects |
| Mint no tokens for timeline / DM-notes / token-preview / VTT export / credits / light theme | Non-feature chrome anywhere |
| Inline SVG iconography, currentColor, zero external assets | Icon fonts, image sprites, remote assets |

## Inspiration & References

- **Lore-Weaver "Create City" wizard (`imports/lore-weaver-create-city-2026-09-24.md`)** —
  the owner's reference image, imported 2026-09-24. It illustrates the v3
  walk anatomy this spine adopts: concept type cards, environment cards,
  tag input, theme/uniqueness counters, "what will be generated" chips
  bound to a depth control, a live-preview rail, and Simple/Advanced walk
  levels. The import's lifted/rejected itemization is the contract for what
  is carried (see `Inspiration & Anti-patterns` in `EXPERIENCE.md` for the
  behavioral reading); its rejected items — credits, a "Quick Create"
  escape hatch, PRO badge chrome — are not mythosCircle features and get no
  design.
- **Archetype templates (`.working/archetype-templates.md`)** — the 10 place
  + 8 faction templates this spine's archetype cards render; bones per the
  trimmed contract (no size/population; Economy carries Exports & Imports).
- The 09-21 mockups remain *shape* references only; their IA is superseded
  by this v3 spine (shape-only, per §Shape-fit): `mockups/create-v2.html`
  (Generate mid-review rail) and `mockups/shell-v2.html` (Author mid-fill
  + sidebar shell) in the 09-21 workspace — superseded artifacts kept for
  history; round-1 `forge-wizard.html` / `accept-screen.html` are the
  round-2 geometry this spine explicitly rejects.