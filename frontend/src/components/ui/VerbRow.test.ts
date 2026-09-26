// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import VerbRow from './VerbRow.vue'

describe('VerbRow', () => {
  it('renders the four consequence verbs from the session image', () => {
    const wrapper = mount(VerbRow, {
      props: { session: { defeated: true, hp: -12 }, canTakeBack: true },
    })
    const text = wrapper.text()
    expect(text).toContain('Defeated') // active affordance reads the state
    expect(text).toContain('Flip allegiance')
    expect(text).toContain('Resolve thread')
    expect(text).toContain('Spend item')
    expect(text).toContain('Take it back')
  })

  it('fires the defeated delta once and the toggle verbs flip', async () => {
    const wrapper = mount(VerbRow, { props: { session: {}, canTakeBack: false } })
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

  it('an active allegiance affordance fires its inverse on the next click', async () => {
    const wrapper = mount(VerbRow, { props: { session: { allegiance: true }, canTakeBack: false } })
    await wrapper.findAll('button')[1]!.trigger('click')
    expect(wrapper.emitted('fire')![0]![0]).toEqual({ allegiance: false })
  })

  it('emits take-back only when a take-back is possible', async () => {
    const possible = mount(VerbRow, { props: { session: { defeated: true }, canTakeBack: true } })
    const takeBack = possible.findAll('button').find((b) => b.text() === 'Take it back')
    expect(takeBack).toBeDefined()
    await takeBack!.trigger('click')
    expect(possible.emitted('take-back')).toHaveLength(1)

    const none = mount(VerbRow, { props: { session: {}, canTakeBack: false } })
    expect(none.text()).not.toContain('Take it back')
  })
})
