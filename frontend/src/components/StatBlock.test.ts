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
})