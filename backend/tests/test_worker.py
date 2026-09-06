"""Worker tests: the queue runner drives the 1.3 primitives (spec-1.4).

Deterministic — the provider is injected, never a live LLM. Covers the
I/O matrix rows RUN_TEXT_JOB, RUN_NO_JOB, RUN_BAD_PROMPT, RUN_BUDGET_ZERO,
RUN_BUDGET_GUARD, RUN_HTTP_ERROR, RUN_CONNECTION_ERROR, RUN_IMAGE_JOB,
RESULT_PERSISTED + the listener emissions.
"""

import asyncio
import json
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.settings import ImageSettings, LLMSettings
from app.pipeline.budget import BudgetExceededError, CallBudget
from app.pipeline.worker import (
    JobPayloadError,
    run_next_job,
    worker_loop,
)
from app.providers.llm import ProviderError
from app.store import (
    app_db_url,
    cancel_job,
    claim_next_job,
    create_campaign,
    enqueue_job,
    init_db,
    job_status,
    models,
    set_change_listener,
)


def _owner_id() -> str:
    """One owner account per scratch DB for campaign creation (spec-1.6)."""
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-worker-{new_id()}@example.com", "password123").id


SETTINGS = LLMSettings(endpoint="http://test/v1", model="test-model")


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'worker.db'}")
    try:
        yield create_campaign(
            _owner_id(),
            title="Worker Test World",
            description="",
            theme="High Fantasy",
            custom_lore="",
        ).id
    finally:
        init_db(previous)


def _enqueue_text(world: str, prompt: str = "build the bar", **overrides: Any) -> str:
    return enqueue_job(world, "text", {"prompt": prompt}, **overrides).id


def _ok_provider(prompt: str, settings: LLMSettings) -> str:
    assert settings is SETTINGS
    return f"generated: {prompt}"


