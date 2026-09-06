"""Operator config loading (AD-22, spec-1.7).

``deploy/config.toml`` is the single operator config. Precedence is
always env > config.toml > code default; secrets remain environment-only
(never in config.toml). Malformed config fails loudly at the first read;
a missing file falls back to code defaults.
"""

import math
import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

#: Env var pointing at the config path (dev overrides the repo default).
CONFIG_ENV = "MYTHOSCIRCLE_CONFIG"
#: The repo's shipped config, then the installed operator path.
_REPO_CONFIG = Path(__file__).resolve().parents[3] / "deploy" / "config.toml"
_INSTALLED_CONFIG = Path("/etc/mythoscircle/config.toml")

#: Env overrides for each config section (env > config).
MAX_PENDING_ENV = "MYTHOSCIRCLE_MAX_PENDING_PER_CAMPAIGN"
LLM_ENDPOINT_ENV = "MYTHOSCIRCLE_LLM_ENDPOINT"
LLM_MODEL_ENV = "MYTHOSCIRCLE_LLM_MODEL"
LLM_TIMEOUT_ENV = "MYTHOSCIRCLE_LLM_TIMEOUT"
IMAGE_ENDPOINT_ENV = "MYTHOSCIRCLE_IMAGE_ENDPOINT"
IMAGE_MODEL_ENV = "MYTHOSCIRCLE_IMAGE_MODEL"
IMAGE_TIMEOUT_ENV = "MYTHOSCIRCLE_IMAGE_TIMEOUT"
VIDEO_ENDPOINT_ENV = "MYTHOSCIRCLE_VIDEO_ENDPOINT"
VIDEO_MODEL_ENV = "MYTHOSCIRCLE_VIDEO_MODEL"
VIDEO_TIMEOUT_ENV = "MYTHOSCIRCLE_VIDEO_TIMEOUT"
DB_ENV = "MYTHOSCIRCLE_DB"
LOG_FILE_ENV = "MYTHOSCIRCLE_LOG_FILE"
MEDIA_DIR_ENV = "MYTHOSCIRCLE_MEDIA_DIR"
IMAGE_BACKEND_ENV = "MYTHOSCIRCLE_IMAGE_BACKEND"
COMFYUI_IMAGE_ENDPOINT_ENV = "MYTHOSCIRCLE_COMFYUI_IMAGE_ENDPOINT"
COMFYUI_IMAGE_WORKFLOW_PATH_ENV = "MYTHOSCIRCLE_COMFYUI_IMAGE_WORKFLOW_PATH"
COMFYUI_IMAGE_PROMPT_NODE_ID_ENV = "MYTHOSCIRCLE_COMFYUI_IMAGE_PROMPT_NODE_ID"
COMFYUI_IMAGE_ASPECT_RATIO_ENV = "MYTHOSCIRCLE_COMFYUI_IMAGE_ASPECT_RATIO"
COMFYUI_IMAGE_MEGAPIXELS_ENV = "MYTHOSCIRCLE_COMFYUI_IMAGE_MEGAPIXELS"
COMFYUI_IMAGE_TIMEOUT_ENV = "MYTHOSCIRCLE_COMFYUI_IMAGE_TIMEOUT"


