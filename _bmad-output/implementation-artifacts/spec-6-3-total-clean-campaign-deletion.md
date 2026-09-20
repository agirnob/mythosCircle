---
title: '6-3 Total, Clean Campaign Deletion'
type: 'chore'
created: '2026-09-20'
status: 'done'
baseline_commit: '5d30a10'
review_loop_iteration: 0
context:
  - '_bmad-output/implementation-artifacts/epic-6-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AR20 ("deleting a campaign actually deletes it — revisions, event log, queue entries, media-manifest rows, media directory — no trace") is implemented end-to-end since spec-1.6 (route + store cascade) and 4-3 (media reclaim), with store- and API-level tests. Two residual edges are NOT pinned: the mid-run job race (campaign deleted while its job is claimed/running — the worker's tolerance is only incidental via its generic exception guard) and API-level zero-count evidence (store-level counts exist; nothing pins the wire contract end-to-end). Sprint row sits at backlog with no spec.

**Approach:** prove-and-pin, not rewrite. Verify the existing delete path against the AR20 bar, add the two missing pins (a deterministic mid-run-delete race test through real store seams; an API-level all-tables-zero assertion), remove the never-used `CampaignDelete` pydantic model, write the spec, flip the sprint row. No behavior change to the delete contract.

## Boundaries & Constraints

**Always:**
- Existing delete contract is frozen: `DELETE /api/campaigns/{id}` with body `{"confirm": true}` → 204; missing/false/ malformed → 400 nothing deleted; foreign/unknown → 404 even without confirm (no enumeration). Store `delete_campaign` stays one transaction (event → revision → entity → edge → proposed_candidate → job → media → campaign row) with all-or-nothing rollback.
- The mid-run race test must be deterministic: a test provider that deletes the campaign (via the store seam) and then returns normal output → the worker's post-run `complete_job` raises `JobNotFoundError` → run_next_job returns the job id cleanly, queue never wedges, nothing staged survives.
- New tests follow the file conventions: https TestClient + scratch DB (API), `_generate_output`/world fixtures with a deleting provider (pipeline).

**Ask First:** none anticipated.

**Never:**
- No new delete surfaces, routes, or UI; no confirmation-flow changes; no changes to the snapshot/restore path.
- Don't "fix" the `JobNotFoundError` path beyond pinning it — the generic guard is the intended behavior (a claimed job's terminal write after deletion is a no-op by design).
- No media-file reclaim changes (the write-after-reclaim orphan window is documented, not chased; files outside the manifest are unreferenced).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| MID_RUN_DELETE | campaign deleted while its job is running (deleting provider) | worker completes without crashing; run_next_job returns the job id; queue keeps flowing; no staged rows survive | JobNotFoundError swallowed by the generic guard |
| DELETE_ZERO_COUNTS | campaign with revisions/events/jobs/media deleted through the API with confirm:true | API 204; revision/event/entity/edge/job/media rows for that id all zero via the store | N/A |
| (existing) DELETE_NO_CONFIRM | DELETE without/with false body | 400, nothing deleted | already pinned (test_campaigns_api.py:144) |
| (existing) DELETE_FOREIGN | foreign/unknown id | 404 no-oracle | already pinned (:170) |
| (existing) MEDIA_RECLAIM | media rows + dirs on confirmed delete | rows removed in-transaction, dir reclaimed post-commit, other campaign untouched | already pinned (:206, :262) |

## Code Map

- `backend/app/api/campaigns.py` -- delete() route L186-222 (confirm body hand-parse; ownership-404 first; reclaim never fails the 204); dead `CampaignDelete` model L56-57 (removed by this story).
- `backend/app/store/campaigns.py` -- `delete_campaign` L171-198 (one transaction, cascade order, owner mismatch → False).
- `backend/app/media/service.py` -- `reclaim_campaign_media` L868-878 (best-effort rmtree of `media/{campaign_id}`; rows-first AD-10).
- `backend/app/pipeline/worker.py` -- `run_next_job` L90-160: the generic `except Exception` guard (L146-160) swallows `JobNotFoundError` from the post-delete `complete_job`/`fail_job` — the mid-run race's tolerance (pinned, not changed).
- `backend/app/store/jobs.py` -- `complete_job`/`_require_running` (L268-305, L~420-428) raise `JobNotFoundError` for a gone row.
- `backend/tests/test_campaigns.py` -- store cascade pin: `test_delete_cascades_all_world_rows` L173-186 (`_counts` all zero).
- `backend/tests/test_campaigns_api.py` -- confirm/foreign/media delete tests L144-172, L206-275; add the API-level zero-count assertion here.
- `backend/tests/test_generate_pipeline.py` -- `_generate_output` + world fixtures; `test_runner_guard_empty_world_at_claim` L1967-1993 documents "campaign deleted after submit is unreachable on the wire" — the race test lands next to it.

## Tasks & Acceptance

**Execution:**
- [ ] `backend/tests/test_generate_pipeline.py` -- add MID_RUN_DELETE race test (deleting provider; run_next_job returns id, no wedge, no staged rows) -- pins the worker's post-delete tolerance deterministically
- [ ] `backend/tests/test_campaigns_api.py` -- extend the confirmed-delete test with API-level revision/event/entity/edge/job/media zero counts -- wire-level AR20 evidence
- [ ] `backend/app/api/campaigns.py` -- remove the unused `CampaignDelete` model + import -- dead code out (clean cutover)
- [ ] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- flip 6-3 to review/ready state per flow -- tracked

