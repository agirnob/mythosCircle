// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import { denseWorld } from './denseFixture'
import {
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  avatarInitial,
  buildGraphModel,
  type GraphEdge,
  type GraphRenderNode,
} from './graphModel'
import VueFlowGraph from './VueFlowGraph.vue'
import { VueFlow } from '@vue-flow/core'

interface GraphProps {
  nodes: GraphRenderNode[]
  edges: GraphEdge[]
  focusId: string | null
  labelsVisible: boolean
  nodeNameById: Record<string, string>
}

// The winner-only component test (story 7.1): the loser (Cytoscape) has no
// component test — it is removed with the losing dependency in the final
// commit; git history + research-7-1 keep the A/B evidence.

/** Vue Flow measures nodes with ResizeObserver — happy-dom lacks it, so the
 * test renders with a no-op observer (layout timing is not under test). */
/** Vue Flow initializes its store asynchronously (dimension measurement +
 * viewport); a settle flushes those ticks before asserting on the render. */
async function mountGraph(worldProps: GraphProps) {
  const wrapper = mount(VueFlowGraph, { props: worldProps })
  await new Promise((resolve) => setTimeout(resolve, 30))
  await flushPromises()
  return wrapper
}

describe('VueFlowGraph (the chosen candidate)', () => {
  it('renders one card per graphModel node with mockup content (name, chip, initial avatar)', async () => {
    const world = denseWorld()
    const model = buildGraphModel(world, world.entities[0].id)
    const wrapper = await mountGraph({
      nodes: model.nodes.map((node) => ({ ...node, portraitUrl: null, initial: avatarInitial(node.name) })),
      edges: model.edges,
      focusId: world.entities[0].id,
      labelsVisible: true,
      nodeNameById: Object.fromEntries(world.entities.map((e) => [e.id, e.name])),
    })
    expect(wrapper.findAll('.graph-node')).toHaveLength(24)
    expect(wrapper.findAll('.graph-node .chip').length).toBe(24)
    expect(wrapper.findAll('.graph-node .name').length).toBe(24)
    expect(wrapper.text()).toContain('Pike the Rook, Harbormaster of the Ninth Quay')
    wrapper.unmount()
  })

  it('clicking a node emits refocus with that node id (drag never refocuses)', async () => {
    const world = denseWorld()
    const model = buildGraphModel(world, world.entities[0].id)
    const wrapper = await mountGraph({
      nodes: model.nodes.map((node) => ({ ...node, portraitUrl: null, initial: avatarInitial(node.name) })),
      edges: model.edges,
      focusId: world.entities[0].id,
      labelsVisible: true,
      nodeNameById: Object.fromEntries(world.entities.map((e) => [e.id, e.name])),
    })
    const node = wrapper.find('.graph-node')
    await node.trigger('click')
    const emitted = wrapper.emitted('refocus')
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toBe(world.entities[0].id)
    wrapper.unmount()
  })

  it('labels toggle + edge label content (selective visibility contract)', async () => {
    const world = denseWorld()
    const model = buildGraphModel(world, world.entities[0].id)
    const wrapper = await mountGraph({
      nodes: model.nodes.map((node) => ({ ...node, portraitUrl: null, initial: avatarInitial(node.name) })),
      edges: model.edges,
      focusId: world.entities[0].id,
      labelsVisible: false,
      nodeNameById: Object.fromEntries(world.entities.map((e) => [e.id, e.name])),
    })
    // With the toggle off, no edge labels render (selective visibility).
    expect(wrapper.findAll('.edge-label').length).toBe(0)
    await wrapper.setProps({ labelsVisible: true })
    expect(wrapper.findAll('.edge-label').length).toBeGreaterThan(0)
    wrapper.unmount()
  })

  it('node geometry is a deterministic engine choice, shared by both candidates', () => {
    expect(GRAPH_NODE_WIDTH).toBe(186)
    expect(GRAPH_NODE_HEIGHT).toBe(66)
  })

  it('a drag cycle suppresses refocus until the drag ends (drag guard)', async () => {
    const world = denseWorld()
    const model = buildGraphModel(world, world.entities[0].id)
    const wrapper = await mountGraph({
      nodes: model.nodes.map((node) => ({ ...node, portraitUrl: null, initial: avatarInitial(node.name) })),
      edges: model.edges,
      focusId: world.entities[0].id,
      labelsVisible: true,
      nodeNameById: Object.fromEntries(world.entities.map((e) => [e.id, e.name])),
    })
    const node = wrapper.find('.graph-node')
    const vf = wrapper.findComponent(VueFlow)
    // Drag in progress (Vue Flow's nodeDragStart fired): a click must NOT
    // refocus — the guard holds.
    await vf.vm!.$emit('nodeDragStart', {})
    await node.trigger('click')
    expect(wrapper.emitted('refocus')).toBeFalsy()
    // Drag ended: the very same click now refocuses as usual.
    await vf.vm!.$emit('nodeDragStop', {})
    await new Promise((resolve) => setTimeout(resolve, 0)) // drag clear tick
    await node.trigger('click')
    const emitted = wrapper.emitted('refocus')
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toBe(world.entities[0].id)
    wrapper.unmount()
  })

  it('a11y tooltip/title never leaks raw entity ids — unknown names render as "(unknown)"', async () => {
    const world = denseWorld()
    const model = buildGraphModel(world, world.entities[0].id)
    // Deliberately drop the name map: every endpoint becomes "unknown".
    const wrapper = await mountGraph({
      nodes: model.nodes.map((node) => ({ ...node, portraitUrl: null, initial: avatarInitial(node.name) })),
      edges: model.edges,
      focusId: world.entities[0].id,
      labelsVisible: true,
      nodeNameById: {},
    })
    const titleElements = Array.from(wrapper.element.querySelectorAll('title'))
    const titles = titleElements.map((el) => (el as { textContent: string | null }).textContent ?? '')
    expect(titles.length).toBeGreaterThan(0)
    for (const title of titles) {
      expect(title).toContain('(unknown)')
      expect(title.match(/[0-9A-HJKMNP-TV-Z]{26}/)).toBeNull()
    }
    wrapper.unmount()
  })

  it('edge hover (edge-mouse-enter) reveals the source → type → target tooltip', async () => {
    const world = denseWorld()
    const model = buildGraphModel(world, world.entities[0].id)
    const wrapper = await mountGraph({
      nodes: model.nodes.map((node) => ({ ...node, portraitUrl: null, initial: avatarInitial(node.name) })),
      edges: model.edges,
      focusId: world.entities[0].id,
      labelsVisible: true,
      nodeNameById: Object.fromEntries(world.entities.map((e) => [e.id, e.name])),
    })
    const edge = model.edges[0]
    const vf = wrapper.findComponent(VueFlow)
    await vf.vm!.$emit('edgeMouseEnter', { edge: { id: edge.id } })
    const tooltip = wrapper.find('.graph-tooltip')
    expect(tooltip.exists()).toBe(true)
    const text = tooltip.text()
    expect(text).toContain('→')
    expect(text).toContain(edge.label)
    wrapper.unmount()
  })
})