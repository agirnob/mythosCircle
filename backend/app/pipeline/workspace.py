"""Structure-only planning and strictly scoped, additions-only generation."""

import dataclasses
import json
from collections.abc import Callable

from pydantic import ValidationError

from app.core.settings import LLMSettings
from app.pipeline import build_in as build
from app.pipeline.budget import CallBudget
from app.store import complete_job, models, report_progress
from app.store.db import session_scope
from app.store.workspace import (
    WorkspaceDraft,
    commit_expansion,
    execution_snapshot,
    pin_materialized_names,
    validate_draft,
    workspace_context,
)


def planning_schema() -> dict:
    """Strict providers require every property, including nullable/defaulted ones."""
    schema = WorkspaceDraft.model_json_schema()

    def close(value: object) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["required"] = list(value.get("properties", {}))
                value["additionalProperties"] = False
            for child in value.values():
                close(child)
        elif isinstance(value, list):
            for child in value:
                close(child)

    close(schema)
    return schema


def run_workspace(job: models.Job, provider: Callable[..., str], settings: LLMSettings) -> None:
    seed, nodes, live = execution_snapshot(job)
    budget = CallBudget(job)
    context = workspace_context(job, live)
    context["campaign"] = {
        "title": seed.title,
        "description": seed.description,
        "theme": seed.theme,
        "custom_lore": seed.custom_lore,
    }
    context["expansion_guidance"] = job.payload["guidance"]
    if job.kind == "plan_expansion":
        prompt = (
            'Suggest structure only for a TTRPG world expansion. Return JSON {"nodes": [...]} with '
            "at most 100 nodes per kind. Each node has id (unique stable key), "
            "kind (place/faction/character), "
            "name (blank is allowed), parent_id (existing or planned place ID or null), faction_id "
            "(existing or planned faction ID or null), guidance. Preserve all supplied draft nodes "
            "and IDs verbatim; suggest additions only. "
            "Do not write narrative records or invent existing targets.\n"
            + json.dumps(
                {
                    "world": context,
                    "scope_id": job.payload.get("scope_id"),
                    "draft": job.payload["draft"],
                    "guidance": job.payload["guidance"],
                }
            )
        )
        schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "workspace_structure",
                "strict": True,
                "schema": planning_schema(),
            },
        }
        output = budget.call(
            lambda: provider(prompt, settings=dataclasses.replace(settings, response_format=schema))
        )
        try:
            draft = WorkspaceDraft.model_validate_json(output)
        except (ValueError, ValidationError) as exc:
            raise build.JobPayloadError(
                "The structure suggestion was invalid; your draft has been preserved"
            ) from exc
        source = WorkspaceDraft.model_validate(job.payload["draft"])
        proposed = {n.id: n for n in draft.nodes}
        if any(proposed.get(n.id) != n for n in source.nodes):
            raise build.JobPayloadError(
                "The suggestion altered an existing draft entry; discard it and try again"
            )
        scope_id = job.payload.get("scope_id")
        original_ids = {n.id for n in source.nodes}
        for node in draft.nodes:
            if node.id in original_ids or scope_id is None:
                continue
            ancestor = node.parent_id
            seen = {node.id}
            while ancestor in proposed and ancestor != scope_id and ancestor not in seen:
                seen.add(ancestor)
                ancestor = proposed[ancestor].parent_id
            if ancestor != scope_id:
                raise build.JobPayloadError(
                    "Suggested additions must stay under the selected place"
                )
        with session_scope() as session:
            validate_draft(session, job.campaign_id, draft)
        complete_job(
            job.id,
            result={
                "proposal": draft.model_dump(),
                "source_draft_version": job.payload["expected_draft_version"],
                "source_world_version": job.payload["expected_world_version"],
            },
        )
        return
    if not nodes:
        # A crash after the atomic checkpoint cannot replay world writes.
        from app.store.workspace import get_workspace

        selected = set(job.payload["selected_ids"])
        complete_job(
            job.id,
            result={
                "receipts": [
                    receipt
                    for receipt in get_workspace(job.campaign_id)["receipts"]
                    if receipt["node_id"] in selected
                ]
            },
        )
        return
    unnamed = [n for n in nodes if not n.name.strip()]
    names = job.payload.get("materialized_names", {})
    if unnamed and not names:
        prompt = (
            'Name exactly these reviewed slots. Return JSON {"names": {draft_id: name}}. '
            + json.dumps({"slots": [n.model_dump() for n in unnamed], "context": context})
        )
        raw = budget.call(lambda: provider(prompt, settings=settings))
        try:
            parsed = json.loads(raw)
            if set(parsed) != {"names"} or not isinstance(parsed["names"], dict):
                raise ValueError("Expected a names object")
            names = pin_materialized_names(job, parsed["names"])
        except (ValueError, TypeError) as exc:
            raise build.JobPayloadError(
                "The model could not name every planned slot; your draft remains"
            ) from exc
    materialized = [n.model_copy(update={"name": names.get(n.id, n.name)}) for n in nodes]
    sections = {"place": "places", "faction": "factions", "character": "key_figures"}
    # Unnamed slots get distinct positional instructions, never fabricated seed identities.
    roster = [
        (
            sections[n.kind],
            n.name,
            {"name": n.name, "description": n.guidance}
            if n.kind != "character" and n.name
            else None,
        )
        for n in materialized
    ]
    notes = (
        "Generate exactly the reviewed slots; do not add entities from guidance. "
        "Existing content is context only.\n"
        + json.dumps(
            {
                "slots": [n.model_dump() for n in nodes],
                "world": context,
                "scope_id": job.payload.get("scope_id"),
            }
        )
    )

    def progress(value: float) -> None:
        report_progress(job.id, value)

    entities, _suggested_edges, cancelled, _dropped = build._run_wave1_chunks(
        job,
        budget,
        provider,
        settings,
        seed,
        notes,
        build._wave1_chunks(roster),
        ceiling=settings.max_tokens,
        progress=progress,
    )
    if cancelled:
        return
    if len(entities) != len(nodes):
        raise build.JobPayloadError(
            "The model did not generate every reviewed slot; the draft is preserved"
        )
    entities = build.strip_noncharacter_stat_blocks(entities)
    # Pin reviewed identities before repair. Model-invented names retain the same draft key.
    entities = [
        dataclasses.replace(
            e,
            name=m.name.strip(),
            data={**(e.data or {}), "name": m.name.strip()} if e.kind == "character" else e.data,
        )
        for m, e in zip(materialized, entities, strict=True)
    ]
    for gate in (
        lambda rows: build._enforce_flat_records(
            job, budget, provider, settings, rows, roster, wave=1
        ),
        lambda rows: build._enforce_entity_names(job, budget, provider, settings, rows, wave=1),
        lambda rows: build._enforce_character_records(
            job, budget, provider, settings, rows, wave=1
        ),
        lambda rows: build._enforce_stat_blocks(job, budget, provider, settings, rows, wave=1),
    ):
        entities, cancelled = gate(entities)
        if cancelled:
            return
    if any(e.name != m.name.strip() for m, e in zip(materialized, entities, strict=True)):
        raise build.JobPayloadError("Record repair changed a reviewed identity; the draft remains")
    receipts = commit_expansion(job, nodes, entities)
    complete_job(job.id, result={"receipts": receipts})
