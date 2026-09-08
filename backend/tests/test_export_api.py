"""World-state export API tests (FR5, AD-11; spec-2.6): the I/O matrix.

Covers: happy JSON + Markdown, empty world, foreign/unknown 404
(no-oracle), unauthenticated 401, the read-only invariant (export never
writes a revision or event), byte-identical repeated exports, stat-block
passthrough in both projections, invalid format 422, the multi-revision
head exposure (the 2.5 base_revision deferral closure), and non-finite
float coercion (a pure read endpoint never 500s on its own data).
"""

import json as _json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core import time
from app.core.ids import new_id
from app.store import add_media, models
from app.store.commit import commit_subgraph
from app.store.db import session_scope


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    """An https TestClient over the real app, with a FRESH scratch DB per
    test — the same isolation as the campaigns API tests."""
    from app.main import app
    from app.store import app_db_url, init_db

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'export-api.db'}")
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


def _create_campaign(client: Any) -> Any:
    return client.post(
        "/api/campaigns",
        json={
            "title": "Aetheria",
            "description": "A living world",
            "theme": "High Fantasy",
            "custom_lore": "The old gods stir",
        },
    )


_VEX_STAT_BLOCK = {"hp": 42, "ac": 17}


def _commit_world(campaign_id: str) -> models.Revision:
    """Two entities (one carrying a stat block) joined by debt + member_of
    edges — one countered semantic, one neutral."""
    vex_id, guild_id = new_id(), new_id()
    return commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=vex_id,
                kind="character",
                name="Vex",
                text="A rogue with a ledger.",
                data={"stat_block": _VEX_STAT_BLOCK},
            ),
            models.EntityInput(id=guild_id, kind="faction", name="The Guild"),
        ],
        edges=[
            models.EdgeInput(src=vex_id, dst=guild_id, type="debt", counter=50),
            models.EdgeInput(src=vex_id, dst=guild_id, type="member_of", counter=1),
        ],
    )


def _commit_second_wave(campaign_id: str, anchor_id: str, *, base_revision: str) -> models.Revision:
    """A second revision: one more entity edge-anchored into existing state.
    The base is explicit — a staged commit against an existing head is
    opt-in optimistic concurrency (AD-2)."""
    mira_id = new_id()
    return commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(id=mira_id, kind="character", name="Mira"),
        ],
        edges=[models.EdgeInput(src=anchor_id, dst=mira_id, type="debt", counter=10)],
        base_revision=base_revision,
    )


def _counts(campaign_id: str) -> tuple[int, int]:
    """The campaign's (revision, event) row counts — the read-only probe."""
    with session_scope() as session:
        revisions = session.scalar(
            select(func.count())
            .select_from(models.Revision)
            .where(models.Revision.campaign_id == campaign_id)
        )
        events = session.scalar(
            select(func.count())
            .select_from(models.Event)
            .where(models.Event.campaign_id == campaign_id)
        )
        assert revisions is not None and events is not None
        return revisions, events


def _parse_frontmatter(md: str) -> dict[str, Any]:
    """Round-trip parse of the exported frontmatter: quoted scalars are
    JSON strings (the exporter's own escaping contract — a JSON string is
    a valid YAML double-quoted scalar), revision/campaign_id are bare ids,
    null is JSON null. No yaml dependency needed."""
    assert md.startswith("---\n")
    end = md.index("\n---\n", 4)
    fields: dict[str, Any] = {}
    for line in md[4:end].splitlines():
        key, sep, raw = line.partition(": ")
        assert sep, line
        if raw == "null":
            fields[key] = None
        elif raw.startswith('"'):
            fields[key] = _json.loads(raw)
        else:
            fields[key] = raw
    return fields


def _fences(md: str) -> list[Any]:
    """Every yaml fence parsed back — JSON is a valid YAML flow subset,
    so json.loads is the validity check (fences may outgrow ```)."""
    return [
        _json.loads(match.group(1))
        for match in re.finditer(r"`{3,}yaml\n(.*?)\n`{3,}", md, re.DOTALL)
    ]


