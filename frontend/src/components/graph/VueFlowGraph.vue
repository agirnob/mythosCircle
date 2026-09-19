<script setup lang="ts">
/**
 * Story 7.1 — Vue Flow candidate. Consumes the shared graphModel output
 * (GraphView props), lays nodes out on a deterministic focus-centered
 * concentric ring (Vue Flow ships no layout algorithm — this is the
 * hand-rolled one), and renders the mockup-7-1 presentation:
 *   - per-edge connector handles so parallel/reciprocal edges anchor at
 *     distinct border points and curve apart (getBezierPath curvature per
 *     parallel slot),
 *   - directed arrowheads that stop at the node border,
 *   - selectively visible edge labels (zoom / hover / selection),
 *   - hover/selection tooltip: source → type(counter) → target,
 *   - click-to-refocus (guarded against drags),
 *   - exposed zoomIn/zoomOut/fitView/resetView/panBy for the view toolbar.
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useId } from 'vue'
import { VueFlow, useVueFlow, getBezierPath } from '@vue-flow/core'
import type { EdgeProps, Node, Edge, ViewportTransform } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'

import type { GraphRenderEdge, GraphRenderNode } from './graphModel'
import {
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  parallelSlots,
  vueFlowCurvature,
} from './graphModel'
import GraphNodeCard from './GraphNodeCard.vue'

const props = defineProps<{
  nodes: GraphRenderNode[]
  edges: GraphRenderEdge[]
  focusId: string | null
  labelsVisible: boolean
  nodeNameById: Record<string, string>
}>()

const emit = defineEmits<{ (event: 'refocus', id: string): void }>()

const flow = useVueFlow()

const markerId = `graph-vf-arrow-${useId()}`
const nodeTypes = { graph: GraphNodeCard }

const hoveredEdgeId = ref<string | null>(null)
const selectedEdgeId = ref<string | null>(null)
const dragging = ref(false)
const initialViewport = ref<ViewportTransform | null>(null)

/** Concentric layout: focus dead center, neighbors on a ring sized to the
 * count so node-on-node overlap is impossible at rest (deterministic). */
function positionsFor(count: number): Array<{ x: number; y: number }> {
  const positions: Array<{ x: number; y: number }> = []
  if (count === 0) return positions
  const radius = Math.max(240, (count * (GRAPH_NODE_WIDTH + 26)) / (2 * Math.PI))
  for (let i = 0; i < count; i += 1) {
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / count
    positions.push({
      x: radius * Math.cos(angle) - GRAPH_NODE_WIDTH / 2,
      y: radius * Math.sin(angle) - GRAPH_NODE_HEIGHT / 2,
    })
  }
  return positions
}

interface HandleSpec {
  edgeId: string
  pct: number
}

function handleSpecs(edges: GraphRenderEdge[], nodeId: string, side: 'src' | 'dst'): HandleSpec[] {
  const incident = edges.filter((edge) => (side === 'src' ? edge.src === nodeId : edge.dst === nodeId))
  return incident.map((edge, index) => ({
    edgeId: edge.id,
    pct: incident.length > 1 ? 16 + 68 * (index / (incident.length - 1)) : 50,
  }))
}

const flowNodes = computed<Node[]>(() => {
  const neighbor = props.nodes.filter((node) => node.id !== props.focusId)
  const positions = positionsFor(neighbor.length)
  const posByOffset: Array<{ x: number; y: number }> = [
    { x: -GRAPH_NODE_WIDTH / 2, y: -GRAPH_NODE_HEIGHT / 2 },
    ...positions,
  ]
  let offset = 0
  return props.nodes.map((node) => {
    const position = posByOffset[offset] ?? { x: 0, y: 0 }
    offset += 1
    return {
      id: node.id,
      type: 'graph',
      position,
      width: GRAPH_NODE_WIDTH,
      height: GRAPH_NODE_HEIGHT,
      data: {
        node,
        outHandles: handleSpecs(props.edges, node.id, 'src'),
        inHandles: handleSpecs(props.edges, node.id, 'dst'),
      },
    }
  })
})

const slots = computed(() => parallelSlots(props.edges))

