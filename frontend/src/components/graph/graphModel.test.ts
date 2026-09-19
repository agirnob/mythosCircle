import { describe, expect, it } from 'vitest'

import type { components } from '../../api/schema'
import {
  ENTITY_CAP,
  buildGraphModel,
  parallelSlots,
} from './graphModel'
import {
  DENSE_ELIGIBLE_NEIGHBORS,
  DENSE_FOCUS_ID,
  DENSE_VISIBLE_NODES,
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

describe('graphModel', () => {
  it('bounded 1-hop happy path: both directions, edgeLabel labels, committed direction', () => {
    const model = buildGraphModel(world(), 'A')
    expect(model.focusId).toBe('A')
    expect(model.focusMissing).toBe(false)
    expect(model.focusDefaulted).toBe(false)
    expect(model.nodes.map((node) => node.id).sort()).toEqual(['A', 'B', 'C'])
    const focus = model.nodes.find((node) => node.id === 'A')!
    expect(focus.focus).toBe(true)
    expect(model.nodes.filter((node) => node.focus)).toHaveLength(1)
    const byId = new Map(model.edges.map((edge) => [edge.id, edge]))
    expect(byId.get('e1')!.label).toBe('employs') // not a counter type → bare
    expect(byId.get('e2')!.label).toBe('ally_of(2)') // counter type → suffix
    // Committed direction, folded into src/dst — never invented.
    expect(byId.get('e1')!.outbound).toBe(true) // A -> B
    expect(byId.get('e2')!.outbound).toBe(false) // C -> A
    // D has an edge only to B (not to the focus) — outside the window.
    expect(model.nodes.some((node) => node.id === 'D')).toBe(false)
    expect(model.edges.some((edge) => edge.src === 'D' || edge.dst === 'D')).toBe(false)
  })

  it('NO FOCUS: defaults to the most-connected entity (hub), flagged', () => {
    const model = buildGraphModel(world(), null)
    // Degrees: A=2, B=3 (hub), C=2, D=1 — B wins, not first-row A.
    expect(model.focusId).toBe('B')
    expect(model.focusDefaulted).toBe(true)
    expect(model.focusMissing).toBe(false)
    // The hub's 1-hop web: every entity (A, C, D all touch B) and all edges.
    expect(model.nodes.map((node) => node.id).sort()).toEqual(['A', 'B', 'C', 'D'])
    expect(model.edges).toHaveLength(4)
  })

  it('NO FOCUS in an edgeless world falls back to the first entity', () => {
    const isolated = world()
    isolated.edges = []
    const model = buildGraphModel(isolated, null)
    expect(model.focusId).toBe('A') // all degrees 0 → first in rowid order
    expect(model.focusDefaulted).toBe(true)
    expect(model.nodes.map((node) => node.id)).toEqual(['A'])
    expect(model.edges).toEqual([])
  })

  it('NO FOCUS hub pick is deterministic across runs (degree tie-break: rowid)', () => {
    const twoHubs = world()
    // A and B both degree 3 (max); others lower — A (first rowid) must win.
    twoHubs.edges = [
      { id: 'e1', src: 'A', dst: 'B', type: 'relationship', counter: 1 },
      { id: 'e2', src: 'A', dst: 'C', type: 'ally_of', counter: 2 },
      { id: 'e3', src: 'B', dst: 'D', type: 'loyalty', counter: 7 },
      { id: 'e4', src: 'B', dst: 'C', type: 'relationship', counter: 1 },
      { id: 'e5', src: 'A', dst: 'D', type: 'employs', counter: 1 },
    ]
    const first = buildGraphModel(twoHubs, null)
    const again = buildGraphModel(twoHubs, null)
    expect(first.focusId).toBe('A')
    expect(again.focusId).toBe('A')
    expect(first.nodes).toEqual(again.nodes)
  })

  it('MISSING FOCUS: unknown id yields the empty set flagged for the view', () => {
    const model = buildGraphModel(world(), 'NOPE')
    expect(model.focusMissing).toBe(true)
    expect(model.nodes).toEqual([])
    expect(model.edges).toEqual([])
    expect(model.focusId).toBeNull()
  })

  it('EMPTY WORLD: empty set, no default focus, no missing flag', () => {
    const model = buildGraphModel(world({ entities: [], edges: [] }), null)
    expect(model.focusId).toBeNull()
    expect(model.nodes).toEqual([])
    expect(model.edges).toEqual([])
    expect(model.focusMissing).toBe(false)
    expect(model.focusDefaulted).toBe(false)
  })

  it('self-loop on the focus stays in the web; foreign self-loops stay out', () => {
    const selfLoopWorld = world({
      edges: [
        { id: 'loop', src: 'A', dst: 'A', type: 'controls', counter: 3 },
        { id: 'e1', src: 'A', dst: 'B', type: 'employs', counter: 1 },
        { id: 'foreign', src: 'C', dst: 'C', type: 'kin_of', counter: 1 },
      ],
    })
    const model = buildGraphModel(selfLoopWorld, 'A')
    expect(model.edges.map((edge) => edge.id)).toContain('loop')
    expect(model.edges.some((edge) => edge.id === 'foreign')).toBe(false)
  })

  it('portrait flag reflects only available image refs', () => {
    const withPortrait = world()
    withPortrait.entities = [
      entity('A'),
      { ...entity('B'), media: [{ id: 'm1', kind: 'image', filename: 'b.png', available: true }] },
      { ...entity('C'), media: [{ id: 'm2', kind: 'image', filename: 'c.png', available: false }] },
      entity('D'),
    ]
    const model = buildGraphModel(withPortrait, 'A')
    const hasPortrait = new Map(model.nodes.map((node) => [node.id, node.hasPortrait]))
    expect(hasPortrait.get('A')).toBe(false)
    expect(hasPortrait.get('B')).toBe(true)
    expect(hasPortrait.get('C')).toBe(false) // flagged unavailable
  })

  it('dense fixture: deterministic cap truncation with the notice flags', () => {
    const first = buildGraphModel(denseWorld(), DENSE_FOCUS_ID)
    const again = buildGraphModel(denseWorld(), DENSE_FOCUS_ID)
    expect(first).toEqual(again) // deterministic across runs
    expect(first.nodes).toHaveLength(DENSE_VISIBLE_NODES) // focus + 23
    expect(first.totalNeighbors).toBe(DENSE_ELIGIBLE_NEIGHBORS) // 25 eligible
    expect(first.truncation).toBe(true)
    // The cap is the AR6 mirror: stop a 24-entity window at the boundary.
    expect(first.nodes.length).toBeLessThanOrEqual(ENTITY_CAP)
  })

  it('dense fixture: truncated and foreign entities never appear; their edges neither', () => {
    const model = buildGraphModel(denseWorld(), DENSE_FOCUS_ID)
    const present = new Set(model.nodes.map((node) => node.id))
    for (let i = 1; i <= 23; i += 1) {
      expect(present).toContain(`N${String(i).padStart(2, '0')}`)
    }
    // N24/N25 were eligible but dropped deterministically; X* never were.
    expect(present).not.toContain('N24')
    expect(present).not.toContain('N25')
    expect(present).not.toContain('X01')
    expect(present).not.toContain('X02')
    expect(present).not.toContain('X03')
    for (const edge of model.edges) {
      expect(present).toContain(edge.src)
      expect(present).toContain(edge.dst)
    }
  })

  it('dense fixture: reciprocal, parallel, labelled, dense-type, all-kind content', () => {
    const model = buildGraphModel(denseWorld(), DENSE_FOCUS_ID)
    const byPair = new Map<string, number>()
    for (const edge of model.edges) {
      const key = [edge.src, edge.dst].sort().join('|')
      byPair.set(key, (byPair.get(key) ?? 0) + 1)
    }
    // e10/e11: A->B + B->A (reciprocal, same type).
    expect(model.edges.filter((edge) => edge.src === 'N10' && edge.dst === DENSE_FOCUS_ID)).toHaveLength(1)
    expect(model.edges.filter((edge) => edge.src === DENSE_FOCUS_ID && edge.dst === 'N10')).toHaveLength(1)
    // e14/e15 and e16/e17: parallel same-direction pairs.
    expect(model.edges.filter((edge) => edge.src === DENSE_FOCUS_ID && edge.dst === 'N12')).toHaveLength(2)
    expect(model.edges.filter((edge) => edge.src === DENSE_FOCUS_ID && edge.dst === 'N13')).toHaveLength(2)
    // Every pair group has 2+ entries where the fixture says so.
    expect(byPair.get(['N10', DENSE_FOCUS_ID].sort().join('|'))).toBe(2)
    // All 16 vocabulary types appear (dense labels).
    const types = new Set(model.edges.map((edge) => edge.type))
    expect(types.size).toBe(16)
    // Incoming AND outgoing relative to the focus exist.
    expect(model.edges.some((edge) => edge.outbound)).toBe(true)
    expect(model.edges.some((edge) => !edge.outbound)).toBe(true)
    // All three mockup kinds are distinguishable.
    const kinds = new Set(model.nodes.map((node) => node.kind))
    expect(kinds).toContain('character')
    expect(kinds).toContain('faction')
    expect(kinds).toContain('place')
    // Long names + missing portraits are exercised.
    expect(model.nodes.some((node) => node.name.length > 40)).toBe(true)
    expect(model.nodes.some((node) => node.hasPortrait)).toBe(true) // N01's available ref
  })

  it('parallelSlots: reciprocal pairs and duplicates fan deterministically', () => {
    const model = buildGraphModel(denseWorld(), DENSE_FOCUS_ID)
    const slots = parallelSlots(model.edges)
    const e10 = slots.get('e10')!
    const e11 = slots.get('e11')!
    expect(e10.total).toBe(2)
    expect(e11.total).toBe(2)
    expect(e10.index).not.toBe(e11.index)
    const e16 = slots.get('e16')!
    const e17 = slots.get('e17')!
    expect(e16.total).toBe(2)
    expect(e17.index).not.toBe(e16.index)
    // A lone edge still gets a base slot (never a dead-straight line).
    expect(slots.get('e01')!.total).toBe(1)
    expect(slots.get('e01')!.index).toBe(0)
  })

  it('array order drives truncation order; hub default is degree-based, not array position', () => {
    const reordered = world()
    reordered.entities = [entity('C'), entity('A'), entity('B'), entity('D')]
    // Hub semantics: B (degree 3) wins regardless of array position.
    const defaulted = buildGraphModel(reordered, null)
    expect(defaulted.focusId).toBe('B')
    // Truncation still follows ARRAY order: explicit-focus web keeps neighbors
    // in array order (C first, then A, then B).
    const model = buildGraphModel(reordered, 'D')
    // D's 1-hop: B only (e3) — D, then its neighbor in array order.
    expect(model.nodes.map((node) => node.id)).toEqual(['D', 'B'])
  })

  it('cap boundary behavior: 23 neighbors stay, 24 neighbors truncate (flag + counts)', () => {
    // Exactly at the boundary: 23 neighbors + focus = 24 entities, no notice.
    const atCap = denseWorld()
    atCap.entities = atCap.entities.slice(0, 24) // F0 + N01..N23 (23 neighbors)
    atCap.edges = atCap.edges.filter(
      (edge) => edge.src !== 'N24' && edge.dst !== 'N24' && edge.src !== 'N25' && edge.dst !== 'N25',
    )
    const full = buildGraphModel(atCap, DENSE_FOCUS_ID)
    expect(full.nodes).toHaveLength(24)
    expect(full.totalNeighbors).toBe(23)
    expect(full.truncation).toBe(false)
    // One over the cap: N24 becomes eligible again → the LAST neighbor in
    // array order (N24) drops, keeping exactly 24 nodes. (Eligibility comes
    // from the committed edges, so N25's edge must leave the fixture.)
    const over = denseWorld()
    over.edges = over.edges.filter((edge) => edge.src !== 'N25' && edge.dst !== 'N25')
    const truncated = buildGraphModel(over, DENSE_FOCUS_ID)
    expect(truncated.totalNeighbors).toBe(24)
    expect(truncated.truncation).toBe(true)
    expect(truncated.nodes).toHaveLength(24)
    expect(truncated.nodes.some((node) => node.id === 'N24')).toBe(false)
    expect(truncated.nodes.some((node) => node.id === 'N23')).toBe(true)
  })
})