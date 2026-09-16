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
exceeded — the build-in runner's waves and bounded stat-repair
passes, and the generate runner's call plus its bounded stat-repair
passes, all reuse the same guard.

"""

import asyncio
import contextlib
import logging
from typing import Any

from app.core.journal import journal_context
from app.core.settings import (
    ComfyUIImageSettings,
    ComfyUIVideoSettings,
    ImageSettings,
    LLMSettings,
    VideoSettings,
    configured_image_backend,
    configured_media_dir,
    configured_video_backend,
    llm_settings,
)
from app.core.settings import (
    comfyui_image_settings as resolve_comfyui_image_settings,
)
from app.core.settings import (
    comfyui_video_settings as resolve_comfyui_video_settings,
)
from app.core.settings import (
    image_settings as resolve_image_settings,
)
from app.core.settings import (
    video_settings as resolve_video_settings,
)
from app.pipeline.budget import BudgetExceededError, CallBudget
from app.providers.comfyui import ComfyUIImageGeneration, comfyui_image_generation
from app.providers.comfyui_video import (
    ComfyUIVideoGeneration,
    comfyui_video_generation,
)
from app.providers.image import ImageGeneration, image_generation
from app.providers.llm import ChatCompletion, ProviderError, chat_completion
from app.providers.video import VideoGeneration, video_generation
from app.store import (
    JobStateConflictError,
    claim_next_job,
    complete_job,
    discard_candidates,
    fail_job,
    job_status,
    models,
)

logger = logging.getLogger(__name__)

#: Provider signatures the worker depends on — injectable for tests.
#: ``chat_completion``/``image_generation`` are the production defaults
#: (transport injected).
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
    image_provider: ImageGeneration | None = None,
    image_settings: ImageSettings | None = None,
    video_provider: VideoGeneration | None = None,
    video_settings: VideoSettings | None = None,
    comfyui_image_provider: ComfyUIImageGeneration | None = None,
    comfyui_image_settings: ComfyUIImageSettings | None = None,
    comfyui_video_provider: ComfyUIVideoGeneration | None = None,
    comfyui_video_settings: ComfyUIVideoSettings | None = None,
) -> str | None:
    """Process at most one job; returns its id, or None when idle.

    Claims the next queued job, runs it to a terminal state, and returns
    the job id. Never leaves a claimed job ``running``: every outcome —
    success, budget exceeded, provider failure, bad payload, unsupported
    kind, unexpected error — ends in ``complete_job`` or ``fail_job`` so
    the queue keeps flowing (AD-3). ``None`` means there was nothing to
    claim; the caller (``worker_loop``) idles before retrying.

    ``image_provider``/``image_settings`` are the portrait dispatch's
    injectables (spec-4.1) — ``image_generation``/``image_settings()``
    are the production defaults, resolved at dispatch time so an
    unrelated text job never pays the image config read.
    ``video_provider``/``video_settings`` mirror them for the
    reveal-video dispatch (spec-4.2). ``comfyui_image_provider``/
    ``comfyui_image_settings`` are the third pair (spec-4.4): when
    ``[image] backend = "comfyui"``, the image branch routes through
    ``comfyui_image_generation``/``comfyui_image_settings()`` instead
    — the OpenAI path is untouched when ``"openai"`` (the default).
    ``comfyui_video_provider``/``comfyui_video_settings`` are the
    fourth pair (spec-4.5): when ``[video] backend = "comfyui"``, the
    video branch routes through ``comfyui_video_generation``/
    ``comfyui_video_settings()`` instead — the OpenAI path is untouched
    when ``"openai"`` (the default).
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
        # The per-job LLM call journal (owner note 7): every provider
        # attempt inside this run transcribes to <journal-dir>/<job-id>.
        # Cost: one mkdir + prune per job; failures are swallowed.
        with journal_context(job.id):
            _run_job(
                job,
                provider,
                settings,
                image_provider,
                image_settings,
                video_provider,
                video_settings,
                comfyui_image_provider,
                comfyui_image_settings,
                comfyui_video_provider,
                comfyui_video_settings,
            )
    except JobStateConflictError:
        # The job is terminal (cancelled) mid-run. The generate runner
        # has already discarded its ghost staged rows by the time it
        # re-raises; the media runners keep their file + manifest row
        # (real content, no dangling row). Either way, failing a
        # cancelled job would raise a second conflict that only
        # produces a spurious log.
        pass
    except Exception as exc:  # noqa: BLE001 - a claimed job must never wedge the queue
        if job.kind in ("generate", "regenerate"):
            # A re-run of a crash-requeued generate job can fail before
            # re-staging (provider outage, <2 valid survivors, world
            # emptied by undo): the first run's staged rows would outlive
            # the FAILED job and be served to the accept screen — "a
            # failed job stages nothing" (spec-3.1 FEWER_THAN_TWO
            # semantics). Discard them; a first-run failure removes zero
            # rows (none staged yet). Success-path idempotency is
            # untouched (review round 2).
            try:
                discard_candidates(job.id)
            except Exception:  # noqa: BLE001 - discard must not mask the original error
                logger.exception("failed to discard staged rows for job %s", job.id)
        try:
            fail_job(job.id, _error_message(exc))
        except Exception:  # noqa: BLE001 - failing the fail must not crash the loop
            logger.exception("failed to record failure for job %s", job.id)
    return job.id


