"""AR25 KnowledgeProvider: local 5e reference data + stat-block validation.

Local reference tables (classes, races, spells, SRD 5.1 stat caps) queried
by the pipeline — spec-2.4. The SRD 5.1 dataset is static and small, so
the tables are typed in-process constants (frozen sets / an immutable
map), not DB tables: the single-SQLite constraint (AR11) stays, and
extending a table is adding an entry to a set — exactly the "or relevant
subset" latitude AR25 describes.

``validate_stat_block`` is the constraint enforcement: a pure function
returning stable violation strings (``[]`` = valid). It checks the
field-set shape (SRD 5.1), role-limited semantics (NPC/BBEG → level,
Monster → CR), SRD vocabularies, hard caps, and combat power (the
``combat`` audit: damage/round outside the DMG band for the declared
level/CR, or HP below half the band low). Derived-value arithmetic

``SPELLS`` maps spell name -> its full-list classes: the SRD 5.1 class
lists (audited against two independent SRD 5.1 mirrors, which agree
spell-for-spell) plus a small curated extension of well-known non-SRD
staples (Hex, the smites, the Hadar spells) carried with their published
class lists — deliberately conservative (no expanded-subclass material,
only high-confidence mappings); spell names outside the local reference
cannot be validated and are flagged, and the repair prompt tells the
model to use listed spells only.
"""

import re
from types import MappingProxyType
from typing import Any

from app.pipeline import combat
from app.store.candidates import ROLES as ROLES

#: Roles in the AR24 identity anchor; NPC/BBEG carry ``level``, Monster
#: carries ``cr`` (AD-18: level for NPCs, CR for monsters). The closed
#: set lives in ``store.candidates`` (the staged-record contract) and is
#: re-exported here so the reference data and the candidate shape stay
#: ONE vocabulary — never two literals to keep in sync.

#: NPC/BBEG level bound (SRD 5.1: 1-20).
LEVEL_MAX = 20

#: Monster CR bound (SRD 5.1: challenge 0-30) plus the fractional tiers.
CR_MAX = 30
CR_FRACTIONS: frozenset[str] = frozenset({"1/8", "1/4", "1/2"})

#: The six ability scores, in canonical order (SRD 5.1 field set).
ABILITY_SCORES: tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha")

#: SRD 5.1 hard caps on ability scores.
ABILITY_MIN = 1
ABILITY_MAX = 30

#: SRD 5.1 base classes (the 12 PHB classes; Artificer is not SRD).
CLASSES: frozenset[str] = frozenset(
    {
        "Barbarian",
        "Bard",
        "Cleric",
        "Druid",
        "Fighter",
        "Monk",
        "Paladin",
        "Ranger",
        "Rogue",
        "Sorcerer",
        "Warlock",
        "Wizard",
    }
)

#: SRD 5.1 player races (the 9 PHB races).
RACES: frozenset[str] = frozenset(
    {"Dragonborn", "Dwarf", "Elf", "Gnome", "Half-Elf", "Half-Orc", "Halfling", "Human", "Tiefling"}
)

#: SRD 5.1 alignments (9-axis + unaligned for monsters).
ALIGNMENTS: frozenset[str] = frozenset(
    {"LG", "NG", "CG", "LN", "N", "CN", "LE", "NE", "CE", "unaligned"}
)

#: The 18 SRD 5.1 skills.
SKILLS: frozenset[str] = frozenset(
    {
        "Acrobatics",
        "Animal Handling",
        "Arcana",
        "Athletics",
        "Deception",
        "History",
        "Insight",
        "Intimidation",
        "Investigation",
        "Medicine",
        "Nature",
        "Perception",
        "Performance",
        "Persuasion",
        "Religion",
        "Sleight of Hand",
        "Stealth",
        "Survival",
    }
)

