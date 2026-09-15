---
title: 'Hybrid Authorship — DM-Authored Seeds & Mandated Relation Endpoints'
type: 'feature'
created: '2026-09-15'
status: 'draft'
baseline_commit: '749b81c'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/deferred-work.md'
---

<!-- draft at CHECKPOINT 1 — not frozen; owner decision gates at the end -->

## Intent

**Problem:** The build-in seed is free-form prose per entity (one line per
place/faction/figure). The DM has NO way to say what a figure WILL be
(role is the model's choice — owner note 2a), no way to author any part of
the generated content (a city's description, a figure's personality, a
full hand-made stat block — owner note 2b), and no way to assert a
relationship whose far end does not exist yet ("Ferdinand is enemy_of Y:
generate Y" — owner note 2c, clarified with the City of Ferdinand example).
The pipeline treats every seed line as a bare name and gives the DM's
intent no structured channel: sections are validated ``must be a list of
strings`` (jobs.py ``_validate_build_in_payload``, build_in.py
``_check_sections``), rendered as ``- {entry}`` lines, and everything —
name, role, text, record, relations — is invented by the model.

**Approach:** The seed entry grows from ``str`` to ``str | SeedEntry``
(plain strings stay legal — name-only seeds, bulk paste path). A
``SeedEntry`` carries the DM's authored fields — description (places/
factions), role + partial AR24 record (figures), relations — plus a
**mandate**: a declared relation whose target names nothing seeded and
nothing committed requires the pipeline to GENERATE the target entity
(exactly that name), with ONE bounded re-emit for a miss. DM-authored
fields are ground truth: the pipeline re-applies them after every repair
and before commit (the model cannot drift them), and declared relation
edges are applied by the pipeline itself — deterministic, never
model-re-typed. This is the 2b/2c feature in one contract: "the DM may
author any field the generator produces, for any kind; declared relations
mandate their endpoints."

## Boundaries & Constraints

**Always:**
- DM-authored text/fields commit **verbatim** — byte-identical to the
  seed (the store's commit path is the only writer; the pipeline re-applies
  authored values after every repair pass and before commit — a repair
  that drifts them is reverted deterministically, never repaired by the
  LLM). This extends the stat-repair's surgical-merge rule (scope-only
  merges, out-of-scope drift reverts) from sections to the seed.
- Declared relations with an unseeded, uncommitted target MANDATE
  generation: the wave-1 output must contain an entity whose
  ``normalize_entity_name`` equals the target's. Missing after ONE bounded
  re-emit (frozen roster, cold+seeded, the ``_anchor_repair`` shape) fails
  the job naming the targets, with ZERO commits — a DM-demanded endpoint
  is load-bearing. Distinguisher from the 2026-09-11 edgeless-commit
  verdict: who demanded the entity (DM-declared → mandatory; model-
  volunteered → commit + prune).
- Declared relation EDGES are pipeline-applied at wave-1 commit
  (deterministic): ``(src = declaring entry's entity, dst = target's
  entity, type, counter)`` with the canonical direction from the kind
  rules — the DM's assertion is ground truth, never fed through the model
  for re-typing. Model-emitted duplicates collapse via the existing
  mirror/duplicate rules.
- Target matching uses ``normalize_entity_name`` (the merge/twins
  predicate — casefold, leading article, whitespace, trailing punct).
- Payload validation rejects unknown seed-keys and malformed members at
  enqueue (422 / ``InvalidJobInputError``) AND at the runner re-check
  (``JobPayloadError``), mirroring the existing double gate.
- Plain-string entries keep today's semantics EXACTLY (prompt line,
  full-overwrite merge, model-authored everything) — all existing tests
  stay green.
- The chunked wave-1 path carries the full declared-relations/mandate
  block in EVERY chunk (the full-notes precedent, build_in.py:2620) and
  the mandate check runs on the ASSEMBLED roster (post-wiring), so a
  target named in chunk A only can be emitted by chunk B.

