"""The regenerate runner (spec-3.5): one re-roll -> zero or one proposed row.

Whole-character re-rolls target a committed entity (``target.kind ==
"entity"``): the runner resolves the target fresh from ``world_state``,
stages ONE new ``ProposedCandidate`` row with ``regenerates_entity_id``
set (the committed entity stays untouched until the DM accepts — the
accept then replaces the entity IN PLACE, one revision, ULID + edges
preserved). Per-section re-rolls target a proposed candidate
(``target.kind == "candidate"``): the runner re-resolves the row fresh
and replaces that row's payload IN PLACE via
``replace_candidate_payload`` — one row per intent, the job's own rows
untouched. A re-roll never calls ``commit_subgraph`` and never mutates
accepted state (AR4, AR7).

The prompt is a pure function of the campaign seed, the target record,
the requested section list, and the rowid-ordered retrieved neighborhood
seeded from the target (entity ULID, or the candidate's staged edge
endpoints) — no ids, timestamps, or job state, ever (AR6/AD-16),
byte-identical for the same inputs. The model must return the FULL
sectioned record in the standard single-candidate envelope with the
requested sections re-rolled and everything else byte-identical; the
runner VALIDATES that (full AR24 shape + preserved sections/unknown
keys/edges equal to the target) and then assembles the staged payload
by construction — ``dict(target_record)`` with only the requested
sections overwritten (trimmed) — so preserved sections are byte-identical
even if the model's echo droops (never model-fidelity-dependent).

One LLM call through ``CallBudget`` (BudgetExceededError fails the job).
Any failure (provider, budget, malformed/off-contract output, a staging
race, a non-proposed candidate) propagates so the worker fails the job
with zero new rows and no revision; a cancel racing the terminal write
discards the entity-target ghost rows before propagating the conflict (a
candidate-target replace owns no rows for this job, so the row's replaced
payload at that point is the documented residual — no new rows, no
revision, matching the build-in "committed wave stays" resilience).
"""

import copy
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

from app.core.settings import LLMSettings
from app.pipeline.budget import CallBudget
from app.pipeline.generate import _job_still_running, _parse_candidates
from app.pipeline.retrieval import (
    DEFAULT_ENTITY_CAP,
    retrieve_neighborhood,
    serialize_context,
)
from app.pipeline.statblocks import (
    spells_reference_text,
    stat_block_rules_text,
)
from app.pipeline.worker import JobPayloadError
from app.store import (
    BOSS_ROLES,
    EDGE_TYPES,
    JobStateConflictError,
    complete_job,
    discard_candidates,
    edge_counter_semantic,
    models,
    replace_candidate_payload,
    report_progress,
    stage_candidates,
)
from app.store.candidates import (
    REGEN_SECTIONS,
    payload_section_violations,
)
from app.store.db import session_scope
from app.store.read import campaign_seed, world_state

logger = logging.getLogger(__name__)

#: Canonical prompt order for the requested sections. ``REGEN_SECTIONS``
#: is a frozenset (closed set — membership only); a frozenset's iteration
#: order is hash-randomized, so the prompt and the splice order use this
#: sorted tuple for byte determinism.
SECTION_ORDER: tuple[str, ...] = tuple(sorted(REGEN_SECTIONS))


