"""World-state export API tests (FR5, AD-11; spec-2.6): the I/O matrix.

Covers: happy JSON + Markdown, empty world, foreign/unknown 404
(no-oracle), unauthenticated 401, the read-only invariant (export never
writes a revision or event), byte-identical repeated exports, stat-block
passthrough in both projections, invalid format 422, the multi-revision
head exposure (the 2.5 base_revision deferral closure), and non-finite
float coercion (a pure read endpoint never 500s on its own data).
"""

import base64
import hashlib
import io
import json as _json
import logging
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core import time
from app.core.ids import new_id
from app.media.service import PNG_SIGNATURE
from app.store import add_media, models, prune_entity_media
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
            models.EdgeInput(src=vex_id, dst=guild_id, type="debt", counter=50, reason="seeded"),
            models.EdgeInput(
                src=vex_id, dst=guild_id, type="member_of", counter=1, reason="seeded"
            ),
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
        edges=[
            models.EdgeInput(src=anchor_id, dst=mira_id, type="debt", counter=10, reason="seeded")
        ],
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
        "is_generic": False,
        "created_at": campaign["created_at"],
    }
    # The 2.5 deferral closed: the latest revision's id + created_at ride along.
    assert body["revision"] == {"id": revision.id, "created_at": revision.created_at}
    assert [(e["kind"], e["name"]) for e in body["entities"]] == [
        ("character", "Vex"),
        ("faction", "The Guild"),
    ]
    assert body["entities"][0]["text"] == "A rogue with a ledger."
    # AD-32: the export carries the saved why — NULL here would be a
    # pre-v3 row, but these edges were committed through the current
    # contract, so the saved why rides verbatim.
    assert body["edges"] == [
        {
            "id": body["edges"][0]["id"],
            "src": body["entities"][0]["id"],
            "dst": body["entities"][1]["id"],
            "type": "debt",
            "counter": 50,
            "reason": "seeded",
        },
        {
            "id": body["edges"][1]["id"],
            "src": body["entities"][0]["id"],
            "dst": body["entities"][1]["id"],
            "type": "member_of",
            "counter": 1,
            "reason": "seeded",
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
    html_response = client.get(f"/api/campaigns/{campaign['id']}/export", params={"format": "html"})
    assert html_response.status_code == 200
    assert "revision: none" in html_response.text  # the empty-world branch of _revision_meta


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
        edges=[
            models.EdgeInput(
                src=probe_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
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
    probe_json = client.get(f"/api/campaigns/{campaign_id}/entities/{probe_id}/export").json()
    assert probe_json["entity"]["data"]["probe"] is None
    probe_html = client.get(
        f"/api/campaigns/{campaign_id}/entities/{probe_id}/export", params={"format": "html"}
    ).text
    assert '"probe": null' in probe_html  # the appendix carries the coerced record


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
        edges=[
            models.EdgeInput(
                src=probe_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
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
    # The entity route shares the guard: the same race must yield the same
    # indistinguishable 404 envelope, not an AttributeError 500.
    second = _create_campaign(client).json()["id"]
    entity_response = client.get(f"/api/campaigns/{second}/entities/{new_id()}/export")
    assert entity_response.status_code == 404
    assert entity_response.json() == response.json()


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
            models.EdgeInput(src=vex_a, dst=vex_b, type="debt", counter=50, reason="seeded"),
            # located_in: character -> place (AD-31).
            models.EdgeInput(src=ab2, dst=ab1, type="located_in", counter=1, reason="seeded"),
            models.EdgeInput(src=vex_a, dst=ab2, type="located_in", counter=1, reason="seeded"),
            models.EdgeInput(
                src=vex_a, dst=edges_named, type="located_in", counter=1, reason="seeded"
            ),
            models.EdgeInput(src=vex_b, dst=ab1, type="located_in", counter=1, reason="seeded"),
            models.EdgeInput(
                src=vex_a, dst=all_unsafe, type="located_in", counter=1, reason="seeded"
            ),
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


def test_export_refers_newest_available_after_keep5_prune(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KEEP-5 retention (epic-4 retro item 13, owner ruling 2026-09-10):
    after a 6th portrait prunes the oldest row (the runner's post-commit
    step), the export still references the NEWEST available rows — the
    hero rule over the bounded history: the pruned row never rides the
    export, every remaining reference is available."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign_id, vespera_id = _sheet_world(client)
    rows = [add_media(campaign_id, vespera_id, f"{new_id()}.png", "image") for _ in range(6)]
    for row in rows:
        _write_media_file(tmp_path, campaign_id, row)
    pruned = prune_entity_media(campaign_id, vespera_id)
    assert [r.id for r in pruned] == [rows[0].id]  # exactly the oldest row

    data = client.get(f"/api/campaigns/{campaign_id}/export").json()
    vespera_media = next(
        entity["media"] for entity in data["entities"] if entity["id"] == vespera_id
    )
    # rowid (insertion) order within the kept five: the newest row rides
    # the export, the pruned oldest is gone, and every kept file is
    # present on disk.
    assert [m["filename"] for m in vespera_media] == [row.filename for row in rows[1:]]
    assert [m["available"] for m in vespera_media] == [True] * 5


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


# ---------------------------------------------------------------------------
# spec-5.1: entity-level export + styled HTML sheets
# ---------------------------------------------------------------------------

_VESPERA_DATA: dict[str, Any] = {
    "name": "Vespera",
    "role": "BBEG",
    "level_cr": "level 12",
    "race_type": "Tiefling",
    "class_profession": "Warlock",
    "alignment": "CE",
    "appearance": "Horned silhouette, ember-lit.",
    "secret": "The guild's founder lives on in her ledger.",
    "stat_block": {
        "identity": {"role": "BBEG", "race": "Tiefling", "level": 12},
        "attributes": {"str": 12, "dex": 16, "con": 14, "int": 18, "wis": 11, "cha": 20},
        "combat": {"armor_class": 16, "hit_points": 99, "speed": "30 ft."},
        "skills": [{"name": "Deception", "bonus": 8}],
        "actions": [{"name": "Ember Lance", "description": "Ranged spell attack, 2d6 & fire <b>."}],
        "traits": [],
        "spells": [],
    },
    # Unknown forward-compat key (AR24): must survive verbatim everywhere.
    "widget_config": {"nested": [1, 2, {"deep": True}]},
}


def _commit_vespera(campaign_id: str, guild_id: str, base_revision: str) -> str:
    """An AR24-ish BBEG sheet fixture: Vespera owes the guild. The base is
    explicit — a second graph commit onto an existing head is opt-in
    optimistic concurrency (AD-2)."""
    vespera_id = new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=vespera_id,
                kind="character",
                name="Vespera",
                text="The ember in the ledger.",
                data=dict(_VESPERA_DATA),
            )
        ],
        edges=[
            models.EdgeInput(src=vespera_id, dst=guild_id, type="debt", counter=5, reason="seeded"),
            models.EdgeInput(
                src=guild_id, dst=vespera_id, type="ally_of", counter=3, reason="seeded"
            ),
        ],
        base_revision=base_revision,
    )
    return vespera_id


def _sheet_world(client: Any) -> tuple[str, str]:
    """Campaign + Vex/guild + Vespera; returns (campaign_id, vespera_id)."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    revision = _commit_world(campaign_id)
    body = client.get(f"/api/campaigns/{campaign_id}/export").json()
    guild_id = body["entities"][1]["id"]
    return campaign_id, _commit_vespera(campaign_id, guild_id, revision.id)


def test_entity_export_json_projection(client: Any) -> None:
    """HAPPY entity (json): the entity, ONLY its touching edges (rowid
    order), and the revision head — the shared Epic 5 engine shape."""
    campaign_id, vespera_id = _sheet_world(client)
    response = client.get(
        f"/api/campaigns/{campaign_id}/entities/{vespera_id}/export", params={"format": "json"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["entity"]["id"] == vespera_id
    assert body["entity"]["name"] == "Vespera"
    assert body["entity"]["data"] == _VESPERA_DATA  # verbatim, unknown keys included
    world = client.get(f"/api/campaigns/{campaign_id}/export").json()
    assert body["edges"] == [e for e in world["edges"] if vespera_id in (e["src"], e["dst"])]
    assert body["revision"] == world["revision"]


def test_entity_export_markdown_pure(client: Any) -> None:
    """HAPPY entity (markdown): frontmatter parses, the full data fence
    round-trips, relations render with plain neighbor names (a standalone
    file has no wikilink targets), and NO inline <style> ships — the .md
    twin stays pure (obsidian.md/help/html rationale)."""
    campaign_id, vespera_id = _sheet_world(client)
    response = client.get(
        f"/api/campaigns/{campaign_id}/entities/{vespera_id}/export",
        params={"format": "markdown"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert re.search(r'filename="vespera-[0-9A-Z]{8}\.md"', response.headers["content-disposition"])
    md = response.text
    front = _parse_frontmatter(md)
    assert front["entity_id"] == vespera_id
    assert front["campaign_id"] == campaign_id
    assert front["name"] == "Vespera"
    assert "# Vespera" in md
    assert _fences(md)[0] == _VESPERA_DATA
    assert "- Vespera --debt(5)--> The Guild" in md
    assert "- The Guild <--ally_of(3)-- Vespera" in md  # inbound renders too
    assert "[[" not in md
    assert "<style" not in md


def test_entity_export_html_sheet(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HAPPY entity (html): self-contained sheet — embedded CSS, the
    portrait as an exact data URI, stat-block panel, verbatim JSON
    appendix; byte-identical repeats; read-only invariant (AR18)."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    campaign_id, vespera_id = _sheet_world(client)
    row = add_media(campaign_id, vespera_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, row)
    url = f"/api/campaigns/{campaign_id}/entities/{vespera_id}/export"
    first = client.get(url, params={"format": "html"})
    second = client.get(url, params={"format": "html"})
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/html")
    assert "attachment" in first.headers["content-disposition"]
    assert first.content == second.content  # byte-identical (determinism)
    html_body = first.text
    assert html_body.startswith("<!DOCTYPE html>")
    assert "@page" in html_body and "print-color-adjust: exact" in html_body  # embedded CSS
    assert f"data:image/png;base64,{base64.b64encode(b'payload').decode()}" in html_body
    # The portrait leads the sheet — before the prose — and the identity
    # 'name' key never duplicates the card heading.
    assert html_body.index("data:image") < html_body.index("The ember in the ledger")
    assert "<h3>Name</h3>" not in html_body
    assert "Stat Block" in html_body and "Ember Lance" in html_body  # panel + actions
    assert "widget_config" in html_body  # unknown key survives verbatim…
    assert '"deep": true' in html_body  # …inside the appendix JSON
    assert "Vespera --debt(5)--> The Guild" in html_body  # relations render
    assert "The Guild &lt;--ally_of(3)-- Vespera" in html_body
    assert "<details class='appendix'" in html_body
    before = _counts(campaign_id)
    client.get(url, params={"format": "html"})
    assert _counts(campaign_id) == before  # read-only (AR18)


def test_entity_export_html_broken_and_oversized_media(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BROKEN / HUGE media (matrix): a missing file is flagged, an image
    over the inline cap is captioned — neither is ever embedded, and
    neither is ever dropped."""
    from app.api.export_sheets import MAX_INLINE_BYTES

    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    campaign_id, vespera_id = _sheet_world(client)
    broken = add_media(campaign_id, vespera_id, f"{new_id()}.png", "image")  # never written
    huge = add_media(campaign_id, vespera_id, f"{new_id()}.png", "image")
    huge_path = tmp_path / "media" / campaign_id / vespera_id / huge.filename
    huge_path.parent.mkdir(parents=True, exist_ok=True)
    huge_path.write_bytes(b"x" * (MAX_INLINE_BYTES + 1))
    html_body = client.get(
        f"/api/campaigns/{campaign_id}/entities/{vespera_id}/export", params={"format": "html"}
    ).text
    assert "(broken: file missing on disk)</span>" in html_body
    assert broken.filename in html_body and huge.filename in html_body  # listed, never dropped
    # The only available image is the oversized one → nothing embeds.
    assert "data:image" not in html_body


def test_entity_export_html_escapes_injection(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Markup in committed data is escaped — a projection is not a script
    execution surface (the self-contained, no-script contract) — including
    attribute context: a quote in the name must not break out of alt=."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    victim_id, quote_id, anchor_id = new_id(), new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=victim_id,
                kind="character",
                name="<img src=x onerror=alert(1)>",
                data={"secret": "</h2><script>evil()</script>"},
            ),
            models.EntityInput(id=quote_id, kind="character", name='V" onload="alert(1)', data={}),
            models.EntityInput(id=anchor_id, kind="place", name="Anchor"),
        ],
        edges=[
            models.EdgeInput(
                src=victim_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            ),
            models.EdgeInput(
                src=quote_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            ),
        ],
    )
    row = add_media(campaign_id, quote_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, row)
    html_body = client.get(
        f"/api/campaigns/{campaign_id}/entities/{victim_id}/export", params={"format": "html"}
    ).text
    assert "<img src=x" not in html_body
    assert "<script>evil()" not in html_body
    assert "&lt;img src=x" in html_body and "&lt;/h2&gt;&lt;script&gt;" in html_body
    quoted = client.get(
        f"/api/campaigns/{campaign_id}/entities/{quote_id}/export", params={"format": "html"}
    ).text
    assert 'alt="V" onload=' not in quoted  # no attribute breakout…
    assert "&quot; onload=&quot;" in quoted  # …escaped inside alt


def test_entity_export_404_shapes_and_format(client: Any) -> None:
    """MISSING/FOREIGN (matrix) + BAD FORMAT 422 on the entity surface.
    (Unauthenticated 401 is pinned by the world route — same dependency.)"""
    _register_login(client, "other@example.com")
    theirs = _create_campaign(client).json()["id"]
    _register_login(client, "dm@example.com")
    foreign = client.get(f"/api/campaigns/{theirs}/entities/{new_id()}/export")
    unknown_campaign = client.get(f"/api/campaigns/{new_id()}/entities/{new_id()}/export")
    assert foreign.status_code == unknown_campaign.status_code == 404
    assert foreign.json() == unknown_campaign.json()  # indistinguishable (no oracle)
    assert foreign.json()["code"] == "not_found"
    campaign_id = _create_campaign(client).json()["id"]
    missing = client.get(f"/api/campaigns/{campaign_id}/entities/{new_id()}/export")
    assert missing.status_code == 404
    bad = client.get(
        f"/api/campaigns/{campaign_id}/entities/{new_id()}/export", params={"format": "pdf"}
    )
    assert bad.status_code == 422  # format validated before the entity lookup


def test_world_export_html_document(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HAPPY world html (matrix): styled attachment — cards for every
    entity, the edge table, media listed by path with availability — and
    NO embedded binaries at world level; deterministic bytes."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    _commit_world(campaign_id)
    body = client.get(f"/api/campaigns/{campaign_id}/export").json()
    vex_id = body["entities"][0]["id"]
    row = add_media(campaign_id, vex_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, row)
    first = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "html"})
    second = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "html"})
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/html")
    assert "aetheria-" in first.headers["content-disposition"]  # human-readable stem
    assert first.content == second.content
    doc = first.text
    assert "<!DOCTYPE html>" in doc and "@page" in doc
    assert "Vex" in doc and "The Guild" in doc
    assert "<th>counter</th>" in doc  # world edge table present
    assert row.filename in doc and f"media/{campaign_id}/{vex_id}" in doc  # path listed
    assert "data:image" not in doc  # no embedded binaries at world level
    assert "<details class='appendix'" not in doc  # the appendix is the sheet's job


