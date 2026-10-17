"""Atomic singleton persistence for non-secret administrator LLM settings."""

from app.core.llm_routing import (
    LLMRoutingConfig,
    LLMSettingsView,
    defaults,
    key_configured,
    require_key,
)
from app.store.db import session_scope
from app.store.models import AdminLLMSettings


def load() -> LLMRoutingConfig | None:
    with session_scope() as session:
        row = session.get(AdminLLMSettings, 1)
        return LLMRoutingConfig.model_validate(row.settings) if row else None


def view() -> LLMSettingsView:
    saved = load()
    return LLMSettingsView(
        **(saved or defaults()).model_dump(),
        saved=saved is not None,
        local_key_configured=key_configured("local"),
        gemini_key_configured=key_configured("gemini"),
        openrouter_key_configured=key_configured("openrouter"),
    )


def save(config: LLMRoutingConfig) -> LLMSettingsView:
    require_key(config.primary)
    if config.backup:
        require_key(config.backup)
    with session_scope() as session:
        row = session.get(AdminLLMSettings, 1)
        if row is None:
            session.add(AdminLLMSettings(id=1, settings=config.model_dump()))
        else:
            row.settings = config.model_dump()
    return LLMSettingsView(
        **config.model_dump(),
        saved=True,
        local_key_configured=key_configured("local"),
        gemini_key_configured=key_configured("gemini"),
        openrouter_key_configured=key_configured("openrouter"),
    )
