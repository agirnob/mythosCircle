// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { h } from 'vue'

import type { components } from '../../api/schema'
import { useWorldStore } from '../../stores/world'
import { DENSE_FOCUS_ID, denseWorld } from './denseFixture'

type WorldExport = components['schemas']['WorldExport']
type WorldEntry = {
  world: WorldExport | null
  loading: boolean
  error: string | null
  notFound: boolean
  fetching: boolean
  dirty: boolean
}

const cfg = vi.hoisted(() => {
  const query: Record<string, string> = {}
  return {
    pushMock: vi.fn(),
    query,
    setQuery: (next: Record<string, string>) => {
      for (const key of Object.keys(query)) delete query[key]
      Object.assign(query, next)
    },
    candidateBehavior: {
      throwOnRender: false,
      /** Exposed toolbar surface calls, in order (keyboard/button wiring). */
      calls: [] as string[],
    },
  }
})

const { pushMock, setQuery, candidateBehavior } = cfg

vi.mock('./VueFlowGraph.vue', () => {
  /** The single shipped candidate — the A/B is historical (winner only). */
  const VueFlowGraphStub = {
    name: 'VueFlowGraphStub',
    props: {
      nodes: { type: Array, default: () => [] },
      edges: { type: Array, default: () => [] },
      focusId: { type: [String, null], default: null },
      oneHop: { type: [Object, null], default: null },
      labelsVisible: { type: Boolean, default: false },
      nodeNameById: { type: Object, default: () => ({}) },
    },
    emits: ['refocus', 'clear-focus'],
    setup(
      props: {
        nodes: Array<{ id: string; name: string; portraitUrl: string | null }>
        edges: Array<{ label: string }>
        labelsVisible: boolean
      },
      context: {
        emit: (event: string, id?: string) => void
        expose: (surface: Record<string, (...args: number[]) => void>) => void
      },
    ) {
      if (candidateBehavior.throwOnRender) throw new Error('stub render failure')
      context.expose({
        zoomIn: () => candidateBehavior.calls.push('zoomIn'),
        zoomOut: () => candidateBehavior.calls.push('zoomOut'),
        fitView: () => candidateBehavior.calls.push('fitView'),
        resetView: () => candidateBehavior.calls.push('resetView'),
        panBy: (dx: number, dy: number) => candidateBehavior.calls.push(`panBy:${dx},${dy}`),
      })
      return () =>
        h('div', { class: 'candidate-vueflow' }, [
          // Empty-space click surface (the pane clears the focus).
          h('div', { class: 'stub-pane', onClick: () => context.emit('clear-focus') }),
          ...(props.nodes as Array<{ id: string; name: string }>).map((node) =>
            h(
              'button',
              {
                class: 'node',
                'data-id': node.id,
                onClick: () => context.emit('refocus', node.id),
              },
              [node.name],
            ),
          ),
          // The labels toggle is consumed: edge labels render only when on.
          ...(props.labelsVisible
            ? (props.edges as Array<{ label: string }>).map((edge) =>
                h('span', { class: 'edge' }, [edge.label]),
              )
            : []),
        ])
    },
  }
  return { default: VueFlowGraphStub }
})

vi.mock('vue-router', async () => {
  // A reactive source over the SAME plain object `cfg.query` (the tests'
  // setQuery mutates it in place): the route's query property stays visible
  // to Vue's computed dependency tracking, so a replace -> mutation ->
  // re-read loop re-renders the view exactly like the real router.
  const { ref } = await import('vue')
  const queryRef = ref(cfg.query)
  const applyQuery = (next: Record<string, string | undefined> | null) => {
    if (!next) return
    for (const key of Object.keys(queryRef.value)) {
      if (!(key in next)) delete queryRef.value[key]
    }
    for (const [key, value] of Object.entries(next)) {
      if (value === undefined || value === null) continue
      queryRef.value[key] = value
    }
  }
  return {
    RouterLink: { template: '<a class="router-link"><slot /></a>' },
    useRoute: () => ({
      params: { id: 'C1' },
      get query() {
        return queryRef.value
      },
    }),
    useRouter: () => ({
      // The real router.replace mutates the routed query (reactive loop):
      // the mock applies the same so the view re-renders on refocus/clear.
      replace: (location: { query?: Record<string, string | undefined> | null }) => {
        cfg.pushMock(location)
        applyQuery(location.query ?? null)
      },
    }),
  }
})

