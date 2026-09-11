"""Stat-block validation and repair machinery (spec-2.4, AR24/AR25).

Unit matrix for the AR25 KnowledgeProvider validator (``knowledge.py``)
and the bounded-repair machinery (``statblocks.py``): role-limited
semantics, vocabularies, caps, strict typing, deterministic prompts, and
the repair contract. No live LLM, no DB (pure functions).
"""

import json
from typing import Any

import pytest

from app.pipeline.knowledge import (
    ABILITY_MAX,
    ABILITY_MIN,
    CR_MAX,
    LEVEL_MAX,
    validate_stat_block,
)
from app.pipeline.statblocks import (
    StatIssue,
    apply_stat_repairs,
    build_stat_repair_prompt,
    collect_stat_issues,
    parse_stat_repair_output,
    spells_reference_text,
    stat_block_rules_text,
    stat_failure_message,
    strip_noncharacter_stat_blocks,
)
from app.pipeline.worker import JobPayloadError
from app.store import models

#: A valid AR25 minimal stat block (NPC, level 5 Human Fighter). Power-floor
#: compliant: 27 DPR inside the level-5 band (33-38, under at <26.4) and
#: hp 66 at the frail line (half of the 131 band low).
VALID: dict[str, Any] = {
    "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter", "alignment": "LG"},
    "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
    "combat": {"ac": 16, "hp": 66},
    "skills": [{"name": "Athletics", "bonus": 5}],
    "actions": [
        {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 4d10+5 slashing"}
    ],
}

#: A valid Monster block (goblin: CR 1/4, unaligned race type). No actions,
#: so DPR abstains; hp 18 sits at the frail line (half of the 36 band low).
MONSTER: dict[str, Any] = {
    "identity": {"role": "Monster", "cr": "1/4", "race": "Goblin", "alignment": "unaligned"},
    "attributes": {"str": 8, "dex": 14, "con": 10, "int": 9, "wis": 11, "cha": 8},
    "combat": {"ac": 15, "hp": 18},
}


# ---------------------------------------------------------------------------
# Validator: acceptance
# ---------------------------------------------------------------------------


def test_valid_npc_block_passes() -> None:
    assert validate_stat_block(VALID) == []


def test_valid_monster_block_passes() -> None:
    assert validate_stat_block(MONSTER) == []


def test_case_insensitive_vocabularies_pass() -> None:
    """LLM output varies in case; casing is presentation, not validity."""
    lowered = {
        "identity": {
            "role": "npc",
            "level": 3,
            "race": "human",
            "class": "wizard",
            "alignment": "ng",
        },
        "attributes": {"str": 10, "dex": 10, "con": 10, "int": 14, "wis": 10, "cha": 10},
        "combat": {"ac": 10, "hp": 51},
        "skills": [{"name": "arcana", "bonus": 6}],
        "spells": ["fireball"],
    }
    assert validate_stat_block(lowered) == []


def test_unknown_extra_keys_tolerated() -> None:
    """AR24 forward compatibility: unknown top-level/section keys are
    skipped, never failures."""
    with_extra = {**VALID, "voice": "gravelly", "hooks": ["owes the guild"]}
    with_extra["combat"] = {**VALID["combat"], "speed": "30 ft."}
    assert validate_stat_block(with_extra) == []


def test_optional_sections_are_optional() -> None:
    minimal = {
        "identity": {"role": "NPC", "level": 1, "race": "Human"},
        "attributes": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "combat": {"ac": 10, "hp": 36},
    }
    assert validate_stat_block(minimal) == []


def test_non_object_block_rejected() -> None:
    assert validate_stat_block("nope") == ["stat_block must be an object"]
    assert validate_stat_block(None) == ["stat_block must be an object"]


# ---------------------------------------------------------------------------
# Validator: role-limited semantics (AR25, AD-18)
# ---------------------------------------------------------------------------


def test_monster_with_level_rejected() -> None:
    errors = validate_stat_block({**MONSTER, "identity": {**MONSTER["identity"], "level": 5}})
    assert any("not allowed for role Monster" in e for e in errors)


def test_npc_with_cr_rejected() -> None:
    errors = validate_stat_block({**VALID, "identity": {**VALID["identity"], "cr": 3}})
    assert any("not allowed for role NPC/BBEG" in e for e in errors)


def test_level_bounds_enforced() -> None:
    for bad in (0, LEVEL_MAX + 1, "5", 4.5, True):
        errors = validate_stat_block({**VALID, "identity": {**VALID["identity"], "level": bad}})
        assert any("identity.level" in e for e in errors), bad

    for cr, hp in ((0, 1), (CR_MAX, 283), ("1/8", 4), ("1/4", 18), ("1/2", 25)):
        block = {
            **MONSTER,
            "identity": {**MONSTER["identity"], "cr": cr},
            "combat": {**MONSTER["combat"], "hp": hp},
        }
        assert validate_stat_block(block) == [], cr
    for bad in (-1, CR_MAX + 1, "1/3", "2.5", "five"):
        errors = validate_stat_block({**MONSTER, "identity": {**MONSTER["identity"], "cr": bad}})
        assert any("identity.cr" in e for e in errors), bad


def test_unknown_role_rejected() -> None:
    errors = validate_stat_block({**VALID, "identity": {**VALID["identity"], "role": "Dragon"}})
    assert any("identity.role" in e for e in errors)


# ---------------------------------------------------------------------------
# Validator: vocabularies + caps
# ---------------------------------------------------------------------------


def test_ability_score_caps_enforced() -> None:
    for bad in (ABILITY_MIN - 1, ABILITY_MAX + 1, "14", 14.0, True):
        attributes = {**VALID["attributes"], "str": bad}
        errors = validate_stat_block({**VALID, "attributes": attributes})
        assert any("attributes.str" in e for e in errors), bad


def test_all_six_abilities_required() -> None:
    missing = dict(VALID["attributes"])
    missing.pop("cha")
    assert validate_stat_block({**VALID, "attributes": missing}) != []


def test_race_vocabulary_enforced_for_npc_and_free_for_monster() -> None:
    """Owner decision (spec-2.4 review): Monster identity.race is the
    creature type/name — deliberately outside the SRD player-race set."""
    errors = validate_stat_block({**VALID, "identity": {**VALID["identity"], "race": "Kobold"}})
    assert any("identity.race" in e and "SRD" in e for e in errors)
    for race in ("Goblin", "Shadow-tainted wolf", "aberration"):
        assert (
            validate_stat_block({**MONSTER, "identity": {**MONSTER["identity"], "race": race}})
            == []
        ), race