#: Spell name -> classes that have it on their class spell list (SRD 5.1
# lists plus the curated non-SRD staples noted in the module docstring).
#: The role-limited rule (AR25: "role=Wizard limits spells to the wizard
#: list or relevant subset") checks membership here. Immutable by contract
#: — the map feeds prompts, so a mutable dict would let one module change
#: the prompt contract for everyone (same reasoning as EDGE_COUNTER_SEMANTICS).
_SPELLS = {
    # Cantrips
    "Acid Splash": {"Sorcerer", "Wizard"},
    "Chill Touch": {"Sorcerer", "Warlock", "Wizard"},
    "Dancing Lights": {"Bard", "Sorcerer", "Wizard"},
    "Eldritch Blast": {"Warlock"},
    "Fire Bolt": {"Sorcerer", "Wizard"},
    "Friends": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Guidance": {"Cleric", "Druid"},
    "Light": {"Bard", "Cleric", "Sorcerer", "Wizard"},
    "Mage Hand": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Mending": {"Bard", "Cleric", "Druid", "Sorcerer", "Wizard"},
    "Message": {"Bard", "Sorcerer", "Wizard"},
    "Minor Illusion": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Poison Spray": {"Druid", "Sorcerer", "Warlock", "Wizard"},
    "Prestidigitation": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Produce Flame": {"Druid"},
    "Ray of Frost": {"Sorcerer", "Wizard"},
    "Resistance": {"Cleric", "Druid"},
    "Sacred Flame": {"Cleric"},
    "Shillelagh": {"Druid"},
    "Spare the Dying": {"Cleric"},
    "Thaumaturgy": {"Cleric"},
    "True Strike": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Vicious Mockery": {"Bard"},
    # 1st level
    "Animal Friendship": {"Bard", "Druid", "Ranger"},
    "Armor of Agathys": {"Warlock"},
    "Arms of Hadar": {"Warlock"},
    "Bane": {"Bard", "Cleric"},
    "Bless": {"Cleric", "Paladin"},
    "Burning Hands": {"Sorcerer", "Wizard"},
    "Charm Person": {"Bard", "Druid", "Sorcerer", "Warlock", "Wizard"},
    "Color Spray": {"Sorcerer", "Wizard"},
    "Command": {"Cleric", "Paladin"},
    "Compelled Duel": {"Paladin"},
    "Comprehend Languages": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Cure Wounds": {"Bard", "Cleric", "Druid", "Paladin", "Ranger"},
    "Detect Evil and Good": {"Cleric", "Paladin"},
    "Detect Magic": {"Bard", "Cleric", "Druid", "Paladin", "Ranger", "Sorcerer", "Wizard"},
    "Disguise Self": {"Bard", "Sorcerer", "Wizard"},
    "Divine Favor": {"Paladin"},
    "Ensnaring Strike": {"Ranger"},
    "Entangle": {"Druid"},
    "Expeditious Retreat": {"Sorcerer", "Warlock", "Wizard"},
    "Faerie Fire": {"Druid"},
    "False Life": {"Sorcerer", "Wizard"},
    "Feather Fall": {"Bard", "Sorcerer", "Wizard"},
    "Fog Cloud": {"Druid", "Ranger", "Sorcerer", "Wizard"},
    "Goodberry": {"Druid", "Ranger"},
    "Guiding Bolt": {"Cleric"},
    "Hail of Thorns": {"Ranger"},
    "Healing Word": {"Bard", "Cleric", "Druid"},
    "Hellish Rebuke": {"Warlock"},
    "Heroism": {"Bard", "Paladin"},
    "Hex": {"Warlock"},
    "Hunter's Mark": {"Ranger"},
    "Identify": {"Bard", "Wizard"},
    "Inflict Wounds": {"Cleric"},
    "Jump": {"Druid", "Ranger", "Sorcerer", "Wizard"},
    "Longstrider": {"Bard", "Druid", "Ranger", "Wizard"},
    "Mage Armor": {"Sorcerer", "Wizard"},
    "Magic Missile": {"Sorcerer", "Wizard"},
    "Protection from Evil and Good": {"Cleric", "Paladin", "Warlock", "Wizard"},
    "Sanctuary": {"Cleric"},
    "Searing Smite": {"Paladin"},
    "Shield": {"Sorcerer", "Wizard"},
    "Shield of Faith": {"Cleric", "Paladin"},
    "Sleep": {"Bard", "Sorcerer", "Wizard"},
    "Speak with Animals": {"Bard", "Druid", "Ranger"},
    "Thunderous Smite": {"Paladin"},
    "Thunderwave": {"Bard", "Druid", "Sorcerer", "Wizard"},
    "Unseen Servant": {"Bard", "Warlock", "Wizard"},
    "Witch Bolt": {"Sorcerer", "Warlock", "Wizard"},
    "Wrathful Smite": {"Paladin"},
    "Zone of Truth": {"Bard", "Cleric", "Paladin"},
    # 2nd level
    "Aid": {"Cleric", "Paladin"},
    "Alter Self": {"Sorcerer", "Wizard"},
    "Animal Messenger": {"Bard", "Druid", "Ranger"},
    "Arcane Lock": {"Wizard"},
    "Blindness/Deafness": {"Bard", "Cleric", "Sorcerer", "Wizard"},
    "Blur": {"Sorcerer", "Wizard"},
    "Branding Smite": {"Paladin"},
    "Cloud of Daggers": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Continual Flame": {"Cleric", "Wizard"},
    "Crown of Madness": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Darkness": {"Sorcerer", "Warlock", "Wizard"},
    "Darkvision": {"Druid", "Ranger", "Sorcerer", "Wizard"},
    "Enhance Ability": {"Bard", "Cleric", "Druid", "Sorcerer"},
    "Enthrall": {"Bard", "Warlock"},
    "Find Steed": {"Paladin"},
    "Flame Blade": {"Druid"},
    "Flaming Sphere": {"Druid", "Wizard"},
    "Gust of Wind": {"Druid", "Sorcerer", "Wizard"},
    "Heat Metal": {"Bard", "Druid"},
    "Hold Person": {"Bard", "Cleric", "Druid", "Sorcerer", "Warlock", "Wizard"},
    "Invisibility": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Knock": {"Bard", "Sorcerer", "Wizard"},
    "Lesser Restoration": {"Bard", "Cleric", "Druid", "Paladin", "Ranger"},
    "Levitate": {"Sorcerer", "Wizard"},
    "Magic Weapon": {"Paladin", "Wizard"},
    "Mirror Image": {"Sorcerer", "Warlock", "Wizard"},
    "Misty Step": {"Sorcerer", "Warlock", "Wizard"},
    "Moonbeam": {"Druid"},
    "Pass without Trace": {"Druid", "Ranger"},
    "Prayer of Healing": {"Cleric"},
    "Protection from Poison": {"Cleric", "Druid", "Paladin", "Ranger"},
    "Ray of Enfeeblement": {"Warlock", "Wizard"},
    "Scorching Ray": {"Sorcerer", "Wizard"},
    "See Invisibility": {"Bard", "Sorcerer", "Wizard"},
    "Shatter": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Silence": {"Bard", "Cleric", "Ranger"},
    "Spider Climb": {"Sorcerer", "Warlock", "Wizard"},
    "Spiritual Weapon": {"Cleric"},
    "Suggestion": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Warding Bond": {"Cleric"},
    "Web": {"Sorcerer", "Wizard"},
    # 3rd level
    "Animate Dead": {"Cleric", "Wizard"},
    "Beacon of Hope": {"Cleric"},
    "Bestow Curse": {"Bard", "Cleric", "Wizard"},
    "Blink": {"Sorcerer", "Wizard"},
    "Call Lightning": {"Druid"},
    "Clairvoyance": {"Bard", "Cleric", "Sorcerer", "Wizard"},
    "Counterspell": {"Sorcerer", "Warlock", "Wizard"},
    "Create Food and Water": {"Cleric", "Druid", "Paladin"},
    "Daylight": {"Cleric", "Druid", "Paladin", "Ranger", "Sorcerer"},
    "Dispel Magic": {"Bard", "Cleric", "Druid", "Paladin", "Sorcerer", "Warlock", "Wizard"},
    "Fear": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Fireball": {"Sorcerer", "Wizard"},
    "Fly": {"Sorcerer", "Warlock", "Wizard"},
    "Gaseous Form": {"Sorcerer", "Warlock", "Wizard"},
    "Glyph of Warding": {"Bard", "Cleric", "Wizard"},
    "Haste": {"Sorcerer", "Wizard"},
    "Hunger of Hadar": {"Warlock"},
    "Hypnotic Pattern": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Lightning Bolt": {"Sorcerer", "Wizard"},
    "Magic Circle": {"Cleric", "Paladin", "Warlock", "Wizard"},
    "Major Image": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Mass Healing Word": {"Cleric"},
    "Protection from Energy": {"Cleric", "Druid", "Ranger", "Sorcerer", "Wizard"},
    "Revivify": {"Cleric", "Paladin"},
    "Sleet Storm": {"Druid", "Sorcerer", "Wizard"},
    "Slow": {"Sorcerer", "Wizard"},
    "Speak with Dead": {"Bard", "Cleric"},
    "Spirit Guardians": {"Cleric"},
    "Stinking Cloud": {"Bard", "Sorcerer", "Wizard"},
    "Tongues": {"Bard", "Cleric", "Sorcerer", "Warlock", "Wizard"},
    "Vampiric Touch": {"Warlock", "Wizard"},
    "Water Breathing": {"Druid", "Ranger", "Sorcerer", "Wizard"},
    "Wind Wall": {"Druid", "Ranger"},
    # 4th level
    "Banishment": {"Cleric", "Paladin", "Sorcerer", "Warlock", "Wizard"},
    "Blight": {"Druid", "Sorcerer", "Warlock", "Wizard"},
    "Confusion": {"Bard", "Druid", "Sorcerer", "Wizard"},
    "Conjure Minor Elementals": {"Druid", "Wizard"},
    "Dimension Door": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Divination": {"Druid"},
    "Dominate Beast": {"Druid", "Sorcerer"},
    "Evard's Black Tentacles": {"Wizard"},
    "Fire Shield": {"Wizard"},
    "Greater Invisibility": {"Bard", "Sorcerer", "Wizard"},
    "Hallucinatory Terrain": {"Bard", "Druid", "Warlock", "Wizard"},
    "Ice Storm": {"Druid", "Sorcerer", "Wizard"},
    "Phantasmal Killer": {"Wizard"},
    "Polymorph": {"Bard", "Druid", "Sorcerer", "Wizard"},
    "Stone Shape": {"Cleric", "Druid", "Wizard"},
    "Stoneskin": {"Druid", "Ranger", "Sorcerer", "Wizard"},
    "Wall of Fire": {"Druid", "Sorcerer", "Wizard"},
    # 5th level
    "Cloudkill": {"Sorcerer", "Wizard"},
    "Cone of Cold": {"Sorcerer", "Wizard"},
    "Conjure Elemental": {"Druid", "Wizard"},
    "Contact Other Plane": {"Warlock", "Wizard"},
    "Contagion": {"Cleric", "Druid"},
    "Death Ward": {"Cleric", "Paladin"},
    "Dominate Person": {"Bard", "Sorcerer", "Wizard"},
    "Dream": {"Bard", "Warlock", "Wizard"},
    "Geas": {"Bard", "Cleric", "Druid", "Paladin", "Wizard"},
    "Greater Restoration": {"Bard", "Cleric", "Druid"},
    "Hold Monster": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Insect Plague": {"Cleric", "Druid", "Sorcerer"},
    "Legend Lore": {"Bard", "Cleric", "Wizard"},
    "Mass Cure Wounds": {"Bard", "Cleric", "Druid"},
    "Planar Binding": {"Bard", "Cleric", "Druid", "Wizard"},
    "Raise Dead": {"Bard", "Cleric", "Paladin"},
    "Scrying": {"Bard", "Cleric", "Druid", "Warlock", "Wizard"},
    "Teleportation Circle": {"Bard", "Sorcerer", "Wizard"},
    "Wall of Force": {"Wizard"},
    "Wall of Stone": {"Druid", "Sorcerer", "Wizard"},
    # 6th level
    "Chain Lightning": {"Sorcerer", "Wizard"},
    "Circle of Death": {"Sorcerer", "Warlock", "Wizard"},
    "Disintegrate": {"Sorcerer", "Wizard"},
    "Eyebite": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Flesh to Stone": {"Warlock", "Wizard"},
    "Globe of Invulnerability": {"Sorcerer", "Wizard"},
    "Harm": {"Cleric"},
    "Heal": {"Cleric", "Druid"},
    "Heroes' Feast": {"Cleric", "Druid"},
    "Mass Suggestion": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Otiluke's Freezing Sphere": {"Sorcerer", "Wizard"},
    "Sunbeam": {"Druid", "Sorcerer", "Wizard"},
    "True Seeing": {"Bard", "Cleric", "Sorcerer", "Warlock", "Wizard"},
    "Wind Walk": {"Druid"},
    # 7th level
    "Delayed Blast Fireball": {"Sorcerer", "Wizard"},
    "Divine Word": {"Cleric"},
    "Etherealness": {"Bard", "Cleric", "Sorcerer", "Warlock", "Wizard"},
    "Finger of Death": {"Sorcerer", "Warlock", "Wizard"},
    "Fire Storm": {"Cleric", "Druid", "Sorcerer"},
    "Mordenkainen's Sword": {"Bard", "Wizard"},
    "Plane Shift": {"Cleric", "Druid", "Sorcerer", "Warlock", "Wizard"},
    "Prismatic Spray": {"Sorcerer", "Wizard"},
    "Regenerate": {"Bard", "Cleric", "Druid"},
    "Resurrection": {"Bard", "Cleric"},
    "Symbol": {"Bard", "Cleric", "Wizard"},
    "Teleport": {"Bard", "Sorcerer", "Wizard"},
    # 8th level
    "Antimagic Field": {"Cleric", "Wizard"},
    "Antipathy/Sympathy": {"Druid", "Wizard"},
    "Clone": {"Wizard"},
    "Control Weather": {"Cleric", "Druid", "Wizard"},
    "Earthquake": {"Cleric", "Druid", "Sorcerer"},
    "Feeblemind": {"Bard", "Druid", "Warlock", "Wizard"},
    "Incendiary Cloud": {"Sorcerer", "Wizard"},
    "Power Word Stun": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Sunburst": {"Druid", "Sorcerer", "Wizard"},
    "Telepathy": {"Wizard"},
    "Word of Recall": {"Cleric"},
    # 9th level
    "Astral Projection": {"Cleric", "Warlock", "Wizard"},
    "Foresight": {"Bard", "Druid", "Warlock", "Wizard"},
    "Gate": {"Cleric", "Sorcerer", "Wizard"},
    "Imprisonment": {"Warlock", "Wizard"},
    "Mass Heal": {"Cleric"},
    "Meteor Swarm": {"Sorcerer", "Wizard"},
    "Power Word Kill": {"Bard", "Sorcerer", "Warlock", "Wizard"},
    "Prismatic Wall": {"Wizard"},
    "Shapechange": {"Druid", "Wizard"},
    "Time Stop": {"Sorcerer", "Wizard"},
    "True Resurrection": {"Cleric", "Druid"},
    "Weird": {"Wizard"},
    "Wish": {"Sorcerer", "Wizard"},
}
SPELLS: MappingProxyType[str, frozenset[str]] = MappingProxyType(
    {name: frozenset(classes) for name, classes in _SPELLS.items()}
)

