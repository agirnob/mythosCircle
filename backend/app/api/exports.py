"""World-state export: read-only JSON + Markdown projections (FR5, AD-11; spec-2.6).

Two surfaces over the same latest-revision snapshot, both pure reads via
the store's rowid-ordered read helpers — export never mutates state: no
revision, no event, no store write (AR18, AD-1):

- ``GET /api/campaigns/{id}/export?format=json`` — the full world state
  (all entities, typed edges, counters, stat blocks) plus the ``revision``
  id+created_at of the latest revision (closing the 2.5 deferral: the
  revision id was previously unexposed, leaving ``base_revision`` unusable).
- ``...?format=markdown`` — an Obsidian-level document: YAML frontmatter,
  one section per entity (kind, text, full ``data`` with stat blocks in a
  fenced yaml block), per-entity Relations with ``[[wikilinks]]`` from both
  endpoints, and a world-level edge table. Served as an attachment.

Only committed state is exported — candidates/proposed entities are
invisible here (AR7). Media/portraits ship with Epic 4. Repeated exports
are byte-identical: determinism falls out of the read helpers' rowid
ordering and stable dict insertion order, and the frontmatter ``exported_at``
is the snapshot's own timestamp (the latest revision's ``created_at``,
falling back to the campaign's creation for an empty world) — never a
wall-clock read.
"""

import json as _json
import math
import re
from collections import Counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.api.auth import get_current_account
from app.store import get_campaign, models
from app.store.commit import edge_counter_semantic
from app.store.db import session_scope
from app.store.read import campaign_seed, latest_revision, world_state

router = APIRouter()


class CampaignMeta(BaseModel):
    id: str
    title: str
    theme: str
    description: str
    custom_lore: str
    created_at: str


class RevisionMeta(BaseModel):
    id: str
    created_at: str


class EntityExport(BaseModel):
    id: str
    kind: str
    name: str
    text: str | None
    data: dict[str, Any]


class EdgeExport(BaseModel):
    id: str
    src: str
    dst: str
    type: str
    counter: int


class WorldExport(BaseModel):
    campaign: CampaignMeta
    revision: RevisionMeta | None
    entities: list[EntityExport]
    edges: list[EdgeExport]


def _yaml_scalar(value: str) -> str:
    """A double-quoted YAML scalar — deterministic and frontmatter-safe.

    Campaign titles/descriptions are free text (colons, quotes, newlines,
    carriage returns, any control character). JSON string escaping IS a
    valid YAML double-quoted scalar and round-trips every character, so
    the frontmatter always parses in Obsidian.
    """
    return _json.dumps(value, ensure_ascii=False)


def _edge_label(edge: EdgeExport) -> str:
    """The Relations-line edge label (spec-2.6 Design Notes): debt shows
    the amount, grudge/loyalty the score, ally/enemy the intensity; the
    neutral types' informational counter renders bare (AD-23 semantics)."""
    if edge_counter_semantic(edge.type) == "neutral":
        return edge.type
    return f"{edge.type}({edge.counter})"


_RESERVED_HEADINGS = frozenset({"Edges", "Relations"})
_WIKI_UNSAFE = re.compile(r"[\[\]|#^\n\r]")


