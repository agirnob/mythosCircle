"""Persistent workspace contracts: draft validation, atomic receipts, and replay."""

import pytest
from sqlalchemy import select

from app.core import ids
from app.core.settings import LLMSettings
from app.pipeline.worker import run_next_job
from app.store import (
    claim_next_job,
    commit_subgraph,
    create_campaign,
    delete_campaign,
    job_status,
    models,
    recover_stale_running,
    session_scope,
)
from app.store.jobs import InvalidJobInputError
from app.store.read import world_edges, world_entities
from app.store.workspace import (
    WorkspaceConflict,
    WorkspaceDraft,
    WorkspaceNode,
    WorkspaceSave,
    WorkspaceSubmit,
    commit_expansion,
    execution_snapshot,
    get_workspace,
    pin_materialized_names,
    save_workspace,
    submit_workspace,
)


def save_nodes(campaign: str, nodes: list[WorkspaceNode]) -> dict:
    snapshot = get_workspace(campaign)
    return save_workspace(
        campaign,
        WorkspaceSave(
            expected_draft_version=snapshot["draft_version"],
            expected_world_version=snapshot["world_version"],
            draft=WorkspaceDraft(nodes=nodes),
        ),
    )


def submit(
    campaign: str,
    *,
    kind: str = "expand_world",
    selected: list[str] | None = None,
    scope: str | None = None,
    request: str | None = None,
) -> models.Job:
    snapshot = get_workspace(campaign)
    return submit_workspace(
        campaign,
        kind,
        WorkspaceSubmit(
            request_id=request or ids.new_id(),
            expected_draft_version=snapshot["draft_version"],
            expected_world_version=snapshot["world_version"],
            selected_ids=selected or [],
            scope_id=scope,
        ),
    )


def graph(campaign: str) -> tuple[list[models.Entity], list[models.Edge]]:
    with session_scope() as session:
        return list(world_entities(session, campaign)), list(world_edges(session, campaign))


def test_draft_save_persists_without_world_mutation(workspace_world):
    _, campaign, harbor = workspace_world
    before = graph(campaign)
    node = WorkspaceNode(
        id=ids.new_id(),
        kind="place",
        name="",
        parent_id=harbor,
        guidance="  Keep this exact.\nAnother line.  ",
    )
    saved = save_nodes(campaign, [node])
    loaded = get_workspace(campaign)
    assert loaded == saved
    assert loaded["draft"]["nodes"][0]["guidance"] == node.guidance
    assert loaded["draft_version"] == 1
    assert [(r.id, r.name) for r in graph(campaign)[0]] == [(r.id, r.name) for r in before[0]]
    assert loaded["world_version"] == get_workspace(campaign)["world_version"]


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "collision",
        "foreign",
        "self",
        "cycle",
        "wrong-parent",
        "wrong-membership",
        "too-many",
    ],
)
def test_invalid_draft_preserves_previous_state(workspace_world, case):
    owner, campaign, harbor = workspace_world
    first = WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)
    second = WorkspaceNode(id=ids.new_id(), kind="place", name="Quay", parent_id=harbor)
    nodes = [first]
    if case == "duplicate":
        nodes = [first, first]
    elif case == "collision":
        nodes = [first.model_copy(update={"name": "The Harbor."})]
    elif case == "foreign":
        other = create_campaign(
            owner, title="Other", description="", theme="High Fantasy", custom_lore=""
        )
        foreign = ids.new_id()
        commit_subgraph(
            other.id,
            [models.EntityInput(id=foreign, kind="place", name="Other place")],
            [],
            base_revision=None,
            allow_orphans=True,
        )
        nodes = [first.model_copy(update={"parent_id": foreign})]
    elif case == "self":
        nodes = [first.model_copy(update={"parent_id": first.id})]
    elif case == "cycle":
        nodes = [
            first.model_copy(update={"parent_id": second.id}),
            second.model_copy(update={"parent_id": first.id}),
        ]
    elif case == "wrong-parent":
        nodes = [
            first,
            WorkspaceNode(id=second.id, kind="character", name="Edda", parent_id=harbor),
            first.model_copy(update={"id": ids.new_id(), "parent_id": second.id}),
        ]
    elif case == "wrong-membership":
        nodes = [first.model_copy(update={"faction_id": harbor})]
    else:
        nodes = [WorkspaceNode(id=ids.new_id(), kind="place", parent_id=harbor) for _ in range(101)]
    previous = get_workspace(campaign)
    with pytest.raises(InvalidJobInputError):
        save_nodes(campaign, nodes)
    assert get_workspace(campaign) == previous
    assert len(graph(campaign)[0]) == 1


