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

function snapshot(revision: string | null, background = 'Opening prose'): components['schemas']['WorldExport'] {
  return {
    campaign: { id: 'C1', title: 'Campaign', theme: '', description: '', custom_lore: '', is_generic: false, created_at: '2026-09-01T00:00:00Z' },
    revision: revision ? { id: revision, created_at: '2026-09-01T00:00:00Z' } : null,
    entities: [{ id: 'E1', kind: 'character', name: 'Captain', text: '', media: [], data: { background } }],
    edges: [],
  }
}

function setup(revision: string | null = 'R1') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const world = useWorldStore()
  world.byCampaign.C1 = { world: snapshot(revision), loading: false, error: null, notFound: false, fetching: false, dirty: false }
  vi.spyOn(world, 'load').mockResolvedValue(undefined)
  vi.spyOn(world, 'fetchMedia').mockResolvedValue(undefined)
  vi.spyOn(useCampaignsStore(), 'fetchOne').mockResolvedValue(undefined)
  vi.spyOn(useTonightStore(), 'fetchKinds').mockResolvedValue(undefined)
  vi.spyOn(useTonightStore(), 'load').mockResolvedValue(undefined)
  const update = vi.spyOn(world, 'updateEntity').mockResolvedValue(undefined)
  const refetch = vi.spyOn(world, 'requestRefetch').mockImplementation(async () => {
    world.byCampaign.C1!.world = snapshot('R3', 'Conflict snapshot')
  })
  const wrapper = mount(NewEntityView, { global: {
    plugins: [pinia], stubs: { VueFlowGraph: true, EntityMediaActions: true },
  } })
  return { world, update, refetch, wrapper }
}

beforeEach(() => vi.restoreAllMocks())

describe('NewEntityView edit sessions', () => {
  it('pins saves and conflict retries to edit-open revision and preserves the draft', async () => {
    const { wrapper, world, update, refetch } = setup()
    await wrapper.findAll('button').find((b) => b.text() === 'Edit')!.trigger('click')
    await wrapper.get('[aria-label="Background"]').setValue('My draft')
    world.byCampaign.C1!.world = snapshot('R2', 'Refetched prose')
    await flushPromises()
    update.mockRejectedValueOnce(new ApiError(409, 'revision_conflict', 'World changed'))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenLastCalledWith('C1', 'E1', expect.objectContaining({ background: 'My draft' }), 'R1')
    expect(refetch).toHaveBeenCalledWith('C1')
    expect(wrapper.get('[role="alert"]').text()).toBe('World changed')
    expect((wrapper.get('[aria-label="Background"]').element as HTMLTextAreaElement).value).toBe('My draft')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenLastCalledWith('C1', 'E1', expect.objectContaining({ background: 'My draft' }), 'R1')
    expect(wrapper.find('form').exists()).toBe(false)
    wrapper.unmount()
  })

  it('captures a fresh revision and draft after cancel/reopen', async () => {
    const { wrapper, world, update } = setup()
    await wrapper.findAll('button').find((b) => b.text() === 'Edit')!.trigger('click')
    await wrapper.get('[aria-label="Background"]').setValue('Discard this')
    world.byCampaign.C1!.world = snapshot('R2', 'New committed prose')
    await wrapper.findAll('button').find((b) => b.text() === 'Cancel')!.trigger('click')
    await wrapper.findAll('button').find((b) => b.text() === 'Edit')!.trigger('click')
    expect((wrapper.get('[aria-label="Background"]').element as HTMLTextAreaElement).value).toBe('New committed prose')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenLastCalledWith('C1', 'E1', expect.objectContaining({ background: 'New committed prose' }), 'R2')
    wrapper.unmount()
  })

  it('retains an explicitly null opening revision after a newer snapshot arrives', async () => {
    const { wrapper, world, update } = setup(null)
    await wrapper.findAll('button').find((b) => b.text() === 'Edit')!.trigger('click')
    world.byCampaign.C1!.world = snapshot('R2')
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenLastCalledWith('C1', 'E1', expect.any(Object), null)
    wrapper.unmount()
  })
})
