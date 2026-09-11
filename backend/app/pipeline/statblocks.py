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
            "  ~19+ at the top; scores ~8-12 for a low challenge, and 26-30 in a",
            "  level-15+/CR-15+ creature's best abilities (the DMG monster table — NOT",
            "  the 20 that caps a player character).",
            "- the action list deepens with challenge: a level-10+/CR-10+ creature has",
            "  TWO TO FOUR entries — a named Multiattack routine plus the distinct",
            "  attacks it uses. One lone attack is a shallow block, not a shortcut,",
            "  and it cannot carry the damage band above on its own.",
            "  Ac and scores are guidance only — the validator",
            "  enforces the DPR/HP bands above.",
            f"skills (optional): entries with an SRD skill name and integer bonus: "
            f"{sorted(SKILLS)}.",
            "actions and traits (optional): entries with a name and a description string.",
            "a damaging ACTION also carries a structured damage list — one entry per",
            "damage type, kept in step with the description (which still states the",
            "numbers for the DM):",
            '  {"name": "Oathblade", "to_hit": 18,',
            '   "description": "Melee Weapon Attack: +18 to hit, reach 5 ft., one target.',
            '     Hit: 19 (2d6 + 12) slashing damage plus 16.5 (3d10) radiant damage.",',
            '   "damage": [{"dice": "2d6", "count": 2, "sides": 6, "bonus": 12,',
            '               "average": 19, "type": "slashing"},',
            '              {"dice": "3d10", "count": 3, "sides": 10, "bonus": 0,',
            '               "average": 16.5, "type": "radiant"}]}',
            "  The damage list is the machine-readable copy the auditor, the character",
            "  sheet and the export read; a Multiattack routine carries NO damage list",
            "  (its text states the count only). Never write damage in a key of its own.",
            "OPTIONAL EXTRA MECHANICS (include what applies, omit the rest — a wrong",
            "number is worse than an absent one):",
            '- combat.hit_dice: a dice expression string, e.g. "24d10 + 192".',
            '- saves: integer save bonus per ability score, e.g. {"con": 15, "wis": 18}.',
            '- features: a list of feature NAMES, e.g. ["Divine Smite", "Aura of',
            '  Protection"] — names only, never objects (that is what traits are for).',
            "- initiative and passive_perception: integers.",
            "- proficiency_bonus: an integer (2 early, 6 by level 17).",
            '- spellcasting: {"dc": 21, "attack_bonus": 13, "slots": [4, 3, 3, 3, 1]}.',
            '- resources: integer pools, e.g. {"lay_on_hands": 85, "channel_divinity": 2}.',
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
        "spells need identity.class from the SRD list (never for Monster): set one class",
        "whose list holds every spell, drop uncovered spells, or delete the spells array.",
        "HP floor follows the DPR row (CR 1/4: 36+, CR 1/2: 50+, CR 5: 131+); harmless",
        "Tiny creatures: CR 0 with zero dice anywhere (exempt from the power check, any",
        "hp passes — but a single die averages over 1.2 DPR and overs, so truly none).",
        "Tiny NPC/BBEG that must stay leveled: zero dice and hp at/above half the band",
        "low (level 1: 36+).",
        "Challenge number is REQUIRED inside identity, never omitted: Monster carries",
        'identity.cr as a bare integer 0-30 (fractions as quoted strings "1/8", "1/4",',
        '"1/2" — a bare 1/2 is invalid JSON); NPC/BBEG carry identity.level as a',
        "bare integer 1-20; never floats, quoted numbers, booleans, or null (no 0.5,",
        'no "8", no 5.0);',
        "never carry the other role's key. (The record target line's",
        '"CR 5" is display text — the identity value is 5.)',
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


_FULL_REPAIR_SCOPE: frozenset[str] = frozenset(
    {
        "identity",
        "attributes",
        "combat",
        "skills",
        "actions",
        "traits",
        "spells",
        "saves",
        "initiative",
        "passive_perception",
        "proficiency_bonus",
        "spellcasting",
        "resources",
        "features",
    }
)
#: Every top-level stat_block section the validator reads (see
#: ``knowledge.validate_stat_block``): ``combat`` owns hp/ac (plus hit_dice),
#: ``actions`` owns damage numbers, ``identity`` owns the challenge
#: declaration (level/CR). Only a missing/non-object block scopes this wide —
#: every other violation names its sections (never a silent whole-block
#: fallback for a new kind).

#: Power-band scope: a DPR miss is fixed with damage numbers, hp/ac, or the
#: challenge declaration — never wording, skills, or spells.
_POWER_REPAIR_SCOPE: frozenset[str] = frozenset({"actions", "combat", "identity"})

