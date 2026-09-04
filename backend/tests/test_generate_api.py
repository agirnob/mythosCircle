"""REST surface of the generate job kind + proposed-candidate reads
(spec-3.1): submission, poll, the authed owner-only candidates list, and
the FOREIGN_OWNER / ASK_EMPTY_WORLD matrix scenarios (wire codes stay
the conventions' own: 401/404/422).

Every test runs against its own scratch DB (the queue is one global
FIFO, AD-3); the background worker is disabled (conftest), so tests
drive the runner themselves with a fake provider.
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
    app_db_url,
    commit_subgraph,
    create_campaign,
    enqueue_job,
    init_db,
    register_account,
    session_scope,
)
from app.store.read import latest_revision, revision_chain, world_entities


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    """The auth limiters are module-global and keyed on the TestClient's
    fixed host ('testclient') — the lifecycle tests each register an
    owner, so without a reset the per-IP registration cap trips mid-run
    (test_auth_api's fixture, same rationale)."""
    from app.api.auth import _login_limiter, _register_limiter

    _login_limiter.reset()
    _register_limiter.reset()
    yield
    _login_limiter.reset()
    _register_limiter.reset()


SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")

_VALID_STAT_BLOCK: dict[str, Any] = {
    "identity": {"role": "NPC", "level": 5, "race": "Human"},
    "attributes": {"str": 14, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 8},
    "combat": {"ac": 16, "hp": 44},
}


def _owner_id() -> str:
    from app.core.ids import new_id

    return register_account(f"owner-genapi-{new_id()}@example.com", "password123").id


@pytest.fixture()
def job_api(tmp_path: Path, client: TestClient) -> Iterator[Callable[[], str]]:
    """Re-point the app's store at a fresh scratch DB; yields a maker for
    an EMPTY campaign (the test commits its own world when needed)."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'job-api.db'}")

    def make() -> str:
        return create_campaign(
            _owner_id(),
            title="API Test World",
            description="",
            theme="High Fantasy",
            custom_lore="",
        ).id

    try:
        yield make
    finally:
        init_db(previous)


def _commit_world(campaign_id: str) -> None:
    """A small committed world through the store's commit path."""
    bar_id, mira_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            EntityInput(kind="faction", name="The Gilded Bar", id=bar_id),
            EntityInput(
                kind="character",
                name="Mira Vane",
                data={"stat_block": _VALID_STAT_BLOCK},
                id=mira_id,
            ),
        ],
        [EdgeInput(src=bar_id, dst=mira_id, type="member_of", counter=1)],
    )


def _post_job(client: TestClient, campaign_id: str, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "campaign_id": campaign_id,
        "kind": "generate",
        "payload": {"ask": "a rival for Mira"},
    }
    payload.update(overrides)
    return client.post("/api/jobs", json=payload)


def _assert_envelope(response: Any, status: int, code: str) -> dict[str, Any]:
    """Assert the envelope shape and return the body."""
    assert response.status_code == status
    body: dict[str, Any] = response.json()
    assert set(body) <= {"code", "message", "details"}
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    return body


def _owned_campaign(client: TestClient) -> str:
    """Register via the API (sets the cookie) and create a campaign owned
    by that account — the candidates read requires ownership (AD-9)."""
    account_id = _register(client)
    return create_campaign(
        account_id, title="Owned World", description="", theme="High Fantasy", custom_lore=""
    ).id


def _register(client: TestClient) -> str:
    """Register via the API (sets the session cookie); returns the id."""
    body = client.post(
        "/api/auth/register",
        json={"email": f"ws-{ids.new_id()}@example.com", "password": "password123"},
    )
    assert body.status_code == 201
    return str(body.json()["id"])


def _fake_generate_output() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "name": f"Candidate {i}",
                "role": "NPC",
                "personality": "dry",
                "secret": "s",
                "rumor": "r",
                "party_hook": "p",
                "stat_block": _VALID_STAT_BLOCK,
                "edges": [{"endpoint": "C0", "direction": "outbound", "type": "rival_of"}],
            }
            for i in range(3)
        ]
    }


