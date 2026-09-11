---
title: 'JSON-schema generation foundation'
type: 'feature'
created: '2026-09-11'
status: 'done'
baseline_commit: 'e2a6fc4d210b9b04b4fdda9fa8c0436441a8fad9'
context:
  - /home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/proto-character-builder/gen.py
  - /home/main/Projects/mythosCircle/_bmad-output/implementation-artifacts/spec-structured-attack-and-stats-fields.md
---
<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every LLM call posts unconstrained text, so a whole failure class (fences, control-char deaths, missing sections, beside-`data` records) is caught downstream instead of prevented — while the proven `response_format: json_schema` mechanism (measured 2026-09-10: enforced, 9s structured calls) and the prototype's code-owned mechanics tables live outside `app/`.

**Approach:** Thread an optional per-call schema through the provider as settings-carried data, send a flat envelope schema on build-in wave calls, and port the prototype's tables+solvers into a tested `pipeline/mechanics.py` library. Parsing, validators, and repair gates stay the contract; schema is the optimization.

## Boundaries & Constraints

**Always:** `response_format` defaults to absent — default-settings bodies stay byte-identical (`{model, max_tokens, messages}` + thinking only when configured). Schemas are flat and `$ref`-free (GBNF subset). `parse_build_output`, validators, and all gates stay untouched and stay the backstop. Owner's stashed `backend/tests/test_config.py` is never staged, edited, or popped by implementation (reclaim is the final task's explicit step).

**Ask First:** Live-proof against `:8888` if the model is down (skip live run, note it, do not block). Any deviation from the prototype's pinned numbers (re-derive, don't "fix" the pins).

**Never:** Words-only generation path (`call_model`/`build`/`render` stay in the prototype dir); schema on generate/regenerate/repair paths; config-file/env plumbing for `response_format` (code-built per path, never operator config); behavior change in `conform_stat_power`, validators, or prompts.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| DEFAULT_BODY | `response_format=None` | body has no `response_format` key | N/A |
| SCHEMA_BODY | `dataclasses.replace(settings, response_format=S)` | body carries `S` verbatim | N/A |
| WAVE_CARRIES_SCHEMA | build-in job, fake records `settings` | wave 1+2 calls carry the envelope schema; job succeeds; parse path unchanged | N/A |
| SCHEMA_IGNORED | fenced/prose wave text despite schema | today's parse + gates handle it identically | existing JobPayloadError channel |
| MECHANICS_PARITY | prototype pins (paladin tags/L17) | identical ranks, scores, dice, routine | N/A |
| UNKNOWN_TAG | `rank_abilities(["bogus"])` | raises `ValueError` (prototype behavior) | caller-visible, no commit |
</frozen-after-approval>

## Code Map

