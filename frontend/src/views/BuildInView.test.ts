// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../api/schema'

type Job = components['schemas']['JobResponse']

const { pushMock, apiFetchMock, socketCalls } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  apiFetchMock: vi.fn(),
  socketCalls: [] as Array<{
    campaignId: string
    onMessage: (message: unknown) => void
  }>,
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ params: { id: 'C1' } }),
  useRouter: () => ({ push: pushMock }),
}))

vi.mock('../ws', () => ({
  connectJobSocket: vi.fn((campaignId: string, onMessage: (message: unknown) => void) => {
    socketCalls.push({ campaignId, onMessage })
    return () => {}
  }),
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

import { ApiError } from '../api/client'
import BuildInView from './BuildInView.vue'

const CAMPAIGN = {
  id: 'C1',
  owner_id: 'A1',
  title: 'The Embermarked Vale',
  description: 'ash and old oaths',
  theme: 'Grimdark',
  custom_lore: '',
  created_at: '2026-09-01T00:00:00Z',
}

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'J1',
    campaign_id: 'C1',
    kind: 'build_in',
    payload: { places: ['Greymarch'], factions: [], key_figures: [], notes: '' },
    state: 'queued',
    progress: 0,
    max_llm_calls: 0,
    max_media_calls: 0,
    error: null,
    result: null,
    created_at: '2026-09-11T10:00:00Z',
    started_at: null,
    finished_at: null,
    queue_position: 1,
    ...overrides,
  }
}

/** The campaign's job list as REST hands it back (terminal frames re-sync it). */
let jobList: Job[] = []

function mockApi(options: { created?: Job; enqueueError?: Error } = {}) {
  apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
    const url = String(path)
    if (url === '/api/campaigns/C1') return CAMPAIGN
    if (url.startsWith('/api/jobs')) {
      if (init?.method === 'POST') {
        if (options.enqueueError) throw options.enqueueError
        return options.created
      }
      return { jobs: jobList, next_cursor: null }
    }
    throw new Error(`unexpected fetch: ${url}`)
  })
}

async function mountView() {
  const wrapper = mount(BuildInView)
  await flushPromises()
  return wrapper
}

function field(wrapper: VueWrapper, label: string) {
  const group = wrapper.findAll('label').find((entry) => entry.get('span').text() === label)
  if (!group) throw new Error(`no field labelled ${label}`)
  return group.get('textarea')
}

describe('BuildInView seed form', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    socketCalls.length = 0
    jobList = []
  })

  it('keeps the seed text through the build and a failure — the DM retries without retyping', async () => {
    const created = job()
    mockApi({ created })
    const wrapper = await mountView()

    await field(wrapper, 'Key places').setValue('Greymarch')
    await field(wrapper, 'Free-form notes').setValue('ash falls for a month')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    // Enqueued — the text is not spent yet, and the button holds until it settles.
    expect(field(wrapper, 'Key places').element.value).toBe('Greymarch')
    expect(field(wrapper, 'Free-form notes').element.value).toBe('ash falls for a month')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('button[type="submit"]').text()).toBe('Building…')

    jobList = [
      job({
        state: 'failed',
        error: 'build_in: wave 2 — orphan Kaelen the Mute',
        queue_position: null,
      }),
    ]
    socketCalls.at(-1)!.onMessage({ type: 'job_failed', job_id: created.id, state: 'failed' })
    await flushPromises()

    expect(field(wrapper, 'Key places').element.value).toBe('Greymarch')
    expect(field(wrapper, 'Free-form notes').element.value).toBe('ash falls for a month')
    expect(wrapper.text()).toContain('orphan Kaelen the Mute')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('clears the seed once the build-in succeeds', async () => {
    const created = job()
    mockApi({ created })
    const wrapper = await mountView()

    await field(wrapper, 'Key places').setValue('Greymarch')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    jobList = [job({ state: 'succeeded', progress: 1, queue_position: null })]
    socketCalls.at(-1)!.onMessage({ type: 'job_done', job_id: created.id, state: 'succeeded' })
    await flushPromises()

    expect(field(wrapper, 'Key places').element.value).toBe('')
  })

  it('keeps text typed for the next batch when the running build succeeds', async () => {
    const created = job()
    mockApi({ created })
    const wrapper = await mountView()

    await field(wrapper, 'Key places').setValue('Greymarch')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await field(wrapper, 'Key places').setValue('Hollowmere')

    jobList = [job({ state: 'succeeded', progress: 1, queue_position: null })]
    socketCalls.at(-1)!.onMessage({ type: 'job_done', job_id: created.id, state: 'succeeded' })
    await flushPromises()

    expect(field(wrapper, 'Key places').element.value).toBe('Hollowmere')
  })

  it('keeps the text when the enqueue itself is rejected', async () => {
    mockApi({
      enqueueError: new ApiError(422, 'invalid_job_input', 'build_in requires at least one entry'),
    })
    const wrapper = await mountView()

    await field(wrapper, 'Key figures').setValue('the mayor')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('build_in requires at least one entry')
    expect(field(wrapper, 'Key figures').element.value).toBe('the mayor')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })
})
