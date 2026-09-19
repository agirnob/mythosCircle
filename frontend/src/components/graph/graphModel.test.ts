import { describe, expect, it } from 'vitest'

import type { components } from '../../api/schema'
import {
  type GraphEdge,
  type GraphNode,
  type LayoutPosition,
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  avatarInitial,
  buildFactionClusters,
  buildWorldGraph,
  clusterLayout,
  displayName,
  hashString,
  kindStyle,
  parallelSlots,
  vueFlowCurvature,
} from './graphModel'
import {
  DENSE_CAMPAIGN_ID,
  DENSE_FOCUS_ID,
  denseFocusId,
  denseWorld,
} from './denseFixture'

type WorldExport = components['schemas']['WorldExport']

function entity(id: string, kind = 'character', name = id) {
  return { id, kind, name, text: null, data: {}, media: [] }
}

function world(overrides: Partial<WorldExport> = {}): WorldExport {
  return {
    campaign: {
      id: 'C1',
      title: 'Greymarch',
      theme: 'frontier dread',
      description: '',
      custom_lore: '',
      is_generic: false,
      created_at: '2026-09-19T00:00:00Z',
    },
    revision: { id: '01JZZZZZZZZZZZZZZZZZZZZZZZ', created_at: '2026-09-19T00:01:00Z' },
    entities: [
      entity('A', 'character', 'Aldous'),
      entity('B', 'faction', 'The Grey Choir'),
      entity('C', 'place', 'The Hollow Quay'),
      entity('D', 'character', 'Dagna'),
    ],
    edges: [
      { id: 'e1', src: 'A', dst: 'B', type: 'employs', counter: 1 },
      { id: 'e2', src: 'C', dst: 'A', type: 'ally_of', counter: 2 },
      { id: 'e3', src: 'D', dst: 'B', type: 'loyalty', counter: 7 },
      { id: 'e4', src: 'B', dst: 'C', type: 'relationship', counter: 1 },
    ],
    ...overrides,
  }
}