- `backend/app/providers/llm.py:63-106` -- `chat_completion` builds `body{model, max_tokens, messages}`; add the one conditional key. `ChatCompletion = Callable[..., str]` unchanged.
- `backend/app/core/settings.py:69-82` -- frozen `LLMSettings`; add `response_format: dict[str, Any] | None = None` (code-set only; `llm_settings()` never fills it).
- `backend/app/pipeline/build_in.py:702-703,784-785` -- wave calls `provider(prompt, settings=settings)`; pass a `dataclasses.replace`d copy carrying `build_wave_schema()`.
- `backend/app/pipeline/mechanics.py` -- NEW: port of `ARCHETYPE_WEIGHTS`, `SCORE_CAPS`, `AC/SWINGS/MAGIC_BY_TIER`, `CLASS_RIDER`, `CLASS_TABLE` (Paladin), `WEAPONS`, `SLOTS`, `tier_for`, `rank_abilities`, `assign_scores`, `modifier`, `solve_dice`, `solve_hit_dice`, `solve_routine` from the prototype (`proto-character-builder/gen.py:26-180,295-348,499-516`); `build`/`call_model`/`render` deliberately excluded.
- `backend/tests/test_providers.py` -- body-shape pins live here (`test_chat_completion_*` pattern, MockTransport).
- `backend/tests/test_build_in_pipeline.py` -- wave fakes are `lambda prompt, settings:` (no `**kwargs`); settings-carried schema needs no changes there -- a fake asserting `settings.response_format` proves the threading. Exception: `test_worker.py:953` was the one double asserting `settings is SETTINGS` on the build-in path -- now asserts endpoint/model equality + carried schema.
- `backend/tests/test_mechanics.py` -- NEW: parity pins captured from prototype output (`paladin-l17.json`: STR 28/CON 26/CHA 24/WIS 20/DEX 16/INT 12, HP 324 = 24d10+192, DPR band 105-110).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/settings.py` -- add `response_format` field (None default, code-only) -- threading without touching any caller signature.
- [x] `backend/app/providers/llm.py` -- forward `settings.response_format` into the body when set -- the single wire change.
- [x] `backend/tests/test_providers.py` -- pin absent-by-default + verbatim-forward -- proves the AD-14 generic-client posture.
- [x] `backend/app/pipeline/build_in.py` -- wave 1+2 (+orphan re-emit at `:1084`) send the envelope schema via settings copy -- first schema'd path.
- [x] `backend/tests/test_build_in_pipeline.py` -- fake asserts carried schema on wave calls; existing suite green unmodified -- threading proven, backstop intact.

**Acceptance Criteria:**
- Given default settings, when any provider call runs, then the body contains no `response_format` key.
- Given a build-in job, when waves execute, then each wave call carried the envelope schema and the job result is identical to today.
- Given the prototype's paladin inputs, when mechanics functions run, then ranks/scores/dice/routine match `paladin-l17.json`.
- Full backend suite + `make lint` + `make typecheck` green; owner's stash still intact at the end.

## Spec Change Log

## Design Notes

Settings-carried (not yesterday's `json_schema=` kwarg sketch): ~40 test doubles use `lambda prompt, settings:` — a new kwarg breaks every one (TypeError), while `dataclasses.replace(settings, ...)` needs zero double churn and keeps `ChatCompletion` unchanged. Same opt-in semantics, boring diff.

Wave envelope schema sketch (built once in `build_in.py`, flat, no `$ref`):
```json
{"type": "object", "required": ["entities", "edges"],
 "properties": {"entities": {"type": "array", "items": {
   "type": "object", "required": ["ref", "kind", "name"],
   "properties": {"ref": {"type": "string"}, "kind": {"type": "string"},
     "name": {"type": "string"}, "text": {"type": "string"},
     "data": {"type": "object"}},
   "additionalProperties": false}}, ...}}
```
`data` stays an open object (record keys vary); `additionalProperties: false` on the item makes beside-`data` slips unrepresentable. Name stays required — the name gate remains the backstop.

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all pass (1094 baseline + new pins)
- `make lint && make typecheck` -- expected: clean
- `git stash list` -- expected: owner's entry still present until the reclaim task
- Live (if `:8888` up): one schema'd wave on a scratch stack -- expected: job succeeds, grammar path exercised

## Suggested Review Order

**Schema threading**

- Wave calls carry the envelope via a settings copy, repairs keep plain settings
  [`build_in.py:701`](../../../backend/app/pipeline/build_in.py#L701)

- Flat $ref-free envelope; open data plus closed items
  [`build_in.py:1042`](../../../backend/app/pipeline/build_in.py#L1042)

- Single conditional wire key; default bodies byte-identical
  [`llm.py:140`](../../../backend/app/providers/llm.py#L140)

- Code-only schema slot; no config or env plumbing
  [`settings.py:85`](../../../backend/app/core/settings.py#L85)

**Mechanics port**

- Verbatim prototype tables plus solvers; no callers yet by design
  [`mechanics.py:1`](../../../backend/app/pipeline/mechanics.py#L1)

**Tests**

- Literal envelope pins plus wave-2, re-emit, and repair-scope carry proofs
  [`test_build_in_pipeline.py:409`](../../../backend/tests/test_build_in_pipeline.py#L409)

- Body-shape pins and paladin parity pins
  [`test_providers.py:197`](../../../backend/tests/test_providers.py#L197)
