"""Each physical attempt is bounded, journaled and uses the job snapshot."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.journal import journal_context
from app.core.llm_routing import (
    GEMINI_ENDPOINT,
    OPENROUTER_ENDPOINT,
    LLMRouting,
    LLMRoutingConfig,
    defaults,
)
from app.core.settings import LLMSettings
from app.pipeline.budget import BudgetExceededError, CallBudget
from app.pipeline.llm_routing import resolve_job_settings
from app.providers.llm import ProviderError, chat_completion
from app.store.db import init_db
from app.store.llm_settings import save
from app.store.models import Job


def routed() -> LLMSettings:
    primary = LLMSettings(endpoint="http://local/v1/", model="local", max_tokens=100)
    backup = LLMSettings(endpoint=GEMINI_ENDPOINT, model="gemini-3.8-flash", provider="gemini")
    return replace(primary, routing=LLMRouting(primary, backup))


def budget(maximum: int = 2) -> CallBudget:
    return CallBudget(Job(max_llm_calls=maximum))


@pytest.mark.parametrize("failure", ["connection", 429, 500, 503])
def test_transient_backup_schema_adjustments_and_journal(
    failure: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            if failure == "connection":
                raise httpx.ConnectError("private internals", request=request)
            return httpx.Response(failure, text="private body")
        assert str(request.url) == GEMINI_ENDPOINT + "chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setenv("MYTHOSCIRCLE_JOURNAL_DIR", str(tmp_path))
    schema = {"type": "json_schema", "json_schema": {"name": "test", "schema": {"type": "object"}}}
    settings = replace(
        routed(), max_tokens=37, temperature=0.2, enable_thinking=True, response_format=schema
    )
    guard = budget()
    with journal_context("job"):
        assert (
            guard.call(
                lambda: chat_completion(
                    "prompt", settings=settings, transport=httpx.MockTransport(respond)
                )
            )
            == "OK"
        )
    assert guard.used == 2 and len(calls) == 2
    assert calls[1]["max_tokens"] == 37 and calls[1]["temperature"] == 0.2
    assert calls[1]["response_format"] == schema and "chat_template_kwargs" not in calls[1]
    entries = [json.loads(line) for line in (tmp_path / "job.jsonl").read_text().splitlines()]
    assert [entry["model"] for entry in entries] == ["local", "gemini-3.8-flash"]
    assert entries[1]["max_tokens"] == 37
    assert entries[1]["label"] == "call_backup"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400),
        httpx.Response(401),
        httpx.Response(403),
        httpx.Response(200, text="invalid"),
        httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
        httpx.Response(
            200, json={"choices": [{"message": {"content": "cut"}, "finish_reason": "length"}]}
        ),
    ],
)
def test_nontransient_never_falls_back(response: httpx.Response) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response

    guard = budget()
    with pytest.raises(ProviderError):
        guard.call(
            lambda: chat_completion(
                "prompt", settings=routed(), transport=httpx.MockTransport(respond)
            )
        )
    assert guard.used == 1 and len(calls) == 1


def test_exhausted_budget_refuses_backup_before_http() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    guard = budget(1)
    with pytest.raises(BudgetExceededError):
        guard.call(
            lambda: chat_completion(
                "prompt", settings=routed(), transport=httpx.MockTransport(respond)
            )
        )
    assert guard.used == 1 and len(calls) == 1


def test_backup_failure_has_no_loop() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    guard = budget(10)
    with pytest.raises(ProviderError):
        guard.call(
            lambda: chat_completion(
                "prompt", settings=routed(), transport=httpx.MockTransport(respond)
            )
        )
    assert guard.used == 2 and len(calls) == 2


def test_concurrent_physical_attempts_stay_within_budget() -> None:
    import threading

    lock = threading.Lock()
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        with lock:
            calls += 1
        return httpx.Response(503)

    guard = budget(7)

    def run(_: int) -> None:
        with suppress(ProviderError, BudgetExceededError):
            guard.call(
                lambda: chat_completion(
                    "prompt", settings=routed(), transport=httpx.MockTransport(respond)
                )
            )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, range(20)))
    assert guard.used == calls == 7


def test_snapshot_and_legacy_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.store import db

    previous = str(db._engine.url) if db._engine is not None else None
    init_db(f"sqlite:///{tmp_path / 'routing.db'}")
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_MODEL", "deployment-model")
    monkeypatch.setenv("MYTHOSCIRCLE_GEMINI_API_KEY", "key")
    try:
        assert resolve_job_settings().model == "deployment-model"
        config = defaults().model_copy(update={"backup": "gemini"})
        save(config)
        snapshot = resolve_job_settings()
        save(config.model_copy(update={"primary": "gemini", "backup": "local"}))
        assert snapshot.model == "deployment-model" and snapshot.routing.backup.provider == "gemini"
        assert resolve_job_settings().provider == "gemini"
    finally:
        if previous:
            init_db(previous)


def test_worker_snapshots_each_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline import worker
    from app.store import db

    previous = str(db._engine.url) if db._engine is not None else None
    init_db(f"sqlite:///{tmp_path / 'worker-routing.db'}")
    monkeypatch.setenv("MYTHOSCIRCLE_GEMINI_API_KEY", "key")
    monkeypatch.setenv("MYTHOSCIRCLE_JOURNAL_ENABLED", "0")
    config = defaults().model_copy(update={"backup": "gemini"})
    save(config)
    calls = []
    monkeypatch.setattr(worker, "claim_next_job", lambda: Job(id="job", kind="text"))

    def run(job: Job, provider: Any, settings: LLMSettings, *args: Any) -> None:
        calls.append(settings.provider)
        save(config.model_copy(update={"primary": "gemini", "backup": "local"}))
        # The saved change never mutates a running job's frozen snapshot.
        assert settings.provider == calls[-1]
        assert settings.routing.backup.provider != settings.provider

    monkeypatch.setattr(worker, "_run_job", run)
    try:
        worker.run_next_job()
        worker.run_next_job()
        assert calls == ["local", "gemini"]
        injected = LLMSettings(model="injected")
        monkeypatch.setattr(
            worker, "_run_job", lambda job, provider, settings, *args: calls.append(settings)
        )
        worker.run_next_job(settings=injected)
        assert calls[-1] is injected
    finally:
        if previous:
            init_db(previous)


def test_fallback_keeps_actual_constraints_even_when_equal_to_primary() -> None:
    settings = routed()
    fallback = settings.routing.fallback_settings(settings)
    assert fallback.max_tokens == 100
    assert fallback.provider == "gemini" and fallback.model == "gemini-3.8-flash"


def test_saved_profile_from_before_openrouter_has_safe_default() -> None:
    old = defaults().model_dump(exclude={"openrouter"})
    loaded = LLMRoutingConfig.model_validate(old)
    assert loaded.openrouter.endpoint == OPENROUTER_ENDPOINT
    assert loaded.openrouter.model == "openrouter/free"


def test_openrouter_backup_uses_its_own_key_and_fixed_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.store import db

    previous = str(db._engine.url) if db._engine is not None else None
    init_db(f"sqlite:///{tmp_path / 'openrouter-routing.db'}")
    monkeypatch.setenv("MYTHOSCIRCLE_GEMINI_API_KEY", "gemini-sentinel")
    monkeypatch.setenv("MYTHOSCIRCLE_OPENROUTER_API_KEY", "router-sentinel")
    config = defaults().model_copy(update={"primary": "gemini", "backup": "openrouter"})
    save(config)
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        if len(requests) == 1:
            assert request.headers["authorization"] == "Bearer gemini-sentinel"
            return httpx.Response(429)
        assert str(request.url) == OPENROUTER_ENDPOINT + "chat/completions"
        assert request.headers["authorization"] == "Bearer router-sentinel"
        assert body["model"] == "openrouter/free"
        assert body["max_tokens"] == 517
        assert "chat_template_kwargs" not in body
        assert "reasoning_effort" not in body
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    try:
        settings = replace(resolve_job_settings(), max_tokens=517, enable_thinking=True)
        assert (
            budget().call(
                lambda: chat_completion(
                    "prompt", settings=settings, transport=httpx.MockTransport(respond)
                )
            )
            == "OK"
        )
        assert len(requests) == 2
    finally:
        if previous:
            init_db(previous)


@pytest.mark.parametrize("kind", ["image", "video", "add_character", "text"])
def test_missing_saved_gemini_key_only_blocks_text(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.media import service
    from app.pipeline import direct, worker
    from app.store import db

    previous = str(db._engine.url) if db._engine is not None else None
    init_db(f"sqlite:///{tmp_path / 'missing-key.db'}")
    monkeypatch.setenv("MYTHOSCIRCLE_GEMINI_API_KEY", "temporary-key")
    monkeypatch.setenv("MYTHOSCIRCLE_JOURNAL_ENABLED", "0")
    save(defaults().model_copy(update={"primary": "gemini"}))
    monkeypatch.delenv("MYTHOSCIRCLE_GEMINI_API_KEY")
    dispatched = []
    failed = []
    monkeypatch.setattr(worker, "claim_next_job", lambda: Job(id="job", kind=kind))
    monkeypatch.setattr(worker, "configured_image_backend", lambda: "openai")
    monkeypatch.setattr(worker, "configured_video_backend", lambda: "openai")
    monkeypatch.setattr(worker, "fail_job", lambda job_id, error: failed.append(error))
    monkeypatch.setattr(service, "run_portrait", lambda *args, **kwargs: dispatched.append("image"))
    monkeypatch.setattr(service, "run_video", lambda *args, **kwargs: dispatched.append("video"))
    monkeypatch.setattr(
        direct, "run_add_character", lambda *args: dispatched.append("add_character")
    )
    try:
        assert worker.run_next_job() == "job"
        if kind == "text":
            assert dispatched == [] and "MYTHOSCIRCLE_GEMINI_API_KEY" in failed[0]
        else:
            assert dispatched == [kind] and failed == []
        injected = LLMSettings(model="injected")
        seen = []
        monkeypatch.setattr(
            worker, "_run_job", lambda job, provider, settings, *args: seen.append(settings)
        )
        worker.run_next_job(settings=injected)
        assert seen == [injected]
    finally:
        if previous:
            init_db(previous)


@pytest.mark.parametrize("primary", ["local", "gemini"])
def test_saved_routing_authentication_and_payload_in_both_directions(
    primary: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.store import db

    previous = str(db._engine.url) if db._engine is not None else None
    init_db(f"sqlite:///{tmp_path / 'credentials.db'}")
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_API_KEY", "distinct-local-sentinel")
    monkeypatch.setenv("MYTHOSCIRCLE_GEMINI_API_KEY", "distinct-gemini-sentinel")
    config = defaults()
    config = config.model_copy(
        update={
            "primary": primary,
            "backup": "gemini" if primary == "local" else "local",
            "local": config.local.model_copy(update={"enable_thinking": True}),
            "gemini": config.gemini.model_copy(update={"enable_thinking": True}),
        }
    )
    save(config)
    schema = {"type": "json_schema", "json_schema": {"name": "test", "schema": {"type": "object"}}}
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        is_gemini = request.url.host == "generativelanguage.googleapis.com"
        expected_key = "distinct-gemini-sentinel" if is_gemini else "distinct-local-sentinel"
        assert request.headers["authorization"] == f"Bearer {expected_key}"
        assert body["response_format"] == schema
        assert body["max_tokens"] == 517
        if is_gemini:
            assert body["reasoning_effort"] == "low"
            assert "chat_template_kwargs" not in body
            assert "seed" not in body
        else:
            assert body["chat_template_kwargs"] == {"enable_thinking": True}
            assert "reasoning_effort" not in body
            assert body["seed"] == 20260912
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    try:
        settings = replace(
            resolve_job_settings(), max_tokens=517, response_format=schema, seed=20260912
        )
        assert (
            budget().call(
                lambda: chat_completion(
                    "prompt", settings=settings, transport=httpx.MockTransport(respond)
                )
            )
            == "OK"
        )
        assert len(requests) == 2
        assert (requests[0].url.host == "generativelanguage.googleapis.com") == (
            primary == "gemini"
        )
        assert requests[0].url.host != requests[1].url.host
    finally:
        if previous:
            init_db(previous)
