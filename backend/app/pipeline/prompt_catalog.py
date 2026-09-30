"""Checked-in prompt contracts shared by the generation pipelines.

The dynamic parts of a prompt (campaign data, world context, submitted records,
and output-size values) stay assembled in Python. Stable instructions live in
``app/prompts.toml`` so prompt review does not require searching long Python
lists and so the active contract has one source of truth.
"""

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

_PROMPTS_FILE = Path(__file__).resolve().parents[1] / "prompts.toml"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict[str, str]]:
    with _PROMPTS_FILE.open("rb") as handle:
        raw: Any = tomllib.load(handle)
    if not isinstance(raw, dict):
        raise RuntimeError(f"prompt catalog must be a TOML object: {_PROMPTS_FILE}")
    return {
        section: {key: value for key, value in values.items() if isinstance(value, str)}
        for section, values in raw.items()
        if isinstance(values, dict)
    }


def prompt_contract(group: str, key: str = "contract") -> str:
    """Return one required prompt contract, failing early if it is missing."""
    try:
        value = _catalog()[group][key]
    except KeyError as exc:
        raise RuntimeError(f"missing prompt contract {group}.{key} in {_PROMPTS_FILE}") from exc
    return value.strip()
