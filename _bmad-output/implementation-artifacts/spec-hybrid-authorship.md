---
title: 'Hybrid Authorship — Two Paths: Template-Driven Direct Commit & Partially-Authored Seeds'
type: 'feature'
created: '2026-09-15'
status: 'draft'
baseline_commit: '749b81c'
review_loop_iteration: 2
context:
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
---

<!-- draft at CHECKPOINT 2 — owner decisions folded in; not frozen -->

## Intent

**Problem:** The build-in seed is free-form prose per entity. The DM has no
way to say what a figure WILL be (role), no way to author any part of the
generated content, no way to assert a relationship whose far end does not
exist yet, and no way to commit a COMPLETE hand-made character without the
LLM touching it at all.

**Approach — two character paths:**

1. **PARTIALLY AUTHORED (hybrid)** — seed entries grow from ``str`` to
   ``str | SeedEntry``; authored fields are ground truth, mechanically
   preserved; declared relations with unseeded/uncommitted targets MANDATE
   generation (one bounded re-emit, second miss → zero commits); declared
   edges are pipeline-applied; authored stat blocks are validated
   reject-only (invalid → job fails, NO repair call; valid → byte-
   identical, zero repairs).
2. **FULLY AUTHORED (direct commit)** — a complete character commits with
   **ZERO LLM involvement of any kind** (structural — the runner has no
   provider). The character is entered through a **template-driven form**
   (dropdowns + structured fields, NOT freeform text — owner ruling F1-F2),
   validated by a **three-layer deterministic gate** (owner ruling F3),
   and **never overwrites anything — every character is a fresh ULID and
   same-name characters coexist** (owner ruling F4).

## The Two-Path Architecture

```
FULLY AUTHORED (path 2 — ZERO LLM):
  template form (dropdowns/structured fields; prose only in description
  slots — conditional TTRPG exceptions live in the description, owner
  ruling F1-F2)
    → Frontend Gate: the canonical JSON Schema mirrored client-side —
      "Add Character" disabled + inline field violations while invalid
      (UX only; the API re-validates)
    → Synchronous Enqueue Gate: POST /api/characters validates the
      payload against the canonical JSON Schema (attribute names, dice
      formula pattern ^\d+d\d+([+-]\d+)?$, required keys) —
          PASS → job enqueued, 202 Accepted + job_id (zero LLM)
          FAIL → 422 Unprocessable Entity with exact schema-path
                 violations (attacks[0].damage: invalid pattern);
                 zero jobs spawned, zero LLM
    → Execution Worker Backstop: the worker re-runs the structural
      check INSIDE the DB transaction immediately before committing
      (backs out state changes between enqueue and run — deleted
      targets, moved world); failure → job_failed with
      error_code STRUCTURAL_VALIDATION_FAILURE, zero LLM, zero commits
    → VALID → direct commit: ONE revision, fresh ULID entity, record +
      stat block byte-identical (no repair, conform, stamp, or folding),
      declared edges applied deterministically in the same transaction.
    NEW JOB KIND: ``add_character`` — the runner has NO provider; zero-LLM
    is structural, not policed.

PARTIALLY AUTHORED (path 1 — the build-in pipeline):
  structured seed entry or plain string
    → build_in waves (generation, record/stat gates, bounded repairs on
      MODEL output only)
    → authored-field backfill (ground truth re-applied after repairs)
    → mandate re-emit for unresolved declared targets (ONE, then fail)
    → declared edges applied deterministically
    → commit: characters ALWAYS fresh ULIDs (never overwrite, ruling F4);
      places/factions keep today's upsert merge (delta for structured).
```

## Boundaries & Constraints

**Always (both paths):**
- DM-authored fields commit **verbatim** — byte-identical to the
  submission; the pipeline re-applies them after every repair pass
  (a repair that drifts them is reverted deterministically, never
  repaired by the LLM).
- Declared relation edges are **pipeline-applied** at commit
  (deterministic); never sent through the LLM for interpretation or
  re-typing — either path.
- **Ruling F4 (identity):** every character is identified by its ULID
  (`entity.id`); names are NOT unique — multiple "Seraphine" characters
  coexist. A new character NEVER overwrites an old one — in the direct
  path, the build-in path, or the candidates path (candidates already
  commit fresh; regenerate (3-5) and hand-editing (3-6) remain the DM's
  deliberate tools for changing an existing character — the ONLY
  legitimate replace paths). Same-name merge/upsert for characters is
  REMOVED; place/faction upsert (kind, normalized name) is unchanged.