def test_class_vocabulary_enforced() -> None:
    errors = validate_stat_block({**VALID, "identity": {**VALID["identity"], "class": "Artificer"}})
    assert any("identity.class" in e for e in errors)


def test_alignment_vocabulary_enforced() -> None:
    errors = validate_stat_block(
        {**VALID, "identity": {**VALID["identity"], "alignment": "Chaotic Stupid"}}
    )
    assert any("identity.alignment" in e for e in errors)


def test_skill_vocabulary_and_bonus_type_enforced() -> None:
    errors = validate_stat_block({**VALID, "skills": [{"name": "Chess", "bonus": 5}]})
    assert any("not in the SRD list" in e for e in errors)
    errors = validate_stat_block({**VALID, "skills": [{"name": "Athletics", "bonus": "5"}]})
    assert any("bonus" in e and "integer" in e for e in errors)
    errors = validate_stat_block(
        {
            **VALID,
            "skills": [{"name": "Athletics", "bonus": 5}, {"name": "ATHLETICS", "bonus": 5}],
        }
    )
    assert any("duplicated" in e for e in errors)


def test_actions_traits_shape_enforced() -> None:
    errors = validate_stat_block({**VALID, "actions": [{"name": "Slam"}]})
    assert any("description" in e for e in errors)
    errors = validate_stat_block({**VALID, "actions": [{"name": 7, "description": "x"}]})
    assert any("non-blank" in e for e in errors)

    wizard: dict[str, Any] = {
        "identity": {"role": "BBEG", "level": 12, "race": "Human", "class": "Wizard"},
        "attributes": {"str": 8, "dex": 14, "con": 12, "int": 18, "wis": 12, "cha": 10},
        "combat": {"ac": 15, "hp": 118},
        "spells": ["Fireball", "Shield"],
    }
    assert validate_stat_block(wizard) == []
    errors = validate_stat_block({**wizard, "spells": ["Cure Wounds"]})
    assert any("not on the Wizard spell list" in e for e in errors)
    errors = validate_stat_block(
        {**wizard, "identity": {**wizard["identity"], "class": None}, "spells": ["Fireball"]}
    )
    assert any("require identity.class" in e for e in errors)
    # Eldritch Blast is in the reference but Warlock-only: a Wizard gets
    # the role-limited rejection, not an unknown-spell rejection.
    errors = validate_stat_block({**wizard, "spells": ["Eldritch Blast"]})
    assert any("not on the Wizard spell list" in e for e in errors)
    # A spell outside the local reference is unknown to the table.
    errors = validate_stat_block({**wizard, "spells": ["Karsus's Avatar"]})
    assert any("not in the local SRD reference" in e for e in errors)


def test_monster_spells_not_allowed() -> None:
    """spec-2.4 (enforced, not just prompt advice): a Monster never carries
    spells — its magic is actions/traits; an empty section stays fine."""
    mon_empty = {**MONSTER, "identity": {**MONSTER["identity"], "class": "Fighter"}, "spells": []}
    assert validate_stat_block(mon_empty) == []
    wizard_monster = {**MONSTER["identity"], "class": "Wizard"}
    mon = {**MONSTER, "identity": wizard_monster, "spells": ["Fireball"]}
    errors = validate_stat_block(mon)
    assert any("not allowed for role Monster" in e for e in errors)


def test_duplicate_spells_rejected() -> None:
    """Parity with skills/actions/traits: a repeated spell name is a violation."""
    wizard = {**VALID, "identity": {**VALID["identity"], "class": "Wizard"}}
    errors = validate_stat_block({**wizard, "spells": ["Fireball", " fireball "]})
    assert any("duplicated" in e for e in errors)


def test_class_whitespace_folding_matches_other_vocabularies() -> None:
    """Casing and padding are presentation, not validity — class folds with
    .strip() like role/race/alignment/skills/spells."""
    wizard = {**VALID, "identity": {**VALID["identity"], "class": "  wizard  "}}
    assert validate_stat_block({**wizard, "spells": ["Fireball"]}) == []


def test_spells_without_identity_report_never_raise() -> None:
    """A spells section with no identity section yields stable violations,
    not a crash (role and class default to None)."""
    errors = validate_stat_block({"spells": ["Fireball"]})
    assert any("identity section missing" in e for e in errors)
    assert any("spells require identity.class" in e for e in errors)


def test_combat_caps_enforced() -> None:
    for bad in (0, -3, "16", 16.0, True):
        errors = validate_stat_block({**VALID, "combat": {"ac": bad, "hp": 44}})
        assert any("combat.ac" in e for e in errors), bad
    for bad in (0, -3, "44", True):
        errors = validate_stat_block({**VALID, "combat": {"ac": 16, "hp": bad}})
        assert any("combat.hp" in e for e in errors), bad


def test_violations_are_stable() -> None:
    bad = {
        **VALID,
        "attributes": {**VALID["attributes"], "str": 40},
        "identity": {**VALID["identity"], "level": 99},
    }
    assert validate_stat_block(bad) == validate_stat_block(bad)
    assert validate_stat_block(bad) != []


# ---------------------------------------------------------------------------
# collect_stat_issues
# ---------------------------------------------------------------------------


def _entity(kind: str, *, data: dict[str, Any] | None = None) -> models.EntityInput:
    return models.EntityInput(kind=kind, name=f"Entity {kind}", data=data or {})


def test_collect_flags_missing_and_invalid_characters_only() -> None:
    missing = _entity("character", data={"goal": "tea"})
    invalid = _entity(
        "character",
        data={"stat_block": {**VALID, "attributes": {**VALID["attributes"], "str": 40}}},
    )
    valid_char = _entity("character", data={"stat_block": VALID})
    faction = _entity("faction", data={})
    place = _entity("place", data={})
    issues = collect_stat_issues([missing, invalid, valid_char, faction, place])
    assert [(i.position, i.entity.name) for i in issues] == [
        (0, "Entity character"),
        (1, "Entity character"),
    ]
    assert issues[0].violations == ("stat_block section missing",)
    assert any("attributes.str" in v for v in issues[1].violations)


def test_collect_none_when_all_valid() -> None:
    entities = [_entity("character", data={"stat_block": VALID}), _entity("faction")]
    assert collect_stat_issues(entities) == []


def test_strip_noncharacter_stat_blocks() -> None:
    """Owner decision (spec-2.4 review): a stray block on a faction/place
    is stripped; characters and all other data are untouched."""
    stray = _entity("faction", data={"stat_block": VALID, "economy": "level 3"})
    char = _entity("character", data={"stat_block": VALID})
    plain = _entity("place", data={"terrain": "marsh"})
    out = strip_noncharacter_stat_blocks([stray, char, plain])
    assert out[0].data == {"economy": "level 3"}
    assert out[0] is not stray  # replaced where stripped
    assert out[1] is char and out[2] is plain  # same objects elsewhere


