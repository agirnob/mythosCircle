---
title: 'Surgical-to-frozen repair sequence (strip drift, per-entity retry, frozen anchor)'
ntype: 'feature'
created: '2026-09-11'
status: 'ready-for-dev'
review_loop_iteration: 0
baseline_commit: '7c983b9'
context:
- /home/main/Projects/mythosCircle/backend/app/pipeline/build_in.py
- /home/main/Projects/mythosCircle/backend/app/pipeline/statblocks.py
- /home/main/Projects/mythosCircle/backend/app/pipeline/budget.py
- /home/main/Projects/mythosCircle/backend/tests/test_build_in_pipeline.py
- /home/main/Projects/mythosCircle/backend/tests/test_statblocks.py
- /home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/ladder-qwen3-2026-09-11/runlog.md
- /home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/ladder-qwen3-2026-09-11/rung-10.json
---

# Repair sequence: strip drift, per-entity retry, frozen anchor

## Why

Eight rung-10 attempts on gemma-4-26B: jobs 0/8, wave-1 commits 4/5 on current
code. The failure taxonomy is stable — F1 stat arithmetic (sometimes converges),
F2 repair drift (our own repairs add sections, drop links, rename entities),
F3 ref discipline (dead since the schema wire, 4/4 clean), F4 anchor wiring
(0/4 wave-2 repairs converge). F2 is self-inflicted and goes first; F1 gets
isolation plus one more attempt; F4 gets a repair the model cannot rename its
way out of. Attempt 8 is the existence proof for step 3: the re-emit wired
everyone and died on names alone.

## Capabilities

1. **Surgical stat-repair merge (strip).** Repair output merges ONLY sections
   inside each issue's `scope_for_violations` set; out-of-scope keys are dropped
   at merge time, and the existing breach warning keeps firing (telemetry is
   kept, drift is not). Success: an attempt-8-shaped repair (converging numbers
   riding with `traits`/`spells` additions) commits the numbers, drops the
   riders, still logs the breach paths.
2. **Per-entity stat repair with a third attempt.** Each repair call carries ONE
   failing entity (prompt, parse, and merge by ref), mirroring the
   `RECORD_REPAIR_CHUNK_SIZE` precedent at `build_in.py:151`; the attempt loop
   extends `(1, 2)` to `(1, 2, 3)` with attempt-3 re-reading attempt-2's block
   plus surviving violations. Worst-case call accounting must fit the
   `max_llm_calls` enqueue default at 10-scale (implementer computes both and
   raises the default if short). Success: rung-10 wave-1 commits hold >= 4/5,
   terminal stat-death sets shrink or hold, drift surface per call is one block.
3. **Entities-frozen anchor repair.** The wave-2 full re-emit
   (`_orphan_reemit`, `build_in.py:1209`) is replaced by an edges-only repair:
   the prompt carries frozen rosters (new entities as ref+name+kind, core as
   C-label+name), the model returns `{"edges": [...]}` ONLY under an edges-only
   schema (src/dst enums of the known N-refs plus C-labels, type enum from
   `store.EDGE_TYPES`, `additionalProperties: false`), and the same anchor
   check validates. Still-orphan fails exactly as today; names and refs cannot
   change because they are not emitted, so the drop guard retires with the
   re-emit it guarded. Success: the attempt-8 shape (wired-but-renamed) commits;
   rename-class deaths go to zero by construction.

## Constraints

- Strip (step 1), never reject-then-retry: attempts 7-8 show convergence riding
  with drift — rejecting the whole repair for rider keys would fail repairs
  whose numbers converged.
- One map for scope: step 1 strips with `scope_for_violations`
  (`statblocks.py:375`), the same map the prompt and breach log share — never a
  second literal.
- Record repair keeps position-pinning (exact flagged refs only,
  `_parse_record_repair_output`): it is already surgical by position, out of
  scope for stripping.
- Step 3 keeps single-bounded-repair semantics (one edges-only pass, no
  escalation loop) and the cancel-race rule (return None when the job stopped).
- Clean cutover each step: the full re-emit path, its prompt builder, and the
  drop guard go when step 3 lands — no parallel anchor machinery. The
  "a third pass does not exist" docstring (`build_in.py:254-257`) goes with
  step 2.
- Each step lands only on its predecessor green: step 2 builds on measured
  step-1 ladder evidence, step 3 on step-2's.

## Non-goals

- Per-entity full generation (roster pass + one-entity-per-call): the held
  fallback if F1 still dominates after step 2, not this spec.
- Model swap; prompt prose redesign beyond roster/excerpt shaping; 25/50-scale
  budget policy (revisit from ladder evidence); record-repair chunk changes.
- Regenerate-path repairs stay plain-settings (Never list holds).

## Code Map

