"""The queue worker — drives Story 1.3's primitives (AR9, AR21).

One worker loop, one job at a time: ``claim_next_job`` (DB-enforced
exactly-one, BEGIN IMMEDIATE) -> dispatch on kind -> provider call over
HTTP -> ``complete_job``/``fail_job`` so ``job_done``/``job_failed``/
``queue_changed`` broadcast over the WS hub. The worker writes no world
rows itself (AD-1): text-job output rides on a job's ``result`` column,
never a revision; build-in jobs commit through the store's
``commit_subgraph``; ``generate`` jobs (spec-3.1) stage
``ProposedCandidate`` rows through the store — the store remains the
sole writer of world state, and the generate runner commits nothing.
A failing generation writes nothing; waves committed before the failure
(the build-in wave-1 core) stay committed — documented resilience, no
compensating undo.
Budget (AR21): every LLM call goes through ``CallBudget``
(``app.pipeline.budget``), which checks the per-job counter against
``job.max_llm_calls`` BEFORE the HTTP request and fails the job when
exceeded — the build-in runner's waves and stat-repair pass, and the
generate runner's call plus its one bounded repair pass, all reuse
the same guard.

"""

import asyncio
import contextlib
import logging
from typing import Any

from app.core.settings import LLMSettings, llm_settings
from app.pipeline.budget import BudgetExceededError, CallBudget
from app.providers.llm import ChatCompletion, ProviderError, chat_completion
from app.store import claim_next_job, complete_job, fail_job, job_status, models

logger = logging.getLogger(__name__)

#: Provider signature the worker depends on — injectable for tests.
#: ``chat_completion`` is the production default (transport injected).
Provider = ChatCompletion

#: Idle sleep between claim attempts (seconds). Claim is a BEGIN IMMEDIATE
#: transaction; a hot loop would hammer the write lock (spec-1.4 Design Notes).
IDLE_SLEEP = 0.2


class JobPayloadError(ValueError):
    """A job's output channel is not what the worker expects: bad payload
    (``prompt`` missing), malformed wave/repair output, or — since spec-2.4 —
    the AR25 stat-block failure whose message becomes the fail event."""


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
        # Lazy import: ``build_in`` imports ``JobPayloadError`` from this
        # module, so a module-level import here would be circular.
        from app.pipeline.build_in import run_build_in

        run_build_in(job, provider, settings)
        return
    if job.kind == "generate":
        # Lazy import (same circularity as ``build_in``): the generate
        # runner stages candidates and commits nothing (spec-3.1).
        from app.pipeline.generate import run_generate

        run_generate(job, provider, settings)
        return
    if job.kind != "text":
        raise JobPayloadError(
            f"job kind {job.kind!r}: the media service lands in Epic 4 — "
            "only text jobs run in story 1.4"
        )
    prompt = _text_prompt(job.payload)
    budget = CallBudget(job)
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
