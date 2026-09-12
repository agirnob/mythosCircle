"""The build-in runner (spec-2.3 + spec-2.4): one job, two internal waves.

Wave 1 digests the named sections (places, factions, key figures) into a
core subgraph of ``character``/``faction``/``place`` entities + typed edges
and commits it atomically (one revision); every wave-1 character must first
carry the full AR24 record and a valid minimal stat block, each repaired in
at most one bounded pass (AR25). Wave 2 digests ``notes`` into a second
subgraph — every new entity wired by at least one typed edge into the
committed core — committed against wave 1's revision (a DM edit landing
between the waves raises ``StaleRevisionError`` and the job fails; never a
silent overwrite, AD-2).

Structure keeps its one bounded pass on wave 2 only (spec: repair sequence
step 3, anchor repair): a wave-2 subgraph whose ONLY defect is
core-unanchored entities gets one edges-only repair over frozen entities;
a still-orphan repair fails the job. Wave 1 commits edgeless (owner verdict 2026-09-11,
reversing the 2026-09-11 wave-1 re-emit — wiring the model will not invent is not worth a lost
build; the DM prunes).

Identity (owner rule 2026-09-12): both waves commit through an upsert
merge — an incoming entity whose (kind, normalized name) matches a
committed row of this campaign updates that row instead of creating a
twin, so re-submitting a grown list grows the world rather than doubling
it; wave 2 additionally drops exact twins of its own wave-1 roster before
validation (the prompt says the same). The job result carries the merge
audit.

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

import contextlib
import dataclasses
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, NamedTuple, cast

from sqlalchemy import select

from app.core import ids
from app.core.settings import LLMSettings
from app.pipeline.budget import BudgetExceededError, CallBudget
from app.pipeline.fencing import json_error, parse_json_object
from app.pipeline.fencing import strip_fence as _strip_fence
from app.pipeline.knowledge import ROLES, identity_field_allowed, identity_field_ok
from app.pipeline.retrieval import retrieve_neighborhood, serialize_context
from app.pipeline.statblocks import (
    StatIssue,
    apply_stat_repairs,
    build_stat_repair_prompt,
    build_stat_repair_schema,
    canonicalize_stat_blocks,
    collect_stat_issues,
    conform_first_targets,
    conform_stat_power,
    parse_stat_repair_output,
    scope_for_violations,
    spells_reference_text,
    stat_block_rules_text,
    stat_failure_message,
    strip_noncharacter_stat_blocks,
)
from app.pipeline.worker import JobPayloadError
from app.providers.llm import ProviderError
from app.store import (
    EDGE_TYPES,
    DanglingEdgeError,
    JobStateConflictError,
    StaleRevisionError,
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

#: Identity fields a stat repair must never degrade (dropped, blanked, or
#: re-typed). The merge restores the pre-repair value when the repair's own
#: value lost the shape the validator wants (knowledge.identity_field_ok);
#: a same-shape edit still lands, so a deliberate level/class change works.
_IDENTITY_GUARDED_FIELDS: tuple[str, ...] = ("class", "level", "cr")

#: Wave-2 context bounds (AR6 seed values): the neighborhood of the
#: wave-1 entities, one hop deep, at most 24 entities.
RETRIEVAL_DEPTH = 1
RETRIEVAL_ENTITY_CAP = 24

#: Record-repair chunk bound (chunking spec 2026-09-10): the record gate
#: splits flagged records into per-call chunks of at most this many so
#: every repair response stays short (a 14-record single response drops
#: braces stochastically and its same-size retry fails the same way).
RECORD_REPAIR_CHUNK_SIZE = 4

#: Wave-1 chunking (M1, the 100-entity cut): a roster larger than one chunk
#: is generated in bounded per-chunk calls instead of one monolithic wave
#: call. Measured at rung 50: Qwen3.8's single wave-1 call ran 411.5s /
#: 106,186 chars; the linear extrapolation to 100 lands at the 65,536-token
#: operator ceiling and the 900s timeout at once, one bad roll costs the
#: whole roster, and a cancel wastes the entire call. Chunks keep every
#: call inside the measured-green rung-10/25 envelope; a roster that fits
#: ONE chunk takes the original single-call path byte-identically.
WAVE1_CHUNK_SIZE = 12
#: Weighted chunk slicing: a key-figure entry (full AR24 record + stat
#: block, ~2.1k output chars) weighs 1.0; a flat place/faction entry ~0.35.
#: A chunk closes at WAVE1_CHUNK_WEIGHT units or WAVE1_CHUNK_HARD_CAP
#: entries, whichever binds first — a deterministic pure function of the
#: roster (AD-16).
WAVE1_CHUNK_WEIGHT = 12.0
WAVE1_CHUNK_HARD_CAP = 16
_WEIGHT_CHARACTER = 1.0
_WEIGHT_FLAT = 0.35
#: Per-call generation-window sizing (B): the measured per-entity output at
#: rung 50 (Qwen3.8 ~530 tokens/entity, gemma ~390) with ~70% headroom,
#: times the pinned count, plus edge-list headroom — capped at the
#: operator's settings.max_tokens (never unbounded, config.py). A
#: truncation retry doubles the window inside the same cap (C).
TOKENS_PER_ENTITY = 900
WAVE_CALL_TOKEN_HEADROOM = 4096
WAVE_CALL_MIN_TOKENS = 2048
#: Fixed window for the edges-only calls (wiring pass, anchor repair): the
#: response is a bounded edge list, never a roster.
EDGES_CALL_MAX_TOKENS = 16384
#: Wave-2 output caps (E1): notes are <=2000 chars and every measured wave 2
#: emitted <=9 entities — the caps make a runaway generation structurally
#: bounded while the roster stays model-decided (unpinned count).
WAVE2_MAX_ENTITIES = 24
WAVE2_MAX_EDGES = 256


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
    settings = _repair_sampling(settings)
    repair_text = budget.call(lambda: provider(prompt, settings=settings), label=label)
    repaired = parse(repair_text, positions)
    if repaired is not None:
        return repaired
    decode_error = json_error(repair_text)
    retry_text = budget.call(
        lambda: provider(
            _repair_retry_prompt(prompt, repair_text, retry_note, decode_error),
            settings=_rolled_seed(settings, 1),
        ),
        label=f"{label}_json_retry",
    )
    repaired = parse(retry_text, positions)
    if repaired is None:
        snippet = retry_text.strip()[:200]
        raise JobPayloadError(
            f"{label} repair: output was not valid JSON after one retry "
            f"(last output starts: {snippet!r})"
        )
    return repaired


class _WaveJsonError(JobPayloadError):
    """The structural retry signal (C, the retry taxonomy): a wave-class
    response that is not parseable JSON AT ALL. ``_call_wave`` gives exactly
    this class one bounded re-elicitation with a rolled seed; semantic
    rejections (bad refs, kinds, edge types — contract shapes inside
    well-formed JSON) stay plain ``JobPayloadError`` and stay terminal:
    they are deterministic, not noise. Subclasses ``JobPayloadError``, so
    every existing handler and fail-event path keeps working unchanged."""


class ContextRef(NamedTuple):
    """One C-labelable entity of the wave-2 anchor set (M2): the detail
    tier's committed rows and the compact tier's wave-1 inputs both reduce
    to ``(id, name, kind)`` — everything the C-ref resolver, the anchor
    rule, the wave-2 prompt roster, and the anchor-repair rosters read."""

    id: str
    name: str
    kind: str


def _context_refs(entities: Sequence[models.Entity]) -> list[ContextRef]:
    """The detail tier's ContextRefs (committed rows, world order)."""
    return [ContextRef(entity.id, entity.name, entity.kind) for entity in entities]


# ---------------------------------------------------------------------------
# Edge kind contract (owner decision 2026-09-12)
# ---------------------------------------------------------------------------

#: Kind compatibility for the closed edge vocabulary. ONE table feeds BOTH
#: the prompt guidance (layer 1 — ``edge_guidance_lines``) and the
#: validator + bounded repair (layer 2 — ``_edge_kind_rows``,
#: ``_clean_edge_kinds``), so the text and the enforcement cannot drift.
#: ``None`` = any kind; an absent type is unrestricted. Audit d5
#: (rung-100 world, 2026-09-12): 39/153 edges violated these rules —
#: 14 member_of edges with a place, 3 reversed located_in, 22 mutual
#: member_of (11 pairs) — plus 87 catch-all ``relationship`` edges the
#: guidance counters. The anti-pattern no per-edge rule captures —
#: membership is hierarchical, never both directions — is enforced as a
#: graph rule by the same layer 2.
EDGE_KIND_RULES: Mapping[str, tuple[frozenset[str] | None, frozenset[str] | None]] = (
    MappingProxyType(
        {
            "located_in": (None, frozenset({"place"})),
            "member_of": (
                frozenset({"character", "faction"}),
                frozenset({"character", "faction"}),
            ),
            "loyalty": (
                frozenset({"character", "faction"}),
                frozenset({"character", "faction"}),
            ),
        }
    )
)

#: The prompt-side surplus for specific types (layer 1). Kept out of the
#: machine-readable table so guidance prose stays prose.
_EDGE_GUIDANCE: Mapping[str, str] = MappingProxyType(
    {
        "located_in": "X is physically inside Y — a place's floor, walls, or waters",
        "member_of": (
            "membership is hierarchical: Y CONTAINS X; a place is never a "
            "member and never a host; ONE direction only — never both "
            "A member_of B and B member_of A"
        ),
        "loyalty": "a place holds and receives no loyalty",
        "relationship": "LAST RESORT — prefer the specific type that fits",
    }
)


def edge_kind_ok(edge_type: str, src_kind: str, dst_kind: str) -> bool:
    """The kind-compatibility rule for one edge (layer 2 enforces, layer 1
    prints). Unrestricted types accept any kind pair."""
    src, dst = EDGE_KIND_RULES.get(edge_type, (None, None))
    return (src is None or src_kind in src) and (dst is None or dst_kind in dst)


def _kind_pattern(kinds: frozenset[str] | None) -> str:
    return "any" if kinds is None else "|".join(sorted(kinds))


def edge_guidance_lines() -> list[str]:
    """The layer-1 EDGE VOCABULARY block shared by every build prompt —
    single-sourced from ``EDGE_KIND_RULES`` so the prompt text and the
    validator rules cannot drift. Pure and byte-deterministic (AD-16):
    a function of nothing."""
    lines = ["EDGE VOCABULARY (closed set — never invent a type)"]
    for edge_type in sorted(EDGE_TYPES):
        src, dst = EDGE_KIND_RULES.get(edge_type, (None, None))
        line = f"- {edge_type}: {_kind_pattern(src)} -> {_kind_pattern(dst)}"
        guidance = _EDGE_GUIDANCE.get(edge_type)
        if guidance:
            line = f"{line} — {guidance}"
        lines.append(line)
    lines.append("- an edge connects two DIFFERENT entities — never a self-edge")
    return lines


def _kind_violation_reason(edge_type: str, src_kind: str, dst_kind: str) -> str:
    if edge_type == "located_in":
        return f"located_in must point at a place, not a {dst_kind}"
    if edge_type == "member_of":
        if src_kind == "place":
            return "a place cannot be a member of anything"
        return "nothing is a member of a place — use located_in"
    if edge_type == "loyalty":
        return "a place holds and receives no loyalty"
    return f"{edge_type} is not allowed between {src_kind} and {dst_kind}"


def _kind_violation_message(wave: int, violations: Sequence[dict[str, Any]]) -> str:
    parts = [
        f"edge {violation['index']} {violation['src']} --{violation['type']}--> "
        f"{violation['dst']} ({violation['src_kind']} -> {violation['dst_kind']}): "
        f"{violation['reason']}"
        for violation in violations
    ]
    return f"wave {wave}: edge-kind rule violations — " + "; ".join(parts)


