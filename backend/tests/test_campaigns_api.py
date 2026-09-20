"""Campaign API tests: auth-gated, owner-scoped CRUD, AR20 delete-gate
(spec-1.6). Uses the https TestClient so the Secure session cookie
round-trips (1.5).
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    """An https TestClient over the real app, with a FRESH scratch DB per
    test — campaign counts must not leak across tests (the shared conftest
    DB would accumulate the same email's campaigns; review isolation)."""
    from app.main import app
    from app.store import app_db_url, init_db

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'campaign-api.db'}")
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


def _create_campaign(client: Any, **overrides: Any) -> Any:
    payload = {
        "title": "Aetheria",
        "description": "A living world",
        "theme": "High Fantasy",
        "custom_lore": "The old gods stir",
    }
    payload.update(overrides)
    return client.post("/api/campaigns", json=payload)


def test_create_requires_auth(client: Any) -> None:
    response = _create_campaign(client)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_create_201_owner_bound(client: Any) -> None:
    _register_login(client)
    response = _create_campaign(client, theme="grimdark")  # case-insensitive
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Aetheria"
    assert body["theme"] == "Grimdark"  # canonical seed form
    assert body["owner_id"]
    assert body["custom_lore"] == "The old gods stir"


def test_create_bad_theme_422(client: Any) -> None:
    _register_login(client)
    response = _create_campaign(client, theme="Cyberpunk")
    assert response.status_code == 422
    assert "seed list" in response.json()["message"]


def test_themes_endpoint_lists_the_seed(client: Any) -> None:
    """GET /api/campaigns/themes (dogfood fix 2026-09-09): the form picker's
    source — auth-gated, config-ordered, and the literal path must win the
    route match over /{campaign_id} (a swallowed themes request would 404
    as a campaign id)."""
    assert client.get("/api/campaigns/themes").status_code == 401
    _register_login(client)
    from app.store.campaigns import seed_themes

    response = client.get("/api/campaigns/themes")
    assert response.status_code == 200
    assert response.json() == {"themes": seed_themes()}
    assert "High Fantasy" in response.json()["themes"]


def test_list_requires_auth_and_scopes(client: Any) -> None:
    _register_login(client, "dm@example.com")
    _create_campaign(client, title="Mine")
    _register_login(client, "other@example.com")
    _create_campaign(client, title="Theirs")
    # Logged in as other: only their own campaign is listed.
    response = client.get("/api/campaigns")
    assert response.status_code == 200
    titles = [c["title"] for c in response.json()["campaigns"]]
    assert titles == ["Theirs"]  # only own worlds (AD-9)
    # The other DM's campaign is 404 to a different owner.
    _register_login(client, "dm@example.com")
    response = client.get("/api/campaigns")
    assert [c["title"] for c in response.json()["campaigns"]] == ["Mine"]
    theirs_id = "0" * 26  # foreign id is not retrievable from this session
    assert client.get(f"/api/campaigns/{theirs_id}").status_code == 404


def test_get_foreign_404_identical_to_unknown(client: Any) -> None:
    _register_login(client, "dm@example.com")
    _create_campaign(client).json()  # dm has a campaign of their own
    _register_login(client, "other@example.com")
    theirs = _create_campaign(client).json()

    # Logged in as dm: their campaign is 404.
    _register_login(client, "dm@example.com")
    foreign_response = client.get(f"/api/campaigns/{theirs['id']}")
    unknown_response = client.get(f"/api/campaigns/{'0' * 26}")
    assert foreign_response.status_code == 404
    assert unknown_response.status_code == 404
    assert foreign_response.json() == unknown_response.json()  # indistinguishable


def test_update_validates_and_scopes(client: Any) -> None:
    _register_login(client, "dm@example.com")
    mine = _create_campaign(client).json()
    # PATCH with no fields -> 422
    assert client.patch(f"/api/campaigns/{mine['id']}", json={}).status_code == 422
    # Valid partial update
    response = client.patch(f"/api/campaigns/{mine['id']}", json={"title": "New Title"})
    assert response.status_code == 200
    assert response.json()["title"] == "New Title"
    assert response.json()["custom_lore"] == "The old gods stir"  # untouched


