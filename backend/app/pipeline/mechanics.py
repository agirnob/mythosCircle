"""Code-owned character mechanics: archetype tables + DPR solvers.

Port of the prototype's (``proto-character-builder/gen.py``) tables and
solvers (spec: JSON-schema generation foundation) — the "how does code
know a mage is smart" weight vectors and the dice arithmetic that picks
expressions landing in the DMG band. Words-only model output plus these
tables is the whole builder design; ``build``/``call_model``/``render``
stay in the prototype dir (Never list) and are deliberately excluded.

Pure and deterministic (AD-16): functions of their inputs alone — no
ids, timestamps, or job state. ``rank_abilities`` raises ``ValueError``
on an unknown tag (prototype behavior); callers surface it, never a
commit.
"""

from typing import Any

from app.pipeline.combat import _die_avg

ABILITY = ("str", "dex", "con", "int", "wis", "cha")

#: tag -> ability weight vector (the "how does code know a mage is smart" table)
ARCHETYPE_WEIGHTS: dict[str, dict[str, int]] = {
    "holy_warrior": {"str": 3, "cha": 2, "con": 2, "wis": 1, "dex": 0, "int": -2},
    "leader": {"str": 1, "cha": 3, "con": 1, "wis": 1, "dex": -1, "int": 0},
    "durable": {"str": 1, "cha": 0, "con": 2, "wis": 1, "dex": 0, "int": -1},
    "brute": {"str": 3, "con": 2, "dex": 1, "int": -2, "cha": -1, "wis": -1},
    "lurker": {"dex": 3, "con": 2, "wis": 1, "int": 0, "cha": -1, "str": -2},
    "invisible": {"dex": 2, "con": 1, "cha": -1, "str": -1, "int": 0, "wis": 0},
    "arcane_caster": {"int": 3, "con": 1, "wis": 1, "dex": 0, "cha": 0, "str": -2},
    "divine_caster": {"wis": 3, "cha": 1, "con": 1, "str": 0, "dex": -1, "int": -1},
    "skirmisher": {"dex": 3, "con": 1, "wis": 1, "str": 0, "int": 0, "cha": -1},
}

#: Slot caps by challenge tier, best-ranked ability first. THREAT scale, not
#: point-buy: an NPC/BBEG is a monster-grade opponent, so a high-challenge
#: NPC sits on the DMG table (ancients run STR 27-28 / CON 25-26), not on
#: the 20 cap that binds player characters. Only the lowest tier is
#: PC-adjacent — which is where point-buy actually applies.
SCORE_CAPS: dict[str, list[int]] = {
    "1-4": [17, 16, 14, 12, 10, 8],
    "5-8": [20, 19, 17, 15, 12, 9],
    "9-14": [24, 22, 20, 17, 14, 10],
    "15+": [28, 26, 24, 20, 16, 12],
}

#: AC by tier — the DMG monster AC column, not armor arithmetic.
AC_BY_TIER: dict[str, int] = {"1-4": 15, "5-8": 17, "9-14": 19, "15+": 21}

#: Attacks in the round's routine by tier (a monster's Multiattack count).
SWINGS_BY_TIER: dict[str, int] = {"1-4": 2, "5-8": 2, "9-14": 3, "15+": 3}

#: Weapon enhancement by tier.
MAGIC_BY_TIER: dict[str, int] = {"1-4": 1, "5-8": 1, "9-14": 2, "15+": 3}

#: The damage type an archetype's extra damage carries.
CLASS_RIDER: dict[str, str] = {"Paladin": "radiant"}

#: class table (hit die, saves, skill picks, paladin slot row, features)
CLASS_TABLE: dict[str, dict[str, Any]] = {
    "Paladin": {
        "hit_die": 10,
        "save_prof": ("wis", "cha"),
        #: the class anchors the primary stat; tags only modulate it
        "priority": {"str": 2, "cha": 3, "con": 1, "wis": 0, "dex": 0, "int": -1},
        "skill_list": (
            "Athletics",
            "Insight",
            "Intimidation",
            "Medicine",
            "Persuasion",
            "Religion",
        ),
        "skill_picks": 2,
        # half-caster: the full SRD slot row by class level (1st..5th)
        "slots": {
            1: (),
            2: (2,),
            3: (3,),
            4: (3,),
            5: (4, 2),
            6: (4, 2),
            7: (4, 3),
            8: (4, 3),
            9: (4, 3, 2),
            10: (4, 3, 2),
            11: (4, 3, 3),
            12: (4, 3, 3),
            13: (4, 3, 3, 1),
            14: (4, 3, 3, 1),
            15: (4, 3, 3, 2),
            16: (4, 3, 3, 2),
            17: (4, 3, 3, 3, 1),
            18: (4, 3, 3, 3, 1),
            19: (4, 3, 3, 3, 2),
            20: (4, 3, 3, 3, 2),
        },
        "extra_attacks": 2,
        "features": [
            (2, "Divine Smite"),
            (2, "Fighting Style"),
            (3, "Channel Divinity"),
            (5, "Extra Attack"),
            (6, "Aura of Protection"),
            (10, "Aura of Courage"),
            (11, "Improved Divine Smite"),
            (14, "Cleansing Touch"),
            (15, "Purity of Spirit"),
        ],
    }
}