def test_export_failure_is_logged_as_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RENDER FAILURE (matrix, FR18): a renderer raising is a commit-path
    regression — exactly one export_failure log event naming the surface,
    then the generic 500 envelope; world state untouched. Its own client:
    the generic 500 needs raise_server_exceptions=False (test_api.py
    pattern), and a second TestClient cannot nest on the shared loop."""
    from app.api import export_sheets
    from app.main import app
    from app.store import app_db_url, init_db

    def boom(export: Any, entity_id: str) -> str:
        raise RuntimeError("simulated renderer regression")

    monkeypatch.setattr(export_sheets, "render_entity_html", boom)
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'export-fail.db'}")
    try:
        with TestClient(
            app, base_url="https://testserver", raise_server_exceptions=False
        ) as boom_client:
            _register_login(boom_client)
            campaign_id = _create_campaign(boom_client).json()["id"]
            _commit_world(campaign_id)
            before = _counts(campaign_id)
            body = boom_client.get(f"/api/campaigns/{campaign_id}/export").json()
            vex_id = body["entities"][0]["id"]
            with caplog.at_level(logging.ERROR):
                response = boom_client.get(
                    f"/api/campaigns/{campaign_id}/entities/{vex_id}/export",
                    params={"format": "html"},
                )
            assert response.status_code == 500
            assert response.json()["code"] == "internal_error"
            events = [r for r in caplog.records if "export_failure" in r.getMessage()]
            assert len(events) == 1
            assert events[0].exc_info is not None  # the traceback rides the event
            message = events[0].getMessage()
            assert campaign_id in message and vex_id in message and "format=html" in message
            assert _counts(campaign_id) == before  # no state change from the failure
    finally:
        init_db(previous)


def test_entity_html_non_string_identity_still_visible(client: Any) -> None:
    """An identity-anchor value that is not a string (reachable via the
    hand-edit path) cannot ride the meta line — it must still appear as a
    body section, never vanish from the styled sheet."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    odd_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(id=odd_id, kind="character", name="Odd", data={"level_cr": 12}),
            models.EntityInput(id=anchor_id, kind="place", name="Anchor"),
        ],
        edges=[
            models.EdgeInput(
                src=odd_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    html_body = client.get(
        f"/api/campaigns/{campaign_id}/entities/{odd_id}/export", params={"format": "html"}
    ).text
    assert "<h3>Level cr</h3>" in html_body and ">12<" in html_body


def _sb_block() -> dict[str, Any]:
    return {
        "identity": {"role": "NPC", "race": "Human", "level": 1},
        "attributes": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        "combat": {"ac": 10, "hp": 1},
        "skills": [],
        "actions": [],
        "traits": [],
        "spells": [],
    }


def test_entity_html_stat_block_order_is_canonical(client: Any) -> None:
    """ORDER DRIFT (dogfood 2026-09-09): two characters whose committed
    stat_blocks differ ONLY in key order (model/repair variance) render the
    same sheet — the renderer owns display order; the JSON appendix keeps
    the committed shape verbatim, order included."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    a_id, b_id, anchor_id = new_id(), new_id(), new_id()
    block = _sb_block()
    shuffled = dict(reversed(list(block.items())))
    shuffled["attributes"] = dict(reversed(list(block["attributes"].items())))
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(id=a_id, kind="character", name="Aa", data={"stat_block": shuffled}),
            models.EntityInput(
                id=b_id, kind="character", name="Bb", data={"stat_block": dict(block)}
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Anchor"),
        ],
        edges=[
            models.EdgeInput(
                src=a_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            ),
            models.EdgeInput(
                src=b_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            ),
        ],
    )

    def sheet(entity_id: str) -> str:
        return str(
            client.get(
                f"/api/campaigns/{campaign_id}/entities/{entity_id}/export",
                params={"format": "html"},
            ).text
        )

    def panel(h: str) -> str:
        start = h.index('class="stat-block"')
        return h[start : h.index("</section>", start)]

    a, b = sheet(a_id), sheet(b_id)
    expected = ["Identity", "Combat", "Skills", "Actions", "Traits", "Spells"]
    assert re.findall(r"<h4>(.*?)</h4>", panel(a)) == expected
    assert re.findall(r"<h4>(.*?)</h4>", panel(b)) == expected
    # The abilities grid leads with STR even when cha was committed first.
    assert panel(a).index('ab-k">STR') < panel(a).index('ab-k">DEX') < panel(a).index('ab-k">CHA')
    # The appendix keeps the committed insertion order verbatim.
    assert a.index('"cha"') < a.index('"wis"') < a.index('"str"')


# ---------------------------------------------------------------------------
# spec-5-2: Owlbear/Forge export + signed portrait URLs
# ---------------------------------------------------------------------------

_FORGE_NS = "com.battle-system.forge"


def _fk(bid: str) -> str:
    """The extension-namespaced Forge metadata key for a BID."""
    return f"{_FORGE_NS}/{bid}"


_SERA_DATA: dict[str, Any] = {
    "name": "Sera",
    "role": "NPC",
    "level_cr": "level 5",
    "race_type": "Human",
    "class_profession": "Wizard",
    "alignment": "NG",
    "appearance": "Sharp-eyed scholar.",
    "personality": "Curious.",
    "secret": "Sold the map.",
    "rumor": "Seen at the docks.",
    "party_hook": "Ask about the map.",
    "equipment": [
        {"name": "Spellbook", "description": "Leather-bound, singed."},
        {"name": "Dagger", "description": "Silvered."},
    ],
    "background": "Sera grew up reading forbidden tomes in the docks.",
    "stat_block": {
        # Identity race/alignment DELIBERATELY diverge from the record
        # (Elf/CE vs Human/NG): the export reads the record fields —
        # identity numerics own ONLY level/CR (owner verdict).
        "identity": {
            "role": "NPC",
            "race": "Elf",
            "level": 5,
            "class": "Wizard",
            "alignment": "CE",
        },
        "attributes": {"str": 8, "dex": 14, "con": 12, "int": 17, "wis": 13, "cha": 10},
        "combat": {"ac": 12, "hp": 27},
        "saves": {"str": 2, "dex": 4, "con": 3, "int": 6, "wis": 3, "cha": 1},
        "skills": [
            {"name": "Perception", "bonus": 3},
            {"name": "Stealth", "bonus": -1},
        ],
        "traits": [{"name": "Keen Mind", "description": "Always knows north."}],
        "actions": [{"name": "Fire Bolt", "description": "Ranged spell attack, 2d10 fire."}],
        "spells": ["Fireball", "Mage Hand"],
    },
}

_GNASHER_DATA: dict[str, Any] = {
    "name": "Gnasher",
    "role": "Monster",
    "stat_block": {
        "identity": {"role": "Monster", "race": "Beast", "cr": "1/2"},
        "attributes": {"str": 15, "dex": 13, "con": 14, "int": 3, "wis": 10, "cha": 5},
        "combat": {"ac": 13, "hp": 22},
        "skills": [],
        "traits": [],
        "actions": [{"name": "Bite", "description": "Melee attack, 1d8+2."}],
        "spells": [],
    },
}


def _commit_owlbear_cast(campaign_id: str) -> tuple[str, str, str]:
    """NPC Sera (full record) + Monster Gnasher (CR fraction, sparse) +
    a place anchor. Returns (sera_id, gnasher_id, anchor_id)."""
    sera_id, gnasher_id, anchor_id = new_id(), new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=sera_id, kind="character", name="Sera", text="Scholar.", data=dict(_SERA_DATA)
            ),
            models.EntityInput(
                id=gnasher_id,
                kind="character",
                name="Gnasher",
                text="Beast.",
                data=dict(_GNASHER_DATA),
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Docks", text="Piers."),
        ],
        edges=[
            models.EdgeInput(
                src=sera_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            ),
            models.EdgeInput(
                src=gnasher_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            ),
        ],
    )
    return sera_id, gnasher_id, anchor_id


def _owlbear(client: Any, campaign_id: str, entity_id: str) -> Any:
    return client.get(
        f"/api/campaigns/{campaign_id}/entities/{entity_id}/export", params={"format": "owlbear"}
    )


def test_owlbear_npc_happy(client: Any) -> None:
    """HAPPY NPC (matrix): the Forge transfer payload populates every
    mapped field with committed values; the export stays read-only."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    before = _counts(campaign_id)
    response = _owlbear(client, campaign_id, sera_id)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment") and disposition.endswith('.json"')
    metadata = response.json()
    # Flat metadata object (live-verified): the unit name rides INSIDE
    # metadata; there is no envelope and no author slot.
    assert metadata[_fk("name")] == "Sera"
    assert "author" not in metadata
    assert metadata[_fk("fabd")] is True  # namespaced, not top-level (review round 1)
    assert metadata[_fk("Z001")] == 5
    assert metadata[_fk("Z003")] == "NG"  # record wins over identity CE
    assert metadata[_fk("Z004")] == "Human"  # record wins over identity Elf
    assert metadata[_fk("Z005")] == 27 and metadata[_fk("Z006")] == 27
    assert metadata[_fk("Z007")] == 12
    assert metadata[_fk("Z014")] == "Perception +3, Stealth -1"
    assert _fk("Z016") not in metadata  # NPCs never carry CR
    assert [metadata[_fk(f"Z{bid:03d}")] for bid in range(17, 23)] == [8, 14, 12, 17, 13, 10]
    assert [metadata[_fk(f"Z{bid:03d}")] for bid in range(23, 29)] == [-1, 2, 1, 3, 1, 0]
    assert metadata[_fk("Z034")] == [
        {"id": f"{sera_id[-8:]}-0", "name": "Keen Mind", "description": "Always knows north."}
    ]
    assert metadata[_fk("Z035")] == [
        {
            "id": f"{sera_id[-8:]}-0",
            "name": "Fire Bolt",
            "description": "Ranged spell attack, 2d10 fire.",
        }
    ]
    assert _fk("Z038") not in metadata  # no boss section, no legendary slot
    assert metadata[_fk("Z039")] == [
        {"id": f"{sera_id[-8:]}-0", "name": "Fireball", "description": ""},
        {"id": f"{sera_id[-8:]}-1", "name": "Mage Hand", "description": ""},
    ]
    assert metadata[_fk("Z040")] == [
        {"id": f"{sera_id[-8:]}-0", "name": "Spellbook", "description": "Leather-bound, singed."},
        {"id": f"{sera_id[-8:]}-1", "name": "Dagger", "description": "Silvered."},
    ]
    assert _counts(campaign_id) == before  # no revision, no event (AR18)


def test_owlbear_monster_cr_fraction_and_determinism(client: Any) -> None:
    """HAPPY Monster (matrix): CR ``1/2`` ships as ``0.5``, level is
    omitted, absent lists stay absent; repeats are byte-identical."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    _, gnasher_id, _ = _commit_owlbear_cast(campaign_id)
    first = _owlbear(client, campaign_id, gnasher_id)
    second = _owlbear(client, campaign_id, gnasher_id)
    assert first.status_code == 200 and second.content == first.content
    metadata = first.json()
    assert metadata[_fk("Z016")] == 0.5
    assert _fk("Z001") not in metadata  # Monsters never carry level
    assert _fk("Z003") not in metadata and _fk("Z004") not in metadata
    assert _fk("Z014") not in metadata  # no skills, sparse omit
    assert _fk("Z034") not in metadata and _fk("Z039") not in metadata
    assert metadata[_fk("Z035")][0]["name"] == "Bite"
    assert metadata[_fk("Z035")][0]["id"] == f"{gnasher_id[-8:]}-0"


def test_owlbear_sparse_place_and_long_combat_keys(client: Any) -> None:
    """SPARSE (matrix): a stat-block-less place exports a valid payload
    with empty metadata; the long combat key spellings (armor_class /
    hit_points, committed by the 5-1 sheet fixture) map like ac / hp."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    _, _, anchor_id = _commit_owlbear_cast(campaign_id)
    anchor = _owlbear(client, campaign_id, anchor_id)
    assert anchor.status_code == 200
    assert anchor.json() == {_fk("name"): "Docks", _fk("fabd"): True}  # sparse, fabd always rides
    long_id = new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=long_id,
                kind="character",
                name="Long",
                data={"stat_block": {"combat": {"armor_class": 15, "hit_points": 40}}},
            ),
        ],
        edges=[
            models.EdgeInput(
                src=long_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
        base_revision=client.get(f"/api/campaigns/{campaign_id}/export").json()["revision"]["id"],
    )
    metadata = _owlbear(client, campaign_id, long_id).json()
    assert metadata[_fk("Z005")] == 40 and metadata[_fk("Z006")] == 40
    assert metadata[_fk("Z007")] == 15


def _fg(client: Any, campaign_id: str, entity_id: str) -> Any:
    return client.get(
        f"/api/campaigns/{campaign_id}/entities/{entity_id}/export", params={"format": "fg"}
    )


def _fg_tree(payload: bytes) -> ET.Element:
    return ET.fromstring(payload)


def test_fg_npc_happy(client: Any) -> None:
    """HAPPY NPC (spec-5-3 matrix): the Fantasy Grounds 2024-record XML
    carries every mapped committed field; the read-only invariant holds."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    before = _counts(campaign_id)
    response = _fg(client, campaign_id, sera_id)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/xml")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment") and disposition.endswith('.xml"')
    npc = _fg_tree(response.content).find("npc")
    assert npc is not None
    assert npc.findtext("name") == "Sera"
    assert npc.findtext("type") == "Elf"
    assert npc.findtext("alignment") == "CE"
    ac_el = npc.find("ac")
    assert ac_el is not None
    assert ac_el.text == "12" and ac_el.attrib["type"] == "number"
    assert npc.findtext("hp") == "27"
    assert npc.findtext("cr") == "5"  # level 5 for an NPC
    assert npc.findtext("version") == "2024"
    assert npc.find("xp") is None  # not tracked — sparse omit
    assert npc.find("spellslots") is None and npc.find("summon") is None
    abilities = npc.find("abilities")
    assert abilities is not None
    strength = abilities.find("strength")
    assert strength is not None
    assert strength.findtext("score") == "8"
    assert strength.find("savemodifier") is not None  # saves ridden on Sera below
    assert strength.findtext("savemodifier") == "3"  # save 2 − mod −1 = 3
    assert abilities.findtext("charisma/score") == "10"
    assert npc.findtext("skills") == "Perception +3, Stealth -1"
    traits = npc.find("traits")
    assert traits is not None
    fire_bolt = npc.find("actions/id-00001")
    assert fire_bolt is not None
    assert fire_bolt.findtext("name") == "Fire Bolt"
    assert fire_bolt.findtext("desc") == "Ranged spell attack, 2d10 fire."
    spells_elem = npc.find("spells")
    assert spells_elem is not None
    assert [e.findtext("name") for e in spells_elem] == ["Fireball", "Mage Hand"]
    # The record's notes carry the AR24 lore, not a stat-block copy
    # (owner ruling 2026-09-14: the sheet already shows the stats).
    text_elem = npc.find("text")
    assert text_elem is not None
    note_paragraphs = [p.text or "" for p in text_elem]
    assert any("forbidden tomes in the docks" in p for p in note_paragraphs)
    assert any("Sold the map" in p for p in note_paragraphs)
    labels = [p for p in note_paragraphs if p in ("Appearance", "Background", "Secret")]
    assert labels == ["Appearance", "Background", "Secret"]  # AR24 order
    assert not any("AC 12" in p or "CR 5" in p for p in note_paragraphs)
    assert _counts(campaign_id) == before  # no revision, no event (AR18/FR18)


def test_fg_monster_cr_fraction_and_determinism(client: Any) -> None:
    """HAPPY Monster (matrix): CR ``1/2`` ships as ``0.5`` on the string
    ``cr`` field; repeats are byte-identical (pure projection)."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    _, gnasher_id, _ = _commit_owlbear_cast(campaign_id)
    first = _fg(client, campaign_id, gnasher_id)
    second = _fg(client, campaign_id, gnasher_id)
    assert first.status_code == 200 and second.content == first.content
    npc = _fg_tree(first.content).find("npc")
    assert npc is not None
    assert npc.findtext("cr") == "0.5"
    assert npc.find("abilities") is not None  # Gnasher has scores
    assert npc.find("traits") is None  # sparse omit
    assert npc.findtext("type") == "Beast"
    # Monster levels never surface.
    assert npc.findtext("cr") != ""


def test_fg_sparse_place_and_long_combat_keys(client: Any) -> None:
    """SPARSE (matrix): a stat-block-less place exports a minimal valid
    record (name + version, no combat/abilities); the long combat key
    spellings (armor_class / hit_points) map like ac / hp."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    _, _, anchor_id = _commit_owlbear_cast(campaign_id)
    anchor = _fg(client, campaign_id, anchor_id)
    assert anchor.status_code == 200
    npc = _fg_tree(anchor.content).find("npc")
    assert npc is not None
    assert npc.findtext("name") == "Docks"
    assert npc.find("ac") is None and npc.find("abilities") is None
    assert npc.findtext("version") == "2024"
    # No lore sections, no media — the notes are the empty <p/> shape FG
    # itself exports for a record with no stat block text.
    text_elem = npc.find("text")
    assert text_elem is not None
    assert [p.text for p in text_elem] == [None]
    long_id = new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=long_id,
                kind="character",
                name="Long",
                data={"stat_block": {"combat": {"armor_class": 15, "hit_points": 40}}},
            ),
        ],
        edges=[
            models.EdgeInput(
                src=long_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
        base_revision=client.get(f"/api/campaigns/{campaign_id}/export").json()["revision"]["id"],
    )
    long_tree = _fg_tree(_fg(client, campaign_id, long_id).content)
    long_npc = long_tree.find("npc")
    assert long_npc is not None
    assert long_npc.findtext("ac") == "15" and long_npc.findtext("hp") == "40"


def test_fg_escapes_and_404_shapes(client: Any) -> None:
    """Matrix: XML escaping survives hostile content; the fg lookup 404s
    like every other format (format-independent, no oracle)."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    hostile_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=hostile_id,
                kind="character",
                name='Bill & <Ted>\'s "Horde"',
                text=".",
                data={
                    "stat_block": {
                        "identity": {"role": "NPC", "race": "Humanoid", "level": 2},
                        "attributes": {
                            "str": 10,
                            "dex": 10,
                            "con": 10,
                            "int": 10,
                            "wis": 10,
                            "cha": 10,
                        },
                        "combat": {"ac": 10, "hp": 10},
                        "actions": [{"name": "Sneer", "description": "A & B < C"}],
                    }
                },
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Pit", text="."),
        ],
        edges=[
            models.EdgeInput(
                src=hostile_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    response = _fg(client, campaign_id, hostile_id)
    assert response.status_code == 200
    npc = _fg_tree(response.content).find("npc")
    assert npc is not None
    assert npc.findtext("name") == 'Bill & <Ted>\'s "Horde"'
    assert npc.findtext("actions/id-00001/desc") == "A & B < C"
    # 404 shape: identical to the json lookup, format-independent.
    missing_fg = _fg(client, campaign_id, new_id())
    missing_json = client.get(
        f"/api/campaigns/{campaign_id}/entities/{new_id()}/export", params={"format": "json"}
    )
    assert missing_fg.status_code == 404
    assert missing_fg.json() == missing_json.json()


def test_fg_render_failure_is_logged_as_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """RENDER FAILURE (matrix, FR18): a raising fg renderer logs exactly
    one export_failure event naming format=fg, then the generic 500;
    world state untouched."""
    from app.api import export_sheets
    from app.main import app
    from app.store import app_db_url, init_db

    def boom(export: Any, entity_id: str) -> str:
        raise RuntimeError("simulated fg regression")

    monkeypatch.setattr(export_sheets, "render_entity_fg", boom)
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'fg-fail.db'}")
    try:
        with TestClient(
            app, base_url="https://testserver", raise_server_exceptions=False
        ) as boom_client:
            _register_login(boom_client)
            campaign_id = _create_campaign(boom_client).json()["id"]
            sera_id, _, _ = _commit_owlbear_cast(campaign_id)
            before = _counts(campaign_id)
            with caplog.at_level(logging.ERROR):
                response = boom_client.get(
                    f"/api/campaigns/{campaign_id}/entities/{sera_id}/export",
                    params={"format": "fg"},
                )
            assert response.status_code == 500
            assert response.json()["code"] == "internal_error"
            events = [r for r in caplog.records if "export_failure" in r.getMessage()]
            assert len(events) == 1
            assert "format=fg" in events[0].getMessage()
            assert _counts(campaign_id) == before
    finally:
        init_db(previous)


# ---------------------------------------------------------------------------
# spec-5-4: MapTool / RPGToken export — deterministic .rptok ZIP
# ---------------------------------------------------------------------------

#: The Dragon template's 55 top-level element names, in order — every
#: emitted content.xml has exactly this inventory (no more, no less).
_RPTOK_TEMPLATE_TAGS: tuple[str, ...] = (
    "id",
    "beingImpersonated",
    "exposedAreaGUID",
    "imageAssetMap",
    "x",
    "y",
    "z",
    "lastX",
    "lastY",
    "anchorX",
    "anchorY",
    "sizeScale",
    "scaleX",
    "scaleY",
    "snapToScale",
    "width",
    "height",
    "isoWidth",
    "isoHeight",
    "sizeMap",
    "snapToGrid",
    "isVisible",
    "visibleOnlyToOwner",
    "vblColorSensitivity",
    "alwaysVisibleTolerance",
    "isAlwaysVisible",
    "name",
    "ownerList",
    "ownerType",
    "tokenShape",
    "tokenType",
    "layer",
    "propertyType",
    "tokenOpacity",
    "speechName",
    "terrainModifier",
    "terrainModifierOperation",
    "terrainModifiersIgnored",
    "isFlippedX",
    "isFlippedY",
    "isFlippedIso",
    "uniqueLightSources",
    "lightSourceList",
    "sightType",
    "hasSight",
    "hasImageTable",
    "notes",
    "notesType",
    "gmNotes",
    "gmNotesType",
    "state",
    "propertyMapCI",
    "macroPropertiesMap",
    "speechMap",
    "allowURIAccess",
)

#: The template paths where entity data lands (substituted markers).
_RPTOK_MARKED_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("id", "baGUID"),
        ("imageAssetMap", "entry", "net.rptools.lib.MD5Key", "id"),
        ("name",),
        ("notes",),
        ("gmNotes",),
        ("macroPropertiesMap",),
    }
)

