# Handoff — Hybrid Authorship Implementation (session boundary)

**GO**: implement the approved spec end-to-end. Owner-evaluated through
three elicitation methods (pre-mortem, second-order, assumption audit);
all rulings are folded in — do NOT re-litigate decisions, do NOT re-run
elicitation.

**Spec (the contract — read it):**
`_bmad-output/implementation-artifacts/spec-hybrid-authorship.md`
(CHECKPOINT 2, round 4, commit `a4695f4`). Baseline `749b81c`.

## What got approved (compress of the spec)

**Two character paths:**
1. **FULLY AUTHORED (`add_character`, zero LLM — structural):** the
   runner has NO provider; `max_llm_calls = 0`. Three-layer gate:
   frontend mirror (contract only — UI story later) → synchronous
   `POST /api/characters` (canonical JSON Schema: completeness +
   stat-block structure incl. dice pattern `^\d+d\d+([+-]\d+)?$`;
   202 + job_id / 422 with schema-path violations, zero jobs spawned)
   → worker backstop re-validating INSIDE the commit transaction
   (fail: `error_code: STRUCTURAL_VALIDATION_FAILURE`, zero LLM, zero
   commits, never a dangling edge). Commit: one revision, FRESH ULID,
   record + stat block byte-identical (no repair/conform/stamp/fold,
   `power` untouched), declared edges applied deterministically in the
   same transaction. Same-name characters coexist (F4). No mandates,
   no `target_name` in the payload (schema violation).
2. **PARTIALLY AUTHORED (hybrid build-in):** seed entries grow from
   `str` to `str | SeedEntry {name, description?, role?, record?,
   relations?}` (figures: role ∈ ROLES; places/factions: description).
   Authored fields = ground truth, backfilled after every repair.
   Authored stat blocks: validated REJECT-ONLY (invalid → job fails
   naming entry + violations, NO repair call; valid → byte-identical,
   zero repairs). Role pin enforced at the stat gate (identity.role).
   Declared relations: named targets; unseeded/uncommitted →
   MANDATE generation (wave-1 roster must contain the normalized name;
   ONE bounded re-emit cold+seeded, `_anchor_repair` shape; second miss
   → fail, ZERO commits). Declared edges pipeline-applied, kind-validated.
   Characters NEVER merge — always fresh ULID (F4); places/factions keep
   upsert (delta for structured entries); wave-2 roster twins-guard stays.

**Three-tier relation targets (both paths):** `{target_id}` committed
ULID (bind direct, no matcher) | `{target_key}` staged batch (both fresh
ULIDs, edge atomic) | `{target_name}` (hybrid: exactly-one normalized
match → resolve; ≥2 → reject naming matches; 0 → mandate. direct:
form-blocked + schema violation). Ambiguity resolved at the UI, not
runtime.

**Owner-mandated acceptance (non-negotiable):** a valid, an invalid, and
an incomplete fully-authored character EACH prove ZERO LLM invocations
(counting provider raises if called); only the valid one commits.

## Contract surface (file:line anchors from extraction)

- `backend/app/store/jobs.py:532` `_validate_build_in_payload` (sections
  `must be a list of strings`), `:575` `_build_in_budget`
- `backend/app/pipeline/build_in.py:3620` `_check_sections`, `:2394`
  `build_wave1_prompt`, `:2607` `build_wave1_chunk_prompt`, `:1498`
  `_merge_with_world` (whole-data replace; byte-equal skip; merge_log
  names flow into `merge_reports`), `:3214` `_OrphanRetryError` +
  anchor-repair shape, `:985` `_enforce_stat_blocks`, worker dispatch
  `backend/app/pipeline/worker.py` `_run_job` (add `add_character` arm
  with NO provider)
- `backend/app/store/candidates.py:93-157` + `:205` `payload_section_violations`
  — the AR24 authorable key set (IDENTITY/LORE/WORLD_INTEGRATION/BOSS
  fields, personality/secret/rumor/party_hook, role, stat_block)
- `backend/app/pipeline/knowledge.py:790-803` role checks; stat gate
  machinery in `statblocks.py` (`collect_stat_issues`,
  `validate_stat_block`, `StatIssue`)
- Store single-writer: commits ONLY through `commit_subgraph`; target
  resolution reads via `world_state`; dangling edges = `DanglingEdgeError`

## Implementation order (spec Tasks 1-9)

1. `add_character` kind: enqueue gate (canonical shape, completeness,
   stat structure, dice pattern, relations well-formed, unknown keys
   rejected, `target_name` banned) + `POST /api/characters` sync route
   (202/422 semantics; on fail ZERO job rows) + `job_failed` error_code
   `STRUCTURAL_VALIDATION_FAILURE` plumb.