def test_export_requires_auth(client: Any) -> None:
    response = client.get("/api/campaigns/whatever/export")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_export_json_happy(client: Any) -> None:
    _register_login(client)
    campaign = _create_campaign(client).json()
    revision = _commit_world(campaign["id"])
    response = client.get(f"/api/campaigns/{campaign['id']}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["campaign"] == {
        "id": campaign["id"],
        "title": "Aetheria",
        "theme": "High Fantasy",
        "description": "A living world",
        "custom_lore": "The old gods stir",
        "created_at": campaign["created_at"],
    }
    # The 2.5 deferral closed: the latest revision's id + created_at ride along.
    assert body["revision"] == {"id": revision.id, "created_at": revision.created_at}
    assert [(e["kind"], e["name"]) for e in body["entities"]] == [
        ("character", "Vex"),
        ("faction", "The Guild"),
    ]
    assert body["entities"][0]["text"] == "A rogue with a ledger."
    assert body["edges"] == [
        {
            "id": body["edges"][0]["id"],
            "src": body["entities"][0]["id"],
            "dst": body["entities"][1]["id"],
            "type": "debt",
            "counter": 50,
        },
        {
            "id": body["edges"][1]["id"],
            "src": body["entities"][0]["id"],
            "dst": body["entities"][1]["id"],
            "type": "member_of",
            "counter": 1,
        },
    ]


def test_export_markdown_happy(client: Any) -> None:
    _register_login(client)
    campaign = _create_campaign(client).json()
    revision = _commit_world(campaign["id"])
    response = client.get(f"/api/campaigns/{campaign['id']}/export", params={"format": "markdown"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    # Frontmatter parses back: campaign identity (id + seed incl.
    # custom_lore), revision, snapshot timestamp.
    front = _parse_frontmatter(body)
    assert front == {
        "campaign_id": campaign["id"],
        "title": "Aetheria",
        "theme": "High Fantasy",
        "description": "A living world",
        "custom_lore": "The old gods stir",
        "revision": revision.id,
        "exported_at": revision.created_at,
    }
    # One section per entity, kind + text + full data in a parseable fence.
    assert "## Vex" in body
    assert "## The Guild" in body
    assert "kind: character" in body
    assert "A rogue with a ledger." in body
    assert _fences(body) == [{"stat_block": _VEX_STAT_BLOCK}, {}]
    # Typed edges appear from both endpoints, wikilinked; per-type counter
    # semantics — debt shows the amount, the neutral member_of renders bare.
    assert "[[Vex]] --debt(50)--> [[The Guild]]" in body
    assert "[[The Guild]] <--debt(50)-- [[Vex]]" in body
    assert "[[Vex]] --member_of--> [[The Guild]]" in body
    assert "[[The Guild]] <--member_of-- [[Vex]]" in body
    # World-level edge table keeps every counter.
    assert "| [[Vex]] | debt | 50 | [[The Guild]] |" in body
    assert "| [[Vex]] | member_of | 1 | [[The Guild]] |" in body


def test_export_empty_world(client: Any) -> None:
    _register_login(client)
    campaign = _create_campaign(client).json()
    json_response = client.get(f"/api/campaigns/{campaign['id']}/export")
    assert json_response.status_code == 200
    body = json_response.json()
    assert body["revision"] is None
    assert body["entities"] == []
    assert body["edges"] == []
    md_response = client.get(
        f"/api/campaigns/{campaign['id']}/export", params={"format": "markdown"}
    )
    assert md_response.status_code == 200
    md = md_response.text
    # Frontmatter parses back; exported_at falls back to the campaign's
    # creation for an empty world (the byte-identical guarantee's basis).
    front = _parse_frontmatter(md)
    assert front["revision"] is None
    assert front["exported_at"] == campaign["created_at"]
    assert "## " not in md  # no entity sections, no edge table
    assert "### Relations" not in md


def test_export_foreign_404_identical_to_unknown(client: Any) -> None:
    _register_login(client, "other@example.com")
    theirs = _create_campaign(client).json()["id"]
    _register_login(client, "dm@example.com")  # switches the session cookie
    foreign = client.get(f"/api/campaigns/{theirs}/export")
    unknown = client.get(f"/api/campaigns/{new_id()}/export")
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["code"] == "not_found"
    assert foreign.json() == unknown.json()  # indistinguishable


def test_export_read_only_invariant(client: Any) -> None:
    _register_login(client)
    campaign = _create_campaign(client).json()
    campaign_id = campaign["id"]
    _commit_world(campaign_id)
    before = _counts(campaign_id)
    assert before[0] > 0  # the commit produced revisions and events
    json_response = client.get(f"/api/campaigns/{campaign_id}/export")
    md_response = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "markdown"})
    assert json_response.status_code == md_response.status_code == 200
    assert _counts(campaign_id) == before  # no revision, no event (AR18)


