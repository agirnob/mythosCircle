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

  // -------------------------------------------------------------------------
  // Inline relation editing (spec-3-4, FR9)
  // -------------------------------------------------------------------------

  it('add relation POSTs the edge and refetches the snapshot', async () => {
    apiFetchMock.mockResolvedValue(populatedWorld())
    const wrapper = mountView()
    await flushPromises()

    // Open the add form on Mira's card (the first entity card).
    const addButtons = wrapper.findAll('button').filter((b) => b.text() === 'Add relation')
    await addButtons[0].trigger('click')
    await flushPromises()

    // Shape the form: direction outbound, type debt, target E2, counter 5.
    const card = wrapper.findAll('article')[0]
    const direction = card.find('select[aria-label="Direction"]')
    const type = card.find('select[aria-label="Relation type"]')
    const target = card.find('select[aria-label="Target entity"]')
    const counter = card.find('input[aria-label="Counter"]')
    await direction.setValue('outbound')
    await type.setValue('debt')
    await target.setValue('E2')
    await counter.setValue('5')
    const callsBefore = apiFetchMock.mock.calls.length
    await card.find('form.add-relation').trigger('submit')
    await flushPromises()

    const post = apiFetchMock.mock.calls[callsBefore]!
    expect(String(post[0])).toBe('/api/campaigns/C1/edges')
    expect(JSON.parse(String(post[1]!.body))).toEqual({
      src: 'E1',
      dst: 'E2',
      type: 'debt',
      counter: 5,
    })
    // The commit lands back as a coalesced snapshot refetch.
    const refetch = apiFetchMock.mock.calls[callsBefore + 1]!
    expect(String(refetch[0])).toBe('/api/campaigns/C1/export')
    wrapper.unmount()
  })

  it('add relation posts inbound edges with the target as source', async () => {
    apiFetchMock.mockResolvedValue(populatedWorld())
    const wrapper = mountView()
    await flushPromises()

    const addButtons = wrapper.findAll('button').filter((b) => b.text() === 'Add relation')
    await addButtons[0].trigger('click')
    await flushPromises()

    const card = wrapper.findAll('article')[0]
    await card.find('select[aria-label="Direction"]').setValue('inbound')
    await card.find('select[aria-label="Target entity"]').setValue('E2')
    const callsBefore = apiFetchMock.mock.calls.length
    await card.find('form.add-relation').trigger('submit')
    await flushPromises()

    const body = JSON.parse(String(apiFetchMock.mock.calls[callsBefore]![1]!.body))
    expect(body.src).toBe('E2')
    expect(body.dst).toBe('E1')
    wrapper.unmount()
  })

  it('edit counter PATCHes the edge and refetches', async () => {
    apiFetchMock.mockResolvedValue(populatedWorld())
    const wrapper = mountView()
    await flushPromises()

    // Mira's card: the debt edge (counter-typed) carries an Edit button.
    const card = wrapper.findAll('article')[0]
    const editButtons = card.findAll('button').filter((b) => b.text() === 'Edit')
    expect(editButtons.length).toBeGreaterThan(0)
    await editButtons[0].trigger('click')
    await flushPromises()

    const counterInput = card.find('input[aria-label="Counter"]')
    await counterInput.setValue('77')
    const callsBefore = apiFetchMock.mock.calls.length
    await card
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]
      .trigger('click')
    await flushPromises()

    const patch = apiFetchMock.mock.calls[callsBefore]!
    expect(String(patch[0])).toBe('/api/campaigns/C1/edges/ED1')
    expect(patch[1]!.method).toBe('PATCH')
    expect(JSON.parse(String(patch[1]!.body))).toEqual({ counter: 77 })
    expect(String(apiFetchMock.mock.calls[callsBefore + 1]![0])).toBe('/api/campaigns/C1/export')
    wrapper.unmount()
  })

  it('neutral-typed edges render no Edit button; every edge has Delete', async () => {
    apiFetchMock.mockResolvedValue(populatedWorld())
    const wrapper = mountView()
    await flushPromises()

    const card = wrapper.findAll('article')[0]
    const text = card.text()
    // Mira's lines: debt (counter), located_in (neutral, inbound), loyalty self-loop.
    expect(text).toContain('Mira Vane --debt(50)--> The Gilded Bar')
    expect(text).toContain('The Gilded Bar --located_in--> Mira Vane')
    expect(text).toContain('Mira Vane --loyalty(7)--> Mira Vane')
    // located_in is neutral: its line has no Edit; all lines have Delete.
    const deleteButtons = card.findAll('button').filter((b) => b.text() === 'Delete')
    expect(deleteButtons.length).toBe(3)
    wrapper.unmount()
  })

  it('delete edge DELETEs and refetches', async () => {
    apiFetchMock.mockResolvedValue(populatedWorld())
    const wrapper = mountView()
    await flushPromises()

    const card = wrapper.findAll('article')[0]
    const callsBefore = apiFetchMock.mock.calls.length
    await card
      .findAll('button')
      .filter((b) => b.text() === 'Delete')[0]
      .trigger('click')
    await flushPromises()

    const del = apiFetchMock.mock.calls[callsBefore]!
    expect(String(del[0])).toBe('/api/campaigns/C1/edges/ED1')
    expect(del[1]!.method).toBe('DELETE')
    expect(String(apiFetchMock.mock.calls[callsBefore + 1]![0])).toBe('/api/campaigns/C1/export')
    wrapper.unmount()
  })

  it('a failed edge mutation renders the error inline and does not refetch', async () => {
    apiFetchMock.mockResolvedValue(populatedWorld())
    const wrapper = mountView()
    await flushPromises()

    const card = wrapper.findAll('article')[0]
    const callsBefore = apiFetchMock.mock.calls.length
    apiFetchMock.mockRejectedValueOnce(new ApiError(409, 'conflict', 'stale base revision'))
    await card
      .findAll('button')
      .filter((b) => b.text() === 'Delete')[0]
      .trigger('click')
    await flushPromises()

    expect(card.text()).toContain('stale base revision')
    // The failed mutation does not trigger a snapshot refetch.
    expect(apiFetchMock.mock.calls.length).toBe(callsBefore + 1)
    wrapper.unmount()
  })
})
