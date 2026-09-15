"""The generate runner (spec-3.1): a plain-language ask -> 2-3 candidates.

One job, one provider call plus at most one bounded stat-repair pass
(AR25). The prompt is a pure function of the campaign seed (AR27), the
ask, and the rowid-ordered retrieved neighborhood (AR6: ``seed_ids=None``
— the full committed world, bounded by the entity cap; AD-16) —
byte-identical for the same world state and ask, pinned by test.

Model output is validated against the AR19 candidate shape (name, role
in {NPC, BBEG, Monster}, personality, the secret/rumor/party-hook
triple, an AR25-valid 5e stat block, >=1 typed edge whose far endpoint
is an existing committed entity) and staged as ``ProposedCandidate``
rows — committed nothing (the runner never calls ``commit_subgraph``;
AR7). Stat-block validation reuses the build-in machinery (spec-2.4)
with its canonical ``E<position>`` refs; exactly one bounded repair pass
runs under the job's ``CallBudget`` (AR21).

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
from typing import Any

import app.store as store
from app.core.settings import LLMSettings
from app.pipeline.budget import CallBudget
from app.pipeline.fencing import strip_fence
from app.pipeline.knowledge import ROLES
from app.pipeline.retrieval import DEFAULT_ENTITY_CAP, retrieve_neighborhood, serialize_context
from app.pipeline.statblocks import (
    apply_stat_repairs,
    build_stat_repair_prompt,
    canonicalize_stat_block,
    collect_stat_issues,
    parse_stat_repair_output,
    stat_block_rules_text,
)
from app.pipeline.worker import JobPayloadError
from app.store import (
    EDGE_TYPES,
    JobStateConflictError,
    complete_job,
    discard_candidates,
    edge_counter_semantic,
    models,
    report_progress,
    stage_candidates,
)
from app.store.candidates import (
    BOSS_FIELDS,
    BOSS_ROLES,
    IDENTITY_FIELDS,
    LORE_FIELDS,
    WORLD_INTEGRATION_FIELDS,
    canonicalize_reaction_matrix,
    payload_section_violations,
)
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
    validation -> AR25 stat validation with exactly one bounded repair
    pass -> stage the 2-3 surviving candidates as ``ProposedCandidate``
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
        or set(payload) != {"ask"}
        or not isinstance(payload.get("ask"), str)
    ):
        raise JobPayloadError("generate: job payload must be exactly {'ask': str}")
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
    context_entities, context_edges = retrieve_neighborhood(
        job.campaign_id, seed_ids=None, entity_cap=DEFAULT_ENTITY_CAP
    )
    prompt = build_generate_prompt(seed, ask, (context_entities, context_edges))
    text = budget.call(lambda: provider(prompt, settings=settings))
    parsed = _parse_candidates(text)[:MAX_CANDIDATES]
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
        violations = _candidate_violations(raw, context_entities)
        if violations:
            drops.append((index, _display_name(raw, index), "; ".join(violations)))
        else:
            valid.append((index, _candidate_payload(raw, context_entities)))

    # Stat-block enforcement (AR25): one bounded repair pass, budget-gated
    # like every provider call. ``inputs`` mirrors the parsed list 1:1 —
    # shape-valid candidates as characters, shape-invalid ones as
    # faction placeholders the stat machinery skips — so the repair
    # machinery's E<position> refs ARE the parsed indices above.
    valid_map = dict(valid)
    inputs = [
        models.EntityInput(
            kind="character",
            name=valid_map[index]["name"],
            data={
                "stat_block": valid_map[index]["stat_block"],
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
    stat_issues = collect_stat_issues(inputs)
    if stat_issues:
        if not _job_still_running(job):
            return
        repair_text = budget.call(
            lambda: provider(build_stat_repair_prompt(stat_issues), settings=settings)
        )
        repaired = parse_stat_repair_output(repair_text, [issue.position for issue in stat_issues])
        if repaired is None:
            # A malformed repair response repairs nothing: the flagged
            # candidates drop below as still-invalid (generate's contract
            # is drop-not-fail — never stage an AR25-invalid block).
            repaired = {}
        inputs = apply_stat_repairs(inputs, repaired)
        remaining = collect_stat_issues(inputs)
        if remaining:
            # Still-invalid candidates are DROPPED (not fatal): the
            # "2-3 candidates" contract governs (INVALID_STATS row).
            for issue in remaining:
                still_bad.add(issue.position)
                # The job.error names the violations; the WARNING line
                # carries the reproducible evidence — the exact block the
                # repair pass was shown and the exact reply it made, so a
                # stuck shape (a non-string trait description, a repair
                # that re-echoes it) is diagnosable from the log alone
                # (2026-09-15: traits entries with dict descriptions).
                block = issue.entity.data.get("stat_block")
                logger.warning(
                    "generate E%s %r still invalid after the repair pass (%s) — "
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
                        "stat block(s) still invalid after the repair pass: "
                        + "; ".join(issue.violations),
                    )
                )
    # The staged payloads carry the (possibly repaired) stat blocks: the
    # repair merges into the EntityInput mirror, so sync it back before
    # dropping still-invalid candidates — an AR25-valid block is never
    # staged unrepaired.
    valid = [
        (index, {**candidate, "stat_block": inputs[index].data.get("stat_block")})
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

    if len(valid) < MIN_CANDIDATES:
        detail = (
            " | ".join(f"E{index} ({name!r}): {reason}" for index, name, reason in drops)
            if drops
            else "the model returned too few candidates"
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
        "TASK",
        "Create exactly 3 candidate entities that answer the ask. Each candidate is a",
        f"character-like figure with a role in {sorted(ROLES)}, woven into the world by",
        "at least one meaningful typed edge whose far endpoint is a committed entity from the",
        "context list above. Weave, don't list: every candidate must fit the existing",
        "world. Edges must connect two different entities — no self-loops.",
        "Prefer more than one edge when that is what clearly establishes the",
        "candidate's role in the committed world.",
        "If the ask names several distinct figures, answer each with its own",
        "candidate, in the order the ask names them.",
        "Candidates are additions to the committed world: never a duplicate or",
        "upgrade of a committed entity, and never two versions of the same character",
        "— each gets a distinct name. Treat an already-established figure as the",
        'existing figure when the ask refers to it generically ("the BBEG"); do not',
        "create a replacement or a second one unless the ask explicitly requests one.",
        "",
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
        '  "boss": {"lair_actions": "...", "legendary_actions": "...", "immunities": "...",',
        '           "vulnerabilities": "..."}  — CONDITIONAL: this one section is REQUIRED',
        "           when the role is BBEG or Monster and OMITTED entirely for NPC (never",
        "           an empty boss object); every other section above is always required,",
        '  "edges": [{"endpoint": "C<index>", "direction": "outbound"|"inbound",',
        '             "type": "<vocabulary member>", "counter": <integer, default 1>}]}.',
        'Fill every boss field for a BBEG/Monster — use "None." where a field does not',
        "apply (e.g. a monster without legendary actions).",
        "Every edge connects the candidate to exactly one committed entity: endpoint",
        "is a C<index> ref matching the context list above (never an entity name or",
        'id), direction says whether the edge points from the candidate ("outbound")',
        'or from the endpoint to the candidate ("inbound"). Names, roles, personality,',
        "and the secret/rumor/party_hook fields must be non-blank.",
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
        "",
        "EDGE VOCABULARY (closed set — never invent a type)",
        *(f"- {edge_type}" for edge_type in sorted(EDGE_TYPES)),
        "",
        "COUNTER SEMANTICS (one integer per edge)",
        *(f"- {edge_type}: {edge_counter_semantic(edge_type)}" for edge_type in sorted(EDGE_TYPES)),
        "Neutral types: counter must be 1.",
        "Amount/score/intensity types: counter must be a positive integer from 1 to 10.",
    ]
    return "\n".join(lines)


def _parse_candidates(text: str) -> list[Any]:
    """Parse the LLM output: fence-strip, then require a JSON object with
    a ``candidates`` list (the AR19 output contract's envelope)."""
    stripped = strip_fence(text)
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise JobPayloadError(f"generate: output is not valid JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise JobPayloadError("generate: output must be a JSON object")
    raw = parsed.get("candidates")
    if not isinstance(raw, list):
        raise JobPayloadError("generate: output must have a 'candidates' list")
    return raw


def _non_blank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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


def _candidate_violations(raw: Any, context_entities: Sequence[models.Entity]) -> list[str]:
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
    violations = payload_section_violations(raw)
    anchor_violations = _edge_violations(raw.get("edges"), context_entities)
    if anchor_violations:
        violations.extend(anchor_violations)
    return violations


def _edge_violations(edges: Any, context_entities: Sequence[models.Entity]) -> list[str]:
    """The anchor rule: at least one well-formed edge into the committed
    world; malformed edges are dropped, and only a candidate left with
    none is invalid (BAD_EDGE)."""
    if not isinstance(edges, list) or not edges:
        return ["edges must be a non-empty list"]
    valid = [edge for edge in edges if _valid_edge(edge, context_entities) is not None]
    if not valid:
        return ["no edge resolves to a committed entity (BAD_EDGE)"]
    return []


def _valid_edge(edge: Any, context_entities: Sequence[models.Entity]) -> dict[str, Any] | None:
    """One well-formed edge: a dict whose endpoint is a canonical
    ``C<index>`` ref in range, whose type is in the closed vocabulary,
    whose direction is outbound/inbound, and whose counter (when given)
    is an int. Returns the staged edge record with the resolved committed
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
    position = _parse_context_ref(edge.get("endpoint"), context_entities)
    if position is None:
        return None
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
    counter = edge.get("counter", 1)
    if counter is None:
        counter = 1
    if type(counter) is not int:
        return None
    return {
        "endpoint": context_entities[position].id,
        "direction": direction,
        "type": edge_type,
        "counter": counter,
    }


def _canonical_block(block: Any) -> Any:
    """One candidate's stat block in canonical form (spec: structured
    attack damage and the missing stat aspects, 2026-09-11) — the same
    folds the build-in/regenerate gate applies, so a candidate staged here
    and a key figure committed there store one shape. A non-dict passes
    through untouched (the validator owns that verdict)."""
    return canonicalize_stat_block(block) if isinstance(block, dict) else block


def _candidate_payload(
    raw: dict[str, Any], context_entities: Sequence[models.Entity]
) -> dict[str, Any]:
    """The staged AR24 record for a shape-valid candidate: the AR19
    required fields and AR24 sections (trimmed strings; the
    world-integration and boss blocks re-assembled field by field) plus
    the edges resolved to committed endpoint ids. The boss section is
    included iff the role is BBEG/Monster (spec-3.3). Assembly is
    deterministic — same raw candidate, same staged key order. Extra
    keys pass through unvalidated (AR24 forward compatibility)."""
    staged = {
        "name": raw["name"].strip(),
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
    staged["edges"] = [
        resolved
        for edge in raw.get("edges", [])
        if (resolved := _valid_edge(edge, context_entities)) is not None
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