def run_regenerate(job: models.Job, provider: Callable[..., str], settings: LLMSettings) -> None:
    """Run one regenerate job to a terminal state (complete_job/fail_job).

    Resolves the target FRESH (an entity deleted/undone since enqueue, a
    candidate settled since enqueue, or a campaign gone) -> JobPayloadError
    -> the worker fails the job, zero rows: the enqueue-time validation is
    not trusted across queue delay. A job cancelled before the provider
    call or before the write is a no-op (cancel-race polls); a cancel
    racing the terminal write discards entity-target ghost rows then
    propagates the conflict (generate's pattern).
    """
    payload = job.payload
    if (
        not isinstance(payload, dict)
        or set(payload) > {"target", "sections"}
        or not isinstance(payload.get("target"), dict)
    ):
        raise JobPayloadError("regenerate: job payload must be {'target': ..., 'sections': ...}")
    target = payload["target"]
    if set(target) != {"kind", "id"}:
        raise JobPayloadError("regenerate: target must be {'kind': ..., 'id': ...}")
    target_kind = target["kind"]
    target_id = target["id"]
    if target_kind not in ("entity", "candidate") or not isinstance(target_id, str):
        raise JobPayloadError("regenerate: target kind/id are malformed")

    with session_scope() as session:
        seed = campaign_seed(session, job.campaign_id)
        if seed is None:
            raise JobPayloadError(f"regenerate: campaign {job.campaign_id} does not exist")
        entities, _edges = world_state(session, job.campaign_id)
        record, seeds = _resolve_record(session, job.campaign_id, target_kind, target_id, entities)

    requested = _requested_sections(payload, record)
    budget = CallBudget(job)

    # Cancel-race poll: a cancel between claim and the call is a no-op.
    if not _job_still_running(job):
        return
    context_entities, context_edges = retrieve_neighborhood(
        job.campaign_id, seed_ids=seeds, entity_cap=DEFAULT_ENTITY_CAP
    )
    prompt = build_regenerate_prompt(seed, record, requested, (context_entities, context_edges))
    text = budget.call(lambda: provider(prompt, settings=settings))

    parsed = _parse_candidates(text)
    # The regenerate contract is a single-candidate envelope: exactly one
    # record in the standard {"candidates": [...]} shape.
    if len(parsed) != 1:
        raise JobPayloadError(f"regenerate: expected exactly 1 candidate, got {len(parsed)}")
    raw = parsed[0]
    if not isinstance(raw, dict):
        raise JobPayloadError("regenerate: candidate must be an object")
    _validate_output(raw, record, requested)

    # Assemble by construction: the target record with only the requested
    # sections overwritten (trimmed) — preserved sections, unknown keys,
    # and edges come from the target, never from the model (byte-identical
    # is an assembly invariant, not a model promise).
    payload = dict(record)
    for section in requested:
        if section in raw:
            payload[section] = _trim_section(raw[section])
    if target_kind == "entity":
        payload["edges"] = []

    # Cancel-race poll before the write: a cancel during the call/validation
    # must not stage nor replace anything.
    if not _job_still_running(job):
        return
    if target_kind == "candidate":
        row = replace_candidate_payload(job.campaign_id, target_id, payload)
        rows = [row]
    else:
        rows = stage_candidates(job.campaign_id, job.id, [payload], target_entity_id=target_id)
    try:
        report_progress(job.id, 1.0)
        complete_job(
            job.id,
            result={
                "candidate_ids": [row.id for row in rows],
                "candidate_count": len(rows),
                "target": {"kind": target_kind, "id": target_id},
                "sections": sorted(requested),
            },
        )
    except JobStateConflictError:
        # Cancel raced the terminal write: the entity-target rows are
        # ghosts — discard them, then propagate (the worker records nothing
        # on a cancelled job). A candidate-target replace owns no rows for
        # this job, so discard is a no-op there (documented residual: the
        # replaced payload landed a moment before the cancel).
        try:
            discard_candidates(job.id)
        except Exception:  # noqa: BLE001 - the conflict is the error to surface
            logger.exception("failed to discard ghost rows for job %s", job.id)
        raise


