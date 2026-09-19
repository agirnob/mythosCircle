/**
 * Story 7.1 — the dense fixture BOTH A/B candidates render and the tests
 * assert against (spec-7-1: "24 visible nodes, >23 eligible neighbors, long
 * names, missing portraits, incoming + outgoing edges, reciprocal pairs,
 * multiple edges between the same pair, same- and cross-type edges, dense
 * labels").
 *
 * Constructed from the committed-data shapes only (WorldExport); ids are
 * readable for debugging. All 16 EDGE_VOCAB types appear, counters ride the
 * 8 COUNTER_TYPES, and the neighbor cap (23 + focus = 24 entities) is
 * exceeded two-fold so truncation is exercised deterministically:
 * N24/N25 are eligible neighbors but drop out in world.entities order;
 * X01..X03 exist but have no edge to the focus, so they (and their edges)
 * are never in the web.
 */

import type { components } from '../../api/schema'

type WorldExport = components['schemas']['WorldExport']
type EntityExport = components['schemas']['EntityExport']
type EdgeExport = components['schemas']['EdgeExport']

export const DENSE_CAMPAIGN_ID = 'C-DENSE'
export const DENSE_FOCUS_ID = 'F0'

/** Eligible 1-hop neighbors — the >23 count that forces truncation. */
export const DENSE_ELIGIBLE_NEIGHBORS = 25
/** Visible nodes: focus + the 23 kept neighbors. */
export const DENSE_VISIBLE_NODES = 24

const KINDS = ['character', 'faction', 'place'] as const

function entity(id: string, kind: string, name: string, media: EntityExport['media'] = []): EntityExport {
  return { id, kind, name, text: null, data: {}, media }
}

const PORTRAIT_REF: EntityExport['media'] = [
  { id: 'm-01', kind: 'image', filename: 'pike-and-company.png', available: true },
]

const LONG_NAMES: Record<string, string> = {
  N01: 'The Grand Aerie of the Salt-Crusted Sky-Pirates',
  N02: 'Marrowdeep Cistern Vaults beneath Old Dock Row',
  N03: 'The Consortium of Lantern-Keepers and Dredge-Masters',
  N07: 'Loremaster Elara Wick of the Ninth Tidal Choir',
  N11: 'The Harbormaster’s Guildhall of Sunken Stairways',
  N18: 'The Sunken Quarter of the Forgotten Bell Foundry',
}

function nameFor(id: string, kind: string, index: number): string {
  const custom = LONG_NAMES[id]
  if (custom) return custom
  const kinds = ['Harper', 'Warden', 'Guild', 'Tide', 'Ash', 'Quay', 'Dockwatch', 'Salt', 'Lantern']
  const names = ['Orin', 'Mara', 'Thorne', 'Vess', 'Bram', 'Sigrid', 'Kaelen', 'Pella', 'Aldous']
  if (kind === 'place') return `The ${kinds[index % kinds.length]} Hollow of ${names[(index + 1) % names.length]}`
  if (kind === 'faction') return `The ${names[index % names.length]} Covenant of Salt`
  return `${names[index % names.length]} ${kinds[index % kinds.length]}`
}

function edge(id: string, src: string, dst: string, type: string, counter = 1): EdgeExport {
  return { id, src, dst, type, counter }
}

