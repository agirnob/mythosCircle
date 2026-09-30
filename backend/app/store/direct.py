"""The fully-authored character path's canonical payload schema (spec:
hybrid authorship — two paths, template-driven direct commit).

ONE shape definition, consumed by BOTH validation gates so they can
never disagree:

- the synchronous enqueue gate (``store.jobs._validate_add_character_payload``
  via :func:`validate_add_character_payload`, 422 at ``POST
  /api/characters``, zero job rows), and
- the execution worker backstop (``pipeline.direct.run_add_character``
  re-runs the same validators INSIDE the commit transaction — the F5
  queue-window race resolves as a named ``STRUCTURAL_VALIDATION_FAILURE``,
  zero LLM, zero commits).

Pure data + functions: no DB access and no pipeline imports (the
dependency direction is pipeline -> store), mirroring
``store.candidates`` — the AR24 record shape below IS the
``payload_section_violations`` shape, imported, not re-declared.

The canonical template form (owner ruling F1-F2 — prose lives only in
description slots):

- the record is the flat AR24 object (``payload_section_violations``
  owns completeness; the key set is closed here — unknown keys are
  schema violations, never ignored);
- stat-block skill entries are ``{name, description}`` text blocks and
  actions are ``{name, description, damage?}`` with ``damage`` matching
  :data:`DICE_PATTERN` (the form normalizes the spaced ``2d6 + 2``
  variant before submit; the gate enforces the tight form) — the export
  renders a bonus-less skill as the bare name and a parts-less action
  from its prose, so the committed block never breaks a consumer;
- every structural field (roles, classes, levels, dice) validates
  against the shared AR25 machinery: the block is checked through a
  mechanical view (description-skills and dice-string damage expressed
  as their AR25 equivalents) so ``knowledge.validate_stat_block`` —
  vocabularies, ranges, role rules, the power audit — is THE validator,
  with violations reported against the submitted paths.
"""

import re
from typing import Any

from app.store.candidates import (
    BOSS_FIELDS,
    IDENTITY_FIELDS,
    LORE_FIELDS,
    ROLES,
    payload_section_violations,
)
from app.store.commit import ARCHETYPES, DIAL_LEVELS, EDGE_KIND_RULES, EDGE_TYPES

#: The canonical dice formula for an authored action's ``damage`` slot:
#: tight operators only (``2d6`` / ``1d8+7`` / ``2d6-1``) — the form
#: normalizes the optional spaced variant before submission.
DICE_PATTERN = r"^\d+d\d+([+-]\d+)?$"

DICE_RE = re.compile(DICE_PATTERN)

#: The AR24 record key set a fully-authored record may carry — the
#: authorable set (candidates.py AR24 sections + the four AR19 fields +
#: the stat block + the AD-36 ``dial`` elaboration key). Anything else
#: is DIRECT_UNKNOWN_KEY.
RECORD_KEYS: frozenset[str] = (
    frozenset({"name", "role", "stat_block", "world_integration", "boss", "personality"})
    | frozenset({"secret", "rumor", "party_hook"})
    | frozenset(IDENTITY_FIELDS)
    | frozenset(LORE_FIELDS)
    | frozenset(BOSS_FIELDS)
    | frozenset({"dial"})
)

#: The AR25 stat-block sections the canonical template knows. Unknown
#: top-level sections are schema violations (a hand-rolled key can name
#: nothing the sheet renders).
STAT_BLOCK_KEYS: frozenset[str] = frozenset(
    {
        "identity",
        "attributes",
        "combat",
        "saves",
        "initiative",
        "passive_perception",
        "proficiency_bonus",
        "spellcasting",
        "resources",
        "features",
        "skills",
        "actions",
        "traits",
        "spells",
    }
)

#: The keys of one declared relation: the edge type, the optional
#: per-type counter, and EXACTLY ONE of the three target tiers.
RELATION_KEYS: frozenset[str] = frozenset(
    {"type", "counter", "target_id", "target_key", "target_name"}
)

