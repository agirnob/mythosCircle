---
status: 'review'
updated: '2026-09-24'
topic: 'Accessibility review of the v3 UX spine pair (DESIGN.md + EXPERIENCE.md)'
standard: 'WCAG 2.2 AA'
verdict: 'SPINE SOUND — APPROVE WITH TOKEN-LEVEL FIXES REQUIRED'
---

# Accessibility review — mythosCircle v3 UX spine pair

Reviewed as a consumer of the dark-first single theme would experience it:
behavioral commitments from `EXPERIENCE.md` (Accessibility Floor, Component
Patterns, Interaction Primitives), visual contract from `DESIGN.md` frontmatter
tokens. Contrast ratios computed (WCAG 2.2 relative luminance) on the literal
token values. Where a spine row commits an a11y behavior, completeness and
consistency were checked; this review never redesigns.

## Overall verdict

**The Accessibility Floor is strong and unusually behavioral.** Every surface
the mandate names — command palette, entity picker, dials, verb rows, dialog
chains, archive/switcher — has a real, specific rule, not a blanket promise.
"Never color-only" is committed consistently (selected states, knowledge-chip
state, queue state), motion is reduced by rule, touch targets are ≥44px on the
acting components, and the aria-live arrival copy is even spelled out
("3 candidates ready"). That layer of the spine **passes as written**.

The failures are concentrated in **one place: the color token contract in
DESIGN.md**, where the accent color is reused as *foreground text* on the
selected/overlay/ghost fills that v3 makes work harder than v2. Four live text
surfaces fail AA 4.5:1 (3.02–3.46:1), plus a Level A link-distinguishability
gap, a meaningful-disabled-text gap, and a non-text boundary gap on inputs.
All are token-level fixes; none require redesign. Dark-first single theme is a
documented law decision — noted, not flagged.

## Contrast table (WCAG 2.2, computed on token frontmatter values)

Legend: **AA** = ≥4.5:1 normal text · **3:1** = ≥3.0:1 (large text / non-text) ·
**FAIL** = below 3:1.

| Pair | Ratio | Verdict | Note |
|---|---|---|---|
| text.primary #e8e6e3 on bg.base #12141b | 14.77 | AA ✓ | |
| text.primary on surface.raised #1b1e27 | 13.36 | AA ✓ | |
| text.primary on surface.selected #262b3d | 11.27 | AA ✓ | mode-switch-active, sidebar-item-active, card-select-active |
| text.primary on surface.overlay #232736 | 11.92 | AA ✓ | palette/confirm/conflict surfaces |
| text.secondary #9aa0a6 on bg.base | 6.97 | AA ✓ | |
| text.secondary on surface.raised | 6.30 | AA ✓ | help/muted/queue labels, change-lines |
| text.secondary on surface.selected | 5.32 | AA ✓ | |
| text.secondary on bg.sunken #0d0f14 | 7.26 | AA ✓ | mode-switch inactive halves, dial track labels |
| text.secondary on surface.overlay | 5.62 | AA ✓ | state-chip / knowledge-chip resting state |
| text.disabled #5e636e on bg.base | 3.05 | **FAIL AA** | disabled generate-chips; see F3 |
| text.disabled on surface.raised | 2.76 | **FAIL** | same, on raised (review rail / chips) |
| text.link #8ab4ff on bg.base | 8.81 | AA ✓ | link vs *background*; vs body text is the problem (F2) |
| accent.on-primary #ffffff on accent.primary #6d5ef0 | 4.66 | AA ✓ | primary buttons, stepper-active, dial current — passes, but only just |
| accent.on-primary on accent.primary-hover #7f73f5 | 3.70 | **FAIL AA** | primary-button label on hover; see F4 |
| accent.primary as text on surface.selected | 3.02 | **FAIL AA** | segmented-cell-active labels, card-select accent-tinted title; F1 |
| accent.primary as text on surface.overlay | 3.19 | **FAIL AA** | command-palette active row (tagged ASSUMPTION); F1 |
| accent.primary as text on accent.ghost #1f1e39 | 3.46 | **FAIL AA** | active generate-chip label; F1 |
| status.error #ff7b72 as text on raised / base | 6.60 / 7.30 | AA ✓ | stale-edit notice, error envelope, terminal chips |
| status.success #7bd88f as text on raised | 9.56 | AA ✓ | |
| status.warning #f5b54a as text on raised / overlay border | 9.19 / 8.19 | AA ✓ | conflict-dialog hairline passes non-text 3:1 |
| status.info #8ab4ff as text on raised | 7.97 | AA ✓ | |
| accent.primary focus ring vs bg.base | 3.95 | 3:1 ✓ | focus visibility met on all rests |
| accent.primary focus ring vs surface.raised | 3.57 | 3:1 ✓ | |
| accent.primary focus ring vs surface.selected | 3.02 | 3:1 ✓ | passes but marginal (see F5) |
| accent.primary vs bg.sunken (stepper/dial markers on track) | 4.12 | 3:1 ✓ | non-text marker, passes |
| border.subtle #2c3038 vs bg.base (input boundary) | 1.39 | **FAIL** | F6 |
| border.subtle vs surface.overlay | 1.12 | **FAIL** | overlay controls (picker results, menus) rely on fill separation; F6 note |
| border.strong #3a3f4b vs surface.raised (hover edge) | 1.58 | **FAIL** | hover-only edge; not a resting requirement — note |
| text.link vs text.primary (link distinguishability) | 1.68 | **FAIL** | F2 |

