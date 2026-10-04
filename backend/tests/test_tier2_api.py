"""Tier-2 wire surface tests (AD-26..AD-29; spec-v3-tier2-routes).

Pins the wire contract of the Tonight Tier-2 routes:
- ``POST .../entities/{id}/session-verb`` — one consequence verb = one
  undoable revision; double-fire is a no-op (204, zero new revisions);
  ownership-404-first; missing/non-object ``update`` and non-string
  ``base_revision`` are 422; malformed/non-object body is a 400; stale
  base is a 409.
- ``POST .../entities/{id}/knowledge-toggle`` — absolute secret<->known
  set, one undoable step; same-value repeat is a no-op; closed field
  set and strict-bool ``known`` are store-backed 422s.
- ``PATCH .../entities/{id}`` — ``knowledge_flips`` rides the SAME
  revision as the edit (AD-29 bundle; take-back inverts both halves);
  a flip-only PATCH is legal; an unchanged re-save emits nothing;
  malformed flips are 422 with zero revisions.
- ``GET .../run-state`` — owner-gated, read-only dumb joins: session
  images + knowledge toggles; empty maps for a fresh world; state
  never leaks into the export projection.

Each test runs on a FRESH scratch DB (the undo-api isolation pattern)
so no cross-module queue/state leaks are possible; these tests enqueue
no jobs anyway.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import ids
from app.store import (
    app_db_url,
    commit_subgraph,
    init_db,
    latest_revision,
    models,
    revision_chain,
    session_scope,
)


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    """An https TestClient over the real app, with a FRESH scratch DB per
    test — the campaigns/undo API test isolation."""
    from app.main import app

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'tier2-api.db'}")
    try:
        with TestClient(app, base_url="https://testserver") as test_client:
            yield test_client
    finally:
        init_db(previous)


@pytest.fixture(autouse=True)
def _reset_limiters() -> Any:
    from app.api.auth import _login_limiter, _register_limiter

    _login_limiter.reset()
    _register_limiter.reset()
    yield
    _login_limiter.reset()
    _register_limiter.reset()


def _register_login(client: Any, email: str = "dm@example.com") -> None:
    client.post("/api/auth/register", json={"email": email, "password": "correct-battery-horse"})
    response = client.post(
        "/api/auth/login", json={"email": email, "password": "correct-battery-horse"}
    )
    assert response.status_code == 200


def _create_campaign(client: Any, title: str = "Aetheria") -> str:
    response = client.post(
        "/api/campaigns",
        json={
            "title": title,
            "description": "A living world",
            "theme": "High Fantasy",
            "custom_lore": "The old gods stir",
        },
    )
    assert response.status_code == 201
    campaign_id = response.json()["id"]
    assert isinstance(campaign_id, str)
    return campaign_id


def _commit_world(campaign_id: str) -> tuple[str, str]:
    """One seed revision: an anchor place + a bare character (text only),
    joined by one edge. Returns (place_id, character_id)."""
    place_id, character_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="place", name="The Anchor", id=place_id),
            models.EntityInput(
                kind="character",
                name="Mira Vane",
                text="A rogue with a ledger.",
                id=character_id,
            ),
        ],
        [
            models.EdgeInput(
                src=character_id, dst=place_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    return place_id, character_id


def _head(campaign_id: str) -> models.Revision:
    with session_scope() as session:
        head = latest_revision(session, campaign_id)
    assert head is not None
    return head


def _chain_len(campaign_id: str) -> int:
    with session_scope() as session:
        return len(list(revision_chain(session, campaign_id)))


def _run_state(client: Any, campaign_id: str) -> dict[str, Any]:
    response = client.get(f"/api/campaigns/{campaign_id}/run-state")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


def _export(client: Any, campaign_id: str) -> dict[str, Any]:
    response = client.get(f"/api/campaigns/{campaign_id}/export")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


# ---------------------------------------------------------------------------
# session-verb
# ---------------------------------------------------------------------------


def test_verb_commits_one_revision_and_reads_back(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    before = _chain_len(campaign_id)
    response = client.post(
        f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
        json={"update": {"defeated": True}},
    )
    assert response.status_code == 204
    assert _chain_len(campaign_id) == before + 1
    state = _run_state(client, campaign_id)
    assert state["session"][character_id] == {"defeated": True}
    assert state["knowledge"] == {}


def test_verb_double_fire_is_a_noop(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    first = client.post(
        f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
        json={"update": {"defeated": True}},
    )
    assert first.status_code == 204
    before = _chain_len(campaign_id)
    second = client.post(
        f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
        json={"update": {"defeated": True}},
    )
    assert second.status_code == 204
    assert _chain_len(campaign_id) == before  # the second fire commits nothing


def test_verb_foreign_campaign_404_before_body_read(client: Any) -> None:
    """Ownership first: an unknown/foreign campaign is the single
    indistinguishable 404 even with a valid body — checked before any
    body is read."""
    _register_login(client)
    response = client.post(
        f"/api/campaigns/{'1' * 26}/entities/{'0' * 26}/session-verb",
        json={"update": {"defeated": True}},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_verb_unknown_entity_404(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _commit_world(campaign_id)
    response = client.post(
        f"/api/campaigns/{campaign_id}/entities/{'0' * 26}/session-verb",
        json={"update": {"defeated": True}},
    )
    assert response.status_code == 404


def test_verb_update_required_object(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    before = _chain_len(campaign_id)
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
            json={},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
            json={"update": 3},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
            json={"update": "defeated"},
        ).status_code
        == 422
    )
    assert _chain_len(campaign_id) == before  # zero revisions written


def test_verb_malformed_body_is_400(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
            content="not json",
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
            content="[1, 2]",
        ).status_code
        == 400
    )


def test_verb_stale_base_is_409(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    base = _head(campaign_id).id
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
            json={"update": {"defeated": True}, "base_revision": base},
        ).status_code
        == 204
    )
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
            json={"update": {"ally_of": "the-captain"}, "base_revision": base},
        ).status_code
        == 409
    )


def test_verb_non_string_base_revision_422(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
            json={"update": {"defeated": True}, "base_revision": 7},
        ).status_code
        == 422
    )


# ---------------------------------------------------------------------------
# knowledge-toggle
# ---------------------------------------------------------------------------


def test_toggle_commits_and_leaves_record_untouched(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    before = _chain_len(campaign_id)
    response = client.post(
        f"/api/campaigns/{campaign_id}/entities/{character_id}/knowledge-toggle",
        json={"field": "secret", "known": True},
    )
    assert response.status_code == 204
    assert _chain_len(campaign_id) == before + 1
    state = _run_state(client, campaign_id)
    assert state["knowledge"][character_id] == {"secret": True}
    # The record never changes — only the marker moves (AD-29).
    exported = _export(client, campaign_id)
    character = next(e for e in exported["entities"] if e["id"] == character_id)
    assert "known" not in character


def test_toggle_double_fire_is_a_noop(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    first = client.post(
        f"/api/campaigns/{campaign_id}/entities/{character_id}/knowledge-toggle",
        json={"field": "secret", "known": True},
    )
    assert first.status_code == 204
    before = _chain_len(campaign_id)
    second = client.post(
        f"/api/campaigns/{campaign_id}/entities/{character_id}/knowledge-toggle",
        json={"field": "secret", "known": True},
    )
    assert second.status_code == 204
    assert _chain_len(campaign_id) == before
    # The reverse flip IS a fresh step.
    third = client.post(
        f"/api/campaigns/{campaign_id}/entities/{character_id}/knowledge-toggle",
        json={"field": "secret", "known": False},
    )
    assert third.status_code == 204
    assert _chain_len(campaign_id) == before + 1
    state = _run_state(client, campaign_id)
    assert state["knowledge"][character_id] == {"secret": False}


def test_toggle_unknown_field_422(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/knowledge-toggle",
            json={"field": "favorite_color", "known": True},
        ).status_code
        == 422
    )


def test_toggle_known_must_be_strict_bool(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    before = _chain_len(campaign_id)
    for bad in (1, 0, "yes", None):
        assert (
            client.post(
                f"/api/campaigns/{campaign_id}/entities/{character_id}/knowledge-toggle",
                json={"field": "secret", "known": bad},
            ).status_code
            == 422
        )
    assert _chain_len(campaign_id) == before


def test_toggle_missing_field_422(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/knowledge-toggle",
            json={"known": True},
        ).status_code
        == 422
    )


def test_toggle_foreign_campaign_404(client: Any) -> None:
    """Ownership first: an unknown/foreign campaign is the single
    indistinguishable 404 even with a valid body."""
    _register_login(client)
    response = client.post(
        f"/api/campaigns/{'1' * 26}/entities/{'0' * 26}/knowledge-toggle",
        json={"field": "secret", "known": True},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# PATCH knowledge_flips bundle
# ---------------------------------------------------------------------------


def test_patch_bundle_edit_and_flip_is_one_revision_with_take_back(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    before = _chain_len(campaign_id)
    response = client.patch(
        f"/api/campaigns/{campaign_id}/entities/{character_id}",
        json={
            "appearance": "scarred twice across the cheek",
            "knowledge_flips": [{"field": "rumor", "known": True}],
        },
    )
    assert response.status_code == 204
    assert _chain_len(campaign_id) == before + 1  # ONE save = ONE revision
    state = _run_state(client, campaign_id)
    assert state["knowledge"][character_id] == {"rumor": True}
    exported = _export(client, campaign_id)
    character = next(e for e in exported["entities"] if e["id"] == character_id)
    assert character["data"]["appearance"] == "scarred twice across the cheek"
    # Take-back inverts BOTH halves in one step (AD-29).
    undo = client.post(f"/api/campaigns/{campaign_id}/undo", json={})
    assert undo.status_code == 204
    assert _run_state(client, campaign_id)["knowledge"] == {}
    exported = _export(client, campaign_id)
    character = next(e for e in exported["entities"] if e["id"] == character_id)
    assert "appearance" not in character["data"]


def test_patch_flip_only_is_a_legal_bundle(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    before = _chain_len(campaign_id)
    response = client.patch(
        f"/api/campaigns/{campaign_id}/entities/{character_id}",
        json={"knowledge_flips": [{"field": "party_hook", "known": False}]},
    )
    assert response.status_code == 204
    assert _chain_len(campaign_id) == before + 1
    state = _run_state(client, campaign_id)
    assert state["knowledge"][character_id] == {"party_hook": False}


def test_patch_resave_unchanged_bundle_is_a_noop(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    body = {
        "appearance": "scarred twice across the cheek",
        "knowledge_flips": [{"field": "secret", "known": True}],
    }
    first = client.patch(f"/api/campaigns/{campaign_id}/entities/{character_id}", json=body)
    assert first.status_code == 204
    before = _chain_len(campaign_id)
    resave = client.patch(f"/api/campaigns/{campaign_id}/entities/{character_id}", json=body)
    assert resave.status_code == 204
    assert _chain_len(campaign_id) == before  # identical content + unchanged flips emit nothing


def test_patch_malformed_flips_422_zero_revisions(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    before = _chain_len(campaign_id)
    for bad in (
        {"knowledge_flips": "secret"},  # not a list
        {"knowledge_flips": [{"known": True}]},  # item missing field
        {"knowledge_flips": [{"field": 3, "known": True}]},  # field non-string
        {"knowledge_flips": [{"field": "secret", "known": 1}]},  # known non-bool
        {"knowledge_flips": ["secret"]},  # item not an object
    ):
        assert (
            client.patch(
                f"/api/campaigns/{campaign_id}/entities/{character_id}", json=bad
            ).status_code
            == 422
        )
    assert _chain_len(campaign_id) == before


def test_patch_flip_is_never_merged_into_record_data(client: Any) -> None:
    """The wire guard: a ``knowledge_flips`` key must NEVER reach the
    record merge (it would become a junk data key on the entity)."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    response = client.patch(
        f"/api/campaigns/{campaign_id}/entities/{character_id}",
        json={"knowledge_flips": [{"field": "secret", "known": True}]},
    )
    assert response.status_code == 204
    exported = _export(client, campaign_id)
    character = next(e for e in exported["entities"] if e["id"] == character_id)
    assert "knowledge_flips" not in character["data"]