#: The keys of one staged character sheet in the ``add_character`` payload.
SHEET_KEYS: frozenset[str] = frozenset({"key", "record", "relations"})

#: The per-section SeedEntry key sets of the hybrid build-in path
#: (spec: ``str | SeedEntry``). Figures carry the pinned role and the
#: authored record; places/factions carry the authored description.
FIGURE_SEED_KEYS: frozenset[str] = frozenset({"name", "role", "record", "relations", "key"})
FLAT_SEED_KEYS: frozenset[str] = frozenset(
    {"name", "description", "relations", "key", "archetype", "dial"}
)

#: Cap on staged sheets per ``add_character`` submission (the build-in
#: section cap — one ceiling for both paths).
MAX_CHARACTERS = 100


def relation_violations(raw: Any, where: str, *, allow_target_name: bool) -> list[str]:
    """Shape violations of one declared relation (``[]`` = well-formed).

    ``{type, counter?, target_id | target_key | target_name}`` — exactly
    one target key per relation; ambiguity is resolved at the UI, not
    discovered at runtime (the three-tier contract). ``target_name`` on
    the direct path is a schema violation (path 2 has no mandate access):
    pass ``allow_target_name=False`` there.
    """
    if not isinstance(raw, dict):
        return [f"{where} must be an object"]
    violations: list[str] = []
    unknown = set(raw) - RELATION_KEYS
    if unknown:
        violations.append(f"{where} has unknown key(s): {sorted(unknown)}")
    edge_type = raw.get("type")
    if not isinstance(edge_type, str) or edge_type.strip() not in EDGE_TYPES:
        violations.append(f"{where}.type must be one of {sorted(EDGE_TYPES)}")
    if "counter" in raw and (type(raw["counter"]) is not int):
        violations.append(f"{where}.counter must be an integer")
    tiers = [tier for tier in ("target_id", "target_key", "target_name") if tier in raw]
    if len(tiers) != 1:
        violations.append(f"{where} must carry exactly one of target_id/target_key/target_name")
        return violations
    tier = tiers[0]
    value = raw[tier]
    if tier == "target_id":
        from app.core import ids

        if not isinstance(value, str) or not ids.is_valid_ulid(value):
            violations.append(f"{where}.target_id must be a ULID")
    elif not isinstance(value, str) or not value.strip():
        violations.append(f"{where}.{tier} must be a non-blank string")
    if tier == "target_name" and not allow_target_name:
        violations.append(
            f"{where}.target_name is not allowed on a fully-authored character "
            "(pick a committed entity or stage the target in this batch)"
        )
    return violations


def _skills_view(entry: Any) -> Any:
    """The AR25 view of one canonical skill entry: ``{name, description}``
    becomes ``{name, bonus, description}`` — the shared validator reads a
    bonus (and the audit never does), the committed block keeps the
    submitted shape."""
    if not isinstance(entry, dict):
        return entry
    return {**entry, "bonus": 0}


def _damage_view(damage: Any) -> Any:
    """The AR25 view of a canonical dice-string ``damage`` slot: one
    structured part carrying the same dice — the auditor, ``combat``,
    and the power band read parts, never strings."""
    if isinstance(damage, str) and DICE_RE.fullmatch(damage.strip()):
        dice, sign, bonus = damage.strip(), "", 0
        match = re.fullmatch(r"(\d+d\d+)([+-]\d+)?", damage.strip())
        if match is not None:
            dice, tail = match.group(1), match.group(2)
            if tail:
                sign, digits = tail[0], tail[1:]
                bonus = int(digits) * (-1 if sign == "-" else 1)
        return [{"dice": dice, "bonus": bonus}]
    return damage


def _stat_block_view(block: dict[str, Any]) -> dict[str, Any]:
    """The AR25-shaped view of one canonical authored block (validation
    only — the committed block is byte-identical to the submission)."""
    view = dict(block)
    if isinstance(view.get("skills"), list):
        view["skills"] = [_skills_view(entry) for entry in view["skills"]]
    if isinstance(view.get("actions"), list):
        view["actions"] = [
            {**entry, "damage": _damage_view(entry.get("damage"))}
            if isinstance(entry, dict)
            else entry
            for entry in view["actions"]
        ]
    return view