## Findings

### F1 — HIGH · DESIGN.md `Colors` + Components (segmented-row, card-select, command-palette, generate-chip)
**Finding:** `accent.primary` doubles as foreground *text* on the very fill
colors v3 emphasizes. `segmented-cell-active` renders labels ("Simple/Advanced",
role "NPC/BBEG/Monster") at 3.02:1; card-select/archetype "accent-tinted title"
at 3.02:1; command-palette active row (accent text, tagged `[ASSUMPTION]`) at
3.19:1; active generate-chip label on the ghost wash at 3.46:1. All are normal
body/small text → all fail AA 4.5:1. These are the states the design makes most
prominent, so the failure lands exactly where the DM is looking.
**Fix:** keep accent for rings/underlines/markers (all pass non-text 3:1), and
render the label text `text.primary` on all four active fills (11.27:1+ on
selected, 11.92 on overlay, 14.77 on base). Resolve the two `[ASSUMPTION]`
tags (palette active row, generate-chip active treatment) to this rule.

### F2 — MEDIUM · DESIGN.md Colors (link) vs EXPERIENCE.md
**Finding:** `text.link #8ab4ff` passes against background (8.81:1) but sits at
1.68:1 against `text.primary` body text, and no spine row commits a non-color
link cue ("inline text links only — never buttons"). WCAG 2.2 Level A 1.4.1:
links must not be identified by color alone. The "thin rules" aesthetic is the
natural fit.
**Fix:** commit in Colors/Do's: inline links carry a hairline underline at rest
(1px `{color.text.link}`), color is never the sole indicator.

### F3 — MEDIUM · DESIGN.md Components (`generate-chip` disabled state)
**Finding:** disabled chips render `text.disabled` (#5e636e, 3.05:1 on base,
2.76:1 on raised). Inactive-UI text is formally exempt from 1.4.3, **but**
these chip labels are meaning-bearing: the disabled chip tells the DM which
sections the dial floor strips ("the chips speak the dial's truth"). At
2.76:1 it is also the only non-text-adjacent pair under 3:1.
**Fix:** either raise disabled text to ≥4.5:1 (e.g. `#6b717d`) or, if the
label must stay muted, move the meaning to the enabled chip row title so the
disabled label is decoration.

### F4 — MEDIUM · DESIGN.md `accent.primary-hover`
**Finding:** white on `accent.primary-hover #7f73f5` = 3.70:1 — primary button
label fails AA during the hover state. Dark themes conventionally *lighten* on
hover, which worsens this; the current hover is already lighter than the
resting accent (4.66:1 passing).
**Fix:** pick a hover that keeps on-primary ≥4.5:1 — darken within the violet
step (target L ≤ 0.183) rather than lightening, or raise the label weight on
hover. Hover is a real state; WCAG applies.

### F5 — LOW · DESIGN.md Elevation/Components (focus representation)
**Finding:** the accent focus ring clears 3:1 on every resting surface but only
at 3.02:1 on `surface.selected` (no headroom), and the spine never defines a
focus treatment for accent-*filled* elements (stepper current step, dial
current level) where an accent ring would be invisible. EXPERIENCE.md commits
"visible focus ring on every interactive element" — that is exactly the case
the visual layer leaves open.
**Fix:** state the filled-element focus treatment (e.g. 2px light ring
`border.strong` or outline) and keep a ≥3.2:1 target for rings on selected.

### F6 — MEDIUM · DESIGN.md Components (`input`, overlays) vs EXPERIENCE.md Floor
**Finding:** text inputs (ask box, guide box, capture line, picker field) are
`bg.base` fill + `border.subtle` boundary = 1.39:1, failing WCAG 1.4.11 non-text
3:1 for a component that is identified *by its boundary* (an empty field has no
label inside it). Quiet/secondary buttons with text labels are exempt via
identification; the fields are not. Overlay lists (picker results, export menu)
at 1.12:1 boundary survive on fill+shadow separation but sit on the same
border token.
**Fix:** give interactive fields `border.strong` (1.58:1 — still short; target
≥3:1) or a `surface.raised` fill (13.36:1 text, boundary then = fill-vs-base
which is itself faint — so prefer a 2px `border.strong` plus resting focus
already granted). Do not widen the finding to card borders, which carry
content and are exempt.

