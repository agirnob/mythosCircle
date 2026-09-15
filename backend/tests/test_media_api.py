"""Media REST surface tests (spec-4.1, AD-9/AD-10; spec-4.3 delete).

Pins the wire contract over the manifest + file routes: ownership-404
first (FOREIGN_CAMPAIGN), the golden HAPPY_PATH (enqueue -> worker run
-> manifest list -> file GET), the NO_APPEARANCE enqueue 422, and
ROW_WITHOUT_FILE (a manifest row whose file is gone is the same 404).
The worker is driven deterministically with an injected mock image
provider (the app's own worker is disabled under MYTHOSCIRCLE_TESTING=1).

Spec-4.3 adds the single-portrait DELETE: one named manifest ROW goes,
its file is unlinked after the commit (a missing file is still a 204),
and neither the world graph (no revision, no entity) nor the campaign's
other rows move.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import ids
from app.core.settings import ImageSettings
from app.pipeline.worker import run_next_job
from app.store import (
    add_media,
    app_db_url,
    commit_subgraph,
    create_campaign,
    create_session,
    init_db,
    models,
    register_account,
    revision_chain,
    session_scope,
    world_state,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"portrait-payload"
MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00reveal-payload"


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

    def provider(prompt: str, settings: ImageSettings, use_rembg: bool = False) -> bytes:
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
    assert (
        client.delete(
            f"/api/campaigns/{campaign_id}/entities/entity/media/{ids.new_id()}"
        ).status_code
        == 401
    )


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


def _commit_boss(campaign_id: str, data: dict[str, Any] | None = None, name: str = "Vashka") -> str:
    """One committed boss-tier character with a usable reveal prompt."""
    from app.store import latest_revision, session_scope

    if data is None:
        data = {
            "name": "Vashka the Unmaker",
            "role": "BBEG",
            "appearance": {"face": "a mask of fused iron"},
            "boss": {"lair_actions": "the walls breathe", "immunities": "fire"},
        }
    with session_scope() as session:
        head = latest_revision(session, campaign_id)
        base = head.id if head is not None else None
    entity_id = ids.new_id()
    anchor_id = ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(kind="character", name=name, data=data, id=entity_id),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
        base_revision=base,
    )
    return entity_id


def _run_video_job() -> None:
    """Drive the FIFO one step with the injected mock video provider —
    the deterministic stand-in for the app's background worker."""
    from app.core.settings import VideoSettings

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        assert "lair_actions" in prompt
        assert first_frame is None  # openai path: no portrait resolution (review round 1)
        return MP4_BYTES

    processed = run_next_job(video_provider=provider)
    assert processed is not None


def _enqueue_video(
    client: TestClient, campaign_id: str, entity_id: str, kind: str = "video"
) -> None:
    response = client.post(
        "/api/jobs",
        json={"campaign_id": campaign_id, "kind": kind, "payload": {"entity_id": entity_id}},
    )
    assert response.status_code == 201


