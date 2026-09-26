---
status: 'final'
updated: '2026-09-25'
topic: 'mythosCircle v3 full UX remake'
sources:
  - .memlog.md
  - LANGUAGE.md
  - imports/lore-weaver-create-city-2026-09-24.md
  - ../ux-mythosCircle-2026-09-21/DESIGN.md
  - ../ux-mythosCircle-2026-09-21/EXPERIENCE.md
---

# EXPERIENCE.md — mythosCircle v3 full UX remake

> Behavior, IA, states, interactions, accessibility, journeys.
> Visual identity lives in `DESIGN.md`; tokens referenced here as `{group.name}`.
> Spines win on conflict with any mock, wireframe, or import.
>
> Redesign v3 supersedes the 2026-09-21 unified-Create spine: scope =
> ADD (run loop, personhood, universal authoring) + CHANGE (creation
> experience, calm/law compliance). Every concrete decision below is from the
> 09-23/09-24 memlog; where the memlog leaves a decision open it is tagged
> `[ASSUMPTION]` and carried to Open Questions at the end.

## Foundation

Web-desktop tool, Vue 3 + TS + Pinia, dark-first single theme (dual-theme
deferred). No framework swap, no new dependencies for looks. The operator is
also the dogfood user — structured reasons stay visible (BAD_EDGE,
stat-repair refs, revision ids) verbatim, written in the calm chamberlain
voice: say what broke and what to do, in one line.

**Store contract discipline.** v3 runs the same store contracts as today's
forge / build-in / add-character / candidates — no invented backend
behavior. The complete list of new backend surface is:
(1) `GET /api/campaigns/{id}/revisions?limit=N` — read-only revision meta +
event summaries for the Tonight "recent changes" block (shipped with
Tier 1); (2) `GET /api/campaigns/kinds` — read-only closed kind registry
(themes-seed pattern, fetched on mount, cacheable); (3) the generation
`dial` as a top-level record key (`DIRECT_KEYS` +1 — survives export and
re-rolls) plus an `archetype` field on place/faction; (4) one new edge
type `part_of` and per-kind edge *availability* served by the registry;
(5) Tier 2 parallel session tables (`entity_session_state`,
`entity_knowledge_state`) with their own revision event types, undo
learning both. WorldExport keeps carrying full records (the Tonight hooks
block reads it, no new fetch); regeneration keeps its candidates
machinery — the DM guide text and dial level ride the regenerate request.
Anything else reuses existing surfaces as-is.

Non-features carried from the law: timeline, DM notes, token preview, VTT
export buttons, credits/cost, light theme.

## Information Architecture

The v3 shell is one sidebar: campaign switcher on top, then Create / World /
Relations / Library (F.6). The World-vs-Overview redundancy is resolved into
three distinct lenses: **the campaign landing is the Tonight prep sheet**
(owner verdict) — the session-start ritual opens when the world opens, and
the at-a-glance synthesis (the "Overview" role) lives there too; **World**
is the edit roster; **Relations** is the graph. One Create section in nav;
three mode routes beneath it.

| Surface | Reached from | Purpose |
|---|---|---|
| Tonight (campaign landing) | Open a world / campaign switcher | Session-start ritual (recap → what-they-know → open hooks → pick scene → go), prep sheet (cast, hooks, recent changes, could-go-wrong), at-a-glance synthesis, the scene-scoped ask thread. Printable. |
| Create — One Create | Sidebar Create → `/create/capture` · `/create/author` · `/create/generate` | Three modes, one stepper + one rail. Capture seeds Author; Author commits (hybrid, any kind); Generate stages candidates. Deep-linkable; URL encodes the mode. |
| World | Sidebar World | Registry-driven edit roster: `{components.kind-tabs}` (character / place / faction + note slot), searchable index into rich pages; flat rows carry the enrich nudge. |
| Relations | Sidebar Relations | Committed edge graph read; per-kind edge availability governs what can be drawn next (adds happen through pickers elsewhere, mirroring today). |
| Library | Sidebar Library / Generic entry | Theme-seeded drafts isolated from every world; sandbox copy source for what-if. |
| Archive | Campaign switcher foot | Finished campaigns, out of the active list, still exportable/restorable; not danger-delete yet. |
| Account / Jobs (existing) | Sidebar footer | Account, queue visibility. |

### The shell and the safe switcher

The campaign switcher selects the working context for every surface: a
non-generic campaign (bound work) or the Generic library (isolated drafts).
Switching never enqueues and never discards — in-flight jobs, an open ask,
and unfinished drafts survive the switch, re-scoped where the contracts
allow (relation drafts require a bound world; the Generic gate hides the
relation block). World-scoped state (scene, cast) is session-scoped
selection, NOT world state, until real sessions validate (RUN-LOOP STATE
position). The switcher lists active campaigns, Generic, and an
`{components.archive-group}` of archived dead worlds — between "in the
list" and "danger delete", still exportable/restorable (reuses the
Generic/move machinery).

### One Create — three routes, one stepper, one rail

