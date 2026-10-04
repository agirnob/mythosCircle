// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useTonightStore } from './tonight'
import { invalidateSession } from '../api/session'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
})
const response = (body: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(body), { status })
const state = (notes: string) => ({ session: { E: { notes, defeated: true } }, knowledge: {} })

describe('Tonight notes action', () => {
  it('posts expected text, reports saved despite refresh failure and propagates POST failures', async () => {
    const fetcher = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(null, 204))
      .mockRejectedValue(new Error('Offline'))
    expect(await useTonightStore().saveNotes('C', 'E', 'text', 'old')).toEqual({ refreshed: false })
    expect(JSON.parse((fetcher.mock.calls[0]![1] as RequestInit).body as string)).toEqual({
      update: { notes: 'text' },
      expected_notes: 'old',
    })
    fetcher.mockResolvedValueOnce(response({ code: 'stale', message: 'Changed' }, 409))
    await expect(useTonightStore().saveNotes('C', 'E', 'draft', 'old')).rejects.toMatchObject({
      status: 409,
    })
  })

  it('does not allow an older Tonight load to overwrite a confirmed save projection', async () => {
    const pending: ((value: Response) => void)[] = []
    vi.spyOn(globalThis, 'fetch')
      .mockImplementationOnce(() => new Promise((resolve) => pending.push(resolve)))
      .mockImplementationOnce(() => new Promise((resolve) => pending.push(resolve)))
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(response(state('new')))
      .mockResolvedValueOnce(response({ revisions: [] }))
    const store = useTonightStore()
    const loading = store.fetchTonight('C')
    expect(await store.saveNotes('C', 'E', 'new', 'old')).toMatchObject({
      refreshed: true,
      savedText: 'new',
    })
    pending[0]!(response(state('old')))
    pending[1]!(response({ revisions: [] }))
    await loading
    expect(store.entry('C').runState).toEqual(state('new'))
  })

  it('ignores account changes during confirmed POST refresh', async () => {
    let resolve!: (value: Response) => void
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r
        }),
    )
    const store = useTonightStore()
    const saving = store.saveNotes('C', 'E', 'new', '')
    invalidateSession()
    resolve(response(null, 204))
    await expect(saving).rejects.toMatchObject({ name: 'Error' })
    expect(store.entry('C').runState).toBeNull()
  })
})

function deferredResponse() {
  let resolve!: (value: Response) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<Response>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
const feed = (id: string) => ({ revisions: [{ revision_id: id, created_at: '', events: [] }] })

it.each(['success', 'failure'])(
  'older save refresh %s cannot overwrite a newer consequence or its feed',
  async (result) => {
    const olderState = deferredResponse()
    const olderFeed = deferredResponse()
    const fetcher = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(null, 204))
      .mockReturnValueOnce(olderState.promise)
      .mockReturnValueOnce(olderFeed.promise)
      .mockResolvedValueOnce(response(null, 204))
      .mockResolvedValueOnce(
        response({
          session: { E: { notes: 'newer server edit', defeated: true, item: true } },
          knowledge: {},
        }),
      )
      .mockResolvedValueOnce(response(feed('consequence')))
    const store = useTonightStore()
    const saving = store.saveNotes('C', 'E', 'saved', '')
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3))
    await store.fireVerb('C', 'E', { item: true })
    if (result === 'success') {
      olderState.resolve(response(state('saved')))
      olderFeed.resolve(response(feed('old-note')))
    } else {
      olderState.reject(new Error('Old read failure'))
      olderFeed.resolve(response(feed('old-note')))
    }
    expect(await saving).toMatchObject({ refreshed: true, savedText: 'newer server edit' })
    expect(store.entry('C').runState?.session.E?.item).toBe(true)
    expect(store.entry('C').revisions?.[0]?.revision_id).toBe('consequence')
    expect(store.entry('C').error).toBeNull()
  },
)

it('replaces an invalidated initial load when a confirmed save refresh fails', async () => {
  const initialState = deferredResponse()
  const initialFeed = deferredResponse()
  const fetcher = vi
    .spyOn(globalThis, 'fetch')
    .mockReturnValueOnce(initialState.promise)
    .mockReturnValueOnce(initialFeed.promise)
    .mockResolvedValueOnce(response(null, 204))
    .mockRejectedValueOnce(new Error('Save refresh unavailable'))
    .mockResolvedValueOnce(response(feed('unusable-save-read')))
    .mockResolvedValueOnce(response(state('confirmed')))
    .mockResolvedValueOnce(response(feed('replacement')))
  const store = useTonightStore()
  const initial = store.fetchTonight('C')
  expect(await store.saveNotes('C', 'E', 'confirmed', '')).toEqual({ refreshed: false })
  expect(store.entry('C').dirty).toBe(true)
  initialState.resolve(response(state('stale')))
  initialFeed.resolve(response(feed('stale-initial')))
  await initial
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(7))
  await vi.waitFor(() => expect(store.entry('C').revisions?.[0]?.revision_id).toBe('replacement'))
  expect(store.entry('C').runState).toEqual(state('confirmed'))
  expect(store.entry('C').error).toBeNull()
})
