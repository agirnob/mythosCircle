"""Optional markdown-fence stripping for LLM JSON output (spec-2.3/2.4).

Shared by the build-in wave parse and the stat-block repair parse: both
eat provider output that may be wrapped in a ```json fence. A fence
without a closer is returned as-is so the JSON error names the real
problem.
"""


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
