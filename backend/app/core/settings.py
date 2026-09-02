"""Environment + config settings (AD-22, spec-1.7).

Precedence is always env > ``deploy/config.toml`` > code default. The
deploy config is resolved once by ``app.core.config.runtime_config``;
these helpers read it first and apply env overrides on top.
"""

import os
from dataclasses import dataclass

from app.core import config as config_mod
from app.core.config import env_float, env_int, runtime_config

#: Environment variables overriding the queue limits. The pending-cap and
#: LLM keys live canonically in ``app.core.config`` (AD-22); these are the
#: settings-layer aliases for the same env vars.
MAX_PENDING_PER_CAMPAIGN = config_mod.MAX_PENDING_ENV
MAX_LLM_CALLS_PER_JOB = "MYTHOSCIRCLE_MAX_LLM_CALLS_PER_JOB"
MAX_MEDIA_CALLS_PER_JOB = "MYTHOSCIRCLE_MAX_MEDIA_CALLS_PER_JOB"

#: Defaults per the spec's Always list.
DEFAULT_MAX_PENDING_PER_CAMPAIGN = config_mod.DEFAULT_MAX_PENDING
DEFAULT_MAX_LLM_CALLS_PER_JOB = 64
DEFAULT_MAX_MEDIA_CALLS_PER_JOB = 8


@dataclass(frozen=True)
class QueueSettings:
    """Queue limits read from env + config (env > config > default)."""

    max_pending_per_campaign: int = DEFAULT_MAX_PENDING_PER_CAMPAIGN
    max_llm_calls_per_job: int = DEFAULT_MAX_LLM_CALLS_PER_JOB
    max_media_calls_per_job: int = DEFAULT_MAX_MEDIA_CALLS_PER_JOB


def queue_settings() -> QueueSettings:
    """Read the queue limits with env > config > default precedence."""
    return QueueSettings(
        max_pending_per_campaign=env_int(
            MAX_PENDING_PER_CAMPAIGN, runtime_config().queue_max_pending
        ),
        max_llm_calls_per_job=env_int(
            MAX_LLM_CALLS_PER_JOB, runtime_config().max_llm_calls_per_job
        ),
        max_media_calls_per_job=env_int(
            MAX_MEDIA_CALLS_PER_JOB, runtime_config().max_media_calls_per_job
        ),
    )


#: Environment variables for the inference adapter (spec-1.4; config in 1.7).
#: Endpoint/model/timeout keys and defaults are canonical in config.py.
LLM_ENDPOINT = config_mod.LLM_ENDPOINT_ENV
LLM_MODEL = config_mod.LLM_MODEL_ENV
LLM_API_KEY = "MYTHOSCIRCLE_LLM_API_KEY"
LLM_TIMEOUT = config_mod.LLM_TIMEOUT_ENV

#: Code defaults (spec-1.4; config.toml [llm] overrides in 1.7).
DEFAULT_LLM_ENDPOINT = config_mod.DEFAULT_LLM_ENDPOINT
DEFAULT_LLM_MODEL = config_mod.DEFAULT_LLM_MODEL
DEFAULT_LLM_TIMEOUT = config_mod.DEFAULT_LLM_TIMEOUT


@dataclass(frozen=True)
class LLMSettings:
    """The OpenAI-compatible endpoint the adapter talks to (AR9/AD-14)."""

    endpoint: str = DEFAULT_LLM_ENDPOINT
    model: str = DEFAULT_LLM_MODEL
    api_key: str | None = None
    timeout: float = DEFAULT_LLM_TIMEOUT


def llm_settings() -> LLMSettings:
    """Read the inference adapter's settings (env > config > default).

    The API key stays environment-only (AD-22) — never config.toml.
    """
    resolved = runtime_config()
    endpoint_env = os.environ.get(LLM_ENDPOINT)
    model_env = os.environ.get(LLM_MODEL)
    return LLMSettings(
        # A set-but-empty env value is treated as unset (falls through to
        # config) — precedence is env > config > default (spec-1.7).
        endpoint=(endpoint_env if endpoint_env else resolved.llm_endpoint).strip()
        or resolved.llm_endpoint,
        model=(model_env if model_env else resolved.llm_model).strip() or resolved.llm_model,
        api_key=os.environ.get(LLM_API_KEY) or None,
        timeout=env_float(LLM_TIMEOUT, resolved.llm_timeout),
    )


#: Environment variable for the session lifetime (spec-1.5).
SESSION_TTL_DAYS = "MYTHOSCIRCLE_SESSION_TTL_DAYS"
#: Default session lifetime in days (AR14/AR29).
DEFAULT_SESSION_TTL_DAYS = 30


def session_ttl_days() -> int:
    """The session lifetime in days; 0 disables expiry (never expire)."""
    return env_int(SESSION_TTL_DAYS, DEFAULT_SESSION_TTL_DAYS)


def configured_themes() -> list[str]:
    """The campaign theme list from config (code seed fallback, spec-1.7)."""
    return list(runtime_config().themes)


def configured_db_url() -> str | None:
    """The DB url from config; env override wins when set."""
    if config_mod.DB_ENV in os.environ:
        return os.environ[config_mod.DB_ENV]
    return runtime_config().db_url


def configured_log_file() -> str | None:
    """The JSON-lines log path from config; env override wins when set."""
    if config_mod.LOG_FILE_ENV in os.environ:
        return os.environ[config_mod.LOG_FILE_ENV]
    return runtime_config().log_file