def test_delete_requires_confirmation(client: Any) -> None:
    _register_login(client, "dm@example.com")
    mine = _create_campaign(client).json()
    # Without confirm (no body / false / malformed) -> 400, nothing deleted.
    assert client.delete(f"/api/campaigns/{mine['id']}").status_code == 400
    assert (
        client.request(
            "DELETE", f"/api/campaigns/{mine['id']}", json={"confirm": False}
        ).status_code
        == 400
    )
    assert (
        client.request(
            "DELETE", f"/api/campaigns/{mine['id']}", json={"confirm": "yes"}
        ).status_code
        == 400
    )
    assert client.get(f"/api/campaigns/{mine['id']}").status_code == 200
    # With confirm -> 204, gone.
    assert (
        client.request("DELETE", f"/api/campaigns/{mine['id']}", json={"confirm": True}).status_code
        == 204
    )
    assert client.get(f"/api/campaigns/{mine['id']}").status_code == 404


def test_delete_foreign_404(client: Any) -> None:
    _register_login(client, "dm@example.com")
    mine = _create_campaign(client).json()
    _register_login(client, "other@example.com")
    response = client.request("DELETE", f"/api/campaigns/{mine['id']}", json={"confirm": True})
    assert response.status_code == 404
    _register_login(client, "dm@example.com")
    assert client.get(f"/api/campaigns/{mine['id']}").status_code == 200  # untouched


def test_create_blank_title_422(client: Any) -> None:
    """CREATE_BLANK: a whitespace-only title is a 422 after trimming
    (review round 1 — pre-trim min_length alone let '   ' through)."""
    _register_login(client)
    response = _create_campaign(client, title="   ")
    assert response.status_code == 422
    assert "blank" in response.json()["message"].lower()


def test_patch_null_field_422(client: Any) -> None:
    """An explicit null PATCH field is a 422 with nothing changed — never
    a 500 (review round 1)."""
    _register_login(client)
    mine = _create_campaign(client).json()
    response = client.patch(f"/api/campaigns/{mine['id']}", json={"title": None})
    assert response.status_code == 422
    assert client.get(f"/api/campaigns/{mine['id']}").json()["title"] == "Aetheria"  # unchanged


def test_list_empty_200(client: Any) -> None:
    _register_login(client)
    response = client.get("/api/campaigns")
    assert response.status_code == 200
    assert response.json()["campaigns"] == []


