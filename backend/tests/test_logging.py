"""JSON-lines logging tests (spec-1.7)."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, Path]]:
    """Point the app's log file at a scratch path, then build the app."""
    log_file = tmp_path / "app.jsonl"
    monkeypatch.setenv("MYTHOSCIRCLE_LOG_FILE", str(log_file))
    from app.main import create_app

    application = create_app()
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client, log_file


def test_log_file_writes_json_lines(app_client: tuple[TestClient, Path], tmp_path: Path) -> None:
    """A route that raises logs a JSON-lines line with the exception —
    the 500 is visible in the operator log, not swallowed (spec-1.7)."""
    from fastapi import FastAPI

    from app.core.errors import register_error_handlers

    client, log_file = app_client

    application = FastAPI()

    @application.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    register_error_handlers(application)
    with TestClient(application, raise_server_exceptions=False) as boom_client:
        response = boom_client.get("/boom")
        assert response.status_code == 500
        assert "secret internals" not in response.text  # envelope never echoes

    # The configured log file received a JSON line mentioning the boom.
    # (A blank leading line from handler setup is tolerated — skip empties.)
    assert log_file.exists()
    json_lines = [line for line in log_file.read_text().splitlines() if line.strip()]
    assert json_lines, "expected at least one log line"
    error_lines = [json.loads(line) for line in json_lines if '"ERROR"' in line]
    assert error_lines, f"no ERROR line in log: {json_lines}"
    parsed = error_lines[-1]
    assert "boom" in parsed["message"]
    assert "Traceback" in parsed.get("exc", "")
