// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { components } from '../api/schema'
import { useAuthStore } from '../stores/auth'
import { useCampaignsStore } from '../stores/campaigns'
import { useTonightStore } from '../stores/tonight'
import { useWorldStore } from '../stores/world'
import TonightView from './TonightView.vue'

beforeEach(() => vi.restoreAllMocks())
afterEach(() => vi.unstubAllGlobals())

function campaign(id: string): components['schemas']['CampaignResponse'] {
  return {
    id,
    owner_id: 'A',
    title: id === 'C1' ? 'First campaign title' : 'Second campaign title',
    description: '',
    theme: '',
    custom_lore: '',
    is_generic: false,
    created_at: '2026-10-04T12:00:00Z',
  }
}

async function setup(fetchCampaign?: (id: string) => Promise<void>, legacy = '') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().account = { id: 'A', email: 'a@example.com' }
  const stored = new Map<string, string>()
  if (legacy) stored.set('mythoscircle:tonight:A:C1', legacy)
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
    removeItem: (key: string) => stored.delete(key),
  })
  const world = useWorldStore()
  vi.spyOn(world, 'load').mockResolvedValue()
  const campaigns = useCampaignsStore()
  const fetchOne = vi.spyOn(campaigns, 'fetchOne')
  if (fetchCampaign) fetchOne.mockImplementation(fetchCampaign)
  else fetchOne.mockResolvedValue()
  const tonight = useTonightStore()
  vi.spyOn(tonight, 'fetchKinds').mockResolvedValue()
  const load = vi.spyOn(tonight, 'load').mockResolvedValue()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/campaigns/:id/tonight', name: 'tonight', component: TonightView },
      { path: '/campaigns/:id', name: 'overview', component: { template: '<div />' } },
      {
        path: '/campaigns/:id/entities/:entityId',
        name: 'entity',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push('/campaigns/C1/tonight')
  const wrapper = mount(TonightView, { global: { plugins: [pinia, router] } })
  return { wrapper, router, tonight, load, stored, world, campaigns }
}