2. `backend/app/pipeline/direct.py` `run_add_character(job)`: fresh world
   read + re-validate INSIDE the commit txn; one-revision commit with
   declared edges; zero provider anywhere. Result
   `{entity_id, revision_id, edges, llm_calls: {}}`.
3. **ZERO-LLM acceptance tests first-class** (`test_direct_character.py):
   valid / invalid / incomplete → all `provider_calls == []`; only valid
   commits. Plus DIRECT_OVER_POWERED (commits uncompromised,
   unstamped), DIRECT_WORLD_MOVED (enqueue → mutate world → run →
   STRUCTURAL_VALIDATION_FAILURE), DIRECT_RELATION_* rows,
   DIRECT_SAME_NAME (two Seraphines), DIRECT_UNKNOWN_KEY.
4. Hybrid payload validation + rendering (single + chunk): STRING_LEGACY
   (all existing plain-string tests stay green), AUTHORED_DESCRIPTION/
   RECORD backfill, ROLE_PIN, HYBRID_AUTHORED_BLOCK_VALID (zero repair
   calls, counting provider) / _INVALID (fail, zero commits, repair never
   offered).
5. `_seed_authored` / `_backfill_authored` (re-apply authored values
   after record/stat repairs; byte-identical enforcement).
6. `_MandatedTargetError` + ONE bounded re-emit on the ASSEMBLED roster
   + second-miss fail; CHUNKED_MANDATE; DECLARED_AMBIGUOUS_TARGET;
   TIER1/TIER2/TIER3 resolution in `_declared_edges`.
7. **F4 merge rework** (`_merge_with_world` + wave-2 remap): characters
   stop being a dedup key — fresh ULID always; places/factions upsert
   unchanged (delta for structured entry fields); audit counts
   place/faction only. Rework the merge pins: known breakers —
   `test_build_in_result_carries_world_context` (I added it 2026-09-15:
   second build now yields 5 entities, not 4, and its by_kind counts
   shift), the 2026-09-12-era upsert tests (merged/unchanged counts for
   characters, e.g. "Captain Harlow"-style rows), roster-twins tests.
   Reword BuildInView's "Same-name entities merge into the existing
   world — nothing is duplicated" copy for the character exception.
8. Full suite + frontend typecheck/lint.

## Verification (run at every milestone, full before commit)

- `uv run --directory backend pytest -q` · `uv run --directory backend
  ruff check .` · `uv run --directory backend ruff format --check .` ·
  `uv run --directory backend mypy app`
- Frontend: `cd frontend && npx vitest run && npm run lint && npm run
  typecheck`
- Restart dev api after backend changes: `hub restart mythos-api`; health:
  `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/campaigns`
  (expect 401). Frontend dev: hub `web` (vite HMR).

## Dev-box facts

- Live DB `/home/main/Projects/mythosCircle/data/mythos.db` (WAL — use
  `sqlite3 data/mythos.db ".backup <dest>"` for faithful copies).
- `MYTHOSCIRCLE_DB=sqlite:////home/main/Projects/mythosCircle/data/mythos.db`,
  `MYTHOSCIRCLE_LOG_FILE=/tmp/mythos-dev/app.jsonl`
  (unset LOG_FILE → startup crash), LLM `http://127.0.0.1:8889/v1`
  (`deploy/config.toml` `[llm]`, thinking=false; `MYTHOSCIRCLE_LLM_*`
  envs override config, AD-22).
- LLM call journal (shipped 2026-09-15): every provider attempt →
  `data/llm-journal/<job-id>.jsonl`; `MYTHOSCIRCLE_JOURNAL_ENABLED=0`
  off. `add_character` jobs must write NO journal file.
- Stale-module gotcha: clear `backend/**/__pycache__` before re-testing
  after surgical edits. `budget.call` lambdas: bind via partial, not lambda
  defaults (ruff B023 + mypy inference lesson).
- Subagents MAY run in parallel (owner rule, 0c07625); parallelization
  should be used for independent test-fix slices (e.g. the merge rework
  wheels).

## Out of scope (do NOT build)

- The entity-card/Add-Character FORM UI (the follow-up story; this spec
  ships its contracts: mirror validator, three-tier picker, path-2
  freeform guardrail, exact-match autocomplete). Frontend work = only the
  BuildInView copy reword + schema contract types.
- One-time characters (note 3), UX redesign (note 5, post-visualizer).
- generate/candidates and regenerate paths — untouched.

## Owned decisions (owner-gated, none blocking)

- Gate 2 (named-only, one re-emit, second miss → zero commits) — keep
  exactly. Gate 3 (delta for structured place/faction re-submits;
  plain strings full-overwrite; characters never merge) — keep exactly.
- If a NEW ambiguity surfaces mid-implementation (e.g. the F4 rework
  bumps an unexpected pinned behavior), bring the owner the scenario +
  the recommendation in-chat; do not silently change pinned semantics.