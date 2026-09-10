"""Stat-block enforcement for the build-in wave-1 (spec-2.4, AR24/AR25).

Wave-1 character entities (key figures) MUST carry a minimal 5e stat
block in ``data["stat_block"]`` by the time they commit. This module is
the enforcement machinery: issue collection (missing or violating blocks),
the shared deterministic rules text (both prompts embed the same
constraints; the spells-by-class reference is scoped per prompt —
full for wave 1, flagged classes only for repair, AD-16),
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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.pipeline import combat
from app.pipeline.fencing import parse_json_object
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
    resolve_class,
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


def stat_block_rules_text(spells_reference: bool = True) -> str:
    """The shared, deterministic stat-block rules block (AD-16).

    A pure function of the reference data (sorted) — embedded verbatim in
    the wave-1 prompt and the repair prompt so the model is told exactly
    the constraints ``validate_stat_block`` enforces. No ids, timestamps,
    or job state, ever. ``spells_reference=False`` (the wave-2 generate
    prompt, which omits the SRD SPELLS BY CLASS table — the runtime
    validator and the repair pass own it) swaps only the closing pointer
    to that table; every other line is identical.
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
            "CHALLENGE SCALING (stats must match the declared level/CR, never be",
            "flat: a tougher declaration means tougher combat numbers and scores.",
            "Enforced by the validator on the build-in and generate paths):",
            "- NPC/BBEG scale with level on the DMG monster table for that number:",
            "  damage/round ~9-14 at level 1, ~33-38 at level 5, ~63-68 at level 10,",
            "  ~93-98 at level 15, ~123-140 at level 20; hp ~71-85 at level 1,",
            "  ~131-145 at level 5, ~206-220 at level 10, ~281-295 at level 15,",
            "  ~356-375 at level 20. hp below half the band low fails (frail);",
            "  high hp never fails.",
            "- Monster scale with CR on the same table: damage/round ~2-3 at CR 1/8,",
            "  ~6-8 at CR 1/2, ~33-38 at CR 5, ~87-92 at CR 14, ~111-116 at CR 18,",
            "  ~141-158 at CR 21, 303+ at CR 30; hp ~7-35 at CR 1/8, ~50-70 at",
            "  CR 1/2, ~131-145 at CR 5, ~266-280 at CR 14, ~326-340 at CR 18,",
            "  ~376-400 at CR 21, 566+ at CR 30. Unlisted levels/CRs interpolate",
            "  between the listed rows. A Multiattack routine deals its",
            "  count times the strongest other attack (the routine itself is",
            "  excluded, an unnamed count means 2, and 0 with no other attack).",
            "  Damage counts with the validator's adjustments: save-for-half at",
            "  0.75x, area effects at 2 targets, limited-use (recharge/per-day)",
            "  at 1/3.",
            "- ac and ability scores rise with challenge: ac ~10-13 at the bottom,",
            "  ~19+ at the top; scores average or low at level 1/CR 0, high at",
            "  level 20/CR 30. Ac and scores are guidance only — the validator",
            "  enforces the DPR/HP bands above.",
            f"skills (optional): entries with an SRD skill name and integer bonus: "
            f"{sorted(SKILLS)}.",
            "actions and traits (optional): entries with a name and a description string.",
            "spells (optional): never for role Monster (a monster's magic is actions",
            "or traits); otherwise only with an identity.class and no repeats — every",
            *(
                [
                    "spell name must appear on that class's line of the SRD SPELLS BY CLASS",
                    "reference below.",
                ]
                if spells_reference
                else [
                    "spell name must be on that class's SRD spell list (the runtime",
                    "validator owns the authoritative table).",
                ]
            ),
        ]
    )


def spells_reference_text(classes: Sequence[str] | None = None) -> str:
    """The SRD spells-by-class reference (AD-16), grouped per class.

    A pure function of the reference data: sorted classes, sorted spell
    names. ``classes`` (the flagged classes of a repair pass) narrows the
    listing to those classes' lines; ``None`` lists every class — the
    wave-1 prompt's form, where no class is known yet.
    """
    if classes is None:
        selected = sorted(CLASSES)
    else:
        selected = sorted({klass for klass in classes if klass in CLASSES})
    lines = ["SRD SPELLS BY CLASS:"]
    for klass in selected:
        spells = sorted(name for name, allowed in SPELLS.items() if klass in allowed)
        lines.append(f"- {klass}: {'; '.join(spells) if spells else '(none listed)'}")
    return "\n".join(lines)


