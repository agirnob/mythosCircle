/**
 * Shared AR24 profile vocabulary + render helpers — ONE home for the
 * sectioned-character constants the world and candidates views carried
 * side by side (epic-3 retro item 2, deferral resolution 2026-09-18).
 *
 * The canonical field sets mirror the backend's AR24 contract
 * (store/candidates.py: IDENTITY_FIELDS / LORE_FIELDS /
 * WORLD_INTEGRATION_FIELDS / BOSS_FIELDS, plus the AR19 core the AR24
 * record carries at top level). `LORE_FIELDS` below is the union a view
 * actually renders: the AR19 core (personality, secret, rumor,
 * party_hook) followed by the AR24 lore sections, in the order the
 * candidates accept screen shows them (WorldView's scalar-fields union
 * dedupes to the same order).
 *
 * Edits to a label or a closed vocabulary land HERE — never in a view
 * copy. The two deliberately divergent spellings stay per-view:
 * WorldView's compact profile editor renders 'Level/CR' / 'Race/Type'
 * (no spaces) while the candidates identity line and re-roll badge use
 * the spaced forms; each view patches those two keys over the base.
 */

/** The AR24 identity-anchor fields (AR19 'name'/'role' stay separate). */
export const IDENTITY_FIELDS = ['level_cr', 'race_type', 'class_profession', 'alignment'] as const

/** The AR19 core narrative fields the AR24 record carries at top level. */
export const CORE_FIELDS = ['personality', 'secret', 'rumor', 'party_hook'] as const

/** The narrative-lore sections a view renders/edits: the AR19 core plus
 * the AR24 lore sections (store/candidates.py LORE_FIELDS) — a superset
 * of either view's old local list, ordered as the accept screen shows. */
export const LORE_FIELDS = [
  'appearance',
  'personality',
  'background',
  'goals',
  'relationships',
  'secret',
  'rumor',
  'party_hook',
  'voice_style',
  'catchphrases',
] as const

/** The conditional boss section's four fields (spec-3.3). */
export const BOSS_FIELDS = [
  'lair_actions',
  'legendary_actions',
  'immunities',
  'vulnerabilities',
] as const

/** The world-integration block fields in contract order (spec-3.3). */
export const WORLD_INTEGRATION_FIELDS = [
  'reputation',
  'factions',
  'current_location',
  'reaction_matrix',
  'on_defeat',
] as const

/** Roles that MUST carry the boss section (spec-3.3). */
export const BOSS_ROLES = new Set(['BBEG', 'Monster'])

/**
 * The closed edge vocabulary (AD-5, 16 members since 2026-09-13) — the
 * add-relation picker. Mirrors app/store/commit.py EDGE_TYPES.
 */
export const EDGE_VOCAB: readonly string[] = [
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

export const EDGE_DIRECTIONS = ['outbound', 'inbound'] as const

/**
 * The AR24 content sections a re-roll may target (mirror of
 * backend store/candidates.py REGEN_SECTIONS) — the Regenerate panel's
 * per-section chips. Ordered for display; 'boss' only applies to
 * BBEG/Monster roles (the backend enforces).
 */
export const REGEN_SECTIONS = [
  'personality',
  'secret',
  'rumor',
  'party_hook',
  'appearance',
  'background',
  'goals',
  'relationships',
  'voice_style',
  'catchphrases',
  'stat_block',
  'world_integration',
  'boss',
] as const

/** Flat entity regeneration contracts — mirrors backend FLAT_REGEN_SECTIONS. */
export const FLAT_REGEN_SECTIONS = {
  place: ['description', 'inhabitants', 'whats_hidden'],
  faction: ['description', 'doctrine', 'assets'],
} as const

export function regenSectionsForKind(kind: string): readonly string[] {
  if (kind === 'place' || kind === 'faction') return FLAT_REGEN_SECTIONS[kind]
  return REGEN_SECTIONS
}

/**
 * Frontend mirror of 2.6's `_edge_label` (AD-23): neutral types render
 * bare; debt/grudge/loyalty/ally/enemy/controls/worships/protects carry
 * the counter.
 */
export const COUNTER_TYPES: ReadonlySet<string> = new Set([
  'debt',
  'grudge',
  'loyalty',
  'ally_of',
  'enemy_of',
  'controls',
  'worships',
  'protects',
])

/** The counter-carrying label of one edge (`debt(3)`), bare otherwise. */
export function edgeLabel(type: string, counter: number): string {
  return COUNTER_TYPES.has(type) ? `${type}(${counter})` : type
}

/** Non-blank string presence check — 'a present AR24 value that
 * renders'. Returns the string when non-blank, null otherwise. */
export function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/**
 * The canonical AR24 label map (spaced forms). WorldView patches its two
 * compact spellings ('Level/CR', 'Race/Type') plus the boss block's
 * label over this base; the candidates view uses it as-is.
 */
export const FIELD_LABELS: Record<string, string> = {
  text: 'Text',
  name: 'Name',
  description: 'Description',
  inhabitants: 'Inhabitants',
  whats_hidden: "What's hidden",
  doctrine: 'Doctrine',
  assets: 'Assets',
  role: 'Role',
  level_cr: 'Level / CR',
  race_type: 'Race / Type',
  class_profession: 'Class / Profession',
  alignment: 'Alignment',
  personality: 'Personality',
  secret: 'Secret',
  rumor: 'Rumor',
  party_hook: 'Party hook',
  appearance: 'Appearance',
  background: 'Background',
  goals: 'Goals',
  relationships: 'Relationships',
  voice_style: 'Voice style',
  catchphrases: 'Catchphrases',
  lair_actions: 'Lair actions',
  legendary_actions: 'Legendary actions',
  immunities: 'Immunities',
  vulnerabilities: 'Vulnerabilities',
  reputation: 'Reputation',
  factions: 'Factions',
  current_location: 'Current location',
  reaction_matrix: 'Reaction matrix',
  on_defeat: 'On defeat',
  stat_block: 'Stat block',
  world_integration: 'World integration',
}
