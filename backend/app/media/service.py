"""Portrait generation runner (spec-4.1, AD-10).

One ``image`` job = one portrait: re-read the COMMITTED entity at run
time (the queue is a global FIFO, so the job may sit queued while the
world moves — the prompt must be a projection of the committed character
at generation time, FR12), build the prompt from the entity's AR24
``appearance`` section ONLY (never free text), budget-guard the
``images/generations`` call, write the file atomically (temp + rename,
so a crash never leaves a half-written image) under
``media_dir/{campaign_id}/{entity_id}/{ulid}.png``, then record the
manifest row through the store (AD-1 — the store is the sole writer of
the ``media`` table; the file lands BEFORE the row, so a row never
dangles) and complete the job with ``{entity_id, filename}``.

Failure vocabulary (all -> ``fail_job`` with a user-facing message, no
file/row left behind):
- entity missing at run time (deleted since enqueue): stable message;
- blank appearance: the enqueue validator already 422s this; a row
  written outside the store (test helper, future caller) fails here too;
- provider failure: ``ProviderError`` maps to an image-flavored message;
- budget exceeded: ``BudgetExceededError`` passes through untouched.

The runner writes no world rows itself (AD-1): the only write is the
manifest row via ``add_media`` — media is not world graph, no revision,
no event (AD-10).
"""

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import literal_column, select

from app.core import ids
from app.core.settings import LLMSettings, configured_video_backend
from app.pipeline.budget import BudgetExceededError, CallBudget, MediaCallBudget
from app.pipeline.worker import JobPayloadError
from app.providers.llm import ChatCompletion, ProviderError
from app.store import (
    JobStateConflictError,
    UnknownEntityError,
    add_media,
    complete_job,
    job_status,
    models,
    report_progress,
)
from app.store.candidates import BOSS_FIELDS, BOSS_ROLES
from app.store.db import session_scope

logger = logging.getLogger(__name__)


class ProviderSettings(Protocol):
    """The settings object a media runner hands to its provider — opaque
    passthrough: the runner never interprets it, so BOTH backends'
    settings shapes satisfy it (``ImageSettings``/``VideoSettings``
    from spec-4.1/4-2 and ``ComfyUIImageSettings``/``ComfyUIVideoSettings``
    from spec-4.4/4-5 — the comfyui objects carry the extra workflow
    knobs the runners never touch). The two properties are the shared
    surface; the provider itself constrains the concrete type.

    Typed so the worker's comfyui dispatch passes the comfyui settings
    object without a ``type: ignore`` (review round 1) — the alternative
    (union-typing ``settings`` per backend) would let a video job accept
    an image settings object and vice versa. Property style matches the
    read-only attributes of the frozen settings dataclasses (review
    round 1; a plain-annotation Protocol would reject them as
    read-only).
    """

    @property
    def endpoint(self) -> str: ...

    @property
    def api_key(self) -> str | None: ...


#: The known AR24 appearance keys, in canonical join order (the frozen
#: spec's prompt-source list). Unknown keys are TOLERATED (AR24 forward
#: compatibility) but never join the prompt — a portrait is a projection
#: of the committed character, and only the documented fields describe
#: how the character looks.
APPEARANCE_KEYS: tuple[str, ...] = ("face", "body", "clothing", "scars", "marks")

#: The PNG magic — the ONLY byte stream a portrait file may carry: the
#: manifest filename is always ``<ulid>.png`` and the file is served as
#: ``image/png``, so a provider response that is not a PNG would land
#: mislabeled and break the entity card's ``<img>``. Validated at the
#: write boundary, BEFORE any file or row exists.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: The mp4 (ISO-BMFF) box magic — the ONLY byte stream a reveal-video
#: file may carry: the manifest filename is always ``<ulid>.mp4`` and the
#: file is served as ``video/mp4``, so a provider response that is not an
#: ISO-BMFF stream (a 200 wrapping an HTML error page, say) would land
#: mislabeled and break the entity card's ``<video>``. Validated at the
#: write boundary, BEFORE any file or row exists — the ``PNG_SIGNATURE``
#: twin: bytes 4-8 of every ISO-BMFF file carry the ``ftyp`` box type.
MP4_SIGNATURE = b"ftyp"