#: Case-insensitive canonicalization indexes. LLM output varies in case
#: ("fireball" vs "Fireball", "human" vs "Human"); casing is presentation,
#: not game validity, so vocabulary checks match on the lowercased form
#: and bind back to the canonical SRD name.
_ROLE_INDEX: MappingProxyType[str, str] = MappingProxyType({role.lower(): role for role in ROLES})
_CLASS_INDEX: MappingProxyType[str, str] = MappingProxyType(
    {klass.lower(): klass for klass in CLASSES}
)
_RACE_INDEX: MappingProxyType[str, str] = MappingProxyType({race.lower(): race for race in RACES})
_ALIGNMENT_INDEX: MappingProxyType[str, str] = MappingProxyType(
    {alignment.lower(): alignment for alignment in ALIGNMENTS}
)
_SKILL_INDEX: MappingProxyType[str, str] = MappingProxyType(
    {skill.lower(): skill for skill in SKILLS}
)
_SPELL_INDEX: MappingProxyType[str, str] = MappingProxyType({name.lower(): name for name in SPELLS})


def _valid_cr(value: Any) -> bool:
    """A Monster challenge rating: an integer in [0, 30] or a fractional tier."""
    if type(value) is int:
        return 0 <= value <= CR_MAX
    return value in CR_FRACTIONS


