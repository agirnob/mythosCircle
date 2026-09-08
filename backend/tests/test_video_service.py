"""Reveal-video runner tests (spec-4.2): prompt building, file+row
ordering, budget, missing entity, provider failure, mp4 signature guard,
atomic write.

Mirrors test_media_service.py (spec-4.1) case for case. Deterministic —
the video provider is injected, never a live server (the ask-first dev
video server decision is still open; documented placeholder defaults).

Covers the I/O matrix rows HAPPY_PATH, NO_VIDEO_PROMPT (run-time),
ENTITY_MISSING, PROVIDER_FAIL, BUDGET_EXCEEDED, NON_MP4, plus the
atomic-write and file-before-row invariants (a row never dangles).
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.core import ids
from app.core.settings import VideoSettings
from app.media.service import REVEAL_FRAMING, bbeg_video_prompt, run_video
from app.pipeline.budget import BudgetExceededError
from app.pipeline.worker import JobPayloadError
from app.providers.llm import ProviderError
from app.store import (
    add_media,
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

SETTINGS = VideoSettings(endpoint="http://video.test/v1", model="vid-model")

MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00reveal-payload"

BOSS_DATA: dict[str, Any] = {
    "name": "Vashka the Unmaker",
    "role": "BBEG",
    "appearance": {"face": "a mask of fused iron", "body": "towering"},
    "boss": {
        "lair_actions": "the walls breathe",
        "legendary_actions": "three per round",
        "immunities": "fire",
        "vulnerabilities": "the old name",
    },
}


def _owner_id() -> str:
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-video-svc-{new_id()}@example.com", "password123").id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'video-svc.db'}")
    try:
        yield create_campaign(
            _owner_id(), title="Svc World", description="", theme="High Fantasy", custom_lore=""
        ).id
    finally:
        init_db(previous)


def _claim_video_job(world: str, entity_id: str) -> models.Job:
    """Enqueue the video job and claim it (deterministic runs)."""
    job_row = enqueue_job(world, "video", {"entity_id": entity_id})
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_row.id
    return claimed


def _claim_direct_video_job(world: str, entity_id: str) -> models.Job:
    """Insert a video job row DIRECTLY (bypassing the enqueue gate) and
    claim it — the gate 422s gate-bypassed shapes first and never writes
    a row, so the RUNNER's own re-checks need a hand-written row."""
    with session_scope() as session:
        row = models.Job(
            id=ids.new_id(),
            campaign_id=world,
            kind="video",
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


def _commit_with_data(world: str, data: dict[str, Any], name: str = "Vashka") -> str:
    """One committed character carrying the given AR24 data (FR2: a new
    entity needs an edge, so the subgraph carries a small anchor pair)."""
    entity_id = ids.new_id()
    anchor_id = ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(kind="character", name=name, data=data, id=entity_id),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    return entity_id


def _attach_portrait(
    world: str, entity_id: str, media_dir: Path, *, write_file: bool = True
) -> str:
    """One kind=image manifest row (+ its file) — the i2v source frame a
    video job resolves (spec-4.5): a running video needs a portrait, and
    the file is what the provider would stage."""
    filename = f"{ids.new_id()}.png"
    if write_file:
        target = media_dir / world / entity_id
        target.mkdir(parents=True, exist_ok=True)
        (target / filename).write_bytes(b"\x89PNG\r\n\x1a\nfixture-portrait")
    add_media(world, entity_id, filename, "image")
    return filename


def _mp4_provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
    """The spec-4.2 (openai) contract fake: the runner passes
    ``first_frame=None`` on the default path — no portrait resolution
    (review round 1)."""
    assert settings is SETTINGS
    assert first_frame is None
    return MP4_BYTES


# ---------------------------------------------------------------------------
# Prompt building (bbeg_video_prompt — the shared enqueue/run builder)
# ---------------------------------------------------------------------------


def test_bbeg_video_prompt_joins_appearance_boss_identity_framing() -> None:
    """The prompt is a projection of the committed AR24 record: the
    appearance join, the boss section's non-blank ``key: value`` lines,
    the identity line, then the pipeline's reveal framing constant."""
    prompt = bbeg_video_prompt(BOSS_DATA)
    assert prompt is not None
    lines = prompt.split("\n")
    assert lines[0] == "face: a mask of fused iron"
    assert lines[1] == "body: towering"
    assert lines[2] == "lair_actions: the walls breathe"
    assert lines[3] == "legendary_actions: three per round"
    assert lines[4] == "immunities: fire"
    assert lines[5] == "vulnerabilities: the old name"
    assert lines[6] == "Vashka the Unmaker — BBEG"
    assert lines[7] == REVEAL_FRAMING
    assert len(lines) == 8


def test_bbeg_video_prompt_string_appearance_verbatim() -> None:
    data = {**BOSS_DATA, "appearance": "gaunt, crowned in smoke"}
    prompt = bbeg_video_prompt(data)
    assert prompt is not None
    assert prompt.startswith("gaunt, crowned in smoke\n")


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"appearance": "", "boss": {"lair_actions": "x"}},
        {"appearance": {"face": "sharp"}, "boss": None},
        {"appearance": {"face": "sharp"}, "boss": {}},
        {"appearance": {"face": "sharp"}, "boss": {"lair_actions": "   "}},
        {"appearance": {"face": "sharp"}, "boss": "not a dict"},
        {"appearance": {"face": "sharp"}},
        "not a dict",
    ],
)
def test_bbeg_video_prompt_insufficient_shapes_yield_none(data: object) -> None:
    """NO_VIDEO_PROMPT: blank/missing appearance OR a missing/blank boss
    section (no non-blank documented value) yields None — the shared
    gate behind the enqueue 422 and the run-time fail."""
    assert bbeg_video_prompt(data) is None


