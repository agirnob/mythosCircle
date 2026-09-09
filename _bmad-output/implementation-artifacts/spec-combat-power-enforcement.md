---
title: 'Combat power enforcement'
type: 'feature'
created: '2026-09-09'
status: 'done'
baseline_commit: '6344ccd970506895cefa1121d268cedab10ce3fb'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `combat.py` (d94ffb5) audits DPR-vs-CR but never rejects, and undercounts Multiattack blocks ("makes three attacks" parses 0 dice); Void-Stalker-class blocks sail through every gate.

**Approach:** Wire the audit into `validate_stat_block` as violations (repair nudge, then fail), teach `round_dpr` Multiattack, adopt full DMG bands, add an HP-frail check, recalibrate prompt scaling text.

## Boundaries & Constraints

**Always:** Pure/deterministic violations; abstain silently on band miss (shape checks already flag bad level/CR) and on zero parseable damage anywhere (non-combatants exempt); level N keys CR N band; enforcement inherits everywhere `validate_stat_block` runs.

**Ask First:** None open — enforcement, scope (all characters), crude parsing, recalibration decided 2026-09-09.

**Never:** Score vulnerabilities/condition immunities (RAW: no CR effect); redesign legendary modeling; fail HP-above-band (defensive averaging, not a bug); touch the audit CLI.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior |
|----------|--------------|---------------------------|
| UNDER_FLAG | CR 18, best action 3d10+6, no Multiattack (~22.5 vs 111-116) | under-powered violation with DPR vs band |
| OVER_FLAG | Level 5 dealing ~60 DPR (band 33-38) | over-powered violation |
| ON_TARGET | CR 18 at ~100 DPR | no power violation |
| MULTIATTACK | "Multiattack: makes three Claw attacks" + Claw 3d10+6 | DPR = 3×22.5 = 67.5 |
| NO_DICE_ABSTAIN | Scholar, no damage expressions | no power violation |
| NO_BAND_ABSTAIN | level 99 | no power violation (shape errors fire) |
| HP_FRAIL | CR 18 with 40 HP | frail violation |
| HP_TANK_OK | CR 18 with 500 HP | no power violation |
| REPAIR_LOOP | build-in flagged, repair healthy | commits; still mismatched → job fails |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/combat.py` -- owns power math; reuse parser/`CR_DPR`/`LEGENDARY_EXTRA_ACTIONS`/ratios; extend `round_dpr` (Multiattack), full DMG DPR bands, HP table + frail predicate.
- `backend/app/pipeline/knowledge.py:475-560` -- `_check_power` in `validate_stat_block` after spells; band lookup uses raw level/cr values, miss → abstain.
- `backend/app/pipeline/statblocks.py:80-137` -- recalibrate CHALLENGE SCALING prose to DMG; repair prompt unchanged (violations flow via `:174-179`).
- `backend/tests/test_combat.py` -- extend 16-test suite (matrix rows, CR 18/21/30 pins).
- `backend/tests/test_build_in_pipeline.py` -- one repair-loop test.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/pipeline/combat.py` -- full DMG DPR bands, Multiattack-aware `round_dpr`, HP bands + frail predicate.
- [x] `backend/app/pipeline/knowledge.py` -- `_check_power` with abstentions, deterministic strings carrying DPR-vs-band numbers.
- [x] `backend/app/pipeline/statblocks.py` -- CHALLENGE SCALING prose to DMG values.
- [x] `backend/tests/test_combat.py` + `backend/tests/test_build_in_pipeline.py` -- matrix edge cases + repair-loop test.

**Acceptance Criteria:**
- Given a Void-Stalker-shaped CR 18 block, when validated, then under-powered fires naming DPR vs 111-116.
- Given a Multiattack block, when audited, then DPR = count × strongest other action.
- Given level-99 or diceless blocks, when validated, then no power violation joins the verdict.
- Given a flagged wave, when repair heals it, then commit; when still mismatched, then fail.
- Given the full suite + lint + typecheck, when run, then green.
## Design Notes

Multiattack: name contains "multiattack" (ci); count = first integer/number-word (one-ten)/"twice"=2 else 2; contributes count × strongest other damaging action (0 when none). Ratios stay 0.8/1.2. HP fails low-only: `hp < 0.5 × band low`.

DMG HP bands (lo-hi): 0:1-6, 1/8:7-35, 1/4:36-49, 1/2:50-70, 1:71-85, 2:86-100, 3:101-115, 4:116-130, 5:131-145, 6:146-160, 7:161-175, 8:176-190, 9:191-205, 10:206-220, 11:221-235, 12:236-250, 13:251-265, 14:266-280, 15:281-295, 16:296-310, 17:311-325, 18:326-340, 19:341-355, 20:356-375, 21:376-400, 22:401-425, 23:426-445, 24:446-465, 25:466-485, 26:486-505, 27:506-525, 28:526-545, 29:546-565, 30:566+. DPR bands: same rows as scouted (CR 18: 111-116; CR 21: 141-158; CR 30: 303+).

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_combat.py tests/test_build_in_pipeline.py tests/test_statblocks.py -q` -- expected: all pass.
- `uv run --directory backend pytest -q` -- expected: full suite green.
- `make lint && make typecheck` -- expected: clean.

## Suggested Review Order

**Enforcement entry point**

- Power verdicts become repair nudge then job failure here
  [`knowledge.py:475`](../../backend/app/pipeline/knowledge.py#L475)

**Power math**

- Multiattack count ignores to-hit bonuses and dice, clamped to 10
  [`combat.py:201`](../../backend/app/pipeline/combat.py#L201)
- Routine excluded from its own multiplier; legendary keys on others
  [`combat.py:282`](../../backend/app/pipeline/combat.py#L282)
- Recharge/per-day averaged over 3 rounds, exposed not silent
  [`combat.py:258`](../../backend/app/pipeline/combat.py#L258)
- Exact-type band guards so True and 5.0 abstain
  [`combat.py:342`](../../backend/app/pipeline/combat.py#L342)

**Prompt contract**

- DMG bands plus adjustments, interpolation, guidance-only AC/scores
  [`statblocks.py:109`](../../backend/app/pipeline/statblocks.py#L109)

**Tests**

- Parser, audit, validation, and repair-loop pins for every matrix row
  [`test_combat.py:1`](../../backend/tests/test_combat.py#L1)
  [`test_statblocks.py:500`](../../backend/tests/test_statblocks.py#L500)
  [`test_build_in_pipeline.py:1670`](../../backend/tests/test_build_in_pipeline.py#L1670)
