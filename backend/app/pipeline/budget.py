"""Per-job provider-call budget guards (AR21).

Every provider call — the text runner, the build-in runner's two waves,
the generate runner's repair pass — goes through ``CallBudget``, which
refuses BEFORE any HTTP request once the per-job counter reaches
``job.max_llm_calls`` (the budget is the ceiling; malformed output is
never retried inside it). ``CallBudget`` is thread-safe and records
per-label call telemetry (improvement plan 0.4) — the build-in runner
fans chunk calls across worker threads. The portrait runner (spec-4.1)
uses the twin ``MediaCallBudget`` keyed on ``job.max_media_calls`` — the
media budget is a per-JOB ceiling like the LLM one, enforced by the same
``BudgetExceededError`` (its runner is single-threaded, so it needs
neither the lock nor the telemetry). Exceeding either budget raises
``BudgetExceededError`` and fails the job.
"""

import threading
import time
from collections.abc import Callable
from typing import Any

from app.store import models


class BudgetExceededError(Exception):
    """The job's per-call budget would be exceeded (AR21) — internal guard."""

    def __init__(self, budget: int, *, scope: str = "llm") -> None:
        super().__init__(f"job would exceed its {scope}-call budget ({budget})")
        self.budget = budget


class CallBudget:
    """Per-job LLM-call budget guard (AR21): refuses before any HTTP request.

    Thread-safe (improvement plan 0.4): the build-in runner fans chunk
    calls across worker threads, so the reserve is atomic — the ceiling
    check AND the increment happen under one lock and a race can never
    push the job past ``job.max_llm_calls``.

    Reserve-then-call is the parallel-safety rule: a provider call that
    RAISES still consumed budget, because the count bounds requests
    ISSUED, not answers received — a failed call hit the server exactly
    like a successful one, and un-counting it would let concurrent
    retries stampede past the AR21 ceiling.

    Every call carries a ``label`` naming its call site; ``snapshot()``
    reports the per-label call counts and accumulated provider wall time
    for the job's telemetry record.
    """

    def __init__(self, job: models.Job) -> None:
        self._budget = job.max_llm_calls
        self._used = 0
        self._lock = threading.Lock()
        self._calls_by_label: dict[str, int] = {}
        self._seconds_by_label: dict[str, float] = {}

    @property
    def used(self) -> int:
        """Calls reserved against the ceiling so far (successes AND failures)."""
        with self._lock:
            return self._used

    def call(self, provider_call: Callable[[], str], *, label: str = "call") -> str:
        """Reserve one call, then run ``provider_call`` and return its text.

        ``BudgetExceededError`` is raised BEFORE the provider is invoked
        once the ceiling is reached (AR21) — a refused call consumes
        nothing and is absent from the telemetry. The invocation's wall
        time is recorded under ``label`` even when it raises (the
        reserve already happened — the class docstring's rule).
        """
        with self._lock:
            if self._used >= self._budget:
                raise BudgetExceededError(self._budget)
            self._used += 1
        started = time.monotonic()
        try:
            return provider_call()
        finally:
            elapsed = time.monotonic() - started
            with self._lock:
                self._calls_by_label[label] = self._calls_by_label.get(label, 0) + 1
                self._seconds_by_label[label] = self._seconds_by_label.get(label, 0.0) + elapsed

    def snapshot(self) -> dict[str, Any]:
        """Telemetry copy: total reserves plus per-label counts and wall time.

        Shape: ``{"total": int, "by_label": {label: {"calls": int,
        "seconds": float}}}`` — ``seconds`` is the accumulated
        ``time.monotonic`` wall time of that label's provider calls,
        rounded to 2 places.
        """
        with self._lock:
            return {
                "total": self._used,
                "by_label": {
                    label: {
                        "calls": calls,
                        "seconds": round(self._seconds_by_label[label], 2),
                    }
                    for label, calls in self._calls_by_label.items()
                },
            }


class MediaCallBudget:
    """Per-job media-call budget guard (AR21): the portrait runner's twin
    of ``CallBudget``, keyed on ``job.max_media_calls`` (spec-4.1)."""

    def __init__(self, job: models.Job) -> None:
        self._budget = job.max_media_calls
        self._used = 0

    def call(self, provider_call: Callable[[], bytes]) -> bytes:
        if self._used >= self._budget:
            raise BudgetExceededError(self._budget, scope="media")
        data = provider_call()
        self._used += 1
        return data
