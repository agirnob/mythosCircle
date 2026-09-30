"""Proposed-candidate staging (AR7, AR19; spec-3.1).

A ``generate`` job stages its 2-3 surviving candidates here — never in
the entity/edge tables (AR7), so retrieval, export, and every world read
stay untouched. The staging function is NOT the commit path: it produces
no revision and no event; AD-1's single-writer rule is honored by routing
the write through this store module (never raw SQL in ``pipeline/``).
The accept/reject lifecycle lives here too (spec-3.2): ``accept_candidate``
commits the staged subgraph and flips the row to ``accepted`` inside ONE
transaction; ``reject_candidate`` settles the row without touching the
world. Terminal statuses are final — rows are kept for the audit trail.
"""

import copy
import json
from typing import Any

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from app.core import ids, time
from app.core.pagination import anchor_rowid, paging

# AD-15: accept must flip the candidate's status and commit its subgraph
# as ONE unit of work, so it calls the internal session-taking ``_commit``
# — not the public ``commit_subgraph``, which owns its session — inside
# the same ``session_scope`` transaction. One call site justifies the
# private import over widening the store's public API.
from app.store import models
from app.store.commit import (
    EDGE_TYPES,
    EntityEditConflictError,
    StoreError,
    UnknownCampaignError,
    _commit,
    edge_kind_ok,
)
from app.store.db import session_scope
from app.store.jobs import (
    DEFAULT_LIST_LIMIT,
    InvalidJobInputError,
    JobNotFoundError,
)
from app.store.models import (
    PROPOSAL_KIND,
    PROPOSAL_STATUS,
    STATUS_ACCEPTED,
    STATUS_PROPOSED,
    STATUS_REJECTED,
)
from app.store.read import latest_revision, world_state

#: The staged edge record keys (the runner resolves each C-ref to a
#: committed entity ULID before staging): the candidate sits on one side,
#: ``endpoint`` is the committed far endpoint, ``direction`` says whether
#: the edge points from the candidate to the endpoint (``outbound``) or
#: from the endpoint to the candidate (``inbound``).
EDGE_DIRECTIONS: frozenset[str] = frozenset({"outbound", "inbound"})

#: The closed regenerable-section set (spec-3.5): the AR24 content
#: sections a ``regenerate`` job may re-roll. The identity anchor
#: (name, role, level_cr, race_type, class_profession, alignment) and
#: ``edges`` are NOT regenerable — hand-edit (3-6) / inline-editing
#: (3-4) territory. Re-exported to the pipeline (``__all__``) so the
#: enqueue validator and the runner share one vocabulary.
REGEN_SECTIONS: frozenset[str] = frozenset(
    {
        "personality",
        "secret",
        "rumor",
        "party_hook",
        "appearance",
        "background",
        "goals",
        "relationships",
        "voice_style",
        "catchphrases",
        "stat_block",
        "world_integration",
        "boss",
    }
)


# ---------------------------------------------------------------------------
# The AR24 sectioned-record shape (spec-3.3) — shared by the generate
# runner's staging validation and the accept-override guard. Pure data +
# functions: no DB access and no pipeline imports (the dependency
# direction is pipeline -> store), so both layers validate ONE shape.
# ---------------------------------------------------------------------------

#: The AR24 identity-anchor fields beyond the AR19 ``name``/``role``:
#: level/CR, race/type, class/profession, alignment.
IDENTITY_FIELDS: tuple[str, ...] = ("level_cr", "race_type", "class_profession", "alignment")

#: The AR24 narrative-lore sections the AR19 core does not already carry.
#: ``personality``, ``secret``, ``rumor`` and ``party_hook`` keep their
#: AR19 names — add, never rename.
LORE_FIELDS: tuple[str, ...] = (
    "appearance",
    "background",
    "goals",
    "relationships",
    "voice_style",
    "catchphrases",
)

#: The AR24 world-integration block: one non-blank prose string per
#: field, weaving the candidate into the committed world.
WORLD_INTEGRATION_FIELDS: tuple[str, ...] = (
    "reputation",
    "factions",
    "current_location",
    "reaction_matrix",
    "on_defeat",
)

#: The conditional boss section's fields (AR24): lair actions, legendary
#: actions, immunities, vulnerabilities.
BOSS_FIELDS: tuple[str, ...] = (
    "lair_actions",
    "legendary_actions",
    "immunities",
    "vulnerabilities",
)

#: Roles that MUST carry the boss section; every other role must NOT
#: have one (spec-3.3: boss present iff role is BBEG/Monster — never an
#: empty boss object).
BOSS_ROLES: frozenset[str] = frozenset({"BBEG", "Monster"})

# Flat entity regeneration uses a different section contract from characters.
FLAT_REGEN_SECTIONS: dict[str, tuple[str, ...]] = {
    "place": ("description", "inhabitants", "whats_hidden"),
    "faction": ("description", "doctrine", "assets"),
}


def _required_str_violations(value: Any, label: str) -> list[str]:
    if isinstance(value, str) and value.strip():
        return []
    return [f"{label} must be a non-blank string"]


