"""Media REST surface tests (spec-4.1, AD-9/AD-10).

Pins the wire contract over the manifest + file routes: ownership-404
first (FOREIGN_CAMPAIGN), the golden HAPPY_PATH (enqueue -> worker run
-> manifest list -> file GET), the NO_APPEARANCE enqueue 422, and
ROW_WITHOUT_FILE (a manifest row whose file is gone is the same 404).
The worker is driven deterministically with an injected mock image
provider (the app's own worker is disabled under MYTHOSCIRCLE_TESTING=1).
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import ids
from app.core.settings import ImageSettings
from app.pipeline.worker import run_next_job
from app.store import (
    app_db_url,
    commit_subgraph,
    create_campaign,
    create_session,
    init_db,
    models,
    register_account,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"portrait-payload"


@pytest.fixture()
def media_api(client: TestClient, tmp_path: Path) -> Iterator[Callable[[], str]]:
    """Re-point the app's store at a fresh scratch DB; yields a campaign
    maker bound to the client's authed account.

    The session is created through the STORE (not the register route): the
    register limiter (10 per 15 min per IP, AR29) is a process-wide shared
    budget, and a per-test register would starve later tests in a full
    suite run. The cookie is set directly so the routes see a real session.
    """
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'media-api.db'}")
    account = register_account(f"dm-{ids.new_id()}@example.com", "password123")
    token, _session = create_session(account.id)
    client.cookies.set("mythoscircle_session", token, path="/api")

    def make(title: str = "Media API World") -> str:
        return create_campaign(
            account.id, title=title, description="", theme="High Fantasy", custom_lore=""
        ).id

    try:
        yield make
    finally:
        init_db(previous)


def _commit_entity(campaign_id: str, appearance: object, name: str = "Mira Vane") -> str:
    """One committed character with the given appearance (FR2: a new
    entity needs an edge, so the subgraph carries a small anchor pair)."""
    from app.store import latest_revision, session_scope

    with session_scope() as session:
        head = latest_revision(session, campaign_id)
        base = head.id if head is not None else None
    entity_id = ids.new_id()
    anchor_id = ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(
                kind="character", name=name, data={"appearance": appearance}, id=entity_id
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
        base_revision=base,
    )
    return entity_id


def _run_portrait_job() -> None:
    """Drive the FIFO one step with the injected mock image provider —
    the deterministic stand-in for the app's background worker."""

    def provider(prompt: str, settings: ImageSettings) -> bytes:
        assert "face" in prompt
        return PNG_BYTES

    processed = run_next_job(image_provider=provider)
    assert processed is not None


def _enqueue_image(client: TestClient, campaign_id: str, entity_id: str) -> None:
    response = client.post(
        "/api/jobs",
        json={"campaign_id": campaign_id, "kind": "image", "payload": {"entity_id": entity_id}},
    )
    assert response.status_code == 201


def _portrait_filename(client: TestClient, campaign_id: str) -> str:
    listing = client.get(f"/api/campaigns/{campaign_id}/media")
    assert listing.status_code == 200
    rows = listing.json()["media"]
    assert len(rows) == 1
    filename = rows[0]["filename"]
    assert isinstance(filename, str)
    return filename


def test_media_routes_require_auth(client: TestClient, media_api: Callable[[], str]) -> None:
    campaign_id = media_api()
    # The media_api fixture signs in — drop the session cookie so the
    # routes see an anonymous request.
    client.cookies.clear()
    assert client.get(f"/api/campaigns/{campaign_id}/media").status_code == 401
    assert client.get(f"/api/campaigns/{campaign_id}/media/entity/file.png").status_code == 401


def test_media_list_foreign_campaign_is_404(
    client: TestClient, media_api: Callable[[], str]
) -> None:
    """FOREIGN_CAMPAIGN: another DM's campaign is the single
    indistinguishable 404 — never a 403 or an empty 200."""
    campaign_id = media_api()
    other_id = create_campaign(
        register_account(f"foreign-{ids.new_id()}@example.com", "password123").id,
        title="Foreign",
        description="",
        theme="Grimdark",
        custom_lore="",
    ).id
    assert client.get(f"/api/campaigns/{other_id}/media").status_code == 404
    # The owner's own campaign still lists fine (200, empty manifest).
    assert client.get(f"/api/campaigns/{campaign_id}/media").status_code == 200


