"""Entity delete API tests: the campaigns DELETE confirm-body pattern
mirrored for entities (FR4, AD-5; spec-2.5). Uses the https TestClient so
the Secure session cookie round-trips (1.5)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import ids
from app.store import commit_subgraph, models, session_scope, world_entities


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    """An https TestClient over a per-test app, with a FRESH scratch DB
    per test (same isolation contract as the campaigns API tests). The
    app is built per test — create_app() runs inside the fixture, not at
    collection — so the store initializes within the test session under
    the conftest env pins (epic-1 retro item 7)."""
    from app.main import create_app
    from app.store import app_db_url, init_db

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'entity-api.db'}")
    try:
        with TestClient(create_app(), base_url="https://testserver") as test_client:
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


def _create_campaign(client: Any) -> Any:
    return client.post(
        "/api/campaigns",
        json={
            "title": "Aetheria",
            "description": "A living world",
            "theme": "High Fantasy",
            "custom_lore": "",
        },
    ).json()


def _seed_world(campaign_id: str) -> tuple[str, str]:
    """The Gilded Bar + Mira Vane with two live edges (store commit path)."""
    bar_id, mira_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            models.EntityInput(kind="character", name="Mira Vane", id=mira_id),
        ],
        [
            models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1),
            models.EdgeInput(src=mira_id, dst=bar_id, type="debt", counter=3),
        ],
        base_revision=None,
    )
    return bar_id, mira_id


def test_delete_requires_auth(client: Any) -> None:
    """No session — 401 before any ownership or confirm check."""
    response = client.delete(f"/api/campaigns/{'1' * 26}/entities/{'2' * 26}")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_delete_foreign_campaign_404(client: Any) -> None:
    """Ownership first: another DM's campaign (or a foreign entity inside
    an owned campaign) is a single indistinguishable 404, even with
    confirm+cascade."""
    _register_login(client, "dm@example.com")
    mine = _create_campaign(client)
    _seed_world(mine["id"])
    _register_login(client, "other@example.com")
    # Another DM's campaign is a 404 even with the full confirm body.
    response = client.request(
        "DELETE",
        f"/api/campaigns/{mine['id']}/entities/{'1' * 26}",
        json={"confirm": True, "cascade": True},
    )
    assert response.status_code == 404
    # The other DM's own campaign works; its entity is theirs to delete.
    theirs = _create_campaign(client)
    foreign_bar, _foreign_mira = _seed_world(theirs["id"])
    assert (
        client.request(
            "DELETE",
            f"/api/campaigns/{theirs['id']}/entities/{foreign_bar}",
            json={"confirm": True, "cascade": True},
        ).status_code
        == 204
    )
    # A foreign entity ULID inside an owned campaign is invisible (404).
    _register_login(client, "dm@example.com")
    response = client.request(
        "DELETE",
        f"/api/campaigns/{mine['id']}/entities/{foreign_bar}",
        json={"confirm": True, "cascade": True},
    )
    assert response.status_code == 404


def test_delete_live_edges_409_lists_affected(client: Any) -> None:
    """DELETE_LIVE_NO_CONFIRM: no body, live edges — a 409 whose envelope
    ``details`` list the affected neighbor entities; nothing changes."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    response = client.delete(f"/api/campaigns/{mine['id']}/entities/{mira_id}")
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "conflict"
    assert body["details"]["affected_entities"] == [{"id": bar_id, "name": "The Gilded Bar"}]
    assert body["details"]["entity_id"] == mira_id
    # The false-confirm variant fails the same way.
    response = client.request(
        "DELETE", f"/api/campaigns/{mine['id']}/entities/{mira_id}", json={"confirm": False}
    )
    assert response.status_code == 409


def test_delete_cascade_without_confirm_400(client: Any) -> None:
    """A bare ``{"cascade": true}`` — the destructive option without the
    explicit confirmation — is a 400 and nothing is deleted."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    response = client.request(
        "DELETE", f"/api/campaigns/{mine['id']}/entities/{mira_id}", json={"cascade": True}
    )
    assert response.status_code == 400
    assert "confirm" in response.json()["message"].lower()


def test_delete_cascade_confirmed_204(client: Any) -> None:
    """DELETE_CASCADE_CONFIRMED: ``{'confirm': True, 'cascade': True}``
    removes the entity plus its touching edges in one revision; the
    entity is gone afterwards (a repeat delete is a 404)."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    response = client.request(
        "DELETE",
        f"/api/campaigns/{mine['id']}/entities/{mira_id}",
        json={"confirm": True, "cascade": True},
    )
    assert response.status_code == 204
    from app.store import entity_live_edges, session_scope

    with session_scope() as session:
        assert list(entity_live_edges(session, mine["id"], mira_id)) == []
        from app.store import world_entities

        assert bar_id in {e.id for e in world_entities(session, mine["id"])}  # neighbor survives
    response = client.request(
        "DELETE", f"/api/campaigns/{mine['id']}/entities/{mira_id}", json={"confirm": True}
    )
    assert response.status_code == 404