def _section_violations(value: Any, fields: tuple[str, ...], label: str) -> list[str]:
    """One AR24 block section: an object whose named fields are all
    non-blank strings."""
    if not isinstance(value, dict):
        return [f"{label} must be an object"]
    violations: list[str] = []
    for field in fields:
        violations.extend(_required_str_violations(value.get(field), f"{label}.{field}"))
    return violations


def _boss_violations(role: Any, boss: Any) -> list[str]:
    """The AR24 boss section's conditionality (spec-3.3): required when
    the role is BBEG or Monster, and must be ABSENT otherwise — an NPC
    never carries a (possibly empty) boss object."""
    if isinstance(role, str) and role.strip() in BOSS_ROLES:
        return _section_violations(boss, BOSS_FIELDS, "boss")
    if boss is not None:
        return ["boss section is only allowed for BBEG or Monster roles"]
    return []


def canonicalize_reaction_matrix(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of an AR24 candidate record whose
    ``world_integration.reaction_matrix`` is a non-blank string — the
    contract form (spec-3.3: one non-blank prose string per field).
    A JSON-object matrix is the shape the compact model emits when the
    prompt says the matrix "says how the committed entities react": the
    ``{"C<i>": "<reaction>", ...}`` mapping. The object's entries ARE the
    canonical string's content (the exported sheet's committed form is
    ``"C0: Neutral, C1: Neutral, ..."``), so the mapping is serialized
    deterministically into that same string form — dogfood 2026-09-09:
    gemma shipped a mapping for all 3 candidates and the generate job
    hard-failed '0 valid candidate(s) survived validation' because the
    shared validator rejects anything but a string. A string (or an
    absent/blank matrix, or a non-dict record) passes through unchanged.
    Only ``C<index>`` keys survive: any other key is dropped, and a
    mapping without a single well-formed ``"C<index>: non-blank text"``
    entry leaves a blank string, which the shared validator still
    rejects — canonicalization never rescues an empty matrix, and
    arbitrary keys can no longer launder themselves into validity.
    """
    if not isinstance(record, dict):
        return record
    world = record.get("world_integration")
    if not isinstance(world, dict):
        return record
    matrix = world.get("reaction_matrix")
    if not isinstance(matrix, dict):
        return record
    entries = []
    for key, value in matrix.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        ref = key.strip()
        # The contract's reacting slots are committed-entity refs: anything
        # else (prose keys, nested shapes flattened to text) is dropped.
        if not ref.startswith("C") or not ref[1:].isdecimal():
            continue
        if not value.strip():
            continue
        entries.append(f"{ref}: {value.strip()}")
    out = dict(record)
    out["world_integration"] = {**world, "reaction_matrix": ", ".join(entries)}
    return out


def payload_section_violations(payload: Any, entity_kind: str = "character") -> list[str]:
    """The required-section shape violations of one AR24 candidate
    payload (``[]`` = valid): the AR19 core (non-blank name, role in the
    closed set, personality, the secret/rumor/party-hook triple), every
    AR24 identity/narrative-lore section, the world-integration block,
    and the boss section required iff the role is BBEG/Monster.

    ``edges`` are deliberately NOT checked here — the staging path
    validates them against committed world state and the accept path
    (spec-3.4) validates the override's own edge set the same way.
    Shared by the generate runner (raw model output) and the accept
    override guard, so both layers enforce one shape.
    """
    if not isinstance(payload, dict):
        return ["candidate must be an object"]
    violations: list[str] = []
    violations.extend(_required_str_violations(payload.get("name"), "name"))
    if entity_kind in FLAT_REGEN_SECTIONS:
        for field in FLAT_REGEN_SECTIONS[entity_kind]:
            violations.extend(_required_str_violations(payload.get(field), field))
        return violations
    for field in ("personality", "secret", "rumor", "party_hook"):
        violations.extend(_required_str_violations(payload.get(field), field))
    role = payload.get("role")
    if not isinstance(role, str) or role.strip() not in ROLES:
        violations.append(f"role must be one of {sorted(ROLES)}")
    for field in IDENTITY_FIELDS + LORE_FIELDS:
        violations.extend(_required_str_violations(payload.get(field), field))
    violations.extend(
        _section_violations(
            payload.get("world_integration"), WORLD_INTEGRATION_FIELDS, "world_integration"
        )
    )
    violations.extend(_boss_violations(role, payload.get("boss")))
    return violations


#: The closed role set of the AR24 identity anchor (AD-18: NPC/BBEG
#: carry level, Monster carries CR). Literal contract, like
#: ``EDGE_DIRECTIONS`` above; ``pipeline.knowledge`` re-exports it so
#: the reference data and this shape stay one vocabulary.
ROLES: frozenset[str] = frozenset({"NPC", "BBEG", "Monster"})


class InvalidCandidateError(StoreError):
    """A staged candidate's edge endpoints do not resolve to committed
    world state (or an edge type is outside the closed vocabulary).

    Generation-time backstop for the STAGING WINDOW: a delete landing
    between the runner's validation and the staging write surfaces here —
    the job fails with a structured error, never a crash, and no staged
    edge dangles at the moment of staging. Deletes AFTER staging are out
    of scope here: payload edges can point at dead ids until story 3.2's
    accept-time commit-path orphan backstop rejects them.
    """


class CandidateNotFoundError(StoreError):
    """Unknown candidate id — or one of another campaign; the two are
    the same indistinguishable error (-> 404, no oracle)."""

    def __init__(self, candidate_id: str) -> None:
        super().__init__(f"unknown candidate: {candidate_id}")
        self.candidate_id = candidate_id


class CandidateSettledError(StoreError):
    """A lifecycle transition was attempted on an already-settled
    candidate — terminal statuses are final (-> 409)."""

    def __init__(self, candidate_id: str, status: str) -> None:
        super().__init__(f"candidate {candidate_id} is already {status}")
        self.candidate_id = candidate_id
        self.status = status


def stage_candidates(
    campaign_id: str,
    job_id: str,
    payloads: list[dict[str, Any]],
    *,
    target_entity_id: str | None = None,
) -> list[models.ProposedCandidate]:
    """Stage candidate records atomically — one transaction, all-or-nothing.

    Public wrapper over ``_stage_candidates`` owning the session; the
    session-taking private variant exists so the regenerate runner can
    splice and stage in ONE transaction (spec-3.6: the staged payload's
    preserved sections and the ``entity_base_data`` conflict base must
    reference the same committed moment — a hand edit landing between
    two transactions would be absent from the payload yet present in the
    base, silently overwriting it at accept).

    Re-validates inside the write transaction (the race backstop): every
    edge endpoint must resolve to a committed ``world_state`` id and
    every edge type must be a string in the closed vocabulary; otherwise
    ``InvalidCandidateError`` and zero rows written. Unknown campaign or
    job is rejected before anything is staged.

    ``target_entity_id`` (spec-3.5): when set, the staged row is a
    regenerate-entity proposal — placed on the new row's
    ``regenerates_entity_id`` column. The target must be a committed
    entity of the campaign at staging time (the race backstop: an entity
    deleted between the runner's fresh resolve and this write fails the
    job, zero rows); the regenerate runner never commits, so the target
    stays untouched until the DM accepts. ``edges: []``-carrying
    proposals are legal here — the target's own committed edges are the
    anchor and the in-place accept preserves them, so there is no
    orphan risk (unlike a fresh-ULID accept).

    Idempotent per job: a crash between the staging commit and
    ``complete_job`` re-queues the job (``recover_stale_running``), so a
    re-run must not stage duplicates — when rows already exist for the
    job, they are returned unchanged and nothing new is written.
    """
    with session_scope() as session:
        return _stage_candidates(
            session, campaign_id, job_id, payloads, target_entity_id=target_entity_id
        )


def _stage_candidates(
    session: Session,
    campaign_id: str,
    job_id: str,
    payloads: list[dict[str, Any]],
    *,
    target_entity_id: str | None = None,
    target_base_data: dict[str, Any] | None = None,
) -> list[models.ProposedCandidate]:
    """The session-taking staging body (see ``stage_candidates``).

    ``target_base_data`` (spec-3.6): the regenerate runner passes the
    SAME record the staged payload was spliced from, so the payload and
    the accept-conflict base share one committed moment by construction.
    When omitted, the target's committed ``data`` is re-read and
    snapshotted in this transaction (correct for callers with no spliced
    payload — the runner's path uses the explicit base).
    """
    if session.get(models.Campaign, campaign_id) is None:
        raise UnknownCampaignError(campaign_id)
    job = session.get(models.Job, job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    if job.campaign_id != campaign_id:
        raise InvalidCandidateError(
            f"job {job_id} belongs to campaign {job.campaign_id}, not {campaign_id}"
        )
    existing = session.scalars(
        select(models.ProposedCandidate).where(models.ProposedCandidate.job_id == job_id)
    ).all()
    if existing:
        return list(existing)
    entities, _edges = world_state(session, campaign_id)
    committed = {entity.id for entity in entities}
    if target_entity_id is not None and target_entity_id not in committed:
        raise InvalidCandidateError(
            f"target entity {target_entity_id} is not committed world state "
            f"of campaign {campaign_id}"
        )
    for index, payload in enumerate(payloads):
        # Strict-JSON backstop (review round 2): Python's json.loads
        # accepts NaN/Infinity and 1e999 overflows to inf, which the
        # SQLAlchemy JSON column stores but Starlette refuses to
        # re-serialize (allow_nan=False) — such a staged payload
        # would 500 the candidates read. The runner already drops
        # these; this is the write-boundary defense for any caller.
        try:
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise InvalidCandidateError(
                f"candidate {index}: payload is not strict JSON ({exc})"
            ) from exc
        _check_candidate_edges(index, payload, committed)
    # Spec-3.6: a regenerate-entity row snapshots its target's committed
    # ``data`` (deep copy — the JSON column's object must outlive the
    # session). ``target_base_data`` is the runner's splice source (ONE
    # transaction — payload and base share a moment); otherwise the
    # target is re-read in this transaction. The accept compares the
    # current target against this base and fails closed on mismatch.
    base_data: dict[str, Any] | None = None
    if target_entity_id is not None:
        if target_base_data is not None:
            base_data = copy.deepcopy(target_base_data)
        else:
            target = next((e for e in entities if e.id == target_entity_id), None)
            if target is not None:
                base_data = copy.deepcopy(target.data)
    rows = [
        models.ProposedCandidate(
            id=ids.new_id(),
            campaign_id=campaign_id,
            job_id=job_id,
            kind=PROPOSAL_KIND,
            status=STATUS_PROPOSED,
            payload=payload,
            created_at=time.now(),
            regenerates_entity_id=target_entity_id,
            entity_base_data=base_data,
        )
        for payload in payloads
    ]
    session.add_all(rows)
    return rows


def discard_candidates(job_id: str) -> int:
    """Delete every still-proposed staged row of one job — the
    cancel-race cleanup: a cancel landing between the staging commit and
    the terminal write must not leave ghost rows behind. Returns the
    number of rows removed. Not the lifecycle mechanism — terminal
    statuses are final and ``accept_candidate``/``reject_candidate`` own
    those transitions; settled (accepted/rejected) rows are audit-trail
    facts and are NEVER deleted here, only a job's own uncommitted rows."""
    with session_scope() as session:
        rows = session.scalars(
            select(models.ProposedCandidate).where(
                models.ProposedCandidate.job_id == job_id,
                models.ProposedCandidate.status == STATUS_PROPOSED,
            )
        ).all()
        for row in rows:
            session.delete(row)
        return len(rows)


def replace_candidate_payload(
    campaign_id: str, candidate_id: str, payload: dict[str, Any]
) -> models.ProposedCandidate:
    """Replace a still-proposed candidate's staged payload IN PLACE
    (spec-3.5): the re-roll of a proposed candidate writes through this
    store function — one row per intent, the row's identity and edges
    contract preserved, the job's own rows untouched (a re-rolled
    candidate keeps its original ``job_id``; the regenerate job owns no
    rows for it).

    One transaction: the replacement commits only when the row is
    ``proposed``, belongs to the campaign, and the payload satisfies the
    same required AR24 section shape as staging
    (``payload_section_violations``) and strict-JSON limits — otherwise
    ``CandidateNotFoundError`` (404) / ``CandidateSettledError`` (409) /
    ``InvalidCandidateError`` (422) and the staged payload is unchanged.
    The ``edges`` list itself is NOT re-validated here (the record's
    edges were validated at staging; a delete since is the accept-time
    commit path's authority) — shape and JSON only, mirroring the
    staging contract's write-boundary guards.

    Spec-3.6: when the row is a regenerate-entity proposal
    (``regenerates_entity_id`` set), the replacement ALSO refreshes
    ``entity_base_data`` to the CURRENT committed ``data`` of that
    target, in the same transaction — a re-rolled row is accept-able
    after a DM hand edit on the target, never stranded (the fresh base
    makes the accept's conflict compare pass).
    """
    with session_scope() as session:
        return _replace_candidate_payload(session, campaign_id, candidate_id, payload)


def _replace_candidate_payload(
    session: Session,
    campaign_id: str,
    candidate_id: str,
    payload: dict[str, Any],
) -> models.ProposedCandidate:
    """The session-taking core of ``replace_candidate_payload`` — the
    regenerate runner uses it to splice its staging-time re-read and the
    payload replacement inside ONE transaction (spec-3.6 Design Notes:
    payload, base snapshot, and the record they came from all reference
    the same moment; the public wrapper owns the session for one-shot
    callers)."""
    candidate = session.get(models.ProposedCandidate, candidate_id)
    if candidate is None or candidate.campaign_id != campaign_id:
        raise CandidateNotFoundError(candidate_id)
    if candidate.status != STATUS_PROPOSED:
        raise CandidateSettledError(candidate_id, candidate.status)
    if candidate.kind != PROPOSAL_KIND:
        raise InvalidCandidateError(
            f"candidate {candidate_id}: kind must be {PROPOSAL_KIND!r}, got {candidate.kind!r}"
        )
    if not isinstance(payload, dict):
        raise InvalidCandidateError(
            f"candidate {candidate_id}: replacement payload must be an object, got {payload!r}"
        )
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidCandidateError(
            f"candidate {candidate_id}: replacement payload is not strict JSON ({exc})"
        ) from exc
    entity_kind = candidate.payload.get("entity_kind", "character")
    if payload.get("entity_kind", "character") != entity_kind:
        raise InvalidCandidateError(
            f"candidate {candidate_id}: replacement cannot change entity_kind"
        )
    violations = payload_section_violations(payload, entity_kind)
    if violations:
        raise InvalidCandidateError(
            f"candidate {candidate_id}: replacement payload fails the required-section "
            f"shape: {'; '.join(violations)}"
        )
    candidate.payload = payload
    if candidate.regenerates_entity_id is not None:
        # Re-snapshot the regenerate target's CURRENT committed data so
        # the re-rolled row is accept-able after a DM hand edit (spec-3.6
        # RE_ROLL_AFTER_EDIT): a target deleted since staging stays NULL
        # (the accept-time target-exists check owns that rejection).
        target = session.get(models.Entity, candidate.regenerates_entity_id)
        if target is not None and target.campaign_id == campaign_id:
            candidate.entity_base_data = copy.deepcopy(target.data)
        else:
            candidate.entity_base_data = None
    return candidate


def accept_candidate(
    campaign_id: str,
    candidate_id: str,
    payload_override: dict[str, Any] | None = None,
    *,
    confirm_overwrite: bool = False,
) -> tuple[models.ProposedCandidate, models.Revision]:
    """Make a staged candidate real: commit its subgraph, settle the row.

    ONE transaction (FR11, AD-15): the subgraph commit and the
    ``accepted`` status flip share the same ``session_scope`` — any
    ``StoreError`` rolls back both, leaving the row ``proposed`` and
    zero new revisions. The commit path is the accept-time authority
    for endpoints deleted since staging: a dead endpoint raises
    ``DanglingEdgeError`` (422, rebase-or-reject, AD-2) with no state
    change. ``base_revision`` is the head read inside THIS transaction
    (``None`` on an empty world — the accept becomes revision 1).

    Spec-3.5 in-place branch: a regenerate-entity row
    (``regenerates_entity_id`` set) commits AS its target — the entity
    ULID is never a fresh id, so ``_commit`` updates the committed row
    in place (one ``entity_updated`` event, one revision, AD-2/AR4) and
    every existing committed edge survives untouched (the proposal's own
    staged edges are the DM's additions and must not duplicate the
    existing graph — ``DuplicateEdgeError`` 422 otherwise). A target
    deleted since staging is an ``InvalidCandidateError`` — there is
    nothing left to replace, and a fresh-ULID create would orphan its
    inbound edges.

    ``payload_override`` (spec-3.3 edit-before-accept, relaxed by
    spec-3.4): when given, it replaces the staged payload as the record
    that commits. Its ``edges`` list is the DM's OWN edge set — added,
    edited, or deleted staged edges are all legal; every edge must
    resolve to committed world state (endpoint is a committed id,
    direction in ``EDGE_DIRECTIONS``, type in the closed vocabulary) or
    the accept raises ``InvalidCandidateError`` (422) with the row left
    ``proposed`` and zero revisions. An override WITHOUT an ``edges``
    key keeps the staged set (3.3 client compat). The override must
    satisfy the same required AR24 section shape as staging
    (``payload_section_violations`` — name/role, every identity/lore
    section, the world-integration block, boss iff BBEG/Monster); a
    non-dict override, an override failing that shape, or a non-str
    ``confirm_overwrite`` (spec-3.6 ACCEPT_ANYWAY): the three-way escape
    from an accept-conflict. For a regenerate-entity row whose target
    was hand-edited since staging (``entity_base_data`` mismatch — or a
    NULL base that cannot be verified), the accept FAILS CLOSED with
    ``EntityEditConflictError`` (409) unless the confirm flag is set;
    with it, the in-place accept proceeds as ONE ``entity_updated``
    revision whose previous revision is the undo (the DM may still
    undo). The flag is NOT enough for a client to skip a human
    confirmation step — it mirrors the destructive-confirmation
    precedent (campaign delete).

    Returns ``(candidate row, new revision)``. Raises
    ``CandidateNotFoundError`` (unknown or foreign-campaign id),
    ``CandidateSettledError`` (already accepted/rejected),
    ``InvalidCandidateError`` (payload shape the staging contract does
    not guarantee: non-int or missing edge counter, edge outside the
    closed vocabulary — and a staged row with zero edges commits nothing
    and fails with the commit path's ``OrphanEntityError``), or whatever
    else the commit path rejects with
    (``InvalidEdgeCounterError``/``DanglingEdgeError``/...).
    """
    with session_scope() as session:
        candidate = session.get(models.ProposedCandidate, candidate_id)
        if candidate is None or candidate.campaign_id != campaign_id:
            raise CandidateNotFoundError(candidate_id)
        if candidate.status != STATUS_PROPOSED:
            raise CandidateSettledError(candidate_id, candidate.status)
        if candidate.kind != PROPOSAL_KIND:
            # A future staging kind or a corrupt row must never silently
            # commit as a character — the closed kind set is enforced at
            # staging and in the DB; this guards rows that bypass both.
            raise InvalidCandidateError(
                f"candidate {candidate_id}: kind must be {PROPOSAL_KIND!r}, got {candidate.kind!r}"
            )
        if not isinstance(candidate.payload, dict):
            raise InvalidCandidateError(f"candidate {candidate_id}: payload must be an object")
        if payload_override is not None:
            if not isinstance(payload_override, dict):
                raise InvalidCandidateError(
                    f"candidate {candidate_id}: payload override must be an object, "
                    f"got {payload_override!r}"
                )
            # Story 3.4: the override may carry the DM's OWN edge set —
            # added, edited, or deleted staged edges (no longer verbatim).
            # An absent ``edges`` keeps the staged set (3.3 client compat).
            if "edges" in payload_override:
                override_edges = payload_override.get("edges")
                if not isinstance(override_edges, list):
                    raise InvalidCandidateError(
                        f"candidate {candidate_id}: payload override 'edges' must be a list, "
                        f"got {override_edges!r}"
                    )
                entities, _ = world_state(session, campaign_id)
                committed = {entity.id for entity in entities}
                _check_candidate_edges(0, {"edges": override_edges}, committed)
                # Counter int-ness is enforced at the commit wiring
                # (_accept_edge); endpoint/vocab/direction resolved above.
                payload = dict(payload_override)
                payload["edges"] = override_edges
            else:
                # No ``edges`` key: keep the staged set (3.3 client
                # compat) — the DM edited sections only.
                payload = dict(payload_override)
                staged_edges = candidate.payload.get("edges")
                if staged_edges is not None:
                    payload["edges"] = staged_edges
            # The override commits AS the record: it must satisfy the same
            # required AR24 section shape as staging — an override that
            # drops or blanks a section is a shape violation (spec-3.3),
            # never a partial commit.
            candidate_kind = candidate.payload.get("entity_kind", "character")
            violations = payload_section_violations(payload_override, candidate_kind)
            if violations:
                raise InvalidCandidateError(
                    f"candidate {candidate_id}: payload override fails the required-section "
                    f"shape: {'; '.join(violations)}"
                )
        else:
            payload = dict(candidate.payload)
        entity_kind = payload.get("entity_kind", "character")
        if entity_kind not in {"character", "faction", "place"}:
            raise InvalidCandidateError(
                f"candidate {candidate_id}: payload entity_kind is invalid: {entity_kind!r}"
            )
        entities, _ = world_state(session, campaign_id)
        payload["edges"] = _repair_legacy_edge_directions(
            _candidate_edges(payload), entity_kind, list(entities)
        )
        if payload_override is not None:
            violations = payload_section_violations(payload_override, entity_kind)
            if violations:
                raise InvalidCandidateError(
                    f"candidate {candidate_id}: payload override fails the required-section "
                    f"shape: {'; '.join(violations)}"
                )
        name = payload.get("name")
        if not isinstance(name, str):
            raise InvalidCandidateError(f"candidate {candidate_id}: payload has no name")
        if candidate.regenerates_entity_id is not None:
            # Spec-3.5 in-place accept: a regenerate-entity proposal
            # replaces its target IN PLACE — same ULID, existing edges
            # preserved, one revision (AD-2, AR4). The target must still
            # be committed world state: a fresh-ULID create would leave a
            # second entity behind (orphaning the original's inbound
            # edges or duplicating pairs), and an entity deleted since
            # staging has nothing left to replace — both are structured
            # rejections, never a guessed create.
            target_id = candidate.regenerates_entity_id
            target = session.get(models.Entity, target_id)
            if target is None or target.campaign_id != campaign_id:
                raise InvalidCandidateError(
                    f"candidate {candidate_id}: regenerates_entity_id names no committed "
                    f"entity of this campaign: {target_id}"
                )
            # Spec-3.6 accept-conflict guard (data-level, precise; runs in
            # the accept's BEGIN IMMEDIATE transaction — no race): a DM
            # hand edit committed on the target since staging would be
            # silently overwritten by the in-place accept. The staged
            # ``entity_base_data`` snapshot is the compare authority: a
            # mismatch (or a NULL base — a pre-3.6 row that cannot be
            # verified) fails closed with ``EntityEditConflictError``
            # (409) UNLESS the DM explicitly confirms the overwrite —
            # then the in-place accept proceeds as one ``entity_updated``
            # revision whose previous revision is the undo. Never a
            # silent merge.
            base = candidate.entity_base_data
            if (base is None or target.data != base) and not confirm_overwrite:
                raise EntityEditConflictError(target_id)
            entity_id = target_id
            # The DM asked to re-roll SECTIONS, not the row's shell: the
            # target's kind and prose text pass through untouched — only
            # ``data`` (and the regenerated name) change.
            entity_kind = target.kind
            entity_text = target.text
            if entity_kind in FLAT_REGEN_SECTIONS:
                description = payload.get("description")
                if isinstance(description, str) and description.strip():
                    entity_text = description.strip()
        else:
            # A fresh ULID minted up front so the staged edges can name
            # the new entity: the commit path needs concrete ids to wire
            # edges, and this id is not any existing entity's — ``_commit``
            # finds no row for it and creates (never updates). The spec's
            # "id=None" wording describes the invariant (fresh ULID, never
            # an existing entity), not the minting site.
            entity_id = ids.new_id()
            entity_text = None
            if entity_kind in FLAT_REGEN_SECTIONS:
                description = payload.get("description")
                entity_text = description.strip() if isinstance(description, str) else None
        related_inputs, related_ids = _accept_related_entities(payload, candidate_id)
        entity = models.EntityInput(
            kind=entity_kind,
            name=name,
            text=entity_text,
            data={
                key: value
                for key, value in payload.items()
                if key not in {"edges", "related_entities"}
            },
            id=entity_id,
        )
        edges = [
            _accept_edge(entity_id, candidate_id, edge, related_ids)
            for edge in _candidate_edges(payload)
        ]
        latest = latest_revision(session, campaign_id)
        revision = _commit(
            session,
            campaign_id,
            [entity, *related_inputs],
            edges,
            latest.id if latest is not None else None,
        )
        candidate.status = STATUS_ACCEPTED
        candidate.accepted_entity_id = entity_id
        candidate.accept_revision_id = revision.id
    return candidate, revision


def _accept_related_entities(
    payload: dict[str, Any], candidate_id: str
) -> tuple[list[models.EntityInput], dict[str, str]]:
    inputs: list[models.EntityInput] = []
    ids_by_ref: dict[str, str] = {}
    for index, related in enumerate(_candidate_related_entities(payload)):
        if not isinstance(related, dict):
            raise InvalidCandidateError(
                f"candidate {candidate_id}: related entity {index} must be an object"
            )
        ref = related.get("ref")
        kind = related.get("kind")
        name = related.get("name")
        description = related.get("description")
        data = related.get("data", {})
        if (
            ref != f"N{index}"
            or kind not in {"character", "faction", "place"}
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(description, str)
            or not description.strip()
            or not isinstance(data, dict)
        ):
            raise InvalidCandidateError(
                f"candidate {candidate_id}: related entity {index} is malformed"
            )
        related_id = ids.new_id()
        ids_by_ref[ref] = related_id
        inputs.append(
            models.EntityInput(
                id=related_id,
                kind=kind,
                name=name.strip(),
                text=description.strip(),
                data=copy.deepcopy(data),
            )
        )
    return inputs, ids_by_ref


def _accept_edge(
    new_entity_id: str,
    candidate_id: str,
    edge: Any,
    related_ids: dict[str, str] | None = None,
) -> models.EdgeInput:
    """One staged edge record -> ``EdgeInput`` wired against the new
    entity: ``outbound`` puts the candidate on the source side, ``inbound``
    on the destination side. ``type`` passes through; ``counter`` must be
    present and an int — the staging contract always writes one, and a
    row outside that contract must not silently default to 1 (a missing
    or non-int counter is an ``InvalidCandidateError``, 422)."""
    if not isinstance(edge, dict):
        raise InvalidCandidateError(
            f"candidate {candidate_id}: edge must be an object, got {edge!r}"
        )
    endpoint = edge.get("endpoint")
    if isinstance(endpoint, str):
        endpoint = (related_ids or {}).get(endpoint, endpoint)
    direction = edge.get("direction")
    edge_type = edge.get("type")
    if (
        not isinstance(endpoint, str)
        or not isinstance(direction, str)
        or direction not in EDGE_DIRECTIONS
        or not isinstance(edge_type, str)
    ):
        raise InvalidCandidateError(
            f"candidate {candidate_id}: edge does not resolve to committed world "
            f"state or is outside the closed vocabulary: {edge!r}"
        )
    counter = edge.get("counter")
    if type(counter) is not int:
        raise InvalidCandidateError(
            f"candidate {candidate_id}: edge counter must be an int, got {counter!r}"
        )
    # AD-32: the staged reason rides the accept into the commit path,
    # where the non-blank contract is the live authority — a staged edge
    # whose reason is blank (or null-prose) rejects the accept with
    # BlankEdgeReasonError (422), never a silent blank row.
    reason = edge.get("reason")
    if direction == "outbound":
        return models.EdgeInput(
            src=new_entity_id, dst=endpoint, type=edge_type, counter=counter, reason=reason
        )
    return models.EdgeInput(
        src=endpoint, dst=new_entity_id, type=edge_type, counter=counter, reason=reason
    )


def reject_candidate(campaign_id: str, candidate_id: str) -> models.ProposedCandidate:
    """Settle a candidate ``rejected`` — the world is untouched (AR7).

    One transaction flipping ``proposed -> rejected``: no revision, no
    event, no world read. The row is kept for the audit trail (never
    deleted); terminal statuses are final.
    """
    with session_scope() as session:
        candidate = session.get(models.ProposedCandidate, candidate_id)
        if candidate is None or candidate.campaign_id != campaign_id:
            raise CandidateNotFoundError(candidate_id)
        if candidate.status != STATUS_PROPOSED:
            raise CandidateSettledError(candidate_id, candidate.status)
        candidate.status = STATUS_REJECTED
    return candidate


def list_candidates(
    campaign_id: str,
    cursor: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    status: str = STATUS_PROPOSED,
) -> tuple[list[models.ProposedCandidate], str | None]:
    """Campaign-scoped staged candidates, oldest first (rowid order).

    ``status`` filters the lifecycle set — the API layer validates it
    against ``PROPOSAL_STATUS`` and defaults to ``proposed`` so the
    accept screen never regresses into settled rows.

    ``cursor`` is the ULID of the last candidate of the previous page
    (the API layer decodes the opaque token, pagination convention).
    Returns ``(candidates, next cursor ULID or None)``.
    """
    if limit < 1:
        raise InvalidJobInputError(f"limit must be >= 1, got {limit}")
    with session_scope() as session:
        if session.get(models.Campaign, campaign_id) is None:
            raise UnknownCampaignError(campaign_id)
        rows = session.scalars(
            select(models.ProposedCandidate)
            .where(
                models.ProposedCandidate.campaign_id == campaign_id,
                models.ProposedCandidate.status == status,
                literal_column("rowid") > _after_rowid(session, campaign_id, cursor),
            )
            .order_by(literal_column("rowid"))
            .limit(limit + 1)
        ).all()
        page, next_cursor = paging(rows, limit)
        return list(page), next_cursor


def _check_edge(endpoint: Any, direction: Any, edge_type: Any, committed: set[str]) -> bool:
    """One staged edge is valid when its endpoint is a committed id, its
    direction is a known member, and its type is a string in the closed
    vocabulary. The isinstance guards keep a non-string JSON value
    (list/dict — unhashable) a clean False, never a TypeError."""
    return (
        isinstance(endpoint, str)
        and endpoint in committed
        and isinstance(direction, str)
        and direction in EDGE_DIRECTIONS
        and isinstance(edge_type, str)
        and edge_type in EDGE_TYPES
    )


def _candidate_related_entities(payload: dict[str, Any]) -> list[dict[str, Any]]:
    related = payload.get("related_entities")
    if not isinstance(related, list):
        return []
    return related


def _candidate_edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The staged ``edges`` list of a candidate payload (empty when absent
    or not a list — the caller's own contract governs the shape)."""
    edges = payload.get("edges")
    return edges if isinstance(edges, list) else []


def _repair_legacy_edge_directions(
    edges: list[dict[str, Any]], candidate_kind: str, entities: list[models.Entity]
) -> list[dict[str, Any]]:
    """Repair only the unambiguous reversed location edge shape.

    Older proposals could stage a place with ``bases_at`` or ``hails_from``
    pointing outward to a character or faction. Those edge kinds are valid
    in the opposite direction, so flip the direction before commit. Other
    invalid edges remain untouched and are rejected by the commit matrix.
    """
    kinds = {entity.id: entity.kind for entity in entities}
    repaired: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            repaired.append(edge)
            continue
        endpoint = edge.get("endpoint")
        endpoint_kind = kinds.get(endpoint) if isinstance(endpoint, str) else None
        edge_type = edge.get("type")
        if (
            edge.get("direction") == "outbound"
            and endpoint_kind is not None
            and isinstance(edge_type, str)
            and not edge_kind_ok(edge_type, candidate_kind, endpoint_kind)
            and edge_kind_ok(edge_type, endpoint_kind, candidate_kind)
            and edge_type in {"bases_at", "hails_from"}
        ):
            repaired.append({**edge, "direction": "inbound"})
        else:
            repaired.append(edge)
    return repaired


def _check_candidate_edges(index: int, payload: dict[str, Any], committed: set[str]) -> None:
    """Reject the batch when any candidate edge references an endpoint
    that is not committed world state or a type outside the vocabulary."""
    if "related_entities" in payload and not isinstance(payload.get("related_entities"), list):
        raise InvalidCandidateError(f"candidate {index}: related_entities must be a list")
    related = _candidate_related_entities(payload)
    for related_index, related_entity in enumerate(related):
        if (
            not isinstance(related_entity, dict)
            or related_entity.get("ref") != f"N{related_index}"
            or related_entity.get("kind") not in {"character", "faction", "place"}
            or not isinstance(related_entity.get("name"), str)
            or not related_entity.get("name", "").strip()
            or not isinstance(related_entity.get("description"), str)
            or not related_entity.get("description", "").strip()
            or not isinstance(related_entity.get("data", {}), dict)
        ):
            raise InvalidCandidateError(
                f"candidate {index}: related_entities[{related_index}] is malformed"
            )
    related_refs = {f"N{index}" for index in range(len(related))}
    for edge in _candidate_edges(payload):
        if not isinstance(edge, dict):
            raise InvalidCandidateError(f"candidate {index}: edge must be an object, got {edge!r}")
        endpoint = edge.get("endpoint")
        valid_endpoint = (
            isinstance(endpoint, str) and (endpoint in related_refs or endpoint in committed)
        )
        edge_endpoint = endpoint if endpoint in committed else "placeholder"
        edge_committed = committed if endpoint in committed else {"placeholder"}
        if not valid_endpoint or not _check_edge(
            edge_endpoint,
            edge.get("direction"),
            edge.get("type"),
            edge_committed,
        ):
            raise InvalidCandidateError(
                f"candidate {index}: edge does not resolve to committed world state "
                f"or is outside the closed vocabulary: {edge!r}"
            )


def _after_rowid(session: Session, campaign_id: str, cursor: str | None) -> int:
    """Rowid anchor for cursor pagination: list candidates strictly after
    this one.

    The cursor is the last item's ULID (pagination convention); a
    well-formed ULID that names no candidate, or one of another
    campaign, is a user error (422): a fabricated cursor must never
    silently reset the page (epic-1 retro item 2).
    """
    if cursor is None:
        return -1
    row = session.get(models.ProposedCandidate, cursor)
    if row is not None and row.campaign_id != campaign_id:
        raise InvalidJobInputError("cursor names a candidate of another campaign")
    return anchor_rowid(
        session,
        models.ProposedCandidate,
        cursor,
        missing_error=InvalidJobInputError(f"cursor names no candidate: {cursor}"),
    )


__all__ = [
    "BOSS_ROLES",
    "EDGE_DIRECTIONS",
    "PROPOSAL_KIND",
    "PROPOSAL_STATUS",
    "REGEN_SECTIONS",
    "STATUS_ACCEPTED",
    "STATUS_PROPOSED",
    "STATUS_REJECTED",
    "CandidateNotFoundError",
    "CandidateSettledError",
    "InvalidCandidateError",
    "accept_candidate",
    "discard_candidates",
    "list_candidates",
    "reject_candidate",
    "replace_candidate_payload",
    "stage_candidates",
]
