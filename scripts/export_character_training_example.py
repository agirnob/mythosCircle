"""Export a verified Forge training example without contacting an LLM.

Run from the repository root with:
    backend/.venv/bin/python scripts/export_character_training_example.py

The handcrafted responses are replayed through the real queue worker and
provider using an HTTP mock. All database writes go through the store into
a temporary database. The application's database and keys are never read.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.config import THEME_DEFAULT_SEEDS
from app.core.settings import LLMSettings
from app.pipeline.build_in import _character_record_lines
from app.pipeline.knowledge import (
    ALIGNMENTS,
    CLASSES,
    RACES,
    SKILLS,
    SPELLS,
    validate_stat_block,
)
from app.pipeline.statblocks import (
    build_stat_repair_schema,
    stat_block_rules_text,
)
from app.pipeline.worker import run_next_job
from app.providers.llm import chat_completion
from app.store import (
    EDGE_TYPES,
    enqueue_job,
    init_db,
    job_status,
    register_account,
    session_scope,
    world_entities,
)
from app.store.campaigns import ensure_generic_campaign
from app.store.candidates import payload_section_violations


def write_json(directory: Path, name: str, value: Any) -> None:
    (directory / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    )


def main() -> None:
    destination = PROJECT_ROOT / "docs/training/character"
    destination.mkdir(parents=True, exist_ok=True)
    record = json.loads(
        (PROJECT_ROOT / "docs/examples/ahmet-character.json").read_text()
    )
    # Generated mechanics use structured damage, unlike the manual form's
    # optional dice-string shortcut. Train the generated wire format.
    block = record["stat_block"]
    block["combat"] = {"ac": 16, "hp": 42, "hit_dice": "5d10+10"}
    block["skills"] = [
        {"name": "Athletics", "bonus": 5},
        {"name": "Performance", "bonus": 2},
    ]
    block["actions"] = [
        {
            "name": "Drumstick Strike",
            "to_hit": 5,
            "description": "Melee Weapon Attack: +5 to hit, reach 5 ft., one target. "
            "Hit: 5.5 (1d6 + 2) bludgeoning damage.",
            "damage": [
                {
                    "dice": "1d6",
                    "count": 1,
                    "sides": 6,
                    "bonus": 2,
                    "average": 5.5,
                    "type": "bludgeoning",
                }
            ],
        },
        {
            "name": "Rallying Rhythm",
            "description": "One ally within 30 feet who can hear the drum gains "
            "advantage on its next saving throw against being frightened before "
            "the start of Ahmet's next turn.",
        },
    ]
    block.update(
        {
            "saves": {"str": 5, "con": 5},
            "features": ["Second Wind", "Action Surge"],
            "initiative": 1,
            "passive_perception": 10,
            "proficiency_bonus": 3,
            "resources": {"second_wind": 1, "action_surge": 1},
        }
    )
    output = {
        "entities": [
            {
                "ref": "E0",
                "kind": "character",
                "name": record["name"],
                "text": "A veteran village drummer who carries a hidden map inside his davul.",
                "data": record,
            }
        ],
        "edges": [],
    }
    payload = {
        "theme": "High Fantasy",
        "key_figures": [
            {
                "name": record["name"],
                "role": "NPC",
                "record": {
                    "level_cr": "level 5",
                    "stat_block": {"identity": block["identity"]},
                },
            }
        ],
    }
    if payload_section_violations(record) or validate_stat_block(block):
        raise RuntimeError("Handcrafted target does not pass the production validators")

    settings = LLMSettings(
        endpoint="http://training.invalid/v1/",
        model="training-example",
        max_tokens=8192,
        enable_thinking=False,
    )
    runs: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="mythos-training-") as scratch:
        os.environ["MYTHOSCIRCLE_JOURNAL_ENABLED"] = "0"
        os.environ["MYTHOSCIRCLE_DB"] = f"sqlite:///{scratch}/training.db"
        init_db(os.environ["MYTHOSCIRCLE_DB"])
        for mode in ("complete", "record-repair", "stat-repair"):
            account = register_account(
                f"{mode}@example.invalid", "training-example-password"
            )
            campaign = ensure_generic_campaign(account.id, "High Fantasy")
            initial = copy.deepcopy(output)
            if mode == "record-repair":
                del initial["entities"][0]["data"]["party_hook"]
                patch = {
                    key: value for key, value in record.items() if key != "stat_block"
                }
                responses = [initial, {"records": [{"ref": "E0", "data": patch}]}]
            elif mode == "stat-repair":
                initial["entities"][0]["data"]["stat_block"]["combat"]["ac"] = 0
                responses = [
                    initial,
                    {"stat_blocks": [{"ref": "E0", "stat_block": block}]},
                ]
            else:
                responses = [initial]
            exchanges: list[dict[str, Any]] = []

            def respond(
                request: httpx.Request,
                responses: list[dict[str, Any]] = responses,
                exchanges: list[dict[str, Any]] = exchanges,
                mode: str = mode,
            ) -> httpx.Response:
                if not responses:
                    raise RuntimeError(f"Unexpected extra provider call in {mode}")
                answer = json.dumps(responses.pop(0), ensure_ascii=False)
                exchanges.append(
                    {"request": json.loads(request.content), "response": answer}
                )
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": answer}, "finish_reason": "stop"}
                        ],
                    },
                )

            def provider(prompt: str, settings: LLMSettings) -> str:
                return chat_completion(
                    prompt, settings=settings, transport=httpx.MockTransport(respond)
                )

            job = enqueue_job(campaign.id, "build_in", copy.deepcopy(payload))
            run_next_job(provider=provider, settings=settings)
            finished, _ = job_status(job.id)
            if finished.state != "succeeded" or responses:
                raise RuntimeError(f"{mode} replay failed: {finished.error}")
            with session_scope() as session:
                entities = world_entities(session, campaign.id)
                if len(entities) != 1 or entities[0].kind != "character":
                    raise RuntimeError("Replay did not commit exactly one character")
                committed = {
                    "kind": entities[0].kind,
                    "name": entities[0].name,
                    "text": entities[0].text,
                    "data": entities[0].data,
                }
            runs[mode] = {
                "state": finished.state,
                "calls": len(exchanges),
                "exchanges": exchanges,
                "committed": committed,
                "telemetry": finished.result.get("llm_calls")
                if finished.result
                else None,
            }

    first = runs["complete"]["exchanges"][0]
    write_json(destination, "forge-input.json", payload)
    description, lore = THEME_DEFAULT_SEEDS["High Fantasy"]
    write_json(
        destination,
        "context.json",
        {
            "title": "Generic (High Fantasy)",
            "theme": "High Fantasy",
            "description": description,
            "custom_lore": lore,
            "existing_entities_in_model_context": [],
            "notes": "",
            "roster": [{"ref": "E0", "kind": "character", "name": record["name"]}],
            "source": "Synthetic example; no private campaign data or live LLM calls",
        },
    )
    write_json(destination, "request.json", first["request"])
    write_json(destination, "expected-output.json", output)
    write_json(destination, "committed-output.json", runs["complete"]["committed"])
    write_json(destination, "replay-traces.json", runs)
    conditional_examples = {}
    for role in ("BBEG", "Monster"):
        variant = copy.deepcopy(output)
        data = variant["entities"][0]["data"]
        data["role"] = role
        data["boss"] = {
            "lair_actions": "On initiative 20, dust fills the ruined hall; sight beyond 20 feet is obscured until the next round.",
            "legendary_actions": "Once after another creature's turn, move up to half speed without provoking opportunity attacks.",
            "immunities": "None.",
            "vulnerabilities": "None.",
        }
        if role == "BBEG":
            data["class_profession"] = "Wizard"
            data["stat_block"]["identity"].update({"role": role, "class": "Wizard"})
            data["stat_block"]["spells"] = ["Magic Missile", "Shield", "Fireball"]
            data["stat_block"]["spellcasting"] = {
                "dc": 13,
                "attack_bonus": 5,
                "slots": [4, 3, 2],
            }
        else:
            data["level_cr"] = "CR 1"
            data["race_type"] = "Construct"
            data["class_profession"] = "Guardian"
            data["stat_block"]["identity"] = {
                "role": role,
                "race": "Construct",
                "cr": 1,
                "alignment": "LG",
            }
            data["stat_block"].pop("spells", None)
            data["stat_block"]["combat"]["hp"] = 60
            action = data["stat_block"]["actions"][0]
            action["description"] = (
                "Melee Weapon Attack: +5 to hit, reach 5 ft., one target. Hit: 10 (2d6 + 3) bludgeoning damage."
            )
            action["damage"] = [
                {
                    "dice": "2d6",
                    "count": 2,
                    "sides": 6,
                    "bonus": 3,
                    "average": 10,
                    "type": "bludgeoning",
                }
            ]
        issues = payload_section_violations(data) + validate_stat_block(
            data["stat_block"]
        )
        if issues:
            raise RuntimeError(
                f"Conditional {role} reference failed validation: {issues}"
            )
        conditional_examples[role] = variant
    write_json(destination, "conditional-examples.json", conditional_examples)
    write_json(
        destination,
        "validation.json",
        {
            "record_violations": payload_section_violations(record),
            "stat_block_violations": validate_stat_block(block),
            "runs": {
                mode: {
                    key: value
                    for key, value in run.items()
                    if key not in {"exchanges", "committed"}
                }
                for mode, run in runs.items()
            },
            "method": "Real queue worker and provider; handcrafted responses over HTTP MockTransport",
        },
    )
    write_json(
        destination,
        "vocabularies.json",
        {
            "roles": ["BBEG", "Monster", "NPC"],
            "races": sorted(RACES),
            "classes": sorted(CLASSES),
            "alignments": sorted(ALIGNMENTS),
            "skills": sorted(SKILLS),
            "edge_types": sorted(EDGE_TYPES),
            "spells_by_class": {
                klass: sorted(
                    name for name, classes in SPELLS.items() if klass in classes
                )
                for klass in sorted(CLASSES)
            },
        },
    )
    write_json(
        destination,
        "schemas.json",
        {
            "wave1_response_format": first["request"]["response_format"],
            "stat_repair_response_format": build_stat_repair_schema(),
        },
    )
    (destination / "system-prompt.txt").write_text(
        first["request"]["messages"][0]["content"] + "\n"
    )
    (destination / "user-prompt.txt").write_text(
        first["request"]["messages"][1]["content"] + "\n"
    )
    (destination / "stat-block-rules.txt").write_text(stat_block_rules_text() + "\n")
    (destination / "character-record-contract.txt").write_text(
        "\n".join(_character_record_lines()) + "\n"
    )
    for mode, run in runs.items():
        exchange = run["exchanges"][-1]
        messages = exchange["request"]["messages"] + [
            {"role": "assistant", "content": exchange["response"]}
        ]
        (destination / f"{mode}.jsonl").write_text(
            json.dumps({"messages": messages}, ensure_ascii=False) + "\n"
        )
        if mode != "complete":
            write_json(destination, f"{mode}-exchange.json", exchange)
    print(
        f"Exported complete example and two verified repair examples to {destination}"
    )
    print(
        "All three replays succeeded; no network calls or application database changes."
    )


if __name__ == "__main__":
    main()
