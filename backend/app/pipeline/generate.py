"""The generate runner (spec-3.1): a plain-language ask -> 2-3 candidates.

One job, one provider call plus at most three bounded stat-repair passes
(AR25; the 2026-09-11/12 owner verdicts stepped build-in's single pass to
three, and generate mirrors the same ceiling — each pass targets only the
violations that survived the previous one). The prompt is a pure function
of the campaign seed (AR27), the
ask, and the rowid-ordered retrieved neighborhood (AR6: ``seed_ids=None``
— the full committed world, bounded by the entity cap; AD-16) —
byte-identical for the same world state and ask, pinned by test.

Model output is validated against the AR19 candidate shape (name, role
in {NPC, BBEG, Monster}, personality, the secret/rumor/party-hook
triple, an AR25-valid 5e stat block, >=1 typed edge whose far endpoint
is an existing committed entity) and staged as ``ProposedCandidate``
rows — committed nothing (the runner never calls ``commit_subgraph``;
AR7). Stat-block validation reuses the build-in machinery (spec-2.4)
with its canonical ``E<position>`` refs; the bounded repair passes run
under the job's ``CallBudget`` (AR21).

Fewer than 2 candidates surviving validation fails the job with a
structured error naming the count — the "2-3 candidates" contract is
kept honest, and regenerating the ask is the DM's retry. The prompt
requests exactly 3 candidates: a fixed count avoids nudging the model
toward manufacturing a third "acceptable" where 2 would do, and the
candidates come back undifferentiated — the runner never picks a
winner; selection is the DM's (stories 3.2/3.3). Validation keeps the
first 3 parsed entries and stages the 2-3 that survive.

Ref schemes: candidates are ``E<index>`` — the PARSED-output position,
one numbering for the whole job (shape-drop reasons and the stat-repair
refs agree because the repair mirror pads shape-invalid entries as
non-characters, which the stat machinery skips); committed context
entities are ``C<index>`` (context order), matching wave 2's scheme.
Staged edges connect a candidate to exactly one committed entity
(candidate-to-candidate edges are not staged: an accepted candidate must
never dangle on a rejected one — the staging contract in
``store.candidates``).

"""

import json
import logging
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any

import app.store as store
from app.core.settings import LLMSettings
from app.pipeline.budget import CallBudget
from app.pipeline.build_in import build_reason_fill_schema
from app.pipeline.fencing import strip_fence, strip_trailing_commas
from app.pipeline.knowledge import ROLES
from app.pipeline.prompt_catalog import prompt_contract
from app.pipeline.retrieval import (
    DEFAULT_ENTITY_CAP,
    retrieve_neighborhood,
    serialize_context,
)
from app.pipeline.statblocks import (
    StatIssue,
    apply_stat_repairs,
    build_stat_repair_prompt,
    canonicalize_stat_block,
    collect_stat_issues,
    parse_stat_repair_output,
    stat_block_rules_text,
)
from app.pipeline.wave import WaveJsonError, call_wave
from app.pipeline.worker import JobPayloadError
from app.store import (
    EDGE_TYPES,
    JobStateConflictError,
    complete_job,
    discard_candidates,
    edge_counter_bounds,
    edge_counter_semantic,
    models,
    report_progress,
    stage_candidates,
)
from app.store.candidates import (
    BOSS_FIELDS,
    BOSS_ROLES,
    FLAT_REGEN_SECTIONS,
    IDENTITY_FIELDS,
    LORE_FIELDS,
    WORLD_INTEGRATION_FIELDS,
    canonicalize_reaction_matrix,
    payload_section_violations,
)
from app.store.commit import edge_kind_ok
from app.store.db import session_scope
from app.store.jobs import GENERATE_MAX_ASK_LENGTH
from app.store.read import campaign_seed, world_state

logger = logging.getLogger(__name__)

#: The prompt requests exactly 3 candidates; validation keeps 2-3
#: (spec-3.1 Design Notes).
MAX_CANDIDATES = 3
MIN_CANDIDATES = 2

#: The staged edge direction values (``store.candidates.EDGE_DIRECTIONS``;
#: duplicated as a literal contract in the prompt below).
_DIRECTIONS = ("outbound", "inbound")


