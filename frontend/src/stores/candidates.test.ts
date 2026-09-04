import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../api/schema'
import { useCandidatesStore } from './candidates'

type Candidate = components['schemas']['CandidateResponse']

function candidate(id: string, overrides: Partial<Candidate> = {}): Candidate {
  return {
    id,
    campaign_id: 'C1',
    job_id: 'J1',
    kind: 'entity',
    status: 'proposed',
    payload: { name: 'Sable Rook', appearance: 'gaunt' },
    created_at: '2026-09-04T20:00:00Z',
    ...overrides,
  } as Candidate
}

function ok(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('candidates store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('proposed filters by campaign and status, ordered by id (rowid order)', () => {
    const store = useCandidatesStore()
    store.upsert(candidate('CA2', { created_at: '2026-09-04T21:00:00Z' }))
    store.upsert(candidate('CA1', { created_at: '2026-09-04T22:00:00Z' }))
    store.upsert(candidate('CA3', { campaign_id: 'C2' }))
    store.upsert(candidate('CA4', { status: 'accepted' }))
    // Id order, NOT created_at order — ULIDs are time-ordered, so id
    // order matches the wire's rowid order within a staging batch.
    expect(store.proposed('C1').map((row) => row.id)).toEqual(['CA1', 'CA2'])
  })

  it('syncList paginates until next_cursor is null', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('cursor=page2')) {
        return ok({ candidates: [candidate('CA3')], next_cursor: null })
      }
      return ok({ candidates: [candidate('CA1'), candidate('CA2')], next_cursor: 'page2' })
    })
    const store = useCandidatesStore()
    await store.syncList('C1')
    expect(Object.keys(store.byId)).toHaveLength(3)
    expect(fetchMock.mock.calls).toHaveLength(2)
  })

  it('syncList prunes server-discarded proposed rows but keeps settled rows', async () => {
    const store = useCandidatesStore()
    store.upsert(candidate('CA0')) // staged earlier; its job failed server-side
    store.upsert(candidate('CA9', { status: 'accepted' })) // settled audit-trail row
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      ok({ candidates: [candidate('CA1')], next_cursor: null }),
    )
    await store.syncList('C1')
    expect(store.byId['CA0']).toBeUndefined() // no ghost proposed card
    expect(store.byId['CA1']).toBeDefined()
    expect(store.byId['CA9']).toBeDefined() // settled rows are never pruned
    expect(store.proposed('C1').map((row) => row.id)).toEqual(['CA1'])
  })

  it('accept without a payload posts no body and settles the row', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(ok(candidate('CA1', { status: 'accepted' })))
    const store = useCandidatesStore()
    store.upsert(candidate('CA1'))
    const settled = await store.accept('C1', 'CA1')
    expect(settled.status).toBe('accepted')
    expect(store.proposed('C1')).toHaveLength(0)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/candidates/CA1/accept')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeUndefined()
  })

  it('accept with an edited payload posts {"payload": ...}', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(ok(candidate('CA1', { status: 'accepted' })))
    const store = useCandidatesStore()
    const edited = { name: 'Sable Rook', appearance: 'redone', edges: [{ endpoint: 'E1' }] }
    await store.accept('C1', 'CA1', edited)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/candidates/CA1/accept')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ payload: edited })
  })

  it('reject posts and settles the row', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(ok(candidate('CA1', { status: 'rejected' })))
    const store = useCandidatesStore()
    store.upsert(candidate('CA1'))
    await store.reject('C1', 'CA1')
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/candidates/CA1/reject')
    expect(init?.method).toBe('POST')
    expect(store.proposed('C1')).toHaveLength(0)
  })

  it('submitAsk posts a generate job with the ask payload', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        ok({ id: 'J1', campaign_id: 'C1', kind: 'generate', state: 'queued' }, 201),
      )
    const store = useCandidatesStore()
    await store.submitAsk('C1', 'a rival for Mira')
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/jobs')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({
      campaign_id: 'C1',
      kind: 'generate',
      payload: { ask: 'a rival for Mira' },
    })
  })
})
