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

const journalRow = {
  id: 'J',
  campaign_id: 'C',
  session_id: 'S',
  headline: 'Saved',
  context: '',
  references: [],
  position: 1024,
  version: 1,
  source_event_id: null,
  action_revision_id: null,
  corrected: false,
  created_at: '',
  updated_at: '',
}
it('applies confirmed journal saves before best-effort projection refresh and invalidates older reads', async () => {
  const pending = deferredResponse()
  const fetcher = vi
    .spyOn(globalThis, 'fetch')
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValueOnce(response(journalRow))
    .mockRejectedValue(new Error('Offline refresh'))
  const store = useTonightStore()
  const key = store.journalKey({ sessionId: 'S' })
  store.ensureEntry('C').journal[key] = { entries: [], nextCursor: null }
  const loading = store.fetchJournal('C', { sessionId: 'S' })
  expect(
    await store.saveJournal(
      'C',
      'S',
      { headline: 'Saved', context: '', references: [] },
      'retry-key',
    ),
  ).toEqual(journalRow)
  expect(JSON.parse((fetcher.mock.calls[1]![1] as RequestInit).body as string)).toMatchObject({
    request_key: 'retry-key',
    session_id: 'S',
  })
  pending.resolve(response({ entries: [], next_cursor: null }))
  await loading
  expect(store.entry('C').journal[key]!.entries).toEqual([journalRow])
})
it('isolates paginated journal projections by session and entity filter', async () => {
  const fetcher = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(response({ entries: [journalRow], next_cursor: 'cursor' }))
    .mockResolvedValueOnce(
      response({ entries: [{ ...journalRow, id: 'J2', position: 2048 }], next_cursor: null }),
    )
  const store = useTonightStore()
  await store.fetchJournal('C', { sessionId: 'S', entityId: 'E' })
  await store.fetchJournal('C', { sessionId: 'S', entityId: 'E' }, true)
  expect(fetcher.mock.calls[1]![0]).toContain('session_id=S&entity_id=E&cursor=cursor')
  expect(
    store
      .entry('C')
      .journal[store.journalKey({ sessionId: 'S', entityId: 'E' })]!.entries.map((row) => row.id),
  ).toEqual(['J', 'J2'])
  expect(store.entry('C').journal[store.journalKey({ sessionId: 'other' })]).toBeUndefined()
})
it('does not restore a journal response across an account reset', async () => {
  const pending = deferredResponse()
  vi.spyOn(globalThis, 'fetch').mockReturnValue(pending.promise)
  const store = useTonightStore()
  const loading = store.fetchJournal('C')
  invalidateSession()
  store.$reset()
  pending.resolve(response({ entries: [journalRow], next_cursor: null }))
  await loading
  expect(store.byCampaign).toEqual({})
})
it('does not let an older session read overwrite a confirmed activation', async () => {
  const pending = deferredResponse()
  vi.spyOn(globalThis, 'fetch')
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValueOnce(response({ active_session_id: 'S2' }))
  const store = useTonightStore()
  const reading = store.fetchSessions('C')
  await store.activateSession('C', 'S2')
  pending.resolve(response({ sessions: [], active_session_id: 'S1', next_cursor: null }))
  await reading
  expect(store.entry('C').activeSessionId).toBe('S2')
})

it('does not let an earlier activation response overwrite a later session choice', async () => {
  const earlier = deferredResponse()
  const later = deferredResponse()
  vi.spyOn(globalThis, 'fetch')
    .mockReturnValueOnce(earlier.promise)
    .mockReturnValueOnce(later.promise)
  const store = useTonightStore()
  const first = store.activateSession('C', 'S1')
  const second = store.activateSession('C', 'S2')
  later.resolve(response({ active_session_id: 'S2' }))
  await second
  earlier.resolve(response({ active_session_id: 'S1' }))
  await first
  expect(store.entry('C').activeSessionId).toBe('S2')
})