def _check_named_list(
    value: Any, section: str, vocab: frozenset[str] | None, errors: list[str]
) -> None:
    """Validate an optional named-entry list (skills / actions / traits).

    Skills carry a numeric ``bonus``; actions and traits carry a string
    ``description``. Duplicate names are rejected; names checked against
    ``vocab`` when given (skills). Unknown extra keys inside entries are
    tolerated (AR24 forward compatibility).
    """
    if value is None:
        return
    if not isinstance(value, list):
        errors.append(f"{section} must be a list")
        return
    vocab_folded = {entry.lower() for entry in vocab} if vocab is not None else None
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            errors.append(f"{section} entries must be objects with a 'name'")
            continue
        name = entry.get("name")
        if vocab_folded is not None:
            if not isinstance(name, str) or name.strip().lower() not in vocab_folded:
                errors.append(f"{section} name {name!r} not in the SRD list")
        elif not isinstance(name, str) or not name.strip():
            errors.append(f"{section} entries must have a non-blank string 'name'")
        if isinstance(name, str):
            folded = name.strip().lower()
            if folded in seen:
                errors.append(f"{section} name {name!r} duplicated")
            seen.add(folded)
        if section == "skills":
            bonus = entry.get("bonus")
            if type(bonus) is not int:
                errors.append(f"skills bonus for {name!r} must be an integer")
        else:
            description = entry.get("description")
            if not isinstance(description, str):
                errors.append(f"{section} entries must have a string 'description'")


