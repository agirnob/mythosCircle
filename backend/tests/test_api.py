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

from app.core.errors import register_error_handlers


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


@pytest.mark.parametrize("status", [401, 403, 405, 408, 409, 418, 429])
def test_http_4xx_envelope(status: int) -> None:
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
    assert body["code"]
    assert body["message"]
