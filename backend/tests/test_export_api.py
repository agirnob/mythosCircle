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

from app.core.ids import new_id
from app.store import models
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
    coerce to null — a pure read endpoint never fails on its own data."""
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
                data={"probe": float("nan"), "inf": float("inf")},
            ),
            models.EntityInput(id=anchor_id, kind="place", name="Anchor"),
        ],
        edges=[models.EdgeInput(src=probe_id, dst=anchor_id, type="located_in", counter=1)],
    )
    body = client.get(f"/api/campaigns/{campaign_id}/export").json()
    assert body["entities"][0]["data"] == {"probe": None, "inf": None}
    md = client.get(f"/api/campaigns/{campaign_id}/export", params={"format": "markdown"}).text
    assert _fences(md) == [{"probe": None, "inf": None}, {}]
    # The neutral located_in renders bare in Relations (second neutral type).
    assert "[[Probe]] --located_in--> [[Anchor]]" in md