def resolve_class(value: Any) -> str | None:
    """Fold a raw ``identity.class`` to its canonical SRD class name, or
    ``None`` when it is absent or outside the vocabulary — case- and
    whitespace-insensitive, like every other vocabulary check."""
    if isinstance(value, str):
        return _CLASS_INDEX.get(value.strip().lower())
    return None


#: Optional mechanics fields (spec: structured attack damage and the missing
#: stat aspects, 2026-09-11). Absence is always legal — a block written
#: before these existed validates exactly as it did — but a PRESENT field
#: must be well-formed, or the consumer (auditor, sheet, export) silently
#: reads the wrong number. Ranges are deliberately generous: only a value
#: that can name nothing legal is a violation.
_SAVE_RANGE = (-10, 40)
_INITIATIVE_RANGE = (-10, 30)
_PASSIVE_RANGE = (1, 40)
_PROFICIENCY_RANGE = (1, 12)
_SPELL_CAST_RANGE = (-5, 40)
_SLOT_MAX = 20
_HIT_DICE_RE = re.compile(r"\d+\s*[dD]\s*\d+")


def _in_range(value: Any, bounds: tuple[int, int]) -> bool:
    return type(value) is int and bounds[0] <= value <= bounds[1]


def _check_hit_dice(value: Any, errors: list[str]) -> None:
    """``combat.hit_dice`` (optional): a real dice expression like
    ``"24d10 + 192"`` — the string the sheet prints, never parsed for
    mechanics (the auditor reads hp)."""
    if value is None:
        return
    if not isinstance(value, str) or not _HIT_DICE_RE.search(value):
        errors.append(
            'combat.hit_dice must be a string holding a dice expression like "18d10 + 90"'
        )


