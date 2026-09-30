from types import SimpleNamespace

import pytest

from app.pipeline.build_in import (
    _apply_flat_record_repairs,
    _collect_flat_record_issues,
    _flat_record_violations,
    build_wave1_prompt,
)
from app.pipeline.generate import build_generate_prompt
from app.pipeline.prompt_catalog import prompt_contract
from app.pipeline.regenerate import _validate_output, build_regenerate_prompt
from app.pipeline.worker import JobPayloadError
from app.store.models import EntityInput


def test_prompt_catalog_contains_kind_and_dial_contracts() -> None:
    place = prompt_contract("build_in", "kind_contract")
    dial = prompt_contract("build_in", "dial_contract")
    assert "whats_hidden" in place
    assert "doctrine" in place
    assert "Each structured seed" in dial


def test_active_generation_prompts_include_catalog_contracts() -> None:
    seed = SimpleNamespace(title="Test", description="", theme="Fantasy", custom_lore="")
    wave = build_wave1_prompt(seed, {"places": ["City"], "factions": [], "key_figures": []})
    generated = build_generate_prompt(seed, "a dockmaster", ([], []))
    regenerated = build_regenerate_prompt(seed, {"role": "NPC"}, ["personality"], ([], []))
    assert "whats_hidden" in wave
    assert "Generate candidates of the requested kind" in generated
    assert "full AR24 character records" in regenerated
    assert "Place and faction regeneration is enrichment" in regenerated


def test_dial_requires_complete_kind_record_and_detail_floor() -> None:
    entity = EntityInput(
        kind="place",
        name="Deepwater",
        text="A busy harbor city.",
        data={"dial": "simple", "description": "A busy harbor city."},
    )
    roster = [
        (
            "places",
            "Deepwater",
            {"name": "Deepwater", "dial": "simple", "description": "A busy harbor city."},
        )
    ]
    violations = _flat_record_violations([entity], roster)
    assert "missing place.inhabitants" in " | ".join(violations)
    assert "missing place.whats_hidden" in " | ".join(violations)

    entity.data["inhabitants"] = (
        "Merchants and sailors fill the harbor streets every morning, while immigrant families "
        "run most neighborhood shops and markets."
    )
    entity.data["whats_hidden"] = (
        "A sealed lighthouse vault hides records of the old rulers and proof of a forgotten treaty."
    )
    assert _flat_record_violations([entity], roster) == []


def test_flat_regeneration_uses_kind_sections_and_dial_floor() -> None:
    seed = SimpleNamespace(title="Test", description="", theme="Fantasy", custom_lore="")
    record = {
        "name": "Deepwater",
        "dial": "draft",
        "archetype": "City",
        "description": "A busy harbor city.",
    }
    prompt = build_regenerate_prompt(
        seed,
        record,
        ["description", "inhabitants", "whats_hidden"],
        ([], []),
        entity_kind="place",
        dial="draft",
    )
    assert "TARGET PLACE RECORD" in prompt
    assert "inhabitants" in prompt and "whats_hidden" in prompt
    good = {
        **record,
        "description": "A crowded harbor city with old walls and busy markets.",
        "inhabitants": "Merchants and sailors fill the harbor streets each morning.",
        "whats_hidden": "A sealed vault beneath the lighthouse hides a forgotten treaty.",
    }
    _validate_output(
        good,
        record,
        ["description", "inhabitants", "whats_hidden"],
        entity_kind="place",
        dial="draft",
    )
    with pytest.raises(JobPayloadError):
        _validate_output(
            {**good, "inhabitants": "Sailors."},
            record,
            ["description", "inhabitants", "whats_hidden"],
            entity_kind="place",
            dial="draft",
        )


def test_flat_record_repair_targets_short_fields_and_preserves_controls() -> None:
    entity = EntityInput(
        kind="place",
        name="Saltglass Harbor",
        text="A crowded harbor.",
        data={
            "description": "A crowded harbor.",
            "inhabitants": "Sailors and traders.",
            "whats_hidden": "A sealed vault.",
            "archetype": "Market",
            "dial": "important",
        },
    )
    roster = [
        (
            "places",
            "Saltglass Harbor",
            {"name": "Saltglass Harbor", "dial": "important", "description": "A crowded harbor."},
        )
    ]
    issues = _collect_flat_record_issues([entity], roster)
    assert [issue.fields for issue in issues] == [("inhabitants", "whats_hidden")]
    repaired = _apply_flat_record_repairs(
        [entity],
        {
            0: {
                "inhabitants": (
                    "Dockworkers, merchant sailors, salvage crews, and tide scavengers crowd "
                    "the harbor as exposed seabeds reveal new routes and dangerous wreckage, "
                    "while brokers, divers, and desperate families compete for every newly "
                    "uncovered "
                    "piece of the drowned city."
                ),
                "whats_hidden": (
                    "Beneath the ancient piers, brine sealed chambers contain maps proving the old "
                    "kingdom once controlled the harbor before the sea withdrew, and a locked "
                    "shrine "
                    "still records the name of the sailor who caused the retreat."
                ),
                "name": "Changed by the model",
                "dial": "nothing",
            }
        },
    )
    assert repaired[0].data["archetype"] == "Market"
    assert repaired[0].data["dial"] == "important"
    assert repaired[0].name == "Saltglass Harbor"
    assert _flat_record_violations(repaired, roster) == []


def test_flat_contract_ignores_discarded_place_twin() -> None:
    first = EntityInput(
        kind="place",
        name="Blackwake Lighthouse",
        text="A lighthouse.",
        data={
            "description": "A black lighthouse rises above the salt flats, guiding ships through "
            "dangerous coastal fog.",
            "inhabitants": "A solitary keeper watches the coast through blue saltfire each night, "
            "joined by spectral sailors who never leave the rocks.",
            "whats_hidden": "The flame conceals an ancient signal chamber beneath the tower "
            "foundations, "
            "where drowned navigators record ships that have not yet arrived.",
        },
    )
    twin = EntityInput(
        kind="place",
        name="Blackwake Lighthouse",
        text="A lighthouse.",
        data={
            "description": "A lighthouse.",
            "inhabitants": "A keeper.",
            "whats_hidden": "A secret.",
        },
    )
    roster = [
        (
            "places",
            "Blackwake Lighthouse",
            {"dial": "simple", "description": "A black lighthouse rises above the salt flats."},
        ),
        ("places", "Blackwake Lighthouse", {"dial": "simple"}),
    ]
    assert _flat_record_violations([first, twin], roster) == []
