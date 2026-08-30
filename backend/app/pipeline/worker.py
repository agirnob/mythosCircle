"""The queue worker — drives Story 1.3's primitives (AR9, AR21).

One worker loop, one job at a time: ``claim_next_job`` (DB-enforced
exactly-one, BEGIN IMMEDIATE) -> dispatch on kind -> provider call over
HTTP -> ``complete_job``/``fail_job`` so ``job_done``/``job_failed``/
``queue_changed`` broadcast over the WS hub. The worker proposes nothing
and commits nothing to the world graph (AD-1) — generation output rides
on a job's ``result`` column, never a revision.

Budget (AR21): every LLM call goes through ``_CallBudget``, which checks
the per-job counter against ``job.max_llm_calls`` BEFORE the HTTP request
and fails the job when exceeded — Epic 2's multi-call generations reuse
the same guard unchanged.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from app.core.settings import LLMSettings, llm_settings
from app.providers.llm import ChatCompletion, ProviderError, chat_completion
from app.store import claim_next_job, complete_job, fail_job, job_status, models

logger = logging.getLogger(__name__)

#: Provider signature the worker depends on — injectable for tests.
#: ``chat_completion`` is the production default (transport injected).
Provider = ChatCompletion

#: Idle sleep between claim attempts (seconds). Claim is a BEGIN IMMEDIATE
#: transaction; a hot loop would hammer the write lock (spec-1.4 Design Notes).
IDLE_SLEEP = 0.2


class BudgetExceededError(Exception):
    """The job's per-call budget would be exceeded (AR21) — internal guard."""

    def __init__(self, budget: int) -> None:
        super().__init__(f"job would exceed its max_llm_calls budget ({budget})")
        self.budget = budget


class JobPayloadError(ValueError):
    """A job's payload is not what the worker expects (e.g. ``prompt`` missing)."""


def run_next_job(
    provider: Provider = chat_completion,
    settings: LLMSettings | None = None,
) -> str | None:
    """Process at most one job; returns its id, or None when idle.

    Claims the next queued job, runs it to a terminal state, and returns
    the job id. Never leaves a claimed job ``running``: every outcome —
    success, budget exceeded, provider failure, bad payload, unsupported
    kind, unexpected error — ends in ``complete_job`` or ``fail_job`` so
    the queue keeps flowing (AD-3). ``None`` means there was nothing to
    claim; the caller (``worker_loop``) idles before retrying.
    """
    try:
        settings = settings or llm_settings()
        job = claim_next_job()
    except Exception:  # noqa: BLE001 - a claim/settings failure must not kill the loop
        logger.exception("worker claim failed")
        return None
    if job is None:
        return None
    try:
        _run_job(job, provider, settings)
    except Exception as exc:  # noqa: BLE001 - a claimed job must never wedge the queue
        try:
            fail_job(job.id, _error_message(exc))
        except Exception:  # noqa: BLE001 - failing the fail must not crash the loop
            logger.exception("failed to record failure for job %s", job.id)
    return job.id


def _run_job(job: models.Job, provider: Provider, settings: LLMSettings) -> None:
    if job.kind == "build_in":
        raise JobPayloadError("job kind 'build_in': the build-in runner lands in Story 2.3")
    if job.kind != "text":
        raise JobPayloadError(
            f"job kind {job.kind!r}: the media service lands in Epic 4 — "
            "only text jobs run in story 1.4"
        )
    prompt = _text_prompt(job.payload)
    budget = _CallBudget(job)
    # Cancel-race poll (review round 1): cancel_job may have freed this
    # slot while we were between claim and call. A terminal job is a
    # no-op — completing a cancelled job would raise JobStateConflict.
    try:
        state, _position = job_status(job.id)
    except Exception:  # noqa: BLE001 - never wedge the queue on a status error
        logger.exception("worker state check failed for job %s", job.id)
        state = None
    if state is not None and state.state != "running":
        return
    try:
        # ``settings`` is keyword-only on ``chat_completion`` — a positional
        # call raises TypeError on every job (review round 1).
        text = budget.call(lambda: provider(prompt, settings=settings))
    except BudgetExceededError:
        raise
    except ProviderError:
        raise
    complete_job(job.id, result={"text": text})


def _text_prompt(payload: dict[str, Any]) -> str:
    """The text job's prompt (spec-1.4 payload contract: ``{"prompt": str}``)."""
    if not isinstance(payload, dict) or "prompt" not in payload:
        raise JobPayloadError("text job payload missing 'prompt'")
    prompt = payload["prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise JobPayloadError("text job payload 'prompt' must be a non-empty string")
    return prompt


class _CallBudget:
    """Per-job LLM-call budget guard (AR21): refuses before any HTTP request."""

    def __init__(self, job: models.Job) -> None:
        self._budget = job.max_llm_calls
        self._used = 0

    def call(self, provider_call: Callable[[], str]) -> str:
        if self._used >= self._budget:
            raise BudgetExceededError(self._budget)
        text = provider_call()
        self._used += 1
        return text


def _error_message(exc: Exception) -> str:
    """A stable, user-facing error string for ``fail_job``."""
    if isinstance(exc, ProviderError) and exc.kind == "http":
        return f"llm call failed: provider returned HTTP {exc.status_code}"
    if isinstance(exc, ProviderError):
        return "llm call failed: provider connection error"
    if isinstance(exc, (BudgetExceededError, JobPayloadError, ValueError)):
        return str(exc)
    return f"worker error: {exc.__class__.__name__}: {exc}"


async def worker_loop(
    stop: asyncio.Event,
    *,
    provider: Provider = chat_completion,
    settings: LLMSettings | None = None,
) -> None:
    """The background queue drainer; exits on ``stop`` between jobs.

    Each iteration runs ``run_next_job`` in a worker thread (store
    primitives are synchronous), then idles on ``stop.wait`` for
    ``IDLE_SLEEP`` — short enough to stay responsive, long enough not to
    hammer the write lock. An in-flight job is allowed to finish; a hard
    kill is requeued by ``recover_stale_running`` (spec-1.3). The
    provider/settings are injectable for deterministic tests.
    """
    while not stop.is_set():
        processed = await asyncio.to_thread(run_next_job, provider, settings)
        if processed is not None:
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP)