class _EdgeKindViolationError(JobPayloadError):
    """Layer 2's kind-violation signal: one or more edges pair kinds
    ``EDGE_KIND_RULES`` forbids, or form a mutual ``member_of`` pair.
    Carries the raw offending rows; the runner's one bounded edges-only
    repair re-fixes them — wave 1 degrades to drop-with-audit (precedent:
    the wiring pass and the edgeless commit), wave 2 fails loud (its
    anchors matter). Subclasses ``JobPayloadError`` so every existing
    fail path keeps working."""

    def __init__(self, message: str, violations: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.violations = violations


def _endpoint_position(ref: Any, prefix: str) -> int | None:
    """Parse ``prefix<index>`` permissively (canonical decimal). None when
    the ref lacks the shape — malformed refs stay ``_resolve_edges``'s
    rejection, not this classifier's."""
    if not isinstance(ref, str) or not ref.startswith(prefix):
        return None
    digits = ref[len(prefix) :]
    if not digits.isdigit():
        return None
    return int(digits)


def _ref_kind(
    ref: Any,
    *,
    entity_kinds: Sequence[str | None],
    context: Sequence[ContextRef],
    ref_offset: int,
) -> str | None:
    """The kind an edge endpoint ref names, or None when unresolvable
    here. E-refs are global and offset into the entity slice; N-refs index
    the slice directly; C-refs index the anchor context."""
    if ref.startswith("E"):
        position = _endpoint_position(ref, "E")
        if position is None:
            return None
        position -= ref_offset
        return entity_kinds[position] if 0 <= position < len(entity_kinds) else None
    if ref.startswith("N"):
        position = _endpoint_position(ref, "N")
        if position is None:
            return None
        return entity_kinds[position] if 0 <= position < len(entity_kinds) else None
    if ref.startswith("C"):
        position = _endpoint_position(ref, "C")
        if position is None:
            return None
        return context[position].kind if 0 <= position < len(context) else None
    return None


def _raw_entity_kinds(raw_entities: Sequence[Any]) -> list[str | None]:
    """The kind per raw entity position, folded with the SAME
    canonicalization ``_validate_entities`` applies (a role written in
    ``kind`` still classifies as character). None = the row will be
    rejected structurally; its edges skip the kind check, not the
    rejection."""
    kinds: list[str | None] = []
    for raw in raw_entities:
        if not isinstance(raw, dict):
            kinds.append(None)
            continue
        folded = canonicalize_entity_kind(raw.get("kind"))
        kinds.append(folded[0] if folded is not None else None)
    return kinds


def _edge_kind_rows(
    wave: int,
    raw_edges: Sequence[Any],
    entity_kinds: Sequence[str | None],
    *,
    context: Sequence[ContextRef] = (),
    ref_offset: int = 0,
    world_member_edges: frozenset[tuple[str, str]] = frozenset(),
) -> list[dict[str, Any]]:
    """Classify raw edge rows against layer 2. Two rule classes: the
    per-type kind pairs of ``EDGE_KIND_RULES``, and the graph rule that
    membership is hierarchical — a ``member_of`` row whose reverse exists
    in the wave (ref pair) or in the committed world (resolved C-id pair)
    is a violation. Returns the offending rows augmented with
    ``src_kind``/``dst_kind``/``reason``."""
    violations: list[dict[str, Any]] = []
    member_rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, dict):
            continue  # structural garbage is _resolve_edges's rejection
        edge_type = raw.get("type")
        if edge_type not in EDGE_TYPES:
            continue
        src_ref, dst_ref = raw.get("src"), raw.get("dst")
        src_kind = _ref_kind(
            src_ref, entity_kinds=entity_kinds, context=context, ref_offset=ref_offset
        )
        dst_kind = _ref_kind(
            dst_ref, entity_kinds=entity_kinds, context=context, ref_offset=ref_offset
        )
        if src_kind is None or dst_kind is None:
            continue
        row = {
            "index": index,
            "src": src_ref,
            "dst": dst_ref,
            "type": edge_type,
            "src_kind": src_kind,
            "dst_kind": dst_kind,
        }
        if not edge_kind_ok(edge_type, src_kind, dst_kind):
            violations.append(
                {**row, "reason": _kind_violation_reason(edge_type, src_kind, dst_kind)}
            )
        elif edge_type == "member_of":
            member_rows.append(row)
    reverse_pairs = {(row["dst"], row["src"]) for row in member_rows}
    for row in member_rows:
        if row["src"] != row["dst"] and (row["src"], row["dst"]) in reverse_pairs:
            violations.append(
                {
                    **row,
                    "reason": "mutual member_of — membership is hierarchical, ONE direction only",
                }
            )
    if world_member_edges:
        for row in member_rows:
            src_position = _endpoint_position(row["src"], "C")
            dst_position = _endpoint_position(row["dst"], "C")
            if src_position is None or dst_position is None:
                continue
            if not 0 <= src_position < len(context) or not 0 <= dst_position < len(context):
                continue
            if (context[dst_position].id, context[src_position].id) in world_member_edges:
                violations.append(
                    {
                        **row,
                        "reason": (
                            "mutual member_of with the committed world — membership is hierarchical"
                        ),
                    }
                )
    return violations


def _world_member_pairs(campaign_id: str) -> frozenset[tuple[str, str]]:
    """The committed world's member_of pairs — the other side of wave-2's
    mutual-membership check (a new C-to-C membership contradicting one the
    world already has)."""
    with session_scope() as session:
        return frozenset(
            (row.src, row.dst)
            for row in session.scalars(
                select(models.Edge).where(
                    models.Edge.campaign_id == campaign_id, models.Edge.type == "member_of"
                )
            )
        )


def _build_edge_kind_repair_prompt(
    *,
    wave: int,
    roster: Sequence[tuple[str, str, str | None]],
    context: Sequence[ContextRef],
    existing: Sequence[Any],
    rejected: Sequence[dict[str, Any]],
) -> str:
    """The one bounded edge-kind repair prompt (layer 2): frozen roster,
    rejected edges with their kinds and reasons, and the same guidance the
    generation prompts carry. The response is edges-only — renames and
    drops are representable by omitting rows."""
    lines = [
        "You are repairing edges rejected by the world's kind rules.",
        "",
        *edge_guidance_lines(),
        "",
        "ENTITIES (frozen — the edge roster):",
        *(f"- {ref} {name!r} ({kind})" for ref, name, kind in roster),
        *(f"- C{index} {ref.name!r} ({ref.kind})" for index, ref in enumerate(context)),
        "",
        "ALREADY-ACCEPTED EDGES (keep as-is — do NOT re-emit them):",
        *(
            f"- {row.get('src')} --{row.get('type')}--> {row.get('dst')}"
            for row in existing
            if isinstance(row, dict)
        ),
        "",
        "REJECTED EDGES — return a corrected version of EACH, or omit it:",
        *(
            f"- {row['src']} --{row['type']}--> {row['dst']} "
            f"({row.get('src_kind')} -> {row.get('dst_kind')}): {row.get('reason')}"
            for row in rejected
        ),
        "",
        'Return ONE JSON object: {"edges": [{"src": "<ref>", "dst": "<ref>",',
        '"type": "<vocabulary member>", "counter": <integer, default 1>}]} —',
        "ONLY corrections for the rejected edges. No self-edges.",
    ]
    return "\n".join(lines)


