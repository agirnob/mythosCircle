"""Persistent generation queue (AD-3, AR11, AR28): enqueue/claim/complete/
fail/progress/cancel/status/list + the change-listener contract.

Covers the I/O matrix rows of spec-1.3 (ENQUEUE_FIRST … CROSS_CAMPAIGN,
CLAIM_CONCURRENT with a threading barrier, RESTART persistence) with
deterministic fixtures — world-fixture style, fixed inputs, no wall-clock
dependence in assertions.
"""

import re
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text

from app.core import ids
from app.core.settings import (
    MAX_LLM_CALLS_PER_JOB,
    MAX_MEDIA_CALLS_PER_JOB,
    MAX_PENDING_PER_CAMPAIGN,
)
from app.store import (
    DuplicateJobError,
    InvalidJobInputError,
    JobNotFoundError,
    JobStateConflictError,
    QueueFullError,
    UnknownCampaignError,
    UnknownEntityError,
    app_db_url,
    cancel_job,
    claim_next_job,
    commit_subgraph,
    complete_job,
    create_campaign,
    enqueue_job,
    fail_job,
    init_db,
    job_status,
    list_jobs,
    models,
    recover_stale_running,
    report_progress,
    session_scope,
    set_change_listener,
)


def _owner_id() -> str:
    """One owner account per scratch DB for campaign creation (spec-1.6)."""
    from app.core.ids import new_id
    from app.store import register_account

    return register_account(f"owner-jobs-{new_id()}@example.com", "password123").id


ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

#: A ULID that can never have been minted (all zero).
MISSING_ID = "0" * 26


@pytest.fixture()
def world(tmp_path: Path) -> Iterator[str]:
    """A fresh scratch database and one empty campaign; yields its id."""
    previous = app_db_url()
    init_db(f"sqlite:///{tmp_path / 'world.db'}")
    try:
        yield create_campaign(
            _owner_id(), title="Test World", description="", theme="High Fantasy", custom_lore=""
        ).id
    finally:
        init_db(previous)


@pytest.fixture()
def listener() -> Iterator[list[tuple[str, str]]]:
    """A recording change listener with cleanup; yields (event, job_id,
    queue_position) tuples."""
    collected: list[tuple[str, str]] = []
    set_change_listener(lambda event, job, _position: collected.append((event, job.id)))
    try:
        yield collected
    finally:
        set_change_listener(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enqueued(campaign_id: str, kind: str = "text", **payload: Any) -> str:
    """Enqueue one job; returns its id."""
    return enqueue_job(campaign_id, kind, {"seed": kind, **payload}).id


def _claimed(campaign_id: str) -> str:
    """Enqueue one job and claim it; returns the running job's id."""
    job_id = _enqueued(campaign_id)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id
    return job_id


def _count_jobs() -> int:
    """Total job rows in the current store."""
    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(models.Job)) or 0


# ---------------------------------------------------------------------------
# ENQUEUE_FIRST
# ---------------------------------------------------------------------------


def test_enqueue_first_job_queued_position_one(world: str) -> None:
    """A fresh queue: ULID minted, state queued, position 1, budgets defaulted."""
    job = enqueue_job(world, "text", {"request": "build the bar"})
    assert ULID_RE.fullmatch(job.id)
    assert job.campaign_id == world
    assert job.kind == "text"
    assert job.state == "queued"
    assert job.payload == {"request": "build the bar"}
    assert job.progress == 0.0
    assert job.max_llm_calls == 64 and job.max_media_calls == 8
    assert job.error is None
    assert ISO_Z_RE.fullmatch(job.created_at)
    assert job.started_at is None and job.finished_at is None
    _job, position = job_status(job.id)
    assert position == 1