**Never:**
- Fuzzy target matching. A blank or description-only target is a payload
  error — the DM must NAME the endpoint (a descriptive name counts:
  "The Rival Port City"). The model emits exactly that name or the re-emit
  names the miss; no similarity scoring (decision gate 2).
- LLM repair of DM-authored fields "so they fit": authored values are
  never reworded; a validity conflict is resolved by filling the ABSENT
  fields or failing with the authored text intact. DM-authored stat
  blocks never burn a repair pass (decision gate 1).
- Touching the generate/candidates path, the regenerate path, or the
  one-time-character story (note 3) — build-in seed surface only.
- Changing plain-string merge semantics (full overwrite stays); the
  fill-missing rule applies ONLY to structured entries' authored fields
  (decision gate 3).
- Frontend work beyond the API consumer contract — the entity-card UI
  (note 1/2) is the follow-up story; this spec ships the contract the UI
  will send.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| STRING_LEGACY | `places: ["Old Town"]` | today's behavior byte-for-byte (prompt line, model-authored, full-overwrite merge) | unchanged |
| AUTHORED_DESCRIPTION | `{name: "Ferdinand", description: "crumbling metropolis…"}` | committed ``text`` == the authored description even if the model drifts (pipeline backfill) | model drift is reverted, never repaired |
| AUTHORED_RECORD | figure entry with ``record: {personality, secret}`` | committed record contains those values verbatim; EVERY other field generated; record repair fixing a missing field leaves authored fields byte-identical (backfill) | same |
| ROLE_PIN | figure entry ``role: "BBEG"`` | committed ``data.role`` == BBEG; ``stat_block.identity.role`` == BBEG enforced at the stat gate (mismatch → repair → fail after ceiling) | mismatch is a violation, not prose guidance |
| AUTHORED_STAT_BLOCK | figure entry ``record.stat_block: {...}`` | commits AS-IS (no AR25 gate, no repair call), stamped ``power: {verdict: "dm_authored"}``; advisory violations ride the job result, never fail | trust per gate 1 |
| DECLARED_TO_SEEDED | relation target == another seed entry's name | no mandate; edge applied at commit between the two generated entities | kind-rule violation → part of the re-emit round |
| DECLARED_TO_COMMITTED | target == committed entity name | no mandate; edge applied at commit to the committed row (post-merge id) | same |
| DECLARED_MANDATE_HIT | target names nothing | model emits entity with exactly that name → edge applied; entity carries the relation's kind-rule-compatible kind | kind mismatch → re-emit names it |
| DECLARED_MANDATE_MISS | target absent from the wave-1 roster | ONE bounded re-emit (frozen roster + named targets, cold+seeded); second miss → job fails naming the target, ZERO commits | fail loud |
| DECLARED_BLANK_TARGET | ``target: ""`` (or whitespace) | enqueue 422 / runner JobPayloadError — targets must be named | reject at both gates |
| UNKNOWN_SEED_KEY | ``{name: "X", class: "…"}`` (not in the authorable set) | 422 / JobPayloadError naming the key | reject |
| RELATION_MALFORMED | non-string type, non-int counter, type ∉ EDGE_TYPES | 422 / JobPayloadError naming the member | reject |
| STRUCTURED_REBUILD | re-submit of a structured entry whose (kind, name) is committed | FIELD-LEVEL merge: authored fields overwrite, absent fields keep the committed row's values (the submission is a delta — gate 3); byte-equal rows still skip (zero events) | per gate 3 |
| STRING_REBUILD | plain-string re-submit of the same name | today's full-overwrite merge, unchanged | unchanged |
| CHUNKED_MANDATE | target declared in chunk A, emitted by chunk B | assembled-roster check passes; edge applied post-wiring | missing → one re-emit on the assembled set |

## Code Map

