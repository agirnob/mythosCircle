// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const { apiFetchMock } = vi.hoisted(() => ({ apiFetchMock: vi.fn() }))

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly code: string,
      message: string,
    ) {
      super(message)
      this.name = 'ApiError'
    }
  },
  apiFetch: apiFetchMock,
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
}))

import { useCampaignsStore } from '../stores/campaigns'
import CampaignsView from './CampaignsView.vue'

describe('CampaignsView theme picker', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('offers only the configured seed themes — no free text (dogfood 2026-09-09)', async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      if (String(path).endsWith('/themes')) {
        return { themes: ['High Fantasy', 'Grimdark', 'Steampunk', 'Planar'] }
      }
      return { campaigns: [], next_cursor: null }
    })
    const wrapper = mount(CampaignsView)
    await flushPromises()

    const select = wrapper.get('select')
    const options = select.findAll('option')
    // Placeholder + the four seeds, exactly as the server's list.
    expect(options).toHaveLength(5)
    expect(options[0]!.attributes('value')).toBe('')
    expect(options.slice(1).map((o) => o.text())).toEqual([
      'High Fantasy',
      'Grimdark',
      'Steampunk',
      'Planar',
    ])
    expect(wrapper.find('input[type="text"][placeholder="Dark fantasy heist"]').exists()).toBe(
      false,
    )
  })

  it('creates with the picked theme and nothing else in the payload', async () => {
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (String(path).endsWith('/themes')) {
        return { themes: ['High Fantasy', 'Grimdark'] }
      }
      if (init?.method === 'POST') {
        return {
          id: '01M1',
          owner_id: '01A',
          title: 'New',
          description: '',
          theme: 'Grimdark',
          custom_lore: '',
          created_at: '2026-09-09T00:00:00Z',
        }
      }
      return { campaigns: [], next_cursor: null }
    })
    const wrapper = mount(CampaignsView)
    await flushPromises()

    await wrapper.get('input[type="text"]').setValue('New')
    await wrapper.get('select').setValue('Grimdark')
    await wrapper.get('form.create').trigger('submit')
    await flushPromises()

    const store = useCampaignsStore()
    expect(store.campaigns[0]?.theme).toBe('Grimdark')
    const post = apiFetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body)).theme).toBe('Grimdark')
  })
})
