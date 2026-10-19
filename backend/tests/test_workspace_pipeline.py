"""Workspace worker: typed draft-only suggestions and receipt-backed expansion."""

import json

import pytest
from test_workspace import graph, save_nodes, submit

from app.core import ids
from app.core.settings import LLMSettings
from app.pipeline import build_in as build
from app.pipeline.worker import run_next_job
from app.store import cancel_job, job_status
from app.store.workspace import WorkspaceNode, get_workspace

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")


def test_planner_returns_typed_structure_without_writing_world(workspace_world):
    _, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)
    job = submit(campaign, kind="plan_expansion", scope=harbor)
    calls = []

    def provider(prompt, **kwargs):
        calls.append(prompt)
        return json.dumps({"nodes": [node.model_dump()]})

    assert run_next_job(provider=provider, settings=SETTINGS) == job.id
    stored = job_status(job.id)[0]
    assert stored.state == "succeeded", stored.error
    assert stored.result["proposal"]["nodes"] == [node.model_dump()]
    assert stored.result["source_draft_version"] == 0
    assert get_workspace(campaign)["draft"]["nodes"] == []
    assert len(graph(campaign)[0]) == 1
    assert "Old quays" in calls[0]
    assert "Keep the ferrymen's oath." in calls[0]
    assert harbor in calls[0]


@pytest.mark.parametrize(
    "bad", ["malformed", "unknown-fields", "out-of-scope", "modifies-source", "cycle"]
)
def test_bad_suggestions_fail_without_changing_world_or_draft(workspace_world, bad):
    _, campaign, harbor = workspace_world
    original = WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)
    save_nodes(campaign, [original])
    before = get_workspace(campaign)
    job = submit(campaign, kind="plan_expansion", scope=harbor)
    extra = WorkspaceNode(id=ids.new_id(), kind="character", name="Edda", parent_id=harbor)
    proposal = {"nodes": [original.model_dump(), extra.model_dump()]}
    if bad == "unknown-fields":
        proposal["entities"] = []
    elif bad == "out-of-scope":
        proposal["nodes"][1]["parent_id"] = None
    elif bad == "modifies-source":
        proposal["nodes"][0]["name"] = "Model overwrote the DM"
    elif bad == "cycle":
        proposal["nodes"][1] = WorkspaceNode(
            id=extra.id, kind="place", parent_id=extra.id
        ).model_dump()

    def provider(*args, **kwargs):
        return "not json" if bad == "malformed" else json.dumps(proposal)

    run_next_job(provider=provider, settings=SETTINGS)
    assert job_status(job.id)[0].state == "failed"
    assert get_workspace(campaign) == before
    assert len(graph(campaign)[0]) == 1


def _fake_content(monkeypatch, generated_rosters):
    from app.store import models

    def generate(job, budget, provider, settings, seed, notes, chunks, **kwargs):
        roster = [row for chunk in chunks for row in chunk]
        generated_rosters.append(roster)
        assert "Keep the ferrymen's oath." in seed.custom_lore
        assert "Old quays" in notes
        rows = [
            models.EntityInput(
                kind={"places": "place", "factions": "faction", "key_figures": "character"}[
                    section
                ],
                name=name,
                text="Generated prose",
                id=ids.new_id(),
            )
            for _position, section, name, _authored in roster
        ]
        return rows, [], False, []

    monkeypatch.setattr(build, "_run_wave1_chunks", generate)
    gates = []
    for name in (
        "_enforce_flat_records",
        "_enforce_entity_names",
        "_enforce_character_records",
        "_enforce_stat_blocks",
    ):

        def gate(job, budget, provider, settings, rows, *args, _name=name, **kwargs):
            gates.append(_name)
            return rows, False

        monkeypatch.setattr(build, name, gate)
    return gates


def test_named_and_multiple_unnamed_slots_keep_scope_and_identity(workspace_world, monkeypatch):
    _, campaign, harbor = workspace_world
    nodes = [
        WorkspaceNode(id=ids.new_id(), kind="place", parent_id=harbor),
        WorkspaceNode(id=ids.new_id(), kind="character", parent_id=harbor),
        WorkspaceNode(id=ids.new_id(), kind="character", name="Lysa", parent_id=harbor),
    ]
    save_nodes(campaign, nodes)
    job = submit(campaign)
    generated = []
    gates = _fake_content(monkeypatch, generated)

    def provider(*args, **kwargs):
        return json.dumps({"names": {nodes[0].id: "Ropewalk", nodes[1].id: "Edda"}})

    run_next_job(provider=provider, settings=SETTINGS)
    stored = job_status(job.id)[0]
    assert stored.state == "succeeded", stored.error
    assert {row[2] for row in generated[0]} == {"Ropewalk", "Edda", "Lysa"}
    assert len(gates) == 4
    receipts = {r["node_id"]: r for r in stored.result["receipts"]}
    assert set(receipts) == {n.id for n in nodes}
    entities, edges = graph(campaign)
    assert {e.name for e in entities} == {"Harbor", "Ropewalk", "Edda", "Lysa"}
    assert {e.dst for e in edges} == {harbor}
    assert get_workspace(campaign)["draft"]["nodes"] == []


