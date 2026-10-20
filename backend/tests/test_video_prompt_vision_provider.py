"""Vision is a per-call attachment: preserve it on retry, never journal bytes."""

import base64
import io
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.core.journal import journal_context
from app.core.llm_routing import LLMRouting
from app.core.settings import LLMSettings
from app.pipeline.budget import CallBudget
from app.providers.llm import chat_completion
from app.store.models import Job


def image_url() -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), "navy").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.parametrize("provider", ["local", "gemini", "openrouter"])
def test_real_image_bytes_and_direction_reach_provider_wire(provider: str) -> None:
    attachment = image_url()
    settings = LLMSettings(
        endpoint="https://example.test/v1/", provider=provider, image_data_url=attachment
    )
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "draft"}}]})

    assert (
        chat_completion(
            "Blink once; no music.", settings=settings, transport=httpx.MockTransport(respond)
        )
        == "draft"
    )
    assert requests[0]["messages"][-1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Blink once; no music."},
            {"type": "image_url", "image_url": {"url": attachment}},
        ],
    }
    assert attachment not in repr(settings)


def test_image_attachment_survives_budgeted_backup_without_journal_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attachment = image_url()
    primary = LLMSettings(endpoint="http://primary.test/v1/", model="primary-vision")
    backup = LLMSettings(endpoint="http://backup.test/v1/", model="backup-vision")
    settings = replace(primary, image_data_url=attachment, routing=LLMRouting(primary, backup))
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "draft"}}]})

    monkeypatch.setenv("MYTHOSCIRCLE_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("MYTHOSCIRCLE_JOURNAL_ENABLED", "1")
    budget = CallBudget(Job(max_llm_calls=2))
    with journal_context("image-prompt"):
        assert (
            budget.call(
                lambda: chat_completion(
                    "Blink once.", settings=settings, transport=httpx.MockTransport(respond)
                )
            )
            == "draft"
        )
    assert budget.used == 2
    assert [call["model"] for call in calls] == ["primary-vision", "backup-vision"]
    for call in calls:
        assert call["messages"][-1]["content"][1]["image_url"]["url"] == attachment
    journal = (tmp_path / "image-prompt.jsonl").read_text()
    entries = [json.loads(line) for line in journal.splitlines()]
    assert len(entries) == 2
    assert all(entry["prompt"] == "Blink once." for entry in entries)
    assert attachment not in journal
    assert attachment.split(",", 1)[1] not in journal


def test_text_only_calls_keep_original_request_shape() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    chat_completion(
        "Describe a tavern.", settings=LLMSettings(), transport=httpx.MockTransport(respond)
    )
    assert requests[0]["messages"][-1]["content"] == "Describe a tavern."
