import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../api/schema'
import { useJobsStore } from './jobs'
import type { WsMessage } from '../ws'

type Job = components['schemas']['JobResponse']

function job(id: string, overrides: Partial<Job> = {}): Job {
  return {
    id,
    campaign_id: 'C1',
    kind: 'build_in',
    payload: { places: ['Greymarch'] },
    state: 'queued',
    progress: 0,
    max_llm_calls: 64,
    max_media_calls: 8,
    error: null,
    result: null,
    created_at: '2026-08-30T20:00:00Z',
    started_at: null,
    finished_at: null,
    queue_position: 1,
    ...overrides,
  } as Job
}

function wsMessage(overrides: Partial<WsMessage>): WsMessage {
  return { type: 'job_progress', job_id: 'J1', state: 'running', ...overrides }
}

describe('jobs store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('submitBuildIn posts kind build_in and upserts the job', async () => {
    const body = job('J1', { queue_position: 1 })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const jobs = useJobsStore()
    const submitted = await jobs.submitBuildIn('C1', {
      places: ['Greymarch'],
      factions: [],
      key_figures: [],
      notes: '',
    })
    expect(submitted.kind).toBe('build_in')
    expect(jobs.latestBuildIn('C1')?.id).toBe('J1')
    const fetchMock = vi.mocked(globalThis.fetch)
    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(init?.body as string)).toEqual({
      campaign_id: 'C1',
      kind: 'build_in',
      payload: { places: ['Greymarch'], factions: [], key_figures: [], notes: '' },
    })
  })

  it('WS progress patches the cached row in place without clobbering REST fields', async () => {
    const jobs = useJobsStore()
    jobs.upsert(job('J1'))
    jobs.handleWsMessage('C1', wsMessage({ type: 'job_progress', progress: 0.5 }))
    const cached = jobs.byId['J1']
    expect(cached?.state).toBe('running')
    expect(cached?.progress).toBe(0.5)
    // Terminal fields the WS does not carry stay intact.
    expect(cached?.payload).toEqual({ places: ['Greymarch'] })
  })

  it('queue_changed re-syncs the list from REST', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ jobs: [job('J2', { queue_position: 2 })], next_cursor: null }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )
    const jobs = useJobsStore()
    jobs.upsert(job('J1'))
    await jobs.handleWsMessage(
      'C1',
      wsMessage({ type: 'queue_changed', state: 'succeeded', job_id: 'J1' }),
    )
    expect(jobs.byId['J2']?.queue_position).toBe(2)
  })

  it('latestBuildIn returns the newest build_in job for the campaign only', () => {
    const jobs = useJobsStore()
    jobs.upsert(job('J1', { created_at: '2026-08-30T20:00:00Z' }))
    jobs.upsert(job('J2', { campaign_id: 'C2', created_at: '2026-08-30T21:00:00Z' }))
    jobs.upsert(job('J3', { id: 'J3', kind: 'text', created_at: '2026-08-30T22:00:00Z' }))
    expect(jobs.latestBuildIn('C1')?.id).toBe('J1')
    expect(jobs.latestBuildIn('C2')?.id).toBe('J2')
  })
})
