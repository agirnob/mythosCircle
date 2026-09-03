// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../api/schema'

type WorldExport = components['schemas']['WorldExport']
type Account = components['schemas']['AccountResponse']

const { pushMock, socketCalls, apiFetchMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  socketCalls: [] as Array<{
    campaignId: string
    onMessage: (message: unknown) => void
    options:
      | {
          onReconnect?: () => void
          onAuthFailure?: () => void
        }
      | undefined
  }>,
  apiFetchMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ params: { id: 'C1' } }),
  useRouter: () => ({ push: pushMock }),
}))

vi.mock('../ws', () => ({
  connectJobSocket: vi.fn(
    (
      campaignId: string,
      onMessage: (message: unknown) => void,
      options?: { onReconnect?: () => void; onAuthFailure?: () => void },
    ) => {
      socketCalls.push({ campaignId, onMessage, options })
      return () => {}
    },
  ),
}))

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly code: string,
      message: string,
    ) {
      super(message)
      this.name = 'ApiError'
    }
  },
  apiFetch: apiFetchMock,
}))

import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import WorldView from './WorldView.vue'

function worldExport(): WorldExport {
  return {
    campaign: {
      id: 'C1',
      title: 'Greymarch',
      theme: 'frontier dread',
      description: '',
      custom_lore: '',
      created_at: '2026-09-03T20:00:00Z',
    },
    revision: { id: '01JZZZZZZZZZZZZZZZZZZZZZZZ', created_at: '2026-09-03T20:05:00Z' },
    entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: 'The barkeep.', data: {} }],
    edges: [],
  }
}

function mountView() {
  return mount(WorldView, {
    global: {
      stubs: { RouterLink: { template: '<a><slot /></a>' } },
    },
  })
}

describe('WorldView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    socketCalls.length = 0
  })

  it('shows the loading state while the fetch is pending, then the world', async () => {
    let release!: (value: WorldExport) => void
    const gate = new Promise<WorldExport>((resolve) => {
      release = resolve
    })
    apiFetchMock.mockImplementation(() => gate)

    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Loading the world…')
    // The socket waits for the snapshot, not for the world to be non-empty.
    expect(socketCalls).toHaveLength(0)

    release(worldExport())
    await flushPromises()
    expect(wrapper.text()).toContain('Greymarch')
    expect(wrapper.text()).toContain('Mira Vane')
    expect(wrapper.text()).toContain('Revision 01JZZZZZZZZZZZZZZZZZZZZZZZ')
    expect(socketCalls).toHaveLength(1)
    expect(socketCalls[0].campaignId).toBe('C1')
  })

  it('onReconnect triggers exactly one export refetch', async () => {
    apiFetchMock.mockResolvedValue(worldExport())
    const wrapper = mountView()
    await flushPromises()
    expect(apiFetchMock).toHaveBeenCalledTimes(1)

    socketCalls[0]!.options?.onReconnect?.()
    await flushPromises()
    expect(apiFetchMock).toHaveBeenCalledTimes(2)
    expect(apiFetchMock.mock.calls[1]![0]).toBe('/api/campaigns/C1/export')
    wrapper.unmount()
  })

  it('onAuthFailure clears the auth account and pushes the login route', async () => {
    apiFetchMock.mockResolvedValue(worldExport())
    const auth = useAuthStore()
    auth.account = { id: 'A1', email: 'dm@example.com' } as Account

    const wrapper = mountView()
    await flushPromises()
    expect(auth.isAuthenticated).toBe(true)

    socketCalls[0]!.options?.onAuthFailure?.()
    await flushPromises()
    expect(auth.account).toBe(null)
    expect(pushMock).toHaveBeenCalledWith({ name: 'login' })
    wrapper.unmount()
  })

  it('a foreign/unknown campaign renders not-found and never opens a socket', async () => {
    apiFetchMock.mockRejectedValue(new ApiError(404, 'not_found', 'Campaign not found.'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('World not found.')
    expect(socketCalls).toHaveLength(0)
    wrapper.unmount()
  })

  it('keeps the last synced world visible when a live refetch fails', async () => {
    apiFetchMock.mockResolvedValueOnce(worldExport())
    apiFetchMock.mockRejectedValueOnce(new ApiError(500, 'server_error', 'Database unavailable.'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Mira Vane')

    socketCalls[0]!.options?.onReconnect?.()
    await flushPromises()
    expect(wrapper.text()).toContain('Mira Vane')
    expect(wrapper.text()).toContain('Live sync failed — showing the last synced world.')
    wrapper.unmount()
  })
})
