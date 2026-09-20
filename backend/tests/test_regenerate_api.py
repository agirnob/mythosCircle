"""REST surface of the regenerate job kind (spec-3.5).

Submission reuses POST /api/jobs with ``kind="regenerate"``; the store
validates the target + closed-section list at enqueue (404 unknown/
foreign target, 422 shape/section violations, zero rows). A valid job
drains through the worker like any other kind.
"""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import ids
from app.core.settings import LLMSettings
from app.pipeline.worker import run_next_job
from app.store import (
    EdgeInput,
    EntityInput,
    accept_candidate,
    app_db_url,
    commit_subgraph,
    create_campaign,
    enqueue_job,
    init_db,
    list_jobs,
    stage_candidates,
)

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    from app.api.auth import _login_limiter, _register_limiter

    _login_limiter.reset()
    _register_limiter.reset()
    yield
    _login_limiter.reset()
    _register_limiter.reset()


def _register(client: TestClient) -> str:
    """Register via the API (sets the session cookie); returns the id."""
    body = client.post(
        "/api/auth/register",
        json={"email": f"regen-{ids.new_id()}@example.com", "password": "password123"},
    )
    assert body.status_code == 201
    return str(body.json()["id"])


@pytest.fixture()
def job_api(tmp_path: Path, client: TestClient) -> Iterator[Callable[[], str]]:
    """Re-point the app's store at a fresh scratch DB; yields a maker for
    an EMPTY campaign owned by the session's account (spec-6-4: the jobs
    routes are session-gated — the owner registers via the API so every
    POST rides the cookie)."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'regen-api.db'}")
    account_id = _register(client)

    def make() -> str:
        return create_campaign(
            account_id,
            title="API World",
            description="",
            theme="High Fantasy",
            custom_lore="",
        ).id

    try:
        yield make
    finally:
        init_db(previous)


def _record(name: str = "Mira Vane", role: str = "NPC") -> dict[str, Any]:
    return {
        "name": name,
        "role": role,
        "personality": "warm",
        "secret": "s",
        "rumor": "r",
        "party_hook": "p",
        "level_cr": "level 5",
        "race_type": "Human",
        "class_profession": "Barkeep",
        "alignment": "NG",
        "appearance": "kind eyes",
        "background": "bg",
        "goals": "g",
        "relationships": "r",
        "voice_style": "v",
        "catchphrases": "c",
        # A valid block for level_cr "level 5": the AR25 gate now runs on
        # the regenerate path too, and an empty block is a real violation.
        "stat_block": {
            "identity": {"role": role, "level": 5, "race": "Human", "class": "Paladin"},
            "attributes": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 13},
            "combat": {"ac": 17, "hp": 140},
            "skills": [],
            "traits": [],
            "spells": [],
            "actions": [
                {
                    "name": "Axe",
                    "description": (
                        "Melee Weapon Attack: +5 to hit, reach 5 ft., one target. "
                        "Hit: 35 (10d6) slashing damage."
                    ),
                }
            ],
        },
        "world_integration": {
            "reputation": "r",
            "factions": "f",
            "current_location": "l",
            "reaction_matrix": "m",
            "on_defeat": "d",
        },
    }


def _commit_world(campaign_id: str) -> tuple[str, str]:
    mira_id, guild_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            EntityInput(kind="character", name="Mira Vane", data=_record(), id=mira_id),
            EntityInput(kind="faction", name="The Guild", id=guild_id),
        ],
        [EdgeInput(src=mira_id, dst=guild_id, type="member_of", counter=1)],
        base_revision=None,
    )
    return mira_id, guild_id


def _post(
    client: TestClient, campaign_id: str, target: dict[str, Any], sections: Any = None
) -> Any:
    payload: dict[str, Any] = {"target": target}
    if sections is not None:
        payload["sections"] = sections
    return client.post(
        "/api/jobs",
        json={"campaign_id": campaign_id, "kind": "regenerate", "payload": payload},
    )


def _zero_jobs(campaign_id: str) -> None:
    """No REGENERATE job was enqueued (the campaign may legitimately
    carry earlier generate/staging jobs)."""
    jobs, _cursor = list_jobs(campaign_id)
    assert [job for job, _position in jobs if job.kind == "regenerate"] == []


def test_post_regenerate_entity_201_and_drain(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """A valid whole-entity regenerate submission returns 201 queued;
    after the fake provider drains it, one new proposal for the entity
    exists, waiting on the accept screen."""
    _ = job_api  # each test re-points the store; the fixture ran first
    account_id = _register(client)
    campaign_id = create_campaign(
        account_id, title="Owned World", description="", theme="High Fantasy", custom_lore=""
    ).id
    mira_id, _guild_id = _commit_world(campaign_id)

    response = _post(client, campaign_id, {"kind": "entity", "id": mira_id}, None)
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "regenerate"
    assert body["state"] == "queued"

    def provider(prompt: str, settings: LLMSettings) -> str:
        out = json.loads(json.dumps(_record()))
        out["personality"] = "re-rolled"
        return json.dumps({"candidates": [out]})

    assert run_next_job(provider=provider, settings=SETTINGS) == body["id"]
    assert client.get(f"/api/jobs/{body['id']}").json()["state"] == "succeeded"
    candidates = client.get(f"/api/campaigns/{campaign_id}/candidates").json()
    assert candidates["candidates"], "a regen proposal must be served to the accept screen"


def test_post_regenerate_candidate_section_201(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """A per-section candidate re-roll enqueues 201 (the target resolves
    to this campaign's proposed row)."""
    campaign_id = job_api()
    mira_id, guild_id = _commit_world(campaign_id)
    job = enqueue_job(campaign_id, "generate", {"ask": "a rival"})
    payload = _record(name="Sable")
    payload["edges"] = [
        {"endpoint": guild_id, "direction": "outbound", "type": "rival_of", "counter": 1}
    ]
    candidate = stage_candidates(campaign_id, job.id, [payload])[0]

    response = _post(
        client, campaign_id, {"kind": "candidate", "id": candidate.id}, ["personality"]
    )
    assert response.status_code == 201
    assert response.json()["state"] == "queued"


def test_post_regenerate_positive_boss_path(client: TestClient, job_api: Callable[[], str]) -> None:
    """A BBEG/Monster target with ``sections=["boss"]`` enqueues 201 (the
    boss section is regenerable when the role requires it)."""
    campaign_id = job_api()
    boss_id, guild_id = ids.new_id(), ids.new_id()
    record = _record(name="The Dread", role="BBEG")
    record["boss"] = {
        "lair_actions": "l",
        "legendary_actions": "g",
        "immunities": "i",
        "vulnerabilities": "v",
    }
    commit_subgraph(
        campaign_id,
        [
            EntityInput(kind="character", name="The Dread", data=record, id=boss_id),
            EntityInput(kind="faction", name="The Guild", id=guild_id),
        ],
        [EdgeInput(src=boss_id, dst=guild_id, type="member_of", counter=1)],
        base_revision=None,
    )
    response = _post(client, campaign_id, {"kind": "entity", "id": boss_id}, ["boss"])
    assert response.status_code == 201


def test_post_regenerate_unknown_target_404_zero_rows(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """An unknown entity target is a 404 at enqueue (zero rows) — the
    wire code the I/O matrix pins (WHOLE_REGEN_ENTITY unknown/foreign)."""
    campaign_id = job_api()
    _commit_world(campaign_id)
    response = _post(client, campaign_id, {"kind": "entity", "id": "0" * 26}, None)
    assert response.status_code == 404
    _zero_jobs(campaign_id)


def test_post_regenerate_foreign_entity_404_zero_rows(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """An entity of ANOTHER campaign is the indistinguishable 404 — no
    oracle, zero rows."""
    campaign_id = job_api()
    other = job_api()
    _commit_world(campaign_id)
    mira_id, _guild_id = _commit_world(other)
    response = _post(client, campaign_id, {"kind": "entity", "id": mira_id}, None)
    assert response.status_code == 404
    _zero_jobs(campaign_id)


def test_post_regenerate_unknown_candidate_404_zero_rows(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    campaign_id = job_api()
    _commit_world(campaign_id)
    response = _post(client, campaign_id, {"kind": "candidate", "id": "0" * 26}, None)
    assert response.status_code == 404
    _zero_jobs(campaign_id)


def test_post_regenerate_foreign_candidate_404_zero_rows(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """A candidate of another campaign is the indistinguishable 404."""
    campaign_id = job_api()
    other = job_api()
    _commit_world(campaign_id)
    mira_id, guild_id = _commit_world(other)
    job = enqueue_job(other, "generate", {"ask": "a rival"})
    payload = _record(name="Sable")
    payload["edges"] = [
        {"endpoint": guild_id, "direction": "outbound", "type": "rival_of", "counter": 1}
    ]
    candidate = stage_candidates(other, job.id, [payload])[0]
    response = _post(client, campaign_id, {"kind": "candidate", "id": candidate.id}, None)
    assert response.status_code == 404
    _zero_jobs(campaign_id)


def test_post_regenerate_settled_candidate_422_zero_rows(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """A settled (accepted/rejected) candidate is a 422 at enqueue — only
    proposed candidates are re-rollable."""
    campaign_id = job_api()
    mira_id, guild_id = _commit_world(campaign_id)
    job = enqueue_job(campaign_id, "generate", {"ask": "a rival"})
    payload = _record(name="Sable")
    payload["edges"] = [
        {"endpoint": guild_id, "direction": "outbound", "type": "rival_of", "counter": 1}
    ]
    candidate = stage_candidates(campaign_id, job.id, [payload])[0]
    accept_candidate(campaign_id, candidate.id)  # settle accepted

    response = _post(client, campaign_id, {"kind": "candidate", "id": candidate.id}, None)
    assert response.status_code == 422
    _zero_jobs(campaign_id)


def test_post_regenerate_non_ar24_target_422_zero_rows(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """A target without an AR24 sectioned profile (build-in style entity
    data) is a 422 at enqueue — nothing to preserve byte-identically."""
    campaign_id = job_api()
    place_id, guild_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            EntityInput(
                kind="place",
                name="The Docks",
                data={"economy": {"level": 2}},
                id=place_id,
            ),
            EntityInput(kind="faction", name="The Guild", id=guild_id),
        ],
        [EdgeInput(src=place_id, dst=guild_id, type="located_in", counter=1)],
        base_revision=None,
    )
    response = _post(client, campaign_id, {"kind": "entity", "id": place_id}, None)
    assert response.status_code == 422
    _zero_jobs(campaign_id)


@pytest.mark.parametrize(
    "payload",
    [
        {"target": {"kind": "entity", "id": "E" * 26}, "sections": None, "ask": "x"},
        {"target": {"kind": "entity", "id": "E" * 26, "extra": 1}, "sections": None},
        {"target": {"kind": "entity", "id": "not-a-ulid"}, "sections": None},
        {"target": {"kind": "entity", "id": "E" * 26}, "sections": "personality"},
        {"target": "entity", "sections": None},
    ],
)
def test_post_regenerate_payload_shape_422_zero_rows(
    client: TestClient, job_api: Callable[[], str], payload: dict[str, Any]
) -> None:
    """Payload-shape violations — extra keys, malformed target, non-ULID
    id, non-list sections — are 422s with zero rows."""
    campaign_id = job_api()
    _commit_world(campaign_id)
    response = client.post(
        "/api/jobs",
        json={"campaign_id": campaign_id, "kind": "regenerate", "payload": payload},
    )
    assert response.status_code == 422
    _zero_jobs(campaign_id)


def test_post_regenerate_bad_sections_422_zero_rows(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """A section outside the closed set, an empty list, or ``boss`` on an
    NPC target is a 422 at enqueue — zero rows written."""
    campaign_id = job_api()
    mira_id, _guild_id = _commit_world(campaign_id)
    cases = [["name"], [], ["boss"]]
    for sections in cases:
        response = _post(client, campaign_id, {"kind": "entity", "id": mira_id}, sections)
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
    _zero_jobs(campaign_id)