**Acceptance Criteria:**
- Given a campaign whose generate job is claimed and running, when the campaign is deleted mid-run, then the worker completes without crashing, `run_next_job` returns the job id, and the queue keeps flowing.
- Given a campaign with revisions, events, jobs, and media deleted through the API with `{"confirm": true}`, then all AR20 tables show zero rows for that id afterward.
- Given the AR20 path, then no trace remains by the list in the story AC (revisions, event log, queue entries, media-manifest rows, media directory) — verified end-to-end.

## Spec Change Log

- 2026-09-20 review round 1 (3 parallel layers; 0 intent_gap, 0 bad_spec — every AC held; 5 doc/test patches applied, 4 findings rejected with rationale):
  - **Mechanism misattribution** (BlindHunter/VerificationGap): the spec + race-test docstring named the swallowed exception as `complete_job`'s `JobNotFoundError`; the delete actually commits before post-call staging, so `stage_candidates`' `UnknownCampaignError` enters the generic guard first (`JobNotFoundError` surfaces via the guard's `fail_job`). Code was already correct; docstrings + Design Notes corrected to the real mechanism. KEEP: the race test's observable pins (run_next_job returns the id; campaign row gone; job_status raises; pre-seeded staged rows zero; survivor drains) — they fail loudly under a mutation check.
  - **Wire zero-counts split** (BlindHunter): the 6-3 sweep was folded into the 4-3 media test — split into a 6.3-named test with multi-state job seeding + keep-campaign negative control; 4-3 restored to media-only scope. KEEP: per-table scoped selects (full-suite DB is shared; never whole-table counts).
  - **Rejected with rationale**: wire-level ProposedCandidate re-pin (store-level pins cover the leg; wire invokes the identical store function); threaded HTTP-vs-worker race test (seam test + route test together pin both halves; a threaded E2E tests timing, not semantics, and risks flakes — noted in Design Notes); single-state job-seeding (delete is campaign_id-scoped, no state filter exists to regress); stage_candidates silent-skip concern (outcome pin + the pipeline's fewer-than-two contract would catch a silent skip).
  - **Code Map/task drift** (BlindHunter): Code Map line anchors drifted at approval time (worker guard L146-160 → actual ~L166; `_require_running` L420-428 → L424-428); the "remove model + import" task described an import that never existed (the class is defined in campaigns.py itself; removal was import-free). Recorded here; no code change needed.
  - **Negative control** (BlindHunter): the wire test now also asserts a keep campaign's revisions/entities/jobs survive — an over-broad cascade cannot pass.

## Design Notes

- The race test's provider deletes the campaign through `store.delete_campaign` (real seam — not SQL), then returns normal wave output. Mechanism (corrected in review round 1): in the generate path staging happens AFTER the provider returns, so the delete commits first — `stage_candidates`' campaign pre-check raises `UnknownCampaignError`, which the worker's generic guard swallows; its `fail_job` then raises `JobNotFoundError` (also swallowed + logged). `run_next_job` still returns the claimed id. Because a provider-side delete always precedes staging, the "staged rows die with the campaign" assertion is satisfied by PRE-SEEDING staged rows from a prior generate run — the `proposed_candidate` cascade leg is exercised for real, not simulated.
- The wire zero-count sweep is a separate 6.3-named test (`test_delete_wire_leaves_zero_rows_in_all_ar20_tables`): after the 204, per-table session selects scoped by the deleted id against the six campaign-owned tables, with jobs seeded in two states (queued + cancelled) and a keep-campaign negative control (rows still present). ProposedCandidate is NOT re-pinned at wire level: the store-level pins (`test_delete_campaign_cascades_staged_candidates`, the mid-run race test) cover that leg and the wire delete invokes the identical store function.
- `JobNotFoundError` on the terminal write is by design (documented in worker.py's conflict comment): the row is gone with the campaign; failing the fail is logged and the loop continues.

## Verification

**Commands:**
- `uv run --directory backend pytest tests/test_generate_pipeline.py tests/test_campaigns_api.py tests/test_campaigns.py -q` -- expected: all pass incl. the new pins
- `uv run --directory backend pytest -q && make lint && uv run --directory backend mypy app` -- expected: full green (NOTE: `make typecheck` carries pre-existing test drift, see spec-6-2 change log)

## Suggested Review Order

**Mid-run delete race (the new pin)**

- Deterministic mid-run-delete race: worker completes cleanly, queue never wedges, staged rows die with the campaign
  [`test_generate_pipeline.py:2016`](../../backend/tests/test_generate_pipeline.py#L2016)

- Store-level staged-candidate cascade (the wire test's ProposedCandidate coverage, per Design Notes)
  [`test_generate_pipeline.py:1664`](../../backend/tests/test_generate_pipeline.py#L1664)

**Wire-level AR20 evidence**

- New DELETE_ZERO_COUNTS: six tables zero after a confirmed 204, two job states, keep-campaign negative control
  [`test_campaigns_api.py:262`](../../backend/tests/test_campaigns_api.py#L262)

- 4-3 media test restored to media-only scope (reclaim rows + dir, keep untouched)
  [`test_campaigns_api.py:206`](../../backend/tests/test_campaigns_api.py#L206)

**Dead code removal**

- Unused `CampaignDelete` pydantic model removed from the delete route (FastAPI cannot bind a DELETE body; route hand-parses)
  [`campaigns.py:56`](../../backend/app/api/campaigns.py#L56)