def _drain(client: TestClient, campaign_id: str) -> None:
    """Run the one queued job with the canned-output fake provider."""

    def provider(prompt: str, settings: LLMSettings) -> str:
        return json.dumps(_fake_generate_output())

    assert run_next_job(provider=provider, settings=SETTINGS) is not None
    _ = client  # the client keeps the cookie jar; nothing to do with it


# ---------------------------------------------------------------------------
# Submission (HAPPY_PATH / ASK_EMPTY_WORLD / payload contract)
# ---------------------------------------------------------------------------


def test_post_generate_job_201_and_poll(client: TestClient, job_api: Callable[[], str]) -> None:
    """A valid generate submission returns 201 queued; after the fake
    provider drains it, the job is succeeded with staged candidate ids."""
    campaign_id = job_api()
    _commit_world(campaign_id)
    body = _post_job(client, campaign_id).json()
    assert body["kind"] == "generate" and body["state"] == "queued"
    assert body["queue_position"] == 1
    _drain(client, campaign_id)
    status = client.get(f"/api/jobs/{body['id']}").json()
    assert status["state"] == "succeeded"
    assert len(status["result"]["candidate_ids"]) == 3


def test_post_generate_empty_world_422_no_job(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """ASK_EMPTY_WORLD: 422 envelope, zero rows written."""
    campaign_id = job_api()
    _assert_envelope(_post_job(client, campaign_id), 422, "validation_error")
    listed = client.get("/api/jobs", params={"campaign_id": campaign_id}).json()
    assert listed["jobs"] == []


def test_post_generate_bad_payload_422(client: TestClient, job_api: Callable[[], str]) -> None:
    """Payload violations are 422 validation_error envelopes."""
    campaign_id = job_api()
    _commit_world(campaign_id)
    for payload in ({"ask": ""}, {"ask": 5}, {"ask": "a", "b": 1}, {}, {"ask": "x" * 2001}):
        _assert_envelope(_post_job(client, campaign_id, payload=payload), 422, "validation_error")
    listed = client.get("/api/jobs", params={"campaign_id": campaign_id}).json()
    assert listed["jobs"] == []


def test_post_generate_unknown_campaign_404(client: TestClient) -> None:
    _assert_envelope(_post_job(client, ids.new_id()), 404, "not_found")


# ---------------------------------------------------------------------------
# Candidates read (auth + FOREIGN_OWNER + pagination)
# ---------------------------------------------------------------------------


def test_candidates_unauthed_401(client: TestClient, job_api: Callable[[], str]) -> None:
    """Unauthenticated candidates read is the generic 401 (AR29)."""
    campaign_id = job_api()
    _commit_world(campaign_id)
    _assert_envelope(client.get(f"/api/campaigns/{campaign_id}/candidates"), 401, "unauthorized")


def test_candidates_owner_reads_staged_rows(client: TestClient, job_api: Callable[[], str]) -> None:
    """The owner (cookie session) reads the staged candidates with the
    full AR19 payload; ids are ULIDs and rows carry job/kind/status."""
    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    _post_job(client, campaign_id)
    _drain(client, campaign_id)

    response = client.get(f"/api/campaigns/{campaign_id}/candidates")
    assert response.status_code == 200
    body = response.json()
    assert len(body["candidates"]) == 3 and body["next_cursor"] is None
    for row in body["candidates"]:
        assert row["kind"] == "entity" and row["status"] == "proposed"
        assert len(row["id"]) == 26 and len(row["job_id"]) == 26
        assert row["campaign_id"] == campaign_id
        payload = row["payload"]
        assert {
            "name",
            "role",
            "personality",
            "secret",
            "rumor",
            "party_hook",
            "stat_block",
            "edges",
        } <= set(payload)
        assert payload["stat_block"] == _VALID_STAT_BLOCK
        assert payload["edges"][0]["type"] == "rival_of"


def test_candidates_foreign_campaign_404_indistinguishable(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """FOREIGN_OWNER: another account's campaign (and an unknown id) are
    the single indistinguishable 404 — never a 403 oracle."""
    campaign_id = job_api()
    _commit_world(campaign_id)
    _register(client)  # account B (not the owner) now holds the cookie
    foreign = _assert_envelope(
        client.get(f"/api/campaigns/{campaign_id}/candidates"), 404, "not_found"
    )
    unknown = _assert_envelope(
        client.get(f"/api/campaigns/{ids.new_id()}/candidates"), 404, "not_found"
    )
    assert foreign["message"] == unknown["message"]


def test_candidates_pagination_and_cursor_guards(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """limit slices with next_cursor; a fabricated cursor is 422."""
    from app.core.pagination import encode_cursor
    from app.store import stage_candidates
    from app.store.db import session_scope
    from app.store.read import world_entities

    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        committed = next(iter(world_entities(session, campaign_id)))
    payload = {
        "name": "X",
        "role": "NPC",
        "personality": "p",
        "secret": "s",
        "rumor": "r",
        "party_hook": "h",
        "stat_block": _VALID_STAT_BLOCK,
        "edges": [{"endpoint": committed.id, "direction": "outbound", "type": "rival_of"}],
    }
    job = enqueue_job(campaign_id, "generate", {"ask": "list me"})
    stage_candidates(
        campaign_id,
        job.id,
        [dict(payload, name=f"Candidate {i}") for i in range(3)],
    )

    page = client.get(f"/api/campaigns/{campaign_id}/candidates", params={"limit": 2}).json()
    assert [row["payload"]["name"] for row in page["candidates"]] == ["Candidate 0", "Candidate 1"]
    assert page["next_cursor"] is not None
    rest = client.get(
        f"/api/campaigns/{campaign_id}/candidates",
        params={"cursor": page["next_cursor"], "limit": 2},
    ).json()
    assert [row["payload"]["name"] for row in rest["candidates"]] == ["Candidate 2"]
    assert rest["next_cursor"] is None
    _assert_envelope(
        client.get(
            f"/api/campaigns/{campaign_id}/candidates",
            params={"cursor": encode_cursor(ids.new_id())},
        ),
        422,
        "validation_error",
    )


# ---------------------------------------------------------------------------
# AR7: candidates invisible in export
# ---------------------------------------------------------------------------


def test_candidates_invisible_in_export(client: TestClient, job_api: Callable[[], str]) -> None:
    """AR7: after staging, the export shows only the committed entities —
    proposed candidates never leak into world reads."""
    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    _post_job(client, campaign_id)
    _drain(client, campaign_id)

    export = client.get(f"/api/campaigns/{campaign_id}/export").json()
    names = {entity["name"] for entity in export["entities"]}
    assert names == {"The Gilded Bar", "Mira Vane"}
    assert len(export["edges"]) == 1


# ---------------------------------------------------------------------------
# Candidate lifecycle routes (spec-3.2): accept / reject / status filter
# ---------------------------------------------------------------------------


def _stage_one(campaign_id: str, mira_id: str, name: str = "Sable Rook") -> str:
    """Stage one candidate directly through the store; returns its id."""
    from app.store import stage_candidates

    payload = {
        "name": name,
        "role": "NPC",
        "personality": "dry",
        "secret": "s",
        "rumor": "r",
        "party_hook": "p",
        "stat_block": _VALID_STAT_BLOCK,
        "edges": [{"endpoint": mira_id, "direction": "outbound", "type": "rival_of", "counter": 1}],
    }
    job = enqueue_job(campaign_id, "generate", {"ask": "a rival"})
    (row,) = stage_candidates(campaign_id, job.id, [payload])
    return row.id


def test_accept_route_commits_and_settles(client: TestClient, job_api: Callable[[], str]) -> None:
    """ACCEPT_HAPPY over the wire: 200 with the settled row; the world
    gains the new entity (+1 revision); the default list hides it."""

    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        mira_id = next(
            entity.id
            for entity in world_entities(session, campaign_id)
            if entity.name == "Mira Vane"
        )
    candidate_id = _stage_one(campaign_id, mira_id)

    response = client.post(f"/api/campaigns/{campaign_id}/candidates/{candidate_id}/accept")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == candidate_id and body["status"] == "accepted"

    export = client.get(f"/api/campaigns/{campaign_id}/export").json()
    assert "Sable Rook" in {entity["name"] for entity in export["entities"]}
    with session_scope() as session:
        assert len(list(revision_chain(session, campaign_id))) == 2  # seed + accept
    listed = client.get(f"/api/campaigns/{campaign_id}/candidates").json()
    assert listed["candidates"] == []  # default filter keeps the accept screen clean


def test_accept_route_stale_endpoint_422_row_stays_proposed(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """ACCEPT_STALE_ENDPOINT: 422 validation_error envelope; no revision;
    the row stays ``proposed`` (rebase-or-reject, AD-2)."""
    from app.store import delete_entity

    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        mira_id = next(
            entity.id
            for entity in world_entities(session, campaign_id)
            if entity.name == "Mira Vane"
        )
        head = latest_revision(session, campaign_id)
        assert head is not None
    candidate_id = _stage_one(campaign_id, mira_id)
    delete_entity(campaign_id, mira_id, cascade=True, base_revision=head.id)

    body = _assert_envelope(
        client.post(f"/api/campaigns/{campaign_id}/candidates/{candidate_id}/accept"),
        422,
        "validation_error",
    )
    assert "dangling" in body["message"].lower()
    listed = client.get(f"/api/campaigns/{campaign_id}/candidates").json()
    assert [row["id"] for row in listed["candidates"]] == [candidate_id]
    assert listed["candidates"][0]["status"] == "proposed"
    with session_scope() as session:
        assert len(list(revision_chain(session, campaign_id))) == 2  # seed + delete


def test_accept_route_unknown_candidate_404(client: TestClient, job_api: Callable[[], str]) -> None:
    """ACCEPT_UNKNOWN: an unknown candidate id under an owned campaign is
    the single 404 (the campaign itself existing keeps it off the
    campaign-404 path)."""
    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    _assert_envelope(
        client.post(f"/api/campaigns/{campaign_id}/candidates/{ids.new_id()}/accept"),
        404,
        "not_found",
    )


def test_accept_route_already_settled_409(client: TestClient, job_api: Callable[[], str]) -> None:
    """ACCEPT_ALREADY_SETTLED: a second accept is a 409 conflict."""
    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        mira_id = next(
            entity.id
            for entity in world_entities(session, campaign_id)
            if entity.name == "Mira Vane"
        )
    candidate_id = _stage_one(campaign_id, mira_id)
    assert (
        client.post(f"/api/campaigns/{campaign_id}/candidates/{candidate_id}/accept").status_code
        == 200
    )
    _assert_envelope(
        client.post(f"/api/campaigns/{campaign_id}/candidates/{candidate_id}/accept"),
        409,
        "conflict",
    )


def test_reject_route_settles_world_untouched(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """REJECT_STANDALONE: 200 rejected; no new revision; the row shows
    up only under ``?status=rejected``."""
    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        mira_id = next(
            entity.id
            for entity in world_entities(session, campaign_id)
            if entity.name == "Mira Vane"
        )
    candidate_id = _stage_one(campaign_id, mira_id)

    response = client.post(f"/api/campaigns/{campaign_id}/candidates/{candidate_id}/reject")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert client.get(f"/api/campaigns/{campaign_id}/candidates").json()["candidates"] == []
    rejected = client.get(
        f"/api/campaigns/{campaign_id}/candidates", params={"status": "rejected"}
    ).json()
    assert [row["id"] for row in rejected["candidates"]] == [candidate_id]
    export = client.get(f"/api/campaigns/{campaign_id}/export").json()
    assert "Sable Rook" not in {entity["name"] for entity in export["entities"]}
    with session_scope() as session:
        assert len(list(revision_chain(session, campaign_id))) == 1  # seed only


def test_reject_route_already_settled_409(client: TestClient, job_api: Callable[[], str]) -> None:
    """REJECT_ALREADY_SETTLED: a second reject is a 409 conflict."""
    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        mira_id = next(
            entity.id
            for entity in world_entities(session, campaign_id)
            if entity.name == "Mira Vane"
        )
    candidate_id = _stage_one(campaign_id, mira_id)
    assert (
        client.post(f"/api/campaigns/{campaign_id}/candidates/{candidate_id}/reject").status_code
        == 200
    )
    _assert_envelope(
        client.post(f"/api/campaigns/{campaign_id}/candidates/{candidate_id}/reject"),
        409,
        "conflict",
    )


def test_candidates_status_filter_rejected_shows_accepted_hides(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """The closed-set status filter: ``accepted`` and ``rejected`` pages
    show exactly the settled rows; the default stays ``proposed``."""
    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        mira_id = next(
            entity.id
            for entity in world_entities(session, campaign_id)
            if entity.name == "Mira Vane"
        )
    accepted_id = _stage_one(campaign_id, mira_id, name="Sable Rook")
    rejected_id = _stage_one(campaign_id, mira_id, name="Vex Marlow")
    assert (
        client.post(f"/api/campaigns/{campaign_id}/candidates/{accepted_id}/accept").status_code
        == 200
    )
    assert (
        client.post(f"/api/campaigns/{campaign_id}/candidates/{rejected_id}/reject").status_code
        == 200
    )

    accepted = client.get(
        f"/api/campaigns/{campaign_id}/candidates", params={"status": "accepted"}
    ).json()
    assert [row["id"] for row in accepted["candidates"]] == [accepted_id]
    rejected = client.get(
        f"/api/campaigns/{campaign_id}/candidates", params={"status": "rejected"}
    ).json()
    assert [row["id"] for row in rejected["candidates"]] == [rejected_id]
    default = client.get(f"/api/campaigns/{campaign_id}/candidates").json()
    assert default["candidates"] == []


def test_candidates_status_filter_junk_422(client: TestClient, job_api: Callable[[], str]) -> None:
    """STATUS_FILTER_BAD: junk status is a 422 validation envelope."""
    campaign_id = _owned_campaign(client)
    _assert_envelope(
        client.get(f"/api/campaigns/{campaign_id}/candidates", params={"status": "junk"}),
        422,
        "validation_error",
    )


def test_accept_route_bad_counter_422(client: TestClient, job_api: Callable[[], str]) -> None:
    """ACCEPT_BAD_COUNTER over the wire: a staged non-int counter (the
    staging validation does not check counter shape) is a 422 validation
    envelope; no revision; the row stays ``proposed``."""
    from app.store import stage_candidates

    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        mira_id = next(
            entity.id
            for entity in world_entities(session, campaign_id)
            if entity.name == "Mira Vane"
        )
    payload = {
        "name": "Sable Rook",
        "role": "NPC",
        "personality": "dry",
        "secret": "s",
        "rumor": "r",
        "party_hook": "p",
        "stat_block": _VALID_STAT_BLOCK,
        "edges": [
            {"endpoint": mira_id, "direction": "outbound", "type": "rival_of", "counter": "three"}
        ],
    }
    job = enqueue_job(campaign_id, "generate", {"ask": "a rival"})
    (row,) = stage_candidates(campaign_id, job.id, [payload])

    body = _assert_envelope(
        client.post(f"/api/campaigns/{campaign_id}/candidates/{row.id}/accept"),
        422,
        "validation_error",
    )
    assert "counter" in body["message"].lower()
    with session_scope() as session:
        assert len(list(revision_chain(session, campaign_id))) == 1  # seed only
    listed = client.get(f"/api/campaigns/{campaign_id}/candidates").json()
    assert [candidate["id"] for candidate in listed["candidates"]] == [row.id]
    assert listed["candidates"][0]["status"] == "proposed"


def test_accept_route_malformed_payload_422(client: TestClient, job_api: Callable[[], str]) -> None:
    """A staged row whose payload lost its name (only possible by
    bypassing the staging contract) is a 422 ``InvalidCandidateError``
    envelope via POST accept — never a 500."""
    from app.store import models, stage_candidates

    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        mira_id = next(
            entity.id
            for entity in world_entities(session, campaign_id)
            if entity.name == "Mira Vane"
        )
    payload = {
        "name": "Sable Rook",
        "role": "NPC",
        "personality": "dry",
        "secret": "s",
        "rumor": "r",
        "party_hook": "p",
        "stat_block": _VALID_STAT_BLOCK,
        "edges": [{"endpoint": mira_id, "direction": "outbound", "type": "rival_of", "counter": 1}],
    }
    job = enqueue_job(campaign_id, "generate", {"ask": "a rival"})
    (row,) = stage_candidates(campaign_id, job.id, [payload])
    with session_scope() as session:
        staged = session.get(models.ProposedCandidate, row.id)
        assert staged is not None
        staged.payload = {key: value for key, value in staged.payload.items() if key != "name"}

    body = _assert_envelope(
        client.post(f"/api/campaigns/{campaign_id}/candidates/{row.id}/accept"),
        422,
        "validation_error",
    )
    assert "name" in body["message"].lower()


def test_status_filter_paging_ignores_anchor_status(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """Status filter x cursor pagination: the rowid anchor is resolved
    regardless of the settled/active status split — settling the page's
    anchor row and paging ``?status=proposed`` from it continues with the
    remaining proposed rows, skipping the anchor itself."""
    campaign_id = _owned_campaign(client)
    _commit_world(campaign_id)
    with session_scope() as session:
        mira_id = next(
            entity.id
            for entity in world_entities(session, campaign_id)
            if entity.name == "Mira Vane"
        )
    first_id = _stage_one(campaign_id, mira_id, name="Sable Rook")
    second_id = _stage_one(campaign_id, mira_id, name="Vex Marlow")
    third_id = _stage_one(campaign_id, mira_id, name="Iseult Kray")

    page_one = client.get(f"/api/campaigns/{campaign_id}/candidates", params={"limit": 2}).json()
    assert [row["id"] for row in page_one["candidates"]] == [first_id, second_id]
    anchor = page_one["next_cursor"]
    assert anchor is not None

    # Settle the anchor row (second); its rowid must still anchor the
    # next proposed-only page.
    assert (
        client.post(f"/api/campaigns/{campaign_id}/candidates/{second_id}/reject").status_code
        == 200
    )
    page_two = client.get(
        f"/api/campaigns/{campaign_id}/candidates",
        params={"status": "proposed", "cursor": anchor, "limit": 2},
    ).json()
    assert [row["id"] for row in page_two["candidates"]] == [third_id]
    assert page_two["next_cursor"] is None


def test_lifecycle_routes_unauthed_401(client: TestClient, job_api: Callable[[], str]) -> None:
    """FOREIGN_OWNER (auth half): unauthenticated accept/reject are the
    generic 401 (AR29)."""
    campaign_id = job_api()
    _commit_world(campaign_id)
    for suffix in ("accept", "reject"):
        _assert_envelope(
            client.post(f"/api/campaigns/{campaign_id}/candidates/{ids.new_id()}/{suffix}"),
            401,
            "unauthorized",
        )


def test_lifecycle_routes_foreign_campaign_404_indistinguishable(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """FOREIGN_OWNER (ownership half): another account's campaign (and an
    unknown one) are the single indistinguishable 404 — never a 403."""
    campaign_id = job_api()
    _commit_world(campaign_id)
    _register(client)  # account B (not the owner) now holds the cookie
    foreign = _assert_envelope(
        client.post(f"/api/campaigns/{campaign_id}/candidates/{ids.new_id()}/accept"),
        404,
        "not_found",
    )
    unknown = _assert_envelope(
        client.post(f"/api/campaigns/{ids.new_id()}/candidates/{ids.new_id()}/reject"),
        404,
        "not_found",
    )
    assert foreign["message"] == unknown["message"]
