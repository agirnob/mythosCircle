"""Environment + config settings (AD-22, spec-1.7).

Precedence is always env > ``deploy/config.toml`` > code default. The
deploy config is resolved once by ``app.core.config.runtime_config``;
these helpers read it first and apply env overrides on top.
"""

import os
from dataclasses import dataclass
from typing import Literal

from app.core import config as config_mod
from app.core.config import env_bool_optional, env_float, env_int, runtime_config

#: Environment variables overriding the queue limits. The pending-cap and
#: LLM keys live canonically in ``app.core.config`` (AD-22); these are the
#: settings-layer aliases for the same env vars.
MAX_PENDING_PER_CAMPAIGN = config_mod.MAX_PENDING_ENV
MAX_LLM_CALLS_PER_JOB = "MYTHOSCIRCLE_MAX_LLM_CALLS_PER_JOB"
MAX_MEDIA_CALLS_PER_JOB = "MYTHOSCIRCLE_MAX_MEDIA_CALLS_PER_JOB"

#: Defaults per the spec's Always list.
DEFAULT_MAX_PENDING_PER_CAMPAIGN = config_mod.DEFAULT_MAX_PENDING
DEFAULT_MAX_LLM_CALLS_PER_JOB = 64
DEFAULT_MAX_MEDIA_CALLS_PER_JOB = 8


@dataclass(frozen=True)
class QueueSettings:
    """Queue limits read from env + config (env > config > default)."""

    max_pending_per_campaign: int = DEFAULT_MAX_PENDING_PER_CAMPAIGN
    max_llm_calls_per_job: int = DEFAULT_MAX_LLM_CALLS_PER_JOB
    max_media_calls_per_job: int = DEFAULT_MAX_MEDIA_CALLS_PER_JOB


def queue_settings() -> QueueSettings:
    """Read the queue limits with env > config > default precedence."""
    return QueueSettings(
        max_pending_per_campaign=env_int(
            MAX_PENDING_PER_CAMPAIGN, runtime_config().queue_max_pending
        ),
        max_llm_calls_per_job=env_int(
            MAX_LLM_CALLS_PER_JOB, runtime_config().max_llm_calls_per_job
        ),
        max_media_calls_per_job=env_int(
            MAX_MEDIA_CALLS_PER_JOB, runtime_config().max_media_calls_per_job
        ),
    )


#: Environment variables for the inference adapter (spec-1.4; config in 1.7).
#: Endpoint/model/timeout keys and defaults are canonical in config.py.
LLM_ENDPOINT = config_mod.LLM_ENDPOINT_ENV
LLM_MODEL = config_mod.LLM_MODEL_ENV
LLM_API_KEY = "MYTHOSCIRCLE_LLM_API_KEY"
LLM_TIMEOUT = config_mod.LLM_TIMEOUT_ENV
LLM_MAX_TOKENS = config_mod.LLM_MAX_TOKENS_ENV
LLM_THINKING = config_mod.LLM_THINKING_ENV

#: Code defaults (spec-1.4; config.toml [llm] overrides in 1.7).
DEFAULT_LLM_ENDPOINT = config_mod.DEFAULT_LLM_ENDPOINT
DEFAULT_LLM_MODEL = config_mod.DEFAULT_LLM_MODEL
DEFAULT_LLM_TIMEOUT = config_mod.DEFAULT_LLM_TIMEOUT
DEFAULT_LLM_MAX_TOKENS = config_mod.DEFAULT_LLM_MAX_TOKENS
DEFAULT_LLM_THINKING = config_mod.DEFAULT_LLM_THINKING


@dataclass(frozen=True)
class LLMSettings:
    """The OpenAI-compatible endpoint the adapter talks to (AR9/AD-14)."""

    endpoint: str = DEFAULT_LLM_ENDPOINT
    model: str = DEFAULT_LLM_MODEL
    api_key: str | None = None
    timeout: float = DEFAULT_LLM_TIMEOUT
    #: Hard ceiling on one completion (never unbounded — see config.py).
    max_tokens: int = DEFAULT_LLM_MAX_TOKENS
    #: Tri-state reasoning control: ``None`` sends no ``chat_template_kwargs``
    #: at all (a backend rejecting unknown fields keeps working), ``False``
    #: turns the model's thinking channel off, ``True`` asks for it.
    enable_thinking: bool | None = DEFAULT_LLM_THINKING