#: The committed Dragon template file (the canonical owner export).
_RPTOK_TEMPLATE_FILE = Path(__file__).resolve().parent / "fixtures" / "maptool-token-template-dragon.xml"


def _assert_rptok_template_fidelity(root: ET.Element) -> None:
    """Template-fidelity contract (owner directive 2026-09-14): every
    non-marker node of an emitted content.xml equals the committed Dragon
    template — same tags, attributes and fixed values, VERBATIM. Only the
    marker slots (baGUID, imageAssetMap MD5Key, name, notes, gmNotes, the
    propertyMapCI ``value`` elements, the macroPropertiesMap subtree)
    may differ. A drift anywhere else is a failed contract."""
    template = ET.fromstring(_RPTOK_TEMPLATE_FILE.read_bytes())

    def walk(parsed: ET.Element, reference: ET.Element, path: tuple[str, ...]) -> None:
        label = "/".join(path) or "<root>"
        assert parsed.tag == reference.tag, f"tag drift at {label}"
        assert parsed.attrib == reference.attrib, f"attrib drift at {label}"
        if path in _RPTOK_MARKED_PATHS or path[-1:] == ("value",) and "propertyMapCI" in path:
            return
        assert len(parsed) == len(reference), f"arity drift at {label}"
        assert parsed.text == reference.text, f"text drift at {label}"
        for child, ref_child in zip(parsed, reference, strict=True):
            walk(child, ref_child, path + (child.tag,))

    walk(root, template, ())


