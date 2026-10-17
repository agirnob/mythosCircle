// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../api/schema'

type Job = components['schemas']['JobResponse']

const { pushMock, apiFetchMock, socketCalls, routeParams } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  apiFetchMock: vi.fn(),
  routeParams: { id: 'C1' } as { id?: string },
  socketCalls: [] as Array<{
    campaignId: string
    onMessage: (message: unknown) => void
  }>,
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ params: routeParams }),
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
      readonly details?: unknown,
    ) {
      super(message)
      this.name = 'ApiError'
    }
  },
  apiFetch: apiFetchMock,
}))

import { ApiError } from '../api/client'
import CharacterForgeView from './CharacterForgeView.vue'
import { useJobsStore } from '../stores/jobs'

enableAutoUnmount(afterEach)
afterEach(() => vi.useRealTimers())

const CAMPAIGN = {
  id: 'C1',
  owner_id: 'A1',
  title: 'The Embermarked Vale',
  description: 'ash and old oaths',
  theme: 'Grimdark',
  custom_lore: '',
}

const WORLD = {
  campaign: CAMPAIGN,
  revision: { id: 'R1', created_at: '2026-09-01T00:00:00Z', note: null },
  entities: [
    { id: 'E1', kind: 'place', name: 'Greymarch', text: null, data: {}, media: [] },
    { id: 'E2', kind: 'faction', name: 'The Gilded Bar', text: null, data: {}, media: [] },
  ],
  edges: [],
}

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'J1',
    campaign_id: 'C1',
    kind: 'build_in',
    payload: {},
    state: 'queued',
    progress: 0,
    max_llm_calls: 0,
    max_media_calls: 0,
    error: null,
    result: null,
    created_at: '2026-09-16T10:00:00Z',
    started_at: null,
    finished_at: null,
    queue_position: 1,
    ...overrides,
  }
}

let jobList: Job[] = []

/** Scripted apiFetch: the export shape and the two write paths the view
 * uses — POST /api/jobs (the hybrid build gate) and the jobs list sync. */
function mockApi(options: { gateError?: ApiError; cancelError?: ApiError; generic?: boolean } = {}) {
  apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
    const url = String(path)
    const campaign = { ...CAMPAIGN, is_generic: options.generic === true }
    if (url === '/api/campaigns') return { campaigns: [campaign] }
    if (url === '/api/campaigns/themes') return { themes: ['High Fantasy', 'Grimdark'] }
    if (url === '/api/campaigns/generic' && init?.method === 'POST') return campaign
    if (url === '/api/campaigns/C1') return campaign
    if (url === '/api/campaigns/C1/export') return WORLD
    if (url.endsWith('/cancel') && init?.method === 'POST') {
      if (options.cancelError) throw options.cancelError
      const original = jobList.find((entry) => url === `/api/jobs/${entry.id}/cancel`)!
      return { ...original, state: 'cancelled', queue_position: null }
    }
    if (url === '/api/jobs' && init?.method === 'POST') {
      if (options.gateError) throw options.gateError
      return { id: 'J9', campaign_id: 'C1', kind: 'build_in', payload: gateBodies.pop(), state: 'queued', progress: 0, max_llm_calls: 64, max_media_calls: 0, error: null, result: null, created_at: '2026-09-16T10:00:00Z', started_at: null, finished_at: null, queue_position: 1 }
    }
    if (url.startsWith('/api/jobs')) return { jobs: jobList, next_cursor: null }
    throw new Error(`unexpected fetch: ${url}`)
  })
}

const gateBodies: unknown[] = []

function buildInBodies(): Array<Record<string, unknown>> {
  return (apiFetchMock.mock.calls as Array<[string, RequestInit]>)
    .filter(([url, init]) => url === '/api/jobs' && init?.method === 'POST')
    .map(([, init]) => JSON.parse((init as RequestInit).body as string))
}

async function mountView(): Promise<VueWrapper> {
  const wrapper = mount(CharacterForgeView)
  await flushPromises()
  return wrapper
}

function labeled(wrapper: VueWrapper, label: string) {
  const group = wrapper.findAll('label').find((entry) => entry.text().startsWith(label))
  if (!group) throw new Error(`no field labelled ${label}`)
  return group
}

async function fillName(wrapper: VueWrapper, value = 'Vesper') {
  await labeled(wrapper, 'Name').get('input').setValue(value)
}

async function toggleSubsection(wrapper: VueWrapper, label: string) {
  const button = wrapper.findAll('button').find((b) => b.text().endsWith(label))
  if (!button) throw new Error(`no subsection toggle ${label}`)
  await button.trigger('click')
}

