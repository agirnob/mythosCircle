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
# Disable the background queue worker during contract tests: it polls
# claim_next_job every 0.2s and would race the tests' exact-frame
# assertions (review round 1). The worker is tested separately.
os.environ["MYTHOSCIRCLE_TESTING"] = "1"
# Point config at a nonexistent file so tests never read the repo's or an
# installed config.toml (spec-1.7: code defaults + explicit test fixtures
# own the config surface; the app's setup_logging must not open /var/log).
os.environ["MYTHOSCIRCLE_CONFIG"] = "/nonexistent/mythoscircle-test-config.toml"


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """An httpx ASGI test client over the real app (no live server).

    The app is built per test — create_app() runs inside the fixture, not
    at collection — so the store initializes within the test session under
    the env pins above (epic-1 retro item 7; test_logging's pattern).
    """
    from app.main import create_app

    application = create_app()
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture()
def workspace_world(tmp_path):
    from app.core import ids
    from app.store import (
        app_db_url,
        commit_subgraph,
        create_campaign,
        init_db,
        models,
        register_account,
    )

    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'workspace-store.db'}")
    owner = register_account(f"workspace-{ids.new_id()}@example.com", "password123")
    campaign = create_campaign(
        owner.id,
        title="Arlea",
        description="River city",
        theme="High Fantasy",
        custom_lore="Keep the ferrymen's oath.",
    )
    harbor = ids.new_id()
    commit_subgraph(
        campaign.id,
        [models.EntityInput(id=harbor, kind="place", name="Harbor", text="Old quays")],
        [],
        base_revision=None,
        allow_orphans=True,
    )
    try:
        yield owner.id, campaign.id, harbor
    finally:
        init_db(previous)