def env_int(name: str, default: int, minimum: int = 0) -> int:
    """Parse an integer env var; malformed or below-minimum values fail
    loudly rather than silently defaulting (operator typos must surface)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    """Parse a float env var; malformed or non-positive values fail loudly.

    The default minimum is strict-positive (0.0 is rejected) — a zero or
    negative timeout would disable the guard entirely. Non-finite values
    (``inf``/``nan``) also fail loudly: a timeout of ``inf`` would make
    the ComfyUI poll deadline unbounded and ``nan`` silently slips past
    every comparison (review round 1).
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float, got {raw!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if value <= minimum:
        raise ValueError(f"{name} must be > {minimum}, got {value}")
    return value


#: Code defaults (fallback when neither config nor env provides).
DEFAULT_MAX_PENDING = 10
DEFAULT_LLM_ENDPOINT = "http://127.0.0.1:8080/v1"
DEFAULT_LLM_MODEL = "mythos-14b-q5"
DEFAULT_LLM_TIMEOUT = 120.0
#: Documented PLACEHOLDER defaults (spec-4.1 ask-first item): the dev
#: image server + model choice is an owner decision at build — until
#: confirmed, these are inert placeholders and tests inject a mock
#: provider. Swapping engines is a config change, never a code change.
DEFAULT_IMAGE_ENDPOINT = "http://127.0.0.1:8081/v1"
DEFAULT_IMAGE_MODEL = "mythos-portrait-v1"
DEFAULT_IMAGE_TIMEOUT = 120.0

#: Documented PLACEHOLDER defaults (spec-4.2 ask-first item): the dev
#: video server + model choice is a pending owner decision (ask-first
#: item); until confirmed these are inert placeholders and the video
#: tests inject a mock provider. Swapping engines is a config change,
#: never a code change.
DEFAULT_VIDEO_ENDPOINT = "http://127.0.0.1:8082/v1"
DEFAULT_VIDEO_MODEL = "mythos-reveal-v1"
DEFAULT_VIDEO_TIMEOUT = 120.0
#: The portrait backend switch (spec-4.4): ``"openai"`` (default —
#: spec-4.1 preserved) or ``"comfyui"`` (opt-in local-dev alternative).
DEFAULT_IMAGE_BACKEND: Literal["openai", "comfyui"] = "openai"

#: Documented PLACEHOLDER defaults (spec-4.4): the ComfyUI portrait
#: backend is an opt-in alternative local-dev path; the workflow JSON
#: lives on the operator machine and is NEVER shipped (spec-4.4 Never
#: list), so ``workflow_path`` starts empty and the job fails at first
#: call until the operator sets it. ``timeout`` bounds the ENTIRE call
#: (submit + poll + fetch) — Krea2 Turbo on a local GPU is 30-90s
#: typical, hence the long default, not the OpenAI shape's per-request
#: 120s.
DEFAULT_COMFYUI_IMAGE_ENDPOINT = "http://127.0.0.1:7896"
DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH = ""
DEFAULT_COMFYUI_IMAGE_PROMPT_NODE_ID = "30:19"
DEFAULT_COMFYUI_IMAGE_ASPECT_RATIO = "1:1 (Square)"
DEFAULT_COMFYUI_IMAGE_MEGAPIXELS = 1.0
DEFAULT_COMFYUI_IMAGE_TIMEOUT = 1800.0
DEFAULT_MEDIA_DIR = "/var/lib/mythoscircle/media"
DEFAULT_THEMES = ["High Fantasy", "Grimdark", "Steampunk", "Planar"]
DEFAULT_DB_URL = "sqlite:////var/lib/mythoscircle/mythoscircle.db"


@dataclass(frozen=True)
class RuntimeConfig:
    """Every operator knob resolved at runtime (env > config > default)."""

    queue_max_pending: int = DEFAULT_MAX_PENDING
    llm_endpoint: str = DEFAULT_LLM_ENDPOINT
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout: float = DEFAULT_LLM_TIMEOUT
    max_llm_calls_per_job: int = 64
    max_media_calls_per_job: int = 8
    image_endpoint: str = DEFAULT_IMAGE_ENDPOINT
    image_model: str = DEFAULT_IMAGE_MODEL
    image_timeout: float = DEFAULT_IMAGE_TIMEOUT
    image_backend: Literal["openai", "comfyui"] = DEFAULT_IMAGE_BACKEND
    comfyui_image_endpoint: str = DEFAULT_COMFYUI_IMAGE_ENDPOINT
    comfyui_image_workflow_path: str = DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH
    comfyui_image_prompt_node_id: str = DEFAULT_COMFYUI_IMAGE_PROMPT_NODE_ID
    comfyui_image_aspect_ratio: str = DEFAULT_COMFYUI_IMAGE_ASPECT_RATIO
    comfyui_image_megapixels: float = DEFAULT_COMFYUI_IMAGE_MEGAPIXELS
    comfyui_image_timeout: float = DEFAULT_COMFYUI_IMAGE_TIMEOUT
    video_endpoint: str = DEFAULT_VIDEO_ENDPOINT
    video_model: str = DEFAULT_VIDEO_MODEL
    video_timeout: float = DEFAULT_VIDEO_TIMEOUT
    themes: list[str] = field(default_factory=lambda: list(DEFAULT_THEMES))
    db_url: str | None = None
    log_file: str | None = None
    media_dir: str = DEFAULT_MEDIA_DIR


def config_path() -> Path:
    """The config file to read: env var, then repo, then installed."""
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    if _REPO_CONFIG.exists():
        return _REPO_CONFIG
    return _INSTALLED_CONFIG


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Parse the config file; {} when absent, loud failure when malformed."""
    target = path or config_path()
    if not target.exists():
        return {}
    try:
        with target.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"malformed config {target}: {exc}") from exc


@lru_cache(maxsize=1)
def runtime_config() -> RuntimeConfig:
    """The resolved runtime config (cached; reset via ``reset_runtime_config``)."""
    data = load_config()
    world = data.get("world", {})
    queue = data.get("queue", {})
    llm = data.get("llm", {})
    image = data.get("image", {})
    video = data.get("video", {})
    comfyui_image = data.get("comfyui_image", {})
    campaigns = data.get("campaigns", {})

    def _config_int(value: Any, name: str, default: int) -> int:
        if value is None:
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"config {name} must be an integer, got {value!r}") from exc
        if parsed < 0:
            raise ValueError(f"config {name} must be >= 0, got {parsed}")
        return parsed

    def _config_float(value: Any, name: str, default: float) -> float:
        if value is None:
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"config {name} must be a float, got {value!r}") from exc
        if parsed <= 0:
            raise ValueError(f"config {name} must be > 0, got {parsed}")
        return parsed

    # queue cap: env > config > default (AR28 pending = in-flight + queued).
    pending = env_int(
        MAX_PENDING_ENV,
        _config_int(
            queue.get("max_in_flight_per_campaign"),
            "queue.max_in_flight_per_campaign",
            DEFAULT_MAX_PENDING,
        ),
    )
    endpoint = os.environ.get(LLM_ENDPOINT_ENV) or str(llm.get("endpoint", DEFAULT_LLM_ENDPOINT))
    model = os.environ.get(LLM_MODEL_ENV) or str(llm.get("model", DEFAULT_LLM_MODEL))
    timeout = env_float(
        LLM_TIMEOUT_ENV,
        _config_float(llm.get("timeout"), "llm.timeout", DEFAULT_LLM_TIMEOUT),
    )
    image_endpoint = os.environ.get(IMAGE_ENDPOINT_ENV) or str(
        image.get("endpoint", DEFAULT_IMAGE_ENDPOINT)
    )
    image_model = os.environ.get(IMAGE_MODEL_ENV) or str(image.get("model", DEFAULT_IMAGE_MODEL))
    image_timeout = env_float(
        IMAGE_TIMEOUT_ENV,
        _config_float(image.get("timeout"), "image.timeout", DEFAULT_IMAGE_TIMEOUT),
    )
    # Backend switch: env > config > default (spec-4.4). Acceptance:
    # unset / misspelled / None falls back to the default OpenAI path —
    # only the literal ``"comfyui"`` opts in, never a runtime fallback
    # chain.
    backend_setting = os.environ.get(IMAGE_BACKEND_ENV) or str(
        image.get("backend", DEFAULT_IMAGE_BACKEND)
    )
    image_backend: Literal["openai", "comfyui"] = (
        "comfyui" if backend_setting == "comfyui" else "openai"
    )
    comfyui_image_endpoint = os.environ.get(COMFYUI_IMAGE_ENDPOINT_ENV) or str(
        comfyui_image.get("endpoint", DEFAULT_COMFYUI_IMAGE_ENDPOINT)
    )
    comfyui_image_workflow_path = os.environ.get(COMFYUI_IMAGE_WORKFLOW_PATH_ENV) or str(
        comfyui_image.get("workflow_path", DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH)
    )
    comfyui_image_prompt_node_id = os.environ.get(COMFYUI_IMAGE_PROMPT_NODE_ID_ENV) or str(
        comfyui_image.get("prompt_node_id", DEFAULT_COMFYUI_IMAGE_PROMPT_NODE_ID)
    )
    comfyui_image_aspect_ratio = os.environ.get(COMFYUI_IMAGE_ASPECT_RATIO_ENV) or str(
        comfyui_image.get("aspect_ratio", DEFAULT_COMFYUI_IMAGE_ASPECT_RATIO)
    )
    # A set-but-empty env value is treated as unset (spec-1.7 env >
    # config > default — the settings-layer docstring's contract): the
    # numeric parse must never see "" (it would raise and contradict
    # the documented precedence, review round 1).
    comfyui_megapixels_env = os.environ.get(COMFYUI_IMAGE_MEGAPIXELS_ENV)
    comfyui_megapixels_config = _config_float(
        comfyui_image.get("megapixels"),
        "comfyui_image.megapixels",
        DEFAULT_COMFYUI_IMAGE_MEGAPIXELS,
    )
    comfyui_image_megapixels = (
        env_float(COMFYUI_IMAGE_MEGAPIXELS_ENV, comfyui_megapixels_config)
        if comfyui_megapixels_env
        else comfyui_megapixels_config
    )
    comfyui_timeout_env = os.environ.get(COMFYUI_IMAGE_TIMEOUT_ENV)
    comfyui_timeout_config = _config_float(
        comfyui_image.get("timeout"),
        "comfyui_image.timeout",
        DEFAULT_COMFYUI_IMAGE_TIMEOUT,
    )
    comfyui_image_timeout = (
        env_float(COMFYUI_IMAGE_TIMEOUT_ENV, comfyui_timeout_config)
        if comfyui_timeout_env
        else comfyui_timeout_config
    )
    video_endpoint = os.environ.get(VIDEO_ENDPOINT_ENV) or str(
        video.get("endpoint", DEFAULT_VIDEO_ENDPOINT)
    )
    video_model = os.environ.get(VIDEO_MODEL_ENV) or str(video.get("model", DEFAULT_VIDEO_MODEL))
    video_timeout = env_float(
        VIDEO_TIMEOUT_ENV,
        _config_float(video.get("timeout"), "video.timeout", DEFAULT_VIDEO_TIMEOUT),
    )
    themes_raw = campaigns.get("themes", DEFAULT_THEMES)
    if not isinstance(themes_raw, list) or not all(isinstance(t, str) for t in themes_raw):
        raise ValueError("config campaigns.themes must be a list of strings")
    themes = list(themes_raw)
    # The env override may be a full ``sqlite:///`` URL; only the bare
    # CONFIG path must be absolute (a relative one would resolve against
    # the process CWD and silently split from the backup/restore scripts).
    db_env = os.environ.get(DB_ENV)
    if db_env:
        db_url = db_env
    else:
        config_db = world.get("sqlite_path")
        if config_db and not os.path.isabs(config_db):
            raise ValueError("config world.sqlite_path must be absolute")
        db_url = config_db
    log_file = os.environ.get(LOG_FILE_ENV) or world.get("log_file")
    max_llm = _config_int(llm.get("max_llm_calls_per_job"), "llm.max_llm_calls_per_job", 64)
    max_media = _config_int(llm.get("max_media_calls_per_job"), "llm.max_media_calls_per_job", 8)
    media_dir = os.environ.get(MEDIA_DIR_ENV) or world.get("media_dir") or DEFAULT_MEDIA_DIR

    return RuntimeConfig(
        queue_max_pending=pending,
        llm_endpoint=endpoint,
        llm_model=model,
        llm_timeout=timeout,
        image_endpoint=image_endpoint,
        image_model=image_model,
        image_timeout=image_timeout,
        image_backend=image_backend,
        comfyui_image_endpoint=comfyui_image_endpoint,
        comfyui_image_workflow_path=comfyui_image_workflow_path,
        comfyui_image_prompt_node_id=comfyui_image_prompt_node_id,
        comfyui_image_aspect_ratio=comfyui_image_aspect_ratio,
        comfyui_image_megapixels=comfyui_image_megapixels,
        comfyui_image_timeout=comfyui_image_timeout,
        video_endpoint=video_endpoint,
        video_model=video_model,
        video_timeout=video_timeout,
        max_llm_calls_per_job=max_llm,
        max_media_calls_per_job=max_media,
        themes=themes,
        db_url=db_url,
        log_file=log_file,
        media_dir=media_dir,
    )


def reset_runtime_config() -> None:
    """Clear the cached config (tests re-point CONFIG_ENV between cases)."""
    runtime_config.cache_clear()