#: Canonical record roles keyed by folded ``data["role"]`` text (the AR24
#: identity anchor). Anything outside this map is unparseable — no target.
_RECORD_ROLES: dict[str, str] = {"npc": "NPC", "bbeg": "BBEG", "monster": "Monster"}

#: Static damage recipes (spec-stat-repair-dpr-guidance, Design Notes).
#: Every number is checked against the parser's own math: dice average to
#: (1 + size) / 2 with flat +N in full; a Multiattack routine contributes
#: its count times the strongest OTHER damaging action (the routine text
#: carries ONLY the count, never dice); recharge / per-day actions average
#: over 3 rounds (x1/3); save-for-half averages x0.75. Static by design —
#: the prompt stays a pure deterministic function of ``issues``.
_DPR_RECIPES: str = "\n".join(
    [
        "Dice averages: d4 2.5, d6 3.5, d8 4.5, d10 5.5, d12 6.5; a flat +N adds in full.",
        "The auditor counts a Multiattack routine as its count times the strongest OTHER",
        "damaging action (the routine text carries ONLY the count, never dice); a",
        "recharge / per-day action averages over 3 rounds (x1/3); a save-for-half",
        "effect averages x0.75; an area attack (cone/radius/line/sphere/cube/area/",
        "within/burst) counts double (assumed 2 targets). Adjustment factors multiply.",
        "Non-blank boss.legendary_actions adds one full extra attack — size base damage",
        "one attack lower when the boss has one.",
        "HP floor: combat.hp at/above the band low (level 1: 71+, 5: 131+, 10: 206+,",
        "15: 281+, 20: 356+; CR targets use the same table row) — below half the low",
        "fails frail.",
        "Worked recipes (nominal averages, tolerance is 0.8x low to 1.2x high):",
        "- level 1 (band 9-14): one attack `2d6+3` (~10).",
        "- level 5 (band 33-38): Multiattack `makes three attacks` + `2d8+4` (~13) x3 = ~39.",
        "- level 10 (band 63-68): Multiattack `makes three attacks` + `4d10+5` (~27) x3 = ~81",
        "  (within the 1.2x over-tolerance, 81.6).",
        "- level 20 (band 123-140): Multiattack `makes four attacks` + `5d10+6` (~33.5) x4 = ~134.",
        "Unlisted levels/CRs interpolate between the neighboring recipes.",
        "Prefer hitting the record target band; lower identity.level only if the damage",
        "cannot reach it. You MAY lower identity.level to a band the damage satisfies",
        "(record level_cr text is display-only; the export derives level/CR from the",
        "stat_block numerics).",
        "With no record target above, declare an identity.level your damage supports —",
        "the HIGHEST such level (never level 1 for an archmage concept).",
        "True non-combatants: write zero dice anywhere and the block is exempt from the",
        "power check — with no +/-N damage modifiers either (a lone `+5 damage` still",
        "counts; only `actions` are audited) — one weak attack is worse than none.",
    ]
)