def llm_settings() -> LLMSettings:
    """Read the inference adapter's settings (env > config > default).

    The API key stays environment-only (AD-22) — never config.toml.
    """
    resolved = runtime_config()
    endpoint_env = os.environ.get(LLM_ENDPOINT)
    model_env = os.environ.get(LLM_MODEL)
    return LLMSettings(
        # A set-but-empty env value is treated as unset (falls through to
        # config) — precedence is env > config > default (spec-1.7).
        endpoint=(endpoint_env if endpoint_env else resolved.llm_endpoint).strip()
        or resolved.llm_endpoint,
        model=(model_env if model_env else resolved.llm_model).strip() or resolved.llm_model,
        api_key=os.environ.get(LLM_API_KEY) or None,
        timeout=env_float(LLM_TIMEOUT, resolved.llm_timeout),
        max_tokens=env_int(LLM_MAX_TOKENS, resolved.llm_max_tokens, minimum=1),
        enable_thinking=env_bool_optional(LLM_THINKING, resolved.llm_thinking),
    )


#: Environment variables for the portrait-image adapter (spec-4.1; keys
#: and defaults canonical in config.py — documented placeholders until the
#: owner resolves the dev image server + model ask-first item).
IMAGE_ENDPOINT = config_mod.IMAGE_ENDPOINT_ENV
IMAGE_MODEL = config_mod.IMAGE_MODEL_ENV
IMAGE_API_KEY = "MYTHOSCIRCLE_IMAGE_API_KEY"
IMAGE_TIMEOUT = config_mod.IMAGE_TIMEOUT_ENV
MEDIA_DIR = config_mod.MEDIA_DIR_ENV

#: Code defaults (spec-4.1; config.toml [image] overrides in 1.7).
DEFAULT_IMAGE_ENDPOINT = config_mod.DEFAULT_IMAGE_ENDPOINT
DEFAULT_IMAGE_MODEL = config_mod.DEFAULT_IMAGE_MODEL
DEFAULT_IMAGE_TIMEOUT = config_mod.DEFAULT_IMAGE_TIMEOUT
DEFAULT_MEDIA_DIR = config_mod.DEFAULT_MEDIA_DIR


@dataclass(frozen=True)
class ImageSettings:
    """The OpenAI-compatible image endpoint the adapter talks to (AR9/AD-14).

    Same shape as ``LLMSettings`` — endpoint/model/api_key/timeout — so
    the portrait adapter mirrors the chat adapter 1:1 (spec-4.1 Code Map).
    """

    endpoint: str = DEFAULT_IMAGE_ENDPOINT
    model: str = DEFAULT_IMAGE_MODEL
    api_key: str | None = None
    timeout: float = DEFAULT_IMAGE_TIMEOUT


def image_settings() -> ImageSettings:
    """Read the image adapter's settings (env > config > default).

    The API key stays environment-only (AD-22) — never config.toml.
    """
    resolved = runtime_config()
    endpoint_env = os.environ.get(IMAGE_ENDPOINT)
    model_env = os.environ.get(IMAGE_MODEL)
    return ImageSettings(
        endpoint=(endpoint_env if endpoint_env else resolved.image_endpoint).strip()
        or resolved.image_endpoint,
        model=(model_env if model_env else resolved.image_model).strip() or resolved.image_model,
        api_key=os.environ.get(IMAGE_API_KEY) or None,
        timeout=env_float(IMAGE_TIMEOUT, resolved.image_timeout),
    )