- **Workflow consequence of F4 (S2/A1 round 3):** re-seeding is
  ADDITIVE — a re-seeded figure is a new entity with no edges (the old
  one keeps its relationships). REGENERATE is the blessed update tool
  for characters; the build-in result audit makes "N new · 0 merged"
  unmistakable; same-name surfacing + bulk-prune are acceptance criteria
  of the follow-up UI story (moved out of fully-deferred).
- **Relation targets — the three-tier contract (round 4 ruling):**
  `{target_id} | {target_key} | {target_name}` — Tier 1 committed ULID
  (searchable dropdown), Tier 2 staged batch key (same-payload
  resolution, fresh ULIDs, atomic edge), Tier 3 freeform name
  (hybrid: mandate; direct: blocked at the form). A `target_name`
  matching one committed entity resolves to it; ≥2 → reject naming the
  matches; 0 → hybrid generate / direct reject. `target_id`/`target_key`
  never touch the name matcher.
- Payload validation rejects unknown keys and malformed members at both
  gates (enqueue 422 / runner JobPayloadError) — the existing double
  gate, extended to path 2 by the dual-layer ruling F3.
- Plain-string build-in entries keep today's semantics EXACTLY, except
  characters: a plain-string character entry no longer overwrites — it
  commits fresh (F4), and the wave-2 roster-twin drop stays as the
  internal duplication guard (wave 2 must not re-create wave-1 subjects
  in the same build).
- The chunked wave-1 path carries the full declared-relations/mandate
  block in EVERY chunk; the mandate check runs on the ASSEMBLED roster.

