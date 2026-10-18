# Consistency and accessibility review — 2026-10-18

Scope: independent review of DESIGN.md, EXPERIENCE.md and DESIGN-PLAN.md against current tokens.css, router.ts and AppShell.vue. Used the bmad-ux validation rubric. No application behavior was tested and no new layout is approved by this review. Locations refer to the reviewed draft; subsequent edits can move lines.

## Overall assessment

The pair consistently describes a proposed cohesion pass, preserves the current navigation and privacy boundaries, and distinguishes current source evidence from unverified authenticated screens. Every router entry, including the candidates redirect and both Forge destinations, has a disposition. Shared component names and token references agree across the pair. State coverage is adequate for planning. Accessibility acceptance needs concrete contrast decisions and more measurable interaction checks before these guides are implementation-ready.

Category verdicts: route/flow coverage **adequate**; token completeness **strong**; component coverage **adequate**; state coverage **adequate**; visual reference coverage **thin** (authenticated captures deliberately pending); inheritance/terminology **strong**; document shape **strong**; keyboard/responsive acceptance **adequate with gaps**.

## Findings

### High — Define an accessible filled-action label pairing

Location: DESIGN.md:122 and 128; EXPERIENCE.md:115. Source evidence: frontend/src/styles/tokens.css:114–115.

The action role gives an explicit violet fill but leaves its foreground to a later check. White on `#8b6cff` is **3.69:1**; text-primary `#eef1fb` on that fill is **3.27:1**. Both miss the guide's 4.5:1 normal-text target. The existing skip link uses white on this exact fill, so faithfully copying current primitives imports a known problem. Large-text eligibility cannot be assumed for ordinary button/skip-link labels.

Fix: commit an accessible foreground/fill pair for normal-sized primary-action labels and use it in state strips. Existing `colors.app` (`#080d16`) on the violet fill gives **5.27:1**, a possible pairing to evaluate visually. Alternatively choose a measured fill/foreground combination through an explicit token change. Include the skip link in the later implementation audit. This review does not authorize a palette change or claim all current buttons use the failing pair.

### Medium — Turn the input-boundary warning into a usable contract

Location: DESIGN.md:128 and 168; EXPERIENCE.md:115.

The warning correctly recognizes weak borders but never supplies a passing treatment for designers to copy. Border `#24344a` against input `#0a1421` is **1.47:1**; even border-bright `#334966` gives only **2.01:1**. Neither reaches the guide's 3:1 target when that outline is the sole visual means of identifying an input. Reusing the brighter border alone cannot fix that case.

Fix: provide one measured default input-boundary treatment, or clearly specify another sufficient visual identification method, and show default/focus/error/selected states. Keep decorative separators distinct from boundaries required to locate controls. Focus-bright `#a994ff` on surface `#0e1724` gives **7.17:1**, but a passing focus state does not establish the default boundary.

### Medium — Make target size and zoom checks measurable

Location: DESIGN.md:156; EXPERIENCE.md:117 and 205; DESIGN-PLAN.md:94–95.

Principal actions have a proposed 44px target, while compact controls require only "adequate spacing" and zoom is unspecified. This leaves no consistent decision for dense toolbar icons, nested entity actions, relation chips or tabs. Testing named viewport widths alone also does not establish usable text resizing or reflow.

Fix: retain 44px as the preferred touch target; define the minimum 24×24 CSS px target or documented spacing/exception criterion for compact controls. Add checks at 200% text resize and 400% browser zoom/reflow, with actions and error messages reachable. Record genuine two-dimensional graph/table exceptions and keep their equivalent reading/task path usable. Check that sticky action bars or overlays do not obscure keyboard focus.

### Medium — Distinguish section links, tabs and wizard steps

Location: DESIGN.md:169; EXPERIENCE.md:81 and 117.

"Tabs/steps" and arrow-key behavior "when appropriate" combine different interaction models. A downstream designer could apply tab roles to ordinary anchor navigation or force arrow keys onto a wizard stepper. Both would make the shared pattern inconsistent despite matching its appearance.

Fix: classify each section navigator in its screen brief. In-page section links remain links; tabs use tablist/tab/tabpanel roles, selected state and a documented arrow-key/focus model; step navigation communicates current step and existing availability constraints without inventing a new wizard. Specify selection activation and edit preservation per actual control.

### Low — Map the plan's additional primitives into the shared catalog

Location: DESIGN-PLAN.md:42; DESIGN.md:162–176; EXPERIENCE.md:71–86.

The plan asks for choice tiles, disclosures and editable list rows, while the paired catalog does not name or map those patterns. They can be legitimate variants, but that relationship is unstated and could produce separate undocumented components or missing behavior.

Fix: map choice tiles to Field, editable list rows to the relevant Field/Entity row pattern, and disclosures to a defined disclosure contract, or add corresponding rows to both guides. Cover disclosure expanded state and keyboard operation and distinguish a choice from navigation.

## Contrast measurements