#: Environment variables for the ComfyUI portrait backend (spec-4.4;
#: keys and defaults canonical in config.py). The backend switch
#: (``[image] backend``) is read via ``configured_image_backend()``;
#: the api_key stays environment-only per AD-22.
IMAGE_BACKEND = config_mod.IMAGE_BACKEND_ENV
COMFYUI_IMAGE_ENDPOINT = config_mod.COMFYUI_IMAGE_ENDPOINT_ENV
COMFYUI_IMAGE_WORKFLOW_PATH = config_mod.COMFYUI_IMAGE_WORKFLOW_PATH_ENV
COMFYUI_IMAGE_PROMPT_NODE_ID = config_mod.COMFYUI_IMAGE_PROMPT_NODE_ID_ENV
COMFYUI_IMAGE_ASPECT_RATIO = config_mod.COMFYUI_IMAGE_ASPECT_RATIO_ENV
COMFYUI_IMAGE_MEGAPIXELS = config_mod.COMFYUI_IMAGE_MEGAPIXELS_ENV
COMFYUI_IMAGE_TIMEOUT = config_mod.COMFYUI_IMAGE_TIMEOUT_ENV
COMFYUI_IMAGE_API_KEY = "MYTHOSCIRCLE_COMFYUI_IMAGE_API_KEY"

#: Code defaults (spec-4.4; config.toml [comfyui_image] overrides in 1.7).
DEFAULT_COMFYUI_IMAGE_ENDPOINT = config_mod.DEFAULT_COMFYUI_IMAGE_ENDPOINT
DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH = config_mod.DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH
DEFAULT_COMFYUI_IMAGE_PROMPT_NODE_ID = config_mod.DEFAULT_COMFYUI_IMAGE_PROMPT_NODE_ID
DEFAULT_COMFYUI_IMAGE_ASPECT_RATIO = config_mod.DEFAULT_COMFYUI_IMAGE_ASPECT_RATIO
DEFAULT_COMFYUI_IMAGE_MEGAPIXELS = config_mod.DEFAULT_COMFYUI_IMAGE_MEGAPIXELS
DEFAULT_COMFYUI_IMAGE_TIMEOUT = config_mod.DEFAULT_COMFYUI_IMAGE_TIMEOUT


@dataclass(frozen=True)
class ComfyUIImageSettings:
    """The ComfyUI portrait backend the adapter talks to (spec-4.4).

    Workflow-driven, not model-driven: the operator's workflow JSON
    carries the node graph and model weights; these knobs configure
    only the endpoint, the prompt node to inject into, the documented
    generation shape (``aspect_ratio`` / ``megapixels`` are config-level
    documentation — one job = one image, fixed shape per workflow, no
    per-job knobs), and the whole-call timeout. The API key stays
    environment-only (AD-22) — never config.toml.
    """

    endpoint: str = DEFAULT_COMFYUI_IMAGE_ENDPOINT
    workflow_path: str = DEFAULT_COMFYUI_IMAGE_WORKFLOW_PATH
    prompt_node_id: str = DEFAULT_COMFYUI_IMAGE_PROMPT_NODE_ID
    aspect_ratio: str = DEFAULT_COMFYUI_IMAGE_ASPECT_RATIO
    megapixels: float = DEFAULT_COMFYUI_IMAGE_MEGAPIXELS
    timeout: float = DEFAULT_COMFYUI_IMAGE_TIMEOUT
    api_key: str | None = None


def comfyui_image_settings() -> ComfyUIImageSettings:
    """Read the ComfyUI image adapter's settings (env > config > default).

    The API key stays environment-only (AD-22) — never config.toml.
    """
    resolved = runtime_config()
    endpoint_env = os.environ.get(COMFYUI_IMAGE_ENDPOINT)
    workflow_env = os.environ.get(COMFYUI_IMAGE_WORKFLOW_PATH)
    node_env = os.environ.get(COMFYUI_IMAGE_PROMPT_NODE_ID)
    aspect_env = os.environ.get(COMFYUI_IMAGE_ASPECT_RATIO)
    return ComfyUIImageSettings(
        # A set-but-empty env value is treated as unset (falls through to
        # config) — precedence is env > config > default (spec-1.7).
        endpoint=(endpoint_env if endpoint_env else resolved.comfyui_image_endpoint).strip()
        or resolved.comfyui_image_endpoint,
        workflow_path=(
            workflow_env if workflow_env else resolved.comfyui_image_workflow_path
        ).strip()
        or resolved.comfyui_image_workflow_path,
        prompt_node_id=(node_env if node_env else resolved.comfyui_image_prompt_node_id).strip()
        or resolved.comfyui_image_prompt_node_id,
        aspect_ratio=(aspect_env if aspect_env else resolved.comfyui_image_aspect_ratio).strip()
        or resolved.comfyui_image_aspect_ratio,
        # The numeric fields get the same empty-env fallthrough BEFORE
        # the numeric parse — env_float("") would raise, contradicting
        # the documented env > config > default precedence (review
        # round 1).
        megapixels=(
            env_float(COMFYUI_IMAGE_MEGAPIXELS, resolved.comfyui_image_megapixels)
            if os.environ.get(COMFYUI_IMAGE_MEGAPIXELS)
            else resolved.comfyui_image_megapixels
        ),
        timeout=(
            env_float(COMFYUI_IMAGE_TIMEOUT, resolved.comfyui_image_timeout)
            if os.environ.get(COMFYUI_IMAGE_TIMEOUT)
            else resolved.comfyui_image_timeout
        ),
        api_key=os.environ.get(COMFYUI_IMAGE_API_KEY) or None,
    )


