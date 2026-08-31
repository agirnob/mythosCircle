---
title: 'Key figures carry a minimal 5e stat block: AR24 section shape, AR25 constraints + one bounded repair pass'
type: 'feature'
created: '2026-08-31'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'e8e2cc2'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/epics.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/spec-2-3-core-first-two-wave-build-in-pipeline.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/epic-2-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** story 2.3 commits wave-1 characters with arbitrary optional `data` — the DM's key figures arrive without stats, so the world is visible but not playable at the first visible moment (2.7's "barkeep with stats ready to roll initiative" fails its premise). AR24's canonical sectioned character model and AR25's KnowledgeProvider + bounded repair pass are the contract, but no code exists for either.

**Approach:** the build-in wave-1 pipeline generates a minimal 5e stat block for every character entity as part of `data.stat_block` (the AR24 mechanics section key), validates it against local SRD 5.1 reference data (AR25 KnowledgeProvider), and runs **exactly one bounded repair pass** (one LLM call covering every flagged character in the wave) when any block is missing or violates the constraints. Validation completes **before** wave-1 commits — an invalid stat block is never committed; a block still invalid after the repair pass fails the job with a fail event naming the character and its violations (AR25). The repair call counts against the job's `max_llm_calls` budget (AR21).

## Boundaries & Constraints

