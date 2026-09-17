"""Combat-power audit for AR25 stat blocks — how hard an entity hits, and
whether that matches its declared challenge (under/over-powered).

The DM hands the pipeline a minimal stat block (``data.stat_block`` —
the AR24/AR25 shape). This module answers their question directly: parse
each action's damage expression out of its prose description, estimate
the expected per-round damage output the DMG-style method would yield,
and compare that to the published "Monster Statistics by Challenge
Rating" damage/round band for the declared CR (5e 2014 DMG).

Pure and deterministic (AD-16), like ``knowledge``: a function of the
stat block dict alone — no ids, timestamps, or job state. The audit feeds
``validate_stat_block`` (``knowledge``): damage/round below the band
(under-powered) and HP below half the band low (frail) are violations
there — a repair nudge, then a fail. Damage/round above the band commits
with a ``power`` annotation instead (owner verdict 2026-09-12: the DM is
told, not protected). The CLI below stays a read-only assist surface for
the DM.
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
    "1/8": (2.0, 3.0),
    "1/4": (4.0, 5.0),
    "1/2": (6.0, 8.0),
    1: (9.0, 14.0),
    2: (15.0, 20.0),
    3: (21.0, 26.0),
    4: (27.0, 32.0),
    5: (33.0, 38.0),
    6: (39.0, 44.0),
    7: (45.0, 50.0),
    8: (51.0, 56.0),
    9: (57.0, 62.0),
    10: (63.0, 68.0),
    11: (69.0, 74.0),
    12: (75.0, 80.0),
    13: (81.0, 86.0),
    14: (87.0, 92.0),
    15: (93.0, 98.0),
    16: (99.0, 104.0),
    17: (105.0, 110.0),
    18: (111.0, 116.0),
    19: (117.0, 122.0),
    20: (123.0, 140.0),
    21: (141.0, 158.0),
    22: (159.0, 176.0),
    23: (177.0, 194.0),
    24: (195.0, 212.0),
    25: (213.0, 230.0),
    26: (231.0, 248.0),
    27: (249.0, 266.0),
    28: (267.0, 284.0),
    29: (285.0, 302.0),
    # The DMG row is open-ended ("303+"): encoded as a point band so the
    # gap math stays finite. Under-detection keys on the 303 low edge
    # (the enforcement target); over fires above 1.2 * 303.
    30: (303.0, 303.0),
}
CR_DPR: MappingProxyType[Any, tuple[float, float]] = MappingProxyType(_CR_DPR)

#: Expected Hit Points (HP) band per Challenge Rating — 5e 2014 DMG,
#: "Monster Statistics by Challenge Rating", same keys as ``_CR_DPR``.
#: Only the low edge enforces: HP below half the band low is "frail" (a
#: glass-jaw block at a serious challenge); HP above the band never fails
#: (defensive averaging across AC/saves/traits is not a bug). CR 30 is
#: open-ended ("566+") and encoded as a point like the DPR row — only its
#: low edge is ever read.
_CR_HP: dict[Any, tuple[int, int]] = {
    0: (1, 6),
    "1/8": (7, 35),
    "1/4": (36, 49),
    "1/2": (50, 70),
    1: (71, 85),
    2: (86, 100),
    3: (101, 115),
    4: (116, 130),
    5: (131, 145),
    6: (146, 160),
    7: (161, 175),
    8: (176, 190),
    9: (191, 205),
    10: (206, 220),
    11: (221, 235),
    12: (236, 250),
    13: (251, 265),
    14: (266, 280),
    15: (281, 295),
    16: (296, 310),
    17: (311, 325),
    18: (326, 340),
    19: (341, 355),
    20: (356, 375),
    21: (376, 400),
    22: (401, 425),
    23: (426, 445),
    24: (446, 465),
    25: (466, 485),
    26: (486, 505),
    27: (506, 525),
    28: (526, 545),
    29: (546, 565),
    30: (566, 566),
}
CR_HP: MappingProxyType[Any, tuple[int, int]] = MappingProxyType(_CR_HP)


#: Expected DPR band for NPC/BBEG blocks — the CLASS-GRADE envelope, keyed
#: by ``identity.level``. Owner feedback 2026-09-17 ("still way
#: underpowered" against the monster row): a class-grade level-20 caster
#: with a 3-beam blast sits near 40 DPR and can NEVER honestly reach the
#: DMG monster row (123-140), so stamping NPCs against that table made
#: the under-powered verdict structurally un-passable. Derived from the
#: monster table instead of a second hand-tuned table — a level's
#: class-grade floor is a caster's routine (the anchors taught in the
#: prompt: a full caster deals roughly half the martial figure) and its
#: ceiling is a martial routine: low = 0.2 x monster low, high = 0.6 x
#: monster high. Level 20 lands at (25, 84) — caster floor ~25, martial
#: ceiling ~80 as anchored. Int keys only: NPCs key on level, and the
#: fractional CR rows are monster keys.
_NPC_DPR: dict[int, tuple[int, int]] = {
    key: (round(0.2 * low), round(0.6 * high))
    for key, (low, high) in _CR_DPR.items()
    if type(key) is int
}
NPC_DPR: MappingProxyType[Any, tuple[float, float]] = MappingProxyType(_NPC_DPR)
#: Fractional CR keys ("1/8", "1/4", "1/2") — derived from the DPR table
#: so the band lookups share one exact-type rule (bools and floats never
#: hit: ``True == 1`` and ``5.0 == 5`` must both abstain).
_FRACTIONAL_CR: frozenset[Any] = frozenset(k for k in _CR_DPR if isinstance(k, str))

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
# read the +1 as a +1 flat — the guard fires only when the d/D is itself
# followed by a digit, so "+6 damage" keeps its +6 while "+1d4" does not).
# This keeps a lone "+2 AC" out of the damage total — the sign must trail
# a die to count as a weapon damage modifier.
_MOD_RE = re.compile(r"(?<=\d)\s*([+-])\s*(\d+)(?!\s*[dD]\d)")
#: A recharge range ("recharge 5-6") is an activation rule, never damage:
#: stripped before parsing so the range's upper bound never reads as a
#: flat modifier ("-6"). Limited-use detection (``_RECHARGE_RE``) runs on
#: the raw prose separately, so nothing about the 1/3 averaging is lost.
_RECHARGE_RANGE_RE = re.compile(r"recharge \d+(?:\s*-\s*\d+)?", re.IGNORECASE)
#: A save-for-half effect: both the save and the half clause appear in
#: the action's prose (may span sentences — e.g. "make a CON save …
#: takes half damage on a success"). Exposed on ActionDamage so a DM can
#: weigh it.
_SAVE_HALF_RE = re.compile(r"\bsave\b[\s\S]*?\bhalf\b|\bhalf\b[\s\S]*?\bsave\b", re.IGNORECASE)
#: A multi-target ("area-of-effect") attack, assumed to catch 2 targets
#: per the DMG's Damage/Round method.
_AOE_RE = re.compile(r"\b(cone|radius|line|sphere|cube|area|within|b?urst)\b", re.IGNORECASE)
#: A limited-use effect (recharge / per-day): the DMG counts such an
#: action once across the 3-round DPR window, so its expectation is
#: averaged over 3 rounds. Exposed on ActionDamage like save-half/AoE.
_RECHARGE_RE = re.compile(r"\brecharge\b|\bper day\b|\d+\s*/\s*day\b", re.IGNORECASE)

#: Number words a Multiattack routine uses ("makes three Claw attacks",
#: "can attack twice"). ``twice`` is the common prose form for 2.
_NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twice": 2,
}
#: The attack count of a Multiattack routine: the first integer or number
_MULTIATTACK_COUNT_RE = re.compile(
    r"(?P<digits>\d+)|(?P<word>\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twice)\b)",
    re.IGNORECASE,
)
#: Prose that marks an action as an ATTACK — the signal that zero parseable
#: damage is a defect rather than a non-combatant. Deliberately narrower than
#: "hit": "restores 20 hit points" is not an attack. "on a hit" and "hit:"
#: are the 5e phrasings the model reaches for when it drops the dice.
_ATTACK_HINT_RE = re.compile(
    r"to hit|attack roll|attack:|melee attack|ranged attack|weapon attack"
    r"|on a hit|on hit|hit:",
    re.IGNORECASE,
)


def is_attack_shaped(name: object, description: object) -> bool:
    """Whether an action reads like an attack.

    Measured 2026-09-11: a level-17 paladin came back with
    ``{"name": "Holy Smite", "description": "…makes one melee attack. On a
    hit, it deals massive radiant damage."}`` — no dice anywhere, so the
    audit read ZERO damage and the non-combatant exemption passed the block
    whole. A creature that has a Multiattack is never a non-combatant.
    """
    if isinstance(name, str) and _is_multiattack_routine(name):
        return True
    return isinstance(description, str) and _ATTACK_HINT_RE.search(description) is not None


#: Noise stripped before the routine-count scan: dice expressions and
#: signed numbers, so a to-hit bonus ("+5 to hit, makes Claw attacks")
#: or a damage parenthetical ("makes three attacks (3d6)") never reads
#: as the attack count. The bare count itself carries no sign and no
#: die letter, so it always survives the strip.
_COUNT_NOISE_RE = re.compile(r"\d+[dD]\d+|[+-]\d+")
#: Absurd counts ("makes 100 attacks") must not detonate DPR into a
#: false over-powered verdict; real routines top out well below this.
_MAX_ROUTINE_ATTACKS = 10


def _is_multiattack_routine(name: str) -> bool:
    """Name-matched Multiattack routine, space-insensitive ("Multi
    Attack" counts; "Extra Attack"/"Triple Claw" deliberately do not —
    different mechanics, no evidence models emit them)."""
    return "multiattack" in name.lower().replace(" ", "")


#: A routine the model gave a FLAVOR name ("Eldritch Torrent") with the
#: mechanics only in the prose — measured 2026-09-17 (fasiha, level 20):
#: "Multiattack: fasiha makes three Eldritch Blast attacks" read as a
#: plain action, so the x3 blast multiplier never fired and the block
#: audited at one beam's damage (13.5 instead of 40.5).
_ROUTINE_LEAD_RE = re.compile(r"\bmultiattack\b", re.IGNORECASE)


def _is_routine_action(name: str, text: str | None) -> bool:
    """Whether an action is the Multiattack routine: by name, or by the
    description carrying the mechanics the name should have had."""
    return _is_multiattack_routine(name) or (
        isinstance(text, str) and _ROUTINE_LEAD_RE.search(text) is not None
    )


def parse_multiattack_count(text: str) -> int:
    """How many attacks a Multiattack routine makes: first integer /
    number-word / "twice" = 2 in the noise-stripped prose, else 2,
    clamped to ``_MAX_ROUTINE_ATTACKS``. Crude by decision."""
    scrubbed = _COUNT_NOISE_RE.sub(" ", text or "")
    match = _MULTIATTACK_COUNT_RE.search(scrubbed)
    if match is None:
        return 2
    if match.group("digits") is not None:
        return min(int(match.group("digits")), _MAX_ROUTINE_ATTACKS)
    return _NUMBER_WORDS[match.group("word").lower()]


@dataclass(frozen=True)
class ActionDamage:
    """The parsed damage of one action and the DMG-derived expectation.

    ``nominal_avg`` is the plain sum of the action's damage dice and flat
    modifiers. ``expected_avg`` folds in the DMG assumptions this module
    makes to place the action on the CR scale: a saving-throw that halves
    on success averages 75% (single-target), a multi-target
    ("area-of-effect") action is assumed to catch 2 targets, and a
    limited-use action (recharge / per-day) is averaged over 3 rounds —
    the same three rules the DMG uses for its Damage/Round figure.
    """

    name: str
    nominal_avg: float
    expected_avg: float
    save_half: bool
    aoe: bool
    limited_use: bool
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
    text = _RECHARGE_RANGE_RE.sub(" ", text)
    for count_s, sides_s in _DICE_RE.findall(text):
        count, sides = int(count_s), int(sides_s)
        total += count * _die_avg(sides)
        sources.append(f"{count}d{sides}")
    for sign, mag_s in _MOD_RE.findall(text):
        mag = int(mag_s)
        total += mag if sign == "+" else -mag
        sources.append(f"{sign} {mag}")
    return total, tuple(sources)


def structured_damage_totals(damage: Any) -> tuple[float, tuple[str, ...]] | None:
    """Sum an action's structured ``damage`` parts (spec 2026-09-11).

    Returns ``(nominal_avg, sources)`` in the same shape as
    :func:`parse_damage_expression`, or ``None`` when the parts are absent
    or unusable — the caller then falls back to the description, so a
    malformed list can never turn a real attack into zero damage.

    A part counts when it carries ``average`` (the model's own figure) or,
    failing that, a dice expression / ``count``+``sides`` pair; a part
    with an ``average`` still contributes its exact number, which is what
    the DM reads on the sheet.
    """
    if not isinstance(damage, list) or not damage:
        return None
    total = 0.0
    sources: list[str] = []
    for part in damage:
        if not isinstance(part, dict):
            continue
        average = part.get("average")
        dice = part.get("dice")
        count, sides = part.get("count"), part.get("sides")
        bonus = part.get("bonus")
        bonus = bonus if type(bonus) is int else 0
        piece: float | None = None
        label: str | None = None
        if isinstance(average, (int, float)) and not isinstance(average, bool):
            piece = float(average)
            label = dice if isinstance(dice, str) and dice.strip() else f"{piece:g}"
        elif isinstance(dice, str) and _DICE_RE.search(dice):
            count_s, sides_s = _DICE_RE.findall(dice)[0]
            piece = int(count_s) * _die_avg(int(sides_s)) + bonus
            label = f"{int(count_s)}d{int(sides_s)}"
        elif type(count) is int and type(sides) is int and count >= 1 and sides >= 2:
            piece = count * _die_avg(sides) + bonus
            label = f"{count}d{sides}"
        if piece is None or label is None:
            continue
        total += piece
        sources.append(label)
    if not sources:
        return None
    return total, tuple(sources)


def analyze_action(name: str, description: str | None, damage: Any = None) -> ActionDamage:
    """Compute an action's nominal and DMG-adjusted expected damage.

    Structured ``damage`` parts win when they are present and usable
    (spec 2026-09-11: the numbers the model stated explicitly, rather than
    the ones re-parsed out of its prose); the description remains the
    fallback AND the source of the modifier flags below, which are
    expressed in words ("DC 15 Dexterity save", "recharge 5-6", "in a
    30-foot cone").

    Assumptions (documented, DMG 2014): a save-for-half effect lands at
    75% of nominal; an area-of-effect action (cone/radius/line/etc.) is
    assumed to hit 2 targets; a limited-use action (recharge / per-day)
    is averaged over 3 rounds. None is guessed silently — all are
    exposed on the returned ``ActionDamage`` so a DM can see the model.
    """
    text = description or ""
    parts = structured_damage_totals(damage)
    nominal, sources = parts if parts is not None else parse_damage_expression(text)
    save_half = _SAVE_HALF_RE.search(text) is not None
    aoe = _AOE_RE.search(text) is not None
    limited_use = _RECHARGE_RE.search(text) is not None
    expected = nominal
    if save_half:
        expected *= 0.75
    if aoe:
        expected *= 2.0
    if limited_use:
        expected /= 3.0
    return ActionDamage(name, nominal, expected, save_half, aoe, limited_use, sources)


def round_dpr(
    actions: Sequence[ActionDamage],
    legendary: bool,
    multiattack_count: int = 0,
    multiattack_targets: Sequence[str] = (),
) -> float:
    """Estimate the creature's expected Damage/Round.

    A creature that can act freely uses its strongest damaging action as
    its attack action each round; a Multiattack routine instead contributes
    its count times the strongest *other* damaging action (0 when the
    routine names no damaging attack — the routine itself, matched by name,
    is never its own multiplier). When the routine's prose names exactly
    ONE other damaging action (the repeated-attack idiom — "makes three
    Eldritch Blast attacks"), the count multiplies THAT action instead of
    the strongest: an area effect beside the routine would otherwise
    inflate the round into a false over-powered verdict. Routines naming
    several actions keep the strongest-other approximation (the classic
    "one bite, two claws" cannot be resolved from a count alone). The DMG
    DPR figure also includes the legendary-action budget: one extra
    action-equivalent when ``boss.legendary_actions`` is present (see
    ``LEGENDARY_EXTRA_ACTIONS``). Lair actions are deliberately not
    counted — the DMG keeps lair effects out of the Damage/Round line.
    """
    if not actions:
        return 0.0
    strongest = max(action.expected_avg for action in actions)
    if multiattack_count:
        others = [action for action in actions if not _is_multiattack_routine(action.name)]
        others_best = max((action.expected_avg for action in others), default=0.0)
        if multiattack_targets:
            named = [
                action
                for action in others
                if action.name in multiattack_targets and action.expected_avg > 0
            ]
            if len(named) == 1:
                others_best = named[0].expected_avg
        base = multiattack_count * others_best
        extra = others_best * LEGENDARY_EXTRA_ACTIONS if legendary else 0.0
        return base + extra
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

    Monsters key on ``identity.cr`` against the DMG monster table;
    NPC/BBEG key on ``identity.level`` against the CLASS-GRADE envelope
    (``NPC_DPR`` — a caster's floor to a martial's ceiling, owner
    feedback 2026-09-17: the monster row made the under verdict
    structurally un-passable for characters). Returns ``None`` for a
    challenge outside the reference data (an unset or out-of-range
    level/CR).
    """
    role = identity.get("role")
    key = identity.get("cr") if role == "Monster" else identity.get("level")
    if type(key) is not int and key not in _FRACTIONAL_CR:
        return None
    return (CR_DPR if role == "Monster" else NPC_DPR).get(key)


def hp_band(identity: dict[str, Any]) -> tuple[int, int] | None:
    """The expected HP band for a stat block's declared challenge (same
    key rule as ``expected_band``; ``None`` outside the reference). Only
    the low edge enforces — see ``is_hp_frail``."""
    role = identity.get("role")
    key = identity.get("cr") if role == "Monster" else identity.get("level")
    if type(key) is not int and key not in _FRACTIONAL_CR:
        return None
    return CR_HP.get(key)


def is_hp_frail(hp: int, band: tuple[int, int]) -> bool:
    """Whether HP is frail for its band: below half the band low. Fails
    low-only — HP at or above the band never fails."""
    return hp < 0.5 * band[0]


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
    multiattack: int | None = None
    routine_text: str | None = None
    for action in raw_actions:
        if not isinstance(action, dict):
            continue
        name = action.get("name")
        name = name if isinstance(name, str) and name.strip() else "(unnamed action)"
        description = action.get("description")
        text = description if isinstance(description, str) else ""
        actions.append(analyze_action(name, text, action.get("damage")))
        if multiattack is None and _is_routine_action(name, text):
            multiattack = parse_multiattack_count(text)
            routine_text = text

    # The repeated-attack idiom: when the routine prose names exactly one
    # other damaging action, the count multiplies THAT action (see
    # round_dpr) instead of the blanket strongest-other.
    targets: tuple[str, ...] = ()
    if multiattack and routine_text:
        scrubbed = routine_text.lower()
        named = [
            action.name
            for action in actions
            if not _is_multiattack_routine(action.name)
            and action.expected_avg > 0
            and action.name.lower() in scrubbed
        ]
        if len(named) == 1:
            targets = (named[0],)

    boss = stat_block.get("boss")
    boss_legendary = (
        isinstance(boss, dict)
        and isinstance(boss.get("legendary_actions"), str)
        and bool(boss["legendary_actions"].strip())
    )
    dpr = round_dpr(actions, boss_legendary, multiattack or 0, targets)

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
        if role != "Monster" and verdict == VERDICT_OVER:
            # A strong character is never nagged: NPC/BBEG blocks key on
            # the class-grade band, and an over-the-band character is the
            # DM's delight, not a defect (owner direction 2026-09-17,
            # same spirit as the 2026-09-12 over-powered-monster verdict).
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