def configured_image_backend() -> str:
    """The portrait backend switch — env > config > default (spec-4.4).

    Only the literal ``"comfyui"`` opts in; unset / misspelled / None
    resolves to ``"openai"`` and the spec-4.1 path runs unchanged
    (acceptance criterion).
    """
    return runtime_config().image_backend


#: Environment variables for the reveal-video adapter (spec-4.2; keys
#: and defaults canonical in config.py — documented placeholders until the
#: owner resolves the dev video server + model ask-first item).
VIDEO_ENDPOINT = config_mod.VIDEO_ENDPOINT_ENV
VIDEO_MODEL = config_mod.VIDEO_MODEL_ENV
VIDEO_API_KEY = "MYTHOSCIRCLE_VIDEO_API_KEY"
VIDEO_TIMEOUT = config_mod.VIDEO_TIMEOUT_ENV

#: Code defaults (spec-4.2; config.toml [video] overrides in 1.7).
DEFAULT_VIDEO_ENDPOINT = config_mod.DEFAULT_VIDEO_ENDPOINT
DEFAULT_VIDEO_MODEL = config_mod.DEFAULT_VIDEO_MODEL
DEFAULT_VIDEO_TIMEOUT = config_mod.DEFAULT_VIDEO_TIMEOUT


@dataclass(frozen=True)
class VideoSettings:
    """The OpenAI-compatible video endpoint the adapter talks to (AR9/AD-14).

    Same shape as ``ImageSettings`` — endpoint/model/api_key/timeout — so
    the reveal-video adapter mirrors the portrait adapter 1:1 (spec-4.2
    Code Map).
    """

    endpoint: str = DEFAULT_VIDEO_ENDPOINT
    model: str = DEFAULT_VIDEO_MODEL
    api_key: str | None = None
    timeout: float = DEFAULT_VIDEO_TIMEOUT


def video_settings() -> VideoSettings:
    """Read the video adapter's settings (env > config > default).

    The API key stays environment-only (AD-22) — never config.toml.
    """
    resolved = runtime_config()
    endpoint_env = os.environ.get(VIDEO_ENDPOINT)
    model_env = os.environ.get(VIDEO_MODEL)
    return VideoSettings(
        endpoint=(endpoint_env if endpoint_env else resolved.video_endpoint).strip()
        or resolved.video_endpoint,
        model=(model_env if model_env else resolved.video_model).strip() or resolved.video_model,
        api_key=os.environ.get(VIDEO_API_KEY) or None,
        timeout=env_float(VIDEO_TIMEOUT, resolved.video_timeout),
    )


