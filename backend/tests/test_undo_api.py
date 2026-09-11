"""Undo REST surface tests (AD-2; the compensating-commit route added
after spec-4.3).

Pins the wire contract of ``POST /api/campaigns/{campaign_id}/undo``:
204 body-less success on an absent body (undo the latest revision) and
on an explicit ``{"revision_id": "<ulid>"}``; an undo REVERTS the
committed edit and APPENDS its own revision (the log is never rewritten);
undo of an entity delete restores the entity and its edges; a stale
target is the store's 409 through the shared mapper; a foreign/unknown
campaign is the single indistinguishable 404 (checked BEFORE any body is
read); a campaign with no revision at all is a 404; and malformed /
non-object / wrong-typed bodies are 400s with zero revisions written.
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
    world_state,
)


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    """An https TestClient over the real app, with a FRESH scratch DB per
    test — the campaigns/export API test isolation."""
    from app.main import app

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'undo-api.db'}")
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
    """One seed revision: an anchor place + a bare character (text only —
    a bare record merges unconstrained, so the DM edit below is legal),
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
        [models.EdgeInput(src=place_id, dst=character_id, type="located_in", counter=1)],
    )
    return place_id, character_id


def _export(client: Any, campaign_id: str) -> dict[str, Any]:
    response = client.get(f"/api/campaigns/{campaign_id}/export")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


def _entity(client: Any, campaign_id: str, entity_id: str) -> dict[str, Any]:
    export = _export(client, campaign_id)
    entities = export["entities"]
    assert isinstance(entities, list)
    matches = [entity for entity in entities if entity["id"] == entity_id]
    assert len(matches) == 1
    match = matches[0]
    assert isinstance(match, dict)
    return match


def _revisions(campaign_id: str) -> list[models.Revision]:
    with session_scope() as session:
        return list(revision_chain(session, campaign_id))


def _head(campaign_id: str) -> models.Revision:
    with session_scope() as session:
        head = latest_revision(session, campaign_id)
    assert head is not None
    return head


def _state(campaign_id: str) -> tuple[set[str], set[str]]:
    with session_scope() as session:
        entities, edges = world_state(session, campaign_id)
    return {entity.id for entity in entities}, {edge.id for edge in edges}


def _patch_text(client: Any, campaign_id: str, entity_id: str, text: str) -> None:
    response = client.patch(
        f"/api/campaigns/{campaign_id}/entities/{entity_id}", json={"text": text}
    )
    assert response.status_code == 204


def test_undo_requires_auth(client: Any) -> None:
    response = client.post(f"/api/campaigns/{ids.new_id()}/undo")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_undo_reverts_a_committed_edit_and_appends_one_revision(client: Any) -> None:
    """HAPPY_PATH: the DM's hand edit is reverted by a compensating
    commit — the entity is back to its committed text, exactly one new
    revision lands (based on the undone one), and nothing is rewritten."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    _patch_text(client, campaign_id, character_id, "Edited by the DM.")
    undone = _head(campaign_id)
    before = _revisions(campaign_id)
    assert _entity(client, campaign_id, character_id)["text"] == "Edited by the DM."

    response = client.post(f"/api/campaigns/{campaign_id}/undo")

    assert response.status_code == 204
    assert response.content == b""
    after = _revisions(campaign_id)
    assert len(after) == len(before) + 1
    # The log is append-only: every earlier revision survives untouched.
    assert [revision.id for revision in after[: len(before)]] == [r.id for r in before]
    undo_revision = after[-1]
    assert undo_revision.id not in {r.id for r in before}
    # The compensating commit is based on the revision it inverted.
    assert undo_revision.base_revision == undone.id
    assert _entity(client, campaign_id, character_id)["text"] == "A rogue with a ledger."


def test_undo_of_an_entity_delete_restores_the_entity_and_its_edges(client: Any) -> None:
    """An explicit ``revision_id`` (the delete is the head) restores the
    entity AND its edges; undoing that undo re-applies the delete (redo
    falls out of the same compensating machinery)."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    place_id, character_id = _commit_world(campaign_id)
    entities_before, edges_before = _state(campaign_id)
    delete = client.request(
        "DELETE",
        f"/api/campaigns/{campaign_id}/entities/{character_id}",
        json={"confirm": True, "cascade": True},
    )
    assert delete.status_code == 204
    deleted_revision = _head(campaign_id)
    assert _state(campaign_id) == (entities_before - {character_id}, set())

    response = client.post(
        f"/api/campaigns/{campaign_id}/undo", json={"revision_id": deleted_revision.id}
    )

    assert response.status_code == 204
    assert _state(campaign_id) == (entities_before, edges_before)
    restored = _entity(client, campaign_id, character_id)
    assert restored["name"] == "Mira Vane"
    assert restored["text"] == "A rogue with a ledger."
    assert place_id in {entity["id"] for entity in _export(client, campaign_id)["entities"]}
    # Redo: undoing the undo re-applies the delete (absent body = latest).
    redo = client.post(f"/api/campaigns/{campaign_id}/undo")
    assert redo.status_code == 204
    assert _state(campaign_id) == (entities_before - {character_id}, set())