def test_per_kind_counts_allow_mixed_more_than_100(workspace_world):
    _, campaign, harbor = workspace_world
    nodes = [
        WorkspaceNode(id=ids.new_id(), kind=kind, parent_id=harbor)
        for kind in ("place", "character")
        for _ in range(60)
    ]
    assert len(save_nodes(campaign, nodes)["draft"]["nodes"]) == 120


def test_stale_draft_and_world_revisions_reject_without_overwrite(workspace_world):
    _, campaign, harbor = workspace_world
    before = get_workspace(campaign)
    node = WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)
    save_nodes(campaign, [node])
    with pytest.raises(WorkspaceConflict):
        save_workspace(
            campaign,
            WorkspaceSave(
                expected_draft_version=before["draft_version"],
                expected_world_version=before["world_version"],
                draft=WorkspaceDraft(),
            ),
        )
    saved = get_workspace(campaign)
    commit_subgraph(
        campaign,
        [models.EntityInput(kind="place", name="Market")],
        [],
        base_revision=saved["world_version"],
        allow_orphans=True,
    )
    with pytest.raises(WorkspaceConflict):
        save_workspace(
            campaign,
            WorkspaceSave(
                expected_draft_version=saved["draft_version"],
                expected_world_version=saved["world_version"],
                draft=WorkspaceDraft(),
            ),
        )
    assert get_workspace(campaign)["draft"] == saved["draft"]


def test_idempotent_submission_and_changed_key_conflict(workspace_world):
    _, campaign, harbor = workspace_world
    save_nodes(
        campaign, [WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)]
    )
    snapshot = get_workspace(campaign)
    body = WorkspaceSubmit(
        request_id=ids.new_id(),
        expected_draft_version=snapshot["draft_version"],
        expected_world_version=snapshot["world_version"],
    )
    first = submit_workspace(campaign, "expand_world", body)
    assert submit_workspace(campaign, "expand_world", body).id == first.id
    with pytest.raises(WorkspaceConflict):
        submit_workspace(
            campaign, "expand_world", body.model_copy(update={"guidance": "Changed input"})
        )


def test_atomic_checkpoint_preserves_existing_and_maps_links(workspace_world):
    _, campaign, harbor = workspace_world
    place = WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)
    faction = WorkspaceNode(id=ids.new_id(), kind="faction", name="Night Tide", parent_id=place.id)
    person = WorkspaceNode(
        id=ids.new_id(), kind="character", name="Edda", parent_id=place.id, faction_id=faction.id
    )
    save_nodes(campaign, [place, faction, person])
    job = submit(campaign)
    assert claim_next_job().id == job.id
    receipts = commit_expansion(
        job,
        [place, faction, person],
        [
            models.EntityInput(kind=n.kind, name=n.name, text=n.guidance or "Generated content")
            for n in (place, faction, person)
        ],
    )
    mapping = {r["node_id"]: r["entity_id"] for r in receipts}
    entities, edges = graph(campaign)
    assert len(entities) == 4
    assert next(r for r in entities if r.id == harbor).text == "Old quays"
    assert (mapping[place.id], harbor, "part_of") in {(e.src, e.dst, e.type) for e in edges}
    assert (mapping[person.id], mapping[place.id], "located_in") in {
        (e.src, e.dst, e.type) for e in edges
    }
    assert (mapping[person.id], mapping[faction.id], "member_of") in {
        (e.src, e.dst, e.type) for e in edges
    }
    assert get_workspace(campaign)["draft"]["nodes"] == []
    assert len(get_workspace(campaign)["receipts"]) == 3


def test_graph_and_receipts_rollback_together(workspace_world, monkeypatch):
    from app.store import workspace as store

    _, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)
    save_nodes(campaign, [node])
    job = submit(campaign)
    claim_next_job()
    before = get_workspace(campaign)
    commit = store._commit

    def fail_after_graph(*args, **kwargs):
        commit(*args, **kwargs)
        raise RuntimeError("Interrupted before receipt")

    monkeypatch.setattr(store, "_commit", fail_after_graph)
    with pytest.raises(RuntimeError):
        commit_expansion(job, [node], [models.EntityInput(kind="place", name="Ropewalk")])
    assert get_workspace(campaign) == before
    assert len(graph(campaign)[0]) == 1


