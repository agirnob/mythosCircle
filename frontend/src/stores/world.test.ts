import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../api/schema'
import { useJobsStore } from './jobs'
import { useWorldStore } from './world'
import type { WsMessage } from '../ws'

type Job = components['schemas']['JobResponse']
type WorldExport = components['schemas']['WorldExport']

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

function worldExport(): WorldExport {
  return {
    campaign: {
      id: 'C1',
      title: 'Greymarch',
      theme: 'frontier dread',
      description: '',
      custom_lore: '',
      created_at: '2026-08-30T20:00:00Z',
    },
    revision: { id: '01JZZZZZZZZZZZZZZZZZZZZZZZ', created_at: '2026-08-30T20:05:00Z' },
    entities: [],
    edges: [],
  }
}

function requestUrl(input: Request | URL | string): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  return input.url
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('world store', () => {
  let exportCalls: string[]

  /** Route mocks: export calls are counted; jobs-list calls drain empty. */
  function mockFetch() {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = requestUrl(input)
      if (url.includes('/export')) {
        exportCalls.push(url)
        return jsonResponse(worldExport())
      }
      if (url.includes('/api/jobs')) {
        return jsonResponse({ jobs: [], next_cursor: null })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    return spy
  }

  /** Gate the next export fetch on a manual release, still counting it. */
  function gateNextExport(): (value: Response) => void {
    let release!: (value: Response) => void
    const gate = new Promise<Response>((resolve) => {
      release = resolve
    })
    vi.mocked(globalThis.fetch).mockImplementationOnce(async (input) => {
      const url = requestUrl(input)
      exportCalls.push(url)
      return gate
    })
    return release
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    exportCalls = []
  })

  it('load stores the export snapshot', async () => {
    mockFetch()
    const world = useWorldStore()
    await world.load('C1')
    const entry = world.entry('C1')
    expect(entry.world?.campaign.title).toBe('Greymarch')
    expect(entry.notFound).toBe(false)
    expect(entry.error).toBe(null)
    expect(exportCalls).toHaveLength(1)
  })

  it('maps a 404 to not-found without an error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ code: 'not_found', message: 'Campaign not found.' }, 404),
    )
    const world = useWorldStore()
    await world.load('C1')
    const entry = world.entry('C1')
    expect(entry.notFound).toBe(true)
    expect(entry.world).toBe(null)
    expect(entry.error).toBe(null)
  })

  it('refetches on qualifying build_in frames only', async () => {
    mockFetch()
    const jobs = useJobsStore()
    jobs.upsert(job('J1'))
    const world = useWorldStore()
    await world.load('C1')
    expect(exportCalls).toHaveLength(1)

    // Sub-threshold progress (before the wave-1 commit) is ignored.
    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 0.25 }))
    expect(exportCalls).toHaveLength(1)
    // 0.49 still precedes the wave-1 commit — no refetch.
    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 0.49 }))
    expect(exportCalls).toHaveLength(1)

    // 0.5 = wave-1 commit; 1.0 = wave-2 commit.
    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 0.5 }))
    expect(exportCalls).toHaveLength(2)
    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 1.0 }))
    expect(exportCalls).toHaveLength(3)

    // Terminal frames refetch: a mid-wave-2 failure still leaves wave 1.
    await world.handleJobMessage('C1', wsMessage({ type: 'job_done', state: 'succeeded' }))
    expect(exportCalls).toHaveLength(4)
    await world.handleJobMessage('C1', wsMessage({ type: 'job_failed', state: 'failed' }))
    expect(exportCalls).toHaveLength(5)
    await world.handleJobMessage('C1', wsMessage({ type: 'job_cancelled', state: 'cancelled' }))
    expect(exportCalls).toHaveLength(6)

    // queue_changed carries no world change.
    await world.handleJobMessage('C1', wsMessage({ type: 'queue_changed', state: 'queued' }))
    expect(exportCalls).toHaveLength(6)
  })

  it('ignores non-build_in jobs (cached and REST-recovered)', async () => {
    mockFetch()
    const jobs = useJobsStore()
    jobs.upsert(job('J1', { kind: 'text' }))
    const world = useWorldStore()
    await world.load('C1')

    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 1.0 }))
    await world.handleJobMessage('C1', wsMessage({ type: 'job_done', state: 'succeeded' }))
    expect(exportCalls).toHaveLength(1)

    // A frame for an uncached id recovers the row via REST — still no
    // refetch when the recovered kind is not build_in.
    vi.mocked(globalThis.fetch).mockImplementationOnce(async (input) => {
      const url = requestUrl(input)
      expect(url).toContain('/api/jobs')
      return jsonResponse({ jobs: [job('J9', { kind: 'text' })], next_cursor: null })
    })
    await world.handleJobMessage(
      'C1',
      wsMessage({ type: 'job_done', job_id: 'J9', state: 'succeeded' }),
    )
    expect(exportCalls).toHaveLength(1)
  })

  it('refetches once on reconnect', async () => {
    mockFetch()
    const world = useWorldStore()
    await world.load('C1')
    world.requestRefetch('C1')
    await vi.waitFor(() => expect(exportCalls).toHaveLength(2))
  })

  it('coalesces a frame arriving during an in-flight fetch into one trailing refetch', async () => {
    mockFetch()
    const release = gateNextExport()

    const jobs = useJobsStore()
    jobs.upsert(job('J1'))
    const world = useWorldStore()
    const load = world.load('C1')

    // A qualifying frame lands while the mount load is still in flight.
    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 1.0 }))
    expect(exportCalls).toHaveLength(1) // no parallel fetch stacked

    release(jsonResponse(worldExport()))
    await load

    // Exactly one trailing fetch covers the frame's delta.
    await vi.waitFor(() => expect(exportCalls).toHaveLength(2))
    await vi.waitFor(() => expect(world.entry('C1').fetching).toBe(false))
    expect(exportCalls).toHaveLength(2)
  })

  it('collapses repeated reconnect requests during an in-flight fetch', async () => {
    mockFetch()
    const release = gateNextExport()

    const world = useWorldStore()
    const load = world.load('C1')
    world.requestRefetch('C1')
    world.requestRefetch('C1')

    release(jsonResponse(worldExport()))
    await load
    await vi.waitFor(() => expect(world.entry('C1').fetching).toBe(false))
    expect(exportCalls).toHaveLength(2)
  })
  it('a 404 on a refetch clears the snapshot into not-found', async () => {
    mockFetch()
    const world = useWorldStore()
    await world.load('C1')
    expect(world.entry('C1').world).not.toBe(null)

    vi.mocked(globalThis.fetch).mockResolvedValue(
      jsonResponse({ code: 'not_found', message: 'Campaign not found.' }, 404),
    )
    world.requestRefetch('C1')
    await vi.waitFor(() => expect(world.entry('C1').notFound).toBe(true))
    expect(world.entry('C1').world).toBe(null)
    expect(world.entry('C1').error).toBe(null)
  })

  it('a failed refetch preserves the last good snapshot with the error set', async () => {
    mockFetch()
    const world = useWorldStore()
    await world.load('C1')
    const good = world.entry('C1').world

    vi.mocked(globalThis.fetch).mockResolvedValue(
      jsonResponse({ code: 'server_error', message: 'Database unavailable.' }, 500),
    )
    world.requestRefetch('C1')
    await vi.waitFor(() => expect(world.entry('C1').error).not.toBe(null))
    expect(world.entry('C1').world).toBe(good)
    expect(world.entry('C1').notFound).toBe(false)
  })

  it('an unresolved-kind terminal frame still triggers a refetch', async () => {
    mockFetch()
    const world = useWorldStore()
    await world.load('C1')

    // No cached job; REST recovery drains empty — the kind stays unknown.
    // job_done is the last frame a build-in emits, so the refetch runs.
    await world.handleJobMessage('C1', wsMessage({ type: 'job_done', state: 'succeeded' }))
    await vi.waitFor(() => expect(exportCalls).toHaveLength(2))

    // Non-terminal frames with an unresolved kind are still ignored.
    await world.handleJobMessage('C1', wsMessage({ type: 'job_progress', progress: 1.0 }))
    expect(exportCalls).toHaveLength(2)
  })

  it('ignores frames for another campaign even when the job is cached', async () => {
    mockFetch()
    const jobs = useJobsStore()
    jobs.upsert(job('J1', { campaign_id: 'C2' }))
    const world = useWorldStore()
    await world.load('C1')

    await world.handleJobMessage(
      'C1',
      wsMessage({ type: 'job_progress', job_id: 'J1', progress: 1.0 }),
    )
    expect(exportCalls).toHaveLength(1)
  })

  it('a terminal frame whose REST recovery fails still refetches (kind unresolved)', async () => {
    mockFetch()
    const world = useWorldStore()
    await world.load('C1')

    // REST recovery itself fails — the kind stays unknown; the read-only
    // refetch still runs (job_done is a build-in's last frame).
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error('jobs api down'))
    await world.handleJobMessage('C1', wsMessage({ type: 'job_done', state: 'succeeded' }))
    await vi.waitFor(() => expect(exportCalls).toHaveLength(2))
  })

  // -------------------------------------------------------------------------
  // Spec-4.1: the WS drives the media manifest refetch (no manual refresh)
  // -------------------------------------------------------------------------

  /** Route mocks counting /media calls; job frames land on the jobs store. */
  function mockMediaFetch() {
    const mediaCalls: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = requestUrl(input)
      if (url.includes('/media')) {
        mediaCalls.push(url)
        return jsonResponse({ media: [] })
      }
      if (url.includes('/export')) {
        exportCalls.push(url)
        return jsonResponse(worldExport())
      }
      if (url.includes('/api/jobs')) {
        return jsonResponse({ jobs: [], next_cursor: null })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    return mediaCalls
  }

  function imageJob(id: string, overrides: Partial<Job> = {}): Job {
    return {
      id,
      campaign_id: 'C1',
      kind: 'image',
      payload: { entity_id: 'E1' },
      state: 'queued',
      progress: 0,
      max_llm_calls: 64,
      max_media_calls: 8,
      error: null,
      result: null,
      created_at: '2026-09-06T10:00:00Z',
      started_at: null,
      finished_at: null,
      queue_position: 1,
      ...overrides,
    } as Job
  }

  it('a job_done frame for a cached image job fetches the media list exactly once', async () => {
    const mediaCalls = mockMediaFetch()
    const jobs = useJobsStore()
    jobs.upsert(imageJob('JI1', { state: 'running' }))
    const world = useWorldStore()
    await world.load('C1')
    expect(mediaCalls).toHaveLength(0)

    await world.handleJobMessage(
      'C1',
      wsMessage({ type: 'job_done', job_id: 'JI1', state: 'succeeded' }),
    )
    await vi.waitFor(() => expect(mediaCalls).toHaveLength(1))
    expect(mediaCalls[0]).toBe('/api/campaigns/C1/media')
    // The snapshot is untouched by an image frame.
    expect(exportCalls).toHaveLength(1)
  })

  it('an image job_failed frame fetches the media list (re-trigger state)', async () => {
    const mediaCalls = mockMediaFetch()
    const jobs = useJobsStore()
    jobs.upsert(imageJob('JI1', { state: 'running' }))
    const world = useWorldStore()
    await world.load('C1')

    await world.handleJobMessage(
      'C1',
      wsMessage({ type: 'job_failed', job_id: 'JI1', state: 'failed' }),
    )
    await vi.waitFor(() => expect(mediaCalls).toHaveLength(1))
  })

  it('running/progress frames for an image job do NOT fetch media', async () => {
    const mediaCalls = mockMediaFetch()
    const jobs = useJobsStore()
    jobs.upsert(imageJob('JI1', { state: 'running' }))
    const world = useWorldStore()
    await world.load('C1')

    await world.handleJobMessage(
      'C1',
      wsMessage({ type: 'job_progress', job_id: 'JI1', progress: 0.5 }),
    )
    await world.handleJobMessage(
      'C1',
      wsMessage({ type: 'queue_changed', job_id: 'JI1', state: 'running' }),
    )
    expect(mediaCalls).toHaveLength(0)
  })

  it('a terminal frame for an UNCACHED image job (kind unresolved) fetches media too', async () => {
    const mediaCalls = mockMediaFetch()
    const world = useWorldStore()
    await world.load('C1')

    // No cached job; REST recovery drains empty — the kind stays unknown,
    // and the unresolved-kind terminal fallback refetches both surfaces.
    await world.handleJobMessage('C1', wsMessage({ type: 'job_done', state: 'succeeded' }))
    await vi.waitFor(() => expect(mediaCalls).toHaveLength(1))
    await vi.waitFor(() => expect(exportCalls).toHaveLength(2))
  })

  it('updateEntity PATCHes the changed fields plus the snapshot base_revision and refetches', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = requestUrl(input)
      if (url.includes('/entities/E1')) {
        return new Response(null, { status: 204 })
      }
      if (url.includes('/export')) {
        exportCalls.push(url)
        return jsonResponse(worldExport())
      }
      if (url.includes('/api/jobs')) {
        return jsonResponse({ jobs: [], next_cursor: null })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    const world = useWorldStore()
    await world.load('C1')
    expect(exportCalls).toHaveLength(1)

    await world.updateEntity('C1', 'E1', { personality: 'rewritten by hand' }, '01JZZZZZZZZZZZZZZZZZZZZZZZ')

    const patchCall = fetchSpy.mock.calls.find((call) => String(call[0]).includes('/entities/E1'))!
    expect(patchCall[1]?.method).toBe('PATCH')
    expect(JSON.parse((patchCall[1]?.body as string) ?? '{}')).toEqual({
      personality: 'rewritten by hand',
      base_revision: '01JZZZZZZZZZZZZZZZZZZZZZZZ',
    })
    // Success lands a snapshot refetch.
    await vi.waitFor(() => expect(exportCalls).toHaveLength(2))
  })

  it('updateEntity omits base_revision when none is supplied', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = requestUrl(input)
      if (url.includes('/entities/E1')) {
        return new Response(null, { status: 204 })
      }
      if (url.includes('/export')) {
        exportCalls.push(url)
        return jsonResponse(worldExport())
      }
      if (url.includes('/api/jobs')) {
        return jsonResponse({ jobs: [], next_cursor: null })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    const world = useWorldStore()
    await world.load('C1')

    await world.updateEntity('C1', 'E1', { secret: 'oh no' }, undefined)

    const patchCall = fetchSpy.mock.calls.find((call) => String(call[0]).includes('/entities/E1'))!
    const body = JSON.parse((patchCall[1]?.body as string) ?? '{}')
    expect(body).toEqual({ secret: 'oh no' })
    expect('base_revision' in body).toBe(false)
  })

  it('updateEntity propagates the ApiError and does not refetch', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = requestUrl(input)
      if (url.includes('/entities/E1')) {
        return jsonResponse({ code: 'stale', message: 'stale base revision' }, 409)
      }
      if (url.includes('/export')) {
        exportCalls.push(url)
        return jsonResponse(worldExport())
      }
      if (url.includes('/api/jobs')) {
        return jsonResponse({ jobs: [], next_cursor: null })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
    const world = useWorldStore()
    await world.load('C1')
    expect(exportCalls).toHaveLength(1)

    await expect(world.updateEntity('C1', 'E1', { secret: 'x' }, 'stale')).rejects.toMatchObject({
      status: 409,
    })
    // No refetch after a rejected PATCH.
    expect(exportCalls).toHaveLength(1)
  })
})
