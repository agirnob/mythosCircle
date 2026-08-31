"""Stat-block enforcement for the build-in wave-1 (spec-2.4, AR24/AR25).

Wave-1 character entities (key figures) MUST carry a minimal 5e stat
block in ``data["stat_block"]`` by the time they commit. This module is
the enforcement machinery: issue collection (missing or violating blocks),
the shared deterministic rules text (both the wave-1 prompt and the
repair prompt embed the same constraints the validator enforces — AD-16),
the single bounded repair pass (one LLM call returning corrected stat
blocks ONLY, so edges/names/kinds already validated are untouchable), and
the merge back into the frozen ``EntityInput`` rows.

The repair pass is budget-gated by the caller (``CallBudget``, AR21):
every call counts against ``max_llm_calls``. A still-invalid block after
the pass fails the job BEFORE the wave commits — an invalid stat block is
never committed (AR25); wave-2 characters, factions, and places are out
of scope (Epic 3's AR19 candidates carry stat blocks).
"""

import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.pipeline.fencing import strip_fence
from app.pipeline.knowledge import (
    ABILITY_MAX,
    ABILITY_MIN,
    ALIGNMENTS,
    CLASSES,
    CR_FRACTIONS,
    CR_MAX,
    LEVEL_MAX,
    RACES,
    ROLES,
    SKILLS,
    SPELLS,
    validate_stat_block,
)
from app.pipeline.worker import JobPayloadError
from app.store import models


@dataclass(frozen=True)
class StatIssue:
    """One wave-1 character whose stat block is missing or invalid.

    ``position`` is the entity's positional index in the validated wave —
    the same one its canonical ``E<position>`` ref uses, so the repair
    pass and the failure message can address it.
    """

    position: int
    entity: models.EntityInput
    violations: tuple[str, ...]


def collect_stat_issues(entities: Sequence[models.EntityInput]) -> list[StatIssue]:
    """Flag every character entity lacking a valid ``data.stat_block``.

    Only ``character`` entities are inspected (key figures); a missing
    section is itself a violation. Valid blocks yield no issue.
    """
    issues: list[StatIssue] = []
    for position, entity in enumerate(entities):
        if entity.kind != "character":
            continue
        block = entity.data.get("stat_block")
        if block is None:
            violations: tuple[str, ...] = ("stat_block section missing",)
        else:
            violations = tuple(validate_stat_block(block))
        if violations:
            issues.append(StatIssue(position, entity, violations))
    return issues


def stat_block_rules_text() -> str:
    """The shared, deterministic stat-block rules block (AD-16).

    A pure function of the reference data (sorted) — embedded verbatim in
    the wave-1 prompt and the repair prompt so the model is told exactly
    the constraints ``validate_stat_block`` enforces. No ids, timestamps,
    or job state, ever.
    """
    return "\n".join(
        [
            "STAT BLOCK RULES (characters only; factions and places never carry one)",
            "A character's data.stat_block is a JSON object with the field set:",
            '{"identity": {...}, "attributes": {...}, "combat": {...}, "skills": [...],',
            ' "actions": [...], "traits": [...], "spells": [...]}',
            "identity (required): role, race, and per role:",
            f"- role in {sorted(ROLES)}.",
            f"- NPC and BBEG carry level: an integer in [1, {LEVEL_MAX}]; never cr.",
            f"- Monster carries cr: an integer in [0, {CR_MAX}] or one of "
            f"{sorted(CR_FRACTIONS)}; never level.",
            "- race (required): for NPC/BBEG one of the SRD races; for Monster a",
            f"non-blank type string. NPC/BBEG races: {sorted(RACES)}.",
            f"- class (optional): one of {sorted(CLASSES)}.",
            f"- alignment (optional): one of {sorted(ALIGNMENTS)}.",
            "attributes (required): six ability scores, each an integer in",
            f"[{ABILITY_MIN}, {ABILITY_MAX}]: str, dex, con, int, wis, cha.",
            "combat (required): ac (a positive integer) and hp (a positive integer).",
            f"skills (optional): entries with an SRD skill name and integer bonus: "
            f"{sorted(SKILLS)}.",
            "actions and traits (optional): entries with a name and a description string.",
            "spells (optional): only for NPC/BBEG with an identity.class; every spell",
            "name must be in the local SRD reference and on the class's list:",
            *(
                f"- {name}: {', '.join(sorted(classes))}"
                for name, classes in sorted(SPELLS.items())
            ),
            "Monsters express magical abilities as actions or traits, never spells.",
        ]
    )


