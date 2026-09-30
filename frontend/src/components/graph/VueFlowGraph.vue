<script setup lang="ts">
/**
 * Story 7.1 — Vue Flow winner renderer (whole-world redesign, owner verdict
 * 2026-09-19). Consumes the shared graphModel output and renders the ENTIRE
 * committed world:
 *   - deterministic cluster layout (semantic faction areas + force
 *     refinement): members hug their faction, places settle near anchors,
 *     no perfect circles; memoized per world change — focusing never moves
 *     or re-lays out a node,
 *   - every committed edge drawn; labels OFF by default,
 *   - focus (from ?focus= or node click): the 1-hop set (both directions)
 *     renders at full opacity WITH edge labels, everything else dims
 *     (grey, still visible); the focus card keeps its halo + strong border,
 *   - click empty canvas → `clear-focus` (GraphView drops ?focus=),
 *   - hover/selection tooltip source → type(counter) → target (ULID-free),
 *   - per-edge connector handles + curvature so parallel/reciprocal edges
 *     anchor apart and curve distinctly,
 *   - drag-guarded refocus, keyboard-activatable cards, pan/zoom/pinch,
 *     toolbar surface (zoomIn/zoomOut/fitView/resetView/panBy).
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useId } from 'vue'
import { VueFlow, useVueFlow, getBezierPath, Position } from '@vue-flow/core'
import type { EdgeProps, Node, Edge, ViewportTransform } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'

import type { GraphRenderEdge, GraphRenderNode, OneHop } from './graphModel'
import {
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  clusterLayout,
  parallelSlots,
  vueFlowCurvature,
} from './graphModel'
import GraphNodeCard from './GraphNodeCard.vue'

const props = defineProps<{
  nodes: GraphRenderNode[]
  edges: GraphRenderEdge[]
  focusId: string | null
  /** The focus's 1-hop incidence (null when none) — drives highlight/dim. */
  oneHop: OneHop | null
  labelsVisible: boolean
  nodeNameById: Record<string, string>
}>()

const emit = defineEmits<{
  (event: 'refocus', id: string): void
  (event: 'clear-focus'): void
}>()

const flow = useVueFlow()

const markerId = `graph-vf-arrow-${useId()}`
const markerIdActive = `${markerId}-active`
const nodeTypes = { graph: GraphNodeCard }

const hoveredEdgeId = ref<string | null>(null)
const selectedEdgeId = ref<string | null>(null)
const dragging = ref(false)
const paneMoved = ref(false)
const initialViewport = ref<ViewportTransform | null>(null)

/** Ring positions per kind — memoized ONCE per world change. Focus changes
 * never move a node: positions are a pure function of the node set and the
 * focus only drives the highlight/dim classes. */
const ringPositions = ref<Map<string, { x: number; y: number }>>(new Map())

interface HandleSpec {
  edgeId: string
  pct: number
  position: Position
}

function handleSpecs(
  edges: GraphRenderEdge[],
  nodeId: string,
  side: 'src' | 'dst',
  positions: Map<string, { x: number; y: number }>,
): HandleSpec[] {
  const incident = edges.filter((edge) => (side === 'src' ? edge.src === nodeId : edge.dst === nodeId))
  const groups = new Map<Position, GraphRenderEdge[]>()
  for (const edge of incident) {
    const otherId = side === 'src' ? edge.dst : edge.src
    const here = positions.get(nodeId)
    const other = positions.get(otherId)
    const dx = (other?.x ?? here?.x ?? 0) - (here?.x ?? 0)
    const dy = (other?.y ?? here?.y ?? 0) - (here?.y ?? 0)
    const position = Math.abs(dx) >= Math.abs(dy)
      ? (dx >= 0 ? Position.Right : Position.Left)
      : (dy >= 0 ? Position.Bottom : Position.Top)
    const list = groups.get(position)
    if (list) list.push(edge)
    else groups.set(position, [edge])
  }
  return incident.map((edge) => {
    const otherId = side === 'src' ? edge.dst : edge.src
    const here = positions.get(nodeId)
    const other = positions.get(otherId)
    const dx = (other?.x ?? here?.x ?? 0) - (here?.x ?? 0)
    const dy = (other?.y ?? here?.y ?? 0) - (here?.y ?? 0)
    const position = Math.abs(dx) >= Math.abs(dy)
      ? (dx >= 0 ? Position.Right : Position.Left)
      : (dy >= 0 ? Position.Bottom : Position.Top)
    const group = groups.get(position) ?? [edge]
    const index = group.indexOf(edge)
    return {
      edgeId: edge.id,
      position,
      pct: group.length > 1 ? 18 + 64 * (index / (group.length - 1)) : 50,
    }
  })
}

