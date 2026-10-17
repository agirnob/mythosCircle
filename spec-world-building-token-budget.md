---
title: Raise world-building token allowance
type: bugfix
created: 2026-10-17
status: done
baseline_commit: 308dc3870128c0262238ef1a9118dc7a444b0555
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** World-building requests use a 900-token allowance per entity, which can truncate legitimate responses from the selected model even when the configured overall completion ceiling is much higher. The supplied Nemotron generation log reports 4,996 completion tokens, all classified as reasoning, and length termination: increasing the allowance alone may still leave no character answer.

**Approach:** Raise the allowance to 5,000 tokens per entity. Retain the existing 4,096-token headroom, configured overall ceiling, chunking, and bounded truncation retry. Send OpenRouter's reasoning control, disabling reasoning by default for these generation requests while honoring an explicitly enabled thinking setting.

## Boundaries & Constraints

**Always:** Keep requests bounded by the operator's configured completion ceiling. Preserve the existing entity and edge validation, privacy, graph writer, and job call budget contracts. Publish through a fix branch and pull request to develop with CI gate required before merging.

**Ask First:** Changing the operator's configured overall ceiling or production deployment.

**Never:** Remove truncation detection or accept incomplete JSON. Change unrelated edge-repair budgets, model routing, or local/Gemini reasoning behavior. Treat hidden reasoning as a character answer or weaken the complete-roster requirement.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Small roster | 9 entities, ceiling 50,000 | Initial request allows 49,096 tokens | Existing bounded retry remains available |
| Large roster | 16 entities, ceiling 65,536 | Initial request allows 65,536 tokens | Truncation at configured ceiling remains terminal |
| Empty roster | 0 entities, ceiling 65,536 | Existing 4,096-token headroom remains | Existing parsing and validation remain |
| OpenRouter thinking default/off | OpenRouter settings with thinking unset or false | Request sends reasoning enabled false | Length termination still rejected |
| OpenRouter thinking on | Explicit thinking true | Request sends reasoning enabled true | Existing bounded retry remains |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/build_in.py:225` — sizing constants and their explanatory comment; `_wave_max_tokens` at line 801 is shared by ordinary waves, chunked waves, and missing-roster calls.
- `backend/app/pipeline/wave.py:84` — existing one-retry truncation policy, bounded by the operator ceiling; preserve.
- `backend/tests/test_build_in_pipeline.py:4054` — chunked generation integration test currently asserts the old 900-token formula; its default settings use the 65,536-token ceiling.
- `backend/tests/test_build_in_pipeline.py:4204` — truncation retry test also embeds the old one-entity allowance; update its initial budget to 9,096 and preserve the doubled-retry assertion.
- `backend/app/providers/llm.py:193` — detects provider length termination and rejects incomplete output; preserve.
- `backend/app/providers/llm.py:134` — OpenRouter request body construction; add provider-specific reasoning control here without sending local chat-template parameters.
- `backend/app/pipeline/build_in.py:3590` — complete-roster validation emits the separate missing-entities error after a successfully returned response; preserve, since the supplied length log alone does not establish that final response's roster contents.
- `backend/tests/test_llm_routing.py` — existing mock HTTP routing tests; verify the serialized OpenRouter reasoning field and retain local/Gemini request contracts.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/pipeline/build_in.py` — set `TOKENS_PER_ENTITY` to 5,000 and update the comment to explain the larger allowance without asserting old measurements justify it.
- [x] `backend/tests/test_build_in_pipeline.py` — update existing chunked generation assertions to cover the capped first chunk and uncapped second chunk, and update the existing truncation retry test to the new one-entity allowance; verify matrix scenarios through existing coverage or a direct sizing check.
- [x] `backend/app/providers/llm.py` — send OpenRouter reasoning enabled false when thinking is unset or false; send true only when explicitly configured.
- [x] `backend/tests/test_llm_routing.py` — verify OpenRouter reasoning default/off/on request bodies with mock HTTP, preserving other provider behavior.
- [x] Run relevant backend checks and review the diff.
- [x] Prepare the reviewed fix branch for publication.
- [ ] Open a pull request into develop; automatic creation requires GitHub API authentication, unavailable in this environment.

**Acceptance Criteria:**
- Given a world-building roster, when its first generation request is constructed, then its allowance uses 5,000 tokens per entity plus existing headroom up to the configured ceiling.
- Given a truncated response, when retry handling runs, then the existing bounded retry and terminal error behavior remain intact.
- Given chunked generation, when it completes with valid provider replies, then entity merge order, schemas, edge wiring, and job success remain valid.
- Given an OpenRouter request with thinking unset or disabled, when the body is serialized, then it explicitly disables reasoning; explicitly enabled thinking remains supported.

## Spec Change Log

## Verification

**Commands:**
- Run backend pytest using the existing backend virtual environment from the isolated worktree; expected: tests pass.
- The shared virtual environment is `/home/main/Projects/mythosCircle/backend/.venv`; run its tools with working directory `/tmp/mythos-token-budget/backend` so imports use this fix's code. Full backend checks include pytest, ruff check, ruff format --check, and mypy, matching CI.
- Check sizing for 9, 16, and 0 entities against the matrix; expected: exact budgets above.
- `git diff --check` — expected: no whitespace errors.

## Review Results

Three independent review passes completed. No blocking findings. Updated the provider comment to describe the OpenRouter controls accurately. The existing below-2,048 ceiling bug was recorded for follow-up; this change retains existing sizing clamp behavior. Existing provider tests cover length rejection, and existing generation tests cover the bounded doubled retry.

## Verification Results

- Focused provider/routing/build-in tests: 219 passed.
- Ruff check app tests: passed.
- Changed provider/routing files: Ruff formatting check passed; the other two edited files have existing formatting issues also present at the baseline revision.
- Direct sizing assertions: 9/50,000 gives 49,096; 16/65,536 gives 65,536; 0/65,536 gives 4,096.
- Full mypy: 240 errors in 20 files; compared with a fresh baseline backend snapshot and confirmed the exact same diagnostics after normalizing shifted line numbers. No new type errors.
- git diff --check: passed.
- Full backend pytest: 1,808 passed, 1 deprecation warning, in 144.53 seconds outside the sandbox. Focused tests also passed inside the sandbox.

## Suggested Review Order

- Increase per-entity capacity while retaining headroom and operator ceilings.
  [build_in.py:225](backend/app/pipeline/build_in.py#L225)
- Disable OpenRouter reasoning unless explicitly enabled.
  [llm.py:134](backend/app/providers/llm.py#L134)
- Verify capped chunks, smaller chunks, and the doubled retry.
  [test_build_in_pipeline.py:4087](backend/tests/test_build_in_pipeline.py#L4087)
- Verify serialized thinking controls for default, disabled, and enabled settings.
  [test_llm_routing.py:327](backend/tests/test_llm_routing.py#L327)