def _check_damage_parts(entry: Any, position: int, errors: list[str]) -> None:
    """One action's structured ``damage`` list (optional).

    A part must be usable by the auditor: either a ``dice`` expression or
    an explicit ``count``/``sides`` pair. ``average``/``bonus``/``type``
    are checked when present and never required — the auditor derives what
    it needs and falls back to the description when a part is unusable.
    """
    if entry is None:
        return
    where = f"actions[{position}].damage"
    if not isinstance(entry, list) or not entry:
        errors.append(f"{where} must be a non-empty list of damage parts")
        return
    for index, part in enumerate(entry):
        if not isinstance(part, dict):
            errors.append(f"{where}[{index}] must be an object")
            continue
        dice = part.get("dice")
        count, sides = part.get("count"), part.get("sides")
        has_dice = isinstance(dice, str) and _HIT_DICE_RE.search(dice)
        has_pair = _in_range(count, (1, 100)) and _in_range(sides, (2, 100))
        if not has_dice and not has_pair:
            errors.append(
                f'{where}[{index}] needs a dice expression ("2d6") or count/sides integers'
            )
        if dice is not None and not has_dice:
            errors.append(f'{where}[{index}].dice must be a dice expression like "2d6"')
        bonus = part.get("bonus")
        if bonus is not None and type(bonus) is not int:
            errors.append(f"{where}[{index}].bonus must be an integer")
        average = part.get("average")
        if average is not None and (
            isinstance(average, bool) or not isinstance(average, (int, float)) or average < 0
        ):
            errors.append(f"{where}[{index}].average must be a non-negative number")
        kind = part.get("type")
        if kind is not None and (not isinstance(kind, str) or not kind.strip()):
            errors.append(f"{where}[{index}].type must be a non-blank damage type")


def _check_optional_mechanics(block: Any, errors: list[str]) -> None:
    """The optional mechanics aspects (spec 2026-09-11): saves, initiative,
    passive perception, proficiency bonus, spellcasting, resources, and the
    structured damage on each action. Every one of them is optional; a
    present one must be well-formed."""
    saves = block.get("saves")
    if saves is not None:
        if not isinstance(saves, dict):
            errors.append("saves must be an object keyed by ability score")
        else:
            for ability, value in saves.items():
                if ability not in ABILITY_SCORES:
                    errors.append(f"saves key {ability!r} must be one of {list(ABILITY_SCORES)}")
                elif not _in_range(value, _SAVE_RANGE):
                    low, high = _SAVE_RANGE
                    errors.append(f"saves.{ability} must be an integer in [{low}, {high}]")

    for field, bounds in (
        ("initiative", _INITIATIVE_RANGE),
        ("passive_perception", _PASSIVE_RANGE),
        ("proficiency_bonus", _PROFICIENCY_RANGE),
    ):
        value = block.get(field)
        if value is not None and not _in_range(value, bounds):
            errors.append(f"{field} must be an integer in [{bounds[0]}, {bounds[1]}]")

    spellcasting = block.get("spellcasting")
    if spellcasting is not None:
        if not isinstance(spellcasting, dict):
            errors.append("spellcasting must be an object")
        else:
            for field in ("dc", "attack_bonus"):
                value = spellcasting.get(field)
                if value is not None and not _in_range(value, _SPELL_CAST_RANGE):
                    errors.append(
                        f"spellcasting.{field} must be an integer in "
                        f"[{_SPELL_CAST_RANGE[0]}, {_SPELL_CAST_RANGE[1]}]"
                    )
            slots = spellcasting.get("slots")
            if slots is not None and (
                not isinstance(slots, list)
                or not all(_in_range(slot, (0, _SLOT_MAX)) for slot in slots)
            ):
                errors.append(f"spellcasting.slots must be a list of integers in [0, {_SLOT_MAX}]")

    resources = block.get("resources")
    if resources is not None:
        if not isinstance(resources, dict):
            errors.append("resources must be an object of name -> integer")
        else:
            for name, value in resources.items():
                if type(value) is not int or value < 0:
                    errors.append(f"resources.{name} must be a non-negative integer")

    features = block.get("features")
    if features is not None and (
        not isinstance(features, list)
        or not all(isinstance(entry, str) and entry.strip() for entry in features)
    ):
        # prototype-2's ``features`` is a list of NAMES ("Divine Smite"),
        # not the AR25 traits shape ({name, description}) — folding one into
        # the other would invent descriptions, so it is its own field.
        errors.append("features must be a list of non-blank feature names")

    actions = block.get("actions")
    if isinstance(actions, list):
        for position, action in enumerate(actions):
            if isinstance(action, dict):
                _check_damage_parts(action.get("damage"), position, errors)