# ---------------------------------------------------------------------------
# run-state read
# ---------------------------------------------------------------------------


def test_run_state_fresh_world_is_empty_maps(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _commit_world(campaign_id)
    assert _run_state(client, campaign_id) == {"session": {}, "knowledge": {}}


def test_run_state_foreign_campaign_404(client: Any) -> None:
    """An unknown/foreign campaign is the single indistinguishable 404
    (no oracle)."""
    _register_login(client)
    response = client.get(f"/api/campaigns/{'1' * 26}/run-state")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_run_state_is_read_only(client: Any) -> None:
    """run-state is a read: a GET must never mutate history."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    before = _chain_len(campaign_id)
    _run_state(client, campaign_id)
    assert _chain_len(campaign_id) == before


def test_verbs_and_toggles_never_leak_into_export(client: Any) -> None:
    """AD-11: the exporter reads entity/edge only — Tonight state rows
    never appear in the export projection."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    before = _export(client, campaign_id)
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/session-verb",
            json={"update": {"defeated": True, "hp": -12}},
        ).status_code
        == 204
    )
    assert (
        client.post(
            f"/api/campaigns/{campaign_id}/entities/{character_id}/knowledge-toggle",
            json={"field": "secret", "known": True},
        ).status_code
        == 204
    )
    after = _export(client, campaign_id)
    assert after["entities"] == before["entities"]
    assert after["edges"] == before["edges"]