def test_media_happy_path_file_round_trip(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HAPPY_PATH end to end: enqueue (201) -> worker run (mock provider)
    -> manifest list (200, one row) -> file GET (200, image/png, exact
    bytes)."""
    campaign_id = media_api()
    entity_id = _commit_entity(campaign_id, {"face": "sharp features", "body": "lean"})
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))

    _enqueue_image(client, campaign_id, entity_id)
    _run_portrait_job()

    listing = client.get(f"/api/campaigns/{campaign_id}/media")
    assert listing.status_code == 200
    rows = listing.json()["media"]
    assert len(rows) == 1
    row = rows[0]
    assert row["entity_id"] == entity_id and row["kind"] == "image"
    filename = row["filename"]
    assert filename.endswith(".png")

    fetched = client.get(f"/api/campaigns/{campaign_id}/media/{entity_id}/{filename}")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    # ``inline`` disposition: the portrait must render inside the entity
    # card's <img>, never download (acceptance criterion 2).
    assert "inline" in fetched.headers.get("content-disposition", "")
    assert "attachment" not in fetched.headers.get("content-disposition", "")
    assert fetched.content == PNG_BYTES


def test_media_file_unknown_entity_or_filename_is_404(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = media_api()
    entity_id = _commit_entity(campaign_id, {"face": "sharp"})
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _enqueue_image(client, campaign_id, entity_id)
    _run_portrait_job()
    filename = _portrait_filename(client, campaign_id)

    other_entity = _commit_entity(campaign_id, {"face": "other"}, name="Other")
    assert (
        client.get(f"/api/campaigns/{campaign_id}/media/{other_entity}/{filename}").status_code
        == 404
    )
    assert (
        client.get(f"/api/campaigns/{campaign_id}/media/{entity_id}/{ids.new_id()}.png").status_code
        == 404
    )
    # Traversal-shaped names never reach the filesystem or the row lookup
    # (a literal /.. is normalized by the router, so the encoded form is
    # the one that exercises the guard).
    assert (
        client.get(f"/api/campaigns/{campaign_id}/media/{entity_id}/..%2Fetc%2Fpasswd").status_code
        == 404
    )
    # A crafted filename separator (encoded %2F) is rejected outright —
    # never decoded into a path traversal.
    assert (
        client.get(
            f"/api/campaigns/{campaign_id}/media/{entity_id}/{ids.new_id()}%2F..%2Fescape.png"
        ).status_code
        == 404
    )


def test_media_file_route_foreign_campaign_is_404(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FOREIGN_CAMPAIGN on the FILE route: another DM's campaign with a
    VALID row filename is the single indistinguishable 404 — ownership
    precedes even a resolvable file (AD-9, never a 403/200)."""
    campaign_id = media_api()
    entity_id = _commit_entity(campaign_id, {"face": "sharp"})
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _enqueue_image(client, campaign_id, entity_id)
    _run_portrait_job()
    filename = _portrait_filename(client, campaign_id)

    other_id = create_campaign(
        register_account(f"foreign-file-{ids.new_id()}@example.com", "password123").id,
        title="Foreign",
        description="",
        theme="Grimdark",
        custom_lore="",
    ).id
    # The foreign campaign has a REAL row + file on disk under its own
    # media dir (same entity id, same filename — as real as it gets); the
    # owner's cookie must still 404 on it (ownership first, AD-9).
    from app.store import add_media, commit_subgraph

    foreign_entity_id = ids.new_id()
    foreign_anchor = ids.new_id()
    commit_subgraph(
        other_id,
        [
            models.EntityInput(kind="place", name="Anchor", id=foreign_anchor),
            models.EntityInput(
                kind="character",
                name="Foreign Mira",
                data={"appearance": {"face": "sharp"}},
                id=foreign_entity_id,
            ),
        ],
        [models.EdgeInput(src=foreign_anchor, dst=foreign_entity_id, type="located_in", counter=1)],
    )
    add_media(other_id, foreign_entity_id, filename, "image")
    foreign_file = tmp_path / "media" / other_id / foreign_entity_id / filename
    foreign_file.parent.mkdir(parents=True, exist_ok=True)
    foreign_file.write_bytes(PNG_BYTES)
    assert (
        client.get(f"/api/campaigns/{other_id}/media/{foreign_entity_id}/{filename}").status_code
        == 404
    )


def test_media_row_without_file_is_404(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ROW_WITHOUT_FILE: the manifest row exists but the file is gone from
    disk — the file GET is the same 404, never a 500."""
    campaign_id = media_api()
    entity_id = _commit_entity(campaign_id, {"face": "sharp"})
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _enqueue_image(client, campaign_id, entity_id)
    _run_portrait_job()
    filename = _portrait_filename(client, campaign_id)

    (tmp_path / "media" / campaign_id / entity_id / filename).unlink()
    fetched = client.get(f"/api/campaigns/{campaign_id}/media/{entity_id}/{filename}")
    assert fetched.status_code == 404


def test_media_no_appearance_enqueue_is_422(
    client: TestClient, media_api: Callable[[], str]
) -> None:
    """NO_APPEARANCE: the forced enqueue for an appearance-less entity is
    the envelope 422 (zero jobs written); the world card never sends it."""
    campaign_id = media_api()
    entity_id = _commit_entity(campaign_id, {"face": "  "})
    response = client.post(
        "/api/jobs",
        json={"campaign_id": campaign_id, "kind": "image", "payload": {"entity_id": entity_id}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