# ---------------------------------------------------------------------------
# Repair prompt: determinism + contract
# ---------------------------------------------------------------------------


def test_repair_prompt_deterministic() -> None:
    issues = [
        StatIssue(0, _entity("character", data={}), ("stat_block section missing",)),
        StatIssue(
            2,
            _entity(
                "character",
                data={"stat_block": {**VALID, "attributes": {**VALID["attributes"], "str": 40}}},
            ),
            ("attributes.str must be an integer in [1, 30]",),
        ),
    ]
    first = build_stat_repair_prompt(issues)
    assert build_stat_repair_prompt(issues) == first
    # No ids/timestamps/job state in the prompt.
    assert "E0" in first and "E2" in first
    assert "MISSING" in first  # the missing block renders as MISSING
    assert "STAT BLOCK RULES" in first
    # The repair pass embeds only the flagged classes' spell lines (the
    # VALID-ish block is a Fighter; no missing-class fallback needed here).
    assert "SRD SPELLS BY CLASS" in first and "- Fighter:" in first
    assert "- Wizard:" not in first
    # The rules text is a pure function of the reference data.
    assert stat_block_rules_text() == stat_block_rules_text()
    assert "STR" in stat_block_rules_text() or "str" in stat_block_rules_text()


def test_second_pass_prompt_carries_what_survived() -> None:
    """SECOND_PASS_PROMPT (owner decision 2026-09-11): the second repair
    pass is not a re-send of the first — it re-reads the block the first
    attempt wrote and names the violations that survived it, and says which
    numbers to move. The first pass's header stays untouched."""
    block = {**VALID, "attributes": {**VALID["attributes"], "str": 40}}
    issues = [
        StatIssue(
            1,
            _entity("character", data={"stat_block": block}),
            ("attributes.str must be an integer in [1, 30]", "under-powered for level 5"),
        ),
    ]
    first = build_stat_repair_prompt(issues)
    second = build_stat_repair_prompt(issues, attempt=2)
    assert "VIOLATIONS TO FIX" in first
    assert "VIOLATIONS STILL UNFIXED" not in first
    assert "VIOLATIONS STILL UNFIXED — SECOND REPAIR PASS" in second
    assert "VIOLATIONS TO FIX" not in second
    # Both surviving violations are named, and the current block shown is
    # the one the first attempt produced (the same block here — the point
    # is that the second pass reads data, not the original prompt).
    assert "attributes.str must be an integer in [1, 30]" in second
    assert "under-powered for level 5" in second
    assert json.dumps(block, sort_keys=True, separators=(",", ":")) in second
    # The rules and the output contract are unchanged between passes.
    assert "STAT BLOCK RULES" in second and "OUTPUT CONTRACT" in second
    assert second != first


def test_rules_text_carries_vocabularies() -> None:
    rules = stat_block_rules_text()
    assert "role in" in rules and "NPC" in rules and "Monster" in rules
    assert "CR_MAX" not in rules  # interpolated, never a name leak
    assert "SRD SPELLS BY CLASS" in rules  # pointer to the embedded reference
    assert "Skill" in rules or "skill" in rules
    # The flag variant (wave-2 generate prompt) swaps only the closing
    # pointer to the embedded table; everything else is identical.
    without_table = stat_block_rules_text(spells_reference=False)
    assert "reference below" not in without_table
    assert "runtime" in without_table and "validator owns the authoritative table" in without_table
    assert without_table != rules


def test_rules_text_instructs_challenge_scaling() -> None:
    """Stats must follow the declared level/CR: the model is told a tougher
    declaration needs tougher combat numbers and scores (not a flat block),
    with the DMG bands the validator enforces."""
    rules = stat_block_rules_text()
    assert "CHALLENGE SCALING" in rules
    assert "level" in rules and "CR" in rules
    assert "hp" in rules and "ac" in rules


def test_spells_reference_text_is_deterministic_and_subsettable() -> None:
    full = spells_reference_text()
    assert spells_reference_text() == full
    assert all(f"- {klass}:" in full for klass in ("Cleric", "Wizard", "Fighter"))
    wizard_only = spells_reference_text(["Wizard"])
    assert wizard_only != full
    assert "- Wizard:" in wizard_only
    assert "- Cleric:" not in wizard_only  # subset: only the named classes
    assert "- Fighter:" not in wizard_only
    # Junk class names are dropped (the reference stays a pure function of SRD data).
    assert spells_reference_text(["Wizard", "NotAClass"]) == wizard_only


# ---------------------------------------------------------------------------
# Repair parse + apply
# ---------------------------------------------------------------------------


def test_parse_repair_output_accepts_all_flagged() -> None:
    text = json.dumps(
        {
            "stat_blocks": [
                {"ref": "E0", "stat_block": VALID},
                {"ref": "E2", "stat_block": MONSTER},
            ]
        }
    )
    repaired = parse_stat_repair_output(text, [0, 2])
    assert repaired == {0: VALID, 2: MONSTER}


def test_parse_repair_output_strips_fence() -> None:
    text = "```json\n" + json.dumps({"stat_blocks": [{"ref": "E0", "stat_block": VALID}]}) + "\n```"
    assert parse_stat_repair_output(text, [0]) == {0: VALID}


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps({"stat_blocks": "nope"}),
        json.dumps({"stat_blocks": [{"ref": "E0"}]}),  # missing stat_block
        json.dumps({"stat_blocks": [{"ref": "X0", "stat_block": VALID}]}),
        json.dumps({"stat_blocks": [{"ref": "E\u00b2", "stat_block": VALID}]}),  # isdigit only
        json.dumps({"stat_blocks": [{"ref": "E01", "stat_block": VALID}]}),
        json.dumps({"stat_blocks": [{"ref": "E3", "stat_block": VALID}]}),  # un-flagged ref
        json.dumps(
            {
                "stat_blocks": [
                    {"ref": "E0", "stat_block": VALID},
                    {"ref": "E0", "stat_block": VALID},
                ]
            }
        ),  # duplicate
        json.dumps({"stat_blocks": [{"ref": "E" + "9" * 5000, "stat_block": VALID}]}),  # huge ref
    ],
)
def test_parse_repair_output_rejects_malformed(payload: str) -> None:
    with pytest.raises(JobPayloadError):
        parse_stat_repair_output(payload, [0, 1])