def build_stat_repair_prompt(issues: Sequence[StatIssue]) -> str:
    """The one bounded repair pass's prompt (AR25).

    A pure, deterministic function of the flagged issues: each character's
    canonical ref, name, current stat block (or MISSING), and its
    violations, plus the shared rules. No ids, timestamps, or job state.
    """
    flagged: list[str] = []
    for issue in issues:
        block = issue.entity.data.get("stat_block")
        current = (
            json.dumps(block, sort_keys=True, separators=(",", ":"))
            if block is not None
            else "MISSING"
        )
        violations = "\n".join(f"  - {violation}" for violation in issue.violations)
        flagged.append(
            f"E{issue.position} ({issue.entity.name!r}):\n"
            f"current stat_block: {current}\n"
            f"violations:\n{violations}"
        )
    return "\n".join(
        [
            "You are repairing minimal 5e stat blocks for characters in a TTRPG world.",
            "Respond with exactly one JSON object — nothing else.",
            "",
            "STAT BLOCK RULES",
            stat_block_rules_text(),
            "",
            "VIOLATIONS TO FIX",
            "\n\n".join(flagged),
            "",
            "TASK",
            "For EVERY character listed, provide a corrected data.stat_block that",
            "passes every rule above. Do not change anything outside the stat_block.",
            "Use the refs exactly as given.",
            "",
            "OUTPUT CONTRACT",
            'Respond with one JSON object: {"stat_blocks": [{"ref": "E<position>",',
            '  "stat_block": {...}}, ...]} — exactly one entry per character listed,',
            "one ref per entry, nothing else.",
        ]
    )


def parse_stat_repair_output(
    text: str, flagged_positions: Sequence[int]
) -> dict[int, dict[str, Any]]:
    """Parse the repair response into {position: stat_block}.

    The response must list EXACTLY the flagged refs (each once, canonical
    ``E<position>``); a missing, unknown, or duplicate ref is a
    ``JobPayloadError`` — the job fails, never a partial merge.
    """
    stripped = strip_fence(text)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise JobPayloadError(f"stat repair: output is not valid JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise JobPayloadError("stat repair: output must be a JSON object")
    raw = parsed.get("stat_blocks")
    if not isinstance(raw, list):
        raise JobPayloadError("stat repair: output must have a 'stat_blocks' list")
    expected = set(flagged_positions)
    repaired: dict[int, dict[str, Any]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise JobPayloadError("stat repair: each stat_blocks entry must be an object")
        position = _parse_ref(entry.get("ref"))
        if position not in expected:
            raise JobPayloadError(f"stat repair: ref E{position} was not flagged for repair")
        if position in repaired:
            raise JobPayloadError(f"stat repair: ref E{position} appears more than once")
        block = entry.get("stat_block")
        if not isinstance(block, dict):
            raise JobPayloadError(f"stat repair: ref E{position} stat_block must be an object")
        repaired[position] = block
    missing = sorted(expected - repaired.keys())
    if missing:
        names = ", ".join(f"E{position}" for position in missing)
        raise JobPayloadError(f"stat repair: missing repaired stat blocks for {names}")
    return repaired


def apply_stat_repairs(
    entities: Sequence[models.EntityInput],
    repaired: Mapping[int, dict[str, Any]],
) -> list[models.EntityInput]:
    """Merge repaired stat blocks into the validated wave.

    Only the flagged positions' ``data["stat_block"]`` changes (the
    frozen ``EntityInput`` is rebuilt via ``dataclasses.replace``);
    everything else the wave validated — edges, names, kinds, other data
    — is untouched by the repair pass.
    """
    return [
        dataclasses.replace(entity, data={**entity.data, "stat_block": repaired[position]})
        if position in repaired
        else entity
        for position, entity in enumerate(entities)
    ]


def stat_failure_message(issues: Sequence[StatIssue]) -> str:
    """The fail-event message for a wave still invalid after the repair.

    Names each character (canonical ref + name) and its violations, so
    the DM and the job log see exactly what to fix (AR25).
    """
    parts = [
        f"E{issue.position} ({issue.entity.name!r}): {'; '.join(issue.violations)}"
        for issue in issues
    ]
    return "wave 1: stat block(s) still invalid after the repair pass: " + " | ".join(parts)


def _parse_ref(ref: Any) -> int:
    """Parse a canonical ``E<position>`` repair ref (never zero-padded)."""
    if not isinstance(ref, str) or not ref.startswith("E"):
        raise JobPayloadError(f"stat repair: ref must be E<position>, got {ref!r}")
    digits = ref[1:]
    if not digits.isdigit() or str(int(digits)) != digits:
        raise JobPayloadError(f"stat repair: ref must be E<position>, got {ref!r}")
    return int(digits)