def test_enqueue_budgets_come_from_env(world: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-job call budgets default from env, validated >= 0 (AR21 stores them).

    The image job rides the spec-4.1 payload contract: a committed entity
    with a non-blank AR24 appearance (the portrait prompt source, FR12).
    """
    monkeypatch.setenv(MAX_LLM_CALLS_PER_JOB, "5")
    monkeypatch.setenv(MAX_MEDIA_CALLS_PER_JOB, "2")
    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="Anchor", id=anchor_id),
            models.EntityInput(
                kind="character", name="Mira", data={"appearance": "sharp"}, id=entity_id
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    job = enqueue_job(world, "image", {"entity_id": entity_id})
    assert job.max_llm_calls == 5 and job.max_media_calls == 2


# ---------------------------------------------------------------------------
# Spec-4.1 image payload contract (the portrait enqueue gate)
# ---------------------------------------------------------------------------


def test_enqueue_image_requires_exact_entity_payload(world: str) -> None:
    """IMAGE_BAD_PAYLOAD: anything but exactly ``{"entity_id": <ULID>}``
    is a 422, zero rows written."""
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "image", {"prompt": "a tavern at dusk"})
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "image", {"entity_id": "not-a-ulid"})
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "image", {"entity_id": "0" * 26, "extra": 1})
    assert _count_jobs() == 0


def test_enqueue_image_unknown_entity_is_404(world: str) -> None:
    """ENTITY_MISSING at enqueue: a fabricated entity id is a
    ``UnknownEntityError`` (404), zero rows written."""
    with pytest.raises(UnknownEntityError):
        enqueue_job(world, "image", {"entity_id": MISSING_ID})
    assert _count_jobs() == 0


def test_enqueue_image_foreign_entity_is_404(world: str) -> None:
    """An entity of ANOTHER campaign is the same indistinguishable 404
    (AD-9 — no oracle), zero rows written."""
    other = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        other,
        [
            models.EntityInput(kind="place", name="Anchor", id=anchor_id),
            models.EntityInput(
                kind="character", name="Stranger", data={"appearance": "x"}, id=entity_id
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    with pytest.raises(UnknownEntityError):
        enqueue_job(world, "image", {"entity_id": entity_id})
    assert _count_jobs() == 0


@pytest.mark.parametrize(
    "appearance",
    [
        "",
        "   \n\t ",
        {},
        {"clothing": "  "},
        {"face": "", "unknown_key": "not a known key"},
    ],
    ids=["blank-string", "whitespace-string", "empty-dict", "blank-known-key", "only-unknown-keys"],
)
def test_enqueue_image_blank_appearance_is_422(world: str, appearance: object) -> None:
    """NO_APPEARANCE: a forced enqueue for an entity without a non-blank
    AR24 appearance is a 422 (the runner's fail condition mirrored at the
    enqueue gate), zero rows written."""
    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="Anchor", id=anchor_id),
            models.EntityInput(
                kind="character", name="Faceless", data={"appearance": appearance}, id=entity_id
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "image", {"entity_id": entity_id})
    assert _count_jobs() == 0


def test_enqueue_image_dict_appearance_accepted(world: str) -> None:
    """The dict shape with at least one non-blank KNOWN key enqueues (the
    prompt projection's gate — unknown keys never rescue a blank dict)."""
    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="Anchor", id=anchor_id),
            models.EntityInput(
                kind="character",
                name="Inkwell",
                data={"appearance": {"face": "hollow eyes", "unknown_key": "x"}},
                id=entity_id,
            ),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    job = enqueue_job(world, "image", {"entity_id": entity_id})
    assert job.kind == "image" and job.state == "queued"


# ---------------------------------------------------------------------------
# Spec-4.2 video payload contract (the reveal-video enqueue gate)
# ---------------------------------------------------------------------------


def _commit_boss(world: str, role: str = "BBEG", data: dict[str, Any] | None = None) -> str:
    """One committed boss-tier character with a usable reveal prompt
    (non-blank appearance + boss section) unless ``data`` overrides."""
    if data is None:
        data = {
            "name": "Vashka the Unmaker",
            "role": role,
            "appearance": {"face": "a mask of fused iron"},
            "boss": {"lair_actions": "the walls breathe"},
        }
    entity_id, anchor_id = ids.new_id(), ids.new_id()
    commit_subgraph(
        world,
        [
            models.EntityInput(kind="place", name="The Anchor", id=anchor_id),
            models.EntityInput(kind="character", name="Vashka", data=data, id=entity_id),
        ],
        [models.EdgeInput(src=anchor_id, dst=entity_id, type="located_in", counter=1)],
    )
    return entity_id


def test_enqueue_video_requires_exact_entity_payload(world: str) -> None:
    """VIDEO_BAD_PAYLOAD: anything but exactly ``{"entity_id": <ULID>}``
    is a 422, zero rows written."""
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "video", {"prompt": "a cinematic reveal"})
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "video", {"entity_id": "not-a-ulid"})
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "video", {"entity_id": "0" * 26, "extra": 1})
    assert _count_jobs() == 0


def test_enqueue_video_unknown_entity_is_404(world: str) -> None:
    """ENTITY_MISSING at enqueue: a fabricated entity id is an
    ``UnknownEntityError`` (404), zero rows written."""
    with pytest.raises(UnknownEntityError):
        enqueue_job(world, "video", {"entity_id": MISSING_ID})
    assert _count_jobs() == 0


def test_enqueue_video_foreign_entity_is_404(world: str) -> None:
    """An entity of ANOTHER campaign is the same indistinguishable 404
    (AD-9 — no oracle), zero rows written."""
    other = create_campaign(
        _owner_id(), title="Other", description="", theme="High Fantasy", custom_lore=""
    ).id
    entity_id = _commit_boss(other)
    with pytest.raises(UnknownEntityError):
        enqueue_job(world, "video", {"entity_id": entity_id})
    assert _count_jobs() == 0