**Path 2 (fully authored) — Always:**
- Template/form-driven input: every structural field is a dropdown or
  fixed-input slot (stat names, classes, dice formulas) so non-canonical
  shapes cannot be produced by the UI. **Freeform text exists ONLY in
  prose/description slots** — conditional exceptions ("on a critical hit
  against a creature wearing heavy armor, target is restrained… unless
  they pass a DC 14 Str save") belong in the action's description, per
  the owner's F1-F2 ruling. The canonical JSON Schema pins: skill entries
  as ``{name, description}`` text blocks; actions as
  ``{name, description, damage?}`` with ``damage`` matching
  ``^\d+d\d+([+-]\d+)?$`` (plus the optional space variant normalized by
  the form); stats/class/level from fixed vocabularies.
- The runner accepts NO provider, imports no provider, and enqueues with
  ``max_llm_calls = 0`.
- Validation is reject-only: any violation — record shape, role
  consistency, stat block, relations — rejects with the named
  violations. NO repair, NO conform, NO stamps, NO canonical folding:
  the committed record is byte-identical to the submission.
- Completeness is enforced at the SYNC enqueue gate (backend), which
  also validates the stat block — a structurally broken character is a
  422 at submit, never an async failure (F3).
- Declared relations must resolve to committed entities — there is no
  generation here. The direct form accepts ONLY Tier 1 (ULID) and Tier 2
  (staged batch) targets; a `target_name` in the `add_character` payload
  is a schema violation (freeform is blocked at the form AND rejected by
  the enqueue gate — path 2 has no mandate access).
- Name collisions DO NOT reject: every submission commits a fresh ULID
  (F4) — the pre-mortem gate-4 rejection is replaced by this ruling.

**Path 2 — Never:**
- No LLM call, no provider import, no budget, no mandate generation, no
  overwrite of an existing entity (fresh ULID always), no mutation of the
  submitted record, no addition of model-generated fields.
- No `power.verdict: "dm_authored"` anywhere — the generated pipeline
  has no bypass concept.

**Path 1 (hybrid) — Always:**
- Authored stat blocks are validated reject-only (invalid → job fails
  naming entry + violations, zero commits, NO repair call; valid →
  byte-identical, zero repairs).
- Role pin enforced at the stat gate: ``identity.role`` must equal the
  pinned role.
- Declared relations: named-only targets; unseeded/uncommitted targets
  MANDATE generation; ONE bounded re-emit (frozen roster, cold+seeded);
  second miss → zero commits (DM-demanded endpoints are load-bearing —
  distinct from the 2026-09-11 edgeless-commit verdict by who demanded
  the entity).
- Characters commit FRESH ULIDs — the wave-1 wave-2/committed merge for
  kind=character is removed (F4); ``_drop_roster_twins`` stays as an
  intra-build guard; places/factions keep the upsert merge (structured
  entries = delta per gate 3).

**Never (either path):**
- Fuzzy target matching; blank/description-only targets are payload
  errors.
- Touching the generate/candidates or regenerate paths, or note 3.
- The entity-card UI implementation goes beyond this spec's CONTRACTS
  (form shape, mirror validator, API) — the UI story builds on them.

## I/O & Edge-Case Matrix

### Path 1 — hybrid (round-1 rows; character merge rows replaced)

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| STRING_LEGACY | `places: ["Old Town"]` | today's behavior byte-for-byte (places/factions) | unchanged |
| CHAR_FRESH_ULID | a key-figure name matching a committed character | commits a NEW entity with a fresh ULID — never overwrites (F4); the old character is untouched | n/a |
| AUTHORED_DESCRIPTION / AUTHORED_RECORD / ROLE_PIN | structured entries | authored values verbatim, rest generated, repairs preserve authored (backfill) | drift reverted |
| HYBRID_AUTHORED_BLOCK_VALID / _INVALID | valid/invalid authored stat block | valid → byte-identical, zero repairs; invalid → job fails, zero commits, NO repair call | fail loud |
| DECLARED_MANDATE_HIT / MANDATE_MISS / BLANK_TARGET / UNKNOWN_SEED_KEY / RELATION_MALFORMED / CHUNKED_MANDATE | as round 1 | as round 1 (named targets; one re-emit; second miss → zero commits) | as round 1 |
| TIER1_ULID_TARGET | relation `{target_id}` (committed entity) | direct ULID binding — matcher never runs; edge wires to that entity | n/a |
| TIER2_STAGED_TARGET | relation `{target_key}` naming another staged seed (same payload) | both entities commit fresh ULIDs; the edge wires atomically in one transaction | n/a |
| TIER3_HYBRID_MANDATE | relation `{target_name}` matching nothing | mandate generation (one re-emit; second miss → zero commits) | as round 1 |
| TIER3_NAME_MATCHES_ONE | `{target_name}` normalized-matches exactly one committed entity | resolves to it — no generation | n/a |
| DECLARED_AMBIGUOUS_TARGET | `{target_name}` matches 2+ committed entities | reject naming the matches — the DM targets by ULID (Tier 1); the UI's exact-match prompt surfaces "Did you mean [Name] (#ULID)?" before submission | reject |
| SAME_NAME_SAME_BUILD | two identical-name seeds/figures in ONE payload | TWO distinct entities, fresh ULIDs, both wired (Tier-2 matched by staged key) — the roster machinery must not collapse them | pin test |
| SAME_NAME_TWO_BUILDS | the same name re-seeded in a later build | a NEW distinct entity; the old one untouched (F4); audit shows 0 merged | pin test |
| STRUCTURED_REBUILD | structured re-submit of a committed place/faction | field-level delta merge (gate 3) | n/a |
| STRING_REBUILD_CHARACTER | plain-string re-submit of a committed character | NOT an update anymore — fresh ULID commit (F4); merge audit shows 0 merged characters | n/a |

### Path 2 — directly authored character (`add_character` job)

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| DIRECT_VALID | complete template form output | 202 + job_id; worker: one revision, fresh ULID, committed byte-identical + declared edges in-txn; result {entity_id, revision_id, edges} | n/a |
| DIRECT_INVALID_STAT | damage pattern `1d6x+2`, unknown stat key | **422 at submit** with schema-path violations (attacks[0].damage: invalid pattern); zero jobs, zero LLM | sync reject |
| DIRECT_INCOMPLETE | missing required field | **422 at submit**; frontend gate also disabled Add | sync reject |
| DIRECT_INVALID_RECORD | bad role, boss mismatch, identity.role ≠ record role | 422 or STRUCTURAL_VALIDATION_FAILURE at run backstop, named | reject |
| DIRECT_OVER_POWERED | Monster block over the band | acceptable (verdict 2026-09-12); commits byte-identical, no stamp | n/a |
| DIRECT_RELATION_OK | declared edge to a committed ULID | applied deterministically | n/a |
| DIRECT_RELATION_UNRESOLVED / AMBIGUOUS / KIND_BREACH / MALFORMED | target gone/ambiguous/wrong kind/blank | reject naming the target(s) — no generation in this path | reject |
| DIRECT_SAME_NAME | another "Seraphine" exists | commits a SECOND distinct ULID character (F4) | n/a |
| DIRECT_WORLD_MOVED | target deleted between enqueue and run | run backstop rejects: job_failed STRUCTURAL_VALIDATION_FAILURE, zero LLM, zero commits (F5) | reject |
| DIRECT_UNKNOWN_KEY | record key outside the schema | 422 naming the key | reject |

## The Three-Layer Validation Gate (owner ruling F3)

| Gate | Timing | Responsibility | HTTP / System Response |
|------|--------|----------------|------------------------|
| Frontend Gate | real-time (client) | mirrors the canonical JSON Schema validation — disable "Add Character" + highlight field violations | disabled button / inline errors |
| Synchronous Enqueue Gate | sync (API) | POST /api/characters validates the payload against the canonical JSON Schema (attribute names, `^\d+d\d+([+-]\d+)?$`, required keys) before any job row exists; PASS → enqueue → 202 + job_id; FAIL → 422 with exact schema-path violations | 202 / 422, zero jobs spawned, zero LLM |
| Execution Worker Backstop | async (worker) | re-runs the structural check INSIDE the DB transaction immediately before committing — covers state changes between enqueue and run (deleted targets, moved world) | job_failed, error_code: STRUCTURAL_VALIDATION_FAILURE, zero LLM |

The canonical JSON Schema is the single source of truth consumed by the
frontend mirror AND the API gate (one definition — the schema.ts/OpenAPI
surface or a shared module; the two can never disagree). A client bypass
(hand-crafted JSON) hits the synchronous 422 and surfaces field-level
errors without launching a job.

## Relation Target Resolution — Three Tiers (owner ruling, round 4)

Prevents runtime `AMBIGUOUS_RELATION_TARGET` rejections and unintended
LLM entity generation by making the UI emit the least-ambiguous target
form possible.

| Tier | UI input | Payload emitted | Backend path |
|------|----------|-----------------|--------------|
| 1 — Existing entities (committed) | searchable dropdown of committed campaign entities | `{target_id: "01H8X…"}` | direct ULID binding — no normalization, no collision ambiguity |
| 2 — Staged entities (same build batch) | dropdown of entities staged in the multi-card session | `{target_key: "staged_entity_02"}` (or the exact staged name) | intra-payload resolution (`SAME_NAME_SAME_BUILD`): both entities commit fresh ULIDs and the edge wires atomically in one transaction |
| 3 — Freeform text ("+ Declare Uncreated Target") | raw text (e.g. "The Shadow Queen") | `{target_name: "The Shadow Queen"}` | **Hybrid path:** mandate generation — unseeded/uncommitted target must be generated (one bounded re-emit, second miss → zero commits). **Direct path (add_character): BLOCKED at the form** — freeform targets are disabled; the UI forces Tier 1 selection or Tier 2 staging (path 2 has zero LLM mandate access, by definition). |

Payload contract for a declared relation: `{type, counter?, target_id |
target_key | target_name}` — exactly one target key per relation;
ambiguity is resolved at the UI, not discovered at runtime.

**Frontend validation invariants:**
- Exact-match warning: typing a freeform string that matches a committed
  entity surfaces an autocomplete prompt — `"Did you mean [Name]
  (#ULID)?"` — before the Tier-3 declaration is accepted.
- Path-2 guardrail: the freeform target input is disabled on the direct
  form; the DM must pick an existing entity or stage the target as a
  manual sheet tab (the add_character payload cannot carry
  `target_name` — the enqueue schema rejects it).

**Backend rule (both paths):** a `target_name` that normalized-matches
exactly ONE committed entity resolves to it (no generation — the DM
declared an existing name); ≥2 matches → reject naming the matches (the
DM targets by ULID — Tier 1); zero matches → hybrid: mandate; direct:
reject. `target_id`/`target_key` bypass the matcher entirely.

## F5 — the queue-window race, explained

The `add_character` job is enqueued (202) and worked asynchronously.
Between submit and the worker's commit, another job (build-in, edit,
delete) may change the world: a declared-relation target can be deleted,
a role/name can change, a same-name character can be added. The enqueue
gate validated against the world at submit time; the world at run time is
what the commit sees. The Execution Worker Backstop resolves this: the
worker re-reads the world fresh and re-validates (schema + relation
resolution) INSIDE the same transaction that commits. Any failure → the
job fails with `error_code: STRUCTURAL_VALIDATION_FAILURE`, zero commits,
zero LLM calls — never a partial write, never a dangling edge (the store
commit's dangling-check is the final invariant). The DM resubmits. This
is the same race in every async-commit flow in the stack; path 2 just
makes it a named failure instead of a raw exception.

## Code Map

- `backend/app/store/jobs.py` — new kind ``add_character``:
  ``_validate_add_character_payload`` (the enqueue half of the dual gate —
  canonical schema: completeness, stat-block structure incl. dice
  pattern, relations well-formed, unknown keys rejected);
  ``max_llm_calls = 0`` forced.
- `backend/app/api/` — NEW route ``POST /api/characters`` (repo
  convention holds: this is a thin synchronous gate that enqueues the
  ``add_character`` job) — 202 {job_id} or 422 {schema-path violations}.
- `backend/app/pipeline/direct.py` (new) — ``run_add_character(job)``:
  re-read world + re-validate inside the commit transaction (backstop);
  ``commit_subgraph`` one revision with declared edges; fail with
  ``error_code STRUCTURAL_VALIDATION_FAILURE``; no provider import.
- `backend/app/pipeline/worker.py` — dispatch ``add_character`` to
  ``run_add_character(job)`` (no provider argument).
- `backend/app/pipeline/build_in.py`:
  - ``_check_sections`` (3620): str-or-SeedEntry shape, authorable key
    set, relations; authored ``stat_block`` flagged validate-don't-repair.
  - ``build_wave1_prompt`` (2394) + chunk prompt (2607): DM-AUTHORED
    blocks + DECLARED RELATIONS / MANDATORY ENTITIES.
  - ``_seed_authored`` / ``_backfill_authored`` — ground-truth map and
    re-application.
  - ``_mandate_check`` / ``_MandatedTargetError`` / ONE bounded re-emit
    (the ``_OrphanRetryError`` 3214 shape) on the ASSEMBLED roster.
  - ``_declared_edges`` — pipeline-built, kind-validated, duplicate-
    collapse; **three-tier targets**: `target_id` binds directly;
    `target_key` resolves intra-payload (both entities fresh-ULID, edge
    atomic); `target_name` resolves by exactly-one normalized match
    (0 → mandate / direct-reject; ≥2 → reject naming the matches).
  - ``_merge_with_world`` (1498): kind=character entries STOP merging —
    fresh ULID commit (characters removed from the dedup key); places/
    factions unchanged (delta for structured per gate 3); the merge
    audit's merged/unchanged now counts place/faction only.
  - ``_enforce_stat_blocks`` (985): authored blocks validated first;
    invalid → fail naming them BEFORE any repair call.
- `frontend/src/` (contract only) — the template form (dropdowns/
  structured slots; prose only in description fields), the canonical
  schema mirror validator (button state + inline violations), the
  three-tier relation target widget: Tier-1 searchable entity dropdown
  (emits `target_id`), Tier-2 staged-batch dropdown (`target_key`),
  Tier-3 freeform with the exact-match autocomplete prompt ("Did you
  mean [Name] (#ULID)?") AND the path-2 guardrail (freeform disabled on
  the direct form). BuildInView's merge-audit copy ("Same-name entities
  merge into the existing world") gets reworded for the character
  exception.
- Tests: `backend/tests/test_direct_character.py` (new),
  `backend/tests/test_hybrid_authorship.py` (new), reworked merge pins in
  `backend/tests/test_build_in_pipeline.py`.

## Tasks & Acceptance

1. `add_character` kind + POST /api/characters sync gate: DIRECT_VALID /
   DIRECT_INVALID_STAT / DIRECT_INCOMPLETE / DIRECT_UNKNOWN_KEY /
   DIRECT_RELATION_MALFORMED pins (202/422 semantics, zero jobs spawned
   on fail, zero LLM).
2. `run_add_character` backstop: DIRECT_INVALID_RECORD /
   DIRECT_WORLD_MOVED (job_failed STRUCTURAL_VALIDATION_FAILURE, zero
   LLM, zero commits), DIRECT_OVER_POWERED, DIRECT_RELATION_OK / _UNRESOLVED
   / _AMBIGUOUS / _KIND_BREACH, DIRECT_SAME_NAME.
3. **Owner-mandated zero-LLM acceptance: a valid, an invalid, and an
   incomplete fully-authored character each prove ZERO LLM invocations
   (counting provider raises if called); only the valid one commits.**
4. Hybrid payload validation + rendering (single + chunk): STRING_LEGACY,
   AUTHORED_DESCRIPTION/RECORD, ROLE_PIN pins.
5. Hybrid authored-stat validation: HYBRID_AUTHORED_BLOCK_VALID (zero
   repairs) / _INVALID (fail, zero commits, counting provider proves the
   repair was never offered).
6. Mandate check + re-emit + second-miss fail + CHUNKED_MANDATE +
   DECLARED_AMBIGUOUS_TARGET.
7. Declared-edge application (both paths) + kind validation + duplicate
   collapse + the THREE-TIER target resolution: TIER1_ULID_TARGET,
   TIER2_STAGED_TARGET (atomic fresh-ULID pair),
   TIER3_HYBRID_MANDATE / TIER3_NAME_MATCHES_ONE /
   DECLARED_AMBIGUOUS_TARGET; `target_name` on `add_character` is a
   schema violation (form blocked + 422).
8. **F4 merge rework:** CHAR_FRESH_ULID (hybrid + direct + candidates),
   STRING_REBUILD_CHARACTER, STRUCTURED_REBUILD (places/factions delta),
   wave-2 roster twins-guard preserved, PLUS the SAME_NAME_SAME_BUILD
   (two same-name figures in one payload → two entities) and
   SAME_NAME_TWO_BUILDS pins; the pre-2026-09-12-era character merge
   pins are reworked from "merged" to "new + audit shows 0 merged".
9. Full suite: the 1319 existing backend tests stay green EXCEPT the
   character-merge pins reworked in task 8 (enumerated in the review
   order below).

## Spec Change Log

- 2026-09-15 round 1: draft (gate-1 = trusted authored blocks).
- 2026-09-15 round 2 (owner): two-path architecture; no stamps; hybrid
  authored blocks validated reject-only; zero-LLM acceptance mandate.
- 2026-09-15 round 3 — CHECKPOINT 2: pre-mortem amendments (F1-F2
  template form; F3 three-layer gate; F4 ULID identity — never
  overwrite, character merge removed; F5 race explained; F6 pins).
- 2026-09-15 round 4 (this version): second-order + assumption-audit
  findings applied — workflow note (re-seed additive, regenerate is the
  blessed update, audit makes "N new" unmistakable, same-name surfacing
  + bulk-prune move into the follow-up UI story's acceptance) — and the
  owner's THREE-TIER relation-target ruling: `target_id` (committed
  ULID) / `target_key` (staged batch, atomic fresh-ULID pair) /
  `target_name` (hybrid mandate; blocked on the direct form, schema
  violation at its gate); exact-match autocomplete "Did you mean [Name]
  (#ULID)?"; SAME_NAME_SAME_BUILD / SAME_NAME_TWO_BUILDS pins.

## Verification

- `uv run --directory backend pytest -q` (1319 minus reworked merge pins,
  plus the two new test modules).
- `uv run --directory backend ruff check . && ruff format --check . &&
  mypy app`.
- Live smoke (owner, dev box): hybrid Ferdinand (description + relation
  to an unseeded city) generates the mandated city, authored text
  verbatim; two same-name direct characters commit as distinct ULIDs;
  a broken stat block submits as 422 before any job exists.

## Suggested Review Order

1. The three-layer gate table + F4 identity ruling vs. the merge-related
   existing tests (task 8 is the only rework of green tests).
2. Path-2 matrix (zero-LLM rows first).
3. Path-1 matrix (character rows replaced).
4. Code Map vs. the extracted surface (jobs.py:532/575,
   build_in.py:2394/3620/1498/3214/985, candidates.py:93-157/205).

---

## CHECKPOINT 2 — confirmed gates (round 3)

1. **Two-path architecture** — confirmed round 2 + amendments now folded:
   template-driven form (F1-F2), three-layer validation gate (F3),
   zero-LLM acceptance mandate.
2. **UNCHANGED exactly:** named-only relation targets; blank rejected;
   hybrid mandate generation; ONE bounded re-emit; second miss → zero
   commits.
3. **UNCHANGED exactly (scope adjusted by F4):** structured re-submit
   delta/fill-missing for PLACES/FACTIONS; plain-string places/factions
   full-overwrite; characters never merge at all (they are always fresh
   ULIDs).
4. **REPLACED by F4:** the direct-path name-collision rejection is gone —
   every character commits a fresh ULID; same-name characters coexist;
   never overwrite anywhere (candidates already fresh; regenerate/edit
   remain the deliberate replace tools).
5. **ADDED round 4 (owner ruling): the three-tier relation target
   contract** — `target_id` / `target_key` / `target_name`, exact-match
   autocomplete, path-2 freeform guardrail (enforced by form AND the
   enqueue schema).

**Ask First (owner):** none — UI stays contract-only in this spec.