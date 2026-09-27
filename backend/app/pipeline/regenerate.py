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
from app.pipeline.build_in import _enforce_stat_blocks
from app.pipeline.fencing import strip_fence
from app.pipeline.generate import _job_still_running
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
    DIAL_LEVELS,
    EDGE_TYPES,
    JobStateConflictError,
    complete_job,
    discard_candidates,
    edge_counter_semantic,
    models,
    report_progress,
)

# One call site (the candidate-target re-roll's same-transaction splice —
# spec-3.6 RE_ROLL_AFTER_EDIT) justifies the private import: the public
# ``replace_candidate_payload`` owns its own session, and payload + base
# snapshot must reference the same moment (accept_candidate -> _commit
# precedent).
from app.store.candidates import (
    REGEN_SECTIONS,
    _replace_candidate_payload,
    _stage_candidates,
    canonicalize_reaction_matrix,
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


def _parse_regenerate_candidates(text: str) -> list[Any]:
    """The single-candidate envelope, tolerating a bare record.

    regenerate expects exactly ONE record, so a top-level object that is not
    the ``{"candidates": [...]}`` envelope is unambiguous — it IS the record.
    Measured 2026-09-11: on a whole-character re-roll the model reliably drops
    the envelope and returns the bare record, which hard-failed every whole
    re-roll with "output must have a 'candidates' list" (two live jobs).
    """
    stripped = strip_fence(text)
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise JobPayloadError(f"regenerate: output is not valid JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise JobPayloadError("regenerate: output must be a JSON object")
    raw = parsed.get("candidates")
    if isinstance(raw, list):
        return raw
    return [parsed]


def _regenerate_retry_prompt(base_prompt: str, bad_text: str, decode_error: str) -> str:
    """The one bounded retry when a re-roll is not parseable JSON.

    Measured 2026-09-11: the model drops the envelope on a whole re-roll and
    drops a closing brace on a per-section one. Both are single-token slips,
    and the decoder's own error line is what tells the model WHERE to look —
    re-sending the identical prompt would just sample the same slip again.
    Mirrors the build-in repair retry (spec-3.5's one bounded pass).
    """
    return "\n".join(
        [
            base_prompt,
            "",
            "YOUR PREVIOUS RESPONSE COULD NOT BE PARSED AS JSON. Correct it:",
            "return the SAME record again as ONE valid JSON object — nothing else.",
            'Shape: {"candidates": [<the full sectioned record>]} — exactly one entry.',
            'Close every brace and bracket, and escape any literal double quote as \\".',
            f"JSON error: {decode_error}",
            "Previous response:",
            bad_text.strip()[:4000],
        ]
    )


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
        or bool(set(payload) - {"target", "sections", "guide", "dial"})
        or not isinstance(payload.get("target"), dict)
    ):
        raise JobPayloadError(
            "regenerate: job payload must be "
            "{'target': ..., 'sections': ...|null, 'guide'?: str, 'dial'?: <dial level>}"
        )
    target = payload["target"]
    if set(target) != {"kind", "id"}:
        raise JobPayloadError("regenerate: target must be {'kind': ..., 'id': ...}")
    target_kind = target["kind"]
    target_id = target["id"]
    if target_kind not in ("entity", "candidate") or not isinstance(target_id, str):
        raise JobPayloadError("regenerate: target kind/id are malformed")
    # AD-38 shaped request: guide + dial ride the envelope; the enqueue
    # gate is not trusted across queue delay — the same checks re-run here
    # before any call (dial stays inside the closed registry, never
    # invented).
    guide = payload.get("guide")
    if guide is not None and (not isinstance(guide, str) or not guide.strip()):
        raise JobPayloadError("regenerate: guide must be a non-blank string when present")
    dial = payload.get("dial")
    if dial is not None and dial not in DIAL_LEVELS:
        raise JobPayloadError(
            f"regenerate: dial {dial!r} is outside the closed set: {sorted(DIAL_LEVELS)}"
        )

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
    prompt = build_regenerate_prompt(
        seed,
        record,
        requested,
        (context_entities, context_edges),
        guide=guide,
        dial=dial,
    )
    text = budget.call(lambda: provider(prompt, settings=settings))

    try:
        parsed = _parse_regenerate_candidates(text)
    except JobPayloadError as exc:
        # One bounded re-emit: a single-token JSON slip must not cost the DM
        # a whole re-roll (the build-in repair gates' precedent).
        if not _job_still_running(job):
            return
        retry_prompt = _regenerate_retry_prompt(prompt, text, str(exc))
        text = budget.call(lambda: provider(retry_prompt, settings=settings))
        parsed = _parse_regenerate_candidates(text)
    # The regenerate contract is a single-candidate envelope: exactly one
    # record in the standard {"candidates": [...]} shape (or the bare record
    # the model substitutes for it — see _parse_regenerate_candidates).
    if len(parsed) != 1:
        raise JobPayloadError(f"regenerate: expected exactly 1 candidate, got {len(parsed)}")
    raw = parsed[0]
    if not isinstance(raw, dict):
        raise JobPayloadError("regenerate: candidate must be an object")
    # Dogfood 2026-09-09 (generate's pattern): the compact model renders
    # ``world_integration.reaction_matrix`` as a ``{"C<i>": "<reaction>"}``
    # mapping — canonicalize the re-rolled record BEFORE validation so a
    # world_integration re-roll of a previously-canonical record cannot
    # hard-fail on the shape; a string matrix passes through.
    raw = canonicalize_reaction_matrix(raw)
    _validate_output(raw, record, requested)

    # The AR25 stat gate runs on THIS path too (2026-09-11): a re-rolled
    # stat_block is subject to exactly the same contract as a build-in one.
    # Skipping it let a level-17 paladin's re-roll ship with prose-only
    # attacks — the auditor read ZERO damage, the non-combatant exemption
    # swallowed the block, and the DM got a hero who cannot fight.
    # Scoped to re-rolls that actually touch the block: a DM re-rolling
    # `secret` must not have the job fail over an unrelated weak block.
    if "stat_block" in requested and isinstance(raw.get("stat_block"), dict):
        checked, cancelled = _enforce_stat_blocks(
            job,
            budget,
            provider,
            settings,
            [
                models.EntityInput(
                    kind="character", name=str(raw.get("name") or ""), text=None, data=raw, id=None
                )
            ],
        )
        if cancelled:
            return
        raw = checked[0].data
        _validate_output(raw, record, requested)

    # Cancel-race poll before the write: a cancel during the call/validation
    # must not stage nor replace anything.
    if not _job_still_running(job):
        return
    if target_kind == "candidate":
        # Spec-3.6 RE_ROLL_AFTER_EDIT: splice + replace the row's payload
        # in ONE transaction — preserved sections come from the CURRENT
        # record (the row's own payload, or the live entity for a
        # regenerate-entity row), the row's staged ``edges`` survive (3-4),
        # and ``entity_base_data`` refreshes to the SAME record the payload
        # was spliced from — the row is accept-able after a hand edit,
        # never stranded (the staging window closes by construction).
        row = _roll_candidate_payload(job.campaign_id, target_id, raw, requested, dial=dial)
        rows = [row]
    else:
        # Spec-3.6 MID_CALL_EDIT: the splice source moves to staging time
        # and SHARES ONE TRANSACTION with the staging write — the staged
        # payload's preserved sections and the ``entity_base_data``
        # conflict base reference the same committed moment, so a DM hand
        # edit landing between the prompt-build read and this write is
        # carried into BOTH (never silently overwritten at accept; the
        # two-transaction variant would put the edit in the base but not
        # the payload — the exact AR4/NFR2 failure this story exists to
        # prevent). Prompt/retrieval keep reading the run-start record
        # (AR6 determinism untouched); only the splice source moves.
        rows = _stage_entity_payload(job.campaign_id, job.id, target_id, raw, requested, dial=dial)
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


def _splice(
    base_record: dict[str, Any], raw: dict[str, Any], requested: Sequence[str]
) -> dict[str, Any]:
    """One staged payload assembled by construction: the target record
    with only the requested sections overwritten (trimmed) — preserved
    sections, unknown keys, and edges come from the base record, never
    from the model (byte-identical is an assembly invariant, not a model
    promise)."""
    payload = dict(base_record)
    for section in requested:
        if section in raw:
            payload[section] = _trim_section(raw[section])
    # Boss gating at splice time mirrors ``_requested_sections`` at run
    # start (spec-3.6 MID_CALL_EDIT): the requested list is frozen from
    # the RUN-START record, but a mid-call hand edit can move the fresh
    # record's role off BOSS_ROLES — splicing the model's boss section
    # onto it would stage (and accept would commit) an AR24-invalid
    # payload (role NPC with a boss section). The DM's edit wins: drop
    # the model's boss. Only a spliced boss is dropped — a preserved
    # boss on an already-invalid record is never touched (no shape
    # forcing, spec-3.6 Never list).
    if "boss" in requested and payload.get("role") not in BOSS_ROLES:
        payload.pop("boss", None)
    return payload


def _stage_entity_payload(
    campaign_id: str,
    job_id: str,
    target_id: str,
    raw: dict[str, Any],
    requested: Sequence[str],
    *,
    dial: str | None = None,
) -> list[models.ProposedCandidate]:
    """The entity-target regenerated candidate, staged in ONE transaction
    (spec-3.6 MID_CALL_EDIT): the target record is read fresh, the
    preserved sections are spliced from THAT record, and the row is
    staged with ``entity_base_data`` = the same record — payload, base,
    and their source share one committed moment, so an accept after a
    mid-generation hand edit never silently overwrites it (accept
    compare, payload, and base all reference the staging moment). A
    target deleted since the prompt-build read fails the job with zero
    rows (the runner never commits — AD-1)."""
    with session_scope() as session:
        entities, _edges = world_state(session, campaign_id)
        entity = next((e for e in entities if e.id == target_id), None)
        if entity is None or not isinstance(entity.data, dict):
            raise JobPayloadError(
                f"regenerate: target entity {target_id} is no longer committed "
                f"world state of campaign {campaign_id}"
            )
        fresh_record = copy.deepcopy(entity.data)
        payload = _splice(fresh_record, raw, requested)
        # AD-36: the dial is the DM's live setting from the shaped request
        # — authoritative, applied AFTER the splice (the splice preserves
        # the base's unknown keys, and the entity's stale dial would win
        # otherwise; 2026-09-27: a draft re-roll inherited pillar).
        if dial is not None:
            payload["dial"] = dial
        payload["edges"] = []
        return _stage_candidates(
            session,
            campaign_id,
            job_id,
            [payload],
            target_entity_id=target_id,
            target_base_data=fresh_record,
        )


def _roll_candidate_payload(
    campaign_id: str,
    target_id: str,
    raw: dict[str, Any],
    requested: Sequence[str],
    *,
    dial: str | None = None,
) -> models.ProposedCandidate:
    """A candidate-target re-roll, spliced and replaced in ONE transaction
    (spec-3.6 RE_ROLL_AFTER_EDIT): the row is re-resolved fresh; for a
    regenerate-entity row the preserved sections splice from the CURRENT
    target entity record and the row's staged ``edges`` survive (3-4);
    the replacement refreshes ``entity_base_data`` to the SAME record the
    payload was spliced from — payload, base, and their source share one
    moment, so the row is accept-able after a hand edit, never stranded.
    A settled/vanished row fails the job (never replaced)."""
    with session_scope() as session:
        row = session.get(models.ProposedCandidate, target_id)
        if row is None or row.campaign_id != campaign_id:
            raise JobPayloadError(
                f"regenerate: target candidate {target_id} does not exist in campaign {campaign_id}"
            )
        if row.status != "proposed":
            raise JobPayloadError(
                f"regenerate: target candidate {target_id} is already {row.status}"
            )
        staged_payload = row.payload
        if not isinstance(staged_payload, dict):
            raise JobPayloadError(f"regenerate: target candidate {target_id} has no record")
        if row.regenerates_entity_id is not None:
            target = session.get(models.Entity, row.regenerates_entity_id)
            if (
                target is None
                or target.campaign_id != campaign_id
                or not isinstance(target.data, dict)
            ):
                raise JobPayloadError(
                    f"regenerate: target entity {row.regenerates_entity_id} is no longer "
                    f"committed world state of campaign {campaign_id}"
                )
            base_record = copy.deepcopy(target.data)
            # The DM's staged edge edits (3-4) survive the re-roll verbatim.
            base_record["edges"] = staged_payload.get("edges", [])
        else:
            base_record = copy.deepcopy(staged_payload)
        payload = _splice(base_record, raw, requested)
        if dial is not None:
            payload["dial"] = dial
        return _replace_candidate_payload(session, campaign_id, target_id, payload)


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

    The AR24-shape demand binds ONLY a target that already satisfies it
    (AD-37 + owner verdict 2026-09-27): a FLAT target — bare build-in
    character, place/faction by design — has no sections to preserve and
    regeneration IS the enrich that grows it; its output shape is the
    model's to write (the whole record is the change set), and only the
    byte-identical preservation rule below still applies.
    """
    is_flat = payload_section_violations(record) != []
    violations: list[str] = [] if is_flat else payload_section_violations(raw)
    for key, value in record.items():
        if key in requested:
            continue
        if key == "dial":
            # AD-36: the dial is the shaped request's own field — the
            # re-rolled record legitimately carries the DM's chosen level,
            # never a byte-identical requirement.
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


#: Per-level elaboration floor (AD-38 teeth): the dial is only prose in
#: the prompt, so it must be CONCRETE — a draft and a pillar re-roll get
#: measurably different instructions, not the same sentence with a label.
_DIAL_GUIDANCE: dict[str, str] = {
    "nothing": "minimal — terse essentials only, one line per section",
    "draft": "compact — one short paragraph per section, the least a playable sheet needs",
    "simple": "solid — one grounded paragraph per section, no padding",
    "important": "deep — two paragraphs per section with concrete names and details",
    "pillar": (
        "maximum — two to three dense paragraphs per section: named"
        " characters, places, textures, hooks"
    ),
}


def build_regenerate_prompt(
    campaign_seed: models.Campaign,
    record: dict[str, Any],
    requested: Sequence[str],
    context: tuple[Sequence[models.Entity], Sequence[models.Edge]],
    *,
    guide: str | None = None,
    dial: str | None = None,
) -> str:
    """The regenerate prompt (AR6/AR27/AD-16): pure and byte-deterministic.

    A function of the campaign seed fields, the target record (with the
    requested sections' contents REDACTED so the model cannot copy-echo
    them — measured live 2026-09-27), the requested section list,
    the retrieved neighborhood's serialized hard truths, and — AD-38 —
    the shaped request's guide text and dial level (an enrich is this
    same call with ``sections: null``, a guide box line, and the dial).
    No ids, timestamps, or job state, ever. The model returns ONE full
    sectioned record: requested sections re-rolled, everything else
    byte-identical (identity anchor + edges + unknown keys untouched),
    in the same single-candidate envelope the generate runner parses.
    """
    entities, edges = context
    # Canonical order for the requested SET: the builder is a pure function
    # of the set, so its bytes are deterministic for the same set (AR6/AD-16).
    requested = [section for section in SECTION_ORDER if section in set(requested)]
    requested_label = ", ".join(requested) or "(none)"
    # The REQUESTED sections' contents are NEVER shown in the target: a
    # copy-echo class measured twice on 2026-09-27 (whole re-rolls echoed
    # the 43-char source; a stat_block re-roll echoed the exact block) —
    # the model sees its own prior text and re-emits it instead of
    # writing fresh. Requested sections are redacted (identity anchor,
    # unrequested sections, edges, and unknown keys stay — those must
    # echo byte-identical); with every section requested that leaves the
    # anchor + unknown keys.
    whole = len(requested) == len(SECTION_ORDER)
    redact = set(requested)
    target_for_prompt = (
        {key: value for key, value in record.items() if key not in redact} if redact else record
    )
    lines = [
        "You are re-rolling an existing TTRPG world character:",
        (
            "regenerate exactly the requested sections of the TARGET RECORD below,"
            if not whole
            else "re-roll EVERY section of the TARGET RECORD below,"
        ),
        (
            "keeping every other section byte-for-byte identical to it."
            if not whole
            else "writing every section FRESH — the target's section contents are"
            " deliberately NOT provided, so never echo; keep the facts consistent"
            " with the COMMITTED WORLD CONTEXT."
        ),
        (
            "The REQUESTED sections' current contents are deliberately omitted"
            " from the TARGET RECORD — write each one FRESH in new words; never"
            " echo the old text. Your re-rolled sections must be visibly"
            " different while staying consistent with the COMMITTED WORLD"
            " CONTEXT and the rules below."
            if not whole
            else ""
        ),
        "Respond with exactly one JSON object — nothing else.",
        "",
        "CAMPAIGN SEED",
        f"title: {campaign_seed.title}",
        f"description: {campaign_seed.description}",
        f"theme: {campaign_seed.theme}",
        f"custom lore: {campaign_seed.custom_lore}",
        "",
        (
            "TARGET RECORD (the character's full committed sectioned profile)"
            if not whole
            else "TARGET RECORD (identity anchor + edges + unknown keys only — every"
            " section is being re-rolled, so section contents are NOT shown)"
        ),
        serialize_record(target_for_prompt),
        "",
        (
            "REQUESTED SECTIONS (regenerate exactly these; do not touch any other)"
            if not whole
            else "REQUESTED SECTIONS (ALL — the whole record is re-rolled)"
        ),
        requested_label,
        (
            "\nDIAL (elaboration weight for the re-rolled sections; the record's"
            "\nlive setting — inherit it, never invent one):\n"
            f"{dial or '(not set — elaborate to the record existing depth)'}"
            + (f"\nElaboration floor: {_DIAL_GUIDANCE[dial]}" if dial in _DIAL_GUIDANCE else "")
        ),
        *(
            [
                "",
                "GUIDE (the DM says what changed or what they want more of —",
                "honor it inside the requested sections):",
                guide,
            ]
            if guide is not None
            else []
        ),
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
        "world_integration.reaction_matrix is one non-blank string (never a",
        "JSON object/mapping): enumerate each reacting committed entity or",
        "faction as 'C<index>: <reaction>' inside that single string.",
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
