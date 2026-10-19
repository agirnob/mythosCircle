"""Ownership, optimistic conflicts and strict workspace wire contracts."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import ids
from app.store import app_db_url, commit_subgraph, create_campaign, init_db, models
from app.store.campaigns import ensure_generic_campaign


@pytest.fixture()
def workspace_api(tmp_path: Path, client: TestClient) -> Iterator[tuple[TestClient, str, str, str]]:
    from app.api.auth import _login_limiter, _register_limiter

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'workspace-api.db'}")
    _login_limiter.reset()
    _register_limiter.reset()
    body = client.post(
        "/api/auth/register",
        json={"email": f"workspace-api-{ids.new_id()}@example.com", "password": "password123"},
    )
    assert body.status_code == 201
    owner = body.json()["id"]
    campaign = create_campaign(
        owner, title="Arlea", description="", theme="High Fantasy", custom_lore=""
    )
    harbor = ids.new_id()
    commit_subgraph(
        campaign.id,
        [models.EntityInput(id=harbor, kind="place", name="Harbor")],
        [],
        base_revision=None,
        allow_orphans=True,
    )
    try:
        yield client, owner, campaign.id, harbor
    finally:
        _login_limiter.reset()
        _register_limiter.reset()
        init_db(previous)


def test_workspace_requires_authenticated_owner(workspace_api):
    client, _, campaign, _ = workspace_api
    url = f"/api/campaigns/{campaign}/workspace"
    own = client.get(url)
    assert own.status_code == 200
    assert own.json()["draft"]["nodes"] == []
    other = client.post(
        "/api/auth/register",
        json={"email": f"other-{ids.new_id()}@example.com", "password": "password123"},
    )
    assert other.status_code == 201
    assert client.get(url).status_code == 404
    assert client.get(f"/api/campaigns/{ids.new_id()}/workspace").status_code == 404
    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_workspace_save_roundtrip_and_409_preserves_draft(workspace_api):
    client, _, campaign, harbor = workspace_api
    url = f"/api/campaigns/{campaign}/workspace"
    snapshot = client.get(url).json()
    node = {
        "id": ids.new_id(),
        "kind": "place",
        "name": "",
        "parent_id": harbor,
        "guidance": "  Exact user text.\nKeep it.  ",
    }
    body = {
        "expected_draft_version": snapshot["draft_version"],
        "expected_world_version": snapshot["world_version"],
        "draft": {"nodes": [node]},
    }
    response = client.put(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["draft"]["nodes"][0]["guidance"] == node["guidance"]
    assert client.put(url, json=body).status_code == 409
    assert client.get(url).json() == response.json()


@pytest.mark.parametrize("mutation", ["unknown-key", "wrong-kind", "wrong-version-type"])
def test_workspace_rejects_noncanonical_wire_fields(workspace_api, mutation):
    client, _, campaign, _ = workspace_api
    url = f"/api/campaigns/{campaign}/workspace"
    snapshot = client.get(url).json()
    body = {
        "expected_draft_version": snapshot["draft_version"],
        "expected_world_version": snapshot["world_version"],
        "draft": {"nodes": []},
    }
    if mutation == "unknown-key":
        body["unreviewed_world_write"] = True
    elif mutation == "wrong-kind":
        body["draft"]["nodes"] = [{"id": ids.new_id(), "kind": "monster"}]
    else:
        body["expected_draft_version"] = "0"
    response = client.put(url, json=body)
    assert response.status_code == 422
    assert set(response.json()) <= {"code", "message", "details"}
    assert client.get(url).json() == snapshot


def test_generic_library_cannot_use_world_workspace(workspace_api):
    client, owner, _, _ = workspace_api
    generic = ensure_generic_campaign(owner, "High Fantasy")
    assert client.get(f"/api/campaigns/{generic.id}/workspace").status_code == 422


def test_plan_submission_is_scoped_idempotent_and_nonmutating(workspace_api):
    client, _, campaign, harbor = workspace_api
    url = f"/api/campaigns/{campaign}/workspace"
    snapshot = client.get(url).json()
    body = {
        "request_id": ids.new_id(),
        "expected_draft_version": snapshot["draft_version"],
        "expected_world_version": snapshot["world_version"],
        "scope_id": harbor,
        "guidance": "A quay and two ferrymen.",
    }
    first = client.post(url + "/plan", json=body)
    assert first.status_code in (200, 201), first.text
    assert first.json()["kind"] == "plan_expansion"
    assert first.json()["payload"]["scope_id"] == harbor
    repeat = client.post(url + "/plan", json=body)
    assert repeat.json()["id"] == first.json()["id"]
    assert client.post(url + "/plan", json={**body, "guidance": "Changed"}).status_code == 409
    assert client.get(url).json()["draft"] == snapshot["draft"]
    export = client.get(f"/api/campaigns/{campaign}/export").json()
    assert [e["name"] for e in export["entities"]] == ["Harbor"]


@pytest.mark.parametrize("action", ["save", "plan", "generate"])
@pytest.mark.parametrize("caller", ["other", "anonymous"])
def test_workspace_mutations_require_owner_and_write_nothing(workspace_api, action, caller):
    from sqlalchemy import select

    from app.store.db import session_scope
    from app.store.workspace import get_workspace

    client, _, campaign, harbor = workspace_api
    url = f"/api/campaigns/{campaign}/workspace"
    snapshot = get_workspace(campaign)
    node = {"id": ids.new_id(), "kind": "place", "name": "Market", "parent_id": harbor}
    version = {
        "expected_draft_version": snapshot["draft_version"],
        "expected_world_version": snapshot["world_version"],
    }
    if caller == "other":
        assert (
            client.post(
                "/api/auth/register",
                json={"email": f"other-{ids.new_id()}@example.com", "password": "password123"},
            ).status_code
            == 201
        )
    else:
        client.cookies.clear()
    response = (
        client.put(url, json={**version, "draft": {"nodes": [node]}})
        if action == "save"
        else client.post(
            url + "/" + action, json={**version, "request_id": ids.new_id(), "scope_id": harbor}
        )
    )
    assert response.status_code == (404 if caller == "other" else 401)
    assert get_workspace(campaign) == snapshot
    with session_scope() as session:
        assert not list(
            session.scalars(select(models.Job).where(models.Job.campaign_id == campaign))
        )
