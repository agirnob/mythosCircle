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
import { useJobsStore } from '../stores/jobs'
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

  // -------------------------------------------------------------------------
  // Re-roll (spec-3-5): whole + per-section, draft discarded visibly
  // -------------------------------------------------------------------------

  it('whole Re-roll posts a regenerate job with no sections and keeps the row', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()

    const reRoll = wrapper.findAll('div.actions button').filter((b) => b.text() === 'Re-roll')[0]
    await reRoll!.trigger('click')
    await flushPromises()

    const jobCall = apiFetchMock.mock.calls.find((call) => String(call[0]) === '/api/jobs')
    expect(jobCall).toBeDefined()
    const jobInit = jobCall![1] as RequestInit
    expect(jobInit.method).toBe('POST')
    expect(JSON.parse(jobInit.body as string)).toEqual({
      campaign_id: 'C1',
      kind: 'regenerate',
      payload: { target: { kind: 'candidate', id: 'CA1' } },
    })
    // The row stays on the accept screen while the job runs.
    expect(wrapper.text()).toContain('Sable Rook')
  })

  it('per-section Re-roll posts the exact section list', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()

    const personalityRoll = wrapper.findAll('button.re-roll')[0] // a section row
    await personalityRoll!.trigger('click')
    await flushPromises()

    const jobCall = apiFetchMock.mock.calls.find((call) => String(call[0]) === '/api/jobs')
    const body = JSON.parse((jobCall![1] as RequestInit).body as string)
    expect(body.kind).toBe('regenerate')
    expect(body.payload.target).toEqual({ kind: 'candidate', id: 'CA1' })
    expect(Array.isArray(body.payload.sections)).toBe(true)
    expect(body.payload.sections.length).toBe(1)
  })

  it('a re-roll visibly discards an in-flight manual draft before enqueueing', async () => {
    stubApi()
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit')[0]
      .trigger('click')
    await flushPromises()
    expect(wrapper.findAll('textarea').length).toBeGreaterThan(1)

    await wrapper
      .findAll('div.actions button')
      .filter((b) => b.text() === 'Re-roll')[0]
      .trigger('click')
    await flushPromises()

    // Only the ask box remains — the draft is gone, never silently merged.
    expect(wrapper.findAll('textarea').length).toBe(1)
    const jobCall = apiFetchMock.mock.calls.find((call) => String(call[0]) === '/api/jobs')
    expect(JSON.parse((jobCall![1] as RequestInit).body as string).kind).toBe('regenerate')
  })

  it('renders the recent regenerate job progress line', async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/candidates')) {
        return { candidates: [SABLE], next_cursor: null }
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
      // GENERATE_JOB is mutated by an earlier test's job_done frame (it is
      // upserted by reference), so the RJ1 fixture pins its own state.
      return {
        jobs: [
          {
            ...GENERATE_JOB,
            id: 'RJ1',
            kind: 'regenerate',
            state: 'running',
          },
        ],
        next_cursor: null,
      }
    })
    const wrapper = mountView()
    await flushPromises()
    const jobs = useJobsStore()
    const rj = Object.values(jobs.byId).find((j) => j.id === 'RJ1')
    // The jobs store caches the regenerate job; the progress line shows it.
    expect(rj?.kind).toBe('regenerate')
    expect(apiFetchMock.mock.calls.map((c) => String(c[0]))).toContain('/api/jobs?campaign_id=C1')
    expect(rj?.state).toBe('running')
    expect(wrapper.text()).toContain('Re-roll — running')
  })

  it('a re-roll job_done replaces the row payload via WS resync', async () => {
    const replaced: Candidate = {
      ...SABLE,
      payload: { ...SABLE.payload, personality: 're-rolled by the model' },
    }
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/candidates')) {
        return { candidates: [replaced], next_cursor: null }
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
      return { jobs: [{ ...GENERATE_JOB, id: 'RJ1', kind: 'regenerate' }], next_cursor: null }
    })

    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('div.actions button')
      .filter((b) => b.text() === 'Re-roll')[0]
      .trigger('click')
    await flushPromises()

    // The job drains; the WS job_done frame lands and re-syncs the rows.
    socketCalls[0].onMessage({ type: 'job_done', job_id: 'RJ1', state: 'succeeded' })
    await flushPromises()
    expect(wrapper.text()).toContain('re-rolled by the model')
  })

  it('a failed re-roll submit keeps the manual draft and edit mode', async () => {
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url === '/api/jobs' && init?.method === 'POST') {
        throw new ApiError(409, 'queue_full', 'pending jobs at the cap')
      }
      if (url.includes('/candidates')) {
        return { candidates: [SABLE], next_cursor: null }
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
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit')[0]
      .trigger('click')
    await flushPromises()
    expect(wrapper.findAll('textarea').length).toBeGreaterThan(1)

    await wrapper
      .findAll('div.actions button')
      .filter((b) => b.text() === 'Re-roll')[0]
      .trigger('click')
    await flushPromises()

    // Submit failed: the draft and edit mode survive; the error renders.
    expect(wrapper.findAll('textarea').length).toBeGreaterThan(1)
    expect(wrapper.text()).toContain('pending jobs at the cap')
    expect(wrapper.findAll('button').some((b) => b.text() === 'Cancel edit')).toBe(true)
  })

  it('badges a re-roll card with the section it re-rolls', async () => {
    // Two re-roll cards for one character read near-identically (each stages
    // the WHOLE record); the badge is what tells them apart (2026-09-11).
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/candidates')) {
        return { candidates: [SABLE], next_cursor: null }
      }
      if (url.includes('/api/jobs')) {
        return {
          jobs: [
            {
              id: 'J1',
              campaign_id: 'C1',
              kind: 'regenerate',
              state: 'succeeded',
              created_at: '2026-09-04T19:06:00Z',
              payload: { target: { kind: 'entity', id: 'E1' }, sections: ['secret'] },
            },
          ],
          next_cursor: null,
        }
      }
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
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Re-roll of Secret')
    wrapper.unmount()
  })

  it('re-roll buttons disable while a roll is in flight', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url === '/api/jobs' && init?.method === 'POST') {
        await gate
        return { ...GENERATE_JOB, id: 'RJ2', kind: 'regenerate' }
      }
      if (url.includes('/candidates')) {
        return { candidates: [SABLE], next_cursor: null }
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
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('div.actions button')
      .filter((b) => b.text() === 'Re-roll')[0]
      .trigger('click')
    await flushPromises()
    // While the roll is in flight, the row's buttons are disabled (actingId).
    const disabled = wrapper
      .findAll('div.actions button')
      .filter((b) => b.text().startsWith('Re-roll'))[0]
      .attributes('disabled')
    expect(disabled).toBeDefined()
    release()
    await flushPromises()
    wrapper.unmount()
  })

  it('an accept 409 naming the edit conflict opens the three-way dialog', async () => {
    const regen = { ...SABLE, id: 'CA2', regenerates_entity_id: 'E1' }
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/accept')) {
        throw new ApiError(
          409,
          'conflict',
          'entity E1 changed since this candidate was generated — re-roll (rebase), accept anyway (overwrite), or cancel (reject)',
        )
      }
      if (url.includes('/candidates')) return { candidates: [regen], next_cursor: null }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: 'x',
          },
          revision: { id: 'R1', created_at: 'x' },
          entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: '', data: {} }],
          edges: [],
        }
      }
      return { jobs: [GENERATE_JOB], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept')[0]!
      .trigger('click')
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('changed since this candidate was generated')
    expect(text).toContain('Mira Vane') // the dialog names the edited target
    expect(
      wrapper.findAll('button').filter((b) => b.text() === 'Re-roll against latest world').length,
    ).toBe(1)
    expect(
      wrapper.findAll('button').filter((b) => b.text() === 'Accept generated version anyway')
        .length,
    ).toBe(1)
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Cancel').length).toBe(1)
  })

  it('a NON-conflict 409 (e.g. already settled) renders the card error, not the dialog', async () => {
    const regen = { ...SABLE, id: 'CA2', regenerates_entity_id: 'E1' }
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/accept')) {
        throw new ApiError(409, 'conflict', 'candidate CA2 is already accepted')
      }
      if (url.includes('/candidates')) return { candidates: [regen], next_cursor: null }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: 'x',
          },
          revision: { id: 'R1', created_at: 'x' },
          entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: '', data: {} }],
          edges: [],
        }
      }
      return { jobs: [GENERATE_JOB], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept')[0]!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('candidate CA2 is already accepted')
    expect(
      wrapper.findAll('button').filter((b) => b.text() === 'Re-roll against latest world').length,
    ).toBe(0)
  })

  it('the conflict dialog Re-roll posts a candidate-target regenerate job and closes', async () => {
    const regen = { ...SABLE, id: 'CA2', regenerates_entity_id: 'E1' }
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url.includes('/accept')) {
        throw new ApiError(
          409,
          'conflict',
          'entity E1 changed since this candidate was generated — re-roll (rebase), accept anyway (overwrite), or cancel (reject)',
        )
      }
      if (url.includes('/candidates')) return { candidates: [regen], next_cursor: null }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: 'x',
          },
          revision: { id: 'R1', created_at: 'x' },
          entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: '', data: {} }],
          edges: [],
        }
      }
      if (init?.method === 'POST') return { ...GENERATE_JOB, state: 'queued', progress: 0 }
      return { jobs: [GENERATE_JOB], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept')[0]!
      .trigger('click')
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Re-roll against latest world')[0]!
      .trigger('click')
    await flushPromises()
    const regenCall = apiFetchMock.mock.calls.find(
      (call) =>
        String(call[0]) === '/api/jobs' && (call[1] as RequestInit | undefined)?.method === 'POST',
    )
    expect(regenCall).toBeDefined()
    const body = JSON.parse((regenCall![1] as RequestInit).body as string)
    expect(body).toMatchObject({ campaign_id: 'C1', kind: 'regenerate' })
    expect(body.payload.target).toEqual({ kind: 'candidate', id: 'CA2' })
    // Dialog closed after enqueue.
    expect(
      wrapper.findAll('button').filter((b) => b.text() === 'Re-roll against latest world').length,
    ).toBe(0)
  })

  it('a failed dialog re-roll keeps the dialog open and shows the error', async () => {
    const regen = { ...SABLE, id: 'CA2', regenerates_entity_id: 'E1' }
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url.includes('/accept')) {
        throw new ApiError(
          409,
          'conflict',
          'entity E1 changed since this candidate was generated — re-roll (rebase), accept anyway (overwrite), or cancel (reject)',
        )
      }
      if (url.includes('/candidates')) return { candidates: [regen], next_cursor: null }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: 'x',
          },
          revision: { id: 'R1', created_at: 'x' },
          entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: '', data: {} }],
          edges: [],
        }
      }
      if (init?.method === 'POST') throw new ApiError(500, 'server_error', 'queue is down')
      return { jobs: [GENERATE_JOB], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept')[0]!
      .trigger('click')
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Re-roll against latest world')[0]!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('queue is down')
    expect(
      wrapper.findAll('button').filter((b) => b.text() === 'Re-roll against latest world').length,
    ).toBe(1)
  })

  it('Accept generated version anyway is two-step and sends confirm_overwrite: true', async () => {
    const regen = { ...SABLE, id: 'CA2', regenerates_entity_id: 'E1' }
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url.includes('/accept')) {
        const body = (init?.body as string) ?? ''
        if (body.includes('confirm_overwrite')) return { ...accepted(regen) }
        throw new ApiError(
          409,
          'conflict',
          'entity E1 changed since this candidate was generated — re-roll (rebase), accept anyway (overwrite), or cancel (reject)',
        )
      }
      if (url.includes('/candidates')) return { candidates: [regen], next_cursor: null }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: 'x',
          },
          revision: { id: 'R1', created_at: 'x' },
          entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: '', data: {} }],
          edges: [],
        }
      }
      return { jobs: [GENERATE_JOB], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept')[0]!
      .trigger('click')
    await flushPromises()
    // First click arms — the label flips, nothing is posted yet.
    expect(
      wrapper.findAll('button').filter((b) => b.text() === 'Accept generated version anyway')
        .length,
    ).toBe(1)
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept generated version anyway')[0]!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Confirm overwrite')
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Confirm overwrite').length).toBe(1)
    const acceptCalls = apiFetchMock.mock.calls.filter((call) =>
      String(call[0]).includes('/accept'),
    )
    expect(acceptCalls).toHaveLength(1) // armed only — no second accept yet
    // Second click confirms: the accept carries confirm_overwrite.
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Confirm overwrite')[0]!
      .trigger('click')
    await flushPromises()
    const confirmed = apiFetchMock.mock.calls.filter((call) => String(call[0]).includes('/accept'))
    expect(confirmed).toHaveLength(2)
    const body = JSON.parse((confirmed[1][1] as RequestInit).body as string)
    expect(body).toEqual({ confirm_overwrite: true })
    expect(
      wrapper.findAll('button').filter((b) => b.text() === 'Re-roll against latest world').length,
    ).toBe(0)
  })

  it('the dialog Cancel closes with the row still proposed and zero extra requests', async () => {
    const regen = { ...SABLE, id: 'CA2', regenerates_entity_id: 'E1' }
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/accept')) {
        throw new ApiError(
          409,
          'conflict',
          'entity E1 changed since this candidate was generated — re-roll (rebase), accept anyway (overwrite), or cancel (reject)',
        )
      }
      if (url.includes('/candidates')) return { candidates: [regen], next_cursor: null }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: 'x',
          },
          revision: { id: 'R1', created_at: 'x' },
          entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: '', data: {} }],
          edges: [],
        }
      }
      return { jobs: [GENERATE_JOB], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept')[0]!
      .trigger('click')
    await flushPromises()
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Cancel').length).toBe(1)
    const requestsBefore = apiFetchMock.mock.calls.length
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Cancel')[0]!
      .trigger('click')
    await flushPromises()
    // The dialog closes; the row stays proposed (Accept is offered again)
    // and the cancel itself posted nothing.
    expect(
      wrapper.findAll('button').filter((b) => b.text() === 'Re-roll against latest world').length,
    ).toBe(0)
    expect(wrapper.findAll('button').filter((b) => b.text() === 'Accept')[0]).toBeDefined()
    expect(apiFetchMock.mock.calls.length).toBe(requestsBefore)
  })

  it('the dialog Re-roll is disabled while a regenerate for the row is queued or running', async () => {
    const regen = { ...SABLE, id: 'CA2', regenerates_entity_id: 'E1' }
    // A non-terminal regenerate for THIS candidate row is in the cache.
    const inFlight = {
      ...GENERATE_JOB,
      id: 'JR1',
      kind: 'regenerate',
      state: 'queued',
      payload: { target: { kind: 'candidate', id: 'CA2' } },
    }
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/candidates')) return { candidates: [regen], next_cursor: null }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: 'x',
          },
          revision: { id: 'R1', created_at: 'x' },
          entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: '', data: {} }],
          edges: [],
        }
      }
      return { jobs: [inFlight], next_cursor: null }
    })
    const wrapper = mountView()
    await flushPromises()
    // The dialog can open (the 409 conflict got it here in the real flow —
    // open it directly via the accept error path).
    apiFetchMock.mockImplementation(async (path: string) => {
      const url = String(path)
      if (url.includes('/accept')) {
        throw new ApiError(
          409,
          'conflict',
          'entity E1 changed since this candidate was generated — re-roll (rebase), accept anyway (overwrite), or cancel (reject)',
        )
      }
      if (url.includes('/candidates')) return { candidates: [regen], next_cursor: null }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: 'x',
          },
          revision: { id: 'R1', created_at: 'x' },
          entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: '', data: {} }],
          edges: [],
        }
      }
      return { jobs: [inFlight], next_cursor: null }
    })
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Accept')[0]!
      .trigger('click')
    await flushPromises()
    const reroll = wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Re-roll against latest world')[0]
    expect(reroll).toBeDefined()
    expect(reroll!.attributes('disabled')).toBeDefined() // in-flight discipline
  })

  // -------------------------------------------------------------------------
  // Portrait auto-enqueue after accept (spec-4.1)
  // -------------------------------------------------------------------------

  /** A portrait-aware accept stub: the accepted row carries an
   * ``accepted_entity_id`` (schema.ts:642); the image-POST route records
   * the enqueue. The accepted appearance reflects the EDIT OVERRIDE when
   * one was sent (the gating decision uses what actually committed). */
  function stubPortraitAcceptApi(options: {
    list?: Candidate[]
    failPortraitWith?: string
    portraitPending?: boolean
  } = {}) {
    const candidate = options.list?.[0] ?? SABLE
    const portraitPosts: Array<{ kind: string; payload?: unknown }> = []
    apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const url = String(path)
      if (url.includes('/accept')) {
        const body = (init?.body as string) ?? '{}'
        const parsed = JSON.parse(body) as { payload?: Record<string, unknown> }
        return {
          ...candidate,
          status: 'accepted',
          accepted_entity_id: 'ACC1',
          payload: (parsed.payload ?? candidate.payload) as Record<string, unknown>,
        }
      }
      if (url.includes('/reject')) return { ...SABLE, status: 'rejected' }
      if (url.includes('/candidates')) {
        return { candidates: options.list ?? [candidate], next_cursor: null }
      }
      if (url.includes('/export')) {
        return {
          campaign: {
            id: 'C1',
            title: 'Greymarch',
            theme: 'dread',
            description: '',
            custom_lore: '',
            created_at: 'x',
          },
          revision: { id: 'R1', created_at: 'x' },
          entities: [{ id: 'E1', kind: 'character', name: 'Mira Vane', text: '', data: {} }],
          edges: [],
        }
      }
      if (url === '/api/jobs' && init?.method === 'POST') {
        if (options.failPortraitWith !== undefined) {
          throw new ApiError(409, 'queue_full', options.failPortraitWith)
        }
        const body = JSON.parse((init.body as string) ?? '{}') as {
          kind: string
          payload?: unknown
        }
        portraitPosts.push({ kind: body.kind, payload: body.payload })
        return {
          ...GENERATE_JOB,
          id: 'JP1',
          kind: body.kind,
          payload: body.payload,
          state: 'queued',
          progress: 0,
          queue_position: 1,
        }
      }
      if (options.portraitPending) {
        return {
          jobs: [
            {
              ...GENERATE_JOB,
              id: 'JP0',
              kind: 'image',
              payload: { entity_id: 'ACC1' },
              state: 'queued',
              progress: 0,
              queue_position: 1,
            },
          ],
          next_cursor: null,
        }
      }
      return { jobs: [GENERATE_JOB], next_cursor: null }
    })
    return portraitPosts
  }

  it('accept with a non-blank appearance auto-enqueues the portrait for the accepted entity', async () => {
    const portraitPosts = stubPortraitAcceptApi()
    const wrapper = mountView()
    await flushPromises()
    await wrapper.findAll('button').filter((b) => b.text() === 'Accept')[0]!.trigger('click')
    await flushPromises()
    const imagePost = portraitPosts.find((p) => p.kind === 'image')
    expect(imagePost).toBeDefined()
    expect(imagePost!.payload).toEqual({ entity_id: 'ACC1' })
    wrapper.unmount()
  })

  it('accept with a blank appearance does NOT auto-enqueue a portrait', async () => {
    const blank: Candidate = {
      ...SABLE,
      payload: { ...SABLE.payload, appearance: '   ' },
    }
    const portraitPosts = stubPortraitAcceptApi({ list: [blank] })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.findAll('button').filter((b) => b.text() === 'Accept')[0]!.trigger('click')
    await flushPromises()
    expect(portraitPosts.filter((p) => p.kind === 'image')).toHaveLength(0)
    wrapper.unmount()
  })

  it('accept with an EDITED OVERRIDE blanks the appearance — the override gates the enqueue', async () => {
    const portraitPosts = stubPortraitAcceptApi()
    const wrapper = mountView()
    await flushPromises()
    // Enter edit mode and blank the appearance.
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Edit')[0]!
      .trigger('click')
    await flushPromises()
    const appearanceArea = wrapper
      .findAll('textarea')
      .find((t) => (t.element as HTMLTextAreaElement).value === 'gaunt, ink-stained fingers')!
    await appearanceArea.setValue('   ')
    await wrapper.findAll('button').filter((b) => b.text() === 'Accept edited')[0]!.trigger('click')
    await flushPromises()
    // The override (blank appearance) committed — no portrait for the
    // accepted entity (the gate uses what actually committed).
    expect(portraitPosts.filter((p) => p.kind === 'image')).toHaveLength(0)
    wrapper.unmount()
  })

  it('a rejected portrait enqueue never fails the accept flow', async () => {
    stubPortraitAcceptApi({ failPortraitWith: 'pending jobs at the cap' })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.findAll('button').filter((b) => b.text() === 'Accept')[0]!.trigger('click')
    await flushPromises()
    // The accept succeeded and the row left the proposed list — the
    // portrait enqueue failure is swallowed (the WorldView button is the
    // fallback), and no accept error is rendered.
    expect(wrapper.text()).not.toContain('Sable Rook --rival_of')
    expect(wrapper.text()).not.toContain('pending jobs at the cap')
    wrapper.unmount()
  })

  it('accept does not double-enqueue when a portrait job for the entity is already pending', async () => {
    const portraitPosts = stubPortraitAcceptApi({ portraitPending: true })
    const wrapper = mountView()
    await flushPromises()
    // Seed the jobs store directly: a pre-existing queued image job for
    // the accepted entity — the pending state the accept path checks.
    const jobs = useJobsStore()
    jobs.byId['JP0'] = {
      ...GENERATE_JOB,
      id: 'JP0',
      kind: 'image',
      payload: { entity_id: 'ACC1' },
      state: 'queued',
      progress: 0,
      queue_position: 1,
      max_llm_calls: 64,
      max_media_calls: 8,
      error: null,
      result: null,
      started_at: null,
      finished_at: null,
      created_at: '2026-09-06T09:00:00Z',
    }
    await wrapper.findAll('button').filter((b) => b.text() === 'Accept')[0]!.trigger('click')
    await flushPromises()
    expect(portraitPosts.filter((p) => p.kind === 'image')).toHaveLength(0)
    wrapper.unmount()
  })

  it('renders the stored structured attack and the optional stat aspects in the candidate stat block', async () => {
    const candidate: Candidate = {
      ...SABLE,
      payload: {
        ...SABLE.payload,
        stat_block: {
          identity: { role: 'NPC', level: 5, race: 'Human' },
          attributes: { str: 14, dex: 12, con: 14, int: 10, wis: 10, cha: 8 },
          combat: { ac: 16, hp: 44, hit_dice: '5d8 + 10' },
          saves: { str: 4, dex: 1 },
          initiative: 3,
          passive_perception: 12,
          proficiency_bonus: 3,
          spellcasting: { dc: 15, attack_bonus: 7, slots: [4, 2] },
          features: ['Sneak Attack'],
          resources: { ki_points: 5 },
          actions: [
            {
              name: 'Rapier',
              to_hit: 7,
              damage: [
                { dice: '1d8', count: 1, sides: 8, bonus: 4, average: 8.5, type: 'piercing' },
              ],
            },
          ],
        },
      },
    }
    stubApi({ list: [candidate] })
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('AC 16 · HP 44 · Hit dice 5d8 + 10')
    expect(text).toContain('Saves STR +4, DEX +1')
    expect(text).toContain('Initiative +3')
    expect(text).toContain('Passive perception 12')
    expect(text).toContain('Proficiency bonus +3')
    expect(text).toContain('Spellcasting DC 15 · Attack +7 · Slots 4/2')
    expect(text).toContain('Features Sneak Attack')
    expect(text).toContain('Resources Ki points 5')
    expect(text).toContain('Rapier +7 — 8.5 (1d8+4) piercing')
    wrapper.unmount()
  })
})