def _rptok_tags(root: ET.Element) -> list[str]:
    return [child.tag for child in root]


#: The MacroButtonProperties shape (copied from the gold fixture) — pins
#: every field name so the emitted buttons stay fixture-shaped.
_RPTOK_BUTTON_FIELDS = frozenset(
    {
        "macroUUID",
        "saveLocation",
        "index",
        "colorKey",
        "hotKey",
        "command",
        "label",
        "group",
        "sortby",
        "autoExecute",
        "includeLabel",
        "applyToTokens",
        "fontColorKey",
        "fontSize",
        "minWidth",
        "maxWidth",
        "allowPlayerEdits",
        "toolTip",
        "displayHotKey",
        "commonMacro",
        "compareGroup",
        "compareSortPrefix",
        "compareCommand",
        "compareIncludeLabel",
        "compareAutoExecute",
        "compareApplyToSelectedTokens",
    }
)

_RPTOK_DEFAULT_IMAGE_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "media" / "maptool_default_token.png"
)

_RPTOK_UUID_NS = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _maptool(client: Any, campaign_id: str, entity_id: str) -> Any:
    return client.get(
        f"/api/campaigns/{campaign_id}/entities/{entity_id}/export",
        params={"format": "maptool"},
    )


def _rptok_zip(payload: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(payload))


def _md5_hex(data: bytes) -> str:
    return hashlib.new("md5", data, usedforsecurity=False).hexdigest()


