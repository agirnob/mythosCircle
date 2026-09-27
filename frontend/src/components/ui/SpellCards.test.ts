// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import SpellCards from './SpellCards.vue'

describe('SpellCards', () => {
  it('renders the seed spells as card rows and emits removals', async () => {
    const wrapper = mount(SpellCards, {
      props: { modelValue: ['Fire Bolt', 'Mage Hand'], disabled: false },
    })
    const inputs = wrapper.findAll('input')
    expect(inputs).toHaveLength(2)
    await inputs[0]!.setValue('Ray of Frost')
    await inputs[0]!.element.dispatchEvent(new Event('blur'))
    await inputs[0]!.trigger('blur')
    const updates = wrapper.emitted('update:modelValue')
    expect(updates).toBeTruthy()
    expect((updates![0]![0] as string[])[0]).toBe('Ray of Frost')
    // The ✕ removes its row.
    const before = wrapper.emitted('update:modelValue')!.length
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('✕'))!
      .trigger('click')
    expect(wrapper.emitted('update:modelValue')).toHaveLength(before + 1)
  })

  it('adds a blank row via the add button and emits null when everything is cleared', async () => {
    const wrapper = mount(SpellCards, { props: { modelValue: [], disabled: false } })
    expect(wrapper.text()).toContain('No spells yet.')
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('Add spell'))!
      .trigger('click')
    expect(wrapper.findAll('input')).toHaveLength(1)
  })
})
