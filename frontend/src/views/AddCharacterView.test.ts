// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

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
import AddCharacterView from './AddCharacterView.vue'

const EXPORT = {
  campaign: { id: 'C1', title: 'Greymarch' },
  revision: { id: '01JZZZZZZZZZZZZZZZZZZZZZZZ', created_at: '2026-09-01T00:00:00Z' },
  entities: [
    { id: 'E1', kind: 'place', name: 'The Gilded Bar', text: '', data: {}, media: [] },
    { id: 'E2', kind: 'character', name: 'Mira Vane', text: '', data: {}, media: [] },
  ],
  edges: [],
}

function stubApi(submit: (init: RequestInit) => unknown = () => ({})) {
  apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
    const url = String(path)
    if (url.includes('/export')) return EXPORT
    if (url.includes('/api/jobs')) return { jobs: [], next_cursor: null }
    if (url === '/api/characters' && init?.method === 'POST') return submit(init!)
    throw new Error(`unexpected fetch: ${url}`)
  })
}

async function mountView(): Promise<VueWrapper> {
  const wrapper = mount(AddCharacterView)
  await flushPromises()
  return wrapper
}

/** Fill every required field of the FIRST sheet as a level-5 Human
 * fighter — everything the canonical mirror demands. */
async function fillCompleteSheet(wrapper: VueWrapper) {
  await wrapper.find('input[aria-label="Character name"]').setValue('Seraphine')
  await wrapper.find('select[aria-label="Role"]').setValue('NPC')
  await wrapper.find('select[aria-label="Race / type"]').setValue('Human')
  await wrapper.find('input[aria-label="Level"]').setValue('5')
  await wrapper.find('select[aria-label="Class / profession"]').setValue('Fighter')
  await wrapper.find('select[aria-label="Alignment"]').setValue('LG')
  for (const label of [
    'Personality',
    'Secret',
    'Rumor',
    'Party hook',
    'Appearance',
    'Background',
    'Goals',
    'Relationships',
    'Voice style',
    'Catchphrases',
  ]) {
    await wrapper.find(`textarea[aria-label="${label}"]`).setValue('prose for ' + label)
  }
  for (const label of [
    'world_integration.reputation',
    'world_integration.factions',
    'world_integration.current_location',
    'world_integration.reaction_matrix',
    'world_integration.on_defeat',
  ]) {
    await wrapper.find(`textarea[aria-label="${label}"]`).setValue('world text')
  }
  for (const attr of ['str', 'dex', 'con', 'int', 'wis', 'cha']) {
    await wrapper.find(`input[aria-label="attributes.${attr}"]`).setValue('10')
  }
  await wrapper.find('input[aria-label="combat.ac"]').setValue('16')
  await wrapper.find('input[aria-label="combat.hp"]').setValue('44')
  await flushPromises()
}

function submitButton(wrapper: VueWrapper) {
  return wrapper.findAll('button').filter((button) => button.attributes('type') === 'submit')[0]!
}

