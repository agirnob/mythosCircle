/**
 * Story 7.1 — shared graph model (the single contract both A/B candidates
 * consume).
 *
 * Pure bounded-neighborhood projection over a WorldExport: given the world
 * snapshot + a focus entity id, produce the 1-hop typed-edge web within the
 * AR6 caps (depth 1, ≤24 entities), exactly as the backend retrieval window
 * bounds it (backend/app/pipeline/retrieval.py DEFAULT_DEPTH / DEFAULT_ENTITY_CAP —
 * semantics mirrored here; the client never fetches a neighborhood).
 *
 * Determinism is the contract: everything is derived from array order
 * (world.entities / world.edges as committed), so truncation and selection
 * are stable across renders and across both candidates.
 *
 * Read-only by construction: no store, no API, no writes — the view feeds
 * this the world store's snapshot and renders what comes back (AR16/AD-20).
 */

import type { components } from '../../api/schema'
import { edgeLabel } from '../profile/profile'

type WorldExport = components['schemas']['WorldExport']

/** AR6 entity cap — the same bound retrieval.py applies to a neighborhood. */
export const ENTITY_CAP = 24

/** The focused entity plus at most this many neighbors fits the cap. */
export const NEIGHBOR_CAP = ENTITY_CAP - 1

/** One entity in the bounded web. */
export interface GraphNode {
  id: string
  name: string
  kind: string
  /** Position in world.entities (rowid) order — the deterministic anchor. */
  order: number
  /** True when this node is the neighborhood's focus entity. */
  focus: boolean
  /** True when the entity's export carries an available image ref. */
  hasPortrait: boolean
}

/** One typed edge whose endpoints are BOTH in the node set. */
export interface GraphEdge {
  id: string
  src: string
  dst: string
  type: string
  counter: number
  /** edgeLabel(type, counter) — the committed label, never remapped. */
  label: string
  /** True when the edge's source is the focused entity (committed direction). */
  outbound: boolean
}

export interface GraphModel {
  /** The resolved focus id, or null when the set is empty / focus unknown. */
  focusId: string | null
  /** True when a focus was requested but no such entity exists (MISSING FOCUS). */
  focusMissing: boolean
  /** True when no focus was requested and the most-connected entity was used. */
  focusDefaulted: boolean
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** Eligible 1-hop neighbors before the cap (the M in "showing N of M"). */
  totalNeighbors: number
  /** True when neighbors were truncated at the cap. */
  truncation: boolean
}

/**
 * Build the bounded 1-hop web around `requestedFocus`.
 *
 * - NO FOCUS: the most-connected entity becomes focus (`focusDefaulted`).
 * - MISSING FOCUS: an unknown requested id yields the empty set
 *   (`focusMissing` marks the state for the view's empty-state message).
 * - EMPTY WORLD: empty set, no default focus.
 * - Neighbors are the entities incident to the focus (either direction) via
 *   ANY committed edge; they are kept in world.entities order and truncated
 *   deterministically at NEIGHBOR_CAP.
 * - Edges render only when both endpoints are in the node set (dangling
 *   edges — including edges to truncated neighbors — are excluded).
 */
export function buildGraphModel(world: WorldExport, requestedFocus: string | null): GraphModel {
  const entities = world.entities
  if (entities.length === 0) {
    return {
      focusId: null,
      focusMissing: false,
      focusDefaulted: false,
      nodes: [],
      edges: [],
      totalNeighbors: 0,
      truncation: false,
    }
  }

  let focusId = requestedFocus
  let focusMissing = false
  let focusDefaulted = false
  if (focusId === null) {
    // NO FOCUS: the most-connected entity — max degree over all committed
    // typed edges, first-in-rowid tie-break. The first rowid entity can be an
    // edgeless place (owner verdict 2026-09-19), which made the default land
    // on an empty web in real worlds.
    const degrees = new Map<string, number>()
    for (const edge of world.edges) {
      degrees.set(edge.src, (degrees.get(edge.src) ?? 0) + 1)
      degrees.set(edge.dst, (degrees.get(edge.dst) ?? 0) + 1)
    }
    let bestId = entities[0]!.id
    let bestDegree = -1
    for (const entity of entities) {
      const degree = degrees.get(entity.id) ?? 0
      if (degree > bestDegree) {
        bestDegree = degree
        bestId = entity.id
      }
    }
    focusId = bestId
    focusDefaulted = true
  } else if (!entities.some((entity) => entity.id === focusId)) {
    focusMissing = true
  }
  if (focusMissing) {
    return {
      focusId: null,
      focusMissing: true,
      focusDefaulted: false,
      nodes: [],
      edges: [],
      totalNeighbors: 0,
      truncation: false,
    }
  }

  // 1 hop, both directions: every edge touching the focus puts its OTHER
  // endpoint in the neighbor set (a self-loop contributes none).
  const edges = world.edges
  const neighborIds = new Set<string>()
  for (const edge of edges) {
    if (edge.src === focusId && edge.dst !== focusId) neighborIds.add(edge.dst)
    if (edge.dst === focusId && edge.src !== focusId) neighborIds.add(edge.src)
  }

  // Deterministic truncation: neighbors in world.entities (rowid) order,
  // capped at NEIGHBOR_CAP. The neighbor set is visited in array order,
  // NOT the edge iteration order — the cap is array-order deterministic.
  const totalNeighbors = neighborIds.size
  const kept: GraphNode[] = []
  let order = 0
  for (const entity of entities) {
    if (!neighborIds.has(entity.id)) continue
    if (kept.length >= NEIGHBOR_CAP) break
    kept.push({
      id: entity.id,
      name: entity.name,
      kind: entity.kind,
      order,
      focus: false,
      hasPortrait: entity.media.some((ref) => ref.kind === 'image' && ref.available),
    })
    order += 1
  }

  const nodes: GraphNode[] = [
    {
      id: focusId,
      name: entities.find((entity) => entity.id === focusId)!.name,
      kind: entities.find((entity) => entity.id === focusId)!.kind,
      order: -1,
      focus: true,
      hasPortrait: entities
        .find((entity) => entity.id === focusId)!
        .media.some((ref) => ref.kind === 'image' && ref.available),
    },
    ...kept,
  ]

  const nodeIds = new Set(nodes.map((node) => node.id))
  const graphEdges: GraphEdge[] = []
  for (const edge of edges) {
    if (!nodeIds.has(edge.src) || !nodeIds.has(edge.dst)) continue
    graphEdges.push({
      id: edge.id,
      src: edge.src,
      dst: edge.dst,
      type: edge.type,
      counter: edge.counter,
      label: edgeLabel(edge.type, edge.counter),
      outbound: edge.src === focusId,
    })
  }

  return {
    focusId,
    focusMissing: false,
    focusDefaulted,
    nodes,
    edges: graphEdges,
    totalNeighbors,
    truncation: totalNeighbors > NEIGHBOR_CAP,
  }
}

