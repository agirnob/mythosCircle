import { describe, expect, it } from 'vitest'

import type { CharacterSheet } from '../api/characters'
import {
  damageViolation,
  recordViolations,
  relationViolations,
  statBlockViolations,
  submissionViolations,
} from './characterValidation'

/** A complete, valid sheet: everything the canonical gate demands. */
function validSheet(): CharacterSheet {
  return {
    key: 'staged-1',
    record: {
      name: 'Seraphine',
      role: 'NPC',
      level_cr: 'level 5',
      race_type: 'Human',
      class_profession: 'Fighter',
      alignment: 'LG',
      personality: 'cold, exacting',
      secret: 'owes the Guild a debt',
      rumor: 'seen at the docks at night',
      party_hook: 'hires the party to guard a shipment',
      appearance: 'sharp features, a brand on the jaw',
      background: 'born in the Drowned Quarter',
      goals: 'repay the debt and leave',
      relationships: 'Mira Vane watches her back',
      voice_style: 'clipped, low',
      catchphrases: 'Everything has a price.',
      world_integration: {
        reputation: 'feared',
        factions: 'the Guild',
        current_location: 'the Gilded Bar',
        reaction_matrix: 'C0: Neutral',
        on_defeat: 'pleads for a chance to repay',
      },
      stat_block: {
        identity: { role: 'NPC', race: 'Human', level: 5, class: 'Fighter', alignment: 'LG' },
        attributes: { str: 14, dex: 12, con: 14, int: 10, wis: 10, cha: 8 },
        combat: { ac: 16, hp: 44 },
      },
    },
    relations: [{ type: 'debt', counter: 50, target_id: '01H8XM4K2JZYXWVUTSRQPONMLK' }],
  }
}

/** The mirror functions take `unknown`; tests mutate typed sheets. The
 * double cast avoids TS2352 (a typed object has no index signature). */
function rec(value: object): Record<string, unknown> {
  return value as unknown as Record<string, unknown>
}

function submission(sheets: CharacterSheet[]): Record<string, unknown> {
  return { campaign_id: 'C1', characters: sheets }
}

describe('submissionViolations — the canonical add_character mirror', () => {
  it('accepts a complete single sheet with a Tier-1 relation', () => {
    expect(submissionViolations(submission([validSheet()]))).toEqual([])
  })

  it('accepts a Boss-tier sheet with a complete boss section and CR', () => {
    const sheet = validSheet()
    sheet.record.role = 'Monster'
    sheet.record.level_cr = 'CR 4'
    rec(sheet.record.stat_block)['identity'] = {
      role: 'Monster',
      race: 'Grick',
      cr: 4,
    }
    sheet.record.boss = {
      lair_actions: 'Cracks the floor beneath one target.',
      legendary_actions: 'Sweeping tail attack.',
      immunities: 'poison',
      vulnerabilities: 'radiant',
    }
    expect(submissionViolations(submission([sheet]))).toEqual([])
  })

  it('rejects blank required record fields with the exact schema paths — DIRECT_INCOMPLETE', () => {
    const sheet = validSheet()
    rec(sheet.record)['personality'] = '   '
    rec(sheet.record)['level_cr'] = ''
    const violations = submissionViolations(submission([sheet]))
    expect(violations).toContain('characters[0].record.personality must be a non-blank string')
    expect(violations).toContain('characters[0].record.level_cr must be a non-blank string')
  })

  it('rejects unknown record keys — DIRECT_UNKNOWN_KEY', () => {
    const sheet = validSheet()
    rec(sheet.record)['invented'] = 'nope'
    const violations = submissionViolations(submission([sheet]))
    expect(violations[0]).toContain('characters[0].record.has unknown key(s)')
    expect(violations[0]).toContain('invented')
  })

  it('enforces the boss-conditionality rule (spec-3.3)', () => {
    const npcWithBoss = validSheet()
    npcWithBoss.record.boss = {
      lair_actions: 'x',
      legendary_actions: 'x',
      immunities: 'x',
      vulnerabilities: 'x',
    }
    expect(submissionViolations(submission([npcWithBoss]))).toContain(
      'characters[0].record.boss section is only allowed for BBEG or Monster roles',
    )

    const bossWithoutSection = validSheet()
    bossWithoutSection.record.role = 'BBEG'
    bossWithoutSection.record.level_cr = 'level 18'
    rec(bossWithoutSection.record.stat_block)['identity'] = {
      role: 'BBEG',
      race: 'Human',
      level: 18,
    }
    const violations = submissionViolations(submission([bossWithoutSection]))
    expect(violations).toContain('characters[0].record.boss must be an object for BBEG or Monster roles')
  })

  it('requires the world-integration block fields — DIRECT_INCOMPLETE', () => {
    const sheet = validSheet()
    sheet.record.world_integration = {
      reputation: 'feared',
      factions: '',
      current_location: '',
      reaction_matrix: '',
      on_defeat: '',
    }
    const violations = submissionViolations(submission([sheet]))
    expect(violations).toContain(
      'characters[0].record.world_integration.factions must be a non-blank string',
    )
  })

  it('rejects a payload without characters or with unknown payload keys', () => {
    expect(submissionViolations({})).toContain(
      "add_character payload must carry a non-empty 'characters' list",
    )
    expect(submissionViolations({ characters: [], extra: 1 })).toContain(
      "add_character payload has unknown key(s): ['extra']",
    )
  })

  it('enforces the batch cap (MAX_CHARACTERS = 100)', () => {
    const many = Array.from({ length: 101 }, () => validSheet())
    expect(submissionViolations(submission(many))[0]).toContain('exceeds 100 characters')
  })

  it('rejects a sheet without a record, or with unknown sheet keys', () => {
    const sheet: Record<string, unknown> = { key: 'staged-1', stray: true }
    expect(submissionViolations(submission([sheet as unknown as CharacterSheet]))).toContain(
      'characters[0].record is required',
    )
    expect(submissionViolations(submission([sheet as unknown as CharacterSheet]))[0]).toContain(
      'has unknown key(s)',
    )
  })
})