def _write_media_png(tmp_path: Path, campaign_id: str, row: models.Media, data: bytes) -> None:
    """A PNG-magic file on disk for a manifest row (the .rptok image
    member is validated by magic, unlike the fg path's byte-blindness)."""
    path = tmp_path / "media" / campaign_id / row.entity_id / row.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _commit_maptool_warrior(campaign_id: str) -> str:
    """One stat-blocked NPC with structured AR25 actions for the macro
    rows: Spear (to_hit + two damage parts), Smite (damage only — no
    to_hit still gets a button), Trip (negative to_hit), and Bite
    (prose-only — no button). Returns the entity id."""
    entity_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=entity_id,
                kind="character",
                name="Marta",
                text="Guard.",
                data={
                    "appearance": "Scarred veteran.",
                    "personality": "Laconic.",
                    "secret": "Owes the watch captain.",
                    "background": "Fought at the eastern gate.",
                    "stat_block": {
                        "identity": {
                            "role": "NPC",
                            "race": "Human",
                            "alignment": "LN",
                            "level": 5,
                        },
                        "attributes": {
                            "str": 16,
                            "dex": 14,
                            "con": 14,
                            "int": 10,
                            "wis": 12,
                            "cha": 10,
                        },
                        "combat": {
                            "ac": 15,
                            "hp": 45,
                            "hit_dice": "6d10 + 12",
                            "speed": "30 ft.",
                            "initiative": 3,
                        },
                        "saves": {"str": 5, "dex": 4, "con": 5, "int": 0, "wis": 3, "cha": 0},
                        "skills": [
                            {"name": "Athletics", "bonus": 6},
                            {"name": "Perception", "bonus": 1},
                        ],
                        "senses": "Darkvision 60 ft.",
                        "languages": "Common",
                        "proficiency_bonus": 3,
                        "passive_perception": 11,
                        "traits": [{"name": "Vigilant", "description": "Never sleeps on duty."}],
                        "actions": [
                            {
                                "name": "Spear",
                                "description": "Melee Weapon Attack, reach 10 ft.",
                                "to_hit": 5,
                                "damage": [
                                    {"count": 1, "sides": 8, "bonus": 7, "type": "piercing"},
                                    {"count": 3, "sides": 10, "bonus": 2, "type": "radiant"},
                                ],
                            },
                            {
                                "name": "Smite",
                                "description": "Radiant smite.",
                                "damage": [
                                    {"count": 2, "sides": 6, "bonus": 12, "type": "radiant"}
                                ],
                            },
                            {
                                "name": "Trip",
                                "to_hit": -1,
                                "damage": [{"count": 1, "sides": 4, "type": "bludgeoning"}],
                            },
                            {"name": "Hurl", "damage": [{"dice": "1d12+1"}]},
                            {
                                "name": "Bash",
                                "description": "Heavy swing.",
                                "damage": [{"dice": "1d8+7", "bonus": 7}],
                            },
                            {
                                "name": "Overcharge",
                                "description": "Arcane surge.",
                                "damage": [{"dice": "1d8+2 acid"}],
                            },
                            {"name": "Bite", "description": "Melee attack, 1d8+2."},
                        ],
                    },
                },
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Gate", text="."),
        ],
        edges=[
            models.EdgeInput(
                src=entity_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    return entity_id


def _rptok_button_props(root: ET.Element) -> list[ET.Element]:
    """The MacroButtonProperties elements of a parsed content.xml (never
    None — the test fixtures emit them; the assert narrows for mypy)."""
    props: list[ET.Element] = []
    for entry in root.findall("macroPropertiesMap/entry"):
        prop = entry.find("net.rptools.maptool.model.MacroButtonProperties")
        assert prop is not None
        props.append(prop)
    return props


def test_maptool_happy_npc(client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HAPPY NPC (spec-5-4 matrix): the .rptok zip structure (content.xml
    + properties.xml + assets/<md5> + .png with exact md5), the XML
    element inventory (a subset of the gold fixture's tag set), the HTML
    stat-block notes, the AR24 lore gmNotes, the derived GUID, one
    deterministic macro button per damage-bearing action, and the
    read-only invariant (AR18/FR18)."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    marta_id = _commit_maptool_warrior(campaign_id)
    row = add_media(campaign_id, marta_id, f"{new_id()}.png", "image")
    png = PNG_SIGNATURE + b"fixture-portrait"
    _write_media_png(tmp_path, campaign_id, row, png)
    before = _counts(campaign_id)

    first = _maptool(client, campaign_id, marta_id)
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("application/zip")
    disposition = first.headers["content-disposition"]
    assert disposition.startswith("attachment") and disposition.endswith('.rptok"')
    archive = _rptok_zip(first.content)

    md5 = _md5_hex(png)
    assert archive.namelist() == [
        "content.xml",
        "properties.xml",
        f"assets/{md5}",
        f"assets/{md5}.png",
    ]
    assert archive.read(f"assets/{md5}.png") == png
    for info in archive.infolist():
        assert info.date_time == (1980, 1, 1, 0, 0, 0)  # fixed timestamps, no wall clock
        assert info.compress_type == zipfile.ZIP_DEFLATED  # pinned compress_type
        assert info.extra == b""  # no member extra fields
    properties = archive.read("properties.xml")
    assert b"<string>version</string>\n    <string>1.18.6</string>" in properties
    assert b"<boolean>false</boolean>" in properties

    content = archive.read("content.xml")
    root = ET.fromstring(content)
    # Template fidelity (owner directive 2026-09-14): EXACTLY the Dragon
    # template's inventory — all 55 children in order; the runtime and
    # campaign fields ride verbatim, only the marker slots carry entity
    # data (checked by the fidelity walker below).
    assert _rptok_tags(root) == list(_RPTOK_TEMPLATE_TAGS)
    assert root.findtext("x") == "400"
    assert root.findtext("exposedAreaGUID/baGUID") == "9maH6b4tRVGCP61AXNzjpA=="
    assert root.findtext("beingImpersonated") == "true"
    assert root.find("propertyMapCI") is not None
    assert root.find("portraitImage") is None  # the template has none either
    _assert_rptok_template_fidelity(root)

    # Derived (never random) GUID: base64 of sha256(entity_id)[:16].
    expected_guid = base64.b64encode(hashlib.sha256(marta_id.encode()).digest()[:16]).decode()
    assert root.findtext("id/baGUID") == expected_guid
    assert root.findtext("name") == "Marta"
    assert root.findtext("tokenType") == "NPC"
    assert root.findtext("tokenShape") == "CIRCLE"
    assert root.findtext("layer") == "TOKEN"
    assert root.findtext("notesType") == "text/html"
    assert root.findtext("gmNotesType") == "text/html"

    # The notes: the 2024-Core stat block as bold-labelled HTML lines.
    notes = root.findtext("notes")
    assert notes == (
        "<b>Human, LN</b><br>"
        "AC 15 Initiative +3 (14)<br>"
        "HP 45 (6d10 + 12)<br>"
        "Speed 30 ft.<br>"
        "MOD SAVE MOD SAVE MOD SAVE<br>"
        "Str 16 +3 +5 Dex 14 +2 +4 Con 14 +2 +5<br>"
        "Int 10 +0 +0 Wis 12 +1 +3 Cha 10 +0 +0<br>"
        "Skills Athletics +6, Perception +1<br>"
        "Senses Darkvision 60 ft.; Passive Perception 11<br>"
        "Languages Common<br>"
        "CR 5<br>"
        "Proficiency Bonus 3<br>"
        "Traits<br><b>Vigilant.</b> Never sleeps on duty.<br>"
        "Actions<br>"
        "<b>Spear.</b> Melee Weapon Attack, reach 10 ft. "
        "Hit: 11.5 (1d8 + 7) piercing damage plus 18.5 (3d10 + 2) radiant damage.<br>"
        "<b>Smite.</b> Radiant smite. Hit: 19 (2d6 + 12) radiant damage.<br>"
        "<b>Trip.</b> Hit: 2.5 (1d4) bludgeoning damage.<br>"
        "<b>Hurl.</b> Hit: 1d12+1 damage.<br>"
        "<b>Bash.</b> Heavy swing. Hit: 1d8+7 damage.<br>"
        "<b>Overcharge.</b> Arcane surge.<br>"
        "<b>Bite.</b> Melee attack, 1d8+2."
    )

    # gmNotes: the AR24 lore as labelled HTML paragraphs, verbatim text,
    # in the AR24 profile order (_FG_LORE_FIELDS).
    assert root.findtext("gmNotes") == (
        "<p><b>Appearance</b><br>Scarred veteran.</p>"
        "<p><b>Personality</b><br>Laconic.</p>"
        "<p><b>Background</b><br>Fought at the eastern gate.</p>"
        "<p><b>Secret</b><br>Owes the watch captain.</p>"
    )

    # imageAssetMap: single null-key entry -> the embedded PNG's MD5Key.
    entry = root.find("imageAssetMap/entry")
    assert entry is not None
    assert entry.find("null") is not None
    assert entry.findtext("net.rptools.lib.MD5Key/id") == md5

    # Asset descriptor round-trips the same md5 + name + png pins.
    asset = ET.fromstring(archive.read(f"assets/{md5}"))
    assert asset.findtext("id/id") == md5
    assert asset.findtext("name") == "Marta"
    assert asset.findtext("extension") == "png"
    assert asset.findtext("type") == "IMAGE"

    # One macro button per action with structured damage, action order;
    # Overcharge (unparseable dice string) gets none.
    buttons = root.findall("macroPropertiesMap/entry")
    assert [entry.findtext("int") for entry in buttons] == ["1", "2", "3", "4", "5"]
    props = _rptok_button_props(root)
    assert [p.findtext("label") for p in props] == ["Spear", "Smite", "Trip", "Hurl", "Bash"]
    assert [p.findtext("index") for p in props] == ["1", "2", "3", "4", "5"]
    assert [p.findtext("command") for p in props] == [
        "[1d20+5] [1d8+7] [3d10+2]",
        "[2d6+12]",
        "[1d20-1] [1d4]",
        "[1d12+1]",
        "[1d8+7]",  # embedded dice bonus wins; the field bonus is not double-added
    ]
    for p in props:
        assert {child.tag for child in p} == _RPTOK_BUTTON_FIELDS  # fixture shape
        assert p.findtext("saveLocation") == "Token"
        assert p.findtext("colorKey") == "default"
        assert p.findtext("hotKey") == "None"
        assert p.findtext("autoExecute") == "true"
        assert p.findtext("fontSize") == "1.00em"
        assert p.findtext("allowPlayerEdits") == "true"
    # macroUUIDs are derived (uuid5 of entity id + action index), never random.
    assert [p.findtext("macroUUID") for p in props] == [
        str(uuid.uuid5(_RPTOK_UUID_NS, f"{marta_id}{position}")) for position in (1, 2, 3, 4, 5)
    ]

    # Byte-identical repeats (determinism: fixed timestamps, derived GUID).
    second = _maptool(client, campaign_id, marta_id)
    assert second.status_code == 200 and second.content == first.content
    assert _counts(campaign_id) == before  # no revision, no event (AR18)


def test_maptool_monster_sparse_place_and_remint(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Matrix: Monster CR fraction derives to a decimal Challenge line;
    a stat-block-less place emits the minimal token with the pinned
    empty-element notes form; re-minting the portrait (new bytes) changes
    the asset md5 but never the derived token GUID, and stays
    byte-identical across repeats."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    _, gnasher_id, anchor_id = _commit_owlbear_cast(campaign_id)

    gnasher = _maptool(client, campaign_id, gnasher_id)
    assert gnasher.status_code == 200
    root = ET.fromstring(_rptok_zip(gnasher.content).read("content.xml"))
    assert root.findtext("name") == "Gnasher"
    notes = root.findtext("notes")
    assert notes is not None
    assert "<b>Beast</b>" in notes
    assert "CR 0.5" in notes  # fraction → decimal, the fg derivation
    assert "Str 15 +2" in notes
    assert "MOD SAVE" in notes
    assert "<b>Spells</b>" not in notes  # no spells block when empty
    assert root.findtext("gmNotes") == ""
    # Gnasher's actions are prose-only → no macro buttons at all.
    assert root.findall("macroPropertiesMap/entry") == []
    _assert_rptok_template_fidelity(root)

    anchor = _maptool(client, campaign_id, anchor_id)
    assert anchor.status_code == 200
    content = _rptok_zip(anchor.content).read("content.xml")
    assert b"<notes></notes>" in content  # pinned empty-element form, never <notes/>
    assert b"<notes/>" not in content
    assert b"<gmNotes></gmNotes>" in content
    assert b"<gmNotes/>" not in content
    bare = ET.fromstring(content)
    assert _rptok_tags(bare) == list(_RPTOK_TEMPLATE_TAGS)
    assert bare.findtext("name") == "Docks"  # place tokens still carry the name
    assert bare.findall("macroPropertiesMap/entry") == []
    _assert_rptok_template_fidelity(bare)
    # No portrait and no other media → the bundled default image, no marker.
    default_png = _RPTOK_DEFAULT_IMAGE_PATH.read_bytes()
    default_md5 = _md5_hex(default_png)
    assert bare.findtext("imageAssetMap/entry/net.rptools.lib.MD5Key/id") == default_md5
    assert "Portrait:" not in (bare.findtext("notes") or "")

    # Re-mint: replace the portrait bytes on disk — derived GUID unchanged,
    # asset md5 follows the new bytes, repeats stay byte-identical.
    row = add_media(campaign_id, gnasher_id, f"{new_id()}.png", "image")
    version_two = PNG_SIGNATURE + b"portrait-v2"
    _write_media_png(tmp_path, campaign_id, row, version_two)
    remint_archive = _rptok_zip(_maptool(client, campaign_id, gnasher_id).content)
    remint_names = remint_archive.namelist()
    assert f"assets/{_md5_hex(version_two)}.png" in remint_names
    assert f"assets/{_md5_hex(version_two)}" in remint_names
    remint_root = ET.fromstring(remint_archive.read("content.xml"))
    assert remint_root.findtext("id/baGUID") == root.findtext("id/baGUID")
    assert remint_root.findtext("imageAssetMap/entry/net.rptools.lib.MD5Key/id") == _md5_hex(
        version_two
    )
    first = _maptool(client, campaign_id, gnasher_id)
    second = _maptool(client, campaign_id, gnasher_id)
    assert first.status_code == 200 and second.content == first.content


def test_maptool_missing_portrait_embeds_default_with_marker(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BROKEN portrait (matrix): an image row whose file is absent → the
    bundled default token PNG is embedded (imageAssetMap + assets pair,
    MD5 of the default's bytes) and the notes carry the honesty marker
    naming the newest missing file with the '[missing]' cause; the zip
    still parses."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    marta_id = _commit_maptool_warrior(campaign_id)
    row = add_media(campaign_id, marta_id, f"{new_id()}.png", "image")  # no file written
    response = _maptool(client, campaign_id, marta_id)
    assert response.status_code == 200
    archive = _rptok_zip(response.content)
    default_png = _RPTOK_DEFAULT_IMAGE_PATH.read_bytes()
    default_md5 = _md5_hex(default_png)
    assert f"assets/{default_md5}.png" in archive.namelist()
    assert archive.read(f"assets/{default_md5}.png") == default_png
    root = ET.fromstring(archive.read("content.xml"))
    assert root.findtext("imageAssetMap/entry/net.rptools.lib.MD5Key/id") == default_md5
    notes = root.findtext("notes")
    assert notes is not None
    assert f"Portrait: {row.filename} [missing — default image used]" in notes
    assert notes.startswith("<b>Human, LN</b>")


def test_maptool_corrupt_portrait_embeds_default_with_unusable_marker(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BROKEN portrait, corrupt bytes: the image row's file EXISTS but is
    not a PNG → the default token image embeds with the '[unusable]'
    marker naming that row."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    marta_id = _commit_maptool_warrior(campaign_id)
    row = add_media(campaign_id, marta_id, f"{new_id()}.png", "image")
    path = tmp_path / "media" / campaign_id / marta_id / row.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not-a-png-at-all")
    response = _maptool(client, campaign_id, marta_id)
    assert response.status_code == 200
    archive = _rptok_zip(response.content)
    default_png = _RPTOK_DEFAULT_IMAGE_PATH.read_bytes()
    assert f"assets/{_md5_hex(default_png)}.png" in archive.namelist()
    root = ET.fromstring(archive.read("content.xml"))
    notes = root.findtext("notes")
    assert notes is not None
    assert f"Portrait: {row.filename} [unusable — default image used]" in notes


def test_maptool_older_portrait_wins_over_corrupt_newer(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Portrait scan (review r1): the newest AVAILABLE row whose on-disk
    bytes start with the PNG magic wins — a corrupt NEWER row falls back
    to the OLDER valid PNG (no default, no marker)."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    marta_id = _commit_maptool_warrior(campaign_id)
    older = add_media(campaign_id, marta_id, f"{new_id()}.png", "image")
    good_png = PNG_SIGNATURE + b"good-older"
    _write_media_png(tmp_path, campaign_id, older, good_png)
    newer = add_media(campaign_id, marta_id, f"{new_id()}.png", "image")
    corrupt_path = tmp_path / "media" / campaign_id / marta_id / newer.filename
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_path.write_bytes(b"corrupt-not-png")
    response = _maptool(client, campaign_id, marta_id)
    assert response.status_code == 200
    archive = _rptok_zip(response.content)
    assert f"assets/{_md5_hex(good_png)}.png" in archive.namelist()
    assert f"assets/{_md5_hex(b'corrupt-not-png')}.png" not in archive.namelist()
    root = ET.fromstring(archive.read("content.xml"))
    assert root.findtext("imageAssetMap/entry/net.rptools.lib.MD5Key/id") == _md5_hex(good_png)
    notes = root.findtext("notes")
    assert notes is not None
    assert "Portrait:" not in notes  # a usable portrait served — no default, no marker


def test_maptool_caster_spells_and_bbeg_challenge(client: Any) -> None:
    """Notes parity (review r1): a bold-labelled Spells block renders
    after Actions, one `<b>Name.</b>` per non-blank spell; a BBEG's
    challenge derives from ``identity.level`` like an NPC's (CR line)."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    sera_root = ET.fromstring(
        _rptok_zip(_maptool(client, campaign_id, sera_id).content).read("content.xml")
    )
    notes = sera_root.findtext("notes")
    assert notes is not None
    assert notes.endswith("<b>Spells</b><br><b>Fireball.</b><br><b>Mage Hand.</b>")
    assert "CR 5" in notes  # Sera is a level-5 NPC

    bbeg_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=bbeg_id,
                kind="character",
                name="Vespera",
                text=".",
                data={
                    "stat_block": {
                        "identity": {"role": "BBEG", "race": "Dragon", "level": 9},
                        "attributes": {
                            "str": 20,
                            "dex": 12,
                            "con": 18,
                            "int": 16,
                            "wis": 14,
                            "cha": 18,
                        },
                    }
                },
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Keep", text="."),
        ],
        edges=[
            models.EdgeInput(
                src=bbeg_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
        base_revision=client.get(f"/api/campaigns/{campaign_id}/export").json()["revision"]["id"],
    )
    bbeg_root = ET.fromstring(
        _rptok_zip(_maptool(client, campaign_id, bbeg_id).content).read("content.xml")
    )
    bbeg_notes = bbeg_root.findtext("notes")
    assert bbeg_notes is not None
    assert "<b>Dragon</b>" in bbeg_notes
    assert "CR 9" in bbeg_notes
    assert bbeg_root.findall("macroPropertiesMap/entry") == []  # no actions, no buttons


def test_maptool_sparse_abilities_and_integral_guards(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Notes/macros guards (review r1): a sparse ability block emits its
    cells WITHOUT the MOD SAVE header (only when all six cells render);
    non-integral to_hit/modifier/initiative/save/perception values are
    omitted, never truncated; ``count 0`` parts never roll."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    entity_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=entity_id,
                kind="character",
                name="Slack",
                text=".",
                data={
                    "stat_block": {
                        "identity": {"role": "NPC", "race": "Humanoid", "level": 2},
                        "attributes": {"str": 10, "dex": 12},
                        "combat": {"ac": 10, "hp": 10, "initiative": 3.5},
                        "saves": {"str": 2.5},
                        "passive_perception": 11.5,
                        "actions": [
                            {
                                "name": "Swing",
                                "description": "Wild swing.",
                                "to_hit": 5.5,
                                "damage": [
                                    {"count": 0, "sides": 6},
                                    {"count": 2, "sides": 6, "bonus": 12},
                                ],
                            }
                        ],
                    }
                },
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Pit", text="."),
        ],
        edges=[
            models.EdgeInput(
                src=entity_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    root = ET.fromstring(
        _rptok_zip(_maptool(client, campaign_id, entity_id).content).read("content.xml")
    )
    notes = root.findtext("notes")
    assert notes is not None
    assert "MOD SAVE" not in notes  # sparse abilities: no header
    assert "Str 10 +0 Dex 12 +1" in notes  # only the present cells
    assert "Int " not in notes and "Wis " not in notes and "Cha " not in notes
    assert "Initiative" not in notes  # 3.5 is non-integral — omitted, never truncated
    assert "Senses" not in notes and "Passive Perception" not in notes  # 11.5 dropped
    assert "Str 10 +0" in notes and "+2.5" not in notes  # non-integral save dropped
    buttons = _rptok_button_props(root)
    assert len(buttons) == 1
    swallow = buttons[0]
    assert swallow.findtext("label") == "Swing"
    # 5.5 to_hit is non-integral → no [1d20…] segment; the count-0 part is
    # skipped; the usable 2d6+12 part still rolls.
    assert swallow.findtext("command") == "[2d6+12]"


def test_maptool_broken_default_image_falls_back_to_no_image(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broken bundled default (review r1, template contract): when the
    default token PNG cannot load, an entity with no portrait embeds NO
    image members — the template's imageAssetMap keeps its literal md5 (a
    dangling reference MapTool skips with a log error) and the zip still
    parses."""
    from app.api import export_sheets

    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(export_sheets, "_rptok_default_image", lambda: None)
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    marta_id = _commit_maptool_warrior(campaign_id)
    response = _maptool(client, campaign_id, marta_id)
    assert response.status_code == 200
    archive = _rptok_zip(response.content)
    assert archive.namelist() == ["content.xml", "properties.xml"]
    root = ET.fromstring(archive.read("content.xml"))
    assert root.find("imageAssetMap") is not None  # template-fixed element stays
    # The template's own md5 literal rides — a dangling ref, no asset pair.
    dangling_md5 = root.findtext("imageAssetMap/entry/net.rptools.lib.MD5Key/id")
    assert dangling_md5 == "87f4e9bfa4f1f3db250b57b3599fa4e9"
    _assert_rptok_template_fidelity(root)
    notes = root.findtext("notes")
    assert notes is not None and "<b>Human, LN</b>" in notes


def test_maptool_content_template_is_committed_verbatim(client: Any) -> None:
    """Template fidelity (owner directive 2026-09-14): the module's
    template constant is byte-identical to the committed Dragon template
    file, every marker token appears exactly once, and a live export's
    non-marker nodes match the template exactly."""
    from app.api import export_sheets

    assert _RPTOK_TEMPLATE_FILE.read_text() == export_sheets._RPTOK_TEMPLATE
    for marker in export_sheets._RPTOK_MARKERS:
        assert export_sheets._RPTOK_TEMPLATE.count(marker) == 1, marker
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    marta_id = _commit_maptool_warrior(campaign_id)
    root = ET.fromstring(
        _rptok_zip(_maptool(client, campaign_id, marta_id).content).read("content.xml")
    )
    _assert_rptok_template_fidelity(root)


def test_maptool_escaping_404_and_bad_format(client: Any) -> None:
    """Matrix: XML 1.0 filtering + full escaping survive hostile
    content (control chars dropped, values round-trip); the maptool
    lookup 404s exactly like json (format-independent); a bad format is
    a 422."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    hostile_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=hostile_id,
                kind="character",
                name='Bill & <Ted>\'s "Horde"\ufffe',
                text=".",
                data={
                    "stat_block": {
                        "identity": {"role": "NPC", "race": "Humanoid", "level": 2},
                        "attributes": {
                            "str": 10,
                            "dex": 10,
                            "con": 10,
                            "int": 10,
                            "wis": 10,
                            "cha": 10,
                        },
                        "combat": {"ac": 10, "hp": 10},
                        "actions": [
                            {"name": "Sneer", "description": "A & B < C \x00\x01 broken"},
                            {
                                "name": "Guffaw",
                                "description": "]]> like this",
                                "to_hit": 2,
                                "damage": [{"count": 1, "sides": 6, "bonus": 1}],
                            },
                        ],
                    }
                },
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Pit", text="."),
        ],
        edges=[
            models.EdgeInput(
                src=hostile_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    response = _maptool(client, campaign_id, hostile_id)
    content = response.content
    root = ET.fromstring(_rptok_zip(content).read("content.xml"))  # control chars dropped → parses
    assert root.findtext("name") == 'Bill & <Ted>\'s "Horde"'
    assert b"\xef\xbf\xbe" not in content  # U+FFFE dropped like other XML-illegals
    notes = root.findtext("notes")
    assert notes is not None
    assert "A & B < C  broken" in notes  # \x00\x01 dropped, then unescaped on parse
    # Full > escaping neutralizes ]]>) — the RAW xml never carries it.
    raw_content = _rptok_zip(content).read("content.xml")
    assert b"]]>" not in raw_content
    guffaw = root.find("macroPropertiesMap/entry/net.rptools.maptool.model.MacroButtonProperties")
    assert guffaw is not None
    assert guffaw.findtext("label") == "Guffaw"
    assert guffaw.findtext("command") == "[1d20+2] [1d6+1]"
    # 404 shape: identical to the json lookup, format-independent.
    missing_maptool = _maptool(client, campaign_id, new_id())
    missing_json = client.get(
        f"/api/campaigns/{campaign_id}/entities/{new_id()}/export", params={"format": "json"}
    )
    assert missing_maptool.status_code == 404
    assert missing_maptool.json() == missing_json.json()
    foreign = _maptool(client, new_id(), hostile_id)
    unknown_campaign = client.get(
        f"/api/campaigns/{new_id()}/entities/{hostile_id}/export", params={"format": "json"}
    )
    assert foreign.status_code == 404
    assert foreign.json() == unknown_campaign.json()  # indistinguishable
    bad = client.get(
        f"/api/campaigns/{campaign_id}/entities/{hostile_id}/export", params={"format": "weird"}
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "validation_error"


def test_maptool_render_failure_is_logged_as_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """RENDER FAILURE (matrix, FR18): a raising maptool renderer logs
    exactly one export_failure event naming format=maptool, then the
    generic 500; world state untouched."""
    from app.api import export_sheets
    from app.main import app
    from app.store import app_db_url, init_db

    def boom(export: Any, entity_id: str) -> bytes:
        raise RuntimeError("simulated maptool regression")

    monkeypatch.setattr(export_sheets, "render_entity_maptool", boom)
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'maptool-fail.db'}")
    try:
        with TestClient(
            app, base_url="https://testserver", raise_server_exceptions=False
        ) as boom_client:
            _register_login(boom_client)
            campaign_id = _create_campaign(boom_client).json()["id"]
            sera_id, _, _ = _commit_owlbear_cast(campaign_id)
            before = _counts(campaign_id)
            with caplog.at_level(logging.ERROR):
                response = boom_client.get(
                    f"/api/campaigns/{campaign_id}/entities/{sera_id}/export",
                    params={"format": "maptool"},
                )
            assert response.status_code == 500
            assert response.json()["code"] == "internal_error"
            events = [r for r in caplog.records if "export_failure" in r.getMessage()]
            assert len(events) == 1
            assert "format=maptool" in events[0].getMessage()
            assert _counts(campaign_id) == before
    finally:
        init_db(previous)


def test_owlbear_404_shapes_and_bad_format(client: Any) -> None:
    """MISSING/FOREIGN (matrix): the owlbear lookup 404s exactly like the
    json one (format-independent); a bad format stays 422."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    missing_owlbear = _owlbear(client, campaign_id, new_id())
    missing_json = client.get(
        f"/api/campaigns/{campaign_id}/entities/{new_id()}/export", params={"format": "json"}
    )
    assert missing_owlbear.status_code == 404
    assert missing_json.status_code == 404
    assert missing_owlbear.json() == missing_json.json()  # format-independent lookup
    foreign = _owlbear(client, new_id(), sera_id)
    unknown_campaign = client.get(
        f"/api/campaigns/{new_id()}/entities/{sera_id}/export", params={"format": "json"}
    )
    assert foreign.status_code == 404
    assert foreign.json() == unknown_campaign.json()  # indistinguishable
    bad = client.get(
        f"/api/campaigns/{campaign_id}/entities/{sera_id}/export", params={"format": "foundry"}
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "validation_error"


def test_owlbear_render_failure_is_logged_as_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """RENDER FAILURE (matrix, FR18): a raising owlbear renderer logs
    exactly one export_failure event naming format=owlbear, then the
    generic 500; world state untouched."""
    from app.api import export_sheets
    from app.main import app
    from app.store import app_db_url, init_db

    def boom(export: Any, entity_id: str) -> dict[str, Any]:
        raise RuntimeError("simulated owlbear regression")

    monkeypatch.setattr(export_sheets, "render_entity_owlbear", boom)
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'owlbear-fail.db'}")
    try:
        with TestClient(
            app, base_url="https://testserver", raise_server_exceptions=False
        ) as boom_client:
            _register_login(boom_client)
            campaign_id = _create_campaign(boom_client).json()["id"]
            sera_id, _, _ = _commit_owlbear_cast(campaign_id)
            before = _counts(campaign_id)
            with caplog.at_level(logging.ERROR):
                response = boom_client.get(
                    f"/api/campaigns/{campaign_id}/entities/{sera_id}/export",
                    params={"format": "owlbear"},
                )
            assert response.status_code == 500
            assert response.json()["code"] == "internal_error"
            events = [r for r in caplog.records if "export_failure" in r.getMessage()]
            assert len(events) == 1
            message = events[0].getMessage()
            assert campaign_id in message and sera_id in message and "format=owlbear" in message
            assert _counts(campaign_id) == before
    finally:
        init_db(previous)


def _mint_url(client: Any, campaign_id: str, entity_id: str) -> dict[str, Any]:
    response = client.get(f"/api/campaigns/{campaign_id}/entities/{entity_id}/portrait-url")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def test_portrait_url_mint_and_signed_fetch(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HAPPY portrait (matrix): the mint route returns an absolute URL +
    expiry; the URL serves the file with no session, inline, read-only."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", "test-secret-5-2")
    monkeypatch.setenv("MYTHOSCIRCLE_BASE_URL", "https://table.example.test")
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    row = add_media(campaign_id, sera_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, row)
    before = _counts(campaign_id)
    body = _mint_url(client, campaign_id, sera_id)
    assert body["url"].startswith("https://table.example.test/api/campaigns/")
    assert f"/media/{sera_id}/{row.filename}?" in body["url"]
    assert "exp=" in body["url"] and "sig=" in body["url"]
    assert body["expires_at"].endswith("Z")
    from urllib.parse import parse_qs, urlparse

    exp = int(parse_qs(urlparse(body["url"]).query)["exp"][0])
    import time as _time

    assert 7 * 24 * 3600 - 5 <= exp - int(_time.time()) <= 7 * 24 * 3600  # ~7d TTL
    client.cookies.clear()  # Forge presents no session
    fetched = client.get(body["url"].replace("https://table.example.test", ""))
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.headers["content-disposition"].startswith("inline")
    assert fetched.headers["cache-control"] == "private"  # bearer URL, no shared cache
    assert fetched.content == b"payload"
    assert _counts(campaign_id) == before  # mint + fetch write nothing


def test_portrait_url_no_portrait_matches_unknown_entity(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NO portrait (matrix): an entity with no available image 404s the
    mint route exactly like an unknown entity — while its owlbear
    payload still exports 200."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", "test-secret-5-2")
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    assert _owlbear(client, campaign_id, sera_id).status_code == 200
    bare = client.get(f"/api/campaigns/{campaign_id}/entities/{sera_id}/portrait-url")
    unknown = client.get(f"/api/campaigns/{campaign_id}/entities/{new_id()}/portrait-url")
    assert bare.status_code == 404 and unknown.status_code == 404
    assert bare.json() == unknown.json()


def test_portrait_url_newest_available_skips_broken(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mint picks the newest AVAILABLE image row (hero rule): a newer
    row whose file is gone is skipped; all-broken reads as no portrait."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", "test-secret-5-2")
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    older = add_media(campaign_id, sera_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, older)
    add_media(campaign_id, sera_id, f"{new_id()}.png", "image")  # newer, never written
    assert older.filename in _mint_url(client, campaign_id, sera_id)["url"]
    (tmp_path / "media" / campaign_id / sera_id / older.filename).unlink()
    missing = client.get(f"/api/campaigns/{campaign_id}/entities/{sera_id}/portrait-url")
    assert missing.status_code == 404


def _signed_params(secret: str, campaign_id: str, entity_id: str, filename: str, exp: int) -> str:
    import hashlib as _hashlib
    import hmac as _hmac

    msg = f"{campaign_id}.{entity_id}.{filename}.{exp}".encode()
    sig = _hmac.new(secret.encode(), msg, _hashlib.sha256).hexdigest()
    return f"exp={exp}&sig={sig}"


def test_signed_url_failures_share_campaign_404(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIG bad/expired (matrix): tampered, expired, half-present, and
    foreign-campaign signatures are all the campaign-missing 404 —
    mutually identical, with no oracle."""
    import time as _time

    secret = "test-secret-5-2"
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", secret)
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    row = add_media(campaign_id, sera_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, row)
    client.cookies.clear()

    def fetch(query: str, cid: str = campaign_id) -> Any:
        return client.get(f"/api/campaigns/{cid}/media/{sera_id}/{row.filename}?{query}")

    good_exp = int(_time.time()) + 3600
    good = _signed_params(secret, campaign_id, sera_id, row.filename, good_exp)
    assert fetch(good).status_code == 200
    tampered = fetch(f"exp={good_exp}&sig={'0' * 64}")
    expired = fetch(_signed_params(secret, campaign_id, sera_id, row.filename, 1))
    half = fetch(f"exp={good_exp}")
    # A non-integer exp is a signed-path 404 like every other failure —
    # never a 422 (the signed path has no typed contract; review round 1).
    nonint = fetch(f"exp=abc&sig={'0' * 64}")
    foreign = client.get(f"/api/campaigns/{new_id()}/media/{sera_id}/{row.filename}?{good}")
    for response in (tampered, expired, half, nonint, foreign):
        assert response.status_code == 404
    assert tampered.json() == expired.json() == half.json() == nonint.json() == foreign.json()


def test_signed_video_row_serves_mp4(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The signed path keeps the row-kind media type: a signed video URL
    serves ``video/mp4`` (the mint route itself only ever mints images)."""
    import time as _time

    secret = "test-secret-5-2"
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", secret)
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    clip = add_media(campaign_id, sera_id, f"{new_id()}.mp4", "video")
    _write_media_file(tmp_path, campaign_id, clip)
    client.cookies.clear()
    query = _signed_params(secret, campaign_id, sera_id, clip.filename, int(_time.time()) + 60)
    fetched = client.get(f"/api/campaigns/{campaign_id}/media/{sera_id}/{clip.filename}?{query}")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "video/mp4"


def test_portrait_url_no_secret_500s_with_one_log_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """NO secret (matrix): the mint route fails closed — one error log
    line, then the generic 500 (never an unsigned URL). Its own client:
    the generic 500 needs raise_server_exceptions=False (test_api.py
    pattern); the FR18 test's isolated-DB dance."""
    from app.main import app
    from app.store import app_db_url, init_db

    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.delenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", raising=False)
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'portrait-secret.db'}")
    try:
        with TestClient(
            app, base_url="https://testserver", raise_server_exceptions=False
        ) as secret_client:
            _register_login(secret_client)
            campaign_id = _create_campaign(secret_client).json()["id"]
            sera_id, _, _ = _commit_owlbear_cast(campaign_id)
            row = add_media(campaign_id, sera_id, f"{new_id()}.png", "image")
            _write_media_file(tmp_path, campaign_id, row)
            with caplog.at_level(logging.ERROR):
                response = secret_client.get(
                    f"/api/campaigns/{campaign_id}/entities/{sera_id}/portrait-url"
                )
            assert response.status_code == 500
            assert response.json()["code"] == "internal_error"
            events = [r for r in caplog.records if "portrait_url_secret_missing" in r.getMessage()]
            assert len(events) == 1
    finally:
        init_db(previous)


def test_cookie_file_get_still_401_without_signature(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The optional-auth refactor keeps the cookie contract: no session
    and no signature on an existing row is the same 401 (never a 404
    oracle, never the file)."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", "test-secret-5-2")
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    row = add_media(campaign_id, sera_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, row)
    client.cookies.clear()
    response = client.get(f"/api/campaigns/{campaign_id}/media/{sera_id}/{row.filename}")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_owlbear_bbeg_level_and_legendary(client: Any) -> None:
    """BBEG (verification gap): level ships on Z001 like an NPC, and the
    boss legendary_actions text ships on Z038 — the one slot the NPC and
    Monster matrices never exercise."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    bbeg_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=bbeg_id,
                kind="character",
                name="Vex",
                data={
                    "role": "BBEG",
                    "stat_block": {
                        "identity": {"role": "BBEG", "race": "Tiefling", "level": 9},
                        "attributes": {
                            "str": 12,
                            "dex": 14,
                            "con": 14,
                            "int": 16,
                            "wis": 12,
                            "cha": 18,
                        },
                        "combat": {"ac": 15, "hp": 120},
                        "skills": [],
                        "traits": [],
                        "actions": [],
                        "spells": [],
                    },
                    "boss": {
                        "lair_actions": "None.",
                        "legendary_actions": "Vex takes 3 legendary actions.",
                        "immunities": "None.",
                        "vulnerabilities": "None.",
                    },
                },
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Lair"),
        ],
        edges=[
            models.EdgeInput(
                src=bbeg_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    metadata = _owlbear(client, campaign_id, bbeg_id).json()
    assert metadata[_fk("Z001")] == 9
    assert _fk("Z016") not in metadata
    assert metadata[_fk("Z038")] == "Vex takes 3 legendary actions."


def test_owlbear_bare_skill_names_and_stripped_spells(client: Any) -> None:
    """PATCH 7 (review round 1): a skill with a missing/unparseable bonus
    keeps its bare name (no invented bonus); spell names ship stripped."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    odd_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=odd_id,
                kind="character",
                name="Odd",
                data={
                    "stat_block": {
                        "identity": {"role": "NPC", "race": "Human", "level": 1},
                        "attributes": {
                            "str": 10,
                            "dex": 10,
                            "con": 10,
                            "int": 10,
                            "wis": 10,
                            "cha": 10,
                        },
                        "combat": {"ac": 10, "hp": 4},
                        "skills": [
                            {"name": "Perception", "bonus": 2},
                            {"name": "History"},
                            {"name": "Arcana", "bonus": "high"},
                        ],
                        "traits": [],
                        "actions": [],
                        "spells": ["  Fireball  "],
                    },
                },
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Library"),
        ],
        edges=[
            models.EdgeInput(
                src=odd_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    metadata = _owlbear(client, campaign_id, odd_id).json()
    assert metadata[_fk("Z014")] == "Perception +2, History, Arcana"
    assert metadata[_fk("Z039")] == [
        {"id": f"{odd_id[-8:]}-0", "name": "Fireball", "description": ""}
    ]


def test_portrait_url_signs_newest_image_despite_newer_video(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mint only ever signs images: a newer video row (a reveal clip
    committed after the portrait) must not shadow the newest portrait."""
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", "test-secret-5-2")
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    portrait = add_media(campaign_id, sera_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, portrait)
    clip = add_media(campaign_id, sera_id, f"{new_id()}.mp4", "video")
    _write_media_file(tmp_path, campaign_id, clip)
    assert portrait.filename in _mint_url(client, campaign_id, sera_id)["url"]


def test_portrait_url_placeholder_origin_warns(
    client: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PATCH 4 (review round 1): minting against the shipped placeholder
    origin logs exactly one operator-misconfig warning (the URL is valid
    but Forge can never fetch it); a configured origin stays silent."""
    from app.core.config import DEFAULT_BASE_URL

    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", "test-secret-5-2")
    monkeypatch.delenv("MYTHOSCIRCLE_BASE_URL", raising=False)
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    row = add_media(campaign_id, sera_id, f"{new_id()}.png", "image")
    _write_media_file(tmp_path, campaign_id, row)
    with caplog.at_level(logging.WARNING):
        body = _mint_url(client, campaign_id, sera_id)
    assert body["url"].startswith(DEFAULT_BASE_URL)
    warnings = [r for r in caplog.records if "portrait_url_placeholder_origin" in r.getMessage()]
    assert len(warnings) == 1


def test_portrait_url_origin_from_config_file(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATCH 10d (review round 1): [server].base_url flows into the minted
    origin end to end; without it the code default does. The config cache
    is reset around the read so no other test observes the tmp file."""
    from app.core.config import reset_runtime_config

    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_URL_SECRET", "test-secret-5-2")
    monkeypatch.delenv("MYTHOSCIRCLE_BASE_URL", raising=False)
    config_path = tmp_path / "origin.toml"
    config_path.write_text('[server]\nbase_url = "https://config.example.test"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(config_path))
    reset_runtime_config()
    try:
        _register_login(client)
        campaign_id = _create_campaign(client).json()["id"]
        sera_id, _, _ = _commit_owlbear_cast(campaign_id)
        row = add_media(campaign_id, sera_id, f"{new_id()}.png", "image")
        _write_media_file(tmp_path, campaign_id, row)
        assert _mint_url(client, campaign_id, sera_id)["url"].startswith(
            "https://config.example.test/api/campaigns/"
        )
    finally:
        reset_runtime_config()


# ---------------------------------------------------------------------------
# Structured attack damage + the optional mechanics aspects (spec 2026-09-11)
#
# Additive export surface: the Forge Z035 entry states the damage the PARTS
# imply, the sheet renders every optional aspect, and a block committed
# before these fields existed renders exactly as it did.
# ---------------------------------------------------------------------------

#: The new optional aspects, one group per rendered sheet region: the
#: stat-block fragment carrying it and markup ONLY that fragment emits
#: (so "absent" is assertable as well as "present").
_NEW_STAT_GROUPS: dict[str, dict[str, Any]] = {
    "hit_dice": {
        "commit": {"combat": {"ac": 20, "hp": 140, "hit_dice": "24d10 + 192", "speed": "30 ft."}},
        "markup": "<dt>Hp</dt><dd>140</dd><dt>Hit dice</dt><dd>24d10 + 192</dd><dt>Speed</dt>",
    },
    "saves": {
        # Committed wis-first: the sheet renders the canonical score order.
        "commit": {"saves": {"wis": 18, "con": 15}},
        "markup": "<dt>Con</dt><dd>15</dd><dt>Wis</dt><dd>18</dd>",
    },
    "initiative": {"commit": {"initiative": 3}, "markup": "<h4>Initiative</h4><p>3</p>"},
    "passive_perception": {
        "commit": {"passive_perception": 14},
        "markup": "<h4>Passive perception</h4><p>14</p>",
    },
    "proficiency_bonus": {
        "commit": {"proficiency_bonus": 5},
        "markup": "<h4>Proficiency bonus</h4><p>5</p>",
    },
    "spellcasting": {
        "commit": {"spellcasting": {"slots": [4, 3, 3, 3, 1], "attack_bonus": 13, "dc": 21}},
        "markup": "<dt>Dc</dt><dd>21</dd><dt>Attack bonus</dt><dd>13</dd>"
        '<dt>Slots</dt><dd><ul class="entries"><li>4</li><li>3</li><li>3</li>'
        "<li>3</li><li>1</li></ul></dd>",
    },
    "features": {
        "commit": {"features": ["Divine Smite", "Aura of Protection"]},
        "markup": "<h4>Features</h4>"
        '<ul class="entries"><li>Divine Smite</li><li>Aura of Protection</li></ul>',
    },
    "resources": {
        "commit": {"resources": {"lay_on_hands": 85, "channel_divinity": 2}},
        "markup": "<dt>Lay on hands</dt><dd>85</dd><dt>Channel divinity</dt><dd>2</dd>",
    },
}


def _structured_block(**overrides: Any) -> dict[str, Any]:
    """A post-change character: the AR25 core plus whichever optional
    aspect the caller adds."""
    block: dict[str, Any] = {
        "identity": {"role": "NPC", "level": 12, "race": "Human", "class": "Paladin"},
        "attributes": {"str": 18, "dex": 10, "con": 16, "int": 9, "wis": 12, "cha": 20},
        "combat": {"ac": 20, "hp": 140, "speed": "30 ft."},
        "skills": [],
        "actions": [],
        "traits": [],
        "spells": [],
    }
    block.update(overrides)
    return block


def _block_sheet(client: Any, data: dict[str, Any]) -> str:
    """Commit one anchored character carrying ``data``; return its sheet."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    hero_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(id=hero_id, kind="character", name="Aldric", data=data),
            models.EntityInput(id=anchor_id, kind="place", name="Anchor"),
        ],
        edges=[
            models.EdgeInput(
                src=hero_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    return str(
        client.get(
            f"/api/campaigns/{campaign_id}/entities/{hero_id}/export", params={"format": "html"}
        ).text
    )


def _panel(html_body: str) -> str:
    """The stat-block panel of a rendered sheet."""
    start = html_body.index('class="stat-block"')
    return html_body[start : html_body.index("</section>", start)]


@pytest.mark.parametrize("group", sorted(_NEW_STAT_GROUPS))
def test_entity_html_new_stat_groups_render(client: Any, group: str) -> None:
    """NEW_STATS_PRESENT (sheet): each optional aspect renders its own
    markup — and a block lacking the others emits NO section for them
    (absent is never an empty heading)."""
    case = _NEW_STAT_GROUPS[group]
    sheet = _block_sheet(client, {"stat_block": _structured_block(**case["commit"])})
    assert case["markup"] in _panel(sheet)
    for other, other_case in _NEW_STAT_GROUPS.items():
        if other != group:
            assert other_case["markup"] not in sheet


def test_entity_html_new_sections_follow_the_spec_order(client: Any) -> None:
    """DISPLAY ORDER: the panel's order is the renderer's, not the commit
    order — the new aspects slot into it (saves, initiative, passive
    perception, proficiency bonus, spellcasting, features, resources) and
    hit dice ride with hp."""
    overrides: dict[str, Any] = {}
    for case in _NEW_STAT_GROUPS.values():
        overrides.update(case["commit"])
    sheet = _block_sheet(client, {"stat_block": _structured_block(**overrides)})
    panel = _panel(sheet)
    assert re.findall(r"<h4>(.*?)</h4>", panel) == [
        "Identity",
        "Combat",
        "Saves",
        "Initiative",
        "Passive perception",
        "Proficiency bonus",
        "Skills",
        "Actions",
        "Traits",
        "Spells",
        "Spellcasting",
        "Features",
        "Resources",
    ]
    assert "<dt>Hp</dt><dd>140</dd><dt>Hit dice</dt><dd>24d10 + 192</dd>" in panel
    assert "<dt>Lay on hands</dt><dd>85</dd>" in panel  # resources close the panel


def test_entity_html_pre_change_block_emits_no_new_sections(client: Any) -> None:
    """NEW_STATS_ABSENT (sheet): the 5-1 fixture — a block committed before
    these fields existed — renders the same six sections as always, with no
    new heading and no empty one."""
    campaign_id, vespera_id = _sheet_world(client)
    sheet = client.get(
        f"/api/campaigns/{campaign_id}/entities/{vespera_id}/export", params={"format": "html"}
    ).text
    panel = _panel(sheet)
    assert re.findall(r"<h4>(.*?)</h4>", panel) == [
        "Identity",
        "Combat",
        "Skills",
        "Actions",
        "Traits",
        "Spells",
    ]
    for case in _NEW_STAT_GROUPS.values():
        assert case["markup"] not in sheet


#: A post-change character: one action whose prose dropped the damage
#: numbers (the parts carry them) and one whose prose already states them
#: (the AR25 contract asks the model to keep both in step).
_STRUCTURED_ACTION_DATA: dict[str, Any] = {
    "name": "Aldric",
    "role": "NPC",
    "level_cr": "level 12",
    "race_type": "Human",
    "class_profession": "Paladin",
    "alignment": "LG",
    "stat_block": {
        "identity": {"role": "NPC", "race": "Human", "level": 12, "class": "Paladin"},
        "attributes": {"str": 18, "dex": 10, "con": 16, "int": 9, "wis": 12, "cha": 20},
        "combat": {"ac": 20, "hp": 140, "hit_dice": "24d10 + 192"},
        "skills": [],
        "traits": [],
        "spells": [],
        "actions": [
            {
                "name": "Oathblade",
                "to_hit": 18,
                "description": "Melee Weapon Attack: +18 to hit, reach 5 ft., one target.",
                "damage": [
                    {
                        "dice": "2d6",
                        "count": 2,
                        "sides": 6,
                        "bonus": 12,
                        "average": 19,
                        "type": "slashing",
                    },
                    # No ``average``: the sentence derives it from the dice.
                    {"dice": "3d10", "count": 3, "sides": 10, "bonus": 0, "type": "radiant"},
                ],
            },
            {
                "name": "Divine Smite",
                "description": "Hit: 16.5 (3d10) radiant damage.",
                "damage": [
                    {
                        "dice": "3d10",
                        "count": 3,
                        "sides": 10,
                        "bonus": 0,
                        "average": 16.5,
                        "type": "radiant",
                    }
                ],
            },
        ],
    },
}


def _structured_actions(client: Any) -> tuple[list[dict[str, str]], str]:
    """The committed structured-action character's Z035 entries."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    aldric_id, anchor_id = new_id(), new_id()
    commit_subgraph(
        campaign_id,
        entities=[
            models.EntityInput(
                id=aldric_id, kind="character", name="Aldric", data=dict(_STRUCTURED_ACTION_DATA)
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Anchor"),
        ],
        edges=[
            models.EdgeInput(
                src=aldric_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    metadata = _owlbear(client, campaign_id, aldric_id).json()
    return metadata[_fk("Z035")], aldric_id


def test_owlbear_action_entry_carries_the_damage_parts_sentence(client: Any) -> None:
    """STRUCTURED_DAMAGE (Forge): the Z035 entry's description states the
    numbers the PARTS imply — this action's prose dropped them — so the
    attack imports with the damage the auditor read."""
    entries, aldric_id = _structured_actions(client)
    assert entries[0] == {
        "id": f"{aldric_id[-8:]}-0",
        "name": "Oathblade",
        "description": "Melee Weapon Attack: +18 to hit, reach 5 ft., one target. "
        "Hit: 19 (2d6 + 12) slashing damage plus 16.5 (3d10) radiant damage.",
    }


def test_owlbear_action_entry_keeps_prose_that_states_the_numbers(client: Any) -> None:
    """The sentence is never appended twice: a description that already
    names the parts' dice (the AR25 contract keeps prose and parts in step)
    ships verbatim."""
    entries, aldric_id = _structured_actions(client)
    assert entries[1] == {
        "id": f"{aldric_id[-8:]}-1",
        "name": "Divine Smite",
        "description": "Hit: 16.5 (3d10) radiant damage.",
    }


def test_owlbear_pre_change_action_payload_unchanged(client: Any) -> None:
    """NEW_STATS_ABSENT (Forge): the 5-2 fixture's actions carry no damage
    parts, so Z035 ships the committed description verbatim — the additive
    rule adds nothing to a block written before it existed."""
    _register_login(client)
    campaign_id = _create_campaign(client).json()["id"]
    sera_id, _, _ = _commit_owlbear_cast(campaign_id)
    entries = _owlbear(client, campaign_id, sera_id).json()[_fk("Z035")]
    assert [entry["description"] for entry in entries] == [
        action["description"] for action in _SERA_DATA["stat_block"]["actions"]
    ]
    assert all("Hit:" not in entry["description"] for entry in entries)