def test_delete_campaign_reclaims_media_rows_and_files(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAMPAIGN_DELETE_MEDIA (spec-4.3): the confirmed AR20 delete removes
    the campaign's manifest rows (inside the store transaction) and its
    whole media directory (post-commit reclaim) — another campaign's
    media rows and files survive untouched."""
    from sqlalchemy import select

    from app.core import ids
    from app.store import add_media, commit_subgraph, models, session_scope

    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    mine = _create_campaign(client).json()
    keep = _create_campaign(client, title="Keeper").json()

    def seed(campaign_id: str) -> str:
        """One committed entity pair (a new entity needs an anchor edge)."""
        entity_id, anchor_id = ids.new_id(), ids.new_id()
        commit_subgraph(
            campaign_id,
            [
                models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
                models.EntityInput(kind="character", name="Mira Vane", id=entity_id),
            ],
            [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
        )
        return entity_id

    mira = seed(mine["id"])
    keep_entity = seed(keep["id"])
    for campaign_id, entity_id in ((mine["id"], mira), (keep["id"], keep_entity)):
        row = add_media(campaign_id, entity_id, f"{ids.new_id()}.png", "image")
        path = tmp_path / "media" / campaign_id / entity_id / row.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"payload")

    response = client.request("DELETE", f"/api/campaigns/{mine['id']}", json={"confirm": True})
    assert response.status_code == 204
    # The deleted campaign's media directory is gone — exactly it.
    assert not (tmp_path / "media" / mine["id"]).exists()
    assert (tmp_path / "media" / keep["id"]).is_dir()
    with session_scope() as session:
        # Scoped selects: the full-suite DB is shared per process, so the
        # probe must not depend on what other tests left in the table.
        mine_rows = session.scalars(
            select(models.Media).where(models.Media.campaign_id == mine["id"])
        ).all()
        assert mine_rows == []
        keep_rows = session.scalars(
            select(models.Media).where(models.Media.campaign_id == keep["id"])
        ).all()
        assert len(keep_rows) == 1


def test_delete_wire_leaves_zero_rows_in_all_ar20_tables(client: Any) -> None:
    """DELETE_ZERO_COUNTS (spec-6.3): a confirmed wire DELETE 204 leaves
    ZERO rows in every AR20 table scoped to the deleted id — revisions,
    event log, entities/edges, queue entries (in more than one state),
    media-manifest rows — while a keep campaign's world rows survive
    (negative control; an over-broad cascade must not touch it).
    ProposedCandidate is NOT re-pinned here: its cascade is pinned at
    store level (test_generate_pipeline.py: test_delete_campaign_cascades_
    staged_candidates + test_mid_run_campaign_delete_does_not_wedge_worker)
    and the wire delete invokes the identical store function."""
    from sqlalchemy import select

    from app.core import ids
    from app.store import (
        add_media,
        cancel_job,
        commit_subgraph,
        enqueue_job,
        models,
        session_scope,
    )

    _register_login(client)
    mine = _create_campaign(client).json()
    keep = _create_campaign(client, title="Keeper").json()

    def seed(campaign_id: str) -> str:
        entity_id, anchor_id = ids.new_id(), ids.new_id()
        commit_subgraph(
            campaign_id,
            [
                models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
                models.EntityInput(kind="character", name="Mira Vane", id=entity_id),
            ],
            [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
        )
        return entity_id

    mira = seed(mine["id"])
    # Queue entries in DIFFERENT states (a state-filtered cascade bug
    # must not pass): one queued, one cancelled-terminal.
    enqueue_job(mine["id"], "text", {"prompt": "first"})
    terminal = enqueue_job(mine["id"], "text", {"prompt": "second"}).id
    cancel_job(terminal)
    add_media(mine["id"], mira, f"{ids.new_id()}.png", "image")
    # Keep-side world rows for the negative control.
    seed(keep["id"])
    enqueue_job(keep["id"], "text", {"prompt": "keeper"})

    response = client.request("DELETE", f"/api/campaigns/{mine['id']}", json={"confirm": True})
    assert response.status_code == 204

    with session_scope() as session:
        ar20_tables: list[tuple[str, Any]] = [
            ("revisions", models.Revision),
            ("events", models.Event),
            ("entities", models.Entity),
            ("edges", models.Edge),
            ("jobs", models.Job),
            ("media", models.Media),
        ]
        for label, table in ar20_tables:
            rows = session.scalars(select(table).where(table.campaign_id == mine["id"])).all()
            assert rows == [], f"{label} rows survive the confirmed delete"
        # Negative control: the keep campaign's world rows still exist —
        # only the deleted id's rows went.
        keep_world = {
            label: len(session.scalars(select(table).where(table.campaign_id == keep["id"])).all())
            for label, table in ar20_tables[:5]
        }
        assert keep_world["revisions"] >= 1 and keep_world["entities"] >= 1
        assert keep_world["jobs"] == 1


def test_delete_media_reclaim_failure_still_204(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reclaim failure never turns a successful delete into an error:
    the helper is imported by name into the route module, so patching
    ``app.api.campaigns.reclaim_campaign_media`` pins the boundary — the
    store rows are already gone and the 204 stands even when the file
    reclaim explodes."""
    from sqlalchemy import select

    from app.api import campaigns as campaigns_module
    from app.core import ids
    from app.store import add_media, commit_subgraph, models, session_scope

    def exploding_reclaim(media_dir: object, campaign_id: str) -> None:
        raise RuntimeError("reclaim exploded")

    monkeypatch.setattr(campaigns_module, "reclaim_campaign_media", exploding_reclaim)
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    mine = _create_campaign(client).json()
    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        mine["id"],
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(kind="character", name="Mira Vane", id=entity_id),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    add_media(mine["id"], entity_id, f"{ids.new_id()}.png", "image")
    response = client.request("DELETE", f"/api/campaigns/{mine['id']}", json={"confirm": True})
    assert response.status_code == 204
    with session_scope() as session:
        rows = session.scalars(
            select(models.Media).where(models.Media.campaign_id == mine["id"])
        ).all()
        assert rows == []
