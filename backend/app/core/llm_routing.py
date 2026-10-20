"""Non-secret routing contracts and execution context; no store dependencies."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.settings import LLM_API_KEY, LLMSettings, llm_settings

GEMINI_API_KEY = "MYTHOSCIRCLE_GEMINI_API_KEY"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENROUTER_API_KEY = "MYTHOSCIRCLE_OPENROUTER_API_KEY"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/"
OPENROUTER_GEMMA_FREE = "google/gemma-4-26b-a4b-it:free"
OPENROUTER_DEEPSEEK_FAST = "deepseek/deepseek-v4-flash-0731:nitro"
ProfileName = Literal["local", "gemini", "openrouter"]


def default_openrouter_profile() -> "LLMProfile":
    return LLMProfile(
        endpoint=OPENROUTER_ENDPOINT,
        model="nvidia/nemotron-3.5-super-vl:free",
        timeout=600,
        max_tokens=50000,
        fallback_models=[OPENROUTER_GEMMA_FREE, OPENROUTER_DEEPSEEK_FAST],
    )


class LLMProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, strict=True)
    endpoint: str
    model: str = Field(min_length=1, max_length=200)
    timeout: float = Field(gt=0, le=3600)
    max_tokens: int = Field(ge=1, le=131072)
    enable_thinking: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    seed: int | None = Field(default=None, ge=0, le=2147483647)
    fallback_models: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Use a valid HTTP(S) endpoint.") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or any(char.isspace() for char in value)
            or port == 0
        ):
            raise ValueError("Use HTTP(S) without credentials, query parameters or fragments.")
        return value

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a model name.")
        return value.strip()

    @field_validator("fallback_models")
    @classmethod
    def valid_fallback_models(cls, value: list[str]) -> list[str]:
        if any(not model.strip() or len(model) > 200 or model != model.strip() for model in value):
            raise ValueError("Enter one valid fallback model ID per line.")
        if len(value) != len(set(value)):
            raise ValueError("Fallback model IDs must be distinct.")
        return value


class LLMRoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    primary: ProfileName = "local"
    backup: ProfileName | None = None
    local: LLMProfile
    gemini: LLMProfile
    # Older saved rows predate OpenRouter; loading them supplies this profile.
    openrouter: LLMProfile = Field(default_factory=default_openrouter_profile)

    @model_validator(mode="after")
    def validate_routing(self) -> "LLMRoutingConfig":
        if self.backup == self.primary:
            raise ValueError("Primary and backup must be different providers.")
        if self.gemini.endpoint != GEMINI_ENDPOINT:
            raise ValueError("Gemini uses Google's fixed HTTPS endpoint.")
        if self.openrouter.endpoint != OPENROUTER_ENDPOINT:
            raise ValueError("OpenRouter uses its fixed HTTPS endpoint.")
        return self


class LLMSettingsView(LLMRoutingConfig):
    openrouter: LLMProfile
    saved: bool
    local_key_configured: bool
    gemini_key_configured: bool
    openrouter_key_configured: bool


class LLMTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: ProfileName
    profile: LLMProfile


class LLMTestResult(BaseModel):
    success: bool
    message: str


def defaults() -> LLMRoutingConfig:
    local = llm_settings()
    return LLMRoutingConfig(
        local=LLMProfile(
            **{
                name: getattr(local, name)
                for name in LLMProfile.model_fields
                if name != "fallback_models"
            },
            fallback_models=list(local.fallback_models),
        ),
        gemini=LLMProfile(
            endpoint=GEMINI_ENDPOINT, model="gemini-3.8-flash", timeout=120, max_tokens=8192
        ),
        openrouter=default_openrouter_profile(),
    )


def key_configured(provider: ProfileName) -> bool:
    name = {
        "local": LLM_API_KEY,
        "gemini": GEMINI_API_KEY,
        "openrouter": OPENROUTER_API_KEY,
    }[provider]
    return bool(os.environ.get(name, "").strip())


def require_key(provider: ProfileName) -> None:
    if provider in {"gemini", "openrouter"} and not key_configured(provider):
        name = GEMINI_API_KEY if provider == "gemini" else OPENROUTER_API_KEY
        raise ValueError(f"Configure {name} in the API environment first.")


def resolve_profile(provider: ProfileName, profile: LLMProfile) -> LLMSettings:
    require_key(provider)
    if provider == "gemini" and profile.endpoint != GEMINI_ENDPOINT:
        raise ValueError("Gemini uses Google's fixed HTTPS endpoint.")
    if provider == "openrouter" and profile.endpoint != OPENROUTER_ENDPOINT:
        raise ValueError("OpenRouter uses its fixed HTTPS endpoint.")
    key_name = {
        "local": LLM_API_KEY,
        "gemini": GEMINI_API_KEY,
        "openrouter": OPENROUTER_API_KEY,
    }[provider]
    return LLMSettings(
        **profile.model_dump(exclude={"fallback_models"}),
        api_key=os.environ.get(key_name) or None,
        provider=provider,
        reasoning_effort="low" if provider == "gemini" else None,
        fallback_models=tuple(profile.fallback_models) if provider == "openrouter" else (),
    )


@dataclass(frozen=True)
class LLMRouting:
    primary: LLMSettings
    backup: LLMSettings | None

    def fallback_settings(self, actual: LLMSettings) -> LLMSettings:
        assert self.backup is not None
        # Carry the actual call constraints (including unchanged-value repair
        # overrides) while keeping the backup endpoint, model, key and timeout.
        changes = {
            field: getattr(actual, field)
            for field in (
                "max_tokens",
                "enable_thinking",
                "temperature",
                "top_p",
                "seed",
            )
        }
        return replace(
            self.backup,
            response_format=actual.response_format,
            image_data_url=actual.image_data_url,
            **changes,
        )


AdditionalAttempt = Callable[[Callable[[], str]], str]
_ADDITIONAL_ATTEMPT: ContextVar[AdditionalAttempt | None] = ContextVar(
    "llm_additional_attempt", default=None
)


@contextmanager
def additional_attempt_context(callback: AdditionalAttempt) -> Iterator[None]:
    token = _ADDITIONAL_ATTEMPT.set(callback)
    try:
        yield
    finally:
        _ADDITIONAL_ATTEMPT.reset(token)


def additional_attempt(call: Callable[[], str]) -> str:
    callback = _ADDITIONAL_ATTEMPT.get()
    if callback is None:
        raise RuntimeError("Backup generation requires a job call budget.")
    return callback(call)