const focusActive = computed(() => props.focusId !== null && props.oneHop !== null)

/** A node is dimmed when a focus is active and it is outside the 1-hop set. */
function nodeDimmed(nodeId: string): boolean {
  return focusActive.value && !props.oneHop!.nodeIds.has(nodeId)
}

const flowNodes = computed<Node[]>(() =>
  props.nodes.map((node) => {
    const focused = node.id === props.focusId
    const ring = ringPositions.value.get(node.id)
    // The focus NEVER moves (owner verdict 2026-09-19): every node keeps its
    // ring position; focus only changes the highlight/dim classes.
    const position = ring ?? { x: -GRAPH_NODE_WIDTH / 2, y: -GRAPH_NODE_HEIGHT / 2 }
    return {
      id: node.id,
      type: 'graph',
      position,
      width: GRAPH_NODE_WIDTH,
      height: GRAPH_NODE_HEIGHT,
      data: {
        node,
        focused,
        dimmed: nodeDimmed(node.id),
        outHandles: handleSpecs(props.edges, node.id, 'src', ringPositions.value),
        inHandles: handleSpecs(props.edges, node.id, 'dst', ringPositions.value),
      },
    }
  }),
)

const slots = computed(() => parallelSlots(props.edges))

const flowEdges = computed<Edge[]>(() =>
  props.edges.map((edge) => {
    const slot = slots.value.get(edge.id) ?? { index: 0, total: 1 }
    const active = focusActive.value && props.oneHop!.edgeIds.has(edge.id)
    return {
      id: edge.id,
      type: 'graph',
      source: edge.src,
      target: edge.dst,
      sourceHandle: `out-${edge.id}`,
      targetHandle: `in-${edge.id}`,
      data: {
        edge,
        active,
        dimmed: focusActive.value && !active,
        curvature: vueFlowCurvature(slot),
        labelDy: (slot.index - (slot.total - 1) / 2) * 7,
      },
    }
  }),
)

/** Labels: shown for the focus's 1-hop edges when a focus is active, or for
 * EVERY edge when the toolbar Labels toggle is on — off by default. */
function labelVisibleFor(p: EdgeProps): boolean {
  const data = p.data as { edge: GraphRenderEdge }
  return props.labelsVisible || (focusActive.value && props.oneHop!.edgeIds.has(data.edge.id))
}

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

/** Any pan/zoom gesture starts a potential viewport-drag; its release
 * click must not be read as a deliberate empty-space click. */
function onViewportGestureStart() {
  paneMoved.value = true
}
function onPaneScroll() {
  paneMoved.value = true
}

/** Empty-space click clears the focus (URL drops ?focus=) — EXCEPT when the
 * click is the release of a pan/zoom gesture (paneClick fires after pane
 * drags end; clearing then would silently drop a deliberate focus). */
function onPaneClick() {
  hoveredEdgeId.value = null
  selectedEdgeId.value = null
  if (paneMoved.value) {
    paneMoved.value = false
    return
  }
  emit('clear-focus')
}

function onNodeDragStart() {
  dragging.value = true
}
function onNodeDragStop() {
  globalThis.setTimeout(() => {
    dragging.value = false
  }, 0)
}

