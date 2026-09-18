"""Bounded deterministic graph retrieval (AR6, AD-16).

Generation prompts are built from a bounded neighborhood of entities
reached by strict graph traversal over typed edges — never similarity,
embeddings, or full-text, and never the whole world (AD-4/AD-16). The
traversal is deterministic: frontiers expand in rowid (commit) order, so
the same world state yields the same neighborhood, and the same
neighborhood serializes to the same context string (the prompt
invariant).

The primitive takes an explicit seed list: build-in wave 2 seeds with
the wave-1 (core) entities; Epic 3's candidate acceptance reuses it
unchanged with the ask's target as the seed.
"""

import json
from collections.abc import Sequence

from app.store import models
from app.store.db import session_scope
from app.store.read import world_state

#: Seed values from AR6: one hop, 24 entities.
DEFAULT_DEPTH = 1
DEFAULT_ENTITY_CAP = 24


def context_summary(
    entities: Sequence[models.Entity], *, cap: int = DEFAULT_ENTITY_CAP
) -> dict[str, int | bool | dict[str, int]]:
    """What a job saw: retrieval counts by kind plus the cap fact (owner
    note 4, 2026-09-15 — build-in transparency). ``truncated`` is True
    when the world is at/over the retrieval cap: the prompt then embeds a
    neighborhood, not the whole world, so later builds/asks live in the
    shadow of earlier ones without seeing everything."""
    by_kind: dict[str, int] = {}
    for entity in entities:
        by_kind[entity.kind] = by_kind.get(entity.kind, 0) + 1
    return {
        "entities": len(entities),
        "by_kind": by_kind,
        "retrieval_cap": cap,
        "truncated": len(entities) >= cap,
    }


def retrieve_neighborhood(
    campaign_id: str,
    seed_ids: Sequence[str] | None = None,
    *,
    depth: int = DEFAULT_DEPTH,
    entity_cap: int = DEFAULT_ENTITY_CAP,
    boost_ids: Sequence[str] | None = None,
    newest_first: bool = False,
) -> tuple[list[models.Entity], list[models.Edge]]:
    """Bounded deterministic BFS over typed edges from the seed entities.

    Level 0 is the seeds (in world rowid order); each further level is
    reached through outgoing typed edges, with frontiers expanded in
    rowid order. ``entity_cap`` truncates the reached set in traversal
    order, so the result is deterministic for a given world state
    (AD-16). ``seed_ids=None`` seeds with every entity (the full-world
    neighborhood).

    ``boost_ids`` (ask-target seeding) and ``newest_first`` (rowid-bias
    fill) reorder the SEED list before the cap truncates it — the
    generate path's retrieval-cap ruling (owner 2026-09-18): when the
    world exceeds the cap, the boosted entities lead the reached set in
    the given order and the remainder prefers the most recently
    committed entities (world rowid order reversed), so a tight context
    keeps the ask's target and the newest additions instead of the
    oldest rowid rows. Both are pure functions of world state plus the
    caller's ids — no ids, timestamps, or job state in the result — so
    the same state yields the same neighborhood (AD-16). A world that
    FITS the cap keeps the plain rowid ordering byte-identically: the
    boost and the bias only decide WHICH rows a tight cap keeps, never
    reorder a complete neighborhood. ``boost_ids`` naming no world
    entity is rejected with ``ValueError`` naming the offenders (caller
    bug, loud — same rule as the seed list).

    A provided seed list that resolves to NO world entities is rejected
    with ``ValueError`` naming the offending seeds — a silently empty
    neighborhood would confuse the caller downstream. Returns the
    reached entities plus the edges whose endpoints are both reached,
    in rowid order.
    """
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth}")
    if entity_cap < 1:
        raise ValueError(f"entity_cap must be >= 1, got {entity_cap}")
    with session_scope() as session:
        entities, edges = world_state(session, campaign_id)
        entity_by_id = {entity.id: entity for entity in entities}
        if seed_ids is None:
            seeds = [entity.id for entity in entities]
        else:
            seed_set = frozenset(seed_ids)
            if not any(seed in entity_by_id for seed in seed_set):
                raise ValueError(
                    "retrieve_neighborhood: no seed entity exists "
                    f"in campaign {campaign_id}: {sorted(seed_set)}"
                )
            seeds = [entity.id for entity in entities if entity.id in seed_set]

        if len(seeds) > entity_cap and (boost_ids or newest_first):
            # The tight-cap reorder: a world that fits the cap is left in
            # plain rowid order (byte-identical to the pre-ruling shape).
            if boost_ids:
                missing = sorted(set(boost_ids) - set(entity_by_id))
                if missing:
                    raise ValueError(
                        "retrieve_neighborhood: boost_ids name no world entity "
                        f"in campaign {campaign_id}: {missing}"
                    )
                boosted: list[str] = []
                seen: set[str] = set()
                for entity_id in boost_ids:
                    if entity_id not in seen:
                        seen.add(entity_id)
                        boosted.append(entity_id)
                rest = [entity_id for entity_id in seeds if entity_id not in seen]
                if newest_first:
                    rest.reverse()
                seeds = [*boosted, *rest]
            else:
                seeds = list(reversed(seeds))

        reached: list[str] = []
        for seed in seeds:
            if len(reached) >= entity_cap:
                break
            reached.append(seed)
        frontier = list(reached)
        for _ in range(depth):
            if len(reached) >= entity_cap:
                break
            next_frontier: list[str] = []
            for src in frontier:
                if len(reached) >= entity_cap:
                    break
                for edge in edges:
                    if edge.src != src or edge.dst in reached:
                        continue
                    reached.append(edge.dst)
                    next_frontier.append(edge.dst)
                    if len(reached) >= entity_cap:
                        break
            frontier = next_frontier

        reached_set = frozenset(reached)
        context_edges = [
            edge for edge in edges if edge.src in reached_set and edge.dst in reached_set
        ]
        return [entity_by_id[entity_id] for entity_id in reached], context_edges


def serialize_context(entities: Sequence[models.Entity], edges: Sequence[models.Edge]) -> str:
    """Serialize a neighborhood into prompt context — hard truths only.

    Carries kind, name, text, structured data, and the typed edges (type
    + counter), with entities referenced by position (``entity[i]``);
    ids and timestamps never appear and data keys are sorted, so the
    string is byte-deterministic for identical input (AD-16).
    """
    lines: list[str] = ["WORLD CONTEXT"]
    for index, entity in enumerate(entities):
        lines.append(f"entity[{index}] kind={entity.kind} name={entity.name!r}")
        lines.append(f"  text: {entity.text if entity.text else '(none)'}")
        lines.append(f"  data: {json.dumps(entity.data, sort_keys=True)}")
    lines.append("EDGES")
    position_by_id = {entity.id: index for index, entity in enumerate(entities)}
    for edge in edges:
        src_index = position_by_id.get(edge.src)
        dst_index = position_by_id.get(edge.dst)
        if src_index is None or dst_index is None:
            continue
        lines.append(
            f"  entity[{src_index}] -[{edge.type} counter={edge.counter}]-> entity[{dst_index}]"
        )
    return "\n".join(lines)