describe('TonightView session notes integration', () => {
  it('waits for loaded projections without a standalone browser editor', async () => {
    const { wrapper, tonight, stored } = await setup()
    expect(wrapper.text()).toContain('Loading Tonight')
    expect(wrapper.find('textarea').exists()).toBe(false)
    tonight.ensureEntry('C1').error = 'Unavailable'
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('Could not load Tonight')
    expect(stored.size).toBe(0)
    wrapper.unmount()
  })

  it('uses the current route campaign when the mounted view is reused', async () => {
    const { wrapper, router, load, stored, world, tonight, campaigns } = await setup()
    for (const [id, entityId, name, session, knowledge, detail] of [
      [
        'C1',
        'E1',
        'Mira Vane',
        { defeated: true, hp: 8, notes: 'First saved notes' },
        { secret: true },
        'Marked defeated',
      ],
      [
        'C2',
        'E2',
        'Captain Sol',
        { thread: true, hp: 22, notes: 'Second saved notes' },
        { rumor: false },
        'Resolved thread',
      ],
    ] as const) {
      world.byCampaign[id] = {
        world: {
          campaign: campaign(id),
          revision: null,
          entities: [{ id: entityId, name, kind: 'character', text: '', data: {}, media: [] }],
          edges: [],
        },
        loading: false,
        error: null,
        notFound: false,
        fetching: false,
        dirty: false,
      }
      const entry = tonight.ensureEntry(id)
      entry.runState = { session: { [entityId]: session }, knowledge: { [entityId]: knowledge } }
      entry.revisions = [
        {
          revision_id: `R-${id}`,
          created_at: '2026-10-04T12:00:00Z',
          events: [
            {
              revision_id: `R-${id}`,
              created_at: '2026-10-04T12:00:00Z',
              actor: 'dm',
              action: 'edited',
              kind: 'session',
              target_names: [name],
              details: [detail],
            },
          ],
        },
      ]
    }
    campaigns.current = campaign('C1')
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.mc-tonight-state').text()).toContain('Mira Vane')
    expect(wrapper.get('.mc-feed').text()).toContain('Marked defeated')
    expect(wrapper.get('.mc-page-description').text()).toContain('First campaign title')
    await wrapper.get('textarea').setValue('First campaign notes')
    stored.set('mythoscircle:tonight:A:C2', 'Recovered second campaign notes')
    await router.push('/campaigns/C2/tonight')
    await wrapper.vm.$nextTick()
    expect(load).toHaveBeenLastCalledWith('C2')
    const state = wrapper.get('.mc-tonight-state').text()
    expect(state).toContain('Captain Sol')
    expect(state).toMatch(/thread:\s*yes/)
    expect(state).toMatch(/hp:\s*22/)
    expect(state).toMatch(/rumor:\s*secret/)
    expect(wrapper.get('.mc-feed').text()).toContain('Resolved thread')
    expect(wrapper.text()).not.toContain('Mira Vane')
    expect(wrapper.text()).not.toContain('Marked defeated')
    expect(wrapper.text()).not.toContain('defeated:')
    expect(wrapper.text()).not.toContain('First campaign title')
    expect(wrapper.find('.mc-page-description').exists()).toBe(false)
    const links = wrapper.findAll('a').map((link) => link.attributes('href'))
    expect(links).toContain('/campaigns/C2')
    expect(links).toContain('/campaigns/C2/entities/E2')
    expect(links.some((href) => href?.includes('/campaigns/C1'))).toBe(false)
    expect(wrapper.get('textarea').element.value).toBe('Second saved notes')
    expect(wrapper.text()).toContain('Recovered second campaign notes')
    expect(state).not.toContain('notes:')
    await wrapper.get('textarea').setValue('Updated second campaign')
    expect(stored.has('mythoscircle:tonight:A:C1')).toBe(false)
    expect(stored.get('mythoscircle:tonight:A:C2')).toBe('Recovered second campaign notes')
    wrapper.unmount()
  })

  it('hides a late campaign response when it does not match the active route', async () => {
    const responses = new Map<string, () => void>()
    const { wrapper, router } = await setup(
      (id) =>
        new Promise<void>((resolve) => {
          responses.set(id, () => {
            useCampaignsStore().current = campaign(id)
            resolve()
          })
        }),
    )
    expect(responses.has('C1')).toBe(true)
    await router.push('/campaigns/C2/tonight')
    expect(responses.has('C2')).toBe(true)
    responses.get('C2')!()
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.mc-page-description').text()).toContain('Second campaign title')
    responses.get('C1')!()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.mc-page-description').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('First campaign title')
    expect(wrapper.get('.mc-page-actions a').attributes('href')).toBe('/campaigns/C2')
    wrapper.unmount()
  })
})

async function seedView(legacy = '') {
  const context = await setup(undefined, legacy)
  context.world.byCampaign.C1 = {
    world: {
      campaign: campaign('C1'),
      revision: null,
      entities: [
        { id: 'E1', name: 'Blacksmith', kind: 'character', text: '', data: {}, media: [] },
        { id: 'E2', name: 'Harbor', kind: 'place', text: '', data: {}, media: [] },
        { id: 'E3', name: 'Inn', kind: 'place', text: '', data: {}, media: [] },
      ],
      edges: [],
    },
    loading: false,
    error: null,
    notFound: false,
    fetching: false,
    dirty: false,
  }
  context.tonight.ensureEntry('C1').runState = {
    session: {
      E1: { defeated: true, notes: 'Existing entity notes' },
      deleted: { notes: 'Deleted notes' },
    },
    knowledge: {},
  }
  await context.wrapper.vm.$nextTick()
  return context
}

