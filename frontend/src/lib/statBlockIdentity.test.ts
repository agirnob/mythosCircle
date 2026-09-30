import { describe, expect, it } from 'vitest'

import { statBlockWithRecordIdentity } from './statBlockIdentity'

describe('statBlockWithRecordIdentity', () => {
  it('preserves class and alignment and parses character level', () => {
    const result = statBlockWithRecordIdentity(
      {
        identity: { role: 'NPC', race: 'Human', level: 2, class: 'Wizard', alignment: 'NG' },
        combat: { ac: 12 },
      },
      {
        role: 'NPC',
        level_cr: 'level 5',
        race_type: 'Elf',
        class_profession: 'Fighter',
        alignment: 'LG',
      },
    )

    expect(result).toEqual({
      identity: { role: 'NPC', race: 'Elf', level: 5, class: 'Fighter', alignment: 'LG' },
      combat: { ac: 12 },
    })
  })

  it('parses CR into cr and never writes it as level', () => {
    const result = statBlockWithRecordIdentity(
      { identity: { role: 'Monster', level: 4, race: 'Dragon' } },
      {
        role: 'Monster',
        level_cr: 'CR 10',
        race_type: 'Dragon',
        class_profession: '',
        alignment: '',
      },
    )

    expect(result?.['identity']).toEqual({ role: 'Monster', race: 'Dragon', cr: 10 })
  })

  it('preserves the complete block when it has no identity value', () => {
    const block = { attributes: { str: 10 }, actions: [] }
    expect(
      statBlockWithRecordIdentity(block, {
        role: '',
        level_cr: '',
        race_type: '',
        class_profession: '',
        alignment: '',
      }),
    ).toEqual(block)
  })
})