The three existing doors ARE the three modes; the modes just unify them.
No picker ceremony: the switcher owns the world-vs-Generic context, so no
Author step-1 world picker exists.

- **Capture (`/create/capture`, front door)** — one intent line. The rail
  parses name + role into a `{components.preview-card}` live preview; one
  action hands off into the Author sheet with name + role prefilled
  [ASSUMPTION — parse mechanism open, see Open Questions]. From there:
  blanks fill-or-generate, one click from submission, and a complete record
  is guaranteed on submit (the authored-figure blank = generated machinery).
  Capture never bypasses to a bare queue — no gamble resurrection.
- **Author (`/create/author`)** — the authored-figure hybrid path widened to
  every kind (B.4): the DM fills what they know, blanks are the model's.
  Zero-LLM commit happens only when the DM fills every section. Per-kind
  walks share one stepper: character `Figure → Mechanics → Review`
  [ASSUMPTION — exact character step map after the world-step retirement],
  place/faction `Concept → Details → World & relations → Review` (per the
  archetype-templates contract). Simple Mode shows Concept + Review only;
  Advanced shows all four steps (`{components.segmented-row}` level toggle).
  Templates as starters (C.6) live inside the walk: save any authored
  entity as a starter, begin from a template.
- **Generate (`/create/generate`)** — the candidates machinery, reframed as
  "consult the world at the table" and extended: ask (context strip names
  the working context), stage, review staged candidates (accept = make it
  canon; default answer resolves the moment), guided regenerate
  guide-box + dial, live preview for every kind (E.5).

### The registry (B.1, R1, R2)

One closed, backend-served registry (`GET /api/campaigns/kinds`), fetched
once on mount and cacheable. It holds the three existing kinds —
character / place / faction — plus ONE generic attachment/note slot;
item/event exist as data, not citizens (B.1 correction; citizenship grows
only on demand). Per kind, the registry carries: section schema, the
per-kind depth tables (dial level → section set + generation instructions),
archetypes, default dials, per-kind allowed edge types, and offered
companions. It is what turns per-type walks into content, not code, and
gives the graph's 3 kinds and the UI's surfaces one consistent vocabulary.

**Dial semantics (owner-corrected):** EVERY dial level carries the full
per-kind section set — place sections are
`description / inhabitants / what's-hidden / relations`, faction sections
are `description / doctrine / assets / relations`. Any section the DM
leaves blank is filled by the LLM exactly like the authored-figure path
(blank = model will write; DM text commits verbatim). The levels
`nothing / draft / simple / important / pillar` differ only in generation
ELABORATION. The dial floor `nothing` + all sections authored = the
zero-LLM commit (B.4 lives at the floor). For characters the dial is
mechanics weight only: narrative always complete (hero doctrine); levels
map to stat-block depth + world-integration width (`nothing` = no stat
block; `pillar` = full stat block + boss section + subsections). The dial
lives in the record (Q3): top-level key, survives export + re-rolls.
Richness is DM-commanded, never pipeline-default.

**Archetypes:** per-kind lists for guided build-in (registry field
`archetype` on place/faction); each archetype = section emphasis + offered
companions + default dial. The 10 place / 8 faction templates in
`.working/archetype-templates.md` are the content the `{components.archetype-card}`
picker renders. Place bones (trimmed): Environment, Climate, Economy (with
its optional Exports & Imports tag detail — the trade story that makes
economy a hook), Key Characteristics, short description/theme ≤ 300,
what-makes-this-unique ≤ 200, generate-chips. Faction bones: Wealth,
Power base, Leadership style, Secrecy, Key Characteristics, doctrine
≤ 300, unique ≤ 200, generate-chips. The archetype picker already encodes
scale — no size slider, no population field, no Scope slider (owner, 2026-09-25).

