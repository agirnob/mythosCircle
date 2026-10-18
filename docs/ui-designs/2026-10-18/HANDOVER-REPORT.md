# mythosCircle UX — Handover Report (prepared 2026-09-25)

Prepared by Sally (UX Designer) after the 2026-09-23/24 v3 remake sessions.
Purpose: a self-contained briefing so a fresh session can continue the
conversation without reading the whole history. Everything here is grounded
in the artifacts listed at the end — quote paths when you need detail.

---

## 1. What this project is

mythosCircle is a **local, privacy-first TTRPG world/NPC generator for DMs**.
Stack: Python 3.12 + FastAPI + SQLAlchemy 2 + SQLite (WAL) backend; Vue 3.5 +
Vite + TypeScript + Pinia frontend; local llama.cpp LLM (gemma-4-26B-A4B on
127.0.0.1:8889). The DM keeps the wheel — the tool generates *drafts* the DM
accepts; your written words always commit verbatim; the world is immutable
(event-sourced revisions, single commit path, undo).

Slogan (v3): **"prep tonight, run the table, keep one living world."**

## 2. Session status — where we are

- **2026-09-21**: a UX run produced `LANGUAGE.md` (the design LAW, from the
  owner's own plain-feeling words) + `DESIGN.md`/`EXPERIENCE.md` spines
  (redesign v2 "unified Create"), both `status: final`.
- **2026-09-23/24**: the owner ordered a **v3 FULL UX remake** — the current
  session. All decisions live in a NEW workspace:
  `_bmad-output/planning-artifacts/ux-designs/ux-mythosCircle-2026-09-23/`
- **The v3 spines (DESIGN.md + EXPERIENCE.md) are `status: draft`** — all
  decisions captured, both reviewers run (rubric + accessibility), all
  findings fixed and verified. **They have NOT been flipped to `final`** —
  that was pending the owner's final design confirmation and two small
  cleanups (see §9).
- The uncommitted frontend rebuild in the repo (AppShell, NewWorldView,
  NewEntityView, NewAskView, ui/ + styles/) is the owner's earlier Stage-2/4
  work — related but separate from the v3 spine; v3 supersedes its IA.

## 3. The design law (LANGUAGE.md) — the theme in the owner's words

Elicited from the owner 2026-09-21. **Everything bows to this page.**

- **Voice: "a calm chamberlain, not a carnival barker."** Short complete
  sentences. Names reasons and next actions. Never celebrates, never
  exclaims, never peppers the DM with tips. Exclamation marks are banned
  in UI copy.
- **Theme: "a modern dark app wearing a few old things"** — gothic accents
  on clean dark:
  - serif display names,
  - a wax-seal-style sigil per campaign,
  - thin rules instead of boxes,
  - ONE arcane accent color for *active / will-fire*.
  - No parchment fills, no candlelight glows, no ornamental borders.
  - Mood comes from portraits and words, never chrome.
  - **Light theme is not a thing.** Dark-first single theme, dual-theme
    deferred.
- **Doctrine 1 — never crowded.** One decision per glance; sections below
  the essentials collapse; collapse state remembered per user.
- **Doctrine 2 — each kind opens its own way.** A place reads like a place,
  a character like a character. No universal template with different labels.
- **Doctrine 3 — character hero.** Playable truth first: voice, secret,
  hook hit first; portrait and name never outrank what the DM needs to play.
- **Doctrine 5 — creation is a walk, not a box or a blank sheet.**
  Guided steps with a live preview growing alongside.
- **Doctrine 6 — fire and wander.** Local builds take minutes; waiting
  never blocks; honest state (queued/running/done/failed) where the DM
  already looks; no progress theater.
- **Doctrine 7 — editing flips the page.** One Edit control turns the whole
  view into its editable self; save/cancel ends it. No inline field-fiddling,
  no side drawers, no raw JSON.
- **Doctrine 8 — full-width shell with sidebar.** Centered narrow columns
  are forbidden — dead margins are a defect.
- **Owner rule (2026-09-24): "images are intent, not inventory."**
  Price reuse/extend against the memlog and `frontend/src`, never against
  mock images; a control in a picture but not in code is BUILD.

## 4. Colors (the exact tokens — from DESIGN.md frontmatter)

Dark-first; tonal hierarchy via fill steps, shadow last.

| Token | Value | Use |
|---|---|---|
| `color.bg.base` | `#12141b` | app canvas, Tonight sheet resting surface. Never cards |
| `color.bg.sunken` | `#0d0f14` | wells: dial track, mode-switch track, code/job dumps, ego-graph canvas, ask/guide textarea beds |
| `color.surface.raised` | `#1b1e27` | cards, Tonight blocks |
| `color.surface.overlay` | `#232736` | floating layers: palette, picker results, export menu, dialogs |
| `color.surface.selected` | `#262b3d` | chosen card-select/archetype, active segment/sidebar/tab/mode-switch half. Never resting |
| `color.border.subtle` | `#2c3038` | 1px separators everywhere |
| `color.border.strong` | `#3a3f4b` | hover/focus-adjacent edges, palette border |
| `color.border.input` | `#606879` | input boundaries (≥3:1 non-text, a11y fix) |
| `color.text.primary` | `#e8e6e3` | body text |
| `color.text.secondary` | `#9aa0a6` | muted/help/queue labels, knowledge states |
| `color.text.disabled` | `#858585` | disabled controls (raised to AA, a11y fix) |
| `color.text.link` | `#8ab4ff` | inline text links only — never buttons/nav; resting underline |
| `color.accent.primary` | `#6d5ef0` | **the single chromatic voice — "will-fire" only**: primary buttons, stepper current step, dial current level, selected rings, focus rings, active generate chips, Tonight "Go", sigil ring |
| `color.accent.primary-hover` | `#5f56d6` | hover (DARKENED for AA, a11y fix) |
| `color.accent.on-primary` | `#ffffff` | text on accent |
| `color.accent.ghost` | `rgba(109,94,240,0.14)` | chip fills, sigil bed |
| `color.status.error` | `#ff7b72` | text/hairline only, unified; danger-zone hairline |
| `color.status.success` | `#7bd88f` | text/hairline only |
| `color.status.warning` | `#f5b54a` | text/hairline only; conflict dialog border |
| `color.status.info` | `#8ab4ff` | text/hairline only |

**Contrast targets (committed):** body text ≥4.5:1 on every resting surface;
accent is a marker, never a body-text fill — active labels render
`text.primary` (≥11:1); white on accent ≥4.5:1 at rest AND hover; focus
rings ≥3:1. WCAG 2.2 AA. (Reviewers verified ratios; see §8.)

**Avoid:** the retired `#2f6feb` blue; gradients; a second chromatic accent;
colored fills behind body text; light-theme tokens.

## 5. Typography & shapes

- **Display (Georgia serif)** — ONLY entity, kind, and campaign display
  names (Tonight banner name, roster rows, preview/candidate headers,
  rich-page headers, 32px→28px). Nothing else.
- **Body (system-ui)** — everything else; 16px base; 14px small; 12px caption
  (all-caps eyebrows, 0.08em tracking); 11px micro (rail eyebrows, timestamps).
- **Title** 22px for surface titles ("Create", "Tonight", "World").
- **Mono** — machine output ONLY: job error payloads, IDs, dice strings,
  relation lines, revision ids. Never prose, never labels.
- Weights: 400 default; 500 card-select/archetype titles; 600 sidebar +
  candidate names; 700 brand.
- **Radii:** 6px sm (controls/inputs), 8px md (cards/sheets/Tonight blocks),
  12px lg (large transient surfaces), 9999px full (chips, sigil, dial track,
  mode switch — the pill is the app's "rounded token"). Nothing between md
  and full; sharp corners for wells.
- **Elevation:** tonal (base→raised→overlay), no resting shadows;
  `overlay` popover shadow on floating layers only.

## 6. Layout strategy

- **Shell:** sidebar 232px + content max 1280px (16px sides). Sidebar:
  brand → campaign switcher → nav **Create / World / Relations / Library** →
  archive group → account/jobs pinned bottom. Sidebar collapses to an icon
  rail under 900px.
- **Campaign landing = the Tonight prep sheet** (owner verdict) — the
  session-start ritual opens when the world opens; the at-a-glance synthesis
  lives there too. Single-column at every width: **"it is a sheet, not a
  dashboard."**
- **Create:** three routes `/create/capture|author|generate`, ONE stepper +
  ONE 340px review rail shared across modes; form column + rail ≥1020px,
  stacks under. Mode switches never reflow the shell.
- **World = a calm roster**, kind tabs + searchable index into rich pages —
  NOT the 108-card grid (density on demand via ⌘K).
- **Rich pages** (every kind): character-page pattern — tabs/accordions,
  ego-graph (1–2 hops), one export menu, danger zone at foot.
- Breakpoints: ≥1020 form+rail / 900–1019 icon rail / 560–899 single-column /
  <560 stepper collapses to "Step N of M".

## 7. The workflows — what, why, how they fit

### 7.1 One Create (the walk) — Capture / Author / Generate
**Why:** three doors today (forge, guided build, add-character) are the same
behavior with different chrome; the 24-field auth wall and the buried forge
are the failures. **How:** one stepper + one rail; URL encodes the mode.

- **Capture (front door):** one intent line ("Sarella Voss — tavern keeper
  who owes the Ash Cult") → parses name+role into a live preview card →
  hands off into the Author sheet prefilled. **Never bypasses to a bare
  queue** (that resurrects the regenerate gamble). OPEN: parse mechanism
  (client-side vs LLM-assisted).
- **Author:** the authored-figure hybrid path widened to every kind — DM
  fills what they know, blanks are the model's ("blank = model will write"
  is visible before submit). Zero-LLM commit only when every section is
  authored. Simple Mode = Concept+Review; Advanced = all four steps.
  Templates (C.6) live inside the walk.
- **Generate:** the existing candidates machinery, reframed as "consult the
  world at the table" + guided regenerate (guide text + dial).

### 7.2 The registry (keystone) — kinds, archetypes, dials, edges
**Why:** per-kind behavior as content, not code; personhood for places and
factions. **How:** one backend-served read-only endpoint
(`GET /api/campaigns/kinds`, themes-seed pattern). Holds:
- **3 kinds + one note slot** (character / place / faction / notes) —
  item/event exist as data only, grow citizenship on demand.
- **Per-kind depth tables:** dial level → section set + generation
  instructions.
- **Archetypes** (10 place / 8 faction — owner approved):
  place bones = Environment, Climate, Economy (+ optional Exports & Imports
  tags), Key Characteristics, theme ≤300, uniqueness ≤200; **no size slider,
  no population field** (archetype encodes scale). Faction bones = Scope,
  Wealth, Power base, Leadership style, Secrecy, tags, doctrine, unique.
- **Offered companions** (softened "always-generates"): a city *proposes*
  Mayor/Watch captain/Market master as tick-to-include — nothing generates
  unless the DM selects it (owner: "forcing a mayor is wrong, the DM chooses
  everything").
- **Per-kind edge availability:** 16-type closed vocabulary; `part_of` is
  the ONE new edge type (no `based_in` alias — one relation, one name);
  places get 8 of 16 types (can't member_of/control/employ), located_in
  only toward places. Pickers filter by source kind, mirroring
  `EDGE_KIND_RULES`.

### 7.3 The generation dial (nothing → draft → simple → important → pillar)
**Owner-corrected semantics:** EVERY dial level carries the FULL per-kind
section set — places always have description/inhabitants/what's-hidden/
relations as record sections; **any blank section is LLM-filled exactly like
an authored figure.** The levels differ only in elaboration (how much care
each section gets). For characters the dial = **mechanics weight only**
(narrative always complete; nothing = no stat block, pillar = full + boss).
Dial lives in the record (survives export + re-rolls). `nothing` + all
sections authored = the zero-LLM commit. E.4's "what will be generated"
chips bind to the dial.

### 7.4 Guided regenerate + enrichment
**Why:** bare re-rolls are a gamble — DM clicks, gets something, no input,
try-again-and-again (owner insight). **Fix:** a guide box ("say what changed
or what you want more of") + the dial ride the regenerate request. **Enrich
= regenerate with per-kind sections + sections:null + guide + dial** — ONE
engine, no new job kind; candidates/accept/conflict machinery reused.
Flat old entities stay flat until the DM acts (lazy render) — the quiet
"Enrich this one." nudge IS the feature.

### 7.5 The Run loop
- **Tier 1 — Tonight (UI-only, ships first):** session-start ritual
  (recap → what-they-know → open hooks → pick scene → go), cast strip
  (local, session-scoped, localStorage), open hooks (reads existing
  WorldExport), recent changes (new read-only
  `GET /api/campaigns/{id}/revisions?limit=N`), could-go-wrong (local),
  printable. NO store writes. **"Tonight IS the campaign landing."**
- **Night ask thread (scene-scoped):** second ask inherits the first;
  accepted answers append to context; thread dies with the night ("This
  answer stays tonight."). Bounded retrieval (AR6) untouched.
- **Tier 2 (two standalone store sub-items, M each, only after Tier 1
  validates):**
  - T2a **consequence verbs** — mark defeated / flip allegiance / resolve
    thread / spend item; one undoable revision each; undo relabeled
    **"Take it back"** (media never restored — boundary named).
  - T2b **party knowledge 3-state** — hidden / shown-to-me / known-by-party
    per secret/rumor/party_hook + reveal-X verb.
  - Tier 2 state lives in **PARALLEL tables** (`entity_session_state`,
    `entity_knowledge_state`) with their own revision event types — the
    world record stays pure, exports stay truth-only (Q3 verdict).
- **Safe switcher:** switching worlds preserves in-flight jobs, open asks,
  drafts — never enqueues, never discards, re-scopes per the Generic gate.
- **Archive:** dead worlds between "in the list" and "danger delete" —
  muted in the switcher, exportable/restorable, delete only via danger
  zone confirm.
- **Sandbox/what-if** (test against a throwaway copy via Generic library):
  optional stretch, not committed.

### 7.6 New backend surface — the COMPLETE list (store contract discipline)
1. `GET /api/campaigns/{id}/revisions?limit=N` (read-only, Tier 1)
2. `GET /api/campaigns/kinds` (read-only registry)
3. generation `dial` as a top-level record key (`DIRECT_KEYS` +1) +
   `archetype` field on place/faction
4. one new edge type `part_of` + per-kind edge availability served by
   the registry
5. Tier 2 parallel tables + their revision event types + undo learning both.

Everything else reuses existing forge/build-in/add-character/candidates
contracts. **That list is the handoff contract for the architecture pass.**

## 8. Review round (2026-09-24) — both lenses run, all fixed

- **Rubric** (mechanical + judgment): overall **strong**; 0 critical, 0 high,
  4 medium, 8 low. Fixed: contrast targets committed in §Colors; kind-tabs
  component rows + OQ entry; stepper collapse 640↔560 contradiction resolved
  (560); `sources` + `spec` frontmatter added; empty states (empty roster/
  relations/library); flow-coverage lows folded in (template start in Flow 3,
  ego-graph + danger-zone in Flow 4); "dime dials" typo.
- **Accessibility**: overall **sound — approve with token-level fixes**;
  all failures were in the color-token contract, now fixed and verified:
  accent-as-text → markers only (active labels `text.primary`), hover
  darkened `#7f73f5→#5f56d6`, disabled `#5e636e→#858585`, new
  `border.input` `#606879` (≥3:1 non-text), resting underline on links.
- Full contrast tables: `review-accessibility.md`, `review-rubric.md`.

## 9. Open questions (mirrored in EXPERIENCE.md §Open Questions)

1. Capture parse mechanism — client-side vs LLM-assisted extraction.
2. Character walk step map after the world-step retirement.
3. Importance / Stance / Complexity level sets for the 3-dial row (C.1
   names the dials, not their scales).
4. Night-thread transport — client-side thread context (assumed) vs
   server-side session tables (Tier 2 option).
5. Tonight landing geometry — single ritual column (assumed) vs a
   wide-screen thread side column.
6. `known-by-party` path (only reveal verb, or derived from accepted
   thread answers) — decided at Tier 2 design time.
7. Ego-graph non-visual fallback form (edge list vs table — assumed list).
8. Sandbox/what-if — stretch, not committed.
9. Tier 2 ships after Tier 1 validates (sequencing gate, not design).
10. Kind-tabs treatment — active-tab anatomy + empty-kind rendering
    (registry-driven tabs decided; selected state open).

**Plus the pre-finalize cleanups left for the owner:**
- Faction **Scope slider** in `archetype-templates.md` vs the "no size
  slider" sentence — scope the sentence to places or drop the slider.
- **Design confirmation**: the owner asked "did we discuss the design?" —
  the v3 conversation inherited the 09-21 design law; the owner should
  confirm the look (colors/typography/layout) or redirect BEFORE `final`.
- Then: flip both spines `status: final` and hand to `bmad-architecture`
  (the Run-loop + registry + guided-regenerate surface is the target).

## 10. Key decisions with reasoning (quick reference)

| Decision | Choice | Reasoning |
|---|---|---|
| Shell nav | Create / World / Relations / Library | F.6 resolves World-vs-Overview redundancy into 3 lenses; Tonight is the landing |
| One Create | 3 routes, one stepper+rail | Deep-linkable modes; Q1=C; Capture seeds Author (Q2=A) |
| Dial home | in the record | survives export + re-rolls; verbs/knowledge in parallel tables (Q3=C) |
| Dial semantics | full section set every level; blank=LLM fills | never incomplete records; `nothing` ≠ empty |
| Character dial | mechanics weight only | narrative always complete (hero doctrine, Q4=B) |
| Edge vocab | +part_of only, no based_in | one relation, one name; C.4 |
| Regenerate | guide text + dial on one engine; enrich = regenerate | kills the gamble; no new job kind |
| Preview image | SVG placeholder, explicit-click only | no implicit image cost/retention (P2) |
| Flat entities | lazy render + enrich nudge | no migration (Q5=C); nudge IS the feature |
| Ask thread | scene-scoped, dies with the night | bounded retrieval untouched (Q6=A) |
| Next steps | review-rail checklist after commit | adopted from Lore-Weaver (P1) |

## 11. Artifact index (all in the 2026-09-23 workspace)

- **DESIGN.md** — visual identity spine (tokens + body), `status: draft`
- **EXPERIENCE.md** — behavioral spine (IA, voice, components, states,
  flows), `status: draft`
- **.memlog.md** — 79 entries, the canonical record of every decision
- **.working/archetype-templates.md** — 10 place + 8 faction templates
- **imports/lore-weaver-create-city-2026-09-24.md** — the owner's reference,
  lifted/rejected itemized
- **reconcile-lore-weaver.md** — 13 lifted / 6 rejected / 0 pending
- **review-rubric.md**, **review-accessibility.md** — review findings
- **Law:** `../ux-mythosCircle-2026-09-21-r2/LANGUAGE.md` (design law)
- **Superseded (context only):** `../ux-mythosCircle-2026-09-21/` spines v2

Suggested next step for the new session: read `.memlog.md` + both spines,
then settle §9's cleanups and flip to `final`, then
`bmad-architecture` for the Run loop + registry + guided-regenerate surface.