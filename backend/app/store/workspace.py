"""Persistent expansion drafts and atomic additions-only checkpoints.

Draft IDs are opaque stable keys. Only receipts translate them into canon ULIDs.
"""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import ids
from app.store import models
from app.store.commit import StoreError, _commit
from app.store.db import session_scope
from app.store.direct import normalize_entity_name
from app.store.jobs import InvalidJobInputError, _enqueue, _require_running
from app.store.read import latest_revision


class WorkspaceConflict(StoreError):
    """Optimistic version or idempotency conflict; editable work is retained."""


class WorkspaceNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["place", "faction", "character"]
    name: str = Field(default="", max_length=500)
    parent_id: str | None = None
    faction_id: str | None = None
    guidance: str = Field(default="", max_length=2000)


class WorkspaceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nodes: list[WorkspaceNode] = Field(default_factory=list, max_length=300)


class WorkspaceSave(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_draft_version: int = Field(ge=0)
    expected_world_version: str | None
    draft: WorkspaceDraft


class WorkspaceSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str
    expected_draft_version: int = Field(ge=0)
    expected_world_version: str | None
    selected_ids: list[str] = Field(default_factory=list, max_length=300)
    guidance: str = Field(default="", max_length=2000)
    scope_id: str | None = None


def world_version(session: Session, campaign_id: str) -> str | None:
    head = latest_revision(session, campaign_id)
    return head.id if head else None


def validate_draft(session: Session, campaign_id: str, draft: WorkspaceDraft) -> None:
    live = {
        r.id: r
        for r in session.scalars(
            select(models.Entity).where(models.Entity.campaign_id == campaign_id)
        )
    }
    if any(
        sum(n.kind == kind for n in draft.nodes) > 100 for kind in ("place", "faction", "character")
    ):
        raise InvalidJobInputError("Keep each kind to 100 entries or fewer")
    nodes = {n.id: n for n in draft.nodes}
    if len(nodes) != len(draft.nodes) or set(nodes) & set(live):
        raise InvalidJobInputError(
            "Draft identities must be unique and separate from existing entities"
        )
    receipts = {
        r.node_id
        for r in session.scalars(
            select(models.WorkspaceReceipt).where(
                models.WorkspaceReceipt.campaign_id == campaign_id
            )
        )
    }
    if receipts & nodes.keys():
        raise InvalidJobInputError("Completed identities cannot be reused; refresh the workspace")
    names = {
        (r.kind, normalize_entity_name(r.name))
        for r in live.values()
        if r.kind in {"place", "faction"}
    }
    for node in draft.nodes:
        if node.kind in {"place", "faction"} and node.name.strip():
            key = (node.kind, normalize_entity_name(node.name))
            if key in names:
                raise InvalidJobInputError(
                    f"A {node.kind} named {node.name!r} already exists in the world or draft"
                )
            names.add(key)
        for target, kind, label in (
            (node.parent_id, "place", "Placement"),
            (node.faction_id, "faction", "Membership"),
        ):
            if target:
                row = nodes.get(target) or live.get(target)
                if row is None or row.kind != kind or target == node.id:
                    raise InvalidJobInputError(
                        f"{label} must reference a different {kind} in this campaign"
                    )
        if node.kind == "place" and node.faction_id:
            raise InvalidJobInputError("Places cannot have faction membership")
        seen = {node.id}
        parent = node.parent_id
        while parent in nodes:
            if parent in seen:
                raise InvalidJobInputError("Containment cycle in workspace")
            seen.add(parent)
            parent = nodes[parent].parent_id


def _row(session: Session, campaign_id: str) -> models.Workspace:
    campaign = session.get(models.Campaign, campaign_id)
    if campaign is None or campaign.is_generic:
        raise InvalidJobInputError("The World workspace requires a non-library campaign")
    row = session.get(models.Workspace, campaign_id)
    if row is None:
        row = models.Workspace(campaign_id=campaign_id, version=0, draft={"nodes": []})
        session.add(row)
    return row


def _response(session: Session, campaign_id: str, row: models.Workspace) -> dict[str, Any]:
    receipts = list(
        session.scalars(
            select(models.WorkspaceReceipt).where(
                models.WorkspaceReceipt.campaign_id == campaign_id
            )
        )
    )
    return {
        "draft_version": row.version,
        "world_version": world_version(session, campaign_id),
        "draft": row.draft,
        "receipts": [r.result for r in receipts],
    }


def get_workspace(campaign_id: str) -> dict[str, Any]:
    with session_scope() as session:
        return _response(session, campaign_id, _row(session, campaign_id))


def _expect(
    session: Session, campaign_id: str, row: models.Workspace, draft: int, world: str | None
) -> None:
    if row.version != draft or world_version(session, campaign_id) != world:
        raise WorkspaceConflict(
            "The workspace or world changed. Reload before applying your changes."
        )


def save_workspace(campaign_id: str, body: WorkspaceSave) -> dict[str, Any]:
    with session_scope() as session:
        row = _row(session, campaign_id)
        _expect(session, campaign_id, row, body.expected_draft_version, body.expected_world_version)
        validate_draft(session, campaign_id, body.draft)
        row.draft = body.draft.model_dump()
        row.version += 1
        return _response(session, campaign_id, row)


def submit_workspace(campaign_id: str, kind: str, body: WorkspaceSubmit) -> models.Job:
    if not ids.is_valid_ulid(body.request_id):
        raise InvalidJobInputError("request_id must be a ULID")
    fingerprint = hashlib.sha256(
        json.dumps({"kind": kind, **body.model_dump()}, sort_keys=True).encode()
    ).hexdigest()
    with session_scope() as session:
        previous = session.scalar(
            select(models.WorkspaceRequest).where(
                models.WorkspaceRequest.campaign_id == campaign_id,
                models.WorkspaceRequest.request_id == body.request_id,
            )
        )
        if previous:
            if previous.fingerprint != fingerprint:
                raise WorkspaceConflict("Request identity was already used with different input")
            return session.get(models.Job, previous.job_id)
        row = _row(session, campaign_id)
        _expect(session, campaign_id, row, body.expected_draft_version, body.expected_world_version)
        draft = WorkspaceDraft.model_validate(row.draft)
        validate_draft(session, campaign_id, draft)
        nodes = {n.id: n for n in draft.nodes}
        if body.scope_id:
            scope = nodes.get(body.scope_id) or session.get(models.Entity, body.scope_id)
            if (
                scope is None
                or scope.kind != "place"
                or (hasattr(scope, "campaign_id") and scope.campaign_id != campaign_id)
            ):
                raise InvalidJobInputError("Planning scope must be a place in this campaign")
        chosen = set(body.selected_ids or nodes.keys())
        if not chosen <= nodes.keys():
            raise InvalidJobInputError("Selected scope contains an unknown draft identity")
        # Planned dependencies are part of the reviewed scope, never implicit seeds.
        for key in chosen:
            node = nodes[key]
            if any(ref in nodes and ref not in chosen for ref in (node.parent_id, node.faction_id)):
                raise InvalidJobInputError(
                    "Include planned placement and membership targets in the selected scope"
                )
        if kind == "expand_world" and not chosen:
            raise InvalidJobInputError("Add at least one planned entry")
        payload = {
            **body.model_dump(),
            "draft": row.draft,
            "selected_ids": sorted(chosen),
            "workspace_request": True,
        }
        job = _enqueue(session, campaign_id, kind, payload, None, max(8, len(chosen) * 10), 0)
        session.add(
            models.WorkspaceRequest(
                campaign_id=campaign_id,
                request_id=body.request_id,
                fingerprint=fingerprint,
                job_id=job.id,
            )
        )
        return job


def execution_snapshot(
    job: models.Job,
) -> tuple[models.Campaign, list[WorkspaceNode], list[models.Entity]]:
    with session_scope() as session:
        _require_running(session, job.id)
        draft = WorkspaceDraft.model_validate(job.payload["draft"])
        completed = {
            r.node_id
            for r in session.scalars(
                select(models.WorkspaceReceipt).where(
                    models.WorkspaceReceipt.campaign_id == job.campaign_id
                )
            )
        }
        remaining = [
            n for n in draft.nodes if n.id in job.payload["selected_ids"] and n.id not in completed
        ]
        # Partial checkpoints may advance our own versions. External writes invalidate execution.
        if remaining or job.kind == "plan_expansion":
            row = _row(session, job.campaign_id)
            _expect(
                session,
                job.campaign_id,
                row,
                job.payload["expected_draft_version"],
                job.payload["expected_world_version"],
            )
        validate_draft(session, job.campaign_id, WorkspaceDraft(nodes=remaining))
        seed = session.get(models.Campaign, job.campaign_id)
        target_ids = {
            ref for n in nodes_for_context(job) for ref in (n.parent_id, n.faction_id) if ref
        }
        if job.payload.get("scope_id"):
            target_ids.add(job.payload["scope_id"])
        all_live = list(
            session.scalars(
                select(models.Entity).where(models.Entity.campaign_id == job.campaign_id)
            )
        )
        live = [r for r in all_live if r.id in target_ids]
        live += [r for r in all_live if r.id not in target_ids][: max(0, 24 - len(live))]
        return seed, remaining, live


def commit_expansion(
    job: models.Job, nodes: list[WorkspaceNode], entities: list[models.EntityInput]
) -> list[dict[str, Any]]:
    """Graph, receipts and removal of confirmed draft entries share one transaction."""
    with session_scope() as session:
        running = _require_running(session, job.id)
        row = _row(session, job.campaign_id)
        existing = list(
            session.scalars(
                select(models.WorkspaceReceipt).where(
                    models.WorkspaceReceipt.campaign_id == job.campaign_id
                )
            )
        )
        mapping = {r.node_id: r.entity_id for r in existing}
        if all(n.id in mapping for n in nodes):
            return [r.result for r in existing if r.node_id in {n.id for n in nodes}]
        if len(nodes) != len(entities):
            raise InvalidJobInputError("Generation did not return every reviewed slot")
        _expect(
            session,
            job.campaign_id,
            row,
            job.payload["expected_draft_version"],
            job.payload["expected_world_version"],
        )
        current = WorkspaceDraft.model_validate(row.draft)
        current_by_id = {n.id: n for n in current.nodes}
        for node in nodes:
            if current_by_id.get(node.id) != node:
                raise WorkspaceConflict(
                    "A planned entry changed during generation; nothing was committed"
                )
        validate_draft(session, job.campaign_id, current)
        live_names = {
            (r.kind, normalize_entity_name(r.name))
            for r in session.scalars(
                select(models.Entity).where(models.Entity.campaign_id == job.campaign_id)
            )
            if r.kind in {"place", "faction"}
        }
        from dataclasses import replace

        staged = []
        for node, entity in zip(nodes, entities, strict=True):
            if entity.kind != node.kind:
                raise InvalidJobInputError("Generated kind differs from reviewed slot")
            pinned_name = node.name.strip() or running.payload.get("materialized_names", {}).get(
                node.id, entity.name
            )
            entity = replace(
                entity,
                id=ids.new_id(),
                name=pinned_name,
                data={**(entity.data or {}), "name": pinned_name}
                if entity.kind == "character"
                else entity.data,
            )
            key = (entity.kind, normalize_entity_name(entity.name))
            if entity.kind in {"place", "faction"} and key in live_names:
                raise InvalidJobInputError(
                    f"Generated name collides with an existing {entity.kind}: {entity.name}"
                )
            live_names.add(key)
            staged.append(entity)
            mapping[node.id] = entity.id
        edges = []
        for node in nodes:
            for target, edge_type in (
                (node.parent_id, "part_of" if node.kind == "place" else "located_in"),
                (node.faction_id, "member_of"),
            ):
                if target:
                    edges.append(
                        models.EdgeInput(
                            src=mapping[node.id],
                            dst=mapping.get(target, target),
                            type=edge_type,
                            reason="Placement reviewed in the World workspace"
                            if target == node.parent_id
                            else "Membership reviewed in the World workspace",
                        )
                    )
        revision = _commit(
            session,
            job.campaign_id,
            staged,
            edges,
            world_version(session, job.campaign_id),
            allow_orphans=True,
        )
        session.flush()
        results = []
        for node, entity in zip(nodes, staged, strict=True):
            edge_rows = list(
                session.scalars(
                    select(models.Edge).where(
                        models.Edge.campaign_id == job.campaign_id, models.Edge.src == entity.id
                    )
                )
            )
            result = {
                "node_id": node.id,
                "entity_id": entity.id,
                "name": entity.name,
                "kind": entity.kind,
                "revision_id": revision.id,
                "edge_ids": [e.id for e in edge_rows],
                "outcome": "completed",
            }
            session.add(
                models.WorkspaceReceipt(
                    campaign_id=job.campaign_id,
                    node_id=node.id,
                    entity_id=entity.id,
                    job_id=job.id,
                    result=result,
                )
            )
            results.append(result)
        done = {n.id for n in nodes}
        # Rewrite surviving references to confirmed canon identities.
        row.draft = {
            "nodes": [
                {
                    **n.model_dump(),
                    "parent_id": mapping.get(n.parent_id, n.parent_id),
                    "faction_id": mapping.get(n.faction_id, n.faction_id),
                }
                for n in current.nodes
                if n.id not in done
            ]
        }
        row.version += 1
        return results


def nodes_for_context(job: models.Job) -> list[WorkspaceNode]:
    return WorkspaceDraft.model_validate(job.payload["draft"]).nodes


def pin_materialized_names(job: models.Job, names: dict[str, str]) -> dict[str, str]:
    """Pin model-named slots durably before any record generation can begin."""
    with session_scope() as session:
        row = _require_running(session, job.id)
        previous = row.payload.get("materialized_names")
        if previous is not None:
            return previous
        unnamed = {
            n.id
            for n in nodes_for_context(job)
            if not n.name.strip() and n.id in job.payload["selected_ids"]
        }
        if set(names) != unnamed or any(
            not isinstance(name, str) or not name.strip() or len(name) > 500
            for name in names.values()
        ):
            raise InvalidJobInputError("The model did not name every reviewed slot")
        pinned = WorkspaceDraft(
            nodes=[
                n.model_copy(update={"name": names.get(n.id, n.name)})
                for n in nodes_for_context(job)
            ]
        )
        validate_draft(session, job.campaign_id, pinned)
        row.payload = {**row.payload, "materialized_names": names}
        return names


def workspace_context(job: models.Job, live: list[models.Entity]) -> dict[str, Any]:
    """Bounded actual records and links; existing rows can never become seeds."""
    live_ids = {e.id for e in live}
    targets = {ref for n in nodes_for_context(job) for ref in (n.parent_id, n.faction_id) if ref}
    if job.payload.get("scope_id"):
        targets.add(job.payload["scope_id"])
    with session_scope() as session:
        edges = list(
            session.scalars(select(models.Edge).where(models.Edge.campaign_id == job.campaign_id))
        )
        return {
            # Keep every reviewed target identifiable even when narrative context is capped.
            "targets": [
                {"id": e.id, "kind": e.kind, "name": e.name[:80]} for e in live if e.id in targets
            ],
            "records": [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "name": e.name,
                    "text": (e.text or "")[:3000],
                    "data_excerpt": json.dumps(e.data, ensure_ascii=False)[:2000],
                }
                for e in live[:24]
            ],
            "connections": [
                {"src": e.src, "dst": e.dst, "type": e.type, "reason": e.reason}
                for e in edges
                if e.src in live_ids and e.dst in live_ids
            ][:96],
        }