def test_parse_repair_output_malformed_json_is_none() -> None:
    """A response that is not parseable as one JSON object returns None
    (the build-in stat gate retries once) — it is NOT a contract
    violation; only well-formed JSON with the wrong CONTRACT raises."""
    assert parse_stat_repair_output("not json", [0, 1]) is None
    # JSON with a bare-string object member (the 2026-09-09 gemma failure).
    malformed = '{"stat_blocks": [{"ref": "E0", "stat_block": {"X: y"}}]}'
    assert parse_stat_repair_output(malformed, [0]) is None
    # Prose-wrapped valid JSON is rescued by the balanced-object extraction.
    wrapped = (
        "Here: " + json.dumps({"stat_blocks": [{"ref": "E0", "stat_block": VALID}]}) + " thanks"
    )
    assert parse_stat_repair_output(wrapped, [0]) == {0: VALID}


def test_apply_repairs_touches_only_flagged_stat_blocks() -> None:
    entities = [
        _entity("character", data={"goal": "tea"}),
        _entity("character", data={"stat_block": VALID}),
        _entity("faction", data={"economy": "level 3"}),
    ]
    merged = apply_stat_repairs(entities, {0: VALID})
    assert merged[0].data["stat_block"] == VALID
    assert merged[0].data["goal"] == "tea"  # untouched data survives
    assert merged[1] is entities[1]  # un-flagged rows are the same objects
    assert merged[2] is entities[2]
    assert merged[0].data is not entities[0].data  # a fresh dict, not a mutation


def test_stat_failure_message_names_characters_and_violations() -> None:
    issues = [
        StatIssue(0, _entity("character", data={}), ("stat_block section missing",)),
        StatIssue(
            1,
            _entity(
                "character",
                data={"stat_block": {**VALID, "attributes": {**VALID["attributes"], "str": 40}}},
            ),
            ("attributes.str must be an integer in [1, 30]",),
        ),
    ]
    message = stat_failure_message(issues)
    assert "still invalid after the repair passes" in message
    assert "E0" in message and "E1" in message
    assert "attributes.str" in message


# ---------------------------------------------------------------------------
# Validator: combat-power enforcement
# ---------------------------------------------------------------------------


def _power_attributes() -> dict[str, int]:
    return {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}


def test_underpowered_block_flagged_with_numbers() -> None:
    """UNDER_FLAG / acceptance: a Void-Stalker-shaped CR 18 block fires
    under-powered naming DPR vs 111-116."""
    block = {
        "identity": {"role": "Monster", "cr": 18, "race": "Void Stalker"},
        "attributes": _power_attributes(),
        "combat": {"ac": 18, "hp": 330},
        "actions": [{"name": "Void Ray", "description": "3d10+6 necrotic"}],
    }
    assert validate_stat_block(block) == [
        "under-powered for CR 18: estimated DPR 22.5 vs 111-116 expected"
    ]


def test_overpowered_block_flagged() -> None:
    """OVER_FLAG: a level 5 dealing ~60 DPR fires over-powered vs 33-38."""
    block = {
        "identity": {"role": "NPC", "level": 5, "race": "Human"},
        "attributes": _power_attributes(),
        "actions": [{"name": "Slam", "description": "10d10+5 force"}],
        "combat": {"ac": 16, "hp": 140},
    }
    assert validate_stat_block(block) == [
        "over-powered for level 5: estimated DPR 60.0 vs 33-38 expected"
    ]


def test_on_target_block_passes() -> None:
    """ON_TARGET: a CR 18 at ~100 DPR raises no power violation."""
    block = {
        "identity": {"role": "Monster", "cr": 18, "race": "Void Stalker"},
        "attributes": _power_attributes(),
        "combat": {"ac": 18, "hp": 330},
        "actions": [{"name": "Void Ray", "description": "17d10+6 necrotic"}],
    }
    assert validate_stat_block(block) == []


def test_multiattack_block_audited_through_validation() -> None:
    """MULTIATTACK (validation level): 3 x Claw lands inside the CR 10
    band, so no power violation joins the verdict."""
    block = {
        "identity": {"role": "Monster", "cr": 10, "race": "Beast"},
        "attributes": _power_attributes(),
        "combat": {"ac": 14, "hp": 210},
        "actions": [
            {"name": "Multiattack", "description": "makes three Claw attacks"},
            {"name": "Claw", "description": "3d10+6 slashing"},
        ],
    }
    assert validate_stat_block(block) == []


def test_diceless_block_abstains_from_power() -> None:
    """NO_DICE_ABSTAIN: a scholar with no damage expressions raises no
    power violation (non-combatants exempt)."""
    block = {
        "identity": {"role": "NPC", "level": 10, "race": "Human"},
        "attributes": _power_attributes(),
        "combat": {"ac": 10, "hp": 110},
        "actions": [{"name": "Lecture", "description": "a devastating argument"}],
    }
    assert validate_stat_block(block) == []


def test_out_of_band_level_abstains_from_power() -> None:
    """NO_BAND_ABSTAIN: level 99 fires the shape error but no power
    violation joins the verdict."""
    errors = validate_stat_block({**VALID, "identity": {**VALID["identity"], "level": 99}})
    assert any("identity.level" in e for e in errors)
    assert not any("powered" in e or "frail" in e for e in errors)


def test_hp_frail_flagged_with_numbers() -> None:
    """HP_FRAIL: a CR 18 with 40 HP fires frail naming HP vs 326-340."""
    block = {
        "identity": {"role": "Monster", "cr": 18, "race": "Void Stalker"},
        "attributes": _power_attributes(),
        "combat": {"ac": 18, "hp": 40},
        "actions": [{"name": "Void Ray", "description": "17d10+6 necrotic"}],
    }
    assert validate_stat_block(block) == ["combat.hp 40 is frail for CR 18 (expected HP 326-340)"]


def test_hp_tank_never_flagged() -> None:
    """HP_TANK_OK: a CR 18 with 500 HP raises no power violation."""
    block = {
        "identity": {"role": "Monster", "cr": 18, "race": "Void Stalker"},
        "attributes": _power_attributes(),
        "combat": {"ac": 18, "hp": 500},
        "actions": [{"name": "Void Ray", "description": "17d10+6 necrotic"}],
    }
    assert validate_stat_block(block) == []


def test_bbeg_underpowered_flagged_on_level_band() -> None:
    """The boss tier keys DPR off level like NPCs: a BBEG 12 dealing
    6.5 DPR fires under-powered vs 75-80."""
    block = {
        "identity": {"role": "BBEG", "level": 12, "race": "Human"},
        "attributes": _power_attributes(),
        "actions": [{"name": "Dagger", "description": "1d8+2 piercing"}],
        "combat": {"ac": 17, "hp": 230},
    }
    assert validate_stat_block(block) == [
        "under-powered for level 12: estimated DPR 6.5 vs 75-80 expected"
    ]


