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


def test_video_settings_config_driven(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[video] endpoint/model/timeout are consumed (spec-4.2)."""
    from app.core.settings import video_settings

    path = _write_config(
        tmp_path,
        '[video]\nendpoint = "http://vid:9000/v1"\nmodel = "cfg-video-model"\ntimeout = 45\n',
    )
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    settings = video_settings()
    assert settings.endpoint == "http://vid:9000/v1"
    assert settings.model == "cfg-video-model"
    assert settings.timeout == 45


def test_video_settings_env_overrides_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MYTHOSCIRCLE_VIDEO_ENDPOINT/MODEL/TIMEOUT win over [video] (env >
    config > default)."""
    from app.core.settings import video_settings

    path = _write_config(tmp_path, '[video]\nendpoint = "http://cfg-vid:8000/v1"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_ENDPOINT", "http://env-vid:9000/v1")
    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_MODEL", "env-video-model")
    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_TIMEOUT", "77")
    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_API_KEY", "sk-video")
    settings = video_settings()
    assert settings.endpoint == "http://env-vid:9000/v1"
    assert settings.model == "env-video-model"
    assert settings.timeout == 77
    assert settings.api_key == "sk-video"


def test_image_backend_defaults_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec-4.4 acceptance: an unset image_backend resolves to the
    default ``"openai"`` — the spec-4.1 path runs unchanged."""
    from app.core.settings import configured_image_backend

    assert configured_image_backend() == "openai"


def test_image_backend_config_override_is_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[image] backend = "comfyui" opts in — a static config switch, no
    fallback chain."""
    from app.core.settings import configured_image_backend

    path = _write_config(tmp_path, '[image]\nbackend = "comfyui"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    assert configured_image_backend() == "comfyui"
    reset_runtime_config()


def test_image_backend_env_wins_over_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MYTHOSCIRCLE_IMAGE_BACKEND wins over [image] backend (env > config
    > default, spec-4.4)."""
    from app.core.settings import configured_image_backend

    path = _write_config(tmp_path, '[image]\nbackend = "comfyui"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_IMAGE_BACKEND", "openai")
    assert configured_image_backend() == "openai"  # env wins
    reset_runtime_config()


def test_image_backend_misspelled_falls_back_to_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec-4.4 acceptance: a misspelled / unknown value resolves to the
    default ``"openai"`` — only the literal ``"comfyui"`` opts in."""
    from app.core.settings import configured_image_backend

    monkeypatch.setenv("MYTHOSCIRCLE_IMAGE_BACKEND", "comfyui-typo")
    assert configured_image_backend() == "openai"


def test_comfyui_image_settings_config_driven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[comfyui_image] endpoint/workflow_path/prompt_node_id/
    aspect_ratio/megapixels/timeout are consumed (spec-4.4)."""
    from app.core.settings import comfyui_image_settings

    path = _write_config(
        tmp_path,
        '[comfyui_image]\nendpoint = "http://comfy:7896"\n'
        'workflow_path = "/wf/krea2.json"\nprompt_node_id = "30:28"\n'
        'aspect_ratio = "16:9 (Wide)"\nmegapixels = 1.5\ntimeout = 900\n',
    )
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    settings = comfyui_image_settings()
    assert settings.endpoint == "http://comfy:7896"
    assert settings.workflow_path == "/wf/krea2.json"
    assert settings.prompt_node_id == "30:28"
    assert settings.aspect_ratio == "16:9 (Wide)"
    assert settings.megapixels == 1.5
    assert settings.timeout == 900
    assert settings.api_key is None
    reset_runtime_config()


def test_comfyui_image_settings_env_overrides_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MYTHOSCIRCLE_COMFYUI_IMAGE_* win over [comfyui_image]; the api_key
    stays environment-only; a set-but-empty env falls through to the
    config value (env > config > default)."""
    from app.core.settings import comfyui_image_settings

    path = _write_config(tmp_path, '[comfyui_image]\nendpoint = "http://cfg-comfy:7896"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_IMAGE_ENDPOINT", "http://env-comfy:7896")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_IMAGE_WORKFLOW_PATH", "/env/wf.json")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_IMAGE_PROMPT_NODE_ID", "5")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_IMAGE_ASPECT_RATIO", "9:16 (Tall)")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_IMAGE_MEGAPIXELS", "2.5")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_IMAGE_TIMEOUT", "77")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_IMAGE_API_KEY", "sk-comfy")
    settings = comfyui_image_settings()
    assert settings.endpoint == "http://env-comfy:7896"
    assert settings.workflow_path == "/env/wf.json"
    assert settings.prompt_node_id == "5"
    assert settings.aspect_ratio == "9:16 (Tall)"
    assert settings.megapixels == 2.5
    assert settings.timeout == 77
    assert settings.api_key == "sk-comfy"
    reset_runtime_config()  # the cache locked env=... values in phase 1
    monkeypatch.delenv("MYTHOSCIRCLE_COMFYUI_IMAGE_ENDPOINT")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_IMAGE_ENDPOINT", "")  # empty -> config
    assert comfyui_image_settings().endpoint == "http://cfg-comfy:7896"
    reset_runtime_config()


def test_comfyui_image_defaults_are_documented_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spec-4.4 defaults are documented placeholders: with no
    config/env, the workflow_path resolves to the repo-shipped Krea2
    workflow (spec-4.5 repo-home decision — computed from the repo's
    deploy dir, never a machine-specific absolute), and the
    endpoint/prompt node match the documented Krea2 Turbo setup. Pinned
    via a missing config file so the shipped config cannot leak in."""
    from app.core.config import DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH
    from app.core.settings import (
        DEFAULT_COMFYUI_IMAGE_ENDPOINT,
        DEFAULT_COMFYUI_IMAGE_PROMPT_NODE_ID,
        DEFAULT_COMFYUI_IMAGE_TIMEOUT,
        comfyui_image_settings,
    )

    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", "/nonexistent/mythoscircle.toml")
    settings = comfyui_image_settings()
    assert settings.endpoint == DEFAULT_COMFYUI_IMAGE_ENDPOINT  # http://127.0.0.1:7896
    assert settings.workflow_path == DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH
    assert os.path.isabs(settings.workflow_path)
    assert settings.workflow_path.endswith("deploy/workflows/image_krea2_turbo_t2i_int8 (2).json")
    assert settings.prompt_node_id == DEFAULT_COMFYUI_IMAGE_PROMPT_NODE_ID  # "30:19"
    assert settings.timeout == DEFAULT_COMFYUI_IMAGE_TIMEOUT  # 1800 — whole-call bound


def test_video_defaults_are_documented_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    """The spec-4.2 ask-first item's defaults are inert placeholders — a
    missing config/env resolves to them without error (the mock provider
    is the test-time real path)."""
    from app.core.settings import DEFAULT_VIDEO_ENDPOINT, video_settings

    assert video_settings().endpoint == DEFAULT_VIDEO_ENDPOINT
    assert video_settings().model == "mythos-reveal-v1"


def test_image_backend_env_only_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """MYTHOSCIRCLE_IMAGE_BACKEND=comfyui ALONE (no config file section)
    opts the runtime into the comfyui backend — env > config > default
    (review round 1)."""
    from app.core.settings import configured_image_backend

    monkeypatch.setenv("MYTHOSCIRCLE_IMAGE_BACKEND", "comfyui")
    assert configured_image_backend() == "comfyui"


def test_image_backend_env_only_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """MYTHOSCIRCLE_IMAGE_BACKEND=openai alone resolves to the openai
    backend — the default preserved (review round 1)."""
    from app.core.settings import configured_image_backend

    monkeypatch.setenv("MYTHOSCIRCLE_IMAGE_BACKEND", "openai")
    assert configured_image_backend() == "openai"


def test_comfyui_empty_timeout_env_falls_through_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A set-but-empty MYTHOSCIRCLE_COMFYUI_IMAGE_TIMEOUT is treated as
    unset — the config value wins; the numeric parse never sees "" (it
    would raise, contradicting env > config > default precedence)."""
    from app.core.settings import comfyui_image_settings

    path = _write_config(tmp_path, "[comfyui_image]\ntimeout = 60\n")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_IMAGE_TIMEOUT", "")
    assert comfyui_image_settings().timeout == 60
    reset_runtime_config()


def test_env_float_rejects_non_finite(monkeypatch: pytest.MonkeyPatch) -> None:
    """env_float rejects inf/nan loudly: a timeout of ``inf`` would make
    the ComfyUI poll deadline unbounded and ``nan`` silently slips past
    every comparison (review round 1)."""
    from app.core.config import env_float

    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TIMEOUT", "inf")
    with pytest.raises(ValueError, match="finite"):
        env_float("MYTHOSCIRCLE_LLM_TIMEOUT", 120.0)
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TIMEOUT", "nan")
    with pytest.raises(ValueError, match="finite"):
        env_float("MYTHOSCIRCLE_LLM_TIMEOUT", 120.0)


# ---------------------------------------------------------------------------
# Spec-4.5: the reveal-video backend switch + ComfyUI video settings
# ---------------------------------------------------------------------------


def test_video_backend_defaults_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec-4.5 acceptance: an unset video_backend resolves to the
    default ``"openai"`` — the spec-4.2 path runs unchanged."""
    from app.core.settings import configured_video_backend

    assert configured_video_backend() == "openai"


def test_video_backend_config_override_is_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[video] backend = "comfyui" opts in — a static config switch, no
    fallback chain."""
    from app.core.settings import configured_video_backend

    path = _write_config(tmp_path, '[video]\nbackend = "comfyui"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    assert configured_video_backend() == "comfyui"
    reset_runtime_config()


def test_video_backend_env_wins_over_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MYTHOSCIRCLE_VIDEO_BACKEND wins over [video] backend (env > config
    > default, spec-4.5)."""
    from app.core.settings import configured_video_backend

    path = _write_config(tmp_path, '[video]\nbackend = "comfyui"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_BACKEND", "openai")
    assert configured_video_backend() == "openai"  # env wins
    reset_runtime_config()


def test_video_backend_misspelled_falls_back_to_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec-4.5 acceptance: a misspelled / unknown value resolves to the
    default ``"openai"`` — only the literal ``"comfyui"`` opts in."""
    from app.core.settings import configured_video_backend

    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_BACKEND", "comfyui-typo")
    assert configured_video_backend() == "openai"


def test_video_backend_env_only_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """MYTHOSCIRCLE_VIDEO_BACKEND=comfyui ALONE (no config file section)
    opts the runtime into the comfyui backend — env > config > default."""
    from app.core.settings import configured_video_backend

    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_BACKEND", "comfyui")
    assert configured_video_backend() == "comfyui"


def test_video_backend_env_only_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """MYTHOSCIRCLE_VIDEO_BACKEND=openai alone resolves to the openai
    backend — the default preserved."""
    from app.core.settings import configured_video_backend

    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_BACKEND", "openai")
    assert configured_video_backend() == "openai"


def test_comfyui_video_settings_config_driven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[comfyui_video] endpoint/workflow_path/prompt_node_id/
    first_frame_node_id/input_dir/timeout are consumed (spec-4.5)."""
    from app.core.settings import comfyui_video_settings

    path = _write_config(
        tmp_path,
        '[comfyui_video]\nendpoint = "http://comfy:7896"\n'
        'workflow_path = "/wf/minimax.json"\nprompt_node_id = "5:1"\n'
        'first_frame_node_id = "9"\ninput_dir = "/comfy/input"\ntimeout = 900\n',
    )
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    settings = comfyui_video_settings()
    assert settings.endpoint == "http://comfy:7896"
    assert settings.workflow_path == "/wf/minimax.json"
    assert settings.prompt_node_id == "5:1"
    assert settings.first_frame_node_id == "9"
    assert settings.input_dir == "/comfy/input"
    assert settings.timeout == 900
    reset_runtime_config()


def test_comfyui_video_settings_env_overrides_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MYTHOSCIRCLE_COMFYUI_VIDEO_* win over [comfyui_video]; the api_key
    stays environment-only; a set-but-empty env falls through to the
    config value (env > config > default)."""
    from app.core.settings import comfyui_video_settings

    path = _write_config(tmp_path, '[comfyui_video]\nendpoint = "http://cfg-comfy:7896"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_VIDEO_ENDPOINT", "http://env-comfy:7896")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_VIDEO_WORKFLOW_PATH", "/env/wf.json")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_VIDEO_PROMPT_NODE_ID", "5")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID", "7")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_VIDEO_INPUT_DIR", "/env/input")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_VIDEO_TIMEOUT", "77")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_VIDEO_API_KEY", "sk-vid")
    settings = comfyui_video_settings()
    assert settings.endpoint == "http://env-comfy:7896"
    assert settings.workflow_path == "/env/wf.json"
    assert settings.prompt_node_id == "5"
    assert settings.first_frame_node_id == "7"
    assert settings.input_dir == "/env/input"
    assert settings.timeout == 77
    assert settings.api_key == "sk-vid"  # env-only (AD-22)
    reset_runtime_config()  # the cache locked env=... values in phase 1
    monkeypatch.delenv("MYTHOSCIRCLE_COMFYUI_VIDEO_ENDPOINT")
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_VIDEO_ENDPOINT", "")  # empty -> config
    assert comfyui_video_settings().endpoint == "http://cfg-comfy:7896"
    reset_runtime_config()


def test_comfyui_video_defaults_are_documented_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spec-4.5 defaults are documented placeholders: with no
    config/env, the workflow_path resolves to the repo-shipped MiniMax
    workflow (spec-4.5 repo-home decision — computed from the repo's
    deploy dir), while ``input_dir`` stays empty (operator must point
    at their ComfyUI install), and the timeout is the whole-call 1800
    bound like the image twin. Pinned via a missing config file so the
    shipped config cannot leak in."""
    from app.core.config import DEFAULT_COMFYUI_VIDEO_WORKFLOW_PATH
    from app.core.settings import (
        DEFAULT_COMFYUI_VIDEO_ENDPOINT,
        DEFAULT_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID,
        DEFAULT_COMFYUI_VIDEO_PROMPT_NODE_ID,
        DEFAULT_COMFYUI_VIDEO_TIMEOUT,
        comfyui_video_settings,
    )

    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", "/nonexistent/mythoscircle.toml")
    settings = comfyui_video_settings()
    assert settings.endpoint == DEFAULT_COMFYUI_VIDEO_ENDPOINT  # http://127.0.0.1:7896
    assert settings.workflow_path == DEFAULT_COMFYUI_VIDEO_WORKFLOW_PATH
    assert os.path.isabs(settings.workflow_path)
    assert settings.workflow_path.endswith("deploy/workflows/video_minimax_h3_i2v_sage.json")
    assert settings.input_dir == ""  # operator must set
    assert settings.prompt_node_id == DEFAULT_COMFYUI_VIDEO_PROMPT_NODE_ID  # "105:104"
    assert settings.first_frame_node_id == DEFAULT_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID  # "114"
    assert settings.timeout == DEFAULT_COMFYUI_VIDEO_TIMEOUT  # 1800 — whole-call bound


def test_comfyui_video_empty_timeout_env_falls_through_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A set-but-empty MYTHOSCIRCLE_COMFYUI_VIDEO_TIMEOUT is treated as
    unset — the config value wins; the numeric parse never sees "" (it
    would raise, contradicting env > config > default precedence)."""
    from app.core.settings import comfyui_video_settings

    path = _write_config(tmp_path, "[comfyui_video]\ntimeout = 60\n")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_COMFYUI_VIDEO_TIMEOUT", "")
    assert comfyui_video_settings().timeout == 60
    reset_runtime_config()


def test_comfyui_video_relative_workflow_path_resolves_against_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A RELATIVE workflow_path resolves against the ACTIVE config
    file's directory — the shipped config's ``deploy/config.toml`` sits
    next to ``deploy/workflows/``, so ``workflow_path =
    "workflows/x.json"`` is machine-independent across checkouts
    (review round 1); an absolute path is used verbatim."""
    from app.core.settings import comfyui_video_settings

    path = _write_config(tmp_path, '[comfyui_video]\nworkflow_path = "workflows/minimax.json"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    assert comfyui_video_settings().workflow_path == str(tmp_path / "workflows" / "minimax.json")
    reset_runtime_config()
    absolute = _write_config(
        tmp_path, f'[comfyui_video]\nworkflow_path = "{tmp_path}/definitely-absolute.json"\n'
    )
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(absolute))
    reset_runtime_config()
    assert comfyui_video_settings().workflow_path == f"{tmp_path}/definitely-absolute.json"
    reset_runtime_config()


def test_comfyui_image_relative_workflow_path_resolves_against_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same relative resolution for the image workflow (review round
    1 — both comfyui sections share the derive-from-config-dir rule)."""
    from app.core.settings import comfyui_image_settings

    path = _write_config(tmp_path, '[comfyui_image]\nworkflow_path = "workflows/krea2.json"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    assert comfyui_image_settings().workflow_path == str(tmp_path / "workflows" / "krea2.json")
    reset_runtime_config()


def test_base_url_config_env_default_flows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[server].base_url is consumed (spec-5.2): missing section and
    empty values fall back to the code default; env wins; a set-but-empty
    env falls through to config (the falsy rule, review round 1)."""
    from app.core.config import DEFAULT_BASE_URL
    from app.core.settings import configured_base_url

    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(tmp_path / "nope.toml"))
    reset_runtime_config()
    assert configured_base_url() == DEFAULT_BASE_URL  # missing file -> default
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text('[server]\nbase_url = "https://cfg.example.test"\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(cfg_file))
    reset_runtime_config()
    assert configured_base_url() == "https://cfg.example.test"  # config file wins
    reset_runtime_config()
    empty_file = tmp_path / "empty.toml"
    empty_file.write_text('[server]\nbase_url = ""\n')
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(empty_file))
    reset_runtime_config()
    assert configured_base_url() == DEFAULT_BASE_URL  # empty config -> default
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(cfg_file))
    monkeypatch.setenv("MYTHOSCIRCLE_BASE_URL", "")  # empty env -> config
    reset_runtime_config()
    assert configured_base_url() == "https://cfg.example.test"
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_BASE_URL", "https://env.example.test")
    assert configured_base_url() == "https://env.example.test"  # env wins
    reset_runtime_config()


def test_llm_sampling_defaults_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset everywhere = None: the request body omits temperature/top_p/
    seed entirely (the thinking tri-state's rationale — a backend
    rejecting unknown fields keeps working). Pinned via a missing config
    file so the shipped config cannot leak in."""
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", "/nonexistent/mythoscircle.toml")
    reset_runtime_config()
    settings = llm_settings()
    assert settings.temperature is None
    assert settings.top_p is None
    assert settings.seed is None
    cfg = runtime_config()
    assert cfg.llm_temperature is None
    assert cfg.llm_top_p is None
    assert cfg.llm_seed is None
    reset_runtime_config()


def test_llm_sampling_config_driven(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[llm] temperature/top_p/seed are consumed (improvement plan G) —
    the config values land in RuntimeConfig and llm_settings()."""
    path = _write_config(tmp_path, "[llm]\ntemperature = 0.7\ntop_p = 0.9\nseed = 12345\n")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    cfg = runtime_config()
    assert cfg.llm_temperature == 0.7
    assert cfg.llm_top_p == 0.9
    assert cfg.llm_seed == 12345
    settings = llm_settings()
    assert settings.temperature == 0.7
    assert settings.top_p == 0.9
    assert settings.seed == 12345
    reset_runtime_config()


def test_llm_sampling_env_overrides_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MYTHOSCIRCLE_LLM_TEMPERATURE/TOP_P/SEED win over [llm] (env >
    config > default, spec-1.7)."""
    path = _write_config(tmp_path, "[llm]\ntemperature = 0.7\ntop_p = 0.9\nseed = 1\n")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TEMPERATURE", "0.2")
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TOP_P", "0.5")
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_SEED", "99")
    settings = llm_settings()
    assert settings.temperature == 0.2
    assert settings.top_p == 0.5
    assert settings.seed == 99
    reset_runtime_config()


def test_llm_sampling_empty_env_falls_through_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A set-but-empty sampling env is treated as unset — the config
    value wins; the numeric parse never sees "" (the endpoint rule,
    review round 1)."""
    path = _write_config(tmp_path, "[llm]\ntemperature = 0.7\ntop_p = 0.9\nseed = 5\n")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TEMPERATURE", "")
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TOP_P", "")
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_SEED", "")
    settings = llm_settings()
    assert settings.temperature == 0.7
    assert settings.top_p == 0.9
    assert settings.seed == 5
    reset_runtime_config()


def test_llm_sampling_zero_values_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """temperature=0 / seed=0 are MEANINGFUL (greedy decoding, a fixed
    RNG seed), never falsy fall-throughs — env zeros AND config zeros
    land verbatim."""
    path = _write_config(tmp_path, "[llm]\ntemperature = 1.5\ntop_p = 1.0\nseed = 7\n")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(path))
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TEMPERATURE", "0")
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_SEED", "0")
    settings = llm_settings()
    assert settings.temperature == 0.0  # env zero beats config 1.5
    assert settings.seed == 0
    assert settings.top_p == 1.0  # untouched config stays
    reset_runtime_config()
    monkeypatch.delenv("MYTHOSCIRCLE_LLM_TEMPERATURE")
    monkeypatch.delenv("MYTHOSCIRCLE_LLM_SEED")
    zeros = tmp_path / "zeros.toml"
    zeros.write_text("[llm]\ntemperature = 0.0\nseed = 0\n")
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", str(zeros))
    reset_runtime_config()
    settings = llm_settings()
    assert settings.temperature == 0.0  # config zero is kept, not defaulted
    assert settings.seed == 0
    assert settings.top_p is None  # absent key stays the None default
    reset_runtime_config()


def test_llm_sampling_malformed_env_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator typo must surface, never silently pick a sampling mode
    (the env_bool_optional rationale); a negative temperature is rejected
    too — 0.0 is the floor (greedy), below it is nonsense."""
    monkeypatch.setenv("MYTHOSCIRCLE_CONFIG", "/nonexistent/mythoscircle.toml")
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TEMPERATURE", "warm")
    with pytest.raises(ValueError, match="must be a float"):
        llm_settings()
    reset_runtime_config()
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_TEMPERATURE", "-1")
    with pytest.raises(ValueError, match="must be >= 0"):
        llm_settings()
    reset_runtime_config()
    monkeypatch.delenv("MYTHOSCIRCLE_LLM_TEMPERATURE")
    monkeypatch.setenv("MYTHOSCIRCLE_LLM_SEED", "12.5")
    with pytest.raises(ValueError, match="must be an integer"):
        llm_settings()
    reset_runtime_config()