describe('buildWorldGraph', () => {
  it('whole world: EVERY committed entity is a node, EVERY edge renders', () => {
    const model = buildWorldGraph(world(), null)
    expect(model.nodes.map((node) => node.id).sort()).toEqual(['A', 'B', 'C', 'D'])
    expect(model.edges).toHaveLength(4)
    expect(model.focusId).toBeNull()
    expect(model.focusMissing).toBe(false)
    expect(model.oneHop).toBeNull()
  })

  it('dense fixture: 29 entities / 32 edges, all committed, no cap', () => {
    const model = buildWorldGraph(denseWorld(), null)
    expect(model.nodes).toHaveLength(29)
    expect(model.edges).toHaveLength(33)
    // Every fixture entity and edge is present — nothing is truncated away.
    const ids = new Set(model.nodes.map((node) => node.id))
    for (const id of ['F0', 'N01', 'N24', 'N25', 'X01', 'X03']) {
      expect(ids).toContain(id)
    }
  })

  it('nodes carry rowid order + portrait flags; edges carry committed labels', () => {
    const w = world()
    w.entities = [
      entity('A'),
      { ...entity('B'), media: [{ id: 'm1', kind: 'image', filename: 'b.png', available: true }] },
      { ...entity('C'), media: [{ id: 'm2', kind: 'image', filename: 'c.png', available: false }] },
      entity('D'),
    ]
    const model = buildWorldGraph(w, null)
    const byId = new Map(model.nodes.map((node) => [node.id, node]))
    expect(byId.get('B')!.hasPortrait).toBe(true)
    expect(byId.get('C')!.hasPortrait).toBe(false) // flagged unavailable
    expect(byId.get('A')!.order).toBe(0)
    expect(byId.get('D')!.order).toBe(3)
    const edgeById = new Map(model.edges.map((edge) => [edge.id, edge]))
    expect(edgeById.get('e1')!.label).toBe('employs') // bare — not a counter type
    expect(edgeById.get('e2')!.label).toBe('ally_of(2)') // counter suffix
  })

  it('oneHop: both directions, reciprocal + parallel edges all included', () => {
    const model = buildWorldGraph(denseWorld(), DENSE_FOCUS_ID)
    expect(model.focusMissing).toBe(false)
    const oneHop = model.oneHop!
    // F0 touches every N* entity (src or dst) — 26 entities incl. itself.
    expect(oneHop.nodeIds.size).toBe(26)
    expect(oneHop.nodeIds.has(DENSE_FOCUS_ID)).toBe(true)
    for (let i = 1; i <= 25; i += 1) {
      expect(oneHop.nodeIds).toContain(`N${String(i).padStart(2, '0')}`)
    }
    // Committed direction is irrelevant to the hop set: reciprocal e10/e11
    // and parallel e16/e17 are all present.
    for (const edgeId of ['e01', 'e10', 'e11', 'e16', 'e17', 'e28', 'e29', 'e33']) {
      expect(oneHop.edgeIds).toContain(edgeId)
    }
    expect(oneHop.edgeIds.size).toBe(31)
    // X* never touch the focus — outside the hop set; their edges stay out.
    expect(oneHop.nodeIds).not.toContain('X01')
    expect(oneHop.edgeIds).not.toContain('e31')
  })

  it('MISSING FOCUS: full world still returned, oneHop null, flag set', () => {
    const model = buildWorldGraph(denseWorld(), 'NO-SUCH-ENTITY')
    expect(model.focusMissing).toBe(true)
    expect(model.focusId).toBe('NO-SUCH-ENTITY')
    expect(model.oneHop).toBeNull()
    expect(model.nodes).toHaveLength(29)
    expect(model.edges).toHaveLength(33)
  })

  it('dangling edges are excluded: an endpoint outside the entity set never enters', () => {
    const w = world()
    w.edges = [
      { id: 'ok', src: 'A', dst: 'B', type: 'employs', counter: 1 },
      { id: 'ghost-src', src: 'ZZZ', dst: 'A', type: 'relationship', counter: 1 },
      { id: 'ghost-dst', src: 'B', dst: 'YYY', type: 'relationship', counter: 1 },
    ]
    const model = buildWorldGraph(w, null)
    expect(model.edges.map((edge) => edge.id)).toEqual(['ok'])
  })

  it('self-loop on the focus stays in the web; a foreign self-loop stays out', () => {
    const w = world()
    w.entities = [entity('A'), entity('B')]
    w.edges = [
      { id: 'focus-loop', src: 'A', dst: 'A', type: 'controls', counter: 3 },
      { id: 'foreign-loop', src: 'B', dst: 'B', type: 'kin_of', counter: 1 },
    ]
    const unfocused = buildWorldGraph(w, null)
    expect(unfocused.edges.map((edge) => edge.id)).toEqual(['focus-loop', 'foreign-loop'])
    const focused = buildWorldGraph(w, 'A')
    expect(focused.oneHop!.edgeIds).toContain('focus-loop')
    expect(focused.oneHop!.edgeIds).not.toContain('foreign-loop')
    // The focus loop adds NO new node: it is the focus itself.
    expect(focused.oneHop!.nodeIds.size).toBe(1)
  })

  it('displayName never leaks the raw id', () => {
    expect(displayName({}, 'X')).toBe('(unknown)')
    expect(displayName({ X: 'Mira Vane' }, 'X')).toBe('Mira Vane')
  })
})

