"""The wave-class retry taxonomy (C, spec: repair sequence) — shared by
the build-in runner and the generate runner.

One bounded re-elicitation for output that is not parseable JSON AT ALL
(``WaveJsonError``), plus one doubled-window retry on truncation — the
same rules for every wave-shaped call so no runner invents a second
convention. Semantic rejections (bad refs, kinds, edge types — contract
shapes inside well-formed JSON) stay plain ``JobPayloadError`` and stay
terminal: they are deterministic, not noise (build_in.py's docstring
for the taxonomy; measured ladder facts: on grammar-enforcing backends
the structural classes are near-dead code, on backends without
enforcement they are the difference between a lost multi-minute wave
and a recovered one).
"""

import logging
from collections.abc import Callable
from dataclasses import replace

from app.core.settings import LLMSettings
from app.pipeline.budget import CallBudget
from app.pipeline.fencing import json_error
from app.pipeline.worker import JobPayloadError
from app.providers.llm import ProviderError

logger = logging.getLogger(__name__)


class WaveJsonError(JobPayloadError):
    """The structural retry signal (C, the retry taxonomy): a wave-class
    response that is not parseable JSON AT ALL. ``call_wave`` gives
    exactly this class one bounded re-elicitation with a rolled seed;
    semantic rejections inside well-formed JSON stay terminal. It is a
    ``JobPayloadError`` subclass, so every existing handler and fail-event
    path keeps working unchanged."""


def repair_retry_prompt(
    base_prompt: str, bad_text: str, retry_note: str, decode_error: str | None = None
) -> str:
    """The one bounded retry when a response is not parseable JSON:
    the same base prompt plus the invalid text, explicit JSON rules, the
    decoder's error line, and one gate-specific shape note (dogfood
    2026-09-09: gemma's record-repair response embedded an excluded
    stat_block whose traits/actions members were bare strings — invalid
    JSON — and the whole wave failed over it). The note is per-gate: the
    record gate excludes the stat block, the stat gate requires it — one
    shared text mis-instructs both. The error line (chunking spec
    2026-09-10: ``JSON error: <str(exc)>`` after the rules) names the exact
    decode failure so the model can fix that spot instead of re-emitting
    the same giant output; omitted only when no decode error is known."""
    lines = [
        base_prompt,
        "",
        "YOUR PREVIOUS RESPONSE COULD NOT BE PARSED AS JSON. Correct it:",
        "return the SAME entries listed above as ONE valid JSON object — nothing else.",
        'JSON rules: every object member is "key": value — a bare string',
        'as an object member is INVALID (e.g. {"A: title"} must become',
        '{"A: title": "..."} or an array of objects); escape any literal',
        'double quote inside a value as \\"; no prose before or after the',
        "object.",
    ]
    if decode_error is not None:
        lines.append(f"JSON error: {decode_error}")
    lines.extend(
        [
            retry_note,
            "",
            "YOUR PREVIOUS INVALID RESPONSE:",
            bad_text.strip(),
        ]
    )
    return "\n".join(lines)


def rolled_seed(settings: LLMSettings, attempt: int) -> LLMSettings:
    """The retry's variance roll (C): when the operator pinned a seed, the
    retry carries seed+attempt so a deterministic profile never re-calls
    the identical failing sample; with no pinned seed the sampling
    temperature already varies and the body stays untouched."""
    if settings.seed is None:
        return settings
    return replace(settings, seed=settings.seed + attempt)


def call_with_truncation_retry(
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    prompt: str,
    *,
    label: str,
    ceiling: int,
) -> str:
    """One provider call under the truncation class of the retry taxonomy
    (B/C): ``finish_reason == "length"`` means the WINDOW was too small for
    this roster — the one retry doubles it inside the operator's cap (a
    plain re-call would deterministically truncate again, which is what
    made the naive retry wrong). Every other ``ProviderError`` propagates
    exactly as before (spec-1.4: connection/http failures are terminal)."""
    try:
        return budget.call(lambda: provider(prompt, settings=settings), label=label)
    except ProviderError as exc:
        doubled = min(settings.max_tokens * 2, ceiling)
        if exc.kind != "truncated" or doubled <= settings.max_tokens:
            raise
        logger.info(
            "%s truncated at max_tokens=%s; retrying once at %s",
            label,
            settings.max_tokens,
            doubled,
        )
        widened = replace(settings, max_tokens=doubled)
        return budget.call(lambda: provider(prompt, settings=widened), label=f"{label}_trunc_retry")


def call_wave[P](
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    prompt: str,
    *,
    label: str,
    parse: Callable[[str], P],
    retry_note: str,
    ceiling: int,
) -> P:
    """One wave-class call under the full bounded retry taxonomy (C):

    * malformed JSON (``WaveJsonError``) -> exactly ONE re-elicitation
      quoting the decoder error (the repair gates' proven
      ``repair_retry_prompt`` shape) with a rolled seed — never an
      identical re-call;
    * truncation -> one doubled-window retry per call (inside
      ``call_with_truncation_retry``, so the JSON retry gets its own);
    * semantic rejection, budget, connection, HTTP -> terminal, unchanged.

    On grammar-enforcing backends the structural classes are near-dead
    code (the ladder measured 4/4 first-try parses under ``json_schema``);
    on backends without enforcement they are the difference between a lost
    multi-minute wave and a recovered one.
    """
    text = call_with_truncation_retry(
        budget, provider, settings, prompt, label=label, ceiling=ceiling
    )
    try:
        return parse(text)
    except WaveJsonError:
        retry_prompt = repair_retry_prompt(prompt, text, retry_note, json_error(text))
        text = call_with_truncation_retry(
            budget,
            provider,
            rolled_seed(settings, 1),
            retry_prompt,
            label=f"{label}_json_retry",
            ceiling=ceiling,
        )
        return parse(text)