describe('AddCharacterView — the F3 frontend gate + POST /api/characters', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    apiFetchMock.mockReset()
    socketCalls.length = 0
  })

  it('removes the selected authored sheet and preserves sibling identity and staged keys', async () => {
    stubApi()
    const wrapper = await mountView()
    await wrapper.get('input[aria-label="Character name"]').setValue('First character')
    const add = wrapper.findAll('button').find((button) => button.text().includes('Add another'))!
    await add.trigger('click')
    await flushPromises()
    await wrapper.findAll('input[aria-label="Character name"]')[1]!.setValue('Second character')
    const forms = wrapper.findAllComponents({ name: 'AuthorSheetForm' })
    const second = forms[1]!.vm
    const key = (second as unknown as { sheetKey: string }).sheetKey
    await wrapper.findAll('button').find((button) => button.text() === 'Remove character 1')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('input[aria-label="Character name"]').element).toHaveProperty('value', 'Second character')
    expect(wrapper.findComponent({ name: 'AuthorSheetForm' }).element).toBe(forms[1]!.element)
    expect((wrapper.findComponent({ name: 'AuthorSheetForm' }).vm as unknown as { sheetKey: string }).sheetKey).toBe(key)
    wrapper.unmount()
  })

  it('mounts one empty sheet with submit disabled and NO violation wall until touched', async () => {
    stubApi()
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('Greymarch')
    expect(submitButton(wrapper).attributes('disabled')).toBeDefined()
    // The violation wall is touch-gated (2026-09-27): a freshly mounted
    // empty form shows the hint line, not 24 red schema paths.
    expect(wrapper.text()).not.toContain('record.personality must be a non-blank string')
    expect(wrapper.text()).toContain('Fill the required fields')
    // The first edit surfaces the mirror's per-sheet list inline.
    await wrapper.find('textarea[aria-label="Personality"]').setValue('steely')
    await flushPromises()
    expect(wrapper.text()).toContain('record.secret must be a non-blank string')
    expect(wrapper.text()).toContain(
      'record.stat_block.attributes section missing or not an object',
    )
    wrapper.unmount()
  })

  it('a complete sheet enables submit and POSTs the canonical payload to /api/characters', async () => {
    const posted: Array<{ url: string; body: unknown }> = []
    stubApi((init) => {
      posted.push({ url: '/api/characters', body: JSON.parse(String(init.body)) })
      return { job_id: 'J1', state: 'queued', max_llm_calls: 0 }
    })
    const wrapper = await mountView()
    await fillCompleteSheet(wrapper)
    await flushPromises()
    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(posted).toHaveLength(1)
    const body = posted[0]!.body as {
      campaign_id: string
      characters: Array<Record<string, unknown>>
    }
    expect(body.campaign_id).toBe('C1')
    expect(body.characters).toHaveLength(1)
    const sheet = body.characters[0]!
    expect(sheet.key).toBe('staged-1')
    const record = sheet.record as Record<string, unknown>
    expect(record.name).toBe('Seraphine')
    expect(record.role).toBe('NPC')
    expect(record.level_cr).toBe('level 5')
    expect(record.race_type).toBe('Human')
    expect(record.class_profession).toBe('Fighter')
    expect(record.alignment).toBe('LG')
    // The identity is ONE fact in two slots: block identity mirrors the record.
    const block = record.stat_block as Record<string, unknown>
    expect((block.identity as Record<string, unknown>).role).toBe('NPC')
    expect((block.identity as Record<string, unknown>).level).toBe(5)
    expect((block.identity as Record<string, unknown>).race).toBe('Human')
    expect(sheet.relations).toBeUndefined() // no relations staged → key absent
    expect(wrapper.text()).toContain('Job started')
    expect(wrapper.text()).toContain('J1')
    wrapper.unmount()
  })

  it('a Tier-1 relation rides the payload as target_id', async () => {
    const posted: Array<Record<string, unknown>> = []
    stubApi((init) => {
      posted.push(JSON.parse(String(init.body)) as Record<string, unknown>)
      return { job_id: 'J1', state: 'queued', max_llm_calls: 0 }
    })
    const wrapper = await mountView()
    await fillCompleteSheet(wrapper)
    const addRelation = wrapper.findAll('button').filter((b) => b.text() === 'Add relation')[0]!
    await addRelation.trigger('click')
    await flushPromises()
    // Tier 1 is the default; search narrows the committed-entity list.
    const search = wrapper.find('input[aria-label="Search committed entities"]')
    await search.setValue('Mira')
    await flushPromises()
    const target = wrapper.find('select[aria-label="Target entity"]')
    expect(target.exists()).toBe(true)
    await target.setValue('E2')
    await flushPromises()
    expect(wrapper.text()).toContain('Mira Vane (#E2)')

    await wrapper.find('form').trigger('submit')
    await flushPromises()
    const sheet = (posted[0]!.characters as Array<Record<string, unknown>>)[0]!
    expect(sheet.relations).toEqual([{ type: 'relationship', target_id: 'E2' }])
    wrapper.unmount()
  })

  it('the named-target tier resolves an exact match via "Did you mean" to a Tier-1 binding', async () => {
    const posted: Array<Record<string, unknown>> = []
    stubApi((init) => {
      posted.push(JSON.parse(String(init.body)) as Record<string, unknown>)
      return { job_id: 'J1', state: 'queued', max_llm_calls: 0 }
    })
    const wrapper = await mountView()
    await fillCompleteSheet(wrapper)
    const addRelation = wrapper.findAll('button').filter((b) => b.text() === 'Add relation')[0]!
    await addRelation.trigger('click')
    await flushPromises()
    // Switch to the named-target tier and type a name that normalized-
    // matches the committed "Mira Vane" (case/whitespace insensitive).
    await wrapper.find('input[value="name"]').setValue(true)
    await flushPromises()
    const named = wrapper.find('input[aria-label="Named target"]')
    await named.setValue('mira   vane')
    await flushPromises()
    expect(wrapper.text()).toContain('Did you mean Mira Vane (#E2)?')
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Use this entity')[0]!
      .trigger('click')
    await flushPromises()

    await wrapper.find('form').trigger('submit')
    await flushPromises()
    const sheet = (posted[0]!.characters as Array<Record<string, unknown>>)[0]!
    // The path-2 guardrail: the wire carries target_id, NEVER target_name.
    expect(sheet.relations).toEqual([{ type: 'relationship', target_id: 'E2' }])
    wrapper.unmount()
  })

  it('a 422 surfaces the server schema-path violations inline', async () => {
    stubApi(() => {
      throw new ApiError(
        422,
        'invalid_payload',
        'add_character payload invalid: characters[0].record.personality must be a non-blank string',
        {
          violations: ['characters[0].record.personality must be a non-blank string'],
        },
      )
    })
    const wrapper = await mountView()
    await fillCompleteSheet(wrapper)
    // Force a server-side rejection regardless of the client gate: the
    // form is valid, so submit is enabled — the API is the final check.
    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('characters[0].record.personality must be a non-blank string')
    wrapper.unmount()
  })

  it('a second sheet enables Tier-2 relations by its staged key', async () => {
    const posted: Array<Record<string, unknown>> = []
    stubApi((init) => {
      posted.push(JSON.parse(String(init.body)) as Record<string, unknown>)
      return { job_id: 'J1', state: 'queued', max_llm_calls: 0 }
    })
    const wrapper = await mountView()
    await fillCompleteSheet(wrapper)
    await wrapper
      .findAll('button')
      .filter((b) => b.text() === 'Add another character to this batch')[0]!
      .trigger('click')
    await flushPromises()

    // Fill the SECOND sheet (index 1) minimally but completely.
    const nameInputs = wrapper.findAll('input[aria-label="Character name"]')
    await nameInputs[1]!.setValue('Ash')
    const roleSelects = wrapper.findAll('select[aria-label="Role"]')
    await roleSelects[1]!.setValue('NPC')
    const raceSelects = wrapper.findAll('select[aria-label="Race / type"]')
    await raceSelects[1]!.setValue('Human')
    const levelInputs = wrapper.findAll('input[aria-label="Level"]')
    await levelInputs[1]!.setValue('3')
    const classSelects = wrapper.findAll('select[aria-label="Class / profession"]')
    await classSelects[1]!.setValue('Rogue')
    const alignmentSelects = wrapper.findAll('select[aria-label="Alignment"]')
    await alignmentSelects[1]!.setValue('N')
    for (const label of [
      'Personality',
      'Secret',
      'Rumor',
      'Party hook',
      'Appearance',
      'Background',
      'Goals',
      'Relationships',
      'Voice style',
      'Catchphrases',
    ]) {
      const texts = wrapper.findAll(`textarea[aria-label="${label}"]`)
      await texts[1]!.setValue('prose')
    }
    for (const label of [
      'world_integration.reputation',
      'world_integration.factions',
      'world_integration.current_location',
      'world_integration.reaction_matrix',
      'world_integration.on_defeat',
    ]) {
      const texts = wrapper.findAll(`textarea[aria-label="${label}"]`)
      await texts[1]!.setValue('world text')
    }
    for (const attr of ['str', 'dex', 'con', 'int', 'wis', 'cha']) {
      const inputs = wrapper.findAll(`input[aria-label="attributes.${attr}"]`)
      await inputs[1]!.setValue('10')
    }
    const acInputs = wrapper.findAll('input[aria-label="combat.ac"]')
    await acInputs[1]!.setValue('14')
    const hpInputs = wrapper.findAll('input[aria-label="combat.hp"]')
    await hpInputs[1]!.setValue('30')
    await flushPromises()

    // Sheet 1 declares a relation targeting staged-2 (Sheet 2).
    const addRelations = wrapper.findAll('button').filter((b) => b.text() === 'Add relation')
    await addRelations[0]!.trigger('click')
    await flushPromises()
    await wrapper.find('input[value="staged"]').setValue(true)
    await flushPromises()
    const staged = wrapper.find('select[aria-label="Staged target sheet"]')
    expect(staged.exists()).toBe(true)
    await staged.setValue('staged-2')
    await flushPromises()

    expect(submitButton(wrapper).attributes('disabled')).toBeUndefined()
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    const characters = posted[0]!.characters as Array<Record<string, unknown>>
    expect(characters).toHaveLength(2)
    expect((characters[0]!.relations as Array<Record<string, unknown>>)[0]).toEqual({
      type: 'relationship',
      target_key: 'staged-2',
    })
    wrapper.unmount()
  })
})
