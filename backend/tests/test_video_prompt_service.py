"""Video-prompt draft runner tests (spec-4.6): the ``video_prompt`` job
kind generates a DM-reviewable MiniMax-H3 I2VA reveal prompt from the
entity's committed AR24 data + the in-repo writing guide.

Mirrors test_video_service.py's runner discipline: payload contract ->
run-time entity re-read -> boss/name/appearance gates -> guide-loaded
LLM call -> non-blank check -> complete_job({entity_id, prompt}). The
LLM is injected, never a live gemma — deterministic.

Covers the I/O matrix rows DRAFT_OK, DRAFT_LLM_DOWN, DRAFT_BLANK_RESULT
(the enqueue-side DRAFT_NO_BOSS / DRAFT_NO_SOURCE rows live in
test_jobs.py; the run-time mirrors are here), plus the render-side
rows RENDER_WITH_PROMPT, RENDER_LEGACY, RENDER_BLANK_PROMPT.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.core import ids
from app.core.settings import LLMSettings, VideoSettings
from app.media.service import (
    VIDEO_PROMPT_GUIDE_PATH,
    bbeg_video_prompt,
    run_video,
    run_video_prompt,
)
from app.pipeline.budget import BudgetExceededError
from app.pipeline.worker import JobPayloadError
from app.providers.llm import ProviderError
from app.store import (
    app_db_url,
    claim_next_job,
    commit_subgraph,
    create_campaign,
    init_db,
    job_status,
    list_media,
    models,
    session_scope,
)

SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")

MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00reveal-payload"

I2VA_DRAFT = (
    "For the target video, at 0.00 seconds into the target video, "
    "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
    "integrated_multimodal_description: [Shot 1] The camera holds on the "
    "masked figure as the iron face tilts toward the lens.\n"
    "overall_soundscape: N/A\n"
    "non_diegetic_music: low brass swell\n"
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
# DRAFT_OK: guide + data -> LLM -> completed job with {entity_id, prompt}
# ---------------------------------------------------------------------------


def test_run_video_prompt_happy_path(world: str) -> None:
    """DRAFT_OK: the draft is the LLM's I2VA-structured output, stored as
    the job result ``{entity_id, prompt}`` — never a file, never a media
    row (a prompt is not media, AD-1). The instruction embeds the guide
    verbatim + the committed appearance + boss projections + the I2VA
    compliance demand."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_draft_job(world, entity_id)
    llm = _llm()

    run_video_prompt(job, llm, SETTINGS)

    state, _position = job_status(job.id)
    assert state.state == "succeeded"
    assert state.result is not None
    assert state.result["entity_id"] == entity_id
    assert state.result["prompt"] == I2VA_DRAFT
    assert list_media(world) == []
    # The instruction embeds the guide verbatim and the data projections
    # the draft must anchor on.
    instruction = llm.calls[0]
    assert VIDEO_PROMPT_WRITING_GUIDE in instruction
    assert "face: a mask of fused iron" in instruction
    assert "lair_actions: the walls breathe" in instruction
    assert "integrated_multimodal_description" in instruction
    assert "overall_soundscape" in instruction
    assert "non_diegetic_music" in instruction


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
# DRAFT_LLM_DOWN / DRAFT_BLANK_RESULT / budget
# ---------------------------------------------------------------------------


def test_run_video_prompt_llm_down_fails_cleanly(world: str) -> None:
    """DRAFT_LLM_DOWN: a provider refusal/timeout surfaces the draft's
    own error vocabulary ('video prompt generation failed: …') — never
    the LLM dialect — and no draft is left behind."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_draft_job(world, entity_id)

    def down(prompt: str, settings: LLMSettings) -> str:
        raise ProviderError(kind="http", status_code=502)

    with pytest.raises(JobPayloadError, match=r"video prompt generation failed: .*502"):
        run_video_prompt(job, down, SETTINGS)


def test_run_video_prompt_connection_error_fails_cleanly(world: str) -> None:
    """A connection refusal is the same clean draft failure."""
    entity_id = _commit_with_data(world, BOSS_DATA)
    job = _claim_draft_job(world, entity_id)

    def down(prompt: str, settings: LLMSettings) -> str:
        raise ProviderError(kind="connection")

    with pytest.raises(JobPayloadError, match=r"video prompt generation failed"):
        run_video_prompt(job, down, SETTINGS)


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
    dm_prompt = "DM's own cinematic reveal: no bbeg framing at all."
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


#: The guide's own expected opening — the point of the full-embed
#: (Ask-First 2) is that the I2VA compliance demand and the guide text
#: ride together in the instruction.
VIDEO_PROMPT_WRITING_GUIDE = VIDEO_PROMPT_GUIDE_PATH.read_text(encoding="utf-8")
