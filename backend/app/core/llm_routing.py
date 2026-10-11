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
ProfileName = Literal["local", "gemini"]


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


class LLMRoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    primary: ProfileName = "local"
    backup: ProfileName | None = None
    local: LLMProfile
    gemini: LLMProfile

    @model_validator(mode="after")
    def validate_routing(self) -> "LLMRoutingConfig":
        if self.backup == self.primary:
            raise ValueError("Primary and backup must be different providers.")
        if self.gemini.endpoint != GEMINI_ENDPOINT:
            raise ValueError("Gemini uses Google's fixed HTTPS endpoint.")
        return self


class LLMSettingsView(LLMRoutingConfig):
    saved: bool
    local_key_configured: bool
    gemini_key_configured: bool


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
        local=LLMProfile(**{name: getattr(local, name) for name in LLMProfile.model_fields}),
        gemini=LLMProfile(
            endpoint=GEMINI_ENDPOINT, model="gemini-3.8-flash", timeout=120, max_tokens=8192
        ),
    )


def key_configured(provider: ProfileName) -> bool:
    return bool(os.environ.get(GEMINI_API_KEY if provider == "gemini" else LLM_API_KEY, "").strip())


def require_key(provider: ProfileName) -> None:
    if provider == "gemini" and not key_configured(provider):
        raise ValueError("Configure MYTHOSCIRCLE_GEMINI_API_KEY in the API environment first.")


def resolve_profile(provider: ProfileName, profile: LLMProfile) -> LLMSettings:
    require_key(provider)
    if provider == "gemini" and profile.endpoint != GEMINI_ENDPOINT:
        raise ValueError("Gemini uses Google's fixed HTTPS endpoint.")
    return LLMSettings(
        **profile.model_dump(),
        api_key=os.environ.get(GEMINI_API_KEY if provider == "gemini" else LLM_API_KEY) or None,
        provider=provider,
        reasoning_effort="low" if provider == "gemini" else None,
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
                "reasoning_effort",
            )
        }
        return replace(self.backup, response_format=actual.response_format, **changes)


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
