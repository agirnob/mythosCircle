/**
 * Story 7.1 — shared world-graph model (owner verdict 2026-09-19: whole-world
 * render redesign; supersedes the capped 1-hop-neighborhood model).
 *
 * Pure full-world projection over a WorldExport — every committed entity is a
 * node and every committed edge is drawn (labels hidden; AR6 caps remain the
 * backend retrieval contract only). Determinism is the contract: selection,
 * degree, and layout derive from array order and committed data, never from
 * randomness or render timing.
 *
 * The model is library-agnostic: the winner renderer (Vue Flow) consumes
 * exactly this output. Read-only by construction — no store, no API, no
 * writes (AR16/AD-20).
 */

import type { components } from '../../api/schema'
import { edgeLabel } from '../profile/profile'

type WorldExport = components['schemas']['WorldExport']

/** One committed entity rendered as a node. */
export interface GraphNode {
  id: string
  name: string
  kind: string
  /** Position in world.entities (rowid) order — the deterministic anchor. */
  order: number
  /** True when the entity's export carries an available image ref. */
  hasPortrait: boolean
}

/** One committed typed edge (both endpoints always in the node set). */
export interface GraphEdge {
  id: string
  src: string
  dst: string
  type: string
  counter: number
  /** edgeLabel(type, counter) — the committed label, never remapped. */
  label: string
  /** True when the edge's source equals the focus (committed direction). */
  outbound: boolean
}

/** The focus's 1-hop incidence (both directions) — drives highlight/dim. */
export interface OneHop {
  /** The focus entity + every OTHER endpoint of every edge touching it. */
  nodeIds: ReadonlySet<string>
  /** Every committed edge with the focus as an endpoint. */
  edgeIds: ReadonlySet<string>
}

export interface WorldGraph {
  /** EVERY committed entity, in world array order. */
  nodes: GraphNode[]
  /** EVERY committed edge, world array order. */
  edges: GraphEdge[]
  /** The resolved focus id — the requested ?focus= value, null when absent. */
  focusId: string | null
  /** True when a focus was requested but no such entity exists. */
  focusMissing: boolean
  /** The 1-hop incidence of the focus; null when no focus (or unknown). */
  oneHop: OneHop | null
}

/**
 * Build the whole-world graph.
 *
 * - NO FOCUS: every entity + every edge, no oneHop (nothing highlighted).
 * - MISSING FOCUS: an unknown requested id still yields the full world;
 *   `focusMissing` flags the notice (the view renders unfocused).
 * - oneHop = the focus + every other endpoint of every edge touching the
 *   focus, plus every such edge's id (both directions, committed src/dst —
 *   the same set WorldView's relation list projects).
 */
export function buildWorldGraph(world: WorldExport, focusId: string | null): WorldGraph {
  const nodes: GraphNode[] = world.entities.map((entity, order) => ({
    id: entity.id,
    name: entity.name,
    kind: entity.kind,
    order,
    hasPortrait: entity.media.some((ref) => ref.kind === 'image' && ref.available),
  }))
  const edges: GraphEdge[] = world.edges.map((edge) => ({
    id: edge.id,
    src: edge.src,
    dst: edge.dst,
    type: edge.type,
    counter: edge.counter,
    label: edgeLabel(edge.type, edge.counter),
    outbound: focusId !== null && edge.src === focusId,
  }))

  let focusMissing = false
  let oneHop: OneHop | null = null
  if (focusId !== null) {
    const exists = nodes.some((node) => node.id === focusId)
    if (!exists) {
      focusMissing = true
    } else {
      const nodeIds = new Set<string>([focusId])
      const edgeIds = new Set<string>()
      for (const edge of edges) {
        if (edge.src === focusId || edge.dst === focusId) {
          nodeIds.add(edge.src)
          nodeIds.add(edge.dst)
          edgeIds.add(edge.id)
        }
      }
      oneHop = { nodeIds, edgeIds }
    }
  }

  return { nodes, edges, focusId, focusMissing, oneHop }
}

/** Resolve a node name for display without ever leaking a raw id. */
export function displayName(nameById: Readonly<Record<string, string>>, id: string): string {
  return nameById[id] ?? '(unknown)'
}

// ---------------------------------------------------------------------------
// Deterministic concentric layout (one ring per kind) — the layout contract.
// ---------------------------------------------------------------------------

