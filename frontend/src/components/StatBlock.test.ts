// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import StatBlock from './StatBlock.vue'

const block = {
  identity: { role: 'Barkeep', race: 'Human', level: 3, alignment: 'Neutral Good' },
  attributes: { str: 13, dex: 12, con: 14, int: 10, wis: 9, cha: 15 },
  combat: { ac: 16, hp: 44 },
  skills: [{ name: 'Persuasion', bonus: 5 }],
  actions: [{ name: 'Rapier', description: 'Melee attack.' }],
  traits: [{ name: 'Brave' }],
  spells: ['Fire Bolt'],
}

describe('StatBlock', () => {
  it('derives modifiers, initiative, and labels deterministically', () => {
    const wrapper = mount(StatBlock, { props: { block } })
    const text = wrapper.text()
    expect(text).toContain('STR13 (+1)')
    expect(text).toContain('AC 16 · HP 44 · Initiative +1')
    expect(text).toContain('Level 3')
    expect(text).toContain('Persuasion +5')
    expect(text).toContain('Rapier — Melee attack.')
    expect(text).toContain('Brave')
    expect(text).toContain('Fire Bolt')
  })

  it('prefers CR over level for monster blocks', () => {
    const wrapper = mount(StatBlock, {
      props: { block: { ...block, identity: { role: 'Monster', cr: 5 } } },
    })
    expect(wrapper.text()).toContain('CR 5')
    expect(wrapper.text()).not.toContain('Level')
  })

  it('tolerates junk: a non-object block renders nothing', () => {
    const wrapper = mount(StatBlock, { props: { block: 'junk' } })
    expect(wrapper.text()).toBe('')
  })

  it('tolerates wrong-typed fields instead of rendering junk', () => {
    const wrapper = mount(StatBlock, {
      props: {
        block: {
          identity: { role: 'X', cr: false },
          attributes: { str: true, dex: '12', con: 10 },
          combat: { ac: '16', hp: null },
          actions: [{ description: 'no name here' }],
        },
      },
    })
    const text = wrapper.text()
    expect(text).toContain('STR—')
    expect(text).toContain('DEX12')
    expect(text).not.toContain('AC')
    expect(text).not.toContain('true')
    expect(text).not.toContain('[object Object]')
    expect(text).toContain('?')
    expect(text).toContain('no name here')
  })

  it('PRE_CHANGE_BLOCK: a stat block written before the optional aspects renders exactly as before', () => {
    const wrapper = mount(StatBlock, { props: { block } })
    // The whole rendered text, not a sample: the additive spec (2026-09-11)
    // must not introduce a single empty row, placeholder, or separator.
    expect(wrapper.text()).toBe(
      'Stat blockBarkeep · Human · Level 3 · Neutral Good' +
        'STR13 (+1)DEX12 (+1)CON14 (+2)INT10 (+0)WIS9 (-1)CHA15 (+2)' +
        'AC 16 · HP 44 · Initiative +1Persuasion +5ActionsRapier — Melee attack.' +
        'TraitsBraveSpellsFire Bolt',
    )
  })

  // The optional mechanics aspects (spec 2026-09-11): one pin per rendered
  // group. Every field is optional, so the group renders only when stored.
  const aspects = {
    identity: { role: 'Monster', cr: 13, race: 'Fiend' },
    attributes: { str: 22, dex: 14, con: 24, int: 16, wis: 15, cha: 20 },
    combat: { ac: 20, hp: 290, hit_dice: '24d10 + 192' },
    saves: { str: 9, dex: 5, con: 10, int: 6, wis: 9, cha: 11 },
    initiative: 5,
    passive_perception: 17,
    proficiency_bonus: 4,
    spellcasting: { dc: 18, attack_bonus: 10, slots: [4, 3, 2] },
    features: ['Divine Smite', 'Aura of Protection'],
    resources: { lay_on_hands: 85, channel_divinity: 3 },
  }

  it('renders hit dice with the combat line', () => {
    const wrapper = mount(StatBlock, { props: { block: aspects } })
    expect(wrapper.text()).toContain('AC 20 · HP 290 · Hit dice 24d10 + 192')
  })

  it('renders saves in ability order', () => {
    const wrapper = mount(StatBlock, { props: { block: aspects } })
    expect(wrapper.text()).toContain('Saves STR +9, DEX +5, CON +10, INT +6, WIS +9, CHA +11')
  })

  it('prefers a stored initiative over the DEX-derived one', () => {
    const wrapper = mount(StatBlock, { props: { block: aspects } })
    const text = wrapper.text()
    expect(text).toContain('Initiative +5')
    // DEX 14 derives +2 — the stated number wins and never doubles up.
    expect(text).not.toContain('Initiative +2')
    expect(text.match(/Initiative/g)).toHaveLength(1)
  })

  it('keeps the DEX-derived initiative when the block states none', () => {
    // The fixture states initiative 5; the derived block omits the key
    // entirely (a stated number wins over the DEX-derived one above).
    const { initiative, ...derived } = aspects
    expect(initiative).toBe(5)
    const wrapper = mount(StatBlock, { props: { block: derived } })
    expect(wrapper.text()).toContain('AC 20 · HP 290 · Hit dice 24d10 + 192 · Initiative +2')
  })

  it('renders passive perception and the proficiency bonus', () => {
    const wrapper = mount(StatBlock, { props: { block: aspects } })
    expect(wrapper.text()).toContain('Passive perception 17')
    expect(wrapper.text()).toContain('Proficiency bonus +4')
  })

  it('renders spellcasting DC, attack bonus, and slots', () => {
    const wrapper = mount(StatBlock, { props: { block: aspects } })
    expect(wrapper.text()).toContain('Spellcasting DC 18 · Attack +10 · Slots 4/3/2')
  })

  it('renders feature names and resources', () => {
    const wrapper = mount(StatBlock, { props: { block: aspects } })
    expect(wrapper.text()).toContain('Features Divine Smite, Aura of Protection')
    expect(wrapper.text()).toContain('Resources Lay on hands 85, Channel divinity 3')
  })

  it('renders the aspect lines in contract order, under the combat line', () => {
    const wrapper = mount(StatBlock, { props: { block: aspects } })
    const text = wrapper.text()
    const order = [
      'Hit dice 24d10 + 192',
      'Saves STR',
      'Initiative +5',
      'Passive perception 17',
      'Proficiency bonus +4',
      'Spellcasting DC 18',
      'Features Divine Smite',
      'Resources Lay on hands',
    ]
    const positions = order.map((marker) => text.indexOf(marker))
    expect(positions.every((position) => position >= 0)).toBe(true)
    expect([...positions].sort((a, b) => a - b)).toEqual(positions)
  })

  it('renders structured attack damage, dropping the prose it supersedes', () => {
    const wrapper = mount(StatBlock, {
      props: {
        block: {
          ...aspects,
          actions: [
            {
              name: 'Oathblade',
              description: 'Melee weapon attack: 19 (2d6 + 12) slashing damage.',
              to_hit: 18,
              damage: [
                { dice: '2d6', count: 2, sides: 6, bonus: 12, average: 19, type: 'slashing' },
                { dice: '3d10', count: 3, sides: 10, bonus: 0, average: 16.5, type: 'radiant' },
              ],
            },
          ],
        },
      },
    })
    // the prose IS the line (owner feedback 2026-09-17); the structured
    // numbers render as a secondary clause, never replacing the prose
    expect(wrapper.text()).toContain(
      'Oathblade — Melee weapon attack: 19 (2d6 + 12) slashing damage.',
    )
    expect(wrapper.text()).toContain(
      'to-hit +18 · 19 (2d6+12) slashing + 16.5 (3d10) radiant',
    )
    // the prose stays — the numbers are additive, not a replacement
  })

  it('falls back to the description when an attack carries no readable part', () => {
    const wrapper = mount(StatBlock, {
      props: {
        block: {
          ...aspects,
          actions: [
            { name: 'Rapier', description: 'Melee attack.', to_hit: 6 },
            { name: 'Claw', description: 'Rake.', damage: ['junk', {}] },
          ],
        },
      },
    })
    const text = wrapper.text()
    expect(text).toContain('Rapier — Melee attack.')
    expect(text).toContain('Claw — Rake.')
    // A to-hit alone is not an attack line: nothing structured to show.
    expect(text).not.toContain('Rapier +6')
  })

  it('derives a part\u2019s dice and average from its count/sides pair', () => {
    const wrapper = mount(StatBlock, {
      props: {
        block: {
          ...aspects,
          actions: [
            { name: 'Bite', to_hit: 5, damage: [{ count: 1, sides: 6, type: 'piercing' }] },
          ],
        },
      },
    })
    expect(wrapper.text()).toContain('Bite')
    expect(wrapper.text()).toContain('to-hit +5 · 3.5 (1d6) piercing')
  })

  it('renders the panel for a block carrying nothing but an optional aspect', () => {
    const wrapper = mount(StatBlock, { props: { block: { resources: { ki: 5 } } } })
    expect(wrapper.text()).toContain('Stat block')
    expect(wrapper.text()).toContain('Resources Ki 5')
  })

  it('renders no aspect line for absent or wrong-typed optional fields', () => {
    const wrapper = mount(StatBlock, {
      props: {
        block: {
          combat: { ac: 16, hp: 44, hit_dice: 24 },
          saves: { strength: 9 },
          initiative: '5',
          passive_perception: null,
          proficiency_bonus: true,
          spellcasting: { dc: '18', slots: [] },
          features: ['', 7],
          resources: { ki: '5' },
        },
      },
    })
    const text = wrapper.text()
    expect(text).toContain('AC 16 · HP 44')
    for (const absent of ['Hit dice', 'Saves', 'Initiative', 'Passive perception']) {
      expect(text).not.toContain(absent)
    }
    expect(text).not.toContain('Proficiency bonus')
    expect(text).not.toContain('Spellcasting')
    expect(text).not.toContain('Features')
    expect(text).not.toContain('Resources')
  })

  it('renders the over-powered flag with audited DPR vs band', () => {
    const wrapper = mount(StatBlock, {
      props: {
        block: {
          ...block,
          power: { dpr: 189, band: [63, 68], verdict: 'over-powered' },
        },
      },
    })
    expect(wrapper.text()).toContain('Over-powered — DPR 189 vs band 63–68')
  })

  it('renders the under-powered flag for an NPC/BBEG stamp (NPC oracle)', () => {
    const wrapper = mount(StatBlock, {
      props: {
        block: {
          ...block,
          power: { dpr: 11, band: [75, 80], verdict: 'under-powered' },
        },
      },
    })
    expect(wrapper.text()).toContain('Under-powered — DPR 11 vs band 75–80')
  })

  it('renders no power line without a stamped verdict', () => {
    const wrapper = mount(StatBlock, { props: { block } })
    expect(wrapper.text()).not.toContain('Over-powered')
    expect(wrapper.text()).not.toContain('Under-powered')
    const onTarget = mount(StatBlock, {
      props: { block: { ...block, power: { dpr: 35, band: [33, 38], verdict: 'on-target' } } },
    })
    expect(onTarget.text()).not.toContain('Over-powered')
    expect(onTarget.text()).not.toContain('Under-powered')
    expect(onTarget.text()).not.toContain('DPR 35')
  })
})
