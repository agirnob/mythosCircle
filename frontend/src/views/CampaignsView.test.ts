// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../api/schema'

const { apiFetchMock, route } = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
  route: { query: {} as Record<string, string> },
}))

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
  useRoute: () => route,
}))

import { ApiError } from '../api/client'
import { useCampaignsStore } from '../stores/campaigns'
import CampaignsView from './CampaignsView.vue'

type Campaign = components['schemas']['CampaignResponse']

function campaign(id: string, title: string): Campaign {
  return {
    id,
    owner_id: 'A1',
    title,
    description: '',
    theme: 'Grimdark',
    custom_lore: '',
    is_generic: false,
    created_at: '2026-09-11T10:00:00Z',
  }
}

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

describe('CampaignsView world delete', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('deletes the world after the DM retypes its title, then refetches the list', async () => {
    let list: Campaign[] = [campaign('W1', 'Greymarch'), campaign('W2', 'The Embermarked Vale')]
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        list = [campaign('W2', 'The Embermarked Vale')] // the server's post-delete list
        return undefined
      }
      if (String(path).endsWith('/themes')) return { themes: ['Grimdark'] }
      return { campaigns: list, next_cursor: null }
    })
    const wrapper = mount(CampaignsView)
    await flushPromises()

    await wrapper
      .findAll('button')
      .filter((candidate) => candidate.text() === 'Delete')[0]!
      .trigger('click')
    await wrapper.get('.delete-world input[type="text"]').setValue('Greymarch')
    await wrapper.get('form.delete-world').trigger('submit')
    await flushPromises()

    const del = apiFetchMock.mock.calls.find(([, init]) => init?.method === 'DELETE')
    expect(String(del?.[0])).toBe('/api/campaigns/W1')
    expect(JSON.parse(String(del?.[1]?.body))).toEqual({ confirm: true })
    // The list refetched: the deleted world is gone, its sibling stays.
    expect(wrapper.text()).not.toContain('Greymarch')
    expect(wrapper.text()).toContain('The Embermarked Vale')
    expect(wrapper.find('.delete-world').exists()).toBe(false)
  })

  it('a mistyped title deletes nothing', async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      if (String(path).endsWith('/themes')) return { themes: ['Grimdark'] }
      return { campaigns: [campaign('W1', 'Greymarch')], next_cursor: null }
    })
    const wrapper = mount(CampaignsView)
    await flushPromises()

    await wrapper
      .findAll('button')
      .filter((candidate) => candidate.text() === 'Delete')[0]!
      .trigger('click')
    await wrapper.get('.delete-world input[type="text"]').setValue('greymarch')
    await wrapper.get('form.delete-world').trigger('submit')
    await flushPromises()

    expect(apiFetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(0)
    expect(wrapper.text()).toContain('The title did not match — nothing was deleted.')
    expect(wrapper.text()).toContain('Greymarch')
    expect(useCampaignsStore().campaigns).toHaveLength(1)
  })

  it('a failed delete keeps the world listed and shows the error', async () => {
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        throw new ApiError(404, 'not_found', 'Campaign not found.')
      }
      if (String(path).endsWith('/themes')) return { themes: ['Grimdark'] }
      return { campaigns: [campaign('W1', 'Greymarch')], next_cursor: null }
    })
    const wrapper = mount(CampaignsView)
    await flushPromises()

    await wrapper
      .findAll('button')
      .filter((candidate) => candidate.text() === 'Delete')[0]!
      .trigger('click')
    await wrapper.get('.delete-world input[type="text"]').setValue('Greymarch')
    await wrapper.get('form.delete-world').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Campaign not found.')
    // Never treated as deleted: the row and the store list keep the world.
    expect(wrapper.text()).toContain('Greymarch')
    expect(useCampaignsStore().campaigns.map((c) => c.id)).toEqual(['W1'])
    expect(wrapper.find('.delete-world').exists()).toBe(true)
  })

  it('a delete that lands but cannot refresh the list is reported as exactly that', async () => {
    let deleted = false
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        deleted = true
        return undefined
      }
      if (String(path).endsWith('/themes')) return { themes: ['Grimdark'] }
      if (deleted) throw new ApiError(500, 'server_error', 'Database unavailable.')
      return { campaigns: [campaign('W1', 'Greymarch')], next_cursor: null }
    })
    const wrapper = mount(CampaignsView)
    await flushPromises()

    await wrapper
      .findAll('button')
      .filter((candidate) => candidate.text() === 'Delete')[0]!
      .trigger('click')
    await wrapper.get('.delete-world input[type="text"]').setValue('Greymarch')
    await wrapper.get('form.delete-world').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('The world was deleted, but the list could not be refreshed.')
    expect(wrapper.text()).toContain('Database unavailable.')
  })
})

it('shows and dismisses only the fixed administrator-denied notice', async () => {
  setActivePinia(createPinia())
  route.query = { notice: 'admin-access-denied' }
  apiFetchMock.mockResolvedValue({ campaigns: [], themes: [], next_cursor: null })
  const wrapper = mount(CampaignsView)
  await flushPromises()
  expect(wrapper.get('[role="status"]').text()).toContain('Administrator access denied')
  await wrapper.get('[role="status"] button').trigger('click')
  expect(wrapper.find('[role="status"]').exists()).toBe(false)
  wrapper.unmount()
  route.query = { notice: '<script>anything</script>' }
  const other = mount(CampaignsView)
  expect(other.find('[role="status"]').exists()).toBe(false)
  other.unmount()
  route.query = {}
})