def _resolve_record(
    session: Any,
    campaign_id: str,
    target_kind: str,
    target_id: str,
    entities: Sequence[models.Entity],
) -> tuple[dict[str, Any], Sequence[str] | None]:
    """Resolve the target's AR24 record fresh inside the runner's session.

    An entity is looked up in the materialized world (a fresh read —
    undo/delete since enqueue fails the job, it never regenerates a
    ghost); a candidate in the staging table (must still be proposed and
    of this campaign). The record is a deep COPY taken before the session
    closes — the JSON column's object must outlive the transaction.
    Returns (record, retrieval seed_ids): seeds None = whole world.
    """
    entity_by_id = {entity.id: entity for entity in entities}
    if target_kind == "entity":
        entity = entity_by_id.get(target_id)
        if entity is None:
            raise JobPayloadError(
                f"regenerate: target entity {target_id} is no longer committed "
                f"world state of campaign {campaign_id}"
            )
        record = entity.data
        if not isinstance(record, dict):
            raise JobPayloadError(f"regenerate: target entity {target_id} has no record")
        seeds: Sequence[str] | None = [target_id]
    else:
        candidate = session.get(models.ProposedCandidate, target_id)
        if candidate is None or candidate.campaign_id != campaign_id:
            raise JobPayloadError(
                f"regenerate: target candidate {target_id} does not exist in campaign {campaign_id}"
            )
        if candidate.status != "proposed":
            raise JobPayloadError(
                f"regenerate: target candidate {target_id} is already {candidate.status}"
            )
        record = candidate.payload
        if not isinstance(record, dict):
            raise JobPayloadError(f"regenerate: target candidate {target_id} has no record")
        # Seed retrieval from the candidate's staged edge endpoints — the
        # committed entities the proposal weaves into. Endpoints deleted
        # since staging resolve to nothing: fall back to the whole world
        # (retrieval rejects an empty seed list; a full-world context is
        # the resilient alternative to a dead-end seed).
        seeds = [
            str(edge.get("endpoint"))
            for edge in record.get("edges", [])
            if isinstance(edge, dict)
            and isinstance(edge.get("endpoint"), str)
            and edge.get("endpoint") in entity_by_id
        ]
        if not seeds:
            seeds = None
    return copy.deepcopy(record), seeds


def _requested_sections(payload: Any, record: dict[str, Any]) -> list[str]:
    """The requested section list: null/absent = the whole record —
    every regenerable content section EXCEPT ``boss`` on a target whose
    role is not BBEG/Monster (a whole re-roll of an NPC must never ask
    the model to re-write a section that must not exist on that record —
    the explicit ``["boss"]``-on-NPC enqueue 422 stays as-is);
    otherwise the validated list. Ordered canonically for the prompt and
    the splice (deterministic bytes)."""
    sections = payload.get("sections") if isinstance(payload, dict) else None
    if sections is None:
        requested = list(SECTION_ORDER)
        role = record.get("role") if isinstance(record, dict) else None
        if role not in BOSS_ROLES:
            requested = [section for section in requested if section != "boss"]
        return requested
    if not isinstance(sections, list) or not sections:
        raise JobPayloadError("regenerate: sections must be null or a non-empty list")
    bad = [s for s in sections if not isinstance(s, str) or s not in REGEN_SECTIONS]
    if bad:
        raise JobPayloadError(f"regenerate: sections outside the closed set: {sorted(set(bad))}")
    # Canonical order (SECTION_ORDER) regardless of the caller's ordering:
    # the prompt and the splice depend on the requested SET, so the bytes
    # stay deterministic for the same set (AR6/AD-16). Duplicates collapse.
    return [section for section in SECTION_ORDER if section in set(sections)]


def _validate_output(raw: dict[str, Any], record: dict[str, Any], requested: Sequence[str]) -> None:
    """The re-rolled candidate must be a full AR24 record (same shape as
    staging) whose every NON-requested section — identity anchor, edges,
    unknown keys — is byte-identical to the target: the DM-sanctioned
    change set is exactly the requested sections. A model that edits name
    or role, drops the edges, or paraphrases a preserved section fails
    the job (MODEL_SHAPE_VIOLATION, zero rows); the assembly would ignore
    such edits anyway, but failing keeps the contract honest (the DM
    asked for exactly one section, not a silent rewrite).
    """
    violations = payload_section_violations(raw)
    for key, value in record.items():
        if key in requested:
            continue
        if raw.get(key) != value:
            violations.append(
                f"{key} must stay byte-identical to the target record — "
                f"regenerate only the requested sections"
            )
    if violations:
        raise JobPayloadError(
            f"regenerate: re-rolled record fails the contract: {'; '.join(violations)}"
        )


