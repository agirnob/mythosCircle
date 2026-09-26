// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import DialPicker from './DialPicker.vue'

describe('DialPicker', () => {
  it('renders the registry levels plus a muted unspecified option', () => {
    const wrapper = mount(DialPicker, {
      props: { levels: ['draft', 'simple', 'important'], current: null },
    })
    const options = wrapper.findAll('option').map((option) => option.text())
    expect(options).toContain('unspecified')
    expect(options).toContain('draft')
    expect(options).toContain('important')
  })

  it('selects the authored dial when present', () => {
    const wrapper = mount(DialPicker, {
      props: { levels: ['draft', 'simple'], current: 'simple' },
    })
    expect(wrapper.find('select').element.value).toBe('simple')
  })

  it('emits change with the chosen level and never renders an error for absent dials', async () => {
    const wrapper = mount(DialPicker, {
      props: { levels: ['draft', 'simple'], current: null },
    })
    expect(wrapper.text()).not.toContain('error')
    await wrapper.find('select').setValue('draft')
    await wrapper.find('select').trigger('change')
    const emitted = wrapper.emitted('change')
    expect(emitted).toBeDefined()
    expect(emitted![0]![0]).toBe('draft')
  })
})
