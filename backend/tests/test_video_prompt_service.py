"""Video-prompt draft runner tests: the ``video_prompt`` job generates a
DM-reviewable reveal prompt from the entity's committed appearance.

Mirrors test_video_service.py's runner discipline: payload contract ->
run-time entity re-read -> boss/appearance gates ->
LLM call -> non-blank check -> complete_job({entity_id, prompt}). The
LLM is injected, never a live gemma — deterministic.

Covers the I/O matrix rows DRAFT_OK, offline text-model fallback, DRAFT_BLANK_RESULT
(the enqueue-side DRAFT_NO_BOSS / DRAFT_NO_SOURCE rows live in
test_jobs.py; the run-time mirrors are here), plus the render-side
rows RENDER_WITH_PROMPT, RENDER_LEGACY, RENDER_BLANK_PROMPT.
"""

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.core import ids
from app.core.settings import LLMSettings, VideoSettings
from app.media.service import bbeg_video_prompt, run_video, run_video_prompt
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

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")

MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00reveal-payload"

I2VA_DRAFT = (
    "Begin in shadow. Slowly reveal the towering figure's fused iron mask "
    "as the camera moves closer in one continuous shot."
)

BOSS_DATA: dict[str, Any] = {
    "name": "Vashka the Unmaker",
    "role": "BBEG",
    "appearance": {"face": "a mask of fused iron", "body": "towering"},
    "boss": {"lair_actions": "the walls breathe"},
}


def _owner_id() -> str:
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-vidprompt-svc-{new_id()}@example.com", "password123").id


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'vidprompt-svc.db'}")
    try:
        yield create_campaign(
            _owner_id(), title="Svc World", description="", theme="High Fantasy", custom_lore=""
        ).id
    finally:
        init_db(previous)


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
        [
            models.EdgeInput(
                src=entity_id, dst=anchor_id, type="located_in", counter=1, reason="seeded"
            )
        ],
    )
    return entity_id


def _claim_draft_job(world: str, entity_id: str) -> models.Job:
    """Insert a video_prompt job row DIRECTLY (bypassing the enqueue
    gate) and claim it — the gate 422s first and never writes a row, so
    the RUNNER's own re-checks need a hand-written row."""
    with session_scope() as session:
        row = models.Job(
            id=ids.new_id(),
            campaign_id=world,
            kind="video_prompt",
            payload={"entity_id": entity_id},
            state="queued",
            progress=0.0,
            max_llm_calls=2,
            max_media_calls=1,
            error=None,
            created_at="2026-09-08T00:00:00Z",
            started_at=None,
            finished_at=None,
        )
        session.add(row)
        job_id = row.id
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id
    return claimed


def _llm(draft: str = I2VA_DRAFT) -> Any:
    """The fake chat-completion callable asserting the injection points:
    a settings passthrough and a non-blank instruction."""
    calls: list[str] = []

    def fake(prompt: str, settings: LLMSettings) -> str:
        assert settings is SETTINGS
        assert prompt.strip()
        calls.append(prompt)
        return draft

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


# ---------------------------------------------------------------------------
# DRAFT_OK: appearance -> LLM -> completed job with {entity_id, prompt}
# ---------------------------------------------------------------------------


def test_run_video_prompt_happy_path(world: str) -> None:
    """DRAFT_OK: the draft is the LLM's concise output, stored as
    the job result ``{entity_id, prompt}`` — never a file, never a media
    row (a prompt is not media, AD-1). Only appearance is supplied as
    character data to the drafting model."""
    entity_id = _commit_with_data(
        world,
        {**BOSS_DATA, "background": "forgotten war", "goals": "raise the fortress"},
    )
    job = _claim_draft_job(world, entity_id)
    llm = _llm()

    run_video_prompt(job, llm, SETTINGS)

    state, _position = job_status(job.id)
    assert state.state == "succeeded"
    assert state.result is not None
    assert state.result["entity_id"] == entity_id
    assert state.result["prompt"] == I2VA_DRAFT
    assert list_media(world) == []
    # The instruction contains no identity, combat, lore, or guide fields.
    instruction = llm.calls[0]
    assert "face: a mask of fused iron" in instruction
    assert "slow cinematic character reveal" in instruction
    assert "lair_actions: the walls breathe" not in instruction
    assert "Vashka the Unmaker" not in instruction
    assert "forgotten war" not in instruction
    assert "raise the fortress" not in instruction
    assert "Appearance:" in instruction
    assert "overall_soundscape" not in instruction