def _trim_section(value: Any) -> Any:
    """One regenerated section trimmed: strings strip; string-valued dict
    sections (world_integration, boss) strip per field; everything else
    (the stat block's nested structure) passes unchanged — generate's
    trimming convention."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return {k: (v.strip() if isinstance(v, str) else v) for k, v in value.items()}
    return value


def build_regenerate_prompt(
    campaign_seed: models.Campaign,
    record: dict[str, Any],
    requested: Sequence[str],
    context: tuple[Sequence[models.Entity], Sequence[models.Edge]],
) -> str:
    """The regenerate prompt (AR6/AR27/AD-16): pure and byte-deterministic.

    A function of the campaign seed fields, the target record (with the
    to-preserve sections embedded verbatim), the requested section list,
    and the retrieved neighborhood's serialized hard truths — no ids,
    timestamps, or job state, ever. The model returns ONE full sectioned
    record: requested sections re-rolled, everything else byte-identical
    (identity anchor + edges + unknown keys untouched), in the same
    single-candidate envelope the generate runner parses.
    """
    entities, edges = context
    # Canonical order for the requested SET: the builder is a pure function
    # of the set, so its bytes are deterministic for the same set (AR6/AD-16).
    requested = [section for section in SECTION_ORDER if section in set(requested)]
    requested_label = ", ".join(requested) or "(none)"
    lines = [
        "You are re-rolling part of an existing TTRPG world character:",
        "regenerate exactly the requested sections of the TARGET RECORD below,",
        "keeping every other section byte-for-byte identical to it.",
        "Respond with exactly one JSON object — nothing else.",
        "",
        "CAMPAIGN SEED",
        f"title: {campaign_seed.title}",
        f"description: {campaign_seed.description}",
        f"theme: {campaign_seed.theme}",
        f"custom lore: {campaign_seed.custom_lore}",
        "",
        "TARGET RECORD (the character's full committed sectioned profile)",
        serialize_record(record),
        "",
        "REQUESTED SECTIONS (regenerate exactly these; do not touch any other)",
        requested_label,
        "",
        "COMMITTED WORLD CONTEXT",
        serialize_context(entities, edges),
        "",
        "TASK",
        'Return ONE JSON object: {"candidates": [one full sectioned record]} —',
        "exactly 1 entry. The record is the TARGET RECORD above with the",
        "REQUESTED SECTIONS re-written fresh; THE IDENTITY ANCHOR (name, role,",
        "level_cr, race_type, class_profession, alignment) and edges must be",
        "byte-identical to the TARGET RECORD (they are never re-rolled). Keep",
        "every other section and every unknown key byte-identical too, or",
        "the job fails. The world_integration, boss (only for BBEG/Monster",
        "roles — omit for NPC), and all narrative sections are non-blank",
        "strings; the boss section is NEVER an empty object.",
        "",
        # The stat-block machinery is only relevant when a stat_block is
        # actually being re-rolled: its rules and the (large) spells
        # reference would otherwise bloat a personality-only prompt, and
        # the byte-identical stat_block is already embedded in the target
        # record for the model to echo. The EDGE VOCABULARY + COUNTER
        # SEMANTICS blocks stay unconditional — edges must echo
        # byte-identical on every re-roll.
        "EDGE VOCABULARY (closed set — never invent a type)",
        *(f"- {edge_type}" for edge_type in sorted(EDGE_TYPES)),
        "",
        "COUNTER SEMANTICS (one integer per edge)",
        *(f"- {edge_type}: {edge_counter_semantic(edge_type)}" for edge_type in sorted(EDGE_TYPES)),
    ]
    if "stat_block" in requested:
        # The full rules + spells reference embed only when the mechanic
        # is re-rolled (kept as a separate block so the byte-deterministic
        # structure stays a pure function of the requested set).
        lines += [
            "",
            "STAT BLOCKS",
            stat_block_rules_text(),
            "",
            spells_reference_text(),
        ]
    return "\n".join(lines)


def serialize_record(record: dict[str, Any]) -> str:
    """The target record's deterministic serialization for the prompt:
    ``json.dumps(sort_keys=True)`` — canonical key order, byte-identical
    for the same record (AD-16), ids/timestamps never appear."""
    return json.dumps(record, sort_keys=True)
