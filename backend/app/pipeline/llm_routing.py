"""Resolve a job's immutable routing snapshot at the pipeline boundary."""

from dataclasses import replace

from app.core.llm_routing import LLMRouting, resolve_profile
from app.core.settings import LLMSettings, llm_settings
from app.store.llm_settings import load


def resolve_job_settings() -> LLMSettings:
    config = load()
    if config is None:
        return llm_settings()
    primary = resolve_profile(config.primary, getattr(config, config.primary))
    backup = (
        resolve_profile(config.backup, getattr(config, config.backup)) if config.backup else None
    )
    return replace(primary, routing=LLMRouting(primary, backup))