def test_revisions_details_use_historical_images_and_real_undo(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    path = f"/api/campaigns/{campaign_id}"
    seed_export = _export(client, campaign_id)
    for defeated in (True, False):
        assert (
            client.post(
                f"{path}/entities/{character_id}/session-verb",
                json={"update": {"defeated": defeated, "hp": 8}},
            ).status_code
            == 204
        )
    assert client.post(f"{path}/undo", json={}).status_code == 204
    for known in (True, False):
        assert (
            client.post(
                f"{path}/entities/{character_id}/knowledge-toggle",
                json={"field": "party_hook", "known": known},
            ).status_code
            == 204
        )
    assert client.post(f"{path}/undo", json={}).status_code == 204
    assert client.post(f"{path}/undo", json={}).status_code == 204
    # Taking back a newly added flag explicitly describes its removal.
    assert (
        client.post(
            f"{path}/entities/{character_id}/session-verb",
            json={"update": {"thread": True}},
        ).status_code
        == 204
    )
    assert client.post(f"{path}/undo", json={}).status_code == 204
    chain_length = _chain_len(campaign_id)
    state = _run_state(client, campaign_id)
    response = client.get(f"{path}/revisions")
    assert response.status_code == 200
    revisions = response.json()["revisions"]
    details = [r["events"][0]["details"] for r in revisions[:-1]]
    assert details == [
        ["Thread: removed"],
        ["Resolved thread"],
        ["Party hook hidden from party"],
        ["Revealed party hook to party"],
        ["Party hook hidden from party"],
        ["Revealed party hook to party"],
        ["Marked defeated"],
        ["Cleared defeated"],
        ["Marked defeated", "HP: 8"],
    ]
    assert all(r["events"][0]["action"] == "edited" for r in revisions[:-1])
    assert all(e["details"] == [] for e in revisions[-1]["events"])
    assert _chain_len(campaign_id) == chain_length
    assert _run_state(client, campaign_id) == state
    assert _export(client, campaign_id)["entities"] == seed_export["entities"]
    _register_login(client, "other@example.com")
    foreign = client.get(f"{path}/revisions")
    unknown = client.get(f"/api/campaigns/{'0' * 26}/revisions")
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()


def test_revisions_surgical_take_back_projects_actual_changed_fields(client: Any) -> None:
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    path = f"/api/campaigns/{campaign_id}"
    toggle = f"{path}/entities/{character_id}/knowledge-toggle"
    assert client.post(toggle, json={"field": "secret", "known": True}).status_code == 204
    reveal_revision = _head(campaign_id).id
    assert client.post(toggle, json={"field": "secret", "known": False}).status_code == 204
    # Undoing the older reveal preserves the later hidden value, with no detail.
    assert client.post(f"{path}/undo", json={"revision_id": reveal_revision}).status_code == 204
    result = client.get(f"{path}/revisions").json()["revisions"][0]["events"][0]
    assert result["action"] == "edited"
    assert result["details"] == []
    assert client.post(toggle, json={"field": "rumor", "known": False}).status_code == 204
    assert client.post(f"{path}/undo", json={}).status_code == 204
    result = client.get(f"{path}/revisions").json()["revisions"][0]["events"][0]
    assert result["details"] == ["Removed rumor knowledge marker"]

    verb = f"{path}/entities/{character_id}/session-verb"
    assert client.post(verb, json={"update": {"hp": 40}}).status_code == 204
    assert client.post(verb, json={"update": {"hp": 28}}).status_code == 204
    damaged_revision = _head(campaign_id).id
    assert client.post(verb, json={"update": {"hp": 35, "defeated": True}}).status_code == 204
    assert client.post(f"{path}/undo", json={"revision_id": damaged_revision}).status_code == 204
    result = client.get(f"{path}/revisions").json()["revisions"][0]["events"][0]
    assert result["details"] == ["HP: 47"]


def test_entity_notes_lifecycle_history_and_surgical_undo(client: Any) -> None:
    _register_login(client)
    campaign = _create_campaign(client)
    place, entity = _commit_world(campaign)
    path = f"/api/campaigns/{campaign}"
    verb = f"{path}/entities/{entity}/session-verb"
    # Empty notes do not fabricate state on an untouched entity.
    count = _chain_len(campaign)
    assert (
        client.post(verb, json={"update": {"notes": ""}, "expected_notes": ""}).status_code == 204
    )
    assert _chain_len(campaign) == count
    assert _run_state(client, campaign)["session"] == {}
    assert client.post(verb, json={"update": {"defeated": True}}).status_code == 204
    assert (
        client.post(
            f"{path}/entities/{entity}/knowledge-toggle", json={"field": "secret", "known": True}
        ).status_code
        == 204
    )
    note = "Defeated at the forge.\n<Still owes the party a sword>"
    assert (
        client.post(verb, json={"update": {"notes": note}, "expected_notes": ""}).status_code == 204
    )
    added = _head(campaign).id
    count = _chain_len(campaign)
    assert (
        client.post(verb, json={"update": {"notes": note}, "expected_notes": note}).status_code
        == 204
    )
    assert _chain_len(campaign) == count
    # Fresh read projections use SQL, not client memory, and keep unrelated flags.
    assert _run_state(client, campaign)["session"][entity] == {"defeated": True, "notes": note}
    assert _run_state(client, campaign)["knowledge"][entity] == {"secret": True}
    independent = TestClient(client.app, base_url="https://testserver")
    assert (
        independent.post(
            "/api/auth/login", json={"email": "dm@example.com", "password": "correct-battery-horse"}
        ).status_code
        == 200
    )
    assert _run_state(independent, campaign)["session"][entity]["notes"] == note
    independent.close()
    assert client.post(verb, json={"update": {"item": True}}).status_code == 204
    assert (
        client.post(verb, json={"update": {"notes": "newer"}, "expected_notes": note}).status_code
        == 204
    )
    assert client.post(f"{path}/undo", json={"revision_id": added}).status_code == 204
    assert _run_state(client, campaign)["session"][entity]["notes"] == "newer"
    assert (
        client.post(verb, json={"update": {"notes": ""}, "expected_notes": "newer"}).status_code
        == 204
    )
    assert client.post(f"{path}/undo", json={}).status_code == 204
    assert _run_state(client, campaign)["session"][entity]["notes"] == "newer"
    feed = client.get(f"{path}/revisions?limit=100").json()["revisions"]
    details = [
        detail for revision in feed for event in revision["events"] for detail in event["details"]
    ]
    assert "Added notes" in details and "Updated notes" in details and "Cleared notes" in details
    assert all(note not in detail and "newer" not in detail for detail in details)
    assert all(
        event["action"] == "edited" for revision in feed[:-1] for event in revision["events"]
    )
    # A note-only row is supported, and undo removes precisely its creation.
    place_verb = f"{path}/entities/{place}/session-verb"
    assert (
        client.post(
            place_verb, json={"update": {"notes": "only notes"}, "expected_notes": ""}
        ).status_code
        == 204
    )
    assert _run_state(client, campaign)["session"][place] == {"notes": "only notes"}
    assert client.post(f"{path}/undo", json={}).status_code == 204
    assert place not in _run_state(client, campaign)["session"]
    assert client.get(f"{path}/revisions").json()["revisions"][0]["events"][0]["details"] == [
        "Removed notes"
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"update": {"notes": 2}, "expected_notes": ""},
        {"update": {"notes": None}, "expected_notes": ""},
        {"update": {"notes": "x" * 20001}, "expected_notes": ""},
        {"update": {"notes": "text"}},
        {"update": {"notes": "text"}, "expected_notes": []},
        {"update": {"notes": "text"}, "expected_notes": None},
    ],
)
def test_notes_invalid_payload_has_no_revision(client: Any, payload: Any) -> None:
    _register_login(client)
    campaign = _create_campaign(client)
    _, entity = _commit_world(campaign)
    count = _chain_len(campaign)
    assert (
        client.post(
            f"/api/campaigns/{campaign}/entities/{entity}/session-verb", json=payload
        ).status_code
        == 422
    )
    assert _chain_len(campaign) == count
    assert _run_state(client, campaign)["session"] == {}