/** Fit the current world into view (a world CHANGE — new node set — refits;
 * a focus change never reaches this path because GraphView passes the SAME
 * node/edge arrays and only the oneHop/focusId props move). The first
 * SUCCESSFUL fit also captures the "reset" home viewport (fitView resolves
 * false pre-init). */
async function fitAfterRender() {
  await nextTick()
  const fitted = await flow.fitView({ padding: 0.12, duration: 200 })
  if (!initialViewport.value && fitted) {
    initialViewport.value = flow.getViewport()
  }
}

watch(
  () => [props.nodes, props.edges] as const,
  () => {
    hoveredEdgeId.value = null
    selectedEdgeId.value = null
    // The watch observes ARRAY IDENTITY: GraphView keeps the world's
    // node/edge arrays stable across focus changes, so this fires only on a
    // real world change (or a filter) — never on refocus.
    ringPositions.value = new Map(
      clusterLayout(props.nodes, props.edges).map((position) => [position.id, { x: position.x, y: position.y }]),
    )
    void fitAfterRender()
  },
  { immediate: true },
)

/** VueFlow initializes its store/panes asynchronously; fit only once ready.
 * The home viewport is captured by that fit (see fitAfterRender). */
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
        <marker
          :id="markerIdActive"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="11"
          markerHeight="11"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#b45309" />
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
      @viewport-change-start="onViewportGestureStart"
      @pane-scroll="onPaneScroll"
    >
      <template #edge-graph="p">
        <g
          class="graph-edge"
          :class="{
            active: (p.data as { active: boolean }).active,
            dim: (p.data as { dimmed: boolean }).dimmed,
            hovered: p.id === hoveredEdgeId,
            selected: p.id === selectedEdgeId,
          }"
        >
          <path
            :d="edgePath(p)"
            :marker-end="(p.data as { active: boolean }).active ? `url(#${markerIdActive})` : `url(#${markerId})`"
          />
          <title>{{ tooltipText(p) }}</title>
          <text
            v-if="labelVisibleFor(p)"
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
              <span class="tt-src">{{
                props.nodeNameById[(p.data as { edge: GraphRenderEdge }).edge.src] ?? '(unknown)'
              }}</span>
              <span class="tt-arrow">→</span>
              <span class="tt-label">{{ (p.data as { edge: GraphRenderEdge }).edge.label }}</span>
              <span class="tt-arrow">→</span>
              <span class="tt-dst">{{
                props.nodeNameById[(p.data as { edge: GraphRenderEdge }).edge.dst] ?? '(unknown)'
              }}</span>
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
  stroke: var(--mc-text-muted);
  stroke-width: 1.8;
  fill: none;
}
/* Whole-world dimming: non-1-hop edges under an active focus (grey, visible). */
.graph-edge.dim {
  opacity: 0.18;
}
.graph-edge.active path {
  stroke: var(--mc-warning);
  stroke-width: 2.6;
}
.graph-edge.hovered path,
.graph-edge.selected path {
  stroke: var(--mc-interactive-bright);
  stroke-width: 2.6;
}
.graph-edge .edge-label {
  font: 600 11.5px system-ui, sans-serif;
  fill: var(--mc-text-primary);
  paint-order: stroke;
  stroke: var(--mc-surface);
  stroke-width: 4px;
  stroke-linejoin: round;
  pointer-events: none;
}
.graph-tooltip {
  font: 11px ui-monospace, Menlo, monospace;
  color: var(--mc-text-primary);
  background: var(--mc-surface-raised);
  border: 1px solid var(--mc-border-bright);
  border-radius: 7px;
  padding: 5px 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.38);
  width: max-content;
  max-width: 300px;
}
.tt-arrow {
  color: var(--mc-warning);
  font-weight: 700;
  margin: 0 2px;
}
.tt-label {
  font-weight: 700;
}
</style>
