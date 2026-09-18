# Handoff — hybrid authorship follow-ups & stat-block power saga (2026-09-17/18)

Session window: 2026-09-17 morning → 2026-09-18. Dev box: all fixes live on `main`, api hub service `mythos-api` restarted on latest commit (`b99105f`). Nothing open on implementation side.

## 0. Read this first

- Repo conventions: `_bmad-output/`, store single-writer rule, no secrets in config — see repo AGENTS.md.
- Dev-stack facts (api service name, env, DB paths, WAL `.backup` trick): unchanged from prior handoffs, still in memory root.
- Model being dogfooded: gemma-4-26B-A4B via raw llama-server on `127.0.0.1:8889` (config.toml `[llm]`).

## 1. What this session shipped (all committed, 1372 backend / 209 frontend green)

### A. Hybrid-authorship implementation (the big spec, from handoff-hybrid-authorship-implementation.md)
Implemented in order per spec: `add_character` job kind + `POST /api/characters` sync gate → `pipeline/direct.py` zero-LLM runner → zero-LLM acceptance tests → hybrid seeds (`_seed_authored`/`_backfill_authored`), mandate re-emit, three-tier declared edges → F4 character-merge rework. (Executed in the archived portion of this session; see spec `_bmad-output/implementation-artifacts/spec-hybrid-authorship.md` for contract details and the sprint rows for what is still `review`.)

### B. Owner-feedback stat-block/profile fixes (2026-09-17, user-reported)
1. `95cf87c` **one edit surface per key** — "stat block edits don't stick" / "Additional data shows stat_block as JSON" / "boss as JSON". Root cause: `stat_block` excluded from JSON_FIELDS but the extras dump's exclusion list was derived from it → two competing edit surfaces; last-write-wins silently reverted edits. Fixed: extras = truly unknown keys only; boss = four text fields with draft/save/dirty/cancel discipline.
2. `6bf4660` **canonical damage parts in StatBlockEditor** — export rendered `5d6+7+7` (bonus baked into dice AND separate bonus field); prefill hid a part's separate bonus field behind an empty mod box. Emit: dice = bare `5d6`, bonus rides own field. Prefill: decompose dice suffix OR fall back to part bonus.
3. `95cf87c` **action prose is the line** — "descriptions of the attacks does not show in the stat block". Renderer's old rule was parts-REPLACE-prose; generated prose (save DCs, riders) is richer than parts, so every action with damage parts lost its description. Now: prose primary, structured numbers as muted secondary clause (`to-hit +14 · 13.5 (1d10+8) force`). Three test pins updated; view pins (CandidatesView/WorldView) updated to match.
4. `62242e2` + `b99105f` **the power-accounting saga** — see §2.
5. Prompt guidance (was sitting uncommitted, landed as `6b263cc`): 3–5 varied actions for level/CR 10+ (was 2–4), scaled class-grade damage anchors (level-20 martial/BBEG ~50–80/round), BBEG boss-mechanics encouragement. Under-power STAMP stays advisory (2026-09-12 NPC-oracle verdict stands).

## 2. The power saga — read before touching combat.py/statblocks.py

Owner feedback drove five deterministic fixes to `backend/app/pipeline/combat.py` + `statblocks.py`. The fasiha case (level-20 NPC warlock) went from "Under-powered — DPR 13.5 vs band 123–140" to on-target 40.5 vs (25, 84).

1. **Routine-by-prose** (`_is_routine_action`): multiattack was detected only by action NAME; models emit flavor names ("Eldritch Torrent") with "Multiattack: … makes three … attacks" in the description. x3 multiplier never fired.
2. **Named-target multiplier** (`round_dpr` new `multiattack_targets` param): when the routine prose names exactly ONE other damaging action, the count multiplies THAT action (else strongest-other approximation) — an AoE beside the routine no longer fakes an over verdict.
3. **Prose→part fold** (`_fold_prose_damage`, in the canonicalize chain): sums every `N (XdY±Z)` idiom in an action's description and rebuilds parts UP from prose when prose total > parts total. Never lowers (healthy multi-part attacks stay byte-identical). Generated path only.
4. **Class-grade NPC bands** (`NPC_DPR`, derived from monster table: low=0.2×low, high=0.6×high; level 20 → (25,84), level 5 → (7,23)). `expected_band` keys Monster→CR_DPR, NPC/BBEG→NPC_DPR. An over-the-band NPC clamps to on-target — a strong character is never nagged (owner direction). `conform_power` STILL never touches NPC/BBEG.
5. **Dice-string identity in completion** (`_complete_damage_parts`): when a part's `dice` string parses, the parsed count/sides WIN over a redundant stated pair that contradicts it. Live-verified shape: `{"dice": "8d8", "count": 1, "average": 36}` was being rewritten down to 1d8/4.5 — the model's honest 36 destroyed by the "completion" fold. THE bug of the session; found only by instrumenting the live gate (see §4).

11 old test pins of the monster-band/over-stamp/name-matched-routine policy were updated (not suppressed — the pins enshrined the pre-fasiha policy).

## 3. The test-gap analysis (owner asked why ~1400 tests missed "easy" bugs)

Four structural reasons, one per bug class:
- **Self-consistent fixtures**: hand-written test blocks never carry contradictions (count vs dice, prose vs parts); the model always did.
- **Fold composition**: prose fold correctly abstained, completion fold then destroyed the data — unit tests of individual folds cannot see sequence interactions. Needs end-to-end tests on REAL captured model output.
- **Tests enshrining wrong policy**: 11 pins asserted NPC==monster band etc. Loyal tests + wrong spec = confident green.
- **No corpus of real model output.** Every fix this week was reactive from owner-pasted output.

**Antidote (next session candidate):** build a `tests/fixtures/model_outputs/` corpus of verbatim live failures and source regression tests from it, plus end-to-end gate tests per captured shape. Recorded in memory (`learn`, 2026-09-18).

## 4. Owner-owned items (deliberately not done)

- Hybrid-authorship spec frontmatter + sprint rows still `review` — owner verdict pending. Same for this week's forge/library/statblock work (not reflected in sprint docs).
- `world_integration` and `Additional data` still edit as raw JSON (only stat block + boss are structured) — owner hasn't asked.
- `POST /api/characters` (zero-LLM direct path) is API-only, no UI consumer by design since the forge went hybrid.
- Media retention/pruning still deferred (story 4.3).
- Cosmetic: fasiha's committed Eldritch Blast prose contains "1d10 + 😎" (emoji for 8) — part data is correct; committed before the prose fold, a re-accept of the pending regenerate row fixes prose AND parts together.

## 5. Live state

- DB `data/mythos.db`: fasiha lives in campaign "the shattered coast" (`01M2Q3A9C4458ZYNKYPMSETA7Z`, entity `01M2RDY2EXJH80V7NY5GWW72ZN`). One `proposed` regenerate candidate (job `01M2RK8CEXH8HGREX5TRYG0S3P`) carries the CORRECT block (Void Collapse 8d8/36) — owner should accept it.
- All my debug probe candidates were rejected; candidate list is clean.
- gemma emits ~1/3 unparseable JSON at current prompt sizes — pre-existing variance, not shape-related.
- Restart the api after backend changes: `hub restart mythos-api`.

## 6. Verification commands

```
uv run --directory backend pytest -q          # 1372 passed
cd backend && uv run ruff check app tests && uv run mypy app
cd frontend && npx vitest run && npm run lint # 209 passed
# combat audit CLI for quick checks:
cd backend && uv run python -m app.pipeline.combat statblock.json
```
