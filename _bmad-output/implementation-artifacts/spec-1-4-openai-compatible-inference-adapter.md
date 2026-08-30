---
title: 'OpenAI-compatible inference adapter: config-swappable LLM worker for the queue'
type: 'feature'
created: '2026-08-30'
status: 'done'
review_loop_iteration: 0
baseline_commit: '05dff8aa2b601b29a2c59140de4b62d85beea490'
context:
  - '/home/main/Projects/mythosCircle/_bmad-output/planning-artifacts/architecture/architecture-mythosCircle-2026-08-23/ARCHITECTURE-SPINE.md'
  - '/home/main/Projects/mythosCircle/_bmad-output/specs/spec-mythosCircle/conventions.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 1.3 ships the queue substrate but nothing consumes it — jobs sit `queued` forever, the LLM is never called, and 1.3's `claim/complete/fail/progress` primitives have no driver. The inference adapter (AR9, AR21) is the missing worker: it claims the next job, talks to any OpenAI-compatible HTTP server, and reports the outcome through the existing primitives.

**Approach:** Fill the two empty seams the spine reserves: `providers/llm.py` — a thin OpenAI-compatible chat-completions client (httpx, no vendor SDK) — and `pipeline/worker.py` — the queue runner that claims text jobs one at a time (DB-enforced exactly-one from 1.3), calls the provider over HTTP only, enforces the job's LLM-call budget (AR21), and drives `complete_job`/`fail_job` so `job_done`/`job_failed`/`queue_changed` broadcast over the 1.3 WS hub. Endpoint/model/key/timeout come from environment; swapping to LM Studio or OpenRouter is a config change, never code (AR9, NFR8). A background task in the app lifespan runs the worker loop; `config.toml` consumption stays deferred to Story 1.7 like 1.3.

## Boundaries & Constraints

**Always:**
- All generation goes through OpenAI-compatible HTTP adapters; the pipeline talks HTTP only — never a server's native bindings (AR9, AD-14).
- Exactly one job runs at a time: the worker uses 1.3's `claim_next_job()` (the BEGIN IMMEDIATE claim invariant), never a second concurrency mechanism (AD-3).
- The worker reports outcomes ONLY through the store primitives: `complete_job` → `job_done`+`queue_changed`, `fail_job` → `job_failed`+`queue_changed`; it never writes world state, never touches revisions/events (AD-1).
- Budget enforcement (AR21): the runner counts LLM HTTP calls per job against `job.max_llm_calls`; a call that would exceed the budget fails the job with a budget error rather than running unbounded. `max_media_calls` is stored but media generation is Epic 4.
- 1.4 runs `text` jobs only; an `image`/`video` job is failed with a clear "media service lands in Epic 4" error — never silently skipped, never left `running` (the queue must keep flowing).
- A text job's payload contract: `{"prompt": str}`. A job missing `prompt` fails with an error naming the missing key (async worker-side validation; the store's 4xx boundary already rejected non-dicts in 1.3).
- The worker's completion is persisted as a new nullable `result` JSON column on `job` (added by this story; jobs are not world graph, so this does not create revisions). The WS shape (AD-17) is unchanged — `result` is read via REST only.
- Endpoint, model, API key (optional, sent as `Authorization: Bearer` when set), and HTTP timeout come from env: `MYTHOSCIRCLE_LLM_ENDPOINT` (default `http://127.0.0.1:8080/v1`), `MYTHOSCIRCLE_LLM_MODEL` (default `mythos-14b-q5`), `MYTHOSCIRCLE_LLM_API_KEY`, `MYTHOSCIRCLE_LLM_TIMEOUT` (default 120) — the defaults mirror `deploy/config.toml` `[llm]`.
- No retries in this story: a provider failure (connection or non-2xx) fails the job with the error surfaced in the job's `error` field. The DM re-submits.
- Deterministic tests only — no live LLM. The provider is tested against an injected `httpx` transport (MockTransport); the worker against a stubbed provider.
- ULID ids; UTC ISO-8601 `Z` timestamps; error envelope `{code, message, details?}`; snake_case Python (conventions.md).

