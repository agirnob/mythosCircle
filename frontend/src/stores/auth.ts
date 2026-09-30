import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import type { components } from '../api/schema'
import { ApiError, apiFetch } from '../api/client'
import { invalidateSession } from '../api/session'
import { useCampaignsStore } from './campaigns'
import { useCandidatesStore } from './candidates'
import { useJobsStore } from './jobs'
import { useWorldStore } from './world'
import { useTonightStore } from './tonight'

type Account = components['schemas']['AccountResponse']
export const useAuthStore = defineStore('auth', () => {
  const account = ref<Account | null>(null)
  const hydrated = ref(false)
  const isAuthenticated = computed(() => account.value !== null)
  const privateStores = [
    useCampaignsStore(),
    useCandidatesStore(),
    useJobsStore(),
    useWorldStore(),
    useTonightStore(),
  ]
  watch(
    () => account.value?.id,
    () => {
      invalidateSession()
      for (const store of privateStores) store.$reset()
    },
    { flush: 'sync' },
  )
  function clearSession() {
    if (account.value) account.value = null
    else {
      invalidateSession()
      for (const store of privateStores) store.$reset()
    }
  }
  async function hydrate() {
    try {
      account.value = await apiFetch<Account>('/api/auth/me')
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) clearSession()
      else throw error
    } finally {
      hydrated.value = true
    }
  }
  async function login(email: string, password: string) {
    account.value = await apiFetch<Account>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
  }
  async function register(email: string, password: string) {
    account.value = await apiFetch<Account>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
  }
  async function logout() {
    // Hide private data immediately, before waiting for the server.
    clearSession()
    try {
      await apiFetch<void>('/api/auth/logout', { method: 'POST' })
    } catch {
      /* Best effort when the cookie is gone or the server is down. */
    }
  }
  return { account, hydrated, isAuthenticated, hydrate, login, register, logout, clearSession }
})
