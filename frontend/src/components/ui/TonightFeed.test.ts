// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import TonightFeed from './TonightFeed.vue'

const feed = [
  {
    revision_id: 'rev-2',
    created_at: '2026-09-27T10:30:00Z',
    events: [
      {
        revision_id: 'rev-2',
        created_at: '2026-09-27T10:30:00Z',
        actor: 'dm',
        action: 'edited',
        target_names: ['Mira'],
        kind: 'knowledge',
      },
      {
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
})
