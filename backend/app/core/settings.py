"""Environment-backed queue settings (AD-22).

The per-campaign pending cap and the per-job call budgets come from
environment variables with fixed defaults (spec-1.3 Always list);
``deploy/config.toml`` consumption is deferred to Story 1.7, so the
queue limits are env-only here.
"""

import os
from dataclasses import dataclass

#: Environment variables overriding the queue limits.
MAX_PENDING_PER_CAMPAIGN = "MYTHOSCIRCLE_MAX_PENDING_PER_CAMPAIGN"
MAX_LLM_CALLS_PER_JOB = "MYTHOSCIRCLE_MAX_LLM_CALLS_PER_JOB"
MAX_MEDIA_CALLS_PER_JOB = "MYTHOSCIRCLE_MAX_MEDIA_CALLS_PER_JOB"

#: Defaults per the spec's Always list.
DEFAULT_MAX_PENDING_PER_CAMPAIGN = 10
DEFAULT_MAX_LLM_CALLS_PER_JOB = 64
DEFAULT_MAX_MEDIA_CALLS_PER_JOB = 8


@dataclass(frozen=True)
class QueueSettings:
    """Queue limits read from the environment (defaults when unset)."""

    max_pending_per_campaign: int = DEFAULT_MAX_PENDING_PER_CAMPAIGN
    max_llm_calls_per_job: int = DEFAULT_MAX_LLM_CALLS_PER_JOB
    max_media_calls_per_job: int = DEFAULT_MAX_MEDIA_CALLS_PER_JOB


def queue_settings() -> QueueSettings:
    """Read the queue limits from the environment with the spec defaults."""
    return QueueSettings(
        max_pending_per_campaign=_env_non_negative_int(
            MAX_PENDING_PER_CAMPAIGN, DEFAULT_MAX_PENDING_PER_CAMPAIGN
        ),
        max_llm_calls_per_job=_env_non_negative_int(
            MAX_LLM_CALLS_PER_JOB, DEFAULT_MAX_LLM_CALLS_PER_JOB
        ),
        max_media_calls_per_job=_env_non_negative_int(
            MAX_MEDIA_CALLS_PER_JOB, DEFAULT_MAX_MEDIA_CALLS_PER_JOB
        ),
    )


def _env_non_negative_int(name: str, default: int) -> int:
    """Parse a non-negative integer env var; malformed values fail loudly.

    A typo'd operator value silently defaulting would hide the mistake at
    the exact moment the queue cap stops being enforced.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value
