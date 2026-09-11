---
title: 'Build-in edgeless commit, edge-type enum, scoped stat repair'
type: 'feature'
created: '2026-09-11'
status: 'done'
baseline_commit: 'b74e600365b8bfcf3fb99095284528bb862d0ca3'
review_loop_iteration: 0
context:
  - /home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/spec-json-schema-generation.md
  - /home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/spec-wave2-orphan-reprompt.md
  - /home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/ladder-qwen3-2026-09-11/runlog.md
---
<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Rung 10 on gemma died three different ways, all post-parse
(runlog): the schema fixed syntax, but (1) the wave-1 orphan rule killed
attempt 3 over E8 unwired twice — wiring the model will not invent; (2) a
bad edge `type` can only die as a whole-job validator rejection; (3) the
stat repair rewrites whole blocks, so pass 1 inflates healthy numbers
(`2d8+5` → `2d8+69`) and chases wording across both passes into the exact
failing shape (E4 `null` → `["Sneak Attack"]` → `[{"name": ...}]`).

**Approach:** Drop the wave-1 internal orphan rule (edgeless entities
commit; the DM prunes), keep the wave-2 core-anchor, pin edge `type` as a
schema enum single-sourced from `store.EDGE_TYPES`, and scope the stat
repair to per-issue EDIT SCOPE lines plus a strict repair-response schema.
Record/name repairs are already minimal and stay untouched.

## Boundaries & Constraints

**Always:** Wave-2 core-anchor stays byte-identical in behavior (same
signal, same re-emit, same tests). The validator's `type ∈ EDGE_TYPES`
check and the commit re-check stay as backstop for non-enforcing
backends. Repair stays two bounded passes + deterministic conform; only
the prompt scope and the response contract change. Scope-breach logging
is log-only, never a new failure class.

**Ask First:** Any new violation string must extend the scope map (the
coverage test enforces it — no silent whole-block fallback for new
kinds). Changing the wave-2 anchor later is a separate decision.

**Never:** Schema on record/name repairs or generate/regenerate paths;
kind/ref enums in the wave schema; merge-diff rejection (breaches log,
never fail); seed or prompt-table edits outside the three surfaces;
re-litigating the power-arithmetic deaths (trim/lower/leave stays open).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| EDGELESS_WAVE1 | valid subgraph, E8 edgeless | commits as-is, no re-emit, 1 wave call | N/A |
| BAD_TYPE_WIRE | grammar backend | unemittable (enum) | N/A |
| BAD_TYPE_BACKSTOP | non-enforcing backend, invented type | validator rejects whole job as today | existing JobPayloadError |
| POWER_SCOPE | DPR violation | EDIT SCOPE names damage numbers/hp/ac/challenge only; other fields verbatim | scope breach logs, re-audit decides |
| TRAITS_SCOPE | traits violation | EDIT SCOPE names traits only | same |
| MISSING_SCOPE | missing block | whole-block scope (only full-scope case) | N/A |
| REPAIR_SHAPE | bare-string / name-only traits | unemittable under repair schema | N/A |
| SCOPE_MAP_GAP | new violation string | coverage test fails until mapped | fail-closed at test time |
</frozen-after-approval>

## Code Map

- `backend/app/pipeline/build_in.py:1439-1444` -- wave-1 orphan branch goes away; wave-2 anchor branch stays (keeps `_OrphanRetryError`, handlers at `:796`, `_orphan_reemit` at `:1120`, drop guard at `:1191`). `wave_positions_in_edges` dies with the wave-1 branch.
- `backend/app/pipeline/build_in.py:703-738` -- wave-1 try/except `_OrphanRetryError` + re-emit block removed (single straight call path like text jobs).
- `backend/app/pipeline/build_in.py:1042-1093` -- `build_wave_schema`: edge `type` becomes `{"enum": sorted(EDGE_TYPES)}` (already imported at `:63`); nothing else changes.
- `backend/app/pipeline/statblocks.py:310-376` -- `build_stat_repair_prompt` gains per-issue EDIT SCOPE lines from a prefix map (codebase idiom: `_CONFORMABLE_PREFIXES` at `:495`); new `build_stat_repair_schema()` strict wrapper (`stat_blocks[{ref, stat_block}]`, trait items require name+description, damage parts require dice/count/sides/bonus/average/type, `strict: True`, closed objects, no minItems anywhere so non-combatants stay legal).
- `backend/app/pipeline/build_in.py:229-294` -- `_enforce_stat_blocks` gains keyword-only `repair_response_format` (default None = plain settings); build-in waves pass `build_stat_repair_schema()`, so initial + JSON retry ride the schema; merge adds scope-breach `logger.warning` via the shared scope map (new `scope_for_violations()` used by prompt builder and gate).
- `backend/app/pipeline/regenerate.py:219` -- call site unchanged (no `repair_response_format`): the shared gate defaults to plain settings, so the regenerate path keeps the Never-list posture; pinned by `test_stat_block_reroll_runs_the_ar25_gate` formats assertion.
- `backend/tests/test_build_in_pipeline.py:648-683` -- orphan section rewritten: edgeless-commits proof, anchor tests untouched, re-emit-carry proof moves to a wave-2 re-emit run; self-loop docstring de-orphaned.
- `backend/app/pipeline/build_in.py:14-17,674-679,1307-1310` + `spec-wave2-orphan-reprompt.md` + deferred-work 2026-09-09 verdict note -- doc updates recording the reversal (owner verdicts: drop confirmed 2026-09-11).

