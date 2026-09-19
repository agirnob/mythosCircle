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
    expect(model.edges).toHaveLength(32)
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

  it('outbound is the committed direction relative to the focus', () => {
    const model = buildWorldGraph(world(), 'A')
    const edgeById = new Map(model.edges.map((edge) => [edge.id, edge]))
    expect(edgeById.get('e1')!.outbound).toBe(true) // A -> B
    expect(edgeById.get('e2')!.outbound).toBe(false) // C -> A
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
    for (const edgeId of ['e01', 'e10', 'e11', 'e16', 'e17', 'e28', 'e29']) {
      expect(oneHop.edgeIds).toContain(edgeId)
    }
    expect(oneHop.edgeIds.size).toBe(30)
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
    expect(model.edges).toHaveLength(32)
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
    return { id, src, dst, type, counter: 1, label: type, outbound: false }
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

  it('a faction with no members is a plain node: no NaN, bounded extent, one position per node', () => {
    const { nodes, edges } = net()
    const layout = clusterLayout(nodes, edges)
    expect(layout).toHaveLength(nodes.length)
    expect(new Set(layout.map((entry) => entry.id)).size).toBe(nodes.length)
    for (const entry of layout) {
      expect(Number.isFinite(entry.x)).toBe(true)
      expect(Number.isFinite(entry.y)).toBe(true)
      expect(Math.abs(entry.x)).toBeLessThan(100_000)
      expect(Math.abs(entry.y)).toBeLessThan(100_000)
    }
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