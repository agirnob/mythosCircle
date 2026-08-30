import { defineStore } from 'pinia'

import type { components } from '../api/schema'
import { ApiError, apiFetch } from '../api/client'

type Account = components['schemas']['AccountResponse']

export const useAuthStore = defineStore('auth', {
  state: () => ({
    account: null as Account | null,
    /** True once the router guard has resolved /api/auth/me this session. */
    hydrated: false,
  }),
  getters: {
    isAuthenticated: (state) => state.account !== null,
  },
  actions: {
    /** Resolve the session cookie to an account (single generic 401, AR29). */
    async hydrate() {
      try {
        this.account = await apiFetch<Account>('/api/auth/me')
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          this.account = null
        } else {
          throw error
        }
      } finally {
        this.hydrated = true
      }
    },
    async login(email: string, password: string) {
      this.account = await apiFetch<Account>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      })
    },
    async register(email: string, password: string) {
      this.account = await apiFetch<Account>('/api/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      })
    },
    async logout() {
      await apiFetch<void>('/api/auth/logout', { method: 'POST' })
      this.account = null
    },
  },
})
