"""Combat-power audit for AR25 stat blocks (how hard an entity attacks for).

Pins the damage-expression parser, the DMG-derived per-action adjustment
(save-for-half, area-of-effect), the per-round estimate (strongest action
+ legendary budget), and the verdict against the CR damage/round band.
"""

import copy
from typing import Any

import pytest

from app.pipeline.combat import (
    CR_DPR,
    CR_HP,
    VERDICT_ONTARGET,
    VERDICT_OVER,
    VERDICT_UNDER,
    analyze_action,
    audit_stat_block,
    expected_band,
    hp_band,
    is_hp_frail,
    parse_damage_expression,
    parse_multiattack_count,
    round_dpr,
)

#: The committed Hollow Seraph block, excerpted from the dev DB: CR 14
#: Monster with four actions. Used as the integration fixture.
HOLLOW: dict[str, Any] = {
    "identity": {"role": "Monster", "race": "Corrupted Celestial", "cr": 14},
    "actions": [
        {
            "name": "Resonant Impact",
            "description": (
                "The Seraph's wings close around a target in a 20-ft. cone, dealing "
                "4d10 bludgeoning damage (DC 14 STR save for half) and 3d8 thunder "
                "damage. The impact sends a shockwave through the ground, dealing "
                "1d6 bludgeoning damage to creatures within 10 ft. of the target."
            ),
        },
        {
            "name": "Bone-Frequency",
            "description": (
                "The Seraph emits a grinding resonance in a 30-ft. radius. Each "
                "creature in the area must make a DC 15 CON save or take 5d8 thunder "
                "damage. A creature that succeeds takes half damage and is unaffected."
            ),
        },
        {
            "name": "Wing-Blade",
            "description": (
                "The Seraph tears a strip from a wing and hurls it. The strip flies "
                "in a straight line 60 ft. and deals 3d10 slashing damage (DC 14 DEX "
                "save for half). It lingers on the ground, dealing 1d4 slashing "
                "damage each round to creatures standing on it."
            ),
        },
        {
            "name": "Heartbeat Pulse",
            "description": (
                "The Seraph's chest flares with amber light. All creatures within "
                "40 ft. must make a DC 16 WIS save or be stunned; the ward pulses "
                "add +2 AC to structures within 500 ft. for 10 minutes."
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# Damage parser
# ---------------------------------------------------------------------------


def test_parse_sums_dice_averages() -> None:
    total, sources = parse_damage_expression("deals 4d10 bludgeoning and 3d8 thunder")
    assert total == 35.5  # 4*5.5 + 3*4.5
    assert sources == ("4d10", "3d8")


def test_parse_counts_adjacent_flat_modifier() -> None:
    assert parse_damage_expression("a club hit deals 2d6+5 slashing")[0] == 12.0
    assert parse_damage_expression("3d10 + 5 fire damage")[0] == 21.5


def test_parse_does_not_count_die_count_as_flat() -> None:
    """``2d8+1d4`` must not read the +1 as a +1 flat bonus."""
    total, sources = parse_damage_expression("deals 2d8+1d4 necrotic damage")
    assert total == 11.5  # 2*4.5 + 2.5, no flat
    assert sources == ("2d8", "1d4")


def test_parse_keeps_flat_modifier_before_damage_word() -> None:
    """``3d10+6 damage`` keeps the +6: the die-count guard fires only
    when the d/D is followed by a digit (review 2026-09-09 — the old
    guard ate the modifier before the word 'damage', repeating the
    critic's dropped-+6 error inside our own parser)."""
    total, sources = parse_damage_expression("Hit: 3d10 + 6 damage.")
    assert total == 22.5  # 3*5.5 + 6
    assert sources == ("3d10", "+ 6")


def test_parse_ignores_non_damage_plus_bonus() -> None:
    """A lone ``+2 AC`` is a bonus, not damage — no die trails the plus."""
    total, sources = parse_damage_expression("grants +2 AC to structures within 100 ft.")
    assert total == 0.0
    assert sources == ()


def test_parse_no_damage_action_is_zero() -> None:
    """A control/stun effect with no damage reads as 0, not a parse error."""
    total, sources = parse_damage_expression("target is stunned until the next turn")
    assert total == 0.0
    assert sources == ()


# ---------------------------------------------------------------------------
# Per-action DMG adjustment
# ---------------------------------------------------------------------------


def test_analyze_action_save_for_half() -> None:
    action = analyze_action("Bite", "the target must make a DC 14 STR save for half")
    assert action.nominal_avg == 0.0  # no damage mentioned
    assert action.save_half is True and action.aoe is False


def test_analyze_action_save_half_spans_sentences() -> None:
    """A save … half split across sentences still counts (the common shape)."""
    action = analyze_action("Pulse", "make a DC 15 CON save or take 5d8; a success is half.")
    assert action.save_half is True
    assert action.expected_avg == 22.5 * 0.75  # 5d8 -> 22.5, save-half -> *0.75


def test_analyze_action_save_half_and_aoe_compound() -> None:
    action = analyze_action("Boom", "in a 10-ft. radius, DC 15 save for half: 4d10 damage")
    assert action.save_half and action.aoe
    assert action.expected_avg == 22.0 * 0.75 * 2.0  # 4d10 * save * 2 targets


# ---------------------------------------------------------------------------
# Round + band
# ---------------------------------------------------------------------------


def test_expected_band_keyed_by_role() -> None:
    assert expected_band({"role": "Monster", "cr": 14}) == (87.0, 92.0)
    assert expected_band({"role": "NPC", "level": 5}) == (33.0, 38.0)
    assert expected_band({"role": "Monster", "cr": 99}) is None
    assert expected_band({"role": "NPC", "level": None}) is None


def test_round_dpr_uses_strongest_and_legendary_budget() -> None:
    weak = analyze_action("Weak", "1d4 damage")
    strong = analyze_action("Strong", "8d10 damage")
    assert round_dpr([weak, strong], legendary=False) == strong.expected_avg
    assert round_dpr([weak, strong], legendary=True) == strong.expected_avg * 2.0


def test_round_dpr_empty() -> None:
    assert round_dpr([], legendary=True) == 0.0


# ---------------------------------------------------------------------------
# Full audit — the Hollow Seraph (CR 14)
# ---------------------------------------------------------------------------


def test_audit_hollow_seraph_is_underpowered() -> None:
    audit = audit_stat_block(HOLLOW)
    assert audit.challenge == "CR 14"
    assert audit.band == CR_DPR[14]
    assert audit.verdict == VERDICT_UNDER
    assert audit.gap_pct < 0
    by_name = {a.name: a for a in audit.actions}
    # Resonant Impact is the strongest repeatable damage action -> the round DPR.
    assert audit.dpr == pytest.approx(by_name["Resonant Impact"].expected_avg)
    # Heartbeat Pulse carries an AC bonus, not damage -> no damage parsed.
    assert by_name["Heartbeat Pulse"].nominal_avg == 0.0


def test_audit_non_dict_block_is_conservative() -> None:
    audit = audit_stat_block("nope")
    assert audit.verdict == VERDICT_UNDER and audit.dpr == 0.0 and audit.actions == ()


def test_audit_missing_actions_still_gets_band() -> None:
    audit = audit_stat_block({"identity": {"role": "Monster", "cr": 14}})
    assert audit.band == CR_DPR[14]
    assert audit.actions == () and audit.dpr == 0.0


def test_audit_on_target_or_over_edge() -> None:
    block = copy.deepcopy(HOLLOW)
    block["actions"][0]["description"] = "deals 17d10 bludgeoning damage"  # ~93.5, band 87-92
    assert audit_stat_block(block).verdict in (VERDICT_ONTARGET, VERDICT_OVER)


def test_audit_overpowered_verdict() -> None:
    block = {
        "identity": {"role": "Monster", "cr": 1},
        "actions": [{"name": "Slam", "description": "6d10 damage"}],
    }
    assert audit_stat_block(block).verdict == VERDICT_OVER


# ---------------------------------------------------------------------------
# Full DMG bands (p.274 pins)
# ---------------------------------------------------------------------------


def test_dpr_bands_match_dmg_table() -> None:
    assert CR_DPR[18] == (111.0, 116.0)
    assert CR_DPR[21] == (141.0, 158.0)
    assert CR_DPR[20] == (123.0, 140.0)
    assert CR_DPR[30] == (303.0, 303.0)  # the open-ended "303+" row
    assert CR_DPR["1/8"] == (2.0, 3.0)
    assert CR_DPR[1] == (9.0, 14.0)


def test_hp_bands_match_dmg_table() -> None:
    assert CR_HP[18] == (326, 340)
    assert CR_HP[5] == (131, 145)
    assert CR_HP["1/4"] == (36, 49)
    assert CR_HP[30] == (566, 566)  # the open-ended "566+" row
    assert hp_band({"role": "Monster", "cr": 18}) == (326, 340)
    assert hp_band({"role": "NPC", "level": 5}) == (131, 145)
    assert hp_band({"role": "Monster", "cr": 99}) is None


def test_is_hp_frail_fails_low_only() -> None:
    assert is_hp_frail(40, (326, 340))  # the HP_FRAIL row
    assert is_hp_frail(162, (326, 340))  # just below half of 326
    assert not is_hp_frail(163, (326, 340))  # at the line: not frail
    assert not is_hp_frail(500, (326, 340))  # the HP_TANK_OK row


# ---------------------------------------------------------------------------
# Multiattack
# ---------------------------------------------------------------------------


def test_parse_multiattack_count_reads_first_count() -> None:
    assert parse_multiattack_count("makes three Claw attacks") == 3
    assert parse_multiattack_count("makes 3 Claw attacks") == 3
    assert parse_multiattack_count("can attack twice") == 2
    assert parse_multiattack_count("makes two attacks, each dealing 2d6 damage") == 2
    assert parse_multiattack_count("Multiattack") == 2  # no count named


def test_round_dpr_multiattack_multiplies_strongest_other() -> None:
    claw = analyze_action("Claw", "3d10+6 slashing")  # 22.5
    multi = analyze_action("Multiattack", "makes three Claw attacks")  # 0 dice
    assert round_dpr([multi, claw], legendary=False, multiattack_count=3) == 67.5
    assert round_dpr([multi, claw], legendary=True, multiattack_count=3) == 90.0


def test_round_dpr_multiattack_with_no_other_attack_is_zero() -> None:
    multi = analyze_action("Multiattack", "makes three Claw attacks")
    assert round_dpr([multi], legendary=False, multiattack_count=3) == 0.0


def test_audit_multiattack_block_counts_routine() -> None:
    block = {
        "identity": {"role": "Monster", "cr": 10},
        "actions": [
            {"name": "Multiattack", "description": "makes three Claw attacks"},
            {"name": "Claw", "description": "3d10+6 slashing"},
        ],
    }
    audit = audit_stat_block(block)
    assert audit.dpr == 67.5  # 3 x 22.5, inside the CR 10 band (63-68)
    assert audit.verdict == VERDICT_ONTARGET


# ---------------------------------------------------------------------------
# I/O matrix rows (audit level)
# ---------------------------------------------------------------------------


def test_audit_underpowered_cr18() -> None:
    block = {
        "identity": {"role": "Monster", "cr": 18},
        "actions": [{"name": "Void Ray", "description": "3d10+6 necrotic"}],
    }
    audit = audit_stat_block(block)
    assert audit.band == (111.0, 116.0)
    assert audit.dpr == 22.5
    assert audit.verdict == VERDICT_UNDER and audit.gap_pct < 0


def test_audit_overpowered_level5() -> None:
    block = {
        "identity": {"role": "NPC", "level": 5},
        "actions": [{"name": "Slam", "description": "10d10+5 force"}],
    }
    audit = audit_stat_block(block)
    assert audit.band == (33.0, 38.0)
    assert audit.dpr == 60.0
    assert audit.verdict == VERDICT_OVER


def test_audit_on_target_cr18() -> None:
    block = {
        "identity": {"role": "Monster", "cr": 18},
        "actions": [{"name": "Void Ray", "description": "17d10+6 necrotic"}],
    }
    audit = audit_stat_block(block)
    assert audit.dpr == 99.5  # within 20% of 111-116
    assert audit.verdict == VERDICT_ONTARGET


def test_parse_multiattack_count_ignores_non_count_integers() -> None:
    """To-hit bonuses and damage parentheticals never read as the count."""
    assert parse_multiattack_count("+5 to hit, makes 3 Claw attacks") == 3
    assert parse_multiattack_count("makes three Claw attacks (3d6)") == 3
    assert parse_multiattack_count("makes 100 attacks") == 10  # clamped


def test_audit_dice_bearing_routine_excluded_from_multiplier() -> None:
    """A routine carrying its own dice multiplies the OTHER action, not
    itself: 3 x 1d4 = 7.5 (UNDER at CR 10), never 3 x 22.5 = 67.5."""
    block = {
        "identity": {"role": "Monster", "cr": 10},
        "actions": [
            {"name": "Multiattack", "description": "makes three Claw attacks, each dealing 3d10+6"},
            {"name": "Claw", "description": "1d4 slashing"},
        ],
    }
    audit = audit_stat_block(block)
    assert audit.dpr == 7.5
    assert audit.verdict == VERDICT_UNDER


def test_multiattack_routine_match_ignores_spaces() -> None:
    """'Multi Attack' counts as the routine; its dice still excluded."""
    routine = analyze_action("Multi Attack", "makes two Claw attacks, each dealing 3d10+6")
    claw = analyze_action("Claw", "1d4 slashing")
    assert round_dpr([routine, claw], legendary=False, multiattack_count=2) == 5.0


def test_round_dpr_legendary_uses_strongest_other_with_routine() -> None:
    """With a routine present, the legendary budget keys on the strongest
    OTHER action — routine dice never inflate it."""
    routine = analyze_action("Multiattack", "makes two Claw attacks, each dealing 3d10+6")
    claw = analyze_action("Claw", "1d4 slashing")  # 2.5
    assert round_dpr([routine, claw], legendary=True, multiattack_count=2) == 7.5


def test_limited_use_action_averaged_over_three_rounds() -> None:
    """Recharge / per-day actions count once per 3 rounds (DMG)."""
    nova = analyze_action("Nova", "10d10+5 force, recharge 5-6")
    assert nova.limited_use
    assert nova.nominal_avg == 60.0
    assert nova.expected_avg == 20.0
    daily = analyze_action("Doom", "1/day: 6d6 fire")
    assert daily.limited_use
    assert daily.expected_avg == 7.0


def test_dpr_bands_match_dmg_mid_rows() -> None:
    """The prompt-advertised mid rows pin the table (typo guard)."""
    assert CR_DPR[5] == (33.0, 38.0)
    assert CR_DPR[10] == (63.0, 68.0)
    assert CR_DPR[14] == (87.0, 92.0)


def test_cr30_band_open_ended() -> None:
    """Top row: 303+ means on-target at 310, over past 1.2x303."""
    assert CR_DPR[30] == (303.0, 303.0)
    base = {"identity": {"role": "Monster", "cr": 30, "race": "Titan"}}

    def slam(desc: str) -> dict[str, object]:
        return {**base, "actions": [{"name": "Slam", "description": desc}]}

    assert audit_stat_block(slam("40d10+90 force")).verdict == VERDICT_ONTARGET
    assert audit_stat_block(slam("60d10+40 force")).verdict == VERDICT_OVER