def test_notes_stale_private_and_missing_targets(client: Any) -> None:
    _register_login(client)
    campaign = _create_campaign(client)
    _, entity = _commit_world(campaign)
    path = f"/api/campaigns/{campaign}/entities/{entity}/session-verb"
    assert (
        client.post(path, json={"update": {"notes": "current"}, "expected_notes": ""}).status_code
        == 204
    )
    count = _chain_len(campaign)
    assert (
        client.post(path, json={"update": {"notes": "stale"}, "expected_notes": ""}).status_code
        == 409
    )
    assert _chain_len(campaign) == count
    assert (
        client.post(
            f"/api/campaigns/{campaign}/entities/{ids.new_id()}/session-verb",
            json={"update": {"notes": "ghost"}, "expected_notes": ""},
        ).status_code
        == 404
    )
    other = _create_campaign(client, "Other")
    assert (
        client.post(
            f"/api/campaigns/{other}/entities/{entity}/session-verb",
            json={"update": {"notes": "cross"}, "expected_notes": ""},
        ).status_code
        == 404
    )
    assert (
        client.request(
            "DELETE",
            f"/api/campaigns/{campaign}/entities/{entity}",
            json={"cascade": True, "confirm": True},
        ).status_code
        == 204
    )
    count = _chain_len(campaign)
    assert (
        client.post(
            path, json={"update": {"notes": "ghost"}, "expected_notes": "current"}
        ).status_code
        == 404
    )
    assert _chain_len(campaign) == count
    client.post("/api/auth/logout")
    _register_login(client, "other@example.com")
    assert client.post(path, content="invalid-json").status_code == 404
    assert client.post(path, json={"update": {"notes": []}, "expected_notes": 7}).status_code == 404
    assert client.get(f"/api/campaigns/{campaign}/run-state").status_code == 404
    assert _chain_len(campaign) == count


@pytest.mark.parametrize("character", ["a", "😀"])
def test_notes_accept_exactly_20000_unicode_codepoints(client: Any, character: str) -> None:
    _register_login(client)
    campaign = _create_campaign(client)
    _, entity = _commit_world(campaign)
    path = f"/api/campaigns/{campaign}/entities/{entity}/session-verb"
    notes = character * 20_000
    assert (
        client.post(path, json={"update": {"notes": notes}, "expected_notes": ""}).status_code
        == 204
    )
    assert _run_state(client, campaign)["session"][entity]["notes"] == notes
    count = _chain_len(campaign)
    assert (
        client.post(
            path, json={"update": {"notes": notes + character}, "expected_notes": notes}
        ).status_code
        == 422
    )
    assert _chain_len(campaign) == count