def _finite_only(value: Any) -> Any:
    """Coerce non-finite floats (NaN/inf can round-trip through the JSON
    column) to null — a read endpoint must never 500 on its data, and
    Starlette's JSONResponse forbids non-finite floats."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite_only(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite_only(item) for item in value]
    return value


def _unique_label(base: str, entity_id: str, used: set[str]) -> str:
    """A label unique against ``used``, grown from the short-id
    discriminator. Full ULIDs are unique, so the loop terminates."""
    for width in range(4, len(entity_id) + 1):
        candidate = f"{base} ({entity_id[-width:]})"
        if candidate not in used:
            return candidate
    raise AssertionError("unreachable: full ULIDs are unique")


def _name_labels(export: WorldExport) -> dict[str, str]:
    """Display label per entity id. Entity names are neither unique (the
    store constrains staged ULIDs, not names) nor wikilink-safe. Uniqueness
    is enforced *after* sanitization — raw-name dedup alone lets distinct
    names ("A[B", "A]B") collapse into one label or an all-unsafe name go
    blank — by growing the short-id discriminator; structural headings
    ("Edges", "Relations") are reserved and get a discriminator too.
    Applied consistently to headings, wikilinks, and table cells."""
    counts = Counter(entity.name for entity in export.entities)
    labels: dict[str, str] = {}
    used: set[str] = set()
    for entity in export.entities:
        base = _WIKI_UNSAFE.sub(" ", entity.name).strip() or entity.id
        if counts[entity.name] == 1 and base not in used and base not in _RESERVED_HEADINGS:
            labels[entity.id] = base
        else:
            labels[entity.id] = _unique_label(base, entity.id, used)
        used.add(labels[entity.id])
    return labels


def _longest_backtick_run(text: str) -> int:
    runs = (match.group() for match in re.finditer(r"`+", text))
    return max((len(run) for run in runs), default=0)


def _render_markdown(export: WorldExport) -> str:
    """The Obsidian document for an already-fetched export snapshot.

    A pure function of the rows — session assembly (the store reads) is
    separated from string building; this never touches a session.
    """
    names = _name_labels(export)
    lines = [
        "---",
        f"campaign_id: {export.campaign.id}",
        f"title: {_yaml_scalar(export.campaign.title)}",
        f"theme: {_yaml_scalar(export.campaign.theme)}",
        f"description: {_yaml_scalar(export.campaign.description)}",
        f"custom_lore: {_yaml_scalar(export.campaign.custom_lore)}",
        f"revision: {export.revision.id if export.revision is not None else 'null'}",
        f"exported_at: {_yaml_scalar(_exported_at(export))}",
        "---",
        "",
    ]
    for entity in export.entities:
        lines += [f"## {names[entity.id]}", "", f"kind: {entity.kind}"]
        if entity.text is not None:
            lines += ["", entity.text]
        # The full data — stat blocks included — serialized verbatim
        # (JSON is a valid YAML flow subset, so the fence parses as yaml).
        # The fence outgrows any backtick run inside the payload, so data
        # containing ``` can never terminate it early.
        data_json = _json.dumps(entity.data, indent=2, ensure_ascii=False)
        fence = "`" * max(3, _longest_backtick_run(data_json) + 1)
        lines += ["", f"{fence}yaml", data_json, fence]
        outbound = [edge for edge in export.edges if edge.src == entity.id]
        inbound = [edge for edge in export.edges if edge.dst == entity.id]
        if outbound or inbound:
            lines += ["", "### Relations", ""]
        for edge in outbound:
            lines.append(
                f"- [[{names.get(edge.src, edge.src)}]]"
                f" --{_edge_label(edge)}--> [[{names.get(edge.dst, edge.dst)}]]"
            )
        for edge in inbound:
            lines.append(
                f"- [[{names.get(edge.dst, edge.dst)}]]"
                f" <--{_edge_label(edge)}-- [[{names.get(edge.src, edge.src)}]]"
            )
        lines.append("")
    if export.edges:
        lines += [
            "## Edges",
            "",
            "| source | type | counter | target |",
            "| --- | --- | --- | --- |",
        ]
        for edge in export.edges:
            src = names.get(edge.src, edge.src)
            dst = names.get(edge.dst, edge.dst)
            lines.append(f"| [[{src}]] | {edge.type} | {edge.counter} | [[{dst}]] |")
        lines.append("")
    return "\n".join(lines)


def _exported_at(export: WorldExport) -> str:
    """The snapshot's own timestamp — the latest revision's ``created_at``,
    or the campaign's creation for an empty world. Deliberately NOT a
    wall-clock read: repeated exports must be byte-identical."""
    if export.revision is not None:
        return export.revision.created_at
    return export.campaign.created_at


@router.get("/api/campaigns/{campaign_id}/export", response_model=WorldExport)
def export_world(
    campaign_id: str,
    current: Annotated[models.Account, Depends(get_current_account)],
    format: Literal["json", "markdown"] = "json",
) -> WorldExport | Response:
    """The complete latest-revision world state as JSON or Obsidian Markdown."""
    # Ownership first: a foreign or unknown campaign is the single
    # indistinguishable 404 (campaign route pattern, no oracle).
    campaign = get_campaign(current.id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    with session_scope() as session:
        # Re-check inside the snapshot transaction: a campaign deleted
        # between the ownership check and here must still 404, not
        # produce a phantom export.
        if campaign_seed(session, campaign_id) is None:
            raise HTTPException(status_code=404, detail="Campaign not found.")
        revision = latest_revision(session, campaign_id)
        entities, edges = world_state(session, campaign_id)
        export = WorldExport(
            campaign=CampaignMeta(
                id=campaign.id,
                title=campaign.title,
                theme=campaign.theme,
                description=campaign.description,
                custom_lore=campaign.custom_lore,
                created_at=campaign.created_at,
            ),
            revision=RevisionMeta(id=revision.id, created_at=revision.created_at)
            if revision is not None
            else None,
            entities=[
                EntityExport(
                    id=entity.id,
                    kind=entity.kind,
                    name=entity.name,
                    text=entity.text,
                    data=_finite_only(entity.data),
                )
                for entity in entities
            ],
            edges=[
                EdgeExport(
                    id=edge.id,
                    src=edge.src,
                    dst=edge.dst,
                    type=edge.type,
                    counter=edge.counter,
                )
                for edge in edges
            ],
        )
    if format == "markdown":
        return Response(
            content=_render_markdown(export),
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="world-export-{campaign_id}.md"'
            },
        )
    return export


__all__ = ["router"]