### F7 — LOW · spine consistency (nits)
- EXPERIENCE.md Accessibility Floor: "…pickers, **dime dials**, verb rows…" —
  "dime" is a typo for "dial(s)"; intent unambiguous, fix the copy.
- Stepper "upcoming subtle-border dot" is `border.subtle` (1.39:1, fails
  non-text) — acceptable because the step label text carries the state, but
  worth an explicit "never the only indicator" line consistent with the rest
  of the spine.
- Ego-graph non-visual fallback and dial-row level sets remain
  `[ASSUMPTION]`-tagged; both are a11y-relevant and should be closed before
  build, especially the fallback form (the spine's only committed non-visual
  path — its form list is assumed).

## Accessibility Floor coverage check (v3 surfaces)

| Commitment | EXPERIENCE.md row | Verdict |
|---|---|---|
| Keyboard reachability — palette | Floor + Command palette row ("keyboard-first: arrows, Enter, Esc"; "fully keyboard-operable, aria-live results") | ✓ Comitted |
| Keyboard reachability — entity picker | Floor "pickers" + Entity picker row (Enter select, Esc close) | ✓ |
| Keyboard reachability — dials | Floor "dime [dial]s" + Dial row (arrows step, Home/End jump) | ✓ (typo) |
| Keyboard reachability — verb rows | Floor "verb rows" + Verb row (commit paths, double-fire gate) | ✓ |
| Keyboard reachability — dialog chains (danger → confirm) | Floor + Danger-zone/confirm row (Cancel zero-change; stack ≤1) | ✓ |
| Keyboard reachability — archive + switcher | Floor "the switcher" + Archive group row (Restore/Export) | ✓ |
| Mode switch commitment difference | Mode switch row — verbatim announcement copy | ✓ |
| Stepper position | Stepper row — "Step 2 of 4: Concept" + collapse text | ✓ |
| Dial level + meaning | Dial row — "level name announced with its elaboration meaning" | ✓ |
| Chips checked state programmatic | Card-select (aria-pressed/radiogroup, never color-only) + Floor "chips expose checked state" | ✓ |
| Knowledge-chip state as text | Knowledge chip row + Floor "never color-only" | ✓ |
| Queue state as text | Job-feed row + Floor "expose queue state as text" | ✓ |
| aria-live arrivals | Floor — count verbatim + "aria-live polite" on thread answers | ✓ |
| Reduced motion | Floor — "instant content swaps", no staged transitions | ✓ |
| Touch targets ≥44px | Floor — card-select, archetype cards, step controls, mode-switch halves, chips that act, verb rows | ✓ (cast chips removable — covered by "chips that act") |
| Focus visibility | Floor — "never color-only… every interactive element" | ✓ as promise; visual layer gap = F5 |
| Ego-graph non-visual fallback | Floor + ego-graph row — form is ASSUMPTION | ✓ committed, form open (F7) |

No medium+ gaps found in the Floor itself; the medium+ findings are all token
contract defects (F1–F4, F6) that the Floor's final line ("Visual contrast
lives in DESIGN.md") pushes onto the layer that fails.

## Token-vs-behavior conflicts (per scope)

1. **text.disabled** (F3) — the Floor requires queue/section truth as readable
   text; the disabled chip's meaning-bearing label is rendered at
   sub-AA contrast. Real conflict, medium.
2. **Status color as hairline only** — no conflict: status text pairs all pass
   AA (6.6–9.56:1), hairlines pass non-text 3:1 (8.19:1 lowest), and every
   status-carrying component also carries prose. Satisfied.
3. **Knowledge-chip announced only via color** — no conflict: state is
   committed as text in both DESIGN.md ("state text takes status color only on
   terminal states") and EXPERIENCE.md ("announced as text, never
   color-only"). Consistent.
4. **Accent as "will fire" voice** (F1) — the visual language leans hardest
   on the one token that fails as text on its own fills. The largest conflict
   in the pair.

## Severity summary

| Sev | Count | Findings |
|---|---|---|
| HIGH | 1 | F1 accent-as-text on active fills (4 surfaces, 3.02–3.46:1) |
| MEDIUM | 4 | F2 link cue, F3 disabled text meaning, F4 hover 3.70:1, F6 input boundaries |
| LOW | 3 | F5 focus headroom/filled treatment, F7 nits (typo, stepper dot, ASSUMPTIONs) |