**Ask First:** adding a second concurrent worker (multi-GPU); streaming completions or retries before 1.4 ships; writing generation *results* into the world graph (Epic 2's commit flow owns that); changing the 1.3 job-table shape beyond the `result` column.

**Never:** an LLM/vendor SDK dependency (the OpenAI client is banned — httpx keeps the OpenAI-compatible contract literal); a worker that writes `job.result` outside `complete_job`; raw SQL outside `store/`; calling the provider from the API request path (generation is always backgrounded, AR5).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| RUN_TEXT_JOB | text job, `{prompt}` payload, healthy endpoint | claim → provider called once with the prompt → `complete_job(result={"text": ...})` → `job_done`+`queue_changed` | N/A |
| RUN_NO_JOB | empty queue (or a job already running) | returns None, no events, no LLM call | N/A |
| RUN_BAD_PROMPT | text job payload without `prompt` | `fail_job` with an error naming the missing key; `job_failed` emitted | N/A |
| RUN_BUDGET_ZERO | `max_llm_calls = 0` | job fails with a budget error, LLM never called | budget error in `job.error` |
| RUN_BUDGET_GUARD | budget-guarded call primitive, counter at the budget | the call is refused before any HTTP request | `BudgetExceededError` (internal) |
| RUN_HTTP_ERROR | endpoint returns 5xx/4xx | `fail_job` with the HTTP status in the error; `job_failed` emitted | provider `ProviderError(http_status=...)` |
| RUN_CONNECTION_ERROR | endpoint unreachable / timeout | `fail_job` with a connection error; `job_failed` emitted | provider `ProviderError(kind="connection")` |
| RUN_IMAGE_JOB | `image` kind job reaches the worker | `fail_job` "media service lands in Epic 4" — queue keeps flowing | N/A |
| RUN_API_KEY | `MYTHOSCIRCLE_LLM_API_KEY` set | `Authorization: Bearer <key>` header present on the request | N/A |
| CONFIG_SWAP | different `MYTHOSCIRCLE_LLM_ENDPOINT`/`_MODEL` | the provider targets them with no code change (AR9) | N/A |
| RESULT_PERSISTED | successful text job | `job.result == {"text": ...}` readable via `job_status`/GET; WS shape unchanged | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/core/settings.py` -- add `llm_settings()` (dataclass from env, defaults above) mirroring `queue_settings()`; reuse `_env_non_negative_int` for timeout.
- `backend/app/providers/llm.py` -- NEW: `ProviderError(Exception)` with `kind` (`connection`|`http`) and optional `status_code`; `chat_completion(messages, *, endpoint, model, api_key=None, timeout=120) -> str` POSTing `{model, messages}` to `{endpoint}/chat/completions` via `httpx.Client`; returns the assistant `content` from the OpenAI-shaped response; bare `httpx.RequestError`/`TimeoutError` → `ProviderError(kind="connection")`; non-2xx → `ProviderError(kind="http", status_code=...)`. The chosen transport is injectable for tests (`httpx.Client(transport=...)`).
- `backend/app/pipeline/worker.py` -- NEW: `run_next_job() -> str | None` (job id processed, or None); claims via `claim_next_job()`, dispatches on `job.kind`, calls the provider, `complete_job(job_id, result={"text": ...})` or `fail_job(job_id, error)`; `_text_prompt(payload)` validates `prompt`; `_llm_call(job, messages)` is the budget-guarded primitive (raises `BudgetExceededError` before the call when the per-job counter is at `job.max_llm_calls`); `worker_loop(stop: asyncio.Event)` — `await asyncio.to_thread(run_next_job)` per iteration, 0.2s idle sleep, exits on stop after the current job.
- `backend/app/store/models.py` -- add `result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)` to `Job` (the only 1.3-shape change; documented in this spec's change log).
- `backend/app/store/jobs.py` -- `complete_job(job_id, result: dict | None = None)` writes `job.result = result`; existing callers/tests unaffected (default None).
- `backend/app/api/jobs.py` -- add `result: dict[str, Any] | None` to `JobResponse` and `_to_response` so a finished job's completion is REST-readable (the WS shape stays unchanged, AD-17).
- `backend/app/main.py` -- lifespan starts `asyncio.create_task(worker_loop(stop_event))` after `hub.start()` and cancels it before `hub.stop()`; a job interrupted mid-run by a hard kill is requeued by 1.3's `recover_stale_running`.
- `backend/pyproject.toml` -- promote `httpx>=0.28,<1` from dev to runtime dependencies (the provider's HTTP client).
- `backend/tests/test_providers.py` -- NEW: MockTransport tests for success content, api_key header, endpoint/model passthrough (CONFIG_SWAP), http error, connection error.
- `backend/tests/test_worker.py` -- NEW: `run_next_job` rows RUN_TEXT_JOB, RUN_NO_JOB, RUN_BAD_PROMPT, RUN_BUDGET_ZERO, RUN_BUDGET_GUARD, RUN_HTTP_ERROR, RUN_CONNECTION_ERROR, RUN_IMAGE_JOB, RESULT_PERSISTED + job_done/job_failed listener assertions; reuse the 1.3 `world`/`listener`-style fixtures (monkeypatch `llm_settings` + inject a stub provider via the worker's provider seam).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/settings.py` -- add `llm_settings()` -- env config for the adapter (AR22: config change, no code change).
- [x] `backend/app/providers/llm.py` -- OpenAI-compatible chat-completions client -- AR9/AD-14 (HTTP only, swappable).
- [x] `backend/app/store/models.py` + `jobs.py` -- `Job.result` column + `complete_job(job_id, result=None)` -- persist the completion for REST reads.
- [x] `backend/app/api/jobs.py` -- `result` on `JobResponse`/`_to_response` -- the completion is GET-readable.
- [x] `backend/app/pipeline/worker.py` -- `run_next_job` + budget guard + `worker_loop` -- the AR9/AR21 worker.
- [x] `backend/app/main.py` -- lifespan worker task -- the queue actually drains in the running app.
- [x] `backend/pyproject.toml` -- httpx runtime dep.
- [x] `backend/tests/test_providers.py` -- I/O matrix rows for the adapter.
- [x] `backend/tests/test_worker.py` -- I/O matrix rows for the runner incl. budget guard + listener emissions.