#: Environment variables for the ComfyUI reveal-video backend (spec-4.5;
#: keys and defaults canonical in config.py). The backend switch
#: (``[video] backend``) is read via ``configured_video_backend()``; the
#: api_key stays environment-only per AD-22.
VIDEO_BACKEND = config_mod.VIDEO_BACKEND_ENV
COMFYUI_VIDEO_ENDPOINT = config_mod.COMFYUI_VIDEO_ENDPOINT_ENV
COMFYUI_VIDEO_WORKFLOW_PATH = config_mod.COMFYUI_VIDEO_WORKFLOW_PATH_ENV
COMFYUI_VIDEO_PROMPT_NODE_ID = config_mod.COMFYUI_VIDEO_PROMPT_NODE_ID_ENV
COMFYUI_VIDEO_FIRST_FRAME_NODE_ID = config_mod.COMFYUI_VIDEO_FIRST_FRAME_NODE_ID_ENV
COMFYUI_VIDEO_INPUT_DIR = config_mod.COMFYUI_VIDEO_INPUT_DIR_ENV
COMFYUI_VIDEO_TIMEOUT = config_mod.COMFYUI_VIDEO_TIMEOUT_ENV
COMFYUI_VIDEO_API_KEY = "MYTHOSCIRCLE_COMFYUI_VIDEO_API_KEY"

#: Code defaults (spec-4.5; config.toml [comfyui_video] overrides in 1.7).
DEFAULT_COMFYUI_VIDEO_ENDPOINT = config_mod.DEFAULT_COMFYUI_VIDEO_ENDPOINT
DEFAULT_COMFYUI_VIDEO_WORKFLOW_PATH = config_mod.DEFAULT_COMFYUI_VIDEO_WORKFLOW_PATH
DEFAULT_COMFYUI_VIDEO_PROMPT_NODE_ID = config_mod.DEFAULT_COMFYUI_VIDEO_PROMPT_NODE_ID
DEFAULT_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID = config_mod.DEFAULT_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID
DEFAULT_COMFYUI_VIDEO_INPUT_DIR = config_mod.DEFAULT_COMFYUI_VIDEO_INPUT_DIR
DEFAULT_COMFYUI_VIDEO_TIMEOUT = config_mod.DEFAULT_COMFYUI_VIDEO_TIMEOUT


@dataclass(frozen=True)
class ComfyUIVideoSettings:
    """The ComfyUI reveal-video backend the adapter talks to (spec-4.5).

    Workflow-driven, not model-driven — the image twin's shape: the
    operator's MiniMax H3 i2v workflow JSON (in-repo at
    ``deploy/workflows/``) carries the node graph and model weights;
    these knobs configure only the endpoint, the MiniMax prompt node to
    inject into (``inputs.prompt`` — NOT ``inputs.value``), the LoadImage
    node whose ``inputs.image`` carries the staged portrait, the
    ComfyUI input directory the portrait is staged into, and the
    whole-call timeout. The API key stays environment-only (AD-22) —
    never config.toml.
    """

    endpoint: str = DEFAULT_COMFYUI_VIDEO_ENDPOINT
    workflow_path: str = DEFAULT_COMFYUI_VIDEO_WORKFLOW_PATH
    prompt_node_id: str = DEFAULT_COMFYUI_VIDEO_PROMPT_NODE_ID
    first_frame_node_id: str = DEFAULT_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID
    input_dir: str = DEFAULT_COMFYUI_VIDEO_INPUT_DIR
    timeout: float = DEFAULT_COMFYUI_VIDEO_TIMEOUT
    api_key: str | None = None


