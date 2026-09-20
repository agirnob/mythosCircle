"""REST + WebSocket surface of the persistent generation queue (AD-17).

Pins the wire contract: envelope codes (4xx = user error, never a state
change), cursor-paginated lists, cancel flow, and the exact WS message
shapes the store's change-listener drives.

Every test runs against its own scratch DB: the queue is one global FIFO
and claims are global (AD-3), so leftover jobs from a shared DB would
leak through ``claim_next_job`` across tests.
"""

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.core import ids
from app.core.pagination import encode_cursor
from app.core.settings import MAX_PENDING_PER_CAMPAIGN
from app.store import (
    app_db_url,
    cancel_job,
    claim_next_job,
    complete_job,
    create_campaign,
    fail_job,
    init_db,
    job_status,
    list_jobs,
    report_progress,
)


@pytest.fixture(autouse=True)
def _reset_limiters() -> Any:
    """Register-quota isolation (the edges/API convention): the register
    limiter is module-global and keyed on the TestClient's fixed host —
    every test here registers via the API (job_api + the newcomer
    switches)."""
    from app.api.auth import _login_limiter, _register_limiter

    _login_limiter.reset()
    _register_limiter.reset()
    yield
    _login_limiter.reset()
    _register_limiter.reset()


def _authed_owned_campaign(client: TestClient) -> str:
    """Register via the API (sets the session cookie) and create a campaign
    owned by that account — the WS subscription requires ownership (AD-9)."""
    email = f"ws-{ids.new_id()}@example.com"
    body = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert body.status_code == 201
    account_id = body.json()["id"]
    return create_campaign(
        account_id,
        title="WS Test World",
        description="",
        theme="High Fantasy",
        custom_lore="",
    ).id


def _switch_account(client: TestClient) -> None:
    """Register a fresh second account via the API — the TestClient jar
    holds one session, so the cookie now belongs to the newcomer."""
    email = f"foreign-{ids.new_id()}@example.com"
    body = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert body.status_code == 201


ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


@pytest.fixture()
def job_api(tmp_path: Path, client: TestClient) -> Iterator[Callable[[], str]]:
    """Re-point the app's store at a fresh scratch DB; yields a campaign
    maker for the session's OWNED account.

    The owner registers via the API so the session cookie rides every
    REST request (spec-6-4: all four jobs routes are session-gated — a
    store-made campaign without a cookie would 401 on its own posts).
    The store engine is module-global (store.db), and the test client's
    endpoints use it at call time, so re-pointing per test isolates the
    queue — every claim is deterministic.
    """
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'job-api.db'}")
    email = f"jobs-{ids.new_id()}@example.com"
    body = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert body.status_code == 201
    account_id = body.json()["id"]

    def make() -> str:
        return create_campaign(
            account_id,
            title="API Test World",
            description="",
            theme="High Fantasy",
            custom_lore="",
        ).id

    try:
        yield make
    finally:
        init_db(previous)


def _post_job(client: TestClient, campaign_id: str, **overrides: Any) -> Response:
    payload: dict[str, Any] = {
        "campaign_id": campaign_id,
        "kind": "text",
        "payload": {"ask": "who?"},
    }
    payload.update(overrides)
    response: Response = client.post("/api/jobs", json=payload)
    return response


def _assert_envelope(response: Response, status: int, code: str) -> dict[str, Any]:
    """Assert the envelope shape and return the body."""
    assert response.status_code == status
    body: dict[str, Any] = response.json()
    assert set(body) <= {"code", "message", "details"}
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    return body


# ---------------------------------------------------------------------------
# Submission + status
# ---------------------------------------------------------------------------


