import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { ApiError } from '../api/client'
import { useAuthStore } from './auth'

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('hydrate resolves the session cookie to an account', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: '01A', email: 'dm@example.com' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const auth = useAuthStore()
    await auth.hydrate()
    expect(auth.account?.email).toBe('dm@example.com')
    expect(auth.isAuthenticated).toBe(true)
    // Session cookie rides along, no token storage.
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/me',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('hydrate clears the account on the generic 401 (AR29)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ code: 'unauthorized', message: 'Session required.' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const auth = useAuthStore()
    auth.account = { id: 'stale', email: 'x@example.com' }
    await auth.hydrate()
    expect(auth.account).toBeNull()
    expect(auth.hydrated).toBe(true)
  })

  it('hydrate surfaces non-401 errors', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ code: 'internal_error', message: 'Internal server error.' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const auth = useAuthStore()
    await expect(auth.hydrate()).rejects.toBeInstanceOf(ApiError)
  })

  it('login stores the returned account', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: '01A', email: 'dm@example.com' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const auth = useAuthStore()
    await auth.login('dm@example.com', 'password123')
    expect(auth.account?.id).toBe('01A')
  })

  it('logout clears the account', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
    const auth = useAuthStore()
    auth.account = { id: '01A', email: 'dm@example.com' }
    await auth.logout()
    expect(auth.account).toBeNull()
  })
})
