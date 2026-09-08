"""Portrait runner tests (spec-4.1): prompt building, file+row ordering,
budget, missing entity, provider failure, atomic write.

Deterministic — the image provider is injected, never a live server.
Covers the I/O matrix rows HAPPY_PATH, NO_APPEARANCE (run-time),
ENTITY_MISSING, PROVIDER_FAIL, BUDGET_EXCEEDED, plus the atomic-write and
file-before-row invariants (a row never dangles).
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core import ids
from app.core.settings import ImageSettings
from app.media.service import (
    appearance_prompt,
    reclaim_campaign_media,
    reclaim_entity_media,
    run_portrait,
)
from app.pipeline.budget import BudgetExceededError
from app.pipeline.worker import JobPayloadError
from app.providers.llm import ProviderError
from app.store import (
    app_db_url,
    claim_next_job,
    commit_subgraph,
    create_campaign,
    enqueue_job,
    init_db,
    job_status,
    list_media,
    models,
    session_scope,
)

SETTINGS = ImageSettings(endpoint="http://image.test/v1", model="img-model")

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"payload"


def _owner_id() -> str:
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-media-svc-{new_id()}@example.com", "password123").id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'media-svc.db'}")
    try:
        yield create_campaign(
            _owner_id(), title="Svc World", description="", theme="High Fantasy", custom_lore=""
        ).id
    finally:
        init_db(previous)


def _claim_image_job(world: str, entity_id: str) -> models.Job:
    """Enqueue the image job and claim it (deterministic runs)."""
    job_row = enqueue_job(world, "image", {"entity_id": entity_id})
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_row.id
    return claimed


def _claim_direct_image_job(world: str, entity_id: str) -> models.Job:
    """Insert an image job row DIRECTLY (bypassing the enqueue gate) and
    claim it — the gate 422s gate-bypassed shapes first and never writes
    a row, so the RUNNER's own re-checks need a hand-written row."""
    from app.store.db import session_scope

    with session_scope() as session:
        row = models.Job(
            id=ids.new_id(),
            campaign_id=world,
            kind="image",
            payload={"entity_id": entity_id},
            state="queued",
            progress=0.0,
            max_llm_calls=1,
            max_media_calls=1,
            error=None,
            created_at="2026-09-06T00:00:00Z",
            started_at=None,
            finished_at=None,
        )
        session.add(row)
        job_id = row.id
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id
    return claimed


def _commit_with_appearance(world: str, appearance: object, name: str = "Mira Vane") -> str:
    """One committed character with the given appearance (FR2: a new
    entity needs an edge, so the subgraph carries a small anchor pair)."""
    from app.store import latest_revision

    with session_scope() as session:
        head = latest_revision(session, world)
        base = head.id if head is not None else None
    entity_id = ids.new_id()
    anchor_id = ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(
                kind="character",
                name=name,
                data={"appearance": appearance},
                id=entity_id,
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
        base_revision=base,
    )
    return entity_id


def _png_provider(prompt: str, settings: ImageSettings) -> bytes:
    assert settings is SETTINGS
    return PNG_BYTES


# ---------------------------------------------------------------------------
# Prompt building (appearance_prompt)
# ---------------------------------------------------------------------------


def test_appearance_prompt_joins_known_dict_keys() -> None:
    """A dict joins the KNOWN keys present with non-blank string values,
    in canonical order; unknown keys are tolerated but never join."""
    assert (
        appearance_prompt({"body": "lean", "face": "sharp", "unknown": "ignored", "scars": "one"})
        == "face: sharp\nbody: lean\nscars: one"
    )


def test_appearance_prompt_string_verbatim() -> None:
    assert appearance_prompt("  gaunt, ink-stained fingers  ") == "gaunt, ink-stained fingers"


@pytest.mark.parametrize(
    "appearance",
    ["", "  \n\t", {}, {"face": ""}, {"clothing": "  "}, {"unknown": "x"}, 42, None, ["list"]],
    ids=[
        "blank-string",
        "whitespace-string",
        "empty-dict",
        "blank-known",
        "blank-known-whitespace",
        "only-unknown",
        "number",
        "none",
        "list",
    ],
)
def test_appearance_prompt_blank_shapes_yield_none(appearance: object) -> None:
    assert appearance_prompt(appearance) is None