def test_video_happy_path_serves_mp4_inline(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HAPPY_PATH end to end: enqueue (201) -> worker run (mock provider)
    -> manifest row kind='video' -> the file GET serves ``video/mp4``
    inline behind the session cookie."""
    campaign_id = media_api()
    entity_id = _commit_boss(campaign_id)
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _enqueue_video(client, campaign_id, entity_id)
    _run_video_job()
    listing = client.get(f"/api/campaigns/{campaign_id}/media")
    assert listing.status_code == 200
    (row,) = listing.json()["media"]
    assert row["kind"] == "video"
    assert row["entity_id"] == entity_id
    filename = row["filename"]
    assert isinstance(filename, str) and filename.endswith(".mp4")
    fetched = client.get(f"/api/campaigns/{campaign_id}/media/{entity_id}/{filename}")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("video/mp4")
    assert fetched.headers.get("content-disposition", "").startswith("inline")
    assert fetched.content == MP4_BYTES
    # The portrait listing is untouched by a video row: the newest video
    # row must never displace the portrait projection (spec-4.2 Never).


def test_video_not_boss_enqueue_is_422(client: TestClient, media_api: Callable[[], str]) -> None:
    """NOT_BOSS: the forced reveal-video enqueue for a non-boss-tier
    entity is the envelope 422 (zero jobs written)."""
    campaign_id = media_api()
    npc_id = _commit_boss(
        campaign_id,
        data={
            "name": "Mira Vane",
            "role": "NPC",
            "appearance": {"face": "sharp"},
            "boss": {"lair_actions": "walls breathe"},
        },
        name="Mira Vane",
    )
    response = client.post(
        "/api/jobs",
        json={"campaign_id": campaign_id, "kind": "video", "payload": {"entity_id": npc_id}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_video_no_usable_prompt_enqueue_is_422(
    client: TestClient, media_api: Callable[[], str]
) -> None:
    """NO_VIDEO_PROMPT: the forced enqueue for a boss-tier entity without
    a usable prompt (no boss section) is the envelope 422."""
    campaign_id = media_api()
    boss_id = _commit_boss(
        campaign_id,
        data={"name": "Vashka", "role": "BBEG", "appearance": {"face": "iron"}},
    )
    response = client.post(
        "/api/jobs",
        json={"campaign_id": campaign_id, "kind": "video", "payload": {"entity_id": boss_id}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_video_file_route_foreign_campaign_is_404(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FOREIGN_CAMPAIGN on a video file: another DM's campaign with a
    REAL row + file on disk is still the indistinguishable 404 (AD-9)."""
    from app.store import add_media, create_campaign, register_account

    campaign_id = media_api()
    entity_id = _commit_boss(campaign_id)
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _enqueue_video(client, campaign_id, entity_id)
    _run_video_job()
    filename = client.get(f"/api/campaigns/{campaign_id}/media").json()["media"][0]["filename"]
    other_id = create_campaign(
        register_account(f"foreign-video-{ids.new_id()}@example.com", "password123").id,
        title="Other",
        description="",
        theme="Grimdark",
        custom_lore="",
    ).id
    foreign_entity_id = ids.new_id()
    foreign_anchor = ids.new_id()
    commit_subgraph(
        other_id,
        [
            models.EntityInput(kind="place", name="Anchor", id=foreign_anchor),
            models.EntityInput(
                kind="character",
                name="Foreign Vashka",
                data={
                    "name": "Foreign Vashka",
                    "role": "BBEG",
                    "appearance": {"face": "iron"},
                    "boss": {"lair_actions": "walls breathe"},
                },
                id=foreign_entity_id,
            ),
        ],
        [models.EdgeInput(src=foreign_anchor, dst=foreign_entity_id, type="located_in", counter=1)],
    )
    add_media(other_id, foreign_entity_id, filename, "video")
    foreign_file = tmp_path / "media" / other_id / foreign_entity_id / filename
    foreign_file.parent.mkdir(parents=True, exist_ok=True)
    foreign_file.write_bytes(MP4_BYTES)
    assert (
        client.get(f"/api/campaigns/{other_id}/media/{foreign_entity_id}/{filename}").status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Spec-4.3: the single-portrait DELETE
# ---------------------------------------------------------------------------


def _world_facts(campaign_id: str) -> tuple[int, int]:
    """(revision-chain length, live entity count) — a media delete moves
    neither: media rows are not world graph (AD-1/AD-10)."""
    with session_scope() as session:
        revisions = len(list(revision_chain(session, campaign_id)))
        entities, _edges = world_state(session, campaign_id)
    return revisions, len(entities)


def _portrait_row(client: TestClient, campaign_id: str) -> dict[str, Any]:
    """The single manifest row of a campaign that has exactly one."""
    rows = client.get(f"/api/campaigns/{campaign_id}/media").json()["media"]
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


def _media_refs(export: dict[str, Any], entity_id: str) -> list[dict[str, Any]]:
    """The export's media references for one entity (spec-4.3/FR14)."""
    entity = [e for e in export["entities"] if e["id"] == entity_id][0]
    refs = entity["media"]
    assert isinstance(refs, list)
    return refs


def test_delete_media_removes_only_the_named_row_and_unlinks_its_file(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HAPPY_PATH: named by its manifest ROW id, one portrait row goes and
    its file is unlinked; a second row of the SAME entity and its file
    survive, and the campaign's revision chain and entity count are
    untouched (media are not world graph)."""
    campaign_id = media_api()
    entity_id = _commit_entity(campaign_id, {"face": "sharp features"})
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _enqueue_image(client, campaign_id, entity_id)
    _run_portrait_job()
    row = _portrait_row(client, campaign_id)
    # A second manifest row for the same entity (a fresh portrait): the
    # delete names ONE row and must leave the other row and file alone.
    second_file = tmp_path / "media" / campaign_id / entity_id / f"{ids.new_id()}.png"
    second_file.write_bytes(PNG_BYTES)
    second = add_media(campaign_id, entity_id, second_file.name, "image")
    first_file = tmp_path / "media" / campaign_id / entity_id / row["filename"]
    assert first_file.is_file() and second_file.is_file()
    facts_before = _world_facts(campaign_id)

    response = client.delete(f"/api/campaigns/{campaign_id}/entities/{entity_id}/media/{row['id']}")

    assert response.status_code == 204
    assert response.content == b""
    remaining = client.get(f"/api/campaigns/{campaign_id}/media").json()["media"]
    assert [item["id"] for item in remaining] == [second.id]
    assert not first_file.exists()
    assert second_file.is_file()
    # The file is gone from disk AND the row from the manifest — the
    # served-file route is the same 404 either way.
    assert (
        client.get(f"/api/campaigns/{campaign_id}/media/{entity_id}/{row['filename']}").status_code
        == 404
    )
    # Not world graph: no revision, no entity change, and the entity
    # survives its portrait (regeneration is the recovery, undo is not).
    assert _world_facts(campaign_id) == facts_before
    export = client.get(f"/api/campaigns/{campaign_id}/export").json()
    assert entity_id in {entity["id"] for entity in export["entities"]}
    assert [ref["id"] for ref in _media_refs(export, entity_id)] == [second.id]


def test_delete_media_missing_file_is_still_204(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file is already gone (or never landed): the row delete is still
    a 204 — reclaiming a missing file is a silent no-op, never an error."""
    campaign_id = media_api()
    entity_id = _commit_entity(campaign_id, {"face": "sharp"})
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _enqueue_image(client, campaign_id, entity_id)
    _run_portrait_job()
    row = _portrait_row(client, campaign_id)
    (tmp_path / "media" / campaign_id / entity_id / row["filename"]).unlink()

    response = client.delete(f"/api/campaigns/{campaign_id}/entities/{entity_id}/media/{row['id']}")

    assert response.status_code == 204
    assert client.get(f"/api/campaigns/{campaign_id}/media").json()["media"] == []


def test_delete_media_unknown_row_id_is_404(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown media id — and an id belonging to ANOTHER entity of the
    same campaign — is the same 404; nothing is removed."""
    campaign_id = media_api()
    entity_id = _commit_entity(campaign_id, {"face": "sharp"})
    other_entity = _commit_entity(campaign_id, {"face": "other"}, name="Other")
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _enqueue_image(client, campaign_id, entity_id)
    _run_portrait_job()
    row = _portrait_row(client, campaign_id)
    file_on_disk = tmp_path / "media" / campaign_id / entity_id / row["filename"]
    assert file_on_disk.is_file()

    fabricated = client.delete(
        f"/api/campaigns/{campaign_id}/entities/{entity_id}/media/{ids.new_id()}"
    )
    wrong_entity = client.delete(
        f"/api/campaigns/{campaign_id}/entities/{other_entity}/media/{row['id']}"
    )

    assert fabricated.status_code == 404
    assert wrong_entity.status_code == 404
    assert fabricated.json()["code"] == "not_found"
    # Both misses are the same envelope code; the message echoes only the
    # ids the caller already supplied (no oracle).
    assert wrong_entity.json()["code"] == fabricated.json()["code"]
    assert wrong_entity.json()["message"].startswith("media not found:")
    remaining = client.get(f"/api/campaigns/{campaign_id}/media").json()["media"]
    assert [item["id"] for item in remaining] == [row["id"]]
    assert file_on_disk.is_file()


def test_delete_media_foreign_or_unknown_campaign_is_404(
    client: TestClient,
    media_api: Callable[[], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FOREIGN_CAMPAIGN: another DM's campaign — with a REAL row and file
    on disk — is the single indistinguishable 404 (AD-9); ownership
    precedes even a resolvable row, and the foreign row survives."""
    campaign_id = media_api()
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    other_id = create_campaign(
        register_account(f"foreign-del-{ids.new_id()}@example.com", "password123").id,
        title="Foreign",
        description="",
        theme="Grimdark",
        custom_lore="",
    ).id
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
    foreign_row = add_media(other_id, foreign_entity_id, f"{ids.new_id()}.png", "image")
    foreign_file = tmp_path / "media" / other_id / foreign_entity_id / foreign_row.filename
    foreign_file.parent.mkdir(parents=True, exist_ok=True)
    foreign_file.write_bytes(PNG_BYTES)

    foreign = client.delete(
        f"/api/campaigns/{other_id}/entities/{foreign_entity_id}/media/{foreign_row.id}"
    )
    unknown = client.delete(
        f"/api/campaigns/{ids.new_id()}/entities/{foreign_entity_id}/media/{foreign_row.id}"
    )

    assert foreign.status_code == 404
    assert unknown.status_code == 404
    assert foreign.json() == unknown.json()  # indistinguishable (no oracle)
    # The foreign row and file survive, and the owner's own campaign is
    # unaffected (no cross-campaign reach).
    assert foreign_file.is_file()
    from app.store import list_media

    assert [item.id for item in list_media(other_id)] == [foreign_row.id]
    assert client.get(f"/api/campaigns/{campaign_id}/media").status_code == 200