def test_run_video_prompt_draft_is_the_llm_output_verbatim(world: str) -> None:
    """The draft is never post-processed (no bbeg substitution, no
    framing append): whatever the LLM returns is what the DM reviews."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_draft_job(world, entity_id)
    custom = "an entirely hand-shaped I2VA prompt,\nno known fields\n"
    llm = _llm(draft=custom)

    run_video_prompt(job, llm, SETTINGS)

    state, _position = job_status(job.id)
    assert state.state == "succeeded"
    assert state.result is not None and state.result["prompt"] == custom


# ---------------------------------------------------------------------------
# Run-time gates (the enqueue mirrors; the gate 422s first, never a row)
# ---------------------------------------------------------------------------


def test_run_video_prompt_bad_payload_fails(world: str) -> None:
    """Run-time payload re-check: anything but exactly {'entity_id'} —
    even through a hand-written row — fails before any provider call."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    with session_scope() as session:
        row = models.Job(
            id=ids.new_id(),
            campaign_id=world,
            kind="video_prompt",
            payload={"entity_id": entity_id, "extra": 1},
            state="queued",
            progress=0.0,
            max_llm_calls=2,
            max_media_calls=1,
            error=None,
            created_at="2026-09-08T00:00:00Z",
            started_at=None,
            finished_at=None,
        )
        session.add(row)
        job_id = row.id
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id
    called: list[str] = []

    def never(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return I2VA_DRAFT

    with pytest.raises(JobPayloadError, match="exactly \\{'entity_id'"):
        run_video_prompt(claimed, never, SETTINGS)
    assert called == []


def test_run_video_prompt_entity_missing_fails(world: str) -> None:
    """Run-time ENTITY_MISSING mirror: the entity deleted between
    enqueue and claim fails cleanly with no draft."""
    job = _claim_draft_job(world, ids.new_id())
    called: list[str] = []

    def never(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return I2VA_DRAFT

    with pytest.raises(JobPayloadError, match="does not exist in this campaign"):
        run_video_prompt(job, never, SETTINGS)
    assert called == []


def test_run_video_prompt_demoted_boss_fails(world: str) -> None:
    """Run-time DRAFT_NO_BOSS mirror: a demotion while the draft job sat
    queued must not yield a boss-reveal draft for a non-boss."""
    data = {**BOSS_DATA, "role": "NPC"}
    entity_id = _commit_with_data(world, data)
    job = _claim_draft_job(world, entity_id)
    called: list[str] = []

    def never(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return I2VA_DRAFT

    with pytest.raises(JobPayloadError, match="no longer boss-tier"):
        run_video_prompt(job, never, SETTINGS)
    assert called == []


def test_run_video_prompt_no_appearance_fails(world: str) -> None:
    """Run-time DRAFT_NO_SOURCE mirror: a boss without a non-blank AR24
    appearance cannot anchor an I2VA draft — fails before any LLM call."""
    data = {**BOSS_DATA, "appearance": "   "}
    entity_id = _commit_with_data(world, data)
    job = _claim_draft_job(world, entity_id)
    called: list[str] = []

    def never(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return I2VA_DRAFT

    with pytest.raises(JobPayloadError, match="no non-blank AR24 appearance"):
        run_video_prompt(job, never, SETTINGS)
    assert called == []


# ---------------------------------------------------------------------------
# Text-model connection fallback / other provider failures / budget
# ---------------------------------------------------------------------------


def test_run_video_prompt_llm_down_fails_cleanly(world: str) -> None:
    """An HTTP provider failure still reports a draft-specific error."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_draft_job(world, entity_id)

    def down(prompt: str, settings: LLMSettings) -> str:
        raise ProviderError(kind="http", status_code=502)

    with pytest.raises(JobPayloadError, match=r"video prompt generation failed: .*502"):
        run_video_prompt(job, down, SETTINGS)


def test_run_video_prompt_connection_error_uses_appearance_draft(world: str) -> None:
    """A video-model-only session still gets an editable appearance draft."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_draft_job(world, entity_id)

    def down(prompt: str, settings: LLMSettings) -> str:
        raise ProviderError(kind="connection")

    run_video_prompt(job, down, SETTINGS)

    state, _position = job_status(job.id)
    assert state.state == "succeeded"
    assert state.result == {
        "entity_id": entity_id,
        "prompt": bbeg_video_prompt(BOSS_DATA),
        "source": "appearance_fallback",
    }
    assert list_media(world) == []


def test_run_video_prompt_blank_draft_fails(world: str) -> None:
    """DRAFT_BLANK_RESULT: a blank/whitespace-only draft never completes
    the job — fails with no draft recorded."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_draft_job(world, entity_id)

    def blank(prompt: str, settings: LLMSettings) -> str:
        return "   \n  "

    with pytest.raises(JobPayloadError, match="blank draft"):
        run_video_prompt(job, blank, SETTINGS)
    state, _position = job_status(job.id)
    assert state.state == "running"  # the caller (worker) records the failure


def test_run_video_prompt_budget_exceeded_fails(world: str) -> None:
    """BUDGET_EXCEEDED: the draft is an LLM call guarded by CallBudget —
    a zero-budget job refuses before any HTTP request (AR21)."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    with session_scope() as session:
        row = models.Job(
            id=ids.new_id(),
            campaign_id=world,
            kind="video_prompt",
            payload={"entity_id": entity_id},
            state="queued",
            progress=0.0,
            max_llm_calls=0,
            max_media_calls=1,
            error=None,
            created_at="2026-09-08T00:00:00Z",
            started_at=None,
            finished_at=None,
        )
        session.add(row)
        job_id = row.id
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id
    called: list[str] = []

    def never(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return I2VA_DRAFT

    with pytest.raises(BudgetExceededError):
        run_video_prompt(claimed, never, SETTINGS)
    assert called == []


# ---------------------------------------------------------------------------
# Render side: run_video with a supplied prompt (RENDER_WITH_PROMPT /
# RENDER_LEGACY / RENDER_BLANK_PROMPT)
# ---------------------------------------------------------------------------


def _claim_video_job(world: str, entity_id: str, prompt: str | None = None) -> models.Job:
    payload: dict[str, Any] = {"entity_id": entity_id}
    if prompt is not None:
        payload["prompt"] = prompt
    with session_scope() as session:
        row = models.Job(
            id=ids.new_id(),
            campaign_id=world,
            kind="video",
            payload=payload,
            state="queued",
            progress=0.0,
            max_llm_calls=1,
            max_media_calls=1,
            error=None,
            created_at="2026-09-08T00:00:00Z",
            started_at=None,
            finished_at=None,
        )
        session.add(row)
        job_id = row.id
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id
    return claimed


def test_run_video_supplied_prompt_used_verbatim(world: str, tmp_path: Path) -> None:
    """RENDER_WITH_PROMPT: the DM's approved prompt is the source of
    truth — the provider receives exactly that text, never a
    ``bbeg_video_prompt`` substitution."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    dm_prompt = "  DM's own cinematic reveal:\nno bbeg framing at all.  "
    job = _claim_video_job(world, entity_id, prompt=dm_prompt)
    seen: list[str] = []

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        seen.append(prompt)
        assert settings is not None
        assert first_frame is None
        return MP4_BYTES

    run_video(
        job,
        provider,
        VideoSettings(endpoint="http://video.test/v1", model="m"),
        media_dir=tmp_path,
    )
    assert seen == [dm_prompt]
    state, _position = job_status(job.id)
    assert state.state == "succeeded"
    assert state.result is not None and state.result["entity_id"] == entity_id
    assert state.result["prompt"] == dm_prompt


def test_run_video_legacy_payload_falls_back_to_bbeg(world: str, tmp_path: Path) -> None:
    """RENDER_LEGACY: a prompt-less ``{entity_id}`` payload still runs
    the bbeg builder — the 4-2 path is unchanged (backward compatible)."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id)
    seen: list[str] = []

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        seen.append(prompt)
        assert first_frame is None
        return MP4_BYTES

    run_video(
        job,
        provider,
        VideoSettings(endpoint="http://video.test/v1", model="m"),
        media_dir=tmp_path,
    )
    assert seen == [bbeg_video_prompt(BOSS_DATA)]
    state, _position = job_status(job.id)
    assert state.state == "succeeded"
    assert state.result is not None and state.result["prompt"] == seen[0]


def test_run_video_blank_supplied_prompt_fails(world: str, tmp_path: Path) -> None:
    """RENDER_BLANK_PROMPT at run time (a row written OUTSIDE the enqueue
    gate): a blank supplied prompt fails before any provider call —
    re-draft or drop the prompt, never render on blank text."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_video_job(world, entity_id, prompt="   ")
    called: list[str] = []

    def provider(prompt: str, settings: VideoSettings, first_frame: str | None = None) -> bytes:
        called.append(prompt)
        return MP4_BYTES

    with pytest.raises(JobPayloadError, match="blank 'prompt'"):
        run_video(
            job,
            provider,
            VideoSettings(endpoint="http://video.test/v1", model="m"),
            media_dir=tmp_path,
        )
    assert called == []
    assert list_media(world) == []
    assert not (tmp_path / world / entity_id).exists()


# H3 image-aware enhancement: legacy tests above exercise previously queued jobs.


def _source(world: str, entity_id: str, tmp_path: Path, *, fmt: str = "PNG") -> models.Media:
    image = add_media(world, entity_id, f"{ids.new_id()}.png")
    path = tmp_path / world / entity_id / image.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3, 2), "blue").save(path, format=fmt)
    return image


def _enhancement(
    world: str, entity_id: str, image_id: str, prompt: str = "Turn slowly. Silence, no music."
) -> models.Job:
    enqueue_job(
        world, "video_prompt", {"entity_id": entity_id, "image_id": image_id, "prompt": prompt}
    )
    job = claim_next_job()
    assert job is not None
    return job


def _h3() -> str:
    return json.dumps(
        {
            "integrated_multimodal_description": (
                "[Shot 1] A blue portrait holds its composition, then turns slowly."
            ),
            "overall_soundscape": "N/A",
            "non_diegetic_music": "N/A",
        }
    )


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_image_enhancement_uses_actual_owned_bytes_only(
    world: str, tmp_path: Path, fmt: str, mime: str
) -> None:
    entity_id = _commit_with_data(world, {**BOSS_DATA, "background": "secret forgotten war"})
    image = _source(world, entity_id, tmp_path, fmt=fmt)
    job = _enhancement(world, entity_id, image.id)
    seen = []

    def llm(prompt: str, settings: LLMSettings) -> str:
        seen.append((prompt, settings))
        return _h3()

    run_video_prompt(job, llm, SETTINGS, media_dir=tmp_path)
    instruction, settings = seen[0]
    assert (
        settings.image_data_url
        == f"data:{mime};base64,"
        + base64.b64encode((tmp_path / world / entity_id / image.filename).read_bytes()).decode()
    )
    assert "Turn slowly. Silence, no music." in instruction
    assert "fused iron" not in instruction and "secret forgotten war" not in instruction
    state, _ = job_status(job.id)
    assert state.result is not None
    assert state.result["image_id"] == image.id
    assert state.result["prompt"].startswith(
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
        "integrated_multimodal_description: [Shot 1]"
    )
    assert "\n\noverall_soundscape: N/A\n\nnon_diegetic_music: N/A" in state.result["prompt"]
    assert len(list_media(world)) == 1


@pytest.mark.parametrize(
    "problem", ["missing", "invalid", "oversize", "unsafe", "deleted", "foreign", "video"]
)
def test_image_enhancement_invalid_source_never_calls_provider(
    world: str, tmp_path: Path, problem: str
) -> None:
    entity_id = _commit_with_data(world, BOSS_DATA)
    image = _source(world, entity_id, tmp_path)
    job = _enhancement(world, entity_id, image.id)
    path = tmp_path / world / entity_id / image.filename
    if problem == "missing":
        path.unlink()
    elif problem == "invalid":
        path.write_bytes(b"not an image")
    elif problem == "oversize":
        with path.open("wb") as f:
            f.truncate(20 * 1024 * 1024 + 1)
    elif problem == "unsafe":
        path.unlink()
        path.symlink_to(tmp_path / "outside.png")
        Image.new("RGB", (1, 1)).save(tmp_path / "outside.png")
    else:
        with session_scope() as session:
            row = session.get(models.Media, image.id)
            assert row is not None
            if problem == "deleted":
                session.delete(row)
            elif problem == "foreign":
                row.entity_id = ids.new_id()
            else:
                row.kind = "video"
    calls: list[Any] = []

    def never(*args: Any, **kwargs: Any) -> str:
        calls.append(args)
        return _h3()

    with pytest.raises(JobPayloadError, match="source image"):
        run_video_prompt(job, never, SETTINGS, media_dir=tmp_path)
    assert calls == []


@pytest.mark.parametrize(
    "output",
    [
        "",
        "{}",
        "bad json",
        "[]",
        json.dumps(
            {
                "integrated_multimodal_description": 3,
                "overall_soundscape": "N/A",
                "non_diegetic_music": "N/A",
            }
        ),
    ],
)
def test_image_enhancement_rejects_malformed_output(
    world: str, tmp_path: Path, output: str
) -> None:
    entity_id = _commit_with_data(world, BOSS_DATA)
    image = _source(world, entity_id, tmp_path)
    job = _enhancement(world, entity_id, image.id)
    with pytest.raises(JobPayloadError, match="malformed H3"):
        run_video_prompt(job, lambda *a, **k: output, SETTINGS, media_dir=tmp_path)


@pytest.mark.parametrize("kind", ["connection", "empty", "http"])
def test_image_enhancement_provider_failure_has_no_appearance_fallback(
    world: str, tmp_path: Path, kind: str
) -> None:
    entity_id = _commit_with_data(world, BOSS_DATA)
    image = _source(world, entity_id, tmp_path)
    job = _enhancement(world, entity_id, image.id)

    def fail(*a: Any, **k: Any) -> str:
        raise ProviderError(kind, status_code=400)

    with pytest.raises(JobPayloadError, match="image-aware.*supports vision"):
        run_video_prompt(job, fail, SETTINGS, media_dir=tmp_path)
    state, _ = job_status(job.id)
    assert state.result is None


@pytest.mark.parametrize("direction", ["", "  ", None, 42, "a" * 8001])
def test_image_enhancement_invalid_direction_not_queued(
    world: str, tmp_path: Path, direction: Any
) -> None:
    from app.store.jobs import InvalidJobInputError

    entity_id = _commit_with_data(world, BOSS_DATA)
    image = _source(world, entity_id, tmp_path)
    with pytest.raises(InvalidJobInputError, match="direction"):
        enqueue_job(
            world,
            "video_prompt",
            {"entity_id": entity_id, "image_id": image.id, "prompt": direction},
        )
    assert claim_next_job() is None


@pytest.fixture()
def image_bound_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import reset_runtime_config

    monkeypatch.setenv("MYTHOSCIRCLE_VIDEO_BACKEND", "comfyui")
    reset_runtime_config()
    try:
        yield
    finally:
        reset_runtime_config()


def test_pinned_render_keeps_exact_image_and_verbatim_edited_prompt(
    world: str, tmp_path: Path, image_bound_backend: None
) -> None:
    entity_id = _commit_with_data(world, {"role": "BBEG"})
    original = _source(world, entity_id, tmp_path)
    _source(world, entity_id, tmp_path)
    approved = "  edited H3 prompt\nno music  "
    enqueue_job(
        world, "video", {"entity_id": entity_id, "image_id": original.id, "prompt": approved}
    )
    job = claim_next_job()
    assert job is not None
    seen = []

    def video(prompt: str, **kwargs: Any) -> bytes:
        seen.append((prompt, kwargs["first_frame"]))
        return MP4_BYTES

    run_video(job, video, VideoSettings(), media_dir=tmp_path)
    assert seen == [(approved, str(tmp_path / world / entity_id / original.filename))]


def test_pinned_render_deleted_source_fails_without_provider(
    world: str, tmp_path: Path, image_bound_backend: None
) -> None:
    entity_id = _commit_with_data(world, BOSS_DATA)
    image = _source(world, entity_id, tmp_path)
    enqueue_job(
        world, "video", {"entity_id": entity_id, "image_id": image.id, "prompt": "Approved"}
    )
    job = claim_next_job()
    assert job is not None
    with session_scope() as session:
        session.delete(session.get(models.Media, image.id))
    calls: list[Any] = []

    def never(*args: Any, **kwargs: Any) -> bytes:
        calls.append(args)
        return MP4_BYTES

    with pytest.raises(JobPayloadError, match="source image"):
        run_video(job, never, VideoSettings(), media_dir=tmp_path)
    assert not calls


def test_image_bound_video_refuses_text_only_backend(world: str, tmp_path: Path) -> None:
    from app.store.jobs import InvalidJobInputError

    entity_id = _commit_with_data(world, BOSS_DATA)
    image = _source(world, entity_id, tmp_path)
    payload = {"entity_id": entity_id, "image_id": image.id, "prompt": "Approved"}
    with pytest.raises(InvalidJobInputError, match="requires the ComfyUI"):
        enqueue_job(world, "video", payload)
    assert claim_next_job() is None
    job = _claim_draft_job(world, entity_id)
    job.kind = "video"
    job.payload = payload
    with pytest.raises(JobPayloadError, match="requires the ComfyUI"):
        run_video(job, lambda *a, **k: MP4_BYTES, VideoSettings(), media_dir=tmp_path)


@pytest.mark.parametrize("invalid", ["foreign_entity", "foreign_campaign", "video", "deleted"])
def test_enhancement_enqueue_rejects_foreign_or_deleted_image(
    world: str, tmp_path: Path, invalid: str
) -> None:
    from app.store.jobs import InvalidJobInputError

    entity_id = _commit_with_data(world, BOSS_DATA)
    image = _source(world, entity_id, tmp_path)
    foreign_campaign = create_campaign(
        _owner_id(), title="Foreign", description="", theme="High Fantasy", custom_lore=""
    ).id
    with session_scope() as session:
        row = session.get(models.Media, image.id)
        assert row is not None
        if invalid == "foreign_entity":
            row.entity_id = ids.new_id()
        elif invalid == "foreign_campaign":
            row.campaign_id = foreign_campaign
        elif invalid == "video":
            row.kind = "video"
        else:
            session.delete(row)
    with pytest.raises(InvalidJobInputError, match="source image does not exist"):
        enqueue_job(
            world,
            "video_prompt",
            {"entity_id": entity_id, "image_id": image.id, "prompt": "Direction"},
        )
    assert claim_next_job() is None


def test_enhancement_zero_budget_and_cancellation_skip_provider(world: str, tmp_path: Path) -> None:
    from app.store import cancel_job

    entity_id = _commit_with_data(world, BOSS_DATA)
    image = _source(world, entity_id, tmp_path)
    job = _enhancement(world, entity_id, image.id)
    job.max_llm_calls = 0
    with pytest.raises(BudgetExceededError):
        run_video_prompt(
            job, lambda *a, **k: pytest.fail("provider called"), SETTINGS, media_dir=tmp_path
        )
    cancel_job(job.id)
    job.max_llm_calls = 2
    run_video_prompt(
        job, lambda *a, **k: pytest.fail("provider called"), SETTINGS, media_dir=tmp_path
    )


@pytest.mark.parametrize("problem", ["pixel_limit", "truncated_png", "escaped_symlink"])
def test_owned_source_validation_rejects_decodable_bombs_and_broken_files(
    world: str, tmp_path: Path, problem: str
) -> None:
    entity_id = _commit_with_data(world, BOSS_DATA)
    image = _source(world, entity_id, tmp_path)
    job = _enhancement(world, entity_id, image.id)
    path = tmp_path / world / entity_id / image.filename
    if problem == "pixel_limit":
        Image.new("1", (4001, 4000)).save(path)
        assert path.stat().st_size < 20 * 1024 * 1024
        expected = "16 megapixel"
    elif problem == "truncated_png":
        path.write_bytes(path.read_bytes()[:-12])
        expected = "unreadable or invalid"
    else:
        outside = tmp_path / "outside.png"
        Image.new("RGB", (2, 2)).save(outside)
        path.unlink()
        path.symlink_to(outside)
        expected = "unsafe"

    def never(*args: Any, **kwargs: Any) -> str:
        pytest.fail("provider must not receive an invalid source")

    with pytest.raises(JobPayloadError, match=expected):
        run_video_prompt(job, never, SETTINGS, media_dir=tmp_path)
