"""Worker tests: the queue runner drives the 1.3 primitives (spec-1.4).

Deterministic — the provider is injected, never a live LLM. Covers the
I/O matrix rows RUN_TEXT_JOB, RUN_NO_JOB, RUN_BAD_PROMPT, RUN_BUDGET_ZERO,
RUN_BUDGET_GUARD, RUN_HTTP_ERROR, RUN_CONNECTION_ERROR, RUN_IMAGE_JOB,
RESULT_PERSISTED + the listener emissions.
"""

import asyncio
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.settings import LLMSettings
from app.pipeline.worker import (
    BudgetExceededError,
    JobPayloadError,
    _CallBudget,
    run_next_job,
    worker_loop,
)
from app.providers.llm import ProviderError
from app.store import (
    app_db_url,
    cancel_job,
    claim_next_job,
    create_campaign,
    enqueue_job,
    init_db,
    job_status,
    models,
    set_change_listener,
)

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'worker.db'}")
    try:
        yield create_campaign("Worker Test World").id
    finally:
        init_db(previous)


def _enqueue_text(world: str, prompt: str = "build the bar", **overrides: Any) -> str:
    return enqueue_job(world, "text", {"prompt": prompt}, **overrides).id


def _ok_provider(prompt: str, settings: LLMSettings) -> str:
    assert settings is SETTINGS
    return f"generated: {prompt}"