def test_replay_after_committed_checkpoint_never_calls_model(workspace_world):
    _, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="character", name="Edda", parent_id=harbor)
    save_nodes(campaign, [node])
    job = submit(campaign)
    claim_next_job()
    commit_expansion(job, [node], [models.EntityInput(kind="character", name="Edda")])
    recover_stale_running()

    def forbidden(*args, **kwargs):
        raise AssertionError("Completed checkpoint replay must not call the model")

    assert (
        run_next_job(
            provider=forbidden, settings=LLMSettings(endpoint="http://test/v1", model="test")
        )
        == job.id
    )
    assert job_status(job.id)[0].state == "succeeded"
    assert len(graph(campaign)[0]) == 2
    assert len(get_workspace(campaign)["receipts"]) == 1


def test_selected_parent_completion_remaps_unfinished_child(workspace_world):
    _, campaign, harbor = workspace_world
    parent = WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)
    child = WorkspaceNode(id=ids.new_id(), kind="character", name="Edda", parent_id=parent.id)
    save_nodes(campaign, [parent, child])
    job = submit(campaign, selected=[parent.id])
    claim_next_job()
    receipts = commit_expansion(job, [parent], [models.EntityInput(kind="place", name="Ropewalk")])
    assert get_workspace(campaign)["draft"]["nodes"] == [
        {**child.model_dump(), "parent_id": receipts[0]["entity_id"]}
    ]


def test_materialized_names_are_pinned_and_collision_validated(workspace_world):
    _, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="place", parent_id=harbor)
    save_nodes(campaign, [node])
    job = submit(campaign)
    claim_next_job()
    with pytest.raises(InvalidJobInputError):
        pin_materialized_names(job, {node.id: "The Harbor."})
    assert pin_materialized_names(job, {node.id: "Ropewalk"}) == {node.id: "Ropewalk"}
    assert pin_materialized_names(job, {node.id: "Different name"}) == {node.id: "Ropewalk"}
    assert job_status(job.id)[0].payload["materialized_names"] == {node.id: "Ropewalk"}
    assert len(graph(campaign)[0]) == 1


def test_empty_planning_snapshot_checks_stale_world(workspace_world):
    _, campaign, harbor = workspace_world
    job = submit(campaign, kind="plan_expansion", scope=harbor)
    before = get_workspace(campaign)
    commit_subgraph(
        campaign,
        [models.EntityInput(kind="place", name="Market")],
        [],
        base_revision=before["world_version"],
        allow_orphans=True,
    )
    claim_next_job()
    with pytest.raises(WorkspaceConflict):
        execution_snapshot(job)


def test_campaign_deletion_cleans_workspace_foreign_keys(workspace_world):
    owner, campaign, harbor = workspace_world
    node = WorkspaceNode(id=ids.new_id(), kind="place", name="Ropewalk", parent_id=harbor)
    save_nodes(campaign, [node])
    job = submit(campaign)
    claim_next_job()
    commit_expansion(job, [node], [models.EntityInput(kind="place", name="Ropewalk")])
    assert delete_campaign(owner, campaign)
    with session_scope() as session:
        for table in (models.Workspace, models.WorkspaceRequest, models.WorkspaceReceipt):
            assert session.scalar(select(table).where(table.campaign_id == campaign)) is None


@pytest.mark.parametrize("kind", ["plan_expansion", "expand_world"])
def test_generic_enqueue_cannot_forge_workspace_request(workspace_world, kind):
    from app.store import enqueue_job

    _, campaign, _ = workspace_world
    with pytest.raises(InvalidJobInputError, match="private workspace"):
        enqueue_job(campaign, kind, {"workspace_request": True}, max_llm_calls=999)


def test_large_context_preserves_every_reviewed_target_and_bounds_data(workspace_world):
    import json

    from app.store.workspace import workspace_context

    _, campaign, _ = workspace_world
    targets = [
        models.EntityInput(
            id=ids.new_id(), kind="place", name=f"Target {i}", data={"large": "x" * 20000}
        )
        for i in range(30)
    ]
    with session_scope() as session:
        from app.store.read import latest_revision

        revision = latest_revision(session, campaign).id
    commit_subgraph(campaign, targets, [], base_revision=revision, allow_orphans=True)
    nodes = [WorkspaceNode(id=ids.new_id(), kind="character", parent_id=t.id) for t in targets]
    save_nodes(campaign, nodes)
    job = submit(campaign)
    claim_next_job()
    _, _, live = execution_snapshot(job)
    context = workspace_context(job, live)
    assert {t["id"] for t in context["targets"]} == {t.id for t in targets}
    assert len(context["records"]) == 24
    assert all(len(t["data_excerpt"]) <= 2000 for t in context["records"])
    assert len(json.dumps(context)) < 140000
