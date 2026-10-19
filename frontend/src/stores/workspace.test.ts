import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkspaceStore } from './workspace'
import { invalidateSession } from '../api/session'
import type { components } from '../api/schema'
type Snapshot = components['schemas']['WorkspaceResponse']
const node = {
  id: 'draft-1',
  kind: 'place' as const,
  name: 'Market',
  parent_id: null,
  faction_id: null,
  guidance: ' Keep this exact. ',
}
const snapshot = (version = 0): Snapshot => ({
  draft_version: version,
  world_version: null,
  draft: { nodes: [node] },
  receipts: [],
})
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
beforeEach(() => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
})
describe('persistent world workspace', () => {
  it('saves stable keys, placement and guidance using optimistic versions', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(snapshot()))
      .mockResolvedValueOnce(response(snapshot(1)))
    const store = useWorkspaceStore()
    await store.load('C1')
    store.entry('C1').nodes[0]!.name = 'New Market'
    store.edit('C1')
    await store.save('C1')
    const body = JSON.parse(String(fetch.mock.calls[1]![1]!.body))
    expect(body.expected_draft_version).toBe(0)
    expect(body.draft.nodes[0]).toEqual({ ...node, name: 'New Market' })
    expect(store.entry('C1').dirty).toBe(false)
  })
  it('keeps editable local work and its source version after a conflicting refresh', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(snapshot()))
      .mockResolvedValueOnce(response(snapshot(2)))
    const store = useWorkspaceStore()
    await store.load('C1')
    store.entry('C1').nodes[0]!.name = 'My edit'
    store.edit('C1')
    await store.load('C1')
    expect(store.entry('C1').nodes[0]!.name).toBe('My edit')
    expect(store.entry('C1').snapshot!.draft_version).toBe(0)
    expect(store.entry('C1').conflict).toBe(true)
  })
  it('cannot overwrite saved state with an older in-flight load', async () => {
    let release!: (value: Response) => void
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(snapshot()))
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            release = resolve
          }),
      )
      .mockResolvedValueOnce(response(snapshot(1)))
    const store = useWorkspaceStore()
    await store.load('C1')
    const loading = store.load('C1')
    store.entry('C1').nodes[0]!.name = 'New edit'
    store.edit('C1')
    await store.save('C1')
    release(response(snapshot()))
    await loading
    expect(store.entry('C1').snapshot!.draft_version).toBe(1)
    expect(store.entry('C1').nodes[0]!.name).toBe('New edit')
  })
  it('does not apply a stale planning suggestion or write any world endpoint', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(snapshot(2)))
    const store = useWorkspaceStore()
    await store.load('C1')
    store.entry('C1').proposal = { draft: { nodes: [] }, draftVersion: 1, worldVersion: null }
    await store.applyProposal('C1')
    expect(store.entry('C1').error).toContain('stale')
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(store.entry('C1').nodes).toHaveLength(1)
  })
  it('reuses a request identity when result delivery fails', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(snapshot()))
      .mockRejectedValueOnce(new Error('lost response'))
      .mockRejectedValueOnce(new Error('lost response'))
    const store = useWorkspaceStore()
    await store.load('C1')
    await store.submit('C1', 'generate')
    await store.submit('C1', 'generate')
    expect(fetch.mock.calls[1]![1]!.body).toBe(fetch.mock.calls[2]![1]!.body)
    expect(JSON.parse(String(fetch.mock.calls[1]![1]!.body)).request_id).toMatch(
      /^[0-9A-HJKMNP-TV-Z]{26}$/,
    )
  })
  it('fences loads across an account change', async () => {
    let release!: (value: Response) => void
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve
        }),
    )
    const store = useWorkspaceStore(),
      pending = store.load('C1')
    invalidateSession()
    store.$reset()
    release(response(snapshot()))
    await pending
    expect(store.byCampaign).toEqual({})
  })
})

it('reconciles completion without adopting another tab draft version over local edits', async () => {
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(response(snapshot()))
    .mockResolvedValueOnce(response(snapshot(5)))
    .mockResolvedValueOnce(response({ entities: [], edges: [] }))
  const store = useWorkspaceStore()
  await store.load('C1')
  store.entry('C1').nodes[0]!.name = 'My unsaved edit'
  store.edit('C1')
  await store.reconcile('C1', {
    campaign_id: 'C1',
    kind: 'expand_world',
    state: 'succeeded',
  } as components['schemas']['JobResponse'])
  expect(store.entry('C1').nodes[0]!.name).toBe('My unsaved edit')
  expect(store.entry('C1').snapshot!.draft_version).toBe(0)
  expect(store.entry('C1').conflict).toBe(true)
  expect(await store.save('C1')).toBe(false)
  expect(fetch.mock.calls.every(([, init]) => init?.method !== 'PUT')).toBe(true)
})

