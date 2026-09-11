---
title: 'Wave-2 orphan re-prompt'
type: 'bugfix'
created: '2026-09-10'
status: 'done'
baseline_commit: '542a68755d70d68be29c1a33b29f58d4c4314ff5'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Wave-2 structural violations (orphan with no edge to the core) hard-fail the wave with no repair — live: wave 1 committed 20 entities, wave 2 died over 'The Mother-Brain' carrying no core edge. Records, stats, and names each have a bounded pass; structure has none.

**Approach:** One bounded wave-2 re-prompt naming the orphans (owner verdict 2026-09-09, mirrors record/stat one-pass): re-emit the full wave with every new entity wired to the core; second miss fails loudly. Also fix the orphan message to use the wave's own ref prefix (N, not E).

## Boundaries & Constraints

**Always:** Owner verdict is the design (deferred-work.md 255-256): ONE re-prompt naming orphans, second miss fails loudly; re-emit goes through the same `_validate_subgraph` + name/record/stat gates as the first attempt (a re-emit can introduce new violations — they flow through the normal gates, same budget); ref prefix follows the wave (E wave 1, N wave 2).

**Ask First:** Auto-dropping orphan entities instead of re-prompting — owner already chose re-prompt; do not implement the alternative.

**Never:** Two re-prompts; partial wave commits (atomicity unchanged — wave 2 commits once or not at all); touch wave-1 orphan handling (fail-fast stays); weaken the anchor rule.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ORPHAN_RETRY | Wave-2 output with 1 orphan | One re-prompt naming it; healed output commits | N/A |
| SECOND_MISS | Re-emit still orphan | Job fails loudly naming orphans (wave 1 stays committed) | JobPayloadError |
| CLEAN_WAVE | No orphans | Zero extra calls — identical behavior to today | N/A |
| PREFIX | Wave-2 orphan message | Uses N prefix (`'X' (N2)`) | N/A |
| WAVE1_UNTOUCHED | Wave-1 orphans | Still immediate fail, E prefix | JobPayloadError |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/build_in.py:657-705` -- wave-2 run block: catch the orphan JobPayloadError from `_validate_subgraph`, re-prompt once, re-validate; second miss raises.
- `backend/app/pipeline/build_in.py:877-1020` -- `_validate_subgraph`: orphan detection + message; prefix per wave (N for wave 2); needs an orphan-specific signal (error subclass or structured return) so the caller retries only orphans, not every validation failure.
- `backend/tests/test_build_in_pipeline.py` -- wave-2 tests with core anchors; append-only ORPHAN_RETRY/SECOND_MISS/CLEAN_WAVE/PREFIX pins.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/pipeline/build_in.py` -- wave-2 orphan re-prompt (name orphans, full re-emit, one retry, N-prefix messages) -- the live wave-2 died with no repair while every other gate has one.
- [x] `backend/tests/test_build_in_pipeline.py` -- matrix pins (retry heals, second miss fails loud, clean wave = no extra call, N prefix) -- the retry contract is the deliverable.

**Acceptance Criteria:**
- Given a wave-2 output with one orphan, when the job runs, then the provider is re-called once naming the orphan and the healed wave commits.
- Given a re-emit that is still orphan, when the job runs, then it fails naming the orphans with wave 1 committed.
- Given the 4-file subset + full suite + ruff, when run, then green with no existing test modified.

## Spec Change Log

- 2026-09-11 — WAVE1_UNTOUCHED renegotiated (owner report, live job
  `01M27S237NNWFMZ2SHA7RG9383`, deployed beta): a 10-entity wave 1 died with
  `wave 1: orphan entity(ies) with no edge in the subgraph: 'Myconid Colony'
  (E7), 'Grymforge' (E8), 'Flaming Fist' (E9)` — wave 1 commits nothing, so
  the whole build was lost, not just a wave. The `Never: touch wave-1 orphan
  handling (fail-fast stays)` line was a carve-out for a case that had not
  been observed; the fix is the SAME owner-approved mechanism (one bounded
  re-emit naming the orphans, second miss fails loudly), applied to wave 1:
  `_Wave2OrphanError` → `_OrphanRetryError` (carries `wave`), the retry prompt
  and ref prefix are per wave (E for wave 1, N + core label for wave 2), and
  the shared `_orphan_reemit` helper owns the call/parse/cancel mechanics. The
  matrix row WAVE1_UNTOUCHED is thereby superseded by ORPHAN_RETRY on wave 1;
  every other boundary (one re-prompt only, no partial commits, no auto-drop,
  other rejection kinds immediate) is unchanged. Owner veto restores fail-fast
  by deleting the wave-1 `except _OrphanRetryError` block.

## Design Notes

Retry prompt: wave-2 base prompt + `PREVIOUS RESPONSE ORPHANS: <'name' (Npos), ...> — every new entity MUST have >= 1 edge to a CORE entity (C0..Ck); re-emit the full {"entities","edges"} object with the refs exactly as given.` Only orphan failures retry — any other `_validate_subgraph` rejection (bad ref, kind, edge type, self-loop) still fails immediately. Signal design left to the implementer (subclass preferred over message-sniffing): keep it inside build_in.py, no new exported API.

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_build_in_pipeline.py tests/test_statblocks.py tests/test_combat.py tests/test_generate_pipeline.py -q` -- expected: all pass, existing tests unmodified.
- `uv run --directory backend pytest -q` -- expected: full suite green.
- `uv run --directory backend ruff check app/pipeline/build_in.py tests/test_build_in_pipeline.py && uv run --directory backend ruff format --check app/pipeline/build_in.py tests/test_build_in_pipeline.py` -- expected: clean.

## Suggested Review Order

- Retry catch with cancel poll, entity-stability guard, re-validation
  [`build_in.py:723`](../../backend/app/pipeline/build_in.py#L723)
- Retry prompt builder plus entity-name helpers and error signal
  [`build_in.py:944`](../../backend/app/pipeline/build_in.py#L944)
- Heal/commit pin with the orphans marker on the retry call
  [`test_build_in_pipeline.py:2158`](../../backend/tests/test_build_in_pipeline.py#L2158)
- Budget, cancel, non-orphan, and gate-fall-through pins
  [`test_build_in_pipeline.py:2321`](../../backend/tests/test_build_in_pipeline.py#L2321)