#: Violation prefixes mapped to the editable sections that fix them
#: (codebase idiom: ``_CONFORMABLE_PREFIXES`` below). Order matters:
#: ``spellcasting`` precedes the bare ``spell`` catch-all, else the
#: mechanics field would scope to spells+identity.
#: Violation prefixes mapped to the editable sections that fix them
#: (codebase idiom: ``_CONFORMABLE_PREFIXES`` below). Order matters:
#: first match wins, so the general ``stat_block`` prefix sits LAST — a
#: future field-specific ``stat_block.<section>...`` message must scope
#: narrowly instead of falling into whole-block. (Today no message has
#: that shape; the 60-row coverage test pins every current mapping, so
#: any reorder breakage fails loudly there.) A new validator message about
#: mechanics field would scope to spells+identity.
_SCOPE_PREFIXES: tuple[tuple[str, frozenset[str]], ...] = (
    ("under-powered for ", _POWER_REPAIR_SCOPE),
    ("over-powered for ", _POWER_REPAIR_SCOPE),
    ("no readable damage for ", frozenset({"actions"})),
    ("identity", frozenset({"identity"})),
    ("attributes", frozenset({"attributes"})),
    ("combat", frozenset({"combat"})),
    ("saves", frozenset({"saves"})),
    ("initiative", frozenset({"initiative"})),
    ("passive_perception", frozenset({"passive_perception"})),
    ("proficiency_bonus", frozenset({"proficiency_bonus"})),
    ("spellcasting", frozenset({"spellcasting"})),
    ("resources", frozenset({"resources"})),
    ("features", frozenset({"features"})),
    ("actions", frozenset({"actions"})),
    ("traits", frozenset({"traits"})),
    ("skills", frozenset({"skills"})),
    # Spells may need identity.class set (or the role changed) to fix, so
    # both sections stay editable — the breach log still sees the diff.
    ("spell", frozenset({"spells", "identity"})),
    ("stat_block", _FULL_REPAIR_SCOPE),
)


def scope_for_violations(violations: Sequence[str]) -> frozenset[str]:
    """The editable top-level stat_block sections for these violations.

    Pure function shared by the repair prompt (EDIT SCOPE lines) and the
    gate's breach log — one map, never two literals to keep in sync.
    Raises ``ValueError`` on an unmapped violation (fail-closed: a new
    validator message must extend ``_SCOPE_PREFIXES`` — the scope-map
    coverage test enforces it — never a silent whole-block fallback).
    """
    if not violations:
        raise ValueError(
            "stat repair: no edit scope for an empty violation list — "
            "an issue always carries its violations"
        )
    scope: set[str] = set()
    for violation in violations:
        for prefix, sections in _SCOPE_PREFIXES:
            if violation.startswith(prefix):
                scope |= sections
                break
        else:
            # Safety net mirroring ``_CONFORMABLE_SUBSTRINGS``: the frail
            # message is caught by the "combat" prefix today, but an
            # hp-scoped rewording must never fall through to ValueError.
            if " is frail for " in violation:
                scope |= {"combat"}
            else:
                raise ValueError(
                    f"stat repair: no edit scope for violation {violation!r} — "
                    "extend _SCOPE_PREFIXES"
                )
    return frozenset(scope)


