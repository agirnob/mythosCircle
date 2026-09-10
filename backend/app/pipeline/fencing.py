"""Optional markdown-fence stripping for LLM JSON output (spec-2.3/2.4).

Shared by the build-in wave parse and the stat-block repair parse: both
eat provider output that may be wrapped in a ```json fence. A fence
without a closer is returned as-is so the JSON error names the real
problem.
"""

import json
from typing import Any


def strip_fence(text: str) -> str:
    """Strip one optional markdown fence pair from LLM output.

    Leading blank lines are ignored; when the first non-blank line is a
    fence opener (`` ``` `` or `` ```json ``) and the last line is a fence
    closer, both are removed (trailing whitespace tolerated on both). A
    fence without a closer is returned as-is so the JSON error names the
    real problem.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return stripped


def extract_json_object(text: str) -> str | None:
    """Extract the first balanced ``{...}`` JSON object from model text.

    The fence-strip handles a markdown fence; this handles output that
    wraps the object in prose ("Here is the JSON: {...} hope that helps")
    or trails content after it. Strings (with ``\\`` escapes) are skipped
    so braces inside values never disturb the balance. Returns the object
    slice, or None when no balanced object is found.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_json_object(text: str) -> dict[str, Any] | None:
    """The JSON object inside ``text``, or None.

    Tries the fence-stripped text, then — when that does not parse — the
    first balanced object extracted from it (prose-wrapped output). The
    result must be a JSON object; anything else (a list, a scalar, or
    malformed JSON) is None so the caller decides whether to retry. A
    JSON-decode failure is NOT a contract violation: callers that can
    re-elicit (the bounded repair gates) retry once; the wave parse keeps
    failing the job.
    """
    candidates: list[str | None] = [strip_fence(text), extract_json_object(text)]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def json_error(text: str) -> str | None:
    """The JSON decode failure in ``text``, or None when parseable.

    Mirrors ``parse_json_object``'s candidate order (the fence-stripped
    text, then the first balanced object extracted from it): the first
    candidate that parses as an object returns None, and otherwise the
    LAST failing candidate's error wins — a prose-wrapped or fenced
    response quotes the inner object's defect, not the surrounding
    backticks or prose. Returns ``str(exc)`` so a retry prompt can quote
    the exact failure; ``"not a JSON object"`` when no candidate exists,
    when there are no braces to decode, or when the text parses as a
    non-object; a bare ``RecursionError`` stringifies empty, so that
    branch falls back to naming the nesting depth.
    """
    candidates: list[str | None] = [strip_fence(text), extract_json_object(text)]
    error: str | None = None
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            error = str(exc) if "{" in candidate else "not a JSON object"
        except RecursionError as exc:
            error = str(exc) or "JSON nested too deeply to decode"
            continue
        else:
            if isinstance(parsed, dict):
                return None
            error = "not a JSON object"
    if error is None:
        return "not a JSON object"
    return error