describe('CharacterForgeView (nudge contract)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    jobList = []
    gateBodies.length = 0
    routeParams.id = 'C1'
    mockApi()
  })

  it('submits a hybrid build with ONLY the filled fields', async () => {
    const wrapper = await mountView()
    await fillName(wrapper, 'Vesper Quick')
    await labeled(wrapper, 'Personality').get('textarea').setValue('Glass and silver.')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const body = buildInBodies()[0]
    expect(body.kind).toBe('build_in')
    const figure = (body.payload as { key_figures: Array<Record<string, unknown>> })
      .key_figures[0]
    expect(figure.name).toBe('Vesper Quick')
    expect(figure.role).toBe('NPC')
    const record = figure.record as Record<string, unknown>
    expect(record.personality).toBe('Glass and silver.')
    // blanks are the generator's job — never sent
    expect(record.secret).toBeUndefined()
    expect(record.race_type).toBeUndefined()
    expect(record.stat_block).toBeUndefined()
  })

  it('sends only the authored stat-block subsections and composes dice boxes', async () => {
    const wrapper = await mountView()
    await fillName(wrapper)
    await toggleSubsection(wrapper, 'Actions')
    await wrapper.findAll('button').find((b) => b.text() === '+ add')!.trigger('click')

    // Identity has no toggle — the dropdowns ARE the authoring; filling
    // one derives both the record display field and the stat-block slot
    await labeled(wrapper, 'Race (SRD)').get('select').setValue('Tiefling')
    const actionName = wrapper.findAll('input[placeholder="Longsword"]')[0]!
    await actionName.setValue('Acid Flask')
    await wrapper
      .findAll('input[placeholder="Melee Weapon Attack: +5 to hit…"]')[0]!
      .setValue('Ranged Spell Attack: +6 to hit.')
    const dice = wrapper.findAll('input[placeholder="dice"]')[0]!
    await dice.setValue('2')
    const sides = wrapper.findAll('input[placeholder="sides"]')[0]!
    await sides.setValue('6')
    const mod = wrapper.findAll('input[placeholder="mod"]')[0]!
    await mod.setValue('3')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const figure = (buildInBodies()[0].payload as { key_figures: Array<Record<string, unknown>> })
      .key_figures[0]
    const recordOut = figure.record as Record<string, unknown>
    const block = recordOut.stat_block as Record<string, unknown>
    // only the authored subsections ride along
    expect(Object.keys(block)).toEqual(['identity', 'actions'])
    expect((block.identity as Record<string, unknown>).race).toBe('Tiefling')
    // the identity dropdowns derive the record's display fields — one
    // fact, one control, two slots, never contradictory
    expect(recordOut.race_type).toBe('Tiefling')
    const action = (block.actions as Array<Record<string, unknown>>)[0]
    expect(action.damage).toBe('2d6+3')
  })

  it('sends an existing target as target_id and a new name as target_name', async () => {
    const wrapper = await mountView()
    await fillName(wrapper)
    await wrapper.findAll('button').find((b) => b.text() === '+ relation')!.trigger('click')
    let row = wrapper.find('.relation-block')
    let selects = row.findAll('select')
    await selects[1].setValue('existing')
    await selects[2].setValue('E1')

    await wrapper.findAll('button').find((b) => b.text() === '+ relation')!.trigger('click')
    row = wrapper.findAll('.relation-block')[1]!
    selects = row.findAll('select')
    await selects[1].setValue('new')
    await row.find('input').setValue('Vaelmoor')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const figure = (buildInBodies()[0].payload as { key_figures: Array<Record<string, unknown>> })
      .key_figures[0]
    expect(figure.relations).toEqual([
      { type: 'located_in', target_id: 'E1' },
      { type: 'located_in', target_name: 'Vaelmoor' },
    ])
  })

  it('renders a gate rejection and keeps the form for the retry', async () => {
    mockApi({ gateError: new ApiError(422, 'validation_error', 'build_in payload invalid: name must be a non-blank string') })
    const wrapper = await mountView()
    await fillName(wrapper)

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('.error').text()).toContain('name must be a non-blank string')
    expect((labeled(wrapper, 'Name').get('input').element as HTMLInputElement).value).toBe('Vesper')
  })

  it('disables the submit button while a build is in flight', async () => {
    jobList = [job({ id: 'J2', state: 'running' })]
    const wrapper = await mountView()
    await fillName(wrapper)
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('shows what a succeeded build produced', async () => {
    jobList = [
      job({
        id: 'J3',
        state: 'succeeded',
        result: { merge: { wave1: { merged: ['E9'] } } },
      }),
    ]
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('built (merged with 1 existing)')
  })

  it('shows the character being generated and live progress', async () => {
    jobList = [job({ state: 'running', progress: 0.1, payload: { key_figures: [{ name: 'Ahmet' }] } })]
    const wrapper = await mountView()
    expect(wrapper.get('.generation-status').text()).toContain('Generating: Ahmet')
    expect(wrapper.get('.generation-status').text()).toContain('Writing your character')
    expect(wrapper.get('progress').attributes('value')).toBe('10')
    useJobsStore().upsert({ ...jobList[0]!, progress: 0.4 })
    await flushPromises()
    expect(wrapper.get('.generation-status').text()).toContain('Checking and repairing character stats')
    expect(wrapper.get('progress').attributes('value')).toBe('40')
  })

  it('restores Generic library history and its active character when reopening global Forge', async () => {
    delete routeParams.id
    mockApi({ generic: true })
    jobList = [
      job({ id: 'J-running', state: 'running', progress: 0.4, payload: { key_figures: [{ name: 'Ahmet' }] } }),
      job({ id: 'J-failed', state: 'failed', error: 'Provider failed', payload: { key_figures: ['Vesper'] } }),
    ]
    const wrapper = await mountView()
    expect(wrapper.get('.generation-status').text()).toContain('Generating: Ahmet')
    expect(wrapper.text()).toContain('Recent builds')
    expect(wrapper.text()).toContain('Vesper')
    expect(wrapper.text()).toContain('Provider failed')
    expect(apiFetchMock).toHaveBeenCalledWith('/api/jobs?campaign_id=C1')
    expect(apiFetchMock.mock.calls.some(([url, init]) => url === '/api/campaigns/generic' && init?.method === 'POST')).toBe(false)
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('keeps a route-pinned Generic library in draft mode', async () => {
    mockApi({ generic: true })
    const wrapper = await mountView()
    await fillName(wrapper)
    expect(wrapper.get('button[type="submit"]').text()).toBe('Generate into the library')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(buildInBodies()[0].payload).toMatchObject({ theme: 'High Fantasy' })
    expect(buildInBodies()[0].payload).not.toHaveProperty('places')
  })

  it.each(['queued', 'running'])('cancels a %s generation and preserves the form', async (state) => {
    jobList = [job({ state, payload: { key_figures: ['Ahmet'] } })]
    const wrapper = await mountView()
    await fillName(wrapper, 'My next character')
    await wrapper.get('.generation-status button').trigger('click')
    await flushPromises()
    expect(apiFetchMock).toHaveBeenCalledWith('/api/jobs/J1/cancel', { method: 'POST' })
    expect(useJobsStore().byId['J1']?.state).toBe('cancelled')
    expect(wrapper.find('.generation-status').exists()).toBe(false)
    expect(wrapper.text()).toContain('Generation cancelled.')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    expect((labeled(wrapper, 'Name').get('input').element as HTMLInputElement).value).toBe('My next character')
  })

  it('keeps the generation active and shows a cancellation failure', async () => {
    jobList = [job({ state: 'running' })]
    mockApi({ cancelError: new ApiError(503, 'unavailable', 'Please retry cancellation.') })
    const wrapper = await mountView()
    await wrapper.get('.generation-status button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Please retry cancellation.')
    expect(wrapper.get('.generation-status button').attributes('disabled')).toBeUndefined()
    expect(useJobsStore().byId['J1']?.state).toBe('running')
  })

  it('refreshes the result if generation finishes before cancellation', async () => {
    jobList = [job({ state: 'running' })]
    mockApi({ cancelError: new ApiError(409, 'conflict', 'Already finished') })
    const wrapper = await mountView()
    jobList = [job({ state: 'succeeded', progress: 1 })]
    await wrapper.get('.generation-status button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.generation-status').exists()).toBe(false)
    expect(wrapper.text()).toContain('already finished')
    expect(useJobsStore().byId['J1']?.state).toBe('succeeded')
  })

  it('shows queue position and polls progress until unmounted', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-16T10:00:05Z'))
    jobList = [job({ queue_position: 2 })]
    const wrapper = await mountView()
    expect(wrapper.get('.generation-status').text()).toContain('Queue position: 2')
    expect(wrapper.get('.generation-status').text()).toContain('Waiting 5s')
    expect(wrapper.find('progress').exists()).toBe(false)
    jobList = [job({ state: 'running', progress: 0.3, started_at: '2026-09-16T10:00:08Z' })]
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.get('.generation-status').text()).toContain('Completing the character record')
    expect(wrapper.get('.generation-status').text()).toContain('Elapsed 2s')
    wrapper.unmount()
    apiFetchMock.mockClear()
    await vi.advanceTimersByTimeAsync(5000)
    expect(apiFetchMock).not.toHaveBeenCalled()
  })
})
