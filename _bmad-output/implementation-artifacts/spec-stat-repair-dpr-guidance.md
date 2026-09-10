---
title: 'Stat-repair DPR guidance'
type: 'bugfix'
created: '2026-09-10'
status: 'done'
baseline_commit: 'f42c79286a37a97cb1935a3c124ce6ac0cf9e6d7'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Wave-1 build-ins fail after the stat repair pass with `under-powered` on every character (live: 14/14 weak, e.g. 16.5 vs 93-98 at level 15), because a MISSING block's repair prompt carries no per-character DPR target and no damage recipe — the one bounded pass is spent inventing weak single-attack blocks at flavorful high levels.

**Approach:** Make `build_stat_repair_prompt` power-aware for MISSING blocks too: echo each character's record target band, add tiered damage recipes with shown arithmetic, and explicitly allow lowering `identity.level` (or going diceless) since record `level_cr` text is display-only.

## Boundaries & Constraints

**Always:** Prompt-only change — validator bands, ratios, one-pass AR25 semantics, output contract, and prompt determinism all unchanged; recipes verified against the parser's own math (Multiattack = count x strongest other, routine excluded; recharge/per-day averages 1/3; save-half 0.75x).

**Ask First:** Any validator leniency (lowering NPC bands, second repair pass, auto-lowering levels mechanically) — needs owner verdict; this spec deliberately does not touch enforcement.

**Never:** Change `validate_stat_block`, `combat.py` math, record-gate prompts, or the repair-loop budget; redesign legendary modeling; invent new edge/role vocabulary.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| MISSING_WITH_LEVEL | No stat_block, record level_cr "level 20" | Prompt carries `record target: ... -> hit DPR band 123-140` plus recipes | N/A |
| MISSING_NO_LEVEL | No stat_block, level_cr blank/garbled | No target line; generic recipes + "declare a level your damage supports" | N/A |
| MONSTER_TARGET | Record role Monster, level_cr "CR 5" | Prompt carries CR 5 band 33-38 | N/A |
| STILL_WEAK | Power violation persists post-repair | Existing fail message unchanged (recipes only aid the repair) | N/A |
| DETERMINISM | Same issues twice | Byte-identical prompt (existing test keeps passing) | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/statblocks.py:172-217` -- `build_stat_repair_prompt`: the only function changed; gains per-issue target lines + static recipe block. Pure/deterministic of `issues` — keep it so.
- `backend/app/pipeline/statblocks.py:80-150` -- `stat_block_rules_text`: reused as-is, not edited.
- `backend/app/pipeline/combat.py:25-70` -- `CR_DPR` band source for target lines; read-only, never import for mutation.
- `backend/app/pipeline/knowledge.py:475-517` -- `_check_power` + exemption rule (diceless abstains): the behavior the new prompt lines describe; not edited.
- `backend/tests/test_statblocks.py:344-348` -- determinism/MISSING pins: must keep passing unmodified.
- `backend/tests/test_build_in_pipeline.py:1670-1727` -- repair-loop pins (`"under-powered" in calls[1]`): must keep passing unmodified.

## Tasks & Acceptance

- [x] `backend/app/pipeline/statblocks.py` -- per-character record target lines + static DPR recipe block + level-lowering/diceless escape valves in `build_stat_repair_prompt` -- the MISSING-block repair currently flies blind, so one weak pass burns the whole wave.
- [x] `backend/tests/test_statblocks.py` -- unit-test the matrix rows (target line present/absent, recipe lines present, determinism) -- prompt text is the contract the small model sees.

**Acceptance Criteria:**
- Given a MISSING block with record level_cr "level 5", when the repair prompt builds, then it names band 33-38 for that ref.
- Given a MISSING block with blank level_cr, when the repair prompt builds, then no target line appears but recipes do.
- Given the full suite + lint + typecheck, when run, then green with no existing test modified.

## Spec Change Log

## Design Notes

Recipes (parser-checked; routine text carries ONLY the count, never dice):
- averages: d4 2.5, d6 3.5, d8 4.5, d10 5.5, d12 6.5; flat +N adds in full.
- level 1 (9-14): single `2d6+3` (~10). level 5 (33-38): Multiattack `makes three attacks` + `2d8+4` (~13) x3 = ~39.
- level 10 (63-68): Multiattack three + `4d10+5` (~27) x3 = ~81 (tolerance reaches 81.6). level 20 (123-140): Multiattack `makes four attacks` + `5d10+6` (~33.5) x4 = ~134.
- escape valves: lower `identity.level` to a band the damage satisfies (record text is display-only; export derives from numerics); true non-combatants write zero dice anywhere and are exempt — one weak attack is worse than none.

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_statblocks.py tests/test_build_in_pipeline.py tests/test_combat.py -q` -- expected: all pass, existing tests unmodified.
- `uv run --directory backend pytest -q` -- expected: full suite green.
- `make lint && make typecheck` -- expected: clean.

## Suggested Review Order

- Repair mirror now forwards the staged record into the prompt
  [`generate.py:176`](../../backend/app/pipeline/generate.py#L176)
- Per-character DPR target parsed from the display-only record
  [`statblocks.py:218`](../../backend/app/pipeline/statblocks.py#L218)
- Static parser-checked recipes plus HP/legendary/AoE valves
  [`statblocks.py:185`](../../backend/app/pipeline/statblocks.py#L185)
- Prompt assembly: target line per issue plus shared recipe block
  [`statblocks.py:304`](../../backend/app/pipeline/statblocks.py#L304)
- Matrix pins: targets, omissions, hardening, determinism
  [`test_statblocks.py:680`](../../backend/tests/test_statblocks.py#L680)
- Generate-path pin: repair prompt names the level-5 band
  [`test_generate_pipeline.py:1712`](../../backend/tests/test_generate_pipeline.py#L1712)
