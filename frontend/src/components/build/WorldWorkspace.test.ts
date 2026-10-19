// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WorldWorkspace from './WorldWorkspace.vue'
import { useWorkspaceStore } from '../../stores/workspace'
import { useJobsStore } from '../../stores/jobs'
import { useWorldStore } from '../../stores/world'
import type { components } from '../../api/schema'
const { api } = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  apiFetch: api,
}))
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  api.mockImplementation(async (path: string) => {
    if (path.endsWith('/workspace'))
      return { draft_version: 0, world_version: null, draft: { nodes: [] }, receipts: [] }
    if (path.endsWith('/export'))
      return {
        campaign: { id: 'C1' },
        entities: [{ id: 'harbor', name: 'Harbor', kind: 'place', text: 'A busy harbor.' }],
        edges: [],
      }
    if (path.includes('/api/jobs')) return { jobs: [], next_cursor: null }
    throw new Error(path)
  })
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})
describe('World workspace interactions', () => {
  it('adds direct nested entries with stable placement, compact controls, and a synchronized outline', async () => {
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '+ place')!
      .trigger('click')
    const store = useWorkspaceStore(),
      place = store.entry('C1').nodes[0]!
    expect(place.parent_id).toBe('harbor')
    await wrapper.get('input[aria-label="Planned place name"]').setValue('Market')
    await flushPromises()
    expect(
      wrapper
        .findAll('.outline-btn')
        .map((b) => b.text())
        .join(' '),
    ).toContain('Market')
    await wrapper
      .findAll('.plan-child-tools button')
      .find((b) => b.text() === '+ character')!
      .trigger('click')
    expect(store.entry('C1').nodes[1]!.parent_id).toBe(place.id)
    expect(wrapper.find('input[aria-label="Planned character name"]').exists()).toBe(true)
    wrapper.unmount()
  })
  it('Undo restores only the removed leaf and preserves later edits and additions', async () => {
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '+ character')!
      .trigger('click')
    await wrapper.get('input[aria-label="Planned character name"]').setValue('Mara')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Remove')!
      .trigger('click')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '+ faction')!
      .trigger('click')
    await wrapper.get('input[aria-label="Planned faction name"]').setValue('Lantern Guild')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Undo removal')!
      .trigger('click')
    expect(
      useWorkspaceStore()
        .entry('C1')
        .nodes.map((n) => n.name),
    ).toEqual(['Mara', 'Lantern Guild'])
    wrapper.unmount()
  })
  it('preserves independent membership and guidance when moving an entry', async () => {
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    const store = useWorkspaceStore()
    store.entry('C1').nodes = [
      {
        id: 'mara',
        kind: 'character',
        name: 'Mara',
        parent_id: 'harbor',
        faction_id: 'guild',
        guidance: 'Secretly a smuggler',
      },
      {
        id: 'guild',
        kind: 'faction',
        name: 'Guild',
        parent_id: 'harbor',
        faction_id: null,
        guidance: '',
      },
    ]
    await flushPromises()
    await wrapper.find('.plan-edit-grid select').setValue('')
    expect(store.entry('C1').nodes[0]).toMatchObject({
      id: 'mara',
      parent_id: null,
      faction_id: 'guild',
      guidance: 'Secretly a smuggler',
    })
    wrapper.unmount()
  })
  it('keeps the new name input mounted and focused while typing beneath a planned place', async () => {
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' }, attachTo: document.body })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '+ place')!
      .trigger('click')
    await flushPromises()
    const placeInput = wrapper.get('input[aria-label="Planned place name"]')
    expect(document.activeElement).toBe(placeInput.element)
    await placeInput.setValue('Market')
    await flushPromises()
    expect(document.activeElement).toBe(placeInput.element)
    expect(placeInput.element.isConnected).toBe(true)
    await wrapper
      .findAll('.outline-btn')
      .find((b) => b.text().includes('Market'))!
      .trigger('click')
    await flushPromises()
    await wrapper
      .findAll('.plan-child-tools button')
      .find((b) => b.text() === '+ character')!
      .trigger('click')
    await flushPromises()
    const characterInput = wrapper.get('input[aria-label="Planned character name"]')
    expect(document.activeElement).toBe(characterInput.element)
    await characterInput.setValue('Mara')
    await flushPromises()
    expect(document.activeElement).toBe(characterInput.element)
    expect(characterInput.element.isConnected).toBe(true)
    wrapper.unmount()
  })

  it('rejects bulk additions exceeding a kind limit without mutating any rows', async () => {
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    const store = useWorkspaceStore()
    store.entry('C1').nodes = Array.from({ length: 99 }, (_, i) => ({
      id: `char-${i}`,
      kind: 'character',
      name: `Name ${i}`,
      parent_id: 'harbor',
      faction_id: null,
      guidance: '',
    }))
    await flushPromises()
    await wrapper.findAll('.allocation input')[2]!.setValue(1)
    await wrapper.findAll('.plan-tools textarea').at(-1)!.setValue('Extra named character')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Add to plan manually')!
      .trigger('click')
    expect(store.entry('C1').nodes).toHaveLength(99)
    expect(wrapper.text()).toContain('100 entries per kind')
    wrapper.unmount()
  })

  it('retries terminal reconciliation after a failed refresh', async () => {
    vi.useFakeTimers()
    const jobs = useJobsStore(),
      store = useWorkspaceStore()
    vi.spyOn(jobs, 'syncList').mockResolvedValue()
    jobs.byId.J1 = {
      id: 'J1',
      campaign_id: 'C1',
      kind: 'expand_world',
      state: 'succeeded',
      created_at: '2026-10-18T00:00:00Z',
    } as (typeof jobs.byId)[string]
    const reconcile = vi
      .spyOn(store, 'reconcile')
      .mockResolvedValueOnce(false)
      .mockResolvedValue(true)
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    expect(reconcile).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(reconcile).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(reconcile).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('does not start polling after unmount during initial loading', async () => {
    let release!: (value: unknown) => void
    api.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve
        }),
    )
    const interval = vi.spyOn(globalThis, 'setInterval')
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    wrapper.unmount()
    release({ draft_version: 0, world_version: null, draft: { nodes: [] }, receipts: [] })
    await flushPromises()
    expect(interval).not.toHaveBeenCalled()
  })

  it('remaps Undo placement and membership after their planned targets complete', async () => {
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    const store = useWorkspaceStore()
    store.entry('C1').nodes = [
      {
        id: 'mara',
        kind: 'character',
        name: 'Mara',
        parent_id: 'planned-market',
        faction_id: 'planned-guild',
        guidance: 'Keep this',
      },
      {
        id: 'planned-market',
        kind: 'place',
        name: 'Market',
        parent_id: 'harbor',
        faction_id: null,
        guidance: '',
      },
    ]
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('Mara') && b.classes().includes('outline-btn'))!
      .trigger('click')
    await wrapper
      .findAll('.plan-row button')
      .find((b) => b.text() === 'Remove')!
      .trigger('click')
    store.entry('C1').nodes = []
    store.entry('C1').snapshot!.receipts = [
      { node_id: 'planned-market', entity_id: 'canon-market' },
      { node_id: 'planned-guild', entity_id: 'canon-guild' },
    ]
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Undo removal')!
      .trigger('click')
    expect(store.entry('C1').nodes[0]).toMatchObject({
      id: 'mara',
      parent_id: 'canon-market',
      faction_id: 'canon-guild',
      guidance: 'Keep this',
    })
    wrapper.unmount()
  })

  it('shows existing factions based at the selected place', async () => {
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    const world = useWorldStore().entry('C1').world!
    world.entities.push({
      id: 'guild',
      name: 'Guild',
      kind: 'faction',
    } as (typeof world.entities)[number])
    world.edges.push({
      src: 'guild',
      dst: 'harbor',
      type: 'bases_at',
    } as (typeof world.edges)[number])
    await flushPromises()
    expect(wrapper.get('.entity-grid').text()).toContain('Guild')
    wrapper.unmount()
  })
  it('refreshes expansion history once and reconciles only the newest structure suggestion', async () => {
    vi.useFakeTimers()
    const jobs = useJobsStore(),
      store = useWorkspaceStore()
    vi.spyOn(jobs, 'syncList').mockResolvedValue()
    const makeJob = (id: string, kind: string, date: string) =>
      ({
        id,
        kind,
        campaign_id: 'C1',
        state: 'succeeded',
        created_at: date,
        payload: {},
        progress: 1,
        max_llm_calls: 8,
        max_media_calls: 0,
        error: null,
        started_at: null,
        finished_at: null,
        queue_position: null,
        result: {
          proposal: {
            nodes: [
              { id, kind: 'place', name: id, guidance: '', parent_id: null, faction_id: null },
            ],
          },
          source_draft_version: 0,
          source_world_version: null,
        },
      }) as components['schemas']['JobResponse']
    for (const job of [
      makeJob('E1', 'expand_world', '2026-10-01'),
      makeJob('E2', 'expand_world', '2026-10-02'),
      makeJob('E3', 'expand_world', '2026-10-03'),
      makeJob('P-old', 'plan_expansion', '2026-10-04'),
      makeJob('P-new', 'plan_expansion', '2026-10-05'),
    ])
      jobs.byId[job.id] = job
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    expect(api.mock.calls.filter(([path]) => String(path).endsWith('/export'))).toHaveLength(2)
    expect(store.entry('C1').proposal?.jobId).toBe('P-new')
    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Discard')!
      .trigger('click')
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(store.entry('C1').proposal).toBeNull()
    expect(api.mock.calls.filter(([path]) => String(path).endsWith('/export'))).toHaveLength(2)
    wrapper.unmount()
  })

  it('promotes selected planned places to their confirmed identities for the next addition', async () => {
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    const store = useWorkspaceStore(),
      world = useWorldStore().entry('C1').world!
    store.entry('C1').nodes = [
      {
        id: 'draft-market',
        kind: 'place',
        name: 'Market',
        parent_id: 'harbor',
        faction_id: null,
        guidance: '',
      },
    ]
    await flushPromises()
    await wrapper
      .findAll('.outline-btn')
      .find((b) => b.text().includes('Market'))!
      .trigger('click')
    world.entities.push({
      id: 'canon-market',
      name: 'Market',
      kind: 'place',
    } as (typeof world.entities)[number])
    store.entry('C1').snapshot!.receipts = [{ node_id: 'draft-market', entity_id: 'canon-market' }]
    store.entry('C1').nodes = []
    await flushPromises()
    expect(wrapper.get('.scope-name').text()).toBe('Market')
    await wrapper
      .findAll('.planning-toolbar button')
      .find((b) => b.text() === '+ character')!
      .trigger('click')
    expect(store.entry('C1').nodes[0]!.parent_id).toBe('canon-market')
    wrapper.unmount()
  })

  it('drains pending edits after unmount and protects unsaved browser navigation', async () => {
    vi.useFakeTimers()
    let release!: (value: unknown) => void
    const original = api.getMockImplementation()!
    const puts: { nodes: unknown[] }[] = []
    api.mockImplementation(async (path: string, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        const request = JSON.parse(String(init.body))
        puts.push(request.draft)
        if (puts.length === 1)
          return new Promise((resolve) => {
            release = resolve
          })
        return { draft_version: 2, world_version: null, draft: request.draft, receipts: [] }
      }
      return original(path, init)
    })
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    await wrapper
      .findAll('.planning-toolbar button')
      .find((b) => b.text() === '+ character')!
      .trigger('click')
    await wrapper.get('input[aria-label="Planned character name"]').setValue('Mara')
    await vi.advanceTimersByTimeAsync(500)
    await flushPromises()
    await wrapper.get('.plan-edit-grid textarea').setValue(' Keep this later edit. ')
    const leaving = new Event('beforeunload', { cancelable: true })
    globalThis.dispatchEvent(leaving)
    expect(leaving.defaultPrevented).toBe(true)
    wrapper.unmount()
    release({ draft_version: 1, world_version: null, draft: puts[0], receipts: [] })
    await flushPromises()
    expect(puts).toHaveLength(2)
    expect(puts[1]!.nodes[0]).toMatchObject({ name: 'Mara', guidance: ' Keep this later edit. ' })
    expect(useWorkspaceStore().entry('C1').dirty).toBe(false)
  })

  it('reports cancelled and failed planning jobs while preserving the plan', async () => {
    const jobs = useJobsStore()
    vi.spyOn(jobs, 'syncList').mockResolvedValue()
    jobs.byId.P1 = {
      id: 'P1',
      campaign_id: 'C1',
      kind: 'plan_expansion',
      state: 'cancelled',
      created_at: '2026-10-19',
    } as components['schemas']['JobResponse']
    const wrapper = mount(WorldWorkspace, { props: { campaignId: 'C1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('Structure suggestion cancelled.')
    jobs.byId.P1!.state = 'failed'
    jobs.byId.P1!.error = 'Provider offline'
    await flushPromises()
    expect(wrapper.text()).toContain('Structure suggestion failed.')
    expect(wrapper.text()).toContain('Provider offline')
    wrapper.unmount()
  })
})