def test_bbeg_overpowered_flagged_on_level_band() -> None:
    """A BBEG 5 dealing ~60 DPR fires over-powered vs 33-38."""
    block = {
        "identity": {"role": "BBEG", "level": 5, "race": "Human"},
        "attributes": _power_attributes(),
        "actions": [{"name": "Slam", "description": "10d10+5 force"}],
        "combat": {"ac": 16, "hp": 140},
    }
    assert validate_stat_block(block) == [
        "over-powered for level 5: estimated DPR 60.0 vs 33-38 expected"
    ]


def test_non_string_action_description_returns_shape_error() -> None:
    """A non-string description records the shape violation — the audit
    coerces it, so validation never raises TypeError on malformed LLM
    output the repair loop exists to handle."""
    block = {
        "identity": {"role": "NPC", "level": 5, "race": "Human"},
        "attributes": _power_attributes(),
        "actions": [{"name": "Slam", "description": 123}],
        "combat": {"ac": 16, "hp": 140},
    }
    assert validate_stat_block(block) == ["actions entries must have a string 'description'"]


def test_legendary_budget_flips_dpr_verdict() -> None:
    """Same 27-DPR block: on-target without legendary actions,
    over-powered with them (27 x 2 = 54 vs 33-38)."""
    base: dict[str, Any] = {
        "identity": {"role": "NPC", "level": 5, "race": "Human"},
        "attributes": _power_attributes(),
        "actions": [{"name": "Longsword", "description": "4d10+5 slashing"}],
        "combat": {"ac": 16, "hp": 140},
    }
    assert validate_stat_block(base) == []
    bossed = {**base, "boss": {"legendary_actions": "A tail sweep each round."}}
    assert validate_stat_block(bossed) == [
        "over-powered for level 5: estimated DPR 54.0 vs 33-38 expected"
    ]


def test_save_half_and_aoe_adjustments_enforced() -> None:
    """Nominal 21 DPR passes level 5 only through the adjustments
    (21 x 0.75 x 2 = 31.5); raw nominal alone would flag under."""
    block = {
        "identity": {"role": "NPC", "level": 5, "race": "Human"},
        "attributes": _power_attributes(),
        "actions": [
            {"name": "Burst", "description": "6d6 fire in a 20-ft radius, DC 13 save for half"}
        ],
        "combat": {"ac": 16, "hp": 140},
    }
    assert validate_stat_block(block) == []


# ---------------------------------------------------------------------------
# Repair prompt: DPR guidance (spec-stat-repair-dpr-guidance)
# ---------------------------------------------------------------------------


def _record_entity(
    position: int, *, role: Any, level_cr: Any, name: str = "Mira Vane"
) -> StatIssue:
    """One MISSING-block issue carrying an AR24 record (role/level_cr)."""
    entity = models.EntityInput(
        kind="character", name=name, data={"role": role, "level_cr": level_cr}
    )
    return StatIssue(position, entity, ("stat_block section missing",))


def test_repair_prompt_missing_with_level_names_band() -> None:
    """MISSING_WITH_LEVEL: a level-5 record names its 33-38 band; a level-20
    record names 123-140."""
    prompt = build_stat_repair_prompt([_record_entity(1, role="NPC", level_cr="level 5")])
    assert "record target: level 5 -> hit DPR band 33-38" in prompt
    prompt20 = build_stat_repair_prompt([_record_entity(0, role="BBEG", level_cr="level 20")])
    assert "record target: level 20 -> hit DPR band 123-140" in prompt20


def test_repair_prompt_missing_no_level_omits_target_keeps_recipes() -> None:
    """MISSING_NO_LEVEL: blank, garbled, and out-of-range level_cr yield no
    target line — but the generic recipes and the declare-a-level valve do."""
    for level_cr in ("", "   ", "lvl five", "level 99", "CR 5", "level twenty"):
        prompt = build_stat_repair_prompt([_record_entity(1, role="NPC", level_cr=level_cr)])
        assert "record target:" not in prompt, level_cr
    prompt = build_stat_repair_prompt([_record_entity(1, role="NPC", level_cr="")])
    assert "MISSING" in prompt
    assert "DAMAGE RECIPES" in prompt
    assert "d4 2.5" in prompt and "d12 6.5" in prompt
    assert "makes three attacks" in prompt and "makes four attacks" in prompt
    assert "declare an identity.level your damage supports" in prompt


def test_repair_prompt_monster_target_names_cr_band() -> None:
    """MONSTER_TARGET: a CR 5 record names its 33-38 band; a fractional CR
    names its own band; a mismatched record (Monster with level text) omits."""
    prompt = build_stat_repair_prompt([_record_entity(2, role="Monster", level_cr="CR 5")])
    assert "record target: CR 5 -> hit DPR band 33-38" in prompt
    fractional = build_stat_repair_prompt([_record_entity(2, role="Monster", level_cr="CR 1/2")])
    assert "record target: CR 1/2 -> hit DPR band 6-8" in fractional
    mismatched = build_stat_repair_prompt([_record_entity(2, role="Monster", level_cr="level 5")])
    assert "record target:" not in mismatched


def test_repair_prompt_dpr_guidance_deterministic_and_valved() -> None:
    """DETERMINISM: the target lines, recipes, and escape valves render
    byte-identical across calls."""
    issues = [
        _record_entity(0, role="NPC", level_cr="level 5"),
        _record_entity(2, role="Monster", level_cr="CR 5"),
    ]
    first = build_stat_repair_prompt(issues)
    assert build_stat_repair_prompt(issues) == first
    assert first.count("record target:") == 2
    assert "You MAY lower identity.level" in first
    assert "zero dice anywhere and the block is exempt" in first
    assert "one weak attack is worse than none" in first
    # The existing OUTPUT CONTRACT is untouched.
    assert first.endswith("one ref per entry, nothing else.")


# ---------------------------------------------------------------------------
# Repair prompt: DPR guidance round 2 (review patches)
# ---------------------------------------------------------------------------


