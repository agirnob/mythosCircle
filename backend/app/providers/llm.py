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

from collections.abc import Callable
from typing import Any

import httpx

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
    (non-2xx response), or ``"timeout"`` (poll exhaustion — the
    ComfyUI provider's only caller, spec-4.4); ``status_code`` is set
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
        else:
            message = "provider connection error"
        super().__init__(message)


def chat_completion(
    user_prompt: str,
    *,
    settings: LLMSettings,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Call the configured endpoint and return the assistant's text.

    POSTs ``{model, messages:[{system}, {user}]}`` to
    ``{endpoint}/chat/completions``. Bare ``httpx`` transport failures
    (DNS, refused, timeout) -> ``ProviderError("connection")``; non-2xx
    -> ``ProviderError("http", status_code=...)``. The transport is
    injectable for deterministic tests (``httpx.MockTransport``).
    """
    try:
        client = httpx.Client(
            base_url=settings.endpoint,
            timeout=settings.timeout,
            transport=transport,
        )
    except (httpx.InvalidURL, ValueError) as exc:
        # An unparseable configured endpoint (missing scheme, garbage) is a
        # connection-class failure, never a raw exception escaping.
        raise ProviderError("connection") from exc
    headers = (
        {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key is not None else {}
    )
    body: dict[str, Any] = {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    with client:
        try:
            response = client.post(_CHAT_COMPLETIONS_PATH, json=body, headers=headers)
        except (httpx.RequestError, httpx.InvalidURL, ValueError) as exc:
            # Request-time URL join failures (missing scheme, garbage
            # endpoint) are connection-class failures, never raw escapes.
            raise ProviderError("connection") from exc
    if response.status_code != 200:
        raise ProviderError("http", status_code=response.status_code)
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        # A 200 with a non-JSON body (HTML error page, empty) or a
        # malformed payload is still a provider error, never a raw
        # JSONDecodeError escaping the provider's contract.
        raise ProviderError("http", status_code=response.status_code) from exc
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("http", status_code=response.status_code)
    return content
