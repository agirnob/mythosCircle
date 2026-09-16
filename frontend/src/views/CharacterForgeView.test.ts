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
  created_at: '2026-09-01T00:00:00Z',
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
    kind: 'add_character',
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
 * uses — POST /api/characters (the gate) and the jobs list sync. */
function mockApi(options: { gateError?: ApiError } = {}) {
  apiFetchMock.mockImplementation(async (path: string) => {
    const url = String(path)
    if (url === '/api/campaigns/C1') return CAMPAIGN
    if (url === '/api/campaigns/C1/export') return WORLD
    if (url === '/api/characters') {
      if (options.gateError) throw options.gateError
      return { job_id: 'J9', state: 'queued', max_llm_calls: 0 }
    }
    if (url.startsWith('/api/jobs')) return { jobs: jobList, next_cursor: null }
    throw new Error(`unexpected fetch: ${url}`)
  })
}

async function mountView(): Promise<VueWrapper> {
  const wrapper = mount(CharacterForgeView)
  await flushPromises()
  return wrapper
}

function textInput(wrapper: VueWrapper, label: string) {
  const group = wrapper
    .findAll('label')
    .find((entry) => entry.text().startsWith(label))
  if (!group) throw new Error(`no field labelled ${label}`)
  return group.get('input')
}

describe('CharacterForgeView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    jobList = []
    mockApi()
  })

  it('submits the canonical sheet to the gate and shows the enqueued job', async () => {
    jobList = []
    const wrapper = await mountView()

    const name = textInput(wrapper, 'Name')
    await name.setValue('Seraphine')
    const personality = wrapper
      .findAll('label')
      .find((entry) => entry.text().startsWith('Personality'))
    await personality!.get('textarea').setValue('Glass and silver.')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/characters',
      expect.objectContaining({ method: 'POST' }),
    )
    const body = JSON.parse(apiFetchMock.mock.calls.find((c) => c[0] === '/api/characters')![1]!
      .body as string)
    expect(body.campaign_id).toBe('C1')
    expect(body.characters).toHaveLength(1)
    expect(body.characters[0].record.name).toBe('Seraphine')
    expect(body.characters[0].record.stat_block.attributes.str).toBe(10)
  })

  it('normalizes the spaced damage variant to the tight canonical form', async () => {
    const wrapper = await mountView()
    await textInput(wrapper, 'Name').setValue('Broken')

    // add an action with a spaced dice string
    // the SECOND '+ add' belongs to Actions (the first is Skills)
    const addButtons = wrapper.findAll('button').filter((b) => b.text() === '+ add')
    await addButtons[1]!.trigger('click')
    const placeholders = wrapper.findAll('input[placeholder="1d8+2 (blank = no damage roll)"]')
    await placeholders[0].setValue('2d6 + 2')
    const actionRow = wrapper.find('.action-row')
    await actionRow.find('input[placeholder="Longsword"]').setValue('Swipe')
    await actionRow
      .find('textarea')
      .setValue('Melee Weapon Attack: +4 to hit. Hit: 7 (2d6+2) damage.')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const body = JSON.parse(
      apiFetchMock.mock.calls.find((c) => c[0] === '/api/characters')![1]!.body as string,
    )
    expect(body.characters[0].record.stat_block.actions[0].damage).toBe('2d6+2')
  })

  it('renders the gate 422 as field-level violations and keeps the form', async () => {
    mockApi({
      gateError: new ApiError(422, 'validation_error', 'add_character payload invalid', {
        violations: [
          'characters[0].record.name must not be blank',
          'characters[0].record.stat_block.actions[0].damage',
        ],
      }),
    })
    const wrapper = await mountView()
    await textInput(wrapper, 'Name').setValue('Broken')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const items = wrapper.findAll('li').map((li) => li.text())
    expect(items).toContain('characters[0].record.name must not be blank')
    expect(items).toContain('characters[0].record.stat_block.actions[0].damage')
    expect((textInput(wrapper, 'Name').element as HTMLInputElement).value).toBe('Broken')
  })


  it('disables the submit button while a commit is in flight', async () => {
    jobList = [job({ id: 'J2', state: 'running' })]
    const wrapper = await mountView()
    await textInput(wrapper, 'Name').setValue('Seraphine')

    const button = wrapper.find('button[type="submit"]')
    expect(button.attributes('disabled')).toBeDefined()
  })

  it('adds the boss section for Monster and removes it for NPC', async () => {
    const wrapper = await mountView()
    expect(wrapper.text()).not.toContain('Lair actions')

    const roleSelect = wrapper.findAll('select')[0]
    await roleSelect.setValue('Monster')
    expect(wrapper.text()).toContain('Lair actions')

    await roleSelect.setValue('NPC')
    expect(wrapper.text()).not.toContain('Lair actions')
  })

  it('sends only relations with a chosen target and includes the counter', async () => {
    const wrapper = await mountView()
    await textInput(wrapper, 'Name').setValue('Seraphine')

    await wrapper.findAll('button').find((b) => b.text() === '+ relation')!.trigger('click')
    const relationRow = wrapper.find('.row.relation')
    const selects = relationRow.findAll('select')
    await selects[0].setValue('located_in')
    await selects[1].setValue('E1')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const body = JSON.parse(
      apiFetchMock.mock.calls.find((c) => c[0] === '/api/characters')![1]!.body as string,
    )
    expect(body.characters[0].relations).toEqual([{ type: 'located_in', target_id: 'E1' }])
  })

  it('shows what a succeeded commit produced', async () => {
    jobList = [
      job({
        id: 'J3',
        state: 'succeeded',
        result: { entity_ids: ['E10'], edges: [{ src: 'E10', dst: 'E1', type: 'located_in' }] },
      }),
    ]
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('1 character')
    expect(wrapper.text()).toContain('+ 1 relation')
  })
})