def test_repair_prompt_recipes_cover_audit_adjustments() -> None:
    """Every audit adjustment the prompt teaches is pinned: AoE doubling,
    the legendary extra attack, HP minimums, interpolation, precedence,
    the bounded fallback, and the modifiers-aware diceless valve."""
    prompt = build_stat_repair_prompt([_record_entity(1, role="NPC", level_cr="level 5")])
    assert "hit each character's record target band" in prompt
    assert "counts double (assumed 2 targets)" in prompt
    assert "Adjustment factors multiply" in prompt
    assert "boss.legendary_actions adds one full extra attack" in prompt
    assert "size base damage" in prompt and "one attack lower" in prompt
    assert "level 1: 71+" in prompt and "20: 356+" in prompt
    assert "CR targets use the same table row" in prompt
    assert "below half the low" in prompt and "fails frail" in prompt
    assert "interpolate between the neighboring recipes" in prompt
    assert "Prefer hitting the record target band" in prompt
    assert "the HIGHEST such level" in prompt
    assert "never level 1 for an archmage concept" in prompt
    assert "no +/-N damage modifiers" in prompt
    assert "only `actions` are audited" in prompt


def test_repair_prompt_target_word_boundaries() -> None:
    """`cr`/`level` need word boundaries: embedded matches never name a band."""
    assert "record target:" not in build_stat_repair_prompt(
        [_record_entity(2, role="Monster", level_cr="sacred 5")]
    )
    assert "record target:" not in build_stat_repair_prompt(
        [_record_entity(1, role="NPC", level_cr="sublevel 5")]
    )


def test_repair_prompt_target_tolerates_missing_whitespace() -> None:
    """`CR5` / `level5` still resolve to their bands."""
    prompt = build_stat_repair_prompt([_record_entity(2, role="Monster", level_cr="CR5")])
    assert "record target: CR 5 -> hit DPR band 33-38" in prompt
    prompt = build_stat_repair_prompt([_record_entity(1, role="NPC", level_cr="level5")])
    assert "record target: level 5 -> hit DPR band 33-38" in prompt


def test_repair_prompt_target_rejects_decimal_challenges() -> None:
    """`CR 0.5` / `level 5.5` omit instead of truncating to `CR 0` / `level 5`."""
    assert "record target:" not in build_stat_repair_prompt(
        [_record_entity(2, role="Monster", level_cr="CR 0.5")]
    )
    assert "record target:" not in build_stat_repair_prompt(
        [_record_entity(1, role="NPC", level_cr="level 5.5")]
    )


def test_repair_prompt_target_tolerates_fractional_spacing() -> None:
    """`CR 1 / 2` (and tab-separated variants) resolve like `CR 1/2`."""
    prompt = build_stat_repair_prompt([_record_entity(2, role="Monster", level_cr="CR 1 / 2")])
    assert "record target: CR 1/2 -> hit DPR band 6-8" in prompt
    prompt = build_stat_repair_prompt([_record_entity(2, role="Monster", level_cr="CR 1\t/\t2")])
    assert "record target: CR 1/2 -> hit DPR band 6-8" in prompt


def test_repair_prompt_target_renders_point_band_open_ended() -> None:
    """CR 30's point band (303.0, 303.0) renders as `303+`, not `303-303`."""
    prompt = build_stat_repair_prompt([_record_entity(2, role="Monster", level_cr="CR 30")])
    assert "record target: CR 30 -> hit DPR band 303+" in prompt
    assert "303-303" not in prompt


# ---------------------------------------------------------------------------
# Repair prompt: spells + tiny-creature valves (spec-stat-repair-spells-tiny)
# ---------------------------------------------------------------------------
def test_repair_prompt_spells_valve_present() -> None:
    """SPELLS_VALVE: classless spells are the systematic repair miss — the
    prompt names the coupling (one class, drop uncovered, or delete) outright."""
    prompt = build_stat_repair_prompt([_record_entity(0, role="NPC", level_cr="level 5")])
    assert (
        "spells need identity.class from the SRD list (never for Monster): set one class\n"
        "whose list holds every spell, drop uncovered spells, or delete the spells array." in prompt
    )


def test_repair_prompt_tiny_valve_present() -> None:
    """TINY_VALVE: frail tiny HP needs the CR-row floor plus the CR-0
    diceless escape; numbers quoted from combat.CR_HP (1/4: 36, 1/2: 50,
    5: 131) and CR_DPR (CR 0 overs above 1.2)."""
    prompt = build_stat_repair_prompt([_record_entity(2, role="Monster", level_cr="CR 1/4")])
    assert "HP floor follows the DPR row (CR 1/4: 36+, CR 1/2: 50+, CR 5: 131+)" in prompt
    assert "Tiny creatures: CR 0 with zero dice anywhere" in prompt
    assert "a single die averages over 1.2 DPR and overs" in prompt
    assert (
        "Tiny NPC/BBEG that must stay leveled: zero dice and hp at/above half the band\n"
        "low (level 1: 36+)." in prompt
    )


def test_repair_prompt_valve_numbers_match_combat_tables() -> None:
    """The valve's quoted lows are the live CR_HP lows, not stale prose."""
    from app.pipeline.combat import CR_HP

    assert CR_HP["1/4"][0] == 36
    assert CR_HP["1/2"][0] == 50
    assert CR_HP[5][0] == 131
    assert CR_HP[0] == (1, 6)


def test_repair_prompt_prior_pins_intact() -> None:
    """PIN_KEEP: the earlier recipe/valve lines survive byte-identical."""
    prompt = build_stat_repair_prompt([_record_entity(1, role="NPC", level_cr="level 5")])
    assert "You MAY lower identity.level" in prompt
    assert "zero dice anywhere and the block is exempt" in prompt
    assert "one weak attack is worse than none" in prompt
    assert "counts double (assumed 2 targets)" in prompt
    assert "Adjustment factors multiply" in prompt
    assert "boss.legendary_actions adds one full extra attack" in prompt
    assert "level 1: 71+" in prompt and "20: 356+" in prompt
    assert "CR targets use the same table row" in prompt
    assert "below half the low" in prompt and "fails frail" in prompt
    assert "interpolate between the neighboring recipes" in prompt
    assert "Prefer hitting the record target band" in prompt
    assert "the HIGHEST such level" in prompt
    assert "never level 1 for an archmage concept" in prompt
    assert "no +/-N damage modifiers" in prompt
    assert "only `actions` are audited" in prompt
    assert "hit each character's record target band" in prompt


# ---------------------------------------------------------------------------
# Repair prompt: challenge-number valve (spec-stat-repair-cr-valve)
# ---------------------------------------------------------------------------
def test_repair_prompt_challenge_valve_present() -> None:
    """CR_VALVE: the challenge number is never omittable — Monster identity.cr,
    NPC/BBEG identity.level, bare integers, never the other role's key."""
    prompt = build_stat_repair_prompt([_record_entity(2, role="Monster", level_cr="")])
    assert (
        "Challenge number is REQUIRED inside identity, never omitted: Monster carries\n"
        'identity.cr as a bare integer 0-30 (fractions as quoted strings "1/8", "1/4",\n'
        '"1/2" — a bare 1/2 is invalid JSON); NPC/BBEG carry identity.level as a\n'
        "bare integer 1-20; never floats, quoted numbers, booleans, or null (no 0.5,\n"
        'no "8", no 5.0);\n'
        "never carry the other role's key. (The record target line's\n"
        '"CR 5" is display text — the identity value is 5.)' in prompt
    )


