"""Mechanics parity pins (spec: JSON-schema generation foundation).

The port in ``app.pipeline.mechanics`` must reproduce the prototype's
(``proto-character-builder/gen.py``) numbers exactly — captured here from
``paladin-l17.json``/``words.json`` (level-17 paladin, tags
divine_caster/durable/holy_warrior). Any deviation from the prototype's
pinned numbers is re-derived, never "fixed" (Ask First list).
"""

import zlib

import pytest

from app.pipeline import mechanics


def test_tier_for_boundaries() -> None:
    assert mechanics.tier_for(1) == "1-4"
    assert mechanics.tier_for(4) == "1-4"
    assert mechanics.tier_for(5) == "5-8"
    assert mechanics.tier_for(8) == "5-8"
    assert mechanics.tier_for(9) == "9-14"
    assert mechanics.tier_for(14) == "9-14"
    assert mechanics.tier_for(15) == "15+"
    assert mechanics.tier_for(17) == "15+"


def test_rank_abilities_paladin_pins() -> None:
    """The prototype's paladin inputs rank str > con > cha > wis > dex > int."""
    priority = mechanics.CLASS_TABLE["Paladin"]["priority"]
    ranked, totals = mechanics.rank_abilities(
        ["divine_caster", "durable", "holy_warrior"], priority
    )
    assert ranked == ["str", "con", "cha", "wis", "dex", "int"]
    assert totals == {"str": 6, "dex": -1, "con": 6, "int": -5, "wis": 5, "cha": 6}


def test_rank_abilities_unknown_tag_raises() -> None:
    """UNKNOWN_TAG: an unpinned tag is caller-visible, never committed."""
    with pytest.raises(ValueError):
        mechanics.rank_abilities(["bogus"])


def test_assign_scores_paladin_pins() -> None:
    """Rank -> 15+ slot caps: STR 28 / CON 26 / CHA 24 / WIS 20 / DEX 16 / INT 12."""
    ranked = ["str", "con", "cha", "wis", "dex", "int"]
    seed = zlib.crc32(b"Sir Thalassos of the Sunken Maw")
    assert mechanics.assign_scores(ranked, 17, jitter_seed=seed) == {
        "str": 28,
        "con": 26,
        "cha": 24,
        "wis": 20,
        "dex": 16,
        "int": 12,
    }


def test_modifier_pins() -> None:
    assert mechanics.modifier(28) == 9
    assert mechanics.modifier(10) == 0
    assert mechanics.modifier(8) == -1


def test_solve_dice_pins() -> None:
    """2d6 averages exactly 7.0 — the smallest expression hitting the target."""
    assert mechanics.solve_dice(7.0) == (2, 6, 7.0)


def test_solve_hit_dice_paladin_pin() -> None:
    """HP 324 = 24d10+192, landing in the CR-17 band (311, 325)."""
    assert mechanics.solve_hit_dice(10, 8, (311, 325)) == (24, 324)


def test_solve_routine_paladin_pin() -> None:
    """Greatsword avg 19.0 + 3d10 rider, 3 swings: 106.5 in the 105-110 band."""
    assert mechanics.solve_routine(19.0, 107.5, (105.0, 110.0), 3) == (3, 3, 10, 16.5)


def test_tables_paladin_pins() -> None:
    assert mechanics.AC_BY_TIER["15+"] == 21
    assert mechanics.SWINGS_BY_TIER["15+"] == 3
    assert mechanics.MAGIC_BY_TIER["15+"] == 3
    assert mechanics.CLASS_RIDER["Paladin"] == "radiant"
    assert mechanics.WEAPONS["greatsword"]["dice"] == (2, 6)
    assert mechanics.CLASS_TABLE["Paladin"]["hit_die"] == 10
    assert mechanics.CLASS_TABLE["Paladin"]["slots"][17] == (4, 3, 3, 3, 1)
    assert mechanics.SLOTS == (
        "to_hit",
        "dice",
        "average",
        "bonus",
        "damage_type",
        "save",
        "dc",
        "reach",
        "targets",
    )