def test_enqueue_video_not_boss_is_422(world: str) -> None:
    """NOT_BOSS: the reveal video is a boss-tier surface — a committed
    entity whose role is not BBEG/Monster is a 422, zero rows written
    (even with a perfectly usable appearance + boss-shaped record)."""
    data = {
        "name": "Mira Vane",
        "role": "NPC",
        "appearance": {"face": "sharp features"},
        "boss": {"lair_actions": "the walls breathe"},
    }
    entity_id = _commit_boss(world, data=data)
    with pytest.raises(InvalidJobInputError, match="not boss-tier"):
        enqueue_job(world, "video", {"entity_id": entity_id})
    assert _count_jobs() == 0


@pytest.mark.parametrize(
    "data",
    [
        {"name": "V", "role": "BBEG"},  # no appearance, no boss
        {"name": "V", "role": "BBEG", "appearance": {"face": "iron"}},  # no boss
        {  # boss present but blank
            "name": "V",
            "role": "Monster",
            "appearance": {"face": "iron"},
            "boss": {"lair_actions": "   "},
        },
        {  # boss present but only unknown keys (never prompt sources)
            "name": "V",
            "role": "Monster",
            "appearance": {"face": "iron"},
            "boss": {"custom_bit": "free text"},
        },
        {  # blank appearance
            "name": "V",
            "role": "Monster",
            "appearance": "  ",
            "boss": {"lair_actions": "walls breathe"},
        },
    ],
)
def test_enqueue_video_no_usable_prompt_is_422(world: str, data: dict[str, Any]) -> None:
    """NO_VIDEO_PROMPT: a boss-tier entity without a non-blank appearance
    + boss section is a 422 (the runner's fail condition mirrored at the
    enqueue gate via the SAME ``bbeg_video_prompt`` builder), zero rows."""
    entity_id = _commit_boss(world, data=data)
    with pytest.raises(InvalidJobInputError, match="no usable reveal prompt"):
        enqueue_job(world, "video", {"entity_id": entity_id})
    assert _count_jobs() == 0


def test_enqueue_video_boss_entity_accepted(world: str) -> None:
    """A committed BBEG with a usable prompt enqueues (kind video,
    state queued) — the FIFO row the worker's video dispatch runs."""
    entity_id = _commit_boss(world)
    job = enqueue_job(world, "video", {"entity_id": entity_id})
    assert job.kind == "video" and job.state == "queued"


# ---------------------------------------------------------------------------
# ENQUEUE_FIFO
# ---------------------------------------------------------------------------


def test_enqueue_fifo_positions_and_claim_order(world: str) -> None:
    """Three enqueues -> positions 1,2,3; claim yields the earliest rowid first."""
    first = _enqueued(world)
    second = _enqueued(world)
    third = _enqueued(world)
    assert job_status(first)[1] == 1
    assert job_status(second)[1] == 2
    assert job_status(third)[1] == 3
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == first
    assert claimed.state == "running"
    assert claimed.started_at is not None
    assert ISO_Z_RE.fullmatch(claimed.started_at)


# ---------------------------------------------------------------------------
# ENQUEUE_CAP
# ---------------------------------------------------------------------------