#: The AR24 boss-section keys the reveal prompt may join — the conditional
#: boss block's own field list (AR24), so the video prompt is a projection
#: of exactly the documented boss fields, never free text.
BOSS_PROMPT_KEYS: tuple[str, ...] = BOSS_FIELDS

#: The reveal framing appended to every bbeg prompt (spec-4.2 Design
#: Notes): a pipeline constant with the same standing as the stat-block
#: rules text — never user-supplied, never free text.
REVEAL_FRAMING = (
    "slow cinematic BBEG reveal: one dramatic shot establishing the "
    "character's menace, presence, and scale"
)


def appearance_prompt(appearance: Any) -> str | None:
    """The portrait prompt for a committed AR24 ``appearance`` — or None
    when it cannot produce one (the run-fail / enqueue-422 condition).

    A dict joins the known keys that are present with non-blank string
    values (``face: …\nbody: …``); a string is used verbatim; blank or
    whitespace-only in either shape — and any other shape — yields None.
    Shared by the enqueue validator (store.jobs) and the runner, so the
    enqueue-time gate and the run-time check can never disagree.
    """
    if isinstance(appearance, str):
        trimmed = appearance.strip()
        return trimmed if trimmed else None
    if isinstance(appearance, dict):
        lines: list[str] = []
        for key in APPEARANCE_KEYS:
            value = appearance.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"{key}: {value.strip()}")
        return "\n".join(lines) if lines else None
    return None


def bbeg_video_prompt(data: Any) -> str | None:
    """The reveal-video prompt for a committed boss-tier entity — or None
    when it cannot produce one (the run-fail / enqueue-422 condition).

    ``data`` is the entity's committed AR24 record: the prompt joins the
    ``appearance_prompt(...)`` output, the boss section's non-blank
    values (``key: value`` lines), and an identity line
    (``name — role``), then appends the pipeline's ``REVEAL_FRAMING``
    constant. Missing/blank appearance or a missing/blank boss section —
    no non-blank documented value — yields None. Shared by the enqueue
    validator (store.jobs) and the runner, so the enqueue-time gate and
    the run-time check can never disagree (the ``appearance_prompt``
    pattern).
    """
    if not isinstance(data, dict):
        return None
    appearance = appearance_prompt(data.get("appearance"))
    if appearance is None:
        return None
    boss = data.get("boss")
    if not isinstance(boss, dict):
        return None
    boss_lines: list[str] = []
    for key in BOSS_PROMPT_KEYS:
        value = boss.get(key)
        if isinstance(value, str) and value.strip():
            boss_lines.append(f"{key}: {value.strip()}")
    if not boss_lines:
        return None
    name = data.get("name")
    role = data.get("role")
    parts = [appearance, *boss_lines]
    if isinstance(name, str) and name.strip() and isinstance(role, str) and role.strip():
        parts.append(f"{name.strip()} — {role.strip()}")
    parts.append(REVEAL_FRAMING)
    return "\n".join(parts)