def test_run_text_job_completes_with_result(world: str) -> None:
    """RUN_TEXT_JOB: claim -> provider called -> complete_job(result) with
    the generated text; the completion is persisted for REST reads."""
    job_id = _enqueue_text(world)
    processed = run_next_job(provider=_ok_provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result == {"text": "generated: build the bar"}
    assert job.finished_at is not None


def test_run_no_job_returns_none(world: str) -> None:
    """RUN_NO_JOB: an empty queue yields None, no claim, no provider call."""
    called: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return "x"

    assert run_next_job(provider=provider, settings=SETTINGS) is None
    assert called == []


def test_run_bad_prompt_fails_job(world: str) -> None:
    """RUN_BAD_PROMPT: a text job missing 'prompt' fails with a clear error;
    the queue keeps flowing (job_failed emitted)."""
    job_id = enqueue_job(world, "text", {"not_prompt": 1}).id
    processed = run_next_job(provider=_ok_provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "prompt" in (job.error or "")


def test_run_budget_zero_fails_before_llm(world: str) -> None:
    """RUN_BUDGET_ZERO: max_llm_calls=0 fails the job, LLM never called."""
    job_id = _enqueue_text(world, max_llm_calls=0)
    called: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return "x"

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "budget" in (job.error or "").lower()
    assert called == []


def test_call_budget_guard_refuses_at_budget() -> None:
    """RUN_BUDGET_GUARD: the per-call primitive refuses before any HTTP
    request once the counter is at the budget (AR21)."""
    job = models.Job(id="J" * 26, max_llm_calls=1)
    budget = CallBudget(job)
    calls: list[str] = []

    def provider_call() -> str:
        calls.append("called")
        return "ok"

    assert budget.call(provider_call) == "ok"
    with pytest.raises(BudgetExceededError):
        budget.call(provider_call)
    assert calls == ["called"]  # the second call never reached the provider


def test_run_http_error_fails_job(world: str) -> None:
    """RUN_HTTP_ERROR: a non-2xx provider response fails the job with the
    HTTP status surfaced in the error."""

    def provider(prompt: str, settings: LLMSettings) -> str:
        raise ProviderError("http", status_code=502)

    job_id = _enqueue_text(world)
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "502" in (job.error or "")


def test_run_connection_error_fails_job(world: str) -> None:
    """RUN_CONNECTION_ERROR: an unreachable endpoint fails the job with a
    connection error (no retries in 1.4)."""

    def provider(prompt: str, settings: LLMSettings) -> str:
        raise ProviderError("connection")

    job_id = _enqueue_text(world)
    run_next_job(provider=provider, settings=SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "connection" in (job.error or "")


def test_run_image_job_runs_portrait_runner(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec-4.1 image dispatch: a valid image job runs the media runner —
    entity re-read, prompt from the committed appearance, provider call,
    file under media_dir/{campaign}/{entity}/, manifest row, completed
    job with {entity_id, filename} (HAPPY_PATH)."""
    from app.core import ids
    from app.store import commit_subgraph, list_media, session_scope, world_entities

    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(
                kind="character",
                name="Mira Vane",
                data={"appearance": {"face": "sharp features", "body": "lean"}},
                id=entity_id,
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    seen_prompts: list[str] = []

    def image_provider(prompt: str, settings: ImageSettings) -> bytes:
        seen_prompts.append(prompt)
        return b"\x89PNG\r\n\x1a\n" + b"portrait-payload"

    job_id = enqueue_job(world, "image", {"entity_id": entity_id}).id
    processed = run_next_job(
        provider=_ok_provider, settings=SETTINGS, image_provider=image_provider
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_id"] == entity_id
    filename = str(job.result["filename"])
    assert filename.endswith(".png")
    with session_scope() as session:
        entities = {e.id: e for e in world_entities(session, world)}
    rows = list_media(world)
    assert entities[entity_id].name == "Mira Vane"
    assert len(rows) == 1 and rows[0].entity_id == entity_id and rows[0].filename == filename
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"portrait-payload"
    assert (tmp_path / "media" / world / entity_id / filename).read_bytes() == image_bytes
    # The prompt is the committed appearance projection (never free text).
    assert seen_prompts == ["face: sharp features\nbody: lean"]


def test_run_video_job_runs_reveal_runner(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec-4.2 video dispatch: a valid video job runs the reveal runner —
    atomic .mp4 file under media_dir/{campaign}/{entity}/, manifest row
    kind='video', completed job with {entity_id, filename} (HAPPY_PATH).
    The prompt is the shared ``bbeg_video_prompt`` projection."""
    from app.core import ids
    from app.core.settings import VideoSettings
    from app.media.service import bbeg_video_prompt
    from app.store import commit_subgraph, list_media, session_scope, world_entities

    boss_data = {
        "name": "Vashka the Unmaker",
        "role": "BBEG",
        "appearance": {"face": "a mask of fused iron", "body": "towering"},
        "boss": {"lair_actions": "the walls breathe", "immunities": "fire"},
    }
    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(kind="character", name="Vashka", data=boss_data, id=entity_id),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    seen_prompts: list[str] = []
    VIDEO_SETTINGS = VideoSettings(endpoint="http://video.test/v1", model="vid-model")

    def video_provider(prompt: str, settings: VideoSettings) -> bytes:
        seen_prompts.append(prompt)
        assert settings is VIDEO_SETTINGS
        return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00reveal-payload"

    job_id = enqueue_job(world, "video", {"entity_id": entity_id}).id
    processed = run_next_job(
        provider=_ok_provider,
        settings=SETTINGS,
        video_provider=video_provider,
        video_settings=VIDEO_SETTINGS,
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result is not None and job.result["entity_id"] == entity_id
    filename = str(job.result["filename"])
    assert filename.endswith(".mp4")
    with session_scope() as session:
        entities = {e.id: e for e in world_entities(session, world)}
    rows = list_media(world)
    assert entities[entity_id].name == "Vashka"
    assert len(rows) == 1 and rows[0].entity_id == entity_id
    assert rows[0].kind == "video" and rows[0].filename == filename
    mp4_bytes = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00reveal-payload"
    assert (tmp_path / "media" / world / entity_id / filename).read_bytes() == mp4_bytes
    # The prompt is the committed AR24 projection (never free text).
    assert seen_prompts == [bbeg_video_prompt(boss_data)]


def test_run_video_job_non_mp4_fails_cleanly(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NON_MP4 through the WORKER: an injected provider returning
    non-ISO-BMFF bytes fails the job with a user-facing message, and no
    file/row lands."""
    from app.core import ids
    from app.core.settings import VideoSettings
    from app.store import commit_subgraph, list_media

    boss_data = {
        "name": "Vashka the Unmaker",
        "role": "BBEG",
        "appearance": {"face": "a mask of fused iron"},
        "boss": {"lair_actions": "the walls breathe"},
    }
    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(kind="character", name="Vashka", data=boss_data, id=entity_id),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    VIDEO_SETTINGS = VideoSettings(endpoint="http://video.test/v1", model="vid-model")

    def bad_video_provider(prompt: str, settings: VideoSettings) -> bytes:
        assert "lair_actions" in prompt
        return b"<html>not a video</html>"

    job_id = enqueue_job(world, "video", {"entity_id": entity_id}).id
    processed = run_next_job(
        provider=_ok_provider,
        settings=SETTINGS,
        video_provider=bad_video_provider,
        video_settings=VIDEO_SETTINGS,
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "non-mp4" in (job.error or "")
    assert list_media(world) == []
    assert not (tmp_path / "media" / world / entity_id).exists()


def test_run_image_job_provider_failure_fails_job_cleanly(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PROVIDER_FAIL through the WORKER: an image job whose injected
    provider raises fails the job with the image-flavored error, and no
    file/row lands. ``run_next_job`` resolves the production-default
    media_dir via env, covering the default-settings resolution path."""
    from app.core import ids
    from app.store import commit_subgraph, list_media

    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(
                kind="character",
                name="Mira Vane",
                data={"appearance": {"face": "sharp features"}},
                id=entity_id,
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))

    def failing_image_provider(prompt: str, settings: ImageSettings) -> bytes:
        assert "face" in prompt
        raise ProviderError("http", status_code=502)

    job_id = enqueue_job(world, "image", {"entity_id": entity_id}).id
    processed = run_next_job(
        provider=_ok_provider,
        settings=SETTINGS,
        image_provider=failing_image_provider,
    )
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "image generation failed" in (job.error or "")
    assert "502" in (job.error or "")
    assert "llm call failed" not in (job.error or "")  # the image dialect, not the LLM's
    assert list_media(world) == []
    assert not (tmp_path / "media" / world / entity_id).exists()


def test_run_image_job_comfyui_backend_dispatch(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec-4.4 comfyui dispatch: with image_backend = "comfyui", an
    image job routes through the comfyui provider under the same
    run_next_job seam — the shared portrait runner writes the file +
    manifest row, the comfyui prompt rides the committed appearance,
    and the OpenAI provider is NEVER called (no fallback chain)."""
    from app.core import ids
    from app.core.config import reset_runtime_config
    from app.core.settings import ComfyUIImageSettings
    from app.store import commit_subgraph, list_media, session_scope, world_entities

    monkeypatch.setenv("MYTHOSCIRCLE_IMAGE_BACKEND", "comfyui")
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    reset_runtime_config()
    try:
        entity_id, anchor_id = ids.new_id(), ids.new_id()
        commit_subgraph(
            world,
            [
                models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
                models.EntityInput(
                    kind="character",
                    name="Mira Vane",
                    data={"appearance": {"face": "sharp features", "body": "lean"}},
                    id=entity_id,
                ),
            ],
            [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
        )
        COMFYUI_SETTINGS = ComfyUIImageSettings(endpoint="http://comfy.test:7896")
        seen_prompts: list[str] = []

        def comfyui_provider(prompt: str, settings: ComfyUIImageSettings) -> bytes:
            assert settings is COMFYUI_SETTINGS
            seen_prompts.append(prompt)
            return b"\x89PNG\r\n\x1a\ncomfyui-payload"

        def openai_provider(prompt: str, settings: ImageSettings) -> bytes:
            raise AssertionError("the OpenAI image provider must not run under comfyui")

        job_id = enqueue_job(world, "image", {"entity_id": entity_id}).id
        processed = run_next_job(
            provider=_ok_provider,
            settings=SETTINGS,
            image_provider=openai_provider,  # poisoned: proves the branch, not a fallback
            comfyui_image_provider=comfyui_provider,
            comfyui_image_settings=COMFYUI_SETTINGS,
        )
        assert processed == job_id
        job, _position = job_status(job_id)
        assert job.state == "succeeded"
        assert job.result is not None and job.result["entity_id"] == entity_id
        filename = str(job.result["filename"])
        assert filename.endswith(".png")
        with session_scope() as session:
            entities = {e.id: e for e in world_entities(session, world)}
        rows = list_media(world)
        assert entities[entity_id].name == "Mira Vane"
        assert len(rows) == 1 and rows[0].entity_id == entity_id and rows[0].filename == filename
        comfy_bytes = b"\x89PNG\r\n\x1a\ncomfyui-payload"
        assert (tmp_path / "media" / world / entity_id / filename).read_bytes() == comfy_bytes
        # The prompt is the committed appearance projection — the same
        # run_portrait contract as the OpenAI path.
        assert seen_prompts == ["face: sharp features\nbody: lean"]
    finally:
        reset_runtime_config()


def test_comfyui_real_provider_end_to_end_with_mock_transport(
    world: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production comfyui path: REAL ``comfyui_image_generation``
    (via a bound MockTransport, workflow file on disk) through
    ``run_next_job`` with image_backend=comfyui. This is the seam that
    proves the keyword-only ``settings=`` call lands — the injected
    dispatch fakes accept positional settings, so a regression to a
    positional call would pass every fake test and TypeError every real
    job (the test_real_provider_end_to_end_with_mock_transport mirror,
    review round 1)."""
    import json as _json

    from app.core import ids
    from app.core.config import reset_runtime_config
    from app.core.settings import ComfyUIImageSettings
    from app.providers.comfyui import comfyui_image_generation
    from app.store import commit_subgraph, list_media

    monkeypatch.setenv("MYTHOSCIRCLE_IMAGE_BACKEND", "comfyui")
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(tmp_path / "media"))
    reset_runtime_config()
    try:
        entity_id, anchor_id = ids.new_id(), ids.new_id()
        commit_subgraph(
            world,
            [
                models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
                models.EntityInput(
                    kind="character",
                    name="Mira Vane",
                    data={"appearance": {"face": "sharp features"}},
                    id=entity_id,
                ),
            ],
            [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
        )
        workflow_path = tmp_path / "krea2.json"
        workflow_path.write_text(
            _json.dumps(
                {
                    "30:19": {"class_type": "CLIPTextEncode", "inputs": {"value": "stale"}},
                    "29": {"class_type": "SaveImage", "inputs": {}},
                }
            )
        )
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00" + b"IEND\xaeB`\x82"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/prompt":
                body = _json.loads(request.read().decode())
                # The committed appearance projection rides the prompt
                # node — proving the real provider ran end-to-end.
                assert body["prompt"]["30:19"]["inputs"]["value"] == "face: sharp features"
                return httpx.Response(200, json={"prompt_id": "p-1"})
            if request.url.path == "/history/p-1":
                return httpx.Response(
                    200,
                    json={
                        "p-1": {
                            "outputs": {
                                "29": {
                                    "images": [
                                        {
                                            "filename": "k.png",
                                            "subfolder": "",
                                            "type": "output",
                                        }
                                    ]
                                }
                            }
                        }
                    },
                )
            assert request.url.path == "/view"
            return httpx.Response(200, content=png_bytes)

        provider = partial(comfyui_image_generation, transport=httpx.MockTransport(handler))
        job_id = enqueue_job(world, "image", {"entity_id": entity_id}).id
        processed = run_next_job(
            provider=_ok_provider,
            settings=SETTINGS,
            comfyui_image_provider=provider,
            comfyui_image_settings=ComfyUIImageSettings(
                endpoint="http://comfy.test:7896", workflow_path=str(workflow_path)
            ),
        )
        assert processed == job_id
        job, _position = job_status(job_id)
        assert job.state == "succeeded", job.error
        assert job.result is not None and job.result["entity_id"] == entity_id
        filename = str(job.result["filename"])
        assert filename.endswith(".png")
        rows = list_media(world)
        assert len(rows) == 1 and rows[0].entity_id == entity_id and rows[0].filename == filename
        assert (tmp_path / "media" / world / entity_id / filename).read_bytes() == png_bytes
    finally:
        reset_runtime_config()


def test_result_persisted_and_ws_shape_unchanged(world: str) -> None:
    """RESULT_PERSISTED: job.result is readable via job_status; the AD-17
    WS message carries no 'result' key (the shape is unchanged)."""
    events: list[tuple[str, str]] = []
    set_change_listener(lambda event, job, _p: events.append((event, job.id)))
    try:
        job_id = _enqueue_text(world)
        run_next_job(provider=_ok_provider, settings=SETTINGS)
        assert ("job_done", job_id) in events
        assert ("queue_changed", job_id) in events
        job, _position = job_status(job_id)
        assert job.result == {"text": "generated: build the bar"}
    finally:
        set_change_listener(None)  # never leak the listener into later tests


def test_unexpected_error_fails_job_not_crash(world: str) -> None:
    """A provider blow-up (unexpected exception) still fails the job — a
    claimed job must never wedge the queue (AD-3)."""

    def provider(prompt: str, settings: LLMSettings) -> str:
        raise RuntimeError("kaboom")

    job_id = _enqueue_text(world)
    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "failed"
    assert "kaboom" in (job.error or "")


def _build_in_wave1_json() -> str:
    """A valid wave-1 output: two core entities wired by typed edges."""
    return json.dumps(
        {
            "entities": [
                {
                    "ref": "E0",
                    "kind": "faction",
                    "name": "The Gilded Bar",
                    "text": "smoke and coin",
                },
                {
                    "ref": "E1",
                    "kind": "character",
                    "name": "Mira Vane",
                    "data": {
                        "goal": "tea house",
                        "stat_block": {
                            "identity": {
                                "role": "NPC",
                                "level": 5,
                                "race": "Human",
                                "class": "Fighter",
                                "alignment": "LG",
                            },
                            "attributes": {
                                "str": 14,
                                "dex": 12,
                                "con": 14,
                                "int": 10,
                                "wis": 10,
                                "cha": 8,
                            },
                            "combat": {"ac": 16, "hp": 44},
                            "skills": [{"name": "Athletics", "bonus": 5}],
                        },
                    },
                },
            ],
            "edges": [
                {"src": "E0", "dst": "E1", "type": "member_of", "counter": 1},
                {"src": "E1", "dst": "E0", "type": "debt", "counter": 3},
            ],
        }
    )


def test_build_in_job_succeeds_and_commits_world(world: str) -> None:
    """Story 2.3's runner: a valid build-in job completes and the core
    subgraph commits (one revision, entities + typed edges); the result
    carries the wave summary and the queue keeps flowing."""
    from app.store import session_scope, world_edges, world_entities

    build_id = enqueue_job(world, "build_in", {"places": ["Greymarch"], "notes": ""}).id

    def provider(prompt: str, settings: LLMSettings) -> str:
        assert settings is SETTINGS
        return _build_in_wave1_json()

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == build_id
    job, _position = job_status(build_id)
    assert job.state == "succeeded"
    assert job.progress == 0.5  # wave 2 skipped (notes blank)
    assert job.result is not None
    assert len(job.result["waves"]) == 1 and job.result["waves"][0]["wave"] == 1
    assert job.result["entity_count"] == 2 and job.result["edge_count"] == 2
    with session_scope() as session:
        entities, edges = world_entities(session, world), world_edges(session, world)
    assert {e.name for e in entities} == {"The Gilded Bar", "Mira Vane"}
    assert {e.type for e in edges} == {"member_of", "debt"}


def test_build_in_malformed_fails_not_wedges(world: str) -> None:
    """MALFORMED_OUTPUT at the worker level: bad LLM output fails the job
    loudly (naming the wave) and the FIFO keeps flowing."""
    build_id = enqueue_job(world, "build_in", {"places": ["Greymarch"], "notes": "a damp city"}).id

    def provider(prompt: str, settings: LLMSettings) -> str:
        return "not json at all"

    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == build_id
    job, _position = job_status(build_id)
    assert job.state == "failed"
    assert "wave 1" in (job.error or "")
    # The slot is freed: a subsequent text job processes normally.
    text_id = _enqueue_text(world)
    assert run_next_job(provider=_ok_provider, settings=SETTINGS) == text_id
    assert job_status(text_id)[0].state == "succeeded"


def test_worker_loop_drains_then_idles_and_stops(tmp_path: Path) -> None:
    """The loop claims jobs (via to_thread) and exits promptly on stop —
    run with a fresh event loop so no plugin is needed (deterministic)."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'loop.db'}")
    try:
        campaign = create_campaign(
            _owner_id(), title="Loop World", description="", theme="High Fantasy", custom_lore=""
        ).id
        job_id = enqueue_job(campaign, "text", {"prompt": "loop"}).id
        _ = job_id

        async def scenario() -> None:
            stop = asyncio.Event()
            task = asyncio.create_task(worker_loop(stop, provider=_ok_provider, settings=SETTINGS))
            # The loop should claim + complete the job, then idle.
            for _ in range(50):
                job, _position = job_status(job_id)
                if job.state == "succeeded":
                    break
                await asyncio.sleep(0.02)
            assert job_status(job_id)[0].state == "succeeded"
            stop.set()
            await asyncio.wait_for(task, timeout=2.0)

        asyncio.run(scenario())
    finally:
        init_db(previous)


def test_job_payload_error_is_value_error() -> None:
    with pytest.raises(JobPayloadError):
        raise JobPayloadError("missing prompt")


# ---------------------------------------------------------------------------
# Review-regression tests (2026-08-30 review round 1)
# ---------------------------------------------------------------------------


def test_real_provider_end_to_end_with_mock_transport(world: str) -> None:
    """The production default path: real ``chat_completion`` (via a bound
    MockTransport) through ``run_next_job``. This is the seam that would
    have caught the keyword-``settings`` TypeError — a positional call in
    ``_run_job`` previously failed every job (review round 1)."""
    from app.providers.llm import chat_completion

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        # The prompt rides as the user message's content (httpx compact
        # JSON: no space after the colon), not a 'prompt' key.
        assert '"content":"build the bar"' in body
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "generated end to end"}}]},
        )

    provider = partial(chat_completion, transport=httpx.MockTransport(handler))
    job_id = _enqueue_text(world)
    processed = run_next_job(provider=provider, settings=SETTINGS)
    assert processed == job_id
    job, _position = job_status(job_id)
    assert job.state == "succeeded"
    assert job.result == {"text": "generated end to end"}


def test_cancelled_mid_run_poll_is_noop(world: str) -> None:
    """The cancel-race poll in ``_run_job``: a claimed job cancelled while
    the worker was between claim and provider call is a no-op — the
    provider is never called, the job stays cancelled (review round 1).

    ``_run_job`` is exercised directly with the stale in-memory ``running``
    object; the store says ``cancelled`` (what the poll sees)."""
    from app.pipeline.worker import _run_job

    called: list[str] = []

    def provider(prompt: str, settings: LLMSettings) -> str:
        called.append(prompt)
        return "x"

    job_id = _enqueue_text(world)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id
    cancel_job(job_id)  # DB now: cancelled; the in-memory object still says running
    _run_job(claimed, provider, SETTINGS)
    job, _position = job_status(job_id)
    assert job.state == "cancelled"  # the poll returned without completing
    assert called == []  # the provider call was never made


def test_migration_adds_result_column_to_old_db(tmp_path: Path) -> None:
    """A database created before story 1.4 (no ``job.result``) gets the
    column on init — create_all never ALTERs, so the migration owns it
    (review round 1); idempotent on a second init."""
    import sqlite3

    from sqlalchemy import text

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE campaign (id VARCHAR(26) PRIMARY KEY, name VARCHAR(500),"
        " created_at VARCHAR(40))"
    )
    conn.execute(
        "CREATE TABLE job (id VARCHAR(26) PRIMARY KEY, campaign_id VARCHAR(26),"
        " kind VARCHAR(64), payload JSON, state VARCHAR(32), progress FLOAT,"
        " max_llm_calls INTEGER, max_media_calls INTEGER, error TEXT,"
        " created_at VARCHAR(40), started_at VARCHAR(40), finished_at VARCHAR(40))"
    )
    conn.commit()
    conn.close()

    previous = app_db_url()
    try:
        init_db(f"sqlite:///{db}")
        from app.store import get_engine

        with get_engine().connect() as connection:
            job_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(job)"))}
            campaign_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(campaign)"))
            }
        assert "result" in job_columns
        # The AR27 seed migration added the world-seed columns and dropped
        # the legacy empty `name` (review round 1 — a real pinned test).
        assert {"owner_id", "title", "description", "theme", "custom_lore"} <= campaign_columns
        assert "name" not in campaign_columns
        init_db(f"sqlite:///{db}")  # idempotent — no error on the second init
    finally:
        init_db(previous)
