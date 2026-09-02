"""API conventions: health endpoint and the error envelope.

Covers the I/O matrix from the story spec:
- GET /api/health -> 200 {"status": "ok"}
- unknown route -> 404 in the envelope
- unhandled exception -> 5xx in the envelope, no internals leaked
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import BaseModel

from app.api.common import store_error_as_http
from app.core.errors import register_error_handlers
from app.store import InvalidEdgeCounterError, models


def _envelope_body(response: Response) -> dict[str, Any]:
    """Assert the response body matches {code, message, details?} and return it."""
    body = response.json()
    assert isinstance(body, dict)
    assert set(body) <= {"code", "message", "details"}
    assert isinstance(body["code"], str) and body["code"]
    assert isinstance(body["message"], str) and body["message"]
    if body.get("details") is not None:
        assert isinstance(body["details"], dict)
    return body


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_404_envelope(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    body = _envelope_body(response)
    assert body["code"] == "not_found"
    assert body["message"]


def test_unhandled_exception_500_envelope() -> None:
    application = FastAPI()
    register_error_handlers(application)

    @application.get("/api/explodes")
    def explodes() -> None:
        raise RuntimeError("secret internals: SELECT * FROM worlds")

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/explodes")
    assert response.status_code == 500
    body = _envelope_body(response)
    assert body["code"] == "internal_error"
    text = response.text
    assert "SELECT" not in text
    assert "RuntimeError" not in text
    assert "traceback" not in text.lower()


def test_validation_error_422_envelope() -> None:
    application = FastAPI()
    register_error_handlers(application)

    class ProbePayload(BaseModel):
        name: str

    @application.post("/api/probe")
    def probe(payload: ProbePayload) -> dict[str, str]:
        return {"ok": payload.name}

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.post("/api/probe", json={})
    assert response.status_code == 422
    body = _envelope_body(response)
    assert body["code"] == "validation_error"
    assert isinstance(body["details"], dict)


def test_store_error_mapper_422_edge_counter_rejection() -> None:
    """The store-error family is pinned at the mapper boundary: an edge
    counter-shape rejection reaches the wire as a 422 validation_error
    envelope (spec-2.2 EDGE_COUNTER_INVALID_SHAPE row), never a 500."""
    application = FastAPI()
    register_error_handlers(application)

    bad_counter: Any = 1.5

    @application.get("/api/probe-edge-counter")
    def probe() -> None:
        store_error_as_http(
            InvalidEdgeCounterError(
                models.EdgeInput(src="0" * 26, dst="1" * 26, type="debt", counter=bad_counter)
            )
        )

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/probe-edge-counter")
    assert response.status_code == 422
    body = _envelope_body(response)
    assert body["code"] == "validation_error"
    assert "debt" in body["message"]


#: Wire contract: every HTTPException status maps to a machine-readable code.
_HTTP_CODE_EXPECTATIONS: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    418: "im_a_teapot",
    429: "rate_limited",
    451: "unavailable_for_legal_reasons",
}


@pytest.mark.parametrize(
    ("status", "code"),
    sorted(_HTTP_CODE_EXPECTATIONS.items()),
)
def test_http_4xx_envelope(status: int, code: str) -> None:
    """Every common 4xx status yields its exact machine-readable code —
    the codes the frontend branches on are pinned, not just non-empty."""
    from starlette.exceptions import HTTPException

    application = FastAPI()
    register_error_handlers(application)

    @application.get("/api/gate")
    def gate() -> None:
        raise HTTPException(status_code=status)

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/gate")
    assert response.status_code == status
    body = _envelope_body(response)
    assert body["code"] == code
    assert body["message"]


def test_http_4xx_custom_detail_echoed_as_message() -> None:
    """A 4xx carrying a detail uses it as the envelope message while the
    machine-readable code stays the status's own."""
    from starlette.exceptions import HTTPException

    application = FastAPI()
    register_error_handlers(application)

    @application.get("/api/gate")
    def gate() -> None:
        raise HTTPException(status_code=409, detail="campaign already exists")

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/gate")
    assert response.status_code == 409
    body = _envelope_body(response)
    assert body["code"] == "conflict"
    assert body["message"] == "campaign already exists"


def test_http_5xx_exception_generic_envelope() -> None:
    """A 5xx HTTPException never leaks its detail: generic internal_error
    envelope, nothing echoed in the response text."""
    from starlette.exceptions import HTTPException

    application = FastAPI()
    register_error_handlers(application)

    @application.get("/api/gate")
    def gate() -> None:
        raise HTTPException(status_code=500, detail="secret internals: SELECT 1")

    with TestClient(application, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/gate")
    assert response.status_code == 500
    body = _envelope_body(response)
    assert body["code"] == "internal_error"
    text = response.text
    assert "SELECT" not in text
    assert "secret" not in text
