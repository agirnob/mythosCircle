"""Combat-power audit for AR25 stat blocks — how hard an entity hits, and
whether that matches its declared challenge (under/over-powered).

The DM hands the pipeline a minimal stat block (``data.stat_block`` —
the AR24/AR25 shape). This module answers their question directly: parse
each action's damage expression out of its prose description, estimate
the expected per-round damage output the DMG-style method would yield,
and compare that to the published "Monster Statistics by Challenge
Rating" damage/round band for the declared CR (5e 2014 DMG).

Pure and deterministic (AD-16), like ``knowledge``: a function of the
stat block dict alone — no ids, timestamps, or job state. This is an
audit/assist surface, NOT validation: it never rejects a block. A combat
audit flags a *power gap* for the DM to weigh; it does not add
constraints to ``validate_stat_block`` (the AR25 contract stays
range/vocabulary only).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

#: Expected Damage/Round (DPR) band per Challenge Rating — 5e 2014 DMG,
#: "Monster Statistics by Challenge Rating" (average over three rounds,
#: best attacks, including legendary actions). Keyed by the canonical CR
#: keys of ``identity.cr`` (ints plus the fractional tiers). Immutable by
#: contract (same reasoning as the SPELLS map in ``knowledge``): the table
#: feeds every audit, so a mutable dict would let one module change the
#: power baseline for everyone.
_CR_DPR: dict[Any, tuple[float, float]] = {
    0: (0.0, 1.0),
    "1/8": (1.0, 2.0),
    "1/4": (3.0, 4.0),
    "1/2": (5.0, 6.0),
    1: (9.0, 10.0),
    2: (15.0, 16.0),
    3: (21.0, 22.0),
    4: (27.0, 28.0),
    5: (33.0, 34.0),
    6: (39.0, 40.0),
    7: (45.0, 46.0),
    8: (51.0, 52.0),
    9: (57.0, 58.0),
    10: (63.0, 64.0),
    11: (69.0, 70.0),
    12: (75.0, 76.0),
    13: (81.0, 82.0),
    14: (87.0, 88.0),
    15: (93.0, 94.0),
    16: (99.0, 100.0),
    17: (105.0, 106.0),
    18: (111.0, 112.0),
    19: (117.0, 118.0),
    20: (123.0, 124.0),
    21: (130.0, 132.0),
    22: (137.0, 139.0),
    23: (144.0, 146.0),
    24: (151.0, 153.0),
    25: (158.0, 160.0),
    26: (165.0, 167.0),
    27: (172.0, 174.0),
    28: (179.0, 181.0),
    29: (186.0, 188.0),
    30: (193.0, 196.0),
}
CR_DPR: MappingProxyType[Any, tuple[float, float]] = MappingProxyType(_CR_DPR)

#: How many extra standard-action-equivalents a legendary-action suite
#: adds to the round budget. a creature spends ~3 legendary actions per
#: round at roughly a third of a standard action each, and the DMG's DPR
#: figure includes that budget — so a ``boss.legendary_actions`` body adds
#: one full standard action's worth. Conservative, transparent, tunable.
LEGENDARY_EXTRA_ACTIONS = 1.0


def _die_avg(sides: int) -> float:
    """Average damage of one die of N sides (1..N uniform): (N + 1) / 2."""
    return (sides + 1) / 2


# Regex fragments shared by the damage parser.
_DICE_RE = re.compile(r"(\d+)[dD](\d+)")  # "4d10" -> count, sides
# Flat damage modifiers: a sign and integer that (a) follow a digit (a
# die's value) and (b) are NOT themselves a die count ("2d8+1d4" must not
# read the +1 as a +1 flat). This keeps a lone "+2 AC" out of the damage
# total — the sign must trail a die to count as a weapon damage modifier.
_MOD_RE = re.compile(r"(?<=\d)\s*([+-])\s*(\d+)(?!\s*[dD])")
#: A save-for-half effect: both the save and the half clause appear in
#: the action's prose (may span sentences — e.g. "make a CON save …
#: takes half damage on a success"). Exposed on ActionDamage so a DM can
#: weigh it.
_SAVE_HALF_RE = re.compile(r"\bsave\b[\s\S]*?\bhalf\b|\bhalf\b[\s\S]*?\bsave\b", re.IGNORECASE)
#: A multi-target ("area-of-effect") attack, assumed to catch 2 targets
#: per the DMG's Damage/Round method.
_AOE_RE = re.compile(r"\b(cone|radius|line|sphere|cube|area|within|b?urst)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ActionDamage:
    """The parsed damage of one action and the DMG-derived expectation.

    ``nominal_avg`` is the plain sum of the action's damage dice and flat
    modifiers. ``expected_avg`` folds in the DMG assumptions this module
    makes to place the action on the CR scale: a saving-throw that halves
    on success averages 75% (single-target), and a multi-target
    ("area-of-effect") action is assumed to catch 2 targets — the same two
    rules the DMG uses for its Damage/Round figure.
    """

    name: str
    nominal_avg: float
    expected_avg: float
    save_half: bool
    aoe: bool
    sources: tuple[str, ...]  # human-readable "4d10" / "+5" pieces found


def parse_damage_expression(text: str) -> tuple[float, tuple[str, ...]]:
    """Sum the average damage an action's description deals.

    Returns ``(nominal_avg, sources)`` where ``sources`` is the ordered
    list of matched damage pieces ("die", "+ 5"). A description with no
    dice and no flat damage yields ``0.0`` and an empty ``sources`` (a
    control/stun effect deals no damage).
    """
    total = 0.0
    sources: list[str] = []
    for count_s, sides_s in _DICE_RE.findall(text):
        count, sides = int(count_s), int(sides_s)
        total += count * _die_avg(sides)
        sources.append(f"{count}d{sides}")
    for sign, mag_s in _MOD_RE.findall(text):
        mag = int(mag_s)
        total += mag if sign == "+" else -mag
        sources.append(f"{sign} {mag}")
    return total, tuple(sources)


def analyze_action(name: str, description: str | None) -> ActionDamage:
    """Compute an action's nominal and DMG-adjusted expected damage.

    Assumptions (documented, DMG 2014): a save-for-half effect lands at
    75% of nominal; an area-of-effect action (cone/radius/line/etc.) is
    assumed to hit 2 targets. Neither is guessed silently — both are
    exposed on the returned ``ActionDamage`` so a DM can see the model.
    """
    text = description or ""
    nominal, sources = parse_damage_expression(text)
    save_half = _SAVE_HALF_RE.search(text) is not None
    aoe = _AOE_RE.search(text) is not None
    expected = nominal
    if save_half:
        expected *= 0.75
    if aoe:
        expected *= 2.0
    return ActionDamage(name, nominal, expected, save_half, aoe, sources)


def round_dpr(actions: Sequence[ActionDamage], legendary: bool) -> float:
    """Estimate the creature's expected Damage/Round.

    A creature that can act freely uses its strongest damaging action as
    its attack action each round; the DMG DPR figure also includes its
    legendary-action budget. With no Multiattack concept in the minimal
    AR25 schema, the round is modeled as: the strongest action, plus one
    extra action-equivalent when ``boss.legendary_actions`` is present
    (see ``LEGENDARY_EXTRA_ACTIONS``). Lair actions are deliberately not
    counted — the DMG keeps lair effects out of the Damage/Round line.
    """
    if not actions:
        return 0.0
    strongest = max(action.expected_avg for action in actions)
    extra = strongest * LEGENDARY_EXTRA_ACTIONS if legendary else 0.0
    return strongest + extra


#: Verdict thresholds beyond the band, as ratios of the band edges. A
#: block lands "under-powered" below ``0.8 * low``, "over-powered" above
#: ``1.2 * high``; anything inside the band, or within 20% of it, is
#: "on-target".
_UNDER_RATIO = 0.8
_OVER_RATIO = 1.2

VERDICT_UNDER = "under-powered"
VERDICT_ONTARGET = "on-target"
VERDICT_OVER = "over-powered"


@dataclass(frozen=True)
class CombatAudit:
    """The full power audit of one stat block.

    ``challenge`` is the declared identity (e.g. ``"CR 14"`` or
    ``"level 5"``). ``band`` is the expected DPR range for that challenge
    (or ``None`` when the declared CR/level is outside the reference).
    ``dpr`` is the estimated per-round output. ``verdict`` is one of the
    VERDICT_* constants; ``gap_pct`` is how far ``dpr`` sits below
    (negative) or above (positive) the band midpoint, for an intuitive read.
    """

    challenge: str
    band: tuple[float, float] | None
    actions: tuple[ActionDamage, ...]
    dpr: float
    verdict: str
    gap_pct: float


def expected_band(identity: dict[str, Any]) -> tuple[float, float] | None:
    """The expected DPR band for a stat block's declared challenge.

    Monsters key on ``identity.cr``; NPC/BBEG key on ``identity.level``
    via a documented approximation (an NPC's level is a fair stand-in for
    its challenge — the same scale the DMG's table spans). Returns ``None``
    for a challenge outside the reference data (an unset or out-of-range
    level/CR).
    """
    role = identity.get("role")
    key = identity.get("cr") if role == "Monster" else identity.get("level")
    return CR_DPR.get(key)


def audit_stat_block(stat_block: Any) -> CombatAudit:
    """Audit an AR25 stat block dict (``data.stat_block``).

    Pure: a function of ``stat_block`` alone. Unknown or malformed blocks
    degrade to a conservative audit (no actions -> zero DPR; the band is
    looked up from the identity when possible) rather than raising.
    """
    if not isinstance(stat_block, dict):
        return CombatAudit("unknown", None, (), 0.0, VERDICT_UNDER, 0.0)
    identity = stat_block.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    role = identity.get("role")
    key = identity.get("cr") if role == "Monster" else identity.get("level")
    challenge = f"CR {key}" if role == "Monster" else f"level {key}"
    band = expected_band(identity)

    raw_actions = stat_block.get("actions")
    raw_actions = raw_actions if isinstance(raw_actions, list) else []
    actions: list[ActionDamage] = []
    for action in raw_actions:
        if not isinstance(action, dict):
            continue
        name = action.get("name")
        name = name if isinstance(name, str) and name.strip() else "(unnamed action)"
        actions.append(analyze_action(name, action.get("description")))

    boss = stat_block.get("boss")
    boss_legendary = (
        isinstance(boss, dict)
        and isinstance(boss.get("legendary_actions"), str)
        and bool(boss["legendary_actions"].strip())
    )
    dpr = round_dpr(actions, boss_legendary)

    if band is None:
        verdict, gap = VERDICT_UNDER, 0.0
    else:
        low, high = band
        mid = (low + high) / 2
        gap = (dpr - mid) / mid * 100 if mid else 0.0
        if dpr < low * _UNDER_RATIO:
            verdict = VERDICT_UNDER
        elif dpr > high * _OVER_RATIO:
            verdict = VERDICT_OVER
        else:
            verdict = VERDICT_ONTARGET

    return CombatAudit(challenge, band, tuple(actions), dpr, verdict, gap)


def _format_audit(audit: CombatAudit) -> str:
    lines = [
        f"challenge:       {audit.challenge}",
        f"expected DPR:    {audit.band if audit.band else 'n/a (outside reference)'}",
        f"estimated DPR:   {audit.dpr:.1f}",
        f"verdict:         {audit.verdict}",
        f"gap vs mid-band: {audit.gap_pct:+.1f}%",
        "actions:",
    ]
    for action in audit.actions:
        flags = []
        if action.save_half:
            flags.append("save-for-half")
        if action.aoe:
            flags.append("aoe")
        flags_txt = f" [{', '.join(flags)}]" if flags else ""
        parts = " + ".join(action.sources) if action.sources else "(no damage)"
        lines.append(
            f"  - {action.name}: {action.nominal_avg:.1f} avg"
            f" -> {action.expected_avg:.1f} expected{flags_txt} [{parts}]"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: audit a stat block from a JSON file (or raw JSON on stdin ``-``).

    Usage: python -m app.pipeline.combat <statblock.json | ->
    Prints a human-readable audit; exits 0.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Audit an AR25 stat block for over/under-power.")
    parser.add_argument("path", help="path to a JSON stat-block file, or '-' for stdin")
    args = parser.parse_args(argv)

    if args.path == "-":
        doc = sys.stdin.read()
    else:
        with open(args.path, encoding="utf-8") as handle:
            doc = handle.read()
    block = json.loads(doc)
    audit = audit_stat_block(block)
    print(_format_audit(audit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