def _run_job(
    job: models.Job,
    provider: Provider,
    settings: LLMSettings,
    image_provider: ImageGeneration | None = None,
    image_settings: ImageSettings | None = None,
    video_provider: VideoGeneration | None = None,
    video_settings: VideoSettings | None = None,
    comfyui_image_provider: ComfyUIImageGeneration | None = None,
    comfyui_image_settings: ComfyUIImageSettings | None = None,
    comfyui_video_provider: ComfyUIVideoGeneration | None = None,
    comfyui_video_settings: ComfyUIVideoSettings | None = None,
) -> None:
    if job.kind == "build_in":
        # Lazy import: ``build_in`` imports ``JobPayloadError`` from this
        # module, so a module-level import here would be circular.
        from app.pipeline.build_in import run_build_in

        run_build_in(job, provider, settings)
        return
    if job.kind == "add_character":
        # The fully-authored path (spec: hybrid authorship): the runner
        # takes NO provider — zero-LLM is structural, not policed. Its
        # backstop rejection is the named STRUCTURAL_VALIDATION_FAILURE
        # code, not the generic worker error.
        from app.pipeline.direct import (
            STRUCTURAL_VALIDATION_FAILURE,
            StructuralValidationError,
            run_add_character,
        )

        try:
            run_add_character(job)
        except StructuralValidationError as exc:
            fail_job(
                job.id,
                f"{STRUCTURAL_VALIDATION_FAILURE}: " + "; ".join(exc.violations),
            )
        return
    if job.kind == "generate":
        # Lazy import (same circularity as ``build_in``): the generate
        # runner stages candidates and commits nothing (spec-3.1).
        from app.pipeline.generate import run_generate

        run_generate(job, provider, settings)
        return
    if job.kind == "regenerate":
        # Lazy import (same circularity): the regenerate runner re-rolls
        # an entity (whole, staging a new proposal) or a proposed
        # candidate (whole/per-section, replacing its row) — commits
        # nothing (spec-3.5).
        from app.pipeline.regenerate import run_regenerate

        run_regenerate(job, provider, settings)
        return
    if job.kind == "image":
        # Lazy import (same circularity): the portrait runner writes the
        # image file + the store's manifest row and commits no world
        # state (spec-4.1, AD-1 — media is not world graph).
        from app.media.service import run_portrait

        if configured_image_backend() == "comfyui":
            # ComfyUI is the opt-in alternative local-dev backend
            # (spec-4.4, [image] backend = "comfyui"): the poll-based
            # provider returns the same PNG bytes, so the shared
            # run_portrait runner — PNG guard, atomic write, manifest
            # row, cancel-race poll — is unchanged; only the provider
            # and its settings swap.
            run_portrait(
                job,
                comfyui_image_provider or comfyui_image_generation,
                # run_portrait's ``settings`` parameter is a transparent
                # passthrough to the provider (the runner never
                # interprets it) — the runner types it as the shared
                # ProviderSettings protocol both settings shapes
                # satisfy, so no ignore is needed (review round 1).
                comfyui_image_settings or resolve_comfyui_image_settings(),
                media_dir=configured_media_dir(),
            )
            return
        run_portrait(
            job,
            image_provider or image_generation,
            # ``image_settings()`` is imported aliased — the parameter of
            # the same name would shadow it and turn the fallback into a
            # NoneType call.
            image_settings or resolve_image_settings(),
            media_dir=configured_media_dir(),
        )
        return
    if job.kind == "video":
        # Lazy import (same circularity): the reveal-video runner writes
        # the .mp4 file + the store's manifest row and commits no world
        # state (spec-4.2/4.5, AD-1 — media is not world graph).
        from app.media.service import run_video

        if configured_video_backend() == "comfyui":
            # ComfyUI is the opt-in alternative local-dev backend
            # (spec-4.5, [video] backend = "comfyui"): the poll-based
            # i2v provider (submit/poll/fetch) returns the same mp4
            # bytes, so the shared run_video runner — portrait
            # resolution, first_frame passthrough, mp4 guard, atomic
            # write, manifest row, cancel-race poll — is unchanged;
            # only the provider and its settings swap.
            run_video(
                job,
                comfyui_video_provider or comfyui_video_generation,
                # run_video's ``settings`` parameter is a transparent
                # passthrough to the provider (the runner never
                # interprets it) — the runner types it as the shared
                # ProviderSettings protocol both settings shapes
                # satisfy, so no ignore is needed (review round 1).
                comfyui_video_settings or resolve_comfyui_video_settings(),
                media_dir=configured_media_dir(),
            )
            return
        run_video(
            job,
            video_provider or video_generation,
            video_settings or resolve_video_settings(),
            media_dir=configured_media_dir(),
        )
        return
    if job.kind == "video_prompt":
        # Lazy import (same circularity): the draft runner produces the
        # DM-reviewable MiniMax-I2VA prompt as the job result (spec-4.6)
        # — an LLM call, no file, no media row, no world write (AD-1).
        from app.media.service import run_video_prompt

        run_video_prompt(job, provider, settings)
        return
    if job.kind != "text":
        raise JobPayloadError(
            f"job kind {job.kind!r}: only text/build_in/generate/regenerate/"
            "image/video/video_prompt jobs run in this build"
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
    image_provider: ImageGeneration | None = None,
    image_settings: ImageSettings | None = None,
) -> None:
    """The background queue drainer; exits on ``stop`` between jobs.

    Each iteration runs ``run_next_job`` in a worker thread (store
    primitives are synchronous), then idles on ``stop.wait`` for
    ``IDLE_SLEEP`` — short enough to stay responsive, long enough not to
    hammer the write lock. An in-flight job is allowed to finish; a hard
    kill is requeued by ``recover_stale_running`` (spec-1.3). The
    providers/settings are injectable for deterministic tests (the LLM
    pair for text/build_in/generate/regenerate, the image pair for
    spec-4.1 portraits).
    """
    while not stop.is_set():
        processed = await asyncio.to_thread(
            run_next_job, provider, settings, image_provider, image_settings
        )
        if processed is not None:
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP)