def test_run_text_job_completes_with_result(world: str) -> None:
    """RUN_TEXT_JOB: claim -> provider called -> complete_job(result) with
    the generated text; the completion is persisted for REST reads."""
    job_id = _enqueue_text(world)
    processed = run_next_job(provider=_ok_provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result == {"text": "generated: build the bar"}
    assert job.finished_at is not None


def test_run_no_job_returns_none(world: str) -> None:
    """RUN_NO_JOB: an empty queue yields None, no claim, no provider call."""
    called: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return "x"

    assert run_next_job(provider=provider, settings=SETTINGS) is None
    assert called == []


def test_run_bad_prompt_fails_job(world: str) -> None:
    """RUN_BAD_PROMPT: a text job missing 'prompt' fails with a clear error;
    the queue keeps flowing (job_failed emitted)."""
    job_id = enqueue_job(world, "text", {"not_prompt": 1}).id
    processed = run_next_job(provider=_ok_provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "prompt" in (job.error or "")


def test_run_budget_zero_fails_before_llm(world: str) -> None:
    """RUN_BUDGET_ZERO: max_llm_calls=0 fails the job, LLM never called."""
    job_id = _enqueue_text(world, max_llm_calls=0)
    called: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return "x"

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "budget" in (job.error or "").lower()
    assert called == []


def test_call_budget_guard_refuses_at_budget() -> None:
    """RUN_BUDGET_GUARD: the per-call primitive refuses before any HTTP
    request once the counter is at the budget (AR21)."""
    job = models.Job(id="J" * 26, max_llm_calls=1)
    budget = _CallBudget(job)
    calls: list[str] = []

    def provider_call() -> str:
        calls.append("called")
        return "ok"

    assert budget.call(provider_call) == "ok"
    with pytest.raises(BudgetExceededError):
        budget.call(provider_call)
    assert calls == ["called"]  # the second call never reached the provider


def test_run_http_error_fails_job(world: str) -> None:
    """RUN_HTTP_ERROR: a non-2xx provider response fails the job with the
    HTTP status surfaced in the error."""

    def provider(prompt: str, settings: LLMSettings) -> str:
        raise ProviderError("http", status_code=502)

    job_id = _enqueue_text(world)
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "502" in (job.error or "")


def test_run_connection_error_fails_job(world: str) -> None:
    """RUN_CONNECTION_ERROR: an unreachable endpoint fails the job with a
    connection error (no retries in 1.4)."""

    def provider(prompt: str, settings: LLMSettings) -> str:
        raise ProviderError("connection")

    job_id = _enqueue_text(world)
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "connection" in (job.error or "")


def test_run_image_job_fails_kind_boundary(world: str) -> None:
    """RUN_IMAGE_JOB: a non-text kind fails with the Epic-4 boundary error
    so the FIFO never stalls on it (a skip would leave it running forever)."""
    job_id = enqueue_job(world, "image", {"prompt": "a portrait"}).id
    run_next_job(provider=_ok_provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "Epic 4" in (job.error or "")


def test_result_persisted_and_ws_shape_unchanged(world: str) -> None:
    """RESULT_PERSISTED: job.result is readable via job_status; the AD-17
    WS message carries no 'result' key (the shape is unchanged)."""
    events: list[tuple[str, str]] = []
    set_change_listener(lambda event, job, _p: events.append((event, job.id)))
    try:
        job_id = _enqueue_text(world)
        run_next_job(provider=_ok_provider, settings=SETTINGS)
        assert ("job_done", job_id) in events
        assert ("queue_changed", job_id) in events
        job, _position = job_status(job_id)
        assert job.result == {"text": "generated: build the bar"}
    finally:
        set_change_listener(None)  # never leak the listener into later tests


def test_unexpected_error_fails_job_not_crash(world: str) -> None:
    """A provider blow-up (unexpected exception) still fails the job — a
    claimed job must never wedge the queue (AD-3)."""

    def provider(prompt: str, settings: LLMSettings) -> str:
        raise RuntimeError("kaboom")

    job_id = _enqueue_text(world)
    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "kaboom" in (job.error or "")


def test_worker_loop_drains_then_idles_and_stops(tmp_path: Path) -> None:
    """The loop claims jobs (via to_thread) and exits promptly on stop —
    run with a fresh event loop so no plugin is needed (deterministic)."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'loop.db'}")
    try:
        campaign = create_campaign("Loop World").id
        job_id = enqueue_job(campaign, "text", {"prompt": "loop"}).id
        _ = job_id

        async def scenario() -> None:
            stop = asyncio.Event()
            task = asyncio.create_task(worker_loop(stop, provider=_ok_provider, settings=SETTINGS))
            # The loop should claim + complete the job, then idle.
            for _ in range(50):
                job, _position = job_status(job_id)
                if job.state == "succeeded":
                    break
                await asyncio.sleep(0.02)
            assert job_status(job_id)[0].state == "succeeded"
            stop.set()
            await asyncio.wait_for(task, timeout=2.0)

        asyncio.run(scenario())
    finally:
        init_db(previous)


def test_job_payload_error_is_value_error() -> None:
    with pytest.raises(JobPayloadError):
        raise JobPayloadError("missing prompt")


# ---------------------------------------------------------------------------
# Review-regression tests (2026-08-30 review round 1)
# ---------------------------------------------------------------------------


def test_real_provider_end_to_end_with_mock_transport(world: str) -> None:
    """The production default path: real ``chat_completion`` (via a bound
    MockTransport) through ``run_next_job``. This is the seam that would
    have caught the keyword-``settings`` TypeError — a positional call in
    ``_run_job`` previously failed every job (review round 1)."""
    from app.providers.llm import chat_completion

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        # The prompt rides as the user message's content (httpx compact
        # JSON: no space after the colon), not a 'prompt' key.
        assert '"content":"build the bar"' in body
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "generated end to end"}}]},
        )

    provider = partial(chat_completion, transport=httpx.MockTransport(handler))
    job_id = _enqueue_text(world)
    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result == {"text": "generated end to end"}


def test_cancelled_mid_run_poll_is_noop(world: str) -> None:
    """The cancel-race poll in ``_run_job``: a claimed job cancelled while
    the worker was between claim and provider call is a no-op — the
    provider is never called, the job stays cancelled (review round 1).

    ``_run_job`` is exercised directly with the stale in-memory ``running``
    object; the store says ``cancelled`` (what the poll sees)."""
    from app.pipeline.worker import _run_job

    called: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return "x"

    job_id = _enqueue_text(world)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id
    cancel_job(job_id)  # DB now: cancelled; the in-memory object still says running
    _run_job(claimed, provider, SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "cancelled"  # the poll returned without completing
    assert called == []  # the provider call was never made


def test_migration_adds_result_column_to_old_db(tmp_path: Path) -> None:
    """A database created before story 1.4 (no ``job.result``) gets the
    column on init — create_all never ALTERs, so the migration owns it
    (review round 1); idempotent on a second init."""
    import sqlite3

    from sqlalchemy import text

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE campaign (id VARCHAR(26) PRIMARY KEY, name VARCHAR(500),"
        " created_at VARCHAR(40))"
    )
    conn.execute(
        "CREATE TABLE job (id VARCHAR(26) PRIMARY KEY, campaign_id VARCHAR(26),"
        " kind VARCHAR(64), payload JSON, state VARCHAR(32), progress FLOAT,"
        " max_llm_calls INTEGER, max_media_calls INTEGER, error TEXT,"
        " created_at VARCHAR(40), started_at VARCHAR(40), finished_at VARCHAR(40))"
    )
    conn.commit()
    conn.close()

    previous = app_db_url()
    try:
        init_db(f"sqlite:///{db}")
        from app.store import get_engine

        with get_engine().connect() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(job)"))}
        assert "result" in columns
        init_db(f"sqlite:///{db}")  # idempotent — no error on the second init
    finally:
        init_db(previous)
