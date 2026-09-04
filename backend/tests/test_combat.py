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
    VERDICT_ONTARGET,
    VERDICT_OVER,
    VERDICT_UNDER,
    analyze_action,
    audit_stat_block,
    expected_band,
    parse_damage_expression,
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
    assert expected_band({"role": "Monster", "cr": 14}) == (87.0, 88.0)
    assert expected_band({"role": "NPC", "level": 5}) == (33.0, 34.0)
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
    block["actions"][0]["description"] = "deals 17d10 bludgeoning damage"  # ~93.5, band 87-88
    assert audit_stat_block(block).verdict in (VERDICT_ONTARGET, VERDICT_OVER)


def test_audit_overpowered_verdict() -> None:
    block = {
        "identity": {"role": "Monster", "cr": 1},
        "actions": [{"name": "Slam", "description": "6d10 damage"}],
    }
    assert audit_stat_block(block).verdict == VERDICT_OVER
