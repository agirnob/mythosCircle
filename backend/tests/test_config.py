"""Runtime config tests: env > config > default precedence, fail-loud
malformed, themes + queue reconciliation (spec-1.7).
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import reset_runtime_config, runtime_config
from app.core.settings import (
    configured_db_url,
    configured_log_file,
    configured_themes,
    llm_settings,
    queue_settings,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Clear config cache + scrub env so each test is deterministic."""
    for key in list(os.environ):
        if key.startswith("MYTHOSCIRCLE"):
            monkeypatch.delenv(key, raising=False)
    reset_runtime_config()
    yield
    reset_runtime_config()


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


def test_config_loaded_all_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(
        tmp_path,
        """[queue]
max_in_flight_per_campaign = 3
[llm]
endpoint = "http://llm:9000/v1"
model = "m2"
timeout = 60
[campaigns]
themes = ["Grimdark", "Steampunk"]
[world]
sqlite_path = "/data/world.db"
log_file = "/var/log/app.jsonl"
""",
    )
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    cfg = runtime_config()
    assert cfg.queue_max_pending == 3
    assert cfg.llm_endpoint == "http://llm:9000/v1"
    assert cfg.llm_model == "m2"
    assert cfg.llm_timeout == 60
    assert cfg.themes == ["Grimdark", "Steampunk"]
    assert cfg.db_url == "/data/world.db"
    assert cfg.log_file == "/var/log/app.jsonl"


def test_config_missing_uses_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(tmp_path / "nope.toml"))
    cfg = runtime_config()
    assert cfg.queue_max_pending == 10
    assert cfg.themes == ["High Fantasy", "Grimdark", "Steampunk", "Planar"]


def test_config_malformed_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, "not [ valid toml")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    with pytest.raises(ValueError, match="malformed config"):
        runtime_config()


def test_env_overrides_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, "[queue]\nmax_in_flight_per_campaign = 1\n")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    monkeypatch.setenv("MYTHOSCIRCLE_MAX_PENDING_PER_CAMPAIGN", "7")
    assert queue_settings().max_pending_per_campaign == 7  # env wins


def test_queue_reconciled_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The config value IS the pending cap (AR28, deferred 1.3)."""
    path = _write_config(tmp_path, "[queue]\nmax_in_flight_per_campaign = 1\n")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    assert queue_settings().max_pending_per_campaign == 1


def test_themes_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, '[campaigns]\nthemes = ["DarkSolarpunk"]\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    assert configured_themes() == ["DarkSolarpunk"]


def test_db_and_log_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(
        tmp_path, '[world]\nsqlite_path = "/custom/world.db"\nlog_file = "/custom/app.jsonl"\n'
    )
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    assert configured_db_url() == "/custom/world.db"
    assert configured_log_file() == "/custom/app.jsonl"
    assert configured_db_url() is not None


def test_llm_settings_config_driven(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, '[llm]\nendpoint = "http://host:8080/v1"\nmodel = "cfg-model"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    settings = llm_settings()
    assert settings.endpoint == "http://host:8080/v1"
    assert settings.model == "cfg-model"


def test_config_themes_reconcile_at_store_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured theme list is what the STORE validates against —
    a configured theme is accepted, a formerly-seeded one is rejected
    (deferred 1.6 reconciliation, review round 1)."""
    path = _write_config(tmp_path, '[campaigns]\nthemes = ["DarkSolarpunk"]\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    from app.store.campaigns import InvalidThemeError, normalize_theme

    assert normalize_theme("darksolarpunk") == "DarkSolarpunk"  # configured accepted
    with pytest.raises(InvalidThemeError):
        normalize_theme("High Fantasy")  # formerly-seeded now rejected
    reset_runtime_config()


def test_llm_env_precedence_and_empty_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The LLM env var wins over config; a set-but-empty env falls through
    to the config value (env > config > default, review round 1)."""
    path = _write_config(tmp_path, '[llm]\nendpoint = "http://cfg:8000/v1"\nmodel = "cfg-model"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    from app.core.settings import llm_settings

    monkeypatch.setenv("MYTHOSCIRCLE_LLM_ENDPOINT", "http://env:9000/v1")
    assert llm_settings().endpoint == "http://env:9000/v1"  # env wins
    reset_runtime_config()  # the cache locked endpoint=env URL in phase 1
    monkeypatch.delenv("MYTHOSCIRCLE_LLM_ENDPOINT")
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_ENDPOINT", "")  # empty -> config
    assert llm_settings().endpoint == "http://cfg:8000/v1"
    reset_runtime_config()


def test_config_budgets_consumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[llm] max_llm_calls_per_job / max_media_calls_per_job are consumed
    (review round 1 — they were dead mirrors in the shipped config). The
    media budget chain is load-bearing since spec-4.1: MediaCallBudget
    enforces ``job.max_media_calls``, which defaults from this config."""
    path = _write_config(
        tmp_path, "[llm]\nmax_llm_calls_per_job = 4\nmax_media_calls_per_job = 2\n"
    )
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    from app.core.settings import queue_settings

    assert queue_settings().max_llm_calls_per_job == 4
    assert queue_settings().max_media_calls_per_job == 2
    reset_runtime_config()


def test_image_settings_config_driven(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[image] endpoint/model/timeout are consumed (spec-4.1)."""
    path = _write_config(
        tmp_path,
        '[image]\nendpoint = "http://img:9000/v1"\nmodel = "cfg-image-model"\ntimeout = 45\n',
    )
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    from app.core.settings import image_settings

    settings = image_settings()
    assert settings.endpoint == "http://img:9000/v1"
    assert settings.model == "cfg-image-model"
    assert settings.timeout == 45
    reset_runtime_config()


def test_image_env_precedence_and_empty_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image env vars win over config; a set-but-empty env falls
    through to the config value (env > config > default)."""
    path = _write_config(tmp_path, '[image]\nendpoint = "http://cfg-img:8000/v1"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    from app.core.settings import image_settings

    monkeypatch.setenv("MYTHOSCIRCLE_IMAGE_ENDPOINT", "http://env-img:9000/v1")
    assert image_settings().endpoint == "http://env-img:9000/v1"
    reset_runtime_config()  # the cache locked endpoint=env URL in phase 1
    monkeypatch.delenv("MYTHOSCIRCLE_IMAGE_ENDPOINT")
    monkeypatch.setenv("MYTHOSCIRCLE_IMAGE_ENDPOINT", "")  # empty -> config
    assert image_settings().endpoint == "http://cfg-img:8000/v1"
    reset_runtime_config()


def test_image_defaults_are_documented_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ask-first item's defaults are inert placeholders — a missing
    config/env resolves to them without error (the mock provider is the
    test-time real path)."""
    from app.core.settings import DEFAULT_IMAGE_ENDPOINT, image_settings

    assert image_settings().endpoint == DEFAULT_IMAGE_ENDPOINT
    assert image_settings().model == "mythos-portrait-v1"


def test_media_dir_from_config_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[world] media_dir is consumed; MYTHOSCIRCLE_MEDIA_DIR wins over
    config (env > config > default, spec-4.1)."""
    from app.core.settings import configured_media_dir

    path = _write_config(tmp_path, '[world]\nmedia_dir = "/custom/media"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    assert configured_media_dir() == "/custom/media"
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", "/env/media")
    assert configured_media_dir() == "/env/media"
    reset_runtime_config()


def test_media_dir_code_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import configured_media_dir

    assert configured_media_dir() == "/var/lib/mythoscircle/media"