def test_repair_prompt_challenge_valve_prior_pins_intact() -> None:
    """PIN_KEEP: the challenge valve lands alongside every earlier pin."""
    prompt = build_stat_repair_prompt([_record_entity(1, role="NPC", level_cr="level 5")])
    assert "Challenge number is REQUIRED inside identity, never omitted" in prompt
    assert "You MAY lower identity.level" in prompt
    assert "zero dice anywhere and the block is exempt" in prompt
    assert "one weak attack is worse than none" in prompt
    assert "counts double (assumed 2 targets)" in prompt
    assert "Adjustment factors multiply" in prompt
    assert "boss.legendary_actions adds one full extra attack" in prompt
    assert "declare an identity.level your damage supports" in prompt
    assert (
        "spells need identity.class from the SRD list (never for Monster): set one class\n"
        "whose list holds every spell, drop uncovered spells, or delete the spells array." in prompt
    )
    assert "Tiny creatures: CR 0 with zero dice anywhere" in prompt


def test_challenge_valve_round_trip_missing_cr_then_cr8() -> None:
    """A Monster block missing cr fails on identity.cr; the same block with
    cr 8 added passes — the prescribed exit validates end to end."""
    identity = {k: v for k, v in MONSTER["identity"].items() if k != "cr"}
    combat = {**MONSTER["combat"], "hp": 100}
    missing = {**MONSTER, "identity": identity, "combat": combat}
    assert any("identity.cr" in e for e in validate_stat_block(missing))
    fixed = {**missing, "identity": {**identity, "cr": 8}}
    assert validate_stat_block(fixed) == []


# ---------------------------------------------------------------------------
# Structured attack damage + the optional mechanics aspects (spec 2026-09-11)
# ---------------------------------------------------------------------------

#: VALID plus every optional aspect the spec adds: hit dice, real save
#: bonuses (distinct from the ability modifiers), initiative, passive
#: perception, proficiency, spellcasting and resource pools — and one
#: action carrying its damage as parts rather than prose only.
STRUCTURED: dict[str, Any] = {
    **VALID,
    "combat": {"ac": 16, "hp": 66, "hit_dice": "12d10 + 0"},
    "saves": {"con": 5, "wis": 3},
    "initiative": 2,
    "passive_perception": 12,
    "proficiency_bonus": 3,
    "spellcasting": {"dc": 13, "attack_bonus": 5, "slots": [4, 3, 0]},
    "resources": {"second_wind": 1, "action_surge": 0},
    "actions": [
        {
            "name": "Longsword",
            "to_hit": 5,
            "description": "Melee Weapon Attack: +5 to hit, 4d10 + 5 slashing",
            "damage": [
                {
                    "dice": "4d10",
                    "count": 4,
                    "sides": 10,
                    "bonus": 5,
                    "average": 27,
                    "type": "slashing",
                }
            ],
        }
    ],
}


def test_optional_mechanics_are_accepted() -> None:
    """STRUCTURED_DAMAGE / NEW_STATS_PRESENT: a block carrying the new
    aspects — and a damage list on its action — validates clean."""
    assert validate_stat_block(STRUCTURED) == []


def test_absent_optional_mechanics_still_validate() -> None:
    """NEW_STATS_ABSENT: a block written before these fields existed is
    untouched by the change — same verdict as always."""
    assert validate_stat_block(VALID) == []
    assert validate_stat_block(MONSTER) == []


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"saves": {"luck": 3}}, "saves key"),
        ({"saves": {"str": "high"}}, "saves.str"),
        ({"initiative": "high"}, "initiative"),
        ({"passive_perception": 0}, "passive_perception"),
        ({"proficiency_bonus": 20}, "proficiency_bonus"),
        ({"spellcasting": {"dc": "high"}}, "spellcasting.dc"),
        ({"spellcasting": {"slots": [4, -1]}}, "spellcasting.slots"),
        ({"resources": {"lay_on_hands": -3}}, "resources.lay_on_hands"),
        ({"combat": {"ac": 16, "hp": 66, "hit_dice": "lots"}}, "combat.hit_dice"),
        ({"features": ["Divine Smite", ""]}, "features"),
        ({"features": [{"name": "Divine Smite"}]}, "features"),
    ],
)
def test_malformed_optional_mechanics_are_flagged(patch: dict[str, Any], expected: str) -> None:
    """A present field that can name nothing legal is a violation naming
    the field — never a silent wrong number on the sheet."""
    block = {**STRUCTURED, **patch}
    violations = validate_stat_block(block)
    assert any(expected in violation for violation in violations), violations


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        ("not-a-list", "actions[0].damage must be a non-empty list"),
        ([], "actions[0].damage must be a non-empty list"),
        (["2d6"], "actions[0].damage[0] must be an object"),
        ([{"type": "slashing"}], "needs a dice expression"),
        ([{"dice": "big", "count": 2, "sides": 6}], "actions[0].damage[0].dice"),
        ([{"count": 2, "sides": 6, "bonus": "5"}], "actions[0].damage[0].bonus"),
        ([{"count": 2, "sides": 6, "average": -1}], "actions[0].damage[0].average"),
        ([{"count": 2, "sides": 6, "type": "  "}], "actions[0].damage[0].type"),
    ],
)
def test_damage_part_shape_is_validated(parts: Any, expected: str) -> None:
    block = {**VALID, "actions": [{"name": "Longsword", "description": "...", "damage": parts}]}
    assert any(expected in violation for violation in validate_stat_block(block))


def test_damage_part_with_dice_only_passes() -> None:
    """The model need not write every derived slot: a part with a dice
    expression is valid (the canonicalizer completes the rest) and its sum
    is what the auditor reads."""
    block = {
        **VALID,
        "actions": [
            {
                "name": "Longsword",
                "description": "...",
                "damage": [{"dice": "4d10", "bonus": 5, "type": "slashing"}],
            }
        ],
    }
    assert validate_stat_block(block) == []


