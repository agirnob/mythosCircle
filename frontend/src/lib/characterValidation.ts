/**
 * The client-side MIRROR of the add_character canonical JSON Schema
 * (the F3 Frontend Gate): the same shape checks the backend's
 * store/direct.py validators run — completeness, stat-block structure
 * incl. the dice pattern, role consistency, the closed key sets, and
 * the path-2 `target_name` ban. It mirrors, it does not replace: the
 * synchronous enqueue gate (POST /api/characters, 422) and the worker
 * backstop ALWAYS re-validate, so a drift here can never commit.
 *
 * Violation strings follow the backend's schema paths
 * (`characters[i].record....`) so a server 422 and the live form read
 * the same way. The power-band audit (combat DPR vs level/CR) and the
 * SRD spell-vocabulary membership are intentionally NOT mirrored — the
 * backend owns them (an over-powered block is explicitly acceptable,
 * DIRECT_OVER_POWERED) and a server 422 still surfaces them inline.
 */

import {
  ALIGNMENTS,
  DICE_PATTERN,
  EDGE_TYPES,
  RECORD_KEYS,
  ROLES,
  SRD_CLASSES,
  SRD_RACES,
  SRD_SKILLS,
  type CharacterRecord,
  type CharacterSheet,
  type CharacterSubmission,
  type DeclaredRelation,
  type StatBlock,
} from '../api/characters'

/** A non-blank string check, backend wording. */
function blankViolation(value: unknown, where: string): string | null {
  return typeof value === 'string' && value.trim() !== '' ? null : `${where} must be a non-blank string`
}

function intViolation(value: unknown, where: string, low: number, high: number): string | null {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < low || value > high) {
    return `${where} must be an integer in [${low}, ${high}]`
  }
  return null
}

