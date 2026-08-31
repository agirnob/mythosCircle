"""Per-job LLM-call budget guard (AR21).

Every provider call — the text runner and the build-in runner's two
waves — goes through ``CallBudget``, which refuses BEFORE any HTTP
request once the per-job counter reaches ``job.max_llm_calls`` (the
budget is the ceiling; malformed output is never retried inside it).
Exceeding the budget raises ``BudgetExceededError`` and fails the job.
"""

from collections.abc import Callable

from app.store import models


class BudgetExceededError(Exception):
    """The job's per-call budget would be exceeded (AR21) — internal guard."""

    def __init__(self, budget: int) -> None:
        super().__init__(f"job would exceed its max_llm_calls budget ({budget})")
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