- `backend/app/store/jobs.py` — ``_validate_build_in_payload`` (532): accept
  ``str | SeedEntry`` per section; new errors; caps: `BUILD_IN_MAX_ENTRIES`
  100, name ≤2000, serialized SeedEntry ≤ 8192 chars, record fields ≤2000,
  relations ≤30 per entry, counter int 0..1e6. Budget ``_build_in_budget``
  (575): structured entries weight as ``_WEIGHT_CHARACTER`` regardless of
  section; authored stat blocks count +1 call headroom (they skip the
  repair ceiling but the wave call itself costs the same).
- `backend/app/pipeline/build_in.py`:
  - ``_check_sections`` (3620): str-or-SeedEntry shape, the authorable key
    set (from ``IDENTITY_FIELDS``+``LORE_FIELDS``+``WORLD_INTEGRATION_FIELDS``+
    ``BOSS_FIELDS``+``personality/secret/rumor/party_hook/role/stat_block``),
    relations well-formedness.
  - ``build_wave1_prompt`` (2394) + ``build_wave1_chunk_prompt`` (2607):
    render entries as name + `[role: …]` + DM-AUTHORED blocks + a
    DECLARED RELATIONS / MANDATORY ENTITIES section (full block rides
    every chunk).
  - New ``_seed_authored(seed)`` map: entry → {name, role?, record…,
    text?, relations[]} — the pipeline's ground truth.
  - New ``_backfill_authored(entities, seeds)``: after the record/stat
    gates' repairs, re-apply authored values (text/record fields + stamp
    dm_authored power on authored blocks) before commit; byte-identical
    enforcement with the byte-equal skip.
  - New ``_mandate_check(roster, seeds, world)`` → missing targets list;
    ``_MandatedTargetError`` (the ``_OrphanRetryError`` 3214 shape) →
    ONE ``call_wave`` re-emit naming exactly the missing targets (frozen
    roster, ``_repair_sampling`` cold+seeded); second miss
    ``JobPayloadError`` with zero commits. Runs on the ASSEMBLED wave-1
    roster (single-call AND chunked/wiring path) before commit.
  - ``_declared_edges(roster, seeds, resolved_ids)``: pipeline-built
    EdgeInput list appended to wave-1's staged edges, kind-validated via
    the existing ``_edge_kind_ok``/`_normalize_edge_directions`; kind
    violations fold into the mandate re-emit round.
  - ``_merge_with_world`` (1498): for structured entries, field-level
    merge (authored overwrite, absent keep committed) instead of the
    whole-data replace.
  - ``_enforce_stat_blocks`` (985): skip validates/repairs for
    ``power.verdict == "dm_authored"`` blocks; advisory violations in the
    job result under ``dm_blocks``.
- `backend/app/pipeline/knowledge.py`: no changes (reuse); the role-pin
  gate adds a ``identity.role == pinned role`` violation where the
  stat-gate already checks role validity (790-803).
- `backend/tests/test_build_in_pipeline.py` + new
  `backend/tests/test_hybrid_authorship.py`.

## Tasks & Acceptance

1. Enqueue + runner payload validation for `str | SeedEntry` (both gates;
   STRING_LEGACY pin).
2. Prompt rendering (single + chunk): DM-AUTHORED blocks, DECLARED
   RELATIONS, MANDATORY ENTITIES.
3. Authored backfill (text + record fields) through record/stat repairs
   → committed verbatim (AUTHORED_DESCRIPTION / AUTHORED_RECORD pins).
4. Role pin + identity-consistency gate (ROLE_PIN).
5. DM stat-block trust (AUTHORED_STAT_BLOCK, advisory result block).
6. Mandate check + one bounded re-emit + second-miss fail (DECLARED_*
   pins incl. the CHUNKED_MANDATE row).
7. Declared-edge application + kind validation + duplicate collapse.
8. Structured-entry field-level merge (STRUCTURED_REBUILD) with
   STRING_REBUILD unchanged.
