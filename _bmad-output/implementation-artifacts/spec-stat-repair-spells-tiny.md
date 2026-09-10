---
title: 'Stat-repair spells and tiny-creature valves'
type: 'bugfix'
created: '2026-09-10'
status: 'done'
baseline_commit: 'b963b16558991618faa05fce227cb1e2761b9118'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Live wave-1 stat repair clears DPR but still fails: four NPCs keep `spells` with no `identity.class` (systematic — the model preserves evocative spells and drops the coupling rule), and Boo (hamster, CR 1/4, hp 15) trips the HP-frail check the level-oriented recipe line does not cover.

**Approach:** Two pointed valve lines in the stat-repair prompt: spells need a class or must go, and CR floors plus a CR-0 diceless escape for harmless tiny creatures. Validator and contract unchanged.

## Boundaries & Constraints

**Always:** Prompt-only, inside `_DPR_RECIPES` or beside it; numbers quoted from `combat.CR_DPR`/`CR_HP` (CR 0 DPR 0-1 never unders, overs above 1.2; CR 0 HP 1-6, high never fails); existing recipe/valve lines keep their pinned substrings.

**Ask First:** Any validator leniency (decoupling spells from class, exempting frail HP) — owner verdict; not in this spec.

**Never:** Touch validator, bands, budget, record gate, chunking, or the output contract; restate the whole spell rules (one line each).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| SPELLS_VALVE | Any repair prompt | Line present: spells need identity.class (Monster never) — add a matching class or delete the array | N/A |
| TINY_VALVE | Any repair prompt | Line present: CR HP floors follow the same row (CR 1/4: 36+); harmless Tiny: CR 0 + zero dice (exempt, any hp passes, but any die over 1.2 DPR overs) | N/A |
| PIN_KEEP | Full prompt build | All previously pinned substrings still present byte-identical | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/statblocks.py:185-216` -- `_DPR_RECIPES`: append the two valve lines here (keeps one static block, keeps determinism).
- `backend/app/pipeline/combat.py:32-36,80-115` -- CR 0 rows (DPR 0-1, HP 1-6): read-only number source, never edited.
- `backend/app/pipeline/knowledge.py:437-473` -- `_check_spells` (class coupling, Monster ban): behavior the first line describes; not edited.
- `backend/tests/test_statblocks.py` -- append-only pins for both lines + PIN_KEEP (existing determinism test already guards shape; add explicit substring re-pins).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/pipeline/statblocks.py` -- spells-coupling valve + tiny-creature HP/CR-0 valve in `_DPR_RECIPES` -- the live repair keeps classless spells and frail tiny HP despite the rules prose.
- [x] `backend/tests/test_statblocks.py` -- append-only pins for both valve lines and prior pinned substrings -- prompt text is the contract.

**Acceptance Criteria:**

## Spec Change Log

## Design Notes

Wording (parser-checked): "spells need identity.class from the SRD list (never for Monster): set a class whose list holds every spell, or delete the spells array." / "HP floor follows the DPR row (CR 1/4: 36+, CR 1/2: 50+, CR 5: 131+); harmless Tiny creatures: CR 0 with zero dice anywhere (exempt from the power check, any hp passes — but a single die averages over 1.2 DPR and overs, so truly none)." CR HP lows: verify 1/2 (50) and 5 (131) against `_CR_HP` at implementation.

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_statblocks.py tests/test_build_in_pipeline.py tests/test_combat.py tests/test_generate_pipeline.py -q` -- expected: all pass, existing tests unmodified.
- `uv run --directory backend pytest -q` -- expected: full suite green.
- `uv run --directory backend ruff check app/pipeline/statblocks.py tests/test_statblocks.py && uv run --directory backend ruff format --check app/pipeline/statblocks.py tests/test_statblocks.py` -- expected: clean.

## Suggested Review Order

- Spells-coupling valve with the drop-uncovered third exit
  [`statblocks.py:214`](../../backend/app/pipeline/statblocks.py#L214)
- Tiny valve: CR floors, CR-0 escape, leveled-tiny exit
  [`statblocks.py:216`](../../backend/app/pipeline/statblocks.py#L216)
- End-to-end loop: classless-spells + frail-tiny repair succeeds
  [`test_build_in_pipeline.py:1628`](../../backend/tests/test_build_in_pipeline.py#L1628)
- Valve presence pins and table-number cross-checks
  [`test_statblocks.py:803`](../../backend/tests/test_statblocks.py#L803)
