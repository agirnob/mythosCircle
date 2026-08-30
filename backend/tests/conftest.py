"""Shared fixtures for the backend test suite."""

import os
import tempfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

# Point the app's world store at a scratch DB so test runs never create
# data/ in the working tree. Must be set before importing app.main.
_TEST_DB = f"sqlite:///{tempfile.mkdtemp(prefix='mythoscircle-test-')}/world.db"
os.environ["MYTHOSCIRCLE_DB"] = _TEST_DB

from app.main import app  # noqa: E402


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """An httpx ASGI test client for the app (no live server)."""
    with TestClient(app) as test_client:
        yield test_client