# ---------------------------------------------------------------------------
# HAPPY_PATH: file + row + completion
# ---------------------------------------------------------------------------


def test_run_portrait_happy_path(world: str, tmp_path: Path) -> None:
    """HAPPY_PATH: no file, no row, job ``running`` -> provider call ->
    PNG at media_dir/{campaign}/{entity}/{ulid}.png -> manifest row ->
    job succeeded with {entity_id, filename}. The file write lands BEFORE
    the row (a row never dangles), and the write is atomic (temp+rename:
    no partial file, no leftover temp)."""
    entity_id = _commit_with_appearance(world, {"face": "sharp features", "body": "lean"})
    job = _claim_image_job(world, entity_id)
    assert list_media(world) == []  # nothing before the run

    run_portrait(job, _png_provider, SETTINGS, media_dir=tmp_path)

    done, _position = job_status(job.id)
    assert done.state == "succeeded"
    assert done.result is not None
    assert done.result["entity_id"] == entity_id
    filename = str(done.result["filename"])
    assert filename.endswith(".png") and ids.is_valid_ulid(filename.removesuffix(".png"))
    rows = list_media(world)
    assert len(rows) == 1
    assert rows[0].filename == filename and rows[0].entity_id == entity_id
    target = tmp_path / world / entity_id
    assert (target / filename).is_file()
    assert (target / filename).read_bytes() == PNG_BYTES
    assert list(target.iterdir()) == [target / filename]  # no .tmp leftover


def test_run_portrait_verbatim_string_appearance(world: str, tmp_path: Path) -> None:
    """A string appearance is the prompt verbatim (never free text from
    other fields)."""
    entity_id = _commit_with_appearance(world, "gaunt, ink-stained fingers")
    seen: list[str] = []

    def provider(prompt: str, settings: ImageSettings) -> bytes:
        seen.append(prompt)
        return PNG_BYTES

    job = _claim_image_job(world, entity_id)
    run_portrait(job, provider, SETTINGS, media_dir=tmp_path)
    assert seen == ["gaunt, ink-stained fingers"]