// ---------------------------------------------------------------------------
// Kind presentation (one home shared by both candidates — mockup-7-1 colors).
// Color is REINFORCEMENT only: the kind text chip + avatar glyph carry the
// meaning, so entity types stay distinguishable without reading a badge.
// ---------------------------------------------------------------------------

export interface KindStyle {
  /** The kind chip / border / avatar ink color. */
  color: string
  /** The chip / avatar background tint. */
  background: string
}

export const KIND_STYLES: Readonly<Record<string, KindStyle>> = {
  character: { color: '#1d4ed8', background: '#e8eefc' },
  faction: { color: '#7c3aed', background: '#f1e9fd' },
  place: { color: '#047857', background: '#e3f5ee' },
}

export const KIND_FALLBACK_STYLE: KindStyle = { color: '#6b7280', background: '#eef0f3' }

export function kindStyle(kind: string): KindStyle {
  return KIND_STYLES[kind] ?? KIND_FALLBACK_STYLE
}

/** The node's avatar glyph — the name's first letter (mockup convention). */
export function avatarInitial(name: string): string {
  return name.trim().charAt(0).toUpperCase() || '?'
}

// ---------------------------------------------------------------------------
// Render-layer contract (the candidates' props): GraphView enriches the pure
// model with store-derived portrait URLs and the avatar glyph, then passes
// the SAME arrays to whichever candidate is active.
// ---------------------------------------------------------------------------

export interface GraphRenderNode extends GraphNode {
  /** Same-origin portrait file URL (from the world store's media manifest), or null. */
  portraitUrl: string | null
  initial: string
}

/** Render-edge alias — the model edge is already the render shape. */
export type GraphRenderEdge = GraphEdge

/** Shared node geometry — the mockup card, 186×66. */
export const GRAPH_NODE_WIDTH = 186
export const GRAPH_NODE_HEIGHT = 66

export interface ParallelSlot {
  /** Slot index within the unordered-pair group (edge array order). */
  index: number
  /** How many edges share the same unordered pair. */
  total: number
}

/**
 * Deterministic parallel/reciprocal separation slots.
 *
 * Group by the UNORDERED pair, slots assigned in edge array order across
 * the whole group: reciprocal pairs (A→B + B→A) land on different slots and
 * curve to opposite sides; duplicate same-direction edges fan out around
 * the shared baseline. `total === 1` edges still get a small base curvature
 * so no edge renders as an unbent line.
 */
export function parallelSlots(edges: ReadonlyArray<GraphEdge>): Map<string, ParallelSlot> {
  const slots = new Map<string, ParallelSlot>()
  const byPair = new Map<string, string[]>()
  for (const edge of edges) {
    const key = [edge.src, edge.dst].sort().join('|')
    const list = byPair.get(key)
    if (list) list.push(edge.id)
    else byPair.set(key, [edge.id])
  }
  for (const ids of byPair.values()) {
    ids.forEach((id, index) => {
      slots.set(id, { index, total: ids.length })
    })
  }
  return slots
}

/** Vue Flow bezier curvature for a slot (spread around a small base). */
export function vueFlowCurvature(slot: ParallelSlot): number {
  return 0.08 + (slot.index - (slot.total - 1) / 2) * 0.14
}