describe('Entity notes and legacy recovery', () => {
  it('renders notes beside live state rows and opens an editor for a note-only entity', async () => {
    const { wrapper } = await seedView()
    expect(wrapper.findAll('textarea')).toHaveLength(1)
    expect(wrapper.get('textarea').element.value).toBe('Existing entity notes')
    expect(wrapper.text()).not.toContain('Deleted notes')
    expect(wrapper.text()).not.toContain('notes:')
    await wrapper.get('#notes-entity').setValue('E2')
    expect(wrapper.findAll('textarea')).toHaveLength(2)
    expect(wrapper.text()).toContain('Notes for Harbor')
    wrapper.unmount()
  })

  it('appends recovered text only after explicit target selection and removes only the unchanged browser entry', async () => {
    const { wrapper, tonight, stored } = await seedView('Browser notes\nLine two')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ session: { E1: { notes: 'Fresh existing' } }, knowledge: {} })),
    )
    const save = vi.spyOn(tonight, 'saveNotes').mockResolvedValue({ refreshed: true })
    const recover = () =>
      wrapper.findAll('button').find((b) => b.text() === 'Append recovered notes')!
    expect(recover().attributes('disabled')).toBeDefined()
    expect(save).not.toHaveBeenCalled()
    await wrapper.get('#recovery-target').setValue('E1')
    await recover().trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(
      'C1',
      'E1',
      'Fresh existing\n\nBrowser notes\nLine two',
      'Fresh existing',
    )
    expect(stored.has('mythoscircle:tonight:A:C1')).toBe(false)
    expect(wrapper.text()).toContain('Recovered notes saved to the entity')
    wrapper.unmount()
  })

  it('preserves recovered text on failed save and concurrent browser edits', async () => {
    const { wrapper, tonight, stored } = await seedView('Browser draft')
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () => new Response(JSON.stringify({ session: {}, knowledge: {} })),
    )
    const save = vi.spyOn(tonight, 'saveNotes').mockRejectedValue(new Error('Offline'))
    await wrapper.get('#recovery-target').setValue('E2')
    const recover = () =>
      wrapper.findAll('button').find((b) => b.text() === 'Append recovered notes')!
    await recover().trigger('click')
    await flushPromises()
    expect(stored.get('mythoscircle:tonight:A:C1')).toBe('Browser draft')
    expect(wrapper.text()).toContain('Recovery not saved')
    save.mockImplementation(async () => {
      stored.set('mythoscircle:tonight:A:C1', 'Changed browser draft')
      return { refreshed: true }
    })
    await recover().trigger('click')
    await flushPromises()
    expect(stored.get('mythoscircle:tonight:A:C1')).toBe('Changed browser draft')
    expect(wrapper.get('pre').text()).toBe('Changed browser draft')
    wrapper.unmount()
  })
})

it('ignores recovery completion after leaving and returning to the campaign', async () => {
  const { wrapper, tonight, stored, router } = await seedView('Browser notes')
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ session: {}, knowledge: {} })),
  )
  let resolve!: (value: { refreshed: boolean }) => void
  vi.spyOn(tonight, 'saveNotes').mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r
      }),
  )
  await wrapper.get('#recovery-target').setValue('E2')
  await wrapper
    .findAll('button')
    .find((b) => b.text() === 'Append recovered notes')!
    .trigger('click')
  await flushPromises()
  await router.push('/campaigns/C2/tonight')
  await router.push('/campaigns/C1/tonight')
  resolve({ refreshed: true })
  await flushPromises()
  expect(stored.get('mythoscircle:tonight:A:C1')).toBe('Browser notes')
  expect(wrapper.text()).not.toContain('Recovered notes saved')
  wrapper.unmount()
})

it('keeps chosen stateless entity editors and drafts mounted until the campaign scope changes', async () => {
  const { wrapper, router } = await seedView()
  await wrapper.get('#notes-entity').setValue('E2')
  await wrapper.findAll('textarea')[1]!.setValue('Harbor draft')
  await wrapper.get('#notes-entity').setValue('E3')
  await wrapper.findAll('textarea')[2]!.setValue('Inn draft')
  await wrapper.get('#notes-entity').setValue('E2')
  expect(wrapper.findAll('textarea')).toHaveLength(3)
  expect(wrapper.findAll('textarea')[1]!.element.value).toBe('Harbor draft')
  expect(wrapper.findAll('textarea')[2]!.element.value).toBe('Inn draft')
  await router.push('/campaigns/C2/tonight')
  await router.push('/campaigns/C1/tonight')
  expect(wrapper.findAll('textarea')).toHaveLength(1)
  wrapper.unmount()
})

