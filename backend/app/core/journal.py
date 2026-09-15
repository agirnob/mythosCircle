"""The per-job LLM call journal (owner note 7, 2026-09-15).

Every provider attempt — prompt, response, settings, outcome — is
written to ``<journal-dir>/<job-id>.jsonl`` so a failed try is fully
checkable after the fact (the repair-session spec's scratch corpora
became the product's default, not a harness). Design rules:

* ONE record per physical provider call, written at the provider
  boundary (``providers.llm``) where the prompt, response, finish
  reason, and token usage all exist — never at the runner level, where
  retries and repairs would blur which attempt produced what.
* Files live BESIDE THE DATABASE (``<db-dir>/llm-journal`` by default)
  — a prompt journal embeds the whole retrieved world neighborhood with
  its secrets, so it inherits the DB's permissions and backup story,
  never /tmp or a web-served path. ``MYTHOSCIRCLE_JOURNAL_DIR``
  overrides; ``MYTHOSCIRCLE_JOURNAL_ENABLED=0`` turns it off.
* Retention is bounded: ``MYTHOSCIRCLE_JOURNAL_RETENTION_DAYS`` (30)
  prunes expired files once per job open — the journal is a debug
  surface, not an archive.
* The journal NEVER breaks a generation: every write failure logs and
  is swallowed (a full disk must not fail a job that already spent an
  LLM call).
* ``pipeline.budget.CallBudget.call`` carries the call-site label in a
  contextvar so a record names its attempt (``wave1_json_retry`` …)
  without threading the label through the provider signature.

Env names are journal-only; read directly here (the media_url_secret
pattern) — the pipeline never configures them through config.toml.
"""

import contextvars
import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import LLMSettings

JOURNAL_ENABLED_ENV = "MYTHOSCIRCLE_JOURNAL_ENABLED"
JOURNAL_DIR_ENV = "MYTHOSCIRCLE_JOURNAL_DIR"
JOURNAL_RETENTION_DAYS_ENV = "MYTHOSCIRCLE_JOURNAL_RETENTION_DAYS"
DEFAULT_RETENTION_DAYS = 30
_DB_ENV = "MYTHOSCIRCLE_DB"

_ACTIVE: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "llm_journal_file", default=None
)
_LABEL: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_journal_label", default=None
)
_WRITE_LOCK = threading.Lock()

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.environ.get(JOURNAL_ENABLED_ENV, "1") != "0"


def journal_dir() -> Path:
    """The journal root — env override, else beside the DB, else ./data.

    ``MYTHOSCIRCLE_DB`` is parsed as a plain sqlite URL (no store import:
    core is the leaf; the store depends on core, never the reverse)."""
    override = os.environ.get(JOURNAL_DIR_ENV)
    if override:
        return Path(override)
    db = os.environ.get(_DB_ENV, "")
    if db.startswith("sqlite:///"):
        return Path(db[len("sqlite:///") :]).parent / "llm-journal"
    return Path("data") / "llm-journal"


def _retention_days() -> int:
    try:
        return max(1, int(os.environ.get(JOURNAL_RETENTION_DAYS_ENV, "")))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def prune(directory: Path, *, retention_days: int | None = None) -> None:
    """Delete journal files older than the retention window; never raises."""
    days = retention_days if retention_days is not None else _retention_days()
    cutoff = time.time() - days * 86400
    try:
        for path in directory.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        logger.warning("llm journal prune failed under %s", directory, exc_info=True)


@contextmanager
def journal_context(job_id: str) -> Iterator[None]:
    """Point the journal at ``<dir>/<job-id>.jsonl`` for one job's run.

    The run's provider calls record through it; nothing is written for
    jobs whose providers never fire (a cancelled claim, a mocked
    provider in tests). Dir creation and pruning are best-effort — an
    unwritable journal must never wedge the worker."""
    if not _enabled():
        yield
        return
    directory = journal_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        prune(directory)
    except OSError:
        logger.warning("llm journal dir %s unavailable — journaling disabled", directory)
        yield
        return
    token = _ACTIVE.set(directory / f"{job_id}.jsonl")
    try:
        yield
    finally:
        _ACTIVE.reset(token)


@contextmanager
def label_context(label: str) -> Iterator[None]:
    """Carry the call-site label (``wave1_json_retry`` …) into ``record``.

    Set by ``CallBudget.call`` around each provider invocation — the
    attempt's paper trail without threading the label through the
    provider signature."""
    token = _LABEL.set(label)
    try:
        yield
    finally:
        _LABEL.reset(token)


def record(
    *,
    prompt: str,
    settings: LLMSettings,
    response: str | None,
    finish_reason: str | None,
    error: str | None,
    usage: dict[str, Any] | None,
    duration_ms: int,
) -> None:
    """Append one provider attempt's full transcript; never raises.

    A ``response=None`` with an ``error`` names the failure kind — a
    connection/http/truncated attempt is still a try worth checking."""
    path = _ACTIVE.get()
    if path is None or not _enabled():
        return
    entry: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "job_id": path.stem,
        "label": _LABEL.get(),
        "model": settings.model,
        "endpoint": settings.endpoint,
        "max_tokens": settings.max_tokens,
        "temperature": settings.temperature,
        "top_p": settings.top_p,
        "seed": settings.seed,
        "enable_thinking": settings.enable_thinking,
        "response_format": settings.response_format,
        "prompt": prompt,
        "response": response,
        "finish_reason": finish_reason,
        "error": error,
        "usage": usage,
        "duration_ms": duration_ms,
    }
    try:
        with _WRITE_LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # A journal must never fail the job that already spent the call.
        logger.warning("llm journal write failed for %s", path, exc_info=True)