**Always:**
- **Scope — wave-1 characters only.** The story AC is "given a key figure": stat-block generation, validation, and repair apply to wave-1 `character` entities (the key figures). Wave-2 characters from `notes` stay narrative-only — Epic 3's candidates carry AR19 stat blocks with the full canonical model; a wave-2 stat-block requirement is out of 2.4 scope (design note).
- **One stat block slot:** `data["stat_block"]` in the entity's structured canonical record (AR24: consumers render known sections, skip unknown — `stat_block` is the mechanics-section key this story ships; other AR24 section keys land in Epic 3). Factions and places never carry one; character entities MUST.
- **Reference data is a pure module** (`pipeline/knowledge.py`, AR25 "local reference tables ... queried by the pipeline"): typed frozen sets/maps — SRD 5.1 classes, races, alignments, the 18 SRD skills, spell-to-class starter lists, and stat caps. In-process constants, not new DB tables — the single-SQLite constraint (AR11) stays; the sets are open and extensible.
- **Validation = AR25 constraint checks:** field-set shape (SRD 5.1), role-limited semantics (NPC/BBEG → `level` 1–20; Monster → `cr` 0–30 or 1/8|1/4|1/2; never both), SRD vocabularies (race/class/alignment/skill/spell membership), hard caps (ability scores 1–30; AC/HP positive ints), strict int typing (never bool). Derived-value arithmetic (skill bonus = PB + modifier) is NOT validated — that is Phase-3/Epic-3 consistency, not the constrained field set.
- **Exactly one bounded repair pass per job:** when any wave-1 character has a missing or invalid stat block, the pipeline makes ONE additional LLM call (gated by `CallBudget` — AR21: repair passes count against `max_llm_calls`) that returns **stat blocks only** (`{"stat_blocks": [{"ref": "E<pos>", "stat_block": {...}}]}` for the flagged refs exactly once each). Repairs merge into the already-validated wave (`dataclasses.replace` on the frozen `EntityInput`); edges, names, kinds, and un-flagged data are untouchable by the repair pass. Revalidate; any still-invalid block ⇒ `JobPayloadError` ⇒ job fails with a fail event naming characters and violations — **zero commits** (validation precedes the wave-1 commit).
- **Prompt determinism holds (AD-16):** the wave-1 prompt gains the stat-block contract with the reference vocabularies embedded (sorted); `build_stat_repair_prompt` is a pure function of the flagged issues (name, current block or absence, violations) + shared rules text — no ids, timestamps, or job state; pinned byte-identical by tests.
- **Shared rules text:** one `stat_block_rules_text()` (a pure function of the sorted reference data) is embedded in both the wave-1 prompt and the repair prompt, so the model sees exactly the constraints the validator enforces.
- **Cancel semantics mirror wave 1:** poll before the repair call (a cancel there is a no-op — the wave doesn't commit); a cancel landing *during* the repair call leaves the repaired core committed (the existing mid-wave-1-call convention — the post-commit poll stops before progress writes, as today).
- **Spells are role-limited** (the AR25 example): `spells` is optional; when present, each name must be a known reference spell **and** the character's `identity.class` must be in that spell's allowed-class set; spells require a class — monsters express magic as `actions`/`traits`, not `spells`.
- Layering unchanged: `pipeline/` owns reference data + validation + repair; `store/` untouched (no schema change — `data` is already a JSON column); providers stay leaves. Job result contract unchanged (`waves`; no stat-specific keys).
- Unknown top-level keys inside `stat_block` are tolerated (AR24 forward compatibility): validate the known sections, ignore the rest.

**Ask First:** any of the frozen decisions above the owner wants changed at review (wave-2 characters in scope, stat-block field set, reference data as module vs DB tables, per-wave vs per-stat-block repair calls, spells rule).

**Never:**
- No DB migrations, no new tables, no world-state writes outside `commit_subgraph`.
- No validation after commit; no committing an invalid or missing stat block for a wave-1 character.
- No repair retry loops: one repair call maximum, ever; still-invalid ⇒ fail loud (AR25).
- No LLM-visible ULIDs/timestamps/job state in the wave-1 or repair prompts.
- No stat-block work for wave 2, factions, or places; no changes to the wave-2 prompt contract.
- No derived-value arithmetic validation (PB/modifier consistency is out of 2.4 scope).
- No frontend/wire changes (2.7 renders `data.stat_block`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| STAT_OK | wave-1 character with a valid `data.stat_block` | block commits as given; data round-trips through `Entity.data`; no repair call; job `succeeded` | N/A |
| STAT_INVALID_REPAIRED | wave-1 character with a constraint-violating block (STR 40, bad skill, monster with `level`) + repair provider returns a valid block | one repair call; repaired block committed; job `succeeded`; call count = 2 | N/A |
| STAT_MISSING_REPAIRED | character with no `stat_block` + repair returns one | "stat_block section missing" flagged; repair supplies the block; committed; `succeeded` | N/A |
| STAT_STILL_INVALID | invalid block + repair still invalid | job `failed`, zero commits, error names the character and violations (fail event, AR25) | `JobPayloadError` |
| STAT_REPAIR_BUDGET | `max_llm_calls=1`, invalid block | repair call refused before HTTP (`BudgetExceededError`); job `failed`; zero commits (AR21) | N/A |
| STAT_REPAIR_BAD_REF | repair output with unknown/missing/duplicate refs | job `failed`; zero commits; error names the ref | `JobPayloadError` |
| STAT_REPAIR_FENCE | repair output markdown-fenced | fence stripped, merge proceeds (same tolerance as wave output) | N/A |
| STAT_VALIDATION_MATRIX | any field-set/vocab/cap violation | `validate_stat_block` returns stable violation strings; unit-pinned | N/A |
| STAT_REPAIR_DETERMINISM | same flagged issues | repair prompt byte-identical across calls; no ids/timestamps | N/A |
| WAVE2_CHARACTERS_UNREQUIRED | wave-2 character without a stat block | commits as today — scope pin (Epic 3 carries AR19) | N/A |
| STAT_CANCEL_BEFORE_REPAIR | cancel lands between wave-1 validation and the repair call | no repair call, no wave-1 commit, job stays `cancelled` | N/A |

## Code Map

- `backend/app/pipeline/knowledge.py` — NEW (AR25): typed reference data — `ROLES`, `LEVEL_MAX=20`, `CR_MAX=30`, `CR_FRACTIONS`, `ABILITY_SCORES`, `ABILITY_MIN=1`, `ABILITY_MAX=30`, `CLASSES`, `RACES`, `ALIGNMENTS`, `SKILLS`, `SPELLS: dict[str, frozenset[str]]` (spell → allowed classes); `validate_stat_block(block: object) -> list[str]` (pure; `[]` = valid) with the role-limited semantics and strict-int checks.
- `backend/app/pipeline/fencing.py` — NEW: `strip_fence(text)` moved verbatim from `build_in._strip_fence` (module-level public); `build_in` imports it as `_strip_fence` (clean cutover, no other callers).
- `backend/app/pipeline/statblocks.py` — NEW: `StatIssue(position, entity, violations)` dataclass; `collect_stat_issues(entities) -> list[StatIssue]` (characters only, missing ⇒ `["stat_block section missing"]`); `stat_block_rules_text()` (shared deterministic rules); `build_stat_repair_prompt(issues) -> str`; `parse_stat_repair_output(text, flagged_positions) -> dict[int, dict]` (refs exactly once, canonical `E<pos>`); `apply_stat_repairs(entities, repaired) -> list[EntityInput]`.
- `backend/app/pipeline/build_in.py` — wave-1 prompt gains the STAT BLOCK section (contract + embedded reference vocab via `stat_block_rules_text`); `run_build_in` wave-1 flow: after `_validate_subgraph` → `collect_stat_issues` → cancel poll → repair call (budget-gated) → parse/apply → re-flag → `JobPayloadError` with the fail-event message when still invalid → then the existing commit. No change to wave 2.
- `backend/tests/test_build_in_pipeline.py` — `_wave1_output()` gains a valid `stat_block` on Mira Vane (contract change; the existing failure-path tests reject in `_validate_subgraph` before stat checks, so they stay green); new rows for the I/O matrix.
- `backend/tests/test_statblocks.py` — NEW: `validate_stat_block` matrix (role/caps/vocab/shape), repair parse (refs, fences, bad refs), repair prompt determinism, rules-text determinism.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/pipeline/knowledge.py` -- reference data + `validate_stat_block` (AR25).
- [x] `backend/app/pipeline/fencing.py` -- `strip_fence` extracted; build_in imports it.
- [x] `backend/app/pipeline/statblocks.py` -- issues collection, rules text, repair prompt/parse/apply.
- [x] `backend/app/pipeline/build_in.py` -- wave-1 stat-block contract in prompt + repair orchestration before commit.
- [x] `backend/tests/*` -- fixture stat block + I/O-matrix rows + unit matrix.
- [x] Sprint sync `2-4-key-figures-carry-a-minimal-5e-stat-block` → in-progress → review (after this implementation).

**Acceptance Criteria:**
- Given a wave-1 key figure, when its stat block is generated, then it follows the AR24 section shape (`data.stat_block`) and passes the AR25 constraint checks (role-limited, SRD 5.1 field set and vocabularies, caps) — or exactly one bounded repair pass (AR25).
- Given a stat block still invalid after the repair pass, when the wave would commit, then it is not committed; the job fails with an error naming the character and the violations — a fail event is logged (AR25).
- Given a valid wave-1 output, when the job runs, then no repair call happens and the block round-trips through the commit (smoke: `Entity.data["stat_block"]`).
- Given the same world state + the same wave-1 payload (or the same flagged issues), when a prompt is built, then it is byte-identical (AD-16) — pinned by unit tests for both the wave-1 and repair prompts.

### Review Findings

- [x] [Review][Patch] Enforce the Monster-spells role gate — **owner decision: enforce**. `_check_spells` now takes the canonical role and rejects non-empty `spells` for Monster (knowledge.py); `test_monster_spells_not_allowed` asserts the rejection.
- [x] [Review][Patch] Ratified free-text Monster `identity.race` — **owner decision: ratify**. Spec Change Log entry added; `test_race_vocabulary_enforced_for_npc_and_free_for_monster` pins both directions.
- [x] [Review][Patch] Kept `SPELLS` full size; prompts now embed `spells_reference_text()` — **owner decision: restructure prompt embedding**. Wave-1: per-class grouped reference (all classes); repair: flagged classes only; wave-1 embedding pinned by `test_build_wave1_prompt_deterministic`; subset/determinism pinned in `test_statblocks.py`.
- [x] [Review][Patch] `stat_block` stripped from non-character entities at wave-1 validation — **owner decision: strip**. `strip_noncharacter_stat_blocks` runs in `run_build_in` after `_validate_subgraph`; pinned by `test_faction_stat_block_is_stripped` + unit test; recorded in Spec Change Log.
- [x] [Review][Patch] `test_missing_stat_block_repaired` fixed — drops the fixture block, asserts two calls and the "stat_block section missing" violation in the repair prompt [backend/tests/test_build_in_pipeline.py]
- [x] [Review][Patch] STAT_CANCEL_BEFORE_REPAIR now has `test_cancel_before_stat_repair_is_noop` — no repair call, `revision_chain == []`, job stays `cancelled` [backend/tests/test_build_in_pipeline.py]
- [x] [Review][Patch] Wave-1 prompt stat-block contract pinned — rules text + spells reference + "characters MUST include" asserted in `test_build_wave1_prompt_deterministic` [backend/tests/test_build_in_pipeline.py]
- [x] [Review][Patch] SRD spell→class mappings corrected against the open5e SRD 5.1 dataset audit (Stoneskin/Bard, Phantasmal Killer/Warlock, Evard's/Warlock, Insect Plague/Sorcerer + full-table pass) [backend/app/pipeline/knowledge.py]
- [x] [Review][Patch] `identity.class` folding now uses `.strip().lower()` via public `resolve_class` — parity with every other vocabulary; pinned by `test_class_whitespace_folding_matches_other_vocabularies` [backend/app/pipeline/knowledge.py]
- [x] [Review][Patch] `_parse_ref` uses `.isdecimal()` — non-decimal digit refs ("E²") raise the canonical `JobPayloadError`, never a raw `ValueError`; malformed-parametrize case added [backend/app/pipeline/statblocks.py]
- [x] [Review][Patch] Duplicate spell names rejected (parity with skills/actions/traits) — `test_duplicate_spells_rejected` [backend/app/pipeline/knowledge.py]
- [x] [Review][Patch] Dead test helper `_stat_block()` deleted [backend/tests/test_build_in_pipeline.py]
- [x] [Review][Patch] Docstrings refreshed post-cutover: build_in.py (spec-2.3 + 2.4, stat enforcement), worker.py (up to three guarded calls), `JobPayloadError` (AR25 fail-event channel), test module headers [backend/app/pipeline/]
- [x] [Review][Patch] Monster-with-level pipeline variant added — `test_monster_with_level_repaired_in_one_pass` exercises the matrix row's second violation [backend/tests/test_build_in_pipeline.py]
- [x] [Review][Defer] Invalid `identity.role` suppresses all level/cr violation reporting (knowledge.py:485-501) — a repair round only ever sees the role error, so a double violation survives the single bounded pass and fails the job; role-unknown cross-checks are inherently ambiguous — deferred

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries. -->

- **2026-08-31 — review loop 1 (owner decisions).** (1) The frozen spells bullet is now *enforced*: `_check_spells` rejects a non-empty `spells` section for role Monster (it previously gated only on class membership); duplicate spell names are rejected (parity with skills/actions/traits). (2) The frozen "SRD vocabularies" bullet carries an explicit carve-out ratifying shipped behavior: `identity.race` is SRD-checked for NPC/BBEG only; Monster race is a free-text creature type/name (2.6/2.7 render it verbatim). (3) The "shared rules text" bullet is re-scoped: `stat_block_rules_text()` no longer enumerates spells; `spells_reference_text()` carries the per-class lists — full reference in the wave-1 prompt, flagged-classes subset in the repair prompt (pure functions of reference data + issues; determinism pins updated). The Design-Notes "~90 spells" figure is superseded: the table stays full-size and its 31 wrong mappings were corrected against two independent SRD 5.1 mirrors (dnd5eapi class lists and the SRD-5.1 markdown mirror, which agree spell-for-spell); the 18 non-SRD staples it also surfaced (Hex, the smites, the Hadar spells…) are kept as a documented curated extension. (4) The Always bullet "factions and places never carry one" gains its enforcement mechanism: `strip_noncharacter_stat_blocks` drops a stray `data["stat_block"]` from non-characters in `run_build_in` before validation/commit — tolerant strip, never a repair subject (the Never bullet's "no stat-block work" still holds: non-characters are never validated or repaired).

## Design Notes

**Why wave 1 only, and why it's honest.** The story AC names key figures; the first-visible-moment value is the core wave. Wave-2 note characters arriving statless is a visible-but-not-yet-playable gap, but 2.3's wave-2 contract is untouched here, and Epic 3's AR19 candidates carry full stat blocks for every generated entity — the world's secondary wave gets stats with the candidate lifecycle. Folding wave 2 in now would expand the repair surface (a note's throwaway character could fail the whole build) for little first-moment value. Extension is one `collect_stat_issues` call site away.

**Why `data["stat_block"]` rather than the full AR24 key set.** AR24's canonical record is "structured JSON keyed by section" with forward compatibility — consumers render known sections and skip unknown ones. 2.4 ships exactly one section key, `stat_block`, holding the minimal playable block (identity anchor, attributes, combat, optional skills/actions/traits/spells) in one place; 2.6's export and 2.7's render read it directly. Epic 3 introduces the remaining AR24 section keys alongside it; nothing in 2.4 breaks when they appear.

**Why module data, not tables.** AR25 says "local reference tables ... queried by the pipeline" — the SRD 5.1 dataset is static, small, and read-only; a DB table adds migrations and a read path for zero query benefit at this scale, and AR11's single-SQLite list (world state, event log, queue, media, accounts) doesn't include reference data. Frozen sets in `pipeline/knowledge.py` are the boring choice; extending the tables is adding entries to a set, which is exactly how AR25's "relevant subset" latitude reads.

**Why one repair call per wave, not per stat block.** "Exactly one bounded repair pass" per AC reads per-stat-block; the budget-sane reading is one *attempt* per stat block, carried in a single LLM call for the whole flag set (the wave's blocks are independent fields, not sequential generations). One call also keeps the worst-case build-in job at three calls (wave 1, repair, wave 2) inside the AR21 ceiling. The repair contract returns stat blocks only, so the repair pass can't silently rewrite edges, names, or kinds the wave already validated.

**Why no derived-value arithmetic.** "Passes AR25 constraint checks" is about the field set and role-limited vocabularies — the SRD's bounded set. Checking that skill bonuses equal PB + ability modifier would fail most first-pass generations (the model must compute modifiers), force repair on playable blocks, and burn budget; it also belongs to Phase-3 counter/derivation consistency, not the constrained field set. The caps (1–30 abilities, level 1–20, CR set) are what make a block *valid*; the bonuses are what make it *tuned*.

**Repair failure damage model.** Stat validation happens before the wave-1 commit, so STAT_STILL_INVALID and STAT_REPAIR_BUDGET leave zero revisions — unlike wave-2 failures (core stays). That is the point of AR25: an invalid stat block is never committed, period.

**Spell seeding risk.** `SPELLS` is a curated starter set of high-confidence SRD base-class mappings (~90 spells), open/extensible. The seed is deliberately conservative — a spell the LLM invents that isn't in the reference is *unknown to the local table* (AR25's "queried by the pipeline" literally cannot validate it) and gets flagged; the repair prompt tells the model to use only listed spells. Wrong class lists would force churn, so only confident mappings are shipped.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. the new stat-block rows (deterministic, no live LLM).
- `make lint && make typecheck` -- expected: ruff, mypy strict clean (frontend untouched — no `gen:api`).

**Smoke (owner dogfood, optional until 2.7):** launch backend with a scripted provider; enqueue a build_in with a key figure; observe the committed `Entity.data["stat_block"]` via a world read; trigger a repair pass by feeding a violation; observe `job_done` with the repaired block — or `job_failed` naming the character when the repair stays invalid.

## Suggested Review Order

**Reference data + validator (AR25)**

- Role-limited semantics: level vs CR split, caps, strict int typing
  [`knowledge.py:validate_stat_block`](../../backend/app/pipeline/knowledge.py)

- Hard caps + SRD vocabularies, spell-to-class role limitation
  [`knowledge.py`](../../backend/app/pipeline/knowledge.py)

**Repair machinery**

- Issues collection — characters only, missing ⇒ flagged
  [`statblocks.py:collect_stat_issues`](../../backend/app/pipeline/statblocks.py)

- Stat-blocks-only repair contract; canonical refs exactly once; merge via dataclasses.replace
  [`statblocks.py:parse_stat_repair_output`](../../backend/app/pipeline/statblocks.py)

- Shared deterministic rules text (wave-1 prompt + repair prompt see the same constraints)
  [`statblocks.py:stat_block_rules_text`](../../backend/app/pipeline/statblocks.py)

**Wave-1 orchestration**

- Validation before commit; exactly one repair call; cancel poll; still-invalid ⇒ fail event
  [`build_in.py:run_build_in`](../../backend/app/pipeline/build_in.py)

- Wave-1 prompt stat-block contract + embedded vocabulary (AD-16)
  [`build_in.py:build_wave1_prompt`](../../backend/app/pipeline/build_in.py)

**Tests**

- I/O matrix: OK/persist, missing+repaired, invalid+repaired, still-invalid fail, budget, bad refs, fences, determinism, wave-2 pin
  [`test_build_in_pipeline.py`](../../backend/tests/test_build_in_pipeline.py)

- Validator unit matrix + repair-parse/prompt pins
  [`test_statblocks.py`](../../backend/tests/test_statblocks.py)