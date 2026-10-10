import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from './auth'
import { useCampaignsStore } from './campaigns'
import { useCandidatesStore } from './candidates'
import { useJobsStore } from './jobs'
import { useWorldStore } from './world'
import { useTonightStore } from './tonight'
import { apiFetch, setUnauthorizedHandler } from '../api/client'
import { SessionChangedError } from '../api/session'
const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 })
function deferred() {
  let resolve!: (response: Response) => void
  const promise = new Promise<Response>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
describe('account isolation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })
  afterEach(() => {
    setUnauthorizedHandler(null)
  })
  function seed() {
    const auth = useAuthStore()
    auth.account = { id: 'A', email: 'a@example.com', is_admin: false }
    const stores = [
      useCampaignsStore(),
      useCandidatesStore(),
      useJobsStore(),
      useWorldStore(),
      useTonightStore(),
    ]
    for (const store of stores) {
      // Seed every state field so reset covers metadata as well as records.
      const initial = JSON.parse(JSON.stringify(store.$state))
      for (const key of Object.keys(initial)) {
        if (Array.isArray(initial[key])) initial[key] = [{ private: 'A' }]
        else if (typeof initial[key] === 'object' && initial[key] !== null)
          initial[key] = { private: 'A' }
        else if (initial[key] === null) initial[key] = 'private A'
        else if (typeof initial[key] === 'boolean') initial[key] = true
      }
      Object.assign(store.$state, initial)
    }
    return { auth, stores }
  }
  it.each(['logout', '401', 'account change'] as const)(
    'clears all private stores on %s',
    async (trigger) => {
      const { auth, stores } = seed()
      if (trigger === 'logout') {
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
        await auth.logout()
      } else if (trigger === '401') {
        setUnauthorizedHandler(() => auth.clearSession())
        vi.spyOn(globalThis, 'fetch').mockResolvedValue(
          new Response(JSON.stringify({ message: 'Session required' }), { status: 401 }),
        )
        await expect(apiFetch('/api/private')).rejects.toThrow('Session required')
      } else auth.account = { id: 'B', email: 'b@example.com', is_admin: false }
      expect(useCampaignsStore().campaigns).toEqual([])
      expect(useCampaignsStore().current).toBeNull()
      for (const store of stores.slice(1))
        expect(Object.values(store.$state).every((value) => Object.keys(value).length === 0)).toBe(
          true,
        )
    },
  )
  it('ignores late campaign, media, candidate, job and Tonight responses after switching accounts', async () => {
    const auth = useAuthStore()
    auth.account = { id: 'A', email: 'a@example.com', is_admin: false }
    const pending: ReturnType<typeof deferred>[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
      const request = deferred()
      pending.push(request)
      return request.promise
    })
    const actions = [
      useCampaignsStore().list(),
      useWorldStore().fetchMedia('C1'),
      useCandidatesStore().syncList('C1'),
      useJobsStore().syncList('C1'),
      useTonightStore().fetchTonight('C1'),
    ]
    const settled = Promise.allSettled(actions)
    auth.account = { id: 'B', email: 'b@example.com', is_admin: false }
    for (const request of pending)
      request.resolve(
        ok({
          campaigns: [{ id: 'secret' }],
          media: [{ id: 'secret' }],
          candidates: [{ id: 'secret' }],
          jobs: [{ id: 'secret' }],
          revisions: [],
          next_cursor: null,
        }),
      )
    await settled
    expect(useCampaignsStore().campaigns).toEqual([])
    expect(useWorldStore().mediaByCampaign).toEqual({})
    expect(useCandidatesStore().byId).toEqual({})
    expect(useJobsStore().byId).toEqual({})
    expect(useTonightStore().byCampaign).toEqual({})
  })
  it('does not schedule a dirty world refetch or let a late 401 log out the new account', async () => {
    const auth = useAuthStore()
    auth.account = { id: 'A', email: 'a@example.com', is_admin: false }
    const request = deferred()
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockReturnValue(request.promise)
    const handler = vi.fn(() => auth.clearSession())
    setUnauthorizedHandler(handler)
    const world = useWorldStore()
    const loading = world.load('C1')
    await world.load('C1')
    auth.account = { id: 'B', email: 'b@example.com', is_admin: false }
    request.resolve(new Response('{}', { status: 401 }))
    await loading
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(handler).not.toHaveBeenCalled()
    expect(auth.account?.id).toBe('B')
    expect(world.byCampaign).toEqual({})
  })
  it('does not invalidate a new login when the preceding logout request finishes', async () => {
    const auth = useAuthStore()
    auth.account = { id: 'A', email: 'a@example.com', is_admin: false }
    const logoutRequest = deferred(),
      loginRequest = deferred()
    vi.spyOn(globalThis, 'fetch')
      .mockReturnValueOnce(logoutRequest.promise)
      .mockReturnValueOnce(loginRequest.promise)
    const logout = auth.logout()
    const login = auth.login('b@example.com', 'password')
    logoutRequest.resolve(new Response(null, { status: 204 }))
    await logout
    loginRequest.resolve(ok({ id: 'B', email: 'b@example.com', is_admin: false }))
    await login
    expect(auth.account?.id).toBe('B')
  })

  it('cannot restore an account from a late hydrate after logout', async () => {
    const auth = useAuthStore()
    const request = deferred()
    vi.spyOn(globalThis, 'fetch')
      .mockReturnValueOnce(request.promise)
      .mockResolvedValue(new Response(null, { status: 204 }))
    const hydration = auth.hydrate()
    const assertion = expect(hydration).rejects.toBeInstanceOf(SessionChangedError)
    await auth.logout()
    request.resolve(ok({ id: 'A', email: 'a@example.com', is_admin: false }))
    await assertion
    expect(auth.account).toBeNull()
  })
})
