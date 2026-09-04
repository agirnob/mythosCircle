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
    StoreError,
    UnknownCampaignError,
    _commit,
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


def payload_section_violations(payload: Any) -> list[str]:
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
    """
    with session_scope() as session:
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
        violations = payload_section_violations(payload)
        if violations:
            raise InvalidCandidateError(
                f"candidate {candidate_id}: replacement payload fails the required-section "
                f"shape: {'; '.join(violations)}"
            )
        candidate.payload = payload
    return candidate


def accept_candidate(
    campaign_id: str,
    candidate_id: str,
    payload_override: dict[str, Any] | None = None,
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
    name raises ``InvalidCandidateError`` (422) with no revision.
    ``None`` accepts the staged payload unchanged.

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
            violations = payload_section_violations(payload_override)
            if violations:
                raise InvalidCandidateError(
                    f"candidate {candidate_id}: payload override fails the required-section "
                    f"shape: {'; '.join(violations)}"
                )
        else:
            payload = dict(candidate.payload)
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
            entity_id = target_id
            # The DM asked to re-roll SECTIONS, not the row's shell: the
            # target's kind and prose text pass through untouched — only
            # ``data`` (and the regenerated name) change.
            entity_kind = target.kind
            entity_text = target.text
        else:
            # A fresh ULID minted up front so the staged edges can name
            # the new entity: the commit path needs concrete ids to wire
            # edges, and this id is not any existing entity's — ``_commit``
            # finds no row for it and creates (never updates). The spec's
            # "id=None" wording describes the invariant (fresh ULID, never
            # an existing entity), not the minting site.
            entity_id = ids.new_id()
            entity_kind = "character"
            entity_text = None
        entity = models.EntityInput(
            kind=entity_kind,
            name=name,
            text=entity_text,
            data={key: value for key, value in payload.items() if key != "edges"},
            id=entity_id,
        )
        edges = [_accept_edge(entity_id, candidate_id, edge) for edge in _candidate_edges(payload)]
        latest = latest_revision(session, campaign_id)
        revision = _commit(
            session,
            campaign_id,
            [entity],
            edges,
            latest.id if latest is not None else None,
        )
        candidate.status = STATUS_ACCEPTED
        candidate.accepted_entity_id = entity_id
        candidate.accept_revision_id = revision.id
    return candidate, revision


def _accept_edge(new_entity_id: str, candidate_id: str, edge: Any) -> models.EdgeInput:
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
    if direction == "outbound":
        return models.EdgeInput(src=new_entity_id, dst=endpoint, type=edge_type, counter=counter)
    return models.EdgeInput(src=endpoint, dst=new_entity_id, type=edge_type, counter=counter)


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


def _candidate_edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The staged ``edges`` list of a candidate payload (empty when absent
    or not a list — the caller's own contract governs the shape)."""
    edges = payload.get("edges")
    return edges if isinstance(edges, list) else []


def _check_candidate_edges(index: int, payload: dict[str, Any], committed: set[str]) -> None:
    """Reject the batch when any candidate edge references an endpoint
    that is not committed world state or a type outside the vocabulary."""
    for edge in _candidate_edges(payload):
        if not isinstance(edge, dict):
            raise InvalidCandidateError(f"candidate {index}: edge must be an object, got {edge!r}")
        if not _check_edge(
            edge.get("endpoint"), edge.get("direction"), edge.get("type"), committed
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
