// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import { FIELD_LABELS } from '../profile/profile'
import KnowledgeChip from './KnowledgeChip.vue'

describe('KnowledgeChip', () => {
  it('announces the marker state as text and carries switch semantics', () => {
    const secret = mount(KnowledgeChip, { props: { field: 'secret', known: false } })
    expect(secret.text()).toContain(FIELD_LABELS['secret'])
    expect(secret.text()).toContain('secret')
    expect(secret.find('button').attributes('role')).toBe('switch')
    expect(secret.find('button').attributes('aria-checked')).toBe('false')

    const known = mount(KnowledgeChip, { props: { field: 'rumor', known: true } })
    expect(known.text()).toContain(FIELD_LABELS['rumor'])
    expect(known.text()).toContain('known by party')
    expect(known.find('button').attributes('aria-checked')).toBe('true')
  })

  it('emits toggle on click — the parent computes the target state', async () => {
    const wrapper = mount(KnowledgeChip, { props: { field: 'party_hook', known: false } })
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('toggle')).toHaveLength(1)
  })

  it('disables without a handler while still readable', () => {
    const wrapper = mount(KnowledgeChip, {
      props: { field: 'secret', known: true, disabled: true },
    })
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
  })
})
