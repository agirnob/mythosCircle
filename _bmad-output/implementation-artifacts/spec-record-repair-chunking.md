---
title: 'Record-repair chunking'
type: 'bugfix'
created: '2026-09-10'
status: 'done'
baseline_commit: '54e3a8b8fecfc2a94061cb1efc3e16423d25cfdf'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The record repair emits all flagged records in ONE response; at 14 records (~13k chars) the model stochastically drops a brace (live: last record's close missing, twice in a row), and the single retry re-emits the same giant output and fails the same way — the whole wave dies on JSON noise.

**Approach:** Split the record repair into bounded per-chunk calls (<=4 records each) so every response stays short, and feed the JSON decode error into the retry prompt so the model can fix the exact spot. Validator, contract strictness, and one-retry-per-chunk semantics unchanged.

## Boundaries & Constraints

**Always:** Chunk size is a module constant (4); each chunk reuses the existing prompt builder, parser, and one-retry helper unchanged in shape; contract violations inside well-formed JSON still fail immediately; error line appended to the shared retry prompt benefits all gates.

**Ask First:** Splitting the stat-gate repair too, or raising retries beyond one per chunk — needs owner verdict; this spec covers the record gate only (stat blocks are small, no JSON flake observed there).

**Never:** Weaken contract checks (exact refs, no dupes, no partial merge); silent structural auto-repair (brace-guessing parsers); touch validator bands, budget ceilings, or wave prompts.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| CHUNK_SPLIT | 14 flagged records | 4 provider calls (4+4+4+2), merged patches | N/A |
| CHUNK_RETRY | One chunk malformed, retry valid | Only that chunk re-called; wave proceeds | N/A |
| CHUNK_FAIL | One chunk malformed twice | Job fails naming the chunk's refs (record label) | JobPayloadError |
| SMALL_WAVE | <=4 flagged records | Single call — identical behavior to today | N/A |
| ERROR_LINE | Any gate retry | Retry prompt names the JSON decode error | N/A |
| DETERMINISM | Same issues twice | Byte-identical chunk prompts | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/pipeline/build_in.py:547-581` -- `_enforce_character_records`: the only gate changed; loops chunks through `_run_repair`, merges patches, then the existing re-check.
- `backend/app/pipeline/build_in.py:135-159` -- `_repair_retry_prompt`: gains one JSON-error line; shared by all gates, signature extended with optional error param.
- `backend/app/pipeline/build_in.py:162-197` -- `_run_repair`: computes the decode error of the bad text and passes it through; call/parse/contract flow untouched.
- `backend/app/pipeline/fencing.py:67-88` -- `parse_json_object`: read-only reference; new `json_error` helper beside it exposes the first decode failure.
- `backend/tests/test_build_in_pipeline.py` -- repair-loop pins (`len(calls) == 2` single-record cases): must keep passing unmodified (small waves stay single-call).
- `backend/tests/test_statblocks.py` + `test_generate_pipeline.py` -- retry-prompt/error-helper pins live here or in build-in tests; append-only.

## Tasks & Acceptance

- [x] `backend/app/pipeline/fencing.py` -- `json_error` helper returning the first JSON decode failure string (None when parseable) -- the retry prompt needs the exact error to quote.
- [x] `backend/app/pipeline/build_in.py` -- chunked record repair (constant 4) + error line in shared retry path -- 14-record single responses flake on braces; short responses plus a pointed retry fix the class.
- [x] `backend/tests/test_build_in_pipeline.py` -- matrix pins (split counts, per-chunk retry isolation, chunk failure naming, small-wave single call, error line present) -- the call pattern is the contract.

**Acceptance Criteria:**
- Given 14 flagged records, when the record gate runs, then the provider sees 4 chunked calls.
- Given a malformed chunk whose retry parses, when the gate runs, then no other chunk is re-called and the wave proceeds.
- Given the full backend suite + ruff, when run, then green with no existing test modified.

## Spec Change Log

## Design Notes

Chunk prompts stay byte-identical in shape to today's single prompt (same builder per subset, same OUTPUT CONTRACT per chunk with that chunk's refs) — the model sees the same contract, just fewer records. Error line format: `JSON error: <str(exc)>` on its own line after the rules. `json_error` mirrors `parse_json_object`'s candidate order (fence-strip, then balanced extraction) and reports the LAST candidate's error... simplest deterministic: the fence-stripped candidate's decode error, falling back to "not a JSON object" when no braces exist.

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_build_in_pipeline.py tests/test_statblocks.py tests/test_combat.py tests/test_generate_pipeline.py -q` -- expected: all pass, existing tests unmodified.
- `uv run --directory backend pytest -q` -- expected: full suite green.
- `make lint 2>&1 | grep -v -E 'export_sheets|media.py|test_export_api' ; uv run --directory backend mypy app/pipeline/ 2>&1 | tail -1` -- expected: no NEW findings (3 baseline format files + 2 baseline mypy rows pre-exist).

## Suggested Review Order

- Chunk loop with per-chunk cancel poll and merged patches
  [`build_in.py:589`](../../backend/app/pipeline/build_in.py#L589)
- Chunk bound plus shared retry prompt with error line
  [`build_in.py:138`](../../backend/app/pipeline/build_in.py#L138)
- Error helper mirroring the parser's candidate order
  [`fencing.py:91`](../../backend/app/pipeline/fencing.py#L91)
- Split/retry/failure pins plus cancel and budget isolation
  [`test_build_in_pipeline.py:1936`](../../backend/tests/test_build_in_pipeline.py#L1936)
- Inner-error, cancel, and budget regression pins
  [`test_build_in_pipeline.py:2024`](../../backend/tests/test_build_in_pipeline.py#L2024)
