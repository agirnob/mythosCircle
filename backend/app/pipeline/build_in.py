"""The build-in runner (spec-2.3): one job, two internal waves.

Wave 1 digests the named sections (places, factions, key figures) into a
fully networked core subgraph of ``character``/``faction``/``place``
entities + typed edges and commits it atomically (one revision). Wave 2
digests ``notes`` into a second subgraph — every new entity wired by at
least one typed edge into the committed core — committed against wave 1's
revision (a DM edit landing between the waves raises
``StaleRevisionError`` and the job fails; never a silent overwrite,
AD-2).

Wave ref schemes: wave 1's entities are ``E<index>`` (positional), wave
2's new entities are ``N<index>`` (positional) so the model never reuses
wave 1's E labels for different entities, and wave-2 edges may also
reference committed context entities as ``C<index>`` (context order).
Refs are canonical — no zero-padded indices.

Determinism (AD-16): prompts are pure functions of the campaign seed and
payload/context — no ids, timestamps, or job state (pinned
byte-identical by tests). Budget (AR21): every provider call goes
through ``CallBudget``; exceeding it fails the job before any HTTP
request. Malformed or invalid output fails the job with an error event;
the failing wave writes nothing, and waves already committed before the
failure (the wave-1 core) stay — documented resilience, no compensating
undo.
"""

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any, cast

from app.core import ids
from app.core.settings import LLMSettings
from app.pipeline.budget import CallBudget
from app.pipeline.retrieval import retrieve_neighborhood, serialize_context
from app.pipeline.worker import JobPayloadError
from app.store import (
    EDGE_TYPES,
    commit_subgraph,
    complete_job,
    edge_counter_semantic,
    job_status,
    models,
    report_progress,
)
from app.store.db import session_scope
from app.store.read import campaign_seed, latest_revision

logger = logging.getLogger(__name__)

#: Entity kinds the build-in output contract accepts (spec-2.3).
ENTITY_KINDS: frozenset[str] = frozenset({"character", "faction", "place"})

#: Build-in payload sections, in prompt order (spec-2.1).
SECTION_NAMES: tuple[str, ...] = ("places", "factions", "key_figures")

#: Wave-2 context bounds (AR6 seed values): the neighborhood of the
#: wave-1 entities, one hop deep, at most 24 entities.
RETRIEVAL_DEPTH = 1
RETRIEVAL_ENTITY_CAP = 24


def run_build_in(job: models.Job, provider: Callable[..., str], settings: LLMSettings) -> None:
    """Run one build-in job to a terminal state (complete_job/fail_job).

    Wave 1 commits first (AR5); wave 2 (notes) commits against wave 1's
    revision. ``report_progress`` marks 0.5 after wave 1 and 1.0 after
    wave 2 (AD-17). A job cancelled before a wave's provider call — or
    between a wave's call and its commit — is a no-op for that wave; a
    cancel racing the commit leaves the committed wave in place but never
    writes progress or a terminal state on the cancelled job. Any failure
    (provider, budget, malformed output, invalid subgraph, stale base)
    propagates so the worker fails the job; earlier committed waves stay.
    """
    with session_scope() as session:
        seed = campaign_seed(session, job.campaign_id)
        if seed is None:
            raise JobPayloadError(f"build_in: campaign {job.campaign_id} does not exist")
        head = latest_revision(session, job.campaign_id)
    wave1_base = head.id if head is not None else None

    payload = job.payload
    if not isinstance(payload, dict):
        raise JobPayloadError("build_in: job payload must be a JSON object")
    _check_sections(payload)
    notes = payload.get("notes", "")
    if not isinstance(notes, str):
        raise JobPayloadError("build_in: payload 'notes' must be a string")
    notes = notes.strip()

    budget = CallBudget(job)

    # Wave 1: the named sections -> a committed, fully networked core.
    if not _job_still_running(job):
        return
    prompt_1 = build_wave1_prompt(seed, payload)
    text_1 = budget.call(lambda: provider(prompt_1, settings=settings))
    parsed_1 = parse_build_output(text_1, wave=1)
    entities_1, edges_1 = _validate_subgraph(1, parsed_1)
    revision_1 = commit_subgraph(job.campaign_id, entities_1, edges_1, base_revision=wave1_base)
    waves: list[dict[str, Any]] = [_wave_result(1, revision_1.id, entities_1, edges_1)]
    # Cancel-race poll: a cancel that landed during wave 1's call/commit
    # leaves the committed core in place but stops before progress writes.
    if not _job_still_running(job):
        return
    report_progress(job.id, 0.5)

    # Wave 2: the free-form notes -> a second subgraph anchored into the core.
    if notes:
        context_entities, context_edges = retrieve_neighborhood(
            job.campaign_id,
            seed_ids=_entity_ulids(entities_1),
            depth=RETRIEVAL_DEPTH,
            entity_cap=RETRIEVAL_ENTITY_CAP,
        )
        core_count = min(len(entities_1), len(context_entities))
        if not _job_still_running(job):
            return
        prompt_2 = build_wave2_prompt(
            seed, notes, (context_entities, context_edges), core_count=core_count
        )
        text_2 = budget.call(lambda: provider(prompt_2, settings=settings))
        parsed_2 = parse_build_output(text_2, wave=2)
        entities_2, edges_2 = _validate_subgraph(
            2, parsed_2, context=context_entities, core_count=core_count
        )
        # Cancel-race poll: a cancel during the wave-2 call/validation must
        # not commit wave 2 — the failed wave writes nothing.
        if not _job_still_running(job):
            return
        revision_2 = commit_subgraph(
            job.campaign_id, entities_2, edges_2, base_revision=revision_1.id
        )
        # Cancel racing the wave-2 commit: the committed wave stays, but a
        # cancelled job gets no progress/terminal write.
        if not _job_still_running(job):
            return
        report_progress(job.id, 1.0)
        waves.append(_wave_result(2, revision_2.id, entities_2, edges_2))

    entity_count = sum(wave["entities"] for wave in waves)
    edge_count = sum(wave["edges"] for wave in waves)
    complete_job(
        job.id,
        result={"waves": waves, "entity_count": entity_count, "edge_count": edge_count},
    )