def test_run_video_blank_prompt_fails_cleanly(world: str, tmp_path: Path) -> None:
    """NO_VIDEO_PROMPT at run time (a job row written OUTSIDE the enqueue
    gate — the gate 422s first and never writes a row): fails with a
    stable message BEFORE any provider call; no file, no row."""
    data = {**BOSS_DATA, "boss": {}}
    entity_id = _commit_with_data(world, data)
    job = _claim_direct_video_job(world, entity_id)
    called: list[str] = []

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        called.append(prompt)
        return MP4_BYTES

    with pytest.raises(JobPayloadError, match="no usable reveal prompt"):
        run_video(job, provider, SETTINGS, media_dir=tmp_path)
    assert called == []
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_bbeg_video_prompt_unknown_boss_keys_never_join() -> None:
    """Unknown boss keys are tolerated (AR24 forward compatibility) but
    never make a prompt: only the documented BOSS_FIELDS join."""
    data = {
        **BOSS_DATA,
        "boss": {"custom_bit": "free text", "lair_actions": "the walls breathe"},
    }
    prompt = bbeg_video_prompt(data)
    assert prompt is not None
    assert "custom_bit" not in prompt


# ---------------------------------------------------------------------------
# HAPPY_PATH: file + row + completion
# ---------------------------------------------------------------------------


def test_run_video_happy_path(world: str, tmp_path: Path) -> None:
    """HAPPY_PATH (openai, default): no file, no row, job ``running`` ->
    provider call with ``first_frame=None`` (spec-4.2 path unchanged;
    review round 1) -> atomic .mp4 write -> manifest row kind='video' ->
    completed job with {entity_id, filename} — and the prompt is the
    shared builder's projection (the enqueue gate and the run can never
    disagree). No portrait is needed or resolved on this path."""
    import os

    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)
    assert job.state == "running"
    seen_prompts: list[str] = []

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        seen_prompts.append(prompt)
        assert settings is SETTINGS
        assert first_frame is None  # the 4-2 path never resolves a portrait
        return MP4_BYTES

    run_video(job, provider, SETTINGS, media_dir=tmp_path)
    state, _position = job_status(job.id)
    assert state.state == "succeeded"
    assert state.result is not None and state.result["entity_id"] == entity_id
    filename = str(state.result["filename"])
    assert filename.endswith(".mp4")
    rows = list_media(world)
    assert len(rows) == 1
    assert rows[0].kind == "video"
    assert rows[0].entity_id == entity_id and rows[0].filename == filename
    assert (tmp_path / world / entity_id / filename).read_bytes() == MP4_BYTES
    # The prompt is the shared builder's projection — appearance + boss +
    # identity + framing, never free text.
    built = bbeg_video_prompt(BOSS_DATA)
    assert seen_prompts == [built]
    # No .tmp leftover.
    target = tmp_path / world / entity_id
    assert sorted(p.name for p in target.iterdir()) == [filename]
    assert not any(name.startswith(".") for name in os.listdir(target))