def test_undo_stale_revision_id_is_409(client: Any) -> None:
    """Any target but the head is ``StaleRevisionError`` -> the envelope
    409 through the shared mapper; no state and no revision change."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    stale = _head(campaign_id)
    _patch_text(client, campaign_id, character_id, "Moved on.")
    before_chain = [revision.id for revision in _revisions(campaign_id)]
    before_state = _state(campaign_id)

    response = client.post(f"/api/campaigns/{campaign_id}/undo", json={"revision_id": stale.id})

    assert response.status_code == 409
    assert response.json()["code"] == "conflict"
    assert [revision.id for revision in _revisions(campaign_id)] == before_chain
    assert _state(campaign_id) == before_state
    assert _entity(client, campaign_id, character_id)["text"] == "Moved on."


def test_undo_foreign_or_unknown_campaign_is_404(client: Any) -> None:
    """No oracle (AD-9): another DM's campaign is the single
    indistinguishable 404 — identical to an unknown id, and the ownership
    gate runs BEFORE the body is read (a malformed body still 404s)."""
    _register_login(client, "dm@example.com")
    campaign_id = _create_campaign(client)
    _commit_world(campaign_id)
    _register_login(client, "other@example.com")
    foreign_id = _create_campaign(client, title="Theirs")
    _commit_world(foreign_id)

    _register_login(client, "dm@example.com")
    foreign = client.post(f"/api/campaigns/{foreign_id}/undo")
    unknown = client.post(f"/api/campaigns/{ids.new_id()}/undo")
    assert foreign.status_code == 404
    assert unknown.status_code == 404
    assert foreign.json() == unknown.json()  # indistinguishable
    # Ownership first: a malformed body never turns the 404 into a 400.
    malformed = client.post(f"/api/campaigns/{foreign_id}/undo", content=b"{")
    assert malformed.status_code == 404
    assert malformed.json() == foreign.json()
    # ...and the foreign world is untouched (no undo ran).
    assert len(_revisions(foreign_id)) == 1


def test_undo_body_errors_are_400(client: Any) -> None:
    """Malformed JSON and a non-object body are the entities.py 400s; a
    non-string ``revision_id`` is the base_revision guard's 400. Nothing
    is committed in any case."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    _commit_world(campaign_id)
    before_chain = [revision.id for revision in _revisions(campaign_id)]

    malformed = client.post(f"/api/campaigns/{campaign_id}/undo", content=b"{")
    assert malformed.status_code == 400
    assert malformed.json()["message"] == "Request body must be valid JSON."
    non_object = client.post(f"/api/campaigns/{campaign_id}/undo", json=["not", "an", "object"])
    assert non_object.status_code == 400
    assert non_object.json()["message"] == "Request body must be a JSON object."
    wrong_type = client.post(f"/api/campaigns/{campaign_id}/undo", json={"revision_id": 17})
    assert wrong_type.status_code == 400
    assert wrong_type.json()["message"] == "revision_id must be a revision id string."

    assert [revision.id for revision in _revisions(campaign_id)] == before_chain


def test_undo_without_any_revision_is_404(client: Any) -> None:
    """A campaign with an empty log has nothing to compensate: 404
    "No revision to undo." — with an absent body and with a named one."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    assert _revisions(campaign_id) == []

    bare = client.post(f"/api/campaigns/{campaign_id}/undo")
    named = client.post(f"/api/campaigns/{campaign_id}/undo", json={"revision_id": ids.new_id()})
    assert bare.status_code == 404
    assert bare.json()["message"] == "No revision to undo."
    assert named.status_code == 404
    assert named.json() == bare.json()
    assert _revisions(campaign_id) == []


def test_undo_explicit_head_revision_id_is_accepted(client: Any) -> None:
    """A named target that IS the head behaves exactly like the absent
    body: one compensating revision, 204 body-less."""
    _register_login(client)
    campaign_id = _create_campaign(client)
    _place_id, character_id = _commit_world(campaign_id)
    _patch_text(client, campaign_id, character_id, "Edited by the DM.")
    head = _head(campaign_id)

    response = client.post(f"/api/campaigns/{campaign_id}/undo", json={"revision_id": head.id})

    assert response.status_code == 204
    assert response.content == b""
    assert _head(campaign_id).base_revision == head.id
    assert _entity(client, campaign_id, character_id)["text"] == "A rogue with a ledger."