def run_generate(job: models.Job, provider: Callable[..., str], settings: LLMSettings) -> None:
    """Run one generate job to a terminal state (complete_job/fail_job).

    Deterministic prompt -> provider call -> fenced parse -> AR19 shape
    validation -> AR25 stat validation with bounded repair passes (up to
    three) -> stage the 2-3 surviving candidates as ``ProposedCandidate``
    rows (one transaction, all-or-nothing) -> ``complete_job`` naming the
    staged ids plus a ``dropped`` summary. Any failure (provider, budget,
    malformed output, fewer than 2 valid candidates, a staging race)
    propagates so the worker fails the job; nothing is ever committed to
    the world graph (AR7). A job cancelled before the provider call or
    before staging is a no-op (the cancel-race poll pattern of the
    build-in runner); a cancel racing the terminal write after staging
    discards the just-staged ghost rows before propagating the conflict.
    """
    with session_scope() as session:
        seed = campaign_seed(session, job.campaign_id)
        if seed is None:
            raise JobPayloadError(f"generate: campaign {job.campaign_id} does not exist")
        entities, _edges = world_state(session, job.campaign_id)
        if not entities:
            raise JobPayloadError(f"generate: campaign {job.campaign_id} has no committed entities")

    payload = job.payload
    if (
        not isinstance(payload, dict)
        or set(payload) not in ({"ask"}, {"ask", "entity_kind"})
        or not isinstance(payload.get("ask"), str)
    ):
        raise JobPayloadError(
            "generate: job payload must contain ask and an optional "
            "entity_kind ('character', 'faction', or 'place')"
        )
    entity_kind = payload.get("entity_kind", "character")
    if entity_kind not in {"character", "faction", "place"}:
        raise JobPayloadError("generate: entity_kind must be character, faction, or place")
    ask = payload["ask"].strip()
    # The runner re-checks the enqueue-time payload contract (non-blank,
    # length-capped): a generate job row written outside ``enqueue_job``
    # (direct store write, test helper, future caller) must never build a
    # prompt with an empty or truncated ask section (review round 3).
    if not ask:
        raise JobPayloadError("generate: ask must be non-blank")
    if len(ask) > GENERATE_MAX_ASK_LENGTH:
        raise JobPayloadError(f"generate: ask exceeds {GENERATE_MAX_ASK_LENGTH} chars")

    budget = CallBudget(job)

    # Cancel-race poll (same pattern as the text/build-in paths): a cancel
    # landing between claim and the provider call is a no-op.
    if not _job_still_running(job):
        return
    # Ask-target seeding (owner ruling 2026-09-18, "both"): when the world
    # exceeds the AR6 cap the ask's named entities lead the retrieval and
    # the remainder fills newest-first (rowid bias), so a tight context
    # keeps the target instead of losing it to the oldest rowid rows. Both
    # are pure functions of world state + the ask, so the prompt stays
    # byte-deterministic (AD-16); a sub-cap world is untouched.
    target_ids = _ask_target_ids(ask, entities)
    context_entities, context_edges = retrieve_neighborhood(
        job.campaign_id,
        seed_ids=None,
        entity_cap=DEFAULT_ENTITY_CAP,
        boost_ids=target_ids or None,
        newest_first=True,
    )
    prompt = build_generate_prompt(seed, ask, (context_entities, context_edges), entity_kind)
    parsed = call_wave(
        budget,
        provider,
        settings,
        prompt,
        label="generate",
        parse=_parse_candidates,
        retry_note='Return ONLY the one JSON object: {"candidates": [...]} — valid '
        "JSON, no prose, no fences, no trailing commas.",
        ceiling=settings.max_tokens,
    )[:MAX_CANDIDATES]
    # Dogfood 2026-09-09: the compact model renders
    # ``world_integration.reaction_matrix`` as a ``{"C<i>": "<reaction>"}``
    # mapping (all 3 candidates were dropped -> the job hard-failed);
    # canonicalize every candidate BEFORE validation so the mapping's
    # entries land in the contract's single-string form and validation
    # sees exactly what will stage. A string matrix passes through.
    parsed = [canonicalize_null_prose(canonicalize_reaction_matrix(raw)) for raw in parsed]

    # Shape validation (AR19): drop candidates that violate the contract.
    # E-refs are the PARSED indices throughout — one numbering for the
    # whole job: shape-drop reasons and the stat-repair refs agree
    # because the mirror below pads shape-invalid entries as
    # non-characters, which the stat machinery skips.
    valid: list[tuple[int, dict[str, Any]]] = []
    drops: list[tuple[int, str, str]] = []  # (E-ref index, name, reason)
    for index, raw in enumerate(parsed):
        violations = _candidate_violations(raw, context_entities, entity_kind)
        if violations:
            drops.append((index, _display_name(raw, index), "; ".join(violations)))
        else:
            valid.append((index, _candidate_payload(raw, context_entities, entity_kind)))

    # Stat-block enforcement (AR25): bounded repair passes (up to three),
    # budget-gated like every provider call. ``inputs`` mirrors the parsed
    # list 1:1 — shape-valid candidates as characters, shape-invalid ones
    # as faction placeholders the stat machinery skips — so the repair
    # machinery's E<position> refs ARE the parsed indices above.
    valid_map = dict(valid)
    inputs = [
        models.EntityInput(
            kind="character" if entity_kind == "character" else entity_kind,
            name=valid_map[index]["name"],
            data={
                **(
                    {"stat_block": valid_map[index]["stat_block"]}
                    if entity_kind == "character"
                    else {}
                ),
                **{
                    key: value
                    for key in ("role", "level_cr")
                    if (value := valid_map[index].get(key)) is not None
                },
            },
        )
        if index in valid_map
        else models.EntityInput(kind="faction", name=_display_name(raw, index))
        for index, raw in enumerate(parsed)
    ]
    still_bad: set[int] = set()
    # Stat-block enforcement (AR25): bounded repair passes (up to three,
    # mirroring build-in's repair-sequence ceiling — owner verdict
    # 2026-09-11/2026-09-12), budget-gated like every provider call. Each
    # pass targets only the issues that SURVIVED the previous one, and
    # ``attempt=N`` makes the prompt show the block the previous repair
    # produced plus exactly what is still wrong (build_stat_repair_prompt).
    # A candidate still invalid after the ceiling drops (generate's
    # contract is drop-not-fail — never stage an AR25-invalid block).
    stat_issues = collect_stat_issues(inputs)
    for attempt in (1, 2, 3):
        if not stat_issues:
            break
        if not _job_still_running(job):
            return
        repair_text = budget.call(
            partial(
                _call_stat_repair,
                provider=provider,
                settings=settings,
                issues=stat_issues,
                attempt=attempt,
            )
        )
        repaired = parse_stat_repair_output(repair_text, [issue.position for issue in stat_issues])
        if repaired is None:
            # A malformed repair response repairs nothing: the flagged
            # candidates drop below as still-invalid (generate's contract
            # is drop-not-fail — never stage an AR25-invalid block).
            repaired = {}
        inputs = apply_stat_repairs(inputs, repaired)
        stat_issues = collect_stat_issues(inputs)
    if stat_issues:
        # Still-invalid candidates are DROPPED (not fatal): the
        # "2-3 candidates" contract governs (INVALID_STATS row).
        for issue in stat_issues:
            still_bad.add(issue.position)
            # The job.error names the violations; the WARNING line
            # carries the reproducible evidence — the exact block the
            # final repair pass was shown and the exact reply it made, so
            # a stuck shape (a non-string trait description, a repair
            # that re-echoes it) is diagnosable from the log alone
            # (2026-09-15: traits entries with dict descriptions).
            block = issue.entity.data.get("stat_block")
            logger.warning(
                "generate E%s %r still invalid after the repair passes (%s) — "
                "block: %s; repair reply: %s",
                issue.position,
                issue.entity.name,
                "; ".join(issue.violations),
                json.dumps(block, sort_keys=True, separators=(",", ":"))
                if block is not None
                else "MISSING",
                repair_text[:2000],
            )
            drops.append(
                (
                    issue.position,
                    issue.entity.name,
                    "stat block(s) still invalid after the repair passes: "
                    + "; ".join(issue.violations),
                )
            )
    # The staged payloads carry the (possibly repaired) stat blocks: the
    # repair merges into the EntityInput mirror, so sync it back before
    # dropping still-invalid candidates — an AR25-valid block is never
    # staged unrepaired.
    valid = [
        (
            index,
            {**candidate, "stat_block": inputs[index].data.get("stat_block")}
            if entity_kind == "character"
            else candidate,
        )
        for index, candidate in valid
        if index not in still_bad
    ]

    # Strict-JSON guard (review round 2): Python's ``json.loads`` accepts
    # NaN/Infinity and turns 1e999 into ``inf`` — values the candidates
    # read cannot re-serialize (Starlette dumps with ``allow_nan=False``;
    # a staged payload containing one would 500 the headline endpoint).
    # Drop such a candidate like any other malformed one; the AR24
    # unknown-key passthrough stays faithful — only representable values
    # pass. ``store.candidates.stage_candidates`` repeats the check as
    # the write-boundary backstop.
    strict: list[tuple[int, dict[str, Any]]] = []
    for index, candidate in valid:
        try:
            json.dumps(candidate, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            drops.append(
                (
                    index,
                    candidate["name"],
                    "payload contains non-finite numbers (not strict JSON)",
                )
            )
        else:
            strict.append((index, candidate))
    valid = strict
    # Duplicate guard (review round 3): "2-3 candidates" means distinct
    # candidates — a model echoing the same candidate twice stages as
    # one row (the duplicate lands in the dropped summary naming the
    # first occurrence), keeping the accept screen honest. Equality is
    # exact and key-order-insensitive; two same-name but different
    # candidates both survive.
    seen: dict[str, int] = {}
    deduped: list[tuple[int, dict[str, Any]]] = []
    for index, candidate in valid:
        key = json.dumps(candidate, sort_keys=True, allow_nan=False)
        first = seen.get(key)
        if first is None:
            seen[key] = index
            deduped.append((index, candidate))
        else:
            drops.append((index, candidate["name"], f"duplicate of E{first}"))
    valid = deduped

    # AD-33 fill-blank (the saved-reason class): one bounded attempt per
    # blank batch across the surviving candidates — the staged edges are
    # the base, the single-field reply supplies reason only, take-only-X.
    # A still-blank edge drops with audit; a candidate left with zero
    # edges drops naming it (BAD_EDGE) — the >=2-candidate floor then
    # fails a thin wave loud.
    valid = _fill_candidate_blank_reasons(
        job, budget, provider, settings, valid, drops, context_entities
    )

    if len(valid) < MIN_CANDIDATES:
        detail = (
            " | ".join(f"E{index} ({name!r}): {reason}" for index, name, reason in drops)
            if drops
            else "the model returned too few candidates"
        )
        # Ambiguity hint (spec: retrieval-cap diagnostic): when the world
        # outgrew the retrieval window and the ask named no committed
        # entity, the failure may be exactly that — the model never saw
        # the ask's target. The banner + this hint make the silent-loss
        # failure diagnosable instead of a nameless drop.
        if len(context_entities) < len(entities) and not target_ids:
            detail += (
                f" — hint: the ask may target an entity outside the retrieval "
                f"window (context truncated to {DEFAULT_ENTITY_CAP} of "
                f"{len(entities)}; the ask names no committed entity)"
            )
        raise JobPayloadError(
            f"generate: {len(valid)} valid candidate(s) survived validation, "
            f"need {MIN_CANDIDATES}: {detail}"
        )

    # Cancel-race poll: a cancel during the call/validation must not stage.
    if not _job_still_running(job):
        return
    rows = stage_candidates(job.campaign_id, job.id, [candidate for _index, candidate in valid])
    try:
        report_progress(job.id, 1.0)
        complete_job(
            job.id,
            result={
                "candidate_ids": [row.id for row in rows],
                "candidate_count": len(rows),
                # The drop log (index, name, reason) rides on success too:
                # when 2 of 3 survive, the DM's accept screen can show why
                # one was discarded (spec-3.1 review round 1).
                "dropped": [
                    {"ref": f"E{index}", "name": name, "reason": reason}
                    for index, name, reason in drops
                ],
                # Per-call telemetry (J, same shape as build-in): the label
                # split shows the wave call and any bounded JSON retry —
                # the retry taxonomy's paper trail (owner notes 6/7).
                "llm_calls": budget.snapshot(),
                # Transparency (owner note 4, 2026-09-15; retrieval-cap
                # diagnostic, 2026-09-18): what the ask SAW — the
                # committed-world retrieval the prompt embedded.
                # requested = world entities at retrieval, included = rows
                # the context actually carried, truncated = included <
                # requested (the AR6 cap shrank the model's window), cap =
                # the retrieval cap. Proves truncation happened instead of
                # silently losing the ask's target.
                "context": {
                    "requested": len(entities),
                    "included": len(context_entities),
                    "truncated": len(context_entities) < len(entities),
                    "cap": DEFAULT_ENTITY_CAP,
                },
            },
        )
    except JobStateConflictError:
        # Cancel raced the terminal write: the just-staged rows are
        # ghosts — discard them, then propagate so the worker records
        # nothing on a cancelled job.
        try:
            discard_candidates(job.id)
        except Exception:  # noqa: BLE001 - the conflict is the error to surface
            logger.exception("failed to discard ghost rows for job %s", job.id)
        raise


def context_ref_pin(context_count: int) -> str:
    """The one-line pin mapping C<index> refs to the context entries —
    the same explicit range pin wave 2 gives its core anchors (P4 of the
    spec-3.1 review round 1). Pure and deterministic."""
    label = f"C0..C{context_count - 1}" if context_count > 0 else "(none)"
    return (
        f"CONTEXT REFS: the committed entities above are {label} in the order shown"
        " (context entry entity[<i>] = C<i>) — edge endpoints reference them as C<index>."
    )


def _call_stat_repair(
    provider: Callable[..., str],
    settings: LLMSettings,
    issues: Sequence[StatIssue],
    attempt: int,
) -> str:
    """One bounded stat-repair pass call. Bound via ``functools.partial``
    (no lambda default args — ruff B023 / mypy-clean), so each pass call
    carries its own attempt's issues snapshot."""
    return provider(build_stat_repair_prompt(issues, attempt=attempt), settings=settings)


def _display_name(raw: Any, index: int) -> str:
    """A display name for drop-reason labels: the candidate's own name
    when it is a non-blank string, otherwise the E-ref alone is used by
    the caller — here a stable placeholder for mirror rows."""
    name = raw.get("name") if isinstance(raw, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"(invalid candidate E{index})"


def build_generate_prompt(
    campaign_seed: models.Campaign,
    ask: str,
    context: tuple[Sequence[models.Entity], Sequence[models.Edge]],
    entity_kind: str = "character",
) -> str:
    """The generate prompt (AR6/AR27/AD-16): pure and byte-deterministic.

    A function of the campaign seed fields, the ask, and the retrieved
    neighborhood's serialized hard truths only — no ids, timestamps, or
    job state, ever. Embeds the closed edge vocabulary + counter
    semantics (spec-2.2), the AR19+AR24 sectioned output contract
    (spec-3.3), and the AR25 stat-block rules — with the SRD SPELLS BY CLASS
    table omitted here (the runtime validator and the one bounded repair
    pass own the authoritative table, AD-16); wave-1 and the repair prompt
    embed the same rules block with the table included.
    """
    entities, edges = context
    lines = [
        "You are growing an existing TTRPG world: turn the DM's plain-language ask",
        "into candidate entities woven into the committed world.",
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
        context_ref_pin(len(entities)),
        "",
        "THE DM'S ASK",
        ask.strip(),
        "",
        prompt_contract("generate"),
        "",
        "TASK",
        f"Create exactly 3 candidate {entity_kind} entities that answer the ask.",
        (
            f"Each candidate is a character-like figure with a role in {sorted(ROLES)}, "
            "woven into the world by"
            if entity_kind == "character"
            else (
                f"Each candidate is a complete {entity_kind} record with no character "
                "role, race, class, or stat block. Use the "
                f"{', '.join(FLAT_REGEN_SECTIONS[entity_kind])} fields required "
                f"for a {entity_kind}."
            )
        ),
        "at least one meaningful typed edge to a committed entity from the context",
        "list above or an explicitly requested new related entity. Weave, don't list:",
        "every candidate must fit the existing world. Edges must connect two different",
        "entities — no self-loops.",
        "Use edge directions that match entity kinds: bases_at and hails_from",
        "point from a character or faction to a place; do not use either",
        "with a place as the source.",
        "Prefer more than one edge when that is what clearly establishes the",
        "candidate's role in the committed world.",
        "If the ask names several distinct figures, answer each with its own",
        "candidate, in the order the ask names them.",
        "Candidates are additions to the committed world: never a duplicate or",
        f"upgrade of a committed entity, and never two versions of the same {entity_kind}",
        "— each gets a distinct name. Treat an already-established figure as the",
        'existing figure when the ask refers to it generically ("the BBEG"); do not',
        "create a replacement or a second one unless the ask explicitly requests one.",
        "",
    ]
    if entity_kind == "character":
        lines.extend(
            [
                "STAT BLOCKS",
                stat_block_rules_text(spells_reference=False),
                "",
                "OUTPUT CONTRACT",
                'Respond with one JSON object: {"candidates": [...]} — exactly 3 entries.',
                "Each candidate is the full sectioned profile — every section below is",
                "required and must be a non-blank string (or an object whose fields are",
                'all non-blank strings): {"name": "...", "role": "NPC|BBEG|Monster",',
                '  "level_cr": "level <n>" for NPC/BBEG or "CR <n>" for Monster,',
                '  "race_type": "...", "class_profession": "...", "alignment": "...",',
                '  "personality": "...", "secret": "...", "rumor": "...", "party_hook": "...",',
                '  "appearance": "painter-grade prose: face, body, clothing, scars, marks",',
                '  "background": "...", "goals": "...", "relationships": "...",',
                '  "voice_style": "...", "catchphrases": "...",',
                '  "stat_block": {...per the STAT BLOCK RULES above...},',
                '  "world_integration": {"reputation": "...", "factions": "...",',
                '                        "current_location": "...", "reaction_matrix": "...",',
                '                        "on_defeat": "..."},',
                "world_integration.reaction_matrix is ONE non-blank prose string —",
                "never a JSON object/mapping: enumerate each reacting committed",
                "entity or faction as 'C<index>: <reaction>' inside that single",
                "string, e.g. 'C5: Friendly — welcomes the party; C2: Hostile —",
                "schemes against them'.",
                '  "boss": {"lair_actions": "...", "legendary_actions": "...",',
                '           "immunities": "...",',
                '           "vulnerabilities": "..."}  — CONDITIONAL: this one section is REQUIRED',
                "           when the role is BBEG or Monster and OMITTED entirely for NPC (never",
                "           an empty boss object); every other section above is always required,",
                '  "related_entities": [{"ref": "N0", "kind": "character|faction|place",',
                '    "name": "...", "description": "...", "data": {...}}],',
                '  "edges": [{"endpoint": "C<index>"|"N<index>",',
                '             "direction": "outbound"|"inbound",',
                '             "type": "<vocabulary member>", "counter": <integer, default 1>,',
                '             "reason": "<one non-blank sentence: why this relation holds>"}]},',
                'Fill every boss field for a BBEG/Monster — use "None." where a field does not',
                "apply (e.g. a monster without legendary actions).",
                "Names, roles, personality, and the secret/rumor/party_hook fields",
                "must be non-blank.",
                "Direction example: the candidate hunts the C2 figure — candidate -> C2, so",
                '"outbound"; the C2 figure hunts the candidate — C2 -> candidate, so "inbound".',
                "Quality bar: secret is a specific concealed fact, rumor a concrete in-world",
                "claim, party_hook a concrete way the party engages the candidate, and",
                "world_integration.reaction_matrix says how the committed entities and",
                "factions above react to the candidate.",
                "Identity consistency: stat_block.identity.role must match the top-level",
                "role. Top-level level_cr is display text only (the export derives",
                "level/CR from the stat_block.identity numerics, which are",
                "authoritative): for NPC/BBEG write 'level <n>' matching",
                "stat_block.identity.level; for Monster write 'CR <n>' matching",
                "stat_block.identity.cr. Use lowercase 'level' and uppercase 'CR' exactly.",
            ]
        )
    else:
        fields = FLAT_REGEN_SECTIONS[entity_kind]
        example = ", ".join(f'"{field}": "..."' for field in fields)
        lines.extend(
            [
                "OUTPUT CONTRACT",
                'Respond with one JSON object: {"candidates": [...]} — exactly 3 entries.',
                f"Every {entity_kind} candidate requires name and the "
                f"{', '.join(fields)} fields as non-blank strings.",
                f'Each candidate: {{"name": "...", {example}, "related_entities": [],',
                '  "edges": [{"endpoint": "C0", "direction": "outbound", "type": "relationship",',
                '             "counter": 1, "reason": "A specific reason for this link."}]}',
                "Use only the listed fields for the main entity. Do not include",
                "character identity,",
                "personality, boss, world_integration, or stat_block fields.",
            ]
        )
    lines.extend(
        [
            "Every edge connects the candidate to exactly one committed entity or one",
            "explicit related_entities record. C<index> references the context list above;",
            "N<index> references related_entities in this same candidate.",
            'Each related entity uses {"ref": "N0", "kind": "character|faction|place",',
            '"name": "...", "description": "...", "data": {...}}.',
            "Use related_entities only for a new named entity explicitly requested by the DM",
            "or required to make the requested named relationship concrete. Do not invent",
            "extra related entities. A related entity is reviewed and accepted as part of",
            "this proposal bundle.",
            "Do not use an entity name or id as an edge endpoint.",
            'Direction says whether the edge points from the candidate ("outbound")',
            'or from the endpoint to the candidate ("inbound").',
            "",
            "EDGE VOCABULARY (closed set — never invent a type)",
            *(f"- {edge_type}" for edge_type in sorted(EDGE_TYPES)),
            "",
            "COUNTER SEMANTICS (one integer per edge)",
            *(
                f"- {edge_type}: {edge_counter_semantic(edge_type)}"
                for edge_type in sorted(EDGE_TYPES)
            ),
            "Neutral types: counter must be 1.",
            "Amount/score/intensity types: counter must be a positive integer from 1 to 10.",
        ]
    )
    return "\n".join(lines)


def _parse_candidates(text: str) -> list[Any]:
    """Parse the LLM output: fence-strip, then require a JSON object with
    a ``candidates`` list (the AR19 output contract's envelope). Dangling
    commas before closers are cleaned first (measured 2026-09-15: the
    wave wrote ``"slots": [4, 3, 3, ]`` and the whole job died with
    "Expecting value" at the comma). A decode failure raises
    ``WaveJsonError`` — the wave-class retry signal: the runner's
    ``call_wave`` gives exactly that class one bounded re-elicitation
    (2026-09-15: the generate wave had NO retry, only the repair gates
    did; a transient malformation failed the whole ask)."""
    stripped = strip_trailing_commas(strip_fence(text))
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise WaveJsonError(f"generate: output is not valid JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise JobPayloadError("generate: output must be a JSON object")
    raw = parsed.get("candidates")
    if not isinstance(raw, list):
        raise JobPayloadError("generate: output must have a 'candidates' list")
    return raw


def _non_blank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _related_entities(value: Any) -> list[dict[str, Any]]:
    """Return the explicit new-entity bundle in model output.

    Related entities are deliberately small transport records here. Their
    kind/name/description/data are reviewed with the proposal and committed
    atomically with it; invented records outside this list cannot become
    world state.
    """
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        ref = item.get("ref", f"N{index}")
        kind = item.get("kind")
        name = item.get("name")
        description = item.get("description", "")
        data = item.get("data", {})
        if (
            ref == f"N{index}"
            and kind in {"character", "faction", "place"}
            and isinstance(name, str) and name.strip()
            and _non_blank_str(description)
            and isinstance(data, dict)
        ):
            result.append(
                {
                    "ref": ref,
                    "kind": kind,
                    "name": name.strip(),
                    "description": description.strip(),
                    "data": data,
                }
            )
    return result


def _related_entity_violations(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return ["related_entities must be a list"]
    violations: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            violations.append(f"related_entities[{index}] must be an object")
            continue
        if item.get("ref") != f"N{index}":
            violations.append(f"related_entities[{index}].ref must be N{index}")
        if item.get("kind") not in {"character", "faction", "place"}:
            violations.append(f"related_entities[{index}].kind is invalid")
        if not _non_blank_str(item.get("name")):
            violations.append(f"related_entities[{index}].name must be non-blank")
        if not _non_blank_str(item.get("description")):
            violations.append(f"related_entities[{index}].description must be non-blank")
        if not isinstance(item.get("data", {}), dict):
            violations.append(f"related_entities[{index}].data must be an object")
    return violations


def _ask_target_ids(ask: str, entities: Sequence[models.Entity]) -> list[str]:
    """The committed entities a plain-language ask NAMES, in rowid order.

    The ask-target seeding rule (owner ruling 2026-09-18, name-match
    boost): normalize both sides and match whole-word containment —
    "the old mill" in "The Old Mill needs a caretaker" — longest names
    first, so "Maeve the Lamplighter" wins over a bare "Maeve", and a
    name contained in an already-matched longer mention is the same
    mention and skipped. No embeddings, closed and deterministic: a
    pure function of the ask and the committed world (AD-16). Empty
    when the ask names nothing.
    """
    from app.store.direct import normalize_entity_name  # noqa: PLC0415 - import cycle

    normalized_ask = f" {normalize_entity_name(ask)} "
    matched: list[tuple[int, str, str]] = []  # (rowid position, normalized name, id)
    for position, entity in enumerate(entities):
        normalized = normalize_entity_name(entity.name)
        if normalized and f" {normalized} " in normalized_ask:
            matched.append((position, normalized, entity.id))
    # Longest name first; a name contained in an already-kept longer one
    # is the same mention (the rowid order breaks ties).
    matched.sort(key=lambda row: len(row[1]), reverse=True)
    kept: list[tuple[int, str]] = []
    held: list[str] = []
    for position, normalized, entity_id in matched:
        if any(normalized in longer for longer in held):
            continue
        kept.append((position, entity_id))
        held.append(normalized)
    return [entity_id for _position, entity_id in sorted(kept)]


#: Required free-text AR24 fields the model leaves BLANK for creatures
#: that have none — a cosmic maw has no profession and no catchphrases.
#: (Measured 2026-09-15, "cthullu ender of worlds": 'The Abyssal Maw' hit
#: "class_profession must be a non-blank string; catchphrases must be a
#: non-blank string" and the whole wave dropped.) Other identity/lore
#: fields are NOT covered: level_cr/race_type/alignment are closed or
#: referenced shapes, and the narrative-lore prose is always written —
#: only these two were measured blank.
_NULLABLE_PROSE_FIELDS: tuple[str, ...] = ("class_profession", "catchphrases")


def canonicalize_null_prose(record: Any) -> Any:
    """Fold a blank/absent ``class_profession``/``catchphrases`` to the
    literal ``"None"`` the model itself writes when it does fill them.

    The fields are required non-blank strings in the AR19/AR24 contract,
    and the model's own honest answer for a mindless creature is
    ``"None"`` (measured in every healthy wave) — so a blank is folded
    to that same marker instead of dropping the candidate. Nothing is
    invented (a blank names no prose to write); the DM sees "None" on
    the accept screen, exactly like every other fill. A non-dict record,
    a non-blank string, and any other field pass through unchanged.
    """
    if not isinstance(record, dict):
        return record
    changed = False
    out = dict(record)
    for field in _NULLABLE_PROSE_FIELDS:
        if not _non_blank_str(out.get(field)):
            out[field] = "None"
            changed = True
    return out if changed else record


def _parse_context_ref(ref: Any, context_entities: Sequence[models.Entity]) -> int | None:
    """Parse a canonical ``C<index>`` endpoint ref into its context
    position, or None when malformed/out of range (BAD_EDGE)."""
    if not isinstance(ref, str) or not ref.startswith("C"):
        return None
    digits = ref[1:]
    # The int-conversion cap (CPython >= 3.11, 4300 digits) would raise
    # ValueError on an adversarial ref like "C" + 5000 digits and abort
    # the WHOLE job — a malformed edge must drop as BAD_EDGE, never brick
    # the batch (review round 2). 6 digits exceeds any real context index
    # (entity cap <= 24) yet is trivially convertible.
    if not digits.isdecimal() or len(digits) > 6:
        return None
    if str(int(digits)) != digits:
        return None
    index = int(digits)
    if index >= len(context_entities):
        return None
    return index


def _candidate_violations(
    raw: Any, context_entities: Sequence[models.Entity], entity_kind: str = "character"
) -> list[str]:
    """The AR19+AR24 shape violations of one raw candidate (``[]`` = valid).

    Checks the AR19 required fields (name, role, personality, the
    secret/rumor/party-hook triple), the AR24 sectioned profile
    (spec-3.3: identity anchor, narrative-lore, world-integration, and
    the boss section required iff the role is BBEG/Monster), and the
    anchor rule (>=1 typed edge into the committed world). A candidate
    missing substance fails here — it never reaches the accept screen
    silent-empty. Individual malformed edges are dropped — a candidate
    is invalid only when NO edge survives (BAD_EDGE is the
    all-edges-bad case). The section shape itself is
    ``store.candidates.payload_section_violations`` — ONE validator
    shared with the accept-override guard. Unknown keys pass through
    unvalidated (AR24 forward compatibility).
    """
    if not isinstance(raw, dict):
        return ["candidate must be an object"]
    violations = payload_section_violations(raw, entity_kind)
    related = _related_entities(raw.get("related_entities"))
    related_violations = _related_entity_violations(raw.get("related_entities"))
    anchor_violations = _edge_violations(raw.get("edges"), context_entities, entity_kind, related)
    related_violations.extend(anchor_violations)
    related_violations = related_violations or []
    if related_violations:
        violations.extend(related_violations)
    return violations


def _edge_violations(
    edges: Any,
    context_entities: Sequence[models.Entity],
    candidate_kind: str = "character",
    related_entities: Sequence[dict[str, Any]] = (),
) -> list[str]:
    """The anchor rule: at least one well-formed edge into the committed
    world; malformed edges are dropped, and only a candidate left with
    none is invalid (BAD_EDGE)."""
    if not isinstance(edges, list) or not edges:
        return ["edges must be a non-empty list"]
    valid = [
        edge
        for edge in edges
        if _valid_edge(edge, context_entities, candidate_kind, related_entities) is not None
    ]
    if not valid:
        return ["no edge resolves to a committed entity (BAD_EDGE)"]
    return []


def _valid_edge(
    edge: Any,
    context_entities: Sequence[models.Entity],
    candidate_kind: str = "character",
    related_entities: Sequence[dict[str, Any]] = (),
) -> dict[str, Any] | None:
    """One well-formed edge: a dict whose endpoint is a canonical
    ``C<index>`` ref in range, whose type is in the closed vocabulary,
    whose direction is outbound/inbound, and whose counter (when given)
    is an int within its semantic range (owner ruling 2026-09-18:
    amount 0..1_000_000, score/intensity 1..10, neutral unbounded).
    Returns the staged edge record with the resolved committed
    endpoint id, or None.

    Measured fold (2026-09-15, "cthullu ender of worlds"): the wave
    wrote the edge TYPE into the ``direction`` slot and omitted ``type``
    (``{"endpoint": "C0", "direction": "protects", "counter": 5}``) —
    three candidates dropped with BAD_EDGE. A direction holding a
    vocabulary type is unambiguous (the closed set never overlaps
    outbound/inbound): the type moves to ``type`` and the direction
    defaults to ``outbound`` (the candidate-to-committed link the prompt
    asks for). The staged record carries the corrected shape, so the
    accept screen and the accept-time override re-validation agree."""
    if not isinstance(edge, dict):
        return None
    endpoint = edge.get("endpoint")
    position = _parse_context_ref(endpoint, context_entities)
    context_kind: str | None
    if position is None:
        if not isinstance(endpoint, str) or not endpoint.startswith("N"):
            return None
        digits = endpoint[1:]
        if not digits.isdecimal() or str(int(digits)) != digits:
            return None
        related_index = int(digits)
        if related_index >= len(related_entities):
            return None
        context_kind = related_entities[related_index]["kind"]
    else:
        context_kind = context_entities[position].kind
    edge_type = edge.get("type")
    direction = edge.get("direction")
    if (
        (not isinstance(edge_type, str) or not edge_type.strip())
        and isinstance(direction, str)
        and direction in EDGE_TYPES
    ):
        edge_type = direction
        direction = "outbound"
    # The isinstance guard keeps a non-string JSON value (list/dict —
    # unhashable) a clean None, never a TypeError from the frozenset
    # membership test.
    if not isinstance(edge_type, str) or edge_type not in EDGE_TYPES:
        return None
    if not isinstance(direction, str) or direction not in _DIRECTIONS:
        return None
    if direction == "outbound":
        if not edge_kind_ok(edge_type, candidate_kind, context_kind):
            return None
    elif not edge_kind_ok(edge_type, context_kind, candidate_kind):
        return None
    counter = edge.get("counter", 1)
    if counter is None:
        counter = 1
    if type(counter) is not int:
        return None
    bounds = edge_counter_bounds(edge_type)
    if bounds is not None and not bounds[0] <= counter <= bounds[1]:
        return None
    resolved = {
        "endpoint": context_entities[position].id if position is not None else endpoint,
        "direction": direction,
        "type": edge_type,
        "counter": counter,
    }
    if store.edge_reason_ok(edge.get("reason")):
        resolved["reason"] = edge["reason"].strip()
    return resolved


def _endpoint_label(endpoint: Any, context_entities: Sequence[models.Entity]) -> str:
    """The committed entity an endpoint id names, for the frozen list."""
    if isinstance(endpoint, str):
        for entity in context_entities:
            if entity.id == endpoint:
                return entity.name
        return endpoint
    return "?"


def _fill_candidate_blank_reasons(
    job: models.Job,
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    valid: list[tuple[int, dict[str, Any]]],
    drops: list[tuple[int, str, str]],
    context_entities: Sequence[models.Entity],
) -> list[tuple[int, dict[str, Any]]]:
    """The AD-33 fill-blank class on the generate path: one bounded call
    for every surviving candidate's blank-reason edges. The saved staged
    edge records are the base; the single-field reply supplies reason
    only and the merge is take-only-X. A still-blank edge drops with
    audit (info log); a candidate left with zero edges drops naming the
    edge (BAD_EDGE). Malformed replies repair nothing (drop, never
    guess); a lying fill counts as missing."""
    blanks: list[tuple[int, int, str, str]] = []
    for index, candidate in valid:
        edges = candidate.get("edges", [])
        if not isinstance(edges, list):
            continue
        for edge_index, edge in enumerate(edges):
            if isinstance(edge, dict) and not store.edge_reason_ok(edge.get("reason")):
                blanks.append(
                    (
                        index,
                        edge_index,
                        _endpoint_label(edge.get("endpoint"), context_entities),
                        str(edge.get("type", "?")),
                    )
                )
    if not blanks:
        return valid
    frozen = "\n".join(
        f"- ordinal {ordinal}: E{cand} edge {edge_index}: {src} --{edge_type}--> (committed)"
        for ordinal, (cand, edge_index, src, edge_type) in enumerate(blanks)
    )
    prompt = "\n".join(
        [
            f"Your response left {len(blanks)} edge(s) without a saved reason.",
            "",
            "FROZEN EDGES (already recorded — supply ONLY their reasons):",
            frozen,
            "",
            "REASON FILL — respond with exactly one JSON object, nothing else:",
            '{"reasons": [{"index": <the frozen ordinal>,',
            '  "reason": "<one non-blank sentence: why this relation holds>"}]}',
            "A blank, 'None', 'N/A', or unknown is not an answer — write the",
            "concrete in-world why or omit the entry.",
        ]
    )
    import dataclasses

    filled_settings = dataclasses.replace(
        settings, response_format=build_reason_fill_schema(len(blanks))
    )
    try:
        reply_text = budget.call(partial(provider, prompt, settings=filled_settings))
    except WaveJsonError:
        # One bounded structural retry (the C taxonomy).
        reply_text = budget.call(
            partial(
                provider,
                prompt + "\n\nRETRY NOTE: Return ONLY the one JSON object "
                '{"reasons": [{"index": <n>, "reason": "..."}]} — valid JSON, no prose.',
                settings=filled_settings,
            )
        )
    try:
        reasons = _parse_reason_reply(reply_text)
    except (WaveJsonError, JobPayloadError):
        reasons = []  # a malformed reply repairs nothing: drop-with-audit below
    fills: dict[int, str] = {}
    for row in reasons:
        if not isinstance(row, dict):
            continue
        fill_index = row.get("index")
        fill_reason = row.get("reason")
        if type(fill_index) is not int or not isinstance(fill_reason, str):
            continue
        if not store.edge_reason_ok(fill_reason):
            continue  # a lying fill is missing, not a fill
        fills[fill_index] = fill_reason.strip()
    kept: list[tuple[int, dict[str, Any]]] = []
    for index, candidate in valid:
        edges = candidate.get("edges")
        if not isinstance(edges, list):
            edges = []
        new_edges: list[dict[str, Any]] = []
        for edge_index, edge in enumerate(edges):
            if not isinstance(edge, dict) or store.edge_reason_ok(edge.get("reason")):
                new_edges.append(edge)
                continue
            ordinal = _blank_ordinal(blanks, index, edge_index)
            fill = fills.get(ordinal) if ordinal is not None else None
            if fill is None:
                logger.info(
                    "generate E%s edge %d (%s) dropped: reason still blank after the fill "
                    "attempt (drop-with-audit, AD-33; job %s)",
                    index,
                    edge_index,
                    edge.get("type"),
                    job.id,
                )
                continue  # drop-with-audit
            new_edges.append({**edge, "reason": fill})
        if not new_edges:
            drops.append(
                (
                    index,
                    str(candidate.get("name", f"E{index}")),
                    "no edge with a saved reason survived (BAD_EDGE)",
                )
            )
            continue
        kept.append((index, {**candidate, "edges": new_edges}))
    return kept


def _blank_ordinal(
    blanks: list[tuple[int, int, str, str]], candidate_index: int, edge_index: int
) -> int | None:
    """The frozen-list ordinal of one (candidate, edge) blank, or None."""
    for order, (c_idx, e_idx, _src, _etype) in enumerate(blanks):
        if c_idx == candidate_index and e_idx == edge_index:
            return order
    return None


def _parse_reason_reply(text: str) -> list[Any]:
    """Parse the fill-blank reply (``{"reasons": [...]}``) — one bounded
    JSON contract."""
    stripped = strip_trailing_commas(strip_fence(text))
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise WaveJsonError(f"generate: reason-fill reply is not valid JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise JobPayloadError("reason-fill: reply must be a JSON object with a 'reasons' list")
    reasons = parsed.get("reasons")
    if not isinstance(reasons, list):
        raise JobPayloadError("reason-fill: reply must have a 'reasons' list")
    return reasons


def _canonical_block(block: Any) -> Any:
    """One candidate's stat block in canonical form (spec: structured
    attack damage and the missing stat aspects, 2026-09-11) — the same
    folds the build-in/regenerate gate applies, so a candidate staged here
    and a key figure committed there store one shape. A non-dict passes
    through untouched (the validator owns that verdict)."""
    return canonicalize_stat_block(block) if isinstance(block, dict) else block


def _candidate_payload(
    raw: dict[str, Any], context_entities: Sequence[models.Entity], entity_kind: str = "character"
) -> dict[str, Any]:
    """The staged AR24 record for a shape-valid candidate: the AR19
    required fields and AR24 sections (trimmed strings; the
    world-integration and boss blocks re-assembled field by field) plus
    the edges resolved to committed endpoint ids. The boss section is
    included iff the role is BBEG/Monster (spec-3.3). Assembly is
    deterministic — same raw candidate, same staged key order. Extra
    keys pass through unvalidated (AR24 forward compatibility)."""
    if entity_kind in FLAT_REGEN_SECTIONS:
        staged = {
            "name": raw["name"].strip(),
            "entity_kind": entity_kind,
            **{field: raw[field].strip() for field in FLAT_REGEN_SECTIONS[entity_kind]},
        }
        related = _related_entities(raw.get("related_entities"))
        staged["related_entities"] = related
        staged["edges"] = [
            resolved
            for edge in raw.get("edges", [])
            if (resolved := _valid_edge(edge, context_entities, entity_kind, related)) is not None
        ]
        for key, value in raw.items():
            if key not in staged:
                staged[key] = value
        return staged

    staged = {
        "name": raw["name"].strip(),
        "entity_kind": entity_kind,
        "role": raw["role"].strip(),
        "personality": raw["personality"].strip(),
        "secret": raw["secret"].strip(),
        "rumor": raw["rumor"].strip(),
        "party_hook": raw["party_hook"].strip(),
    }
    for field in IDENTITY_FIELDS + LORE_FIELDS:
        staged[field] = raw[field].strip()
    staged["world_integration"] = {
        field: raw["world_integration"][field].strip() for field in WORLD_INTEGRATION_FIELDS
    }
    if staged["role"] in BOSS_ROLES:
        staged["boss"] = {field: raw["boss"][field].strip() for field in BOSS_FIELDS}
    staged["stat_block"] = _canonical_block(raw.get("stat_block"))
    related = _related_entities(raw.get("related_entities"))
    staged["related_entities"] = related
    staged["edges"] = [
        resolved
        for edge in raw.get("edges", [])
        if (resolved := _valid_edge(edge, context_entities, entity_kind, related)) is not None
    ]
    for key, value in raw.items():
        if key not in staged:
            staged[key] = value
    return staged


def _job_still_running(job: models.Job) -> bool:
    """Cancel-race poll (build_in's pattern): a job cancelled between
    claim and a provider call or the staging write is a no-op —
    completing/failing a cancelled job would raise a conflict.

    A transient status-READ error must never wedge the queue (build_in
    makes the same call): returning True lets the job run to a terminal
    state; returning False here would leave it ``running`` forever with
    ``claim_next_job`` refusing every claim.
    """
    try:
        state, _position = store.job_status(job.id)
    except Exception:  # noqa: BLE001 - a status error must never wedge the queue
        logger.exception("worker state check failed for job %s", job.id)
        return True
    return state is None or state.state == "running"