def build_wave1_prompt(campaign_seed: models.Campaign, payload: dict[str, Any]) -> str:
    """The wave-1 prompt: digest the named sections into a core subgraph.

    Pure and byte-deterministic (AD-16): a function of the campaign seed
    and the payload sections only — no ids, timestamps, or job state.
    Sections are trimmed (whitespace-only entries dropped) in fixed
    order; the closed edge vocabulary and its counter semantics are
    embedded so the model can never invent an edge type (spec-2.2).
    """
    sections: list[str] = []
    for section in SECTION_NAMES:
        entries = payload.get(section, [])
        trimmed = [entry.strip() for entry in entries if isinstance(entry, str) and entry.strip()]
        sections.append(f"{section} ({len(trimmed)}):")
        sections.extend(f"- {entry}" for entry in trimmed)
    lines = [
        "You are digesting a TTRPG world-build-in submission into the world graph.",
        "Respond with exactly one JSON object — nothing else.",
        "",
        "CAMPAIGN SEED",
        f"title: {campaign_seed.title}",
        f"description: {campaign_seed.description}",
        f"theme: {campaign_seed.theme}",
        f"custom lore: {campaign_seed.custom_lore}",
        "",
        "SUBMITTED SECTIONS",
        *sections,
        "",
        "TASK",
        "Create the core subgraph: entities for the notable key figures, places, and",
        "factions in the submitted sections, connected by typed directed edges",
        "(characters for key figures, places for places, factions for factions).",
        "Every entity must appear in at least one edge within this subgraph — no orphans.",
        "Edges must connect two different entities — no self-loops.",
        "",
        "OUTPUT CONTRACT",
        'Respond with one JSON object: {"entities": [...], "edges": [...]}.',
        'Each entity: {"ref": "E<index>", "kind": "character|faction|place", "name": "...",',
        '  "text": "optional narrative", "data": {optional hard-truth object}}.',
        "Refs are positional and canonical: the first entity in the list is E0, the",
        "second E1, and so on (never E01, E007).",
        'Each edge: {"src": "<ref>", "dst": "<ref>", "type": "<vocabulary member>",',
        '  "counter": <integer, default 1>}.',
        "Edge src/dst must be refs of entities in this wave. Names must be non-blank.",
        "Once this wave commits, later waves reference these entities as C<index> in",
        "the context list they receive.",
        "",
        "EDGE VOCABULARY (closed set — never invent a type)",
        *(f"- {edge_type}" for edge_type in sorted(EDGE_TYPES)),
        "",
        "COUNTER SEMANTICS (one integer per edge)",
        *(f"- {edge_type}: {edge_counter_semantic(edge_type)}" for edge_type in sorted(EDGE_TYPES)),
    ]
    return "\n".join(lines)