it('refreshes loaded extent and walks bounded pages through a confirmed target beyond page one', async () => {
  const store = useTonightStore()
  const rows = Array.from({ length: 301 }, (_, index) => ({
    ...journalRow,
    id: `J${index}`,
    position: (index + 1) * 1024,
    version: 2,
  }))
  const key = store.journalKey({ sessionId: 'S' })
  store.ensureEntry('C').journal[key] = {
    entries: [...rows.slice(0, 150).map((row) => ({ ...row, version: 1 })), rows[300]!],
    nextCursor: '150',
  }
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const offset = Number(
      new URL(input as string, 'https://test').searchParams.get('cursor') ?? '0',
    )
    return response({
      entries: rows.slice(offset, offset + 100),
      next_cursor: offset + 100 < rows.length ? String(offset + 100) : null,
    })
  })
  await store.fetchJournal('C', { sessionId: 'S' }, false, 'J300')
  expect(fetcher).toHaveBeenCalledTimes(4)
  expect(store.entry('C').journal[key]!.entries).toEqual(rows)
  expect(store.entry('C').journal[key]!.nextCursor).toBeNull()
})

it('journal mutations do not invalidate an unrelated session read and errors stay independent', async () => {
  const store = useTonightStore()
  const pending = deferredResponse()
  vi.spyOn(globalThis, 'fetch')
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValueOnce(response({ entries: [], next_cursor: null }))
  const reading = store.fetchSessions('C')
  store.ensureEntry('C').sessionsError = 'Earlier session failure'
  store.ensureEntry('C').journalError = 'Earlier journal failure'
  store.applyJournalEntry('C', journalRow)
  await store.fetchJournal('C')
  expect(store.entry('C').sessionsError).toBe('Earlier session failure')
  pending.resolve(response({ sessions: [], active_session_id: 'S', next_cursor: null }))
  await reading
  expect(store.entry('C').activeSessionId).toBe('S')
  expect(store.entry('C').sessionsError).toBeNull()
  store.ensureEntry('C').journalError = 'Journal still failed'
  vi.mocked(globalThis.fetch).mockResolvedValueOnce(
    response({ sessions: [], active_session_id: 'S', next_cursor: null }),
  )
  await store.fetchSessions('C')
  expect(store.entry('C').journalError).toBe('Journal still failed')
})

it('coalesces older Changes requests and ignores their stale response after a fresh feed', async () => {
  const store = useTonightStore()
  const pending = deferredResponse()
  const fetcher = vi
    .spyOn(globalThis, 'fetch')
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValueOnce(response({ session: {}, knowledge: {} }))
    .mockResolvedValueOnce(response({ ...feed('fresh'), next_cursor: 'fresh-cursor' }))
  store.ensureEntry('C').revisionsCursor = 'old-cursor'
  const reading = store.moreChanges('C')
  await store.moreChanges('C')
  expect(fetcher).toHaveBeenCalledTimes(1)
  await store.refreshProjections('C')
  pending.resolve(response({ ...feed('stale'), next_cursor: 'stale-cursor' }))
  await reading
  expect(store.entry('C').revisions?.map((row) => row.revision_id)).toEqual(['fresh'])
  expect(store.entry('C').revisionsCursor).toBe('fresh-cursor')
  expect(store.entry('C').changesLoading).toBe(false)
})

it('exposes retryable older Changes failures and releases loading', async () => {
  const store = useTonightStore()
  store.ensureEntry('C').revisionsCursor = 'cursor'
  vi.spyOn(globalThis, 'fetch')
    .mockRejectedValueOnce(new Error('Older changes offline'))
    .mockResolvedValueOnce(response({ ...feed('older'), next_cursor: null }))
  await store.moreChanges('C')
  expect(store.entry('C').changesError).toBe('Older changes offline')
  expect(store.entry('C').changesLoading).toBe(false)
  await store.moreChanges('C')
  expect(store.entry('C').changesError).toBeNull()
  expect(store.entry('C').revisions?.[0]?.revision_id).toBe('older')
})
