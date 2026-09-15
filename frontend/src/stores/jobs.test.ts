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

  it('buildInJobs returns build_in jobs for the campaign, newest first', () => {
    const jobs = useJobsStore()
    jobs.upsert(job('J1', { created_at: '2026-08-30T20:00:00Z' }))
    jobs.upsert(job('J2', { campaign_id: 'C2', created_at: '2026-08-30T21:00:00Z' }))
    jobs.upsert(job('J3', { id: 'J3', kind: 'text', created_at: '2026-08-30T22:00:00Z' }))
    jobs.upsert(job('J4', { id: 'J4', kind: 'build_in', created_at: '2026-08-30T23:00:00Z' }))
    expect(jobs.buildInJobs('C1').map((j) => j.id)).toEqual(['J4', 'J1'])
    expect(jobs.buildInJobs('C2').map((j) => j.id)).toEqual(['J2'])
  })

  it('job_failed frame patches a cached running job and re-syncs terminal fields from REST', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          jobs: [
            job('J1', {
              state: 'failed',
              progress: 1,
              error: 'llm call failed: provider returned HTTP 429',
            }),
          ],
          next_cursor: null,
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )
    const jobs = useJobsStore()
    jobs.upsert(job('J1', { state: 'running', progress: 0.5 }))
    await jobs.handleWsMessage('C1', wsMessage({ type: 'job_failed', state: 'failed' }))
    const cached = jobs.byId['J1']
    expect(cached?.state).toBe('failed')
    expect(cached?.progress).toBe(1)
    expect(cached?.error).toBe('llm call failed: provider returned HTTP 429')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/jobs?campaign_id=C1',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('WS frame for an uncached job id recovers via a REST re-sync', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          jobs: [job('J9', { state: 'running', progress: 0.25 })],
          next_cursor: null,
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )
    const jobs = useJobsStore()
    await jobs.handleWsMessage('C1', wsMessage({ job_id: 'J9' }))
    expect(jobs.byId['J9']?.id).toBe('J9')
    expect(jobs.byId['J9']?.state).toBe('running')
  })

  it('upsert is monotonic: a lower-progress REST snapshot never regresses the cached row', async () => {
    const jobs = useJobsStore()
    jobs.upsert(job('J1', { state: 'running', progress: 0.5 }))
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ jobs: [job('J1', { state: 'running', progress: 0 })], next_cursor: null }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )
    await jobs.syncList('C1')
    expect(jobs.byId['J1']?.progress).toBe(0.5)
  })

  it('upsert skips a stale queued snapshot for a cached running job', () => {
    const jobs = useJobsStore()
    jobs.upsert(job('J1', { state: 'running', progress: 0.5 }))
    jobs.upsert(job('J1', { state: 'queued', progress: 0 }))
    expect(jobs.byId['J1']?.state).toBe('running')
    expect(jobs.byId['J1']?.progress).toBe(0.5)
  })

  it('syncList paginates until next_cursor is null', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ jobs: [job('J1')], next_cursor: 'cursor-2' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ jobs: [job('J2', { queue_position: 2 })], next_cursor: null }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      )
    const jobs = useJobsStore()
    await jobs.syncList('C1')
    expect(jobs.byId['J1']?.id).toBe('J1')
    expect(jobs.byId['J2']?.queue_position).toBe(2)
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/jobs?campaign_id=C1', expect.anything())
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/jobs?campaign_id=C1&cursor=cursor-2',
      expect.anything(),
    )
  })

  it('submitRegenerate posts a regenerate job with sections null (whole)', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(
          JSON.stringify(
            job('J5', { id: 'J5', kind: 'regenerate', state: 'queued', queue_position: 1 }),
          ),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    const jobs = useJobsStore()
    await jobs.submitRegenerate('C1', { kind: 'entity', id: 'E1' }, null)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('/api/jobs')
    expect(JSON.parse(init?.body as string)).toEqual({
      campaign_id: 'C1',
      kind: 'regenerate',
      payload: { target: { kind: 'entity', id: 'E1' } },
    })
    expect(jobs.byId['J5']?.kind).toBe('regenerate')
  })

  it('submitRegenerate includes the sections list for a per-section re-roll', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(
          JSON.stringify(
            job('J6', { id: 'J6', kind: 'regenerate', state: 'queued', queue_position: 1 }),
          ),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    const jobs = useJobsStore()
    await jobs.submitRegenerate('C1', { kind: 'candidate', id: 'CA1' }, ['personality'])
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('/api/jobs')
    expect(JSON.parse(init?.body as string)).toEqual({
      campaign_id: 'C1',
      kind: 'regenerate',
      payload: { target: { kind: 'candidate', id: 'CA1' }, sections: ['personality'] },
    })
  })

  it('submitPortrait posts an image job naming the entity and upserts it', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify(
          job('JP1', {
            id: 'JP1',
            kind: 'image',
            state: 'queued',
            payload: { entity_id: 'E1' },
            queue_position: 1,
          }),
        ),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const jobs = useJobsStore()
    await jobs.submitPortrait('C1', 'E1')
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('/api/jobs')
    expect(JSON.parse(init?.body as string)).toEqual({
      campaign_id: 'C1',
      kind: 'image',
      payload: { entity_id: 'E1' },
    })
    expect(jobs.byId['JP1']?.kind).toBe('image')
  })

  it('submitPortrait posts the P1 options snake_cased and omits blanks', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify(job('JP1', {})), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(job('JP2', {})), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    const jobs = useJobsStore()
    await jobs.submitPortrait('C1', 'E1', {
      style: 'custom',
      framing: 'headshot',
      background: 'transparent',
      customStyle: 'art nouveau',
    })
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('/api/jobs')
    expect(JSON.parse(init?.body as string)).toEqual({
      campaign_id: 'C1',
      kind: 'image',
      payload: {
        entity_id: 'E1',
        style: 'custom',
        framing: 'headshot',
        background: 'transparent',
        custom_style: 'art nouveau',
      },
    })
    // Blank/absent options never enter the payload (backend defaults win).
    fetchMock.mockClear()
    await jobs.submitPortrait('C1', 'E1', {
      style: 'illustration',
      framing: 'portrait',
      background: 'scene',
      customStyle: '  ',
    })
    const [, init2] = fetchMock.mock.calls[0]
    expect(JSON.parse(init2?.body as string)).toEqual({
      campaign_id: 'C1',
      kind: 'image',
      payload: { entity_id: 'E1', style: 'illustration', framing: 'portrait', background: 'scene' },
    })
  })

  it('portraitInFlight is true while the entity image job is pending and releases on terminal state', () => {
    const jobs = useJobsStore()
    jobs.upsert(
      job('JP1', { id: 'JP1', kind: 'image', payload: { entity_id: 'E1' }, state: 'queued' }),
    )
    expect(jobs.portraitInFlight('C1', 'E1')).toBe(true)
    expect(jobs.portraitInFlight('C1', 'E2')).toBe(false) // another entity is free
    expect(jobs.portraitInFlight('C1', 'E1')).toBe(true) // still queued
    jobs.upsert(
      job('JP1', {
        id: 'JP1',
        kind: 'image',
        payload: { entity_id: 'E1' },
        state: 'failed',
        error: 'boom',
      }),
    )
    expect(jobs.portraitInFlight('C1', 'E1')).toBe(false) // failed releases the button
  })
})
