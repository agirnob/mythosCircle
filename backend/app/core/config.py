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
LLM_MAX_TOKENS_ENV = "MYTHOSCIRCLE_LLM_MAX_TOKENS"
LLM_THINKING_ENV = "MYTHOSCIRCLE_LLM_THINKING"
#: Sampling-control overrides (improvement plan G) — tri-state like
#: LLM_THINKING_ENV: unset (or set-but-empty) omits the request field.
LLM_TEMPERATURE_ENV = "MYTHOSCIRCLE_LLM_TEMPERATURE"
LLM_TOP_P_ENV = "MYTHOSCIRCLE_LLM_TOP_P"
LLM_SEED_ENV = "MYTHOSCIRCLE_LLM_SEED"
IMAGE_ENDPOINT_ENV = "MYTHOSCIRCLE_IMAGE_ENDPOINT"
IMAGE_MODEL_ENV = "MYTHOSCIRCLE_IMAGE_MODEL"
IMAGE_TIMEOUT_ENV = "MYTHOSCIRCLE_IMAGE_TIMEOUT"
VIDEO_ENDPOINT_ENV = "MYTHOSCIRCLE_VIDEO_ENDPOINT"
VIDEO_MODEL_ENV = "MYTHOSCIRCLE_VIDEO_MODEL"
VIDEO_TIMEOUT_ENV = "MYTHOSCIRCLE_VIDEO_TIMEOUT"
VIDEO_BACKEND_ENV = "MYTHOSCIRCLE_VIDEO_BACKEND"
COMFYUI_VIDEO_ENDPOINT_ENV = "MYTHOSCIRCLE_COMFYUI_VIDEO_ENDPOINT"
COMFYUI_VIDEO_WORKFLOW_PATH_ENV = "MYTHOSCIRCLE_COMFYUI_VIDEO_WORKFLOW_PATH"
COMFYUI_VIDEO_PROMPT_NODE_ID_ENV = "MYTHOSCIRCLE_COMFYUI_VIDEO_PROMPT_NODE_ID"
COMFYUI_VIDEO_FIRST_FRAME_NODE_ID_ENV = "MYTHOSCIRCLE_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID"
COMFYUI_VIDEO_INPUT_DIR_ENV = "MYTHOSCIRCLE_COMFYUI_VIDEO_INPUT_DIR"
COMFYUI_VIDEO_TIMEOUT_ENV = "MYTHOSCIRCLE_COMFYUI_VIDEO_TIMEOUT"
DB_ENV = "MYTHOSCIRCLE_DB"
LOG_FILE_ENV = "MYTHOSCIRCLE_LOG_FILE"
MEDIA_DIR_ENV = "MYTHOSCIRCLE_MEDIA_DIR"
#: Env override for the deployment's public origin (spec-5.2): the signed
#: portrait URLs minted for Forge's portrait override must be absolute.
BASE_URL_ENV = "MYTHOSCIRCLE_BASE_URL"
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