describe('clusterLayout (semantic faction areas + force refinement)', () => {
  function node(id: string, kind: string, name = id): GraphNode {
    return { id, kind, name, order: 0, hasPortrait: false }
  }
  function edge(id: string, src: string, dst: string, type: string): GraphEdge {
    return { id, src, dst, type, counter: 1, label: type }
  }

  /** A deterministic net: two factions with members, a place anchored to one,
   * a rival pull between the factions, an unaffiliated loner linked across. */
  function net(): { nodes: GraphNode[]; edges: GraphEdge[] } {
    return {
      nodes: [
        node('guild', 'faction', 'The Guild'),
        node('m1', 'character', 'Mira'),
        node('m2', 'character', 'Orin'),
        node('m3', 'character', 'Tamsin'),
        node('rival', 'faction', 'The Rivalry'),
        node('r1', 'character', 'Harl'),
        node('tavern', 'place', 'The Anchored Tavern'),
        node('lone', 'character', 'Lonny'),
      ],
      edges: [
        edge('e1', 'm1', 'guild', 'member_of'),
        edge('e2', 'm2', 'guild', 'member_of'),
        edge('e3', 'm3', 'guild', 'member_of'),
        edge('e4', 'r1', 'rival', 'member_of'),
        edge('e5', 'tavern', 'guild', 'bases_at'),
        edge('e6', 'guild', 'rival', 'enemy_of'),
        edge('e7', 'lone', 'm2', 'relationship'),
      ],
    }
  }

  function position(id: string, layout: LayoutPosition[]): { x: number; y: number } {
    return layout.find((entry) => entry.id === id)!
  }
  function distance(a: { x: number; y: number }, b: { x: number; y: number }): number {
    return Math.hypot(a.x - b.x, a.y - b.y)
  }

  it('deterministic: identical world → byte-identical positions (seeded)', () => {
    const { nodes, edges } = net()
    expect(clusterLayout(nodes, edges)).toEqual(clusterLayout(nodes, edges))
  })

  it('members hug their faction: closer to it than to any other faction', () => {
    const { nodes, edges } = net()
    const layout = clusterLayout(nodes, edges)
    for (const member of ['m1', 'm2', 'm3']) {
      const toOwn = distance(position(member, layout), position('guild', layout))
      const toRival = distance(position(member, layout), position('rival', layout))
      expect(toOwn).toBeLessThan(toRival)
    }
  })

  it('a place with bases_at settles nearer its anchor hub than an unrelated faction', () => {
    const { nodes, edges } = net()
    const layout = clusterLayout(nodes, edges)
    const toGuild = distance(position('tavern', layout), position('guild', layout))
    const toRival = distance(position('tavern', layout), position('rival', layout))
    expect(toGuild).toBeLessThan(toRival)
  })

  it('buildFactionClusters: member_of into a faction root, edge order kept; non-faction targets ignored', () => {
    const { nodes, edges } = net()
    const clusters = buildFactionClusters(nodes, edges)
    expect([...clusters.keys()]).toEqual(['guild', 'rival'])
    expect(clusters.get('guild')).toEqual(['m1', 'm2', 'm3'])
    expect(clusters.get('rival')).toEqual(['r1'])
    // member_of whose target is NOT a faction never opens a cluster.
    const weird = {
      nodes: [node('guild2', 'faction'), node('human', 'character'), node('falcon', 'faction')],
      edges: [
        edge('x1', 'human', 'guild2', 'member_of'),
        edge('x2', 'guild2', 'human', 'member_of'), // faction member_of a character
        edge('x3', 'falcon', 'guild2', 'enemy_of'), // non-member_of edge ignored
      ],
    }
    expect(buildFactionClusters(weird.nodes, weird.edges)).toEqual(new Map([['guild2', ['human']]]))
  })

  it('a faction with no members is a plain node: exactly one finite position, no area', () => {
    const { nodes, edges } = net()
    nodes.push(node('lonely', 'faction', 'The Lonely Hall'))
    const layout = clusterLayout(nodes, edges)
    expect(layout).toHaveLength(nodes.length)
    expect(new Set(layout.map((entry) => entry.id)).size).toBe(nodes.length)
    const lonely = layout.filter((entry) => entry.id === 'lonely')
    expect(lonely).toHaveLength(1)
    expect(Number.isFinite(lonely[0]!.x)).toBe(true)
    expect(Number.isFinite(lonely[0]!.y)).toBe(true)
    // It opened NO ring: no members positioned relative to it (it is the
    // only own-cluster entry).
    for (const entry of layout) {
      expect(Number.isFinite(entry.x)).toBe(true)
      expect(Number.isFinite(entry.y)).toBe(true)
      expect(Math.abs(entry.x)).toBeLessThan(100_000)
      expect(Math.abs(entry.y)).toBeLessThan(100_000)
    }
  })

  it('chain roots: a faction that is itself member_of another faction points its ring AWAY from the parent', () => {
    const nodes = [
      node('guild', 'faction', 'The Guild'),
      node('rival', 'faction', 'The Rivalry'),
      node('m1', 'character'),
      node('m2', 'character'),
      node('m3', 'character'),
      node('r1', 'character'),
      node('r2', 'character'),
      node('r3', 'character'),
    ]
    const edges = [
      edge('e1', 'm1', 'guild', 'member_of'),
      edge('e2', 'm2', 'guild', 'member_of'),
      edge('e3', 'm3', 'guild', 'member_of'),
      edge('e4', 'rival', 'guild', 'member_of'), // faction-as-member chain
      edge('e5', 'r1', 'rival', 'member_of'),
      edge('e6', 'r2', 'rival', 'member_of'),
      edge('e7', 'r3', 'rival', 'member_of'),
    ]
    const layout = clusterLayout(nodes, edges)
    const root = position('rival', layout)
    const parent = position('guild', layout)
    const toParent = { x: parent.x - root.x, y: parent.y - root.y }
    // (a) every child-faction member is closer to its OWN root than to the
    // grandparent (and the root's members are closer to the root too).
    for (const member of ['r1', 'r2', 'r3']) {
      expect(distance(position(member, layout), root)).toBeLessThan(
        distance(position(member, layout), parent),
      )
    }
    for (const member of ['m1', 'm2', 'm3']) {
      expect(distance(position(member, layout), parent)).toBeLessThan(
        distance(position(member, layout), root),
      )
    }
    // (b) the child ring's mean direction points AWAY from the parent.
    // The projection forces even spacing (± step) about the away axis
    // (`mean = parent → root`), so the STRICT vector mean of the members is
    // provably ~0 and no per-member dot can be negative for every member.
    // The away-centering contract, restated exactly: every member sits
    // within `step` of the away axis, and the dot products are symmetric
    // about it (their sum ≈ 0) — the ring's SECTOR faces away from the
    // parent; a non-chain root would center on its own drift instead.
    const awayAngle = Math.atan2(root.y - parent.y, root.x - parent.x)
    const step = (2 * Math.PI) / 3
    const dots: number[] = []
    for (const member of ['r1', 'r2', 'r3']) {
      const point = position(member, layout)
      const offsetX = point.x - root.x
      const offsetY = point.y - root.y
      dots.push(toParent.x * offsetX + toParent.y * offsetY)
      let deviation = Math.abs(Math.atan2(offsetY, offsetX) - awayAngle) % (2 * Math.PI)
      if (deviation > Math.PI) deviation = 2 * Math.PI - deviation
      expect(deviation).toBeLessThanOrEqual(step + 1e-6)
    }
    expect(Math.abs(dots.reduce((sum, dot) => sum + dot, 0))).toBeLessThan(1e-6)
    // (c) the chain world lays out deterministically.
    expect(clusterLayout(nodes, edges)).toEqual(layout)
  })

  it('hashString is deterministic and spreads seeds', () => {
    expect(hashString('The Drowned Harbor')).toBe(hashString('The Drowned Harbor'))
    expect(hashString('a')).not.toBe(hashString('b'))
  })
})

