"""The build-in runner (spec-2.3 + spec-2.4): one job, two internal waves.

Wave 1 digests the named sections (places, factions, key figures) into a
fully networked core subgraph of ``character``/``faction``/``place``
entities + typed edges and commits it atomically (one revision); every
wave-1 character must first carry the full AR24 record and a valid minimal
stat block, each repaired in at most one bounded pass (AR25). Wave 2
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

import dataclasses
import json
import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from app.core import ids
from app.core.settings import LLMSettings
from app.pipeline.budget import CallBudget
from app.pipeline.fencing import json_error, parse_json_object
from app.pipeline.fencing import strip_fence as _strip_fence
from app.pipeline.knowledge import ROLES
from app.pipeline.retrieval import retrieve_neighborhood, serialize_context
from app.pipeline.statblocks import (
    apply_stat_repairs,
    build_stat_repair_prompt,
    canonicalize_action_damage,
    collect_stat_issues,
    conform_stat_power,
    parse_stat_repair_output,
    spells_reference_text,
    stat_block_rules_text,
    stat_failure_message,
    strip_noncharacter_stat_blocks,
)
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
from app.store.candidates import (
    BOSS_FIELDS,
    IDENTITY_FIELDS,
    LORE_FIELDS,
    WORLD_INTEGRATION_FIELDS,
    canonicalize_reaction_matrix,
    payload_section_violations,
)
from app.store.db import session_scope
from app.store.read import campaign_seed, latest_revision

logger = logging.getLogger(__name__)

#: Entity kinds the build-in output contract accepts (spec-2.3).
ENTITY_KINDS: frozenset[str] = frozenset({"character", "faction", "place"})


def _character_record_lines() -> list[str]:
    """The AR24 full-record contract, derived from the store's section
    constants — ONE shape definition, shared with the generate/accept
    validators (``payload_section_violations``). Dogfood fix 2026-09-09:
    build-in characters used to commit with only a stat block — no
    appearance, no lore sections, and sheets that read inconsistently.
    The flat JSON-literal shape is the one proven on the generate path:
    prose labels ("identity anchor: …") nudged the small model to nest
    those fields under an ``identity`` object the validator never sees."""
    anchor = ", ".join(f'"{field}": "..."' for field in IDENTITY_FIELDS if field != "level_cr")
    lore = ", ".join(f'"{field}": "..."' for field in LORE_FIELDS if field != "appearance")
    world = ", ".join(f'"{field}": "..."' for field in WORLD_INTEGRATION_FIELDS)
    boss = ", ".join(f'"{field}": "..."' for field in BOSS_FIELDS)
    return [
        "CHARACTER RECORDS (AR24)",
        "Every character's data is ONE FLAT JSON object — every named field",
        "a non-blank string at the TOP LEVEL of data (never grouped under",
        "'identity'/'lore' subsections), except stat_block and the two",
        "section objects shown below:",
        '  {"name": "<matching the entity name exactly>",',
        '   "role": "NPC|BBEG|Monster",',
        '   "level_cr": display text only ("level <n>" for NPC/BBEG, "CR <n>"',
        "     for Monster — lowercase 'level', uppercase 'CR' exactly; the",
        "     stat_block.identity numerics are authoritative and the export",
        "     derives level/CR from them),",
        f"   {anchor},",
        '   "personality": "...",',
        '   "secret": "a specific concealed fact",',
        '   "rumor": "a concrete in-world claim",',
        '   "party_hook": "a concrete way the party engages the figure",',
        '   "appearance": "painter-grade prose: face, body, clothing, scars, marks",',
        f"   {lore},",
        '   "stat_block": {...per the STAT BLOCK RULES above; its',
        "     identity.role matches the record role...},",
        f'   "world_integration": {{{world}}},',
        "world_integration.reaction_matrix is ONE non-blank string (never a",
        "JSON object/mapping): enumerate each reacting committed entity or",
        "faction as 'C<index>: <reaction>' inside that single string.",
        f'   "boss": {{{boss}}}',
        "  }",
        "boss is CONDITIONAL: required when role is BBEG or Monster (write",
        '"None." in a field that does not apply), and omitted entirely for',
        "NPC — never an empty boss object.",
        "Factions and places carry none of this.",
    ]


#: Build-in payload sections, in prompt order (spec-2.1).
SECTION_NAMES: tuple[str, ...] = ("places", "factions", "key_figures")

#: Wave-2 context bounds (AR6 seed values): the neighborhood of the
#: wave-1 entities, one hop deep, at most 24 entities.
RETRIEVAL_DEPTH = 1
RETRIEVAL_ENTITY_CAP = 24

#: Record-repair chunk bound (chunking spec 2026-09-10): the record gate
#: splits flagged records into per-call chunks of at most this many so
#: every repair response stays short (a 14-record single response drops
#: braces stochastically and its same-size retry fails the same way).
RECORD_REPAIR_CHUNK_SIZE = 4


def _repair_retry_prompt(
    base_prompt: str, bad_text: str, retry_note: str, decode_error: str | None = None
) -> str:
    """The one bounded retry when a repair response is not parseable JSON:
    the same base prompt plus the invalid text, explicit JSON rules, the
    decoder's error line, and one gate-specific shape note (dogfood
    2026-09-09: gemma's record-repair response embedded an excluded
    stat_block whose traits/actions members were bare strings — invalid
    JSON — and the whole wave failed over it). The note is per-gate: the
    record gate excludes the stat block, the stat gate requires it — one
    shared text mis-instructs both. The error line (chunking spec
    2026-09-10: ``JSON error: <str(exc)>`` after the rules) names the exact
    decode failure so the model can fix that spot instead of re-emitting
    the same giant output; omitted only when no decode error is known."""
    lines = [
        base_prompt,
        "",
        "YOUR PREVIOUS RESPONSE COULD NOT BE PARSED AS JSON. Correct it:",
        "return the SAME entries listed above as ONE valid JSON object — nothing else.",
        'JSON rules: every object member is "key": value — a bare string',
        'as an object member is INVALID (e.g. {"A: title"} must become',
        '{"A: title": "..."} or an array of objects); escape any literal',
        'double quote inside a value as \\"; no prose before or after the',
        "object.",
    ]
    if decode_error is not None:
        lines.append(f"JSON error: {decode_error}")
    lines.extend(
        [
            retry_note,
            "",
            "YOUR PREVIOUS INVALID RESPONSE:",
            bad_text.strip(),
        ]
    )
    return "\n".join(lines)


def _run_repair[R: Mapping[int, Any]](
    *,
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    prompt: str,
    parse: Callable[[str, Sequence[int]], R | None],
    positions: Sequence[int],
    label: str,
    retry_note: str,
) -> R:
    """One bounded repair pass with exactly one JSON retry (AR25 stays
    one CONTENT repair — a malformed response is not a content verdict).
    The provider's output is parsed; when it is not parseable as one JSON
    object (prose, truncation, unescaped quotes, a bare-string object
    member — the 2026-09-09 gemma failure), the invalid text is fed back
    with the gate's shape note and the model is asked exactly once more.
    A second malformed response fails the job with a clear message;
    CONTRACT violations (wrong refs, duplicates, missing entries) inside
    well-formed JSON still fail immediately — they are deterministic, not
    JSON noise."""
    repair_text = budget.call(lambda: provider(prompt, settings=settings))
    repaired = parse(repair_text, positions)
    if repaired is not None:
        return repaired
    decode_error = json_error(repair_text)
    retry_text = budget.call(
        lambda: provider(
            _repair_retry_prompt(prompt, repair_text, retry_note, decode_error), settings=settings
        )
    )
    repaired = parse(retry_text, positions)
    if repaired is None:
        snippet = retry_text.strip()[:200]
        raise JobPayloadError(
            f"{label} repair: output was not valid JSON after one retry "
            f"(last output starts: {snippet!r})"
        )
    return repaired


def _enforce_stat_blocks(
    job: models.Job,
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    entities: list[models.EntityInput],
) -> tuple[list[models.EntityInput], bool]:
    """The stat-block gate shared by both waves: collect issues, run exactly
    one bounded repair pass when there are any, re-check. Returns
    ``(entities, cancelled)`` — cancelled True means the job was cancelled
    mid-gate and the caller must stop without committing this wave."""
    # A block whose dice sit in a non-standard ``damage`` key reports ZERO
    # damage to the auditor, which the non-combatant exemption then swallows
    # whole — so the gate must fold it in before it decides anything.
    entities = canonicalize_action_damage(entities)
    issues = collect_stat_issues(entities)
    if not issues:
        return entities, False
    if not _job_still_running(job):
        return entities, True
    repaired = _run_repair(
        budget=budget,
        provider=provider,
        settings=settings,
        prompt=build_stat_repair_prompt(issues),
        parse=parse_stat_repair_output,
        positions=[issue.position for issue in issues],
        label="stat",
        retry_note='Return ONLY a "stat_blocks" list — each entry '
        '{"ref": "E<position>", "stat_block": {...}} with the full corrected block.',
    )
    entities = apply_stat_repairs(entities, repaired)
    remaining = collect_stat_issues(entities)
    if remaining:
        # The repair pass is one LLM shot at arithmetic a model cannot do:
        # measured live, blocks came back 5.5 vs 15-20, 16 vs 27-32 and
        # 50 vs 93-98 — never in band (2026-09-10). Before failing the job,
        # give the block one deterministic chance to meet its own DMG row.
        # Only power-band violations are touched; the model's words, actions
        # and identity survive untouched (see statblocks.conform_power).
        entities = conform_stat_power(entities, remaining)
        remaining = collect_stat_issues(entities)
    if remaining:
        raise JobPayloadError(stat_failure_message(remaining))
    return entities, False


@dataclasses.dataclass(frozen=True)
class _RecordIssue:
    """One wave character whose ``data`` fails the AR24 full-record shape."""

    position: int
    entity: models.EntityInput
    violations: tuple[str, ...]


def _collect_record_issues(
    entities: Sequence[models.EntityInput],
) -> list[_RecordIssue]:
    """Flag every character entity lacking a valid full AR24 record
    (the same shape ``store.candidates.payload_section_violations`` guards
    on the generate path — ONE definition, dogfood fix 2026-09-09)."""
    issues: list[_RecordIssue] = []
    for position, entity in enumerate(entities):
        if entity.kind != "character":
            continue
        violations = payload_section_violations(entity.data)
        if violations:
            issues.append(_RecordIssue(position, entity, tuple(violations)))
    return issues


def _build_record_repair_prompt(issues: Sequence[_RecordIssue]) -> str:
    """The record gate's one bounded repair pass (AR25 semantics, same
    shape as the stat repair): each flagged character's ref, name, current
    record (stat_block excluded — that field is the other gate's domain)
    and violations, plus the shared record contract."""
    flagged: list[str] = []
    for issue in issues:
        current = {k: v for k, v in issue.entity.data.items() if k != "stat_block"}
        violations = "\n".join(f"  - {violation}" for violation in issue.violations)
        flagged.append(
            f"E{issue.position} ({issue.entity.name!r}):\n"
            f"current data: {json.dumps(current, sort_keys=True, separators=(',', ':'))}\n"
            f"violations:\n{violations}"
        )
    return "\n".join(
        [
            "You are completing full character records for a TTRPG world.",
            "Respond with exactly one JSON object — nothing else.",
            "",
            "\n".join(_character_record_lines()),
            "",
            "VIOLATIONS TO FIX",
            "\n\n".join(flagged),
            "",
            "TASK",
            "For EVERY character listed, supply the record fields that are",
            "missing or wrong (a sectioned object may be returned whole).",
            "Invent concrete in-world content that fits the name and the",
            "current data. Use the refs exactly as given.",
            "NEVER return a stat_block or a different name — the stat block",
            "is owned by a separate pass and ANY stat_block in this response",
            "is discarded; including one can invalidate the whole response.",
            "Reply with STRICTLY VALID JSON: every object member is",
            '"key": value — a bare string as an object member (e.g.',
            '{"Tavern Keep: ...", "Steady Hand: ..."}) is INVALID; escape',
            'any literal " inside a value as \\"; nothing but the object,',
            "no surrounding prose.",
            "",
            "OUTPUT CONTRACT",
            'Respond with one JSON object: {"records": [{"ref": "E<position>",',
            '  "data": {...}}, ...]} — exactly one entry per character listed,',
            "one ref per entry, nothing else. The data object is FLAT:",
            '  {"role": "NPC", "level_cr": "level 3", "appearance": "..."} —',
            "never a nested identity/lore subsection.",
        ]
    )


def _parse_record_repair_output(
    text: str, flagged_positions: Sequence[int]
) -> dict[int, dict[str, Any]] | None:
    """Parse the repair response into {position: record patch}.

    Same strictness as the stat repair: EXACTLY the flagged refs, each once;
    a missing, unknown, or duplicate ref fails the job — never a partial
    merge. Returns None when the text is not parseable as one JSON object
    (the gate retries once — ``_run_repair``); a well-formed JSON object
    with the wrong CONTRACT fails immediately."""
    parsed = parse_json_object(text)
    if parsed is None:
        return None
    raw = parsed.get("records")
    if not isinstance(raw, list):
        raise JobPayloadError("record repair: output must have a 'records' list")
    expected = set(flagged_positions)
    repaired: dict[int, dict[str, Any]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise JobPayloadError("record repair: each records entry must be an object")
        ref = entry.get("ref")
        if not isinstance(ref, str) or not ref.startswith("E"):
            raise JobPayloadError(f"record repair: ref must be E<position>, got {ref!r}")
        digits = ref[1:]
        # Length guard first: int() on a 4300+-digit string raises raw
        # ValueError (CPython int-string limit), escaping the JobPayloadError
        # channel as a 500 — positions are wave indices, never this long.
        if len(digits) > 6 or not digits.isdecimal() or str(int(digits)) != digits:
            raise JobPayloadError(f"record repair: ref must be E<position>, got {ref!r}")
        position = int(digits)
        if position not in expected:
            raise JobPayloadError(f"record repair: ref E{position} was not flagged for repair")
        if position in repaired:
            raise JobPayloadError(f"record repair: ref E{position} appears more than once")
        patch = entry.get("data")
        if not isinstance(patch, dict):
            raise JobPayloadError(f"record repair: ref E{position} data must be an object")
        repaired[position] = patch
    missing = sorted(expected - repaired.keys())
    if missing:
        names = ", ".join(f"E{position}" for position in missing)
        raise JobPayloadError(f"record repair: missing repaired records for {names}")
    return repaired


def _apply_record_repairs(
    entities: Sequence[models.EntityInput],
    repaired: Mapping[int, dict[str, Any]],
) -> list[models.EntityInput]:
    """Merge repaired record fields into the validated wave.

    The entity-level name stays authoritative and ``stat_block`` is never
    touched by this gate — only record fields merge, everything else the
    wave validated (edges, kinds, names) is untouched.
    """
    merged: list[models.EntityInput] = []
    for position, entity in enumerate(entities):
        if position in repaired:
            patch = {k: v for k, v in repaired[position].items() if k not in ("stat_block", "name")}
            # The repair patch can carry a mapping-form ``reaction_matrix``
            # too — canonicalize the merged record so the re-check sees the
            # string form (dogfood 2026-09-09, generate's pattern).
            entity = dataclasses.replace(
                entity,
                data=canonicalize_reaction_matrix({**entity.data, **patch}),
            )
        merged.append(entity)
    return merged


def _record_failure_message(wave: int, issues: Sequence[_RecordIssue]) -> str:
    """The fail-event message for a wave still failing records after the repair."""
    parts = [
        f"entity {issue.position} ({issue.entity.name}) fails the character record: "
        + "; ".join(issue.violations)
        for issue in issues
    ]
    return f"wave {wave}: " + " | ".join(parts)


def _collect_name_issues(
    entities: Sequence[models.EntityInput],
) -> list[tuple[int, models.EntityInput]]:
    """Flag every entity whose name is missing or blank — of ANY kind
    (place/faction/character, dogfood fix 2026-09-09: the compact model
    shipped a fully-detailed wave-1 character with no name field at all,
    and the old structural check hard-failed the whole wave over it)."""
    return [
        (position, entity)
        for position, entity in enumerate(entities)
        if not (isinstance(entity.name, str) and entity.name.strip())
    ]


def _build_name_repair_prompt(issues: Sequence[tuple[int, models.EntityInput]]) -> str:
    """The name gate's one bounded repair pass (AR25 semantics, same
    shape as the stat/record repairs): each flagged entity's ref, kind,
    and the narrative hints that carry its identity (text +
    personality/appearance/role, stat_block and empty name excluded) so
    the supplied name fits the entity."""
    flagged: list[str] = []
    for position, entity in issues:
        excerpt: dict[str, Any] = {}
        if entity.text:
            excerpt["text"] = entity.text
        for key in ("role", "personality", "appearance", "background"):
            value = entity.data.get(key) if isinstance(entity.data, dict) else None
            if isinstance(value, str) and value.strip():
                excerpt[key] = value
        excerpt_json = json.dumps(excerpt, sort_keys=True, separators=(",", ":"))
        flagged.append(f"E{position} ({entity.kind}):\ncurrent record: {excerpt_json}")
    return "\n".join(
        [
            "You are naming entities for a TTRPG world-build-in.",
            "Respond with exactly one JSON object — nothing else.",
            "",
            "TASK",
            "Each entity listed below was created without a name. Supply ONE",
            "concise, evocative name per entity, fitting its kind and the",
            "current record (a barkeep character might be 'Barkeep Whostbos',",
            "an old archivist 'Old Ferrick').",
            "",
            "\n\n".join(flagged),
            "",
            "OUTPUT CONTRACT",
            'Respond with one JSON object: {"names": [{"ref": "E<position>",',
            '  "name": "..."}, ...]} — exactly one entry per entity listed,',
            "one ref per entry, nothing else. The name must be a non-blank string.",
        ]
    )


def _parse_name_repair_output(text: str, flagged_positions: Sequence[int]) -> dict[int, str] | None:
    """Parse the name repair response into {position: name}.

    Same strictness as the record repair: EXACTLY the flagged refs, each
    once, each with a non-blank string name; a missing, unknown,
    duplicate, or blank ref fails the job — never a partial merge.
    Returns None when the text is not parseable as one JSON object (the
    gate retries once — ``_run_repair``)."""
    parsed = parse_json_object(text)
    if parsed is None:
        return None
    raw = parsed.get("names")
    if not isinstance(raw, list):
        raise JobPayloadError("name repair: output must have a 'names' list")
    expected = set(flagged_positions)
    repaired: dict[int, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise JobPayloadError("name repair: each names entry must be an object")
        ref = entry.get("ref")
        if not isinstance(ref, str) or not ref.startswith("E"):
            raise JobPayloadError(f"name repair: ref must be E<position>, got {ref!r}")
        digits = ref[1:]
        # Same length guard as the record parser above: a huge digit string
        # must fail as a contract violation, not a raw ValueError.
        if len(digits) > 6 or not digits.isdecimal() or str(int(digits)) != digits:
            raise JobPayloadError(f"name repair: ref must be E<position>, got {ref!r}")
        position = int(digits)
        if position not in expected:
            raise JobPayloadError(f"name repair: ref E{position} was not flagged for repair")
        if position in repaired:
            raise JobPayloadError(f"name repair: ref E{position} appears more than once")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise JobPayloadError(f"name repair: ref E{position} name must be a non-blank string")
        repaired[position] = name.strip()
    missing = sorted(expected - repaired.keys())
    if missing:
        names = ", ".join(f"E{position}" for position in missing)
        raise JobPayloadError(f"name repair: missing repaired names for {names}")
    return repaired


def _apply_name_repairs(
    entities: Sequence[models.EntityInput],
    repaired: Mapping[int, str],
) -> list[models.EntityInput]:
    """Apply repaired names: the entity-level name always; for a
    character the record's ``name`` too (the record contract requires it
    to match). Everything else is untouched."""
    merged: list[models.EntityInput] = []
    for position, entity in enumerate(entities):
        if position in repaired:
            name = repaired[position]
            data = {**entity.data, "name": name} if entity.kind == "character" else entity.data
            entity = dataclasses.replace(entity, name=name, data=data)
        merged.append(entity)
    return merged


def _name_failure_message(wave: int, remaining: Sequence[tuple[int, models.EntityInput]]) -> str:
    """The fail-event message for a wave still carrying nameless entities
    after the repair."""
    parts = [
        f"entity {position} ({entity.kind}) still has no non-blank name"
        for position, entity in remaining
    ]
    return f"wave {wave}: " + " | ".join(parts)


def _enforce_entity_names(
    job: models.Job,
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    entities: list[models.EntityInput],
    wave: int,
) -> tuple[list[models.EntityInput], bool]:
    """The structural-name gate shared by both waves (dogfood fix
    2026-09-09): collect nameless entities, run exactly one bounded
    repair pass, re-check. Returns ``(entities, cancelled)`` — cancelled
    True means the job was cancelled mid-gate and the caller must stop
    without committing this wave. Runs BEFORE the record gate so the
    record repair's NAME-verbatim rule sees a real name."""
    issues = _collect_name_issues(entities)
    if not issues:
        return entities, False
    if not _job_still_running(job):
        return entities, True
    repaired = _run_repair(
        budget=budget,
        provider=provider,
        settings=settings,
        prompt=_build_name_repair_prompt(issues),
        parse=_parse_name_repair_output,
        positions=[position for position, _entity in issues],
        label="name",
        retry_note='Return ONLY a "names" list — each entry '
        '{"ref": "E<position>", "name": "..."} with one non-blank name.',
    )
    entities = _apply_name_repairs(entities, repaired)
    remaining = _collect_name_issues(entities)
    if remaining:
        raise JobPayloadError(_name_failure_message(wave, remaining))
    return entities, False