def test_post_job_201_and_get_status(client: TestClient, job_api: Callable[[], str]) -> None:
    campaign_id = job_api()
    response = _post_job(client, campaign_id)
    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "queued"
    assert body["queue_position"] == 1
    assert body["kind"] == "text"
    assert body["campaign_id"] == campaign_id
    assert body["payload"] == {"ask": "who?"}
    assert body["progress"] == 0.0
    assert body["max_llm_calls"] == 64
    assert body["max_media_calls"] == 8
    assert body["error"] is None
    assert body["started_at"] is None and body["finished_at"] is None
    assert ISO_Z_RE.match(body["created_at"])
    job_id = body["id"]
    assert ULID_RE.match(job_id)

    status = client.get(f"/api/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json() == body


def test_post_job_honors_optional_budgets(client: TestClient, job_api: Callable[[], str]) -> None:
    campaign_id = job_api()
    body = _post_job(client, campaign_id, max_llm_calls=3, max_media_calls=1).json()
    assert body["max_llm_calls"] == 3 and body["max_media_calls"] == 1


# ---------------------------------------------------------------------------
# ENQUEUE_BAD_INPUT / ENQUEUE_DUP_ID / ENQUEUE_CAP (envelope codes, no state change)
# ---------------------------------------------------------------------------


def test_post_job_invalid_input_422_no_state_change(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    campaign_id = job_api()
    cases: list[dict[str, Any]] = [
        {"kind": "simulate"},  # kind outside the closed set
        {"payload": ["not", "a", "dict"]},  # payload not an object
        {"max_llm_calls": -1},  # negative budget
        {"max_media_calls": -1},
        {"job_id": "not-a-ulid"},  # store-level rejection
    ]
    for overrides in cases:
        _assert_envelope(_post_job(client, campaign_id, **overrides), 422, "validation_error")
    listed = client.get("/api/jobs", params={"campaign_id": campaign_id}).json()
    assert listed["jobs"] == []  # zero rows written


def test_post_build_in_job_201(client: TestClient, job_api: Callable[[], str]) -> None:
    """A valid build-in submission returns 201 with kind build_in and a
    queue position (spec-2.1 BUILDIN_SUBMIT)."""
    campaign_id = job_api()
    response = client.post(
        "/api/jobs",
        json={
            "campaign_id": campaign_id,
            "kind": "build_in",
            "payload": {"places": ["Greymarch"], "factions": ["The Guild"], "notes": "rain"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "build_in"
    assert body["state"] == "queued"
    assert body["queue_position"] == 1


def test_post_build_in_blank_422(client: TestClient, job_api: Callable[[], str]) -> None:
    """An all-blank build-in submission is a 422 before any row is written
    (spec-2.1 BUILDIN_ALL_BLANK)."""
    campaign_id = job_api()
    response = client.post(
        "/api/jobs",
        json={
            "campaign_id": campaign_id,
            "kind": "build_in",
            "payload": {"places": [], "factions": [], "key_figures": [], "notes": ""},
        },
    )
    _assert_envelope(response, 422, "validation_error")


def test_post_video_prompt_draft_201_with_supplied_prompt_render(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """Spec-4.6 wire contract: a valid ``video_prompt`` draft enqueues
    201, and a ``video`` payload carrying the DM's approved prompt
    enqueues 201 (the prompt rides the payload dict, no wire change)."""
    from app.store import commit_subgraph, models

    campaign_id = job_api()
    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(
                kind="character",
                name="Vashka",
                data={
                    "name": "Vashka the Unmaker",
                    "role": "BBEG",
                    "appearance": {"face": "a mask of fused iron"},
                },
                id=entity_id,
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    draft = client.post(
        "/api/jobs",
        json={
            "campaign_id": campaign_id,
            "kind": "video_prompt",
            "payload": {"entity_id": entity_id},
        },
    )
    assert draft.status_code == 201
    assert draft.json()["kind"] == "video_prompt"
    assert draft.json()["state"] == "queued"
    render = client.post(
        "/api/jobs",
        json={
            "campaign_id": campaign_id,
            "kind": "video",
            "payload": {"entity_id": entity_id, "prompt": "DM-approved reveal"},
        },
    )
    assert render.status_code == 201
    assert render.json()["kind"] == "video"


def test_post_video_prompt_bad_payload_422(client: TestClient, job_api: Callable[[], str]) -> None:
    """Spec-4.6 DRAFT_NO_* envelope: a forced draft for a non-boss or an
    appearance-less boss is the 422 validation_error envelope, zero rows."""
    from app.store import commit_subgraph, models

    campaign_id = job_api()
    npc_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        campaign_id,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(
                kind="character",
                name="Mira",
                data={"name": "Mira", "role": "NPC", "appearance": {"face": "sharp"}},
                id=npc_id,
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=npc_id, type="located_in", counter=1)],
    )
    response = client.post(
        "/api/jobs",
        json={
            "campaign_id": campaign_id,
            "kind": "video_prompt",
            "payload": {"entity_id": npc_id},
        },
    )
    _assert_envelope(response, 422, "validation_error")
    listed = client.get("/api/jobs", params={"campaign_id": campaign_id}).json()
    assert listed["jobs"] == []  # zero rows written


def test_post_job_missing_campaign_fields_422(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    # Payload with neither campaign_id nor kind is rejected by validation
    # (the session gate has already passed — job_api registered the owner).
    response = client.post("/api/jobs", json={"payload": {}})
    _assert_envelope(response, 422, "validation_error")


def test_post_job_duplicate_job_id_409(client: TestClient, job_api: Callable[[], str]) -> None:
    campaign_id = job_api()
    job_id = ids.new_id()
    assert _post_job(client, campaign_id, job_id=job_id).status_code == 201
    body = _assert_envelope(_post_job(client, campaign_id, job_id=job_id), 409, "conflict")
    assert "job already exists" in body["message"]
    listed = client.get("/api/jobs", params={"campaign_id": campaign_id}).json()
    assert len(listed["jobs"]) == 1  # idempotent by job-id: no double enqueue


def test_post_job_unknown_campaign_404(client: TestClient, job_api: Callable[[], str]) -> None:
    _assert_envelope(_post_job(client, ids.new_id()), 404, "not_found")


def test_jobs_anon_generic_401_all_four_routes(client: TestClient) -> None:
    """JOBS_ANON (spec-6-4): with no session cookie every jobs route is
    the generic 401 envelope — the anonymous hole (list anyone's queue,
    enqueue on any campaign, cancel anyone's job) is closed."""
    _assert_envelope(_post_job(client, ids.new_id()), 401, "unauthorized")
    _assert_envelope(
        client.get("/api/jobs", params={"campaign_id": ids.new_id()}),
        401,
        "unauthorized",
    )
    _assert_envelope(client.get(f"/api/jobs/{ids.new_id()}"), 401, "unauthorized")
    _assert_envelope(client.post(f"/api/jobs/{ids.new_id()}/cancel"), 401, "unauthorized")
    # The BARE list (no query at all) is 401 too — the session gate fires
    # before query validation (the deployed flip: 422 -> 401).
    _assert_envelope(client.get("/api/jobs"), 401, "unauthorized")


def test_foreign_campaign_create_and_list_404(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """JOBS_FOREIGN create/list (spec-6-4): another owner's campaign is a
    404 byte-identical to an unknown one (no oracle), and the stranger's
    create writes zero job rows."""
    campaign_id = job_api()  # cookie = owner A
    _switch_account(client)  # cookie = account B
    foreign = _post_job(client, campaign_id)
    _assert_envelope(foreign, 404, "not_found")
    assert foreign.json() == _post_job(client, ids.new_id()).json()  # cipher-identical
    jobs, _cursor = list_jobs(campaign_id)
    assert jobs == []  # zero rows written by the stranger's 404
    foreign_list = client.get("/api/jobs", params={"campaign_id": campaign_id})
    unknown_list = client.get("/api/jobs", params={"campaign_id": ids.new_id()})
    _assert_envelope(foreign_list, 404, "not_found")
    assert foreign_list.json() == unknown_list.json()
    # The ownership 404 fires BEFORE cursor decoding (the route's
    # documented order): a foreign campaign plus an undecodable cursor is
    # the same 404 as the no-cursor one — the gate wins over cursor
    # validation, so the cursor error surface leaks nothing to a stranger.
    undecodable = client.get(
        "/api/jobs", params={"campaign_id": campaign_id, "cursor": "not-a-cursor"}
    )
    _assert_envelope(undecodable, 404, "not_found")
    assert undecodable.json() == foreign_list.json()


def test_foreign_job_get_404_identical_to_unknown(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """JOBS_FOREIGN_GET (spec-6-4): a job of another owner's campaign is
    the store's unknown-job 404 for THAT id — a stranger cannot tell it
    exists (the caller supplied the id, so the echo leaks nothing)."""
    campaign_id = job_api()
    job_id = _post_job(client, campaign_id).json()["id"]
    _switch_account(client)
    foreign = client.get(f"/api/jobs/{job_id}")
    _assert_envelope(foreign, 404, "not_found")
    assert foreign.json() == {
        "code": "not_found",
        "message": f"unknown job: {job_id}",
        "details": None,
    }


def test_foreign_cancel_404_state_and_row_untouched(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    """JOBS_FOREIGN_CANCEL (spec-6-4): cancel checks ownership BEFORE any
    mutation — a foreign cancel is the unknown-job 404 and the job's
    state and row are untouched (still queued, still cancellable)."""
    campaign_id = job_api()
    job_id = _post_job(client, campaign_id).json()["id"]
    _switch_account(client)
    foreign = client.post(f"/api/jobs/{job_id}/cancel")
    _assert_envelope(foreign, 404, "not_found")
    assert foreign.json() == {
        "code": "not_found",
        "message": f"unknown job: {job_id}",
        "details": None,
    }
    job, _position = job_status(job_id)
    assert job.state == "queued"  # the stranger's 404 mutated nothing
    assert cancel_job(job_id).state == "cancelled"  # the owner's path still works


def test_post_job_queue_full_409(
    client: TestClient, job_api: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MAX_PENDING_PER_CAMPAIGN, "1")
    campaign_id = job_api()
    assert _post_job(client, campaign_id).status_code == 201
    _assert_envelope(_post_job(client, campaign_id), 409, "conflict")
    listed = client.get("/api/jobs", params={"campaign_id": campaign_id}).json()
    assert len(listed["jobs"]) == 1  # no state change


def test_get_unknown_job_404(client: TestClient, job_api: Callable[[], str]) -> None:
    _assert_envelope(client.get(f"/api/jobs/{ids.new_id()}"), 404, "not_found")


# ---------------------------------------------------------------------------
# Cancel flow
# ---------------------------------------------------------------------------


def test_cancel_queued_via_api(client: TestClient, job_api: Callable[[], str]) -> None:
    campaign_id = job_api()
    first = _post_job(client, campaign_id).json()
    second = _post_job(client, campaign_id).json()

    response = client.post(f"/api/jobs/{first['id']}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "cancelled"
    assert body["queue_position"] is None
    assert body["finished_at"] is not None

    # Slot freed: the runner claims the remaining queued job.
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == second["id"]

    # Terminal jobs cannot be cancelled again.
    _assert_envelope(client.post(f"/api/jobs/{first['id']}/cancel"), 409, "conflict")
    status = client.get(f"/api/jobs/{first['id']}").json()
    assert status["state"] == "cancelled"  # state unchanged


def test_cancel_running_via_api(client: TestClient, job_api: Callable[[], str]) -> None:
    campaign_id = job_api()
    job_id = _post_job(client, campaign_id).json()["id"]
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id

    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert claim_next_job() is None  # slot freed, nothing queued


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_jobs_campaign_scoped_and_paginated(
    client: TestClient, job_api: Callable[[], str]
) -> None:
    campaign_a = job_api()
    campaign_b = job_api()
    a1 = _post_job(client, campaign_a).json()["id"]
    b1 = _post_job(client, campaign_b).json()["id"]
    a2 = _post_job(client, campaign_a).json()["id"]

    listed = client.get("/api/jobs", params={"campaign_id": campaign_a}).json()
    assert [job["id"] for job in listed["jobs"]] == [a1, a2]
    assert [job["queue_position"] for job in listed["jobs"]] == [1, 2]
    assert listed["next_cursor"] is None

    b_listed = client.get("/api/jobs", params={"campaign_id": campaign_b}).json()
    assert [job["id"] for job in b_listed["jobs"]] == [b1]
    # A's jobs never appear in B's list (per-campaign privacy, AD-9).

    page = client.get("/api/jobs", params={"campaign_id": campaign_a, "limit": 1}).json()
    assert [job["id"] for job in page["jobs"]] == [a1]
    assert page["next_cursor"] is not None
    page2 = client.get(
        "/api/jobs",
        params={"campaign_id": campaign_a, "limit": 1, "cursor": page["next_cursor"]},
    ).json()
    assert [job["id"] for job in page2["jobs"]] == [a2]

    empty = client.get("/api/jobs", params={"campaign_id": job_api()}).json()
    assert empty["jobs"] == [] and empty["next_cursor"] is None


def test_list_jobs_bad_cursor_422(client: TestClient, job_api: Callable[[], str]) -> None:
    """A cursor that cannot decode to a ULID is a 422 validation_error
    envelope — the route's decode-failure branch (never a 500)."""
    response = client.get("/api/jobs", params={"campaign_id": job_api(), "cursor": "not-a-cursor"})
    _assert_envelope(response, 422, "validation_error")


def test_list_jobs_fabricated_cursor_422(client: TestClient, job_api: Callable[[], str]) -> None:
    """UNKNOWN_CURSOR (epic-1 retro item 2): a decode-valid cursor naming
    no job is a 422 user error on the wire, never a silent page reset."""
    campaign_id = job_api()
    cursor = encode_cursor(ids.new_id())
    response = client.get("/api/jobs", params={"campaign_id": campaign_id, "cursor": cursor})
    _assert_envelope(response, 422, "validation_error")


def _drive_transition(fn: Callable[[], Any]) -> Any:
    """Run a store transition on a background thread — the exact shape of
    1.4's worker — so the test's main thread is always free to receive on
    the WebSocket (TestClient's portal serializes its calls, so firing the
    broadcast from the main thread while ``receive_json`` is pending can
    deadlock the harness)."""
    import threading

    result: dict[str, Any] = {}
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # pragma: no cover - error path
            errors.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(timeout=15)
    if errors:
        raise errors[0]
    return result.get("value")


def test_ws_jobs_broadcasts_transitions(client: TestClient, job_api: Callable[[], str]) -> None:
    campaign_id = _authed_owned_campaign(
        client
    )  # job_api re-points the store at a fresh scratch DB
    with client.websocket_connect(f"/api/ws/jobs?campaign_id={campaign_id}") as ws:
        first = _post_job(client, campaign_id).json()["id"]
        second = _post_job(client, campaign_id).json()["id"]
        assert ws.receive_json() == {
            "type": "queue_changed",
            "job_id": first,
            "state": "queued",
            "queue_position": 1,
        }
        assert ws.receive_json() == {
            "type": "queue_changed",
            "job_id": second,
            "state": "queued",
            "queue_position": 2,
        }
        claimed = _drive_transition(lambda: claim_next_job())
        assert claimed is not None and claimed.id == first  # FIFO
        assert ws.receive_json() == {
            "type": "queue_changed",
            "job_id": first,
            "state": "running",
            "queue_position": 1,
        }
        _drive_transition(lambda: report_progress(first, 0.5))
        assert ws.receive_json() == {
            "type": "job_progress",
            "job_id": first,
            "state": "running",
            "queue_position": 1,
            "progress": 0.5,
        }
        _drive_transition(lambda: complete_job(first))
        assert ws.receive_json() == {
            "type": "job_done",
            "job_id": first,
            "state": "succeeded",
        }
        assert ws.receive_json() == {
            "type": "queue_changed",
            "job_id": first,
            "state": "succeeded",
        }


def test_ws_jobs_fail_event(client: TestClient, job_api: Callable[[], str]) -> None:
    campaign_id = _authed_owned_campaign(client)
    with client.websocket_connect(f"/api/ws/jobs?campaign_id={campaign_id}") as ws:
        job_id = _post_job(client, campaign_id).json()["id"]
        assert ws.receive_json()["type"] == "queue_changed"
        claimed = _drive_transition(lambda: claim_next_job())
        assert claimed is not None and claimed.id == job_id
        assert ws.receive_json() == {
            "type": "queue_changed",
            "job_id": job_id,
            "state": "running",
            "queue_position": 1,
        }
        _drive_transition(lambda: fail_job(job_id, "inference exploded"))
        assert ws.receive_json() == {
            "type": "job_failed",
            "job_id": job_id,
            "state": "failed",
        }
        assert ws.receive_json() == {
            "type": "queue_changed",
            "job_id": job_id,
            "state": "failed",
        }


def test_ws_jobs_cancel_flow(client: TestClient, job_api: Callable[[], str]) -> None:
    campaign_id = _authed_owned_campaign(client)
    with client.websocket_connect(f"/api/ws/jobs?campaign_id={campaign_id}") as ws:
        job_id = _post_job(client, campaign_id).json()["id"]
        assert ws.receive_json()["type"] == "queue_changed"
        response = client.post(f"/api/jobs/{job_id}/cancel")
        assert response.status_code == 200
        assert ws.receive_json() == {
            "type": "job_cancelled",
            "job_id": job_id,
            "state": "cancelled",
        }
        assert ws.receive_json() == {
            "type": "queue_changed",
            "job_id": job_id,
            "state": "cancelled",
        }


def test_ws_jobs_campaign_isolation(client: TestClient, job_api: Callable[[], str]) -> None:
    """A's transitions never surface on B's socket — B's frames are exactly
    its own job's sequence, asserted exactly (review round 1 hardening)."""
    # Both campaigns belong to the ONE session account (job_api registers
    # once): posting to A must still be allowed (spec-6-4 ownership), and
    # the isolation property is per-CAMPAIGN, not per-account.
    campaign_a = job_api()
    campaign_b = job_api()
    with client.websocket_connect(f"/api/ws/jobs?campaign_id={campaign_b}") as ws:
        a_job = _post_job(client, campaign_a).json()["id"]  # must not leak to B
        b_job = _post_job(client, campaign_b).json()["id"]
        assert ws.receive_json()["job_id"] == b_job  # B's first message is its own job

        # Drive A's job through its whole lifecycle while B is subscribed.
        claimed = _drive_transition(lambda: claim_next_job())
        assert claimed is not None and claimed.id == a_job
        _drive_transition(lambda: complete_job(a_job))

        # Cancel B's still-queued job. The next frames on B's socket must be
        # exactly its own cancel sequence — any interleaved A frame breaks
        # the exact-match assertions.
        response = client.post(f"/api/jobs/{b_job}/cancel")
        assert response.status_code == 200
        assert ws.receive_json() == {
            "type": "job_cancelled",
            "job_id": b_job,
            "state": "cancelled",
        }
        assert ws.receive_json() == {
            "type": "queue_changed",
            "job_id": b_job,
            "state": "cancelled",
        }