def test_export_determinism(client: Any) -> None:
    _register_login(client)
    campaign = _create_campaign(client).json()
    _commit_world(campaign["id"])
    first_md = client.get(f"/api/campaigns/{campaign['id']}/export", params={"format": "markdown"})
    second_md = client.get(f"/api/campaigns/{campaign['id']}/export", params={"format": "markdown"})
    assert first_md.content == second_md.content  # byte-identical
    first_json = client.get(f"/api/campaigns/{campaign['id']}/export")
    second_json = client.get(f"/api/campaigns/{campaign['id']}/export")
    assert first_json.content == second_json.content


def test_export_stat_block_passthrough(client: Any) -> None:
    """STAT_BLOCK_PRESENT: the stat block serializes verbatim in both
    projections — export is a projection, never a re-validation."""
    _register_login(client)
    campaign = _create_campaign(client).json()
    _commit_world(campaign["id"])
    body = client.get(f"/api/campaigns/{campaign['id']}/export").json()
    assert body["entities"][0]["data"] == {"stat_block": _VEX_STAT_BLOCK}
    md = client.get(f"/api/campaigns/{campaign['id']}/export", params={"format": "markdown"}).text
    fence = re.search(r"`{3,}yaml\n(.*?)\n`{3,}", md, re.DOTALL)
    assert fence is not None
    assert '"stat_block": {' in fence.group(1)
    assert '"hp": 42' in fence.group(1)
    assert '"ac": 17' in fence.group(1)


def test_export_invalid_format_422(client: Any) -> None:
    """The only parameterized surface pins its rejection shape."""
    _register_login(client)
    campaign = _create_campaign(client).json()
    response = client.get(f"/api/campaigns/{campaign['id']}/export", params={"format": "xml"})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_export_multi_revision_exposes_head(client: Any) -> None:
    """The exposed revision id is the HEAD across revisions, and it is a
    usable base_revision — the 2.5 deferral closure, end to end."""
    _register_login(client)
    campaign = _create_campaign(client).json()
    campaign_id = campaign["id"]
    first_revision = _commit_world(campaign_id)
    first = client.get(f"/api/campaigns/{campaign_id}/export").json()
    anchor_id = first["entities"][0]["id"]  # Vex
    second = _commit_second_wave(campaign_id, anchor_id, base_revision=first_revision.id)
    body = client.get(f"/api/campaigns/{campaign_id}/export").json()
    assert body["revision"] == {"id": second.id, "created_at": second.created_at}
    # The exposed id drives the DELETE optimistic-concurrency flow.
    response = client.request(
        "DELETE",
        f"/api/campaigns/{campaign_id}/entities/{anchor_id}",
        json={"cascade": True, "confirm": True, "base_revision": second.id},
    )
    assert response.status_code == 204


def test_export_non_finite_floats_never_500(client: Any) -> None:
    """Non-finite floats (NaN/inf round-trip through the JSON column)
    coerce to null at any nesting depth — a pure read endpoint never
    fails on its own data."""
    _register_login(client)
    campaign = _create_campaign(client).json()
    campaign_id = campaign["id"]
    probe_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=probe_id,
                kind="character",
                name="Probe",
                data={
                    "probe": float("nan"),
                    "inf": float("inf"),
                    "stat_block": {"hp": float("nan")},
                    "note": [float("inf")],
                },
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Anchor"),
        ],
        edges=[models.EdgeInput(src=probe_id, dst=anchor_id, type="located_in", counter=1)],
    )
    body = client.get(f"/api/campaigns/{campaign_id}/export").json()
    assert body["entities"][0]["data"] == {
        "probe": None,
        "inf": None,
        "stat_block": {"hp": None},
        "note": [None],
    }
    md = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "markdown"}).text
    assert _fences(md) == [
        {"probe": None, "inf": None, "stat_block": {"hp": None}, "note": [None]},
        {},
    ]
    # The neutral located_in renders bare in Relations (second neutral type).
    assert "[[Probe]] --located_in--> [[Anchor]]" in md