def build_wave2_prompt(
    campaign_seed: models.Campaign,
    notes: str,
    context: tuple[Sequence[models.Entity], Sequence[models.Edge]],
    *,
    core_count: int,
) -> str:
    """The wave-2 prompt: digest ``notes`` into entities wired into the core.

    Pure and byte-deterministic (AD-16): a function of the campaign seed,
    the notes, and the retrieved neighborhood. ``core_count`` is how many
    of the context entities are the wave-1 core (the first entries of the
    context list); those are the required anchor points for the orphan
    rule. The context serialization carries hard truths only. New
    entities use ``N<index>`` refs — never the wave-1 ``E<index>`` labels
    — so an edge can never silently target the wrong entity.
    """
    entities, edges = context
    core_label = f"C0..C{core_count - 1}" if core_count > 0 else "(none)"
    lines = [
        "You are continuing a TTRPG world-build-in: digest the free-form notes into",
        "new entities wired into the already-committed core world.",
        "Respond with exactly one JSON object — nothing else.",
        "",
        "CAMPAIGN SEED",
        f"title: {campaign_seed.title}",
        f"description: {campaign_seed.description}",
        f"theme: {campaign_seed.theme}",
        f"custom lore: {campaign_seed.custom_lore}",
        "",
        "COMMITTED WORLD CONTEXT",
        serialize_context(entities, edges),
        "",
        "NOTES TO DIGEST",
        notes.strip(),
        "",
        "TASK",
        "Create entities for the notable subjects of the notes (character/faction/place).",
        f"The CORE entities are {core_label} — the first {core_count} context entries —",
        "the entities this build created first. Wire every new entity to at least one",
        "CORE entity with a typed edge — no orphans. Other context entities are",
        "background only. Edges must connect two different entities — no self-loops.",
        "",
        "OUTPUT CONTRACT",
        'Respond with one JSON object: {"entities": [...], "edges": [...]}.',
        'Each new entity: {"ref": "N<index>", "kind": "character|faction|place",',
        '  "name": "...", "text": "optional narrative", "data": {optional hard-truth object}}.',
        "Refs are positional and canonical: the first new entity in the list is N0,",
        "the second N1, and so on (never N01, N007).",
        'Each edge: {"src": "<ref>", "dst": "<ref>", "type": "<vocabulary member>",',
        '  "counter": <integer, default 1>}.',
        "Edge src/dst may be a new-entity ref (N<index>) or a context ref (C<index>)",
        f"matching the context list above (the core is {core_label}).",
        "Names must be non-blank.",
        "",
        "EDGE VOCABULARY (closed set — never invent a type)",
        *(f"- {edge_type}" for edge_type in sorted(EDGE_TYPES)),
        "",
        "COUNTER SEMANTICS (one integer per edge)",
        *(f"- {edge_type}: {edge_counter_semantic(edge_type)}" for edge_type in sorted(EDGE_TYPES)),
    ]
    return "\n".join(lines)


