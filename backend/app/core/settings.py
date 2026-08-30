"""Environment-backed settings (AD-22).

Queue limits (spec-1.3) and the inference adapter's endpoint/model/key/
timeout (spec-1.4) come from environment variables with fixed defaults
mirroring ``deploy/config.toml``; ``config.toml`` consumption is deferred
to Story 1.7, so these are env-only here.
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


#: Environment variables for the inference adapter (spec-1.4).
LLM_ENDPOINT = "MYTHOSCIRCLE_LLM_ENDPOINT"
LLM_MODEL = "MYTHOSCIRCLE_LLM_MODEL"
LLM_API_KEY = "MYTHOSCIRCLE_LLM_API_KEY"
LLM_TIMEOUT = "MYTHOSCIRCLE_LLM_TIMEOUT"

#: Defaults mirroring ``deploy/config.toml`` ``[llm]`` (spec-1.4).
DEFAULT_LLM_ENDPOINT = "http://127.0.0.1:8080/v1"
DEFAULT_LLM_MODEL = "mythos-14b-q5"
DEFAULT_LLM_TIMEOUT = 120


@dataclass(frozen=True)
class LLMSettings:
    """The OpenAI-compatible endpoint the adapter talks to (AR9/AD-14)."""

    endpoint: str = DEFAULT_LLM_ENDPOINT
    model: str = DEFAULT_LLM_MODEL
    api_key: str | None = None
    timeout: float = DEFAULT_LLM_TIMEOUT


def llm_settings() -> LLMSettings:
    """Read the inference adapter's settings from the environment.

    Swapping engines (llama-server → LM Studio → OpenRouter) is a config
    change, never a code change (AR9, NFR8). The API key, when set, is an
    environment variable — never a config file value (AD-22).
    """
    return LLMSettings(
        endpoint=os.environ.get(LLM_ENDPOINT, DEFAULT_LLM_ENDPOINT).strip() or DEFAULT_LLM_ENDPOINT,
        model=os.environ.get(LLM_MODEL, DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL,
        api_key=os.environ.get(LLM_API_KEY) or None,
        timeout=_env_positive_float(LLM_TIMEOUT, DEFAULT_LLM_TIMEOUT),
    )


def _env_positive_float(name: str, default: float) -> float:
    """Parse a positive float env var; malformed values fail loudly.

    A typo'd or zero/negative timeout would either crash obscurely inside
    httpx at request time or disable the timeout entirely — fail at
    start, exactly like the queue settings (review round 1).
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value