WEAPONS: dict[str, dict[str, Any]] = {
    "greatsword": {"dice": (2, 6), "type": "slashing", "two_handed": True},
    "maul": {"dice": (2, 6), "type": "bludgeoning", "two_handed": True},
    "greataxe": {"dice": (1, 12), "type": "slashing", "two_handed": True},
    "halberd": {"dice": (1, 10), "type": "slashing", "reach": True, "two_handed": True},
    "longsword": {"dice": (1, 8), "type": "slashing"},
    "warhammer": {"dice": (1, 8), "type": "bludgeoning"},
    "flail": {"dice": (1, 8), "type": "bludgeoning"},
    "mace": {"dice": (1, 6), "type": "bludgeoning"},
    "shortsword": {"dice": (1, 6), "type": "piercing", "finesse": True},
    "dagger": {"dice": (1, 4), "type": "piercing", "finesse": True, "thrown": True},
    "javelin": {"dice": (1, 6), "type": "piercing", "thrown": True},
    "lance": {"dice": (1, 12), "type": "piercing", "reach": True},
}

SLOTS = ("to_hit", "dice", "average", "bonus", "damage_type", "save", "dc", "reach", "targets")


def tier_for(challenge: int) -> str:
    if challenge <= 4:
        return "1-4"
    if challenge <= 8:
        return "5-8"
    if challenge <= 14:
        return "9-14"
    return "15+"


def rank_abilities(
    tags: list[str], class_priority: dict[str, int] | None = None
) -> tuple[list[str], dict[str, int]]:
    """Sum the class anchor and the tag weight vectors; rank best-first.

    Ties break on ABILITY order, so the result is deterministic. The class
    vector is what stops `divine_caster` from turning a paladin into a cleric:
    the class owns the primary stat, the tags only modulate around it.
    """
    totals = dict.fromkeys(ABILITY, 0)
    if class_priority:
        for ability, weight in class_priority.items():
            totals[ability] += weight
    for tag in tags:
        weights = ARCHETYPE_WEIGHTS.get(tag)
        if weights is None:
            raise ValueError(f"unknown archetype tag {tag!r}")
        for ability, weight in weights.items():
            totals[ability] += weight
    ranked = sorted(ABILITY, key=lambda a: (-totals[a], ABILITY.index(a)))
    return ranked, totals


def assign_scores(ranked: list[str], challenge: int, jitter_seed: int = 0) -> dict[str, int]:
    """Rank -> slot caps, with a deterministic +/-1 jitter on the middle slots."""
    caps = SCORE_CAPS[tier_for(challenge)]
    scores: dict[str, int] = {}
    for index, ability in enumerate(ranked):
        cap = caps[index]
        floor = 8
        delta = 0
        if 2 <= index <= 4:  # mid slots wobble; primary, secondary and dump stay crisp
            delta = ((jitter_seed >> (index * 3)) % 3) - 1
        scores[ability] = max(floor, min(cap, cap + delta))
    return scores


def modifier(score: int) -> int:
    return (score - 10) // 2


def solve_dice(target: float, multiplier: int = 1, bonus: int = 0) -> tuple[int, int, float]:
    """Smallest dice expression whose average lands closest to the target."""
    best: tuple[float, int, int, float] | None = None
    for count in range(1, 7):
        for sides in (4, 6, 8, 10, 12):
            avg = multiplier * (count * _die_avg(sides) + bonus)
            gap = abs(avg - target)
            if best is None or gap < best[0]:
                best = (gap, count, sides, avg)
    assert best is not None
    return best[1], best[2], best[3]


def solve_hit_dice(die: int, con_mod: int, band: tuple[int, int]) -> tuple[int, int]:
    """A hit-dice count whose average HP lands in the monster band."""
    die_avg = _die_avg(die)
    best: tuple[float, int, int] | None = None
    for count in range(1, 81):
        hp = int(count * die_avg + count * con_mod)
        low, high = band
        if low <= hp <= high:
            return count, hp
        gap = min(abs(hp - low), abs(hp - high))
        if best is None or gap < best[0]:
            best = (gap, count, hp)
    assert best is not None
    return best[1], best[2]


#: Rider dice candidates, weakest first. The zero entry matters: a low-challenge
#: creature whose weapon ALREADY lands in band must not get damage bolted on.
_RIDERS: tuple[tuple[int, int], ...] = ((0, 0),) + tuple(
    (count, sides) for count in range(1, 9) for sides in (4, 6, 8, 10, 12)
)


def solve_routine(
    weapon_avg: float, target: float, band: tuple[float, float], preferred_swings: int
) -> tuple[int, int, int, float]:
    """Pick (swings, rider count, rider sides, rider average) that lands in band.

    Searches the action economy, not just the damage: a level-1 threat swinging
    a greatsword already overshoots its band, so the solver must be able to
    drop to one attack. Falls back to the closest fit when nothing lands.
    """
    low, high = band
    best: tuple[tuple[float, float], int, int, int, float] | None = None
    fallback: tuple[float, int, int, int, float] | None = None
    for swings in (preferred_swings, 1, 2, 3, 4):
        if swings < 1:
            continue
        for count, sides in _RIDERS:
            rider_avg = count * _die_avg(sides)
            total = swings * (weapon_avg + rider_avg)
            gap = abs(total - target)
            if fallback is None or gap < fallback[0]:
                fallback = (gap, swings, count, sides, rider_avg)
            if low <= total <= high:
                penalty = (abs(swings - preferred_swings), gap)
                if best is None or penalty < best[0]:
                    best = (penalty, swings, count, sides, rider_avg)
    assert fallback is not None
    if best is None:
        _, swings, count, sides, rider_avg = fallback
        return swings, count, sides, rider_avg
    _, swings, count, sides, rider_avg = best
    return swings, count, sides, rider_avg
