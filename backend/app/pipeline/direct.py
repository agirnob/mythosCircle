"""The fully-authored character runner (spec: hybrid authorship, path 2).

One ``add_character`` job commits a complete, DM-authored character —
ZERO LLM involvement of any kind. The zero-LLM property is STRUCTURAL:
this runner takes NO provider parameter, imports no provider module,
and its job enqueues with ``max_llm_calls = 0`` (the store forces it) —
it is not policed, it cannot call.

The Execution Worker Backstop (owner ruling F3/F5): the payload was
validated at the synchronous enqueue gate, but the world can move
between the 202 and this run (a declared target deleted, a kind
changed). So the runner re-reads the world fresh and re-runs the FULL
canonical validation (``store.direct.validate_add_character_payload``)
PLUS the target resolution INSIDE the same transaction that commits —
``store.commit._commit``, the session-taking internal (the
``candidates.accept_candidate`` precedent) — so validation, resolution,
and the one-revision commit are one atomic unit. Any violation fails
the job with ``error_code`` :data:`STRUCTURAL_VALIDATION_FAILURE`, zero
LLM calls, zero commits, never a dangling edge (the commit's
dangling-check stays the final invariant).

Commit contract (F4 identity): ONE revision, every sheet a FRESH ULID
entity (same-name characters coexist — a submission never overwrites
anything), record + stat block byte-identical to the submission (no
repair, conform, stamp, canonical folding — ``power`` untouched), and
the declared edges applied deterministically in the same transaction.
"""

from typing import Any, cast

from app.core import ids
from app.store import complete_job, models
from app.store.commit import _commit, edge_kind_ok
from app.store.db import session_scope
from app.store.direct import validate_add_character_payload
from app.store.read import latest_revision

#: The named run-time failure code (the F5 race + any state drift the
#: backstop catches). Plumbed as the ``job.error`` prefix so the REST/WS
#: surface carries a stable code, not a raw exception string.
STRUCTURAL_VALIDATION_FAILURE = "STRUCTURAL_VALIDATION_FAILURE"