Computed from the literal sRGB hex tokens using relative luminance and `(lighter + 0.05) / (darker + 0.05)`. Rounded to two decimals. These are opaque token pair measurements, not a browser audit of gradients, opacity, images or composited states.

| Foreground / background | Ratio | Interpretation |
|---|---:|---|
| White / interactive | 3.69:1 | Fails normal text target; current skip-link pair |
| text-primary / interactive | 3.27:1 | Fails normal text target |
| app / interactive | 5.27:1 | Passes normal text target |
| text-primary / surface | 15.95:1 | Passes normal text target |
| text-secondary / surface-hover | 8.14:1 | Passes normal text target |
| text-muted / surface | 4.68:1 | Passes normal text on this background only |
| text-muted / surface-raised | 4.35:1 | Fails normal text target |
| text-muted / surface-hover | 4.01:1 | Fails normal text target |
| text-faint / surface | 2.90:1 | Fails text targets; retain nonessential/decorative restriction |
| border / input | 1.47:1 | Insufficient as sole identifying input boundary |
| border-bright / input | 2.01:1 | Insufficient as sole identifying input boundary |
| interactive-bright / surface | 7.17:1 | Strong opaque focus-color contrast on this background |

Do not treat disabled/inactive controls as though they have the same contrast requirement as active controls. Readable explanatory text about availability still needs its applicable text contrast.

## Verified consistency and remaining evidence

- All 16 current router entries appear in the route inventory, including the compatibility redirect. Dormant views are correctly identified as non-targets.
- Every frontmatter token reference resolves; documented CSS values match current tokens. Shared visual/behavior catalog pattern names agree.
- The guides preserve the actual 800px shell reflow instead of claiming the historical icon rail is shipped.
- Snapshot sources exist. The baseline login image exists and is labeled unauthenticated evidence. Authenticated screenshot absence is disclosed rather than presented as validation.
- Empty, populated, cold-load, permission/missing, pending, form-error, offline/reconnect, conflict, no-image and long-content conditions have explicit common rules plus route-specific checklists.
- Proposal versus committed content, explicit image generation, guarded campaign access and unsaved-edit uncertainty are communicated consistently.
- Source-only guidance cannot validate actual screen-reader announcements, modal traps, touch hit areas, focus visibility, reflow or error recovery. Those remain required browser checks after selected drafts are implemented.
- The parent update is already reconciling the owner's first-draft selection to Forge, World and Overview. Superseded Add Character priority text is not counted as an unresolved review finding here.

Counts at review time: **0 critical, 1 high, 3 medium, 1 low**. No scope-expanding feature recommendations.

## Resolution recheck — 2026-10-18

Rechecked only the five findings against the revised guide contract. The original assessment above records the earlier draft and is not the current evidence inventory.

| Finding | Guide resolution | Application verification |
|---|---|---|
| Filled-action label pairing — high | Addressed. DESIGN frontmatter adds `components.button.foreground-on-accent`; DESIGN:124 and EXPERIENCE:115 specify app text on interactive fill, measured at 5.27:1, and identify the skip link for implementation audit. | Unverified. No rendered fix or contrast-composite audit was performed. |
| Default input boundary — medium | Addressed. DESIGN frontmatter adds `components.field.boundary`; DESIGN:130 and EXPERIENCE:115 specify opaque text-muted on input, measured at 4.81:1, and retain state/compositing checks. | Unverified. Default, focus, error and selected input states require implementation/browser checks. |
| Target size, zoom and obscured focus — medium | Addressed. DESIGN:158 and EXPERIENCE:117 define preferred 44px targets, 24px or documented spacing/exception criteria, 200% text resizing, 400% zoom/reflow, and unobscured keyboard focus. | Unverified. Screenshots alone do not establish target geometry, text resizing, focus visibility or keyboard usability. |
| Section-navigation semantics — medium | Addressed. DESIGN:171 and EXPERIENCE:81/117 separate links, tabs and steps and specify selection, activation, focus and disclosure behavior. | Unverified. Actual controls need semantic and interaction checks. |
| Additional primitive catalog mapping — low | Addressed. Choice tiles and editable rows map to Field; disclosure behavior is specified. DESIGN-PLAN:44 now consistently identifies disclosures as Section navigation variants, matching DESIGN and EXPERIENCE. | No application change required to resolve this documentation mismatch. |

**Guide result:** all five findings addressed; no outstanding guide blocker from this review. These are proposed contracts, not shipped accessibility fixes or approved layouts.

**Updated visual evidence:** current desktop and mobile fixture captures now exist for Forge, World and Overview under `mockups/`, along with exported desktop/mobile captures and HTML of the saved Forge Superdesign version 7. The fixture captures show actual Vue rendering with synthetic sample data; they do not verify real authentication, private campaign authorization, production/backend behavior, or complete interaction/state accessibility. Earlier references above to missing authenticated screenshots must be read in that narrower sense: real authentication verification remains outstanding, while source-screen fixture snapshots are now available. Tonight reference capture and consequential-state QA remain separate work.
