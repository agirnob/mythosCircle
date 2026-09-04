// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../api/schema'

type Candidate = components['schemas']['CandidateResponse']

const { pushMock, apiFetchMock, socketCalls } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  apiFetchMock: vi.fn(),
  socketCalls: [] as Array<{
    campaignId: string
    onMessage: (message: unknown) => void
  }>,
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ params: { id: 'C1' } }),
  useRouter: () => ({ push: pushMock }),
}))

vi.mock('../ws', () => ({
  connectJobSocket: vi.fn((campaignId: string, onMessage: (message: unknown) => void) => {
    socketCalls.push({ campaignId, onMessage })
    return () => {}
  }),
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
import CandidatesView from './CandidatesView.vue'

const SABLE: Candidate = {
  id: 'CA1',
  campaign_id: 'C1',
  job_id: 'J1',
  kind: 'entity',
  status: 'proposed',
  payload: {
    name: 'Sable Rook',
    role: 'NPC',
    personality: 'dry, watchful',
    secret: 'owes the Guild a debt',
    rumor: 'seen at the docks at night',
    party_hook: 'hires the party to guard a shipment',
    level_cr: 'level 5',
    race_type: 'Human',
    class_profession: 'Fence',
    alignment: 'NE',
    appearance: 'gaunt, ink-stained fingers',
    background: 'ex-Guild scribe',
    goals: 'buy back her name',
    relationships: 'pays protection to the Gilded Bar',
    voice_style: 'clipped, low',
    catchphrases: '"Everything has a price."',
    stat_block: {
      identity: { role: 'NPC', level: 5, race: 'Human', alignment: 'NE' },
      attributes: { str: 14, dex: 12, con: 14, int: 10, wis: 10, cha: 8 },
      combat: { ac: 16, hp: 44 },
    },
    world_integration: {
      reputation: 'the fixer of the docks',
      factions: 'The Guild',
      current_location: 'the Gilded Bar',
      reaction_matrix: 'buys drinks, sells favors',
      on_defeat: 'flees, leaving the ledger behind',
    },
    edges: [{ endpoint: 'E1', direction: 'outbound', type: 'rival_of', counter: 1 }],
    // AR24 forward compatibility: an unknown section is skipped, never rendered.
    ambient_detail: { cloak: 'grey' },
  },
  created_at: '2026-09-04T20:00:00Z',
}

const GENERATE_JOB = {
  id: 'J9',
  campaign_id: 'C1',
  kind: 'generate',
  state: 'running',
  progress: 0.5,
  max_llm_calls: 64,
  max_media_calls: 8,
  error: null,
  result: null,
  created_at: '2026-09-04T20:01:00Z',
  started_at: '2026-09-04T20:01:00Z',
  finished_at: null,
  queue_position: null,
}

function accepted(candidate: Candidate): Candidate {
  return { ...candidate, status: 'accepted' }
}

function mountView() {
  return mount(CandidatesView, {
    global: {
      stubs: { RouterLink: { template: '<a><slot /></a>' } },
    },
  })
}

describe('CandidatesView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    socketCalls.length = 0
  })

  function stubApi(options: { list?: Candidate[]; failAcceptWith?: string } = {}) {
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (options.failAcceptWith !== undefined && url.includes('/accept')) {
        throw new ApiError(422, 'validation_error', options.failAcceptWith)
      }
      if (url.includes('/accept')) {
        return accepted(options.list?.[0] ?? SABLE)
      }
      if (url.includes('/reject')) return { ...SABLE, status: 'rejected' }
      if (url.includes('/candidates')) {
        return { candidates: options.list ?? [SABLE], next_cursor: null }
      }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: '2026-09-04T19:00:00Z',
          },
          revision: { id: '01JZZZZZZZZZZZZZZZZZZZZZZZ', created_at: '2026-09-04T19:05:00Z' },
          entities: [
            { id: 'E1', kind: 'character', name: 'Mira Vane', text: 'The barkeep.', data: {} },
          ],
          edges: [],
        }
      }
      if (init?.method === 'POST') return { ...GENERATE_JOB, state: 'queued', progress: 0 }
      return { jobs: [GENERATE_JOB], next_cursor: null }
    })
  }

  it('renders the full AR24 sectioned profile of each proposed candidate', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    for (const marker of [
      'Sable Rook',
      'Appearance',
      'gaunt, ink-stained fingers',
      'Personality',
      'Voice style',
      'clipped, low',
      'Secret',
      'owes the Guild a debt',
      'Level / CR',
      'World integration',
      'the fixer of the docks',
      'Stat block',
    ]) {
      expect(text).toContain(marker)
    }
    // Relations: staged edges are read-only relation lines (WorldView
    // convention); the counter renders whenever the edge carries one.
    expect(text).toContain('Sable Rook --rival_of(1)--> Mira Vane')
    // Unknown sections are skipped silently.
    expect(text).not.toContain('ambient_detail')
    expect(text).not.toContain('cloak')
  })

  it('shows the empty state with the ask box when nothing is proposed', async () => {
    stubApi({ list: [] })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('No candidates waiting')
    const textarea = wrapper.find('textarea')
    await textarea.setValue('a rival for Mira')
    await wrapper.find('form button').trigger('submit')
    await flushPromises()
    const jobCall = apiFetchMock.mock.calls.find((call) => String(call[0]) === '/api/jobs')
    expect(jobCall).toBeDefined()
    const jobInit = jobCall![1] as RequestInit
    expect(jobInit.method).toBe('POST')
    expect(JSON.parse(jobInit.body as string)).toEqual({
      campaign_id: 'C1',
      kind: 'generate',
      payload: { ask: 'a rival for Mira' },
    })
  })

  it('keeps the ask text on submit and clears it only when the generate job succeeds', async () => {
    stubApi({ list: [] })
    const wrapper = mountView()
    await flushPromises()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('a rival for Mira')
    await wrapper.find('form button').trigger('submit')
    await flushPromises()
    // Still there — a failed job must not cost the DM a retyping.
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('a rival for Mira')
    // A job_done for the generate job clears it.
    socketCalls[0].onMessage({ type: 'job_done', job_id: 'J9', state: 'succeeded' })
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('accept without edits posts no body (3.2 behavior unchanged)', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    const acceptButton = wrapper.findAll('button').filter((b) => b.text() === 'Accept')[0]
    expect(acceptButton).toBeDefined()
    await acceptButton!.trigger('click')
    await flushPromises()
    const acceptCall = apiFetchMock.mock.calls.find((call) => String(call[0]).includes('/accept'))
    expect(acceptCall).toBeDefined()
    const [path, init] = acceptCall!
    expect(String(path)).toContain('/candidates/CA1/accept')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeUndefined()
    // The accepted row leaves the proposed list.
    expect(wrapper.text()).not.toContain('Sable Rook --rival_of(1)--> Mira Vane')
  })

  it('a failed accept keeps the row and renders the error on the card', async () => {
    stubApi({ failAcceptWith: 'payload override must carry the staged edges verbatim' })
    const wrapper = mountView()
    await flushPromises()
    const acceptButton = wrapper.findAll('button').filter((b) => b.text() === 'Accept')[0]
    await acceptButton!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('payload override must carry the staged edges verbatim')
    // The row stays on the accept screen.
    expect(wrapper.text()).toContain('gaunt, ink-stained fingers')
  })

  it('edit toggles per-section textareas with labels; accept sends the edited payload with edges verbatim', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    const editButton = wrapper.findAll('button').filter((b) => b.text() === 'Edit')[0]
    await editButton!.trigger('click')
    await flushPromises()
    const appearanceArea = wrapper
      .findAll('textarea')
      .find((t) => (t.element as HTMLTextAreaElement).value === 'gaunt, ink-stained fingers')
    expect(appearanceArea).toBeDefined()
    // Every section textarea is programmatically labeled.
    for (const area of wrapper.findAll('textarea')) {
      const label = area.attributes('aria-label') ?? area.attributes('aria-labelledby')
      expect(label).toBeTruthy()
    }
    await appearanceArea!.setValue('redone by hand')
    // Reject / Edit / Accept remain one tap each while editing.
    const buttons = wrapper.findAll('button').map((b) => b.text())
    expect(buttons).toContain('Reject')
    expect(buttons).toContain('Cancel edit')
    expect(buttons).toContain('Accept edited')
    const acceptEdited = wrapper.findAll('button').filter((b) => b.text() === 'Accept edited')[0]
    await acceptEdited!.trigger('click')
    await flushPromises()
    const acceptCall = apiFetchMock.mock.calls.find((call) => String(call[0]).includes('/accept'))
    expect(acceptCall).toBeDefined()
    const [path, init] = acceptCall!
    expect(String(path)).toContain('/candidates/CA1/accept')
    const body = JSON.parse((init?.body as string) ?? '{}')
    expect(body.payload.appearance).toBe('redone by hand')
    expect(body.payload.goals).toBe('buy back her name') // unedited sections pass through
    expect(body.payload.edges).toEqual(SABLE.payload.edges) // staged edges verbatim
  })

  it('a payload swap mid-edit visibly discards the draft instead of merging silently', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    const editButton = wrapper.findAll('button').filter((b) => b.text() === 'Edit')[0]
    await editButton!.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('textarea').length).toBeGreaterThan(0)

    // The server-side payload changes (regeneration / a new sync wave)…
    const swapped: Candidate = {
      ...SABLE,
      payload: { ...SABLE.payload, appearance: 'replaced by a fresh wave' },
    }
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/candidates')) {
        return { candidates: [swapped], next_cursor: null }
      }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: '2026-09-04T19:00:00Z',
          },
          revision: { id: '01JZZZZZZZZZZZZZZZZZZZZZZZ', created_at: '2026-09-04T19:05:00Z' },
          entities: [
            { id: 'E1', kind: 'character', name: 'Mira Vane', text: 'The barkeep.', data: {} },
          ],
          edges: [],
        }
      }
      return { jobs: [GENERATE_JOB], next_cursor: null }
    })

    // …and a WS-triggered re-sync delivers it.
    socketCalls[0].onMessage({ type: 'job_done', job_id: 'J9', state: 'succeeded' })
    await flushPromises()

    // The draft is gone — no stale textareas binding old data.
    expect(wrapper.findAll('textarea').length).toBe(1) // only the ask box remains
    // The reset is visible, not silent.
    expect(wrapper.text()).toContain('your draft was discarded')
    expect(wrapper.text()).toContain('replaced by a fresh wave')
  })

  it('reject is one tap and posts the reject route', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    const rejectButton = wrapper.findAll('button').filter((b) => b.text() === 'Reject')[0]
    await rejectButton!.trigger('click')
    await flushPromises()
    const rejectCall = apiFetchMock.mock.calls.find((call) => String(call[0]).includes('/reject'))
    expect(rejectCall).toBeDefined()
    const [path, init] = rejectCall!
    expect(String(path)).toContain('/candidates/CA1/reject')
    expect(init?.method).toBe('POST')
    expect(wrapper.text()).not.toContain('gaunt, ink-stained fingers')
  })

  // -------------------------------------------------------------------------
  // Staged-edge editing before accept (spec-3-4, FR9)
  // -------------------------------------------------------------------------

  it('edit mode shows the edge draft; a counter edit makes the accept send the edited edges', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit')[0]
      .trigger('click')
    await flushPromises()

    // The staged edge renders as an editable draft line with its counter.
    const counterInputs = wrapper.findAll('input[aria-label="Counter"]')
    expect(counterInputs.length).toBeGreaterThan(0)
    const edgeCounter = counterInputs[0]
    expect((edgeCounter.element as HTMLInputElement).value).toBe('1')
    await edgeCounter.setValue('4')

    // Only the edge changed — Accept already reads "Accept edited".
    const acceptEdited = wrapper.findAll('button').filter((b) => b.text() === 'Accept edited')[0]
    await acceptEdited.trigger('click')
    await flushPromises()

    const acceptCall = apiFetchMock.mock.calls.find((call) => String(call[0]).includes('/accept'))
    const body = JSON.parse((acceptCall?.[1]?.body as string) ?? '{}')
    expect(body.payload.edges).toEqual([
      { endpoint: 'E1', direction: 'outbound', type: 'rival_of', counter: 4 },
    ])
  })

  it('deleting a draft edge and accepting sends the reduced edge set', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit')[0]
      .trigger('click')
    await flushPromises()

    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Delete')[0]
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('No relations staged')

    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept edited')[0]
      .trigger('click')
    await flushPromises()
    const acceptCall = apiFetchMock.mock.calls.find((call) => String(call[0]).includes('/accept'))
    const body = JSON.parse((acceptCall?.[1]?.body as string) ?? '{}')
    expect(body.payload.edges).toEqual([])
  })

  it('adding a draft edge to a committed entity and accepting sends the extended set', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit')[0]
      .trigger('click')
    await flushPromises()

    const type = wrapper.find('select[aria-label="Relation type"]')
    const target = wrapper.find('select[aria-label="Target entity"]')
    const counter = wrapper.find('form.add-relation input[aria-label="Counter"]')
    await type.setValue('ally_of')
    await target.setValue('E1')
    await counter.setValue('2')
    await wrapper.find('form.add-relation').trigger('submit')
    await flushPromises()

    // The new edge renders in the draft with its counter.
    expect(wrapper.text()).toContain('Sable Rook --ally_of(2)--> Mira Vane')

    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept edited')[0]
      .trigger('click')
    await flushPromises()
    const acceptCall = apiFetchMock.mock.calls.find((call) => String(call[0]).includes('/accept'))
    const body = JSON.parse((acceptCall?.[1]?.body as string) ?? '{}')
    expect(body.payload.edges).toEqual([
      { endpoint: 'E1', direction: 'outbound', type: 'rival_of', counter: 1 },
      { endpoint: 'E1', direction: 'outbound', type: 'ally_of', counter: 2 },
    ])
  })

  it('an edge-only edit outside edit mode changes nothing — read-only lines persist', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    // No edit toggled: no counter inputs, no add form, no delete buttons.
    expect(wrapper.findAll('input[aria-label="Counter"]').length).toBe(0)
    expect(wrapper.find('form.add-relation').exists()).toBe(false)
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Delete').length).toBe(0)
    expect(wrapper.text()).toContain('Sable Rook --rival_of(1)--> Mira Vane')
  })
})
