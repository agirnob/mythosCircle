// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { VueFlow } from '@vue-flow/core'

import { denseWorld } from './denseFixture'
import {
  avatarInitial,
  buildWorldGraph,
  type GraphEdge,
  type GraphRenderNode,
  type OneHop,
} from './graphModel'
import VueFlowGraph from './VueFlowGraph.vue'

// The winner's component test (story 7.1, whole-world edition): the OPPOSITE
// candidate was removed with its dependency after the A/B verdict (research-
// 7-1 records the evidence); git history keeps that record.

interface GraphProps {
  nodes: GraphRenderNode[]
  edges: GraphEdge[]
  focusId: string | null
  oneHop: OneHop | null
  labelsVisible: boolean
  nodeNameById: Record<string, string>
}

/** Vue Flow initializes its store asynchronously (dimension measurement +
 * viewport); a settle flushes those ticks before asserting on the render. */
async function mountGraph(worldProps: GraphProps) {
  const wrapper = mount(VueFlowGraph, { props: worldProps })
  await new Promise((resolve) => setTimeout(resolve, 30))
  await flushPromises()
  return wrapper
}

function worldProps(focusId: string | null, labelsVisible: boolean, omitNames = false): GraphProps {
  const model = buildWorldGraph(denseWorld(), focusId)
  return {
    nodes: model.nodes.map((node) => ({
      ...node,
      portraitUrl: null,
      initial: avatarInitial(node.name),
    })),
    edges: model.edges,
    focusId,
    oneHop: model.oneHop,
    labelsVisible,
    nodeNameById: omitNames ? {} : Object.fromEntries(model.nodes.map((node) => [node.id, node.name])),
  }
}

describe('VueFlowGraph (the chosen candidate, whole world)', () => {
  it('renders EVERY committed entity as a node card — 29, no cap', async () => {
    const wrapper = await mountGraph(worldProps(null, false))
    expect(wrapper.findAll('.graph-node')).toHaveLength(29)
    expect(wrapper.findAll('.graph-node .chip').length).toBe(29)
    expect(wrapper.findAll('.graph-node .name').length).toBe(29)
    expect(wrapper.text()).toContain('Pike the Rook, Harbormaster of the Ninth Quay')
    wrapper.unmount()
  })

  it('labels are OFF by default: no edges show labels without a focus or the toggle', async () => {
    const wrapper = await mountGraph(worldProps(null, false))
    expect(wrapper.findAll('.edge-label')).toHaveLength(0)
    wrapper.unmount()
  })

  it('focus highlights the 1-hop set and dims the rest (nodes and edges)', async () => {
    const wrapper = await mountGraph(worldProps('F0', false))
    // 29 nodes: the focus + 25 oneHop members stay full; X01..X03 dim.
    expect(wrapper.findAll('.graph-node.focus')).toHaveLength(1)
    expect(wrapper.findAll('.graph-node.dim')).toHaveLength(3)
    // 32 edges: the 30 focus-incident edges are active; e31/e32 (X-side)
    // dim.
    expect(wrapper.findAll('.graph-edge.active')).toHaveLength(30)
    expect(wrapper.findAll('.graph-edge.dim')).toHaveLength(2)
    wrapper.unmount()
  })

  it('focus-mode labels: only the 1-hop edges carry labels while the toggle is off', async () => {
    const wrapper = await mountGraph(worldProps('F0', false))
    expect(wrapper.findAll('.edge-label')).toHaveLength(30)
    wrapper.unmount()
  })

  it('labels toggle shows EVERY edge label when on', async () => {
    const withToggle = await mountGraph(worldProps('F0', true))
    expect(withToggle.findAll('.edge-label')).toHaveLength(32)
    withToggle.unmount()
    const withoutFocus = await mountGraph(worldProps(null, true))
    expect(withoutFocus.findAll('.edge-label')).toHaveLength(32)
    withoutFocus.unmount()
  })

  it('clicking a node emits refocus with that node id (drag never refocuses)', async () => {
    const wrapper = await mountGraph(worldProps('F0', false))
    await wrapper.find('.graph-node').trigger('click')
    const emitted = wrapper.emitted('refocus')
    expect(emitted).toBeTruthy()
    expect(emitted![0][0]).toBe('F0')
    wrapper.unmount()
  })

  it('pane click emits clear-focus (empty-space clears the focus)', async () => {
    const wrapper = await mountGraph(worldProps('F0', false))
    const vf = wrapper.findComponent(VueFlow)
    await vf.vm!.$emit('paneClick', {})
    expect(wrapper.emitted('clear-focus')).toBeTruthy()
    wrapper.unmount()
  })

  it('the focused node KEEPS its ring position — focus never repositions it', async () => {
    const before = await mountGraph(worldProps(null, false))
    const f0Before = before.find('.graph-node').element // F0 is first in entity order
    const transformBefore = f0Before.parentElement!.getAttribute('style') ?? ''
    before.unmount()

    const focused = await mountGraph(worldProps('F0', false))
    const f0Focused = focused.find('.graph-node.focus').element
    // Same flow-node wrapper position — no center jump, no relayout (the
    // card's own border/shadow DO change — that is the focus treatment).
    expect(f0Focused.parentElement!.getAttribute('style')).toBe(transformBefore)
    // And F0 is NOT parked at the graph origin — it sits on its ring.
    expect(transformBefore).not.toMatch(/translate\(0px, 0px\)/)
    focused.unmount()
  })

  it('a drag cycle suppresses refocus until the drag ends (drag guard)', async () => {
    const wrapper = await mountGraph(worldProps('F0', false))
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
    expect(emitted![0][0]).toBe('F0')
    wrapper.unmount()
  })

  it('edge hover (edge-mouse-enter) reveals the source → type → target tooltip', async () => {
    const wrapper = await mountGraph(worldProps('F0', false))
    const model = buildWorldGraph(denseWorld(), 'F0')
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

  it('a11y tooltip/title never leaks raw entity ids — unknown names render as "(unknown)"', async () => {
    const wrapper = await mountGraph(worldProps('F0', false, true))
    const titleElements = Array.from(wrapper.element.querySelectorAll('title'))
    const titles = titleElements.map((el) => (el as { textContent: string | null }).textContent ?? '')
    expect(titles.length).toBeGreaterThan(0)
    for (const title of titles) {
      expect(title).toContain('(unknown)')
      expect(title.match(/[0-9A-HJKMNP-TV-Z]{26}/)).toBeNull()
    }
    wrapper.unmount()
  })
})