// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'

import router from './router'

// The real route table: the WorldView link and the CandidatesView route
// are wired by name — this pins the path the name resolves to.
describe('router', () => {
  it('resolves the candidates route under its campaign', () => {
    expect(router.resolve({ name: 'candidates', params: { id: 'X' } }).path).toBe(
      '/campaigns/X/candidates',
    )
  })

  it('resolves the world route under its campaign', () => {
    expect(router.resolve({ name: 'world', params: { id: 'X' } }).path).toBe('/campaigns/X/world')
  })

  it('resolves the graph route under its campaign (story 7.1 entry point)', () => {
    const resolved = router.resolve({ name: 'graph', params: { id: 'X' }, query: { focus: 'E1' } })
    expect(resolved.path).toBe('/campaigns/X/graph')
    expect(resolved.query.focus).toBe('E1')
    expect(resolved.meta.requiresAuth).toBe(true)
  })

  it('resolves the overview route under its campaign (rebuild stage 2)', () => {
    expect(router.resolve({ name: 'overview', params: { id: 'X' } }).path).toBe(
      '/campaigns/X/overview',
    )
  })

  it('resolves the entity detail route under its campaign (rebuild stage 3)', () => {
    const resolved = router.resolve({ name: 'entity', params: { id: 'X', entityId: 'E1' } })
    expect(resolved.path).toBe('/campaigns/X/entities/E1')
    expect(resolved.meta.requiresAuth).toBe(true)
  })

  it('resolves the ask route under its campaign (rebuild stage 4)', () => {
    const resolved = router.resolve({ name: 'ask', params: { id: 'X' } })
    expect(resolved.path).toBe('/campaigns/X/ask')
    expect(resolved.meta.requiresAuth).toBe(true)
  })
})

// Exercise the registered guards with real navigation, including fresh grants.
import { beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAuthStore } from './stores/auth'

describe('administrator navigation authorization', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })
  afterEach(() => vi.restoreAllMocks())
  it('denies anonymous entry independently of cached hydration', async () => {
    useAuthStore().hydrated = true
    await router.push('/admin')
    expect(router.currentRoute.value.name).toBe('login')
  })
  it('refreshes cached grants and redirects ordinary users with the fixed notice', async () => {
    const auth = useAuthStore()
    auth.hydrated = true
    auth.account = { id: 'A', email: 'a@example.com', is_admin: true }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          id: 'A',
          email: 'a@example.com',
          is_admin: false,
        }),
      ),
    )
    await router.push('/admin')
    expect(router.currentRoute.value.name).toBe('campaigns')
    expect(router.currentRoute.value.query.notice).toBe('admin-access-denied')
    expect(auth.isAdmin).toBe(false)
  })
  it('allows fresh administrators and rechecks query-only navigation without discarding capability', async () => {
    const auth = useAuthStore()
    auth.hydrated = true
    auth.account = { id: 'A', email: 'a@example.com', is_admin: true }
    const deny = vi.spyOn(auth, 'denyAdmin')
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            id: 'A',
            email: 'a@example.com',
            is_admin: true,
          }),
        ),
    )
    await router.push('/admin')
    expect(router.currentRoute.value.name).toBe('admin')
    await router.push('/admin?q=example')
    expect(router.currentRoute.value.name).toBe('admin')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(deny).not.toHaveBeenCalled()
  })
  it('fails closed when fresh capability cannot be fetched', async () => {
    const auth = useAuthStore()
    auth.hydrated = true
    auth.account = { id: 'A', email: 'a@example.com', is_admin: true }
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    await router.push('/admin?fresh=failed')
    expect(router.currentRoute.value.name).toBe('campaigns')
    expect(auth.isAdmin).toBe(false)
  })
})