## Tasks & Acceptance

**Execution:**
- [x] `build_in.py` validator -- remove wave-1 orphan branch + `wave_positions_in_edges`; wave-2 anchor untouched -- edgeless wave-1 validates.
- [x] `build_in.py` wave-1 path -- remove re-emit handler block; `_orphan_reemit` keeps its wave-2 caller -- one straight call.
- [x] `build_wave_schema` -- edge `type` enum from `EDGE_TYPES` -- illegal types unemittable on grammar backends.
- [x] `statblocks.py` -- scope map + EDIT SCOPE lines + `build_stat_repair_schema()` -- repairs name their editable fields.
- [x] `_enforce_stat_blocks` -- schema-carrying settings into `_run_repair` (+`repair_response_format` opt-in so regenerate stays plain; `wave` context for breach logs); scope-breach log-only check -- shape enforced, breaches measured.
- [x] Tests -- edgeless-commits proof; anchor suite green unmodified; carry proof on wave-2 re-emit; enum + repair-schema literal pins; scope-map coverage; prompt scope-line pins.
- [x] Docs -- module/run/validator docstrings, orphan spec supersede-note (wave-1 half), ledger verdict note.

**Acceptance Criteria:**
- Given a valid wave-1 subgraph with an edgeless entity, when the job runs, then it commits with exactly one wave call and no re-emit.
- Given a wave-2 peer-only subgraph, when the job runs, then the anchor re-emit still fires once and still fails on a second miss.
- Given a grammar backend, when waves run, then no emitted edge type can fall outside `EDGE_TYPES` (pin lists the enum by value).
- Given a traits-shape violation, when the stat repair runs, then the fixed shape is the only emittable one and unrelated fields log breaches instead of drifting silently.
- Full suite + lint + typecheck green; scratch ladder payloads untouched.

## Spec Change Log

## Design Notes

Reversals recorded, not hidden: owner verdict 2026-09-09 (bounded re-prompt) + 0fd4f27 (wave-1 extension) + schema-spec Never (no schema on repairs) all partially superseded — wave-1 orphan gone, repair responses schema'd. Wave-2 anchor and the 2026-09-09 verdict's wave-2 half stand.

No merge-diff rejection by design: breach handling has no good failure answer (fail = new death class; discard = silent loss), so breaches log with paths for the next decision while the re-audit stays authoritative. If evidence shows the model honoring scope, the log stays quiet; if not, the log is the next spec's input.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- measured: 1176 pass
- `make lint && make typecheck` -- measured: clean
- Live (ladder gate, runlog attempt 4): wave 1 committed 10/10 with 12 edges, all schema'd calls parsed first try, 6 breach warnings with job/wave/attempt context, wave-2 drop guard caught a re-emit rename with wave 1 staying committed. Edgeless-live case not observed (model wired everyone); edgeless proof stays unit-level.

## Suggested Review Order

**Orphan removal**

- Wave-1 commits edgeless with surfacing; wave-2 anchor untouched
  [`build_in.py:804`](../../../backend/app/pipeline/build_in.py#L804)

- Validator: wave-1 branch gone, wave guard, anchor intact
  [`build_in.py:1413`](../../../backend/app/pipeline/build_in.py#L1413)

- Store backstop opens only under explicit opt-in
  [`commit.py:309`](../../../backend/app/store/commit.py#L309)

**Repair scope**

- Scope map, EDIT SCOPE lines, strict repair schema
  [`statblocks.py:343`](../../../backend/app/pipeline/statblocks.py#L343)

- Gate threading, breach telemetry, recursive path diff
  [`build_in.py:233`](../../../backend/app/pipeline/build_in.py#L233)

**Tests**

- Edgeless proofs, wave-2 carry, gate-level breach wiring
  [`test_build_in_pipeline.py:485`](../../../backend/tests/test_build_in_pipeline.py#L485)

- Scope-map coverage, repair-schema pins, structural minItems
  [`test_statblocks.py:396`](../../../backend/tests/test_statblocks.py#L396)
