import { describe, expect, it } from 'vitest'

import type { components } from '../../api/schema'
import {
  CARD_PITCH,
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  avatarInitial,
  buildWorldGraph,
  concentricLayoutByKind,
  displayName,
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

describe('concentricLayoutByKind', () => {
  function radius(id: string, positions: Array<{ id: string; x: number; y: number }>): number {
    const position = positions.find((entry) => entry.id === id)!
    return Math.hypot(position.x, position.y)
  }

  it('deterministic: identical input → identical output', () => {
    const nodes = [
      { id: 'a', kind: 'character', name: 'a', order: 0, hasPortrait: false },
      { id: 'b', kind: 'faction', name: 'b', order: 1, hasPortrait: false },
      { id: 'c', kind: 'place', name: 'c', order: 2, hasPortrait: false },
    ]
    expect(concentricLayoutByKind(nodes)).toEqual(concentricLayoutByKind(nodes))
  })

  it('rings ordered by node count — the LARGEST kind is the OUTERMOST', () => {
    const nodes = [
      ...Array.from({ length: 3 }, (_, i) => ({ id: `c${i}`, kind: 'character', name: `c${i}`, order: i, hasPortrait: false })),
      { id: 'f', kind: 'faction', name: 'f', order: 99, hasPortrait: false },
      ...Array.from({ length: 5 }, (_, i) => ({ id: `p${i}`, kind: 'place', name: `p${i}`, order: 100 + i, hasPortrait: false })),
    ]
    const layout = concentricLayoutByKind(nodes)
    // 5 places (outer), 3 characters (middle), 1 faction (inner).
    const faction = radius('f', layout)
    const character = radius('c0', layout)
    const place = radius('p0', layout)
    expect(faction).toBeLessThan(character)
    expect(character).toBeLessThan(place)
    // Faction (1 node) sits at the inner clearance — never closer to (0,0).
    expect(faction).toBeGreaterThanOrEqual(CARD_PITCH)
  })

  it('count ties break by kind order: character inner, place outer', () => {
    const nodes = [
      ...Array.from({ length: 2 }, (_, i) => ({ id: `c${i}`, kind: 'character', name: `c${i}`, order: i, hasPortrait: false })),
      ...Array.from({ length: 2 }, (_, i) => ({ id: `f${i}`, kind: 'faction', name: `f${i}`, order: 10 + i, hasPortrait: false })),
      ...Array.from({ length: 2 }, (_, i) => ({ id: `p${i}`, kind: 'place', name: `p${i}`, order: 20 + i, hasPortrait: false })),
    ]
    const layout = concentricLayoutByKind(nodes)
    expect(radius('c0', layout)).toBeLessThan(radius('f0', layout))
    expect(radius('f0', layout)).toBeLessThan(radius('p0', layout))
  })

  it('rings never invert: sparse outer ring cannot fall inside a dense inner one', () => {
    const nodes = [
      ...Array.from({ length: 12 }, (_, i) => ({ id: `c${i}`, kind: 'character', name: `c${i}`, order: i, hasPortrait: false })),
      ...Array.from({ length: 2 }, (_, i) => ({ id: `f${i}`, kind: 'faction', name: `f${i}`, order: 20 + i, hasPortrait: false })),
    ]
    const layout = concentricLayoutByKind(nodes)
    // 12 characters outrank 2 factions: characters go outer despite the
    // faction ring being sparse.
    expect(radius('c0', layout)).toBeGreaterThan(radius('f0', layout))
  })

  it('per-kind ring radii follow the count formula plus ring clearance', () => {
    const nodes = [
      ...Array.from({ length: 4 }, (_, i) => ({ id: `f${i}`, kind: 'faction', name: `f${i}`, order: i, hasPortrait: false })),
      ...Array.from({ length: 2 }, (_, i) => ({ id: `p${i}`, kind: 'place', name: `p${i}`, order: 10 + i, hasPortrait: false })),
    ]
    const layout = concentricLayoutByKind(nodes)
    // p-ring inner (2 nodes): max(2*190/2π, 190) = 190.
    // f-ring outer (4 nodes): max(4*190/2π≈121, 190+190=380) = 380.
    expect(radius('p0', layout)).toBeCloseTo(CARD_PITCH, 0)
    expect(radius('f0', layout)).toBeCloseTo(CARD_PITCH * 2, 0)
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