def _strip_fence(text: str) -> str:
    """Strip one optional markdown fence pair from LLM output.

    Leading blank lines are ignored; when the first non-blank line is a
    fence opener (`` ``` `` or `` ```json ``) and the last line is a fence
    closer, both are removed (trailing whitespace tolerated on both). A
    fence without a closer is returned as-is so the JSON error names the
    real problem.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return stripped


def parse_build_output(text: str, wave: int = 1) -> dict[str, Any]:
    """Parse one wave's LLM output: fence-strip, then require a JSON object.

    An optional markdown fence is stripped tolerantly (see
    ``_strip_fence``); the result must be an object with ``entities`` and
    ``edges`` lists. Malformed output raises ``JobPayloadError`` naming
    the wave — the job fails, never a partial commit.
    """
    stripped = _strip_fence(text)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise JobPayloadError(f"wave {wave}: output is not valid JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise JobPayloadError(f"wave {wave}: output must be a JSON object")
    entities = parsed.get("entities")
    edges = parsed.get("edges")
    if not isinstance(entities, list) or not isinstance(edges, list):
        raise JobPayloadError(
            f"wave {wave}: output must be a JSON object with 'entities' and 'edges' lists"
        )
    return parsed


def _validate_subgraph(
    wave: int,
    parsed: dict[str, Any],
    *,
    context: Sequence[models.Entity] = (),
    core_count: int = 0,
) -> tuple[list[models.EntityInput], list[models.EdgeInput]]:
    """Validate a parsed wave against the spec-2.3 output contract and map
    refs to runner-generated ULIDs (each entity gets ``ids.new_id()`` so
    edges wire before commit). Rejections are ``JobPayloadError`` naming
    the wave, the offending entity/edge, and the reason — the job fails,
    never a partial commit.

    Wave-1 rules: refs are positional and canonical (``E<position>``),
    kinds in {character, faction, place}, types in ``EDGE_TYPES``,
    counters integers, names non-blank, no self-loops, and every entity
    appears in >= 1 edge within the subgraph (no orphans, FR2/FR4).

    Wave 2's new-entity refs are ``N<position>``; edges may also reference
    the committed context as ``C<position>``, and every entity must have
    >= 1 edge whose other endpoint is a core entity
    (``context[0:core_count]`` — the wave-1 entities).
    """
    raw_entities = parsed.get("entities")
    raw_edges = parsed.get("edges")
    if not isinstance(raw_entities, list) or not isinstance(raw_edges, list):
        raise JobPayloadError(
            f"wave {wave}: output must be a JSON object with 'entities' and 'edges' lists"
        )
    if not raw_entities:
        raise JobPayloadError(f"wave {wave}: no entities in output")

    prefix = "E" if wave == 1 else "N"
    entity_inputs: list[models.EntityInput] = []
    assigned_ids: list[str] = []
    for position, raw in enumerate(raw_entities):
        if not isinstance(raw, dict):
            raise JobPayloadError(f"wave {wave}: entity {position} is not an object")
        ref = raw.get("ref")
        if ref != f"{prefix}{position}":
            raise JobPayloadError(
                f"wave {wave}: entity {position} ref must be {prefix}{position}, got {ref!r}"
            )
        kind = raw.get("kind")
        if kind not in ENTITY_KINDS:
            raise JobPayloadError(
                f"wave {wave}: entity {position} kind {kind!r} not in {sorted(ENTITY_KINDS)}"
            )
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise JobPayloadError(f"wave {wave}: entity {position} name must be a non-blank string")
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise JobPayloadError(f"wave {wave}: entity {position} text must be a string")
        data = raw.get("data")
        if data is not None and not isinstance(data, dict):
            raise JobPayloadError(f"wave {wave}: entity {position} data must be an object")
        entity_id = ids.new_id()
        assigned_ids.append(entity_id)
        entity_inputs.append(
            models.EntityInput(
                id=entity_id,
                kind=kind,
                name=name.strip(),
                text=text,
                data=data or {},
            )
        )

    core_ids = {context[i].id for i in range(min(core_count, len(context)))}
    edge_inputs: list[models.EdgeInput] = []
    wave_positions_in_edges: set[int] = set()
    anchored_positions: set[int] = set()
    for edge_index, raw in enumerate(raw_edges):
        if not isinstance(raw, dict):
            raise JobPayloadError(f"wave {wave}: edge {edge_index} is not an object")
        edge_type = raw.get("type")
        if edge_type not in EDGE_TYPES:
            raise JobPayloadError(
                f"wave {wave}: edge {edge_index} type {edge_type!r} not in the "
                f"vocabulary: {sorted(EDGE_TYPES)}"
            )
        counter = raw.get("counter", 1)
        if type(counter) is not int:
            raise JobPayloadError(f"wave {wave}: edge {edge_index} counter must be an integer")
        src_id, src_position = _resolve_endpoint(
            raw.get("src"), wave, edge_index, "src", assigned_ids, context
        )
        dst_id, dst_position = _resolve_endpoint(
            raw.get("dst"), wave, edge_index, "dst", assigned_ids, context
        )
        if src_id == dst_id:
            raise JobPayloadError(
                f"wave {wave}: edge {edge_index} is a self-loop "
                f"({raw.get('src')!r} -> {raw.get('dst')!r}) — edges must connect "
                "distinct entities"
            )
        edge_inputs.append(
            models.EdgeInput(src=src_id, dst=dst_id, type=edge_type, counter=counter)
        )
        if src_position is not None:
            wave_positions_in_edges.add(src_position)
            if dst_id in core_ids:
                anchored_positions.add(src_position)
        if dst_position is not None:
            wave_positions_in_edges.add(dst_position)
            if src_id in core_ids:
                anchored_positions.add(dst_position)

    if wave == 1:
        orphans = [p for p in range(len(entity_inputs)) if p not in wave_positions_in_edges]
        reason = "orphan entity(ies) with no edge in the subgraph"
    else:
        orphans = [p for p in range(len(entity_inputs)) if p not in anchored_positions]
        reason = "orphan entity(ies) with no edge to the core world"
    if orphans:
        names = ", ".join(f"{entity_inputs[p].name!r} (E{p})" for p in orphans)
        if wave == 2:
            names += f" — core anchors are C0..C{core_count - 1} (the visible core)"
        raise JobPayloadError(f"wave {wave}: {reason}: {names}")

    return entity_inputs, edge_inputs


def _ref_index(ref: Any, prefix: str, *, wave: int, edge_index: int, side: str) -> int:
    """Parse a canonical ``prefix<index>`` endpoint ref into its index.

    The index must be canonical decimal — ``E0``/``N3``/``C10`` are
    valid, ``E01``/``N007``/``C+1`` are rejected. This is the single ref
    parser for edge endpoints; entity refs are additionally required to
    be positional (checked in the entity loop).
    """
    what = f"edge {edge_index} {side}"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        raise JobPayloadError(f"wave {wave}: {what} ref must be {prefix}<index>, got {ref!r}")
    digits = ref[len(prefix) :]
    if not digits.isdigit():
        raise JobPayloadError(f"wave {wave}: {what} ref must be {prefix}<index>, got {ref!r}")
    index = int(digits)
    if str(index) != digits:
        raise JobPayloadError(f"wave {wave}: {what} ref must be {prefix}<index>, got {ref!r}")
    return index


def _resolve_endpoint(
    ref: Any,
    wave: int,
    edge_index: int,
    side: str,
    assigned_ids: Sequence[str],
    context: Sequence[models.Entity],
) -> tuple[str, int | None]:
    """Map an edge endpoint ref to an entity ULID, or reject.

    Wave refs (``E<index>`` for wave 1, ``N<index>`` for wave 2) resolve
    into the wave's runner-assigned ULIDs and return their position;
    context refs (``C<index>``, wave 2 only) resolve into the committed
    context and return ``None`` (a context endpoint is never a wave
    position). A malformed, non-canonical, or out-of-range ref is a
    ``JobPayloadError`` naming the wave, edge, and side.
    """
    prefix = "E" if wave == 1 else "N"
    if isinstance(ref, str) and ref.startswith(prefix):
        position = _ref_index(ref, prefix, wave=wave, edge_index=edge_index, side=side)
        if position >= len(assigned_ids):
            raise JobPayloadError(
                f"wave {wave}: edge {edge_index} {side} {ref!r} names no entity in the wave"
            )
        return assigned_ids[position], position
    if wave == 2 and isinstance(ref, str) and ref.startswith("C"):
        position = _ref_index(ref, "C", wave=wave, edge_index=edge_index, side=side)
        if position >= len(context):
            raise JobPayloadError(
                f"wave {wave}: edge {edge_index} {side} {ref!r} names no context entity"
            )
        return context[position].id, None
    suffix = " or C<index>" if wave == 2 else ""
    raise JobPayloadError(
        f"wave {wave}: edge {edge_index} {side} ref {ref!r} must be {prefix}<index>{suffix}"
    )


def _check_sections(payload: dict[str, Any]) -> None:
    """The payload section lists must be lists of strings (validated at
    enqueue time; re-checked so a tampered payload fails loudly, never a
    raw type error mid-run)."""
    for section in SECTION_NAMES:
        entries = payload.get(section, [])
        if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
            raise JobPayloadError(
                f"build_in: payload section {section!r} must be a list of strings"
            )


def _entity_ulids(entities: Sequence[models.EntityInput]) -> list[str]:
    """The runner-assigned ULIDs of a validated wave (``_validate_subgraph``
    always generates one per entity, so the optional field is never None)."""
    return [cast(str, entity.id) for entity in entities]


def _wave_result(
    wave: int,
    revision_id: str,
    entities: Sequence[models.EntityInput],
    edges: Sequence[models.EdgeInput],
) -> dict[str, Any]:
    """The per-wave entry of ``complete_job``'s result contract."""
    return {
        "wave": wave,
        "revision_id": revision_id,
        "entities": len(entities),
        "edges": len(edges),
        "entity_ids": _entity_ulids(entities),
    }


def _job_still_running(job: models.Job) -> bool:
    """Cancel-race poll (same pattern as the text path, worker.py): a job
    cancelled after claim must not be completed or failed by the runner.
    True when the job is still running (or the status read failed — a
    status error must never wedge the queue)."""
    try:
        state, _position = job_status(job.id)
    except Exception:  # noqa: BLE001 - never wedge the queue on a status error
        logger.exception("worker state check failed for job %s", job.id)
        return True
    return state is None or state.state == "running"