- Step 1: `build_in.py:293-296` (breach log then `apply_stat_repairs` merge
  inside `_enforce_stat_blocks`, `build_in.py:233`); strip between the two
  using `scope_for_violations` (`statblocks.py:375`) over `_SCOPE_PREFIXES`
  (`statblocks.py:351`); merge target `apply_stat_repairs`
  (`statblocks.py:686`). Breach tests in `test_build_in_pipeline.py` /
  `test_statblocks.py` gain stripped-merge assertions.
- Step 2: attempt loop `build_in.py:276` (`for attempt in (1, 2)`), repair
  prompt builder `build_stat_repair_prompt` (`statblocks.py:414`, attempt
  semantics at :418), per-entity chunking after the `RECORD_REPAIR_CHUNK_SIZE`
  precedent (`build_in.py:151,728-732`); budget fit vs `CallBudget`
  (`budget.py:27`) and the `max_llm_calls` enqueue default.
- Step 3: `_orphan_reemit` (`build_in.py:1209`),
  `_build_orphan_retry_prompt` (`build_in.py:1238`), drop guard
  `_raise_on_reemit_entity_change` (`build_in.py:1271`, called at :896),
  anchor check (`_validate_subgraph`, `build_in.py:1373`, orphans at
  :1521-1529), core C-label mapping (`build_in.py:1482`, prompt labels at
  :1061, :1115-1116), wave schema precedent `build_wave_schema`
  (`build_in.py:1128`), wave-2 call sequence (`build_in.py:868-913`).

## Verification

- Unit (each step): strip semantics (converging numbers kept, rider sections
  dropped, breach still logged); per-entity merge-by-ref (sibling blocks
  untouched, attempt-3 prompt shape); power-discipline lines pinned;
  edges-only parse plus anchor validation (frozen-entity rename
  unrepresentable, still-orphan fails, cancel returns None). Existing
  suites stay green (`pytest -q`, `make lint`, `make typecheck`);
  breach/drop-guard tests are rewritten, never re-pinned.
- Ladder gate (each step, identical `rung-10.json` on gemma): baseline is
  wave-1 commits 4/5, jobs 0/8, breach classes as logged. Step 1 greens on
  commits held + strips observed in telemetry; step 2 on terminal stat-death
  sets shrunk-or-held; step 3 on a wiring-green job (or a death naming only
  stats, never anchors/renames). Next step builds only off its gate evidence.

## Deviation (owner-approved 2026-09-11, ladder attempts 10-11)

- Step 2's gate failed twice in one family (repair overshoot past the
  band top ×3, level-breakage ×2, level-escalation ×1), so prompt
  steering came into scope despite the Non-goal: the repair prompt
  carries POWER DISCIPLINE (aim mid-band, never above the top;
  move DPR with damage, never by raising level; level violations fixed
  with the level field alone). Note the prompt already sanctioned
  level edits ("You MAY lower identity.level",
  "declare an identity.level your damage supports") — the model was
  obeying, not drifting, so the strip correctly never saw it.

## Deviation (owner verdict 2026-09-12, ladder attempt 17)

- Over-powered is fine and commits — no trim, no fail. The validator's
  over-powered branch is deleted (repairs never chase it); `power:
  {dpr, band, verdict}` is stamped deterministically at canonicalize
  (over-powered verdict only, so on-target blocks stay byte-identical
  and repair prompts stay lean); the repair prompt's POWER DISCIPLINE
  states the new truth (above-band commits declared, still aim
  mid-band); `StatBlock.vue` renders the flag for the DM. Retires the
  overshoot death class (ladder DPR 60, 31.5, 189) and resolves the
  ledger's over-powered entry. Forge export omits the key by
  construction (mapper picks known fields); sheets render it
  generically.

## Design Notes

- Attempt ledger: runlog attempts 5-8 (same directory as `rung-10.json`).
  Compressed evidence: repairs complete the block instead of fixing listed
  numbers (attempts 5-7); EDIT SCOPE partially bites (attempt 6-8 breaches
  shrank to skills/traits-only); re-emit renames instead of wiring (3 of 4
  wave-2 repairs) or wires-but-renames (attempt 8, killed by the guard it
  needed).
- Harness note (operator action, not code): the ladder runner tees responses
  but no prompts — store prompt text beside each call file before the step-1
  gate run, or post-hoc analysis stays blind.
- Step order is load-bearing: stripping first isolates step-2 measurement
  (attempt-3 convergence must be credited to retries, not to accepted drift);
  per-entity second isolates step-3 measurement (anchor deaths must not hide
  behind stat drift).

## Suggested Review Order

1. `statblocks.py` scope map + merge (`_SCOPE_PREFIXES`, `scope_for_violations`,
   `apply_stat_repairs`) — the strip's single map.
2. `build_in.py` `_enforce_stat_blocks` — loop, chunking, breach-then-merge
   order, budget fit.
3. `build_in.py` wave-2 sequence + anchor repair — edges-only schema, frozen
   rosters, guard retirement.
4. Tests — stripped-merge pins, per-entity pins, edges-only pins.
5. Runlog gate entries — per-step evidence before the next step builds.