def build_stat_repair_prompt(issues: Sequence[StatIssue], *, attempt: int = 1) -> str:
    """One bounded repair pass's prompt (AR25; the second pass added
    2026-09-11 by owner decision).

    A pure, deterministic function of the flagged issues and the attempt
    number: each character's canonical ref, name, current stat block (or
    MISSING), its violations, and its EDIT SCOPE line (the
    ``scope_for_violations`` sections for those violations — the repair
    names its editable fields so pass 1 stops inflating healthy numbers),
    plus the shared rules. On ``attempt=2`` the "current stat_block" is the
    one the FIRST repair produced and the violations are the ones that
    SURVIVED it — the model is correcting its own edit against exactly
    what is still wrong, which is what the owner asked for. No ids,
    timestamps, or job state.
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
        scope = scope_for_violations(issue.violations)
        if scope >= _FULL_REPAIR_SCOPE:
            scope_line = "EDIT SCOPE: whole stat_block (missing block — write the complete block)"
        else:
            scope_line = (
                f"EDIT SCOPE: {', '.join(sorted(scope))} — change ONLY these "
                "sections; leave every other section byte-identical"
            )
        flagged.append(
            f"E{issue.position} ({issue.entity.name!r}):\n"
            f"current stat_block: {current}{target_line}\n"
            f"violations:\n{violations}\n"
            f"{scope_line}"
        )
    classes = _flagged_classes(issues) or None
    if attempt == 1:
        violations_header = ["VIOLATIONS TO FIX"]
    else:
        violations_header = [
            "VIOLATIONS STILL UNFIXED — SECOND REPAIR PASS",
            "The stat_block shown under each character is the one YOUR FIRST",
            "REPAIR produced, and it still fails every violation listed below",
            "it. Change the NUMBERS — attack count, dice faces, flat bonus,",
            "combat.hp, combat.ac — because a reworded description changes no",
            "number the validator reads.",
        ]
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
            *violations_header,
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


def build_stat_repair_schema() -> dict[str, Any]:
    """The strict response schema carried on stat-repair calls (spec: edgeless
    repair scope) — the ``response_format`` wrapper follows the wave calls'
    measured ``json_schema`` convention; the inner schema is flat and
    ``$ref``-free (GBNF subset). Trait items require name+description and
    damage parts require their full six fields, so a bare-string or
    name-only shape (the runlog's E4 chase across both passes) is
    unemittable on enforcing backends; ``strict: True`` with closed
    objects everywhere except the free-form ``resources`` map. No
    ``minItems`` anywhere, so a true non-combatant's empty lists stay
    legal. Parsing and all gates stay the backstop — the schema is the
    optimization, for non-enforcing backends nothing changes.
    """
    damage_part: dict[str, Any] = {
        "type": "object",
        "required": ["dice", "count", "sides", "bonus", "average", "type"],
        "properties": {
            "dice": {"type": "string"},
            "count": {"type": "integer"},
            "sides": {"type": "integer"},
            "bonus": {"type": "integer"},
            "average": {"type": "number"},
            "type": {"type": "string"},
        },
        "additionalProperties": False,
    }
    action_item: dict[str, Any] = {
        "type": "object",
        "required": ["name", "description"],
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "to_hit": {"type": "integer"},
            "damage": {"type": "array", "items": damage_part},
        },
        "additionalProperties": False,
    }
    named_item: dict[str, Any] = {
        "type": "object",
        "required": ["name", "description"],
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
        },
        "additionalProperties": False,
    }
    skill_item: dict[str, Any] = {
        "type": "object",
        "required": ["name", "bonus"],
        "properties": {
            "name": {"type": "string"},
            "bonus": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    stat_block: dict[str, Any] = {
        "type": "object",
        "required": ["identity", "attributes", "combat"],
        "properties": {
            "identity": {
                "type": "object",
                "required": ["role", "race"],
                "properties": {
                    "role": {"type": "string"},
                    "level": {"type": "integer"},
                    "cr": {"type": ["integer", "string"]},
                    "race": {"type": "string"},
                    "class": {"type": "string"},
                    "alignment": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "attributes": {
                "type": "object",
                "required": ["str", "dex", "con", "int", "wis", "cha"],
                "properties": {
                    "str": {"type": "integer"},
                    "dex": {"type": "integer"},
                    "con": {"type": "integer"},
                    "int": {"type": "integer"},
                    "wis": {"type": "integer"},
                    "cha": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            "combat": {
                "type": "object",
                "required": ["ac", "hp"],
                "properties": {
                    "ac": {"type": "integer"},
                    "hp": {"type": "integer"},
                    "hit_dice": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "skills": {"type": "array", "items": skill_item},
            "actions": {"type": "array", "items": action_item},
            "traits": {"type": "array", "items": named_item},
            "spells": {"type": "array", "items": {"type": "string"}},
            "saves": {
                "type": "object",
                "properties": {
                    "str": {"type": "integer"},
                    "dex": {"type": "integer"},
                    "con": {"type": "integer"},
                    "int": {"type": "integer"},
                    "wis": {"type": "integer"},
                    "cha": {"type": "integer"},
                },
                "additionalProperties": False,
            },
            "initiative": {"type": "integer"},
            "passive_perception": {"type": "integer"},
            "proficiency_bonus": {"type": "integer"},
            "spellcasting": {
                "type": "object",
                "properties": {
                    "dc": {"type": "integer"},
                    "attack_bonus": {"type": "integer"},
                    "slots": {"type": "array", "items": {"type": "integer"}},
                },
                "additionalProperties": False,
            },
            # Free-form name -> integer pools: the keys are DM/model-chosen,
            # so this one object stays open (never a shape the repair must
            # fix — the validator only checks value types).
            "resources": {"type": "object"},
            "features": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "stat_repair",
            "strict": True,
            "schema": {
                "type": "object",
                "required": ["stat_blocks"],
                "properties": {
                    "stat_blocks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["ref", "stat_block"],
                            "properties": {
                                "ref": {"type": "string"},
                                "stat_block": stat_block,
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
    }


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


#: Damage types the deterministic power-conform reaches for when it must
#: add a clause the model did not write. Thematic where the class is known
#: and neutral otherwise — it never overrides a type already in the block.
_CONFORM_DAMAGE_TYPES: dict[str, str] = {
    "Paladin": "radiant",
    "Cleric": "radiant",
    "Warlock": "necrotic",
    "Druid": "poison",
    "Rogue": "poison",
    "Ranger": "piercing",
    "Fighter": "slashing",
    "Barbarian": "slashing",
    "Monk": "bludgeoning",
    "Bard": "psychic",
    "Wizard": "force",
    "Sorcerer": "force",
}
_DEFAULT_CONFORM_DAMAGE = "force"

#: Violation shapes this pass can fix. Under-powered is a prefix; the frail
#: message reads "combat.hp 52 is frail for level 4 (...)", so it matches on
#: the substring. Everything else (shape, vocabulary, role rules) belongs to
#: the model's one repair pass.
_CONFORMABLE_PREFIXES = ("under-powered for ", "no readable damage for ")
_CONFORMABLE_SUBSTRINGS = (" is frail for ",)

#: Upper bound on rider-picking iterations; the solve is exact, so one pass
#: normally suffices and this only catches a mis-modelled multiplier.
_CONFORM_MAX_ROUNDS = 4


def is_conformable(violations: Sequence[str]) -> bool:
    """Whether every violation is a power-band miss this pass can repair."""
    if not violations:
        return False
    return all(
        violation.startswith(_CONFORMABLE_PREFIXES)
        or any(marker in violation for marker in _CONFORMABLE_SUBSTRINGS)
        for violation in violations
    )


def _rider_dice(target: float) -> tuple[int, int, float]:
    """The dice expression whose average lands closest to ``target``."""
    best: tuple[float, int, int, float] | None = None
    for count in range(1, 21):
        for sides in (4, 6, 8, 10, 12):
            average = count * combat._die_avg(sides)
            gap = abs(average - target)
            if best is None or gap < best[0]:
                best = (gap, count, sides, average)
    assert best is not None
    return best[1], best[2], best[3]


def _append_damage_clause(
    block: dict[str, Any], action_name: str, text: str, part: dict[str, Any] | None = None
) -> bool:
    """Append a canonical 5e damage clause to one action's description.

    When the action already carries a structured ``damage`` list, the same
    numbers are appended there too (spec 2026-09-11): the auditor, sheet
    and export read the parts, so a clause the parts do not know about
    would leave the block stating two different damages. A block with no
    parts stays prose-only — this pass never invents structure.
    """
    actions = block.get("actions")
    if not isinstance(actions, list):
        return False
    for action in actions:
        if isinstance(action, dict) and action.get("name") == action_name:
            description = action.get("description")
            description = description if isinstance(description, str) else ""
            description = description.rstrip()
            if description.endswith("."):
                description = description[:-1]
            action["description"] = f"{description} {text}"
            parts = action.get("damage")
            if part is not None and isinstance(parts, list):
                parts.append(part)
            return True
    return False


#: Ability-score floors by challenge tier, highest-ranked ability first.
#: THREAT scale, not point-buy: a level-17 NPC sits on the DMG monster table
#: (ancients run STR 27-28 / CON 25-26), so the model's own scores are lifted
#: to the row for their challenge — the model picks the RANKING (a paladin's
#: STR over its INT), the table picks the magnitude.
_CONFORM_SCORES: dict[str, tuple[int, ...]] = {
    "1-4": (17, 16, 14, 12, 10, 8),
    "5-8": (20, 19, 17, 15, 12, 9),
    "9-14": (24, 22, 20, 17, 14, 10),
    "15+": (28, 26, 24, 20, 16, 12),
}

#: Armour-class floor by tier (the DMG monster AC column, not armor math).
_CONFORM_AC: dict[str, int] = {"1-4": 15, "5-8": 17, "9-14": 19, "15+": 21}

_ABILITY_ORDER: tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha")

#: "…+8 to hit…" and the flat half of "…35 (5d10 + 6) …".
_TO_HIT_RE = re.compile(r"([+-])\s*(\d+)\s+to hit")
_FLAT_DAMAGE_RE = re.compile(r"(\d+d\d+)\s*\+\s*(\d+)")
#: The 5e damage pair "19 (2d8 + 10)" — leading average, dice in brackets.
_DAMAGE_PAIR_RE = re.compile(r"\d+(?:\.\d+)?\s*\(([^()]*\d+d\d+[^()]*)\)")


def _tier_of(identity: Mapping[str, Any]) -> str | None:
    """The score/AC tier for a declared challenge, or ``None`` when it has
    no reference row (the caller then leaves the block alone)."""
    role = identity.get("role")
    key: Any = identity.get("cr") if role == "Monster" else identity.get("level")
    if isinstance(key, str):
        return "1-4" if key in CR_FRACTIONS else None
    if type(key) is not int or not 0 <= key <= CR_MAX:
        return None
    if key <= 4:
        return "1-4"
    if key <= 8:
        return "5-8"
    if key <= 14:
        return "9-14"
    return "15+"


def _attack_modifier(attributes: Mapping[str, Any]) -> int:
    """The modifier an attack would use: the better of STR and DEX."""
    scores = [v for v in (attributes.get("str"), attributes.get("dex")) if isinstance(v, int)]
    return (max(scores) - 10) // 2 if scores else 0


def _conform_ability_scores(block: dict[str, Any], tier: str) -> int:
    """Lift the six scores to the tier floor, preserving the model's ranking.

    Returns the attack-modifier delta so the numbers the same prose already
    states (to-hit, flat damage) can be shifted to match — lifting a score
    without it would leave the block stating a bonus its scores no longer
    support.
    """
    attributes = block.get("attributes")
    if not isinstance(attributes, dict):
        return 0
    before = _attack_modifier(attributes)
    floors = _CONFORM_SCORES[tier]
    ranked = sorted(
        _ABILITY_ORDER,
        key=lambda ability: (
            -(attributes[ability] if isinstance(attributes.get(ability), int) else 0),
            _ABILITY_ORDER.index(ability),
        ),
    )
    for position, ability in enumerate(ranked):
        current = attributes.get(ability)
        if isinstance(current, int):
            attributes[ability] = max(current, floors[position])
    return _attack_modifier(attributes) - before


def _shift_derived_numbers(
    block: dict[str, Any], delta: int, *, to_hit: bool = True, flat: bool
) -> None:
    """Shift every action's to-hit, and/or its flat damage, by ``delta``.

    Shifting rather than recomputing keeps whatever weapon enhancement the
    model wrote alongside the ability bonus. The two halves are separable
    because only the flat half moves DPR: the score lift always takes the
    to-hit, and takes the flat only where the band tolerates it. ``to_hit``
    defaults off on the second call so a block cannot be shifted twice.
    """
    if delta == 0:
        return
    actions = block.get("actions")
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, dict):
            continue
        description = action.get("description")
        if not isinstance(description, str) or not description:
            continue

        def shift_to_hit(match: re.Match[str]) -> str:
            signed = int(f"{match.group(1)}{match.group(2)}") + delta
            return f"{signed:+d} to hit"

        def shift_flat(match: re.Match[str]) -> str:
            return f"{match.group(1)} + {int(match.group(2)) + delta}"

        if to_hit:
            description = _TO_HIT_RE.sub(shift_to_hit, description)
        if flat:
            description = _FLAT_DAMAGE_RE.sub(shift_flat, description)
        action["description"] = description
        # The structured copy moves with the prose (spec 2026-09-11): the
        # auditor reads the parts when they exist, so a part left behind
        # would silently undo this shift.
        if to_hit and type(action.get("to_hit")) is int:
            action["to_hit"] = action["to_hit"] + delta
        parts = action.get("damage")
        if flat and isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and type(part.get("bonus")) is int and part["bonus"]:
                    part["bonus"] = part["bonus"] + delta

    # The shift changed the dice' sum, so every "35 (5d10 + 6)" pair now
    # states an average its own brackets contradict. Recomputed with the
    # auditor's own parser — the number the DM reads is the number the
    # auditor sees. The structured parts' averages are recomputed the same
    # way, from their own count/sides/bonus.
    for action in actions:
        if not isinstance(action, dict):
            continue
        description = action.get("description")
        if isinstance(description, str):
            action["description"] = _DAMAGE_PAIR_RE.sub(_recompute_average, description)
        parts = action.get("damage")
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                count, sides, bonus = part.get("count"), part.get("sides"), part.get("bonus")
                if type(count) is int and type(sides) is int:
                    part["average"] = round(
                        count * (sides + 1) / 2 + (bonus if type(bonus) is int else 0), 2
                    )


def _recompute_average(match: re.Match[str]) -> str:
    """Rewrite one damage pair's leading average from its own dice."""
    average, sources = combat.parse_damage_expression(match.group(1))
    if not sources:
        return match.group(0)
    return f"{average:g} ({match.group(1)})"


@dataclass(frozen=True)
class _NamedAction:
    """The audited action the rider clause is appended to (its name only)."""

    name: str


def conform_power(block: Any) -> dict[str, Any] | None:
    """Rewrite a block's NUMBERS so the DMG row for its challenge is met.

    The model writes the block; a model cannot be asked to hit an interval
    reliably — the one LLM repair pass measurably does not converge
    (2026-09-10 live jobs died at 5.5 vs 15-20, 16 vs 27-32, 50 vs 93-98).
    This is the deterministic half: identity, prose and the action list are
    untouched; only the round's damage numbers and a frail HP floor move,
    and only until the SHIPPED auditor agrees. The marginal multiplier is
    measured from the auditor itself rather than re-derived, so a Multiattack
    routine, a legendary budget, or a save-for-half adjustment is all
    accounted for without this function knowing about any of them.

    Returns the conformed block, or ``None`` when it cannot be conformed —
    no reference band for the declared challenge, no damaging action to
    carry the budget, or damage ABOVE the band (trimming an over-powered
    block is deliberately out of scope: the model's own repair pass may
    lower the declared challenge instead).
    """
    if not isinstance(block, dict):
        return None
    identity = block.get("identity")
    if not isinstance(identity, dict):
        return None
    band = combat.expected_band(identity)
    if band is None:
        return None
    low, high = band

    conformed: dict[str, Any] = json.loads(json.dumps(block))  # JSON-safe deep copy
    tier = _tier_of(identity)

    # AC cannot move the auditor's DPR, so the tier floor is safe here.
    combat_block = conformed.get("combat")
    if tier is not None and isinstance(combat_block, dict):
        ac = combat_block.get("ac")
        if isinstance(ac, int) and ac < _CONFORM_AC[tier]:
            combat_block["ac"] = _CONFORM_AC[tier]

    hp_band = combat.hp_band(identity)
    if isinstance(combat_block, dict) and hp_band is not None:
        hp = combat_block.get("hp")
        if isinstance(hp, int) and combat.is_hp_frail(hp, hp_band):
            combat_block["hp"] = hp_band[0]

    # Scores lift on EVERY path — a level-17 character reading STR 16 is the
    # complaint, not a detail. The to-hit moves with them; the flat damage
    # does not, because that is the half that moves DPR and an in-band block
    # must not be pushed out of it by a cosmetic lift.
    delta = _conform_ability_scores(conformed, tier) if tier is not None else 0
    if delta:
        _shift_derived_numbers(conformed, delta, flat=False)

    audit = combat.audit_stat_block(conformed)
    # The tolerance here is the VALIDATOR's, not the raw band: a block at
    # 134 against an 111-116 row is on-target (over fires past 1.2x high),
    # and bailing on it left such blocks with the model's own scores — the
    # exact complaint this lift exists to answer.
    if audit.band is None or audit.dpr > high * combat._OVER_RATIO:
        return None
    if audit.dpr >= low * combat._UNDER_RATIO:
        return conformed if not validate_stat_block(conformed) else None

    # Under-powered: the flat damage may follow the scores, but only while
    # the band still has room for it.
    if delta:
        before_flat = json.loads(json.dumps(conformed))
        _shift_derived_numbers(conformed, delta, to_hit=False, flat=True)
        lifted = combat.audit_stat_block(conformed)
        if lifted.band is None or lifted.dpr > high:
            conformed = before_flat
        audit = combat.audit_stat_block(conformed)

    damaging = [a for a in audit.actions if a.expected_avg > 0]
    if damaging:
        strongest_name = max(damaging, key=lambda a: a.expected_avg).name
    else:
        # Nothing to scale — the block states no dice at all. Arm the first
        # attack-shaped action that is NOT the routine: a Multiattack
        # contributes its count times another action, so arming it alone
        # would change nothing (round_dpr excludes the routine itself).
        strongest_name = ""
        for action in conformed.get("actions") or []:
            if not isinstance(action, dict):
                continue
            name = action.get("name")
            if isinstance(name, str) and combat._is_multiattack_routine(name):
                continue
            if combat.is_attack_shaped(name, action.get("description")):
                strongest_name = name if isinstance(name, str) else ""
                break
        if not strongest_name:
            return None
    strongest = _NamedAction(strongest_name)

    klass = identity.get("class")
    damage_type = _CONFORM_DAMAGE_TYPES.get(klass or "", _DEFAULT_CONFORM_DAMAGE)

    target = (low + high) / 2
    for _round in range(_CONFORM_MAX_ROUNDS):
        before = combat.audit_stat_block(conformed).dpr
        if before >= low:
            break
        probe_count, probe_sides, probe_avg = _rider_dice(10.0)
        probe = f"plus {probe_avg:g} ({probe_count}d{probe_sides}) {damage_type} damage."
        probe_part = _damage_part(probe_count, probe_sides, probe_avg, damage_type)
        if not _append_damage_clause(conformed, strongest.name, probe, probe_part):
            return None
        after = combat.audit_stat_block(conformed).dpr
        multiplier = (after - before) / probe_avg
        if multiplier <= 0:
            return None
        need = (target - before) / multiplier
        # Undo the probe by rewriting the clause we just appended (and the
        # part appended with it).
        actions = conformed.get("actions")
        assert isinstance(actions, list)
        replaced = False
        for action in actions:
            if not isinstance(action, dict) or action.get("name") != strongest.name:
                continue
            description = action.get("description")
            if isinstance(description, str) and description.endswith(probe):
                count, sides, average = _rider_dice(need)
                action["description"] = description[: -len(probe)] + (
                    f"plus {average:g} ({count}d{sides}) {damage_type} damage."
                )
                parts = action.get("damage")
                if isinstance(parts, list) and parts and parts[-1] == probe_part:
                    parts[-1] = _damage_part(count, sides, average, damage_type)
                replaced = True
            break
        if not replaced:
            return None

    final = combat.audit_stat_block(conformed)
    if final.band is None or not (low <= final.dpr <= high):
        return None
    return conformed if not validate_stat_block(conformed) else None


def _conform_positions(
    entities: Sequence[models.EntityInput], targets: Sequence[StatIssue]
) -> list[models.EntityInput]:
    """Conform the blocks at ``targets``, leaving every other entity alone."""
    fixed: dict[int, dict[str, Any]] = {}
    for issue in targets:
        block = (issue.entity.data or {}).get("stat_block")
        conformed = conform_power(block)
        if conformed is not None:
            fixed[issue.position] = conformed
    if not fixed:
        return list(entities)
    out: list[models.EntityInput] = []
    for position, entity in enumerate(entities):
        block = fixed.get(position)
        if block is None or entity.kind != "character":
            out.append(entity)
            continue
        out.append(dataclasses.replace(entity, data={**entity.data, "stat_block": block}))
    return out


def conform_stat_power(
    entities: Sequence[models.EntityInput], issues: Sequence[StatIssue]
) -> list[models.EntityInput]:
    """Apply :func:`conform_power` to every conformable flagged character.

    Entities whose violations are shape problems are left exactly as the
    repair pass left them — this pass never touches anything but numbers.
    """
    return _conform_positions(
        entities, [issue for issue in issues if is_conformable(issue.violations)]
    )


def _damage_part(count: int, sides: int, average: float, damage_type: str) -> dict[str, Any]:
    """One canonical structured damage part (spec 2026-09-11) — the shape
    the auditor, the character sheet and the export read, matching the
    ``damage[]`` entry the prompt asks the model for."""
    return {
        "dice": f"{count}d{sides}",
        "count": count,
        "sides": sides,
        "bonus": 0,
        "average": round(average, 2),
        "type": damage_type,
    }


def damage_parts_sentence(damage: Any) -> str | None:
    """The one-line damage sentence a structured ``damage`` list implies
    (spec: structured attack damage, 2026-09-11), e.g. ``"Hit: 19 (2d6 +
    12) slashing damage plus 16.5 (3d10) radiant damage."`` — or ``None``
    when the list names no usable part. Used where a text form is wanted
    (the Forge unit-card entry, the sheet) so the numbers the DM reads
    come from the parts rather than from a re-parse of the prose."""
    if not isinstance(damage, list):
        return None
    clauses: list[str] = []
    for part in damage:
        if not isinstance(part, dict):
            continue
        count, sides, bonus = part.get("count"), part.get("sides"), part.get("bonus")
        if type(count) is not int or type(sides) is not int:
            continue
        bonus = bonus if type(bonus) is int else 0
        average = part.get("average")
        average = (
            float(average)
            if isinstance(average, (int, float)) and not isinstance(average, bool)
            else count * (sides + 1) / 2 + bonus
        )
        kind = part.get("type")
        kind = kind.strip() if isinstance(kind, str) and kind.strip() else "damage"
        flat = f" + {bonus}" if bonus else ""
        clauses.append(f"{average:g} ({count}d{sides}{flat}) {kind}")
    if not clauses:
        return None
    head, *tail = clauses
    sentence = f"Hit: {head} damage"
    for clause in tail:
        sentence += f" plus {clause} damage"
    return f"{sentence}."


def canonicalize_stat_blocks(
    entities: Sequence[models.EntityInput],
) -> list[models.EntityInput]:
    """Fold a model's near-miss stat-block shape into the canonical one.

    Spec: structured attack damage and the missing stat aspects
    (2026-09-11). Returns each entity with its block canonicalized — a
    block already in canonical form comes back byte-identical (no copy),
    so an untouched wave is untouched. See
    :func:`canonicalize_stat_block` for the folds themselves.
    """
    out: list[models.EntityInput] = []
    for entity in entities:
        block = (entity.data or {}).get("stat_block")
        if not isinstance(block, dict):
            out.append(entity)
            continue
        canonical = canonicalize_stat_block(block)
        out.append(
            entity
            if canonical is block
            else dataclasses.replace(entity, data={**entity.data, "stat_block": canonical})
        )
    return out


def canonicalize_stat_block(block: dict[str, Any]) -> dict[str, Any]:
    """The canonical form of one stat block (see the three folds below).

    Shared by the build-in/regenerate gate and the generate staging path,
    so a candidate and a committed key figure store the same shape.
    Returns the SAME object when nothing needed folding; otherwise a new
    block (the input is never mutated).
    """
    folded = _fold_stat_block_aliases(block)
    completed = _complete_damage_parts(folded)
    return _fold_string_damage(completed)


#: ``stats`` members that move to a top-level key of the same meaning
#: (``abilities`` is the one rename — prototype-2 calls the six scores
#: ``abilities``, the AR25 contract calls them ``attributes``).
_STATS_MEMBER_MAP: dict[str, str] = {
    "abilities": "attributes",
    "combat": "combat",
    "saves": "saves",
    "initiative": "initiative",
    "passive_perception": "passive_perception",
    "proficiency_bonus": "proficiency_bonus",
    "spellcasting": "spellcasting",
    "resources": "resources",
}


def _fold_stat_block_aliases(block: dict[str, Any]) -> dict[str, Any]:
    """Fold the prototype-2 nesting into the canonical flat block.

    A ``stats`` object folds its members out to their own top-level keys —
    filling only slots that are ABSENT, so a canonical field always wins.
    Same spirit as ``canonicalize_entity_kind``: repair the shape the model
    certainly meant, never guess at one it did not. (``features`` is NOT
    folded into ``traits``: prototype-2's features are bare names, the AR25
    traits entries are ``{name, description}`` objects — folding would
    invent descriptions, so ``features`` is its own optional field.)
    Unknown keys are never dropped.
    """
    filled: dict[str, Any] = {}
    stats = block.get("stats")
    if isinstance(stats, dict):
        for member, key in _STATS_MEMBER_MAP.items():
            if member in stats and (key not in block or block.get(key) is None):
                filled[key] = stats[member]
        hit_dice = stats.get("combat")
        if isinstance(hit_dice, dict):
            dice = hit_dice.get("hit_dice")
            combat_block = block.get("combat")
            if (
                dice is not None
                and isinstance(combat_block, dict)
                and combat_block.get("hit_dice") is None
            ):
                filled["combat"] = {**combat_block, "hit_dice": dice}
    if not filled:
        return block
    return {**block, **filled}


#: The slots a damage part is completed into.
_PART_KEYS: tuple[str, ...] = ("dice", "count", "sides", "bonus", "average", "type")


def _complete_damage_parts(block: dict[str, Any]) -> dict[str, Any]:
    """Complete every structured damage part from its own ``dice`` text.

    ``{"dice": "2d6", "bonus": 12}`` becomes ``{"dice": "2d6", "count": 2,
    "sides": 6, "bonus": 12, "average": 19, "type": ...}`` — the explicit
    numbers the auditor, sheet and export read. A part that is already
    complete is left byte-identical; a part with no derivable dice is left
    for the validator to flag (never silently dropped).
    """
    actions = block.get("actions")
    if not isinstance(actions, list):
        return block
    changed = False
    new_actions: list[Any] = []
    for action in actions:
        if not isinstance(action, dict):
            new_actions.append(action)
            continue
        parts = action.get("damage")
        if not isinstance(parts, list) or not parts:
            new_actions.append(action)
            continue
        new_parts: list[Any] = []
        for part in parts:
            if not isinstance(part, dict):
                new_parts.append(part)
                continue
            dice = part.get("dice")
            count, sides = part.get("count"), part.get("sides")
            if isinstance(dice, str):
                found = combat._DICE_RE.findall(dice)
                if found and not (type(count) is int and type(sides) is int):
                    count, sides = int(found[0][0]), int(found[0][1])
            if type(count) is not int or type(sides) is not int:
                new_parts.append(part)
                continue
            bonus = part.get("bonus")
            bonus = bonus if type(bonus) is int else 0
            completed: dict[str, Any] = {
                "dice": f"{count}d{sides}",
                "count": count,
                "sides": sides,
                "bonus": bonus,
                "average": round(count * (sides + 1) / 2 + bonus, 2),
                "type": part.get("type") if isinstance(part.get("type"), str) else "untyped",
            }
            extra = {key: value for key, value in part.items() if key not in _PART_KEYS}
            merged = {**extra, **completed}
            if merged != part:
                changed = True
            new_parts.append(merged)
        new_actions.append({**action, "damage": new_parts})
    if not changed:
        return block
    return {**block, "actions": new_actions}


def _fold_string_damage(block: dict[str, Any]) -> dict[str, Any]:
    """Fold a non-standard STRING action ``damage`` into its description.

    Measured 2026-09-11 (regenerate, level 17): the model shipped
    ``{"name": "Holy Smite", "damage": "5d10+6 radiant", "description":
    "deals massive radiant damage"}`` — the dice sit in a key the auditor
    never reads, so the block reports ZERO damage, the non-combatant
    exemption swallows it whole, and it ships with no usable offence (and no
    damage at all on the exported Forge token). The contract's damage is
    either a string folded into the description or a structured list; a
    string with dice in it is unambiguous, so it moves into the prose.
    """
    actions = block.get("actions")
    if not isinstance(actions, list):
        return block
    changed = False
    new_actions: list[Any] = []
    for action in actions:
        if not isinstance(action, dict):
            new_actions.append(action)
            continue
        damage = action.get("damage")
        description = action.get("description")
        if (
            not isinstance(damage, str)
            or not combat._DICE_RE.search(damage)
            or (isinstance(description, str) and combat._DICE_RE.search(description))
        ):
            new_actions.append(action)
            continue
        head = description.strip() if isinstance(description, str) else ""
        sentence = f"Hit: {damage.strip().rstrip('.')}."
        new_actions.append({**action, "description": f"{head} {sentence}".strip()})
        changed = True
    if not changed:
        return block
    return {**block, "actions": new_actions}


def canonicalize_action_damage(
    entities: Sequence[models.EntityInput],
) -> list[models.EntityInput]:
    """Entity-level wrapper for the string-damage fold (see
    :func:`_fold_string_damage`): the rule the regenerate path shipped
    with, kept as its own name because that is what it was pinned as."""
    out: list[models.EntityInput] = []
    for entity in entities:
        block = (entity.data or {}).get("stat_block")
        if not isinstance(block, dict):
            out.append(entity)
            continue
        folded = _fold_string_damage(block)
        out.append(
            entity
            if folded is block
            else dataclasses.replace(entity, data={**entity.data, "stat_block": folded})
        )
    return out


def stat_failure_message(issues: Sequence[StatIssue]) -> str:
    """The fail-event message for a wave still invalid after the repair
    passes.

    Names each character (canonical ref + name) and its violations, so
    the DM and the job log see exactly what to fix (AR25).
    """
    parts = [
        f"E{issue.position} ({issue.entity.name!r}): {'; '.join(issue.violations)}"
        for issue in issues
    ]
    return "wave 1: stat block(s) still invalid after the repair passes: " + " | ".join(parts)


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
