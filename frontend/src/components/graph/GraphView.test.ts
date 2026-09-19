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
      rendered: [] as string[],
      /** Exposed toolbar surface calls, in order (keyboard/button wiring). */
      calls: [] as string[],
    },
  }
})

const { pushMock, setQuery, candidateBehavior } = cfg

function stub(name: string, cls: string) {
  return {
    name,
    props: {
      nodes: { type: Array, default: () => [] },
      edges: { type: Array, default: () => [] },
      focusId: { type: [String, null], default: null },
      labelsVisible: { type: Boolean, default: true },
      nodeNameById: { type: Object, default: () => ({}) },
    },
    emits: ['refocus'],
    setup(
      props: {
        nodes: Array<{ id: string; name: string }>
        edges: Array<{ label: string }>
        labelsVisible: boolean
      },
      context: {
        emit: (event: string, id: string) => void
        expose: (surface: Record<string, (...args: number[]) => void>) => void
      },
    ) {
      candidateBehavior.rendered.push(cls)
      if (candidateBehavior.throwOnRender) throw new Error('stub render failure')
      context.expose({
        zoomIn: () => candidateBehavior.calls.push('zoomIn'),
        zoomOut: () => candidateBehavior.calls.push('zoomOut'),
        fitView: () => candidateBehavior.calls.push('fitView'),
        resetView: () => candidateBehavior.calls.push('resetView'),
        panBy: (dx: number, dy: number) => candidateBehavior.calls.push(`panBy:${dx},${dy}`),
      })
      return () =>
        h('div', { class: cls }, [
          ...(props.nodes as Array<{ id: string; name: string }>).map((node) =>
            h(
              'button',
              { class: 'node', 'data-id': node.id, onClick: () => context.emit('refocus', node.id) },
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
}

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a class="router-link"><slot /></a>' },
  useRoute: () => ({
    params: { id: 'C1' },
    get query() {
      return { ...cfg.query }
    },
  }),
  useRouter: () => ({ replace: cfg.pushMock }),
}))

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

vi.mock('./VueFlowGraph.vue', () => ({ default: stub('VueFlowGraphStub', 'candidate-vueflow') }))

import GraphView from './GraphView.vue'
import { apiFetch } from '../../api/client'

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
    return mount(GraphView, { global: { provide: {} } })
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    setQuery({})
    candidateBehavior.throwOnRender = false
    candidateBehavior.rendered = []
    candidateBehavior.calls = []
    pushMock.mockClear()
  })

  it('renders nodes + edges from a store-provided world with HUD counts and truncation notice', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const nodes = wrapper.findAll('button.node')
    expect(nodes).toHaveLength(24)
    expect(wrapper.findAll('span.edge').length).toBeGreaterThan(0)
    const edgeLabels = wrapper.findAll('span.edge').map((span) => span.text())
    expect(edgeLabels).toContain('ally_of(2)')
    expect(edgeLabels).toContain('employs')
    expect(wrapper.text()).toContain('24 entities')
    expect(wrapper.text()).toContain('28 relationships')
    expect(wrapper.text()).toContain(
      `showing 23 of 25 connected entities, plus the focused entity`,
    )
    wrapper.unmount()
  })

  it('breadcrumb is human-readable: Campaigns / world / Graph / entity name — no raw ULID shape', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('Campaigns')
    expect(text).toContain('The Drowned Harbor (dense fixture)')
    expect(text).toContain('Graph')
    expect(text).toContain('Pike the Rook, Harbormaster of the Ninth Quay')
    // No 26-char ULID-looking tokens leak into the UI.
    expect(text.match(/[0-9A-HJKMNP-TV-Z]{26}/)).toBeNull()
    wrapper.unmount()
  })

  it('node click refocuses: router.replace carries the new ?focus', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('button.node[data-id="N01"]').trigger('click')
    const call = pushMock.mock.calls[pushMock.mock.calls.length - 1][0]
    expect(call.query.focus).toBe('N01')
    wrapper.unmount()
  })

  it('NO FOCUS: defaults to the first entity and surfaces ?focus in the URL', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const call = pushMock.mock.calls.find(([args]) => args?.query?.focus === DENSE_FOCUS_ID)
    expect(call).toBeTruthy()
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

  it('MISSING FOCUS renders the empty-state with a hint, not a crash', async () => {
    seed(denseWorld())
    setQuery({ focus: 'NO-SUCH-ENTITY' })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('That entity is not in this world.')
    expect(wrapper.findAll('button.node')).toHaveLength(0)
    wrapper.unmount()
  })

  it('NO RELATIONSHIPS state still renders the lone focus node with a notice', async () => {
    const w = denseWorld()
    w.entities = w.entities.slice(0, 1)
    w.edges = []
    seed(w)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.findAll('button.node')).toHaveLength(1)
    expect(wrapper.text()).toContain('No relationships')
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
    expect(after.length).toBeLessThan(24)
    wrapper.unmount()
  })

  it('FILTERED EMPTY renders the message with a reset hint that recovers', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    // Kind + relationship filters together: F0's employs edge touches only
    // characters/factions, so place + employs excludes every visible node.
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

  it('labels toggle is consumed by the candidate: edge labels drop when off', async () => {
    seed(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const canvas = () => wrapper.find('.canvas')
    expect(canvas().findAll('span.edge').length).toBeGreaterThan(0)
    const checkbox = wrapper.find('input[aria-label="Show edge labels"]')
    expect((checkbox.element as unknown as { checked: boolean }).checked).toBe(true)
    await checkbox.setValue(false)
    await flushPromises()
    expect((checkbox.element as unknown as { checked: boolean }).checked).toBe(false)
    expect(canvas().findAll('span.edge')).toHaveLength(0)
    wrapper.unmount()
  })

  it('cold mount without ?focus gains it once the world lands (async store load)', async () => {
    // Empty store entry: the view must fetch through the store and only then
    // default the focus to the first entity and surface it in the URL.
    seed(null)
    vi.mocked(apiFetch).mockResolvedValueOnce(denseWorld())
    const wrapper = mountView()
    await flushPromises()
    const call = pushMock.mock.calls.find(([args]) => args?.query?.focus === DENSE_FOCUS_ID)
    expect(call).toBeTruthy()
    wrapper.unmount()
  })

  it('noFilteredRelationships: kind filter keeps nodes, relationship filter empties the edge set', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    // loyalty edges touch F0(char) + N05/N17 (places) — kind=character keeps
    // the focus but drops both loyalty endpoints → edges empty, nodes remain.
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

  it('filtered HUD counts track the rendered subset, not the model', async () => {
    seed(denseWorld())
    setQuery({ focus: DENSE_FOCUS_ID })
    const wrapper = mountView()
    await flushPromises()
    // Unfiltered: rendered == model counts.
    expect(wrapper.text()).toContain('24 entities')
    expect(wrapper.text()).toContain('28 relationships')
    await wrapper
      .find('select[aria-label="Filter by entity type"]')
      .setValue('place')
    await flushPromises()
    const canvasNodes = wrapper.find('.canvas').findAll('button.node').length
    expect(wrapper.find('.hud').text()).toContain(`${canvasNodes} entities`)
    // Truncation note stays model-truncation, but only shows unfiltered.
    expect(wrapper.find('.hud').text()).not.toContain('showing 23 of 25')
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