class StructuralValidationError(Exception):
    """The backstop's rejection: the canonical schema violations (or the
    target-resolution failures) that block the commit — never repaired,
    never committed. The worker maps it to ``fail_job`` with the
    :data:`STRUCTURAL_VALIDATION_FAILURE` prefix."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = list(violations)


def _staged_entities(payload: dict[str, Any]) -> list[models.EntityInput]:
    """One fresh-ULID character entity per sheet (F4: same-name
    characters coexist; a submission never overwrites). The record
    commits byte-identical — the submitted dict IS the entity data, no
    conform, fold, stamp, or key normalization of any kind."""
    return [
        models.EntityInput(
            kind="character",
            name=sheet["record"]["name"],
            text=None,
            data=sheet["record"],
            # F4: the runner assigns the fresh ULID — the same id the
            # declared edges wire to, and never an existing row's.
            id=ids.new_id(),
        )
        for sheet in payload["characters"]
    ]


def _resolve_declared_edges(
    session: Any,
    campaign_id: str,
    payload: dict[str, Any],
    entities: list[models.EntityInput],
) -> list[models.EdgeInput]:
    """The declared relations as ``EdgeInput`` rows, resolved against the
    fresh world INSIDE the backstop transaction.

    Tier 1 (``target_id``): direct ULID binding — the matcher never
    runs; the target must be a committed entity of this campaign (the F5
    race: a delete between enqueue and run names itself here). Tier 2
    (``target_key``): intra-payload resolution to the sibling sheet's
    fresh ULID — the batch commits and wires atomically. ``target_name``
    is a schema violation before this point (path 2 has no mandate
    access). Kind pairs validate against the store's edge-kind table;
    duplicates (two declared rows for the same relationship, or one the
    live world already carries) are collapsed/rejected deterministically
    — the store's duplicate backstop never gets to crash the run.
    """
    by_key = {
        sheet["key"]: entity
        for sheet, entity in zip(payload["characters"], entities, strict=True)
        if isinstance(sheet.get("key"), str) and sheet.get("key", "").strip()
    }
    edges: list[models.EdgeInput] = []
    staged: set[tuple[str, str, str]] = set()
    for index, (sheet, entity) in enumerate(zip(payload["characters"], entities, strict=True)):
        relations = sheet.get("relations")
        if relations is None:
            continue
        for r_index, raw in enumerate(relations):
            where = f"characters[{index}].relations[{r_index}]"
            edge_type = raw["type"]
            counter = raw.get("counter", 1)
            if "target_id" in raw:
                target_id = raw["target_id"]
                target = session.get(models.Entity, target_id)
                if target is None or target.campaign_id != campaign_id:
                    raise StructuralValidationError(
                        [f"{where}.target_id {target_id} names no committed entity"]
                    )
                dst_kind = target.kind
                dst = target_id
            else:
                target_key = raw["target_key"]
                target = by_key.get(target_key)
                if target is None:
                    raise StructuralValidationError(
                        [f"{where}.target_key {target_key!r} names no staged sheet in this payload"]
                    )
                dst_kind = target.kind
                dst = cast(str, target.id)
            if not edge_kind_ok(edge_type, entity.kind, dst_kind):
                raise StructuralValidationError(
                    [f"{where}: {edge_type} cannot run from a {entity.kind} to a {dst_kind}"]
                )
            src_id = cast(str, entity.id)
            relationship = (src_id, dst, edge_type)
            if relationship in staged:
                continue  # duplicate collapse: one declared row per relationship
            staged.add(relationship)
            # AD-32: a declared relation may carry the DM's own why;
            # otherwise a deterministic fallback from the declaration —
            # never a blank (the commit path would reject it).
            raw_reason = raw.get("reason")
            reason = (
                raw_reason.strip()
                if isinstance(raw_reason, str) and raw_reason.strip()
                else f"declared {edge_type} relation"
            )
            edges.append(
                models.EdgeInput(
                    src=src_id, dst=dst, type=edge_type, counter=counter, reason=reason
                )
            )
    return edges


def run_add_character(job: models.Job) -> None:
    """Run one ``add_character`` job to a terminal state.

    Re-validates the canonical schema and resolves the declared targets
    INSIDE the commit transaction, then commits ONE revision (fresh
    ULIDs, byte-identical records, declared edges) — or fails with the
    :data:`STRUCTURAL_VALIDATION_FAILURE` code, zero LLM, zero commits.
    The result carries the committed ids: ``{entity_id, entity_ids,
    revision_id, edges, llm_calls: {}}`` — ``llm_calls`` is structurally
    empty (no provider exists on this path).
    """
    payload = job.payload
    if not isinstance(payload, dict):
        raise StructuralValidationError(["add_character payload must be a JSON object"])
    with session_scope() as session:
        violations = validate_add_character_payload(payload)
        if violations:
            raise StructuralValidationError(violations)
        entities = _staged_entities(payload)
        edges = _resolve_declared_edges(session, job.campaign_id, payload, entities)
        head = latest_revision(session, job.campaign_id)
        revision = _commit(
            session,
            job.campaign_id,
            entities,
            edges,
            head.id if head is not None else None,
            # Edgeless fully-authored characters are legal (the wave-1
            # owner verdict's principle): the DM wires later.
            allow_orphans=True,
        )
    complete_job(
        job.id,
        result={
            "entity_id": cast(str, entities[0].id),
            "entity_ids": [cast(str, entity.id) for entity in entities],
            "revision_id": revision.id,
            "edges": [
                {"src": edge.src, "dst": edge.dst, "type": edge.type, "counter": edge.counter}
                for edge in edges
            ],
            "llm_calls": {},
        },
    )


__all__ = [
    "STRUCTURAL_VALIDATION_FAILURE",
    "StructuralValidationError",
    "run_add_character",
]
