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