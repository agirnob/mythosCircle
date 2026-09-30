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
  // Dangling-edge exclusion: an edge whose src or dst is NOT a committed
  // entity never enters the model (Vue Flow misbehaves with unknown endpoint
  // ids; a committed world cannot produce one, but exports can).
  const entityIds = new Set(world.entities.map((entity) => entity.id))
  const edges: GraphEdge[] = world.edges
    .filter((edge) => entityIds.has(edge.src) && entityIds.has(edge.dst))
    .map((edge) => ({
      id: edge.id,
      src: edge.src,
      dst: edge.dst,
      type: edge.type,
      counter: edge.counter,
      label: edgeLabel(edge.type, edge.counter),
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
// Deterministic cluster layout — semantic faction areas + force refinement
// (owner verdict 2026-09-19 "do it right": faction members sit close to
// their faction, places near their anchors; no perfect circles, no empty
// center). Pure + deterministic: seeded PRNG, fixed iterations — repeated
// layout of the same world is byte-identical.
// ---------------------------------------------------------------------------

/** Shared node geometry — the mockup card, 186×66. */
export const GRAPH_NODE_WIDTH = 186
export const GRAPH_NODE_HEIGHT = 66

/** Arc pitch for cluster members (px along the member ring). */
const CLUSTER_PITCH = 172
/** Minimum member-ring radius — a faction card plus its closest members. */
const CLUSTER_MIN_RADIUS = 120
/** Ideal edge length for the ambient force pass. */
const IDEAL_EDGE = 230

/** Force refinement: iterations + spring/repulsion weights. */
const REFINE_ITERATIONS = 220
const EDGE_SPRING = 1.0
// Anchor spring during refinement (the final ring projection in clusterLayout
// enforces membership structurally; this only shapes the angular drift).
const CLUSTER_SPRING = 0.8
// Repulsion vs springs: cluster roots separate by ~1.5×IDEAL and member rings
// never intrude on a neighbouring faction's area...
const REPULSION = 300_000
// Soft gravity to the running centroid: bounds edgeless/low-degree nodes
// (pure repulsion alone lets them drift outward like t^(1/3) — the observed
// 17k-px blowout) and keeps the layout's usable extent.
const CENTER_GRAVITY = 0.04
const MAX_STEP = 46
const DAMPING = 0.86

/** FNV-1a — deterministic uint32 seed from any string (per-world seed). */
export function hashString(input: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return hash >>> 0
}

/** mulberry32 — deterministic PRNG (seed-safe, no Date/random). */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export interface LayoutPosition {
  id: string
  /** Node CENTER in graph coordinates. */
  x: number
  y: number
}

type Point = { x: number; y: number }

/**
 * Faction clusters from the committed vocabulary: every `member_of` edge
 * whose target is a faction makes the source a member of that faction's
 * cluster (deterministic: member order = edge rowid order). A faction with
 * no members is just a plain node (no empty areas).
 */
export function buildFactionClusters(
  nodes: ReadonlyArray<GraphNode>,
  edges: ReadonlyArray<GraphEdge>,
): Map<string, string[]> {
  const kindById = new Map(nodes.map((node) => [node.id, node.kind]))
  const clusters = new Map<string, string[]>()
  for (const edge of edges) {
    if (edge.type !== 'member_of') continue
    if (kindById.get(edge.dst) !== 'faction') continue
    const members = clusters.get(edge.dst)
    if (members) members.push(edge.src)
    else clusters.set(edge.dst, [edge.src])
  }
  return clusters
}

/** The representative id used for a node in the inter-cluster skeleton:
 * a member counts as its faction (the cluster moves as one body). */
function representative(id: string, clusterOfMember: Map<string, string>): string {
  return clusterOfMember.get(id) ?? id
}

/**
 * Deterministic cluster layout:
 *
 * 1. SKELETON — factions (with member mass) + unaffiliated nodes laid out by
 *    a seeded force pass over the inter-cluster graph (members count as
 *    their faction; internal cluster edges drop out).
 * 2. FILL — each faction's members placed on a deterministic ring around
 *    their faction, radius by member count (this is the "area": faction at
 *    the center, members hugging it).
 * 3. REFINE — a full force pass over EVERY node: committed edges attract,
 *    all pairs repel, and every member keeps a spring to its faction, so
 *    the semantic areas survive while cross-world relations pull clusters
 *    toward each other and places settle near their anchors. Fixed
 *    iterations, damped, deterministic (no randomness in this pass).
 *
 * Pure: same world ⇒ same positions; focus never influences the layout.
 */
/**
 * Lane layout used by the graph view. The world graph is intentionally
 * organized by entity kind first, then by faction membership within the
 * character lane. This keeps the overview readable when a world has many
 * cross-links instead of letting the force pass pull cards into a dense knot.
 */
export function clusterLayout(
  nodes: ReadonlyArray<GraphNode>,
  edges: ReadonlyArray<GraphEdge>,
): LayoutPosition[] {
  const clusters = buildFactionClusters(nodes, edges)

  const factions = nodes.filter((node) => node.kind === 'faction')
  const characters = nodes.filter((node) => node.kind === 'character')
  const places = nodes.filter((node) => node.kind === 'place')
  const positions = new Map<string, LayoutPosition>()
  const placed = new Set<string>()
  const goldenAngle = Math.PI * (3 - Math.sqrt(5))
  const factionCenters = { x: -320, y: 0 }

  // Factions form separated islands around a loose orbit. This preserves a
  // readable high-level structure without the artificial rows of a grid.
  factions.forEach((faction, index) => {
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / Math.max(factions.length, 1)
    const radius = factions.length === 1 ? 0 : 270
    positions.set(faction.id, {
      id: faction.id,
      x: factionCenters.x + radius * Math.cos(angle),
      y: factionCenters.y + radius * Math.sin(angle),
    })
    placed.add(faction.id)
  })

  // If a faction belongs to another faction, push it outward from the parent
  // so the nested group's members naturally occupy the far side of its hub.
  const parentFaction = new Map<string, string>()
  for (const [parentId, members] of clusters) {
    for (const memberId of members) {
      if (factions.some((faction) => faction.id === memberId)) parentFaction.set(memberId, parentId)
    }
  }
  for (const [childId, parentId] of parentFaction) {
    const child = positions.get(childId)
    const parent = positions.get(parentId)
    if (!child || !parent) continue
    const dx = child.x - parent.x
    const dy = child.y - parent.y
    const distance = Math.hypot(dx, dy) || 1
    child.x = parent.x + (dx / distance) * 360
    child.y = parent.y + (dy / distance) * 360
  }

  // Members orbit their faction hub. The fixed golden-angle ordering avoids
  // parallel stacks while remaining deterministic across renders.
  for (const [factionId, members] of clusters) {
    const root = positions.get(factionId)
    if (!root) continue
    const directCharacters = members.filter((id) => nodes.find((node) => node.id === id)?.kind === 'character')
    const memberRadius = Math.max(210, (directCharacters.length * 205) / (2 * Math.PI))
    const parent = parentFaction.get(factionId)
    const parentPoint = parent ? positions.get(parent) : undefined
    const awayAngle = parentPoint ? Math.atan2(root.y - parentPoint.y, root.x - parentPoint.x) : -Math.PI / 2
    directCharacters.forEach((id, index) => {
      const angle = awayAngle + (index - (directCharacters.length - 1) / 2) * (2 * Math.PI / Math.max(directCharacters.length, 1))
      positions.set(id, {
        id,
        x: root.x + memberRadius * Math.cos(angle),
        y: root.y + memberRadius * Math.sin(angle),
      })
      placed.add(id)
    })
  }

  // Anchored places sit on the outer edge of their faction island. Other
  // places and unaffiliated characters form loose organic clouds by kind.
  places.forEach((place) => {
    const edge = edges.find(
      (candidate) =>
        (candidate.src === place.id || candidate.dst === place.id) &&
        (candidate.type === 'bases_at' || candidate.type === 'located_in') &&
        factions.some((faction) => faction.id === (candidate.src === place.id ? candidate.dst : candidate.src)),
    )
    if (!edge) return
    const factionId = edge.src === place.id ? edge.dst : edge.src
    const root = positions.get(factionId)
    if (!root) return
    const angle = (hashString(place.id) % 360) * (Math.PI / 180)
    positions.set(place.id, { id: place.id, x: root.x + 270 * Math.cos(angle), y: root.y + 270 * Math.sin(angle) })
    placed.add(place.id)
  })

  const cloud = (items: GraphNode[], centerX: number, centerY: number) => {
    items.forEach((node, index) => {
      const angle = index * goldenAngle
      const radius = 150 + 72 * Math.sqrt(index)
      positions.set(node.id, {
        id: node.id,
        x: centerX + radius * Math.cos(angle),
        y: centerY + radius * Math.sin(angle),
      })
      placed.add(node.id)
    })
  }
  cloud(characters.filter((node) => !placed.has(node.id)), 80, 0)
  cloud(places.filter((node) => !placed.has(node.id)), 520, 0)

  // Final rectangle separation. Organic placement is useful only when cards
  // remain individually readable, so resolve collisions without relying on
  // render timing or browser physics.
  const minDx = GRAPH_NODE_WIDTH + 24
  const minDy = GRAPH_NODE_HEIGHT + 24
  for (let pass = 0; pass < 80; pass += 1) {
    let moved = false
    for (let i = 0; i < nodes.length; i += 1) {
      const a = positions.get(nodes[i]!.id)
      if (!a) continue
      for (let j = i + 1; j < nodes.length; j += 1) {
        const b = positions.get(nodes[j]!.id)
        if (!b) continue
        const dx = b.x - a.x
        const dy = b.y - a.y
        const overlapX = minDx - Math.abs(dx)
        const overlapY = minDy - Math.abs(dy)
        if (overlapX <= 0 || overlapY <= 0) continue
        if (overlapX < overlapY) {
          const shift = overlapX / 2
          const direction = dx === 0 ? 1 : Math.sign(dx)
          a.x -= direction * shift
          b.x += direction * shift
        } else {
          const shift = overlapY / 2
          const direction = dy === 0 ? 1 : Math.sign(dy)
          a.y -= direction * shift
          b.y += direction * shift
        }
        moved = true
      }
    }
    if (!moved) break
  }

  return nodes.map((node) => positions.get(node.id) ?? { id: node.id, x: 0, y: 0 })
}

/** Retained for layout experiments and regression comparisons. */
export function legacyClusterLayout(
  nodes: ReadonlyArray<GraphNode>,
  edges: ReadonlyArray<GraphEdge>,
): LayoutPosition[] {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const kindById = new Map(nodes.map((node) => [node.id, node.kind]))
  const clusters = buildFactionClusters(nodes, edges)
  const memberOf = new Map<string, string>()
  for (const [factionId, members] of clusters) {
    for (const member of members) memberOf.set(member, factionId)
  }

  // Associations: a non-member, non-faction node whose ENTIRE edge incidence
  // lands inside ONE faction cluster (root or members) binds to that cluster
  // — e.g. a place with only `bases_at → guild` sits beside the guild, on the
  // outskirts of its member ring, instead of drifting into the sparser side
  // of the drawing.
  const associates = new Map<string, string>()
  for (const node of nodes) {
    if (memberOf.has(node.id) || node.kind === 'faction') continue
    const incident = edges.filter((edge) => edge.src === node.id || edge.dst === node.id)
    if (incident.length === 0) continue
    const touched = new Set<string>()
    for (const edge of incident) {
      const other = edge.src === node.id ? edge.dst : edge.src
      const root = memberOf.get(other) ?? (kindById.get(other) === 'faction' ? other : null)
      touched.add(root ?? '\u0000unaffiliated')
    }
    if (touched.size === 1 && !touched.has('\u0000unaffiliated')) {
      associates.set(node.id, [...touched][0]!)
    }
  }
  const clusterOfNode = new Map([...memberOf, ...associates])

  const positions = new Map<string, Point>()

  // 1. Skeleton: cluster roots + unaffiliated nodes.
  const skeletonIds = nodes
    .map((node) => node.id)
    .filter((id) => !clusterOfNode.has(id)) // members + associates ride their faction
  const rep = (id: string) => representative(id, clusterOfNode)
  const skeletonEdgePairs = edges
    .map((edge) => [rep(edge.src), rep(edge.dst)] as const)
    .filter(([a, b]) => a !== b)

  if (skeletonIds.length > 0) {
    const random = mulberry32(hashString(byId.get(skeletonIds[0]!)?.name ?? 'skeleton'))
    for (const id of skeletonIds) {
      positions.set(id, {
        x: (random() - 0.5) * 600,
        y: (random() - 0.5) * 600,
      })
    }
    relax(positions, skeletonIds, skeletonEdgePairs, REFINE_ITERATIONS, (id) => ({
      // Cluster roots carry their member mass: heavier roots push harder.
      mass: 1 + (clusters.get(id)?.length ?? 0) * 0.35,
      anchor: null,
    }))
  }

  // 2. Fill: members on a deterministic ring around their faction;
  //    associates just outside that ring (bottom-right bias, fixed order).
  for (const [factionId, members] of clusters) {
    const center = positions.get(factionId) ?? { x: 0, y: 0 }
    const radius = Math.max((members.length * CLUSTER_PITCH) / (2 * Math.PI), CLUSTER_MIN_RADIUS)
    const startAngle = -Math.PI / 2
    members.forEach((memberId, index) => {
      const angle = startAngle + (index * 2 * Math.PI) / members.length
      positions.set(memberId, {
        x: center.x + radius * Math.cos(angle),
        y: center.y + radius * Math.sin(angle),
      })
    })
  }
  const associateIds = [...associates.keys()]
  associateIds.forEach((associateId, index) => {
    const rootId = associates.get(associateId)!
    const center = positions.get(rootId) ?? { x: 0, y: 0 }
    const members = clusters.get(rootId) ?? []
    const radius = Math.max((members.length * CLUSTER_PITCH) / (2 * Math.PI), CLUSTER_MIN_RADIUS) + 10
    const angle = -Math.PI / 2 + (index * 2 * Math.PI) / Math.max(associateIds.length, 1)
    positions.set(associateId, {
      x: center.x + radius * Math.cos(angle),
      y: center.y + radius * Math.sin(angle),
    })
  })

  // 3. Refine: every node, all forces — edges attract, pairs repel,
  //    cluster-anchored nodes spring back to their faction at ring spacing
  //    (members on the member ring, associates just outside it).
  const allIds = nodes.map((node) => node.id)
  const pairEdges = edges.map((edge) => [edge.src, edge.dst] as const).filter(([a, b]) => a !== b)
  const anchorIdeal = new Map<string, number>()
  for (const [factionId, members] of clusters) {
    const radius = Math.max((members.length * CLUSTER_PITCH) / (2 * Math.PI), CLUSTER_MIN_RADIUS)
    for (const member of members) anchorIdeal.set(member, radius)
    for (const [associate, root] of associates) {
      if (root === factionId) anchorIdeal.set(associate, radius + 10)
    }
  }
  relax(positions, allIds, pairEdges, REFINE_ITERATIONS, (id) => {
    const rootId = clusterOfNode.get(id)
    if (rootId === undefined) return { mass: null, anchor: null }
    const factionPoint = positions.get(rootId)
    return factionPoint
      ? {
          mass: null,
          anchor: { point: factionPoint, ideal: anchorIdeal.get(id) ?? CLUSTER_MIN_RADIUS },
        }
      : { mass: null, anchor: null }
  })

  // 3b. Cluster separation: factions must stand at least one full visual
  //     footprint apart — each faction claims its own member-ring reach PLUS
  //     its own depth when it is itself a member of another faction (faction-
  //     as-member chains double the reach; Wren/Cathedral 18px was this).
  const ringIds = new Map<string, string>(memberOf)
  for (const [id, rootId] of associates) ringIds.set(id, rootId)
  const factionIds = nodes.filter((node) => node.kind === 'faction').map((node) => node.id)
  if (factionIds.length > 1) {
    const reachOf = new Map<string, number>()
    for (const id of factionIds) {
      let reach = 0
      for (const [memberId, rootId] of ringIds) {
        if (rootId === id) reach = Math.max(reach, anchorIdeal.get(memberId) ?? 0)
      }
      const ownDepth = ringIds.get(id)
      if (ownDepth !== undefined) reach += (anchorIdeal.get(id) ?? 0)
      reachOf.set(id, Math.max(reach, 150))
    }
    for (let pass = 0; pass < 200; pass += 1) {
      let moved = false
      for (let i = 0; i < factionIds.length; i += 1) {
        const a = factionIds[i]!
        const pa = positions.get(a)
        if (!pa) continue
        for (let j = i + 1; j < factionIds.length; j += 1) {
          const b = factionIds[j]!
          const pb = positions.get(b)
          if (!pb) continue
          const dx = pb.x - pa.x
          const dy = pb.y - pa.y
          const d = Math.hypot(dx, dy)
          const minSep = reachOf.get(a)! + reachOf.get(b)! + 180
          if (d >= minSep) continue
          const deficit = minSep - d
          const ux = d === 0 ? 1 : dx / d
          const uy = d === 0 ? 0 : dy / d
          const half = deficit / 2
          pa.x -= ux * half
          pa.y -= uy * half
          pb.x += ux * half
          pb.y += uy * half
          moved = true
        }
      }
      if (!moved) break
    }
  }

  // 4. Enforce membership (structural guarantee, not physics): project every
  //    cluster-anchored node back onto its faction ring — own distance
  //    exactly `ideal`, angle preserved. With the separation pass above the
  //    ring radius can never reach a foreign faction, so a member is always
  //    closer to its own faction than to any other.
  //    Same-type refinement can still pull two members of ONE cluster to the
  //    same angle — the angular redistribution below re-spaces each cluster's
  //    members evenly, ORDER-preserving around the ring and centred on their
  //    circular-mean direction (the organic lean survives).
  const ringOf = new Map<string, string>()
  for (const [id, rootId] of memberOf) ringOf.set(id, rootId)
  for (const [id, rootId] of associates) ringOf.set(id, rootId)
  const groupIds = new Map<string, string[]>()
  for (const [id, rootId] of ringOf) {
    const group = groupIds.get(rootId)
    if (group) group.push(id)
    else groupIds.set(rootId, [id])
  }
  for (const [rootId, group] of groupIds) {
    const root = positions.get(rootId)
    if (!root) continue
    const ring = (id: string) => anchorIdeal.get(id) ?? CLUSTER_MIN_RADIUS
    // Circular mean of the drift directions (wrap-safe). A faction that is
    // itself a member of another faction points its ring AWAY from the
    // parent, so its members can never sit between it and the grandparent.
    let sumX = 0
    let sumY = 0
    const byAngle = group
      .map((id) => {
        const point = positions.get(id)!
        return { id, angle: Math.atan2(point.y - root.y, point.x - root.x) }
      })
      .sort((a, b) => a.angle - b.angle)
    const parentOfRoot = ringIds.get(rootId)
    let mean: number
    if (parentOfRoot !== undefined) {
      const parent = positions.get(parentOfRoot)
      mean = parent ? Math.atan2(root.y - parent.y, root.x - parent.x) : 0
    } else {
      for (const entry of byAngle) {
        sumX += Math.cos(entry.angle)
        sumY += Math.sin(entry.angle)
      }
      mean = Math.atan2(sumY, sumX)
    }
    const count = byAngle.length
    const step = (2 * Math.PI) / count
    byAngle.forEach((entry, index) => {
      const angle = mean + (index - (count - 1) / 2) * step
      const radius = ring(entry.id)
      const point = positions.get(entry.id)!
      point.x = root.x + radius * Math.cos(angle)
      point.y = root.y + radius * Math.sin(angle)
    })
  }

  // 6. (removed) A per-member "membership guard" that pushes foreign
  //     factions away accumulated unboundedly across passes and ballooned the
  //     layout to thousands of px. Nested chains are handled structurally
  //     instead: chain roots' rings point away from their parent (step 4) and
  //     the reach-aware separation (step 3b) reserves each faction's full
  //     footprint.

  // 5. Ambient de-overlap: nodes NOT anchored on a ring still get separated
  //     from anything closer than one card diagonal (~150px). Ring members
  //     never move (their positions are the membership guarantee); only the
  //     free nodes give way, deterministically (later index moves, bounded
  //     passes).
  const NODE_SEP = 150
  const freeIds = nodes.map((node) => node.id).filter((id) => !ringIds.has(id))
  if (freeIds.length > 1) {
    for (let pass = 0; pass < 60; pass += 1) {
      let moved = false
      for (let i = 0; i < nodes.length; i += 1) {
        const a = nodes[i]!.id
        const pa = positions.get(a)
        if (!pa) continue
        for (let j = i + 1; j < nodes.length; j += 1) {
          const b = nodes[j]!.id
          const pb = positions.get(b)
          if (!pb) continue
          const dx = pb.x - pa.x
          const dy = pb.y - pa.y
          const d = Math.hypot(dx, dy)
          if (d >= NODE_SEP) continue
          const free = ringIds.has(a) ? (ringIds.has(b) ? null : b) : a
          if (free === null) continue // two ring members: guaranteed by rings
          const target = free === a ? pa : pb
          const other = free === a ? pb : pa
          const ux = d === 0 ? 1 : dx / d
          const uy = d === 0 ? 0 : dy / d
          const push = (NODE_SEP - d) / 2
          const awayX = free === a ? -ux : ux
          const awayY = free === a ? -uy : uy
          target.x = other.x + awayX * (d + push)
          target.y = other.y + awayY * (d + push)
          moved = true
        }
      }
      if (!moved) break
    }
  }

  // Normalize: centroid at the origin (stable, deterministic).
  let cx = 0
  let cy = 0
  for (const point of positions.values()) {
    cx += point.x
    cy += point.y
  }
  cx /= positions.size || 1
  cy /= positions.size || 1
  return nodes.map((node) => {
    const point = positions.get(node.id) ?? { x: 0, y: 0 }
    return { id: node.id, x: point.x - cx, y: point.y - cy }
  })
}

interface RefineHook {
  /** Repulsion mass multiplier (null = 1). */
  mass: number | null
  /** Optional fixed-point spring (member → faction at ring spacing). */
  anchor: { point: Point; ideal: number } | null
}

/** Seeded, damped force refinement over the given node ids. `hook` scales a
 * node's repulsion mass and/or attaches it to a fixed point (the cluster
 * spring). Iterations, damping and max step are fixed — deterministic. */
function relax(
  positions: Map<string, Point>,
  ids: string[],
  edgePairs: ReadonlyArray<readonly [string, string]>,
  iterations: number,
  hook: (id: string) => RefineHook,
): void {
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const forces = new Map<string, Point>()
    for (const id of ids) forces.set(id, { x: 0, y: 0 })

    // Centroid gravity: a soft pull to the graph's current center, so
    // edgeless nodes never drift unboundedly (1/d² repulsion alone gives a
    // t^(1/3) outward drift).
    let cx = 0
    let cy = 0
    for (const id of ids) {
      const point = positions.get(id)!
      cx += point.x
      cy += point.y
    }
    cx /= ids.length || 1
    cy /= ids.length || 1
    for (const id of ids) {
      const point = positions.get(id)!
      forces.get(id)!.x += (cx - point.x) * CENTER_GRAVITY
      forces.get(id)!.y += (cy - point.y) * CENTER_GRAVITY
    }

    // Repulsion (all pairs).
    for (let i = 0; i < ids.length; i += 1) {
      const a = ids[i]!
      const pa = positions.get(a)!
      const massA = hook(a).mass ?? 1
      for (let j = i + 1; j < ids.length; j += 1) {
        const b = ids[j]!
        const pb = positions.get(b)!
        const dx = pa.x - pb.x
        const dy = pa.y - pb.y
        const d2 = dx * dx + dy * dy + 1
        const d = Math.sqrt(d2)
        const force = (REPULSION * ((massA + (hook(b).mass ?? 1)) / 2)) / d2
        const fx = (dx / d) * force
        const fy = (dy / d) * force
        forces.get(a)!.x += fx
        forces.get(a)!.y += fy
        forces.get(b)!.x -= fx
        forces.get(b)!.y -= fy
      }
    }

    // Springs: committed edges pull endpoints to the ideal length.
    for (const [a, b] of edgePairs) {
      const pa = positions.get(a)
      const pb = positions.get(b)
      if (!pa || !pb) continue
      const dx = pb.x - pa.x
      const dy = pb.y - pa.y
      const d = Math.sqrt(dx * dx + dy * dy) || 1
      const force = (d - IDEAL_EDGE) * EDGE_SPRING
      const fx = (dx / d) * force
      const fy = (dy / d) * force
      forces.get(a)!.x += fx
      forces.get(a)!.y += fy
      forces.get(b)!.x -= fx
      forces.get(b)!.y -= fy
    }

    // Cluster anchors: members spring back to their faction's LIVE location.
    for (const id of ids) {
      const anchor = hook(id).anchor
      if (!anchor) continue
      const point = positions.get(id)!
      const target = anchor.point
      const dx = target.x - point.x
      const dy = target.y - point.y
      const d = Math.sqrt(dx * dx + dy * dy) || 1
      const force = (d - anchor.ideal) * CLUSTER_SPRING
      forces.get(id)!.x += (dx / d) * force
      forces.get(id)!.y += (dy / d) * force
    }

    // Apply with damping + a max step (no explosions, still deterministic).
    for (const id of ids) {
      const point = positions.get(id)!
      const { x: fx, y: fy } = forces.get(id)!
      const length = Math.sqrt(fx * fx + fy * fy) || 1
      const clamp = Math.min(length, MAX_STEP) / length
      point.x += fx * clamp * DAMPING
      point.y += fy * clamp * DAMPING
    }
  }
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