it.each(['Changed source', ''])(
  'aborts recovery when the browser source changes before append: %s',
  async (source) => {
    const { wrapper, tonight, stored } = await seedView('Displayed old source')
    const save = vi.spyOn(tonight, 'saveNotes').mockResolvedValue({ refreshed: true })
    const fetcher = vi.spyOn(globalThis, 'fetch')
    stored.set('mythoscircle:tonight:A:C1', source)
    await wrapper.get('#recovery-target').setValue('E2')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Append recovered notes')!
      .trigger('click')
    expect(fetcher).not.toHaveBeenCalled()
    expect(save).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Browser notes changed or were removed')
    if (source) expect(wrapper.get('pre').text()).toBe(source)
    else expect(wrapper.find('pre').exists()).toBe(false)
    wrapper.unmount()
  },
)

it.each(['cleanup failure', 'lost response'])(
  'recovery retries after %s do not append duplicate notes',
  async (failure) => {
    const { wrapper, tonight, stored } = await seedView('Legacy note')
    let server = 'Existing prose'
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () =>
        new Response(JSON.stringify({ session: { E1: { notes: server } }, knowledge: {} })),
    )
    vi.spyOn(tonight, 'refreshProjections').mockImplementation(async () => ({
      refreshed: true,
      runState: { session: { E1: { notes: server } }, knowledge: {} },
    }))
    const save = vi
      .spyOn(tonight, 'saveNotes')
      .mockImplementation(async (_campaign, _entity, notes) => {
        server = notes
        if (failure === 'lost response') throw new Error('Response lost')
        return { refreshed: true }
      })
    const remove = vi.spyOn(globalThis.localStorage, 'removeItem')
    if (failure === 'cleanup failure')
      remove.mockImplementationOnce(() => {
        throw new Error('Storage blocked')
      })
    await wrapper.get('#recovery-target').setValue('E1')
    const append = () =>
      wrapper.findAll('button').find((b) => b.text() === 'Append recovered notes')!
    await append().trigger('click')
    await flushPromises()
    expect(stored.get('mythoscircle:tonight:A:C1')).toBe('Legacy note')
    if (failure === 'cleanup failure')
      expect(wrapper.text()).toContain('saved to the entity. Could not remove the browser copy')
    await append().trigger('click')
    await flushPromises()
    expect(server).toBe('Existing prose\n\nLegacy note')
    expect(save).toHaveBeenCalledTimes(1)
    expect(stored.has('mythoscircle:tonight:A:C1')).toBe(false)
    expect(wrapper.text()).toContain('Recovered notes saved to the entity')
    wrapper.unmount()
  },
)

it('keeps oversized recovered notes copyable and counts non-BMP recovery text correctly', async () => {
  const legacy = '😀'.repeat(20001)
  const { wrapper, tonight, stored } = await seedView(legacy)
  const save = vi.spyOn(tonight, 'saveNotes').mockResolvedValue({ refreshed: true })
  await wrapper.get('#recovery-target').setValue('E2')
  await wrapper
    .findAll('button')
    .find((b) => b.text() === 'Append recovered notes')!
    .trigger('click')
  expect(wrapper.text()).toContain('Recovered browser notes exceed 20,000 characters')
  expect(wrapper.get('pre').text()).toBe(legacy)
  expect(stored.get('mythoscircle:tonight:A:C1')).toBe(legacy)
  expect(save).not.toHaveBeenCalled()
  wrapper.unmount()
  const valid = '😀'.repeat(20000)
  const second = await seedView(valid)
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ session: {}, knowledge: {} })),
  )
  const saveValid = vi.spyOn(second.tonight, 'saveNotes').mockResolvedValue({ refreshed: true })
  await second.wrapper.get('#recovery-target').setValue('E2')
  await second.wrapper
    .findAll('button')
    .find((b) => b.text() === 'Append recovered notes')!
    .trigger('click')
  await flushPromises()
  expect(saveValid).toHaveBeenCalledWith('C1', 'E2', valid, '')
  second.wrapper.unmount()
})