/** Backend-style sorted-key list: `['a', 'b']`. */
function keyList(keys: string[]): string {
  return `[${keys.sort().map((key) => `'${key}'`).join(', ')}]`
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** The CR fraction pattern (store/direct.py `\d+(/\d+)?`). */
const CR_FRACTION_RE = /^\d+(?:\/\d+)?$/

/** A canonical dice-string damage slot — `^\d+d\d+([+-]\d+)?$`. */
export function isDiceString(value: unknown): boolean {
  return typeof value === 'string' && DICE_PATTERN.test(value.trim())
}

/** A stat-block damage slot the gate accepts: one canonical dice string
 * or a parts array whose every part carries a non-blank dice string
 * (the StatBlockEditor's emit shape — the backend's subset validator
 * allows both). */
export function damageViolation(damage: unknown, where: string): string | null {
  if (typeof damage === 'string') {
    return isDiceString(damage)
      ? null
      : `${where} '${damage}' must match the dice pattern ^\\d+d\\d+([+-]\\d+)?$ (e.g. "2d6+2")`
  }
  if (Array.isArray(damage)) {
    for (const [index, part] of damage.entries()) {
      if (!isObject(part) || typeof part['dice'] !== 'string' || !part['dice'].trim()) {
        return `${where}[${index}] must carry a non-blank 'dice' string`
      }
    }
    return damage.length === 0 ? `${where} must not be an empty list` : null
  }
  return damage === undefined || damage === null
    ? null
    : `${where} must be a dice string ('2d6+2') or a list of damage parts`
}

/** The AR19 + AR24 completeness of one record (the mirror of
 * store/candidates.payload_section_violations + the closed key set). */
export function recordViolations(record: unknown): string[] {
  if (!isObject(record)) return ['record must be an object']
  const violations: string[] = []
  const unknown = Object.keys(record).filter((key) => !RECORD_KEYS.has(key))
  if (unknown.length > 0) {
    violations.push(`has unknown key(s): ${keyList(unknown)}`)
  }
  for (const field of ['name', 'personality', 'secret', 'rumor', 'party_hook']) {
    const violation = blankViolation(record[field], field)
    if (violation) violations.push(violation)
  }
  const role = record['role']
  if (typeof role !== 'string' || !ROLES.includes(role as (typeof ROLES)[number])) {
    violations.push(`role must be one of ${ROLES.join(', ')}`)
  }
  for (const field of ['level_cr', 'race_type', 'class_profession', 'alignment']) {
    const violation = blankViolation(record[field], field)
    if (violation) violations.push(violation)
  }
  for (const field of [
    'appearance',
    'background',
    'goals',
    'relationships',
    'voice_style',
    'catchphrases',
  ]) {
    const violation = blankViolation(record[field], field)
    if (violation) violations.push(violation)
  }
  const world = record['world_integration']
  if (!isObject(world)) {
    violations.push('world_integration must be an object')
  } else {
    for (const field of ['reputation', 'factions', 'current_location', 'reaction_matrix', 'on_defeat']) {
      const violation = blankViolation(world[field], `world_integration.${field}`)
      if (violation) violations.push(violation)
    }
  }
  const boss = record['boss']
  const bossRole = typeof role === 'string' && (role === 'BBEG' || role === 'Monster')
  if (bossRole) {
    if (!isObject(boss)) {
      violations.push('boss must be an object for BBEG or Monster roles')
    } else {
      for (const field of ['lair_actions', 'legendary_actions', 'immunities', 'vulnerabilities']) {
        const violation = blankViolation(boss[field], `boss.${field}`)
        if (violation) violations.push(violation)
      }
    }
  } else if (boss !== undefined && boss !== null) {
    violations.push('boss section is only allowed for BBEG or Monster roles')
  }
  const block = record['stat_block']
  if (block === undefined || block === null) {
    violations.push('stat_block section missing')
  } else {
    violations.push(...statBlockViolations(block))
    const blockIdentity = isObject(block) ? block['identity'] : null
    const blockRole = isObject(blockIdentity) ? blockIdentity['role'] : null
    if (
      typeof role === 'string' &&
      typeof blockRole === 'string' &&
      role.trim().toLowerCase() !== blockRole.trim().toLowerCase()
    ) {
      violations.push(
        `stat_block.identity.role ${blockRole!} must match the record role ${role}`,
      )
    }
  }
  return violations
}

/** The canonical stat block shape (mirror of store/direct.py
 * stat_block_violations + knowledge.validate_stat_block): the three
 * required subsections, role-limited level/CR, SRD vocabularies, hard
 * caps, and the name/description row contracts. The band audit and the
 * spell list membership are backend-owned. */
export function statBlockViolations(block: unknown): string[] {
  if (!isObject(block)) return ['stat_block must be an object']
  const violations: string[] = []

  const identity = block['identity']
  if (!isObject(identity)) {
    violations.push('stat_block.identity section missing or not an object')
  } else {
    const role = identity['role']
    if (typeof role !== 'string' || !ROLES.includes(role as (typeof ROLES)[number])) {
      violations.push(`stat_block.identity.role must be one of ${ROLES.join(', ')}`)
    }
    const isMonster = role === 'Monster'
    if (isMonster) {
      if (identity['level'] !== undefined) {
        violations.push('stat_block.identity.level is not allowed for role Monster (use cr)')
      }
      const cr = identity['cr']
      const crOk =
        (typeof cr === 'number' && Number.isInteger(cr) && cr >= 0 && cr <= 30) ||
        (typeof cr === 'string' && CR_FRACTION_RE.test(cr.trim()))
      if (!crOk) violations.push('stat_block.identity.cr must be an integer in [0, 30] or a fraction (1/8, 1/4, 1/2)')
    } else {
      if (identity['cr'] !== undefined) {
        violations.push('stat_block.identity.cr is not allowed for role NPC/BBEG (use level)')
      }
      const violation = intViolation(identity['level'], 'stat_block.identity.level', 1, 20)
      if (violation) violations.push(violation)
    }
    const race = identity['race']
    if (typeof race !== 'string' || !race.trim()) {
      violations.push('stat_block.identity.race must be a non-blank string')
    } else if (!isMonster && !SRD_RACES.includes(race)) {
      violations.push(`stat_block.identity.race must be one of the SRD races: ${SRD_RACES.join(', ')}`)
    }
    const klass = identity['class']
    if (klass !== undefined && (typeof klass !== 'string' || !SRD_CLASSES.includes(klass))) {
      violations.push(`stat_block.identity.class must be one of ${SRD_CLASSES.join(', ')}`)
    }
    const alignment = identity['alignment']
    if (alignment !== undefined && (typeof alignment !== 'string' || !ALIGNMENTS.includes(alignment))) {
      violations.push(`stat_block.identity.alignment must be one of ${ALIGNMENTS.join(', ')}`)
    }
  }

  const attributes = block['attributes']
  if (!isObject(attributes)) {
    violations.push('stat_block.attributes section missing or not an object')
  } else {
    for (const score of ['str', 'dex', 'con', 'int', 'wis', 'cha']) {
      const violation = intViolation(attributes[score], `stat_block.attributes.${score}`, 1, 30)
      if (violation) violations.push(violation)
    }
  }

  const combat = block['combat']
  if (!isObject(combat)) {
    violations.push('stat_block.combat section missing or not an object')
  } else {
    for (const field of ['ac', 'hp']) {
      const value = combat[field]
      if (typeof value !== 'number' || !Number.isInteger(value) || value < 1) {
        violations.push(`stat_block.combat.${field} must be a positive integer`)
      }
    }
    if (combat['hit_dice'] !== undefined) {
      const violation = blankViolation(combat['hit_dice'], 'stat_block.combat.hit_dice')
      if (violation) violations.push(violation)
    }
  }

  for (const section of ['skills', 'actions', 'traits'] as const) {
    const list = block[section]
    if (list === undefined) continue
    if (!Array.isArray(list)) {
      violations.push(`stat_block.${section} must be a list`)
      continue
    }
    for (const [index, entry] of list.entries()) {
      if (!isObject(entry)) {
        violations.push(`stat_block.${section}[${index}] must be an object`)
        continue
      }
      for (const field of ['name', 'description'] as const) {
        const violation = blankViolation(entry[field], `stat_block.${section}[${index}].${field}`)
        if (violation) violations.push(violation)
      }
      if (section === 'skills') {
        const name = entry['name']
        if (typeof name === 'string' && !SRD_SKILLS.includes(name)) {
          violations.push(`stat_block.skills[${index}] name ${name!} not in the SRD list`)
        }
      }
      if (section === 'actions' && entry['damage'] !== undefined) {
        const violation = damageViolation(entry['damage'], `stat_block.actions[${index}].damage`)
        if (violation) violations.push(violation)
      }
    }
  }

  const spells = block['spells']
  if (spells !== undefined) {
    if (!Array.isArray(spells)) {
      violations.push('stat_block.spells must be a list')
    } else {
      for (const [index, spell] of spells.entries()) {
        const violation = blankViolation(spell, `stat_block.spells[${index}]`)
        if (violation) violations.push(violation)
      }
    }
  }
  return violations
}

/** One declared relation on the DIRECT path: mirror of
 * store/direct.py relation_violations(allow_target_name=False) — the
 * closed type set, the optional integer counter, EXACTLY one target
 * key, and the tier-2 sibling-key resolution rule. */
export function relationViolations(raw: unknown, where: string, knownStagedKeys: ReadonlySet<string>): string[] {
  if (!isObject(raw)) return [`${where} must be an object`]
  const violations: string[] = []
  const unknown = Object.keys(raw).filter((key) => !['type', 'counter', 'target_id', 'target_key', 'target_name'].includes(key))
  if (unknown.length > 0) {
    violations.push(`${where} has unknown key(s): ${unknown.sort()}`)
  }
  const type = raw['type']
  if (typeof type !== 'string' || !EDGE_TYPES.includes(type)) {
    violations.push(`${where}.type must be one of ${EDGE_TYPES.join(', ')}`)
  }
  if (raw['counter'] !== undefined && (typeof raw['counter'] !== 'number' || !Number.isInteger(raw['counter']))) {
    violations.push(`${where}.counter must be an integer`)
  }
  const tiers = ['target_id', 'target_key', 'target_name'].filter((tier) => raw[tier] !== undefined)
  if (tiers.length !== 1) {
    violations.push(`${where} must carry exactly one of target_id/target_key/target_name`)
    return violations
  }
  const tier = tiers[0]!
  const value = raw[tier]
  if (typeof value !== 'string' || !value.trim()) {
    violations.push(`${where}.${tier} must be a non-blank string`)
  }
  if (tier === 'target_name') {
    violations.push(
      `${where}.target_name is not allowed on a fully-authored character ` +
        '(pick a committed entity or stage the target in this batch)',
    )
  }
  if (tier === 'target_key' && (typeof value !== 'string' || !knownStagedKeys.has(value))) {
    violations.push(
      `${where}.target_key '${value}' must name another staged sheet's key in this payload`,
    )
  }
  return violations
}

function sheetViolations(sheet: unknown, index: number, stagedKeys: ReadonlySet<string>): string[] {
  const where = `characters[${index}]`
  if (!isObject(sheet)) return [`${where} must be an object`]
  const violations: string[] = []
  const unknown = Object.keys(sheet).filter((key) => !['key', 'record', 'relations'].includes(key))
  if (unknown.length > 0) {
    violations.push(`${where} has unknown key(s): ${keyList(unknown)}`)
  }
  const key = sheet['key']
  if (key !== null && key !== undefined && (typeof key !== 'string' || !key.trim())) {
    violations.push(`${where}.key must be a non-blank string`)
  }
  const record = sheet['record']
  if (record === undefined || record === null) {
    violations.push(`${where}.record is required`)
  } else if (!isObject(record)) {
    violations.push(`${where}.record must be an object`)
  } else {
    violations.push(...recordViolations(record).map((violation) => `${where}.record.${violation}`))
  }
  const relations = sheet['relations']
  if (relations === undefined || relations === null) return violations
  if (!Array.isArray(relations)) {
    violations.push(`${where}.relations must be a list`)
    return violations
  }
  // The tier-2 sibling rule excludes the sheet's OWN key (backend:
  // ``target_key == key`` is a violation — a character cannot target
  // itself through the staged set).
  const otherKeys = new Set(stagedKeys)
  if (typeof key === 'string' && key.trim()) otherKeys.delete(key.trim())
  for (const [rIndex, raw] of relations.entries()) {
    violations.push(...relationViolations(raw, `${where}.relations[${rIndex}]`, otherKeys))
  }
  return violations
}

/** The FULL mirror of validate_add_character_payload: payload shape,
 * non-empty bounded batch, per-sheet completeness, and the tier-2
 * sibling-key rule. Returns backend-style schema paths. */
export function submissionViolations(payload: unknown): string[] {
  if (!isObject(payload)) return ['add_character payload must be a JSON object']
  const unknown = Object.keys(payload).filter((key) => !['campaign_id', 'characters'].includes(key))
  if (unknown.length > 0) {
    return [`add_character payload has unknown key(s): ${keyList(unknown)}`]
  }
  const characters = payload['characters']
  if (!Array.isArray(characters) || characters.length === 0) {
    return ["add_character payload must carry a non-empty 'characters' list"]
  }
  if (characters.length > 100) {
    return ['add_character payload exceeds 100 characters']
  }
  const stagedKeys = new Set<string>()
  for (const sheet of characters) {
    if (isObject(sheet) && typeof sheet['key'] === 'string' && sheet['key'].trim()) {
      stagedKeys.add(sheet['key'])
    }
  }
  const violations: string[] = []
  for (const [index, sheet] of characters.entries()) {
    violations.push(...sheetViolations(sheet, index, stagedKeys))
  }
  return violations
}

/** Build-time helper: the staged keys of a submission's sheets (every
 * non-blank `key`), for the tier-2 sibling rule. */
export function stagedKeysOf(sheets: ReadonlyArray<CharacterSheet>): Set<string> {
  const keys = new Set<string>()
  for (const sheet of sheets) {
    if (typeof sheet.key === 'string' && sheet.key.trim()) keys.add(sheet.key.trim())
  }
  return keys
}

/** Per-sheet violations for live rendering (index-relative). */
export function sheetViolationsFor(sheet: CharacterSheet, index: number, stagedKeys: ReadonlySet<string>): string[] {
  return sheetViolations(sheet, index, stagedKeys)
}

/** True when the whole submission passes the mirror gate. */
export function submissionIsValid(payload: unknown): boolean {
  return submissionViolations(payload).length === 0
}

// Type-only re-exports so consumers build against the canonical shapes.
export type { CharacterRecord, CharacterSheet, CharacterSubmission, DeclaredRelation, StatBlock }