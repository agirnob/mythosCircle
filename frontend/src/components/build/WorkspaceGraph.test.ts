// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import WorkspaceGraph from './WorkspaceGraph.vue'
import GraphView from '../graph/GraphView.vue'
import VueFlowGraph from '../graph/VueFlowGraph.vue'
import { denseWorld } from '../graph/denseFixture'
import { useWorldStore } from '../../stores/world'
import type { WorkspaceNode } from '../../stores/workspace'

vi.mock('../graph/VueFlowGraph.vue', () => ({
  default: {
    props: ['nodes', 'edges', 'focusId', 'oneHop', 'labelsVisible'],
    emits: ['refocus', 'clear-focus'],
    template: '<div class="shared-renderer" />',
  },
}))
beforeEach(() => setActivePinia(createPinia()))

describe('Workspace graph using the shared Graph view', () => {
  it('adapts nested drafts and membership without writing canon or the route', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/campaigns/:id/build-in', component: { template: '<div />' } }],
    })
    await router.push('/campaigns/C1/build-in?focus=N01')
    const store = useWorldStore()
    const original = denseWorld()
    store.byCampaign.C1 = { ...store.entry('C1'), world: original }
    store.mediaByCampaign.C1 = []
    const before = JSON.stringify(original)
    const nodes: WorkspaceNode[] = [
      { id: 'draft-place', kind: 'place', name: 'Market', guidance: '', parent_id: 'X03' },
      {
        id: 'draft-person',
        kind: 'character',
        name: 'Edda',
        guidance: '',
        parent_id: 'draft-place',
        faction_id: 'N01',
      },
    ]
    const wrapper = mount(WorkspaceGraph, {
      props: { campaignId: 'C1', nodes, selectedId: 'draft-place' },
      global: { plugins: [router] },
    })
    await flushPromises()
    expect(wrapper.findComponent(GraphView).props('embedded')).toBe(true)
    expect(wrapper.find('[aria-label="Breadcrumb"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Filter by relationship type"]').exists()).toBe(true)
    const renderer = wrapper.findComponent(VueFlowGraph)
    expect(renderer.props('nodes')).toHaveLength(original.entities.length + 2)
    expect(renderer.props('edges')).toHaveLength(original.edges.length + 3)
    expect(renderer.props('focusId')).toBe('draft-place')
    expect(renderer.props('oneHop')?.nodeIds.has('draft-person')).toBe(true)
    renderer.vm.$emit('refocus', 'draft-person')
    await flushPromises()
    expect(wrapper.emitted('select')?.at(-1)).toEqual(['draft-person'])
    expect(router.currentRoute.value.query.focus).toBe('N01')
    await wrapper.setProps({ selectedId: 'draft-person' })
    expect(renderer.props('focusId')).toBe('draft-person')
    renderer.vm.$emit('clear-focus')
    expect(wrapper.emitted('select')?.at(-1)).toEqual([null])
    await wrapper.setProps({
      nodes: [nodes[0]!, { ...nodes[1]!, name: 'Renamed Edda', parent_id: 'X03' }],
    })
    expect(renderer.props('nodes').find((n: { id: string }) => n.id === 'draft-person')?.name).toBe(
      'Renamed Edda',
    )
    expect(
      renderer.props('edges').filter((e: { src: string }) => e.src === 'draft-person'),
    ).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ dst: 'X03', type: 'located_in', planned: true }),
        expect.objectContaining({ dst: 'N01', type: 'member_of', planned: true }),
      ]),
    )
    await wrapper.setProps({ nodes: [] })
    expect(renderer.props('nodes')).toHaveLength(original.entities.length)
    expect(renderer.props('edges')).toHaveLength(original.edges.length)
    expect(JSON.stringify(store.entry('C1').world)).toBe(before)
    wrapper.unmount()
  })
})