def test_generation_failure_after_names_preserves_entire_draft(workspace_world, monkeypatch):
    _, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="character", parent_id=harbor)
    save_nodes(campaign, [node])
    before = get_workspace(campaign)
    job = submit(campaign)

    def crash(*args, **kwargs):
        raise RuntimeError("Interrupted record generation")

    monkeypatch.setattr(build, "_run_wave1_chunks", crash)

    def provider(*args, **kwargs):
        return json.dumps({"names": {node.id: "Edda"}})

    run_next_job(provider=provider, settings=SETTINGS)
    stored = job_status(job.id)[0]
    assert stored.state == "failed"
    assert stored.payload["materialized_names"] == {node.id: "Edda"}
    assert get_workspace(campaign) == before
    assert len(graph(campaign)[0]) == 1


def test_cancel_during_content_generation_commits_nothing(workspace_world, monkeypatch):
    _, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)
    save_nodes(campaign, [node])
    job = submit(campaign)

    def cancel(*args, **kwargs):
        cancel_job(job.id)
        return [], [], True, []

    monkeypatch.setattr(build, "_run_wave1_chunks", cancel)
    run_next_job(provider=lambda *a, **k: "", settings=SETTINGS)
    assert job_status(job.id)[0].state == "cancelled"
    assert len(get_workspace(campaign)["draft"]["nodes"]) == 1
    assert len(graph(campaign)[0]) == 1


def test_generated_collision_rolls_back_all_reviewed_additions(workspace_world, monkeypatch):
    _, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="place", parent_id=harbor)
    save_nodes(campaign, [node])
    job = submit(campaign)
    generated = []
    _fake_content(monkeypatch, generated)
    # The provider naming phase itself rejects a canonical collision before content work.
    run_next_job(
        provider=lambda *a, **k: json.dumps({"names": {node.id: "The Harbor."}}), settings=SETTINGS
    )
    assert job_status(job.id)[0].state == "failed"
    assert not generated
    assert len(graph(campaign)[0]) == 1
    assert len(get_workspace(campaign)["draft"]["nodes"]) == 1


def test_real_generation_quality_pipeline_with_unnamed_character(workspace_world):
    """Run the real chunk/record/stat gates; only the remote model is replaced."""
    from test_build_in_pipeline import _MIRA_STAT_BLOCK, _character_record

    _, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="character", parent_id=harbor)
    save_nodes(campaign, [node])
    job = submit(campaign)
    calls = []

    def provider(prompt, **kwargs):
        calls.append(prompt)
        if prompt.startswith("Name exactly these reviewed slots."):
            return json.dumps({"names": {node.id: "Rook"}})
        if "You are wiring an already-generated TTRPG world" in prompt:
            return json.dumps({"edges": []})
        return json.dumps(
            {
                "entities": [
                    {
                        "ref": "E0",
                        "kind": "character",
                        "name": "Rook",
                        "data": _character_record("Rook", stat_block=_MIRA_STAT_BLOCK),
                    }
                ],
                "edges": [],
            }
        )

    run_next_job(provider=provider, settings=SETTINGS)
    stored = job_status(job.id)[0]
    assert stored.state == "succeeded", stored.error
    created = next(e for e in graph(campaign)[0] if e.name == "Rook")
    assert created.data["stat_block"]["identity"]["role"] == "NPC"
    assert any(
        e.src == created.id and e.dst == harbor and e.type == "located_in"
        for e in graph(campaign)[1]
    )
    assert stored.result["receipts"][0]["entity_id"] == created.id
    assert len(calls) >= 2


def test_planner_schema_closes_defaulted_properties():
    from app.pipeline.workspace import planning_schema

    schema = planning_schema()

    def check(value):
        if isinstance(value, dict):
            assert "default" not in value
            if value.get("type") == "object":
                assert set(value["required"]) == set(value["properties"])
                assert value["additionalProperties"] is False
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(schema)


def test_expansion_global_guidance_reaches_real_content_prompt(workspace_world):
    from test_build_in_pipeline import _MIRA_STAT_BLOCK, _character_record

    from app.store.workspace import WorkspaceSubmit, submit_workspace

    _, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="character", name="Rook", parent_id=harbor)
    snapshot = save_nodes(campaign, [node])
    job = submit_workspace(
        campaign,
        "expand_world",
        WorkspaceSubmit(
            request_id=ids.new_id(),
            expected_draft_version=snapshot["draft_version"],
            expected_world_version=snapshot["world_version"],
            guidance="Everyone fears the glass tide.",
        ),
    )
    prompts = []

    def provider(prompt, **kwargs):
        prompts.append(prompt)
        if "You are wiring an already-generated TTRPG world" in prompt:
            return json.dumps({"edges": []})
        return json.dumps(
            {
                "entities": [
                    {
                        "ref": "E0",
                        "kind": "character",
                        "name": "Rook",
                        "data": _character_record("Rook", stat_block=_MIRA_STAT_BLOCK),
                    }
                ],
                "edges": [],
            }
        )

    run_next_job(provider=provider, settings=SETTINGS)
    assert job_status(job.id)[0].state == "succeeded"
    assert "Everyone fears the glass tide." in prompts[0]
