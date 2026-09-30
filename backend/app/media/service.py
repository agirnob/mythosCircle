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
    prune_entity_media,
    report_progress,
)
from app.store.candidates import BOSS_ROLES
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

#: The movement and camera direction appended to the committed appearance.
REVEAL_FRAMING = (
    "Slow cinematic character reveal. Begin in shadow, then gently reveal "
    "the face and distinctive features with a gradual camera move. "
    "Keep the character's appearance consistent with the source portrait. "
    "One continuous shot, subtle motion, no cuts, text, or other characters."
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


#: The P1 portrait option vocabularies (2026-09-15) — closed lists, so
#: an in-band option can only name something the prompt composition can
#: build AND the enqueue gate can reject. An absent option means "the
#: spec-4.1 default": no directive, the refiner's own taste (existing
#: behavior preserved for clients that send only ``entity_id``).
PORTRAIT_STYLES: frozenset[str] = frozenset(
    {"photorealistic", "cartoonish", "illustration", "custom"}
)
PORTRAIT_FRAMINGS: frozenset[str] = frozenset({"portrait", "headshot", "full_body"})
PORTRAIT_BACKGROUNDS: frozenset[str] = frozenset({"scene", "plain", "dark", "transparent"})

#: The prompt directive per preset — plain descriptive phrasing the
#: Krea2 refiner echoes rather than re-styles. ``custom`` has no table
#: entry: its directive IS the DM's owned ``custom_style`` text.
_STYLE_DIRECTIVES: dict[str, str] = {
    "photorealistic": "style: photorealistic — photographic realism, natural skin and lighting",
    "cartoonish": "style: cartoon — bold outlines, flat colors, animated-series look",
    "illustration": "style: painted fantasy illustration — painterly, rich saturated color",
}
_FRAMING_DIRECTIVES: dict[str, str] = {
    "portrait": (
        "portrait of the character's face, straight at the viewer: the entire head "
        "and hair inside the frame with air above the hair, both eyes clearly "
        "visible, face centered in the upper third; waist-up body below, belt near "
        "the bottom edge, single subject, the head never cropped"
    ),
    "headshot": (
        "head-and-shoulders portrait: the face filling the upper half of the frame, "
        "both eyes clearly visible, the head inside with slight air above the hair, "
        "single subject, the head never cropped"
    ),
    "full_body": (
        "full-body portrait: the entire body inside the frame, the head and face "
        "clearly visible and detailed with air above the head, both eyes visible, "
        "feet near the bottom edge, single subject, nothing cropped"
    ),
}
_BACKGROUND_DIRECTIVES: dict[str, str] = {
    "scene": "background: a detailed surrounding scene",
    "plain": "background: a plain, clean, uncluttered light backdrop",
    "dark": "background: a plain dark backdrop, moody",
    "transparent": "background: none — the lone subject only, isolated, no environment",
}


def portrait_options(payload: Any) -> dict[str, str]:
    """Validate the optional P1 portrait knobs of an image job payload
    and return the present ones as plain strings.

    Raises ``ValueError`` on any malformed option — the caller maps it
    to its own error type (store.jobs' ``InvalidJobInputError``, the
    runner's ``JobPayloadError``), so the enqueue gate and the run-time
    check can never disagree. The canonicalization rule: a
    ``custom_style`` is ONLY meaningful under ``style=custom`` — a
    custom style on any other preset would be a silently dropped
    directive (the wrong-data-wrong-place lesson), so it is rejected.
    """
    if not isinstance(payload, dict):
        raise ValueError("image payload must be a JSON object")
    allowed = frozenset({"entity_id", "style", "framing", "background", "custom_style"})
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"image payload has unknown key(s): {', '.join(sorted(unknown))}")
    if not isinstance(payload.get("entity_id"), str) or not payload["entity_id"]:
        raise ValueError("image payload must carry a non-blank string entity_id")
    options: dict[str, str] = {}
    style = payload.get("style")
    if style is not None:
        if not isinstance(style, str) or style not in PORTRAIT_STYLES:
            raise ValueError(f"image payload style must be one of {sorted(PORTRAIT_STYLES)}")
        options["style"] = style
    framing = payload.get("framing")
    if framing is not None:
        if not isinstance(framing, str) or framing not in PORTRAIT_FRAMINGS:
            raise ValueError(f"image payload framing must be one of {sorted(PORTRAIT_FRAMINGS)}")
        options["framing"] = framing
    background = payload.get("background")
    if background is not None:
        if not isinstance(background, str) or background not in PORTRAIT_BACKGROUNDS:
            raise ValueError(
                f"image payload background must be one of {sorted(PORTRAIT_BACKGROUNDS)}"
            )
        options["background"] = background
    custom = payload.get("custom_style")
    if custom is not None:
        if not isinstance(custom, str) or not custom.strip():
            raise ValueError("image payload custom_style must be a non-blank string")
        if style != "custom":
            raise ValueError("image payload custom_style requires style=custom")
        options["custom_style"] = custom.strip()
    if style == "custom" and "custom_style" not in options:
        raise ValueError("image payload style=custom requires a non-blank custom_style")
    return options