describe('statBlockViolations — structure, dice pattern, vocabularies', () => {
  it('requires identity, attributes and combat — DIRECT_INVALID_STAT', () => {
    const violations = statBlockViolations({})
    expect(violations).toContain('stat_block.identity section missing or not an object')
    expect(violations).toContain('stat_block.attributes section missing or not an object')
    expect(violations).toContain('stat_block.combat section missing or not an object')
  })

  it('enforces the level/cr role rule (Monster → cr, never level)', () => {
    const block = {
      identity: { role: 'Monster', race: 'Grick', level: 5 },
      attributes: { str: 14, dex: 12, con: 14, int: 10, wis: 10, cha: 8 },
      combat: { ac: 16, hp: 44 },
    }
    const violations = statBlockViolations(block)
    expect(violations).toContain(
      'stat_block.identity.level is not allowed for role Monster (use cr)',
    )
    expect(violations.some((v) => v.includes('identity.cr must be an integer'))).toBe(true)
  })

  it('enforces SRD race vocabulary for non-Monster roles only', () => {
    const identity: Record<string, unknown> = { role: 'NPC', race: 'Dragon', level: 5 }
    const block = {
      identity,
      attributes: { str: 14, dex: 12, con: 14, int: 10, wis: 10, cha: 8 },
      combat: { ac: 16, hp: 44 },
    }
    expect(statBlockViolations(block).some((v) => v.includes('identity.race must be one of the SRD races'))).toBe(true)
    identity.role = 'Monster'
    identity.cr = 4
    delete identity['level']
    expect(statBlockViolations(block)).toEqual([])
  })

  it('rejects an action damage string failing the dice pattern with the pattern named', () => {
    const block = {
      identity: { role: 'NPC', race: 'Human', level: 5 },
      attributes: { str: 14, dex: 12, con: 14, int: 10, wis: 10, cha: 8 },
      combat: { ac: 16, hp: 44 },
      actions: [{ name: 'Longsword', description: 'A clean cut.', damage: '1d6x+2' }],
    }
    expect(statBlockViolations(block)[0]).toContain(
      "stat_block.actions[0].damage '1d6x+2' must match the dice pattern",
    )
  })

  it('accepts an actions PARTS damage list whose dice are canonical', () => {
    const block = {
      identity: { role: 'NPC', race: 'Human', level: 5 },
      attributes: { str: 14, dex: 12, con: 14, int: 10, wis: 10, cha: 8 },
      combat: { ac: 16, hp: 44 },
      actions: [
        {
          name: 'Longsword',
          description: 'A clean cut.',
          damage: [{ dice: '1d8', count: 1, sides: 8, bonus: 2, average: 7, type: 'slashing' }],
        },
      ],
    }
    expect(statBlockViolations(block)).toEqual([])
  })

  it('rejects a skill name outside the SRD skill list', () => {
    const block = {
      identity: { role: 'NPC', race: 'Human', level: 5 },
      attributes: { str: 14, dex: 12, con: 14, int: 10, wis: 10, cha: 8 },
      combat: { ac: 16, hp: 44 },
      skills: [{ name: 'Nunchaku', description: 'whips around corners' }],
    }
    expect(statBlockViolations(block)[0]).toContain('not in the SRD list')
  })

  it('flags attribute and combat hard-cap breaches', () => {
    const block = {
      identity: { role: 'NPC', race: 'Human', level: 5 },
      attributes: { str: 99, dex: 12, con: 14, int: 10, wis: 10, cha: 8 },
      combat: { ac: 0, hp: 44 },
    }
    const violations = statBlockViolations(block)
    expect(violations).toContain('stat_block.attributes.str must be an integer in [1, 30]')
    expect(violations).toContain('stat_block.combat.ac must be a positive integer')
  })

  it('mirrors the damageViolation primitive for both allowed shapes', () => {
    expect(damageViolation('2d6+2', 'damage')).toBeNull()
    expect(damageViolation('2d6', 'damage')).toBeNull()
    expect(damageViolation('2d6 - 1', 'damage')).toContain('dice pattern')
    expect(damageViolation([{ dice: '1d8' }], 'damage')).toBeNull()
    expect(damageViolation([{ dice: '  ' }], 'damage')).toContain("non-blank 'dice'")
    expect(damageViolation([], 'damage')).toContain('empty list')
  })

  it('rejects a record whose role disagrees with the block identity role — DIRECT_INVALID_RECORD', () => {
    const sheet = validSheet()
    rec(sheet.record.stat_block)['identity'] = {
      role: 'BBEG',
      race: 'Human',
      level: 5,
    }
    expect(submissionViolations(submission([sheet]))).toContain(
      'characters[0].record.stat_block.identity.role BBEG must match the record role NPC',
    )
  })
})