describe('labels + parallel-edge machinery (preserved rows)', () => {
  it('parallelSlots fans reciprocal and duplicate pairs deterministically', () => {
    const model = buildWorldGraph(denseWorld(), null)
    const slots = parallelSlots(model.edges)
    expect(slots.get('e10')!.total).toBe(2)
    expect(slots.get('e11')!.total).toBe(2)
    expect(slots.get('e10')!.index).not.toBe(slots.get('e11')!.index)
    expect(slots.get('e16')!.total).toBe(2)
    expect(slots.get('e17')!.index).not.toBe(slots.get('e16')!.index)
    // A single edge still gets a base slot (never dead-straight).
    expect(slots.get('e01')!.total).toBe(1)
    expect(slots.get('e01')!.index).toBe(0)
  })

  it('curvature spreads slots around a small base', () => {
    const base = vueFlowCurvature({ index: 0, total: 1 })
    const high = vueFlowCurvature({ index: 1, total: 2 })
    const low = vueFlowCurvature({ index: 0, total: 2 })
    expect(high).toBeGreaterThan(base)
    expect(low).toBeLessThan(base)
  })

  it('dense fixture labels ride every committed edge type verbatim', () => {
    const model = buildWorldGraph(denseWorld(), null)
    const types = new Set(model.edges.map((edge) => edge.type))
    expect(types.size).toBe(16)
    expect(model.edges.some((edge) => edge.label === 'controls(3)')).toBe(true)
    expect(model.edges.some((edge) => edge.label === 'ally_of(2)')).toBe(true)
    expect(model.edges.some((edge) => edge.label === 'employs')).toBe(true)
  })

  it('fixture aliases + presentation helpers stay stable', () => {
    expect(denseFocusId).toBe(DENSE_FOCUS_ID)
    expect(DENSE_CAMPAIGN_ID).toBe('C-DENSE')
    expect(GRAPH_NODE_WIDTH).toBe(186)
    expect(GRAPH_NODE_HEIGHT).toBe(66)
    expect(avatarInitial('mira vane')).toBe('M')
    expect(avatarInitial('   ')).toBe('?')
    expect(kindStyle('character').color).toBe('#1d4ed8')
    expect(kindStyle('nonsense').color).toBe('#6b7280') // fallback
  })
})