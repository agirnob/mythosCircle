"""Relation-edge REST tests: add / edit-counter / delete through the
store commit path (FR9; spec-3.4). Mirrors the entities/delete API
fixture pattern (https TestClient, per-test scratch DB, ownership-404-
first)."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import ids
from app.store import commit_subgraph, models, session_scope, world_edges


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    """An https TestClient over a per-test app, with a FRESH scratch DB
    per test (same isolation contract as the campaigns/entities API
    tests)."""
    from app.main import create_app
    from app.store import app_db_url, init_db

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'edge-api.db'}")
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
    """The Gilded Bar + Mira Vane with one live edge (store commit path)."""
    bar_id, mira_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            models.EntityInput(kind="character", name="Mira Vane", id=mira_id),
        ],
        [models.EdgeInput(src=mira_id, dst=bar_id, type="member_of", counter=1)],
        base_revision=None,
    )
    return bar_id, mira_id


def _edge_ids(campaign_id: str) -> set[str]:
    with session_scope() as session:
        return {e.id for e in world_edges(session, campaign_id)}


def test_edges_require_auth(client: Any) -> None:
    """No session — 401 before any ownership or body check."""
    resp = client.post(f"/api/campaigns/{'1' * 26}/edges", json={})
    assert resp.status_code == 401
    resp = client.delete(f"/api/campaigns/{'1' * 26}/edges/{'2' * 26}")
    assert resp.status_code == 401


def test_add_foreign_campaign_404(client: Any) -> None:
    """Ownership first: another DM's campaign is a single indistinguishable
    404 even with a valid body."""
    _register_login(client)
    resp = client.post(
        f"/api/campaigns/{'1' * 26}/edges",
        json={"src": "1" * 26, "dst": "2" * 26, "type": "debt", "counter": 5},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_add_edge_commits_one_revision(client: Any) -> None:
    """EDGE_ADD: a closed-vocabulary typed edge between committed entities
    commits in exactly one revision (+1 chain, exactly one new edge id)
    and returns 201 with the assigned EdgeOut."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, mira = _seed_world(mine["id"])
    ids_before = _edge_ids(mine["id"])
    with session_scope() as session:
        from app.store import revision_chain

        revisions_before = len(list(revision_chain(session, mine["id"])))
    resp = client.post(
        f"/api/campaigns/{mine['id']}/edges",
        json={"src": mira, "dst": bar, "type": "debt", "counter": 3},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["src"] == mira
    assert body["dst"] == bar
    assert body["type"] == "debt"
    assert body["counter"] == 3
    assert len(body["id"]) == 26
    # Exactly one new edge, exactly one new revision (non-tautological:
    # the before-set and chain length were captured pre-POST).
    assert _edge_ids(mine["id"]) == ids_before | {body["id"]}
    with session_scope() as session:
        from app.store import revision_chain

        assert len(list(revision_chain(session, mine["id"]))) == revisions_before + 1


def test_add_self_loop_422(client: Any) -> None:
    """EDGE_ADD: a self-loop src==dst is rejected by the store (422),
    with zero edges and zero revisions committed."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, _ = _seed_world(mine["id"])
    ids_before = _edge_ids(mine["id"])
    with session_scope() as session:
        from app.store import revision_chain

        revisions_before = len(list(revision_chain(session, mine["id"])))
    resp = client.post(
        f"/api/campaigns/{mine['id']}/edges",
        json={"src": bar, "dst": bar, "type": "loyalty", "counter": 1},
    )
    assert resp.status_code == 422
    assert _edge_ids(mine["id"]) == ids_before
    with session_scope() as session:
        from app.store import revision_chain

        assert len(list(revision_chain(session, mine["id"]))) == revisions_before


def test_add_stale_base_409_then_head_succeeds(client: Any) -> None:
    """EDGE_ADD: a stale ``base_revision`` is a 409; omitting it targets the
    current head and succeeds (201)."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, mira = _seed_world(mine["id"])
    stale = "0" * 26
    resp = client.post(
        f"/api/campaigns/{mine['id']}/edges",
        json={"src": mira, "dst": bar, "type": "debt", "counter": 3, "base_revision": stale},
    )
    assert resp.status_code == 409
    resp = client.post(
        f"/api/campaigns/{mine['id']}/edges",
        json={"src": mira, "dst": bar, "type": "debt", "counter": 3},
    )
    assert resp.status_code == 201


def test_add_invalid_type_422(client: Any) -> None:
    """EDGE_ADD: a type outside the closed vocabulary is a 422."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, mira = _seed_world(mine["id"])
    resp = client.post(
        f"/api/campaigns/{mine['id']}/edges",
        json={"src": mira, "dst": bar, "type": "friends", "counter": 1},
    )
    assert resp.status_code == 422


def test_add_dangling_endpoint_422(client: Any) -> None:
    """EDGE_ADD: an endpoint that is not a committed entity is a 422."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, _ = _seed_world(mine["id"])
    resp = client.post(
        f"/api/campaigns/{mine['id']}/edges",
        json={"src": bar, "dst": "F" * 26, "type": "debt", "counter": 1},
    )
    assert resp.status_code == 422


def test_edit_counter_commits(client: Any) -> None:
    """EDGE_EDIT: counter updates in one revision; src/dst/type untouched."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, mira = _seed_world(mine["id"])
    edge_id = next(iter(_edge_ids(mine["id"])))
    resp = client.patch(
        f"/api/campaigns/{mine['id']}/edges/{edge_id}",
        json={"counter": 42},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["counter"] == 42
    assert body["src"] == mira
    assert body["dst"] == bar
    assert body["type"] == "member_of"


def test_edit_unknown_edge_404(client: Any) -> None:
    """EDGE_EDIT: an unknown/foreign edge id is a 404 before any store
    commit."""
    _register_login(client)
    mine = _create_campaign(client)
    resp = client.patch(
        f"/api/campaigns/{mine['id']}/edges/{'1' * 26}",
        json={"counter": 1},
    )
    assert resp.status_code == 404


def test_edit_retarget_forbidden_by_shape(client: Any) -> None:
    """EDGE_EDIT: the PATCH body carries only ``counter`` — src/dst/type
    are immutable (AD-2), so the schema drops any attempt to retarget."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, mira = _seed_world(mine["id"])
    edge_id = next(iter(_edge_ids(mine["id"])))
    resp = client.patch(
        f"/api/campaigns/{mine['id']}/edges/{edge_id}",
        json={"counter": 7, "src": bar},
    )
    # Unknown field is ignored by Pydantic extra=ignore; counter applied.
    assert resp.status_code == 200
    assert resp.json()["counter"] == 7
    assert resp.json()["src"] == mira


def test_delete_edge_commits(client: Any) -> None:
    """EDGE_DELETE: a single edge deletes with no confirm in one revision;
    both neighbor entities survive (only the edge goes)."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, mira = _seed_world(mine["id"])
    edge_id = next(iter(_edge_ids(mine["id"])))
    resp = client.delete(f"/api/campaigns/{mine['id']}/edges/{edge_id}")
    assert resp.status_code == 204
    assert edge_id not in _edge_ids(mine["id"])
    with session_scope() as session:
        from app.store import world_entities

        assert {e.id for e in world_entities(session, mine["id"])} == {bar, mira}


def test_delete_unknown_edge_404(client: Any) -> None:
    """EDGE_DELETE: an unknown/foreign edge id is a 404."""
    _register_login(client)
    mine = _create_campaign(client)
    resp = client.delete(f"/api/campaigns/{mine['id']}/edges/{'1' * 26}")
    assert resp.status_code == 404


def test_delete_stale_base_409(client: Any) -> None:
    """EDGE_DELETE: a stale ``base_revision`` is a 409; no state change."""
    _register_login(client)
    mine = _create_campaign(client)
    _seed_world(mine["id"])
    edge_id = next(iter(_edge_ids(mine["id"])))
    resp = client.delete(f"/api/campaigns/{mine['id']}/edges/{edge_id}?base_revision={'0' * 26}")
    assert resp.status_code == 409
    assert edge_id in _edge_ids(mine["id"])


def test_delete_absent_body_is_legal(client: Any) -> None:
    """EDGE_DELETE: no body required — the 204 path with no confirmation."""
    _register_login(client)
    mine = _create_campaign(client)
    _seed_world(mine["id"])
    edge_id = next(iter(_edge_ids(mine["id"])))
    resp = client.delete(f"/api/campaigns/{mine['id']}/edges/{edge_id}")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Ownership-404-first body ordering + strict wire counter (spec-3-4 review)
# ---------------------------------------------------------------------------


def test_add_malformed_body_400_but_foreign_campaign_still_404(client: Any) -> None:
    """Ownership-404-first: a malformed JSON body on a foreign campaign is
    the indistinguishable 404 — the campaign check precedes the body; the
    same malformed body on an owned campaign is a 400."""
    _register_login(client)
    mine = _create_campaign(client)
    resp = client.post(
        f"/api/campaigns/{'1' * 26}/edges",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 404
    resp = client.post(
        f"/api/campaigns/{mine['id']}/edges",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_add_non_dict_body_400(client: Any) -> None:
    """A JSON body that is not an object (array, number) is a 400 — an
    owned campaign gets the structural rejection, a foreign one still
    gets the indistinguishable 404 first."""
    _register_login(client)
    mine = _create_campaign(client)
    resp = client.post(f"/api/campaigns/{mine['id']}/edges", json=[1, 2])
    assert resp.status_code == 400
    resp = client.post(f"/api/campaigns/{'1' * 26}/edges", json=[1, 2])
    assert resp.status_code == 404


def test_add_absent_body_422_missing_fields(client: Any) -> None:
    """An absent body is an empty object: the required fields fail their
    own 422 (never a 500 or a state change)."""
    _register_login(client)
    mine = _create_campaign(client)
    ids_before = _edge_ids(mine["id"])
    resp = client.post(f"/api/campaigns/{mine['id']}/edges")
    assert resp.status_code == 422
    assert _edge_ids(mine["id"]) == ids_before


def test_add_non_string_endpoint_422(client: Any) -> None:
    """A non-string endpoint (or type) is a 422 before the store is called."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, mira = _seed_world(mine["id"])
    ids_before = _edge_ids(mine["id"])
    resp = client.post(
        f"/api/campaigns/{mine['id']}/edges",
        json={"src": 7, "dst": bar, "type": "debt"},
    )
    assert resp.status_code == 422
    resp = client.post(
        f"/api/campaigns/{mine['id']}/edges",
        json={"src": mira, "dst": bar, "type": 7},
    )
    assert resp.status_code == 422
    assert _edge_ids(mine["id"]) == ids_before


def test_add_counter_coercions_rejected_422(client: Any) -> None:
    """The wire counter is strictly an int: numeric strings, integral
    floats, and booleans are 422 — never a silent coercion (spec-2-2)."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, mira = _seed_world(mine["id"])
    ids_before = _edge_ids(mine["id"])
    for bad in ("3", 3.0, True, None):
        resp = client.post(
            f"/api/campaigns/{mine['id']}/edges",
            json={"src": mira, "dst": bar, "type": "debt", "counter": bad},
        )
        assert resp.status_code == 422, bad
    assert _edge_ids(mine["id"]) == ids_before


def test_add_edge_counter_semantic_bounds_422(client: Any) -> None:
    """Owner ruling 2026-09-18: semantic counter bounds reach the wire as
    422 validation_error (grudge 0/11, ally_of 0/11, debt -1/1_000_001),
    while boundary values commit (debt 0, ally_of 10)."""
    _register_login(client)
    mine = _create_campaign(client)
    bar, mira = _seed_world(mine["id"])
    ids_before = _edge_ids(mine["id"])
    for edge_type, counter in [
        ("grudge", 0),
        ("grudge", 11),
        ("ally_of", 0),
        ("ally_of", 11),
        ("debt", -1),
        ("debt", 1_000_001),
    ]:
        resp = client.post(
            f"/api/campaigns/{mine['id']}/edges",
            json={"src": mira, "dst": bar, "type": edge_type, "counter": counter},
        )
        assert resp.status_code == 422, (edge_type, counter)
        assert resp.json()["code"] == "validation_error", (edge_type, counter)
    assert _edge_ids(mine["id"]) == ids_before
    added: set[str] = set()
    for edge_type, counter in [("debt", 0), ("ally_of", 10)]:
        resp = client.post(
            f"/api/campaigns/{mine['id']}/edges",
            json={"src": mira, "dst": bar, "type": edge_type, "counter": counter},
        )
        assert resp.status_code == 201, (edge_type, counter)
        added.add(resp.json()["id"])
    assert _edge_ids(mine["id"]) == ids_before | added


def test_patch_non_int_counter_422(client: Any) -> None:
    """EDGE_EDIT: a non-int counter on the PATCH wire is a 422, no state
    change."""
    _register_login(client)
    mine = _create_campaign(client)
    _seed_world(mine["id"])
    edge_id = next(iter(_edge_ids(mine["id"])))
    resp = client.patch(
        f"/api/campaigns/{mine['id']}/edges/{edge_id}",
        json={"counter": "42"},
    )
    assert resp.status_code == 422


def test_patch_after_delete_does_not_resurrect(client: Any) -> None:
    """The review's resurrection race, pinned at the wire: a PATCH whose
    read happened before a concurrent DELETE must fail (404 via the
    store's UnknownEdgeError backstop, or 409 stale base) — the edge
    must NOT come back under the same ULID (explicit ids never create).
    Simulated by staging the PATCH's commit after the delete: read the
    edge, delete it, then PATCH."""
    _register_login(client)
    mine = _create_campaign(client)
    _seed_world(mine["id"])
    edge_id = next(iter(_edge_ids(mine["id"])))
    # The DELETE commits first.
    assert client.delete(f"/api/campaigns/{mine['id']}/edges/{edge_id}").status_code == 204
    ids_after_delete = _edge_ids(mine["id"])
    # The PATCH arrives after the delete: unknown edge -> 404, and the
    # deleted edge stays deleted (no same-ULID resurrection).
    resp = client.patch(
        f"/api/campaigns/{mine['id']}/edges/{edge_id}",
        json={"counter": 9},
    )
    assert resp.status_code == 404
    assert _edge_ids(mine["id"]) == ids_after_delete


def test_patch_foreign_campaign_404(client: Any) -> None:
    """EDGE_EDIT ownership first: a foreign campaign is the 404, even
    with an invalid body (pydantic-free body parsing)."""
    _register_login(client)
    resp = client.patch(
        f"/api/campaigns/{'1' * 26}/edges/{'2' * 26}",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 404