def _check_spells(value: Any, klass: str | None, role: str | None, errors: list[str]) -> None:
    """Role-limited spell check (AR25, spec-2.4): a Monster never carries
    spells (its magic is actions/traits); otherwise spells require a
    class, no name may repeat (parity with skills/actions/traits), every
    name must be in the local SRD reference, and the class must be on
    that spell's list."""
    if value is None:
        return
    if not isinstance(value, list):
        errors.append("spells must be a list of spell names")
        return
    if not value:
        return
    if role == "Monster":
        errors.append(
            "spells are not allowed for role Monster (express magic as actions or traits)"
        )
        return
    if klass is None:
        errors.append("spells require identity.class (a class from the SRD class list)")
        return
    seen: set[str] = set()
    for spell in value:
        if not isinstance(spell, str) or not spell.strip():
            errors.append("spell names must be non-blank strings")
            continue
        folded = spell.strip().lower()
        if folded in seen:
            errors.append(f"spell {spell!r} duplicated")
        seen.add(folded)
        canonical = _SPELL_INDEX.get(folded)
        if canonical is None:
            errors.append(f"spell {spell!r} is not in the local SRD reference")
            continue
        if klass not in SPELLS[canonical]:
            errors.append(f"spell {spell!r} is not on the {klass} spell list")


def _check_power(block: Any, canonical_role: str | None, errors: list[str]) -> None:
    """Combat-power enforcement (wires the ``combat`` audit into the verdict).

    Pure and deterministic: violation strings carry the estimated DPR
    against the band numbers, so the repair pass can address them. A
    missing band (a level/CR outside the reference — the shape checks
    already flag those) abstains silently, as does a block with zero
    parseable damage anywhere (non-combatants are exempt). Level N keys
    the CR N band. HP fails low-only (below half the band low is frail);
    HP at or above the band never fails.
    """
    if not isinstance(block, dict) or canonical_role is None:
        return
    identity = block.get("identity")
    if not isinstance(identity, dict):
        return
    key = identity.get("cr") if canonical_role == "Monster" else identity.get("level")
    if isinstance(key, bool) or not isinstance(key, (int, str)):
        return
    dpr_band = combat.CR_DPR.get(key)
    if dpr_band is None:
        return
    audit = combat.audit_stat_block({**block, "identity": {**identity, "role": canonical_role}})
    if any(action.nominal_avg != 0.0 for action in audit.actions):
        low, high = dpr_band
        if audit.verdict == combat.VERDICT_UNDER:
            errors.append(
                f"under-powered for {audit.challenge}: estimated DPR "
                f"{audit.dpr:.1f} vs {low:.0f}-{high:.0f} expected"
            )
        elif audit.verdict == combat.VERDICT_OVER:
            errors.append(
                f"over-powered for {audit.challenge}: estimated DPR "
                f"{audit.dpr:.1f} vs {low:.0f}-{high:.0f} expected"
            )
        hp_band = combat.CR_HP.get(key)
        combat_section = block.get("combat")
        hp = combat_section.get("hp") if isinstance(combat_section, dict) else None
        if hp_band is not None and type(hp) is int and combat.is_hp_frail(hp, hp_band):
            low, high = hp_band
            errors.append(
                f"combat.hp {hp} is frail for {audit.challenge} (expected HP {low}-{high})"
            )
    elif any(
        combat.is_attack_shaped(action.get("name"), action.get("description"))
        for action in (block.get("actions") or [])
        if isinstance(action, dict)
    ):
        # The exemption above exists for TRUE non-combatants (a scholar with
        # no attacks at all). A creature that swings — a Multiattack, a
        # "Melee Weapon Attack: … to hit" — but states no dice is not
        # exempt, it is unreadable: measured 2026-09-11, a level-17 paladin
        # shipped with "makes one melee attack… deals massive radiant
        # damage", the audit read ZERO damage, and the block passed whole.
        errors.append(
            f"no readable damage for {audit.challenge}: the actions read as "
            "attacks but state no dice — every attack must give its damage as "
            "N (XdY + Z) TYPE (a true non-combatant writes no attack at all)"
        )