describe('relationViolations — the three-tier contract on the DIRECT path', () => {
  it('accepts exactly one tier key: target_id and target_key', () => {
    expect(relationViolations({ type: 'debt', target_id: '01H8XM4K2JZYXWVUTSRQPONMLK' }, 'r', new Set())).toEqual([])
    expect(relationViolations({ type: 'relationship', target_key: 'staged-2' }, 'r', new Set(['staged-2']))).toEqual([])
  })

  it('bans target_name on the fully-authored path', () => {
    const violations = relationViolations({ type: 'kin_of', target_name: 'The Shadow Queen' }, 'r', new Set())
    expect(violations).toContain(
      'r.target_name is not allowed on a fully-authored character (pick a committed entity or stage the target in this batch)',
    )
  })

  it('requires exactly one target tier and a closed edge type', () => {
    expect(relationViolations({ type: 'debt' }, 'r', new Set())).toContain(
      'r must carry exactly one of target_id/target_key/target_name',
    )
    expect(relationViolations({ type: 'watches', target_id: 'x' }, 'r', new Set())[0]).toContain(
      'r.type must be one of',
    )
    expect(relationViolations({ type: 'debt', counter: '5', target_id: 'x' }, 'r', new Set())).toContain(
      'r.counter must be an integer',
    )
  })

  it('target_key must name ANOTHER staged sheet (self-reference rejected)', () => {
    const violations = relationViolations(
      { type: 'relationship', target_key: 'staged-1' },
      'r',
      new Set(['staged-1']),
    )
    // sheetViolations builds the "other keys" set, so the self-key rule
    // is exercised there; the primitive only checks membership.
    expect(violations).toEqual([])
    expect(
      submissionViolations(submission([{ ...validSheet(), relations: [{ type: 'relationship', target_key: 'staged-1' }] }])),
    ).toContain("characters[0].relations[0].target_key 'staged-1' must name another staged sheet's key in this payload")
  })

  it('Tier-2 target_key must resolve to a sibling sheet in the same payload', () => {
    const sheet = validSheet()
    sheet.relations = [{ type: 'relationship', target_key: 'staged-2' }]
    expect(submissionViolations(submission([sheet]))).toContain(
      "characters[0].relations[0].target_key 'staged-2' must name another staged sheet's key in this payload",
    )
    const sibling = validSheet()
    sibling.key = 'staged-2'
    expect(submissionViolations(submission([sheet, sibling]))).toEqual([])
  })
})

describe('recordViolations — completeness mirror', () => {
  it('flags a missing stat_block and an out-of-vocabulary role', () => {
    const record = rec(validSheet().record)
    delete record['stat_block']
    record['role'] = 'Hero'
    const violations = recordViolations(record)
    expect(violations).toContain('stat_block section missing')
    expect(violations.some((v) => v.startsWith('role must be one of'))).toBe(true)
  })
})