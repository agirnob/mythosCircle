"""Shared fixtures for the backend test suite."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """An httpx ASGI test client for the app (no live server)."""
    with TestClient(app) as test_client:
        yield test_client