def _enforce_character_records(
    job: models.Job,
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    entities: list[models.EntityInput],
    wave: int,
) -> tuple[list[models.EntityInput], bool]:
    """The character-record gate shared by both waves: collect violations,
    repair them in bounded per-chunk calls, re-check. Each chunk holds at
    most ``RECORD_REPAIR_CHUNK_SIZE`` flagged records and reuses the same
    prompt builder, parser, and one-retry helper — a chunk's prompt is
    byte-identical in shape to the old single prompt for that subset, just
    shorter, so a dropped brace fails only its chunk and only that chunk
    is re-called. Merged patches apply together; contract violations
    inside well-formed JSON still fail immediately. Returns
    ``(entities, cancelled)`` — cancelled True means the job was
    cancelled mid-gate and the caller must stop without committing this
    wave. Runs BEFORE the stat gate so a repaired role is what the stat
    block is next checked against."""
    issues = _collect_record_issues(entities)
    if not issues:
        return entities, False
    if not _job_still_running(job):
        return entities, True
    chunked = len(issues) > RECORD_REPAIR_CHUNK_SIZE
    merged: dict[int, dict[str, Any]] = {}
    for start in range(0, len(issues), RECORD_REPAIR_CHUNK_SIZE):
        if not _job_still_running(job):
            return entities, True
        chunk = issues[start : start + RECORD_REPAIR_CHUNK_SIZE]
        label = "record"
        if chunked:
            refs = ", ".join(f"E{issue.position}" for issue in chunk)
            label = f"record chunk {refs}"
        repaired = _run_repair(
            budget=budget,
            provider=provider,
            settings=settings,
            prompt=_build_record_repair_prompt(chunk),
            parse=_parse_record_repair_output,
            positions=[issue.position for issue in chunk],
            label=label,
            retry_note="For records, NEVER include a stat_block — it belongs to a "
            "separate pass and is discarded here.",
        )
        merged.update(repaired)
    entities = _apply_record_repairs(entities, merged)
    remaining = _collect_record_issues(entities)
    if remaining:
        raise JobPayloadError(_record_failure_message(wave, remaining))
    return entities, False


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
    Wave 2 alone has a repair pass for structure (spec: wave-2 orphan
    re-prompt): a wave-2 subgraph that is valid except for orphan entities
    gets one full re-emit naming the orphans; a still-orphan (or
    entity-dropping) re-emit fails the job with wave 1 committed.
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
    # Only characters carry stat blocks (AR24, spec-2.4 review decision): a
    # stray block from a faction/place is stripped before validation or commit.
    entities_1 = strip_noncharacter_stat_blocks(entities_1)
    # Structural-name enforcement (dogfood fix 2026-09-09): a wave entity
    # missing its name (gemma shipped a fully-detailed character with no
    # name field) gets one bounded repair pass BEFORE the record gate, so
    # the record repair's name-verbatim rule sees a real name.
    entities_1, cancelled = _enforce_entity_names(
        job, budget, provider, settings, entities_1, wave=1
    )
    if cancelled:
        return
    # Record enforcement (dogfood fix 2026-09-09): every wave character
    # carries the full AR24 record — violations go through exactly one
    # bounded repair pass, same AR25 semantics as the stat gate below. It
    # runs FIRST so a repaired role is what the stat block is checked against.
    entities_1, cancelled = _enforce_character_records(
        job, budget, provider, settings, entities_1, wave=1
    )
    if cancelled:
        return
    # Stat-block enforcement (AR24/AR25, spec-2.4): every character must carry
    # a valid minimal stat block before the wave commits — or exactly one
    # bounded repair pass; a block still invalid after the repair fails the
    # job with an error event, zero commits.
    entities_1, cancelled = _enforce_stat_blocks(job, budget, provider, settings, entities_1)
    if cancelled:
        return
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
        first_names = _wave_entity_names(parsed_2)
        try:
            entities_2, edges_2 = _validate_subgraph(
                2, parsed_2, context=context_entities, core_count=core_count
            )
        # NOTE: this handler must precede any `except JobPayloadError` —
        # _Wave2OrphanError subclasses it, so a broader handler first would
        # swallow the retry signal and orphans would fail immediately.
        except _Wave2OrphanError as exc:
            # ORPHAN_RETRY (spec: wave-2 orphan re-prompt) — the only
            # _validate_subgraph rejection with a repair pass: one full
            # re-emit naming the orphans, through the same budget and the
            # same name/record/stat gates below. A still-orphan re-emit
            # raises again and fails the job with wave 1 committed; any
            # other rejection was never caught and stays immediate.
            if not _job_still_running(job):
                return
            retry_prompt = _build_wave2_orphan_retry_prompt(prompt_2, exc.orphans, core_count)
            text_2 = budget.call(lambda: provider(retry_prompt, settings=settings))
            parsed_2 = parse_build_output(text_2, wave=2)
            _raise_on_reemit_entity_change(first_names, _wave_entity_names(parsed_2))
            entities_2, edges_2 = _validate_subgraph(
                2, parsed_2, context=context_entities, core_count=core_count
            )
        # The same gates as wave 1 (dogfood fix 2026-09-09): wave-2 names
        # (name gate), then characters carry full AR24 records (record
        # gate, then the stat-block gate) before committing.
        entities_2 = strip_noncharacter_stat_blocks(entities_2)
        entities_2, cancelled = _enforce_entity_names(
            job, budget, provider, settings, entities_2, wave=2
        )
        if cancelled:
            return
        entities_2, cancelled = _enforce_character_records(
            job, budget, provider, settings, entities_2, wave=2
        )
        if cancelled:
            return
        entities_2, cancelled = _enforce_stat_blocks(job, budget, provider, settings, entities_2)
        if cancelled:
            return
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
    notes = payload.get("notes", "")
    notes = notes.strip() if isinstance(notes, str) else ""
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
        # Wave 1 builds the entities named above, so a DM note that fixes a
        # level, role, or power for one of them has to land HERE — a note
        # saying "sanberi is level 18" that only wave 2 ever saw is why a
        # level-18 key figure got a level-2 record and an unreachable DPR
        # band (2026-09-10). New subjects stay wave 2's job.
        *(
            (
                "",
                "DM NOTES (authoritative directives about the entities above: any",
                "level, role, class, or power named here OVERRIDES what you would",
                "otherwise infer. Do not create entities for new subjects named only",
                "here — a later wave digests those.)",
                notes,
            )
            if notes
            else ()
        ),
        "",
        "TASK",
        "Create the core subgraph: entities for the notable key figures, places, and",
        "factions in the submitted sections, connected by typed directed edges",
        "(characters for key figures, places for places, factions for factions).",
        "Every entity must appear in at least one edge within this subgraph — no orphans.",
        "Edges must connect two different entities — no self-loops.",
        "",
        "STAT BLOCKS",
        stat_block_rules_text(),
        "",
        spells_reference_text(),
        "",
        *_character_record_lines(),
        "",
        "OUTPUT CONTRACT",
        'Respond with one JSON object: {"entities": [...], "edges": [...]}.',
        "A character keeps its whole record INSIDE the data object — never beside it:",
        '{"ref": "E2", "kind": "character", "name": "Sanberi", "text": "optional narrative",',
        '  "data": {"role": "NPC", "level_cr": "level 18", "race_type": "Human",',
        '           "class_profession": "Paladin", "alignment": "LG", "personality": "...",',
        '           "secret": "...", "rumor": "...", "party_hook": "...", "appearance": "...",',
        '           "background": "...", "goals": "...", "relationships": "...",',
        '           "voice_style": "...", "catchphrases": "...",',
        '           "world_integration": {"reputation": "...", "factions": "...",',
        '                                 "current_location": "...", "reaction_matrix": "...",',
        '                                 "on_defeat": "..."},',
        '           "stat_block": {the STAT BLOCK RULES above}}}',
        "role, level_cr, race_type, class_profession, alignment, and stat_block are keys",
        "of data — never keys of the entity itself. An entity carrying them at the top",
        "level loses them: the record is read from data and nowhere else.",
        'A place or faction is flat: {"ref": "E0", "kind": "place", "name": "City of Gallorb",',
        '  "text": "the hard truth about it"} — data is optional for those two kinds.',
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
        "STAT BLOCKS",
        stat_block_rules_text(),
        "",
        spells_reference_text(),
        "",
        *_character_record_lines(),
        "",
        "OUTPUT CONTRACT",
        'Respond with one JSON object: {"entities": [...], "edges": [...]}.',
        "A character keeps its whole record INSIDE the data object — never beside it:",
        '{"ref": "N1", "kind": "character", "name": "Captain Harlow", "text": "optional",',
        '  "data": {"role": "NPC", "level_cr": "level 6", "race_type": "Human",',
        '           "class_profession": "Fighter", "alignment": "LN", "personality": "...",',
        '           "secret": "...", "rumor": "...", "party_hook": "...", "appearance": "...",',
        '           "background": "...", "goals": "...", "relationships": "...",',
        '           "voice_style": "...", "catchphrases": "...",',
        '           "world_integration": {"reputation": "...", "factions": "...",',
        '                                 "current_location": "...", "reaction_matrix": "...",',
        '                                 "on_defeat": "..."},',
        '           "stat_block": {the STAT BLOCK RULES above}}}',
        'kind is exactly "character", "faction", or "place" — never a role word like',
        '"Monster" or "NPC": the role belongs to data.role, and an entity carrying',
        "record keys at the top level loses them — the record is read from data alone.",
        'A place or faction is flat: {"ref": "N0", "kind": "faction", "name": "The Guild",',
        '  "text": "the hard truth about it"} — data is optional for those two kinds.',
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