const flowEdges = computed<Edge[]>(() =>
  props.edges.map((edge) => {
    const slot = slots.value.get(edge.id) ?? { index: 0, total: 1 }
    return {
      id: edge.id,
      type: 'graph',
      source: edge.src,
      target: edge.dst,
      sourceHandle: `out-${edge.id}`,
      targetHandle: `in-${edge.id}`,
      data: {
        edge,
        curvature: vueFlowCurvature(slot),
        labelDy: (slot.index - (slot.total - 1) / 2) * 7,
      },
    }
  }),
)

/** Selective edge-label visibility: labels toggle AND (zoomed in OR the edge
 * is hovered/selected) — no permanent label pile-up at density. */
const edgeLabelsOn = computed(
  () =>
    props.labelsVisible &&
    (flow.viewport.value.zoom >= 0.8 || hoveredEdgeId.value !== null || selectedEdgeId.value !== null),
)

function edgePath(p: EdgeProps): string {
  const data = p.data as { curvature: number; edge: GraphRenderEdge }
  if (data.edge.src === data.edge.dst) {
    // Self-loop: a small arc over the node instead of a degenerate line.
    return `M ${p.sourceX} ${p.sourceY} C ${p.sourceX + 34} ${p.sourceY - 46}, ${p.targetX - 34} ${p.targetY - 46}, ${p.targetX} ${p.targetY}`
  }
  const [path] = getBezierPath({
    sourceX: p.sourceX,
    sourceY: p.sourceY,
    sourcePosition: p.sourcePosition,
    targetX: p.targetX,
    targetY: p.targetY,
    targetPosition: p.targetPosition,
    curvature: data.curvature,
  })
  return path
}

function labelPoint(p: EdgeProps): { x: number; y: number } {
  const data = p.data as { labelDy: number; curvature: number }
  const [, labelX, labelY] = getBezierPath({
    sourceX: p.sourceX,
    sourceY: p.sourceY,
    sourcePosition: p.sourcePosition,
    targetX: p.targetX,
    targetY: p.targetY,
    targetPosition: p.targetPosition,
    curvature: data.curvature,
  })
  return { x: labelX + 34, y: labelY - 7 + data.labelDy }
}

function tooltipText(p: EdgeProps): string {
  const data = p.data as { edge: GraphRenderEdge }
  const { edge } = data
  // Never leak the raw entity id into the UI — unknown names render as
  // "(unknown)" (a missing name still means the endpoint is unknown).
  return `${props.nodeNameById[edge.src] ?? '(unknown)'} → ${edge.label} → ${props.nodeNameById[edge.dst] ?? '(unknown)'}`
}

function onNodeClick({ node }: { node: { id: string } }) {
  if (dragging.value) return
  emit('refocus', node.id)
}

function onEdgeEnter({ edge }: { edge: { id: string } }) {
  hoveredEdgeId.value = edge.id
}
function onEdgeLeave() {
  hoveredEdgeId.value = null
}
function onEdgeClick({ edge }: { edge: { id: string } }) {
  selectedEdgeId.value = edge.id
}
function onPaneClick() {
  hoveredEdgeId.value = null
  selectedEdgeId.value = null
}
function onNodeDragStart() {
  dragging.value = true
}
function onNodeDragStop() {
  globalThis.setTimeout(() => {
    dragging.value = false
  }, 0)
}

/** Refocus keeps the same campaign: the new set replaces the old, then the
 * view fits once the re-render settles (a smooth focus transition). The
 * post-fit viewport is the "reset" home position — captured on the first
 * fit that runs while the viewport is initialized. */
async function fitAfterRender() {
  await nextTick()
  const fitted = await flow.fitView({ padding: 0.12, duration: 200 })
  // The first SUCCESSFUL fit means the viewport exists — that is when the
  // "reset" home position is captured (fitView resolves false pre-init).
  if (!initialViewport.value && fitted) {
    initialViewport.value = flow.getViewport()
  }
}

watch(
  () => props.nodes,
  () => {
    hoveredEdgeId.value = null
    selectedEdgeId.value = null
    void fitAfterRender()
  },
  { immediate: true },
)

/** VueFlow initializes its store/panes asynchronously; fit only once ready.
 * The home viewport is captured by that fit (see fitAfterRender) — never
 * read before the viewport exists. */
function onFlowInit() {
  void fitAfterRender()
}

