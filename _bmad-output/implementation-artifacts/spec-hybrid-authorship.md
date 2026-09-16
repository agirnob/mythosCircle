---
title: 'Hybrid Authorship — Two Paths: Partially-Authored Seeds & Fully-Authored Direct Commit'
type: 'feature'
created: '2026-09-15'
status: 'draft'
baseline_commit: '749b81c'
review_loop_iteration: 1
context:
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
---

<!-- draft at CHECKPOINT 1 (revised round 2) — not frozen; owner gate at the end -->

## Intent

**Problem:** The build-in seed is free-form prose per entity (one line per
place/faction/figure). The DM has NO way to say what a figure WILL be
(role is the model's choice — owner note 2a), no way to author any part of
the generated content (a city's description, a figure's personality — owner
note 2b), no way to assert a relationship whose far end does not exist yet
("Ferdinand is enemy_of Y: generate Y" — owner note 2c), and no way to
commit a COMPLETE hand-made character WITHOUT the LLM touching it at all
(owner gate-1 ruling, 2026-09-15 round 2: a fully-authored character must
never enter the LLM pipeline).

**Approach:** Two character paths, explicitly separated:

1. **PARTIALLY AUTHORED (hybrid)** — seed entries grow from ``str`` to
   ``str | SeedEntry`` (plain strings stay legal). A ``SeedEntry`` carries
   authored description (places/factions), role + partial AR24 record
   (figures), and declared relations. Authored fields are ground truth,
   mechanically preserved (backfilled after every repair). Declared
   relations with an unseeded/uncommitted target MANDATE generation (one
   bounded re-emit; second miss fails with zero commits). Declared edges
   are pipeline-applied. Authored stat blocks are validated determinis-
   tically — invalid → the job fails naming them (authored fields are
   never repaired or mutated, not even by a repair pass); valid →
   commit byte-identical. There is NO bypass stamp (`dm_authored`) inside
   this pipeline: an authored block is not "trusted", it is *validated
   and preserved* — and a full character belongs on path 2, not here.
2. **FULLY AUTHORED (direct commit)** — a complete character (full AR24
   record + full stat block + relations) is committed with **ZERO LLM
   involvement of any kind**: zero generation, zero repair, zero
   enrichment, zero re-typing, zero mutation. The submitted character is
   the source of truth; validation determines acceptability and NEVER
   modifies it. Backend validation is deterministic and independent of
   the frontend completeness gate (which is UX only). Declared relation-
   ships are validated and applied deterministically by the pipeline —
   never sent through the LLM.

## The Two-Path Architecture

```
FULLY AUTHORED (path 2 — ZERO LLM):
  frontend completeness gate (UX only; "Add Character" disabled while
  incomplete/invalid)
    → backend enqueue completeness gate (rejects incomplete payloads)
    → deterministic validation of the FULL record + stat block + relations
        invalid → fail event, ZERO commits, ZERO LLM calls
    → valid → DIRECT commit (one revision) with declared edges applied
      deterministically; record + stat block commit byte-identical
      (no repair, no conform, no stamp, no canonical folding)
    NEW JOB KIND: ``add_character`` — the runner has NO provider at all,
    so zero-LLM is structural, not policed.

PARTIALLY AUTHORED (path 1 — hybrid, the build-in pipeline):
  structured seed entry (or plain string)
    → build_in waves (generation, record/stat gates, bounded repairs)
    → authored-field backfill (ground truth re-applied after repairs)
    → mandate re-emit for unresolved declared targets (ONE, then fail)
    → declared edges applied deterministically
    → commit (structured entries merge field-level / delta)
```

## Boundaries & Constraints

**Always (both paths):**
- DM-authored text/fields commit **verbatim** — byte-identical to the
  submission (pipeline re-applies authored values after every repair
  pass and before commit; a repair that drifts them is reverted
  deterministically, never repaired by the LLM).
- Declared relation edges are **pipeline-applied** at commit
  (deterministic): ``(src, dst, type, counter)`` with the canonical
  direction from the kind rules; model-emitted duplicates collapse via
  the existing mirror/duplicate rules. Declared relations are never sent
  through the LLM for interpretation or re-typing — either path.
- Target matching uses ``normalize_entity_name`` (the merge/twins
  predicate).
- Payload validation rejects unknown seed-keys and malformed members at
  enqueue (422 / ``InvalidJobInputError``) AND at the runner re-check
  (``JobPayloadError``), the existing double gate.
- Plain-string build-in entries keep today's semantics EXACTLY.
- The chunked wave-1 path carries the full declared-relations/mandate
  block in EVERY chunk and the mandate check runs on the ASSEMBLED
  roster.

**Path 2 (fully authored) — Always:**
- The runner accepts NO provider and never imports one; ``max_llm_calls``
  is fixed at 0 at enqueue. Zero LLM is structural.
- Validation is reject-only: any violation in the record shape, role
  consistency, stat block, or relations rejects with the named violations
  — zero commits, zero partial writes, zero mutation attempts. NO
  repair, NO conform, NO power/color stamps, NO canonical folding of the
  submitted stat block: the committed record is byte-identical to the
  submission (deterministic validators that only READ are the checks).
- Completeness is enforced at ENQUEUE (backend), independent of the
  frontend gate: missing required fields reject at submit time.
- Declared relations must resolve to committed world entities — there is
  no generation in this path, so an unresolved target is a rejection
  named in the error (no mandate).
- A (kind, normalized-name) collision with a committed entity rejects
  with the existing entity named — the DM edits or deletes it first
  (decision gate 4).
- The stat-block gate is the same issue set the generated pipeline treats
  as terminal-after-repair (shape/identity/spells/band/frail); the
  over-powered stamp is NOT a rejection (owner verdict 2026-09-12: an
  over-powered block is acceptable and commits — on path 2 it simply
  commits unstamped, because "not modify it" forbids adding the stamp).

**Path 2 — Never:**
- No LLM call, no provider import, no budget for repairs, no mandate
  generation, no merge/overwrite of an existing entity, no mutation of
  the submitted record (folds, conform, stamps, canonicalization), no
  addition of model-generated fields.
- No `power.verdict: "dm_authored"` anywhere in the project — the
  generated pipeline has no bypass concept; the direct path IS the
  bypass because the LLM is never involved.

**Path 1 (hybrid) — Always:**
- Authored stat blocks are VALIDATED (deterministic, reject-only for the
  authored parts): invalid → the job fails naming the entry + violations
  (zero commits — the wave-1 contract), and NO repair pass touches an
  authored block (reparations would mutate ground truth). Valid authored
  blocks commit byte-identical with zero repair calls.
- Role pin (``role``) is enforced at the stat gate: ``identity.role``
  must equal the pinned role (currently prose-only guidance).
- Declared relations: targets must be named; unseeded/uncommitted targets
  MANDATE generation (wave-1 output must contain an entity with that
  normalized name); ONE bounded re-emit (frozen roster, cold+seeded, the
  ``_anchor_repair`` shape); second miss fails with ZERO commits — a
  DM-demanded endpoint is load-bearing (distinct from the 2026-09-11
  edgeless-commit verdict: who demanded the entity).

**Never (either path):**
- Fuzzy target matching (no similarity scoring); blank/description-only
  targets are payload errors.
- Touching the generate/candidates or regenerate paths, or note 3
  (one-time characters).
- Frontend work beyond the API contract; the entity-card UI is the
  follow-up story.

## I/O & Edge-Case Matrix

### Path 1 — hybrid (unchanged rows + authored-stat rows)

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| STRING_LEGACY | `places: ["Old Town"]` | today's behavior byte-for-byte | unchanged |
| AUTHORED_DESCRIPTION | `{name: "Ferdinand", description: "crumbling…"}` | committed ``text`` == authored, even if the model drifts (backfill) | drift reverted, never repaired |
| AUTHORED_RECORD | figure entry with partial ``record`` | authored values verbatim; every OTHER field generated; repairs leave authored fields byte-identical (backfill) | same |
| ROLE_PIN | figure entry ``role: "BBEG"`` | committed ``data.role`` == BBEG; ``identity.role`` == BBEG enforced at the stat gate (repair → fail after ceiling) | violation, not prose |
| HYBRID_AUTHORED_BLOCK_VALID | figure entry with a VALID authored ``stat_block`` | commits byte-identical, ZERO repair calls | n/a |
| HYBRID_AUTHORED_BLOCK_INVALID | figure entry with an INVALID authored ``stat_block`` | job FAILS naming entry + violations, ZERO commits, NO repair call (authored = never mutated) | fail loud — the DM fixes the block or moves it to path 2 |
| DECLARED_TO_SEEDED / TO_COMMITTED / MANDATE_HIT / MANDATE_MISS / BLANK_TARGET / UNKNOWN_SEED_KEY / RELATION_MALFORMED / CHUNKED_MANDATE | as in round 1 | as in round 1 (named-only targets; one re-emit; second miss → zero commits; blank targets rejected) | as in round 1 |
| STRUCTURED_REBUILD | structured re-submit of a committed (kind, name) | FIELD-LEVEL merge (authored overwrite, absent keep committed values) — delta semantics | per gate 3 |
| STRING_REBUILD | plain-string re-submit | full-overwrite, unchanged | unchanged |

### Path 2 — directly authored character (`add_character` job)

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| DIRECT_VALID | complete record + valid stat block + resolvable relations | ONE revision commits the record + stat block byte-identical; declared edges applied in the SAME transaction; result: {entity_id, revision_id, edges, relation_results} | n/a |
| DIRECT_INVALID_STAT | stat block with any terminal-class violation (shape, identity, spells, under-band, frail) | fail event naming the violations; ZERO commits; ZERO LLM calls | reject (validate, never modify) |
| DIRECT_INVALID_RECORD | missing/non-blank-violating field, bad role, boss-section mismatch, identity.role ≠ record role | enqueue or run rejection naming the field(s); ZERO commits; ZERO LLM | reject |
| DIRECT_INCOMPLETE | missing required fields (the frontend gate would disable Add) | ENQUEUE gate rejects (backend completeness independent of the UI); ZERO commits; ZERO LLM | 422 at submit |
| DIRECT_OVER_POWERED | Monster block over the band | ACCEPTABLE (owner verdict 2026-09-12) — commits byte-identical, NO stamp (path 2 never modifies) | n/a |
| DIRECT_RELATION_OK | declared edge to a committed entity | applied deterministically | n/a |
| DIRECT_RELATION_UNRESOLVED | target names no committed entity | reject naming the target (no generation in this path — no mandate) | reject |
| DIRECT_RELATION_KIND_BREACH | located_in targeting a character etc. | reject naming the kind-rule breach | reject |
| DIRECT_RELATION_MALFORMED | type ∉ EDGE_TYPES, non-int counter, blank target | reject at both gates | reject |
| DIRECT_NAME_COLLISION | (kind, normalized name) == committed entity | reject naming the existing entity (edit/delete first) | per gate 4 |
| DIRECT_UNKNOWN_KEY | record key outside the authorable set | reject naming the key | reject |

## Code Map

- `backend/app/store/jobs.py` — new job kind ``add_character``:
  ``_validate_add_character_payload`` (enqueue gate: completeness =
  FULL AR24 record incl. non-blank identity/lore/world-integration/
  personality/secret/rumor/party_hook, role ∈ ROLES, boss per role,
  stat_block dict, relations well-formed and targets non-blank; unknown
  keys rejected; ``max_llm_calls = 0`` forced). ``_run_job`` dispatch has
  NO provider for this kind.
- `backend/app/pipeline/direct.py` (new) — ``run_add_character(job)``:
  deterministic validation (reuse ``payload_section_violations`` +
  ``collect_stat_issues`` + role-consistency + edge kind rules) →
  ``commit_subgraph`` (one revision) with declared edges applied in the
  same transaction; ``complete_job``/``fail_job`` with named violations.
  No provider import, no budget beyond reads.
- `backend/app/pipeline/worker.py` — dispatch ``add_character`` to
  ``run_add_character(job)`` (no provider argument).
- `backend/app/pipeline/build_in.py`:
  - ``_check_sections`` (3620): str-or-SeedEntry shape, the authorable
    key set, relations well-formedness; authored ``stat_block`` flagged
    for the validate-don't-repair rule.
  - ``build_wave1_prompt`` (2394) + ``build_wave1_chunk_prompt`` (2607):
    DM-AUTHORED blocks + DECLARED RELATIONS / MANDATORY ENTITIES.
  - ``_seed_authored`` + ``_backfill_authored`` (ground-truth map and
    re-application after record/stat repairs).
  - ``_mandate_check`` + ``_MandatedTargetError`` + ONE bounded re-emit
    (the ``_OrphanRetryError`` 3214 shape); runs on the ASSEMBLED roster
    before commit; second miss → fail, zero commits.
  - ``_declared_edges``: pipeline-built edges appended at commit,
    kind-validated via the existing rules.
  - ``_merge_with_world`` (1498): structured entries merge field-level
    (gate 3).
  - ``_enforce_stat_blocks`` (985): authored blocks are VALIDATED FIRST;
    invalid → fail naming them before any repair call; valid → skipped
    by repairs (byte-identical).
- `frontend/src/` (contract only) — the Add Character surface's
  completeness gate: all required fields non-blank + role/level valid →
  "Add Character" enabled; the gate is UX — the backend enforces the
  same completeness at enqueue.
- Tests: `backend/tests/test_direct_character.py` (new) + additions to
  `backend/tests/test_build_in_pipeline.py`.

## Tasks & Acceptance

1. `add_character` job kind: enqueue completeness gate (DIRECT_INCOMPLETE,
   DIRECT_UNKNOWN_KEY, DIRECT_RELATION_MALFORMED), zero-budget, worker
   dispatch without a provider.
2. `run_add_character`: full deterministic validation (record + stat +
   role consistency + relations), one-revision commit with declared
   edges, rejection events with named violations (DIRECT_VALID /
   DIRECT_INVALID_STAT / DIRECT_INVALID_RECORD / DIRECT_RELATION_OK /
   DIRECT_RELATION_UNRESOLVED / DIRECT_RELATION_KIND_BREACH /
   DIRECT_NAME_COLLISION / DIRECT_OVER_POWERED pins).
3. **Owner-mandated zero-LLM acceptance: tests where the provider would
   raise if called — a valid, an invalid, and an incomplete fully
   authored character each prove ZERO LLM invocations, and only the
   valid one commits.** (Three tests, counting provider, assert
   ``provider_calls == []`` in every case and the commit state.)
4. Hybrid payload validation + rendering (single + chunk): STRING_LEGACY
   pin, AUTHORED_DESCRIPTION/RECORD, ROLE_PIN.
5. Hybrid authored-stat validation rule: HYBRID_AUTHORED_BLOCK_VALID
   (zero repairs) and HYBRID_AUTHORED_BLOCK_INVALID (fail, zero commits,
   no repair call — counting provider proves the repair was never
   offered).
6. Mandate check + re-emit + second-miss fail + CHUNKED_MANDATE.
7. Declared-edge application (both paths) + kind validation + duplicate
   collapse.
8. Structured-entry field-level merge (STRUCTURED_REBUILD) with
   STRING_REBUILD unchanged.
9. Full suite: 1319 existing backend tests stay green.

## Spec Change Log

- 2026-09-15 round 1: draft with gate-1 = trusted authored stat blocks
  (``dm_authored`` stamp).
- 2026-09-15 round 2 (owner gate-1 ruling): REPLACED. No stamp, no
  bypass anywhere. Two explicit paths: fully-authored characters commit
  through a new zero-LLM direct job kind; hybrid entries validate
  authored blocks reject-only (never repair them). Owner mandate: zero-
  LLM acceptance tests for valid/invalid/incomplete direct characters.

## Design Notes

- **Why a separate job kind for path 2.** "Zero LLM" must be structural,
  not policed: the direct runner has no provider parameter and the worker
  dispatch for `add_character` never constructs one. A flag inside
  build_in would share the wave machinery, where one misordered check
  could silently hand an authored character to a repair call.
- **Why authored stat blocks in hybrid fail rather than repair.** The
  owner's rule extends to every authored field: ground truth is
  preserved, never mutated. Repairs exist to fix MODEL output; an
  authored block that fails validation is a submission error — the DM
  fixes it and resubmits (or uses path 2, which is the same validation
  without the generation cost). No pass, no conform, no "trusted" stamp:
  validation decides, and the acceptable outcome is byte-identical
  preservation.
- **No mandate on path 2.** Generation is the hybrid path's answer to an
  unresolved target. The direct path has no generator, so an unresolved
  declared target is simply invalid — the error names it and the DM
  builds/commits it first. This keeps "declared relations validated and
  applied deterministically" true on both paths.
- **Path 2 validation = the generated pipeline's terminal issue set.**
  Reuse ``collect_stat_issues``/``payload_section_violations`` so the two
  paths can never disagree about what "acceptable" means; only the
  response differs (reject vs. repair). The over-powered stamp stays a
  generated-path notion — on path 2, "no modification" wins and the
  block commits unstamped.
- **Authored fields live in the pipeline, not the prompt contract.** The
  model sees them as immutable; the pipeline enforces immutability
  mechanically (backfill after every repair). Prompt promises are not
  guarantees (the record-repair re-echo lesson, 2026-09-09..11).

## Verification

- `uv run --directory backend pytest -q` (1319 + new pins).
- `uv run --directory backend ruff check . && ruff format --check . &&
  mypy app`.
- Live smoke (owner, dev box): hybrid Ferdinand entry (description +
  relation to an unseeded city) generates the mandated city and commits
  authored text verbatim; one fully-authored Seraphine `add_character`
  job commits with the jobs list showing zero LLM calls in
  ``llm_calls`` and the journal dir containing no file for that job.

## Suggested Review Order

1. Two-Path Architecture + CHECKPOINT 1 gates (owner).
2. Path-2 matrix rows vs. the Always/Never list (zero-LLM proof rows
   first).
3. Path-1 matrix vs. the round-1 rows still present (Gate 2/3 unchanged).
4. Code Map vs. the extracted contract surface (jobs.py:532/575,
   build_in.py:2394/3620/1498/3214/985, candidates.py:93-157/205).

---

## CHECKPOINT 1 (revised round 2) — owner decision gates

**[A] Approve the revision, or [E] edit:**

1. **REPLACED (gate-1 ruling, round 2): two-path architecture.**
   - Path 2 (fully authored): new ``add_character`` job kind, structural
     zero-LLM (no provider in the runner), backend completeness gate at
     enqueue, deterministic reject-only validation, direct one-revision
     commit, byte-identical record — no stamp, no fold, no conform, no
     mutation; declared edges applied deterministically; no mandates
     (unresolved targets reject). Owner-mandated acceptance: valid /
     invalid / incomplete each prove ZERO LLM invocations, only the
     valid one commits.
   - Path 1 (hybrid): authored stat blocks are validated reject-only —
     invalid authored block fails the job (zero commits, NO repair call);
     valid ones commit byte-identical with zero repairs. No
     ``power.verdict: "dm_authored"`` exists anywhere.
2. **UNCHANGED (keep exactly): relation targets are named-only; blank
   targets are rejected; mandated unseeded targets require generation in
   the hybrid workflow; one bounded re-emit; second miss → zero commits.**
3. **UNCHANGED (keep exactly): structured re-submit = fill-missing/delta;
   authored fields overwrite; absent fields preserve committed values;
   plain-string behavior remains full-overwrite.**
4. **NEW (small, my recommendation): direct-path name collisions.**
   Recommended: ``add_character`` rejects when the (kind, normalized
   name) already exists, naming the existing entity (the DM edits or
   deletes it first). Alternatives: (a) fill-missing merge like hybrid
   structured entries, (b) full overwrite. Rejection is recommended
   because path 2 is a zero-LLM, zero-mutation flow — silent overwrite
   would violate "not modify".

**Ask First (owner):** none — no UI work in this spec; the frontend
completeness gate is specified as a contract for the follow-up story.