def test_canonicalize_completes_damage_parts() -> None:
    """STRUCTURED_DAMAGE: count/sides/average are derived from the part's
    own dice and bonus, extra keys survive, and a complete part is left
    byte-identical (the same object comes back)."""
    from app.pipeline.statblocks import canonicalize_stat_block

    block = {
        **VALID,
        "actions": [
            {
                "name": "Claw",
                "description": "x",
                "damage": [{"dice": "2d6", "bonus": 12, "reach_ft": 5}],
            }
        ],
    }
    canonical = canonicalize_stat_block(block)
    part = canonical["actions"][0]["damage"][0]
    assert part["count"] == 2 and part["sides"] == 6 and part["bonus"] == 12
    assert part["average"] == 19  # 2 * 3.5 + 12
    assert part["type"] == "untyped"  # absent type names nothing, so it is explicit
    assert part["reach_ft"] == 5  # the model's own key is not dropped

    complete = {**block, "actions": [{"name": "Claw", "description": "x", "damage": [part]}]}
    assert canonicalize_stat_block(complete) is complete


def test_canonicalize_folds_prototype2_nesting() -> None:
    """The stats nesting folds into the flat canonical block, filling only
    absent slots (a canonical field always wins), and nothing is dropped —
    including ``features``, which stays its own list of names."""
    from app.pipeline.statblocks import canonicalize_stat_block

    prototype = {
        "identity": {"role": "NPC", "level": 17, "race": "Human", "class": "Paladin"},
        "attributes": {"str": 28, "dex": 16, "con": 26, "int": 12, "wis": 20, "cha": 24},
        "combat": {"ac": 21, "hp": 324},
        "features": ["Divine Smite", "Aura of Protection"],
        "stats": {
            "abilities": {"str": 30},  # must NOT overwrite the canonical attributes
            "saves": {"con": 15},
            "initiative": 3,
            "passive_perception": 15,
            "combat": {"hit_dice": "24d10 + 192"},
            "spellcasting": {"dc": 21, "attack_bonus": 13, "slots": [4, 3, 3, 3, 1]},
            "resources": {"lay_on_hands": 85},
        },
        "routine": [{"name": "Multiattack", "description": "makes 3 attacks"}],
    }
    canonical = canonicalize_stat_block(prototype)
    assert canonical["features"] == ["Divine Smite", "Aura of Protection"]
    assert "traits" not in canonical  # a name list is not a traits list
    assert canonical["attributes"]["str"] == 28  # the canonical block wins
    assert canonical["saves"] == {"con": 15}
    assert canonical["initiative"] == 3 and canonical["passive_perception"] == 15
    assert canonical["combat"]["hit_dice"] == "24d10 + 192"
    assert canonical["spellcasting"]["dc"] == 21
    assert canonical["resources"] == {"lay_on_hands": 85}
    assert canonical["stats"]["abilities"]["str"] == 30  # never dropped
    assert canonical["routine"] == prototype["routine"]  # display shape passes through
    assert validate_stat_block(canonical) == []


def test_canonicalize_string_damage_folds_and_keeps_parts() -> None:
    """STRING_DAMAGE still folds into the description; a structured list is
    never folded away."""
    from app.pipeline.statblocks import canonicalize_stat_block

    string_damage = {
        **VALID,
        "actions": [
            {
                "name": "Holy Smite",
                "damage": "5d10+6 radiant",
                "description": "deals massive radiant damage",
            }
        ],
    }
    folded = canonicalize_stat_block(string_damage)
    assert "5d10+6 radiant" in folded["actions"][0]["description"]
    assert isinstance(folded["actions"][0]["damage"], str)  # left where it was

    structured = canonicalize_stat_block(STRUCTURED)
    assert isinstance(structured["actions"][0]["damage"], list)
    assert structured["actions"][0]["description"] == STRUCTURED["actions"][0]["description"]


def test_rules_text_asks_for_the_structured_shape() -> None:
    """The prompt is where the shape comes from: it must name the damage
    list and every optional aspect, or the model never writes them."""
    rules = stat_block_rules_text()
    assert '"damage": [{"dice": "2d6"' in rules
    assert "Multiattack routine carries NO damage list" in rules
    for field in ("combat.hit_dice", "saves", "initiative", "passive_perception"):
        assert field in rules
    for field in ("proficiency_bonus", "spellcasting", "resources"):
        assert field in rules


def test_conform_moves_structured_damage_with_the_prose() -> None:
    """STRUCTURED_CONFORM: the deterministic conform keeps the parts and
    the description in step — the auditor reads the parts, so a part left
    behind would silently undo the lift."""
    from app.pipeline import combat
    from app.pipeline.statblocks import conform_power

    weak = {
        "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter"},
        "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
        "combat": {"ac": 16, "hp": 66},
        "actions": [
            {
                "name": "Longsword",
                "to_hit": 5,
                "description": "Melee Weapon Attack: +5 to hit, 1d8 + 2 slashing",
                "damage": [
                    {
                        "dice": "1d8",
                        "count": 1,
                        "sides": 8,
                        "bonus": 2,
                        "average": 6.5,
                        "type": "slashing",
                    }
                ],
            }
        ],
    }
    conformed = conform_power(weak)
    assert conformed is not None
    audit = combat.audit_stat_block(conformed)
    assert audit.band is not None and audit.band[0] <= audit.dpr <= audit.band[1]
    assert validate_stat_block(conformed) == []
    # The scored-off block's own action survives; its parts carry the lift.
    action = conformed["actions"][0]
    assert action["name"] == "Longsword"
    parts = action["damage"]
    assert parts and parts[-1]["average"] > 6.5
    assert parts[-1]["average"] == pytest.approx(
        parts[-1]["count"] * (parts[-1]["sides"] + 1) / 2 + parts[-1]["bonus"]
    )
    # The prose states the same rider the last part carries.
    assert f"{parts[-1]['average']:g}" in action["description"]


def test_damage_parts_sentence_states_the_parts_it_reads() -> None:
    """The exported/prose damage sentence comes from the PARTS: one clause
    per usable part (riders chained with "plus"), the average from the
    part's own dice when the model omitted it, and ``None`` when no part
    names a die — the caller then keeps the prose it already has."""
    from app.pipeline.statblocks import damage_parts_sentence

    parts = [
        {"dice": "2d6", "count": 2, "sides": 6, "bonus": 12, "average": 19, "type": "slashing"},
        {"dice": "3d10", "count": 3, "sides": 10, "bonus": 0, "type": "radiant"},
    ]
    assert damage_parts_sentence(parts) == (
        "Hit: 19 (2d6 + 12) slashing damage plus 16.5 (3d10) radiant damage."
    )
    assert damage_parts_sentence("2d6 + 4 slashing") is None  # a folded string is not parts
    assert damage_parts_sentence([{"dice": "2d6"}]) is None  # no count/sides: unusable
    assert damage_parts_sentence([]) is None
