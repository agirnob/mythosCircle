"""Real journal wire contracts: private scope, retry identity and linked action integrity."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_tier2_api import (
    _chain_len,
    _create_campaign,
    _register_login,
    _reset_limiters,  # noqa: F401 - autouse fixture
)

from app.core import ids
from app.store import app_db_url, init_db, models
from app.store.commit import commit_subgraph
from app.store.db import session_scope
from app.store.read import latest_revision


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    from app.main import app

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'journal-api.db'}")
    try:
        with TestClient(app, base_url="https://testserver") as test_client:
            yield test_client
    finally:
        init_db(previous)


def _commit_world(campaign_id: str) -> tuple[str, str]:
    place_id, entity_id = ids.new_id(), ids.new_id()
    with session_scope() as session:
        head = latest_revision(session, campaign_id)
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(id=place_id, kind="place", name="Anchor"),
            models.EntityInput(id=entity_id, kind="character", name="Mira Vane"),
        ],
        [models.EdgeInput(src=entity_id, dst=place_id, type="located_in", reason="seeded")],
        base_revision=head.id if head else None,
    )
    return place_id, entity_id


def _scope(client: Any) -> tuple[str, str]:
    _register_login(client)
    campaign = _create_campaign(client)
    response = client.post(
        f"/api/campaigns/{campaign}/play-sessions",
        json={
            "title": "Trouble in Arlea",
            "play_date": "2026-10-04",
            "request_key": ids.new_id(),
        },
    )
    assert response.status_code == 201, response.text
    return campaign, response.json()["id"]


def _entry(client: Any, campaign: str, session: str, **fields: Any) -> dict[str, Any]:
    response = client.post(
        f"/api/campaigns/{campaign}/journal-entries",
        json={
            "session_id": session,
            "headline": "The party arrived",
            "request_key": ids.new_id(),
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_empty_historical_source_rejects_without_creating_story(client: Any) -> None:
    campaign, play_session = _scope(client)
    base = f"/api/campaigns/{campaign}/journal-entries"
    before = _chain_len(campaign)
    response = client.post(
        base,
        json={
            "session_id": play_session,
            "headline": "Invalid promotion",
            "source_event_id": "",
            "request_key": ids.new_id(),
        },
    )
    assert response.status_code == 404, response.text
    assert client.get(base).json()["entries"] == []
    assert _chain_len(campaign) == before


def test_session_activation_version_and_idempotent_creation(client: Any) -> None:
    campaign, session = _scope(client)
    base = f"/api/campaigns/{campaign}/play-sessions"
    payload = {"title": "The road", "play_date": "2026-09-27", "request_key": ids.new_id()}
    one = client.post(base, json=payload)
    before = _chain_len(campaign)
    assert client.post(base, json=payload).json() == one.json()
    assert _chain_len(campaign) == before
    assert client.post(base, json={**payload, "title": "changed"}).status_code == 409
    assert client.post(f"{base}/{session}/activate").json() == {"active_session_id": session}
    listed = client.get(base).json()
    assert listed["active_session_id"] == session
    assert len(listed["sessions"]) == 2
    edited = client.patch(f"{base}/{session}", json={"version": 1, "title": "A new title"})
    assert edited.status_code == 200
    assert edited.json()["version"] == 2
    assert (
        client.patch(f"{base}/{session}", json={"version": 1, "title": "stale"}).status_code == 409
    )


def test_entry_retry_keeps_original_identity_and_context_edits_keep_position(client: Any) -> None:
    campaign, session = _scope(client)
    key = ids.new_id()
    entry = _entry(client, campaign, session, request_key=key)
    base = f"/api/campaigns/{campaign}/journal-entries"
    revision_count = _chain_len(campaign)
    retry = _entry(client, campaign, session, request_key=key)
    assert retry == entry
    assert _chain_len(campaign) == revision_count
    edited = client.patch(
        f"{base}/{entry['id']}", json={"version": 1, "context": "The guard saw them"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["position"] == entry["position"]
    assert edited.json()["session_id"] == session
    assert _entry(client, campaign, session, request_key=key) == entry
    assert (
        client.patch(f"{base}/{entry['id']}", json={"version": 1, "context": "stale"}).status_code
        == 409
    )
    assert len(client.get(base).json()["entries"]) == 1
    changed = client.post(
        base, json={"session_id": session, "headline": "changed", "request_key": key}
    )
    assert changed.status_code == 409
    summaries = client.get(f"/api/campaigns/{campaign}/revisions").json()["revisions"]
    journal = next(
        event
        for revision in summaries
        for event in revision["events"]
        if event["entry_id"] == entry["id"]
    )
    assert journal["target_names"] == ["The party arrived"]
    assert "Context: The guard saw them" in journal["details"]


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/play-sessions", "{broken"),
        ("post", "/journal-entries", "{broken"),
        ("patch", "/play-sessions/foreign", "{broken"),
        ("delete", "/play-sessions/foreign?version=bad", None),
        ("get", "/journal-entries?limit=bad&cursor=invalid", None),
        ("patch", "/journal-entries/foreign", "{broken"),
        ("post", "/journal-entries/foreign/correct", "{broken"),
        ("delete", "/journal-entries/foreign?version=bad", None),
    ],
)
def test_foreign_campaign_is_404_before_all_validation(
    client: Any,
    method: str,
    path: str,
    body: str | None,
) -> None:
    campaign, _ = _scope(client)
    before = _chain_len(campaign)
    client.post("/api/auth/logout")
    _register_login(client, "other@example.com")
    response = client.request(method, f"/api/campaigns/{campaign}{path}", content=body)
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"
    assert _chain_len(campaign) == before


def test_foreign_resources_are_404_before_version_or_body_validation(client: Any) -> None:
    campaign, session = _scope(client)
    entry = _entry(client, campaign, session)
    another = _create_campaign(client, "Another")
    base = f"/api/campaigns/{another}"
    assert client.patch(f"{base}/play-sessions/{session}", content="{broken").status_code == 404
    assert client.delete(f"{base}/journal-entries/{entry['id']}?version=bad").status_code == 404
    assert (
        client.post(f"{base}/journal-entries/{entry['id']}/correct", content="{broken").status_code
        == 404
    )


def test_chronology_pagination_filter_and_cross_scope_cursor(client: Any) -> None:
    campaign, later = _scope(client)
    early = client.post(
        f"/api/campaigns/{campaign}/play-sessions",
        json={
            "title": "Earlier",
            "play_date": "2026-09-01",
        },
    ).json()["id"]
    _, entity = _commit_world(campaign)
    last = _entry(client, campaign, later, headline="Later")
    first = _entry(
        client,
        campaign,
        early,
        headline="First",
        references=[{"entity_id": entity, "label": "Mira Vane"}],
    )
    second = _entry(client, campaign, early, headline="Second")
    base = f"/api/campaigns/{campaign}/journal-entries"
    page = client.get(f"{base}?limit=1").json()
    assert page["entries"][0]["id"] == first["id"]
    rest = client.get(base, params={"cursor": page["next_cursor"]}).json()
    assert [entry["id"] for entry in rest["entries"]] == [second["id"], last["id"]]
    filtered = client.get(base, params={"entity_id": entity}).json()
    assert [entry["id"] for entry in filtered["entries"]] == [first["id"]]
    assert (
        client.get(base, params={"cursor": page["next_cursor"], "session_id": later}).status_code
        == 422
    )
    moved = client.patch(f"{base}/{second['id']}", json={"version": 1, "position": 1})
    assert moved.status_code == 200, moved.text
    ordered = client.get(base, params={"session_id": early}).json()["entries"]
    assert [entry["id"] for entry in ordered] == [second["id"], first["id"]]


def test_unicode_mentions_are_validated_and_survive_rename_deletion(client: Any) -> None:
    campaign, session = _scope(client)
    _, entity = _commit_world(campaign)
    text = "🌙 @Mira Vane arrived"
    reference = {
        "entity_id": entity,
        "label": "Mira Vane",
        "field": "context",
        "start": 2,
        "end": 12,
        "token": "@Mira Vane",
    }
    # End counts Unicode codepoints; the selected mention ends at index 12.
    reference["end"] = 12
    entry = _entry(client, campaign, session, context=text, references=[reference])
    base = f"/api/campaigns/{campaign}"
    assert client.patch(f"{base}/entities/{entity}", json={"name": "Renamed"}).status_code == 204
    assert (
        client.request(
            "DELETE", f"{base}/entities/{entity}", json={"confirm": True, "cascade": True}
        ).status_code
        == 204
    )
    response = client.patch(
        f"{base}/journal-entries/{entry['id']}",
        json={
            "version": 1,
            "context": text + ".",
            "references": [reference],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["references"][0]["label"] == "Mira Vane"
    before = _chain_len(campaign)
    invalid = client.post(
        f"{base}/journal-entries",
        json={
            "session_id": session,
            "headline": "Invalid reference",
            "request_key": ids.new_id(),
            "references": [{"entity_id": ids.new_id(), "label": "Nobody"}],
        },
    )
    assert invalid.status_code == 422
    assert _chain_len(campaign) == before


def test_paired_action_edit_correction_preserves_later_notes_and_no_replay(client: Any) -> None:
    campaign, session = _scope(client)
    _, entity = _commit_world(campaign)
    base = f"/api/campaigns/{campaign}"
    action = {
        "update": {"defeated": True},
        "session_id": session,
        "headline": "Mira was defeated",
        "context": "A duel",
        "request_key": ids.new_id(),
    }
    before = _chain_len(campaign)
    response = client.post(f"{base}/entities/{entity}/session-verb", json=action)
    assert response.status_code == 204, response.text
    assert _chain_len(campaign) == before + 1
    entry = client.get(f"{base}/journal-entries").json()["entries"][0]
    assert entry["action_entity_id"] == entity
    assert entry["source_event_id"]
    source = next(
        event
        for revision in client.get(f"{base}/revisions").json()["revisions"]
        for event in revision["events"]
        if event["event_id"] == entry["source_event_id"]
    )
    assert source["entry_id"] == entry["id"]
    assert client.post(f"{base}/entities/{entity}/session-verb", json=action).status_code == 204
    assert _chain_len(campaign) == before + 1
    edited = client.patch(
        f"{base}/journal-entries/{entry['id']}", json={"version": 1, "context": "Revised context"}
    )
    assert edited.status_code == 200
    assert (
        client.post(
            f"{base}/entities/{entity}/session-verb",
            json={
                "update": {"notes": "Keep these notes"},
                "expected_notes": "",
            },
        ).status_code
        == 204
    )
    corrected = client.post(f"{base}/journal-entries/{entry['id']}/correct", json={"version": 2})
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["corrected"] is True
    assert corrected.json()["context"] == "Revised context"
    state = client.get(f"{base}/run-state").json()["session"][entity]
    assert not state.get("defeated")
    assert state["notes"] == "Keep these notes"


def test_promoting_history_never_replays_flags_and_story_delete_leaves_state(client: Any) -> None:
    campaign, session = _scope(client)
    _, entity = _commit_world(campaign)
    base = f"/api/campaigns/{campaign}"
    assert (
        client.post(
            f"{base}/entities/{entity}/session-verb", json={"update": {"defeated": True}}
        ).status_code
        == 204
    )
    summary = client.get(f"{base}/revisions").json()["revisions"][0]["events"][0]
    assert summary["source_event_id"] == summary["event_id"]
    entry = _entry(client, campaign, session, source_event_id=summary["source_event_id"])
    assert entry["action_revision_id"] is None
    assert (
        client.post(
            f"{base}/journal-entries/{entry['id']}/correct", json={"version": 1}
        ).status_code
        == 409
    )
    assert client.delete(f"{base}/journal-entries/{entry['id']}?version=1").status_code == 204
    assert client.get(f"{base}/run-state").json()["session"][entity]["defeated"] is True
    assert client.get(f"{base}/journal-entries").json()["entries"] == []
    promoted = next(
        event
        for revision in client.get(f"{base}/revisions").json()["revisions"]
        for event in revision["events"]
        if event["event_id"] == summary["event_id"]
    )
    assert promoted["entry_id"] == entry["id"]


def test_audit_cursor_is_stable_bounded_and_private(client: Any) -> None:
    campaign, session = _scope(client)
    _entry(client, campaign, session)
    _entry(client, campaign, session, headline="Second")
    path = f"/api/campaigns/{campaign}/revisions"
    first = client.get(path, params={"limit": 1}).json()
    assert first["next_cursor"]
    _entry(client, campaign, session, headline="Added during pagination")
    rest = client.get(path, params={"limit": 100, "cursor": first["next_cursor"]})
    assert rest.status_code == 200, rest.text
    assert first["revisions"][0]["revision_id"] not in [
        row["revision_id"] for row in rest.json()["revisions"]
    ]
    assert len(rest.json()["revisions"]) == 2
    foreign = _create_campaign(client)
    assert (
        client.get(
            f"/api/campaigns/{foreign}/revisions", params={"cursor": first["next_cursor"]}
        ).status_code
        == 422
    )
    assert (
        client.get(f"/api/campaigns/{ids.new_id()}/revisions?limit=bad&cursor=bad").status_code
        == 404
    )


@pytest.mark.parametrize(
    "operation,field",
    [
        ("session_create", "title"),
        ("session_create", "request_key"),
        ("session_update", "title"),
        ("entry_create", "headline"),
        ("entry_create", "context"),
        ("entry_create", "request_key"),
        ("entry_update", "headline"),
        ("entry_update", "context"),
        ("action", "headline"),
        ("action", "context"),
        ("action", "request_key"),
    ],
)
def test_lone_surrogate_json_is_400_and_leaves_no_partial_writes(
    client: Any,
    operation: str,
    field: str,
) -> None:
    campaign, session = _scope(client)
    _, entity_id = _commit_world(campaign)
    entry = _entry(client, campaign, session)
    base = f"/api/campaigns/{campaign}"
    bodies = {
        "session_create": {
            "title": "Another session",
            "play_date": "2026-10-05",
            "request_key": ids.new_id(),
        },
        "session_update": {"version": 1, "title": "Updated title"},
        "entry_create": {
            "session_id": session,
            "headline": "Another entry",
            "context": "More context",
            "request_key": ids.new_id(),
        },
        "entry_update": {
            "version": 1,
            "headline": "Updated headline",
            "context": "Updated context",
        },
        "action": {
            "session_id": session,
            "headline": "Mira was defeated",
            "context": "A duel",
            "request_key": ids.new_id(),
            "update": {"defeated": True},
        },
    }
    paths = {
        "session_create": ("POST", f"{base}/play-sessions"),
        "session_update": ("PATCH", f"{base}/play-sessions/{session}"),
        "entry_create": ("POST", f"{base}/journal-entries"),
        "entry_update": ("PATCH", f"{base}/journal-entries/{entry['id']}"),
        "action": ("POST", f"{base}/entities/{entity_id}/session-verb"),
    }
    body = bodies[operation]
    body[field] = "\ud800"
    before = _chain_len(campaign)
    sessions_before = client.get(f"{base}/play-sessions").json()
    entries_before = client.get(f"{base}/journal-entries").json()
    state_before = client.get(f"{base}/run-state").json()
    method, path = paths[operation]
    response = client.request(
        method,
        path,
        content=json.dumps(body, ensure_ascii=True),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "bad_request"
    assert _chain_len(campaign) == before
    assert client.get(f"{base}/play-sessions").json() == sessions_before
    assert client.get(f"{base}/journal-entries").json() == entries_before
    assert client.get(f"{base}/run-state").json() == state_before


def test_malformed_unicode_does_not_mask_privacy_or_ordinary_422(client: Any) -> None:
    campaign, session = _scope(client)
    base = f"/api/campaigns/{campaign}"
    assert (
        client.post(
            f"{base}/journal-entries",
            json={"session_id": session, "headline": "", "request_key": ids.new_id()},
        ).status_code
        == 422
    )
    # Even a length-invalid surrogate string is rejected before validation can echo it.
    malformed = json.dumps({"title": "\ud800" * 301, "play_date": "2026-10-05"})
    assert client.post(f"{base}/play-sessions", content=malformed).status_code == 400
    client.post("/api/auth/logout")
    _register_login(client, "other@example.com")
    assert client.post(f"{base}/play-sessions", content=malformed).status_code == 404
