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

#: A valid AR25 minimal stat block (NPC, level 5 Human Fighter).
VALID: dict[str, Any] = {
    "identity": {"role": "NPC", "level": 5, "race": "Human", "class": "Fighter", "alignment": "LG"},
    "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
    "combat": {"ac": 16, "hp": 44},
    "skills": [{"name": "Athletics", "bonus": 5}],
    "actions": [
        {"name": "Longsword", "description": "Melee Weapon Attack: +5 to hit, 1d8+2 slashing"}
    ],
}

#: A valid Monster block (goblin: CR 1/4, unaligned race type).
MONSTER: dict[str, Any] = {
    "identity": {"role": "Monster", "cr": "1/4", "race": "Goblin", "alignment": "unaligned"},
    "attributes": {"str": 8, "dex": 14, "con": 10, "int": 9, "wis": 11, "cha": 8},
    "combat": {"ac": 15, "hp": 7},
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
        "combat": {"ac": 10, "hp": 20},
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
        "combat": {"ac": 10, "hp": 8},
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


def test_cr_bounds_and_fractions_enforced() -> None:
    for cr in (0, CR_MAX, "1/8", "1/4", "1/2"):
        block = {**MONSTER, "identity": {**MONSTER["identity"], "cr": cr}}
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


def test_spells_require_class_and_are_role_limited() -> None:
    """AR25's example: role=Wizard limits spells to the wizard list."""
    wizard: dict[str, Any] = {
        "identity": {"role": "BBEG", "level": 12, "race": "Human", "class": "Wizard"},
        "attributes": {"str": 8, "dex": 14, "con": 12, "int": 18, "wis": 12, "cha": 10},
        "combat": {"ac": 15, "hp": 80},
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
    declaration needs tougher combat numbers and scores (not a flat block).
    This is generation guidance, not validation — the validator still only
    enforces ranges and vocabulary."""
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
        json.dumps({"stat_blocks": [{"ref": "E0", "stat_block": VALID}]}),  # E1 missing
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
    assert "still invalid after the repair pass" in message
    assert "E0" in message and "E1" in message
    assert "attributes.str" in message