def _build_wave2_orphan_retry_prompt(
    base_prompt: str, orphans: Sequence[tuple[str, int]], core_count: int
) -> str:
    """The one bounded wave-2 orphan re-prompt (spec: wave-2 orphan
    re-prompt): the base wave-2 prompt plus a PREVIOUS RESPONSE ORPHANS
    line naming each orphan by name and N ref, so the re-emit wires every
    new entity to the core. The re-emit is a full {"entities","edges"}
    object with the refs exactly as given."""
    named = ", ".join(f"{name!r} (N{position})" for name, position in orphans)
    core_label = f"C0..C{core_count - 1}" if core_count > 0 else "no visible core"
    lines = [
        base_prompt,
        "",
        "PREVIOUS RESPONSE ORPHANS: "
        f"{named} — every new entity MUST have >= 1 edge to a CORE entity "
        f"({core_label}) — keep entities in the same order with the same refs; "
        "re-emit the full "
        '{"entities","edges"} object with the refs exactly as given.',
    ]
    return "\n".join(lines)


def _wave_entity_names(parsed: dict[str, Any]) -> list[str]:
    """Entity names of a parsed wave in order (missing/non-string names read
    as ``""``) — the re-emit drop guard compares these multisets."""
    names = []
    for raw in parsed.get("entities", []):
        name = raw.get("name") if isinstance(raw, dict) else None
        names.append(name if isinstance(name, str) else "")
    return names