export function denseWorld(): WorldExport {
  const entities: EntityExport[] = [
    entity(DENSE_FOCUS_ID, 'character', 'Pike the Rook, Harbormaster of the Ninth Quay', PORTRAIT_REF),
  ]
  for (let i = 1; i <= 25; i += 1) {
    const id = `N${String(i).padStart(2, '0')}`
    entities.push(entity(id, KINDS[i % KINDS.length]!, nameFor(id, KINDS[i % KINDS.length]!, i)))
  }
  // Not neighbors: no edge to the focus — must never appear in the web.
  entities.push(entity('X01', 'faction', 'The Quiet Scholarium'))
  entities.push(entity('X02', 'character', 'Warden Ilyra of the Outer Reach'))
  entities.push(entity('X03', 'place', 'The Verge of Broken Tides'))

  const edges: EdgeExport[] = [
    // Outgoing (focus → neighbor).
    edge('e01', DENSE_FOCUS_ID, 'N01', 'employs'),
    edge('e02', DENSE_FOCUS_ID, 'N02', 'bases_at'),
    edge('e03', DENSE_FOCUS_ID, 'N03', 'controls', 3),
    edge('e04', DENSE_FOCUS_ID, 'N04', 'relationship'),
    // Incoming (neighbor → focus).
    edge('e05', 'N05', DENSE_FOCUS_ID, 'loyalty', 7),
    edge('e06', 'N06', DENSE_FOCUS_ID, 'debt', 2),
    edge('e07', 'N07', DENSE_FOCUS_ID, 'ally_of', 2),
    edge('e08', 'N08', DENSE_FOCUS_ID, 'enemy_of', 1),
    edge('e09', 'N09', DENSE_FOCUS_ID, 'relationship'),
    // Reciprocal pair, same type.
    edge('e10', DENSE_FOCUS_ID, 'N10', 'ally_of', 1),
    edge('e11', 'N10', DENSE_FOCUS_ID, 'ally_of', 1),
    // Reciprocal pair, same type (protects).
    edge('e12', DENSE_FOCUS_ID, 'N11', 'protects', 1),
    edge('e13', 'N11', DENSE_FOCUS_ID, 'protects', 1),
    // Parallel edges, same pair + same direction, different types.
    edge('e14', DENSE_FOCUS_ID, 'N12', 'debt', 1),
    edge('e15', DENSE_FOCUS_ID, 'N12', 'grudge', 1),
    // Parallel edges, same pair + same direction + same type (duplicates).
    edge('e16', DENSE_FOCUS_ID, 'N13', 'relationship'),
    edge('e17', DENSE_FOCUS_ID, 'N13', 'relationship'),
    // The remaining vocabulary members (dense labels; every type used).
    edge('e18', DENSE_FOCUS_ID, 'N14', 'hails_from'),
    edge('e19', DENSE_FOCUS_ID, 'N15', 'member_of'),
    edge('e20', 'N16', DENSE_FOCUS_ID, 'member_of'),
    edge('e21', DENSE_FOCUS_ID, 'N17', 'worships', 1),
    edge('e22', 'N17', DENSE_FOCUS_ID, 'loyalty', 4),
    edge('e23', DENSE_FOCUS_ID, 'N18', 'located_in'),
    edge('e24', DENSE_FOCUS_ID, 'N19', 'kin_of'),
    edge('e25', 'N20', DENSE_FOCUS_ID, 'rival_of'),
    edge('e26', DENSE_FOCUS_ID, 'N21', 'relationship'),
    edge('e27', DENSE_FOCUS_ID, 'N22', 'relationship'),
    edge('e28', 'N23', DENSE_FOCUS_ID, 'relationship'),
    // Eligible but TRUNCATED away (drop in world order by the cap).
    edge('e29', 'N24', DENSE_FOCUS_ID, 'relationship'),
    edge('e30', DENSE_FOCUS_ID, 'N25', 'employs'),
    // Never in the web: both endpoints outside the neighbor set.
    edge('e31', 'X01', 'X02', 'relationship'),
    edge('e32', 'X02', 'X03', 'ally_of', 2),
  ]

  return {
    campaign: {
      id: DENSE_CAMPAIGN_ID,
      title: 'The Drowned Harbor (dense fixture)',
      theme: 'sunless harbor dread',
      description: '',
      custom_lore: '',
      is_generic: false,
      created_at: '2026-09-19T00:00:00Z',
    },
    revision: { id: '01JDENSEFIXTURE00000000000', created_at: '2026-09-19T00:01:00Z' },
    entities,
    edges,
  }
}