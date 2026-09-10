---
title: 'Stat-repair challenge-number valve'
type: 'bugfix'
created: '2026-09-10'
status: 'done'
baseline_commit: 'e6312618a0a87180dedbff2fd1aa8fa56b8f702b'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Live wave-1 stat repair clears spells, DPR, and HP but still fails on two Monsters (Red Wizard, Boo) whose identity omits `cr` entirely — the model writes role+race and stops, and the one repair pass preserves the omission.

**Approach:** One pointed valve line in the stat-repair prompt: the challenge number is never omittable — Monster carries bare-integer `cr`, NPC/BBEG bare-integer `level`, never the display strings. Validator unchanged.

## Boundaries & Constraints

**Always:** Prompt-only, inside `_DPR_RECIPES`; bare-integer rule (write `8`, never `"CR 8"`; write `5`, never `"level 5"`); fractional CRs quoted exactly (`1/8`, `1/4`, `1/2`); previously pinned substrings byte-identical.

**Ask First:** Any validator defaulting (e.g. inferring cr from the record) — owner verdict; not in this spec.

**Never:** Touch validator, bands, budget, gates, chunking, or the output contract.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| CR_VALVE | Any repair prompt | Line present: Monster `cr` required (bare int 0-30 or 1/8, 1/4, 1/2); NPC/BBEG `level` required (bare int 1-20); never display strings, never omit, never the other role's key | N/A |
| PIN_KEEP | Full prompt build | All previously pinned substrings still present | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/statblocks.py:185-221` -- `_DPR_RECIPES`: append the valve line; determinism unchanged.
- `backend/app/pipeline/knowledge.py:545-557` -- `_valid_cr` + level/cr exclusivity: behavior described; not edited.
- `backend/tests/test_statblocks.py` -- append-only presence pin + PIN_KEEP.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/pipeline/statblocks.py` -- challenge-number valve in `_DPR_RECIPES` -- the live repair omits Monster `cr` twice while everything else validates.
- [x] `backend/tests/test_statblocks.py` -- append-only pins -- prompt text is the contract.

**Acceptance Criteria:**
- Given any issue list, when the repair prompt builds, then it contains the challenge-number line.
- Given the 4-file subset + ruff, when run, then green with no existing test modified.

## Design Notes

Wording: "Challenge number is REQUIRED, never omitted: Monster carries `cr` as a bare integer 0-30 (write 8, never \"CR 8\"; fractions only 1/8, 1/4, 1/2); NPC/BBEG carry `level` as a bare integer 1-20 (write 5, never \"level 5\"); never carry the other role's key."

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_statblocks.py tests/test_build_in_pipeline.py tests/test_combat.py tests/test_generate_pipeline.py -q` -- expected: all pass, existing tests unmodified.
- `uv run --directory backend ruff check app/pipeline/statblocks.py tests/test_statblocks.py && uv run --directory backend ruff format --check app/pipeline/statblocks.py tests/test_statblocks.py` -- expected: clean.

## Suggested Review Order

- Reworded valve: identity-anchored, int-vs-string, display-text split
  [`statblocks.py:221`](../../backend/app/pipeline/statblocks.py#L221)
- Presence and prior-pin assertions on the reworded line
  [`test_statblocks.py:861`](../../backend/tests/test_statblocks.py#L861)
- Round-trip: missing cr fails, cr 8 validates
  [`test_statblocks.py:894`](../../backend/tests/test_statblocks.py#L894)
