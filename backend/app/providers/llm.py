"""OpenAI-compatible chat-completions provider (AR9, AD-14).

All generation goes through OpenAI-compatible HTTP adapters — the
pipeline talks HTTP only, never a server's native bindings. Swapping
engines (llama-server, LM Studio, Ollama, OpenRouter) is a config change
(``MYTHOSCIRCLE_LLM_*`` env), never a code change.

Leaf of the dependency graph: providers depend on nothing else in
``app/`` except settings; only ``pipeline/`` and ``media/`` depend on
providers. No vendor SDK — httpx keeps the OpenAI-compatible contract
literal.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx

from app.core.journal import record
from app.core.settings import LLMSettings

#: A provider call: one user prompt in, assistant text out. ``...``
#: accepts the keyword-only ``transport`` kwarg injected by tests.
ChatCompletion = Callable[..., str]

#: Relative path appended under the endpoint base — httpx joins a
#: relative path under ``base_url`` (``http://host/v1`` +
#: ``chat/completions`` -> ``http://host/v1/chat/completions``). A leading
#: slash would replace the base path and drop the ``/v1`` mount.
_CHAT_COMPLETIONS_PATH = "chat/completions"

#: System preamble telling the model it is writing for a TTRPG DM.
_SYSTEM_PROMPT = (
    "You are a world-building assistant for a TTRPG Game Master. "
    "Write concise, evocative, game-ready material. No preamble."
)


class ProviderError(Exception):
    """A provider call failed — the job fails, never retried (spec-1.4).

    ``kind`` is ``"connection"`` (unreachable/timeout), ``"http"``
    (non-2xx response), ``"timeout"`` (poll exhaustion — the
    ComfyUI provider's only caller, spec-4.4), ``"truncated"`` (the
    model hit the generation ceiling mid-answer, 2026-09-10), or
    ``"empty"`` (a healthy 200 whose assistant content is blank — the
    reasoning channel consumed the whole budget); ``status_code`` is set
    for http errors so the error message can surface the server's
    status.
    """

    def __init__(self, kind: str, *, status_code: int | None = None) -> None:
        self.kind = kind
        self.status_code = status_code
        if kind == "http":
            message = f"provider returned HTTP {status_code}"
        elif kind == "timeout":
            message = "provider timed out waiting for generation"
        elif kind == "truncated":
            message = "provider stopped at the max_tokens ceiling (output is incomplete)"
        elif kind == "empty":
            message = (
                "model returned no content — the reasoning channel may have "
                "consumed the whole completion budget"
            )
        else:
            message = "provider connection error"
        super().__init__(message)


class _CallOutcome:
    """One provider attempt's raw result — what the journal transcribes.

    ``error`` is the failure kind (connection/http/truncated) or None on
    success; ``content``/``finish_reason``/``usage`` carry whatever the
    server actually returned even on a failed attempt, so a truncated
    fragment or an HTTP error page is checkable, not swallowed."""

    __slots__ = ("content", "finish_reason", "error", "status_code", "usage")

    def __init__(
        self,
        content: str | None,
        finish_reason: str | None,
        error: str | None,
        status_code: int | None,
        usage: dict[str, Any] | None,
    ) -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.error = error
        self.status_code = status_code
        self.usage = usage


def _complete(
    user_prompt: str,
    *,
    settings: LLMSettings,
    transport: httpx.BaseTransport | None,
) -> _CallOutcome:
    """The raw OpenAI-compatible exchange, mapped to a ``_CallOutcome``
    (never raises): a malformed URL, transport failure, non-2xx, unparsable
    body, cut-off answer, and empty content are outcome ``error`` kinds —
    the caller decides the raise, so the journal sees every attempt's
    transcript exactly once."""
    try:
        client = httpx.Client(
            base_url=settings.endpoint,
            timeout=settings.timeout,
            transport=transport,
        )
    except (httpx.InvalidURL, ValueError):
        # An unparseable configured endpoint (missing scheme, garbage) is a
        # connection-class failure, never a raw exception escaping.
        return _CallOutcome(None, None, "connection", None, None)
    headers = (
        {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key is not None else {}
    )
    body: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    if settings.enable_thinking is not None:
        # A reasoning model with thinking on will spend its whole budget
        # reasoning and never emit content (measured 2026-09-10: 8000 tokens,
        # 26,580 chars, zero content). Sent only when configured — ``None``
        # keeps the body free of a field some backends reject outright.
        body["chat_template_kwargs"] = {"enable_thinking": settings.enable_thinking}
    if settings.temperature is not None:
        # Sampling controls (improvement plan G) ride the body ONLY when
        # configured — ``None`` omits the field so the provider's own
        # default rules and a backend rejecting unknown request fields
        # keeps working (the enable_thinking tri-state's rationale).
        # 0.0/0 are meaningful values, never falsy omissions.
        body["temperature"] = settings.temperature
    if settings.top_p is not None:
        body["top_p"] = settings.top_p
    if settings.seed is not None:
        body["seed"] = settings.seed
    if settings.response_format is not None:
        # Opt-in per-call schema (spec: JSON-schema generation) — carried
        # verbatim. Absent by default so default-settings bodies stay
        # byte-identical ({model, max_tokens, messages} + thinking only).
        body["response_format"] = settings.response_format
    with client:
        try:
            response = client.post(_CHAT_COMPLETIONS_PATH, json=body, headers=headers)
        except (httpx.RequestError, httpx.InvalidURL, ValueError):
            # Request-time URL join failures (missing scheme, garbage
            # endpoint) are connection-class failures, never raw escapes.
            return _CallOutcome(None, None, "connection", None, None)
    if response.status_code != 200:
        return _CallOutcome(None, None, "http", response.status_code, None)
    try:
        payload = response.json()
        choice = payload["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        # A 200 with a non-JSON body (HTML error page, empty) or a
        # malformed payload is still a provider error, never a raw
        # JSONDecodeError escaping the provider's contract.
        return _CallOutcome(None, None, "http", response.status_code, None)
    usage = payload.get("usage") if isinstance(payload, dict) else None
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        # The ceiling was reached: whatever came back is a fragment. Say so
        # here — downstream this would surface as a baffling "not valid JSON
        # (Unterminated string)" in the middle of a JSON document.
        return _CallOutcome(
            content if isinstance(content, str) else None,
            finish_reason,
            "truncated",
            response.status_code,
            usage,
        )
    if not isinstance(content, str) or not content.strip():
        # A healthy 200 with blank content — a reasoning model that
        # burned its whole budget in thinking and never answered. Its
        # own error kind, never a misleading HTTP/connection label.
        return _CallOutcome(None, finish_reason, "empty", response.status_code, usage)
    return _CallOutcome(content, finish_reason, None, None, usage)


def chat_completion(
    user_prompt: str,
    *,
    settings: LLMSettings,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Call the configured endpoint and return the assistant's text.

    POSTs ``{model, max_tokens, messages:[{system}, {user}]}`` to
    ``{endpoint}/chat/completions``, plus ``chat_template_kwargs`` when a
    reasoning mode is configured, ``temperature``/``top_p``/``seed`` when
    the matching sampling control is configured (improvement plan G —
    each tri-state, omitted when ``None``), and ``response_format`` when
    ``settings.response_format`` carries a per-call schema (all absent by
    default, so default-settings bodies stay byte-identical). Bare ``httpx``
    transport failures
    (DNS, refused, timeout) -> ``ProviderError("connection")``; non-2xx
    -> ``ProviderError("http", status_code=...)``; an answer cut off at
    the generation ceiling -> ``ProviderError("truncated")``, which is a
    named failure rather than a downstream "not valid JSON". The transport
    is injectable for deterministic tests (``httpx.MockTransport``).

    Every attempt — success AND failure — is transcribed to the per-job
    LLM call journal (app.core.journal, owner note 7) BEFORE the raise:
    one record per physical call, written where the prompt, response,
    finish reason, and usage all exist.
    """
    started = time.monotonic()
    outcome = _complete(user_prompt, settings=settings, transport=transport)
    record(
        prompt=user_prompt,
        settings=settings,
        response=outcome.content,
        finish_reason=outcome.finish_reason,
        error=outcome.error,
        usage=outcome.usage,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if outcome.error is not None:
        raise ProviderError(outcome.error, status_code=outcome.status_code)
    assert outcome.content is not None
    return outcome.content
