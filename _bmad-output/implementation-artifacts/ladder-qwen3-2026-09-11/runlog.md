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