9. Full suite: 1319 existing backend tests stay green (STRING_LEGACY
   guarantees it); new matrix rows pinned by name.

## Spec Change Log

- 2026-09-15: created (draft). Owner notes 2a/2b/2c + note 4 merge
  semantics folded in; note 1 (entity-add UI) is the frontend follow-up.

## Design Notes

- **Who writes the declared edges — the pipeline, not the model.** The
  DM's relation is ground truth with a closed, validated shape (type +
  counter + named target); sending it through the model invites
  re-typing/dropping (measured: gemma re-types relation rows). The
  pipeline constructs the edge deterministically at commit, exactly like
  the merge audit it already computes.
- **Mandate vs. orphan verdicts.** The 2026-09-11 edgeless-commit rule
  covers model-volunteered orphans; a DM-declared endpoint is mandatory
  (fail loud after one re-emit), the same load-bearing logic as the
  wave-2 anchor rule. Written here so a reviewer cannot "simplify" the
  two together.
- **Authored fields live in the pipeline, not the prompt contract.** The
  model sees them as immutable; the pipeline enforces immutability
  mechanically (backfill after every repair). Prompt promises are not
  guarantees (the record-repair re-echo lesson, 2026-09-09..11).
- **Kind inference for mandated targets.** The target's kind is decided
  by the model under the kind rules (``located_in`` → a place; the
  re-emit round names any kind-rule breach). The DM can force it by
  naming the target in the matching section (a target that IS a seeded
  place resolves as a place — the declaration rides the seed's kind).
- **Serialized-entry cap 8192.** A partial record + stat block fits;
  a whole generous AR24 record is ~2-3k; the cap keeps the prompt cache
  warm and the chunk weights honest.

## Verification

- `uv run --directory backend pytest -q` (1319 + new pins).
- `uv run --directory backend ruff check . && uv run --directory backend
  ruff format --check . && uv run --directory backend mypy app`.
- Live smoke (owner, dev box): one build-in with a structured Ferdinand
  entry (description + relation to an unseeded city), one figure with
  role=BBEG + authored secret, and confirm (a) the mandated city is
  generated, (b) the authored text is verbatim in WorldView, (c) the job
  result's merge/context blocks tell the story.

## Suggested Review Order

1. Intent + gate-1/2/3 decisions (owner CHECKPOINT 1).
2. I/O matrix rows vs. the Always/Never list.
3. Code Map vs. the extracted contract surface (build_in.py:2394, 3620,
   1498, 3214; jobs.py:532; candidates.py:93-157, 205).
4. Tasks & Acceptance completeness — every matrix row named.

---

## CHECKPOINT 1 — owner decision gates

**[A] Approve the spec with the three decisions below, or [E] edit:**

1. **DM-authored stat blocks — TRUSTED.**
   Recommended: an authored ``record.stat_block`` commits as-is (no AR25
   gate, zero repair calls), stamped ``power: {verdict: "dm_authored"}``,
   with advisory violations listed in the job result (never fail). The DM
   keeps the wheel; exports render what is committed. Alternative:
   validate-and-fail like model blocks (a hand-made block could then kill
   an otherwise-good build).
2. **Relation targets — NAMED ONLY.**
   Recommended: target must be a non-blank name (descriptive names like
   "The Rival Port City" count); the model emits exactly that name or the
   bounded re-emit names the miss; blank targets are a payload error.
   Alternative: allow description-only targets and fuzzy-match the
   generated entity — rejected (no similarity scoring in the stack).
3. **Structured-entry merge — FILL-MISSING.**
   Recommended: a structured re-submit is a DELTA — authored fields
   overwrite, absent fields keep the committed row's values (fixes the
   note-4 overwrite hazard for the authored surface); plain-string
   re-submits keep full-overwrite exactly as today.

**Ask First (owner):** none — no UI work in this spec; the contract is
the deliverable for the follow-up entity-card story.