def run_portrait(
    job: models.Job,
    provider: Callable[..., bytes],
    settings: ProviderSettings,
    media_dir: str | os.PathLike[str],
) -> None:
    """Run one image job to a terminal state (complete_job/fail_job).

    Deterministic per committed entity: payload contract
    (``{"entity_id": <ULID>}``) -> run-time entity re-read -> prompt from
    ``data.appearance`` -> budget-guarded provider all -> atomic file
    write -> ``add_media`` row -> ``complete_job`` with
    ``{entity_id, filename}``. Every failure propagates so the worker
    fails the job; no file/row is left behind by a failing run.
    """
    payload = job.payload
    if (
        not isinstance(payload, dict)
        or set(payload) != {"entity_id"}
        or not isinstance(payload.get("entity_id"), str)
    ):
        raise JobPayloadError("image: job payload must be exactly {'entity_id': <ULID>}")
    entity_id = payload["entity_id"]

    # Run-time re-read of the COMMITTED entity: the job may have queued
    # while the DM deleted the entity — ENTITY_MISSING fails cleanly with
    # a stable message, no file, no row.
    with session_scope() as session:
        entity = session.get(models.Entity, entity_id)
        if entity is None or entity.campaign_id != job.campaign_id:
            raise JobPayloadError(f"image: entity {entity_id} does not exist in this campaign")
        appearance = (entity.data or {}).get("appearance")
    prompt = appearance_prompt(appearance)
    if prompt is None:
        raise JobPayloadError(
            f"image: entity {entity_id} has no non-blank AR24 appearance — a portrait needs one"
        )

    budget = MediaCallBudget(job)

    # Cancel-race poll (the build_in/generate pattern): a cancel landing
    # between claim and the provider call is a no-op.
    if not _job_still_running(job):
        return
    try:
        data = budget.call(lambda: provider(prompt, settings=settings))
    except BudgetExceededError:
        raise
    except ProviderError as exc:
        # The image job's user-facing error vocabulary — "llm call
        # failed" would be a lie for a portrait (worker._error_message
        # speaks the LLM dialect; the media runner owns its own).
        raise JobPayloadError(f"image generation failed: {exc}") from exc
    if not data:
        raise JobPayloadError("image generation returned no image data")
    if not data.startswith(PNG_SIGNATURE):
        # A JPEG/WebP/blob (or a truncated body) is never written as a
        # .png: the job fails cleanly with no file and no row.
        raise JobPayloadError("image generation returned non-PNG data")

    # Atomic write FIRST (a row never dangles over a missing file), then
    # the manifest row through the store (AD-1). Filename is a fresh
    # ULID (conventions.md), validated by add_media.
    filename = f"{ids.new_id()}.png"
    target_dir = Path(media_dir) / job.campaign_id / entity_id
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / filename
    tmp_path = target_dir / f".{filename}.tmp"
    try:
        # Atomic write: temp + rename, so a crash never leaves a
        # half-written image at the final path. A disk-full / I/O error
        # (write OR replace) removes the partial temp — no .tmp litter.
        tmp_path.write_bytes(data)
        os.replace(tmp_path, final_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    try:
        add_media(job.campaign_id, entity_id, filename, "image")
    except Exception as exc:
        # ANY manifest-write failure — entity deleted (UnknownEntityError),
        # campaign deleted (UnknownCampaignError), transient DB error —
        # removes the just-written file: a failed job leaves no file and
        # no row (the module's no-dangling invariant).
        final_path.unlink(missing_ok=True)
        if isinstance(exc, UnknownEntityError):
            raise JobPayloadError(
                f"image: entity {entity_id} no longer exists — portrait discarded"
            ) from exc
        raise
    try:
        report_progress(job.id, 1.0)
        complete_job(job.id, result={"entity_id": entity_id, "filename": filename})
    except JobStateConflictError:
        # A cancel raced the terminal write: the file+row stay — the
        # portrait is real content with its file present (no dangling
        # row), and the cancelled job simply never reports success.
        raise


def run_video(
    job: models.Job,
    provider: Callable[..., bytes],
    settings: ProviderSettings,
    media_dir: str | os.PathLike[str],
) -> None:
    """Run one reveal-video job to a terminal state (spec-4.2/4.5/4.6).

    The ``run_portrait`` discipline mirrored: payload contract
    (``{"entity_id": <ULID>}``, optionally joined by the DM's approved
    ``prompt`` from spec-4.6) -> run-time entity re-read -> prompt from
    the supplied ``payload["prompt"]`` when non-blank, else
    ``bbeg_video_prompt(data)`` (spec-4.6: a supplied prompt is the
    source of truth and is used verbatim — never a substitution) ->
    (comfyui backend ONLY) the i2v source-frame gate -> budget-guarded
    provider call -> mp4-signature guard -> atomic file write ->
    ``add_media`` row -> ``complete_job`` with ``{entity_id, filename}``.
    Every failure propagates so the worker fails the job; no file/row is
    left behind by a failing run.

    The i2v source-frame (spec-4.5) is scoped to the ComfyUI backend
    ONLY (review round 1): the MiniMax workflow animates the entity's
    NEWEST portrait (the last kind=image manifest row — rowid/insertion
    order), so under ``[video] backend = "comfyui"`` the runner resolves
    it and passes its path as the provider's ``first_frame`` kwarg — and
    a boss-tier entity with NO portrait fails the job before any
    provider call (you cannot i2v without a source frame; matrix row
    NO_FIRST_FRAME). Under ``"openai"`` (the default) the spec-4.2
    text-to-video path runs EXACTLY as before: no portrait resolution,
    ``first_frame=None``, and the OpenAI provider's ignored optional
    kwarg keeps the two backends on one call shape.
    """
    payload = job.payload
    if (
        not isinstance(payload, dict)
        or not set(payload) <= {"entity_id", "prompt"}
        or not isinstance(payload.get("entity_id"), str)
    ):
        raise JobPayloadError(
            "video: job payload must be {'entity_id': <ULID>}, optionally with a non-blank 'prompt'"
        )
    entity_id = payload["entity_id"]
    supplied_prompt = payload.get("prompt")
    if supplied_prompt is not None and not isinstance(supplied_prompt, str):
        raise JobPayloadError("video: job payload 'prompt' must be a string when present")
    comfyui = configured_video_backend() == "comfyui"

    # Run-time re-read of the COMMITTED entity: the job may have queued
    # while the DM deleted the entity — ENTITY_MISSING fails cleanly with
    # a stable message, no file, no row. Under the comfyui backend, the
    # entity's NEWEST portrait (spec-4.5) is read in the SAME snapshot
    # session — plain rowid SELECT, no nested write transaction (the
    # 4-3 media-read precedent); the OpenAI path never touches the
    # manifest (spec-4.2 behavior preserved, review round 1).
    portrait_filename: str | None = None
    with session_scope() as session:
        entity = session.get(models.Entity, entity_id)
        if entity is None or entity.campaign_id != job.campaign_id:
            raise JobPayloadError(f"video: entity {entity_id} does not exist in this campaign")
        data = entity.data or {}
        if comfyui:
            portrait = session.scalar(
                select(models.Media)
                .where(
                    models.Media.campaign_id == job.campaign_id,
                    models.Media.entity_id == entity_id,
                    models.Media.kind == "image",
                )
                .order_by(literal_column("rowid").desc())
                .limit(1)
            )
            if portrait is not None:
                portrait_filename = portrait.filename
    if data.get("role") not in BOSS_ROLES:
        # Run-time mirror of the enqueue gate's NOT_BOSS row: the job may
        # have queued while the DM demoted the entity, and a demotion
        # that leaves boss data in place would still yield a prompt —
        # a clip for a non-boss is the wrong output, so fail cleanly.
        raise JobPayloadError(f"video: entity {entity_id} is no longer boss-tier (BBEG or Monster)")
    if supplied_prompt is not None:
        if not supplied_prompt.strip():
            # Run-time mirror of the enqueue gate's RENDER_BLANK_PROMPT
            # row: a blank supplied prompt is rejected — use legacy
            # (drop the key) or re-draft, never render on blank text.
            raise JobPayloadError(
                f"video: entity {entity_id} has a blank 'prompt' — re-draft or drop the prompt"
            )
        if appearance_prompt(data.get("appearance")) is None:
            # The source-frame gate still applies with a supplied prompt
            # (spec-4.6 frozen): a reveal clip needs the entity's
            # source-frame appearance even when the DM owns the prompt
            # text — only the boss-section projection is relaxed.
            raise JobPayloadError(
                f"video: entity {entity_id} has no non-blank AR24 appearance — "
                "a reveal needs a source-frame description even with a supplied prompt"
            )
        # The DM's approved prompt is the source of truth: used verbatim,
        # never a ``bbeg_video_prompt`` substitution (spec-4.6).
        prompt = supplied_prompt
    else:
        built = bbeg_video_prompt(data)
        if built is None:
            raise JobPayloadError(
                f"video: entity {entity_id} has no usable reveal prompt — "
                "a boss-tier reveal needs a non-blank AR24 appearance and boss section"
            )
        prompt = built
    first_frame: str | None = None
    if comfyui:
        if portrait_filename is None:
            # The i2v source-frame gate (comfyui backend ONLY — the
            # OpenAI path is text-to-video and never needed a source
            # frame, review round 1): a boss with no portrait
            # (kind=image manifest row) cannot render through MiniMax —
            # fail before any provider call, no file, no row (matrix row
            # NO_FIRST_FRAME).
            raise JobPayloadError(
                f"video: entity {entity_id} has no portrait — the i2v reveal needs a source frame"
            )
        first_frame = str(Path(media_dir) / job.campaign_id / entity_id / portrait_filename)

    budget = MediaCallBudget(job)

    # Cancel-race poll (the build_in/generate/portrait pattern): a cancel
    # landing between claim and the provider call is a no-op.
    if not _job_still_running(job):
        return
    try:
        video_bytes = budget.call(
            lambda: provider(prompt, settings=settings, first_frame=first_frame)
        )
    except BudgetExceededError:
        raise
    except ProviderError as exc:
        # The video job's user-facing error vocabulary — "llm call
        # failed" would be a lie for a clip (worker._error_message
        # speaks the LLM dialect; the media runner owns its own).
        raise JobPayloadError(f"video generation failed: {exc}") from exc
    if not video_bytes:
        raise JobPayloadError("video generation returned no video data")
    if len(video_bytes) < 8 or video_bytes[4:8] != MP4_SIGNATURE:
        # Non-ISO-BMFF bytes (an HTML error page, a webm, a truncated
        # body) are never written as a .mp4: the job fails cleanly with
        # no file and no row.
        raise JobPayloadError("video generation returned non-mp4 data")

    # Atomic write FIRST (a row never dangles over a missing file), then
    # the manifest row through the store (AD-1). Filename is a fresh
    # ULID (conventions.md), validated by add_media.
    filename = f"{ids.new_id()}.mp4"
    target_dir = Path(media_dir) / job.campaign_id / entity_id
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / filename
    tmp_path = target_dir / f".{filename}.tmp"
    try:
        # Atomic write: temp + rename, so a crash never leaves a
        # half-written clip at the final path. A disk-full / I/O error
        # (write OR replace) removes the partial temp — no .tmp litter.
        tmp_path.write_bytes(video_bytes)
        os.replace(tmp_path, final_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    try:
        add_media(job.campaign_id, entity_id, filename, "video")
    except Exception as exc:
        # ANY manifest-write failure — entity deleted (UnknownEntityError),
        # campaign deleted (UnknownCampaignError), transient DB error —
        # removes the just-written file: a failed job leaves no file and
        # no row (the module's no-dangling invariant).
        final_path.unlink(missing_ok=True)
        if isinstance(exc, UnknownEntityError):
            raise JobPayloadError(
                f"video: entity {entity_id} no longer exists — clip discarded"
            ) from exc
        raise
    try:
        report_progress(job.id, 1.0)
        complete_job(job.id, result={"entity_id": entity_id, "filename": filename})
    except JobStateConflictError:
        # A cancel raced the terminal write: the file+row stay — the clip
        # is real content with its file present (no dangling row), and
        # the cancelled job simply never reports success.
        raise


#: The MiniMax-H3 video-prompt writing guide ships in-repo (spec-4.6):
#: loaded into the draft instruction VERBATIM (Ask-First 2 answer: full
#: embed, no distillation) — never edited by code, only read into a
#: prompt. The deploy-contract test pins its existence + non-empty.
VIDEO_PROMPT_GUIDE_PATH = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "guides"
    / "VIDEO_PROMPT_WRITING_GUIDE_base_en.md"
)


def run_video_prompt(
    job: models.Job,
    llm: ChatCompletion,
    settings: LLMSettings,
) -> None:
    """Draft a MiniMax-H3 I2VA reveal prompt for the job's boss entity
    (spec-4.6).

    The ``run_portrait`` discipline mirrored, but the prompt IS the
    output: payload contract (``{"entity_id": <ULID>}``) -> run-time
    entity re-read -> boss-tier gate -> non-blank appearance gate (an
    I2VA draft needs the source-frame description) -> the writing guide
    loaded from its in-repo path -> one budget-guarded LLM call ->
    non-blank result -> ``complete_job`` with ``{entity_id, prompt}``.
    A draft is a job result the DM reviews/edits before any render —
    never a media row, never a world-state write (AD-1); no file is
    written. Every failure propagates so the worker fails the job; no
    draft, no leftover.
    """
    payload = job.payload
    if (
        not isinstance(payload, dict)
        or set(payload) != {"entity_id"}
        or not isinstance(payload.get("entity_id"), str)
    ):
        raise JobPayloadError("video prompt: job payload must be exactly {'entity_id': <ULID>}")
    entity_id = payload["entity_id"]

    # Run-time re-read of the COMMITTED entity: the job may have queued
    # while the DM deleted the entity or demoted it — both fail cleanly
    # with a stable message, no draft.
    with session_scope() as session:
        entity = session.get(models.Entity, entity_id)
        if entity is None or entity.campaign_id != job.campaign_id:
            raise JobPayloadError(
                f"video prompt: entity {entity_id} does not exist in this campaign"
            )
        data = entity.data or {}
    if data.get("role") not in BOSS_ROLES:
        # Run-time mirror of the enqueue gate's DRAFT_NO_BOSS row: a
        # demotion between enqueue and claim must not yield a boss-reveal
        # draft for a non-boss.
        raise JobPayloadError(
            f"video prompt: entity {entity_id} is no longer boss-tier (BBEG or Monster)"
        )
    appearance = appearance_prompt(data.get("appearance"))
    if appearance is None:
        # A MiniMax-I2VA draft needs the source-frame description: the
        # entity's non-blank appearance must anchor the prompt's Picture 1
        # alignment (matrix row DRAFT_NO_SOURCE).
        raise JobPayloadError(
            f"video prompt: entity {entity_id} has no non-blank AR24 appearance — "
            "an I2VA draft needs a source-frame description"
        )

    try:
        guide = VIDEO_PROMPT_GUIDE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        # The guide ships in-repo and is pinned by the deploy-contract
        # test; a read failure is a deployment defect, never a silent
        # prompt without the guide.
        raise JobPayloadError(f"video prompt generation failed: guide unreadable: {exc}") from exc

    instruction = _video_prompt_draft_instruction(data, appearance, guide)
    budget = CallBudget(job)

    # Cancel-race poll (the build_in/generate/portrait pattern): a cancel
    # landing between claim and the provider call is a no-op.
    if not _job_still_running(job):
        return
    try:
        draft = budget.call(lambda: llm(instruction, settings=settings))
    except BudgetExceededError:
        raise
    except ProviderError as exc:
        # The draft job's user-facing error vocabulary — "llm call
        # failed" would be a lie for a video-prompt draft (worker
        # _error_message speaks the LLM dialect; the media runner owns
        # its own).
        raise JobPayloadError(f"video prompt generation failed: {exc}") from exc
    if not draft.strip():
        raise JobPayloadError("video prompt generation returned a blank draft")
    try:
        complete_job(job.id, result={"entity_id": entity_id, "prompt": draft})
    except JobStateConflictError:
        # A cancel raced the terminal write: a draft is just a job
        # result — nothing to clean up, and the cancelled job simply
        # never reports the draft.
        raise


def _video_prompt_draft_instruction(data: dict[str, Any], appearance: str, guide: str) -> str:
    """The single-prompt draft instruction: the writing guide verbatim
    plus the entity's committed projections, closing with the I2VA
    compliance demand (spec-4.6 Design Notes).

    ``data`` is the committed AR24 record; ``appearance`` the already-
    checked non-blank appearance projection (the runner computes it once
    for the gate and reuses it here — the instruction must not repeat
    the projection logic). The I2VA compliance demand is the guardrail
    against the hallucinated-dialogue failure: the draft MUST lead with
    the guide's Picture-1 alignment line and carry the three core
    fields, with ``N/A`` for silence.
    """
    parts: list[str] = [
        "You are writing the video prompt for a MiniMax-H3 image-to-video "
        "generation: a slow cinematic BBEG reveal clip from the entity's "
        "portrait. Follow the writing guide EXACTLY. Your prompt MUST: "
        "(1) open with the guide's I2VA picture-alignment instruction; "
        "(2) carry the three core fields `integrated_multimodal_description`, "
        "`overall_soundscape`, `non_diegetic_music`; (3) use `overall_soundscape: N/A` "
        "and `non_diegetic_music: N/A` when the scene is silent — never invent "
        "dialogue, singing, or music the character would not have. "
        "Committed character data:",
        "",
    ]
    name = data.get("name")
    role = data.get("role")
    if isinstance(name, str) and name.strip() and isinstance(role, str) and role.strip():
        parts.append(f"name — role: {name.strip()} — {role.strip()}")
    parts.append(f"appearance: {appearance}")
    boss = data.get("boss")
    if isinstance(boss, dict):
        boss_lines: list[str] = []
        for key in BOSS_PROMPT_KEYS:
            value = boss.get(key)
            if isinstance(value, str) and value.strip():
                boss_lines.append(f"{key}: {value.strip()}")
        if boss_lines:
            parts.append("boss: " + " | ".join(boss_lines))
    for key in ("background", "goals"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}: {value.strip()}")
    parts.extend(
        [
            "",
            "THE WRITING GUIDE (verbatim, follow it exactly):",
            "-----",
            guide,
            "-----",
            "Emit ONLY the I2VA prompt itself — nothing else, no commentary, no markdown fences.",
        ]
    )
    return "\n".join(parts)


def _job_still_running(job: models.Job) -> bool:
    """Cancel-race poll (build_in/generate's pattern): a job cancelled
    between claim and the provider call is a no-op.

    A transient status-READ error must never wedge the queue: returning
    True lets the job run to a terminal state; returning False here would
    leave it ``running`` forever with ``claim_next_job`` refusing every
    claim.
    """
    try:
        state, _position = job_status(job.id)
    except Exception:  # noqa: BLE001 - a status error must never wedge the queue
        logger.exception("worker state check failed for job %s", job.id)
        return True
    return state is None or state.state == "running"


# ---------------------------------------------------------------------------
# Reclaim-on-delete (spec-4.3, AD-10): the API layer's post-commit half
# ---------------------------------------------------------------------------


def _rmtree_best_effort(target: Path) -> None:
    """Best-effort and fully idempotent recursive delete: a missing
    target is a silent no-op, any other ``OSError`` is logged and
    swallowed — a reclaim failure never turns a successful 204 into an
    error (spec-4.3), and the rows are already gone regardless.
    """
    try:
        shutil.rmtree(target)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("media reclaim failed for %s: %s", target, exc)


def reclaim_entity_media(
    media_dir: str | os.PathLike[str], campaign_id: str, entity_id: str
) -> None:
    """Remove ``{media_dir}/{campaign_id}/{entity_id}/`` after the store
    committed the manifest-row deletion — the file half of AD-10's
    "media are reclaimed when their entity is deleted" (the store owns
    rows; this owns files).

    Rows-first ordering (spec-4.3 Design Notes): a crash before this runs
    leaves garbage files but no dangling manifest row, and a failed file
    delete never re-enters the DB.
    """
    _rmtree_best_effort(Path(media_dir) / campaign_id / entity_id)


def reclaim_campaign_media(media_dir: str | os.PathLike[str], campaign_id: str) -> None:
    """Remove ``{media_dir}/{campaign_id}/`` after the store committed the
    campaign's total hard delete (AR20/AD-25; spec-4.3).

    Removes exactly the campaign's own directory — never walks above it.
    """
    _rmtree_best_effort(Path(media_dir) / campaign_id)
