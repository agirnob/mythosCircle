// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { components } from '../api/schema'
import { ApiError } from '../api/client'
import { useWorldStore } from '../stores/world'
import { useCampaignsStore } from '../stores/campaigns'
import { useTonightStore } from '../stores/tonight'
import NewEntityView from './NewEntityView.vue'

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>', props: ['to'] },
  useRoute: () => ({ params: { id: 'C1', entityId: 'E1' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

function snapshot(
  revision: string | null,
  background = 'Opening prose',
): components['schemas']['WorldExport'] {
  return {
    campaign: {
      id: 'C1',
      title: 'Campaign',
      theme: '',
      description: '',
      custom_lore: '',
      is_generic: false,
      created_at: '2026-09-01T00:00:00Z',
    },
    revision: revision ? { id: revision, created_at: '2026-09-01T00:00:00Z' } : null,
    entities: [
      { id: 'E1', kind: 'character', name: 'Captain', text: '', media: [], data: { background } },
    ],
    edges: [],
  }
}

function setup(revision: string | null = 'R1') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const world = useWorldStore()
  world.byCampaign.C1 = {
    world: snapshot(revision),
    loading: false,
    error: null,
    notFound: false,
    fetching: false,
    dirty: false,
  }
  vi.spyOn(world, 'load').mockResolvedValue(undefined)
  vi.spyOn(world, 'fetchMedia').mockResolvedValue(undefined)
  vi.spyOn(useCampaignsStore(), 'fetchOne').mockResolvedValue(undefined)
  vi.spyOn(useTonightStore(), 'fetchKinds').mockResolvedValue(undefined)
  vi.spyOn(useTonightStore(), 'load').mockResolvedValue(undefined)
  const update = vi.spyOn(world, 'updateEntity').mockResolvedValue(undefined)
  const refetch = vi.spyOn(world, 'requestRefetch').mockImplementation(async () => {
    world.byCampaign.C1!.world = snapshot('R3', 'Conflict snapshot')
  })
  const wrapper = mount(NewEntityView, {
    global: {
      plugins: [pinia],
      stubs: { VueFlowGraph: true, EntityMediaActions: true },
    },
  })
  return { world, update, refetch, wrapper }
}

beforeEach(() => vi.restoreAllMocks())

describe('NewEntityView edit sessions', () => {
  it('pins saves and conflict retries to edit-open revision and preserves the draft', async () => {
    const { wrapper, world, update, refetch } = setup()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Edit')!
      .trigger('click')
    await wrapper.get('[aria-label="Background"]').setValue('My draft')
    world.byCampaign.C1!.world = snapshot('R2', 'Refetched prose')
    await flushPromises()
    update.mockRejectedValueOnce(new ApiError(409, 'revision_conflict', 'World changed'))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenLastCalledWith(
      'C1',
      'E1',
      expect.objectContaining({ background: 'My draft' }),
      'R1',
    )
    expect(refetch).toHaveBeenCalledWith('C1')
    expect(wrapper.get('[role="alert"]').text()).toBe('World changed')
    expect((wrapper.get('[aria-label="Background"]').element as HTMLTextAreaElement).value).toBe(
      'My draft',
    )
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenLastCalledWith(
      'C1',
      'E1',
      expect.objectContaining({ background: 'My draft' }),
      'R1',
    )
    expect(wrapper.find('form').exists()).toBe(false)
    wrapper.unmount()
  })

  it('captures a fresh revision and draft after cancel/reopen', async () => {
    const { wrapper, world, update } = setup()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Edit')!
      .trigger('click')
    await wrapper.get('[aria-label="Background"]').setValue('Discard this')
    world.byCampaign.C1!.world = snapshot('R2', 'New committed prose')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Cancel')!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Edit')!
      .trigger('click')
    expect((wrapper.get('[aria-label="Background"]').element as HTMLTextAreaElement).value).toBe(
      'New committed prose',
    )
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenLastCalledWith(
      'C1',
      'E1',
      expect.objectContaining({ background: 'New committed prose' }),
      'R2',
    )
    wrapper.unmount()
  })

  it('retains an explicitly null opening revision after a newer snapshot arrives', async () => {
    const { wrapper, world, update } = setup(null)
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Edit')!
      .trigger('click')
    world.byCampaign.C1!.world = snapshot('R2')
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenLastCalledWith('C1', 'E1', expect.any(Object), null)
    wrapper.unmount()
  })
})

it('renders saved multiline session notes as prose while preserving consequence facts', async () => {
  const { wrapper } = setup()
  useTonightStore().ensureEntry('C1').runState = {
    session: { E1: { notes: '<Forge promise>\nBring back the blade.', hp: 8, defeated: true } },
    knowledge: {},
  }
  await flushPromises()
  expect(wrapper.get('.mc-saved-session-notes').text()).toBe(
    '<Forge promise>\nBring back the blade.',
  )
  expect(wrapper.find('forge').exists()).toBe(false)
  expect(wrapper.get('.mc-session-facts').text()).toContain('hp')
  expect(wrapper.get('.mc-session-facts').text()).not.toContain('notes')
  wrapper.unmount()
})

it('requires a named session and pairs optional context with one consequence', async () => {
  const { wrapper } = setup()
  const tonight = useTonightStore()
  const projection = tonight.ensureEntry('C1')
  projection.runState = { session: {}, knowledge: {} }
  projection.sessions = [
    {
      id: 'S1',
      campaign_id: 'C1',
      title: 'Harbor',
      play_date: '2026-10-05',
      sequence: 1,
      version: 1,
      created_at: '',
      updated_at: '',
    },
  ]
  const fire = vi
    .spyOn(tonight, 'fireVerb')
    .mockRejectedValueOnce(new ApiError(409, 'conflict', 'State changed'))
    .mockResolvedValueOnce()
  vi.spyOn(tonight, 'fetchJournal').mockResolvedValue(true)
  await flushPromises()
  await wrapper
    .findAll('button')
    .find((button) => button.text() === 'Mark defeated')!
    .trigger('click')
  const apply = () =>
    wrapper.findAll('button').find((button) => button.text() === 'Apply and record')!
  expect(apply().attributes('disabled')).toBeDefined()
  expect(fire).not.toHaveBeenCalled()
  await wrapper.get('#consequence-session').setValue('S1')
  await wrapper.get('#consequence-context').setValue('The party defended the harbor.')
  await wrapper.get('.mc-consequence-form').trigger('submit')
  await flushPromises()
  expect(fire).toHaveBeenCalledWith(
    'C1',
    'E1',
    { defeated: true },
    expect.objectContaining({
      session_id: 'S1',
      context: 'The party defended the harbor.',
      request_key: expect.any(String),
    }),
  )
  expect((wrapper.get('#consequence-context').element as HTMLTextAreaElement).value).toBe(
    'The party defended the harbor.',
  )
  const originalKey = fire.mock.calls[0]![3]!.request_key
  await wrapper.get('.mc-consequence-form').trigger('submit')
  await flushPromises()
  expect(fire.mock.calls[1]![3]!.request_key).toBe(originalKey)
  expect(wrapper.find('.mc-consequence-form').exists()).toBe(false)
  wrapper.unmount()
})

it('takes back a linked story action by entry identity', async () => {
  const { wrapper } = setup()
  const tonight = useTonightStore()
  const projection = tonight.ensureEntry('C1')
  projection.runState = { session: { E1: { defeated: true } }, knowledge: {} }
  const row = {
    id: 'J1',
    campaign_id: 'C1',
    session_id: 'S1',
    headline: 'Captain defeated',
    context: '',
    references: [{ entity_id: 'E1', label: 'Captain' }],
    position: 1024,
    version: 3,
    source_event_id: 'EV1',
    action_revision_id: 'R1',
    corrected: false,
    created_at: '',
    updated_at: '',
  }
  projection.journal[tonight.journalKey({ entityId: 'E1' })] = { entries: [row], nextCursor: null }
  const correct = vi.spyOn(tonight, 'correctJournal').mockResolvedValue({ ...row, corrected: true })
  const fire = vi.spyOn(tonight, 'fireVerb')
  await flushPromises()
  await wrapper
    .findAll('button')
    .find((button) => button.text() === 'Take back action')!
    .trigger('click')
  await flushPromises()
  expect(correct).toHaveBeenCalledWith('C1', expect.objectContaining({ id: 'J1', version: 3 }))
  expect(fire).not.toHaveBeenCalled()
  wrapper.unmount()
})

it('does not show an empty consequences message while a boolean consequence is active', async () => {
  const { wrapper } = setup()
  useTonightStore().ensureEntry('C1').runState = {
    session: { E1: { defeated: true } },
    knowledge: {},
  }
  await flushPromises()
  expect(wrapper.text()).toContain('Mark undefeated')
  expect(wrapper.text()).not.toContain('No consequences yet.')
  wrapper.unmount()
})
