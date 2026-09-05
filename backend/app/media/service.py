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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core import ids
from app.core.settings import ImageSettings
from app.pipeline.budget import BudgetExceededError, MediaCallBudget
from app.pipeline.worker import JobPayloadError
from app.providers.llm import ProviderError
from app.store import (
    JobStateConflictError,
    UnknownEntityError,
    add_media,
    complete_job,
    job_status,
    models,
    report_progress,
)
from app.store.db import session_scope

logger = logging.getLogger(__name__)

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


def run_portrait(
    job: models.Job,
    provider: Callable[..., bytes],
    settings: ImageSettings,
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
