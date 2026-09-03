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

/** The I/O-matrix HAPPY_PATH fixture: two kinds, counter + neutral + self-loop edges, stat block. */
function populatedWorld(): WorldExport {
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
    entities: [
      {
        id: 'E1',
        kind: 'character',
        name: 'Mira Vane',
        text: 'The barkeep.',
        data: {
          stat_block: {
            identity: { role: 'Barkeep', race: 'Human', level: 3, alignment: 'Neutral Good' },
            attributes: { str: 13, dex: 12, con: 14, int: 10, wis: 9, cha: 15 },
            combat: { ac: 16, hp: 44 },
            skills: [{ name: 'Persuasion', bonus: 5 }],
            actions: [{ name: 'Rapier', description: 'Melee attack.' }],
          },
        },
      },
      { id: 'E2', kind: 'place', name: 'The Gilded Bar', text: 'Tavern.', data: {} },
    ],
    edges: [
      { id: 'ED1', src: 'E1', dst: 'E2', type: 'debt', counter: 50 },
      { id: 'ED2', src: 'E2', dst: 'E1', type: 'located_in', counter: 0 },
      { id: 'ED3', src: 'E1', dst: 'E1', type: 'loyalty', counter: 7 },
    ],
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

  it('HAPPY_PATH: groups by kind with counts, renders counter + bare edge labels, self-loops once, and the stat block', async () => {
    apiFetchMock.mockResolvedValue(populatedWorld())
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()

    // Kind grouping in first-seen (rowid) order, with counts.
    expect(text).toContain('character (1)')
    expect(text).toContain('place (1)')
    expect(text.indexOf('character (1)')).toBeLessThan(text.indexOf('place (1)'))

    // Counter semantics: debt carries its amount, located_in renders bare.
    expect(text).toContain('Mira Vane --debt(50)--> The Gilded Bar')
    expect(text).toContain('The Gilded Bar --located_in--> Mira Vane')

    // A self-loop lands exactly once per endpoint.
    expect(text.match(/loyalty\(7\)/g)).toHaveLength(1)

    // Modifier arithmetic, combat line, and identity of the stat block.
    expect(text).toContain('STR13 (+1)')
    expect(text).toContain('AC 16 · HP 44 · Initiative +1')
    expect(text).toContain('Level 3')
    expect(text).toContain('Persuasion +5')
    expect(text).toContain('Rapier — Melee attack.')
    wrapper.unmount()
  })

  it('EMPTY_WORLD: no entities renders the empty state with the build-in CTA', async () => {
    const empty = worldExport()
    empty.entities = []
    apiFetchMock.mockResolvedValue(empty)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('This world is still empty — nothing has been built yet.')
    expect(wrapper.text()).toContain('Open build-in')
    wrapper.unmount()
  })

  it('ODD_DATA: null text gets a placeholder, junk stat_block renders nothing', async () => {
    const odd = worldExport()
    odd.entities = [
      { id: 'E1', kind: 'character', name: 'Mira Vane', text: null, data: {} },
      {
        id: 'E2',
        kind: 'place',
        name: 'The Gilded Bar',
        text: 'Tavern.',
        data: { stat_block: 'junk' },
      },
    ]
    apiFetchMock.mockResolvedValue(odd)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('No description.')
    expect(wrapper.text()).toContain('Tavern.')
    expect(wrapper.text()).not.toContain('Stat block')
    wrapper.unmount()
  })

  it('an initial 500 shows the error card without a socket; Retry recovers and opens exactly one', async () => {
    apiFetchMock.mockRejectedValueOnce(new ApiError(500, 'server_error', 'Database unavailable.'))
    apiFetchMock.mockResolvedValueOnce(worldExport())
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Database unavailable.')
    expect(socketCalls).toHaveLength(0)

    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Greymarch')
    expect(socketCalls).toHaveLength(1)
    wrapper.unmount()
  })
})
