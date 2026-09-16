// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import type { components } from '../api/schema'

type Job = components['schemas']['JobResponse']

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
function mockApi(options: { gateError?: ApiError } = {}) {
  apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
    const url = String(path)
    if (url === '/api/campaigns/C1') return CAMPAIGN
    if (url === '/api/campaigns/C1/export') return WORLD
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
    await toggleSubsection(wrapper, 'Identity')
    await toggleSubsection(wrapper, 'Actions')
    await wrapper.findAll('button').find((b) => b.text() === '+ add')!.trigger('click')

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
    const block = (figure.record as Record<string, unknown>).stat_block as Record<string, unknown>
    // only the toggled subsections ride along
    expect(Object.keys(block)).toEqual(['identity', 'actions'])
    expect((block.identity as Record<string, unknown>).race).toBe('Tiefling')
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
})