**Offered companions (softened "always-generates"):** archetypes propose
sub-entities + their edges (a city's Mayor `controls`, a cult's Leader
`controls` + Champion/Zealot `member_of`), staged like any mandated target
(the build's MANDATED-TARGETS path, verified in `build_in.py`). They are
tick-to-include at build/enrich time — nothing generates unless the DM
selects it (softened per owner 09-24).

**Lazy render (Q5):** existing flat entities are not migrated — records
stay physically flat until the DM acts. The section-slot model renders
absent fields as silently empty with the `{components.enrich-nudge}`
("Enrich this one."). The nudge IS the feature; enrichment is a
DM-commanded verb that grows a flat row into the sections-shape.

### Edges (C.4)

Only `part_of` is new (`located_in` + `bases_at` already carry the
containment backbone; no `based_in` alias — one relation, one name). Edge
availability is per-kind, not per-wording (owner 09-24): a place cannot be
`member_of`/`control`/`employ`; `located_in` only reaches toward places.
Relation pickers filter by source kind, mirroring `EDGE_KIND_RULES` plus
the layer-2 guidance exceptions.

### Rich pages (B.2, F)

Every kind gets the character-page pattern with type-specific tabs: stat
tiles / section accordions, history & goals, notes slot, regenerate, one
`{components.export-menu}`, the `{components.ego-graph}` (1–2 hops,
"Open full map" → Relations), the `{components.dial}` + `{components.dial-row}`
(Importance / Stance / Complexity — net-new on ALL kinds, introduced once
in the universal pattern), the atmosphere & specificity fields (vibe
one-liner, flavor chips, defining-trait tags, what-makes-this-unique,
bounded prose), and the `{components.danger-zone}` with a real confirm.
Editing flips the page (Doctrine 7); raw JSON edit sheets are gone (F.2) —
a DM never sees a brace. Populate (C.3) is a first-class action: place
seeds districts/NPCs/factions, faction seeds members — always through
tick-to-include offered companions, never forced.

### Run loop

**Tier 1 — Tonight (ships with the revisions endpoint):** the campaign
landing, one surface: campaign banner (sigil, scene) + cast strip
(session-scoped local pick, localStorage per campaign, cast from the
world's roster) + open hooks (reads `party_hook`/`secret`/`rumor` from the
existing WorldExport — full AR24 records are already there, no new fetch)
+ recent changes (from `GET /api/campaigns/{id}/revisions?limit=N`,
read-only, AD-1-safe) + could-go-wrong free text (local). Printable via
the existing printer-friendly export machinery (render_world_html pattern,
media-free). NO store writes, NO new tables.

**Session-start ritual (the DM's most repeated ritual, elevated):** one
orchestrated 60-second flow on the same surface — recap → what-they-know →
open hooks → pick scene → go. One surface, not five.

**Night ask thread (E.2 + Q6):** the Tonight surface holds the thread.
Asks are implicitly about the active scene/cast; the first ask frames it,
the second ask inherits the first — accepted answers append to context, a
conversation with the world, the at-the-table brain. Default answer
resolves the moment; accept appends to the thread (pin-to-canon deferred
as a later entity flag); the thread dies with the night, fresh next
session. Bounded retrieval (AR6) untouched. The ask itself rides the
existing ask/generate path with scene+cast+prior-accepted context
[ASSUMPTION — transport: local thread context compiled client-side].

**Tier 2 (two standalone store sub-items, each committed state with its
own revision entry + undo — bet the store only after Tier 1 validates):**
- T2a consequence verbs — `{components.verb-row}`: mark defeated / flip
  allegiance / resolve thread / spend item. Per-entity mutable state with a
  dedicated commit path mirroring the entity PATCH (one
  `entity_updated`-style revision, idempotent when value-identical) so
  undo covers it for free once the undo machinery knows the event type.
- T2b party knowledge — `{components.knowledge-chip}` 3-state per
  secret/rumor/party_hook field (`hidden / shown-to-me / known-by-party`)
  + a reveal-X verb. The fields exist today as inert data; the reveal
  state-machine + UI is what is added (A.3 refined).
Tier 2 state lives in PARALLEL tables (`entity_session_state`,
`entity_knowledge_state`) with their own revision event types — the world
record stays pure, exports stay truth-only, undo learns the two new event
types (owner verdicts). Relabeled across the run loop (E.3): undo is
**"Take it back"** — the rewind-the-scene power surfaces where the DM
sessions, and its boundary is named (media is never restored).

**Archive & sandbox:** dead worlds live in the switcher's archive group —
exportable/restorable, one restore step back into the active list, delete
only via the danger zone's confirm. What-if / sandbox (test "what if they
raid the bank?" against a throwaway copy via the Generic library, no canon
touch) is an optional stretch [ASSUMPTION].

## Voice and Tone

Microcopy. Brand voice and aesthetic posture live in `DESIGN.md`; the law
is the calm chamberlain: short complete sentences, names reasons and next
actions, never celebrates, never exclaims, never peppers the DM with tips.

| Do | Don't |
|---|---|
| "Tonight." / "Set the scene." | "Get ready for an epic night! ✨" |
| "Recap — Six sessions since the Ash Cult's vault job." | "Welcome back, Dungeon Master!" |
| "Two hooks are open. Select scene to go." | "You have 2 open hooks! 🎯" |
| "Take it back" | "Undo" (as action copy in the run loop) |
| "Mark defeated — commits a revision you can take back." | "Success! Enemy eliminated ✓" |
| "Enrich this one." | "Generate more detailed content!" |
| "Say what changed or what you want more of." (guide box) | "Enter guidance text" |
| "The vault job is done — the bank is no longer at stake." | "Crisis resolved! 🎉" |
| "This answer stays tonight." (thread ephemerality) | "Your ask has been saved forever" |
| "Draft mode: no world is touched." | "Click below to get started!" |
| "Dropped: E2 Sarella — edge target not in this world (BAD_EDGE)." | "Something went wrong" |
| "Queued (position 2)" / "Built (merged with 2 existing)" | "Success! Your request is being processed ✓" |

Rules: operator vocabulary stays visible — job states, edge directions,
dial levels, revision counts — never translated into consumer euphemism.
No streaks, no encouragement copy, no exclamation marks. The generate
chips speak the dial's truth ("At simple: Economy & Trade · Districts &
Locations."), never a promise of richness the DM didn't command.

## Component Patterns

Behavioral. Visual specs live in `DESIGN.md.Components`; names here match
`DESIGN.md` names exactly.

### Shell & global

| Component | Use | Behavioral rules |
|---|---|---|
| Sidebar shell (`{components.app-shell}`) | Global | Campaign switcher selects working context; nav Create / World / Relations / Library; switching preserves Create input, re-scopes per the Generic gate, never enqueues; collapsed to an icon rail under `900px`. |
| Sigil (`{components.sigil}`) | Switcher, Tonight banner | One per campaign, decorative identity; never interactive, never alone as a target affordance. |
| Command palette (`{components.command-palette}`) | Global ⌘K | One global search: entities, commands, surfaces. Keyboard-first: arrows move, Enter fires, Esc closes; "makes the 108 feel like 3". |
| Kind tabs (`{components.kind-tabs}`) | World roster | Registry-driven tab set (`character / place / faction / notes`); only kinds with rows render a tab; selection is a `radiogroup` (arrows move), active tab announced; empty kind = no tab [ASSUMPTION - tab treatment, see Open Questions]. |
| Entity picker (`{components.entity-picker}`) | Roster, relation targets, cast add | Typeahead over the switcher's world; results filtered per caller contract (kind filter for relation targets, cast-eligible for the cast strip); Enter selects, Esc closes without changing state. |
| Archive group (`{components.archive-group}`) | Switcher foot | Archived entries are muted, not selectable as working context; each offers Restore and Export (delete only on its page's danger zone). |

### One Create

| Component | Use | Behavioral rules |
|---|---|---|
| Mode switch (`{components.mode-switch}`) | Create header | `radiogroup` of three: Capture / Author / Generate, arrow-key traversal, exactly one active. Route encodes the mode (deep-linkable). Switching preserves shared context (campaign, theme, draft, ask) and swaps only the work area; never enqueues, never discards. Announcement states the commitment difference ("Capture — one line seeds the sheet" / "Author — you write, blanks generate" / "Generate — the model drafts, you accept"). |
| Stepper (shared) | All modes | Per-kind step maps; `hasContent`-style gates lock forward steps until their contract holds ("Give the character a name first."); completed steps revisitable, returning never discards. Current step announced (`Step 2 of 4: Concept`). |
| Card-select grid + archetype card (`{components.archetype-card}`) | Walk Concept / Details steps | Closed single-pick cards, whole card clickable + keyboard-activatable, selected state programmatic (`aria-pressed`/`radiogroup`), never color-only. Archetype cards show seed tags and default dial; the DM may pick any kind's walk at any time. |
| Dial (`{components.dial}`) | Every walk + rich pages | Five levels, one active; arrow keys step, Home/End jump, level name announced with its elaboration meaning. Lives in the record (Q3) — a re-roll or enrich inherits the dial. Never offers levels that strip sections. |
| Dial row (`{components.dial-row}`) | Every rich page | Importance / Stance / Complexity as a standard row; same interaction as the dial [ASSUMPTION — level sets]; introduced once in the universal pattern, not per-kind bolt-ons. |
| Generate chips (`{components.generate-chip}`) | Walk Review, detail dials | Checkboxes-as-chips bound to the dial: below the floor they render disabled — the DM controls what AI produces (trust + cost control). Toggling a chip updates the review rail immediately; the payload request reflects exactly the selected sections. |
| Preview card (`{components.preview-card}`) | Review rail, all modes | Live preview for every entity kind: image slot + serif name, kind badge, attribute chips, description. Updates on every keystroke/selection (debounced ~150ms). The image slot shows a basic inline-SVG placeholder (kind-aware silhouette) or the entity's attached portrait — it NEVER auto-generates an image; painting it requires a specific click (the explicit-click portrait discipline). It previews text/state plus placeholder only — no fake content, no implicit image cost. |
| Review rail (`{components.review-rail}`) | Both walks | Mirrors, never gates: authored-section mirror with filled-vs-model-will-write markers, relation lines, generate-chip summary, commit bar (Author/Capture: `Build into the world` / `Generate into the library`; Generate: selected-candidate summary). Empty fields render muted placeholders, never holes. After a successful commit the rail foot lists **suggested next steps** — quiet checkbox rows naming the natural follow-ons for what was just built (City walk → "Add districts and locations", "Populate with NPCs and factions"), each derived from kind + dial + result and linking to its surface; rows never promise a non-feature. |
| Guide box (`{components.guide-box}`) | Re-roll / enrich | Text preserved on failure (same rule as the ask box); rides the regenerate request with the dial; one line of honesty above it ("Say what changed or what you want more of."). |
| Ask box (`{components.ask-box}`) | Generate step 1; Night thread | Preserved on failure, cleared on generate success (existing `onJobMessage` rule). On the Tonight surface it composes thread asks with scene/cast context — same component, different context strip. |
| Candidate card (`{components.candidate-card}`) | Generate review only | Four equal actions (Reject / Edit / Re-roll / Accept, `Accept edited` when edited); cities and factions render their registry sections; re-roll opens the guide box + dial. Settled cards leave the default `proposed` view immediately. |
| Job-feed row (`{components.job-feed-row}`) | Authors + feeds | Queue truth: `Queued (position N)` → running → built / failed (mono error verbatim). |
| Conflict dialog / stale-edit notice / error envelope | Candidate review | Carried: three-way on 409 edit-conflict; visible discard on stale edit; inline mono error envelopes, never modals for failures. |
| Enrich nudge (`{components.enrich-nudge}`) | Rich pages, roster rows | The flat record's only affordance on absent sections; one tap opens the enrich action (guide + dial) — never auto-enriches, never migrates. |

### Tonight / run loop

| Component | Use | Behavioral rules |
|---|---|---|
| Tonight sheet (`{components.tonight-sheet}`) | Campaign landing | Opens into the session-start ritual; blocks in ritual order; printable via printer-friendly export (media-free); no store writes. |
| Tonight banner (`{components.tonight-banner}`) | Landing top | Sigil + campaign name + scene; "Take it back" entry surfaces the rewind power with the current revision context. |
| Cast strip (`{components.cast-strip}`) | Landing | Session-scoped local pick (`{components.tag-chip}`s, localStorage per campaign), add via the entity picker filtered to cast-eligible roster; removal never touches the world record. |
| Hook row (`{components.hook-row}`) | Landing, open hooks | One row per open hook (party_hook/secret/rumor); the knowledge chip carries its 3-state + reveal action; reading it is read-only always. |
| Change line (`{components.change-line}`) | Landing, recent changes | One line per revision (meta + event summary); the block degrades gracefully if the revisions endpoint is unavailable — error line + retry, the rest of the sheet unaffected. |
| Note block (`{components.note-block}`) | Landing | Could-go-wrong free text, local persistence; part of the printout. |
| Thread line (`{components.thread-line}`) | Landing, Night thread | One ask/answer pair; accept appends to thread context; the thread is session-lifetime and stated so ("This answer stays tonight."). |
| Knowledge chip (`{components.knowledge-chip}`) | T2b, hooks + entity pages | 3-state per secret/rumor/party_hook + reveal-X verb; state announced as text, never color-only; commits go through the parallel knowledge table with their own revision type. |
| Verb row (`{components.verb-row}`) | T2a, entity pages + landing | Consequence verbs commit one undoable revision each (mirrors entity PATCH one-revision path); in-flight verbs gate against double fire; "Take it back" restores the prior revision. |
| Ego graph (`{components.ego-graph}`) | Every rich page | Default 1–2 hop local graph; "Open full map" goes to Relations; text fallback lists the edges for the non-visual case [ASSUMPTION — fallback form]. |
| Export menu (`{components.export-menu}`) | Rich pages, roster rows | One trigger, one menu, all export formats; F.4's five links are gone. |
| Danger zone / confirm dialog (`{components.danger-zone}` / `{components.confirm-dialog}`) | Rich pages foot | Delete lives only here; the confirm names the entity and states the irreversibility; Cancel returns with zero state change. |

### Rail freshness vs job states (both walks, carried)

| Job state | Author/Capture rail shows | Generate rail shows |
|---|---|---|
| No job yet | Sheet/preview mirror + "model will write" placeholders | Ask context + "No candidates waiting…" + ask hint |
| `queued` | Position line verbatim; mirror frozen at submit-time snapshot | `Ask — Queued (position N)`; candidates step locked |
| `running` | Live frames as received; no invented percentages | Same; regenerate rows read `Re-roll — running` |
| `succeeded` | `built` / `built (merged with N existing)`; mirror retained for the next figure | Ask cleared; "N candidates ready"; selected-candidate summary |
| `failed` | Mono error verbatim + edit-and-resubmit; input preserved | Ask preserved + `Ask — failed: reason`; staged rows untouched |

## State Patterns

| State | Surface | Treatment |
|---|---|---|
| Job + candidate states (`queued` / `running` / `proposed` / `accepted` / `rejected`, conflicts, stale edits, ask preserved-on-failure) | Create walks | Carried unchanged from 09-21 — these contracts are code truth, not redesigned. |
| Dial levels (`nothing`/`draft`/`simple`/`important`/`pillar`) | Record, rail, chips | One active level; place/faction = elaboration over the full section set; character = mechanics weight; survives export + re-rolls (top-level key). |
| Flat record (absence of sections) | Rich pages | Silently empty section slots + `{components.enrich-nudge}`; no migration, no phantom content. The nudge IS the feature. |
| Enrich / guided regenerate in flight or failed | Rich pages, candidate cards | Guide text + dial preserved on failure; error verbatim; never auto-retries into canon. |
| Night thread | Tonight landing | Session-lifetime; fresh next session; accepted answers append; pins to canon deferred. |
| Knowledge 3-state (`hidden` → `shown-to-me` → `known-by-party`) | T2b hooks, entity pages | Reveal-X sets `shown-to-me`; the path into `known-by-party` is decided with Tier 2 [ASSUMPTION — see Open Questions]; commits carry their own revision type; never exported. |
| Consequence verbs (defeated / allegiance / thread / item) | T2a verb row | Per-entity mutable state in the parallel session table; one revision per commit; "Take it back" rewinds (media never restored — named). |
| Archive (`archived` / restored / deleted) | Switcher, danger zone | Archived worlds out of the active list, exportable/restorable; delete only via danger zone confirm. |
| Switcher mid-flight (jobs in flight / open ask / draft) | Global | Context switch preserves all three (re-scoped per the Generic gate); never enqueues, never discards. |
| Revisions endpoint unavailable | Tonight recent-changes block | Error line + retry in the block; the rest of the sheet renders normally. |
| Offline / WS drop | Create walks | Carried: job lists re-sync on reconnect; candidates re-sync with stale-edit reconciliation; no blocking banner. |
| Load failure | Create entry | Carried: error verbatim + "Back to your worlds" link. |
| Empty world roster / empty kind tab | World | "No {kind} yet — create one in Create, or enrich a flat row." Kind tabs render only for kinds with rows [ASSUMPTION - tab treatment, see Open Questions]. |
| Empty relations graph | Relations | "No relations yet — add one from any entity page." Graph viewport renders the empty state, filters disabled. |
| Empty library drafts | Library | "Draft mode: no world is touched." + theme-seed card; the Generic isolation proof line. |

## Interaction Primitives

- Click / Enter / Space to act; whole-card activation for card-select,
  archetype cards, and roster rows; inner links stop propagation.
- `radiogroup` semantics: mode switch (3 halves), segmented rows (incl.
  Simple/Advanced), dial (stepped single-choice; arrows + Home/End).
- Keyboard-first density: ⌘K palette everywhere; `{components.entity-picker}`
  typeahead replaces every 108-wall plain select.
- Form-first: filled fields commit verbatim and are never rewritten; blanks
  are the model's; the UI MUST show yours-vs-models before submit — carried,
  and now true for place/faction walks too.
- Esc closes the topmost overlay (switcher, palette, picker, dialogs);
  never discards form input. Modal stacks never exceed one level.
- Optimistic updates forbidden on Accept, Author submit, verbs, and restore
  — all commit paths wait for the store response, then re-render.
- No drag-to-reorder anywhere. No infinite scroll (cursor pagination per
  API convention).
- **Banned:** modal stacks > 1; hover-only affordances; celebratory
  animation on build/accept/verb — the world row, the revision, or the
  resolved moment is the reward; raw-JSON edit sheets; an `based_in`-style
  second name for one relation.

## Accessibility Floor

Behavioral. Visual contrast lives in `DESIGN.md`.

- WCAG 2.2 AA on the dark-first surface; focus visibility never color-only
  (visible focus ring on every interactive element).
- Keyboard reachability for every Create action, the palette (fully
  keyboard-operable, `aria-live` results), pickers, dials, verb rows,
  dialog chains (danger → confirm), and the switcher. Tab order matches
  reading order: sidebar → banner/ritual → work area → rail.
- Every input labeled. Mode switch announces the commitment difference;
  stepper announces `Step N of M`; dial announces level + elaboration
  meaning; chips expose checked state programmatically; knowledge chips
  expose state as text, never color-only; job rows expose queue state as
  text; articulate arrival announces count ("3 candidates ready");
  accepted thread answers announce with `aria-live` polite.
- The ego-graph has a non-visual fallback listing the 1–2 hop edges
  [ASSUMPTION — fallback form, see Open Questions].
- Reduced motion: no staged transitions on state changes — queue/feed/
  thread swaps are instant content swaps.
- Touch targets ≥ 44px on card-select, archetype cards, step controls,
  mode-switch halves, chips that act, and verb row actions.

## Key Flows

### Flow 1 — Session-start ritual, Friday 19:40 (Yigit, players at the door in twenty)

1. Yigit opens Greymarch from the switcher. The campaign landing is Tonight
   — no dashboard, no five-click setup; the session-start ritual is the page.
2. Ritual beats in order: **recap** ("Six sessions since the Ash Cult's
   vault job — the bank is evening accounts"), **what-they-know** (3-state
   readout: party knows the vault job; Blaise's debt stayed hidden),
   **open hooks** (two hook rows: Censer's ledger, the constable's
   questions), **pick scene** ("Keeper's Fall"), **go**.
3. The prep sheet beneath confirms tonight's state: cast strip (Sarella,
   Mara, Censer — session-scoped, local), recent changes (revision lines
   from the read-only endpoint: "Sarella — edited · 6h"), could-go-wrong
   (Yigit notes "the bank's factor has leverage"). He exports the printable
   sheet (media-free) to the table.
4. **Climax:** "Go" lands on a scene-ready, printed sheet while no world
   revision was written — prep is done and the table can start.

Failure: the revisions endpoint is down → the recent-changes block shows an
error line + retry; everything else renders, the sheet still prints.
Failure: no cast yet → "No cast yet — add who's playing." with the entity
picker one key away.

### Flow 2 — Capture into Author, 1am (Yigit, a tavern keeper for tomorrow)

1. Sidebar Create → `/create/capture`. Yigit types one line into the front
   door: "Sarella Voss — a tavern keeper who owes the Ash Cult for her
   daughter's debt." The rail parses name + role into the preview card live
   [ASSUMPTION — parse mechanism].
2. He continues into `/create/author`: the Author sheet lands with name +
   role prefilled; Figure fields sit as "model will write" blanks; the dial
   defaults to `simple`; one relation draft — `debt`, existing, target Ash
   Cult, counter 2.
3. He fills voice and secret; Review signs off the mirror
   (`Sarella --debt(2)--> Ash Cult`); `Build into the world`. Feed:
   `Queued (position 1)` → running → `built (merged with 2 existing)`.
4. **Climax:** Sarella is a committed Greymarch entity, debt edge wired, in
   one revision — and tonight's cast strip can now suggest her. One line
   became a complete record, never a bare queue.

Failure: parse missed the role → the role segmented row opens, half-filled
by the line's hints. Failure: unnamed submit → "Give the character a name
first.", no job enqueued, input preserved. Failure: build job fails → mono
error verbatim on the row; edit and resubmit.

### Flow 3 — The archetype walk: a city with a mayor (Yigit builds "Deepwater")

1. `/create/author` → he begins from a **template** (C.6): "Deepwater — founding
   harbor city" saved from an earlier walk; the Concept step lands prefilled
   (archetype City, Environment Coastal, Economy Trade) + the seed tags, and
   he edits from there. Kind pick reveals per-kind walks; Place is chosen.
   Concept: archetype cards (Village…City…Fort) — scale encoded in the
   archetype, no size slider; **City** confirmed (seed tags: Busy Harbor ·
   Diverse Culture · Old Walls · Corruption; default dial `important`).
2. Details: Environment Coastal, Climate Temperate, Economy Trade with
   **Exports & Imports** tags ("arms out, grain in"), Key Characteristics,
   theme + uniqueness. The generate chips ride the dial: Government & Law,
   Economy & Trade, Districts & Locations, Notable NPCs, Factions & Groups,
   History, Rumors & Secrets, Points of Interest.
3. World & relations: `located_in` toward the campaign region; offered
   companions list Mayor (controls), Watch captain (employs), Market master
   (employs) — he ticks Mayor and Watch captain; nothing is forced, the
   unselected Market master is never generated.
4. Review: the rail previews the city live (name badge, attribute chips,
   description); `Build into the world`.
5. **Climax:** Deepwater, its Mayor, and its Watch captain land in one wave
   (the mandated-targets machinery), edges wired — the city has a face
   before the next session. The rail foot lists suggested next steps:
   "Add districts and locations", "Populate with NPCs and factions" —
   Yigit is one tap from the natural sequel, never sold a non-feature.

Failure: dial at `nothing` with every section authored → zero-LLM commit,
no provider call, no queue. Failure: a companion tick unselected → it
simply does not exist; no apology generation, no forced mayor.

### Flow 4 — Enrich a flat faction (the old "Ledger" build-wave row)

1. World → Faction tabs → "The Ledger": a flat row from an old build wave.
   Its rich page shows quietly empty section slots and the
   `{components.enrich-nudge}` — "Enrich this one." No migration ran.
2. Yigit enriches with a guide ("a merchant cartel dunning the bank, four
   partners, one heretical ledger") and sets the dial to `simple`.
   Enrichment serves the existing regenerate contract with `sections: null`
   + the guide + dial — the registry's per-kind vocab is what lets
   regenerate serve places/factions at all. The page's ego-graph shows the
   Ledger's 1–2 hop web (the Constable, the bank) with "Open full map" to
   Relations.
3. **Climax:** The Ledger grows into the sections-shape — doctrine, assets,
   relations — reading like any character page. His guide shaped it; the
   dial kept the fluff honest.

Failure: enrich fails → error verbatim, guide text preserved, the flat row
unchanged. Failure: asking for a raw editable brace → not offered; edit
flips the page (Doctrine 7), JSON stays dead. Failure: he wants to delete
the Ledger → no on-card delete; the page-foot danger zone names it in a
confirm dialog before anything happens.

### Flow 5 — Mid-session, the Night thread (Monday, at the table: "what if they raid the bank?")

1. Tonight is open; the ask box composes in the scene's voice — cast and
   scene already scoped from the ritual. Yigit asks: "What if they raid the
   bank instead of paying?"
2. The world answers; the default answer resolves the moment — no build
   detour, no candidate screen at the table. Yigit accepts; the answer
   appends to tonight's thread.
3. A beat later the table pushes: "And the vault door?" — the second ask
   inherits the first (scene + cast + prior accepted answers in context).
4. **Climax:** the table gets a coherent, continuous answer before the
   dice roll — and tomorrow the thread is gone, cleanly, stated as such
   ("This answer stays tonight."). No canon was touched; pin-to-canon is
   deferred.

Failure: ask fails → text preserved, resubmit without retyping. Failure:
Yigit wants the answer permanent → not available tonight; the deferred flag
is the named future path.

### Flow 6 — Consequences and the rewind (Tier 2; Blaise's defeat)

1. During play, Blaise falls. His entity page's `{components.verb-row}`:
   Yigit hits **Mark defeated**. A revision commits (the verb path mirrors
   the entity PATCH one-revision idempotent commit); the Tonight banner now
   shows the scene at its new state.
2. A secret came out mid-session: Yigit reveals Censer's ledger secret —
   the `{components.knowledge-chip}` flips `hidden → shown-to-me` and the
   party's copy is what the table knows; exports still carry only the truth.
3. Next day he reconsiders. **Take it back** rewinds the defeat —
   compensating commit, latest revision, media never restored (named
   boundary).
4. **Climax:** the session's world change was real at the table and
   reversible after it — the rewind power is part of the run loop, not a
   buried utility.

Failure: double verb fire → gated, second click changes nothing. Failure:
take-it-back asked to restore a portrait → the boundary says media is not
restored, before the action.

## Inspiration & Anti-patterns

- **Lore-Weaver "Create City" wizard (imports/lore-weaver-create-city-2026-09-24.md)**
  — the owner reference imported 09-24. It illustrates the v3 creation
  anatomy: concept type cards, environment cards, tag input, theme +
  uniqueness counters, "what will be generated" chips bound to a depth
  control, the live-preview rail, and Simple/Advanced walk levels. Adopted
  into the v3 walk and the archetype templates per the import's
  lifted/rejected itemization. Rejected from it: Estimated Cost / credits
  (no cost model — queue truth is position + state text); "Use Quick
  Create" as a separate escape hatch (v3 Capture seeds Author — no second
  door); the switcher medium without the 09-23 context-boundary rules
  (safe switcher, archive); library-category lists as separate surfaces
  (v3 rolls them into the registry roster lenses); PRO badge / account
  chrome.
- **Anti-pattern — the gamble regenerate (owner insight, 09-24):** re-roll
  with no input and no control, try-again-and-again. Fixed by the
  guide-box (a small-or-big text) + the dial controlling how much comes
  back — regenerate is a function of seed, record, requested, context, DM
  guide, and dial level.
- **Anti-patterns carried from 09-21 (never repeat):** Author and Generate
  as divorced pages; token-swap restyles that preserve a broken IA;
  staging candidate cards inside the Author flow.
- **Rejected — the 108-card grid as the browse surface:** v3's World is a
  calm, searchable roster into rich pages; density lives in ⌘K, not in a
  wall.
- **Rejected — raw JSON edit sheets:** a DM never sees a brace; structured
  page-flip editing (Doctrine 7) owns world_integration and Additional data.
- **Rejected — five export links; on-card delete without confirm:** one
  Export menu; deletion in a danger zone behind a real dialog.
- **Rejected — edge aliases:** no `based_in`; one relation, one name.
- **Rejected — size sliders / population fields:** the archetype picker
  already encodes scale.
- **Rejected — timeline / DM-notes / token-preview / VTT export / credits:**
  still non-features; no surface may promise them, even as empty states.

## Responsive

Web-desktop first; the shell collapses, never the content:

| Breakpoint | Behavior |
|---|---|
| `≥ 1020px` | Sidebar full; Create work area form + rail; Tonight blocks full column; roster 2-up lists |
| `900–1019px` | Sidebar collapses to icon rail; Create work area stacks form-over-rail |
| `560–899px` | Single-column everything; kind tabs scroll horizontally; walk grids 1-up |
| `< 560px` | Stepper collapses to "Step N of M — {name}"; dials and chips wrap; print stays the same media-free export |

The Tonight landing is intentionally single-column at every width — it is a
sheet, not a dashboard.

## Open Questions

Small, genuinely-open memlog points, mirrored from the `[ASSUMPTION]` tags:

0. Kind-tabs treatment — active-tab anatomy and empty-kind rendering
   (registry-driven tabs are decided; their selected state and
   empty-kind behavior are open, DESIGN-side `[ASSUMPTION - tab treatment]`).

1. Capture parse mechanism — client-side extraction of name + role vs an
   LLM-assisted parse of the intent line (Q2 says only "one line → lands
   in the Author sheet with name+role prefilled").
2. Character walk step map after the world-step retirement (world context
   now always comes from the switcher).
3. Importance / Stance / Complexity level sets for the
   `{components.dial-row}` (C.1 names the three dials, not their scales).
4. Night-thread transport — thread context compiled client-side and sent
   with the existing ask path is assumed; server-side session tables are a
   Tier 2 option only after Tier 1 validates.
5. Tonight landing geometry — single ritual column assumed; a wide-screen
   side column for the thread is possible but uncommitted.
6. The `known-by-party` path (only the reveal verb, or party-knows from
   accepted thread answers) is decided at Tier 2 design time.
7. Ego-graph non-visual fallback form (edge list vs table) — assumed list.
8. Sandbox / what-if remains an optional stretch, per the memlog
   assumption; not in the v3 committed scope.
9. Tier 2 ships after Tier 1 validates (RUN-LOOP STATE position) — the
   sequencing gate, not the design, is open.