def _record_target_line(entity: models.EntityInput) -> str | None:
    """One character's DPR target from its AR24 record (``role``/``level_cr``).

    Pure/deterministic: folds the display-only record text into a band key
    (``level <n>`` for NPC/BBEG, ``CR <n>`` — int or 1/8, 1/4, 1/2 — for
    Monster) and reads the band off ``combat.CR_DPR``. Returns ``None``
    when the record is unparseable or bandless (blank/garbled ``level_cr``,
    unknown role, out-of-range challenge) — the caller then omits the line
    and the generic recipes carry the repair.
    """
    data = entity.data if isinstance(entity.data, dict) else {}
    role_raw = data.get("role")
    level_cr_raw = data.get("level_cr")
    if not isinstance(role_raw, str) or not isinstance(level_cr_raw, str):
        return None
    canonical = _RECORD_ROLES.get(role_raw.strip().lower())
    if canonical is None:
        return None
    text = level_cr_raw.strip()
    if canonical == "Monster":
        match = re.search(r"\bcr\s*(\d+\s*/\s*\d+|\d+)(?![\d.])", text, re.IGNORECASE)
        if match is None:
            return None
        key: Any = re.sub(r"\s+", "", match.group(1))
        if "/" in key:
            if key not in CR_FRACTIONS:
                return None
        else:
            key = int(key)
            if not 0 <= key <= CR_MAX:
                return None
        challenge = f"CR {key}"
    else:
        match = re.search(r"\blevel\s*(\d+)(?![\d.])", text, re.IGNORECASE)
        if match is None:
            return None
        key = int(match.group(1))
        if not 1 <= key <= LEVEL_MAX:
            return None
        challenge = f"level {key}"
    band = combat.CR_DPR.get(key)
    if band is None:
        return None
    low, high = band
    if low == high:
        return f"record target: {challenge} -> hit DPR band {low:.0f}+"
    return f"record target: {challenge} -> hit DPR band {low:.0f}-{high:.0f}"


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
        target = _record_target_line(issue.entity)
        target_line = f"\n{target}" if target is not None else ""
        violations = "\n".join(f"  - {violation}" for violation in issue.violations)
        flagged.append(
            f"E{issue.position} ({issue.entity.name!r}):\n"
            f"current stat_block: {current}{target_line}\n"
            f"violations:\n{violations}"
        )
    classes = _flagged_classes(issues) or None
    return "\n".join(
        [
            "You are repairing minimal 5e stat blocks for characters in a TTRPG world.",
            "Respond with exactly one JSON object — nothing else.",
            "",
            "STAT BLOCK RULES",
            stat_block_rules_text(),
            "",
            spells_reference_text(classes),
            "",
            "VIOLATIONS TO FIX",
            "\n\n".join(flagged),
            "",
            "DAMAGE RECIPES (parser-checked — hit each character's record target band)",
            _DPR_RECIPES,
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
) -> dict[int, dict[str, Any]] | None:
    """Parse the repair response into {position: stat_block}.

    The response must list EXACTLY the flagged refs (each once, canonical
    ``E<position>``); a missing, unknown, or duplicate ref is a
    ``JobPayloadError`` — the job fails, never a partial merge. Returns
    None when the text is not parseable as one JSON object (the build-in
    stat gate retries once — ``_run_repair``)."""
    parsed = parse_json_object(text)
    if parsed is None:
        return None
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


def _flagged_classes(issues: Sequence[StatIssue]) -> list[str]:
    """Canonical classes present in the flagged blocks — the repair
    prompt's spell-reference subset. Empty when no flagged block carries
    a valid class (then every class is embedded, since the repair may
    invent one)."""
    classes: set[str] = set()
    for issue in issues:
        block = issue.entity.data.get("stat_block")
        if isinstance(block, dict):
            identity = block.get("identity")
            if isinstance(identity, dict):
                klass = resolve_class(identity.get("class"))
                if klass is not None:
                    classes.add(klass)
    return sorted(classes)


def strip_noncharacter_stat_blocks(
    entities: Sequence[models.EntityInput],
) -> list[models.EntityInput]:
    """Drop ``data["stat_block"]`` from faction/place entities (spec-2.4
    review decision): only characters carry stat blocks (AR24) and 2.6/2.7
    must never see a stats-bearing faction. A stray block from a
    non-character is stripped — tolerated, never a reason to fail the
    whole build. Characters pass through untouched.
    """
    return [
        dataclasses.replace(
            entity,
            data={k: v for k, v in entity.data.items() if k != "stat_block"},
        )
        if entity.kind != "character" and "stat_block" in entity.data
        else entity
        for entity in entities
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
    # Length guard first: int() on a 4300+-digit string raises raw
    # ValueError, escaping the JobPayloadError channel as a 500.
    if len(digits) > 6 or not digits.isdecimal() or str(int(digits)) != digits:
        raise JobPayloadError(f"stat repair: ref must be E<position>, got {ref!r}")
    return int(digits)
