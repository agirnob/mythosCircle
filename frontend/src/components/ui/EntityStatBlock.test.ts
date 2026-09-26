// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import EntityStatBlock from './EntityStatBlock.vue'

const block = {
  identity: { role: 'Barkeep', race: 'Human', level: 3, alignment: 'Neutral Good' },
  attributes: { str: 13, dex: 12, con: 14, int: 10, wis: 9, cha: 15 },
  combat: { ac: 16, hp: 44 },
  skills: [{ name: 'Persuasion', bonus: 5 }],
  actions: [{ name: 'Rapier', description: 'Melee attack.' }],
  traits: [{ name: 'Brave' }],
  spells: ['Fire Bolt'],
}

describe('EntityStatBlock', () => {
  it('renders the combat strip, ability grid, and collapsible sections', () => {
    const wrapper = mount(EntityStatBlock, { props: { block } })
    const text = wrapper.text()
    expect(text).toContain('16')
    expect(text).toContain('44')
    expect(text).toContain('+1')
    expect(text).toContain('STR')
    expect(text).toContain('Persuasion +5')
    expect(text).toContain('Rapier')
    expect(text).toContain('Brave')
    expect(text).toContain('Fire Bolt')
    expect(text).toContain('Level 3')
  })

  it('renders nothing for a non-object block', () => {
    const wrapper = mount(EntityStatBlock, { props: { block: 'junk' } })
    expect(wrapper.text()).toBe('')
  })

  it('omits absent sections instead of rendering placeholders', () => {
    const wrapper = mount(EntityStatBlock, {
      props: { block: { identity: { role: 'Barkeep' } } },
    })
    const text = wrapper.text()
    expect(text).toContain('Barkeep')
    expect(text).not.toContain('Actions')
    expect(text).not.toContain('Spells')
    expect(text).not.toContain('AC')
  })
})
