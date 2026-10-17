// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { components } from '../api/schema'

const { apiFetchMock, socketCalls } = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
  socketCalls: [] as Array<(message: unknown) => void>,
}))
vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ params: { id: 'C1' } }),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('../api/client', async (original) => ({
  ...(await original<typeof import('../api/client')>()),
  apiFetch: apiFetchMock,
}))
vi.mock('../ws', () => ({
  connectJobSocket: vi.fn((_campaign: string, onMessage: (message: unknown) => void) => {
    socketCalls.push(onMessage)
    return () => {}
  }),
}))
import NewWorldView from './NewWorldView.vue'

enableAutoUnmount(afterEach)
afterEach(() => vi.useRealTimers())
type Job = components['schemas']['JobResponse']
const campaign = {
  id: 'C1',
  title: 'Generic library',
  theme: 'High Fantasy',
  description: '',
  custom_lore: '',
  is_generic: true,
  created_at: '2026-10-13T00:00:00Z',
}
const running: Job = {
  id: 'J1',
  campaign_id: 'C1',
  kind: 'build_in',
  state: 'running',
  progress: 0.1,
  payload: { key_figures: [{ name: 'Ahmet' }] },
  max_llm_calls: 64,
  max_media_calls: 0,
  error: null,
  result: null,
  queue_position: null,
  created_at: '2026-10-13T00:00:00Z',
  started_at: '2026-10-13T00:00:01Z',
  finished_at: null,
}
let jobList: Job[]

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  socketCalls.length = 0
  jobList = [structuredClone(running)]
  apiFetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === '/api/campaigns/C1/export')
      return { campaign, entities: [], edges: [], revision: null }
    if (url === '/api/campaigns/C1') return campaign
    if (url === '/api/campaigns/C1/media') return { media: [], next_cursor: null }
    if (url === '/api/jobs/J1/cancel' && init?.method === 'POST') {
      jobList = jobList.map((job) => (job.id === 'J1' ? { ...job, state: 'cancelled' } : job))
      return jobList[0]
    }
    if (url.startsWith('/api/jobs?')) return { jobs: jobList, next_cursor: null }
    throw new Error(`Unexpected request: ${url}`)
  })
})

describe('Generic library build activity', () => {
  it.each(['overview', 'directory'] as const)(
    'shows character, stage, failures, and cancel on the %s page',
    async (mode) => {
      jobList.push({
        ...running,
        id: 'J2',
        state: 'failed',
        payload: { key_figures: ['Vesper'] },
        error: 'Missing skills',
      })
      const wrapper = mount(NewWorldView, { props: { mode } })
      await flushPromises()
      expect(wrapper.get('.generation-status').text()).toContain('Generating: Ahmet')
      expect(wrapper.get('.generation-status').text()).toContain('Writing your character')
      expect(wrapper.text()).toContain('Recent builds')
      expect(wrapper.text()).toContain('Vesper')
      expect(wrapper.text()).toContain('Missing skills')
      await wrapper.get('.generation-status button').trigger('click')
      await flushPromises()
      expect(apiFetchMock).toHaveBeenCalledWith('/api/jobs/J1/cancel', { method: 'POST' })
      expect(wrapper.find('.generation-status').exists()).toBe(false)
      expect(wrapper.text()).toContain('Generation cancelled.')
    },
  )

  it('updates the visible stage from a live job frame', async () => {
    const wrapper = mount(NewWorldView)
    await flushPromises()
    socketCalls[0]!({ type: 'job_progress', job_id: 'J1', state: 'running', progress: 0.4 })
    await flushPromises()
    expect(wrapper.get('.generation-status').text()).toContain(
      'Checking and repairing character stats',
    )
    expect(wrapper.get('progress').attributes('value')).toBe('40')
  })

  it('refreshes the world when polling discovers the build has finished', async () => {
    vi.useFakeTimers()
    const wrapper = mount(NewWorldView)
    await flushPromises()
    const exportsBefore = apiFetchMock.mock.calls.filter(
      ([url]) => url === '/api/campaigns/C1/export',
    ).length
    jobList = [{ ...running, state: 'succeeded', progress: 1, result: { entity_count: 1 } }]
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.find('.generation-status').exists()).toBe(false)
    expect(wrapper.text()).toContain('succeeded')
    expect(
      apiFetchMock.mock.calls.filter(([url]) => url === '/api/campaigns/C1/export').length,
    ).toBeGreaterThan(exportsBefore)
  })
})