def test_enqueue_cap_rejected_zero_rows(world: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pending (queued + running) at the cap -> QueueFullError, nothing written."""
    monkeypatch.setenv(MAX_PENDING_PER_CAMPAIGN, "2")
    first = _enqueued(world)
    second = _enqueued(world)
    assert job_status(first)[1] == 1 and job_status(second)[1] == 2
    with pytest.raises(QueueFullError) as excinfo:
        enqueue_job(world, "text", {"x": 1})
    assert excinfo.value.max_pending == 2
    assert _count_jobs() == 2
    # A running job also occupies the cap budget (pending = queued + running).
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == first
    with pytest.raises(QueueFullError):
        enqueue_job(world, "text", {"x": 2})
    assert _count_jobs() == 2
    # The cap is per campaign: a second campaign is unaffected.
    other = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    assert enqueue_job(other, "text", {"x": 1}).state == "queued"


# ---------------------------------------------------------------------------
# ENQUEUE_DUP_ID
# ---------------------------------------------------------------------------


def test_enqueue_dup_id_rejected(world: str) -> None:
    """A caller-supplied job_id that already exists -> DuplicateJobError (409);
    rejected, never double-enqueued (idempotent by job-id)."""
    job_id = ids.new_id()
    enqueue_job(world, "text", {"x": 1}, job_id=job_id)
    with pytest.raises(DuplicateJobError):
        enqueue_job(world, "text", {"x": 2}, job_id=job_id)
    assert _count_jobs() == 1
    _job, _ = job_status(job_id)
    assert _job.payload == {"x": 1}  # the first submission wins


# ---------------------------------------------------------------------------
# ENQUEUE_BAD_INPUT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "payload", "job_id", "max_llm_calls", "max_media_calls"),
    [
        pytest.param("simulate", {"x": 1}, None, None, None, id="kind-outside-closed-set"),
        pytest.param("text", {"x": 1}, "not-a-ulid", None, None, id="non-ulid-job-id"),
        pytest.param("text", {"x": 1}, None, -1, None, id="negative-llm-budget"),
        pytest.param("text", {"x": 1}, None, None, -1, id="negative-media-budget"),
        pytest.param("text", ["not", "a", "dict"], None, None, None, id="payload-not-a-dict"),
    ],
)
def test_enqueue_bad_input_rejected_zero_rows(
    world: str,
    kind: Any,
    payload: Any,
    job_id: Any,
    max_llm_calls: Any,
    max_media_calls: Any,
) -> None:
    """Malformed input -> InvalidJobInputError (422), zero rows written."""
    with pytest.raises(InvalidJobInputError):
        enqueue_job(
            world,
            kind,
            payload,
            job_id=job_id,
            max_llm_calls=max_llm_calls,
            max_media_calls=max_media_calls,
        )
    assert _count_jobs() == 0


def test_enqueue_unknown_campaign_rejected(world: str) -> None:
    """Enqueue against a campaign that does not exist -> UnknownCampaignError."""
    with pytest.raises(UnknownCampaignError):
        enqueue_job(MISSING_ID, "text", {"x": 1})
    assert _count_jobs() == 0


# ---------------------------------------------------------------------------
# BUILD_IN (spec-2.1)
# ---------------------------------------------------------------------------


def test_enqueue_build_in_valid_position_one(world: str) -> None:
    """A valid build-in submission enqueues with kind build_in, position 1."""
    job = enqueue_job(
        world,
        "build_in",
        {"places": ["Greymarch"], "factions": [], "key_figures": ["Mira"], "notes": ""},
    )
    assert job.kind == "build_in"
    assert job.state == "queued"
    assert job_status(job.id)[1] == 1
    # Whitespace-only entries are trimmed away; other content still counts.
    blank_trimmed = enqueue_job(world, "build_in", {"places": ["   "], "notes": "only notes count"})
    assert blank_trimmed.state == "queued"


def test_enqueue_build_in_notes_at_cap_accepted(world: str) -> None:
    """notes trimmed to exactly the 2000-char cap is valid; the cap is
    inclusive (spec-2.1 notes contract)."""
    job = enqueue_job(world, "build_in", {"notes": "x" * 2000})
    assert job.state == "queued"
    assert job_status(job.id)[1] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"places": [], "factions": [], "key_figures": [], "notes": ""},  # all blank
        {"places": ["   "], "key_figures": [], "notes": ""},  # whitespace-only entries
        {"places": ["x" * 2001]},  # entry too long
        {"places": [1, 2]},  # non-string entry
        {"places": "not a list"},  # section not a list
        {"notes": 7},  # notes not a string
        {"notes": None},  # notes key present but null
        {"notes": "x" * 2001},  # notes over the 2000-char cap
        {"factions": list(range(101))},  # too many entries
    ],
)
def test_enqueue_build_in_bad_payload_rejected_zero_rows(
    world: str, payload: dict[str, object]
) -> None:
    """Malformed build-in payloads -> InvalidJobInputError, zero rows written."""
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "build_in", payload)
    assert _count_jobs() == 0


def test_enqueue_build_in_ignores_unknown_keys(world: str) -> None:
    """Unrecognized payload keys are not an error up front; the section shape
    caps validate (the runner in 2.3 owns the prompt contract)."""
    job = enqueue_job(world, "build_in", {"places": ["Greymarch"], "custom_meta": {"k": 1}})
    assert job.state == "queued"


# ---------------------------------------------------------------------------
# CLAIM_EXACTLY_ONE
# ---------------------------------------------------------------------------


def test_claim_exactly_one_job_runs(world: str) -> None:
    """Exactly one job runs at a time: a claim while running returns None (AD-3)."""
    first = _enqueued(world)
    second = _enqueued(world)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == first
    assert claimed.state == "running"
    assert claim_next_job() is None
    assert _count_jobs() == 2
    # Completing frees the slot; the next claim takes the remaining queued job.
    complete_job(first)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == second


# ---------------------------------------------------------------------------
# CLAIM_CONCURRENT
# ---------------------------------------------------------------------------


def test_claim_concurrent_exactly_one_claims(world: str) -> None:
    """Two threads claim with a barrier: BEGIN IMMEDIATE serializes; exactly
    one job is claimed, the loser sees the running row and returns None."""
    first = _enqueued(world)
    _enqueued(world)
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def _claim(label: str) -> None:
        barrier.wait()  # both threads race for the write lock together
        claimed = claim_next_job()
        results[label] = claimed.id if claimed is not None else None

    threads = [threading.Thread(target=_claim, args=(label,)) for label in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [results[label] for label in ("A", "B") if results[label] is not None]
    assert len(winners) == 1, f"expected exactly one claim: {results}"
    assert winners[0] == first  # FIFO: the earliest rowid wins


# ---------------------------------------------------------------------------
# COMPLETE
# ---------------------------------------------------------------------------


def test_complete_succeeds_and_frees_slot(world: str) -> None:
    first = _enqueued(world)
    second = _enqueued(world)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == first
    done = complete_job(claimed.id)
    assert done.state == "succeeded"
    assert done.finished_at is not None
    assert ISO_Z_RE.fullmatch(done.finished_at)
    assert done.error is None
    claimed_again = claim_next_job()
    assert claimed_again is not None and claimed_again.id == second


def test_complete_wrong_state_and_unknown(world: str) -> None:
    queued = _enqueued(world)
    with pytest.raises(JobStateConflictError):
        complete_job(queued)  # queued, not running
    assert job_status(queued)[0].state == "queued"  # state unchanged
    with pytest.raises(JobNotFoundError):
        complete_job(MISSING_ID)
    # Complete the still-queued job, then a terminal re-complete is a conflict.
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == queued
    complete_job(claimed.id)
    with pytest.raises(JobStateConflictError):  # terminal jobs cannot complete again
        complete_job(claimed.id)
    assert job_status(queued)[0].state == "succeeded"


# ---------------------------------------------------------------------------
# FAIL
# ---------------------------------------------------------------------------


def test_fail_stores_error_and_frees_slot(world: str) -> None:
    first = _enqueued(world)
    second = _enqueued(world)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == first
    failed = fail_job(claimed.id, "inference exploded: OOM")
    assert failed.state == "failed"
    assert failed.error == "inference exploded: OOM"
    assert failed.finished_at is not None
    assert ISO_Z_RE.fullmatch(failed.finished_at)
    claimed_again = claim_next_job()
    assert claimed_again is not None and claimed_again.id == second


def test_fail_wrong_state_and_unknown(world: str) -> None:
    queued = _enqueued(world)
    with pytest.raises(JobStateConflictError):
        fail_job(queued, "boom")
    assert job_status(queued)[0].state == "queued"
    with pytest.raises(JobNotFoundError):
        fail_job(MISSING_ID, "boom")


# ---------------------------------------------------------------------------
# PROGRESS
# ---------------------------------------------------------------------------


def test_progress_stores_and_stays_running(world: str) -> None:
    running = _claimed(world)
    report_progress(running, 0.5)
    job, position = job_status(running)
    assert job.state == "running"
    assert job.progress == 0.5
    assert position == 1
    report_progress(running, 0.0)
    report_progress(running, 1.0)
    assert job_status(running)[0].progress == 1.0


@pytest.mark.parametrize("bad", [-0.1, 1.1, float("nan")])
def test_progress_out_of_range_rejected(world: str, bad: float) -> None:
    running = _claimed(world)
    with pytest.raises(InvalidJobInputError):
        report_progress(running, bad)
    assert job_status(running)[0].progress == 0.0  # unchanged


def test_progress_wrong_state_and_unknown(world: str) -> None:
    queued = _enqueued(world)
    with pytest.raises(JobStateConflictError):
        report_progress(queued, 0.5)
    with pytest.raises(JobNotFoundError):
        report_progress(MISSING_ID, 0.5)


# ---------------------------------------------------------------------------
# CANCEL_QUEUED / CANCEL_RUNNING / CANCEL_TERMINAL
# ---------------------------------------------------------------------------


def test_cancel_queued_frees_slot(world: str) -> None:
    first = _enqueued(world)
    second = _enqueued(world)
    cancelled = cancel_job(first)
    assert cancelled.state == "cancelled"
    assert cancelled.finished_at is not None
    assert cancelled.started_at is None  # never ran
    # Slot freed: the claim skips the cancelled job (rowid order).
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == second
    _job, position = job_status(second)
    assert position == 1  # terminal jobs do not count toward positions


def test_cancel_running_frees_slot(world: str) -> None:
    running = _claimed(world)
    cancelled = cancel_job(running)
    assert cancelled.state == "cancelled"
    assert cancelled.started_at is not None and cancelled.finished_at is not None
    assert claim_next_job() is None  # queued empty; the running slot is freed
    next_id = _enqueued(world)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == next_id


def test_cancel_terminal_rejected_state_unchanged(world: str) -> None:
    succeeded = _claimed(world)
    complete_job(succeeded)
    with pytest.raises(JobStateConflictError):
        cancel_job(succeeded)
    assert job_status(succeeded)[0].state == "succeeded"

    failed = _claimed(world)
    fail_job(failed, "boom")
    with pytest.raises(JobStateConflictError):
        cancel_job(failed)
    assert job_status(failed)[0].state == "failed"

    cancelled = _claimed(world)
    cancel_job(cancelled)
    with pytest.raises(JobStateConflictError):
        cancel_job(cancelled)
    assert job_status(cancelled)[0].state == "cancelled"


# ---------------------------------------------------------------------------
# STATUS_POSITION
# ---------------------------------------------------------------------------


def test_status_position_counts_non_terminal_earlier(world: str) -> None:
    """Position = 1 + count of same-campaign non-terminal jobs with earlier
    rowid (per-campaign and privacy-safe, AD-9)."""
    a = _enqueued(world)
    b = _enqueued(world)
    c = _enqueued(world)
    cancel_job(b)
    assert job_status(a)[1] == 1
    assert job_status(b)[1] is None  # terminal: no longer in the queue
    assert job_status(c)[1] == 2
    with pytest.raises(JobNotFoundError):
        job_status(MISSING_ID)


# ---------------------------------------------------------------------------
# RESTART (AR11)
# ---------------------------------------------------------------------------


def test_restart_restores_queue_no_loss(tmp_path: Path) -> None:
    """Process restart (same file re-opened by a fresh engine): queued/running
    jobs restored with identical positions — the queue lives in the store."""
    url = f"sqlite:///{tmp_path / 'restart.db'}"
    previous = app_db_url()
    init_db(url)
    try:
        campaign = create_campaign(
            _owner_id(), title="Restart", description="", theme="High Fantasy", custom_lore=""
        ).id
        first = _enqueued(campaign)
        second = _enqueued(campaign)
        claimed = claim_next_job()
        assert claimed is not None and claimed.id == first
        # "Restart": re-init a fresh engine against the same file.
        init_db(f"sqlite:///{tmp_path / 'elsewhere.db'}")
        init_db(url)
        _job, first_pos = job_status(first)
        assert _job.state == "running" and first_pos == 1
        _job, second_pos = job_status(second)
        assert _job.state == "queued" and second_pos == 2
        assert claim_next_job() is None  # the running invariant survives restart
        complete_job(first)  # transitions still work after the restart
        claimed_again = claim_next_job()
        assert claimed_again is not None and claimed_again.id == second
    finally:
        init_db(previous)


# ---------------------------------------------------------------------------
# CROSS_CAMPAIGN
# ---------------------------------------------------------------------------


def test_cross_campaign_lists_and_positions_isolated(world: str) -> None:
    """A's list/positions never show B's jobs (AD-9); the global FIFO claims
    across campaigns in rowid order (AD-3)."""
    other = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    a1 = _enqueued(world)
    b1 = _enqueued(other)
    a2 = _enqueued(world)

    assert job_status(a1)[1] == 1
    assert job_status(a2)[1] == 2
    assert job_status(b1)[1] == 1  # per-campaign positions, not global

    a_jobs, b_jobs = list_jobs(world), list_jobs(other)
    assert [job.id for job, _ in a_jobs[0]] == [a1, a2]
    assert [job.id for job, _ in b_jobs[0]] == [b1]

    claimed = claim_next_job()
    assert claimed is not None and claimed.id == a1  # global FIFO by rowid


# ---------------------------------------------------------------------------
# list_jobs cursor pagination
# ---------------------------------------------------------------------------


def test_list_jobs_cursor_pagination(world: str) -> None:
    a = _enqueued(world)
    b = _enqueued(world)
    c = _enqueued(world)
    page1, cursor = list_jobs(world, limit=2)
    assert [job.id for job, _ in page1] == [a, b]
    assert [position for _, position in page1] == [1, 2]
    assert cursor == b
    page2, cursor2 = list_jobs(world, cursor=cursor, limit=2)
    assert [job.id for job, _ in page2] == [c]
    assert [position for _, position in page2] == [3]  # later pages keep positions
    assert cursor2 is None
    # A fabricated cursor is a user error (422), never a silent page reset
    # (epic-1 retro item 2) — jobs are never deleted, so a miss can only
    # mean the cursor names nothing.
    with pytest.raises(InvalidJobInputError):
        list_jobs(world, cursor=ids.new_id(), limit=2)


# ---------------------------------------------------------------------------
# Change-listener contract (the WebSocket seam)
# ---------------------------------------------------------------------------


def test_change_listener_receives_every_transition(
    world: str, listener: list[tuple[str, str]]
) -> None:
    """Every committed transition fires the AD-17 event names in order."""
    job_id = _enqueued(world)
    assert listener == [("queue_changed", job_id)]
    listener.clear()
    claimed = claim_next_job()
    assert claimed is not None
    assert listener == [("queue_changed", job_id)]  # claim -> queue_changed
    listener.clear()
    report_progress(job_id, 0.5)
    assert listener == [("job_progress", job_id)]
    listener.clear()
    complete_job(job_id)
    assert listener == [("job_done", job_id), ("queue_changed", job_id)]


def test_change_listener_fail_and_cancel(world: str, listener: list[tuple[str, str]]) -> None:
    """A failed job frees the slot and shifts positions — it must emit
    ``queue_changed`` exactly like complete/cancel (review round 1)."""
    failed = _claimed(world)
    listener.clear()
    fail_job(failed, "boom")
    assert listener == [("job_failed", failed), ("queue_changed", failed)]
    cancelled = _claimed(world)
    listener.clear()
    cancel_job(cancelled)
    assert listener == [("job_cancelled", cancelled), ("queue_changed", cancelled)]


def test_rejected_transitions_fire_nothing(
    world: str, listener: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """4xx rejections are never a state change — and never emit."""
    monkeypatch.setenv(MAX_PENDING_PER_CAMPAIGN, "1")
    queued = _enqueued(world)
    listener.clear()
    with pytest.raises(QueueFullError):
        enqueue_job(world, "text", {"x": 1})
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "simulate", {"x": 1})
    with pytest.raises(JobStateConflictError):
        complete_job(queued)  # queued, not running
    assert listener == []


def test_listener_clear_stops_notifications(world: str) -> None:
    set_change_listener(None)
    _enqueued(world)  # must not raise with no listener registered
    job_id = list_jobs(world)[0][0][0].id
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == job_id  # normal operation


# ---------------------------------------------------------------------------
# Review-regression tests (2026-08-30 review round 1)
# ---------------------------------------------------------------------------


def test_recover_stale_running_requeues(tmp_path: Path) -> None:
    """A ``running`` job surviving a process death is re-queued at startup
    recovery (AR11, spec RESTART_STALE_RUNNING) — the FIFO never deadlocks
    on a stale ``running`` row."""
    url = f"sqlite:///{tmp_path / 'recover.db'}"
    init_db(url)
    campaign = create_campaign(
        _owner_id(), title="Recover World", description="", theme="High Fantasy", custom_lore=""
    ).id
    first = _enqueued(campaign)
    _second = _enqueued(campaign)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == first

    # "Crash": a fresh engine re-opens the same file; recovery runs.
    init_db(url)
    recovered = recover_stale_running()
    assert recovered == 1

    # The stale job is queued again at the head of the FIFO, started_at cleared.
    job, _position = job_status(first)
    assert job.state == "queued"
    assert job.started_at is None
    # The FIFO flows: claiming proceeds in original order.
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == first
    job, _position = job_status(first)
    assert job.state == "running"
    assert job.started_at is not None


def test_recover_stale_running_ignores_queued_and_terminal(tmp_path: Path) -> None:
    """Only ``running`` rows are re-queued; queued and terminal jobs are
    untouched by startup recovery."""
    url = f"sqlite:///{tmp_path / 'recover2.db'}"
    init_db(url)
    campaign = create_campaign(
        _owner_id(), title="Recover World 2", description="", theme="High Fantasy", custom_lore=""
    ).id
    queued_id = _enqueued(campaign)
    running_id = _enqueued(campaign)
    done_id = _enqueued(campaign)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == queued_id  # FIFO: earliest first
    complete_job(queued_id)
    claimed = claim_next_job()  # next in FIFO: running_id
    assert claimed is not None and claimed.id == running_id
    # now: running_id running, done_id queued — recover finds the one running.
    init_db(url)  # crash + restart
    assert recover_stale_running() == 1
    running_job, _ = job_status(running_id)
    done_job, _ = job_status(done_id)
    assert running_job.state == "queued"  # re-queued
    assert running_job.started_at is None
    assert done_job.state == "queued"  # untouched
    queued_job, _ = job_status(queued_id)
    assert queued_job.state == "succeeded"  # untouched


def test_cancel_unknown_job_rejected(world: str) -> None:
    """Cancelling a job that does not exist is a structured 404 (the cancel
    route's user-reachable error path, pinned — review round 1)."""
    with pytest.raises(JobNotFoundError):
        cancel_job(MISSING_ID)


def test_list_includes_terminal_jobs_with_null_position(world: str) -> None:
    """The campaign list keeps terminal jobs (full-history, AR11 no-loss)
    with ``queue_position`` null; pagination orders across them."""
    a = _enqueued(world)
    b = _enqueued(world)
    c = _enqueued(world)
    claimed = claim_next_job()
    assert claimed is not None and claimed.id == a
    complete_job(a)
    cancel_job(b)  # b is queued — cancel succeeds

    rows, _next = list_jobs(world, limit=10)
    by_id = {job.id: (job.state, position) for job, position in rows}
    assert by_id[a] == ("succeeded", None)  # terminal, no position
    assert by_id[b] == ("cancelled", None)
    assert by_id[c] == ("queued", 1)  # the only pending job left
    assert [job.id for job, _ in rows] == [a, b, c]  # rowid order preserved


def test_enqueue_cap_race_exactly_one_wins(world: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two concurrent enqueues racing the cap at cap-1: BEGIN IMMEDIATE
    serializes the count-query + insert; exactly one lands, one QueueFull."""
    monkeypatch.setenv(MAX_PENDING_PER_CAMPAIGN, "1")
    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def _enqueue(label: str) -> None:
        barrier.wait()
        try:
            enqueue_job(world, "text", {"label": label})
            outcomes[label] = "ok"
        except QueueFullError:
            outcomes[label] = "full"

    threads = [threading.Thread(target=_enqueue, args=(label,)) for label in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes.values()) == ["full", "ok"]
    _rows, _next = list_jobs(world, limit=10)
    assert len(_rows) == 1


def test_cross_campaign_cursor_rejected(world: str) -> None:
    """A cursor naming another campaign's job is a user error (422), never a
    silent page skip (review round 1 pagination scoping)."""
    other = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    other_job = _enqueued(other)
    with pytest.raises(InvalidJobInputError):
        list_jobs(world, cursor=other_job)


def test_list_unknown_campaign_rejected(world: str) -> None:
    """Listing a campaign that does not exist is 404 — it must not look
    like an empty world (review round 1)."""
    with pytest.raises(UnknownCampaignError):
        list_jobs(MISSING_ID)


def test_job_id_globally_unique(world: str) -> None:
    """A caller-supplied ``job_id`` is unique across the whole store (the
    job id is the primary key) — reusing one idempotency key in a second
    campaign is a structured rejection, never a raw IntegrityError."""
    other = create_campaign(
        _owner_id(), title="Other World", description="", theme="High Fantasy", custom_lore=""
    ).id
    job_id = ids.new_id()
    enqueue_job(world, "text", {"x": 1}, job_id=job_id)
    with pytest.raises(DuplicateJobError):
        enqueue_job(other, "text", {"x": 2}, job_id=job_id)


def test_non_json_serializable_payload_rejected(world: str) -> None:
    """A payload value SQLAlchemy's JSON column cannot serialize is a 422 at
    the store boundary, never a raw flush-time TypeError (review round 1)."""
    with pytest.raises(InvalidJobInputError):
        enqueue_job(world, "text", {"when": __import__("datetime").datetime.now()})
    assert _count_jobs() == 0


def test_migrate_job_kind_adds_build_in(tmp_path: Path) -> None:
    """A pre-2.1 database's job-kind CHECK is rebuilt to admit ``build_in``:
    SQLite cannot ALTER a CHECK constraint, so ``store.db._migrate_job_kind``
    rebuilds the table and re-creates its indexes; the queue keeps working
    (store-level migration test)."""
    db_path = tmp_path / "old-schema.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE account (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            email VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            created_at VARCHAR(40) NOT NULL
        );
        CREATE TABLE campaign (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            owner_id VARCHAR(26) NOT NULL REFERENCES account(id),
            title VARCHAR(300) NOT NULL,
            description TEXT NOT NULL,
            theme VARCHAR(100) NOT NULL,
            custom_lore TEXT NOT NULL,
            created_at VARCHAR(40) NOT NULL
        );
        CREATE TABLE job (
            id VARCHAR(26) NOT NULL PRIMARY KEY,
            campaign_id VARCHAR(26) NOT NULL REFERENCES campaign(id),
            kind VARCHAR(64) NOT NULL,
            payload JSON NOT NULL,
            state VARCHAR(32) NOT NULL,
            progress FLOAT NOT NULL,
            max_llm_calls INTEGER NOT NULL,
            max_media_calls INTEGER NOT NULL,
            error TEXT,
            result JSON,
            created_at VARCHAR(40) NOT NULL,
            started_at VARCHAR(40),
            finished_at VARCHAR(40),
            CONSTRAINT ck_job_kind CHECK (kind IN ('text','image','video'))
        );
        CREATE INDEX ix_job_state ON job (state);
        CREATE INDEX ix_job_campaign_id ON job (campaign_id);
        """
    )
    raw.commit()
    raw.close()

    previous = app_db_url()
    init_db(f"sqlite:///{db_path}")
    try:
        campaign = create_campaign(
            _owner_id(),
            title="Migrated World",
            description="",
            theme="High Fantasy",
            custom_lore="",
        ).id
        job = enqueue_job(campaign, "build_in", {"places": ["Greymarch"]})
        assert job.kind == "build_in" and job.state == "queued"
        with session_scope() as session:
            ddl = session.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name='job'")
            ).scalar_one()
            assert "build_in" in ddl  # the rebuilt constraint admits build_in
            index_names = set(
                session.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='job'")
                )
                .scalars()
                .all()
            )
        assert {"ix_job_state", "ix_job_campaign_id"} <= index_names
        # Idempotent: re-init leaves the already-migrated table untouched.
        init_db(f"sqlite:///{db_path}")
        with session_scope() as session:
            count = session.scalar(select(func.count()).select_from(models.Job)) or 0
        assert count == 1  # the build_in job survived the re-init
    finally:
        init_db(previous)


def test_enqueue_build_in_idempotent_by_job_id(world: str) -> None:
    """Same valid build-in payload + same caller-supplied job_id twice ->
    DuplicateJobError (409); exactly one row is written and the first
    payload is unchanged (idempotent by job-id, spec-2.1)."""
    job_id = ids.new_id()
    payload = {"places": ["Greymarch"], "factions": ["The Guild"], "notes": "rain"}
    first = enqueue_job(world, "build_in", payload, job_id=job_id)
    assert first.state == "queued"
    with pytest.raises(DuplicateJobError):
        enqueue_job(world, "build_in", payload, job_id=job_id)
    assert _count_jobs() == 1
    _job, _position = job_status(job_id)
    assert _job.payload == payload  # the first submission wins, unchanged
