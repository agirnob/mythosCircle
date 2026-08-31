---
title: 'Typed edge vocabulary on commit: per-type counter semantics and no free text'
type: 'feature'
created: '2026-08-31'
status: 'done'
review_loop_iteration: 0
baseline_commit: '12a080e'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/specs/spec-mythosCircle/conventions.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/spec-1-2-versioned-world-store.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegottates">

## Intent

**Problem:** The world's relationships are typed directed edges enforced on commit (EDGE_TYPES + InvalidEdgeTypeError landed in 1.2), but AD-23's per-type counter semantics (`debt = amount, grudge/loyalty = score, ally/enemy = intensity`) exist only as prose — no code contract maps a type to its counter's meaning, so the pipeline (2.3) and the 5e/constraint layer (2.4) have nothing to import; and counters are accepted in any shape SQLite tolerates (a float or bool stores as-is). 1.2's review deferred exactly this: *"Edge counters have no semantic/range validation — AD-23 per-type meaning has no type-to-schema map"* (defer item, `store/models.py`). FR3's "never free text" is currently structural luck (the Edge model has no label column), not a pinned invariant.

**Approach:** Make the edge vocabulary a typed code contract on the commit path. Add a per-type counter-semantics map + resolver next to `EDGE_TYPES` in `store/commit.py` (one import for pipeline and store, AD-23's "semantics are defined per type" becomes data, not folklore); validate the staged counter's shape at the store boundary (must be an `int`; SQLite's `Integer` column does not enforce it — same boundary discipline as the ULID check); pin AC1/AC2/AC3 with store tests, including the structural no-free-text invariant.

## Boundaries & Constraints

**Always:**
- The commit path rejects edge types outside the closed Phase-1 vocabulary (relationship, debt, grudge, loyalty, member_of, located_in, rival_of, kin_of, ally_of, enemy_of) — AD-5/FR3. Already enforced; 2.2 keeps it and pins both directions (every member committable, non-members rejected).
- Counter semantics resolve from a code map, not prose: `debt → amount`, `grudge → score`, `loyalty → score`, `ally_of → intensity`, `enemy_of → intensity`; every other vocabulary member carries the neutral default semantic. The map lives beside `EDGE_TYPES` and both export from `app.store`.
- Counters are integers at the store boundary: a staged counter that is not an `int` (float, bool, str, …) is a structured rejection in the 422 family with zero rows written — never a silent SQLite coercion. Range semantics (how large a score, whether a debt amount may be negative) stay **pipeline-owned** per the 1.2 defer: the store validates shape, not range.
- Counters change only via the commit path — a staged `edge_updated` (existing edge ULID) or a compensating-commit undo; there is no other writer (AD-1). Already enforced; pinned.
- Zero dangling edges after every commit — edge endpoints exist in the current revision or the same staged subgraph (AD-23). Already enforced; pinned.
- No free-text relation label: the `edge` table carries exactly `{id, campaign_id, src, dst, type, counter, created_at}` and `EdgeInput` exactly `{src, dst, type, counter, id}` — pinned structurally so a label column/field can never creep in.

**Ask First:** adding a vocabulary member beyond the Phase-1 set (existing 1.2 Ask First — the frozenset is the single code gate); counter range bounds before the pipeline assigns counters (2.3/2.4 concern).

**Never:**
- Free-text labels on edges — never a `label`/`text` field on `Edge` or `EdgeInput`; the relation is the typed edge itself (FR3).
- Counter mutation outside the commit path; no new writer to `edge` rows.
- A DB `CHECK` constraint on `edge.type`: every write already flows through `commit_subgraph` (AD-13 sole writer), so a constraint would duplicate the gate and add a per-type migration for zero enforcement gain. The frozenset is the single source of truth.
- HTTP routes or an edge API in this story — 2.2 is store-level (epic-2 context: "store-level and may run in parallel with 2.1"); edge routes arrive with candidate acceptance in Epic 3. The API error mapper gains the new error class only, keeping the store-error family complete for the routes that call into the store.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| EDGE_TYPE_VALID | type in EDGE_TYPES | commit accepted; one `edge_created` event (existing pin) | N/A |
| EDGE_TYPE_INVALID | type outside vocabulary | whole subgraph rejected, zero rows, `InvalidEdgeTypeError` naming the type (existing pin) | 422 |
| EDGE_COUNTER_VALID | int counter on any vocabulary member | commit accepted; counter stored verbatim; default 1 when omitted | N/A |
| EDGE_COUNTER_INVALID_SHAPE | counter = 1.5 / True / "3" | whole subgraph rejected, zero rows, `InvalidEdgeCounterError` naming the edge | 422 |
| EDGE_COUNTER_SEMANTICS | any vocabulary member via `edge_counter_semantic(type)` | debt→amount, grudge/loyalty→score, ally_of/enemy_of→intensity, others→neutral; map keys exactly the four semantic-bearing types | N/A |
| EDGE_COUNTER_CHANGE | existing edge ULID staged with a new int counter | `edge_updated` event; counter changes only via commit; undo restores the prior counter (existing pins) | N/A |
| EDGE_DANGLING | endpoint in neither revision nor subgraph | whole subgraph rejected, `DanglingEdgeError` (existing pin) | 422 |
| EDGE_FREE_TEXT_LABEL | any attempt to carry a label on an edge | rejected structurally at construction (`EdgeInput` has no label field → TypeError); `edge` table columns pinned to the exact set | N/A |

## Code Map

- `backend/app/store/commit.py` -- add `EDGE_COUNTER_SEMANTICS: dict[str, str]` (the four semantic-bearing types), `DEFAULT_EDGE_COUNTER_SEMANTIC = "neutral"`, and `edge_counter_semantic(edge_type: str) -> str` beside `EDGE_TYPES`; add `InvalidEdgeCounterError(StoreError)`; raise it in the edge-validation loop when `type(edge.counter) is not int`.
- `backend/app/store/__init__.py` -- re-export `EDGE_COUNTER_SEMANTICS`, `DEFAULT_EDGE_COUNTER_SEMANTIC`, `edge_counter_semantic`, `InvalidEdgeCounterError`.
- `backend/app/api/common.py` -- add `InvalidEdgeCounterError` to the 422 family of `_store_error_as_http` (contiguous with `InvalidEdgeTypeError`).
- `backend/tests/test_store.py` -- new tests: semantic map contract (every EDGE_TYPES member resolves; map keys exactly the four; documented semantics set exactly {amount, score, intensity, neutral}); invalid-shape counter rejection (float/bool/str each reject the whole subgraph with zero rows); free-text structural pins (`Edge.__table__.columns` and `EdgeInput.__dataclass_fields__` exact sets; `EdgeInput(..., label=...)` raises TypeError); existing vocabulary/counter/dangling pins kept.

## Tasks & Acceptance

**Execution:**
- [ ] `backend/app/store/commit.py` -- counter-semantics map + resolver; `InvalidEdgeCounterError` with an int-shape check in the edge-validation loop (before DUPLICATE/retarget checks, alongside the vocabulary check).
- [ ] `backend/app/store/__init__.py` + `backend/app/api/common.py` -- exports and the 422 mapping row.
- [ ] `backend/tests/test_store.py` -- EDGE_COUNTER_SEMANTICS / EDGE_COUNTER_INVALID_SHAPE / EDGE_FREE_TEXT_LABEL rows; keep the EDGE_TYPE_VALID/INVALID, counter-change, dangling pins green.

**Acceptance Criteria:**
- Given a generated edge, when it is committed, then its type is from the closed directional set, extensible by adding a type to the frozenset, never free text (FR3, AR3) — pinned from both the acceptance and rejection sides.
- Given a free-form relation label, when it is persisted, then it is never stored as free text — the edge table and input dataclass carry no label field (FR3).
- Given a per-type counter (debt = amount, grudge/loyalty = score, ally/enemy = intensity), when the counter changes, then it changes only via a commit, its semantic resolves from the code map, and zero dangling edges remain (AR8, AD-23).

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries. -->

## Design Notes

**Why the semantics map lands in 2.2 (and ranges do not).** 1.2's review deferred the type-to-counter-schema map with "*ranges are the pipeline's concern once counters are assigned*". 2.2 lands the map — AD-23's per-type semantics become a code contract both the 2.3 pipeline (assigning counters) and Epic 3's acceptance (validating them) import from the same place `EDGE_TYPES` lives. Range bounds (score 0–10? debt ever negative?) are deliberately not decided here: no pipeline assigns counters yet, so any bound would be speculation the 2.3/2.4 stories must own. The store validates **shape**, not range.

**Why the store rejects non-int counters.** SQLite does not enforce `Integer` (same reason 1.2 validates ULID shape at the store boundary — the column is `String(26)` only in the mapper's intent). A float or bool counter would persist silently and poison Phase-3's deterministic counter arithmetic. Shape rejection is the last line of defense; the 422 family is where the shared mapper sends it, contiguous with `InvalidEdgeTypeError`.

**Why no DB CHECK constraint on `edge.type`.** Every `edge` write flows through `commit_subgraph`'s validated loop (AD-13 sole writer; raw SQL outside the store is forbidden). A `CHECK (type IN (...))` would duplicate that gate, drift from the frozenset, and force a table rebuild per new type (`_migrate_job_kind` exists precisely because jobs' states were trusted assumptions). Adding a type = one frozenset edit + one line of prompt/schema work downstream — the "extensible by adding a type" contract.

**Neutral default for non-semantic members.** `relationship`, `member_of`, `located_in`, `kin_of`, `rival_of` carry `counter` as a structural default of 1; the spine assigns no meaning to them, so the store gives them a neutral semantic and leaves the counter informational until a later phase defines it.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: full suite green incl. the new semantics/invalid-shape/free-text pins (deterministic, no live LLM).
- `make lint && make typecheck` -- expected: ruff, mypy strict, eslint, vue-tsc clean.

## Suggested Review Order

1. `../../backend/app/store/commit.py` -- EDGE_COUNTER_SEMANTICS + `edge_counter_semantic` + `InvalidEdgeCounterError` and its position in the validation loop.
2. `../../backend/app/store/__init__.py` -- exports.
3. `../../backend/app/api/common.py` -- the 422 mapping row.
4. `../../backend/tests/test_store.py` -- the EDGE_COUNTER_SEMANTICS / EDGE_COUNTER_INVALID_SHAPE / EDGE_FREE_TEXT_LABEL rows.