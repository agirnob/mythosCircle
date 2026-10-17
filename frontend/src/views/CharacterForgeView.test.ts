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
    disconnect: ReturnType<typeof vi.fn>
    options: { onReconnect: () => void; onAuthFailure: () => void }
  }>,
}))

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ params: routeParams }),
  useRouter: () => ({ push: pushMock }),
}))

vi.mock('../ws', () => ({
  connectJobSocket: vi.fn((campaignId: string, onMessage: (message: unknown) => void, options: { onReconnect: () => void; onAuthFailure: () => void }) => {
    const disconnect = vi.fn()
    socketCalls.push({ campaignId, onMessage, disconnect, options })
    return disconnect
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
import CharacterSheetEditor from '../components/CharacterSheetEditor.vue'
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
  const detailed = wrapper.findAll('button').find((button) => button.text() === 'Detailed builder')
  if (detailed) await detailed.trigger('click')
  await labeled(wrapper, 'Name').get('input').setValue(value)
}

async function toggleSubsection(wrapper: VueWrapper, label: string) {
  const mechanics = wrapper.findAll('button').find((b) => b.text() === 'Mechanics')
  if (mechanics) await mechanics.trigger('click')
  const button = wrapper.findAll('button').find((b) => b.text().startsWith(label))
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

  it('defaults to Quick and shares concept and authored Background across modes', async () => {
    const wrapper = await mountView()
    expect(wrapper.get('[role="tab"][aria-selected="true"]').text()).toBe('Quick generate')
    const concept = 'A retired dragon hunter who secretly funds the creatures she pursued.'
    const background = 'Nera learned to mend wounds from the same dragon she once hunted.  '
    await wrapper.get('textarea[placeholder*="retired dragon hunter"]').setValue(concept)
    await wrapper.get('input[placeholder="Generated if left blank"]').setValue('Nera Vane')
    await clickButton(wrapper, 'Detailed builder')
    await labeled(wrapper, 'Background').get('textarea').setValue(background)
    await clickButton(wrapper, 'Quick generate')
    expect((wrapper.get('textarea[placeholder*="retired dragon hunter"]').element as HTMLTextAreaElement).value).toBe(concept)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(buildInBodies()[0]!.payload).toMatchObject({ concept, key_figures: [{ name: 'Nera Vane', record: { background } }] })
  })

  it('keeps ordered spell names and descriptions when the subsection is collapsed', async () => {
    const wrapper = await mountView()
    await fillName(wrapper)
    await toggleSubsection(wrapper, 'Spells')
    await clickButton(wrapper, 'Add spell')
    await clickButton(wrapper, 'Add spell')
    const names = wrapper.findAll('input[aria-label="Spell name"]')
    const descriptions = wrapper.findAll('input[aria-label="Spell description"]')
    await names[0]!.setValue('Fireball')
    await descriptions[0]!.setValue('A bead of fire bursts in the air.')
    await names[1]!.setValue('Shield')
    await wrapper.findAll('button').find((button) => button.text().startsWith('Spells'))!.trigger('click')
    expect(wrapper.findAll('input[aria-label="Spell name"]')).toHaveLength(0)
    await wrapper.findAll('button').find((button) => button.text().startsWith('Spells'))!.trigger('click')
    expect(wrapper.findAll('input[aria-label="Spell name"]')).toHaveLength(2)
    expect((wrapper.findAll('input[aria-label="Spell name"]')[0]!.element as HTMLInputElement).value).toBe('Fireball')
    await wrapper.findAll('button').find((button) => button.text().startsWith('Spells'))!.trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const figure = (buildInBodies()[0]!.payload as { key_figures: Array<Record<string, unknown>> }).key_figures[0]
    const record = figure.record as Record<string, unknown>
    expect((record.stat_block as Record<string, unknown>).spells).toEqual([
      { name: 'Fireball', description: 'A bead of fire bursts in the air.' },
      { name: 'Shield' },
    ])
  })

  it('serializes Monster challenge rating as CR rather than level', async () => {
    const wrapper = await mountView()
    await fillName(wrapper)
    await labeled(wrapper, 'Role').get('select').setValue('Monster')
    await labeled(wrapper, 'Challenge rating').get('input').setValue('3')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const figure = (buildInBodies()[0]!.payload as { key_figures: Array<Record<string, unknown>> }).key_figures[0]
    const record = figure.record as Record<string, unknown>
    expect(figure).toMatchObject({ role: 'Monster', record: { level_cr: 'CR 3', stat_block: { identity: { cr: '3' } } } })
    expect((record.stat_block as Record<string, unknown>).identity).not.toHaveProperty('level')
  })

  it('summarizes authored optional fields, mechanics and connections alongside generated sections', async () => {
    const wrapper = await mountView()
    await fillName(wrapper)
    await toggleSubsection(wrapper, 'Character')
    const detailButton = wrapper.findAll('button').find((button) => button.text().startsWith('Personality'))!
    await detailButton.trigger('click')
    await wrapper.get('textarea[aria-label="Personality"]').setValue('Keeps promises')
    await toggleSubsection(wrapper, 'Mechanics')
    await toggleSubsection(wrapper, 'Spells')
    await clickButton(wrapper, 'Add spell')
    await wrapper.get('input[aria-label="Spell name"]').setValue('Shield')
    await toggleSubsection(wrapper, 'Connections')
    await labeled(wrapper, 'Reputation').get('textarea').setValue('Trusted by dockworkers')
    await clickButton(wrapper, 'Add relation')
    await wrapper.get('.relation-block').findAll('select')[2]!.setValue('E1')

    const summary = wrapper.get('[aria-label="Character input summary"]').text()
    expect(summary).toContain('Personality: Keeps promises')
    expect(summary).toContain('Spell: Shield')
    expect(summary).toContain('Reputation: Trusted by dockworkers')
    expect(summary).toContain('Relation: located in → Greymarch')
    expect(summary).toContain('Relations')
    expect(summary).toContain('Background')
    expect(summary).toContain('Skills')
  })

  it('hides collapsed optional details and retains their authored value', async () => {
    const wrapper = await mountView()
    await fillName(wrapper)
    await clickButton(wrapper, 'Character')

    const toggle = () => wrapper.findAll('button').find((button) => button.text().startsWith('Personality'))!
    await toggle().trigger('click')
    const personality = wrapper.get('textarea[aria-label="Personality"]')
    await personality.setValue('Keeps every promise.')
    await toggle().trigger('click')

    expect((personality.element as HTMLTextAreaElement).style.display).toBe('none')
    await toggle().trigger('click')
    expect((personality.element as HTMLTextAreaElement).style.display).not.toBe('none')
    expect((personality.element as HTMLTextAreaElement).value).toBe('Keeps every promise.')
  })

  it('submits a hybrid build with ONLY the filled fields', async () => {
    const wrapper = await mountView()
    await fillName(wrapper, 'Vesper Quick')
    await wrapper.get('textarea[aria-label="Personality"]').setValue('Glass and silver.')

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
    await wrapper.findAll('button').find((b) => b.text() === 'Add action')!.trigger('click')

    // Identity has no toggle — the dropdowns ARE the authoring; filling
    // one derives both the record display field and the stat-block slot
    await labeled(wrapper, 'Species').get('select').setValue('Tiefling')
    const actionName = wrapper.findAll('input[placeholder="Longsword"]')[0]!
    await actionName.setValue('Acid Flask')
    await wrapper
      .findAll('input[placeholder="Describe the action"]')[0]!
      .setValue('Ranged Spell Attack: +6 to hit.')
    const dice = wrapper.findAll('input[placeholder="Dice"]')[0]!
    await dice.setValue('2')
    const sides = wrapper.findAll('input[placeholder="Sides"]')[0]!
    await sides.setValue('6')
    const mod = wrapper.findAll('input[placeholder="Modifier"]')[0]!
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
    await wrapper.findAll('button').find((b) => b.text() === 'Add relation')!.trigger('click')
    let row = wrapper.find('.relation-block')
    let selects = row.findAll('select')
    await selects[1].setValue('existing')
    await selects[2].setValue('E1')

    await wrapper.findAll('button').find((b) => b.text() === 'Add relation')!.trigger('click')
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


function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

async function clickButton(wrapper: VueWrapper, text: string) {
  if (text === 'Add relation') {
    if (!wrapper.findAll('button').some((button) => button.text() === 'Detailed builder')) {
      throw new Error('Detailed builder tab missing')
    }
    const detailed = wrapper.findAll('button').find((button) => button.text() === 'Detailed builder')!
    await detailed.trigger('click')
    const connections = wrapper.findAll('button').find((button) => button.text() === 'Connections')
    if (connections) await connections.trigger('click')
  }
  const button = wrapper.findAll('button').find((entry) => entry.text() === text)
  if (!button) throw new Error(`No button: ${text}`)
  await button.trigger('click')
  await flushPromises()
}

const SECOND_WORLD = { ...CAMPAIGN, id: 'C2', title: 'The Silver Coast' }
const GENERIC = { ...CAMPAIGN, id: 'G1', title: 'Generic', is_generic: true }

function destinationApi() {
  const state = {
    metadataError: false,
    historyError: false,
    genericError: false,
    entityError: false,
    delayedThemes: null as ReturnType<typeof deferred<{ themes: string[] }>> | null,
    active: false,
    delayedMetadata: null as ReturnType<typeof deferred<typeof CAMPAIGN>> | null,
    delayedHistory: null as ReturnType<typeof deferred<{ jobs: Job[]; next_cursor: null }>> | null,
    delayedEntities: null as ReturnType<typeof deferred<typeof WORLD>> | null,
  }
  apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/api/campaigns') return { campaigns: [CAMPAIGN, SECOND_WORLD] }
    if (path === '/api/campaigns/themes' && state.delayedThemes) return state.delayedThemes.promise
    if (path === '/api/campaigns/themes') return { themes: ['High Fantasy', 'Grimdark'] }
    if (path === '/api/campaigns/generic') {
      if (state.genericError) throw new ApiError(503, 'unavailable', 'Library unavailable')
      return GENERIC
    }
    if (path === '/api/campaigns/C1') {
      if (state.delayedMetadata) return state.delayedMetadata.promise
      if (state.metadataError) throw new ApiError(503, 'unavailable', 'World unavailable')
      return CAMPAIGN
    }
    if (path === '/api/campaigns/C2') return SECOND_WORLD
    if (path === '/api/campaigns/G1') return GENERIC
    if (path === '/api/campaigns/C1/export') {
      if (state.delayedEntities) return state.delayedEntities.promise
      return WORLD
    }
    if (path.endsWith('/export') && state.entityError) throw new ApiError(503, 'unavailable', 'Entities unavailable')
    if (path.endsWith('/export')) return { ...WORLD, entities: [{ ...WORLD.entities[0], id: 'E3', name: 'Silver Harbour' }] }
    if (path.startsWith('/api/jobs?')) {
      if (path.includes('C1') || path.includes('G1')) {
        if (state.delayedHistory) return state.delayedHistory.promise
        if (state.historyError) throw new ApiError(503, 'unavailable', 'History unavailable')
        if (state.active) return { jobs: [job({ campaign_id: path.includes('G1') ? 'G1' : 'C1' })], next_cursor: null }
      }
      return { jobs: [], next_cursor: null }
    }
    if (path === '/api/jobs' && init?.method === 'POST') {
      const body = JSON.parse(init.body as string)
      return job({ id: 'J-new', campaign_id: body.campaign_id, payload: body.payload })
    }
    throw new Error(`Unexpected destination request ${path}`)
  })
  return state
}

async function authorDraft(wrapper: VueWrapper) {
  await fillName(wrapper, 'Preserved Vesper')
  await wrapper.get('textarea[aria-label="Personality"]').setValue('Keeps promises')
  await toggleSubsection(wrapper, 'Combat')
  await labeled(wrapper, 'HP').get('input').setValue(37)
}

function expectDraft(wrapper: VueWrapper, editor: unknown) {
  expect(wrapper.findComponent(CharacterSheetEditor).vm.$).toBe(editor)
  expect(labeled(wrapper, 'Name').get('input').element.value).toBe('Preserved Vesper')
  expect(labeled(wrapper, 'HP').get('input').element.value).toBe('37')
}

function expectAuthoredPayload(campaignId: string) {
  expect(buildInBodies().at(-1)).toMatchObject({
    campaign_id: campaignId,
    payload: { key_figures: [{ name: 'Preserved Vesper', record: { personality: 'Keeps promises', stat_block: { combat: { hp: 37 } } } }] },
  })
}

describe('CharacterForgeView destination recovery', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    socketCalls.length = 0
    delete routeParams.id
  })

  it('keeps the selected world named above history and switches worlds with the same editor', async () => {
    destinationApi()
    const wrapper = await mountView()
    const editor = wrapper.findComponent(CharacterSheetEditor).vm.$
    await authorDraft(wrapper)
    await clickButton(wrapper, CAMPAIGN.title)
    const destination = wrapper.get('.destination-control')
    expect(destination.text()).toContain(CAMPAIGN.title)
    expect(wrapper.html().indexOf('destination-control')).toBeLessThan(wrapper.html().indexOf('Recent builds'))
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, SECOND_WORLD.title)
    expect(destination.text()).toContain(SECOND_WORLD.title)
    expectDraft(wrapper, editor)
    expect(socketCalls[0]!.disconnect).toHaveBeenCalledOnce()
    expect(socketCalls.at(-1)!.campaignId).toBe('C2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expectAuthoredPayload('C2')
    wrapper.unmount()
    expect(socketCalls.at(-1)!.disconnect).toHaveBeenCalledOnce()
  })

  it('returns to Generic with a visible theme and omits preserved world relations', async () => {
    destinationApi()
    const wrapper = await mountView()
    await authorDraft(wrapper)
    const editor = wrapper.findComponent(CharacterSheetEditor).vm.$
    await clickButton(wrapper, CAMPAIGN.title)
    await clickButton(wrapper, 'Add relation')
    await wrapper.get('.relation-block').findAll('select')[2]!.setValue('E1')
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, 'Use Generic library')
    expect(wrapper.get('.destination-control').text()).toContain('Generic library')
    await labeled(wrapper, 'Theme').get('select').setValue('Grimdark')
    expectDraft(wrapper, editor)
    expect(wrapper.find('.relation-block').exists()).toBe(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expectAuthoredPayload('G1')
    const payload = buildInBodies()[0]!.payload as { theme: string; key_figures: unknown[] }
    expect(payload.theme).toBe('Grimdark')
    expect(payload).not.toHaveProperty('places')
    expect(payload.key_figures[0]).not.toHaveProperty('relations')
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, CAMPAIGN.title)
    expect(wrapper.get('.relation-block').findAll('select')[2]!.element.value).toBe('E1')
  })

  it.each(['metadataError', 'historyError'] as const)('preserves authoring after %s and retries before submitting', async (failure) => {
    const state = destinationApi()
    const wrapper = await mountView()
    await authorDraft(wrapper)
    const editor = wrapper.findComponent(CharacterSheetEditor).vm.$
    state[failure] = true
    await clickButton(wrapper, CAMPAIGN.title)
    expectDraft(wrapper, editor)
    expect(wrapper.get('[role="alert"]').text()).toContain('unavailable')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('Recent builds')
    await wrapper.get('form').trigger('submit')
    expect(buildInBodies()).toHaveLength(0)
    await wrapper.get('textarea[aria-label="Secret"]').setValue('Edited during failure')
    state[failure] = false
    await clickButton(wrapper, 'Retry destination')
    expectDraft(wrapper, editor)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expectAuthoredPayload('C1')
    expect(buildInBodies()[0]).toMatchObject({ payload: { key_figures: [{ record: { secret: 'Edited during failure' } }] } })
  })

  it('keeps the pinned editor usable during a metadata failure and allows another destination', async () => {
    routeParams.id = 'C1'
    const state = destinationApi()
    state.metadataError = true
    const wrapper = await mountView()
    await authorDraft(wrapper)
    const editor = wrapper.findComponent(CharacterSheetEditor).vm.$
    expect(wrapper.get('[role="alert"]').text()).toContain('World unavailable')
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, SECOND_WORLD.title)
    expectDraft(wrapper, editor)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expectAuthoredPayload('C2')
  })

  it.each(['genericError', 'historyError'] as const)('does not enqueue when Generic binding encounters %s', async (failure) => {
    const state = destinationApi()
    state[failure] = true
    const wrapper = await mountView()
    await authorDraft(wrapper)
    const editor = wrapper.findComponent(CharacterSheetEditor).vm.$
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expectDraft(wrapper, editor)
    expect(buildInBodies()).toHaveLength(0)
    expect(wrapper.get('[role="alert"]').text()).toContain('unavailable')
    state[failure] = false
    await clickButton(wrapper, 'Retry destination')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expectAuthoredPayload('G1')
  })

  it('waits for history on retry and enforces the recovered active-build gate', async () => {
    const state = destinationApi()
    const wrapper = await mountView()
    await authorDraft(wrapper)
    state.historyError = true
    await clickButton(wrapper, CAMPAIGN.title)
    state.historyError = false
    state.delayedHistory = deferred()
    await clickButton(wrapper, 'Retry destination')
    await wrapper.get('form').trigger('submit')
    expect(buildInBodies()).toHaveLength(0)
    state.delayedHistory.resolve({ jobs: [job({ state: 'running' })], next_cursor: null })
    await flushPromises()
    expect(wrapper.get('button[type="submit"]').text()).toBe('Building…')
    await wrapper.get('form').trigger('submit')
    expect(buildInBodies()).toHaveLength(0)
  })

  it.each(['resolve', 'reject'] as const)('ignores obsolete metadata that later %ss', async (completion) => {
    const state = destinationApi()
    state.delayedMetadata = deferred()
    const wrapper = await mountView()
    await authorDraft(wrapper)
    await clickButton(wrapper, CAMPAIGN.title)
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, SECOND_WORLD.title)
    if (completion === 'resolve') state.delayedMetadata.resolve(CAMPAIGN)
    else state.delayedMetadata.reject(new ApiError(503, 'unavailable', 'Obsolete failure'))
    await flushPromises()
    expect(wrapper.get('.destination-control').text()).toContain(SECOND_WORLD.title)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(socketCalls.map((call) => call.campaignId)).toEqual(['C2'])
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expectAuthoredPayload('C2')
  })

  it('initializes a deferred theme after an early world selection and return to Generic', async () => {
    const state = destinationApi()
    state.delayedThemes = deferred()
    const wrapper = await mountView()
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, CAMPAIGN.title)
    state.delayedThemes.resolve({ themes: ['Grimdark'] })
    await flushPromises()
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, 'Use Generic library')
    expect(labeled(wrapper, 'Theme').get('select').element.value).toBe('Grimdark')
    await fillName(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(buildInBodies()[0]).toMatchObject({ campaign_id: 'G1', payload: { theme: 'Grimdark' } })
  })

  it('handles current socket progress and ignores obsolete messages, reconnects, and auth failures', async () => {
    destinationApi()
    const wrapper = await mountView()
    await clickButton(wrapper, CAMPAIGN.title)
    const obsolete = socketCalls.at(-1)!
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, SECOND_WORLD.title)
    const current = socketCalls.at(-1)!
    const jobs = useJobsStore()
    jobs.upsert(job({ id: 'J-current', campaign_id: 'C2', state: 'running', progress: 0.1 }))
    jobs.upsert(job({ id: 'J-old', campaign_id: 'C1', state: 'running', progress: 0.1 }))
    await flushPromises()
    current.onMessage({ type: 'job_progress', job_id: 'J-current', state: 'running', progress: 0.4 })
    await flushPromises()
    expect(wrapper.get('progress').attributes('value')).toBe('40')
    apiFetchMock.mockClear()
    obsolete.onMessage({ type: 'job_progress', job_id: 'J-old', state: 'running', progress: 0.8 })
    obsolete.options.onReconnect()
    obsolete.options.onAuthFailure()
    await flushPromises()
    expect(jobs.byId['J-old']!.progress).toBe(0.1)
    expect(wrapper.get('progress').attributes('value')).toBe('40')
    expect(apiFetchMock).not.toHaveBeenCalled()
    expect(pushMock).not.toHaveBeenCalled()
    current.options.onReconnect()
    await flushPromises()
    expect(apiFetchMock).toHaveBeenCalledWith('/api/jobs?campaign_id=C2')
  })

  it.each([CAMPAIGN.title, 'Use Generic library'])('returns picker focus after choosing %s', async (choice) => {
    destinationApi()
    const wrapper = mount(CharacterForgeView, { attachTo: document.body })
    await flushPromises()
    const disclosure = wrapper.findAll('button').find((button) => button.text() === 'Change destination')!
    expect(disclosure.attributes('aria-controls')).toBe('forge-destination-picker')
    expect(disclosure.attributes('aria-expanded')).toBe('true')
    const choiceButton = wrapper.findAll('button').find((button) => button.text() === choice)!
    choiceButton.element.focus()
    expect(document.activeElement).toBe(choiceButton.element)
    await choiceButton.trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(disclosure.element)
    expect(disclosure.attributes('aria-expanded')).toBe('false')
  })

  it('shows the preserved selected target explicitly when a retry export fails', async () => {
    const state = destinationApi()
    const wrapper = await mountView()
    await fillName(wrapper)
    await clickButton(wrapper, CAMPAIGN.title)
    await clickButton(wrapper, 'Add relation')
    await wrapper.get('.relation-block').findAll('select')[2]!.setValue('E1')
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, SECOND_WORLD.title)
    state.delayedEntities = deferred()
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, CAMPAIGN.title)
    state.delayedEntities.reject(new ApiError(503, 'unavailable', 'Export unavailable'))
    await flushPromises()
    const target = wrapper.get('.relation-block').findAll('select')[2]!
    expect(target.element.value).toBe('E1')
    expect(target.get('option[value="E1"]').text()).toContain('Selected target unavailable (E1)')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(buildInBodies()[0]).toMatchObject({ payload: { key_figures: [{ relations: [{ target_id: 'E1' }] }] } })
  })

  it('leaves relation options empty when the next world export fails', async () => {
    const state = destinationApi()
    const wrapper = await mountView()
    await clickButton(wrapper, CAMPAIGN.title)
    await clickButton(wrapper, 'Add relation')
    expect(wrapper.get('.relation-block').text()).toContain('Greymarch')
    state.entityError = true
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, SECOND_WORLD.title)
    await clickButton(wrapper, 'Add relation')
    expect(wrapper.get('.relation-block').findAll('select')[2]!.findAll('option')).toHaveLength(1)
  })

  it('does not connect late destination loads after the editor unmounts', async () => {
    const state = destinationApi()
    state.delayedMetadata = deferred()
    const wrapper = await mountView()
    await clickButton(wrapper, CAMPAIGN.title)
    wrapper.unmount()
    state.delayedMetadata.resolve(CAMPAIGN)
    await flushPromises()
    expect(socketCalls).toHaveLength(0)
    expect(buildInBodies()).toHaveLength(0)
  })

  it('clears relation options on a switch, ignores late exports, and restores original drafts', async () => {
    const state = destinationApi()
    const wrapper = await mountView()
    await fillName(wrapper)
    await clickButton(wrapper, CAMPAIGN.title)
    await clickButton(wrapper, 'Add relation')
    await wrapper.get('.relation-block').findAll('select')[2]!.setValue('E1')
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, SECOND_WORLD.title)
    await clickButton(wrapper, 'Add relation')
    expect(wrapper.get('.relation-block').text()).toContain('Silver Harbour')
    expect(wrapper.get('.relation-block').text()).not.toContain('Greymarch')
    state.delayedEntities = deferred()
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, CAMPAIGN.title)
    expect(wrapper.get('.relation-block').text()).not.toContain('Silver Harbour')
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, SECOND_WORLD.title)
    state.delayedEntities.resolve(WORLD)
    await flushPromises()
    expect(wrapper.get('.relation-block').text()).not.toContain('Greymarch')
    state.delayedEntities = null
    await clickButton(wrapper, 'Change destination')
    await clickButton(wrapper, CAMPAIGN.title)
    expect(wrapper.get('.relation-block').findAll('select')[2]!.element.value).toBe('E1')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(buildInBodies()[0]).toMatchObject({ campaign_id: 'C1', payload: { key_figures: [{ relations: [{ type: 'located_in', target_id: 'E1' }] }] } })
  })
})