def test_run_portrait_blank_appearance_fails_cleanly(world: str, tmp_path: Path) -> None:
    """NO_APPEARANCE at run time (a job row written OUTSIDE the enqueue
    gate — the gate 422s first and never writes a row): fails with a
    stable message BEFORE any provider call; no file, no row."""
    entity_id = _commit_with_appearance(world, {"face": "  "})
    job = _claim_direct_image_job(world, entity_id)
    called: list[str] = []

    def provider(prompt: str, settings: ImageSettings) -> bytes:
        called.append(prompt)
        return PNG_BYTES

    with pytest.raises(JobPayloadError, match="appearance"):
        run_portrait(job, provider, SETTINGS, media_dir=tmp_path)
    assert called == []
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_portrait_missing_entity_fails_cleanly(world: str, tmp_path: Path) -> None:
    """ENTITY_MISSING: the entity was deleted after enqueue — fails with
    a stable message before any provider call; no file, no row."""
    entity_id = _commit_with_appearance(world, "sharp")
    job = _claim_image_job(world, entity_id)
    from app.store import delete_entity

    delete_entity(world, entity_id, cascade=True)  # remove it mid-queue
    with pytest.raises(JobPayloadError, match="does not exist"):
        run_portrait(job, _png_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_portrait_bad_payload_fails(world: str, tmp_path: Path) -> None:
    """A payload that is not exactly {entity_id} fails at run time (the
    worker's text-runner precedent re-checks at dispatch: a row written
    outside the enqueue gate — direct store write, test helper — must
    never reach the provider with a malformed payload)."""
    from app.store.db import session_scope

    with session_scope() as session:
        row = models.Job(
            id=ids.new_id(),
            campaign_id=world,
            kind="image",
            payload={"prompt": "stale row"},
            state="queued",
            progress=0.0,
            max_llm_calls=1,
            max_media_calls=1,
            error=None,
            created_at="2026-09-06T00:00:00Z",
            started_at=None,
            finished_at=None,
        )
        session.add(row)
        job_id = row.id
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id
    with pytest.raises(JobPayloadError, match="entity_id"):
        run_portrait(claimed, _png_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []


def test_run_portrait_provider_failure_fails_job_cleanly(world: str, tmp_path: Path) -> None:
    """PROVIDER_FAIL: a non-2xx provider response fails the run with an
    image-flavored message; no file, no row."""
    entity_id = _commit_with_appearance(world, "sharp")

    def failing_provider(prompt: str, settings: ImageSettings) -> bytes:
        raise ProviderError("http", status_code=502)

    job = _claim_image_job(world, entity_id)
    with pytest.raises(JobPayloadError, match="image generation failed.*502"):
        run_portrait(job, failing_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_portrait_connection_failure_fails_cleanly(world: str, tmp_path: Path) -> None:
    entity_id = _commit_with_appearance(world, "sharp")

    def refusing_provider(prompt: str, settings: ImageSettings) -> bytes:
        raise ProviderError("connection")

    job = _claim_image_job(world, entity_id)
    with pytest.raises(JobPayloadError, match="connection"):
        run_portrait(job, refusing_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_portrait_empty_provider_bytes_fails(world: str, tmp_path: Path) -> None:
    """A provider that returns empty bytes fails the job (never writes an
    empty file/row)."""
    entity_id = _commit_with_appearance(world, "sharp")
    job = _claim_image_job(world, entity_id)

    def empty_provider(prompt: str, settings: ImageSettings) -> bytes:
        return b""

    with pytest.raises(JobPayloadError, match="no image data"):
        run_portrait(job, empty_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_portrait_non_png_bytes_fails_cleanly(world: str, tmp_path: Path) -> None:
    """A provider that returns non-PNG bytes (JPEG/WebP/blob) fails the
    job BEFORE anything is written: no mislabeled ``.png``, no file, no
    row (the write-boundary PNG validation)."""
    entity_id = _commit_with_appearance(world, "sharp")
    job = _claim_image_job(world, entity_id)

    def jpeg_provider(prompt: str, settings: ImageSettings) -> bytes:
        # A real JPEG magic — the exact class of mislabeled content.
        return b"\xff\xd8\xff\xe0" + b"jpeg-body"

    with pytest.raises(JobPayloadError, match="non-PNG"):
        run_portrait(job, jpeg_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_portrait_add_media_store_error_unlinks_file(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GENERIC store error from add_media (here: UnknownCampaignError,
    not the special-cased UnknownEntityError) still unlinks the
    just-written file — a failed job leaves no file and no row."""
    import app.media.service as service

    entity_id = _commit_with_appearance(world, "sharp")
    job = _claim_image_job(world, entity_id)
    from app.store import UnknownCampaignError as StoreUnknownCampaignError

    def exploding_add_media(campaign_id: str, entity_id: str, filename: str, kind: str) -> None:
        raise StoreUnknownCampaignError(campaign_id)

    monkeypatch.setattr(service, "add_media", exploding_add_media)
    with pytest.raises(StoreUnknownCampaignError):
        run_portrait(job, _png_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not any((tmp_path / world / entity_id).glob("*"))


def test_run_portrait_atomic_write_removes_partial_temp(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing rename (disk-full class) removes the partial ``.tmp`` —
    no temp litter, no final file, no row."""
    import os

    entity_id = _commit_with_appearance(world, "sharp")
    job = _claim_image_job(world, entity_id)

    def exploding_replace(src: str, dst: str) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(os, "replace", exploding_replace)
    with pytest.raises(OSError, match="No space"):
        run_portrait(job, _png_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists() or not any(
        (tmp_path / world / entity_id).glob("*")
    )


def test_run_portrait_budget_zero_fails_before_provider(world: str, tmp_path: Path) -> None:
    """BUDGET_EXCEEDED: max_media_calls=0 refuses BEFORE the provider
    call (the media budget variant of RUN_BUDGET_ZERO); no file, no row."""
    entity_id = _commit_with_appearance(world, "sharp")
    job_row = enqueue_job(world, "image", {"entity_id": entity_id}, max_media_calls=0)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_row.id
    called: list[str] = []

    def provider(prompt: str, settings: ImageSettings) -> bytes:
        called.append(prompt)
        return PNG_BYTES

    with pytest.raises(BudgetExceededError):
        run_portrait(claimed, provider, SETTINGS, media_dir=tmp_path)
    assert called == []
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_media_call_budget_counter_is_media_scoped() -> None:
    """The media budget counts MEDIA calls (max_media_calls): one allowed
    call, the second refused — and the error names the media scope."""
    from app.pipeline.budget import MediaCallBudget

    job = models.Job(id="J" * 26, max_media_calls=1)
    budget = MediaCallBudget(job)
    calls: list[str] = []

    def _record_call() -> bytes:
        calls.append("x")
        return PNG_BYTES

    budget.call(_record_call)
    with pytest.raises(BudgetExceededError) as excinfo:
        budget.call(lambda: PNG_BYTES)
    assert "media" in str(excinfo.value)
    assert calls == ["x"]


def test_run_portrait_entity_deleted_mid_run_no_dangling(world: str, tmp_path: Path) -> None:
    """Entity deleted between the run-time read and add_media: the job
    fails with a stable message and the just-written FILE is removed —
    no row, no orphan file (ENTITY_MISSING)."""
    entity_id = _commit_with_appearance(world, "sharp")
    job = _claim_image_job(world, entity_id)
    original = run_portrait

    def deleting_provider(prompt: str, settings: ImageSettings) -> bytes:
        # Delete the entity mid-run: the file lands, then add_media
        # re-checks existence and rejects.
        from app.store import delete_entity

        delete_entity(world, entity_id, cascade=True)
        return PNG_BYTES

    with pytest.raises(JobPayloadError) as excinfo:
        original(job, deleting_provider, SETTINGS, media_dir=tmp_path)
    assert "no longer exists" in str(excinfo.value)
    assert list_media(world) == []
    # The directory may remain, but no FILE may: the orphaned portrait
    # is removed with the failing row (a row/file never dangles).
    assert not any((tmp_path / world / entity_id).glob("*"))


# ---------------------------------------------------------------------------
# Reclaim-on-delete (spec-4.3, AD-10): file half, post-commit
# ---------------------------------------------------------------------------


def test_reclaim_entity_media_removes_dir_idempotently(tmp_path: Path) -> None:
    """Reclaim removes exactly the entity's media directory — leftover
    temp dotfiles included — leaves the campaign root, and a missing
    target (already gone, or never written) is a silent no-op."""
    entity_dir = tmp_path / "camp" / "ent"
    entity_dir.mkdir(parents=True)
    (entity_dir / "a.png").write_bytes(b"x")
    (entity_dir / ".a.png.tmp").write_bytes(b"x")  # atomic-write leftover
    reclaim_entity_media(tmp_path, "camp", "ent")
    assert not entity_dir.exists()
    assert (tmp_path / "camp").exists()  # never walks above the entity
    reclaim_entity_media(tmp_path, "camp", "ent")  # already gone: no-op
    reclaim_entity_media(tmp_path, "camp", "missing-entity")  # never written


def test_reclaim_entity_media_swallows_oserror_logs_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed file delete never raises: an OSError is logged as a
    warning — a reclaim failure must never turn a successful 204 into an
    error (spec-4.3), and the row is already gone regardless."""

    def exploding_rmtree(path: object) -> None:
        raise PermissionError(13, "no permission")

    monkeypatch.setattr("app.media.service.shutil.rmtree", exploding_rmtree)
    with caplog.at_level(logging.WARNING, logger="app.media.service"):
        reclaim_entity_media(tmp_path, "camp", "ent")
    assert "media reclaim failed" in caplog.text


def test_reclaim_campaign_media_removes_only_campaign_dir(tmp_path: Path) -> None:
    """Campaign reclaim removes ``{media_dir}/{campaign}`` and nothing
    above or beside it; missing targets are no-ops."""
    (tmp_path / "camp" / "ent").mkdir(parents=True)
    (tmp_path / "camp" / "ent" / "a.png").write_bytes(b"x")
    sibling = tmp_path / "other-camp"
    sibling.mkdir()
    reclaim_campaign_media(tmp_path, "camp")
    assert not (tmp_path / "camp").exists()
    assert sibling.exists()
    assert tmp_path.exists()  # the media root itself survives
    reclaim_campaign_media(tmp_path, "camp")  # idempotent
    reclaim_campaign_media(tmp_path, "missing-camp")  # never existed
