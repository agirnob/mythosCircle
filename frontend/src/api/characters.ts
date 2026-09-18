/**
 * The fully-authored character path's LOCAL types (spec: hybrid
 * authorship — the follow-up Add-Character form UI).
 *
 * This is the frontend half of the canonical schema: the committed
 * character-draft mirror lives in `characterSchema.ts` (same contract,
 * written by the backend sprint) and is re-exported here as THE import
 * surface for the form, the mirror validator, and the submission — the
 * backend's store/direct.py + store/jobs.py gate ALWAYS re-validates
 * (F3), so a drift between the mirrors can never commit.
 *
 * This module also carries the closed-vocabulary mirrors the picker and
 * the client-side mirror validator need (edge types + the backend's
 * kind-compatibility table, roles, SRD vocabularies) — one definition
 * each, mirroring backend/app/store/commit.py and
 * backend/app/pipeline/knowledge.py.
 */

export * from './characterSchema'

/** The closed edge vocabulary (store/commit.py EDGE_TYPES). */
export const EDGE_TYPES: readonly string[] = [
  'relationship',
  'debt',
  'grudge',
  'loyalty',
  'member_of',
  'located_in',
  'rival_of',
  'kin_of',
  'ally_of',
  'enemy_of',
  'bases_at',
  'controls',
  'employs',
  'worships',
  'hails_from',
  'protects',
]

/** The closed role set (store/candidates.py ROLES). */
export const ROLES = ['NPC', 'BBEG', 'Monster'] as const

/** SRD 5.1 races (knowledge.py RACES — the 9 PHB races). */
export const SRD_RACES: readonly string[] = [
  'Dragonborn',
  'Dwarf',
  'Elf',
  'Gnome',
  'Half-Elf',
  'Half-Orc',
  'Halfling',
  'Human',
  'Tiefling',
]

/** SRD 5.1 base classes (knowledge.py CLASSES — the 12 PHB classes). */
export const SRD_CLASSES: readonly string[] = [
  'Barbarian',
  'Bard',
  'Cleric',
  'Druid',
  'Fighter',
  'Monk',
  'Paladin',
  'Ranger',
  'Rogue',
  'Sorcerer',
  'Warlock',
  'Wizard',
]

/** SRD 5.1 alignments (knowledge.py ALIGNMENTS). */
export const ALIGNMENTS: readonly string[] = ['LG', 'NG', 'CG', 'LN', 'N', 'CN', 'LE', 'NE', 'CE', 'unaligned']

/** SRD 5.1 skills (knowledge.py SKILLS — 18). */
export const SRD_SKILLS: readonly string[] = [
  'Acrobatics',
  'Animal Handling',
  'Arcana',
  'Athletics',
  'Deception',
  'History',
  'Insight',
  'Intimidation',
  'Investigation',
  'Medicine',
  'Nature',
  'Perception',
  'Performance',
  'Persuasion',
  'Religion',
  'Sleight of Hand',
  'Stealth',
  'Survival',
]

/**
 * The kind-compatibility table for the closed edge vocabulary
 * (store/commit.py EDGE_KIND_RULES): `src`/`dst` kind sets, absent =
 * any kind. The relation picker filters its Tier-1/Tier-2 target lists
 * with `edgeKindOk` so a `located_in` can never aim at a character —
 * the enqueue gate enforces the same pair (kind breach → 422).
 */
export const EDGE_KIND_RULES: Record<string, { src?: ReadonlySet<string>; dst?: ReadonlySet<string> }> = {
  located_in: { dst: new Set(['place']) },
  member_of: { src: new Set(['character', 'faction']), dst: new Set(['character', 'faction']) },
  loyalty: { src: new Set(['character', 'faction']), dst: new Set(['character', 'faction']) },
  bases_at: { src: new Set(['character', 'faction']), dst: new Set(['place']) },
  hails_from: { src: new Set(['character', 'faction']), dst: new Set(['place']) },
  controls: { src: new Set(['character', 'faction']), dst: new Set(['place', 'faction']) },
  employs: { src: new Set(['character', 'faction']), dst: new Set(['character', 'faction']) },
  worships: { src: new Set(['character', 'faction']), dst: new Set(['character', 'faction']) },
  protects: { src: new Set(['character', 'faction']) },
}

/** Mirror of store/commit.py `edge_kind_ok` — whether an edge of
 * `edgeType` may run from `srcKind` to `dstKind`. */
export function edgeKindOk(edgeType: string, srcKind: string, dstKind: string): boolean {
  const rules = EDGE_KIND_RULES[edgeType]
  if (!rules) return true
  if (rules.src && !rules.src.has(srcKind)) return false
  if (rules.dst && !rules.dst.has(dstKind)) return false
  return true
}

/** Mirror of store/direct.py `normalize_entity_name` — the dedup
 * identity key: casefolded, leading article stripped, whitespace
 * collapsed, trailing punctuation dropped. The Tier-3 exact-match
 * prompt uses it exactly. */
export function normalizeEntityName(name: string): string {
  let normalized = name.toLowerCase().split(/\s+/).join(' ').trim().replace(/[.!?,;:]+$/, '')
  for (const article of ['the ', 'a ', 'an ']) {
    if (normalized.startsWith(article)) {
      normalized = normalized.slice(article.length)
      break
    }
  }
  return normalized
}

/** A committed campaign entity's picker projection (id + name + kind). */
export interface EntityRef {
  id: string
  name: string
  kind: string
}

/** The UI draft of ONE declared-relation row (the three-tier picker's
 * v-model). `tier` selects the target source: 'entity' (Tier 1,
 * committed ULID), 'staged' (Tier 2, a sibling sheet's key), 'name'
 * (Tier 3, freeform — on the direct form this tier only resolves NAMES
 * to a Tier-1 binding; the wire never carries `target_name`). */
export interface RelationDraft {
  type: string
  /** '' = omit; a filled value must be an integer (parsed at build). */
  counter: string
  tier: 'entity' | 'staged' | 'name'
  targetId: string
  targetKey: string
  targetName: string
}

/** The AR24 record key set a fully-authored record may carry (the
 * direct.py RECORD_KEYS mirror: the AR19 core + identity anchor + AR24
 * lore + the section objects). Unknown keys are schema violations. */
export const RECORD_KEYS: ReadonlySet<string> = new Set([
  'name',
  'role',
  'personality',
  'secret',
  'rumor',
  'party_hook',
  'level_cr',
  'race_type',
  'class_profession',
  'alignment',
  'appearance',
  'background',
  'goals',
  'relationships',
  'voice_style',
  'catchphrases',
  'world_integration',
  'stat_block',
  'boss',
])