def validate_stat_block(block: Any) -> list[str]:
    """AR25 constraint checks for a minimal stat block; ``[]`` = valid.

    Pure and stable: violation strings are deterministic so the repair
    pass (and tests) can address them. Checks the field set (SRD 5.1),
    role-limited semantics, vocabularies, and hard caps; unknown keys
    inside known sections are tolerated (AR24 forward compatibility).
    """
    errors: list[str] = []
    if not isinstance(block, dict):
        return ["stat_block must be an object"]

    identity = block.get("identity")
    canonical_class: str | None = None
    canonical_role: str | None = None
    if not isinstance(identity, dict):
        errors.append("identity section missing or not an object")
    else:
        role = identity.get("role")
        if isinstance(role, str):
            canonical_role = _ROLE_INDEX.get(role.strip().lower())
        if canonical_role is None:
            errors.append(f"identity.role must be one of {sorted(ROLES)}")
        else:
            level, cr = identity.get("level"), identity.get("cr")
            if canonical_role == "Monster":
                if level is not None:
                    errors.append("identity.level is not allowed for role Monster (use cr)")
                if not _valid_cr(cr):
                    errors.append(
                        f"identity.cr must be an integer in [0, {CR_MAX}] or one of "
                        f"{sorted(CR_FRACTIONS)}"
                    )
            else:
                if cr is not None:
                    errors.append("identity.cr is not allowed for role NPC/BBEG (use level)")
                if type(level) is not int or not 1 <= level <= LEVEL_MAX:
                    errors.append(f"identity.level must be an integer in [1, {LEVEL_MAX}]")
        race = identity.get("race")
        if not isinstance(race, str) or not race.strip():
            errors.append("identity.race must be a non-blank string")
        elif canonical_role != "Monster" and _RACE_INDEX.get(race.strip().lower()) is None:
            errors.append(f"identity.race must be one of the SRD races: {sorted(RACES)}")
        klass = identity.get("class")
        if klass is not None:
            canonical_class = resolve_class(klass)
            if canonical_class is None:
                errors.append(f"identity.class must be one of {sorted(CLASSES)}")
        alignment = identity.get("alignment")
        if alignment is not None and (
            not isinstance(alignment, str)
            or _ALIGNMENT_INDEX.get(alignment.strip().lower()) is None
        ):
            errors.append(f"identity.alignment must be one of {sorted(ALIGNMENTS)}")

    attributes = block.get("attributes")
    if not isinstance(attributes, dict):
        errors.append("attributes section missing or not an object")
    else:
        for score in ABILITY_SCORES:
            value = attributes.get(score)
            if type(value) is not int or not ABILITY_MIN <= value <= ABILITY_MAX:
                errors.append(
                    f"attributes.{score} must be an integer in [{ABILITY_MIN}, {ABILITY_MAX}]"
                )

    combat = block.get("combat")
    if not isinstance(combat, dict):
        errors.append("combat section missing or not an object")
    else:
        ac = combat.get("ac")
        if type(ac) is not int or ac < 1:
            errors.append("combat.ac must be a positive integer")
        hp = combat.get("hp")
        if type(hp) is not int or hp < 1:
            errors.append("combat.hp must be a positive integer")
        _check_hit_dice(combat.get("hit_dice"), errors)

    _check_optional_mechanics(block, errors)
    _check_named_list(block.get("skills"), "skills", SKILLS, errors)
    _check_named_list(block.get("actions"), "actions", None, errors)
    _check_named_list(block.get("traits"), "traits", None, errors)
    _check_spells(
        block.get("spells"),
        canonical_class,
        canonical_role,
        errors,
    )
    _check_power(block, canonical_role, errors)
    return errors
