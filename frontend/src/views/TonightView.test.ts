// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
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

async function setup(fetchCampaign?: (id: string) => Promise<void>) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().account = { id: 'A', email: 'a@example.com' }
  const stored = new Map<string, string>()
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
  it('keeps notes editable while projections load or fail', async () => {
    const { wrapper, tonight, stored } = await setup()
    expect(wrapper.text()).toContain('Loading Tonight')
    await wrapper.get('textarea').setValue('Prep during loading')
    tonight.ensureEntry('C1').error = 'Unavailable'
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('Could not load Tonight')
    expect(wrapper.get('textarea').element.value).toBe('Prep during loading')
    expect(wrapper.get('textarea').element.disabled).toBe(false)
    expect(stored.get('mythoscircle:tonight:A:C1')).toBe('Prep during loading')
    wrapper.unmount()
  })

  it('uses the current route campaign when the mounted view is reused', async () => {
    const { wrapper, router, load, stored, world, tonight, campaigns } = await setup()
    for (const [id, entityId, name, session, knowledge, detail] of [
      ['C1', 'E1', 'Mira Vane', { defeated: true, hp: 8 }, { secret: true }, 'Marked defeated'],
      ['C2', 'E2', 'Captain Sol', { thread: true, hp: 22 }, { rumor: false }, 'Resolved thread'],
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
    stored.set('mythoscircle:tonight:A:C2', 'Second campaign notes')
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
    expect(wrapper.get('textarea').element.value).toBe('Second campaign notes')
    await wrapper.get('textarea').setValue('Updated second campaign')
    expect(stored.get('mythoscircle:tonight:A:C1')).toBe('First campaign notes')
    expect(stored.get('mythoscircle:tonight:A:C2')).toBe('Updated second campaign')
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