def portrait_prompt(
    appearance: Any,
    *,
    style: str | None = None,
    framing: str | None = None,
    background: str | None = None,
    custom_style: str | None = None,
) -> str | None:
    """The portrait prompt for a committed AR24 ``appearance`` plus the
    optional P1 knobs — or None when the appearance cannot produce one
    (the run-fail / enqueue-422 condition, identical to
    ``appearance_prompt``).

    The appearance projection joins VERBATIM (spec-4.1), but the framing
    directive LEADS the prompt: a portrait is a picture of the face, and
    when the appearance text is body/torso-heavy ("waistcoat, belt laden
    with trinkets") a trailing framing line lets the model crop the head
    off (live-verified 2026-09-15 — face-anchored composition keeps the
    head in frame; trailing composition cropped it at nose/chin level).
    ``background="transparent"`` also selects the rembg workflow
    upstream — here it only steers the prompt.
    """
    base = appearance_prompt(appearance)
    if base is None:
        return None
    parts: list[str] = []
    if framing is not None:
        parts.append(_FRAMING_DIRECTIVES[framing])
    parts.append(base)
    if style is not None and style != "custom":
        # ``custom`` has no preset directive — its styling line IS the
        # DM's text below (closed vocab: any other style has an entry).
        parts.append(_STYLE_DIRECTIVES[style])
    if custom_style is not None:
        parts.append(f"styling: {custom_style}")
    if background is not None:
        parts.append(_BACKGROUND_DIRECTIVES[background])
    return "\n".join(parts)


def bbeg_video_prompt(data: Any) -> str | None:
    """The reveal-video prompt for a committed boss-tier entity — or None
    when it cannot produce one (the run-fail / enqueue-422 condition).

    Only the appearance projection and reveal direction reach the video
    provider. The boss-tier role is checked by the enqueue validator and
    runner; combat data and identity never enter this prompt.
    """
    if not isinstance(data, dict):
        return None
    appearance = appearance_prompt(data.get("appearance"))
    if appearance is None:
        return None
    return f"{appearance}\n{REVEAL_FRAMING}"


def run_portrait(
    job: models.Job,
    provider: Callable[..., bytes],
    settings: ProviderSettings,
    media_dir: str | os.PathLike[str],
) -> None:
    """Run one image job to a terminal state (complete_job/fail_job).

    Deterministic per committed entity: payload contract
    (``{"entity_id": <ULID>}`` plus the optional P1 knobs ``style`` /
    ``framing`` / ``background`` / ``custom_style``) -> run-time entity
    re-read -> prompt from ``data.appearance`` + the knobs -> budget-
    guarded provider call (``background="transparent"`` routes through
    the rembg workflow by passing ``use_rembg`` on the call — the video
    runner's ``first_frame`` pattern) -> atomic file write ->
    ``add_media`` row -> ``complete_job`` with ``{entity_id, filename, prompt}``.
    Every failure propagates so the worker fails the job; no file/row is
    left behind by a failing run.
    """
    payload = job.payload
    try:
        options = portrait_options(payload)
    except ValueError as exc:
        raise JobPayloadError(f"image: {exc}") from exc
    entity_id = payload["entity_id"]

    # Run-time re-read of the COMMITTED entity: the job may have queued
    # while the DM deleted the entity — ENTITY_MISSING fails cleanly with
    # a stable message, no file, no row.
    with session_scope() as session:
        entity = session.get(models.Entity, entity_id)
        if entity is None or entity.campaign_id != job.campaign_id:
            raise JobPayloadError(f"image: entity {entity_id} does not exist in this campaign")
        appearance = (entity.data or {}).get("appearance")
    prompt = portrait_prompt(
        appearance,
        style=options.get("style"),
        framing=options.get("framing"),
        background=options.get("background"),
        custom_style=options.get("custom_style"),
    )
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
        # ``background="transparent"`` routes through the rembg workflow:
        # the flag is passed on the call (the run_video ``first_frame``
        # pattern — providers accept and the openai path ignores it), so
        # the provider can load the second workflow + its alpha SaveImage.
        use_rembg = options.get("background") == "transparent"
        data = budget.call(lambda: provider(prompt, settings=settings, use_rembg=use_rembg))
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

    # The shared write tail: atomic file write FIRST (a row never
    # dangles over a missing file), then the manifest row through the
    # store (AD-1), then the terminal job write.
    _persist_media_output(job, media_dir=media_dir, entity_id=entity_id, data=data, kind="image")


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
            # text.
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
                "add a non-blank appearance to render this reveal"
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

    # The shared write tail: atomic file write FIRST (a row never
    # dangles over a missing file), then the manifest row through the
    # store (AD-1), then the terminal job write.
    _persist_media_output(
        job, media_dir=media_dir, entity_id=entity_id, data=video_bytes, kind="video", prompt=prompt
    )


