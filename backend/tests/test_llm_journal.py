"""The per-job LLM call journal (owner note 7, 2026-09-15): every provider
attempt transcribes to <journal-dir>/<job-id>.jsonl — prompt, response,
settings, outcome — so a failed try is checkable after the fact. These
tests pin the writer, the provider-boundary recording, retention, and
the off switch."""

import json
import os
import time

import httpx
import pytest

from app.core.journal import (
    JOURNAL_DIR_ENV,
    JOURNAL_ENABLED_ENV,
    journal_context,
    journal_dir,
    prune,
    record,
)
from app.core.settings import LLMSettings
from app.providers.llm import ProviderError, chat_completion

DEFAULT = LLMSettings()
JOB = "01J00000000000000000000000"


def _completion(content: str, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def _entries(journal_path) -> list[dict]:
    with journal_path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


@pytest.fixture()
def journal_dir_fixture(tmp_path, monkeypatch):
    """A writable journal root pointing at a tmp dir; restores env after."""
    target = tmp_path / "journal"
    monkeypatch.setenv(JOURNAL_DIR_ENV, str(target))
    monkeypatch.delenv(JOURNAL_ENABLED_ENV, raising=False)
    return target


def test_chat_completion_transcribes_full_transcript(journal_dir_fixture) -> None:
    """One record per successful attempt: prompt, response, settings,
    finish reason, and usage all land in the job's file."""
    with journal_context(JOB):
        text = chat_completion(
            "describe the bar",
            settings=LLMSettings(model="test-model", max_tokens=4096, temperature=0.0, seed=7),
            transport=httpx.MockTransport(lambda request: _completion("The Gilded Bar.")),
        )
    assert text == "The Gilded Bar."
    entries = _entries(journal_dir_fixture / f"{JOB}.jsonl")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["job_id"] == JOB
    assert entry["prompt"] == "describe the bar"
    assert entry["response"] == "The Gilded Bar."
    assert entry["finish_reason"] == "stop"
    assert entry["error"] is None
    assert entry["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
    assert entry["model"] == "test-model"
    assert entry["max_tokens"] == 4096
    assert entry["temperature"] == 0.0
    assert entry["seed"] == 7
    assert entry["label"] is None
    assert entry["duration_ms"] >= 0


def test_record_carries_the_call_site_label(journal_dir_fixture) -> None:
    """The budget label (``wave1_json_retry``) rides the record via its
    contextvar — the attempt's paper trail without provider plumbing."""
    from app.core.journal import label_context

    with journal_context(JOB), label_context("wave2_json_retry"):
        record(
            prompt="p",
            settings=DEFAULT,
            response="r",
            finish_reason="stop",
            error=None,
            usage=None,
            duration_ms=1,
        )
    entry = _entries(journal_dir_fixture / f"{JOB}.jsonl")[0]
    assert entry["label"] == "wave2_json_retry"


def test_truncated_attempt_is_recorded_with_its_fragment(journal_dir_fixture) -> None:
    """A ceiling cut-off raises truncation AND the journal keeps the
    fragment + finish_reason=length — the checkable evidence for the
    doubled-window retry decision."""
    with journal_context(JOB), pytest.raises(ProviderError) as excinfo:
        chat_completion(
            "long prompt",
            settings=DEFAULT,
            transport=httpx.MockTransport(lambda request: _completion("half a j", "length")),
        )
    assert excinfo.value.kind == "truncated"
    entry = _entries(journal_dir_fixture / f"{JOB}.jsonl")[0]
    assert entry["error"] == "truncated"
    assert entry["response"] == "half a j"
    assert entry["finish_reason"] == "length"


def test_connection_failure_is_recorded_without_response(journal_dir_fixture) -> None:
    """A transport failure is still a try: the journal entry names
    connection with no response — never a silently lost attempt."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with journal_context(JOB), pytest.raises(ProviderError) as excinfo:
        chat_completion("hi", settings=DEFAULT, transport=httpx.MockTransport(handler))
    assert excinfo.value.kind == "connection"
    entry = _entries(journal_dir_fixture / f"{JOB}.jsonl")[0]
    assert entry["error"] == "connection"
    assert entry["response"] is None


def test_no_active_journal_is_a_noop(tmp_path, monkeypatch) -> None:
    """Without ``journal_context`` (tests, direct calls) nothing is
    written and the directory is not even created."""
    monkeypatch.setenv(JOURNAL_DIR_ENV, str(tmp_path / "journal"))
    chat_completion(
        "hi", settings=DEFAULT, transport=httpx.MockTransport(lambda request: _completion("ok"))
    )
    assert not (tmp_path / "journal").exists()


def test_journal_disabled_writes_nothing(journal_dir_fixture, monkeypatch) -> None:
    monkeypatch.setenv(JOURNAL_ENABLED_ENV, "0")
    with journal_context(JOB):
        chat_completion(
            "hi", settings=DEFAULT, transport=httpx.MockTransport(lambda request: _completion("ok"))
        )
    assert not (journal_dir_fixture / f"{JOB}.jsonl").exists()


def test_prune_removes_expired_keeps_fresh(journal_dir_fixture, monkeypatch) -> None:
    monkeypatch.setenv("MYTHOSCIRCLE_JOURNAL_RETENTION_DAYS", "30")
    journal_dir_fixture.mkdir(parents=True, exist_ok=True)
    old = journal_dir_fixture / "OLD.jsonl"
    fresh = journal_dir_fixture / "NEW.jsonl"
    old.write_text("{}")
    fresh.write_text("{}")
    old_mtime = time.time() - 31 * 86400
    os.utime(old, (old_mtime, old_mtime))
    prune(journal_dir_fixture, retention_days=30)
    assert not old.exists()
    assert fresh.exists()


def test_journal_dir_defaults_beside_the_db(tmp_path, monkeypatch) -> None:
    """The default root hugs the DB file (same permissions/backup story —
    a prompt journal embeds the world's secrets): env override wins."""
    monkeypatch.setenv("MYTHOSCIRCLE_DB", f"sqlite:///{tmp_path}/data/mythos.db")
    monkeypatch.delenv(JOURNAL_DIR_ENV, raising=False)
    assert journal_dir() == tmp_path / "data" / "llm-journal"