def _clean_edge_kinds(
    *,
    wave: int,
    raw_edges: list[Any],
    entity_roster: Sequence[tuple[str, str, str | None]],
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    ceiling: int,
    context: Sequence[ContextRef] = (),
    world_member_edges: frozenset[tuple[str, str]] = frozenset(),
    allow_drop: bool,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Layer 2's bounded edge-kind repair: classify the raw rows; on
    violations, ONE edges-only repair call (frozen roster, cold+seeded,
    ref-pinned — the anchor-repair pattern) asks for corrected versions; a
    still-violating remainder is DROPPED with the audit under
    ``allow_drop`` (wave 1 — its edges are best-effort, precedent: the
    wiring pass) or fails the job (wave 2 — its anchors matter). Returns
    ``(cleaned_rows, audit)``; the audit is empty when nothing dropped."""
    kinds = [kind for _ref, _name, kind in entity_roster]
    violations = _edge_kind_rows(
        wave, raw_edges, kinds, context=context, world_member_edges=world_member_edges
    )
    if not violations:
        return raw_edges, []
    invalid_indices = {violation["index"] for violation in violations}
    new_refs = [ref for ref, _name, _kind in entity_roster]
    core_refs = [f"C{index}" for index in range(len(context))]
    repair_settings = _repair_sampling(
        dataclasses.replace(
            settings,
            response_format=build_anchor_repair_schema(new_refs, core_refs),
            max_tokens=min(ceiling, EDGES_CALL_MAX_TOKENS),
        )
    )
    prompt = _build_edge_kind_repair_prompt(
        wave=wave,
        roster=entity_roster,
        context=context,
        existing=raw_edges,
        rejected=violations,
    )
    try:
        repaired = _call_wave(
            budget,
            provider,
            repair_settings,
            prompt,
            label=f"wave{wave}_edge_kind_repair",
            parse=lambda text: _parse_edges_only_output(
                text, what=f"wave {wave}: edge-kind repair"
            ),
            retry_note='Return ONLY the one JSON object: {"edges": [...]} — the corrected edges.',
            ceiling=ceiling,
        )
    except (JobPayloadError, ProviderError, BudgetExceededError) as exc:
        if not allow_drop:
            raise
        logger.warning(
            "wave %d edge-kind repair failed (%s: %s) — dropping the %d rejected "
            "edge(s) (wave-1 edges are best-effort)",
            wave,
            exc.__class__.__name__,
            exc,
            len(violations),
        )
        # The rejected edges never came back — record them as dropped so
        # the job result shows exactly what the failure cost.
        return (
            [row for index, row in enumerate(raw_edges) if index not in invalid_indices],
            violations,
        )
    merged = [row for index, row in enumerate(raw_edges) if index not in invalid_indices]
    merged.extend(_filter_wiring_edges(repaired, frozenset([*new_refs, *core_refs])))
    residual = _edge_kind_rows(
        wave, merged, kinds, context=context, world_member_edges=world_member_edges
    )
    if residual:
        if not allow_drop:
            raise JobPayloadError(_kind_violation_message(wave, residual))
        drop = {violation["index"] for violation in residual}
        kept = [row for index, row in enumerate(merged) if index not in drop]
        logger.info(
            "wave %d: %d edge(s) still kind-invalid after repair — dropped: %s",
            wave,
            len(residual),
            "; ".join(
                f"{violation['src']} --{violation['type']}--> {violation['dst']}"
                for violation in residual
            ),
        )
        return kept, residual
    return merged, []


def _rolled_seed(settings: LLMSettings, attempt: int) -> LLMSettings:
    """The retry's variance roll (C): when the operator pinned a seed, the
    retry carries seed+attempt so a deterministic profile never re-calls
    the identical failing sample; with no pinned seed the sampling
    temperature already varies and the body stays untouched."""
    if settings.seed is None:
        return settings
    return dataclasses.replace(settings, seed=settings.seed + attempt)


#: Repair sampling preset (K, owner verdict 2026-09-12): a repair is a rule
#: fix, not creative work — cold and seeded so every patch is reproducible
#: from the tee'd calls and the ledger. Wave-class calls stay warm (the
#: operator's sampling default). Per-MODEL profiles collapsed to the model
#: choice itself: the d4/d5 measurements show no behavioral difference
#: between Qwen and gemma beyond latency and repair traffic, so a
#: per-model field registry would be invented machinery.
REPAIR_TEMPERATURE = 0.0
REPAIR_SEED = 20260912


def _repair_sampling(settings: LLMSettings) -> LLMSettings:
    """The cold+seeded preset for repair-class calls. Operator-pinned
    values win — an explicit ``MYTHOSCIRCLE_LLM_SEED``/temperature is a
    deliberate choice this preset must not stomp. ``_rolled_seed`` still
    varies a JSON retry by seed+attempt, so a cold profile never
    re-samples the identical failing patch."""
    updates: dict[str, Any] = {}
    if settings.temperature is None:
        updates["temperature"] = REPAIR_TEMPERATURE
    if settings.seed is None:
        updates["seed"] = REPAIR_SEED
    return dataclasses.replace(settings, **updates) if updates else settings


def _wave_max_tokens(entity_count: int, ceiling: int) -> int:
    """The per-call generation window for a pinned-count wave call (B):
    ``TOKENS_PER_ENTITY`` x count + edge headroom, inside the operator's
    ceiling. Sized from the recorded corpora so a legitimate roster never
    truncates, while a runaway generation hits the wall in minutes instead
    of burning the full ceiling."""
    wanted = TOKENS_PER_ENTITY * entity_count + WAVE_CALL_TOKEN_HEADROOM
    return max(WAVE_CALL_MIN_TOKENS, min(ceiling, wanted))


def _call_with_truncation_retry(
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    prompt: str,
    *,
    label: str,
    ceiling: int,
) -> str:
    """One provider call under the truncation class of the retry taxonomy
    (B/C): ``finish_reason == "length"`` means the WINDOW was too small for
    this roster — the one retry doubles it inside the operator's cap (a
    plain re-call would deterministically truncate again, which is what
    made the naive retry wrong). Every other ``ProviderError`` propagates
    exactly as before (spec-1.4: connection/http failures are terminal)."""
    try:
        return budget.call(lambda: provider(prompt, settings=settings), label=label)
    except ProviderError as exc:
        doubled = min(settings.max_tokens * 2, ceiling)
        if exc.kind != "truncated" or doubled <= settings.max_tokens:
            raise
        logger.info(
            "%s truncated at max_tokens=%s; retrying once at %s",
            label,
            settings.max_tokens,
            doubled,
        )
        widened = dataclasses.replace(settings, max_tokens=doubled)
        return budget.call(lambda: provider(prompt, settings=widened), label=f"{label}_trunc_retry")


def _call_wave[P](
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    prompt: str,
    *,
    label: str,
    parse: Callable[[str], P],
    retry_note: str,
    ceiling: int,
) -> P:
    """One wave-class call under the full bounded retry taxonomy (C):

    * malformed JSON (``_WaveJsonError``) -> exactly ONE re-elicitation
      quoting the decoder error (the repair gates' proven
      ``_repair_retry_prompt`` shape) with a rolled seed — never an
      identical re-call;
    * truncation -> one doubled-window retry per call (inside
      ``_call_with_truncation_retry``, so the JSON retry gets its own);
    * semantic rejection, budget, connection, HTTP -> terminal, unchanged.

    On grammar-enforcing backends the structural classes are near-dead
    code (the ladder measured 4/4 first-try parses under ``json_schema``);
    on backends without enforcement they are the difference between a lost
    multi-minute wave and a recovered one.
    """
    text = _call_with_truncation_retry(
        budget, provider, settings, prompt, label=label, ceiling=ceiling
    )
    try:
        return parse(text)
    except _WaveJsonError:
        retry_prompt = _repair_retry_prompt(prompt, text, retry_note, json_error(text))
        text = _call_with_truncation_retry(
            budget,
            provider,
            _rolled_seed(settings, 1),
            retry_prompt,
            label=f"{label}_json_retry",
            ceiling=ceiling,
        )
        return parse(text)


def _wave1_roster(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """The trimmed wave-1 roster: ``(section, entry)`` pairs in fixed prompt
    order — the same trimming the count pin and the enqueue budget share
    (``jobs._build_in_budget``). Pure function of the payload."""
    roster: list[tuple[str, str]] = []
    for section in SECTION_NAMES:
        for entry in payload.get(section, []):
            if isinstance(entry, str) and entry.strip():
                roster.append((section, entry.strip()))
    return roster


def _wave1_chunks(roster: Sequence[tuple[str, str]]) -> list[list[tuple[int, str, str]]]:
    """Slice the roster into weighted generation chunks (M1): each item is
    ``(global_position, section, entry)`` — the position fixes the entity's
    canonical E-ref, so chunks merge in one deterministic order and edges
    resolve against the assembled roster. Greedy and pure: a chunk closes
    when the next entry would cross the weight budget or the hard cap."""
    chunks: list[list[tuple[int, str, str]]] = []
    current: list[tuple[int, str, str]] = []
    weight = 0.0
    for position, (section, entry) in enumerate(roster):
        entry_weight = _WEIGHT_CHARACTER if section == "key_figures" else _WEIGHT_FLAT
        if current and (
            weight + entry_weight > WAVE1_CHUNK_WEIGHT or len(current) >= WAVE1_CHUNK_HARD_CAP
        ):
            chunks.append(current)
            current = []
            weight = 0.0
        current.append((position, section, entry))
        weight += entry_weight
    if current:
        chunks.append(current)
    return chunks


def _enforce_stat_blocks(
    job: models.Job,
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    entities: list[models.EntityInput],
    *,
    repair_response_format: dict[str, Any] | None = None,
    wave: int | None = None,
) -> tuple[list[models.EntityInput], bool]:
    """The stat-block gate shared by both waves and the regenerate path:
    collect issues, run up to THREE bounded repair passes, re-check, then the
    deterministic power conform. Returns ``(entities, cancelled)`` —
    cancelled True means the job was cancelled mid-gate and the caller must
    stop without committing this wave.

    Three passes, not one (owner decision 2026-09-11 for the second,
    repair-sequence spec step 2 for the third): the model's single shot at
    arithmetic it cannot do was the most common live build failure
    (measured 2026-09-10: 5.5 vs 15-20, 16 vs 27-32, 50 vs 93-98 — never
    in band), and a repair call is cheap next to losing the DM's whole
    build. Each pass re-reads the block the PREVIOUS attempt wrote and
    exactly which violations survived it
    (`build_stat_repair_prompt(..., attempt=N)`); the deterministic conform
    is the last word before the job fails, and the model still never gets
    to fix wording.

    Conform runs FIRST for Monster-role power-only misses (2026-09-12, the
    NPC-oracle cut): both ladder models measured non-convergent on band
    arithmetic — Qwen nudges 8.5 -> 13 -> 17 and never reaches the band,
    gemma overshoots it — while the deterministic conform lands the exact
    DMG row in one pass. A power-only Monster miss therefore costs ZERO
    provider calls, and the passes below are left to the SHAPE violations
    where the model is the only writer.

    Each repair call carries exactly ONE failing entity (prompt, parse, and
    merge by ref), after the ``RECORD_REPAIR_CHUNK_SIZE`` precedent: a
    multi-entity repair response stays long enough to drop braces
    stochastically, and one block's drift no longer rides on another's
    converging numbers — the step-1 strip still runs between the breach log
    and the merge for every per-entity merge. Worst-case budget at rung-10
    scale (6 characters per wave: wave + name + 2 record chunks + 6 x 3
    stat calls = 22 per wave, 23 on wave 2 with the orphan re-emit, 45 per
    job) fits the 64-call enqueue default with headroom, so the default
    stays.
    """
    # A block whose dice sit in a non-standard ``damage`` key reports ZERO
    # damage to the auditor, which the non-combatant exemption then swallows
    # whole — so the gate must fold it in before it decides anything. The
    # same pass folds the prototype-2 near-misses (``features`` -> ``traits``,
    # a ``stats`` object's members) and completes structured damage parts
    # (spec: structured attack damage and the missing stat aspects).
    entities = canonicalize_stat_blocks(entities)
    issues = collect_stat_issues(entities)
    # Conform-first (see the docstring): Monster power-only misses go
    # straight to the deterministic conform before any LLM pass; NPC/BBEG
    # power misses are no longer violations at all (the NPC oracle,
    # knowledge._check_power), so they never reach here.
    first = conform_first_targets(issues)
    if first:
        entities = conform_stat_power(entities, first)
        entities = canonicalize_stat_blocks(entities)
        issues = collect_stat_issues(entities)
    # Repair calls carry the strict repair-response schema via a settings
    # copy (spec: edgeless repair scope) — the wave calls keep the wave
    # envelope, the record/name gates keep plain settings (Never list).
    # Both the initial repair call and _run_repair's one JSON retry ride
    # these settings, so the shape is enforced on every repair attempt.
    if repair_response_format is None:
        repair_settings = settings
    else:
        repair_settings = dataclasses.replace(settings, response_format=repair_response_format)
    for attempt in (1, 2, 3):
        if not issues:
            break
        if not _job_still_running(job):
            return entities, True
        before = {issue.position: issue.entity.data.get("stat_block") for issue in issues}
        # One call per failing entity (spec step 2, after the record-chunk
        # precedent): the prompt names a single block, the parser demands
        # exactly its ref, and the merge below lands by ref — a sibling's
        # drift can no longer ride on another block's converging numbers.
        # A cancel racing the per-entity sequence stops without merging
        # this attempt (the merge lands only after the loop).
        repaired: dict[int, dict[str, Any]] = {}
        for issue in issues:
            if not _job_still_running(job):
                return entities, True
            single = _run_repair(
                budget=budget,
                provider=provider,
                settings=repair_settings,
                prompt=build_stat_repair_prompt([issue], attempt=attempt),
                parse=parse_stat_repair_output,
                positions=[issue.position],
                label="stat",
                retry_note='Return ONLY a "stat_blocks" list — one entry '
                '{"ref": "E<position>", "stat_block": {...}} with the full corrected block.',
            )
            repaired.update(single)
        _log_stat_repair_scope_breaches(
            issues, before, repaired, job_id=job.id, attempt=attempt, wave=wave
        )
        # Surgical merge (spec: strip drift): repair output is model output —
        # converging numbers may ride with out-of-scope rider sections
        # (attempt-8 shape). Merge ONLY each issue's scope_for_violations
        # sections (the same map the prompt and breach log share); an
        # out-of-scope key reverts to the pre-repair block (added riders are
        # dropped, deleted riders restored). The breach log above already
        # fired, so telemetry is kept while drift is not. A missing
        # pre-repair block is whole-block scope — nothing to strip.
        stripped: dict[int, dict[str, Any]] = {}
        for issue in issues:
            new = repaired.get(issue.position)
            if new is None:
                continue
            old = before.get(issue.position)
            if not isinstance(old, dict):
                stripped[issue.position] = new
                continue
            allowed = scope_for_violations(issue.violations)
            merged: dict[str, Any] = {}
            for key in set(old) | set(new):
                if key in allowed:
                    if key in new:
                        merged[key] = new[key]
                elif key in old:
                    merged[key] = old[key]
            # A repair that degrades an identity field is never a fix (ladder
            # 2026-09-11/12: attempt 19 dropped identity.class and later
            # passes — scope includes identity — edited around the hole;
            # Qwen3.8 rung-10 attempt 1 dropped identity.level twice in a
            # row, so the repair chased a level the validator could no longer
            # read). Restore a pre-repair value whose shape the validator
            # wants when the post-repair value lost that shape (dropped,
            # blanked, or re-typed): a same-shape edit — a deliberate level
            # or class change — still lands, and an invalid original keeps
            # failing as before. A role change skips the restore (an
            # NPC -> Monster repair legitimately retires level for cr).
            old_identity = old.get("identity")
            merged_identity = merged.get("identity")
            if (
                isinstance(old_identity, dict)
                and isinstance(merged_identity, dict)
                and merged_identity.get("role") == old_identity.get("role")
            ):
                restored = {
                    field: old_identity[field]
                    for field in _IDENTITY_GUARDED_FIELDS
                    if field in old_identity
                    and identity_field_ok(field, old_identity[field])
                    and identity_field_allowed(merged_identity.get("role"), field)
                    and not identity_field_ok(field, merged_identity.get(field))
                }
                if restored:
                    merged["identity"] = {**merged_identity, **restored}
            stripped[issue.position] = merged
        entities = apply_stat_repairs(entities, stripped)
        # The repair response is model output like any other: re-canonicalize
        # so the block the auditor re-checks (and the block that commits) is
        # the canonical one. Measured live 2026-09-11: a repair shipped
        # ``2d6 + 13`` with ``average: 16``, and the auditor trusts the
        # stated average — the parts and the dice must agree.
        entities = canonicalize_stat_blocks(entities)
        issues = collect_stat_issues(entities)
    if issues:
        # The repair passes are LLM shots at arithmetic a model cannot do:
        # before failing the job, give each block one deterministic chance
        # to meet its own DMG row. Only power-band violations are touched;
        # the model's words, actions and identity survive untouched (see
        # statblocks.conform_power).
        entities = conform_stat_power(entities, issues)
        issues = collect_stat_issues(entities)
    if issues:
        raise JobPayloadError(stat_failure_message(issues))
    return entities, False


def _changed_paths(old: Any, new: Any, path: str = "") -> list[str]:
    """Dotted/bracketed paths where two JSON values differ (spec: edgeless
    repair scope) — the breach log names nested drift (``actions[0].damage``)
    not just top-level sections, so an E8-class bonus rewrite under an
    unchanged ``actions`` key still logs."""
    if isinstance(old, dict) and isinstance(new, dict):
        paths: list[str] = []
        for key in set(old) | set(new):
            child = f"{path}.{key}" if path else str(key)
            if key in old and key in new and old[key] != new[key]:
                if isinstance(old[key], (dict, list)) and isinstance(new[key], type(old[key])):
                    paths.extend(_changed_paths(old[key], new[key], child))
                else:
                    paths.append(child)
            elif key not in old or key not in new:
                paths.append(child)
        return paths
    if isinstance(old, list) and isinstance(new, list):
        paths = []
        for index, (item_old, item_new) in enumerate(zip(old, new, strict=False)):
            child = f"{path}[{index}]"
            if item_old != item_new:
                if isinstance(item_old, (dict, list)) and isinstance(item_new, type(item_old)):
                    paths.extend(_changed_paths(item_old, item_new, child))
                else:
                    paths.append(child)
        for index in range(min(len(old), len(new)), max(len(old), len(new))):
            paths.append(f"{path}[{index}]")
        return paths
    return [path] if old != new else []


def _log_stat_repair_scope_breaches(
    issues: Sequence[StatIssue],
    before: Mapping[int, Any],
    repaired: Mapping[int, dict[str, Any]],
    *,
    job_id: str,
    attempt: int,
    wave: int | None,
) -> None:
    """Log-only scope check on what the model actually emitted (spec:
    edgeless repair scope): for each repaired position, the changed paths
    versus the pre-repair block must sit inside ``scope_for_violations``
    for that issue's violations. Breaches log with paths plus job, wave,
    and attempt for the next decision; the re-audit stays authoritative —
    never a new failure class, never silent drift, never merge rejection.
    Compared against the raw repair output (not the re-canonicalized
    block) so the gate's own folding is never misread as model drift; a
    missing pre-repair block is whole-block scope, nothing to flag."""
    for issue in issues:
        old = before.get(issue.position)
        new = repaired.get(issue.position)
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        allowed = scope_for_violations(issue.violations)
        paths = _changed_paths(old, new)
        extra = sorted(path for path in paths if path.split(".")[0].split("[")[0] not in allowed)
        if extra:
            logger.warning(
                "stat repair scope breach: E%s changed %s outside EDIT SCOPE %s "
                "(violations: %s; job %s, wave %s, attempt %s)",
                issue.position,
                extra,
                sorted(allowed),
                list(issue.violations),
                job_id,
                wave if wave is not None else "?",
                attempt,
            )


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


class _Wave2StaleEndpoints(Exception):
    """The bounded full-repass signal (M4): the wave-2 commit found its
    validated endpoints gone — a DM DELETE landed mid-job — so neither the
    original base nor the cheap rebase can commit the staged subgraph.
    ``run_build_in`` re-runs the WHOLE wave-2 pass exactly once against the
    new head; a second loss is an actively-rewritten world and fails loud."""


def normalize_entity_name(name: str) -> str:
    """The dedup identity key (owner rule 2026-09-12): casefolded, leading
    article stripped, whitespace collapsed, trailing punctuation dropped.
    EXACT-name matching on purpose — epithet variants ("Sim (The Drowned)")
    are not merged; the wave-2 prompt rule deters them and the DM prunes
    survivors. A fuzzy merge is the two-Jorahs hazard the ledger warned
    about and stays out of scope."""
    normalized = " ".join(name.casefold().split()).strip(".!?,;:")
    for article in ("the ", "a ", "an "):
        if normalized.startswith(article):
            return normalized[len(article) :]
    return normalized


def _merge_with_world(
    campaign_id: str,
    entities: Sequence[models.EntityInput],
    edges: Sequence[models.EdgeInput],
) -> tuple[
    list[models.EntityInput],
    list[models.EdgeInput],
    list[models.EntityInput],
    list[models.EdgeInput],
    dict[str, Any],
]:
    """The upsert rule (owner decision 2026-09-12, resolving the 2.3
    re-submit entry): an incoming entity whose (kind, normalized name)
    matches a committed entity of this campaign UPDATES that row instead
    of creating a twin — the store's explicit-id update contract (AD-2:
    inbound edges and media survive) does the work, so the commit path
    itself is untouched. Edges follow: endpoints remap to the surviving
    row, self-loops and relationships the world already has are dropped
    (the store would reject them as duplicates anyway). An update whose
    content is byte-equal to the row is skipped entirely — a re-submit of
    the same lists writes zero events. Twins WITHIN one wave merge the
    same way (first occurrence wins). Returns ``(staged_entities,
    staged_edges, roster, remapped_edges, report)``: what the store
    should write, the FINAL-ID roster the wave represents (wave 2 seeds
    its retrieval from it, so merged waves stay wirable), the wave's
    wiring view, and the audit report."""
    with session_scope() as session:
        world = [
            (row.id, row.kind, row.name, row.text, row.data)
            for row in session.scalars(
                select(models.Entity).where(models.Entity.campaign_id == campaign_id)
            )
        ]
        live_relationships = {
            (row.src, row.dst, row.type)
            for row in session.scalars(
                select(models.Edge).where(models.Edge.campaign_id == campaign_id)
            )
        }
    index: dict[tuple[str, str], str] = {}
    for row_id, kind, name, _text, _data in world:
        index.setdefault((kind, normalize_entity_name(name)), row_id)
    rows_by_id = {row_id: (kind, name, text, data) for row_id, kind, name, text, data in world}
    resolution: dict[str, str] = {}
    seen_keys: dict[tuple[str, str], str] = {}
    staged: list[models.EntityInput] = []
    roster: list[models.EntityInput] = []
    taken: set[str] = set()
    merge_log: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for entity in entities:
        staged_id = entity.id or ids.new_id()
        key = (entity.kind, normalize_entity_name(entity.name))
        target = index.get(key) or seen_keys.get(key) or staged_id
        seen_keys.setdefault(key, target)
        resolution[staged_id] = target
        if target in taken:
            continue  # in-batch twin: folded into the first occurrence
        taken.add(target)
        final = dataclasses.replace(entity, id=target)
        roster.append(final)
        if target == staged_id:
            staged.append(final)
            continue
        row = rows_by_id.get(target)
        if row is not None and row == (entity.kind, entity.name, entity.text, entity.data):
            unchanged.append({"name": entity.name, "kind": entity.kind, "into": target})
            continue
        staged.append(final)
        merge_log.append({"name": entity.name, "kind": entity.kind, "into": target})

    remapped: list[models.EdgeInput] = []
    staged_edges: list[models.EdgeInput] = []
    dropped_edges: list[dict[str, Any]] = []
    staged_relationships: set[tuple[str, str, str]] = set()
    for edge in edges:
        src = resolution.get(edge.src, edge.src)
        dst = resolution.get(edge.dst, edge.dst)
        if src == dst:
            dropped_edges.append(
                {
                    "src": edge.src,
                    "dst": edge.dst,
                    "type": edge.type,
                    "why": "self-loop after merge",
                }
            )
            continue
        final_edge = dataclasses.replace(edge, src=src, dst=dst)
        remapped.append(final_edge)
        relationship = (src, dst, edge.type)
        if relationship in live_relationships or relationship in staged_relationships:
            dropped_edges.append(
                {
                    "src": edge.src,
                    "dst": edge.dst,
                    "type": edge.type,
                    "why": "duplicate relationship",
                }
            )
            continue
        staged_relationships.add(relationship)
        staged_edges.append(final_edge)
    return (
        staged,
        staged_edges,
        roster,
        remapped,
        {
            "merged": merge_log,
            "unchanged": unchanged,
            "dropped_edges": dropped_edges,
        },
    )


class _MergeOutcome(NamedTuple):
    """One wave's commit: the revision written (or the head that stands
    when the merge left nothing to write), the FINAL-ID roster the wave
    represents (every incoming entity resolved to its committed row —
    wave 2 seeds its retrieval from this, so merged waves stay wirable),
    the wave's wiring view (remapped edges, duplicates included — they
    describe what the wave asserts, the report says what was written),
    and the audit report."""

    revision: models.Revision
    roster: list[models.EntityInput]
    edges: list[models.EdgeInput]
    report: dict[str, Any]


def _commit_wave(
    campaign_id: str,
    entities: Sequence[models.EntityInput],
    edges: Sequence[models.EdgeInput],
    base_revision: str | None,
    *,
    allow_orphans: bool = False,
    label: str,
) -> _MergeOutcome:
    """Merge against the live world (upsert), then commit one wave with
    the cheap stale rebase (M4): a DM edit between the generation read and
    the commit makes ``base_revision`` stale — at 100-entity scale that
    window is MINUTES, and the pre-rebase runner incinerated the whole
    generation on it. The staged subgraph is fresh ULIDs whose edges
    resolve inside the wave or the still-live context, so re-committing
    against the NEW head is valid whenever the endpoints exist; the store
    re-checks everything regardless (a deleted endpoint raises
    ``DanglingEdgeError`` — wave 2's re-pass signal). Bounded to ONE
    rebase: a second stale head means an actively-editing DM and
    propagates. The merge is RECOMPUTED after a rebase: a DM delete that
    moved the head also removes the row an earlier merge resolved into, so
    the entity re-stages as a fresh create under its own ULID instead of
    resurrecting the deleted one."""
    staged_entities, staged_edges, roster, remapped_edges, report = _merge_with_world(
        campaign_id, entities, edges
    )
    for note in report["merged"]:
        logger.info(
            "%s upsert: %s %r merged into committed row %s",
            label,
            note["kind"],
            note["name"],
            note["into"],
        )
    if not staged_entities and not staged_edges:
        with session_scope() as session:
            head = latest_revision(session, campaign_id)
        if head is None:
            raise JobPayloadError(f"{label}: nothing left to commit after the merge")
        logger.info(
            "%s commit is a no-op after merge (%d unchanged) — head %s stands",
            label,
            len(report["unchanged"]),
            head.id,
        )
        return _MergeOutcome(head, roster, remapped_edges, report)
    try:
        revision = commit_subgraph(
            campaign_id,
            staged_entities,
            staged_edges,
            base_revision=base_revision,
            allow_orphans=allow_orphans,
        )
    except StaleRevisionError:
        with session_scope() as session:
            head = latest_revision(session, campaign_id)
        rebased = head.id if head is not None else None
        logger.info("%s commit rebased onto head %s (stale base %s)", label, rebased, base_revision)
        staged_entities, staged_edges, roster, remapped_edges, report = _merge_with_world(
            campaign_id, entities, edges
        )
        if not staged_entities and not staged_edges:
            assert head is not None  # a merge that skipped everything implies committed rows
            return _MergeOutcome(head, roster, remapped_edges, report)
        revision = commit_subgraph(
            campaign_id,
            staged_entities,
            staged_edges,
            base_revision=rebased,
            allow_orphans=allow_orphans,
        )
    return _MergeOutcome(revision, roster, remapped_edges, report)


def _run_wave1_chunks(
    job: models.Job,
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    seed: models.Campaign,
    notes: str,
    chunks: Sequence[Sequence[tuple[int, str, str]]],
    *,
    ceiling: int,
    progress: Callable[[float], None],
) -> tuple[list[models.EntityInput], list[models.EdgeInput], bool, list[dict[str, Any]]]:
    """Chunked wave-1 generation plus the edges-only wiring pass (M1).

    Each chunk call is pinned to its slice (count AND ref enums over its
    GLOBAL positions), carries the full DM notes, and validates its
    entities independently — a chunk's malformed output retries once
    inside ``_call_wave`` and then fails the job with ZERO commits, exactly
    like the single call it replaces, but the blast radius is one bounded
    call instead of a 15-minute monolith. Chunks merge in fixed positional
    order (AD-16: same model outputs, same committed graph). The wiring
    pass then adds cross-chunk edges over the compact roster; it is
    BEST-EFFORT — any failure degrades to committing the chunk-internal
    edges only (the edgeless commit is legal, owner verdict 2026-09-11:
    the DM prunes). The assembled edge set then passes layer 2's kind
    cleanup (one bounded repair; residual violations drop with the audit —
    wave-1 edges are best-effort, same verdict). Returns
    ``(entities, edges, cancelled, kind_dropped)`` where ``kind_dropped``
    is the audit of edges dropped by the cleanup (empty when none)."""
    entities: list[models.EntityInput] = []
    assigned_ids: list[str] = []
    raw_edges: list[Any] = []
    total = len(chunks)
    retry_note = 'Return ONLY the one JSON object: {"entities": [...], "edges": [...]}.'
    for index, chunk in enumerate(chunks):
        if not _job_still_running(job):
            return entities, [], True, []
        refs = [f"E{position}" for position, _section, _entry in chunk]
        chunk_settings = dataclasses.replace(
            settings,
            response_format=build_wave_schema(len(chunk), refs=refs),
            max_tokens=_wave_max_tokens(len(chunk), ceiling),
        )
        prompt = build_wave1_chunk_prompt(seed, notes, chunk, chunk_index=index, chunk_total=total)
        parsed = _call_wave(
            budget,
            provider,
            chunk_settings,
            prompt,
            label=f"wave1_chunk{index}",
            parse=lambda text: parse_build_output(text, wave=1),
            retry_note=retry_note,
            ceiling=ceiling,
        )
        raw_entities = parsed.get("entities")
        chunk_raw_edges = parsed.get("edges")
        assert isinstance(raw_entities, list) and isinstance(chunk_raw_edges, list)
        chunk_entities, chunk_ids = _validate_entities(1, raw_entities, ref_offset=chunk[0][0])
        entities.extend(chunk_entities)
        assigned_ids.extend(chunk_ids)
        raw_edges.extend(chunk_raw_edges)
        progress(0.05 + 0.30 * (index + 1) / total)
    # The wiring pass: edges-only over the compact full roster — the proven
    # anchor-repair shape (frozen entities, endpoint/type enums), so a rename
    # or an invented endpoint is unrepresentable on grammar backends.
    if not _job_still_running(job):
        return entities, [], True, []
    valid_refs = frozenset(f"E{position}" for position in range(len(entities)))
    wiring_roster = [
        (f"E{position}", entity.name, entity.kind, _wiring_excerpt(entity))
        for position, entity in enumerate(entities)
    ]
    wiring_settings = dataclasses.replace(
        settings,
        response_format=build_anchor_repair_schema(sorted(valid_refs), []),
        max_tokens=min(ceiling, EDGES_CALL_MAX_TOKENS),
    )
    try:
        wiring_rows = _call_wave(
            budget,
            provider,
            wiring_settings,
            _build_wiring_prompt(seed, wiring_roster),
            label="wave1_wiring",
            parse=_parse_wiring_output,
            retry_note='Return ONLY {"edges": [...]} — the additional typed edges.',
            ceiling=ceiling,
        )
        raw_edges.extend(_filter_wiring_edges(wiring_rows, valid_refs))
    except (JobPayloadError, ProviderError, BudgetExceededError) as exc:
        logger.warning(
            "wave-1 wiring pass skipped (%s: %s) — committing chunk edges only (job %s)",
            exc.__class__.__name__,
            exc,
            job.id,
        )
    cleaned_edges, kind_dropped = _clean_edge_kinds(
        wave=1,
        raw_edges=raw_edges,
        entity_roster=[
            (f"E{position}", entity.name, entity.kind) for position, entity in enumerate(entities)
        ],
        budget=budget,
        provider=provider,
        settings=settings,
        ceiling=ceiling,
        allow_drop=True,
    )
    edges, _anchored = _resolve_edges(1, cleaned_edges, assigned_ids)
    progress(0.42)
    return entities, edges, False, kind_dropped


def _drop_roster_twins(
    parsed: dict[str, Any], roster_keys: set[tuple[str, str]]
) -> tuple[dict[str, Any], list[str]]:
    """The twin gate (owner decision 2026-09-12): a wave-2 entity whose
    (kind, normalized name) EXACTLY matches a wave-1 roster entry is
    dropped before validation — the roster subject already owns that
    identity, and the upsert merge would otherwise let the notes-derived
    thin record overwrite the full committed one. N-refs are positional,
    so kept entities are renumbered and edges rewritten through the ref
    map in the same pass; edges that pointed at a twin follow it out, and
    a survivor the drop orphans goes through the existing anchor-repair
    taxonomy. Epithet variants are the prompt rule's job, not this gate's
    (exact matching is deliberate — see ``normalize_entity_name``)."""
    raw_entities = parsed.get("entities")
    raw_edges = parsed.get("edges")
    if not isinstance(raw_entities, list) or not isinstance(raw_edges, list):
        return parsed, []
    kept: list[Any] = []
    dropped: list[str] = []
    dropped_refs: set[Any] = set()
    ref_map: dict[Any, str] = {}
    for raw in raw_entities:
        if (
            isinstance(raw, dict)
            and (
                raw.get("kind"),
                normalize_entity_name(str(raw.get("name", ""))),
            )
            in roster_keys
        ):
            dropped.append(str(raw.get("name")))
            dropped_refs.add(raw.get("ref"))
            continue
        if isinstance(raw, dict):
            new_ref = f"N{len(kept)}"
            ref_map[raw.get("ref")] = new_ref
            kept.append({**raw, "ref": new_ref})
        else:
            kept.append(raw)
    if not dropped:
        return parsed, []
    edges: list[Any] = []
    for edge in raw_edges:
        if not isinstance(edge, dict):
            edges.append(edge)
            continue
        if edge.get("src") in dropped_refs or edge.get("dst") in dropped_refs:
            continue
        edges.append(
            {
                **edge,
                "src": ref_map.get(edge.get("src"), edge.get("src")),
                "dst": ref_map.get(edge.get("dst"), edge.get("dst")),
            }
        )
    return {"entities": kept, "edges": edges}, dropped


def _run_wave2(
    job: models.Job,
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    seed: models.Campaign,
    notes: str,
    entities_1: Sequence[models.EntityInput],
    base_revision: str | None,
    *,
    ceiling: int,
    progress: Callable[[float], None],
) -> tuple[_MergeOutcome, dict[str, Any]] | None:
    """One FULL wave-2 pass (M4's re-pass unit): retrieve -> two-tier
    context (M2) -> prompt -> bounded-taxonomy call -> roster-twin drop ->
    anchor validation against the FULL wave-1 roster -> the one edges-only
    anchor repair -> the three gates -> commit with the upsert merge and
    the cheap rebase. Returns ``(outcome, wave_result)``, or None when the
    job was cancelled at any
    poll. Raises ``_Wave2StaleEndpoints`` when the commit — even rebased —
    finds deleted endpoints; the caller re-runs this whole pass once."""
    context_entities, context_edges = retrieve_neighborhood(
        job.campaign_id,
        seed_ids=_entity_ulids(entities_1),
        depth=RETRIEVAL_DEPTH,
        entity_cap=RETRIEVAL_ENTITY_CAP,
    )
    core_count = min(len(entities_1), len(context_entities))
    # Two-tier context (M2): the detail tier stays the AR6 neighborhood
    # (24 rows, full data); the compact tier names EVERY wave-1 entity the
    # cap could not carry, so a 100-entity core stays wirable from notes.
    # Pre-tier, a legal edge to C30 died as a phantom orphan (ledger,
    # spec-2.3 review) and the model could not even see the roster past
    # C23. Contiguity invariant: the wave-1 core is always
    # anchor_context[:full_core] — retrieval seeds with the wave-1 ULIDs,
    # so the detail tier's first core_count rows are wave-1, and the
    # compact tier continues them (a sub-cap wave 1 leaves neighbors in
    # the detail tail and an empty compact tier).
    detail_refs = _context_refs(context_entities)
    detail_ids = {ref.id for ref in detail_refs}
    compact = [
        ContextRef(cast(str, entity.id), entity.name, entity.kind)
        for entity in entities_1
        if entity.id not in detail_ids
    ]
    compact_core = [
        (len(detail_refs) + index, ref.name, ref.kind) for index, ref in enumerate(compact)
    ]
    anchor_context = detail_refs + compact
    full_core = core_count + len(compact)
    core_ids = frozenset(_entity_ulids(entities_1))
    if not _job_still_running(job):
        return None
    progress(0.55)
    prompt_2 = build_wave2_prompt(
        seed,
        notes,
        (context_entities, context_edges),
        core_count=full_core,
        compact_core=compact_core,
    )
    wave_settings = dataclasses.replace(
        settings,
        response_format=build_wave_schema(
            max_entities=WAVE2_MAX_ENTITIES, max_edges=WAVE2_MAX_EDGES
        ),
    )
    parsed_2 = _call_wave(
        budget,
        provider,
        wave_settings,
        prompt_2,
        label="wave2",
        parse=lambda text: parse_build_output(text, wave=2),
        retry_note='Return ONLY the one JSON object: {"entities": [...], "edges": [...]}.',
        ceiling=ceiling,
    )
    parsed_2, twins_dropped = _drop_roster_twins(
        parsed_2, {(entity.kind, normalize_entity_name(entity.name)) for entity in entities_1}
    )
    if twins_dropped:
        logger.info("wave 2: %d roster twin(s) dropped: %s", len(twins_dropped), twins_dropped)
    if not parsed_2["entities"] and not parsed_2["edges"]:
        # Every notes subject duplicated the roster — nothing to commit;
        # the wave-1 head stands and the job completes with an empty
        # wave-2 result carrying the drop list.
        logger.info("wave 2: all subjects duplicate the roster — nothing to commit")
        with session_scope() as session:
            head = latest_revision(session, job.campaign_id)
        assert head is not None  # wave 1 committed moments ago
        return (
            _MergeOutcome(
                head,
                [],
                [],
                {
                    "merged": [],
                    "unchanged": [],
                    "dropped_edges": [],
                    "twins_dropped": twins_dropped,
                },
            ),
            _wave_result(2, head.id, (), ()),
        )
    raw_entities_2 = parsed_2.get("entities") or []
    kinds_2 = _raw_entity_kinds(raw_entities_2)
    entity_roster_2: list[tuple[str, str, str | None]] = []
    for index, (row, kind) in enumerate(zip(raw_entities_2, kinds_2, strict=True)):
        name = row.get("name", "") if isinstance(row, dict) else ""
        entity_roster_2.append((f"N{index}", name if isinstance(name, str) else "", kind))
    cleaned_2, _kind_drop_2 = _clean_edge_kinds(
        wave=2,
        raw_edges=list(parsed_2.get("edges") or []),
        entity_roster=entity_roster_2,
        budget=budget,
        provider=provider,
        settings=settings,
        ceiling=ceiling,
        context=anchor_context,
        world_member_edges=_world_member_pairs(job.campaign_id),
        allow_drop=False,
    )
    parsed_2 = {**parsed_2, "edges": cleaned_2}
    progress(0.7)
    try:
        entities_2, edges_2 = _validate_subgraph(
            2, parsed_2, context=anchor_context, core_count=full_core, core_ids=core_ids
        )
    # NOTE: this handler must precede any `except JobPayloadError` —
    # _OrphanRetryError subclasses it, so a broader handler first would
    # swallow the retry signal and orphans would fail immediately.
    except _OrphanRetryError as exc:
        # ANCHOR_REPAIR (spec: repair sequence step 3) — the only
        # _validate_subgraph rejection with a repair pass: one edges-only
        # repair naming the orphans, over entities frozen from the first
        # attempt, through the same budget and the same gates below. The
        # core roster is the FULL wave-1 set (M2), so every committed
        # entity is a legal anchor. Renames and drops are unrepresentable
        # (no entity list is emitted). Still-orphan after the repair fails
        # the job with wave 1 committed; any other rejection stays
        # immediate.
        repaired_2 = _anchor_repair(
            job=job,
            budget=budget,
            provider=provider,
            settings=settings,
            first=parsed_2,
            orphans=exc.orphans,
            context=anchor_context,
            core_count=full_core,
            ceiling=ceiling,
        )
        if repaired_2 is None:
            return None
        entities_2, edges_2 = _validate_subgraph(
            2, repaired_2, context=anchor_context, core_count=full_core, core_ids=core_ids
        )
    # The same gates as wave 1 (dogfood fix 2026-09-09): wave-2 names
    # (name gate), then characters carry full AR24 records (record gate,
    # then the stat-block gate) before committing.
    entities_2 = strip_noncharacter_stat_blocks(entities_2)
    entities_2, cancelled = _enforce_entity_names(
        job, budget, provider, settings, entities_2, wave=2
    )
    if cancelled:
        return None
    entities_2, cancelled = _enforce_character_records(
        job, budget, provider, settings, entities_2, wave=2
    )
    if cancelled:
        return None
    entities_2, cancelled = _enforce_stat_blocks(
        job,
        budget,
        provider,
        settings,
        entities_2,
        repair_response_format=build_stat_repair_schema(),
        wave=2,
    )
    if cancelled:
        return None
    # Cancel-race poll: a cancel during the wave-2 call/validation must
    # not commit wave 2 — the failed wave writes nothing.
    if not _job_still_running(job):
        return None
    progress(0.9)
    try:
        outcome_2 = _commit_wave(
            job.campaign_id, entities_2, edges_2, base_revision, label="wave 2"
        )
    except DanglingEdgeError as exc:
        raise _Wave2StaleEndpoints(str(exc)) from exc
    outcome_2.report["twins_dropped"] = twins_dropped
    return outcome_2, _wave_result(2, outcome_2.revision.id, outcome_2.roster, outcome_2.edges)


def run_build_in(job: models.Job, provider: Callable[..., str], settings: LLMSettings) -> None:
    """Run one build-in job to a terminal state (complete_job/fail_job).

    Wave 1 commits first (AR5); wave 2 (notes) commits against wave 1's
    revision. Wave-1 GENERATION is chunked above one chunk's roster (M1,
    the 100-entity cut): bounded per-chunk calls with global E-refs, an
    edges-only wiring pass over the assembled roster (best-effort — a
    failure degrades to the legal edgeless commit), and a deterministic
    ordered merge; a roster that fits ONE chunk keeps the original single
    call byte-for-byte. Commits rebase once on a stale head instead of
    dying (M4 — a 100-entity job's generation window is minutes, and a DM
    hand-edit mid-job used to incinerate it); wave 2 additionally re-runs
    its whole pass once when the rebase finds endpoints deleted.
    ``report_progress`` marks per-chunk/per-gate milestones on the way to
    0.5 (wave 1 committed) and 1.0 (wave 2 committed) (AD-17 + H). A job
    cancelled before a call — or between a call and its commit — is a
    no-op for that wave; earlier committed waves stay. Any other failure
    (provider, budget, malformed output, invalid subgraph) propagates so
    the worker fails the job. Structure keeps its one bounded pass on
    wave 2 only (anchor repair); wave 1 commits edgeless (owner verdict
    2026-09-11) — the DM prunes. The job result carries the call
    telemetry (J): ``llm_calls`` per label, and the upsert audit:
    ``merge`` per wave (merged/unchanged/dropped_edges/twins_dropped).
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
    ceiling = settings.max_tokens
    last_progress = [0.0]

    def progress(value: float) -> None:
        """Monotone milestone progress (H): a 20-minute 100-entity job must
        never look wedged — every chunk/gate boundary moves the bar, and a
        milestone behind the bar never regresses it. Cancel-safe: a cancel
        landing between the poll and the write makes report_progress raise
        JobStateConflictError — swallowed here (a cancelled job has no
        progress to show), and the next cancel poll stops the run, the
        same race contract the terminal writes follow."""
        if value > last_progress[0]:
            last_progress[0] = value
            with contextlib.suppress(JobStateConflictError):
                report_progress(job.id, value)

    roster = _wave1_roster(payload)
    chunks = _wave1_chunks(roster)
    # Wave 1: the named sections -> a committed core (edgeless allowed).
    if not _job_still_running(job):
        return
    if len(chunks) <= 1:
        # Single-call path: the byte-identical prompt for any roster that
        # fits one chunk (the rung-10/25 regression property). The schema
        # gains the E1/E2 tightening (kind enum, ref enums, name minLength,
        # present-key data constraints) and the window is sized from the
        # pin (B); wave 1's copy pins the entities array to the trimmed
        # roster count (ladder rung 50: shape pinned but count free emitted
        # 28 then 25 of 50).
        refs = [f"E{position}" for position in range(len(roster))]
        wave1_settings = dataclasses.replace(
            settings,
            # An empty roster passes no refs: {"enum": []} is a legal JSON
            # Schema but a pathological GBNF alternation — with the count
            # pinned to 0 the items schema is unreachable anyway.
            response_format=build_wave_schema(len(roster), refs=refs or None),
            max_tokens=_wave_max_tokens(len(roster), ceiling),
        )
        prompt_1 = build_wave1_prompt(seed, payload)
        parsed_1 = _call_wave(
            budget,
            provider,
            wave1_settings,
            prompt_1,
            label="wave1",
            parse=lambda text: parse_build_output(text, wave=1),
            retry_note='Return ONLY the one JSON object: {"entities": [...], "edges": [...]}.',
            ceiling=ceiling,
        )
        raw_entities_1 = parsed_1.get("entities") or []
        kinds_1 = _raw_entity_kinds(raw_entities_1)
        entity_roster_1: list[tuple[str, str, str | None]] = []
        for index, (row, kind) in enumerate(zip(raw_entities_1, kinds_1, strict=True)):
            name = row.get("name", "") if isinstance(row, dict) else ""
            entity_roster_1.append((f"E{index}", name if isinstance(name, str) else "", kind))
        cleaned_1, kind_dropped_1 = _clean_edge_kinds(
            wave=1,
            raw_edges=list(parsed_1.get("edges") or []),
            entity_roster=entity_roster_1,
            budget=budget,
            provider=provider,
            settings=settings,
            ceiling=ceiling,
            allow_drop=True,
        )
        parsed_1 = {**parsed_1, "edges": cleaned_1}
        entities_1, edges_1 = _validate_subgraph(1, parsed_1)
        progress(0.2)
    else:
        entities_1, edges_1, cancelled, kind_dropped_1 = _run_wave1_chunks(
            job,
            budget,
            provider,
            settings,
            seed,
            notes,
            chunks,
            ceiling=ceiling,
            progress=progress,
        )
        if cancelled:
            return
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
    progress(0.3)
    # Record enforcement (dogfood fix 2026-09-09): every wave character
    # carries the full AR24 record — violations go through exactly one
    # bounded repair pass, same AR25 semantics as the stat gate below. It
    # runs FIRST so a repaired role is what the stat block is checked against.
    entities_1, cancelled = _enforce_character_records(
        job, budget, provider, settings, entities_1, wave=1
    )
    if cancelled:
        return
    progress(0.4)
    # Stat-block enforcement (AR24/AR25, spec-2.4): every character must carry
    # a valid minimal stat block before the wave commits — or up to three
    # bounded per-entity repair passes; a block still invalid after the
    # repairs fails the job with an error event, zero commits.
    entities_1, cancelled = _enforce_stat_blocks(
        job,
        budget,
        provider,
        settings,
        entities_1,
        repair_response_format=build_stat_repair_schema(),
        wave=1,
    )
    if cancelled:
        return
    progress(0.45)
    # Edgeless wave-1 commits through the store's FR2 backstop explicitly
    # (owner verdict 2026-09-11): the pipeline no longer requires internal
    # wiring, so the commit must not either — the DM prunes. Every other
    # caller keeps the default (reject), including wave 2 below.
    outcome_1 = _commit_wave(
        job.campaign_id, entities_1, edges_1, wave1_base, allow_orphans=True, label="wave 1"
    )
    revision_1 = outcome_1.revision
    touched = {edge.src for edge in outcome_1.edges} | {edge.dst for edge in outcome_1.edges}
    edgeless = sorted(entity.name for entity in outcome_1.roster if entity.id not in touched)
    if edgeless:
        logger.info("wave 1 committed edgeless: %s (job %s)", edgeless, job.id)
    waves: list[dict[str, Any]] = [
        _wave_result(1, revision_1.id, outcome_1.roster, outcome_1.edges)
    ]
    merge_reports: dict[str, Any] = {"wave1": outcome_1.report}
    if kind_dropped_1:
        merge_reports["wave1"]["edge_kind_dropped"] = kind_dropped_1
    # Cancel-race poll: a cancel that landed during wave 1's call/commit
    # leaves the committed core in place but stops before progress writes.
    if not _job_still_running(job):
        return
    progress(0.5)

    # Wave 2: the free-form notes -> a second subgraph anchored into the core.
    if notes:
        try:
            outcome = _run_wave2(
                job,
                budget,
                provider,
                settings,
                seed,
                notes,
                outcome_1.roster,
                revision_1.id,
                ceiling=ceiling,
                progress=progress,
            )
        except _Wave2StaleEndpoints as exc:
            # M4's bounded full re-pass: a DM delete mid-job took an
            # endpoint the validated wave referenced. Re-run the WHOLE
            # pass once against the live head; a second endpoint loss is
            # an actively-rewritten world and fails loud.
            logger.info("wave 2 re-pass against the new head (%s)", exc)
            with session_scope() as session:
                head = latest_revision(session, job.campaign_id)
            try:
                outcome = _run_wave2(
                    job,
                    budget,
                    provider,
                    settings,
                    seed,
                    notes,
                    outcome_1.roster,
                    head.id if head is not None else None,
                    ceiling=ceiling,
                    progress=progress,
                )
            except _Wave2StaleEndpoints as second:
                raise JobPayloadError(
                    "wave 2: the committed world changed twice mid-build "
                    f"(deleted endpoints: {second}) — re-submit the notes"
                ) from second
        if outcome is None:
            return
        outcome_2, wave2_result = outcome
        merge_reports["wave2"] = outcome_2.report
        # Cancel racing the wave-2 commit: the committed wave stays, but a
        # cancelled job gets no progress/terminal write.
        if not _job_still_running(job):
            return
        progress(1.0)
        waves.append(wave2_result)

    entity_count = sum(wave["entities"] for wave in waves)
    edge_count = sum(wave["edges"] for wave in waves)
    complete_job(
        job.id,
        result={
            "waves": waves,
            "entity_count": entity_count,
            "edge_count": edge_count,
            "llm_calls": budget.snapshot(),
            "merge": merge_reports,
        },
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
        *edge_guidance_lines(),
        "",
        "COUNTER SEMANTICS (one integer per edge)",
        *(f"- {edge_type}: {edge_counter_semantic(edge_type)}" for edge_type in sorted(EDGE_TYPES)),
    ]
    return "\n".join(lines)


def build_wave1_chunk_prompt(
    campaign_seed: models.Campaign,
    notes: str,
    chunk: Sequence[tuple[int, str, str]],
    *,
    chunk_index: int,
    chunk_total: int,
) -> str:
    """One wave-1 chunk's prompt (M1): the shared rules text plus ONLY this
    chunk's roster slice, every entry named with its GLOBAL E-ref.

    Pure and byte-deterministic (AD-16): a function of the seed, the notes,
    and the chunk slice — the identical static prefix across chunks keeps
    the backend's per-slot prompt cache warm. The FULL DM notes ride every
    chunk: a directive like "sanberi is level 18" must land in whichever
    chunk owns Sanberi (the 2026-09-10 level-miss bug class). Cross-chunk
    wiring is NOT asked for here — a later edges-only pass wires the
    assembled world (the proven anchor-repair shape).
    """
    start, end = chunk[0][0], chunk[-1][0]
    lines = [
        f"You are digesting PART {chunk_index + 1} of {chunk_total} of a TTRPG",
        "world-build-in submission into the world graph.",
        "Respond with exactly one JSON object — nothing else.",
        "",
        "CAMPAIGN SEED",
        f"title: {campaign_seed.title}",
        f"description: {campaign_seed.description}",
        f"theme: {campaign_seed.theme}",
        f"custom lore: {campaign_seed.custom_lore}",
        "",
        f"YOUR ENTITIES (emit EXACTLY {len(chunk)} entities, IN THIS ORDER — the first",
        f"is E{start}, the last is E{end}; the other parts' entities are generated",
        "separately and are NOT visible here):",
        *(f"- E{position} ({section}): {entry}" for position, section, entry in chunk),
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
        "Create the entities named above: characters for key figures, places for",
        "places, factions for factions — each character with its full record and",
        "stat block. Wire entities OF THIS PART to each other with typed directed",
        "edges where the entries plainly relate; an entity with no edge here is",
        "fine — a later pass wires the whole world. Edges must connect two",
        "different entities listed above — no self-loops.",
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
        f"The refs are FIXED global positions: your first entity is E{start}, your",
        f"second E{start + 1}, and so on up to E{end} (never E01, E007).",
        'Each edge: {"src": "<ref>", "dst": "<ref>", "type": "<vocabulary member>",',
        '  "counter": <integer, default 1>}.',
        f"Edge src/dst must be refs from YOUR ENTITIES list (E{start}..E{end}).",
        "Names must be non-blank.",
        "",
        *edge_guidance_lines(),
        "",
        "COUNTER SEMANTICS (one integer per edge)",
        *(f"- {edge_type}: {edge_counter_semantic(edge_type)}" for edge_type in sorted(EDGE_TYPES)),
    ]
    return "\n".join(lines)


def _wiring_excerpt(entity: models.EntityInput) -> str:
    """One compact wiring-roster line's excerpt (M1): the entity's own text,
    else its record's most telling line, whitespace-folded and truncated —
    enough for the wiring pass to see who belongs to what, small enough for
    a 300-line roster (~3K tokens)."""
    raw = entity.text
    if not (isinstance(raw, str) and raw.strip()):
        data = entity.data if isinstance(entity.data, dict) else {}
        raw = None
        for key in ("personality", "goals", "background"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                raw = value
                break
    folded = " ".join((raw or "").split())
    return folded[:100]


def _build_wiring_prompt(
    campaign_seed: models.Campaign,
    roster: Sequence[tuple[str, str, str, str]],
) -> str:
    """The edges-only wiring pass over the assembled wave-1 roster (M1):
    ref+name+kind+excerpt per entity, the closed vocabulary, and one demand
    — wire the world. The proven anchor-repair shape: entities are frozen
    and never re-emitted, the response is ``{"edges": [...]}`` under
    endpoint/type enums, so renames and drops are unrepresentable. The pass
    is BEST-EFFORT: on failure the wave commits with its chunk-internal
    edges only (edgeless commit is legal — owner verdict 2026-09-11, the DM
    prunes), never a job death over wiring."""
    lines = [
        "You are wiring an already-generated TTRPG world: the entities below are",
        "RECORDED — you add the typed edges between them, nothing else.",
        "Respond with exactly one JSON object — nothing else.",
        "",
        "CAMPAIGN SEED",
        f"title: {campaign_seed.title}",
        f"description: {campaign_seed.description}",
        f"theme: {campaign_seed.theme}",
        f"custom lore: {campaign_seed.custom_lore}",
        "",
        f"RECORDED ENTITIES ({len(roster)} — frozen, never re-emit them):",
        *(f"- {ref} {name!r} ({kind}) — {excerpt}" for ref, name, kind, excerpt in roster),
        "",
        "TASK",
        'Respond with one JSON object: {"edges": [...]} wiring this world: every',
        "entity should carry at least one edge where the names and excerpts make a",
        "relationship plain (members to factions, factions and people to places,",
        "rivals, allies, enemies, debts as the entries imply). Do not force an edge",
        "where nothing relates. Do not invent entities.",
        'Each edge: {"src": "<ref>", "dst": "<ref>", "type": "<vocabulary member>",',
        '  "counter": <integer, default 1>}.',
        "src/dst must be refs from the list above; edges must connect two different",
        "entities — no self-loops; never repeat an identical edge.",
        "",
        *edge_guidance_lines(),
        "",
        "COUNTER SEMANTICS (one integer per edge)",
        *(f"- {edge_type}: {edge_counter_semantic(edge_type)}" for edge_type in sorted(EDGE_TYPES)),
    ]
    return "\n".join(lines)


def _filter_wiring_edges(raw: Sequence[Any], valid_refs: frozenset[str]) -> list[Any]:
    """Boundary filter for the best-effort wiring pass: keep only rows that
    are dict-shaped, name two DIFFERENT known refs, carry a vocabulary
    type, and an integer counter — the things ``_resolve_edges`` would
    otherwise die on. A bad row degrades to a drop (info log) instead of
    killing a validated 100-entity wave; exact-duplicate and self-loop
    handling stays in ``_resolve_edges`` (one boundary, one rule set)."""
    kept: list[Any] = []
    dropped = 0
    for row in raw:
        if (
            isinstance(row, dict)
            and row.get("type") in EDGE_TYPES
            and isinstance(row.get("src"), str)
            and isinstance(row.get("dst"), str)
            and row["src"] in valid_refs
            and row["dst"] in valid_refs
            and row["src"] != row["dst"]
            and type(row.get("counter", 1)) is int
        ):
            kept.append(row)
        else:
            dropped += 1
    if dropped:
        logger.info("wiring pass: dropped %d unusable edge row(s)", dropped)
    return kept


def build_wave2_prompt(
    campaign_seed: models.Campaign,
    notes: str,
    context: tuple[Sequence[models.Entity], Sequence[models.Edge]],
    *,
    core_count: int,
    compact_core: Sequence[tuple[int, str, str]] = (),
) -> str:
    """The wave-2 prompt: digest ``notes`` into entities wired into the core.

    Pure and byte-deterministic (AD-16): a function of the campaign seed,
    the notes, and the retrieved neighborhood. ``core_count`` is the FULL
    wave-1 core size (detail tier + compact tier); the compact tier (M2)
    rides as ``compact_core`` — ``(C-label index, name, kind)`` lines for
    every wave-1 entity the 24-row detail cap could not carry — so a
    100-entity core stays wirable from notes without a 200KB context. The
    detail serialization carries hard truths only. New entities use
    ``N<index>`` refs — never the wave-1 ``E<index>`` labels — so an edge
    can never silently target the wrong entity.
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
        *(
            (
                "",
                "COMPACT CORE ROSTER (recorded core entities, details omitted — every",
                "C-label below is a legal edge endpoint):",
                *(f"- C{index} {name!r} ({kind})" for index, name, kind in compact_core),
            )
            if compact_core
            else ()
        ),
        "",
        "NOTES TO DIGEST",
        notes.strip(),
        "",
        "TASK",
        "Create entities for the notable subjects of the notes (character/faction/place).",
        "Subjects that already appear in the detail context or the compact",
        "roster EXIST in the world — never create an entity for one of them;",
        "wire your new entities to that subject's C-label instead.",
        f"The CORE entities are {core_label} — the detail context entries first, then",
        f"the compact roster — the {core_count} entities this build created first.",
        "Wire every new entity to at least one CORE entity with a typed edge — no",
        "orphans. Detail context entries beyond the core are background only.",
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
        '"Monster" or "NPC": the role belongs in data.role, and an entity carrying',
        "record keys at the top level loses them — the record is read from data alone.",
        'A place or faction is flat: {"ref": "N0", "kind": "faction", "name": "The Guild",',
        '  "text": "the hard truth about it"} — data is optional for those two kinds.',
        "Refs are positional and canonical: the first new entity in the list is N0,",
        "the second N1, and so on (never N01, N007).",
        'Each edge: {"src": "<ref>", "dst": "<ref>", "type": "<vocabulary member>",',
        '  "counter": <integer, default 1>}.',
        "Edge src/dst may be a new-entity ref (N<index>) or a context ref (C<index>)",
        f"matching the detail context and the compact roster above (the core is {core_label}).",
        "Names must be non-blank.",
        "",
        *edge_guidance_lines(),
        "",
        "COUNTER SEMANTICS (one integer per edge)",
        *(f"- {edge_type}: {edge_counter_semantic(edge_type)}" for edge_type in sorted(EDGE_TYPES)),
    ]
    return "\n".join(lines)


#: ``data`` stays an OPEN object in the wave schema. The E2 present-key
#: constraints (identity role/level enums, attribute bounds, combat ints —
#: legal JSON Schema that binds only when the keys are present) were tried
#: and REVERTED on live evidence: ladder attempt d1 (Qwen3.8-27B, rung 10,
#: 2026-09-12) shipped every character with ``data.stat_block`` — the only
#: described key — and NO AR24 record fields at all, a described-key bias
#: under grammar sampling that turned one wave call into a six-record
#: repair storm and a terminal boss-shape death. The open-``data`` schema
#: never produced that shape across the whole green ladder. Identity
#: garbage (attempts 10-15's signature death) stays owned by the
#: validator, the identity merge guards, and the retry taxonomy.


def build_wave_schema(
    entity_count: int | None = None,
    refs: Sequence[str] | None = None,
    *,
    max_entities: int | None = None,
    max_edges: int | None = None,
) -> dict[str, Any]:
    """The flat envelope schema carried on build-in wave calls (spec: JSON-schema
    generation foundation) — the ``response_format`` wrapper follows the prototype's
    measured ``json_schema`` convention; the inner schema is flat and ``$ref``-free
    (GBNF subset). ``additionalProperties: false`` on the entity item makes
    beside-``data`` slips unrepresentable, and the edge ``type`` is an enum
    single-sourced from ``store.EDGE_TYPES`` (spec: edgeless repair scope), so an
    invented type is unemittable on grammar-enforcing backends. Parsing,
    validators, and all gates stay the backstop — the schema is the optimization,
    so fenced/prose output still parses identically.

    With ``entity_count`` the entities array is pinned to exactly that many
    items (ladder rung 50: the model emitted 28 then 25 of a 50-roster with
    shape pinned but count free — the roster is exact, so the schema is
    too); with ``refs`` each entity's ref is an enum of exactly its chunk's
    GLOBAL refs (E1) — the ref-discipline death class (rung 10 attempt 2's
    restarted 'E0') becomes unemittable. Tightened further (E1, the
    100-entity cut): ``kind`` is an enum of the closed contract set and
    ``name`` carries minLength 1. ``data`` stays an OPEN object — the E2
    present-key constraints were tried and reverted on live evidence (see
    the comment above ``build_wave_schema``: described-key bias under
    grammar sampling dropped whole AR24 records). ``max_entities``/``max_edges``
    bound the unpinned wave-2 output. Wave 1 carries the trimmed roster
    count (or its chunk slice); wave 2's notes-driven roster is
    model-decided and stays count-free.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "build_wave",
            "strict": True,
            "schema": {
                "type": "object",
                "required": ["entities", "edges"],
                "properties": {
                    "entities": {
                        "type": "array",
                        **(
                            {"minItems": entity_count, "maxItems": entity_count}
                            if entity_count is not None
                            else {}
                        ),
                        **({"maxItems": max_entities} if max_entities is not None else {}),
                        "items": {
                            "type": "object",
                            "required": ["ref", "kind", "name"],
                            "properties": {
                                "ref": {"enum": list(refs)}
                                if refs is not None
                                else {"type": "string"},
                                "kind": {"enum": sorted(ENTITY_KINDS)},
                                "name": {"type": "string", "minLength": 1},
                                "text": {"type": "string"},
                                "data": {"type": "object"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "edges": {
                        "type": "array",
                        **({"maxItems": max_edges} if max_edges is not None else {}),
                        "items": {
                            "type": "object",
                            "required": ["src", "dst", "type"],
                            "properties": {
                                "src": {"type": "string"},
                                "dst": {"type": "string"},
                                "type": {"enum": sorted(EDGE_TYPES)},
                                "counter": {"type": "integer"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
    }


def parse_build_output(text: str, wave: int = 1) -> dict[str, Any]:
    """Parse one wave's LLM output: fence-strip, then require a JSON object.

    An optional markdown fence is stripped tolerantly (see
    ``_strip_fence``); the result must be an object with ``entities`` and
    ``edges`` lists. Malformed output raises ``JobPayloadError`` naming
    the wave — the job fails, never a partial commit. A JSON-DECODE
    failure raises the ``_WaveJsonError`` subclass: ``_call_wave`` gives
    exactly that class one bounded re-elicitation (C, the retry taxonomy);
    semantic rejections inside well-formed JSON stay terminal.
    """
    stripped = _strip_fence(text)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise _WaveJsonError(f"wave {wave}: output is not valid JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise JobPayloadError(f"wave {wave}: output must be a JSON object")
    entities = parsed.get("entities")
    edges = parsed.get("edges")
    if not isinstance(entities, list) or not isinstance(edges, list):
        raise JobPayloadError(
            f"wave {wave}: output must be a JSON object with 'entities' and 'edges' lists"
        )
    return parsed


def build_anchor_repair_schema(new_refs: Sequence[str], core_refs: Sequence[str]) -> dict[str, Any]:
    """The edges-only schema carried on the wave-2 anchor-repair call
    (spec: repair sequence step 3) — the ``response_format`` wrapper follows
    the wave calls' measured ``json_schema`` convention; the inner schema is
    flat and ``$ref``-free (GBNF subset). The response is ``{"edges": [...]}``
    ONLY: no entity list is emitted, so renames and drops are
    unrepresentable. ``src``/``dst`` are enums of exactly the known refs
    (the frozen new-entity N refs plus the visible core C labels), and the
    edge ``type`` is an enum single-sourced from ``store.EDGE_TYPES`` — an
    invented type or an off-roster endpoint is unemittable on
    grammar-enforcing backends. Parsing, validators, and the anchor check
    stay the backstop — the schema is the optimization.
    """
    endpoints = sorted([*new_refs, *core_refs])
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "build_anchor_repair",
            "strict": True,
            "schema": {
                "type": "object",
                "required": ["edges"],
                "properties": {
                    "edges": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["src", "dst", "type"],
                            "properties": {
                                "src": {"enum": endpoints},
                                "dst": {"enum": endpoints},
                                "type": {"enum": sorted(EDGE_TYPES)},
                                "counter": {"type": "integer"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
    }


def _build_anchor_repair_prompt(
    *,
    orphans: Sequence[tuple[str, int]],
    new_roster: Sequence[tuple[str, str, str]],
    core_roster: Sequence[tuple[str, str]],
    existing_edges: Sequence[str],
) -> str:
    """The anchor-repair prompt (spec: repair sequence step 3): frozen
    rosters plus the orphan demand. New entities are listed as ref+name+kind
    and the core as C-label+name — already recorded, never re-emitted — so
    the model returns additional edges only. Existing edges are listed so
    the model does not echo them (an echo is harmless: the merge dedups
    exact copies, and the merged wave re-runs the same validation)."""
    named = ", ".join(f"{name!r} (N{position})" for name, position in orphans)
    core_label = f"C0..C{len(core_roster) - 1}" if core_roster else "no visible core"
    lines = [
        "Your previous wave-2 response left orphan entities with no edge to the core: "
        f"{named} — every new entity MUST have >= 1 edge to a CORE entity ({core_label}).",
        "",
        "ANCHOR REPAIR — respond with exactly one JSON object, nothing else.",
        "",
        "FROZEN NEW ENTITIES (already recorded — never re-emit them):",
        *(f"- {ref} {name!r} ({kind})" for ref, name, kind in new_roster),
        "",
        "CORE ENTITIES (anchor targets):",
        (
            "\n".join(f"- {label} {name!r}" for label, name in core_roster)
            if core_roster
            else "- (none visible)"
        ),
        "",
        "EXISTING EDGES (already recorded — do not repeat them):",
        ("\n".join(f"- {edge}" for edge in existing_edges) if existing_edges else "- (none)"),
        "",
        "TASK",
        'Respond with one JSON object: {"edges": [...]} containing ONLY the additional',
        "edges needed so every orphan above has >= 1 edge to a CORE entity.",
        'Each edge: {"src": "<ref>", "dst": "<ref>", "type": "<vocabulary member>",',
        '  "counter": <integer, default 1>}.',
        "src/dst must be a frozen new-entity ref or a core ref from the rosters above.",
        "Edges must connect two different entities — no self-loops.",
        "",
        *edge_guidance_lines(),
    ]
    return "\n".join(lines)


def _parse_edges_only_output(text: str, *, what: str) -> list[Any]:
    """Parse an edges-only response (``{"edges": [...]}``) — the shape the
    wave-1 wiring pass and the wave-2 anchor repair share. ``what`` names
    the call in failures. A JSON-decode failure raises ``_WaveJsonError``
    (``_call_wave``'s one bounded structural retry — the anchor repair had
    ZERO retries before the taxonomy cut); a contract violation inside
    well-formed JSON stays terminal ``JobPayloadError``. An ``entities``
    key, if present, is ignored: both callers freeze the entities, so a
    renamed or dropped roster is unrepresentable by construction (the
    attempt-8 shape now commits)."""
    stripped = _strip_fence(text)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise _WaveJsonError(f"{what}: output is not valid JSON ({exc})") from exc
    if not isinstance(parsed, dict):
        raise JobPayloadError(f"{what}: output must be a JSON object with an 'edges' list")
    edges = parsed.get("edges")
    if not isinstance(edges, list):
        raise JobPayloadError(f"{what}: output must be a JSON object with an 'edges' list")
    return edges


def _parse_anchor_repair_output(text: str) -> list[Any]:
    """The anchor-repair parse: edges-only under the wave-2 label."""
    return _parse_edges_only_output(text, what="wave 2: anchor repair")


def _parse_wiring_output(text: str) -> list[Any]:
    """The wiring-pass parse: edges-only under the wave-1 label."""
    return _parse_edges_only_output(text, what="wave 1: wiring pass")


def _anchor_repair(
    *,
    job: models.Job,
    budget: CallBudget,
    provider: Callable[..., str],
    settings: LLMSettings,
    first: dict[str, Any],
    orphans: Sequence[tuple[str, int]],
    context: Sequence[ContextRef] = (),
    core_count: int = 0,
    ceiling: int,
) -> dict[str, Any] | None:
    """The one bounded anchor repair (spec: repair sequence step 3) — the
    wave-2 anchor's only repair pass: frozen rosters (new entities as
    ref+name+kind, the FULL core as C-label+name — M2: every wave-1 entity
    is a legal anchor, not just the 24 the detail tier carried) plus the
    orphan demand, under the edges-only schema and the bounded retry
    taxonomy (C: one JSON re-elicitation, one doubled-window truncation
    retry). Returns the merged wave object — the first attempt's entities
    verbatim plus its edges with the repair edges appended (exact-duplicate
    echoes deduped) — for the caller to re-run through the same anchor
    check; or None when the job was cancelled before the call, the same
    cancel-race rule as the first attempt."""
    if not _job_still_running(job):
        return None
    raw_entities = first["entities"]
    raw_edges = first["edges"]
    assert isinstance(raw_entities, list) and isinstance(raw_edges, list)
    new_roster: list[tuple[str, str, str]] = []
    new_refs: list[str] = []
    for position, raw in enumerate(raw_entities):
        ref = raw.get("ref", f"N{position}") if isinstance(raw, dict) else f"N{position}"
        name = raw.get("name", "") if isinstance(raw, dict) else ""
        kind = raw.get("kind", "") if isinstance(raw, dict) else ""
        new_roster.append(
            (
                ref if isinstance(ref, str) else f"N{position}",
                name if isinstance(name, str) else "",
                kind if isinstance(kind, str) else "",
            )
        )
        if isinstance(ref, str):
            new_refs.append(ref)
    core_roster = [(f"C{i}", ref.name) for i, ref in enumerate(context[:core_count])]
    core_refs = [label for label, _name in core_roster]
    existing = [
        f"{raw.get('src')} -> {raw.get('dst')} [{raw.get('type')}]"
        for raw in raw_edges
        if isinstance(raw, dict)
    ]
    retry_prompt = _build_anchor_repair_prompt(
        orphans=orphans,
        new_roster=new_roster,
        core_roster=core_roster,
        existing_edges=existing,
    )
    repair_settings = _repair_sampling(
        dataclasses.replace(
            settings,
            response_format=build_anchor_repair_schema(new_refs, core_refs),
            max_tokens=min(settings.max_tokens, EDGES_CALL_MAX_TOKENS),
        )
    )
    repair_edges = _call_wave(
        budget,
        provider,
        repair_settings,
        retry_prompt,
        label="anchor_repair",
        parse=_parse_anchor_repair_output,
        retry_note='Return ONLY {"edges": [...]} — the additional edges, nothing else.',
        ceiling=ceiling,
    )
    merged = list(raw_edges)
    for edge in repair_edges:
        if isinstance(edge, dict) and edge in merged:
            continue  # the model echoed an already-recorded edge
        merged.append(edge)
    return {"entities": raw_entities, "edges": merged}


class _OrphanRetryError(JobPayloadError):
    """The orphan-only retry signal (spec: repair sequence step 3, anchor
    repair): raised instead of a plain ``JobPayloadError`` when the wave-2
    subgraph is valid except for entities with no edge to the core. (Wave 1
    raised it too from 2026-09-11 until the edgeless verdict the same day
    dropped the wave-1 internal orphan rule — wave 1 returns edgeless before
    this point.) The runner catches exactly this type for wave 2's one
    bounded edges-only repair; every other ``_validate_subgraph`` rejection
    stays immediate. Carries the wave and the orphan ``(name, position)``
    pairs so the repair prompt can name them.
    """

    def __init__(self, message: str, *, wave: int, orphans: list[tuple[str, int]]) -> None:
        super().__init__(message)
        self.wave = wave
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


def _validate_entities(
    wave: int, raw_entities: Sequence[Any], *, ref_offset: int = 0
) -> tuple[list[models.EntityInput], list[str]]:
    """The entity half of the wave contract (split out for chunked wave-1,
    M1): refs are positional and canonical — ``E<ref_offset + position>``
    for wave 1, where a chunk's slice carries its GLOBAL positions, and
    ``N<position>`` for wave 2 — kinds fold through
    ``canonicalize_entity_kind``, names fall back to the record's, and a
    character's beside-``data`` record is relocated. Returns the inputs
    plus their runner-assigned ULIDs (each entity gets ``ids.new_id()`` so
    edges wire before commit). Rejections are ``JobPayloadError`` naming
    the wave, the offending entity, and the reason — the job fails, never a
    partial commit."""
    if wave not in (1, 2):
        raise ValueError(f"unknown wave {wave}")
    if not raw_entities:
        raise JobPayloadError(f"wave {wave}: no entities in output")
    prefix = "E" if wave == 1 else "N"
    entity_inputs: list[models.EntityInput] = []
    assigned_ids: list[str] = []
    for position, raw in enumerate(raw_entities):
        if not isinstance(raw, dict):
            raise JobPayloadError(f"wave {wave}: entity {position} is not an object")
        ref = raw.get("ref")
        expected = f"{prefix}{ref_offset + position}" if wave == 1 else f"{prefix}{position}"
        if ref != expected:
            raise JobPayloadError(
                f"wave {wave}: entity {position} ref must be {expected}, got {ref!r}"
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
    return entity_inputs, assigned_ids


def _resolve_edges(
    wave: int,
    raw_edges: Sequence[Any],
    assigned_ids: Sequence[str],
    context: Sequence[ContextRef] = (),
    *,
    ref_offset: int = 0,
    core_ids: frozenset[str] = frozenset(),
) -> tuple[list[models.EdgeInput], set[int]]:
    """The edge half of the wave contract: vocabulary types, integer
    counters, E/N/C refs resolved to ULIDs, and the two boundary drops
    (self-loops, exact duplicates — first wins, info log). Returns the edge
    inputs plus the wave positions anchored to a core entity (the wave-2
    orphan rule's input). ``ref_offset`` maps wave-1 GLOBAL E-refs into the
    local ``assigned_ids`` slice (chunked generation resolves its merged
    edges in one pass with offset 0; the single-call path is unchanged)."""
    edge_inputs: list[models.EdgeInput] = []
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
            raw.get("src"), wave, edge_index, "src", assigned_ids, context, ref_offset=ref_offset
        )
        dst_id, dst_position = _resolve_endpoint(
            raw.get("dst"), wave, edge_index, "dst", assigned_ids, context, ref_offset=ref_offset
        )
        if src_id == dst_id:
            # Ladder rung 50: the forced count makes wiring sloppy (self-loop
            # deaths 2/2 pinned runs) and a loop carries zero graph
            # information under the closed vocabulary — no member_of-self or
            # rival_of-self means anything. Drop it at the boundary (first
            # wins' sibling: the edge dedup below); quality is still judged
            # by the record/stat/anchor gates, and a wave-2 entity left
            # anchorless by a dropped loop flows to the anchor repair.
            logger.info(
                "wave %d: dropping self-loop edge %r",
                wave,
                raw.get("src"),
            )
            continue
        key = (src_id, dst_id, edge_type, counter)
        if any((known.src, known.dst, known.type, known.counter) == key for known in edge_inputs):
            # Ladder rung 25: the model stuttered a byte-identical edge row
            # (E24 -> E11 relationship twice) and the store's AD-23 backstop
            # failed the whole wave at commit. An exact duplicate carries no
            # information, so the boundary drops it (first wins); a same-pair
            # edge with a different type or counter stays loud at commit.
            logger.info(
                "wave %d: dropping duplicate edge %r -> %r [%s]",
                wave,
                raw.get("src"),
                raw.get("dst"),
                edge_type,
            )
            continue
        edge_inputs.append(
            models.EdgeInput(src=src_id, dst=dst_id, type=edge_type, counter=counter)
        )
        if src_position is not None and dst_id in core_ids:
            anchored_positions.add(src_position)
        if dst_position is not None and src_id in core_ids:
            anchored_positions.add(dst_position)
    return edge_inputs, anchored_positions


def _validate_subgraph(
    wave: int,
    parsed: dict[str, Any],
    *,
    context: Sequence[ContextRef] = (),
    core_count: int = 0,
    core_ids: frozenset[str] | None = None,
    ref_offset: int = 0,
) -> tuple[list[models.EntityInput], list[models.EdgeInput]]:
    """Validate a parsed wave against the spec-2.3 output contract and map
    refs to runner-generated ULIDs (each entity gets ``ids.new_id()`` so
    edges wire before commit). Rejections are ``JobPayloadError`` naming
    the wave, the offending entity/edge, and the reason — the job fails,
    never a partial commit.

    Wave-1 rules: refs are positional and canonical (``E<position>``),
    kinds in {character, faction, place}, types in ``EDGE_TYPES``,
    counters integers, names non-blank, no self-loops. Wave-1 entities
    commit edgeless (owner verdict 2026-09-11 — no internal orphan rule;
    the DM prunes; the wave-1 commit passes ``allow_orphans`` through the
    store's FR2 backstop explicitly).

    Wave 2's new-entity refs are ``N<position>``; edges may also reference
    the committed context as ``C<position>`` — the TWO-TIER anchor set
    (M2): the detail neighborhood plus the compact roster of every wave-1
    entity the 24-row cap could not carry — and every entity must have
    >= 1 edge whose other endpoint is a core entity. ``core_ids`` names
    the core explicitly (the full wave-1 ULID set); absent, it defaults to
    ``context[0:core_count]``. A wave-2 subgraph that is valid except for
    such orphans raises ``_OrphanRetryError`` (never a plain
    ``JobPayloadError``) carrying the wave and the orphan ``(name,
    position)`` pairs — the runner's one bounded anchor repair catches
    exactly that type; every other rejection stays immediate.
    """
    raw_entities = parsed.get("entities")
    raw_edges = parsed.get("edges")
    if not isinstance(raw_entities, list) or not isinstance(raw_edges, list):
        raise JobPayloadError(
            f"wave {wave}: output must be a JSON object with 'entities' and 'edges' lists"
        )
    entity_inputs, assigned_ids = _validate_entities(wave, raw_entities, ref_offset=ref_offset)
    kind_violations = _edge_kind_rows(
        wave,
        raw_edges,
        [entity.kind for entity in entity_inputs],
        context=context,
        ref_offset=ref_offset,
    )
    if kind_violations:
        # Layer 2's enforcement backstop: the runner cleans raw edges
        # BEFORE validation (the bounded repair / drop-with-audit path), so
        # this fires only on a cleaning miss — the wave-2 re-validation
        # after the anchor repair, whose new edges the repair never saw.
        raise _EdgeKindViolationError(
            _kind_violation_message(wave, kind_violations), kind_violations
        )
    resolved_core = (
        core_ids
        if core_ids is not None
        else frozenset(context[i].id for i in range(min(core_count, len(context))))
    )
    edge_inputs, anchored_positions = _resolve_edges(
        wave, raw_edges, assigned_ids, context, ref_offset=ref_offset, core_ids=resolved_core
    )

    if wave == 1:
        # Wave-1 commits edgeless (owner verdict 2026-09-11): no internal
        # orphan rule — the DM prunes. Wave 2's core-anchor below is untouched.
        return entity_inputs, edge_inputs
    orphans = [p for p in range(len(entity_inputs)) if p not in anchored_positions]
    reason = "orphan entity(ies) with no edge to the core world"
    if orphans:
        names = ", ".join(f"{entity_inputs[p].name!r} (N{p})" for p in orphans)
        if core_count > 0:
            names += f" — core anchors are C0..C{core_count - 1} (the visible core)"
        else:
            names += " — no visible core"
        # Orphan-only rejection is the one _validate_subgraph failure the
        # runner repairs: one bounded edges-only anchor repair. Only wave 2
        # reaches here — wave 1 commits edgeless and returns above.
        raise _OrphanRetryError(
            f"wave {wave}: {reason}: {names}",
            wave=wave,
            orphans=[(entity_inputs[p].name, p) for p in orphans],
        )

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
    context: Sequence[ContextRef],
    *,
    ref_offset: int = 0,
) -> tuple[str, int | None]:
    """Map an edge endpoint ref to an entity ULID, or reject.

    Wave refs (``E<index>`` for wave 1 — GLOBAL positions, so a chunk's
    edges resolve against the assembled roster via ``ref_offset`` —
    ``N<index>`` for wave 2) resolve into the wave's runner-assigned ULIDs
    and return their LOCAL position; context refs (``C<index>``, wave 2
    only) resolve into the two-tier anchor context (M2: detail rows plus
    the compact core) and return ``None`` (a context endpoint is never a
    wave position). A malformed, non-canonical, or out-of-range ref is a
    ``JobPayloadError`` naming the wave, edge, and side.
    """
    prefix = "E" if wave == 1 else "N"
    if isinstance(ref, str) and ref.startswith(prefix):
        index = _ref_index(ref, prefix, wave=wave, edge_index=edge_index, side=side)
        position = index - ref_offset if wave == 1 else index
        if position < 0 or position >= len(assigned_ids):
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