#: Manifest kind -> (filename extension, the noun the run-time failure
#: vocabulary uses): the write-tail helper's only kind-specific atoms.
_MEDIA_KINDS: dict[str, tuple[str, str]] = {
    "image": ("png", "portrait"),
    "video": ("mp4", "clip"),
}


def _persist_media_output(
    job: models.Job,
    *,
    media_dir: str | os.PathLike[str],
    entity_id: str,
    data: bytes,
    kind: str,
    prompt: str | None = None,
) -> None:
    """The run_portrait/run_video shared write tail (epic-4 retro item
    12): atomic file write (temp + rename, file-before-row), the
    manifest row through the store (AD-1), the KEEP-5 retention prune
    (rows + files, retro item 13), then the terminal job write.

    A row never dangles over a missing file: the file lands FIRST (temp
    + rename, so a crash never leaves a half-written artifact), then
    ``add_media`` re-checks campaign + entity existence inside its own
    transaction. ANY manifest-write failure removes the just-written
    file — a failed job leaves no file and no row (the module's
    no-dangling invariant); an entity deleted mid-run surfaces the
    stable ``{kind}: entity {entity_id} no longer exists — {noun}
    discarded`` user-facing message through ``JobPayloadError``
    (UnknownEntityError is the only special-cased store failure). A
    cancel racing the terminal write raises ``JobStateConflictError``
    with the file+row kept — the artifact is real content with its file
    present (no dangling row), and the cancelled job simply never
    reports success.
    """
    extension, discard_noun = _MEDIA_KINDS[kind]
    filename = f"{ids.new_id()}.{extension}"
    target_dir = Path(media_dir) / job.campaign_id / entity_id
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / filename
    tmp_path = target_dir / f".{filename}.tmp"
    try:
        # Atomic write: temp + rename, so a crash never leaves a
        # half-written artifact at the final path. A disk-full / I/O
        # error (write OR replace) removes the partial temp — no .tmp
        # litter.
        tmp_path.write_bytes(data)
        os.replace(tmp_path, final_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    try:
        add_media(job.campaign_id, entity_id, filename, kind)
    except Exception as exc:
        # ANY manifest-write failure — entity deleted
        # (UnknownEntityError), campaign deleted (UnknownCampaignError),
        # transient DB error — removes the just-written file: a failed
        # job leaves no file and no row (the no-dangling invariant).
        final_path.unlink(missing_ok=True)
        if isinstance(exc, UnknownEntityError):
            raise JobPayloadError(
                f"{kind}: entity {entity_id} no longer exists — {discard_noun} discarded"
            ) from exc
        raise
    # KEEP-5 retention (epic-4 retro item 13, owner ruling 2026-09-10):
    # per entity, history is bounded to the 5 newest rows (rowid order) —
    # prune the surplus post-commit, then reclaim the pruned rows' FILES
    # (rows-first, the spec-4.3 delete pattern: rows in the store
    # transaction, files after the commit). A prune/reclaim failure must
    # never fail the job: the surplus rows stay referenced until the next
    # write prunes again, and per-file reclaim errors are logged and
    # swallowed by reclaim_media_file itself.
    try:
        for old in prune_entity_media(job.campaign_id, entity_id):
            reclaim_media_file(media_dir, job.campaign_id, entity_id, old.filename)
    except Exception:  # noqa: BLE001 - retention must never fail the job
        logger.warning(
            "media retention prune failed for %s/%s", job.campaign_id, entity_id, exc_info=True
        )
    try:
        report_progress(job.id, 1.0)
        result = {"entity_id": entity_id, "filename": filename}
        if kind == "video" and prompt is not None:
            result["prompt"] = prompt
        complete_job(job.id, result=result)
    except JobStateConflictError:
        # A cancel raced the terminal write: the file+row stay — the
        # artifact is real content with its file present (no dangling
        # row), and the cancelled job simply never reports success.
        raise


def run_video_prompt(
    job: models.Job,
    llm: ChatCompletion,
    settings: LLMSettings,
) -> None:
    """Draft a concise reveal prompt for the job's boss entity.

    The ``run_portrait`` discipline mirrored, but the prompt IS the
    output: payload contract (``{"entity_id": <ULID>}``) -> run-time
    entity re-read -> boss-tier gate -> non-blank appearance gate (a
    reveal draft needs the source-frame description) -> one budget-guarded
    LLM call -> non-blank result -> ``complete_job`` with ``{entity_id,
    prompt}``. If the text model cannot be reached, use the same
    appearance-based prompt as direct video rendering. A draft is a job
    result the DM reviews/edits before any render — never a media row or
    world-state write (AD-1); no file is written.
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
        # A reveal draft must stay anchored to the character's appearance.
        raise JobPayloadError(
            f"video prompt: entity {entity_id} has no non-blank AR24 appearance — "
            "a reveal draft needs a source-frame description"
        )

    instruction = _video_prompt_draft_instruction(appearance)
    budget = CallBudget(job)

    # Cancel-race poll (the build_in/generate/portrait pattern): a cancel
    # landing between claim and the provider call is a no-op.
    if not _job_still_running(job):
        return
    fallback_used = False
    try:
        draft = budget.call(lambda: llm(instruction, settings=settings))
    except BudgetExceededError:
        raise
    except ProviderError as exc:
        if exc.kind == "connection":
            # Video rendering only needs the committed appearance and the
            # portrait. A local video model may be running while the text
            # model is unloaded, so keep the draft/edit flow usable.
            logger.warning("text model unavailable for reveal prompt; using appearance draft")
            draft = f"{appearance}\n{REVEAL_FRAMING}"
            fallback_used = True
        else:
            raise JobPayloadError(f"video prompt generation failed: {exc}") from exc
    if not draft.strip():
        raise JobPayloadError("video prompt generation returned a blank draft")
    try:
        result = {"entity_id": entity_id, "prompt": draft}
        if fallback_used:
            result["source"] = "appearance_fallback"
        complete_job(job.id, result=result)
    except JobStateConflictError:
        # A cancel raced the terminal write: a draft is just a job
        # result — nothing to clean up, and the cancelled job simply
        # never reports the draft.
        raise


def _video_prompt_draft_instruction(appearance: str) -> str:
    """Ask for a short reveal prompt grounded only in committed appearance."""
    return (
        "Write a concise image-to-video prompt for a slow cinematic character reveal. "
        "Use only the appearance below for character details and keep the source "
        "portrait consistent. Reveal the character gradually in one continuous "
        "shot with a subtle camera move. Do not add identity, backstory, combat "
        "abilities, other characters, dialogue, music, captions, or extra lore. "
        "Return only the video prompt in two sentences at most.\n\n"
        f"Appearance:\n{appearance}"
    )


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


def reclaim_media_file(
    media_dir: str | os.PathLike[str], campaign_id: str, entity_id: str, filename: str
) -> None:
    """Remove ONE portrait/video file after the store committed its
    manifest-row deletion (spec-4.3) — the single-row twin of
    ``reclaim_entity_media`` (the DM's portrait delete keeps the entity,
    so only the named file may go).

    Rows-first ordering (spec-4.3 Design Notes): a crash before this runs
    leaves an orphan file but no dangling manifest row, and a failed
    unlink never re-enters the DB. A missing file is a silent no-op —
    the row is gone either way and regenerating is the recovery.
    """
    target = Path(media_dir) / campaign_id / entity_id / filename
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("media reclaim failed for %s: %s", target, exc)


def reclaim_campaign_media(media_dir: str | os.PathLike[str], campaign_id: str) -> None:
    """Remove ``{media_dir}/{campaign_id}/`` after the store committed the
    campaign's total hard delete (AR20/AD-25; spec-4.3).

    Removes exactly the campaign's own directory — never walks above it.
    """
    _rmtree_best_effort(Path(media_dir) / campaign_id)
