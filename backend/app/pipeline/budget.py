"""Per-job provider-call budget guards (AR21).

Every provider call — the text runner, the build-in runner's two waves,
the generate runner's repair pass — goes through ``CallBudget``, which
refuses BEFORE any HTTP request once the per-job counter reaches
``job.max_llm_calls`` (the budget is the ceiling; malformed output is
never retried inside it). The portrait runner (spec-4.1) uses the twin
``MediaCallBudget`` keyed on ``job.max_media_calls`` — the media budget
is a per-JOB ceiling like the LLM one, enforced by the same
``BudgetExceededError``. Exceeding either budget raises
``BudgetExceededError`` and fails the job.
"""

from collections.abc import Callable

from app.store import models


class BudgetExceededError(Exception):
    """The job's per-call budget would be exceeded (AR21) — internal guard."""

    def __init__(self, budget: int, *, scope: str = "llm") -> None:
        super().__init__(f"job would exceed its {scope}-call budget ({budget})")
        self.budget = budget


class CallBudget:
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