def test_run_video_missing_entity_fails_cleanly(world: str, tmp_path: Path) -> None:
    """ENTITY_MISSING: the entity was deleted after enqueue — fails with
    a stable message, no file, no row."""
    job = _claim_direct_video_job(world, ids.new_id())
    with pytest.raises(JobPayloadError, match="does not exist in this campaign"):
        run_video(job, _mp4_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []


def test_run_video_bad_payload_fails(world: str, tmp_path: Path) -> None:
    """A payload with a key outside {entity_id, prompt} fails at run time
    (the worker maps the raise to fail_job; the enqueue gate 422s
    first) — spec-4.6 relaxed the exact-shape contract to allow the
    DM's optional prompt, nothing else."""
    from app.store.db import session_scope as scope

    entity_id = _commit_with_data(world, BOSS_DATA)
    with scope() as session:
        row = models.Job(
            id=ids.new_id(),
            campaign_id=world,
            kind="video",
            payload={"entity_id": entity_id, "extra": 1},
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
    claimed = claim_next_job()
    assert claimed is not None
    with pytest.raises(JobPayloadError, match="job payload must be"):
        run_video(claimed, _mp4_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []


def test_run_video_provider_failure_fails_job_cleanly(world: str, tmp_path: Path) -> None:
    """PROVIDER_FAIL: a non-2xx provider response fails the run with the
    video-flavored user-facing message — no file, no row."""

    def failing_provider(
        prompt: str, settings: VideoSettings, first_frame: str | None = None
    ) -> bytes:
        raise ProviderError("http", status_code=502)

    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)
    with pytest.raises(JobPayloadError, match="video generation failed"):
        run_video(job, failing_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_video_connection_failure_fails_cleanly(world: str, tmp_path: Path) -> None:
    def failing_provider(
        prompt: str, settings: VideoSettings, first_frame: str | None = None
    ) -> bytes:
        raise ProviderError("connection")

    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)
    with pytest.raises(JobPayloadError, match="video generation failed"):
        run_video(job, failing_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []


def test_run_video_empty_provider_bytes_fails(world: str, tmp_path: Path) -> None:
    """A provider that returns empty bytes fails the job (never writes an
    empty .mp4 or a manifest row)."""

    def empty_provider(
        prompt: str, settings: VideoSettings, first_frame: str | None = None
    ) -> bytes:
        return b""

    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)
    with pytest.raises(JobPayloadError, match="no video data"):
        run_video(job, empty_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


@pytest.mark.parametrize(
    "bad",
    [
        b"\x1aE\xdf\xa3webm-bytes",  # a webm EBML header
        b"<html>oops</html>",  # a 200 wrapping an HTML error page
        b"\x00\x00\x00ft",  # a truncated body
    ],
)
def test_run_video_non_mp4_bytes_fails_cleanly(world: str, tmp_path: Path, bad: bytes) -> None:
    """NON_MP4: a provider 200 wrapping non-ISO-BMFF bytes (a webm, an
    HTML page, a truncated body) fails BEFORE any write — the ftyp
    guard mirrors PNG_SIGNATURE."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)

    def bad_provider(
        prompt: str,
        settings: VideoSettings,
        first_frame: str | None = None,
        payload: bytes = bad,
    ) -> bytes:
        return payload

    with pytest.raises(JobPayloadError, match="non-mp4 data"):
        run_video(job, bad_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_video_add_media_store_error_unlinks_file(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GENERIC store error from add_media (here: UnknownCampaignError,
    not the special-cased UnknownEntityError) still unlinks the
    just-written file — a failed job leaves no file and no row."""
    import app.media.service as service
    from app.store import UnknownCampaignError as StoreUnknownCampaignError

    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)

    def exploding_add_media(campaign_id: str, entity_id: str, filename: str, kind: str) -> None:
        raise StoreUnknownCampaignError(campaign_id)

    monkeypatch.setattr(service, "add_media", exploding_add_media)
    with pytest.raises(StoreUnknownCampaignError):
        run_video(job, _mp4_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not any((tmp_path / world / entity_id).glob("*"))


def test_run_video_atomic_write_removes_partial_temp(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing rename (disk-full class) removes the partial ``.tmp`` —
    no temp litter, no final file, no row."""
    import os

    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)

    def exploding_replace(src: str, dst: str) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(os, "replace", exploding_replace)
    with pytest.raises(OSError, match="No space"):
        run_video(job, _mp4_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    # iterdir(), not glob("*"): pathlib glob skips dotfiles, so a leftover
    # ``.<ulid>.mp4.tmp`` would be invisible to the old assertion.
    target = tmp_path / world / entity_id
    assert not target.exists() or list(target.iterdir()) == []


def test_run_video_demoted_entity_fails_role_gate(world: str, tmp_path: Path) -> None:
    """Run-time mirror of the enqueue NOT_BOSS gate (review round 1): an
    entity demoted while the job sat queued — role NPC, boss data still
    present, so the prompt gate alone would pass — fails cleanly with no
    file and no row."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job_row = enqueue_job(world, "video", {"entity_id": entity_id})
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_row.id
    # Demote IN PLACE while the job sits queued: same ULID re-committed
    # with role NPC and the boss data still present — the prompt gate
    # alone would pass, so only the run-time role gate stops the clip.
    from app.store.db import session_scope
    from app.store.read import latest_revision

    with session_scope() as session:
        head = latest_revision(session, world)
    commit_subgraph(
        world,
        [
            models.EntityInput(
                kind="character",
                name="Vashka",
                data={**BOSS_DATA, "role": "NPC"},
                id=entity_id,
            )
        ],
        [],
        base_revision=head.id if head is not None else None,
    )
    with pytest.raises(JobPayloadError, match="no longer boss-tier"):
        run_video(claimed, _mp4_provider, SETTINGS, media_dir=tmp_path)
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_video_budget_zero_fails_before_provider(world: str, tmp_path: Path) -> None:
    """BUDGET_EXCEEDED: max_media_calls=0 refuses BEFORE the provider
    call — no partial file, no row."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job_row = enqueue_job(world, "video", {"entity_id": entity_id}, max_media_calls=0)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_row.id
    called: list[str] = []

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        called.append(prompt)
        return MP4_BYTES

    with pytest.raises(BudgetExceededError):
        run_video(claimed, provider, SETTINGS, media_dir=tmp_path)
    assert called == []
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_video_entity_deleted_mid_run_no_dangling(world: str, tmp_path: Path) -> None:
    """Entity deleted between the run-time read and add_media: the job
    fails with a stable message and the just-written FILE is removed —
    no row, no orphan file (ENTITY_MISSING)."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)

    def deleting_provider(
        prompt: str, settings: VideoSettings, first_frame: str | None = None
    ) -> bytes:
        # Delete the entity mid-run: the file lands, then add_media
        # re-checks existence and rejects.
        from app.store import delete_entity

        delete_entity(world, entity_id, cascade=True)
        return MP4_BYTES

    with pytest.raises(JobPayloadError) as excinfo:
        run_video(job, deleting_provider, SETTINGS, media_dir=tmp_path)
    assert "no longer exists" in str(excinfo.value)
    assert list_media(world) == []
    # The directory may remain, but no FILE may: the orphaned clip
    # is removed with the failing row (a row/file never dangles).
    assert not any((tmp_path / world / entity_id).glob("*"))


# ---------------------------------------------------------------------------
# Spec-4.5: the i2v source-frame gate — scoped to the COMFYUI backend
# (the OpenAI path is text-to-video and runs unchanged, review round 1)
# ---------------------------------------------------------------------------


@pytest.fixture()
def comfyui_video_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the reveal-video dispatch at the ComfyUI backend for one
    test: ``configured_video_backend()`` (and run_video's in-run read)
    resolves env > config, so the switch rides the env var and the
    cached runtime config is reset around the case."""
    from app.core.config import reset_runtime_config

    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_BACKEND", "comfyui")
    reset_runtime_config()
    try:
        yield
    finally:
        reset_runtime_config()


def test_run_video_no_portrait_fails_cleanly(
    world: str, tmp_path: Path, comfyui_video_backend: None
) -> None:
    """NO_FIRST_FRAME (comfyui backend): a boss-tier entity with no
    kind=image manifest row fails the job BEFORE any provider call —
    i2v needs a source frame — with no video file and no video row
    (acceptance criterion)."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)
    called: list[str] = []

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        called.append(prompt)
        return MP4_BYTES

    with pytest.raises(JobPayloadError, match="no portrait"):
        run_video(job, provider, SETTINGS, media_dir=tmp_path)
    assert called == []
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


def test_run_video_passes_newest_portrait_as_first_frame(
    world: str, tmp_path: Path, comfyui_video_backend: None
) -> None:
    """HAPPY_PATH_COMFYUI's runner half: with several portraits, the
    provider receives the NEWEST (last kind=image manifest row — rowid
    order), so the reveal animates the latest committed likeness, never
    the first."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    _attach_portrait(world, entity_id, tmp_path)
    newest = _attach_portrait(world, entity_id, tmp_path)
    job = _claim_video_job(world, entity_id)
    seen_frames: list[str] = []

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        seen_frames.append(first_frame or "")
        return MP4_BYTES

    run_video(job, provider, SETTINGS, media_dir=tmp_path)
    assert seen_frames == [str(tmp_path / world / entity_id / newest)]
    state, _position = job_status(job.id)
    assert state.state == "succeeded"


def test_run_video_non_image_rows_do_not_satisfy_first_frame(
    world: str, tmp_path: Path, comfyui_video_backend: None
) -> None:
    """The source frame is specifically a PORTRAIT (kind=image): a prior
    video clip (kind=video) is not a source frame, so the job still
    fails the no-portrait gate."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    filename = f"{ids.new_id()}.mp4"
    add_media(world, entity_id, filename, "video")
    job = _claim_video_job(world, entity_id)

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        raise AssertionError("no provider call may happen without a portrait")

    with pytest.raises(JobPayloadError, match="no portrait"):
        run_video(job, provider, SETTINGS, media_dir=tmp_path)
    assert [row.kind for row in list_media(world)] == ["video"]