def test_export_fence_grows_with_backticks(client: Any) -> None:
    """The yaml fence outgrows any backtick run inside the payload: data
    containing ``` must never terminate the fence early (review patch P3
    — a constant 3-backtick fence passes every other assertion while
    corrupting real exports)."""
    _register_login(client)
    campaign = _create_campaign(client).json()
    campaign_id = campaign["id"]
    probe_id, anchor_id = new_id(), new_id()
    payload = {"snippet": "```yaml\ntricky: true\n````"}
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(id=probe_id, kind="character", name="Probe", data=payload),
            models.EntityInput(id=anchor_id, kind="place", name="Anchor"),
        ],
        edges=[models.EdgeInput(src=probe_id, dst=anchor_id, type="located_in", counter=1)],
    )
    md = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "markdown"}).text
    # Longest backtick run in the payload is 4, so the opening fence is 5.
    assert re.search(r"`{5}yaml", md)
    assert _fences(md) == [payload, {}]


def test_export_campaign_deleted_mid_request_404s(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-transaction campaign re-check is load-bearing: a campaign
    deleted between the ownership check and the snapshot transaction must
    still 404, not produce a phantom empty-world 200 (review patch P4)."""
    _register_login(client)
    campaign = _create_campaign(client).json()
    campaign_id = campaign["id"]
    from app.api import exports as exports_module
    from app.store import get_campaign as real_get_campaign

    def get_campaign_then_delete(owner_id: str, cid: str) -> Any:
        found = real_get_campaign(owner_id, cid)
        if found is not None:
            # Simulate the concurrent delete: the real DELETE route (AR20)
            # runs after the ownership check has already passed.
            client.request("DELETE", f"/api/campaigns/{cid}", json={"confirm": True})
        return found

    monkeypatch.setattr(exports_module, "get_campaign", get_campaign_then_delete)
    response = client.get(f"/api/campaigns/{campaign_id}/export")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_export_markdown_label_uniqueness(client: Any) -> None:
    """Post-sanitization label uniqueness (review patch P1): distinct
    names can sanitize to one label, an all-unsafe name goes blank, and
    structural headings are reserved — every emitted heading is unique,
    duplicates grow ULID discriminators, and wikilinks match headings."""
    _register_login(client)
    campaign = _create_campaign(client).json()
    campaign_id = campaign["id"]
    vex_a, vex_b, ab1, ab2, edges_named, all_unsafe = (
        new_id(),
        new_id(),
        new_id(),
        new_id(),
        new_id(),
        new_id(),
    )
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(id=vex_a, kind="character", name="Vex"),
            models.EntityInput(id=vex_b, kind="character", name="Vex"),
            models.EntityInput(id=ab1, kind="place", name="A[B"),
            models.EntityInput(id=ab2, kind="place", name="A]B"),
            models.EntityInput(id=edges_named, kind="place", name="Edges"),
            models.EntityInput(id=all_unsafe, kind="place", name="[[[#]"),
        ],
        edges=[
            models.EdgeInput(src=vex_a, dst=vex_b, type="debt", counter=50),
            models.EdgeInput(src=ab1, dst=ab2, type="located_in", counter=1),
            models.EdgeInput(src=ab2, dst=vex_a, type="located_in", counter=1),
            models.EdgeInput(src=edges_named, dst=vex_a, type="located_in", counter=1),
            models.EdgeInput(src=all_unsafe, dst=ab1, type="located_in", counter=1),
        ],
    )
    md = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "markdown"}).text
    headings = re.findall(r"^## (.+)$", md, re.MULTILINE)
    # Every heading unique — the sanitized "A B" collision got a
    # discriminator, the all-unsafe name fell back to its ULID.
    assert len(headings) == len(set(headings))
    assert headings.count("Edges") == 1  # structural heading not shadowed
    vexes = [h for h in headings if h.startswith("Vex (")]
    assert len(vexes) == 2 and vexes[0][-4:] != vexes[1][-4:]
    sanitized_collide = [h for h in headings if h.startswith("A B")]
    assert len(sanitized_collide) == 2
    # Wikilinks use the same labels as headings.
    assert f"[[{vexes[0]}]] --debt(50)--> [[{vexes[1]}]]" in md


def _write_media_file(tmp_path: Path, campaign_id: str, row: models.Media) -> None:
    """The file on disk for a manifest row, under the env-pinned media root."""
    path = tmp_path / "media" / campaign_id / row.entity_id / row.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"payload")


def test_export_media_refs_flag_disk_presence(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EXPORT_VALID_REF / EXPORT_BROKEN_REF (spec-4.3, FR14): each
    entity's manifest rows ride the export in rowid order with
    ``available`` resolved against disk — a broken reference is flagged,
    never dropped; markdown lists valid files unflagged and marks the
    broken one."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign = _create_campaign(client).json()
    campaign_id = campaign["id"]
    _commit_world(campaign_id)
    body = client.get(f"/api/campaigns/{campaign_id}/export").json()
    vex_id, guild_id = body["entities"][0]["id"], body["entities"][1]["id"]

    portrait = add_media(campaign_id, vex_id, f"{new_id()}.png", "image")
    clip = add_media(campaign_id, vex_id, f"{new_id()}.mp4", "video")
    broken = add_media(campaign_id, vex_id, f"{new_id()}.png", "image")
    guild_portrait = add_media(campaign_id, guild_id, f"{new_id()}.png", "image")
    for row in (portrait, clip, guild_portrait):
        _write_media_file(tmp_path, campaign_id, row)

    data = client.get(f"/api/campaigns/{campaign_id}/export").json()
    vex_media = data["entities"][0]["media"]
    assert [m["filename"] for m in vex_media] == [
        portrait.filename,
        clip.filename,
        broken.filename,
    ]
    assert [m["kind"] for m in vex_media] == ["image", "video", "image"]
    assert [m["available"] for m in vex_media] == [True, True, False]
    guild_media = data["entities"][1]["media"]
    assert [m["available"] for m in guild_media] == [True]

    md = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "markdown"}).text
    assert md.count("### Media") == 2
    assert f"- image: {portrait.filename}" in md
    assert f"- video: {clip.filename}" in md
    assert f"- image: {broken.filename} (broken: file missing on disk)" in md
    assert f"- image: {guild_portrait.filename}" in md


def test_export_without_media_lists_nothing(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EXPORT_NO_MEDIA: entities with no manifest rows export an empty
    media array and the markdown carries no Media block."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign = _create_campaign(client).json()
    _commit_world(campaign["id"])
    data = client.get(f"/api/campaigns/{campaign['id']}/export").json()
    assert all(e["media"] == [] for e in data["entities"])
    md = client.get(f"/api/campaigns/{campaign['id']}/export", params={"format": "markdown"}).text
    assert "### Media" not in md


def test_export_ships_no_orphan_media_rows(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EXPORT_ORPHAN_LEGACY: a pre-4.3 orphan row (its entity is gone)
    belongs to no listed entity — never shipped; the row itself persists
    (no sweep of legacy rows in this story)."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign = _create_campaign(client).json()
    campaign_id = campaign["id"]
    _commit_world(campaign_id)
    orphan = models.Media(
        id=new_id(),
        campaign_id=campaign_id,
        entity_id=new_id(),  # no such entity
        filename=f"{new_id()}.png",
        kind="image",
        created_at=time.now(),
    )
    with session_scope() as session:
        session.add(orphan)
    data = client.get(f"/api/campaigns/{campaign_id}/export").json()
    shipped = {m["filename"] for e in data["entities"] for m in e["media"]}
    assert orphan.filename not in shipped
    with session_scope() as session:
        assert session.get(models.Media, orphan.id) is not None  # row persists


def test_export_determinism_includes_media(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The byte-identical guarantee holds with the media field: a fixed
    world + media store yields identical repeated exports (changing a
    file changes ``available`` — that is the flag's purpose)."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign = _create_campaign(client).json()
    campaign_id = campaign["id"]
    _commit_world(campaign_id)
    body = client.get(f"/api/campaigns/{campaign_id}/export").json()
    vex_id = body["entities"][0]["id"]
    row = add_media(campaign_id, vex_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, row)
    first_json = client.get(f"/api/campaigns/{campaign_id}/export")
    second_json = client.get(f"/api/campaigns/{campaign_id}/export")
    assert first_json.content == second_json.content
    first_md = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "markdown"})
    second_md = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "markdown"})
    assert first_md.content == second_md.content
