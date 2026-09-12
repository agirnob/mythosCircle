# Ladder run log — gemma-4-26B (Qwen3.5 was not actually loaded; owner chose gemma)

Stack: scratch DB `/tmp/mythos-ladder/ladder.db` (fresh per attempt), runner
`/tmp/mythos-ladder/run_rung.py` (throwaway, tee'd provider), endpoint
`http://127.0.0.1:8888/v1`, model `unsloth/gemma-4-26B-A4B-it-qat-GGUF`,
thinking=false (config), timeout 900, max_tokens 65536, budget 64 (default).
Campaign: "The Drowned Harbor" / Grimdark / salt-drowned custom lore.

## Rung 10 — attempt 1 (job 01M28G7775WTEA21S1HHYB8KMA) — FAILED, 4 calls

- Call 1 (wave 1, schema=True): 20,048-char prompt → 17,180-char response,
  32.5s. 10/10 entities, all named, edges present — parsed first try.
- Call 2 (record repair, schema=False): healed; wave committed to gates.
- Calls 3+4 (stat repair passes 1+2): made things worse (see below).
- Terminal: `E4 Ilsa Vane: traits entries must have a string 'description'`
  + `E8 Salt Abbess: over-powered for level 12: DPR 156.0 vs 75-80`.
- Repair-inflation evidence (from tee'd responses): wave E8 was `2d8+5`
  (sane); repair pass 1 rewrote it to `2d8+69` and pass 2 left it untouched.
  Same pattern on E5 (`1d6+?` → `+18` → `+3`), E6 (`1d8+18`, `4d8+26` →
  corrected in pass 2), E7 (`1d6+22` unchanged both passes — still passed
  its band), E9 (`+24` → `+9`). E4 traits: wave `null` → pass 1
  `["Sneak Attack"]` → pass 2 `[{"name": "Sneak Attack"}]` — chased the
  format across both passes into the exact failing shape (wording is
  unrepairable by design, so the job was doomed once wave shipped null).

## Rung 10 — attempt 2, identical payload (job 01M28GAWV5VZKCBTTDATEFHVMJ) — FAILED, 1 call

- Call 1 (wave 1, schema=True): 20,048-char prompt → 31,977-char response,
  65.4s. Model rambled (nearly 2x attempt 1).
- Terminal: `wave 1: entity 10 ref must be E10, got 'E0'` — an 11th entity
  with a restarted ref. No repair path exists for bad refs (validator
  rejects; not an orphan case).

## Standing verdicts after rung 10

- Schema wire: 4/4 schema'd calls (3 waves + 1 orphan re-emit) parsed
  first try — the historical wave-1 syntax failure class is gone on this
  backend. Live grammar proof: PASS (compat), calibration: N/A.
- Calibration: 0/3 converge. Failure modes are post-parse now (power
  arithmetic, wording, refs, orphan wiring) — the open owner decisions
  (trim vs lower-level vs leave; repair-prompt steering) own the next
  step, not more retries.
- Rung gate holds: 25 and 50 do NOT run off an uncommitted rung 10.
- Housekeeping: attempts 1-2 tee'd calls were wiped by fresh-dir resets;
  only `/tmp/mythos-ladder/calls-a3/` survives — per-attempt dirs from
  now on.


## Rung 10 — attempt 3, identical payload — FAILED, 2 calls

- Call 1 (wave 1, schema=True): 20,048-char prompt → 17,615-char response,
  29.9s. Structurally clean, but `The Salt Abbess` (E8) unwired.
- Call 2 (orphan re-emit, schema=True): 20,334-char prompt → 17,093-char
  response, 31.8s. Re-emit carried the schema; E8 still orphaned.
- Terminal: still-orphan re-emit fails with nothing committed. Tee'd
  calls in `/tmp/mythos-ladder/calls-a3/`.

## Pattern after 3 attempts: never the same death twice, never a commit

- Attempt 1: power arithmetic + wording (repair-inflated).
- Attempt 2: ref discipline (11th entity, restarted ref).
- Attempt 3: orphan wiring (E8 unwired twice in a row).
- E8 (Salt Abbess) is the recurring problem child: over-powered in
  attempt 1, unwired in attempt 3, clean in attempt 4.

## Rung 10 — attempt 4, new code (edgeless + enum + scoped repair) — wave 1 COMMITTED

- 5 calls, all schema-carrying, all parsed first try (no JSON retries).
  Wave 1 (10/10: 6 chars, 2 places, 2 factions) committed revision 1 with
  12 edges; every entity wired this roll, so the edgeless-live case did
  NOT trigger — edgeless proof stays at unit level (validator + 1-call
  job + log assert, all green).
- Breach telemetry fired live: 6 warnings (E4–E9 traits touched outside
  power/skills scopes), each with job id, wave 1, attempt 1 — the exact
  next-decision data the log-only design promised. All six blocks still
  passed the re-audit (traits fixes were well-shaped — no E4-class death
  this roll; suggestive for the repair schema, N=1).
- Wave 2 died on the kept anchor rule: Hedd unwired → re-emit renamed him
  ('Scavenger' → 'Salvager') instead of wiring → drop guard caught it
  with the new wave-2-specific message, wave 1 staying committed
  (documented resilience). Job failed, world kept its core.
- Verification verdict: no-regression end-to-end (commit, repairs,
  anchor, drop guard, telemetry all exercised live); edgeless-commit
  live observation still open and left to chance rolls, not chased.
  Tee'd calls in `/tmp/mythos-ladder/calls-a4/`.

## Rung 10 — attempt 5, new code — FAILED, 3 calls

- Pass 1 repair touched traits/features outside power+skills scopes
  (logged); pass 2 added spellcasting/spells/features while dropping the
  identity.class link — terminal: E5/E6/E8 `spells require identity.class`
  plus remaining under-power (E5 DPR 14 vs 21-26, E8 DPR 42 vs 75-80).
- New pattern: repairs complete the block (add sections) instead of
  fixing listed numbers — EDIT SCOPE lines do not constrain the model,
  but every instance was logged with paths. Tee'd calls wiped by the
  next fresh-dir reset (housekeeping miss — a5 dir was reused).

## Rung 10 — attempt 6, new code — FAILED, 5 calls (wave 1 committed)

- Wave 1 committed again (pass-2 breaches down to skills-only drift);
  wave 2 hit the anchor orphan, and the re-emit again renamed instead of
  wiring — two entities this time ('Hedd the Scavenger'→'Hedd', 'The Iron
  Husk'→'The Driftwood Hulk'). Drop guard caught both, wave 1 committed.
- Systematic, not flake: the re-emit renames the orphan rather than
  wiring it, 2/2 wave-2 repairs. Tee'd calls in
  `/tmp/mythos-ladder/calls-a6/`.

## Rung 10 — attempt 7, new code — FAILED, 5 calls (wave 1 committed)

- Wave 1 committed (small pass-2 repair converged twice now); wave 2
  orphaned N2 Quarantine Hulk, re-emit left it unwired again (no rename
  this time — plain still-orphan second miss). Wave-2 repairs on gemma:
  0/3 converge (rename, rename×2, still-orphan).
- Cap reached (attempts 5-7): wave-1 commits 3/4 on new code, jobs 0/4.
  The wall is the wave-2 anchor re-emit, never wave 1 anymore.

Standing owner rules for the ladder (2026-09-11): after every attempt,
report per wave in basic form (what failed) plus a verdict plus a
suggestion. Attempt-7 review proposed an edges-only anchor repair
(entities frozen, src/dst+type schema enums of known IDs) to replace
the full re-emit; owner chose to keep rolling identical payloads
instead — variance as the lever, attempts 8+ continue on gemma.

- Report format (owner rule): per attempt, per wave, per entity — which
  entity failed which gate with numbers — plus verdict plus suggestion.
- ## Rung 10 — attempt 8, new code — FAILED, 6 calls (wave 1 committed)
- Wave 1: E5 Ssketh record repair (call-02, full record merged); stat
  pass 1 flagged E4 Ilsa Vane L4 (skills×4 strings, DPR 6.5 vs 27-32, HP
  31 frail), E6 Wren L7 (skills×2, DPR 9 vs 45-50, HP 45), E8 Abbess L10
  (skills×2, DPR 13 vs 63-68, HP 78); pass 2 converged E6 (DPR 28) + E8
  (DPR 39) with traits-only drift. Committed 10/10 + 15 edges; E0-E3
  places/factions all carried text, E7/E9 clean first try.
- Wave 2: call-05 emitted N0 'Driftwood Hulk' place, N1 'Hedd the
  Salvager', N2 'The Knocker' — orphan was Hedd (N1, N-only edges; N0
  had C7-inbound, N2 had C3-outbound). Re-emit wired everyone
  (N1→C7, N3→C3) but renamed the hulk ('Leaking Hulk') and renumbered
  refs (Knocker N2→N3) — drop guard killed a structurally good answer.
  Wave-2 anchor repairs on gemma: 0/4. Lesson: entities-frozen repair
  would have greened this exact job. Tee'd calls in
  `/tmp/mythos-ladder/calls-a8/` (responses only — runner stores no
  prompts; counts + seconds + schema flag alongside).

## Rung 10 — attempt 9, step-1 code — SUCCEEDED, 8 calls (FIRST GREEN JOB)

- Wave 1: call-01 clean parse; record gate clean (no call-02 repair
  needed); stat pass 1 flagged E4/E5/E6/E7/E8/E9 (string-skills + DPR
  5.5-30 across the board, worst wave-1 yet); pass 2 converged to E5
  (illegal Guiding Bolt) + E9 (pass-1 overshoot to DPR 60) and both
  cleared. Committed 10/10 + 14 edges. Every merge stripped riders
  (traits/spells additions logged at all 8 breach lines) — committed
  blocks carry no `traits` key anywhere (DB-verified), spells only on
  legal casters.
- Wave 2: first attempt needed the re-emit (behaved this time — names
  kept), then a record repair + 2 stat passes (E1 Dagger-dup + DPR,
  E2 illegal class + DPR) converged. Committed N0 'The Iron Hulk'
  place, N1 'Hedd the Scavenger', N2 'The Hull-Knocker' + 5 edges.
  Total 13 entities, 19 edges. Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-b1/`.
- Verdict: step-1 gate GREEN (commits held at 5/6 wave-1 streak, strips
  observed in telemetry AND in committed rows). Green partly variance
  (anchor wired first-try-equivalent via a behaving re-emit), but the
  strip's fingerprint is in the DB. Step 2 unlocked.

## Rung 10 — attempt 10, step-2 code — FAILED, 14 calls (wave-1 death)

- Wave 1: per-entity machinery visible live — pass 1 six ~10k calls
  (3-5s each, all parsed), pass 2 four calls, pass 3 three calls (THIRD
  header exercised). E6/E7/E8 converged across passes. Terminal: E5
  Ssketh L3 over-powered DPR 31.5 vs 21-26 (passes: 4.5 → 10.5 → 31.5,
  each repair overcorrected past the band); E9 Cobb identity.level
  non-integer (a repair wrote garbage into an in-scope section — strip
  is blind intra-section).
- New top-killer pattern (2nd instance; attempt 9's E9 11.5→60 was the
  first): repairs overshoot the band top, and over-powered is
  unrepairable by design. The repair prompt names the band floor but
  steers past its ceiling — prompt prose, a spec Non-goal.
- Verdict: step-2 gate FAILED (death set did not shrink-or-hold). No
  evidence of step-2 regression either (attempt-9's overshoot was
  batched-code). Step 3 stays locked per spec. Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-b2/`.

## Rung 10 — attempt 11, step-2 code — FAILED, 15 calls (wave-1 death)

- Wave 1 only. Pass 1 flagged E4/E5/E6/E7/E8/E9 (DPR 5.5-19); pass 2
  E6/E7/E8/E9; pass 3 E6/E8/E9. Terminal: E6 Wren identity.level
  non-integer — pass-1 repair broke it, and pass-2/3 (scope exactly
  ['identity'], violation naming identity.level) edited proficiency,
  hp, and descriptions instead of the level field. All stripped, level
  still broken. Second consecutive level-breakage death (attempt 10:
  E9 Cobb).
- Companion drift: E9's repair raised its level 4→10 chasing the band
  (intra-scope identity drift, strip-blind); E8 climbed 6.5→43.5→57
  toward its 75-80 band while dropping its class link.
- Verdict: re-roll answers the variance question — NOT variance, same
  failure family twice. Step-2 gate 0/2. Per-entity machinery clean
  (15 calls, all parsed, 3-5s each). The wall is repair steering: no
  ceiling, and the model fixes power by editing level instead of
  damage. Tee'd calls + prompts in `/tmp/mythos-ladder/calls-b3/`.

## Env note (2026-09-11, verification scope)

- System node broke mid-session (libada.so.3→.so.4 distro upgrade;
  `node`/`npm` exit 127, no passwordless sudo to reinstall). `make lint`
  fails at the frontend eslint step and vue-tsc cannot launch — both
  environmental, zero frontend files dirty. Backend gates authoritative
  meanwhile: `pytest -q`, ruff check, ruff format, mypy. Owner action:
  finish the system update / reinstall nodejs, then re-run frontend gates.

## Rung 10 — attempt 12, ceiling code — FAILED, 12 calls (wave-1 COMMITTED)

- Wave 1: pass 1 flagged E4-E9 (string-skills + DPR 5.5-28); pass 2 only
  E6 (DPR 49.5, class-link) + E7 (pass-1 broke its level); E7's level
  HEALED on attempt 2 — first level-breakage ever repaired (dial rule
  bit). No overshoot anywhere (E6 stopped at 49.5 vs 63-68, under not
  over). Committed 10/10. Wave-1 stat-death set: two entities → zero.
- Wave 2: drop-guard death again ('The Knocker'→'The Knocking Thing').
  Anchor repairs on gemma 0/5. Wave-1 commits 6/7 across code versions.
- Verdict: step-2 gate GREEN on its own criterion (stat-death set
  shrunk to zero; ceiling + dial both show first positive evidence),
  job death belongs to step 3's wall. Step 3 unlock decision is the
  owner's. Tee'd calls + prompts in `/tmp/mythos-ladder/calls-b4/`.

## Rung 10 — attempt 13, ceiling code — FAILED, 17 calls (wave-1 death)

- Wave 1 only, all 3 passes used. Terminal: E5 Ssketh — a repair fixed
  'spells require identity.class' by stuffing ~60 non-Druid spells into
  the list (in-scope `spells` section: strip-blind) plus DPR 39
  over-powered; E9 Cobb identity.level garbage (3rd consecutive
  level-breakage death). Pass-3 E6 breach shows repairs now fiddle
  damage dice/counts freely inside in-scope actions — the strip's
  section granularity is the floor of its vision.
- New pathology: spell-list stuffing as a class-link fix. Same family
  as level-escalation: the model reaches for the biggest in-scope dial.
- Verdict: green-job hunt continues (att9 green; att10/11/13 wave-1,
  att12 wave-2). Tee'd calls + prompts in `/tmp/mythos-ladder/calls-b5/`.

## Rung 10 — attempt 14, ceiling code — FAILED, 16 calls (wave-1 COMMITTED)

- Wave 1: E4 clean first try; pass 1 flagged E5-E9; pass 2 E5/E6/E7/E9
  (repairs escalated levels mid-flight: E5 3→8, E6 7→12, E7 8→11,
  E9 4→10 — dial rule defied, then pass 3 walked E5 back to 3 and
  converged everything). Committed 10/10 through all three passes.
  Wave-1 commits 7/8 — the grind works, slowly.
- Wave 2: drop guard ('The Driftwood Hulk'→'The Rusting Hulk').
  Anchor repairs 0/6 (sole success att9's behaving re-emit).
- Verdict: wave-1 machinery now survives its own detours; the job
  lottery is purely the re-emit rename. Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-b6/`.

## Rung 10 — attempt 15, ceiling code — FAILED, 16 calls (wave-1 death)

- Wave 1, all 3 passes. Terminal: E5 Ssketh identity.cr garbage
  (pass-1 broke it; pass-2/3 with scope ['identity'] edited actions,
  skills, spells — everything except cr; the att11 Wren pattern on a
  new field); E6 Wren class-link dropped while fixing DPR.
- Verdict: the identity-field repair blindness is now the wave-1
  signature (level ×3, cr ×1): scope-identity repairs polish siblings
  and never touch the named field. Green-job hunt: 1/7 on current
  code. Tee'd calls + prompts in `/tmp/mythos-ladder/calls-b7/`.

## Rung 10 — attempt 16, ceiling code — FAILED, 10 calls (wave-1 COMMITTED)

- Wave 1: fastest convergence yet — pass 1 flagged six, pass 2 only E7
  (DPR 37.5 vs 63-68), converged with no pass 3. Committed 10/10.
  Wave-1 commits 8/9.
- Wave 2: re-emit's first ADD — kept everyone but invented 'The
  Salt-Choked Channel' and renamed 'Iron Hulk'→'Quarantine Hulk'.
  Guard caught both. Anchor repairs 0/7.
- Verdict: green-job hunt 1/8. Wave-1 wall held two straight; the job
  lottery stays purely re-emit behavior. Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-b8/`.

## Rung 10 — attempt 17, ceiling code — FAILED, 15 calls (wave-1 death)

- Wave 1, all 3 passes. Everything healed (E5 cr, E8/E9 levels, E7/E9
  DPR) except one detonation: E6 Wren over-powered DPR 189 vs 63-68
  (pass-3 repair, chasing escalated L10 numbers). Worst overshoot yet;
  the ceiling steers but does not bind.
- Verdict: trim-to-band (ledger-pending owner decision) would have
  greened this outright — sole terminal. Third overshoot death (att9
  E9-60 rode along a wave-1 commit; att10 E5-31.5 paired with level
  garbage). Green-job hunt 1/9. Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-b9/`.

## Power-flag code ships (2026-09-12, owner verdict: over-powered is fine)

- `knowledge.audit_power` + `_stamp_power` in canonicalize (over verdict
  only), validator OVER branch deleted, scope row deleted, POWER
  DISCIPLINE + rules text rewritten, `StatBlock.vue` flag line, ledger
  entry resolved. Gates: 1182 backend passed, ruff + mypy clean, 201
  frontend passed, vue-tsc + eslint clean (node healed overnight to
  v26.8.1). Design point: flagged-only stamping keeps on-target blocks
  byte-identical (no prompt bloat, no fixture churn); stale flags trim
  on re-canonicalize. Next: attempt 18 re-rolls rung 10 on flag code.

## Rung 10 — attempt 18, flag code — FAILED, 14 calls (wave-1 COMMITTED)

- Wave 1 committed 10/10 through all three passes (class-link breaks on
  E6/E8 healed at pass 3). Tidecaller Wren committed WITH a
  `"power": over-powered` stamp — the first live over-powered commit;
  the death class is gone by construction (no over-powered violation
  exists to fail on). Wave-1 commits 9/10.
- Wave 2: rename-guard death ('Hedd'→'Hedd the Scavenger',
  'The Drift-Hulk' renamed). Anchor repairs 0/8.
- Verdict: the verdict works live. The job lottery is now ONLY the
  re-emit rename (step-3 territory). Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-c1/`.

## Step-3 code ships, ladder re-measures (2026-09-12, owner: green)

- Edges-only anchor repair replaces the full re-emit: frozen rosters,
  `{edges:[...]}`-only schema (src/dst enums + EDGE_TYPES enum),
  verbatim-first-entities merge, drop guard retired. Attempt-8 shape
  commits by construction. Gates: 1183 backend passed, ruff + mypy
  clean, 201 frontend passed, vue-tsc + eslint clean. Next: attempt 19
  re-rolls rung 10 on step-3 code.

## Rung 10 — attempt 19, step-3 code — FAILED, 12 calls (wave-1 death)

- E5 Ssketh: `spells require identity.class` survived passes 2+3. Pass 1
  repaired damage-shape; the repair dropped identity.class, and two
  further passes (scope included identity) edited
  features/spellcasting/traits around the hole without restoring it.
  New death family: class-link drop unrepairable. E6 also unrepaired
  (L10 DPR 27 vs 63-68). Wave-1 commits 9/11. Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-c2/`.

## Class-preserve ships (2026-09-12, owner: preserve it)

- The per-entity merge restores a valid pre-repair identity.class a
  repair dropped (deliberate class changes still land; invalid originals
  keep failing). Regression test
  `test_stat_repair_dropping_class_keeps_original` converges in 2 calls.
  Gates: 1184 backend passed, ruff + mypy clean. Next: attempt 20
  re-rolls rung 10.

## Rung 10 — attempt 20 — SUCCEEDED, 16 calls (FIRST GREEN JOB)

- Wave 1 committed 10/10 WITH 9 first-attempt edges (model wired first
  try — edgeless rule unneeded); wave-2 stat gate converged through all
  three passes; anchor repair wired the orphans with names verbatim
  ('Hedd the Scavenger', 'The Knocker' as first-emitted — the rename
  class is gone). 13 entities, 14 edges, closed vocabulary only.
- THREE live over-powered stamps (Ilsa, Harlow, Knocker) — the verdict
  paying off threefold in one job; all three would have been terminal
  deaths a day ago. Green-job hunt 2/12. Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-c3/`.
- Spec repair-sequence steps 1-3 all now measured green on gemma. Next:
  rung 25 on identical code.

## Rung 25 — attempt 1 — FAILED, 26 calls (commit-time stutter)

- Wave 1 converged through all three passes at 25-scale (15 pass-1
  repairs, class-link drops healed at passes 2-3 — the preserve holds).
  Terminal was NOT a stat death: the wave-1 output stuttered a
  byte-identical edge row (E24 -> E11 relationship x2) and the store's
  AD-23 backstop failed the whole wave at commit.
- Fix ships: `_validate_subgraph` drops exact-duplicate edge rows at the
  boundary (first wins, info log; different type/counter stays loud) +
  `test_wave_duplicate_edge_rows_collapse_to_one`. Gates: 1185 backend
  passed, ruff + mypy clean. Next: rung-25 attempt 2.

## Rung 25 — attempt 2 — SUCCEEDED, 40 calls (GREEN)

- Wave 1 committed 25/25 + 29 edges (dedup fired on stutter, info log);
  wave 2 committed 3 + 6 through anchor + stat gates. 28 entities, 35
  edges, closed vocabulary (located_in/member_of/relationship/rival_of/
  ally_of/enemy_of), 3 over-powered stamps. 40 calls, inside the 64
  budget. Tee'd calls + prompts in `/tmp/mythos-ladder/calls-c5/`.
- Next: rung 50 on identical code.

## Rung 50 — attempt 1 — SUCCEEDED, 26 calls (SHORT count)

- Job green (26 calls, wave 1 edgeless-commit path, wave 2 anchor,
  31 entities / 29 edges / closed vocabulary / 0 stamps needed), BUT
  the seed asked 50 (30+8+12) and the model emitted 28. The wave schema
  pins shape, never count — a scale gap in the contract, not a code
  bug. Tee'd calls + prompts in `/tmp/mythos-ladder/calls-c6/`.
- Open: does rung-50 green mean job-success or 50-emitted?

## Rung 50 — attempt 2 — FAILED, 1 call (dangling ref + short count)

- Emitted 25 entities (E0-E24) with an edge pointing at E25 — dead on
  call 1, no repair path for structure (correct: fail-fast). Variance
  answers the count question: 28, 25 — the 26B clusters at ~half the
  50-roster, not lottery. Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-c7/`.

## Count-pin ships (2026-09-12, owner: pin it)

- `build_wave_schema(n)` pins entities to exactly n items; wave 1
  carries the trimmed roster count, wave 2 stays free. Carry asserts
  updated (wave-1 slots pinned, wave-2 slots free) + COUNT_PIN literals.
  Gates: 1185 backend passed, ruff + mypy clean. Next: rung-50
  attempt 3 on pin code.

## Rung 50 — attempt 3 — FAILED, 1 call (pin works, self-loop)

- The pin WORKS: 50 entities emitted, 88 edges (dense wiring under the
  forced count). Dead on a self-loop (E17 -> E17) — structure fail-fast,
  no repair path. Count question answered; the loop is a fresh one-off.
  Tee'd calls + prompts in `/tmp/mythos-ladder/calls-c8/`. Next:
  attempt 4 re-rolls identical code.

## Rung 50 — attempt 4 — FAILED, 1 call (self-loop again, E4)

- Pin holds 2/2 (50 emitted both runs); self-loop deaths 2/2. Owner
  verdict: drop loops at the boundary (info log), the dedup's sibling —
  zero graph information under the closed vocabulary. Store backstop
  untouched (test_store/test_edges_api pins hold); pipeline reject-test
  rewritten to the drop contract. Gates: 1185 backend passed, ruff +
  mypy clean. Next: attempt 5 re-rolls.

## Rung 50 — attempt 5 — FAILED, 64 calls (budget ceiling)

- Structure clean (50 emitted, loops/dups dropped at boundary) — died
  spending the 64-call default inside wave-1 repairs. Not a quality
  failure: worst-case 50-scale needs ~91 wave-1 calls. Runner now
  scales the ceiling (3 x figures + 32; rungs 10/25 unchanged at 64/77,
  rung 50 gets 122). PRODUCT FOLLOW-UP: the 64 default must scale with
  roster in prod or big DM jobs die while converging — ledger entry.
  Tee'd calls + prompts in `/tmp/mythos-ladder/calls-c10/`. Next:
  attempt 6 re-rolls.

## Rung 50 — attempt 6 — SUCCEEDED, 67 calls (GREEN, FULL ROSTER)

- Wave 1 committed 50/50 + 47 edges; wave 2 committed 3 + 6. 53
  entities (33 characters / 8 factions / 12 places), 53 edges, closed
  vocabulary, 1 over-powered stamp. 67 calls, inside the scaled 122
  ceiling (would have died at 64). Tee'd calls + prompts in
  `/tmp/mythos-ladder/calls-c11/`.
- LADDER COMPLETE: rung 10 (16 calls) → rung 25 (40) → rung 50 (67),
  all green on identical code.
---

# Qwen3.8-27B (UD-Q4_K_M) re-measure — 2026-09-12

Same rung JSON, same runner (rebuilt after /tmp wipe — tee'd calls + prompts
+ committed.db snapshot per attempt), same endpoint, `enable_thinking: false`
(verified: 0 reasoning tokens, immediate content), `max_tokens` 65536,
roster-scaled budget. Model swap is the ONLY variable.

## Rung 10 — attempt 1 — FAILED, 13 calls / 221 model-seconds

- Wave 1 (105.3s, 28,679-char response) parsed first try, 10/10 + 10 edges.
  **Every character under-powered**: DPR 7.5-11.0 vs bands (L5 33-38,
  L8 51-56, L10 63-68, L12 75-80). Blocks are authentic SRD: "Melee Weapon
  Attack: +7 to hit, reach 5 ft., one target. Hit: 10 (1d8 + 4) bludgeoning".
- Repairs nudge gently (8.5 → 13 → 17) — never near the band, never an
  overshoot (gemma's death mode is absent).
- Terminal: E7 `identity.level must be an integer in [1, 20]` — the pass-1
  and pass-2 repairs each DROPPED `identity.level` while raising damage, and
  the follow-up passes (scope `['identity']`) edited `spells` instead of
  restoring the missing field. Same blindness family as gemma's, new
  variant: drop, not garbage.

## Rung 10 — attempts 2-3 — SUCCEEDED, 16 and 11 calls

- Attempt 2 (pre-guard): 16 calls, 13 entities / 16 edges, 0 stamps.
- Attempt 3 (after the identity-field merge guard): 11 calls, 13 entities /
  17 edges. Repairs converged in one pass for wave 1; the deterministic
  `conform_stat_power` is what carries Qwen's conservative blocks into band
  (verified directly: a 13-DPR L8 Rogue block conforms to 53 DPR in band).

## Code change this measure produced (commit 3d5f7d0)

- `knowledge.identity_field_ok` + `identity_field_allowed` (single-sourced
  AR25 shapes) and a generalized merge guard in `_enforce_stat_blocks`:
  a repair may not degrade `class`/`level`/`cr` (dropped, blanked, re-typed
  restores the pre-repair value; same-shape edits land; a role change and
  the Monster level→cr swap are exempt). Live cost of the gap: one job.

## Rung 25 — attempt 1 — SUCCEEDED, 22 calls / 417 model-seconds (≈7 min)

- Wave 1: 25/25 entities emitted (count pin holds), 24 edges, call-01 took
  199.8s for a 53,079-char response (gemma: 72.9s / 39,217 chars — Qwen is
  ~2.7x slower and ~1.35x more verbose at the same roster).
- ALL 15 characters arrived under-powered on the first pass (one repair call
  each, E10-E24), three needed a second pass, wave 2 needed one. Qwen never
  overshoots; it under-repairs by ~3-6x and the deterministic
  `conform_stat_power` is what lands the band (13 → 53 DPR at L8, verified).
- Wave 2 emitted 2 entities + 3 edges (gemma emitted 3 + 6).
- Total 27 entities / 27 edges. Calls 22 vs gemma's 40 for the same rung —
  fewer calls because Qwen's blocks are structurally valid (no skills-shape
  or spell-link churn), only numerically under.

## Rung 50 — attempt 1 — SUCCEEDED, 48 calls / 867 model-seconds (14.5 min)

- Wave 1: 50/50 emitted (count pin holds at 3x the earlier gemma scale),
  61 edges, one 411.5s / 106,186-char response (gemma's 25-scale wave was
  72.9s / 39,217 chars — Qwen is ~4.5x slower per wave call and ~1.35x
  more verbose). Wave 2: 3 entities + 8 edges. 53 entities (33/8/12),
  69 edges, closed vocabulary, ZERO power stamps.
- Repairs: 33 pass-1 calls (one per character — every single block arrived
  under-band) + 12 later-pass calls = 45 of the 48 calls. Gemma at this
  rung needed 67 calls but for shape/link churn, not a uniform power miss.
- Ladder on Qwen3.8: rung 10 (2/3 attempts green, 11-16 calls), rung 25
  (green first try, 22 calls), rung 50 (green first try, 48 calls).
- Product finding (owner decision, ledgered): the level->DPR band is
  MONSTER-grade (a CR-5 monster deals 33-38, a level-5 NPC fighter deals
  ~10). Qwen writes the authentic NPC number and is flagged under-powered
  on 100% of characters; the deterministic `conform_stat_power` then
  rewrites the damage upward, so the committed block is the conform's
  arithmetic, not the model's. Gemma "passes" the band only by inflating.

---

# The 100-entity cut ships — d-series (2026-09-12, owner: "start the implementation")

Code (one session, three tracks; backend 1215 passed / ruff+format+mypy clean,
frontend 202 passed / vue-tsc+eslint clean):

- Foundation: sampling passthrough (temperature/top_p/seed tri-state,
  env > config > default, body carries each key only when set); build_in
  enqueue budget `max(64, 3*figures + ceil(total/8) + 32)` (explicit
  payload value still wins); CallBudget reserve-then-call under a lock
  (parallel-safe, refuse-before-HTTP kept) + per-label telemetry — the job
  result now carries `llm_calls: {total, by_label{calls, seconds}}`.
- NPC oracle (the ledger's option (b) + DM-visible stamp): `_check_power`
  band enforcement is MONSTER-ONLY; NPC/BBEG under-powered/frail never
  violate; `conform_power` refuses non-Monsters (no more machine
  inflation); `_stamp_power` now also stamps NPC/BBEG under-powered
  (`StatBlock.vue` renders "Under-powered — DPR x vs band y"); rules text,
  POWER DISCIPLINE, and the DPR recipes rewritten honestly — the prompt no
  longer quotes the monster table as the NPC grade.
- Conform-first: Monster power-only misses go straight to the
  deterministic conform, ZERO LLM calls (measured rationale: Qwen nudges
  8.5->13->17 and never reaches band; gemma overshoots; the conform lands
  the exact row in one pass). The LLM passes are left to SHAPE violations.
- Runner: chunked wave-1 generation (weighted slices — figures 1.0, flats
  0.35, budget 12.0, hard cap 16; global E-refs; per-chunk count AND ref
  enum pins; FULL DM notes ride every chunk) + an edges-only wiring pass
  over the compact assembled roster (best-effort: any failure degrades to
  the legal edgeless commit, never a job death). Rosters within one chunk
  keep the original single-call path byte-identically (rung-10 regression
  property). Retry taxonomy: malformed JSON -> ONE re-elicitation quoting
  the decoder error with a rolled seed (never an identical re-call);
  truncation -> ONE doubled-window retry inside the operator cap; semantic
  rejections stay terminal; the anchor repair gains its first JSON retry.
  Per-call `max_tokens` sized from the pinned count (900/entity + 4096,
  min 2048, inside settings.max_tokens); edges-only calls get a fixed
  16,384 window; wave 2 caps at 24 entities / 256 edges.
- Wave-2 two-tier context: the 24-row AR6 detail tier stays, plus a
  COMPACT roster line for every wave-1 entity beyond the cap; C-refs, the
  anchor rule, and the anchor-repair enums all validate against the FULL
  wave-1 roster (the ledger's phantom-orphan-at-scale entry, resolved for
  build-in; the generate-path sibling stays open).
- Stale-rebase (both waves): a stale base re-commits once against the new
  head — no regeneration lost; wave 2 additionally re-runs its WHOLE pass
  exactly once when the rebase finds deleted endpoints.
- Progress: monotone per-chunk/per-gate milestones (cancel-safe), 0.5 at
  the wave-1 commit and 1.0 at the wave-2 commit kept.
- E2 (present-key `data` constraints in the wave schema) was tried and
  REVERTED the same day on live evidence — attempt d1 below. `data` stays
  an open object; identity garbage remains owned by the validator + the
  identity merge guards.

## Rung 10 — attempt d1, E2 schema — FAILED, 4 calls (wave-1 record death)

- Call 1 (wave 1, schema=True): 20,209-char prompt -> 4,466-char response
  in 43.8s. All six characters carried `data.stat_block` — the ONLY
  described key inside `data` — and NO AR24 record fields at all
  (role/personality/... absent). Described-key bias under grammar
  sampling: the schema described stat_block's innards, and the model wrote
  exactly the described shape and nothing else.
- Record gate burned its one pass across 2 chunks (calls 2-3); terminal:
  `E5 Ssketh fails the character record: boss must be an object` (Ssketh
  rolled Monster; the repaired record's boss stayed malformed).
- Verdict: E2's identity-garbage hardening is not worth a new death class —
  every prior Qwen/gemma roll on the OPEN data schema shipped full
  records in-wave. Reverted; d2 re-rolls on the reverted schema. Tee'd
  calls in `/tmp/mythos-ladder/calls-d1/`.

## Rung 10 — attempt d2, cut code — SUCCEEDED, 3 calls (GREEN, 147.6s)

- Wave 1: 20,209-char prompt -> 26,580 chars in 99.3s, parsed first try,
  10/10 entities + 10 edges, full records in-wave (E1 kind/ref enums +
  count pin carried; window sized 13,096). ONE record repair (2.4s).
- ZERO stat calls: all six NPCs arrived authentic — committed DPR 6.5-12.5
  ("Hit: 10 (1d8 + ...)", the model's own arithmetic), none flagged, and
  6/6 carry the new under-powered stamp (DB-verified: Wren
  `{dpr: 6.5, band: [75, 80], verdict: under-powered}`). Two Monsters
  (Ssketh CR 2, The Knocker CR 3) committed unstamped. The ledger's
  "committed block is the conform's arithmetic, not the model's" is dead.
- Wave 2: 3 entities + 6 edges, first try, no anchor repair. Total 13
  entities / 16 edges, closed vocabulary. Old-code Qwen baseline on this
  rung: 11-16 calls (and 1 death in 3 attempts). New: 3 calls.
- Telemetry live in the result: `llm_calls.by_label` = wave1 / record /
  wave2 with per-label seconds. Tee'd calls in `/tmp/mythos-ladder/calls-d2/`.

## Rung 25 — attempt d3, chunked path — SUCCEEDED, 9 calls (GREEN, 348s)

- Wave 1 CHUNKED live: chunk0 = 18 entities (10 flats + 8 figures, weight
  budget binds at 12.0) — 21,296-char prompt -> 27,565 in 109.7s; chunk1 =
  7 figures — 20,381 -> 38,371 in 148.3s. Count + global-ref pins held on
  both (E0..E17 / E18..E24), both parsed first try. Wiring pass over the
  25-line compact roster: 4,224 -> 2,361 chars in 13.0s under the
  edges-only schema. Wave 1 committed 25/25 + 40 edges.
- Repairs: ONE record call (2.0s); THREE stat calls — E12 Wren + E14 Salt
  Abbess `spells require identity.class`, E18 Scribe Pell `spell 'Silence'
  is not on the Wizard spell list` — all SHAPE family, zero power-driven
  calls. The oracle + conform-first prediction holds live: the LLM only
  repairs what Python cannot.
- Wave 2: two-tier context prompt 70,180 chars (24 detail rows + compact
  core lines; ~17K tokens, comfortable inside the server's 44.8K ctx) ->
  2 entities; one orphan -> the anchor repair converged FIRST TRY
  (edges-only, 75 chars, 6.4s) — the re-emit rename wall (0/8 on gemma)
  has no purchase on this shape. Committed 2 + 6.
- Totals: 27 entities / 46 edges, closed vocabulary, 15/16 characters
  power-stamped (NPC under-powered), progress 1.0, per-label telemetry
  complete (chunk0 109.7s / chunk1 148.3s / wiring 13.0s / record 2.0s /
  stat 29.3s / wave2 39.0s / anchor 6.4s). Old-code Qwen baseline: 22
  calls / 417s monolithic (single 199.8s wave call). New: 9 calls / 348s —
  every call bounded (<150s), cancellable between chunks, truncation
  structurally out of reach against sized windows.
- Verdict: the d-series gate is GREEN on both rungs on Qwen3.8-27B
  (single-call AND chunked paths). Suggestion: next rolls are rung 50
  re-run on identical code, then a rung-100 seed (≈60/24/16 with notes
  naming >24-position entities to exercise the compact tier as the
  acceptance probe), plus a gemma-4-26B-A4B comparison pass. Tee'd calls
  + prompts in `/tmp/mythos-ladder/calls-d3/`, DB snapshot beside them.

## Rung 100 — attempt d4, chunked path — SUCCEEDED, 35 calls / 1483s ≈ 24.7 min (GREEN, FIRST TRY)

- Seed: `rung-100.json` (60 figures / 24 places / 16 factions + notes-D),
  the harbor superset extended; notes-D deliberately names late-roster
  figures (Orm, Sim, Agda, Cistern King "is level 18", Quill,
  Salt-Marrow, Vess) and four new wave-2 subjects — the compact-tier
  acceptance probe.
- Wave 1 CHUNKED: 8 chunks (2 flat-heavy 16-entry chunks at 18-23s each;
  6 figure chunks, the biggest 54,123 chars in 208.6s) — every count+ref
  pin held, every chunk parsed FIRST TRY (8/8), zero truncations against
  the sized windows. Wiring pass: 14,465-char compact-roster prompt ->
  8,307 chars in 47.6s. Wave 1 committed 100/100 + 189 edges.
- Repairs: ONE record chunk (3.9s); 24 stat calls total (18 wave-1 + 6
  wave-2), all SHAPE family (spell/class-link dominant) — zero
  power-driven calls at 60 characters. The oracle prediction scaled: the
  same roster pre-oracle would have burned ~60-180 stat calls on band
  misses alone (rung-50 measured 45/48 at half this size).
- Wave 2: prompt only 27,235 chars at a 100-entity core — the detail tier
  happened to be the 24 flat places (rowid order), so 76 core entities
  rode the COMPACT tier, and the model anchored edges straight into it:
  located_in/member_of/rival_of touching E28, E31, and E64 (Freshwater
  Trust, Deep Maw's Brood, the Cistern King) — all beyond C23, all legal,
  zero phantom orphans, NO anchor repair needed (17 edges, wired first
  try). 10 entities committed: the four notes subjects (Tollhouse of
  Teeth, Pale Ledger, Tideglass, Brackish Herald) plus six variants of
  wave-1 figures the notes mentioned ("Sim (The Drowned)", "The Cistern
  King (Level 18)", ...). The variants are the long-ledgered dedup
  question (2.3 entry / L-decision) surfacing WITHIN one job — owner
  decision, not a defect; names stay distinct so nothing collides.
- Directive probe: the notes' "Cistern King is level 18" landed on the
  WAVE-1 figure (chunk 5) — record level_cr "level 18" AND identity.level
  18, committed stamped `{dpr: 44.0, band: [111, 116], verdict:
  under-powered}` — the full-notes-per-chunk rule works and the stamp
  tells the DM exactly what a "level 18" at authentic numbers means.
- Census: 110 entities (67 characters / 16 factions / 27 places), 206
  edges, closed vocabulary; 58 characters stamped under-powered
  (authentic DPR 4.5-44), 4 over-powered, 5 unstamped. Telemetry:
  8×chunk + wiring + 1 record + 24 stat + wave2 = 35/212 budget.
- Verdict: the 100-entity target is GREEN first try on Qwen3.8-27B,
  24.7 min sequential — every call bounded (≤209s), cancellable between
  chunks, truncation unreachable. The old stack could not have reached
  this payload: one ~820s/~212KB monolith against the 900s timeout and
  the 65,536-token ceiling, then an oracle repair storm past any budget.
  Suggestion: gemma-4-26B-A4B comparison roll next, then Cut-3 (chunk
  pool ≈ 4× wall-clock, gated on the server's parallel-slot config) and
  the owner's dedup call for the variant-entity behavior. Tee'd calls +
  prompts + committed.db in `/tmp/mythos-ladder/calls-d4/`.

## Rung 100 — attempt d5, gemma-4-26B-A4B comparison — SUCCEEDED, 67 calls / 591s ≈ 9.8 min (GREEN, FIRST TRY)

- Same seed (`rung-100.json`), same cut code INCLUDING the 2026-09-12
  identity work (upsert merge + roster-twin gate), model =
  unsloth/gemma-4-26B-A4B-it-qat-GGUF (MoE, 4B active).
- Wave 1 CHUNKED: 8/8 chunks parsed first try (biggest 32,096 chars in
  67.7s — 2-3x faster per chunk than Qwen), wiring pass 9,070 chars in
  25.9s. Committed 100/100 + 148 edges.
- Repairs: one record chunk (6,124 chars, schema=False); 56 stat calls
  (vs Qwen's 24) — gemma's blocks stay SHAPE-violation-prone
  (spell/class-link family), each repair 3-4s. ZERO power-driven calls;
  census 54 under-powered stamps (authentic DPR 2.5-73.5), 0
  over-powered — the old gemma overshoot death class (rung-50: DPR
  189/60/31.5) stays extinct under the oracle + conform-first.
- Wave 2: 26,201-char two-tier prompt -> EXACTLY the four notes subjects
  (Tollhouse of Teeth, Pale Ledger, Tideglass, Brackish Herald), 4
  entities / 5 edges, ZERO roster twins — the anti-twin prompt rule held
  on gemma outright; d4's Qwen needed the gate for its six variants.
  104 entities / 153 edges total.
- Head-to-head on the same payload and code: Qwen3.8-27B 35 calls /
  24.7 min; gemma-4-26B-A4B 67 calls / 9.8 min. Gemma wins wall-clock
  2.5x on local hardware (its calls are 3-10s); Qwen wins call count ~2x
  (the number that matters on metered routes). Both green first try at
  100 — the cut is model-portable, which is the K-profiles precondition.
- Profile read-off for K: same chunk sizes serve both (gemma's chunks are
  latency-bound, not size-bound); gemma wants a larger stat-repair
  allowance, Qwen the reverse. Default-model choice stays the owner's —
  deploy/config.toml still carries the ask-first placeholder.
- Tee'd calls + prompts + committed.db in `/tmp/mythos-ladder/calls-d5/`.

## Owner verdict — model default + K profiles (2026-09-12)

- Owner's call after reading d4/d5: **gemma-4-26B-A4B is the default**
  ("even with the recalls twice as much it finished 2.5 times faster").
  `DEFAULT_LLM_MODEL` (app/core/config.py) and deploy/config.toml now
  carry `unsloth/gemma-4-26B-A4B-it-qat-GGUF`; Qwen3.8-27B-GGUF is
  documented beside it as the metered-route choice (half the billed
  calls — the metric that inverts on paid APIs).
- K resolved THINNER than the plan drafted: the d4/d5 pair shows no
  per-model behavioral difference beyond latency and repair traffic —
  the same chunk budget serves both, both converge inside the same retry
  caps, and sampling temperature was measured on neither. A per-model
  field registry would be invented machinery, so it does not exist.
  What shipped is the call-CLASS profile from step 8: repair-class calls
  are cold + seeded (`REPAIR_TEMPERATURE = 0.0`, `REPAIR_SEED`,
  operator pins win, the JSON retry rolls seed+1 so a cold profile never
  re-samples its own failure); wave-class calls stay warm. Per-model
  batch/pool sizes remain deferred with Cut 3 (remote-API trigger).

## Rung 25 — attempt d6, edge-kind layers 1+2 — SUCCEEDED, 26 calls / 226s (GREEN)

- Code = the 2026-09-12 edge-kind cut (owner decision after the d5 world
  audit: 39/153 = 25.5% structurally invalid edges): every prompt now
  carries per-type kind guidance from EDGE_KIND_RULES (layer 1), and the
  validator + ONE bounded edges-only repair enforce the same table plus
  the mutual-member_of graph rule (layer 2); wave-1 residual violations
  drop with the job-result audit, wave-2 fails loud. Same seed
  (rung-25), same model (gemma-4-26B-A4B).
- LIVE PROOF the enforcement was needed: the guidance alone did NOT stop
  the model — `wave1_edge_kind_repair` fired ONCE on real output; the
  repair fixed every rejected edge; ZERO drops (audit empty). The
  committed graph is kind-clean AND mutual-clean (0 violations / 0
  mutual pairs on 42 edges) vs d5's 25.5% / 11 pairs.
- The type mix MOVED with the guidance: located_in 4 -> 18 (the model
  finally used the spatial type it had been faking with member_of),
  member_of 56 -> 6, generic relationship 87 -> 11. The bare-enum
  vocabulary block WAS the cause — confirmed by the fix, not assumed.
- Suite: 1233 backend (incl. 9 new edge-kind tests: rule pins, prompt
  guidance pins, wave-1 repair / residual-drop audit / dead-repair drop,
  mutual repair, wave-2 repaired-vs-fail, world-mutual), ruff/format/mypy
  clean.
- Tee'd calls + prompts + committed.db in `/tmp/mythos-ladder/calls-d6/`.