/** The arc pitch between ring nodes (px, along the ring) — also the minimum
 * gap between successive rings so ring cards never collide. */
export const CARD_PITCH = 190

/** Shared node geometry — the mockup card, 186×66. */
export const GRAPH_NODE_WIDTH = 186
export const GRAPH_NODE_HEIGHT = 66

/** Kind ring ordering — the SEMANTIC ring order (owner verdict 2026-09-19):
 * characters innermost, factions middle, places outermost; unknown kinds
 * trail the big three in first-seen order. */
const KIND_ORDER: Readonly<Record<string, number>> = {
  character: 0,
  faction: 1,
  place: 2,
}

function kindOrder(kind: string): number {
  return KIND_ORDER[kind] ?? 3
}

export interface LayoutPosition {
  id: string
  /** Node CENTER in graph coordinates. */
  x: number
  y: number
}

/**
 * Deterministic concentric rings, one per kind, in SEMANTIC order:
 * characters innermost → factions middle → places outermost (unknown kinds
 * after the big three, first-seen order). Ring radius = (count × CARD_PITCH)/2π,
 * enforced outward-monotonic with a CARD_PITCH clearance between rings so
 * ring cards never collide (a dense inner ring can push sparse outer rings
 * outward — that is the cost of the semantic order). The first ring starts
 * one CARD_PITCH from the origin and the center stays EMPTY: the focus never
 * moves (owner verdict 2026-09-19 — focused nodes stay in place, no center
 * slot).
 *
 * Pure: no randomness; a pure function of the node set, so positions are
 * stable across focus changes by construction.
 */
export function concentricLayoutByKind(nodes: ReadonlyArray<GraphNode>): LayoutPosition[] {
  const byKind = new Map<string, GraphNode[]>()
  for (const node of nodes) {
    const list = byKind.get(node.kind)
    if (list) list.push(node)
    else byKind.set(node.kind, [node])
  }
  const kinds = [...byKind.keys()].sort((a, b) => kindOrder(a) - kindOrder(b))

  const positions: LayoutPosition[] = []
  let previousOuter = 0
  for (const kind of kinds) {
    const group = byKind.get(kind)!
    const count = group.length
    const radius = Math.max((count * CARD_PITCH) / (2 * Math.PI), previousOuter + CARD_PITCH)
    previousOuter = radius
    const startAngle = -Math.PI / 2
    group.forEach((node, index) => {
      const angle = startAngle + (index * 2 * Math.PI) / count
      positions.push({
        id: node.id,
        x: radius * Math.cos(angle),
        y: radius * Math.sin(angle),
      })
    })
  }
  return positions
}

// ---------------------------------------------------------------------------
// Kind presentation (one home shared by every renderer — mockup colors).
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
// Parallel/reciprocal edge separation slots (deterministic, shared).
// ---------------------------------------------------------------------------

export interface ParallelSlot {
  /** Slot index within the unordered-pair group (edge array order). */
  index: number
  /** How many edges share the same unordered pair. */
  total: number
}

/**
 * Group by the UNORDERED pair; slots assigned in edge array order across the
 * whole group: reciprocal pairs (A→B + B→A) land on different slots and curve
 * to opposite sides; duplicate same-direction edges fan out around the
 * shared baseline. A single edge still gets a base slot (never unbent.
 */
export function parallelSlots(edges: ReadonlyArray<GraphEdge>): Map<string, ParallelSlot> {
  const byPair = new Map<string, string[]>()
  for (const edge of edges) {
    const key = [edge.src, edge.dst].sort().join('|')
    const list = byPair.get(key)
    if (list) list.push(edge.id)
    else byPair.set(key, [edge.id])
  }
  const slots = new Map<string, ParallelSlot>()
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

// ---------------------------------------------------------------------------
// Render-layer contract (the winner's props): GraphView enriches the pure
// model with store-derived portrait URLs + the avatar glyph, then the winner
// renders the SAME arrays every render.
// ---------------------------------------------------------------------------

export interface GraphRenderNode extends GraphNode {
  /** Same-origin portrait file URL (from the world store's media manifest), or null. */
  portraitUrl: string | null
  initial: string
}

/** Render-edge alias — the model edge is already the render shape. */
export type GraphRenderEdge = GraphEdge