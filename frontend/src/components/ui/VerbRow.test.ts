// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import VerbRow from './VerbRow.vue'

describe('VerbRow', () => {
  it('renders the four consequence verbs from the session image', () => {
    const wrapper = mount(VerbRow, {
      props: { session: { defeated: true, hp: -12 } },
    })
    const text = wrapper.text()
    expect(text).toContain('Mark undefeated') // active affordance reads the state
    expect(text).toContain('Flip allegiance')
    expect(text).toContain('Resolve thread')
    expect(text).toContain('Spend item')
    expect(text).not.toContain('Take it back') // linked take-back is offered with story provenance
  })

  it('fires the defeated delta once and the toggle verbs flip', async () => {
    const wrapper = mount(VerbRow, { props: { session: {} } })
    const buttons = wrapper.findAll('button')
    await buttons[0]!.trigger('click')
    expect(wrapper.emitted('fire')![0]![0]).toEqual({ defeated: true })
    await buttons[1]!.trigger('click')
    expect(wrapper.emitted('fire')![1]![0]).toEqual({ allegiance: true })
    await buttons[2]!.trigger('click')
    expect(wrapper.emitted('fire')![2]![0]).toEqual({ thread: true })
    await buttons[3]!.trigger('click')
    expect(wrapper.emitted('fire')![3]![0]).toEqual({ item: true })
  })

  it('an active affordance emits an absolute false state for a new action', async () => {
    const wrapper = mount(VerbRow, { props: { session: { defeated: true, allegiance: true } } })
    const buttons = wrapper.findAll('button')
    await buttons[0]!.trigger('click')
    expect(wrapper.emitted('fire')![0]![0]).toEqual({ defeated: false })
    await buttons[1]!.trigger('click')
    expect(wrapper.emitted('fire')![1]![0]).toEqual({ allegiance: false })
  })
})