// Toolbar surface (GraphView wires its buttons to these).
async function zoomIn() {
  await flow.zoomIn({ duration: 150 })
}
async function zoomOut() {
  await flow.zoomOut({ duration: 150 })
}
async function fitView() {
  await flow.fitView({ padding: 0.12, duration: 150 })
}
async function resetView() {
  if (initialViewport.value) {
    await flow.setViewport(initialViewport.value, { duration: 150 })
  } else {
    await flow.fitView({ padding: 0.12, duration: 150 })
  }
}
async function panBy(dx: number, dy: number) {
  const viewport = flow.getViewport()
  await flow.setViewport({ x: viewport.x + dx, y: viewport.y + dy, zoom: viewport.zoom }, { duration: 80 })
}

defineExpose({ zoomIn, zoomOut, fitView, resetView, panBy })
</script>

<template>
  <div class="vue-flow-graph">
    <svg width="0" height="0" aria-hidden="true">
      <defs>
        <marker
          :id="markerId"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="11"
          markerHeight="11"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#4b5563" />
        </marker>
      </defs>
    </svg>
    <VueFlow
      :nodes="flowNodes"
      :edges="flowEdges"
      :node-types="nodeTypes"
      :min-zoom="0.1"
      :max-zoom="2.5"
      :nodes-connectable="false"
      :edges-updatable="false"
      :delete-key-code="null"
      :pan-on-scroll="false"
      @init="onFlowInit"
      @node-click="onNodeClick"
      @node-drag-start="onNodeDragStart"
      @node-drag-stop="onNodeDragStop"
      @edge-mouse-enter="onEdgeEnter"
      @edge-mouse-leave="onEdgeLeave"
      @edge-click="onEdgeClick"
      @pane-click="onPaneClick"
    >
      <template #edge-graph="p">
        <g
          class="graph-edge"
          :class="{ hovered: p.id === hoveredEdgeId, selected: p.id === selectedEdgeId }"
        >
          <path :d="edgePath(p)" :marker-end="`url(#${markerId})`" />
          <title>{{ tooltipText(p) }}</title>
          <text
            v-if="edgeLabelsOn"
            class="edge-label"
            :x="labelPoint(p).x"
            :y="labelPoint(p).y"
            text-anchor="middle"
          >
            {{ (p.data as { edge: GraphRenderEdge }).edge.label }}
          </text>
          <foreignObject
            v-if="p.id === hoveredEdgeId || p.id === selectedEdgeId"
            :x="labelPoint(p).x - 8"
            :y="labelPoint(p).y + 8"
            width="320"
            height="58"
            class="tooltip-box"
          >
            <div class="graph-tooltip">
              <span class="tt-src">{{ props.nodeNameById[(p.data as { edge: GraphRenderEdge }).edge.src] }}</span>
              <span class="tt-arrow">→</span>
              <span class="tt-label">{{ (p.data as { edge: GraphRenderEdge }).edge.label }}</span>
              <span class="tt-arrow">→</span>
              <span class="tt-dst">{{ props.nodeNameById[(p.data as { edge: GraphRenderEdge }).edge.dst] }}</span>
            </div>
          </foreignObject>
        </g>
      </template>
    </VueFlow>
  </div>
</template>

<style scoped>
.vue-flow-graph {
  width: 100%;
  height: 100%;
  min-height: 0;
}
.graph-edge path {
  stroke: #7d8695;
  stroke-width: 1.8;
  fill: none;
}
.graph-edge.hovered path,
.graph-edge.selected path {
  stroke: #b45309;
  stroke-width: 2.6;
}
.graph-edge .edge-label {
  font: 600 11.5px system-ui, sans-serif;
  fill: #1c2330;
  paint-order: stroke;
  stroke: #ffffff;
  stroke-width: 3px;
  stroke-linejoin: round;
  pointer-events: none;
}
.graph-tooltip {
  font: 11px ui-monospace, Menlo, monospace;
  color: #1c2330;
  background: #ffffff;
  border: 1px solid #d8dbe0;
  border-radius: 7px;
  padding: 5px 8px;
  box-shadow: 0 2px 8px rgba(20, 30, 50, 0.15);
  width: max-content;
  max-width: 300px;
}
.tt-arrow {
  color: #b45309;
  font-weight: 700;
  margin: 0 2px;
}
.tt-label {
  font-weight: 700;
}
</style>