def comfyui_video_settings() -> ComfyUIVideoSettings:
    """Read the ComfyUI video adapter's settings (env > config > default).

    The API key stays environment-only (AD-22) — never config.toml.
    """
    resolved = runtime_config()
    endpoint_env = os.environ.get(COMFYUI_VIDEO_ENDPOINT)
    workflow_env = os.environ.get(COMFYUI_VIDEO_WORKFLOW_PATH)
    prompt_node_env = os.environ.get(COMFYUI_VIDEO_PROMPT_NODE_ID)
    frame_node_env = os.environ.get(COMFYUI_VIDEO_FIRST_FRAME_NODE_ID)
    input_dir_env = os.environ.get(COMFYUI_VIDEO_INPUT_DIR)
    return ComfyUIVideoSettings(
        # A set-but-empty env value is treated as unset (falls through to
        # config) — precedence is env > config > default (spec-1.7).
        endpoint=(endpoint_env if endpoint_env else resolved.comfyui_video_endpoint).strip()
        or resolved.comfyui_video_endpoint,
        workflow_path=(
            workflow_env if workflow_env else resolved.comfyui_video_workflow_path
        ).strip()
        or resolved.comfyui_video_workflow_path,
        prompt_node_id=(
            prompt_node_env if prompt_node_env else resolved.comfyui_video_prompt_node_id
        ).strip()
        or resolved.comfyui_video_prompt_node_id,
        first_frame_node_id=(
            frame_node_env if frame_node_env else resolved.comfyui_video_first_frame_node_id
        ).strip()
        or resolved.comfyui_video_first_frame_node_id,
        input_dir=(input_dir_env if input_dir_env else resolved.comfyui_video_input_dir).strip()
        or resolved.comfyui_video_input_dir,
        # The numeric field gets the same empty-env fallthrough BEFORE the
        # numeric parse — env_float("") would raise, contradicting the
        # documented env > config > default precedence (the image twin's
        # review-round-1 contract).
        timeout=(
            env_float(COMFYUI_VIDEO_TIMEOUT, resolved.comfyui_video_timeout)
            if os.environ.get(COMFYUI_VIDEO_TIMEOUT)
            else resolved.comfyui_video_timeout
        ),
        api_key=os.environ.get(COMFYUI_VIDEO_API_KEY) or None,
    )


def configured_video_backend() -> Literal["openai", "comfyui"]:
    """The reveal-video backend switch — env > config > default (spec-4.5).

    Only the literal ``"comfyui"`` opts in; unset / misspelled / None
    resolves to ``"openai"`` and the spec-4.2 path runs unchanged
    (acceptance criterion). The Literal return keeps the narrow type
    from ``RuntimeConfig.video_backend`` (review round 1).
    """
    return runtime_config().video_backend


def configured_media_dir() -> str:
    """The media storage root — env > config > default (spec-4.1).

    The media service writes portrait files under
    ``{media_dir}/{campaign_id}/{entity_id}/`` and the API serves them
    from the same root, so both resolve through this one helper.
    """
    if config_mod.MEDIA_DIR_ENV in os.environ:
        return os.environ[config_mod.MEDIA_DIR_ENV]
    return runtime_config().media_dir


#: Environment variable for the signed portrait-URL HMAC secret
#: (spec-5.2). Env-only per AD-22 — never config.toml, never the client:
#: anyone holding it can mint portrait URLs for any campaign.
MEDIA_URL_SECRET = "MYTHOSCIRCLE_MEDIA_URL_SECRET"


def media_url_secret() -> str | None:
    """The HMAC secret minting signed portrait URLs, or None when unset
    (the portrait-url route 500s — fail closed, never mint unsigned)."""
    value = os.environ.get(MEDIA_URL_SECRET)
    return value if value else None


def configured_base_url() -> str:
    """The deployment's public origin — env > config > default (spec-5.2).
    The mint route builds absolute portrait URLs from it (Forge's
    portrait override needs a public URL, not a same-origin path). A
    set-but-empty env value falls through to config (the falsy rule the
    secret reader and the numeric settings share)."""
    return os.environ.get(config_mod.BASE_URL_ENV) or runtime_config().base_url


#: Environment variable for the session lifetime (spec-1.5).
SESSION_TTL_DAYS = "MYTHOSCIRCLE_SESSION_TTL_DAYS"
#: Default session lifetime in days (AR14/AR29).
DEFAULT_SESSION_TTL_DAYS = 30


def session_ttl_days() -> int:
    """The session lifetime in days; 0 disables expiry (never expire)."""
    return env_int(SESSION_TTL_DAYS, DEFAULT_SESSION_TTL_DAYS)


def configured_themes() -> list[str]:
    """The campaign theme list from config (code seed fallback, spec-1.7)."""
    return list(runtime_config().themes)


def configured_db_url() -> str | None:
    """The DB url from config; env override wins when set."""
    if config_mod.DB_ENV in os.environ:
        return os.environ[config_mod.DB_ENV]
    return runtime_config().db_url


def configured_log_file() -> str | None:
    """The JSON-lines log path from config; env override wins when set."""
    if config_mod.LOG_FILE_ENV in os.environ:
        return os.environ[config_mod.LOG_FILE_ENV]
    return runtime_config().log_file
