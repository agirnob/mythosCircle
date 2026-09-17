"""The Generic library (owner spec, 2026-09-17): a per-account storage
world for characters generated without a canon world.

Invariants under test:
- ensure_generic_campaign is create-or-get, one per account, theme-free.
- The generic build gate: characters only (no world-shaping sections, no
  declared relations), a theme with a default seed, authored entries.
- Context isolation: the wave-1 prompt carries the THEME's default
  description/lore and NEVER the library's entities or lore.
- move: fresh-ULID copy into the canon world + cascade delete out of the
  library, ownership on both campaigns, generic-shape checks.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.config import THEME_DEFAULT_SEEDS
from app.core.settings import LLMSettings
from app.pipeline.worker import run_next_job
from app.store import (
    InvalidJobInputError,
    UnknownCampaignError,
    commit_subgraph,
    create_campaign,
    enqueue_job,
    job_status,
)
from app.store.campaigns import ensure_generic_campaign
from app.store.commit import move_character_from_generic
from app.store.db import init_db, session_scope
from app.store.models import EntityInput
from app.store.read import world_entities, world_state

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")


_OWNER: dict[str, str] = {}


def _owner_id() -> str:
    """One account per test session — the register rate limiter is shared
    module-globals-wide, and this file only ever needs a single owner."""
    from app.core.ids import new_id
    from app.store import register_account

    if "id" not in _OWNER:
        _OWNER["id"] = register_account(
            f"generic-{new_id()}@example.com", "password123"
        ).id
    return _OWNER["id"]


def ids_new() -> str:
    from app.core import ids

    return ids.new_id()


@pytest.fixture(autouse=True)
def _db(tmp_path: Path) -> Any:
    from app.store.db import app_db_url

    previous = app_db_url()
    _OWNER.clear()  # the cached account row died with the previous test DB
    init_db(f"sqlite:///{tmp_path / 'generic.db'}")
    yield
    init_db(previous)


@pytest.fixture
def world(_db: None) -> Any:
    return create_campaign(
        _owner_id(),
        title="Canon World",
        description="a real world",
        theme="High Fantasy",
        custom_lore="canon lore",
    ).id


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def test_generic_is_create_or_get_one_per_account() -> None:
    first = ensure_generic_campaign(_owner_id(), "Grimdark")
    assert first.is_generic is True
    second = ensure_generic_campaign(first.owner_id, "High Fantasy")
    assert second.id == first.id  # the theme rides jobs, not the world


def test_generic_rejects_unknown_theme() -> None:
    with pytest.raises(Exception, match="theme"):
        ensure_generic_campaign(_owner_id(), "Cyberpunk")


# ---------------------------------------------------------------------------
# The generic build gate (enqueue, zero rows on any rejection)
# ---------------------------------------------------------------------------


def _figure(name: str = "Corrosion Sal", **record: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "role": "NPC"}
    if record:
        entry["record"] = record
    return entry


def _generic_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key_figures": [_figure(personality="DM-authored personality.")],
        "theme": "Grimdark",
    }
    payload.update(overrides)
    return payload


def test_generic_gate_rejects_world_shaping_sections(world: str) -> None:
    generic = ensure_generic_campaign(_owner_id(), "Grimdark")
    for section, value in (
        ("places", ["The Silt Forges"]),
        ("factions", ["A cult"]),
        ("notes", "world lore"),
    ):
        with pytest.raises(InvalidJobInputError, match="world-shaping"):
            enqueue_job(generic.id, "build_in", _generic_payload(**{section: value}))


def test_generic_gate_rejects_declared_relations(world: str) -> None:
    generic = ensure_generic_campaign(_owner_id(), "Grimdark")
    figure = _figure(personality="p.")
    figure["relations"] = [{"type": "located_in", "target_name": "Vaelmoor"}]
    with pytest.raises(InvalidJobInputError, match="relations"):
        enqueue_job(generic.id, "build_in", {"key_figures": [figure], "theme": "Grimdark"})


def test_generic_gate_rejects_legacy_strings_and_bad_theme(world: str) -> None:
    generic = ensure_generic_campaign(_owner_id(), "Grimdark")
    with pytest.raises(InvalidJobInputError, match="authored entry"):
        enqueue_job(generic.id, "build_in", {"key_figures": ["plain"], "theme": "Grimdark"})
    with pytest.raises(InvalidJobInputError, match="theme"):
        enqueue_job(
            generic.id,
            "build_in",
            {"key_figures": [_figure()], "theme": "Cyberpunk"},
        )


def test_generic_gate_accepts_a_clean_payload(world: str) -> None:
    generic = ensure_generic_campaign(_owner_id(), "Grimdark")
    job = enqueue_job(generic.id, "build_in", _generic_payload())
    assert job.kind == "build_in"


# ---------------------------------------------------------------------------
# Context isolation: the wave-1 prompt sees the THEME, never the library
# ---------------------------------------------------------------------------


def test_generic_prompt_carries_theme_seed_not_library_state(world: str) -> None:
    generic = ensure_generic_campaign(_owner_id(), "Grimdark")
    # The library already holds a character whose lore must NEVER leak
    # into the next generation's prompt.
    first = _figure("First Resident", personality="Authored personality.")
    job_1 = enqueue_job(generic.id, "build_in", {"key_figures": [first], "theme": "Grimdark"})
    prompts: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        prompts.append(prompt)
        return json.dumps(
            {
                "entities": [
                    {
                        "ref": "E0",
                        "kind": "character",
                        "name": "First Resident",
                        "data": _generated_record("First Resident"),
                    }
                ],
                "edges": [],
            }
        )

    assert run_next_job(provider=provider, settings=SETTINGS) == job_1.id
    assert len(prompts) == 1
    defaults_description, defaults_lore = THEME_DEFAULT_SEEDS["Grimdark"]
    assert defaults_description in prompts[0]
    assert defaults_lore in prompts[0]

    # A SECOND generic build must not see the first character or its lore
    second = _figure("Second Resident", personality="Also authored.")
    job_2 = enqueue_job(generic.id, "build_in", {"key_figures": [second], "theme": "Grimdark"})
    assert run_next_job(provider=provider, settings=SETTINGS) == job_2.id
    latest = prompts[-1]
    assert "First Resident" not in latest
    assert "Authored personality." not in latest
    assert "Second Resident" in latest


def _generated_record(name: str) -> dict[str, Any]:
    """A gate-clean generated record (the fake model's output)."""
    return {
        "name": name,
        "role": "NPC",
        "level_cr": "level 5",
        "race_type": "Human",
        "class_profession": "Fighter",
        "alignment": "LG",
        "personality": "Generated personality.",
        "secret": "Generated secret.",
        "rumor": "Generated rumor.",
        "party_hook": "Generated hook.",
        "appearance": "Generated appearance.",
        "background": "Generated background.",
        "goals": "Generated goals.",
        "relationships": "Generated relationships.",
        "voice_style": "Generated voice.",
        "catchphrases": "Generated catchphrases.",
        "world_integration": {
            "reputation": "Generated reputation.",
            "factions": "Generated factions.",
            "current_location": "Generated location.",
            "reaction_matrix": "C0: watches.",
            "on_defeat": "Generated defeat.",
        },
        "stat_block": {
            "identity": {"role": "NPC", "race": "Human", "level": 5},
            "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
            "combat": {"ac": 16, "hp": 66},
            "actions": [
                {
                    "name": "Longsword",
                    "description": "Melee Weapon Attack: +5 to hit, 4d10+5 slashing",
                }
            ],
        },
    }


def test_generic_build_commits_into_the_library_with_authored_verbatim(world: str) -> None:
    generic = ensure_generic_campaign(_owner_id(), "Grimdark")

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(
            {
                "entities": [
                    {
                        "ref": "E0",
                        "kind": "character",
                        "name": "Corrosion Sal",
                        "data": _generated_record("Corrosion Sal"),
                    }
                ],
                "edges": [],
            }
        )

    job_id = enqueue_job(
        generic.id,
        "build_in",
        _generic_payload(
            key_figures=[_figure("Corrosion Sal", personality="DM-authored personality.")]
        ),
    )
    assert run_next_job(provider=provider, settings=SETTINGS) == job_id.id
    job, _position = job_status(job_id.id)
    assert job.state == "succeeded"
    with session_scope() as session:
        rows = {row.name: row for row in world_entities(session, generic.id)}
    sal = rows["Corrosion Sal"]
    assert sal.kind == "character"
    assert sal.data["personality"] == "DM-authored personality."


# ---------------------------------------------------------------------------
# Move: Generic -> canon world
# ---------------------------------------------------------------------------


def _commit_character(campaign_id: str, name: str) -> str:
    commit_subgraph(
        campaign_id,
        entities=[
            EntityInput(kind="character", name=name, text=None, data=_generated_record(name))
        ],
        allow_orphans=True,
    )
    with session_scope() as session:
        entities, _edges = world_state(session, campaign_id)
        found = next(entity.id for entity in entities if entity.name == name)
    return found


def test_move_copies_into_canon_and_leaves_the_library(_db: None) -> None:
    owner = _owner_id()
    canon = create_campaign(
        owner, title="Canon", description="d", theme="High Fantasy", custom_lore="l"
    ).id
    generic = ensure_generic_campaign(owner, "Grimdark")
    entity_id = _commit_character(generic.id, "Corrosion Sal")

    new_id, _revision = move_character_from_generic(owner, canon, generic.id, entity_id)
    assert new_id != entity_id  # a fresh ULID in the canon world

    with session_scope() as session:
        canon = list(world_entities(session, canon))
    moved = next(row for row in canon if row.id == new_id)
    assert moved.kind == "character"
    assert moved.data["stat_block"]["identity"]["race"] == "Human"
    with session_scope() as session:
        library = list(world_entities(session, generic.id))
    assert all(row.id != entity_id for row in library)  # the library row left


def test_move_rejects_non_generic_source_and_generic_target(_db: None) -> None:
    owner = _owner_id()
    canon = create_campaign(
        owner, title="Canon", description="d", theme="High Fantasy", custom_lore="l"
    ).id
    generic = ensure_generic_campaign(owner, "Grimdark")
    entity_id = _commit_character(generic.id, "Corrosion Sal")

    # canon -> canon is not a library move
    with pytest.raises(InvalidJobInputError, match="not a Generic library"):
        move_character_from_generic(owner, canon, canon, entity_id)
    # generic -> generic is not a canon move
    with pytest.raises(InvalidJobInputError, match="canon world"):
        move_character_from_generic(owner, generic.id, generic.id, entity_id)


def test_move_rejects_a_foreign_library(world: str) -> None:
    # a SECOND account (the limiter allows a handful of registrations per
    # process; this file registers exactly two)
    from app.core.ids import new_id as _new_id
    from app.store import register_account

    stranger = register_account(f"generic-stranger-{_new_id()}@example.com", "password123")
    foreign_generic = ensure_generic_campaign(stranger.id, "Grimdark")
    entity_id = _commit_character(foreign_generic.id, "Corrosion Sal")
    with pytest.raises(UnknownCampaignError):
        move_character_from_generic(
            _owner_id(),  # a DIFFERENT account
            world,
            foreign_generic.id,
            entity_id,
        )
