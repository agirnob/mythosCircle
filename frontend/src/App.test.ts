// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h } from 'vue'
import App from './App.vue'
import GraphView from './components/graph/GraphView.vue'
import { useAuthStore } from './stores/auth'
import { useWorldStore } from './stores/world'
import { denseWorld, DENSE_FOCUS_ID } from './components/graph/denseFixture'
vi.mock('./components/shell/AppShell.vue', () => ({
  default: { template: '<main><slot /></main>' },
}))
const Renderer = defineComponent({
  name: 'Renderer',
  props: ['nodes', 'edges', 'focusId', 'labelsVisible', 'oneHop'],
  setup: () => () => h('div', { class: 'renderer' }),
})
it('preserves graph filters and renderer on focus queries, remounting on campaign and account changes', async () => {
  const pinia = createPinia()
  const auth = useAuthStore(pinia)
  auth.account = { id: 'A', email: 'a@example.com' }
  const world = useWorldStore(pinia)
  vi.spyOn(world, 'load').mockResolvedValue()
  vi.spyOn(world, 'fetchMedia').mockResolvedValue()
  world.byCampaign.C1 = {
    world: denseWorld(),
    loading: false,
    fetching: false,
    dirty: false,
    error: null,
    notFound: false,
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/campaigns/:id/graph', component: GraphView, meta: { requiresAuth: true } }],
  })
  await router.push('/campaigns/C1/graph')
  await router.isReady()
  const wrapper = mount(App, {
    global: { plugins: [pinia, router], stubs: { VueFlowGraph: Renderer, RouterLink: true } },
  })
  await flushPromises()
  await wrapper.get('[aria-label="Filter by entity type"]').setValue('character')
  await wrapper.get('[aria-label="Filter by relationship type"]').setValue('relationship')
  await wrapper.get('[aria-label="Show edge labels"]').setValue(true)
  const renderer = wrapper.get('.renderer').element
  for (const query of [{ focus: DENSE_FOCUS_ID }, {}]) {
    await router.replace({ query })
    await flushPromises()
    expect(wrapper.get('.renderer').element).toBe(renderer)
    expect(
      (wrapper.get('[aria-label="Filter by entity type"]').element as HTMLSelectElement).value,
    ).toBe('character')
    expect((wrapper.get('[aria-label="Filter by relationship type"]').element as HTMLSelectElement).value).toBe('relationship')
    expect(
      (wrapper.get('[aria-label="Show edge labels"]').element as HTMLInputElement).checked,
    ).toBe(true)
  }
  const graph = wrapper.getComponent(GraphView).element
  await router.push('/campaigns/C2/graph')
  await flushPromises()
  expect(wrapper.getComponent(GraphView).element).not.toBe(graph)
  const nextGraph = wrapper.getComponent(GraphView).element
  auth.account = { id: 'B', email: 'b@example.com' }
  await flushPromises()
  expect(wrapper.getComponent(GraphView).element).not.toBe(nextGraph)
  wrapper.unmount()
})


it('never mounts the protected graph when anonymous or while logout is pending', async () => {
  const pinia = createPinia()
  const auth = useAuthStore(pinia), world = useWorldStore(pinia)
  const load = vi.spyOn(world, 'load').mockResolvedValue()
  const media = vi.spyOn(world, 'fetchMedia').mockResolvedValue()
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/campaigns/:id/graph', component: GraphView, meta: { requiresAuth: true } },
    { path: '/login', component: { render: () => h('div', 'Login') } },
  ] })
  await router.push('/campaigns/C1/graph'); await router.isReady()
  const wrapper = mount(App, { global: { plugins: [pinia, router], stubs: { RouterLink: true } } })
  await flushPromises()
  expect(wrapper.findComponent(GraphView).exists()).toBe(false)
  expect(load).not.toHaveBeenCalled()
  auth.account = { id: 'A', email: 'a@example.com' }; await flushPromises()
  expect(wrapper.findComponent(GraphView).exists()).toBe(true)
  expect(load).toHaveBeenCalledOnce()
  let resolveLogout!: (response: Response) => void
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise<Response>((resolve) => { resolveLogout = resolve }))
  const logout = auth.logout(); await flushPromises()
  expect(auth.account).toBeNull()
  expect(wrapper.findComponent(GraphView).exists()).toBe(false)
  expect(load).toHaveBeenCalledOnce()
  expect(media).toHaveBeenCalledOnce()
  resolveLogout(new Response(null, { status: 204 })); await logout; await flushPromises()
  expect(wrapper.findComponent(GraphView).exists()).toBe(false)
  expect(load).toHaveBeenCalledOnce()
  await router.push('/login'); await flushPromises()
  expect(wrapper.text()).toBe('Login')
  fetchMock.mockRestore()
  wrapper.unmount()
})