def test_delete_edgeless_no_confirmation_needed(client: Any) -> None:
    """DELETE_EDGELESS: an entity with zero live edges deletes with no
    body at all — AD-5 requires confirmation only "with live edges"."""
    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    # Cascade-delete mira; the bar is left edgeless (neighbors survive).
    assert (
        client.request(
            "DELETE",
            f"/api/campaigns/{mine['id']}/entities/{mira_id}",
            json={"confirm": True, "cascade": True},
        ).status_code
        == 204
    )
    response = client.delete(f"/api/campaigns/{mine['id']}/entities/{bar_id}")
    assert response.status_code == 204
    from app.store import session_scope, world_entities

    with session_scope() as session:
        assert {e.id for e in world_entities(session, mine["id"])} == set()


def test_delete_unknown_entity_404(client: Any) -> None:
    _register_login(client)
    mine = _create_campaign(client)
    _seed_world(mine["id"])
    response = client.request(
        "DELETE",
        f"/api/campaigns/{mine['id']}/entities/{'0' * 26}",
        json={"confirm": True, "cascade": True},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_delete_stale_base_revision_409_then_current_head_succeeds(client: Any) -> None:
    """DELETE_STALE at the wire: a delete carrying a stale ``base_revision``
    is a 409 envelope with no state change; repeating it with the current
    head succeeds — optimistic concurrency is opt-in per request."""
    from app.store import revision_chain, session_scope, world_entities
    from app.store.read import latest_revision

    _register_login(client)
    mine = _create_campaign(client)
    bar_id, mira_id = _seed_world(mine["id"])
    with session_scope() as session:
        head = latest_revision(session, mine["id"])
        assert head is not None
        seed_head = head.id
    # Advance the head so the captured seed revision goes stale.
    stale_maker_id = ids.new_id()
    commit_subgraph(
        mine["id"],
        [models.EntityInput(kind="place", name="Stale Maker", id=stale_maker_id)],
        [models.EdgeInput(src=stale_maker_id, dst=bar_id, type="located_in")],
        base_revision=seed_head,
    )
    response = client.request(
        "DELETE",
        f"/api/campaigns/{mine['id']}/entities/{mira_id}",
        json={"confirm": True, "cascade": True, "base_revision": seed_head},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    assert "stale" in response.json()["message"].lower()
    with session_scope() as session:
        assert mira_id in {e.id for e in world_entities(session, mine["id"])}  # unchanged
        head = latest_revision(session, mine["id"])
        assert head is not None
        fresh_head = head.id
    assert (
        client.request(
            "DELETE",
            f"/api/campaigns/{mine['id']}/entities/{mira_id}",
            json={"confirm": True, "cascade": True, "base_revision": fresh_head},
        ).status_code
        == 204
    )
    with session_scope() as session:
        # seed, stale-maker, delete — the stale attempt wrote nothing.
        assert len(list(revision_chain(session, mine["id"]))) == 3


def test_delete_malformed_body_400(client: Any) -> None:
    """A syntactically invalid body is a client error — 400, never
    silently treated as absent; nothing is deleted (owner decision
    2026-09-03, mirroring campaigns DELETE)."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    response = client.request(
        "DELETE",
        f"/api/campaigns/{mine['id']}/entities/{mira_id}",
        content=b"{oops",
    )
    assert response.status_code == 400
    with session_scope() as session:
        assert mira_id in {e.id for e in world_entities(session, mine["id"])}


def test_delete_non_dict_body_400(client: Any) -> None:
    """A JSON body that is not an object (array, number) is a 400 — it
    is a present body the client sent, not an absent one."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    for body in (b"[1, 2]", b"42", b'"cascade"'):
        response = client.request(
            "DELETE",
            f"/api/campaigns/{mine['id']}/entities/{mira_id}",
            content=body,
        )
        assert response.status_code == 400
    with session_scope() as session:
        assert mira_id in {e.id for e in world_entities(session, mine["id"])}


def test_delete_wrong_typed_flags_400(client: Any) -> None:
    """Non-boolean ``cascade``/``confirm`` values are rejected with 400 —
    never silently treated as absent (an edgeless target would otherwise
    delete on ``{\"cascade\": \"true\"}``)."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    for payload in (
        {"cascade": "true"},
        {"cascade": 1},
        {"confirm": "yes"},
        {"confirm": 1, "cascade": True},
    ):
        response = client.request(
            "DELETE", f"/api/campaigns/{mine['id']}/entities/{mira_id}", json=payload
        )
        assert response.status_code == 400, payload
    with session_scope() as session:
        assert mira_id in {e.id for e in world_entities(session, mine["id"])}


def test_delete_non_string_base_revision_400(client: Any) -> None:
    """A non-string ``base_revision`` is a 400 before any store call."""
    _register_login(client)
    mine = _create_campaign(client)
    _bar_id, mira_id = _seed_world(mine["id"])
    response = client.request(
        "DELETE",
        f"/api/campaigns/{mine['id']}/entities/{mira_id}",
        json={"confirm": True, "cascade": True, "base_revision": 123},
    )
    assert response.status_code == 400
    with session_scope() as session:
        assert mira_id in {e.id for e in world_entities(session, mine["id"])}