def _raise_on_reemit_entity_change(first_names: Sequence[str], second_names: Sequence[str]) -> None:
    """The re-emit drop guard (step-04 review): the re-emit must carry the
    same entity name multiset as the first attempt — a model that drops the
    orphan instead of wiring it fails loudly here (no third attempt, wave 1
    stays committed), naming what changed."""
    if Counter(second_names) == Counter(first_names):
        return
    dropped = sorted((Counter(first_names) - Counter(second_names)).elements())
    added = sorted((Counter(second_names) - Counter(first_names)).elements())
    parts = []
    if dropped:
        parts.append("dropped " + ", ".join(repr(name) for name in dropped))
    if added:
        parts.append("added " + ", ".join(repr(name) for name in added))
    raise JobPayloadError(
        f"wave 2: re-emit changed the wave entities ({'; '.join(parts)}) — "
        "re-emit the full wave with every entity wired to the core"
    )


class _Wave2OrphanError(JobPayloadError):
    """The orphan-only retry signal for wave 2 (spec: wave-2 orphan
    re-prompt): raised instead of a plain ``JobPayloadError`` when a wave-2
    subgraph is valid except for entities with no edge to the core. The
    wave-2 runner catches exactly this type for its one bounded re-emit;
    every other ``_validate_subgraph`` rejection stays immediate. Carries
    the orphan ``(name, position)`` pairs so the re-prompt can name them.
    """

    def __init__(self, message: str, *, orphans: list[tuple[str, int]]) -> None:
        super().__init__(message)
        self.orphans = orphans


