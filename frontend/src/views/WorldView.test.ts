// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
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
import { useJobsStore } from '../stores/jobs'
import WorldView from './WorldView.vue'

type Job = components['schemas']['JobResponse']

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
    entities: [
      { id: 'E1', kind: 'character', name: 'Mira Vane', text: 'The barkeep.', data: {}, media: [] },
    ],
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
        media: [],
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
      { id: 'E2', kind: 'place', name: 'The Gilded Bar', text: 'Tavern.', data: {}, media: [] },
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
    // Mount: the export snapshot PLUS the media manifest fetch (spec-4.1).
    expect(apiFetchMock).toHaveBeenCalledTimes(2)

    socketCalls[0]!.options?.onReconnect?.()
    await flushPromises()
    expect(apiFetchMock).toHaveBeenCalledTimes(3)
    expect(apiFetchMock.mock.calls[2]![0]).toBe('/api/campaigns/C1/export')
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
    // Deterministic choreography: the FIRST export succeeds; the media
    // fetch (spec-4.1) and every later export refetch fail with 500.
    let exportCalls = 0
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/export')) {
        exportCalls += 1
        if (exportCalls === 1) return worldExport()
        throw new ApiError(500, 'server_error', 'Database unavailable.')
      }
      if (url.includes('/media')) {
        throw new ApiError(500, 'server_error', 'Database unavailable.')
      }
      throw new Error(`unexpected fetch: ${url}`)
    })
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
      { id: 'E1', kind: 'character', name: 'Mira Vane', text: null, data: {}, media: [] },
      {
        id: 'E2',
        kind: 'place',
        name: 'The Gilded Bar',
        text: 'Tavern.',
        media: [],
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

  it('Regenerate posts a whole-entity regenerate job and surfaces it in the jobs store', async () => {
    const world = populatedWorld()
    world.entities[0] = {
      ...world.entities[0],
      data: {
        stat_block: world.entities[0].data['stat_block'],
        name: 'Mira Vane',
        role: 'NPC',
        personality: 'warm',
        secret: 's',
      },
    }
    apiFetchMock.mockResolvedValue(world)
    const wrapper = mountView()
    await flushPromises()

    const regen = wrapper.findAll('button').filter((b) => b.text() === 'Regenerate')[0]
    expect(regen).toBeDefined()
    await regen!.trigger('click')
    await flushPromises()

    const jobCall = apiFetchMock.mock.calls.find(
      (call) => String(call[0]) === '/api/jobs' || String(call[0]).includes('/api/jobs'),
    )
    expect(jobCall).toBeDefined()
    const body = JSON.parse((jobCall![1] as RequestInit).body as string)
    expect(body.kind).toBe('regenerate')
    expect(body.payload).toEqual({ target: { kind: 'entity', id: 'E1' } })
    // The world snapshot is untouched — regeneration stages, it does not commit.
    expect(apiFetchMock.mock.calls.some((call) => String(call[0]).includes('/export'))).toBe(true)
    wrapper.unmount()
  })

  it('hides Regenerate on non-AR24 entity cards', async () => {
    const mixed = populatedWorld()
    // E2 (The Gilded Bar) has no sectioned profile — no Regenerate button.
    apiFetchMock.mockResolvedValue(mixed)
    const wrapper = mountView()
    await flushPromises()
    const regen = wrapper.findAll('button').filter((b) => b.text() === 'Regenerate')
    // Mira (E1) has an AR24-shaped data record? No — the fixture data is a
    // stat_block only, so the button is hidden for BOTH cards here.
    expect(regen).toHaveLength(0)
    wrapper.unmount()
  })

  it('shows Regenerate on an AR24 entity card', async () => {
    const world = populatedWorld()
    world.entities[0] = {
      ...world.entities[0],
      data: {
        name: 'Mira Vane',
        role: 'NPC',
        personality: 'warm',
        secret: 's',
      },
    }
    apiFetchMock.mockResolvedValue(world)
    const wrapper = mountView()
    await flushPromises()
    const regen = wrapper.findAll('button').filter((b) => b.text() === 'Regenerate')
    expect(regen).toHaveLength(1)
    wrapper.unmount()
  })

  it('regenerate buttons disable while a roll is in flight', async () => {
    const world = populatedWorld()
    world.entities[0] = {
      ...world.entities[0],
      data: {
        name: 'Mira Vane',
        role: 'NPC',
        personality: 'warm',
        secret: 's',
      },
    }
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url === '/api/jobs' && init?.method === 'POST') {
        await gate
        return {
          id: 'RJ1',
          campaign_id: 'C1',
          kind: 'regenerate',
          state: 'queued',
          queue_position: 1,
        }
      }
      return world
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Regenerate')[0]
      .trigger('click')
    await flushPromises()
    // While the roll is in flight (regeneratingId set), the button shows
    // Regenerating… and is disabled.
    const inFlight = wrapper.findAll('button').filter((b) => b.text() === 'Regenerating…')[0]
    expect(inFlight).toBeDefined()
    expect(inFlight.attributes('disabled')).toBeDefined()
    release()
    await flushPromises()
    wrapper.unmount()
  })

  /** The spec-3.6 AR24 profile fixture: full sectioned record + unknown keys. */
  function ar24World(): WorldExport {
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
          media: [],
          data: {
            name: 'Mira Vane',
            role: 'NPC',
            level_cr: 'level 5',
            race_type: 'Human',
            class_profession: 'Barkeep',
            alignment: 'NG',
            personality: 'cold, exacting',
            secret: 'owes the Guild a debt',
            rumor: 'seen at the docks',
            party_hook: 'hires the party',
            appearance: 'gaunt, ink-stained fingers',
            background: 'ex-Guild scribe',
            goals: 'buy back her name',
            relationships: 'pays the Guild',
            voice_style: 'clipped',
            catchphrases: '"Fair price."',
            stat_block: {
              identity: { role: 'NPC', level: 5, race: 'Human', alignment: 'NG' },
              attributes: { str: 13, dex: 12, con: 14, int: 10, wis: 9, cha: 15 },
              combat: { ac: 16, hp: 44 },
            },
            world_integration: {
              reputation: 'the fixer of the docks',
              factions: 'The Guild',
              current_location: 'the Gilded Bar',
              reaction_matrix: 'buys drinks',
              on_defeat: 'flees',
            },
            // AR24 forward compat: unknown keys render in the additional block.
            notes: 'owes a favor to Old Wren',
            coin: 42,
          },
        },
      ],
      edges: [],
    }
  }

  it('renders the full AR24 profile plus the additional-data block', async () => {
    apiFetchMock.mockResolvedValue(ar24World())
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    for (const marker of [
      'Level/CR',
      'level 5',
      'Personality',
      'cold, exacting',
      'Secret',
      'World integration',
      'the fixer of the docks',
      'Additional data',
      'owes a favor to Old Wren',
    ]) {
      expect(text).toContain(marker)
    }
  })

  it('Edit profile sends only the changed fields plus base_revision and refetches', async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) return undefined // 204, no body
      if (url.includes('/export')) return ar24World()
      return { jobs: [], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    const editButton = wrapper.findAll('button').filter((b) => b.text() === 'Edit profile')[0]
    expect(editButton).toBeDefined()
    await editButton!.trigger('click')
    await flushPromises()
    const personality = wrapper.find('textarea[aria-label="Personality"]')
    await personality.setValue('rewritten by hand')
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]!
      .trigger('click')
    await flushPromises()
    const patchCall = apiFetchMock.mock.calls.find((call) =>
      String(call[0]).includes('/entities/E1'),
    )
    expect(patchCall).toBeDefined()
    const [path, init] = patchCall!
    expect(String(path)).toContain('/entities/E1')
    expect(init?.method).toBe('PATCH')
    expect(JSON.parse((init?.body as string) ?? '{}')).toEqual({
      personality: 'rewritten by hand',
      base_revision: '01JZZZZZZZZZZZZZZZZZZZZZZZ',
    })
    // The editor closes after a successful save (Edit profile re-appears).
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Edit profile').length).toBe(1)
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Save').length).toBe(0)
    // Snapshot refetch happened (the PATCH call plus a fresh export call).
    expect(
      apiFetchMock.mock.calls.filter((call) => String(call[0]).includes('/export')),
    ).toHaveLength(2)
  })

  it('sends the base_revision the editor was OPENED against, even after a mid-edit refetch', async () => {
    // AR4/NFR2: a WS-triggered refetch landing mid-edit moves the LIVE
    // snapshot's head; the PATCH must still carry the seed-time
    // base_revision so the backend's stale-base 409 stays reachable —
    // never the moved head (which would silently merge seed-time drafts
    // over a changed record).
    const movedHead = '01JZZZZZZZZZZZZZZZZZZZZZZX'
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) return undefined
      if (url.includes('/export')) return ar24World()
      return { jobs: [], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit profile')[0]!
      .trigger('click')
    await flushPromises()
    // A commit lands elsewhere; a terminal WS frame for an uncached job
    // triggers the snapshot refetch — the world head moves to `movedHead`.
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) return undefined
      if (url.includes('/export')) {
        return { ...ar24World(), revision: { id: movedHead, created_at: '2026-09-03T20:06:00Z' } }
      }
      return { jobs: [], next_cursor: null }
    })
    const socket = socketCalls[socketCalls.length - 1]
    socket.onMessage({ type: 'job_done', job_id: 'J9' })
    await flushPromises()
    expect(wrapper.text()).not.toContain('loading')
    // The drafts were seeded from the OLD head; save against the moved one.
    const personality = wrapper.find('textarea[aria-label="Personality"]')
    await personality.setValue('rewritten by hand')
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]!
      .trigger('click')
    await flushPromises()
    const patchCall = apiFetchMock.mock.calls.find((call) =>
      String(call[0]).includes('/entities/E1'),
    )
    expect(patchCall).toBeDefined()
    expect(JSON.parse((patchCall![1]?.body as string) ?? '{}')).toEqual({
      personality: 'rewritten by hand',
      base_revision: '01JZZZZZZZZZZZZZZZZZZZZZZZ', // the OPEN-time base, not movedHead
    })
  })

  it('a bare record without AR24 shape saves a text edit unconstrained', async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) return undefined
      if (url.includes('/export')) return populatedWorld() // E1 data has only stat_block
      return { jobs: [], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    const editButton = wrapper.findAll('button').filter((b) => b.text() === 'Edit profile')[0]
    await editButton!.trigger('click')
    await flushPromises()
    const textArea = wrapper.find('textarea[aria-label="Text"]')
    await textArea.setValue('The barkeep with a secret ledger.')
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]!
      .trigger('click')
    await flushPromises()
    const patchCall = apiFetchMock.mock.calls.find((call) =>
      String(call[0]).includes('/entities/E1'),
    )
    const body = JSON.parse((patchCall![1]?.body as string) ?? '{}')
    expect(body).toEqual({
      text: 'The barkeep with a secret ledger.',
      base_revision: '01JZZZZZZZZZZZZZZZZZZZZZZZ',
    })
  })

  it('a 409 renders the inline conflict notice with Reload/Discard and does not refetch', async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) {
        throw new ApiError(409, 'conflict', 'stale base revision')
      }
      if (url.includes('/export')) return ar24World()
      return { jobs: [], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit profile')[0]!
      .trigger('click')
    await flushPromises()
    const personality = wrapper.find('textarea[aria-label="Personality"]')
    await personality.setValue('conflicted edit')
    const exportCallsBefore = apiFetchMock.mock.calls.filter((call) =>
      String(call[0]).includes('/export'),
    ).length
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]!
      .trigger('click')
    await flushPromises()
    // Conflict notice with both escape buttons; NO refetch after the 409.
    expect(wrapper.text()).toContain('stale base revision')
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Reload').length).toBe(1)
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Discard').length).toBe(1)
    expect(
      apiFetchMock.mock.calls.filter((call) => String(call[0]).includes('/export')).length,
    ).toBe(exportCallsBefore)
  })

  it('Reload awaits the fresh snapshot, then closes the editor', async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) throw new ApiError(409, 'conflict', 'stale base revision')
      if (url.includes('/export')) return ar24World()
      return { jobs: [], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit profile')[0]!
      .trigger('click')
    await flushPromises()
    await wrapper.find('textarea[aria-label="Personality"]').setValue('conflicted edit')
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]!
      .trigger('click')
    await flushPromises()
    // Reload refetches and closes the editor (Edit profile visible again).
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Reload')[0]!
      .trigger('click')
    await flushPromises()
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Edit profile').length).toBe(1)
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Reload').length).toBe(0)
  })

  it('clearing the Text box sends text: null, not an empty string', async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) return undefined
      if (url.includes('/export')) return ar24World()
      return { jobs: [], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit profile')[0]!
      .trigger('click')
    await flushPromises()
    await wrapper.find('textarea[aria-label="Text"]').setValue('   ')
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]!
      .trigger('click')
    await flushPromises()
    const patchCall = apiFetchMock.mock.calls.find((call) =>
      String(call[0]).includes('/entities/E1'),
    )
    const body = JSON.parse((patchCall![1]?.body as string) ?? '{}')
    expect(body).toMatchObject({ text: null })
  })

  it('editing a boss-bearing record and switching role to NPC sends boss: null', async () => {
    const bossWorld = ar24World()
    bossWorld.entities[0].data = {
      ...bossWorld.entities[0].data,
      role: 'BBEG',
      boss: {
        lair_actions: 'villainous',
        legendary_actions: 'two per round',
        immunities: 'fire',
        vulnerabilities: 'radiant',
      },
    }
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) return undefined
      if (url.includes('/export')) return bossWorld
      return { jobs: [], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit profile')[0]!
      .trigger('click')
    await flushPromises()
    // Flip the role select to NPC (the only changed field).
    const roleSelect = wrapper.find('select[aria-label="Role"]')
    await roleSelect.setValue('NPC')
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]!
      .trigger('click')
    await flushPromises()
    const patchCall = apiFetchMock.mock.calls.find((call) =>
      String(call[0]).includes('/entities/E1'),
    )
    const body = JSON.parse((patchCall![1]?.body as string) ?? '{}')
    expect(body.role).toBe('NPC')
    expect(body.boss).toBe(null)
  })

  it('custom keys edit through the additional-data JSON box (added + removed)', async () => {
    const notesWorld = ar24World()
    notesWorld.entities[0].data = { ...notesWorld.entities[0].data, notes: 'keep an eye on Wren' }
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) return undefined
      if (url.includes('/export')) return notesWorld
      return { jobs: [], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit profile')[0]!
      .trigger('click')
    await flushPromises()
    const box = wrapper.find('textarea[aria-label="Additional data (JSON)"]')
    await box.setValue(
      JSON.stringify({ notes: 'updated note', new_marker: 'the turncloak' }, null, 2),
    )
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]!
      .trigger('click')
    await flushPromises()
    const patchCall = apiFetchMock.mock.calls.find((call) =>
      String(call[0]).includes('/entities/E1'),
    )
    const body = JSON.parse((patchCall![1]?.body as string) ?? '{}')
    expect(body.notes).toBe('updated note')
    expect(body.new_marker).toBe('the turncloak')
  })

  it('a bare card with only custom keys renders the additional-data block (no scalar gate)', async () => {
    const bareWorld = ar24World()
    bareWorld.entities[0].data = { location: 'Dockside', patron: 'Old Wren' }
    apiFetchMock.mockResolvedValue(bareWorld)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Additional data')
    expect(wrapper.text()).toContain('Dockside')
    expect(wrapper.text()).toContain('Old Wren')
  })

  it('a failed Reload keeps the editor and the conflict notice', async () => {
    let exportCalls = 0
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/entities/E1')) throw new ApiError(409, 'conflict', 'stale base revision')
      if (url.includes('/export')) {
        exportCalls += 1
        if (exportCalls === 1) return ar24World()
        throw new ApiError(500, 'server_error', 'Database unavailable.')
      }
      return { jobs: [], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit profile')[0]!
      .trigger('click')
    await flushPromises()
    await wrapper.find('textarea[aria-label="Personality"]').setValue('still typing')
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Save')[0]!
      .trigger('click')
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Reload')[0]!
      .trigger('click')
    await flushPromises()
    // Fetch failed: the editor stays open with the draft + conflict intact.
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Save').length).toBe(1)
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Reload').length).toBe(1)
    expect(
      (wrapper.find('textarea[aria-label="Personality"]').element as HTMLTextAreaElement).value,
    ).toBe('still typing')
  })

  // -------------------------------------------------------------------------
  // Portraits (spec-4.1, FR12/AD-10)
  // -------------------------------------------------------------------------

  const PORTRAIT_FILENAME = '01JZZZZZZZZZZZZZZZZZZZZZZX.png'

  function portraitWorld(appearance: unknown): WorldExport {
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
          media: [],
          text: 'The barkeep.',
          data: { appearance },
        },
      ],
      edges: [],
    }
  }

  function portraitMedia(entityId = 'E1'): { media: components['schemas']['MediaResponse'][] } {
    return {
      media: [
        {
          id: 'M1',
          campaign_id: 'C1',
          entity_id: entityId,
          filename: PORTRAIT_FILENAME,
          kind: 'image',
          created_at: '2026-09-06T10:00:00Z',
        },
      ],
    }
  }

  function imageJob(id: string, entityId: string, overrides: Partial<Job> = {}): Job {
    return {
      id,
      campaign_id: 'C1',
      kind: 'image',
      payload: { entity_id: entityId },
      state: 'queued',
      progress: 0,
      max_llm_calls: 64,
      max_media_calls: 8,
      error: null,
      result: null,
      created_at: '2026-09-06T10:00:00Z',
      started_at: null,
      finished_at: null,
      queue_position: 1,
      ...overrides,
    } as Job
  }

  /** Route mocks for the portrait surface: export + media + jobs POST. */
  function stubPortraitApi(
    world: WorldExport,
    media: { media: components['schemas']['MediaResponse'][] },
  ) {
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url.includes('/media')) return media
      if (url === '/api/jobs' && init?.method === 'POST') {
        return imageJob('JP1', 'E1', { state: 'queued', queue_position: 1 })
      }
      return world
    })
  }

  function generatePortraitButton(wrapper: VueWrapper) {
    return wrapper.findAll('button').filter((b) => b.text().startsWith('Generate portrait'))[0]
  }

  it('portrait: the latest manifest row renders as the card image with a visible re-generate button', async () => {
    stubPortraitApi(portraitWorld({ face: 'sharp features', body: 'lean' }), portraitMedia())
    const wrapper = mountView()
    await flushPromises()
    const img = wrapper.find('.portrait-img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe(`/api/campaigns/C1/media/E1/${PORTRAIT_FILENAME}`)
    expect(wrapper.text()).not.toContain('No portrait.')
    expect(generatePortraitButton(wrapper)).toBeDefined()
    wrapper.unmount()
  })

  it('portrait: an entity with an appearance but no media shows "No portrait" with an enabled button', async () => {
    stubPortraitApi(portraitWorld({ face: 'sharp features' }), { media: [] })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('No portrait.')
    const button = generatePortraitButton(wrapper)
    expect(button).toBeDefined()
    expect(button.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('portrait: an entity without an appearance gets a disabled button and a hint (enqueue gate mirrored)', async () => {
    stubPortraitApi(portraitWorld(''), { media: [] })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Add an appearance to generate a portrait.')
    const button = generatePortraitButton(wrapper)
    expect(button).toBeDefined()
    expect(button.attributes('disabled')).toBeDefined()
    // A whitespace-only string appearance is blank the same way.
    wrapper.unmount()

    stubPortraitApi(portraitWorld('   \n\t '), { media: [] })
    const wrapper2 = mountView()
    await flushPromises()
    expect(wrapper2.text()).toContain('Add an appearance to generate a portrait.')
    expect(generatePortraitButton(wrapper2).attributes('disabled')).toBeDefined()
    wrapper2.unmount()
  })

  it('portrait: a queued image job shows its queue position and disables the button while pending', async () => {
    stubPortraitApi(portraitWorld({ face: 'sharp' }), { media: [] })
    const jobs = useJobsStore()
    jobs.upsert(imageJob('JI1', 'E1', { state: 'queued', queue_position: 2 }))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Portrait queued — position 2')
    // The in-flight discipline swaps the button label and disables it.
    const button = wrapper
      .findAll('button')
      .filter((b) => b.text().startsWith('Portrait queued'))[0]
    expect(button).toBeDefined()
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('Generate portrait')
    wrapper.unmount()
  })

  it('portrait: a running image job shows the generating status', async () => {
    stubPortraitApi(portraitWorld({ face: 'sharp' }), { media: [] })
    const jobs = useJobsStore()
    jobs.upsert(imageJob('JI1', 'E1', { state: 'running', progress: 0.5 }))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Generating portrait…')
    wrapper.unmount()
  })

  it('portrait: a failed job shows the failure and re-enables the button (DM re-triggers)', async () => {
    stubPortraitApi(portraitWorld({ face: 'sharp' }), { media: [] })
    const jobs = useJobsStore()
    jobs.upsert(
      imageJob('JI1', 'E1', {
        state: 'failed',
        error: 'image generation failed: provider returned HTTP 502',
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain(
      'Portrait failed: image generation failed: provider returned HTTP 502',
    )
    const button = generatePortraitButton(wrapper)
    expect(button.attributes('disabled')).toBeUndefined() // failed releases the button
    wrapper.unmount()
  })

  it('portrait: a failed re-generation is visible even when an older portrait renders', async () => {
    stubPortraitApi(portraitWorld({ face: 'sharp' }), portraitMedia())
    const jobs = useJobsStore()
    jobs.upsert(
      imageJob('JI1', 'E1', {
        state: 'failed',
        error: 'image generation failed: provider returned HTTP 502',
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    // The old portrait still renders…
    expect(wrapper.find('.portrait-img').exists()).toBe(true)
    // …but the failed re-generation is NOT hidden behind it (acceptance
    // criterion 4: the DM sees the failure and re-triggers).
    expect(wrapper.text()).toContain(
      'Portrait failed: image generation failed: provider returned HTTP 502',
    )
    wrapper.unmount()
  })

  it('portrait: a stale enqueue error clears when the entity job reaches a terminal state', async () => {
    // Enqueue fails (network/4xx): the inline error renders.
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url.includes('/media')) return { media: [] }
      if (url === '/api/jobs' && init?.method === 'POST') {
        throw new ApiError(409, 'queue_full', 'pending jobs at the cap')
      }
      return portraitWorld({ face: 'sharp' })
    })
    const wrapper = mountView()
    await flushPromises()
    await generatePortraitButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('pending jobs at the cap')

    // A terminal image job arrives (e.g. the auto-enqueue from the accept
    // screen, or a later retry): the stale red text clears.
    const jobs = useJobsStore()
    jobs.upsert(imageJob('JI1', 'E1', { state: 'succeeded', result: { filename: 'x.png' } }))
    await flushPromises()
    expect(wrapper.text()).not.toContain('pending jobs at the cap')
    wrapper.unmount()
  })

  it('portrait: a later VIDEO row does not displace the portrait as the image src', async () => {
    const imageRow = portraitMedia().media[0]!
    const videoRow: components['schemas']['MediaResponse'] = {
      id: 'M2',
      campaign_id: 'C1',
      entity_id: 'E1',
      filename: '01JZZZZZZZZZZZZZZZZZZZZZZY.mp4',
      kind: 'video',
      created_at: '2026-09-06T11:00:00Z', // NEWER than the portrait row
    }
    stubPortraitApi(portraitWorld({ face: 'sharp' }), { media: [imageRow, videoRow] })
    const wrapper = mountView()
    await flushPromises()
    const img = wrapper.find('.portrait-img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe(`/api/campaigns/C1/media/E1/${PORTRAIT_FILENAME}`)
    wrapper.unmount()
  })

  it('portrait: a failed media-list fetch renders "Portrait list unavailable", not "No portrait."', async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/media')) {
        throw new ApiError(500, 'server_error', 'Database unavailable.')
      }
      return portraitWorld({ face: 'sharp' })
    })
    const wrapper = mountView()
    await flushPromises()
    // The failure is tracked: absence is NOT assumed from a broken list.
    expect(wrapper.text()).toContain('Portrait list unavailable.')
    expect(wrapper.text()).not.toContain('No portrait.')
    wrapper.unmount()
  })

  // -------------------------------------------------------------------------
  // Reveal video (spec-4.2, beta)
  // -------------------------------------------------------------------------

  const VIDEO_FILENAME = '01JZZZZZZZZZZZZZZZZZZZZZZY.mp4'

  function bossWorld(dataOverrides: Record<string, unknown> = {}): WorldExport {
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
          name: 'Vashka the Unmaker',
          text: 'The BBEG.',
          media: [],
          data: {
            name: 'Vashka the Unmaker',
            role: 'BBEG',
            appearance: { face: 'a mask of fused iron' },
            boss: { lair_actions: 'the walls breathe' },
            ...dataOverrides,
          },
        },
      ],
      edges: [],
    }
  }

  function videoMedia(): { media: components['schemas']['MediaResponse'][] } {
    return {
      media: [
        {
          id: 'M2',
          campaign_id: 'C1',
          entity_id: 'E1',
          filename: VIDEO_FILENAME,
          kind: 'video',
          created_at: '2026-09-06T11:00:00Z',
        },
      ],
    }
  }

  function videoJob(id: string, entityId: string, overrides: Partial<Job> = {}): Job {
    return { ...imageJob(id, entityId, overrides), kind: 'video' } as Job
  }

  /** Route mocks for the reveal-video surface: export + media + jobs POST. */
  function stubVideoApi(
    world: WorldExport,
    media: { media: components['schemas']['MediaResponse'][] },
  ) {
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url.includes('/media')) return media
      if (url === '/api/jobs' && init?.method === 'POST') {
        return videoJob('JV1', 'E1', { state: 'queued', queue_position: 1 })
      }
      return world
    })
  }

  /**
   * Spec-4.2/4.6: the reveal-video surface gains the two-phase prompt
   * flow — 'Draft reveal prompt' + editable textarea + 'Render reveal
   * video'. The render button is the render gate.
   */
  function revealVideoButton(wrapper: VueWrapper) {
    return wrapper.findAll('button').filter((b) => b.text().startsWith('Render reveal video'))[0]
  }

  function draftPromptButton(wrapper: VueWrapper) {
    return wrapper.findAll('button').filter((b) => b.text().startsWith('Draft reveal prompt'))[0]
  }

  function revealPromptTextarea(wrapper: VueWrapper) {
    return wrapper.find('textarea.reveal-prompt-textarea')
  }

  function promptDraftJob(
    id: string,
    entityId: string,
    prompt: string,
    overrides: Partial<Job> = {},
  ): Job {
    return {
      ...imageJob(id, entityId, overrides),
      kind: 'video_prompt',
      result: { entity_id: entityId, prompt },
    } as Job
  }

  it('reveal video: a boss-tier card shows the draft + render surface; a non-boss card shows none', async () => {
    stubVideoApi(bossWorld(), { media: [] })
    const wrapper = mountView()
    await flushPromises()
    expect(revealVideoButton(wrapper)).toBeDefined()
    // The valid-boss enabled state is a load-bearing pin: every other
    // video button test asserts disabled, so only this one catches a
    // hasVideoPrompt/videoPromptFor regression that permanently disables
    // the feature.
    expect(revealVideoButton(wrapper)?.attributes('disabled')).toBeUndefined()
    expect(draftPromptButton(wrapper)).toBeDefined()
    expect(revealPromptTextarea(wrapper).exists()).toBe(true)
    wrapper.unmount()

    stubVideoApi(bossWorld({ role: 'NPC' }), { media: [] })
    const wrapper2 = mountView()
    await flushPromises()
    expect(revealVideoButton(wrapper2)).toBeUndefined()
    expect(draftPromptButton(wrapper2)).toBeUndefined()
    expect(wrapper2.text()).not.toContain('Reveal video')
    wrapper2.unmount()
  })

  it('reveal video: clicking the button enqueues a video job for the entity', async () => {
    const bodies: Array<Record<string, unknown>> = []
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url === '/api/jobs' && init?.method === 'POST') {
        bodies.push(JSON.parse(String(init.body)))
        return videoJob('JV1', 'E1', { state: 'queued', queue_position: 1 })
      }
      if (url.includes('/media')) return { media: [] }
      if (url.includes('/jobs')) return { jobs: [], next_cursor: null }
      return bossWorld()
    })
    const wrapper = mountView()
    await flushPromises()
    await revealVideoButton(wrapper)!.trigger('click')
    await flushPromises()
    expect(bodies).toHaveLength(1)
    expect(bodies[0]).toMatchObject({
      campaign_id: 'C1',
      kind: 'video',
      payload: { entity_id: 'E1' },
    })
    wrapper.unmount()
  })

  it('reveal video: the latest video row renders inline in the card', async () => {
    stubVideoApi(bossWorld(), videoMedia())
    const wrapper = mountView()
    await flushPromises()
    const clip = wrapper.find('video.reveal-video-clip')
    expect(clip.exists()).toBe(true)
    expect(clip.attributes('src')).toBe(`/api/campaigns/C1/media/E1/${VIDEO_FILENAME}`)
    // ROLE_FLIP with an existing row is covered by the non-boss case: the
    // block (and the <video>) is v-if'd on the boss-tier role.
    wrapper.unmount()
  })

  it('reveal video: a boss card without a usable prompt gets a disabled render button, but can draft', async () => {
    stubVideoApi(bossWorld({ boss: { lair_actions: '   ' } }), { media: [] })
    const wrapper = mountView()
    await flushPromises()
    expect(revealVideoButton(wrapper)?.attributes('disabled')).toBeDefined()
    // The two-phase flow needs only a boss-tier role + appearance to
    // draft — the spec-4.6 point is that the DM drafts BEFORE a good boss
    // section exists, so the draft button stays live.
    expect(draftPromptButton(wrapper)?.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('reveal video: a boss card without an appearance gets the draft hint and no actions', async () => {
    stubVideoApi(bossWorld({ appearance: '   ' }), { media: [] })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Add an appearance to draft a reveal video prompt.')
    // No appearance -> no draft surface and no render (the backend gates
    // both on the source-frame description).
    expect(draftPromptButton(wrapper)).toBeUndefined()
    expect(revealPromptTextarea(wrapper).exists()).toBe(false)
    expect(revealVideoButton(wrapper)?.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('reveal video: a queued video job shows its queue position and disables the button', async () => {
    stubVideoApi(bossWorld(), { media: [] })
    const jobs = useJobsStore()
    jobs.upsert(videoJob('JV1', 'E1', { state: 'queued', queue_position: 2 }))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Reveal video queued — position 2')
    // The in-flight discipline swaps the button label and disables it.
    const button = wrapper
      .findAll('button')
      .filter((b) => b.text().startsWith('Reveal video queued'))[0]
    expect(button).toBeDefined()
    expect(button?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('Generate reveal video')
    wrapper.unmount()
  })

  it('reveal video: a running video job shows the generating status', async () => {
    stubVideoApi(bossWorld(), { media: [] })
    const jobs = useJobsStore()
    jobs.upsert(videoJob('JV1', 'E1', { state: 'running', progress: 0.5 }))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Generating reveal video…')
    wrapper.unmount()
  })

  it('reveal video: a failed job shows the failure and re-enables the button over an existing clip', async () => {
    stubVideoApi(bossWorld(), videoMedia())
    const jobs = useJobsStore()
    jobs.upsert(
      videoJob('JV1', 'E1', {
        state: 'failed',
        error: 'video generation failed: provider returned HTTP 502',
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    // The old clip still renders…
    expect(wrapper.find('video.reveal-video-clip').exists()).toBe(true)
    // …but the failed re-generation is NOT hidden behind it (the DM sees
    // the failure and re-triggers — acceptance criterion 3).
    expect(wrapper.text()).toContain(
      'Reveal video failed: video generation failed: provider returned HTTP 502',
    )
    expect(revealVideoButton(wrapper)?.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  // -----------------------------------------------------------------------
  // Spec-4.6 two-phase prompt flow
  // -----------------------------------------------------------------------

  it('reveal prompt: clicking Draft enqueues a video_prompt job for the entity', async () => {
    const bodies: Array<Record<string, unknown>> = []
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url === '/api/jobs' && init?.method === 'POST') {
        bodies.push(JSON.parse(String(init.body)))
        return promptDraftJob('JP1', 'E1', 'draft', { state: 'queued', queue_position: 1 })
      }
      if (url.includes('/media')) return { media: [] }
      if (url.includes('/jobs')) return { jobs: [], next_cursor: null }
      return bossWorld()
    })
    const wrapper = mountView()
    await flushPromises()
    await draftPromptButton(wrapper)!.trigger('click')
    await flushPromises()
    expect(bodies).toHaveLength(1)
    expect(bodies[0]).toMatchObject({
      campaign_id: 'C1',
      kind: 'video_prompt',
      payload: { entity_id: 'E1' },
    })
    wrapper.unmount()
  })

  it('reveal prompt: a succeeded draft prefills the editable textarea', async () => {
    stubVideoApi(bossWorld({ boss: {} }), { media: [] })
    const jobs = useJobsStore()
    const draft = 'For the target video, at 0.00 seconds…\n\nintegrated_multimodal_description: …'
    jobs.upsert(promptDraftJob('JP1', 'E1', draft, { state: 'succeeded' }))
    const wrapper = mountView()
    await flushPromises()
    const textarea = revealPromptTextarea(wrapper)
    expect(textarea.exists()).toBe(true)
    expect((textarea.element as HTMLTextAreaElement).value).toBe(draft)
    // A boss with NO boss section can now render — the draft IS the prompt
    // source; the old bbeg gate is relaxed by the supplied prompt.
    expect(revealVideoButton(wrapper)?.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('reveal prompt: a DM-edited prompt travels verbatim on render', async () => {
    const bodies: Array<Record<string, unknown>> = []
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url === '/api/jobs' && init?.method === 'POST') {
        bodies.push(JSON.parse(String(init.body)))
        return videoJob('JV1', 'E1', { state: 'queued', queue_position: 1 })
      }
      if (url.includes('/media')) return { media: [] }
      if (url.includes('/jobs')) return { jobs: [], next_cursor: null }
      return bossWorld()
    })
    const wrapper = mountView()
    await flushPromises()
    const textarea = revealPromptTextarea(wrapper)
    expect(textarea.exists()).toBe(true)
    await textarea.setValue('the DM edits until it is perfect: silence, dread, no dialogue')
    await flushPromises()
    await revealVideoButton(wrapper)!.trigger('click')
    await flushPromises()
    expect(bodies).toHaveLength(1)
    expect(bodies[0]).toMatchObject({
      campaign_id: 'C1',
      kind: 'video',
      payload: {
        entity_id: 'E1',
        prompt: 'the DM edits until it is perfect: silence, dread, no dialogue',
      },
    })
    wrapper.unmount()
  })

  it('reveal prompt: a failed draft shows the failure and releases the button', async () => {
    stubVideoApi(bossWorld(), { media: [] })
    const jobs = useJobsStore()
    jobs.upsert(
      promptDraftJob('JP1', 'E1', '', {
        state: 'failed',
        error: 'video prompt generation failed: provider returned HTTP 502',
        result: null,
      }),
    )
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain(
      'Reveal prompt failed: video prompt generation failed: provider returned HTTP 502',
    )
    expect(draftPromptButton(wrapper)?.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
  it('renders export anchors that link the entity/world export endpoints', async () => {
    apiFetchMock.mockResolvedValue(populatedWorld())
    const wrapper = mountView()
    await flushPromises()

    // World level: Markdown + styled HTML sheet.
    const worldHtml = wrapper.findAll('a').find((anchor) => anchor.text().trim() === 'Export HTML')
    expect(worldHtml?.attributes('href')).toBe('/api/campaigns/C1/export?format=html')
    expect(worldHtml?.attributes('download')).toBeDefined()
    const worldMd = wrapper
      .findAll('a')
      .find((anchor) => anchor.text().trim() === 'Export Markdown')
    expect(worldMd?.attributes('href')).toBe('/api/campaigns/C1/export?format=markdown')

    // Per entity: one Markdown + one sheet link for each committed entity.
    const entityHtml = wrapper
      .findAll('a')
      .find((anchor) => anchor.text().trim() === 'Sheet (HTML)')
    expect(entityHtml?.attributes('href')).toBe('/api/campaigns/C1/entities/E1/export?format=html')
    expect(entityHtml?.attributes('download')).toBeDefined()
    expect(
      wrapper
        .findAll('a')
        .filter((anchor) => anchor.text().trim() === 'Markdown')
        .map((anchor) => anchor.attributes('href')),
    ).toEqual([
      '/api/campaigns/C1/entities/E1/export?format=markdown',
      '/api/campaigns/C1/entities/E2/export?format=markdown',
    ])
    wrapper.unmount()
  })
  it('RECORD_IN_DATA: scalar profile fields never duplicate into Additional data', async () => {
    // Dogfood 2026-09-09: committed character data carries the full
    // record (generate parity), so data.name/role/personality/... rendered
    // once as profile rows AND again as an "Additional data" JSON dump.
    const world = worldExport()
    world.entities = [
      {
        id: 'E1',
        kind: 'character',
        name: 'Markov',
        text: 'The resolute leader.',
        media: [],
        data: {
          name: 'Markov',
          role: 'NPC',
          personality: 'Weary but indomitable.',
          secret: 'Carries a forbidden relic.',
          rumor: 'Was a general of the capital.',
          party_hook: 'Seeks volunteers.',
          level_cr: 'level 10',
          race_type: 'Human',
          class_profession: 'Paladin',
          alignment: 'LG',
          appearance: 'Towering, iron beard.',
          world_integration: {
            reputation: 'Highly respected.',
            factions: 'Heroes Guild',
            current_location: 'Town of Salem',
            reaction_matrix: 'C0: Neutral',
            on_defeat: 'Morale collapses.',
          },
        },
      },
    ]
    apiFetchMock.mockResolvedValue(world)
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('Weary but indomitable.')
    expect(text).not.toContain('Additional data')
    // The profile rows render exactly once — no JSON echo.
    expect(text.match(/Weary but indomitable\./g)).toHaveLength(1)
    wrapper.unmount()
  })

  it('WORLD_INTEGRATION: subfields render as labeled rows, not raw JSON', async () => {
    // Dogfood 2026-09-09: the block rendered as a pretty JSON dump.
    const world = worldExport()
    world.entities = [
      {
        id: 'E1',
        kind: 'character',
        name: 'Markov',
        text: 'The resolute leader.',
        media: [],
        data: {
          name: 'Markov',
          role: 'NPC',
          personality: 'Weary.',
          secret: 'A relic.',
          world_integration: {
            reputation: 'Highly respected.',
            reaction_matrix: 'C0: Neutral',
          },
        },
      },
    ]
    apiFetchMock.mockResolvedValue(world)
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('World integration')
    expect(text).toContain('Reputation')
    expect(text).toContain('Highly respected.')
    expect(text).toContain('Reaction matrix')
    expect(text).not.toContain('"reputation"')
    expect(text).not.toContain('"reaction_matrix"')
    wrapper.unmount()
  })

  it('REGEN_SCOPE: whole-character default plus one section picker per entity', async () => {
    // Dogfood 2026-09-09: WorldView only offered whole-entity regen —
    // the backend has owned per-section re-rolls since spec-3.5.
    const world = worldExport()
    world.entities = [
      {
        id: 'E1',
        kind: 'character',
        name: 'Markov',
        text: 'The resolute leader.',
        media: [],
        data: { name: 'Markov', role: 'NPC', personality: 'Weary.', secret: 'A relic.' },
      },
    ]
    apiFetchMock.mockResolvedValue(world)
    const wrapper = mountView()
    await flushPromises()
    const scope = wrapper.find('select[aria-label="Regenerate scope"]')
    expect(scope.exists()).toBe(true)
    const options = scope.findAll('option').map((option) => option.text())
    expect(options[0]).toBe('Whole character')
    expect(options).toContain('Personality')
    expect(options).toContain('World integration')
    expect(options).toContain('Stat block')
    // Identity anchor is NOT regenerable — no Name/Role options.
    expect(options).not.toContain('Name')
    expect(options).not.toContain('Role')
    wrapper.unmount()
  })
})