vi.mock('../../api/client', () => {
  class ApiErrorMock extends Error {
    readonly status: number
    readonly code: string
    constructor(status: number, code: string, message: string) {
      super(message)
      this.name = 'ApiError'
      this.status = status
      this.code = code
    }
  }
  return {
    ApiError: ApiErrorMock,
    apiFetch: vi.fn().mockRejectedValue(
      new ApiErrorMock(500, 'server_error', 'Database unavailable.'),
    ),
  }
})

import { apiFetch } from '../../api/client'
import VueFlowGraphStub from './VueFlowGraph.vue'
import GraphView from './GraphView.vue'

describe('GraphView', () => {
  let entry: WorldEntry

  function seed(world: WorldExport | null, overrides: Partial<WorldEntry> = {}) {
    entry = {
      world,
      loading: false,
      error: null,
      notFound: false,
      fetching: false,
      dirty: false,
      ...overrides,
    }
    const store = useWorldStore()
    store.byCampaign['C1'] = entry
    store.mediaByCampaign['C1'] = []
  }

  function mountView(): VueWrapper {
    return mount(GraphView)
  }

  /** The driver stub's props (the contract GraphView feeds the candidate). */
  function stubProps(wrapper: VueWrapper) {
    return wrapper.findComponent(VueFlowGraphStub).props() as {
      nodes: Array<{ id: string; portraitUrl: string | null }>
      edges: Array<{ label: string }>
      focusId: string | null
      oneHop: { nodeIds: Set<string>; edgeIds: Set<string> } | null
      labelsVisible: boolean
    }
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    setQuery({})
    candidateBehavior.throwOnRender = false
    candidateBehavior.calls = []
    pushMock.mockClear()
  })

  it('renders the WHOLE world from a store-provided snapshot with full HUD counts', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.findAll('button.node')).toHaveLength(29)
    expect(wrapper.findAll('span.edge')).toHaveLength(0) // labels off by default
    expect(wrapper.text()).toContain('29 entities')
    expect(wrapper.text()).toContain('33 relationships')
    // No focus anywhere: the candidate receives focusId null and no oneHop.
    const props = stubProps(wrapper)
    expect(props.focusId).toBeNull()
    expect(props.oneHop).toBeNull()
    expect(wrapper.find('button.clear-focus').exists()).toBe(false)
    wrapper.unmount()
  })

  it('breadcrumb is human-readable; no raw ULID shape leaks into the UI', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('Campaigns')
    expect(text).toContain('The Drowned Harbor (dense fixture)')
    expect(text).toContain('Graph')
    expect(text.match(/[0-9A-HJKMNP-TV-Z]{26}/)).toBeNull()
    wrapper.unmount()
  })

  it('node click refocuses: ?focus replaced AND the candidate receives the new focusId/oneHop', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    expect(stubProps(wrapper).focusId).toBe(DENSE_FOCUS_ID)
    await wrapper.find('button.node[data-id="N01"]').trigger('click')
    await flushPromises()
    const call = pushMock.mock.calls[pushMock.mock.calls.length - 1][0]
    expect(call.query.focus).toBe('N01')
    // The reactive loop re-renders the candidate with the new focus.
    const props = stubProps(wrapper)
    expect(props.focusId).toBe('N01')
    expect(props.oneHop?.nodeIds.size).toBe(2) // N01 + F0
    expect(props.oneHop?.nodeIds).toContain('F0')
    wrapper.unmount()
  })

  it('deep-linked ?focus survives an async store load (cold mount)', async () => {
    seed(null)
    setQuery({ focus: 'N01' })
    vi.mocked(apiFetch).mockResolvedValueOnce(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const props = stubProps(wrapper)
    expect(props.focusId).toBe('N01')
    // N01's 1-hop: itself + F0, via its single employs edge (e01).
    expect(props.oneHop?.nodeIds.size).toBe(2)
    expect(props.oneHop?.edgeIds.size).toBe(1)
    // Absent focus is never written; a PRESENT focus needs no URL rewrite.
    expect(pushMock).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('cold mount WITHOUT ?focus writes nothing and renders the whole world unfocused', async () => {
    seed(null)
    vi.mocked(apiFetch).mockResolvedValueOnce(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    expect(pushMock).not.toHaveBeenCalled()
    expect(wrapper.findAll('button.node')).toHaveLength(29)
    expect(stubProps(wrapper).focusId).toBeNull()
    wrapper.unmount()
  })

  it('empty-space click clears the focus: ?focus drops and the candidate unhighlights', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    expect(stubProps(wrapper).focusId).toBe(DENSE_FOCUS_ID)
    await wrapper.find('.stub-pane').trigger('click')
    await flushPromises()
    const call = pushMock.mock.calls[pushMock.mock.calls.length - 1][0]
    expect(call.query.focus).toBeUndefined()
    // The reactive loop returns the candidate to the full unhighlighted web.
    const props = stubProps(wrapper)
    expect(props.focusId).toBeNull()
    expect(props.oneHop).toBeNull()
    expect(props.nodes).toHaveLength(29)
    wrapper.unmount()
  })

  it('the Clear focus control performs the same query drop', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('button.clear-focus').exists()).toBe(true)
    await wrapper.find('button.clear-focus').trigger('click')
    const call = pushMock.mock.calls[pushMock.mock.calls.length - 1][0]
    expect(call.query.focus).toBeUndefined()
    wrapper.unmount()
  })

  it('MISSING FOCUS: whole world renders unfocused with a notice — never an error card', async () => {
    seed(denseWorld())
    setQuery({ focus: 'NO-SUCH-ENTITY' })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.findAll('button.node')).toHaveLength(29)
    expect(stubProps(wrapper).focusId).toBeNull()
    expect(wrapper.text()).toContain('Focus not found — showing the whole world.')
    expect(wrapper.text()).not.toContain('error')
    wrapper.unmount()
  })

  it('focus highlight reaches the candidate: oneHop + focusId on a known entity', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    const props = stubProps(wrapper)
    expect(props.focusId).toBe(DENSE_FOCUS_ID)
    expect(props.oneHop?.nodeIds.size).toBe(26)
    expect(props.oneHop?.nodeIds).toContain('N01')
    expect(props.oneHop?.nodeIds).not.toContain('X01')
    wrapper.unmount()
  })

  it('portrait URL flows from the store manifest into the rendered nodes', async () => {
    const w = denseWorld()
    const store = useWorldStore()
    seed(w)
    // The manifest row for F0 (whose export carries an available image ref).
    store.mediaByCampaign['C1'] = [
      {
        id: 'm-01',
        campaign_id: 'C1',
        entity_id: 'F0',
        filename: 'pike.png',
        kind: 'image',
        created_at: '2026-09-19T00:00:00Z',
      },
    ]
    const wrapper = mountView()
    await flushPromises()
    const props = stubProps(wrapper)
    const f0 = props.nodes.find((node) => node.id === 'F0')
    expect(f0?.portraitUrl).toBe('/api/campaigns/C1/media/F0/pike.png')
    wrapper.unmount()
  })

  it('loading state shows the world loader', async () => {
    seed(null, { loading: true })
    const wrapper = mountView()
    // The loader renders synchronously at mount — before the (mocked)
    // snapshot fetch settles through its loading=false transition.
    expect(wrapper.text()).toContain('Loading the world…')
    wrapper.unmount()
  })

  it('notFound state shows the world-not-found card', async () => {
    seed(null, { notFound: true })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('World not found.')
    wrapper.unmount()
  })

  it('store error state offers Retry', async () => {
    seed(null)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Database unavailable.')
    expect(wrapper.text()).toContain('Retry')
    wrapper.unmount()
  })

  it('empty world state shows the empty-world card', async () => {
    const empty = denseWorld()
    empty.entities = []
    empty.edges = []
    seed(empty)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('This world is still empty')
    wrapper.unmount()
  })

  it('entity-type filter narrows the visible set', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const kinds = wrapper.findAll('select[aria-label="Filter by entity type"]')[0].element.querySelectorAll('option')
    expect(Array.from(kinds).map((option) => option.textContent)).toContain('faction')
    await wrapper
      .find('select[aria-label="Filter by entity type"]')
      .setValue('place')
    await flushPromises()
    const after = wrapper.findAll('button.node')
    expect(after.length).toBeGreaterThan(0)
    expect(after.length).toBeLessThan(29)
    wrapper.unmount()
  })

  it('FILTERED EMPTY renders the message with a reset hint that recovers', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    // employs touches F0(char) + N01/N25(factions) — place + employs
    // excludes every visible node.
    await wrapper
      .find('select[aria-label="Filter by entity type"]')
      .setValue('place')
    await wrapper
      .find('select[aria-label="Filter by relationship type"]')
      .setValue('employs')
    await flushPromises()
    expect(wrapper.findAll('button.node')).toHaveLength(0)
    expect(wrapper.text()).toContain('No entities match the current filter.')
    expect(wrapper.text()).toContain('Reset filters')
    await wrapper.find('button.cta').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('button.node').length).toBeGreaterThan(0)
    wrapper.unmount()
  })

  it('noFilteredRelationships: kind filter keeps nodes, relationship filter empties the edge set', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    // loyalty touches F0(char) + N05/N17(places) — kind=character keeps the
    // focus but drops both loyalty endpoints → edges empty, nodes remain.
    await wrapper
      .find('select[aria-label="Filter by entity type"]')
      .setValue('character')
    await wrapper
      .find('select[aria-label="Filter by relationship type"]')
      .setValue('loyalty')
    await flushPromises()
    const canvas = () => wrapper.find('.canvas')
    expect(canvas().findAll('button.node').length).toBeGreaterThan(0)
    expect(canvas().findAll('span.edge')).toHaveLength(0)
    expect(wrapper.text()).toContain('No relationships match the current filter.')
    wrapper.unmount()
  })

  it('labels toggle is OFF by default and shows every edge label when on', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const checkbox = wrapper.find('input[aria-label="Show edge labels"]')
    expect((checkbox.element as unknown as { checked: boolean }).checked).toBe(false)
    expect(wrapper.findAll('span.edge')).toHaveLength(0)
    await checkbox.setValue(true)
    await flushPromises()
    expect(wrapper.findAll('span.edge')).toHaveLength(33)
    wrapper.unmount()
  })

  it('filtered HUD counts track the rendered subset', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('29 entities')
    await wrapper
      .find('select[aria-label="Filter by entity type"]')
      .setValue('place')
    await flushPromises()
    const canvasNodes = wrapper.find('.canvas').findAll('button.node').length
    expect(wrapper.find('.hud').text()).toContain(`${canvasNodes} entities`)
    wrapper.unmount()
  })

  it('keyboard pan/zoom: arrows pan, +/- zoom, f fits, r resets via the exposed API', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const canvas = wrapper.find('.canvas')
    for (const key of ['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown', '+', '-', 'f', 'r']) {
      await canvas.trigger('keydown', { key })
    }
    expect(candidateBehavior.calls).toEqual([
      'panBy:36,0',
      'panBy:0,36',
      'panBy:-36,0',
      'panBy:0,-36',
      'zoomIn',
      'zoomOut',
      'fitView',
      'resetView',
    ])
    wrapper.unmount()
  })

  it('toolbar buttons call the same exposed surface', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('button[title="Zoom in"]').trigger('click')
    await wrapper.find('button[title="Zoom out"]').trigger('click')
    await wrapper.find('button[title="Fit view"]').trigger('click')
    await wrapper.find('button[title="Reset view"]').trigger('click')
    expect(candidateBehavior.calls).toEqual(['zoomIn', 'zoomOut', 'fitView', 'resetView'])
    wrapper.unmount()
  })

  it('RENDERER ERROR shows the error state with Retry — never a blank canvas', async () => {
    seed(denseWorld())
    candidateBehavior.throwOnRender = true
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('The graph renderer failed.')
    candidateBehavior.throwOnRender = false
    await wrapper.find('button.cta').trigger('click') // Retry
    await flushPromises()
    expect(wrapper.findAll('button.node').length).toBeGreaterThan(0)
    wrapper.unmount()
  })
})