"""Prototype: archetype-table + DPR-solver character builder (design demo).

Model writes WORDS and TAG CHOICES only — never a number.
Code writes every NUMBER: scores, HP, AC, saves, skills, DC, slots, dice.
Slots in the model's attack text (`{dice}`, `{to_hit}`, ...) are filled by code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zlib
from typing import Any

sys.path.insert(0, "/home/main/Projects/mythosCircle/backend")
from app.pipeline import knowledge as K  # noqa: E402
from app.pipeline.combat import (  # noqa: E402
    CR_DPR, CR_HP, _die_avg, audit_stat_block, parse_damage_expression)
from app.pipeline.knowledge import validate_stat_block  # noqa: E402

ENDPOINT = "http://127.0.0.1:8888/v1/chat/completions"
MODEL = "unsloth/gemma-4-26B-A4B-it-qat-GGUF"

# ---------------------------------------------------------------------------
# CODE TABLES (the artifact that needs authoring — everything else is arithmetic)
# ---------------------------------------------------------------------------

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
        "skill_list": ("Athletics", "Insight", "Intimidation", "Medicine", "Persuasion", "Religion"),
        "skill_picks": 2,
        # half-caster: the full SRD slot row by class level (1st..5th)
        "slots": {
            1: (), 2: (2,), 3: (3,), 4: (3,), 5: (4, 2), 6: (4, 2), 7: (4, 3),
            8: (4, 3), 9: (4, 3, 2), 10: (4, 3, 2), 11: (4, 3, 3), 12: (4, 3, 3),
            13: (4, 3, 3, 1), 14: (4, 3, 3, 1), 15: (4, 3, 3, 2), 16: (4, 3, 3, 2),
            17: (4, 3, 3, 3, 1), 18: (4, 3, 3, 3, 1), 19: (4, 3, 3, 3, 2),
            20: (4, 3, 3, 3, 2),
        },
        "extra_attacks": 2,
        "features": [
            (2, "Divine Smite"), (2, "Fighting Style"), (3, "Channel Divinity"),
            (5, "Extra Attack"), (6, "Aura of Protection"), (10, "Aura of Courage"),
            (11, "Improved Divine Smite"), (14, "Cleansing Touch"), (15, "Purity of Spirit"),
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


# ---------------------------------------------------------------------------
# CODE: tag vectors -> ranks -> scores
# ---------------------------------------------------------------------------

def tier_for(challenge: int) -> str:
    if challenge <= 4:
        return "1-4"
    if challenge <= 8:
        return "5-8"
    if challenge <= 14:
        return "9-14"
    return "15+"


def rank_abilities(tags: list[str], class_priority: dict[str, int] | None = None) -> tuple[list[str], dict[str, int]]:
    """Sum the class anchor and the tag weight vectors; rank best-first.

    Ties break on ABILITY order, so the result is deterministic. The class
    vector is what stops `divine_caster` from turning a paladin into a cleric:
    the class owns the primary stat, the tags only modulate around it.
    """
    totals = {a: 0 for a in ABILITY}
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


# ---------------------------------------------------------------------------
# CODE: the DPR solver (pick dice that hit the target, by arithmetic)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# MODEL CALL: words + tag choices only, never a number
# ---------------------------------------------------------------------------

def call_model(paladin_spells: list[str], skills: list[str]) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "race": {"type": "string"},
            "alignment": {"type": "string"},
            "tags": {"type": "array", "minItems": 2, "maxItems": 4,
                     "items": {"type": "string", "enum": sorted(ARCHETYPE_WEIGHTS)}},
            "appearance": {"type": "string"},
            "personality": {"type": "string"},
            "background": {"type": "string"},
            "goals": {"type": "string"},
            "secret": {"type": "string"},
            "rumor": {"type": "string"},
            "party_hook": {"type": "string"},
            "voice_style": {"type": "string"},
            "catchphrases": {"type": "string"},
            "reputation": {"type": "string"},
            "current_location": {"type": "string"},
            "reaction_matrix": {"type": "string"},
            "attacks": {
                "type": "array", "minItems": 2, "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "weapon": {"type": "string", "enum": sorted(WEAPONS)},
                        "weight": {"type": "string", "enum": ["light", "standard", "heavy"]},
                        "text": {"type": "string"},
                    },
                    "required": ["name", "weapon", "weight", "text"],
                    "additionalProperties": False,
                },
            },
            "skills": {"type": "array", "minItems": 2, "maxItems": 2,
                       "items": {"type": "string", "enum": skills}},
            "spells": {"type": "array", "minItems": 4, "maxItems": 8,
                       "items": {"type": "string", "enum": paladin_spells}},
        },
        "required": ["name", "race", "alignment", "tags", "appearance", "personality",
                     "background", "goals", "secret", "rumor", "party_hook", "voice_style",
                     "catchphrases", "reputation", "current_location", "reaction_matrix",
                     "attacks", "skills", "spells"],
        "additionalProperties": False,
    }
    prompt = "\n".join([
        "Design ONE level 17 paladin NPC — a sworn knight of a drowned god in a grimdark",
        "harbor-city campaign. Every field below is a non-blank string.",
        "",
        "YOU WRITE WORDS ONLY. NEVER A NUMBER.",
        "Every number inside an attack text is a PLACEHOLDER that other code fills in.",
        "Allowed placeholders, and nothing else: {to_hit} {dice} {average} {bonus}",
        "{damage_type} {dc} {save} {reach} {targets}.",
        "",
        f"TAGS (choose 2 to 4): {sorted(ARCHETYPE_WEIGHTS)}",
        f"WEAPONS (attack `weapon` must be one of): {sorted(WEAPONS)}",
        f"SKILLS (choose exactly 2): {skills}",
        f"SPELLS (choose 4 to 8, these names only): {paladin_spells}",
        "",
        "OUTPUT CONTRACT",
        "Respond with one JSON object, nothing else:",
        '{"name": "...", "race": "Human", "alignment": "LG",',
        '  "tags": ["holy_warrior"],',
        '  "appearance": "...", "personality": "...", "background": "...",',
        '  "goals": "...", "secret": "...", "rumor": "...", "party_hook": "...",',
        '  "voice_style": "...", "catchphrases": "...",',
        '  "reputation": "...", "current_location": "...", "reaction_matrix": "...",',
        '  "skills": ["Persuasion", "Insight"],',
        '  "spells": ["Bless", "Revivify"],',
        '  "attacks": [{"name": "Oathblade", "weapon": "greatsword",',
        '               "weight": "heavy",',
        '               "text": "Melee Weapon Attack: +{to_hit} to hit, reach 5 ft., one',
        '                        target. Hit: {average} ({dice} + {bonus}) {damage_type}',
        '                        damage."}]}',
        "",
        "The attacks array holds 2 or 3 entries, each `text` written in that canonical 5e",
        "form with the placeholders in place of every number. `weight` is light, standard,",
        "or heavy. The narrative fields are painter-grade prose, one or two sentences each;",
        "`secret` is a specific concealed fact, `rumor` a concrete in-world telling of it,",
        "`party_hook` a reason the party needs this knight.",
    ])
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 4000,
        # The model otherwise reasons indefinitely (26k chars, still no content):
        # llama.cpp/Unsloth honours the template kwarg.
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "npc", "strict": True, "schema": schema}},
    }
    raw = subprocess.run(
        ["curl", "-s", "-m", "600", ENDPOINT, "-H", "Content-Type: application/json",
         "-d", json.dumps(body)],
        capture_output=True, text=True, check=True,
    ).stdout
    payload = json.loads(raw)
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return json.loads(payload["choices"][0]["message"]["content"])


# ---------------------------------------------------------------------------
# CODE: assemble the sheet
# ---------------------------------------------------------------------------

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


def build(words: dict[str, Any], level: int = 17) -> dict[str, Any]:
    cls = "Paladin"
    table = CLASS_TABLE[cls]
    tier = tier_for(level)
    prof = 2 + (level - 1) // 4
    magic_bonus = MAGIC_BY_TIER[tier]

    ranked, totals = rank_abilities(words["tags"], table.get("priority"))
    # crc32, not hash(): str hashing is salted per process and must not leak
    # into the sheet (same input -> same scores, always).
    jitter_seed = zlib.crc32(words["name"].encode("utf-8"))
    scores = assign_scores(ranked, level, jitter_seed=jitter_seed)
    mods = {a: modifier(s) for a, s in scores.items()}

    # HP, AC and the round's damage all aim at the DMG monster row for this
    # challenge — the NPC is a threat, so the table sets the magnitude and the
    # archetype only sets the shape.
    dice_count, hp = solve_hit_dice(table["hit_die"], mods["con"], CR_HP[level])
    hit_dice = f"{dice_count}d{table['hit_die']} + {dice_count * mods['con']}"
    armor = AC_BY_TIER[tier]
    band_low, band_high = CR_DPR[level]
    target = (band_low + band_high) / 2

    save_prof = set(table["save_prof"])
    aura = mods["cha"]  # Aura of Protection: +CHA mod to every save
    saves = {a: mods[a] + (prof if a in save_prof else 0) + aura for a in ABILITY}

    skill_names = sorted(words["skills"])
    skills = [{"name": s, "bonus": prof + _skill_mod(s, mods)} for s in skill_names]
    passive_perception = 10 + mods["wis"] + (prof if "Perception" in skill_names else 0)

    slots_row = table["slots"][level]
    spell_dc = 8 + prof + mods["cha"]
    spell_attack = prof + mods["cha"]

    weapon_bonus = mods["str"] + magic_bonus
    attacks = []
    primary_avg = 0.0
    for index, entry in enumerate(words["attacks"]):
        weapon = WEAPONS[entry["weapon"]]
        count, sides = weapon["dice"]
        to_hit = mods["str"] + prof + magic_bonus
        dice = f"{count}d{sides}"
        average = round(count * _die_avg(sides) + weapon_bonus, 1)
        damage = [{"dice": dice, "count": count, "sides": sides,
                   "bonus": weapon_bonus, "average": average, "type": weapon["type"]}]
        # Only the strongest attack carries the budget; the rest are the
        # creature's alternative actions (the auditor reads exactly that way).
        if index == 0:
            swings, rider_count, rider_sides, rider_avg = solve_routine(
                average, target, (band_low, band_high), SWINGS_BY_TIER[tier])
            if rider_count:
                damage.append({"dice": f"{rider_count}d{rider_sides}", "count": rider_count,
                               "sides": rider_sides, "bonus": 0, "average": rider_avg,
                               "type": CLASS_RIDER[cls]})
            primary_avg = average + rider_avg
        rendered = _fill(entry["text"], {
            "to_hit": to_hit, "dice": dice, "average": _fmt(average),
            "bonus": weapon_bonus, "damage_type": weapon["type"],
            "save": "Constitution",
            "dc": spell_dc, "reach": 10 if weapon.get("reach") else 5, "targets": 1,
        })
        if index == 0 and rider_count:
            # The template closes its own sentence; the rider is a continuation
            # of the damage clause, not a new sentence ("... damage plus ...").
            rendered = rendered.rstrip()
            if rendered.endswith("."):
                rendered = rendered[:-1]
            rendered += (f" plus {_fmt(rider_avg)} ({rider_count}d{rider_sides}) "
                         f"{CLASS_RIDER[cls]} damage.")
        attacks.append({
            "name": entry["name"],
            "kind": "melee_weapon",
            "weapon": entry["weapon"],
            "weight": entry["weight"],
            "to_hit": to_hit,
            "reach_ft": 10 if weapon.get("reach") else 5,
            "targets": 1,
            "damage": damage,
            # `description` is the AR24 field name AND what the Forge exporter
            # already reads -> damage reaches the token with no exporter change.
            "description": rendered,
        })

    if swings > 1:
        routine = [{
            "name": "Multiattack",
            "description": (f"The knight makes {swings} attacks with the "
                            f"{attacks[0]['name']}."),
        }, *attacks]
    else:
        routine = list(attacks)
    base_dpr = swings * primary_avg
    smite_dpr = base_dpr + _die_avg(8) * 5
    nova_dpr = base_dpr + _die_avg(8) * 5 * swings

    # round-trip proof: the rendered text must re-parse to what code computed
    reparsed, sources = parse_damage_expression(attacks[0]["description"])
    assert abs(reparsed - primary_avg) < 1e-9, (reparsed, primary_avg)

    # the shipped auditor is the backstop: the generated block must pass it
    block = {
        "identity": {"role": "NPC", "level": level, "race": words["race"], "class": cls},
        "attributes": scores,
        "combat": {"ac": armor, "hp": hp},
        "skills": skills,
        "actions": routine,
        "traits": [],
        "spells": [],
    }
    violations = validate_stat_block(block)
    audit = audit_stat_block(block)

    return {
        "model_version": "prototype-2",
        "identity": {"name": words["name"], "role": "NPC", "race": words["race"],
                     "class": cls, "level": level, "alignment": words["alignment"],
                     "tags": words["tags"]},
        "narrative": {k: words[k] for k in ("appearance", "personality", "background", "goals",
                                            "secret", "rumor", "party_hook", "voice_style",
                                            "catchphrases")},
        "world_integration": {k: words[k] for k in ("reputation", "current_location",
                                                    "reaction_matrix")},
        "stats": {
            "proficiency_bonus": prof,
            "abilities": scores,
            "modifiers": mods,
            "saves": saves,
            "initiative": mods["dex"],
            "passive_perception": passive_perception,
            "combat": {"ac": armor, "hp": hp, "hit_dice": hit_dice},
            "spellcasting": {"dc": spell_dc, "attack_bonus": spell_attack,
                             "slots": list(slots_row)},
            "skills": skills,
        },
        "routine": routine,
        "attacks": attacks,
        "spells": sorted(words["spells"]),
        "features": [name for lvl, name in table["features"] if lvl <= level],
        "resources": {"lay_on_hands": 5 * level, "cleansing_touch": mods["cha"],
                      "channel_divinity": 2},
        "power": {"base_dpr": base_dpr, "smite_dpr": smite_dpr, "nova_dpr": nova_dpr,
                  "band": (band_low, band_high), "hp_band": CR_HP[level],
                  "audited_dpr": audit.dpr, "violations": violations,
                  "round_trip_sources": list(sources), "rank_totals": totals},
    }


def _skill_mod(skill: str, mods: dict[str, int]) -> int:
    return {
        "Athletics": mods["str"], "Insight": mods["wis"], "Intimidation": mods["cha"],
        "Medicine": mods["wis"], "Persuasion": mods["cha"], "Religion": mods["int"],
        "Perception": mods["wis"], "Survival": mods["wis"], "Acrobatics": mods["dex"],
        "Arcana": mods["int"], "Deception": mods["cha"], "History": mods["int"],
        "Investigation": mods["int"], "Nature": mods["int"], "Performance": mods["cha"],
        "Sleight of Hand": mods["dex"], "Stealth": mods["dex"],
        "Animal Handling": mods["wis"],
    }[skill]


def _fill(template: str, values: dict[str, Any]) -> str:
    for slot in SLOTS:
        template = template.replace("{" + slot + "}", str(values[slot]))
    if "{" in template or "}" in template:
        raise ValueError(f"template has unfilled slots: {template!r}")
    return template


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def render(sheet: dict[str, Any]) -> str:
    ident, stats, power = sheet["identity"], sheet["stats"], sheet["power"]
    mods, scores = stats["modifiers"], stats["abilities"]
    ab = lambda a: f"{a.upper()} {scores[a]} ({mods[a]:+d})"  # noqa: E731
    lines = [
        f"# {ident['name']} — {ident['race']} {ident['class']} {ident['level']}",
        f"*{ident['alignment']} · tags: {', '.join(ident['tags'])}*",
        "",
        f"**AC** {stats['combat']['ac']}  **HP** {stats['combat']['hp']} "
        f"({stats['combat']['hit_dice']})  **Prof** +{stats['proficiency_bonus']}  "
        f"**Init** {stats['initiative']:+d}  **Passive Perception** {stats['passive_perception']}",
        "",
        "**Abilities** " + " · ".join(ab(a) for a in ABILITY),
        "",
        "**Saves** " + " · ".join(f"{a.upper()} {stats['saves'][a]:+d}" for a in ABILITY)
        + "   *(Aura of Protection: +CHA to all)*",
        "",
        "**Skills** " + " · ".join(f"{s['name']} {s['bonus']:+d}" for s in stats["skills"]),
        "",
        f"**Spellcasting** DC {stats['spellcasting']['dc']} · attack "
        f"{stats['spellcasting']['attack_bonus']:+d} · slots "
        + ("/".join(str(n) for n in stats["spellcasting"]["slots"]) or "none")
        + (" (1st–5th)" if stats["spellcasting"]["slots"] else ""),
        "",
        "## Attacks",
    ]
    for attack in sheet["routine"]:
        lines.append(f"- **{attack['name']}** — {attack['description']}")
    lines += [
        "",
        f"**DPR** {power['audited_dpr']:.1f} vs band {power['band'][0]:.0f}-"
        f"{power['band'][1]:.0f} · +one 4th-level smite {power['smite_dpr']:.1f} · "
        f"+two {power['nova_dpr']:.1f} · **validator: "
        f"{'clean' if not power['violations'] else power['violations']}**",
        "",
        "## Spells",
        ", ".join(sheet["spells"]),
        "",
        "## Features",
        ", ".join(sheet["features"]),
        "",
        f"**Resources** Lay on Hands {sheet['resources']['lay_on_hands']} · Cleansing Touch "
        f"{sheet['resources']['cleansing_touch']}/rest · Channel Divinity "
        f"{sheet['resources']['channel_divinity']}/rest",
        "",
        "## Narrative",
    ]
    for key, value in sheet["narrative"].items():
        lines.append(f"- **{key.replace('_', ' ').capitalize()}** — {value}")
    lines.append("")
    lines.append("**World integration**")
    for key, value in sheet["world_integration"].items():
        lines.append(f"- {key.replace('_', ' ').capitalize()} — {value}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    paladin_spells = sorted(s for s, cs in K.SPELLS.items() if "Paladin" in cs)
    skills = list(CLASS_TABLE["Paladin"]["skill_list"])
    here = os.path.dirname(os.path.abspath(__file__))
    words_path = os.path.join(here, "words.json")
    if os.path.exists(words_path):
        words = json.load(open(words_path))
        print("[words: cached]", file=sys.stderr)
    else:
        words = call_model(paladin_spells, skills)
        json.dump(words, open(words_path, "w"), indent=1)
    sheet = build(words)

    with open(os.path.join(here, "paladin-l17.json"), "w") as handle:
        json.dump(sheet, handle, indent=2)
    with open(os.path.join(here, "paladin-l17.md"), "w") as handle:
        handle.write(render(sheet))

    power = sheet["power"]
    print(render(sheet))
    print("=" * 72)
    print(f"rank: class anchor + tags {words['tags']} -> " + " > ".join(
        sorted(ABILITY, key=lambda a: (-power['rank_totals'][a], ABILITY.index(a)))))
    print(f"round-trip: rendered Oathblade re-parses to {power['round_trip_sources']} "
          f"= {power['base_dpr'] / 3:.1f} per hit (code computed "
          f"{sheet['attacks'][0]['damage'][0]['average']} weapon + "
          f"{sheet['attacks'][0]['damage'][1]['average']} rider)")
    print(f"auditor (shipped combat.py): DPR {power['audited_dpr']:.1f} vs band "
          f"{power['band'][0]:.0f}-{power['band'][1]:.0f}; hp "
          f"{sheet['stats']['combat']['hp']} vs band {power['hp_band'][0]}-"
          f"{power['hp_band'][1]}")
    print(f"validate_stat_block -> {power['violations'] or 'CLEAN (0 violations)'}")