def env_bool_optional(name: str, default: bool | None) -> bool | None:
    """Parse a tri-state boolean env var (unset -> ``default``).

    Only the documented spellings are accepted; anything else fails loudly
    exactly like ``env_int`` — a typo must never silently pick a sampling
    mode. ``None`` is a meaningful value, not a missing one: it means
    "send no reasoning-control field at all".
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


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


def env_float_optional(name: str, default: float | None) -> float | None:
    """Parse a tri-state float env var (unset or set-but-empty -> ``default``).

    ``None`` is a meaningful value, not a missing one: it means "send no
    such request field at all" (the ``env_bool_optional`` rationale). A
    set-but-empty value falls through like the endpoint rule (spec-1.7
    env > config > default). Malformed, non-finite, or negative values
    fail loudly — a typo must never silently pick a sampling mode. Unlike
    ``env_float``, 0.0 is legal: temperature 0 (greedy decoding) is the
    point of reproducible sampling (improvement plan G).
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float, got {raw!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def env_int_optional(name: str, default: int | None, minimum: int = 0) -> int | None:
    """Parse a tri-state integer env var (unset or set-but-empty -> ``default``).

    ``None`` means "send no such request field at all" (improvement plan
    G); malformed or below-minimum values fail loudly exactly like
    ``env_int`` — a typo must never silently pick a sampling mode.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


#: Code defaults (fallback when neither config nor env provides).
DEFAULT_MAX_PENDING = 10
DEFAULT_LLM_ENDPOINT = "http://127.0.0.1:8080/v1"
DEFAULT_LLM_MODEL = "mythos-14b-q5"
DEFAULT_LLM_TIMEOUT = 120.0
#: Hard ceiling on one completion's generated tokens. This is a BACKSTOP,
#: not the guard against a reasoning model rambling — that is
#: ``DEFAULT_LLM_THINKING`` below, which stops the reasoning channel
#: outright. What the ceiling catches is generation that never terminates
#: (a loop, a malformed template): unbounded, such a call runs until the
#: whole-call timeout and returns nothing (measured 2026-09-10: one call
#: emitted 8000 tokens / 26,580 characters of reasoning and zero content).
#: A truncated call is a NAMED provider error, never a confusing "not valid
#: JSON" failure further down the pipeline. Sized well above any real
#: output — a 14-character build-in wave is ~3k tokens — and inside the
#: dev model's 262k context.
DEFAULT_LLM_MAX_TOKENS = 65536
#: Tri-state reasoning control for chat templates that accept
#: ``enable_thinking``. ``None`` = omit the field entirely, so a backend
#: that rejects unknown request fields (OpenAI, Azure) keeps working and
#: AR9/AD-14's "swapping engines is a config change" promise stays true.
#: The local gemma stack sets ``false``: with thinking on, one call spent
#: 8000 tokens / 26,580 characters reasoning and wrote zero content.
DEFAULT_LLM_THINKING: bool | None = None
#: Tri-state sampling controls (improvement plan G): ``None`` = omit the
#: field from the request body entirely, so the provider's own default
#: rules and a backend rejecting unknown request fields keeps working
#: (the ``DEFAULT_LLM_THINKING`` rationale). A set value rides the body
#: verbatim — temperature 0.0 (greedy decoding) plus a fixed seed pin
#: reproducible sampling; 0/0.0 are meaningful, never falsy omissions.
DEFAULT_LLM_TEMPERATURE: float | None = None
DEFAULT_LLM_TOP_P: float | None = None
DEFAULT_LLM_SEED: int | None = None
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

#: The ComfyUI portrait backend is an opt-in local-dev path (spec-4.4);
#: the repo-home decision (spec-4.5 Ask First: None) moves the workflow
#: JSONs INTO the repo at ``deploy/workflows/``, so the default
#: workflow_path is COMPUTED relative to the repo's deploy dir — never
#: a machine-specific absolute path (review round 1). ``timeout`` bonds
#: the ENTIRE call (submit + poll + fetch) — Krea2 Turbo on a local GPU
#: is 30-90s typical, hence the long default.
DEFAULT_COMFYUI_IMAGE_ENDPOINT = "http://127.0.0.1:7896"
DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH = str(
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "workflows"
    / "image_krea2_turbo_t2i_int8 (2).json"
)
DEFAULT_COMFYUI_IMAGE_PROMPT_NODE_ID = "30:19"
DEFAULT_COMFYUI_IMAGE_ASPECT_RATIO = "1:1 (Square)"
DEFAULT_COMFYUI_IMAGE_MEGAPIXELS = 1.0
DEFAULT_COMFYUI_IMAGE_TIMEOUT = 1800.0
#: The reveal-video backend switch (spec-4.5): ``"openai"`` (default —
#: spec-4.2 preserved) or ``"comfyui"`` (opt-in local-dev MiniMax
#: i2v alternative).
DEFAULT_VIDEO_BACKEND: Literal["openai", "comfyui"] = "openai"

#: The ComfyUI video backend is an opt-in local-dev path (spec-4.5);
#: the MiniMax H3 i2v workflow JSON ships in the repo at
#: ``deploy/workflows/``, so the default workflow_path is COMPUTED
#: relative to the repo's deploy dir (review round 1) while
#: ``input_dir`` stays empty (operator must point at their ComfyUI
#: install's input/). ``timeout`` bounds the ENTIRE call (submit +
#: poll + fetch) — an 11s MiniMax clip renders in ~1-2 min on a local
#: GPU, hence the long default (the image twin's rationale).
DEFAULT_COMFYUI_VIDEO_ENDPOINT = "http://127.0.0.1:7896"
DEFAULT_COMFYUI_VIDEO_WORKFLOW_PATH = str(
    Path(__file__).resolve().parents[3] / "deploy" / "workflows" / "video_minimax_h3_i2v_sage.json"
)
DEFAULT_COMFYUI_VIDEO_PROMPT_NODE_ID = "105:104"
DEFAULT_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID = "114"
DEFAULT_COMFYUI_VIDEO_INPUT_DIR = ""
DEFAULT_COMFYUI_VIDEO_TIMEOUT = 1800.0
DEFAULT_MEDIA_DIR = "/var/lib/mythoscircle/media"
#: The deployment's public origin (spec-5.2): matches the shipped
#: ``deploy/config.toml`` [server] base_url (the Caddy origin). Secrets
#: never live here (AD-22) — this is a public URL prefix only.
DEFAULT_BASE_URL = "https://world.example.tld"
DEFAULT_THEMES = ["High Fantasy", "Grimdark", "Steampunk", "Planar"]
DEFAULT_DB_URL = "sqlite:////var/lib/mythoscircle/mythoscircle.db"


@dataclass(frozen=True)
class RuntimeConfig:
    """Every operator knob resolved at runtime (env > config > default)."""

    queue_max_pending: int = DEFAULT_MAX_PENDING
    llm_endpoint: str = DEFAULT_LLM_ENDPOINT
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout: float = DEFAULT_LLM_TIMEOUT
    llm_max_tokens: int = DEFAULT_LLM_MAX_TOKENS
    llm_thinking: bool | None = DEFAULT_LLM_THINKING
    #: Tri-state sampling controls (improvement plan G) — ``None`` omits
    #: the request-body field entirely (the ``llm_thinking`` rationale).
    llm_temperature: float | None = DEFAULT_LLM_TEMPERATURE
    llm_top_p: float | None = DEFAULT_LLM_TOP_P
    llm_seed: int | None = DEFAULT_LLM_SEED
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
    video_backend: Literal["openai", "comfyui"] = DEFAULT_VIDEO_BACKEND
    comfyui_video_endpoint: str = DEFAULT_COMFYUI_VIDEO_ENDPOINT
    comfyui_video_workflow_path: str = DEFAULT_COMFYUI_VIDEO_WORKFLOW_PATH
    comfyui_video_prompt_node_id: str = DEFAULT_COMFYUI_VIDEO_PROMPT_NODE_ID
    comfyui_video_first_frame_node_id: str = DEFAULT_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID
    comfyui_video_input_dir: str = DEFAULT_COMFYUI_VIDEO_INPUT_DIR
    comfyui_video_timeout: float = DEFAULT_COMFYUI_VIDEO_TIMEOUT
    themes: list[str] = field(default_factory=lambda: list(DEFAULT_THEMES))
    db_url: str | None = None
    log_file: str | None = None
    media_dir: str = DEFAULT_MEDIA_DIR
    #: The deployment's public origin (spec-5.2) — env > config > default.
    base_url: str = DEFAULT_BASE_URL


def config_path() -> Path:
    """The config file to read: env var, then repo, then installed."""
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    if _REPO_CONFIG.exists():
        return _REPO_CONFIG
    return _INSTALLED_CONFIG


def _resolve_workflow_path(value: str) -> str:
    """Resolve a workflow path to an absolute path for the providers.

    A RELATIVE value resolves against the ACTIVE config file's
    directory — the shipped config's ``deploy/config.toml`` sits next
    to ``deploy/workflows/``, so ``workflow_path = "workflows/…json"``
    is machine-independent across checkouts (review round 1). An
    absolute value (an operator installing the config elsewhere and
    pointing at their own checkout) is used verbatim; empty stays
    empty (the job fails at first call until configured).
    """
    if not value or os.path.isabs(value):
        return value
    return str(Path(config_path()).resolve().parent / value)


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
    server = data.get("server") or {}
    world = data.get("world", {})
    queue = data.get("queue", {})
    llm = data.get("llm", {})
    image = data.get("image", {})
    video = data.get("video", {})
    comfyui_image = data.get("comfyui_image", {})
    comfyui_video = data.get("comfyui_video", {})
    campaigns = data.get("campaigns", {})

    def _config_int(value: Any, name: str, default: int, minimum: int = 0) -> int:
        if value is None:
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"config {name} must be an integer, got {value!r}") from exc
        if parsed < minimum:
            raise ValueError(f"config {name} must be >= {minimum}, got {parsed}")
        return parsed

    def _config_bool(value: Any, name: str, default: bool | None) -> bool | None:
        if value is None:
            return default
        if not isinstance(value, bool):
            raise ValueError(f"config {name} must be a boolean, got {value!r}")
        return value

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

    def _config_float_optional(value: Any, name: str, default: float | None) -> float | None:
        if value is None:
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"config {name} must be a float, got {value!r}") from exc
        if not math.isfinite(parsed):
            raise ValueError(f"config {name} must be finite, got {parsed!r}")
        if parsed < 0:
            raise ValueError(f"config {name} must be >= 0, got {parsed}")
        return parsed

    def _config_int_optional(value: Any, name: str, default: int | None) -> int | None:
        if value is None:
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"config {name} must be an integer, got {value!r}") from exc
        if parsed < 0:
            raise ValueError(f"config {name} must be >= 0, got {parsed}")
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
    # Generation ceiling: env > config > default. minimum=1 so a 0 (which
    # would forbid any output at all) fails loudly instead of silently.
    max_tokens = env_int(
        LLM_MAX_TOKENS_ENV,
        _config_int(llm.get("max_tokens"), "llm.max_tokens", DEFAULT_LLM_MAX_TOKENS, minimum=1),
        minimum=1,
    )
    thinking = env_bool_optional(
        LLM_THINKING_ENV,
        _config_bool(llm.get("thinking"), "llm.thinking", DEFAULT_LLM_THINKING),
    )
    # Sampling controls (improvement plan G): tri-state like thinking —
    # env > config > default(None). A set-but-empty env falls through
    # (the endpoint rule) and None means the request body omits the
    # field entirely; 0.0/0 are meaningful values, never falsy
    # fall-throughs (temperature 0 = greedy decoding).
    temperature = env_float_optional(
        LLM_TEMPERATURE_ENV,
        _config_float_optional(llm.get("temperature"), "llm.temperature", DEFAULT_LLM_TEMPERATURE),
    )
    top_p = env_float_optional(
        LLM_TOP_P_ENV,
        _config_float_optional(llm.get("top_p"), "llm.top_p", DEFAULT_LLM_TOP_P),
    )
    seed = env_int_optional(
        LLM_SEED_ENV,
        _config_int_optional(llm.get("seed"), "llm.seed", DEFAULT_LLM_SEED),
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
    comfyui_image_workflow_path = _resolve_workflow_path(
        os.environ.get(COMFYUI_IMAGE_WORKFLOW_PATH_ENV)
        or str(comfyui_image.get("workflow_path", DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH))
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
    # Reveal-video backend switch: env > config > default (spec-4.5).
    # Mirror of the image switch: unset / misspelled / None falls back to
    # the default OpenAI path — only the literal ``"comfyui"`` opts in,
    # never a runtime fallback chain.
    video_backend_setting = os.environ.get(VIDEO_BACKEND_ENV) or str(
        video.get("backend", DEFAULT_VIDEO_BACKEND)
    )
    video_backend: Literal["openai", "comfyui"] = (
        "comfyui" if video_backend_setting == "comfyui" else "openai"
    )
    comfyui_video_endpoint = os.environ.get(COMFYUI_VIDEO_ENDPOINT_ENV) or str(
        comfyui_video.get("endpoint", DEFAULT_COMFYUI_VIDEO_ENDPOINT)
    )
    comfyui_video_workflow_path = _resolve_workflow_path(
        os.environ.get(COMFYUI_VIDEO_WORKFLOW_PATH_ENV)
        or str(comfyui_video.get("workflow_path", DEFAULT_COMFYUI_VIDEO_WORKFLOW_PATH))
    )
    comfyui_video_prompt_node_id = os.environ.get(COMFYUI_VIDEO_PROMPT_NODE_ID_ENV) or str(
        comfyui_video.get("prompt_node_id", DEFAULT_COMFYUI_VIDEO_PROMPT_NODE_ID)
    )
    comfyui_video_first_frame_node_id = os.environ.get(
        COMFYUI_VIDEO_FIRST_FRAME_NODE_ID_ENV
    ) or str(comfyui_video.get("first_frame_node_id", DEFAULT_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID))
    comfyui_video_input_dir = os.environ.get(COMFYUI_VIDEO_INPUT_DIR_ENV) or str(
        comfyui_video.get("input_dir", DEFAULT_COMFYUI_VIDEO_INPUT_DIR)
    )
    # A set-but-empty env value is treated as unset (spec-1.7 env >
    # config > default): the numeric parse must never see "" (it would
    # raise and contradict the documented precedence — the image twin's
    # review-round-1 contract).
    comfyui_video_timeout_env = os.environ.get(COMFYUI_VIDEO_TIMEOUT_ENV)
    comfyui_video_timeout_config = _config_float(
        comfyui_video.get("timeout"),
        "comfyui_video.timeout",
        DEFAULT_COMFYUI_VIDEO_TIMEOUT,
    )
    comfyui_video_timeout = (
        env_float(COMFYUI_VIDEO_TIMEOUT_ENV, comfyui_video_timeout_config)
        if comfyui_video_timeout_env
        else comfyui_video_timeout_config
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
    base_url = os.environ.get(BASE_URL_ENV) or server.get("base_url") or DEFAULT_BASE_URL

    return RuntimeConfig(
        queue_max_pending=pending,
        llm_endpoint=endpoint,
        llm_model=model,
        llm_timeout=timeout,
        llm_max_tokens=max_tokens,
        llm_thinking=thinking,
        llm_temperature=temperature,
        llm_top_p=top_p,
        llm_seed=seed,
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
        video_backend=video_backend,
        comfyui_video_endpoint=comfyui_video_endpoint,
        comfyui_video_workflow_path=comfyui_video_workflow_path,
        comfyui_video_prompt_node_id=comfyui_video_prompt_node_id,
        comfyui_video_first_frame_node_id=comfyui_video_first_frame_node_id,
        comfyui_video_input_dir=comfyui_video_input_dir,
        comfyui_video_timeout=comfyui_video_timeout,
        max_llm_calls_per_job=max_llm,
        max_media_calls_per_job=max_media,
        themes=themes,
        db_url=db_url,
        log_file=log_file,
        media_dir=media_dir,
        base_url=base_url,
    )


def reset_runtime_config() -> None:
    """Clear the cached config (tests re-point CONFIG_ENV between cases)."""
    runtime_config.cache_clear()
