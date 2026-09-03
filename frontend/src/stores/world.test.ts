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
})