#: The stat-block subsections an AUTHORED PARTIAL block may carry. Every
#: present subsection must be structurally sound; the absent ones are the
#: generator's job (the nudge contract). ``power`` is a computed stamp —
#: never authored.
STAT_SUBSECTION_KEYS: frozenset[str] = frozenset(
    {"identity", "attributes", "combat", "skills", "actions", "traits", "spells"}
)

IDENTITY_SUBKEYS: frozenset[str] = frozenset({"role", "race", "level", "cr", "class", "alignment"})
ATTRIBUTE_KEYS: frozenset[str] = frozenset({"str", "dex", "con", "int", "wis", "cha"})
COMBAT_SUBKEYS: frozenset[str] = frozenset({"ac", "hp", "hit_dice"})


def _int_violation(value: Any, where: str, low: int, high: int) -> str | None:
    if type(value) is not int or not low <= value <= high:
        return f"{where} must be an integer in [{low}, {high}]"
    return None


def _non_blank_str_violation(value: Any, where: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return f"{where} must be a non-blank string"
    return None


def stat_block_subset_violations(block: Any, where: str = "stat_block") -> list[str]:
    """The per-subsection shape violations of one AUTHORED PARTIAL stat
    block (``[]`` = valid) — the nudge contract's enqueue screen: every
    PRESENT subsection must be structurally sound (the generator fills
    the absent ones), while the full canonical verdict (cross-checks,
    power band) stays the run-time reject-only gate.

    List subsections merge BY NAME at run time (an authored entry
    force-updates the generated entry with the same normalized name;
    unmatched authored entries append), so their entries are screened
    with that shape in mind.
    """
    if not isinstance(block, dict):
        return [f"{where} must be an object"]
    violations: list[str] = []
    unknown = set(block) - STAT_SUBSECTION_KEYS
    if unknown:
        violations.append(
            f"{where} has unknown key(s): {sorted(unknown)} — only "
            f"{sorted(STAT_SUBSECTION_KEYS)} are authorable (power is a generated stamp)"
        )

    identity = block.get("identity")
    if identity is not None:
        if not isinstance(identity, dict):
            violations.append(f"{where}.identity must be an object")
        else:
            identity_unknown = set(identity) - IDENTITY_SUBKEYS
            if identity_unknown:
                violations.append(
                    f"{where}.identity has unknown key(s): {sorted(identity_unknown)}"
                )
            if "role" in identity:
                role = identity["role"]
                if not isinstance(role, str) or role.strip() not in ROLES:
                    violations.append(f"{where}.identity.role must be one of {sorted(ROLES)}")
            for field in ("race", "class", "alignment"):
                if field in identity:
                    violation = _non_blank_str_violation(
                        identity[field], f"{where}.identity.{field}"
                    )
                    if violation:
                        violations.append(violation)
            if "level" in identity:
                violation = _int_violation(identity["level"], f"{where}.identity.level", 1, 20)
                if violation:
                    violations.append(violation)
            if "cr" in identity:
                cr = identity["cr"]
                if type(cr) is not int and not (
                    isinstance(cr, str) and re.fullmatch(r"\d+(/\d+)?", cr.strip())
                ):
                    violations.append(
                        f"{where}.identity.cr must be a positive integer or a fraction ('1/2')"
                    )

    attributes = block.get("attributes")
    if attributes is not None:
        if not isinstance(attributes, dict):
            violations.append(f"{where}.attributes must be an object")
        else:
            for key, value in attributes.items():
                if key not in ATTRIBUTE_KEYS:
                    violations.append(f"{where}.attributes has unknown key {key!r}")
                    continue
                violation = _int_violation(value, f"{where}.attributes.{key}", 1, 30)
                if violation:
                    violations.append(violation)

    combat = block.get("combat")
    if combat is not None:
        if not isinstance(combat, dict):
            violations.append(f"{where}.combat must be an object")
        else:
            for key in ("ac", "hp"):
                if key in combat:
                    value = combat[key]
                    if type(value) is not int or value <= 0:
                        violations.append(f"{where}.combat.{key} must be a positive integer")
            if "hit_dice" in combat:
                violation = _non_blank_str_violation(combat["hit_dice"], f"{where}.combat.hit_dice")
                if violation:
                    violations.append(violation)

    skills = block.get("skills")
    if skills is not None:
        if not isinstance(skills, list):
            violations.append(f"{where}.skills must be a list")
        else:
            for index, entry in enumerate(skills):
                if not isinstance(entry, dict):
                    violations.append(f"{where}.skills[{index}] must be an object")
                    continue
                unknown_keys = set(entry) - {"name", "bonus", "description"}
                if unknown_keys:
                    violations.append(
                        f"{where}.skills[{index}] has unknown key(s): {sorted(unknown_keys)}"
                    )
                violation = _non_blank_str_violation(
                    entry.get("name"), f"{where}.skills[{index}].name"
                )
                if violation:
                    violations.append(violation)
                if "bonus" in entry and type(entry["bonus"]) is not int:
                    violations.append(f"{where}.skills[{index}].bonus must be an integer")
                if "description" in entry:
                    violation = _non_blank_str_violation(
                        entry["description"], f"{where}.skills[{index}].description"
                    )
                    if violation:
                        violations.append(violation)

    actions = block.get("actions")
    if actions is not None:
        if not isinstance(actions, list):
            violations.append(f"{where}.actions must be a list")
        else:
            for index, entry in enumerate(actions):
                if not isinstance(entry, dict):
                    violations.append(f"{where}.actions[{index}] must be an object")
                    continue
                unknown_keys = set(entry) - {"name", "description", "damage", "to_hit"}
                if unknown_keys:
                    violations.append(
                        f"{where}.actions[{index}] has unknown key(s): {sorted(unknown_keys)}"
                    )
                violation = _non_blank_str_violation(
                    entry.get("name"), f"{where}.actions[{index}].name"
                )
                if violation:
                    violations.append(violation)
                violation = _non_blank_str_violation(
                    entry.get("description"), f"{where}.actions[{index}].description"
                )
                if violation:
                    violations.append(violation)
                if "to_hit" in entry and type(entry["to_hit"]) is not int:
                    violations.append(f"{where}.actions[{index}].to_hit must be an integer")
                damage = entry.get("damage")
                if isinstance(damage, str):
                    if not DICE_RE.fullmatch(damage.strip()):
                        violations.append(
                            f"{where}.actions[{index}].damage {damage!r} must match the dice "
                            f'pattern {DICE_PATTERN} (e.g. "2d6+2")'
                        )
                elif isinstance(damage, list):
                    for part_index, part in enumerate(damage):
                        if (
                            not isinstance(part, dict)
                            or not isinstance(part.get("dice"), str)
                            or not part["dice"].strip()
                        ):
                            violations.append(
                                f"{where}.actions[{index}].damage[{part_index}] must carry a "
                                "non-blank 'dice' string"
                            )
                elif damage is not None:
                    violations.append(
                        f"{where}.actions[{index}].damage must be a dice string ('2d6+2')"
                    )

    traits = block.get("traits")
    if traits is not None:
        if not isinstance(traits, list):
            violations.append(f"{where}.traits must be a list")
        else:
            for index, entry in enumerate(traits):
                if not isinstance(entry, dict):
                    violations.append(f"{where}.traits[{index}] must be an object")
                    continue
                unknown_keys = set(entry) - {"name", "description"}
                if unknown_keys:
                    violations.append(
                        f"{where}.traits[{index}] has unknown key(s): {sorted(unknown_keys)}"
                    )
                for field in ("name", "description"):
                    violation = _non_blank_str_violation(
                        entry.get(field), f"{where}.traits[{index}].{field}"
                    )
                    if violation:
                        violations.append(violation)

    spells = block.get("spells")
    if spells is not None:
        if not isinstance(spells, list):
            violations.append(f"{where}.spells must be a list")
        else:
            for index, spell in enumerate(spells):
                violation = _non_blank_str_violation(spell, f"{where}.spells[{index}]")
                if violation:
                    violations.append(violation)

    return violations


def stat_block_is_complete(block: dict[str, Any]) -> bool:
    """A partial authored block is complete iff every REQUIRED canonical
    subsection (identity, attributes, combat) is present — the point
    where whole-block byte-identical semantics apply instead of merge."""
    return all(key in block for key in ("identity", "attributes", "combat"))


def stat_block_violations(block: Any, where: str = "stat_block") -> list[str]:
    """The canonical stat-block violations (``[]`` = valid).

    Runs the SHARED AR25 validator (``knowledge.validate_stat_block``)
    over the mechanical view of the submitted block, so the fixed
    vocabularies, ranges, role rules, and the power band are one
    definition for both authorship paths. The direct gate and the
    hybrid reject-only check share this function.
    """
    from app.pipeline.knowledge import validate_stat_block  # noqa: PLC0415

    if not isinstance(block, dict):
        return [f"{where} must be an object"]
    # A dice-string damage slot that fails the canonical pattern names the
    # pattern itself (the gate's 422 is the form's error message — the
    # shared validator's "non-empty list of damage parts" is the shape
    # verdict for the GENERATED path, not the pattern breach a DM can fix
    # by retyping the formula).
    if isinstance(block.get("actions"), list):
        for index, action in enumerate(block["actions"]):
            if not isinstance(action, dict):
                continue
            damage = action.get("damage")
            if isinstance(damage, str) and not DICE_RE.fullmatch(damage.strip()):
                return [
                    f"{where}.actions[{index}].damage {damage!r} must match the dice "
                    f'pattern {DICE_PATTERN} (e.g. "2d6+2"; the form normalizes '
                    "the spaced variant)"
                ]
    view = _stat_block_view(block)
    return [f"{where}.{violation}" for violation in validate_stat_block(view)]


def record_violations(record: Any, where: str = "record") -> list[str]:
    """One staged sheet's record: closed key set + AR24 completeness +
    the canonical stat block (structure, dice pattern, role consistency).
    """
    if not isinstance(record, dict):
        return [f"{where} must be an object"]
    violations: list[str] = []
    unknown = set(record) - RECORD_KEYS
    if unknown:
        violations.append(f"{where} has unknown key(s): {sorted(unknown)}")
    violations.extend(payload_section_violations(record))
    block = record.get("stat_block")
    if block is None:
        violations.append(f"{where}.stat_block section missing")
    else:
        violations.extend(stat_block_violations(block, f"{where}.stat_block"))
        # The identity anchor is ONE fact in two places (AR24): a record
        # role that disagrees with the block's role is the classic
        # DIRECT_INVALID_RECORD row — rejected before any commit.
        record_role = record.get("role")
        block_role = (
            block.get("identity", {}).get("role")
            if isinstance(block, dict) and isinstance(block.get("identity"), dict)
            else None
        )
        if (
            isinstance(record_role, str)
            and isinstance(block_role, str)
            and record_role.strip().lower() != block_role.strip().lower()
        ):
            violations.append(
                f"{where}.stat_block.identity.role {block_role!r} must match the record role "
                f"{record_role!r}"
            )
    return violations


def declared_target_kinds(relations: Any) -> dict[str, set[str]]:
    """The candidate kinds each declared target_name could be, from the
    edge-kind table (``store.commit.EDGE_KIND_RULES``): the mandate's kind
    inference. A target demanded by a ``located_in`` relation can only be
    a place; one demanded only by unrestricted types defaults to a
    character (the roster's dominant kind). Values are INTERSECTIONS over
    every relation naming that target; a never-constrained target maps to
    ``{"character"}``."""
    demanded: dict[str, set[str]] = {}
    if not isinstance(relations, list):
        return demanded
    for raw in relations:
        if not isinstance(raw, dict) or "target_name" not in raw:
            continue
        name = raw.get("target_name")
        if not isinstance(name, str) or not name.strip():
            continue
        edge_type = raw.get("type")
        rules = (
            EDGE_KIND_RULES.get(edge_type, (None, None))
            if isinstance(edge_type, str)
            else (None, None)
        )
        dst = rules[1]
        constrained = set(dst) if dst is not None else None
        current = demanded.setdefault(
            name.strip(), constrained if constrained is not None else {"character"}
        )
        if constrained is not None:
            current &= constrained
            if not current:
                # Contradictory demands (a located_in AND a worships on the
                # same name): fall back to the roster's dominant kind rather
                # than an ungenerable empty set.
                demanded[name.strip()] = {"character"}
    return demanded


def seed_entry_violations(entry: Any, section: str) -> list[str]:
    """The hybrid build-in seed entry's shape violations (``[]`` = valid).

    A legacy plain string is always valid (STRING_LEGACY). A structured
    entry is a dict: a required non-blank ``name``, the section's key set
    (figures: ``role`` pinned into the closed ROLES set plus the authored
    ``record``; places/factions: ``description``), and well-formed
    declared relations (``target_name`` allowed — path 1 owns the
    mandate). The authored record is only STRUCTURALLY screened here
    (closed keys, object sections) — its reject-only stat-block verdict
    is the RUN-time gate (an invalid authored block fails the job naming
    the entry, never a 422: the DM authored it inside a batch that may
    still be generative).
    """
    if isinstance(entry, str):
        return []
    if not isinstance(entry, dict):
        return [f"build_in {section} entry must be a string or an object"]
    where = f"build_in {section} entry"
    violations: list[str] = []
    keys = FIGURE_SEED_KEYS if section == "key_figures" else FLAT_SEED_KEYS
    unknown = set(entry) - keys
    if unknown:
        violations.append(f"{where} has unknown key(s): {sorted(unknown)}")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        violations.append(f"{where}.name must be a non-blank string")
    elif len(name.strip()) > 2000:
        violations.append(f"{where}.name exceeds 2000 chars")
    key = entry.get("key")
    if key is not None and (not isinstance(key, str) or not key.strip()):
        violations.append(f"{where}.key must be a non-blank string")
    if section == "key_figures":
        role = entry.get("role")
        if role is not None and (not isinstance(role, str) or role.strip() not in ROLES):
            violations.append(f"{where}.role must be one of {sorted(ROLES)}")
        record = entry.get("record")
        if record is not None:
            if not isinstance(record, dict):
                violations.append(f"{where}.record must be an object")
            else:
                record_unknown = set(record) - RECORD_KEYS
                if record_unknown:
                    violations.append(
                        f"{where}.record has unknown key(s): {sorted(record_unknown)}"
                    )
                block = record.get("stat_block")
                if not isinstance(block, (dict, type(None))):
                    violations.append(f"{where}.record.stat_block must be an object")
                elif isinstance(block, dict):
                    violations.extend(
                        stat_block_subset_violations(block, f"{where}.record.stat_block")
                    )
    else:
        dial = entry.get("dial")
        if dial is not None and dial not in DIAL_LEVELS:
            violations.append(f"{where}.dial must be one of {list(DIAL_LEVELS)}")
        archetype = entry.get("archetype")
        if archetype is not None:
            if not isinstance(archetype, str) or not archetype.strip():
                violations.append(f"{where}.archetype must be a non-blank string")
            elif (section[:-1], archetype.strip()) not in {
                (kind, name) for kind, name, _dial in ARCHETYPES
            }:
                kind = "place" if section == "places" else "faction"
                allowed = sorted(name for row_kind, name, _dial in ARCHETYPES if row_kind == kind)
                violations.append(
                    f"{where}.archetype must be one of {allowed} for {kind} entries"
                )
    relations = entry.get("relations")
    if relations is not None:
        if not isinstance(relations, list):
            violations.append(f"{where}.relations must be a list")
        else:
            for index, raw in enumerate(relations):
                violations.extend(
                    relation_violations(raw, f"{where}.relations[{index}]", allow_target_name=True)
                )
    return violations


def validate_add_character_payload(payload: Any) -> list[str]:
    """The canonical schema violations of one ``add_character`` payload
    (``[]`` = valid). The enqueue half of the three-layer gate: every
    violation is a 422 with the exact schema path, BEFORE any job row
    exists.

    The payload is exactly ``{"characters": [<sheet>, ...]}`` — one
    fully-authored character per sheet, each COMPLETE (a structurally
    broken character is a 422 at submit, never an async failure). Tier-2
    well-formedness is checked here too: a ``target_key`` must name
    another sibling sheet's ``key`` in the same payload (the batch IS the
    staged set). A ``target_id`` target's EXISTENCE and kind pair are
    validated by the enqueue gate against the committed world (F5:
    re-resolved at run time) — those need a DB session, which this pure
    validator does not take.
    """
    if not isinstance(payload, dict):
        return ["add_character payload must be a JSON object"]
    unknown = set(payload) - {"characters"}
    if unknown:
        return [f"add_character payload has unknown key(s): {sorted(unknown)}"]
    characters = payload.get("characters")
    if not isinstance(characters, list) or not characters:
        return ["add_character payload must carry a non-empty 'characters' list"]
    if len(characters) > MAX_CHARACTERS:
        return [f"add_character payload exceeds {MAX_CHARACTERS} characters"]
    violations: list[str] = []
    staged_keys: set[str] = set()
    for sheet in characters:
        if isinstance(sheet, dict) and isinstance(sheet.get("key"), str) and sheet["key"].strip():
            staged_keys.add(sheet["key"])
    for index, sheet in enumerate(characters):
        where = f"characters[{index}]"
        if not isinstance(sheet, dict):
            violations.append(f"{where} must be an object")
            continue
        unknown = set(sheet) - SHEET_KEYS
        if unknown:
            violations.append(f"{where} has unknown key(s): {sorted(unknown)}")
        key = sheet.get("key")
        if key is not None and (not isinstance(key, str) or not key.strip()):
            violations.append(f"{where}.key must be a non-blank string")
        record = sheet.get("record")
        if record is None:
            violations.append(f"{where}.record is required")
        else:
            violations.extend(record_violations(record, f"{where}.record"))
        relations = sheet.get("relations")
        if relations is None:
            continue
        if not isinstance(relations, list):
            violations.append(f"{where}.relations must be a list")
            continue
        for r_index, raw in enumerate(relations):
            rel_where = f"{where}.relations[{r_index}]"
            violations.extend(relation_violations(raw, rel_where, allow_target_name=False))
            if not isinstance(raw, dict):
                continue
            target_key = raw.get("target_key")
            if isinstance(target_key, str) and (target_key not in staged_keys or target_key == key):
                violations.append(
                    f"{rel_where}.target_key {target_key!r} must name another staged "
                    "sheet's key in this payload"
                )
    return violations


def normalize_entity_name(name: str) -> str:
    """The dedup identity key (owner rule 2026-09-12): casefolded, leading
    article stripped, whitespace collapsed, trailing punctuation dropped.
    EXACT-name matching on purpose — epithet variants ("Sim (The Drowned)")
    are not merged; the wave-2 prompt rule deters them and the DM prunes
    survivors. A fuzzy merge is the two-Jorahs hazard the ledger warned
    about and stays out of scope.

    Lives in the store (pure text, no pipeline imports) so the enqueue
    budget's mandate counting and the pipeline's merge share ONE
    normalizer; ``pipeline.build_in`` re-exports it.
    """
    normalized = " ".join(name.casefold().split()).strip(".!?,;:")
    for article in ("the ", "a ", "an "):
        if normalized.startswith(article):
            return normalized[len(article) :]
    return normalized
