// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import TonightFeed from './TonightFeed.vue'

const feed = [
  {
    revision_id: 'rev-2',
    created_at: '2026-09-27T10:30:00Z',
    events: [
      {
        event_id: 'event-2',
        revision_id: 'rev-2',
        created_at: '2026-09-27T10:30:00Z',
        actor: 'dm',
        action: 'edited',
        target_names: ['Mira'],
        kind: 'knowledge',
      },
      {
        event_id: 'event-2',
        revision_id: 'rev-2',
        created_at: '2026-09-27T10:30:00Z',
        actor: 'dm',
        action: 'edited',
        target_names: ['Mira'],
        kind: 'session',
      },
    ],
  },
  {
    revision_id: 'rev-1',
    created_at: '2026-09-27T09:00:00Z',
    events: [
      {
        event_id: 'event-1',
        revision_id: 'rev-1',
        created_at: '2026-09-27T09:00:00Z',
        actor: 'dm',
        action: 'created',
        target_names: ['The Anchor'],
        kind: 'entity',
      },
    ],
  },
]

describe('TonightFeed', () => {
  it('renders every event line verbatim — take-backs and verbs read edited', () => {
    const wrapper = mount(TonightFeed, { props: { revisions: feed } })
    const text = wrapper.text()
    expect(text).toContain('edited')
    expect(text).toContain('created')
    expect(text).toContain('Mira')
    expect(text).toContain('The Anchor')
    expect(text).toContain('knowledge')
    expect(text).toContain('session')
    expect(text).toContain('entity')
  })

  it('renders an empty message for an empty feed', () => {
    const wrapper = mount(TonightFeed, { props: { revisions: [] } })
    expect(wrapper.text()).toContain('No changes yet.')
  })

  it('renders escaped details and preserves multiple events of the same kind on update', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const events = [
      { ...feed[0]!.events[1]!, details: ['Marked defeated', '<img src=x onerror=alert(1)>'] },
      { ...feed[0]!.events[1]!, target_names: ['The Anchor'], details: ['Resolved thread'] },
    ]
    const wrapper = mount(TonightFeed, { props: { revisions: [{ ...feed[0]!, events }] } })
    expect(wrapper.findAll('.mc-feed-line')).toHaveLength(2)
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
    expect(wrapper.find('img').exists()).toBe(false)
    await wrapper.setProps({ revisions: [{ ...feed[0]!, events: [...events].reverse() }] })
    const lines = wrapper.findAll('.mc-feed-line')
    expect(lines[0]!.text()).toContain('The Anchor')
    expect(lines[0]!.text()).toContain('Resolved thread')
    expect(lines[1]!.text()).toContain('Mira')
    expect(lines[1]!.text()).toContain('Marked defeated')
    expect(warn).not.toHaveBeenCalled()
    warn.mockRestore()
  })
})