#: Every key an AR24 character record owns. A model that writes them BESIDE
#: ``data`` instead of inside it has still written the record — the shape is
#: unambiguous, so it is relocated rather than dropped. Dropping it is what
#: let the record gate re-invent an entirely different character from scraps:
#: dogfood 2026-09-11, a key figure submitted as "the hero paladin sanberi"
#: committed as a level-2 Cartographer because its wave-1 record was flat.
_RECORD_KEYS: frozenset[str] = (
    frozenset({"role", "stat_block", "world_integration", "boss"})
    | frozenset(IDENTITY_FIELDS)
    | frozenset(LORE_FIELDS)
    | frozenset(WORLD_INTEGRATION_FIELDS)
    | frozenset(BOSS_FIELDS)
    | frozenset({"personality", "secret", "rumor", "party_hook"})
)


def canonicalize_entity_record(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """Fold a character record written beside ``data`` into it.

    Returns the merged record (or ``None`` when the entity wrote none of the
    record keys at all, leaving ``data`` exactly as it was). Keys already
    inside ``data`` win: the nested form is the contract, and a model that
    wrote both meant the nested one. Same spirit as
    ``canonicalize_reaction_matrix`` — repair the shape the model certainly
    meant instead of discarding work it actually did.
    """
    stray = {key: raw[key] for key in _RECORD_KEYS if key in raw}
    if not stray:
        return None
    nested = raw.get("data")
    if isinstance(nested, dict):
        return {**stray, **nested}
    return stray


def canonicalize_entity_kind(value: Any) -> tuple[str, str | None] | None:
    """Fold an entity kind to its contract form, tolerating model slips.

    The contract kinds are lowercase (``character``/``faction``/``place``).
    Two slips are unambiguous and worth absorbing rather than killing a
    whole wave over (dogfood 2026-09-10: ``kind: "monster"`` failed every
    build-in at wave 2):

    * a case variant of a contract kind ("Place" -> "place");
    * the ROLE written in ``kind`` ("Monster", "NPC", "BBEG") — every entry
      of ``ROLES`` is a character-kind entity, so the reading is forced, and
      the role is returned so the caller can seed ``data.role`` from it.

    Returns ``(kind, role)`` where ``role`` is ``None`` unless the value was
    a role word, or ``None`` when the value names neither. Same spirit as
    ``canonicalize_reaction_matrix``: repair the shape the model certainly
    meant, never guess at one it did not.
    """
    if not isinstance(value, str):
        return None
    folded = value.strip().lower()
    if folded in ENTITY_KINDS:
        return folded, None
    for role in ROLES:
        if folded == role.lower():
            return "character", role
    return None


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
    (``context[0:core_count]`` — the wave-1 entities). A wave-2 subgraph
    that is valid except for such orphans raises ``_Wave2OrphanError``
    (never a plain ``JobPayloadError``) carrying the orphan ``(name,
    position)`` pairs — the runner's one bounded re-emit catches exactly
    that type; every other rejection stays immediate.
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
        canonical = canonicalize_entity_kind(raw.get("kind"))
        if canonical is None:
            raise JobPayloadError(
                f"wave {wave}: entity {position} kind {raw.get('kind')!r} "
                f"not in {sorted(ENTITY_KINDS)}"
            )
        kind, role_from_kind = canonical
        text = raw.get("text")
        if text is not None and not isinstance(text, str):
            raise JobPayloadError(f"wave {wave}: entity {position} text must be a string")
        data = raw.get("data")
        if data is not None and not isinstance(data, dict):
            raise JobPayloadError(f"wave {wave}: entity {position} data must be an object")
        name = raw.get("name")
        # A missing/blank/mis-typed name is NOT a hard-fail here (dogfood
        # 2026-09-09: the compact model shipped a fully-detailed character
        # with no name field at all, killing the whole wave). It flows to
        # the bounded name-repair gate (_enforce_entity_names) — but a
        # character whose record carries a name (the CHARACTER RECORDS
        # contract requires data.name == entity name) falls back to it
        # right here, so the repair pass is never burned on a name the
        # model already wrote once. ``""`` keeps EntityInput.name a string
        # (the column is non-nullable); the gate runs before any commit.
        clean_name = name.strip() if isinstance(name, str) else ""
        if kind == "character" and not clean_name:
            record_name = data.get("name") if isinstance(data, dict) else None
            if isinstance(record_name, str) and record_name.strip():
                clean_name = record_name.strip()
        if kind == "character":
            # The entity-level name is authoritative inside the record too
            # (generate parity: committed character data carries "name").
            # The AR24 record SHAPE is not checked here: violations flow to
            # the bounded repair pass in _enforce_character_records below.
            # ``reaction_matrix`` is canonicalized to its string form first
            # (dogfood 2026-09-09: the compact model ships a
            # {"C<i>": "<reaction>"} mapping, which the shared validator
            # would flag and burn the record gate's one repair pass on).
            relocated = canonicalize_entity_record(raw)
            data = relocated if relocated is not None else data
            data = canonicalize_reaction_matrix({**(data or {}), "name": clean_name})
            if role_from_kind is not None:
                # The model named the role in `kind` instead of the record:
                # keep that information where the record and gates read it.
                existing_role = data.get("role")
                if not (isinstance(existing_role, str) and existing_role.strip()):
                    data["role"] = role_from_kind
        entity_id = ids.new_id()
        assigned_ids.append(entity_id)
        entity_inputs.append(
            models.EntityInput(
                id=entity_id,
                kind=kind,
                name=clean_name,
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
        names = ", ".join(f"{entity_inputs[p].name!r} ({prefix}{p})" for p in orphans)
        if wave == 2:
            if core_count > 0:
                names += f" — core anchors are C0..C{core_count - 1} (the visible core)"
            else:
                names += " — no visible core"
            raise _Wave2OrphanError(
                f"wave {wave}: {reason}: {names}",
                orphans=[(entity_inputs[p].name, p) for p in orphans],
            )
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