it('reports failed terminal refreshes as retryable instead of marking completion reconciled', async () => {
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
  expect(
    await useWorkspaceStore().reconcile('C1', {
      campaign_id: 'C1',
      kind: 'expand_world',
      state: 'succeeded',
    } as components['schemas']['JobResponse']),
  ).toBe(false)
})

it('drains edits made during an active save without a mounted view', async () => {
  let release!: (value: Response) => void
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(response(snapshot()))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve
        }),
    )
    .mockResolvedValueOnce(response(snapshot(2)))
  const store = useWorkspaceStore()
  await store.load('C1')
  store.entry('C1').nodes[0]!.name = 'First'
  store.edit('C1')
  const saving = store.save('C1')
  store.entry('C1').nodes[0]!.guidance = 'Later edits after navigation'
  store.edit('C1')
  const leaving = store.save('C1')
  release(response(snapshot(1)))
  await Promise.all([saving, leaving])
  const second = JSON.parse(String(fetch.mock.calls[2]![1]!.body))
  expect(second.expected_draft_version).toBe(1)
  expect(second.draft.nodes[0]).toMatchObject({
    name: 'First',
    guidance: 'Later edits after navigation',
  })
  expect(store.entry('C1').dirty).toBe(false)
})

it('cannot let a late save response overwrite a later forced server load', async () => {
  let release!: (value: Response) => void
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(response(snapshot()))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve
        }),
    )
    .mockResolvedValueOnce(
      response({ ...snapshot(5), draft: { nodes: [{ ...node, name: 'Other tab' }] } }),
    )
  const store = useWorkspaceStore()
  await store.load('C1')
  store.entry('C1').nodes[0]!.name = 'Local'
  store.edit('C1')
  const saving = store.save('C1')
  await store.load('C1', true)
  release(response(snapshot(1)))
  expect(await saving).toBe(false)
  expect(store.entry('C1').snapshot!.draft_version).toBe(5)
  expect(store.entry('C1').nodes[0]!.name).toBe('Other tab')
})

it('applies an exact planning proposal with source versions and remembers its resolved identity per owner', async () => {
  const storage = new Map<string, string>()
  const localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storage.set(key, value)
    },
  }
  vi.stubGlobal('localStorage', localStorage)
  const proposed = [node, { ...node, id: 'new-slot', name: '', guidance: ' A quiet market. ' }]
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(response(snapshot(3)))
    .mockResolvedValueOnce(response({ ...snapshot(4), draft: { nodes: proposed } }))
  const store = useWorkspaceStore()
  store.configureOwner('C1', 'owner-a')
  await store.load('C1')
  const job = {
    id: 'P1',
    campaign_id: 'C1',
    kind: 'plan_expansion',
    state: 'succeeded',
    payload: {},
    progress: 1,
    max_llm_calls: 8,
    max_media_calls: 0,
    error: null,
    created_at: '2026-10-19T00:00:00Z',
    started_at: null,
    finished_at: null,
    queue_position: null,
    result: { proposal: { nodes: proposed }, source_draft_version: 3, source_world_version: null },
  } as components['schemas']['JobResponse']
  await store.reconcile('C1', job)
  expect(store.entry('C1').nodes).toEqual([node])
  await store.applyProposal('C1')
  expect(JSON.parse(String(fetch.mock.calls[1]![1]!.body))).toEqual({
    expected_draft_version: 3,
    expected_world_version: null,
    draft: { nodes: proposed },
  })
  expect(store.entry('C1').proposal).toBeNull()
  store.$reset()
  store.configureOwner('C1', 'owner-a')
  await store.reconcile('C1', job)
  expect(store.entry('C1').proposal).toBeNull()
  store.configureOwner('C1', 'owner-b')
  await store.reconcile('C1', job)
  expect(store.entry('C1').proposal?.draft.nodes).toEqual(proposed)
  store.dismissProposal('C1')
  store.$reset()
  store.configureOwner('C1', 'owner-b')
  await store.reconcile('C1', job)
  expect(store.entry('C1').proposal).toBeNull()
  vi.unstubAllGlobals()
})