**Acceptance Criteria:**
- Given a config pointing at a local llama-server, when a queued text job runs, then the LLM is called over HTTP only and completion is reported via `job_done` with the result persisted (AD-14, AR9).
- Given a job declaring max LLM calls, when it would exceed the budget, then the job fails with an error event rather than running unbounded (AR21).
- Given a config change swapping the endpoint (e.g. LM Studio or Ollama), when redeployed, then no code change is required (NFR8, AR9).

## Spec Change Log

<!-- Append-only. Populated by step-04 during review loops. Do not modify or delete existing entries.
     Each entry records: what finding triggered the change, what was amended, what known-bad state
     the amendment avoids, and any KEEP instructions (what worked well and must survive re-derivation).
     Empty until the first bad_spec loopback. -->

## Design Notes

1.4's budget guard is a mechanism, not a fiction: the single-completion runner makes one call per job today, but the guard lives on the per-job counter so Epic 2's multi-call generations (retrieval passes, AR25 repair passes — each counting against `max_llm_calls`) reuse it unchanged. A job declaring `max_llm_calls = 0` is the "would exceed" case proven end to end.

Non-text jobs are failed, not skipped: skipping would leave the earliest job `running` forever (claim is FIFO) and stall the queue; failing surfaces the "media service lands in Epic 4" boundary honestly and keeps the FIFO flowing (AD-3). Epic 4's media worker takes over both kinds.

The worker sleeps 0.2s when idle rather than spinning: claim is a DB transaction (BEGIN IMMEDIATE), and a hot loop would hammer the write lock. The stop event is checked between jobs; an in-flight `to_thread` job is allowed to finish (a hard kill requeues it via 1.3's `recover_stale_running`, so no job is ever lost).

## Verification

**Commands:**
- `uv run --directory backend pytest -q` -- expected: all provider/worker tests + the full suite green (deterministic, no live LLM).
- `make lint && make typecheck` -- expected: ruff, mypy strict, eslint, vue-tsc clean (backend changes only).

## Suggested Review Order

**The adapter (HTTP-only, swappable)**

- The OpenAI-compatible chat-completions client — request shape, error classes, injectable transport
  [`llm.py:56`](../../backend/app/providers/llm.py#L56)

**The worker (queue driver, budget)**

- claim→dispatch→complete/fail; every outcome leaves a claimed job terminal (AD-3)
  [`worker.py:49`](../../backend/app/pipeline/worker.py#L49)

- Budget guard refuses before any HTTP call (AR21) — Epic 2 reuses this primitive
  [`worker.py:119`](../../backend/app/pipeline/worker.py#L119)

- Cancel-race poll: a job cancelled between claim and call is a no-op (review round 1)
  [`worker.py:80`](../../backend/app/pipeline/worker.py#L80)

- The lifespan drain loop — 0.2s idle, exits on stop; tests may disable it
  [`worker.py:145`](../../backend/app/pipeline/worker.py#L145)
  [`main.py:25`](../../backend/app/main.py#L25)

**Schema + settings**

- The one 1.3-shape change persisted via complete_job(result=), migrated idempotently
  [`db.py:95`](../../backend/app/store/db.py#L95)

- Env config with fail-loud parsing (positive timeout, trimmed endpoint/model)
  [`settings.py:102`](../../backend/app/core/settings.py#L102)

**Tests (review-regression)**

- The seam that caught the keyword-settings TypeError: real provider end to end
  [`test_worker.py:238`](../../backend/tests/test_worker.py#L238)

- Cancel-race poll + migration + timeout classification
  [`test_worker.py:264`](../../backend/tests/test_worker.py#L264)
  [`test_worker.py:289`](../../backend/tests/test_worker.py#L289)
  [`test_providers.py:104`](../../backend/tests/test_providers.py#L104)
