<script setup lang="ts">
/**
 * Story 7.1 — the graph view shell.
 *
 * Reads the world store ONLY (AR16/AD-20): the snapshot comes from
 * `world.entry(campaignId).world` and portraits from the store's media
 * manifest; no view-local API calls, no private caches. The renderer is the
 * spike winner — Vue Flow (@vue-flow/core); the Cytoscape A/B and its verdict
 * live in research-7-1-graph-visualizer.md.
 *
 * State machine: loader / notFound / error / empty-world / missing-focus /
 * no-relationships / filtered-empty / renderer-error — never a blank canvas.
 * Breadcrumb is human-readable (Campaigns / <world> / Graph / <entity>);
 * the raw ULID only ever lives in `?focus=`.
 */
import { computed, onErrorCaptured, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import type { components } from '../../api/schema'
import { useWorldStore } from '../../stores/world'
import { EDGE_VOCAB } from '../profile/profile'
import {
  NEIGHBOR_CAP,
  avatarInitial,
  buildGraphModel,
  type GraphRenderEdge,
  type GraphRenderNode,
} from './graphModel'
import VueFlowGraph from './VueFlowGraph.vue'

type WorldExport = components['schemas']['WorldExport']

type CandidateApi = {
  zoomIn(): void
  zoomOut(): void
  fitView(): void
  resetView(): void
  panBy(dx: number, dy: number): void
}

const route = useRoute()
const router = useRouter()
const campaignId = route.params.id as string

const world = useWorldStore()

const labelsVisible = ref(true)
const kindFilter = ref('')
const edgeTypeFilter = ref('')
const rendererKey = ref(0)
const rendererError = ref(false)
const candidateRef = ref<CandidateApi | null>(null)

const entry = computed(() => world.entry(campaignId))
const exportData = computed<WorldExport | null>(() => entry.value.world)

/** ?focus= is the only focus channel (back/forward + direct nav restore it). */
const requestedFocus = computed<string | null>(() => {
  const focus = route.query.focus
  return typeof focus === 'string' && focus.length > 0 ? focus : null
})

const model = computed(() =>
  exportData.value ? buildGraphModel(exportData.value, requestedFocus.value) : null,
)

// NO FOCUS: the model defaults to the first entity — surface it in the URL so
// refresh / back / direct nav all restore the same focus. Watched (not
// setup-time) because on a cold load the model lands asynchronously.
watch(
  () => [model.value, requestedFocus.value] as const,
  () => {
    if (model.value?.focusDefaulted && requestedFocus.value === null && model.value.focusId) {
      void router.replace({ query: { focus: model.value.focusId } })
    }
  },
  { immediate: true },
)

onMounted(() => {
  // The world snapshot comes from the store — never fetched here. If a
  // Phase-1 view already loaded it, use it as-is (remounts on ?focus=
  // changes are then free of network churn).
  if (!world.entry(campaignId).world) {
    void world.load(campaignId)
  }
  if (world.mediaByCampaign[campaignId] === undefined && world.mediaErrorByCampaign[campaignId] === undefined) {
    void world.fetchMedia(campaignId)
  }
})

function onRefocus(id: string) {
  void router.replace({ query: { ...route.query, focus: id } })
}

/** Kind + relationship filters over the model (the visible subset only). */
const kinds = computed(() => {
  const seen: string[] = []
  for (const node of model.value?.nodes ?? []) {
    if (!seen.includes(node.kind)) seen.push(node.kind)
  }
  return seen
})

const edgeTypes = computed(() => {
  const seen = new Set<string>()
  const types: string[] = []
  for (const type of EDGE_VOCAB) {
    if ((model.value?.edges ?? []).some((edge) => edge.type === type) && !seen.has(type)) {
      seen.add(type)
      types.push(type)
    }
  }
  for (const edge of model.value?.edges ?? []) {
    if (!seen.has(edge.type)) {
      seen.add(edge.type)
      types.push(edge.type)
    }
  }
  return types
})

const nodeNameById = computed<Record<string, string>>(() => {
  const names: Record<string, string> = {}
  for (const node of model.value?.nodes ?? []) {
    names[node.id] = node.name
  }
  return names
})

/** The store's portrait URL for an entity (manifest-backed, ONE convention). */
function portraitSrcFor(nodeId: string): string | null {
  return world.portraitSrc(campaignId, nodeId)
}

const renderNodes = computed<GraphRenderNode[]>(() => {
  const nodes = model.value?.nodes ?? []
  const kindFiltered = kindFilter.value
    ? nodes.filter((node) => node.kind === kindFilter.value)
    : nodes
  // A relationship filter shows the sub-web: only entities incident to a
  // matching edge stay visible (the focus too, when it has one).
  let kept = kindFiltered
  if (edgeTypeFilter.value) {
    const incident = new Set<string>()
    for (const edge of model.value?.edges ?? []) {
      if (edge.type === edgeTypeFilter.value) {
        incident.add(edge.src)
        incident.add(edge.dst)
      }
    }
    kept = kindFiltered.filter((node) => incident.has(node.id))
  }
  return kept.map((node) => ({
    ...node,
    portraitUrl: node.hasPortrait ? portraitSrcFor(node.id) : null,
    initial: avatarInitial(node.name),
  }))
})

const renderEdges = computed<GraphRenderEdge[]>(() => {
  const edges = model.value?.edges ?? []
  const byType = edgeTypeFilter.value ? edges.filter((edge) => edge.type === edgeTypeFilter.value) : edges
  const visibleIds = new Set(renderNodes.value.map((node) => node.id))
  return byType.filter((edge) => visibleIds.has(edge.src) && visibleIds.has(edge.dst))
})

const focusName = computed(() =>
  model.value?.focusId ? nodeNameById.value[model.value.focusId] ?? null : null,
)

/** No-relationships: the focus renders alone with a notice. */
const noRelationships = computed(
  () => (model.value?.nodes.length ?? 0) > 0 && (model.value?.edges.length ?? 0) === 0,
)

/** Relationship filter hid every edge while nodes remain. */
const noFilteredRelationships = computed(
  () =>
    !noRelationships.value &&
    renderNodes.value.length > 0 &&
    (model.value?.edges.length ?? 0) > 0 &&
    renderEdges.value.length === 0,
)

function clearFilters() {
  kindFilter.value = ''
  edgeTypeFilter.value = ''
}
const filtersActive = computed(() => kindFilter.value !== '' || edgeTypeFilter.value !== '')

function retryStore() {
  void world.load(campaignId)
}
function retryRenderer() {
  rendererKey.value += 1
  rendererError.value = false
}

onErrorCaptured(() => {
  rendererError.value = true
  return false
})

/** Keyboard pan/zoom equivalents — the canvas is a real focusable box. */
function onCanvasKeydown(event: { key: string; preventDefault(): void }) {
  const candidate = candidateRef.value
  if (!candidate) return
  const step = 36
  switch (event.key) {
    case 'ArrowUp':
      event.preventDefault()
      candidate.panBy(0, step)
      break
    case 'ArrowDown':
      event.preventDefault()
      candidate.panBy(0, -step)
      break
    case 'ArrowLeft':
      event.preventDefault()
      candidate.panBy(step, 0)
      break
    case 'ArrowRight':
      event.preventDefault()
      candidate.panBy(-step, 0)
      break
    case '+':
    case '=':
      candidate.zoomIn()
      break
    case '-':
    case '_':
      candidate.zoomOut()
      break
    case 'f':
    case 'F':
      candidate.fitView()
      break
    case 'r':
    case 'R':
      candidate.resetView()
      break
  }
}
</script>

<template>
  <section class="graph-view">
    <nav class="crumbs" aria-label="Breadcrumb">
      <RouterLink :to="{ name: 'campaigns' }">Campaigns</RouterLink>
      <span class="sep" aria-hidden="true">/</span>
      <RouterLink :to="{ name: 'world', params: { id: campaignId } }">
        {{ exportData?.campaign.title ?? 'World' }}
      </RouterLink>
      <span class="sep" aria-hidden="true">/</span>
      <span>Graph</span>
      <template v-if="focusName">
        <span class="sep" aria-hidden="true">/</span>
        <b>{{ focusName }}</b>
      </template>
    </nav>

    <div v-if="entry.notFound" class="card">
      <p class="error">World not found.</p>
      <p class="muted">This campaign does not exist or belongs to another DM.</p>
      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
    </div>
    <p v-else-if="entry.loading && !exportData" class="muted">Loading the world…</p>
    <div v-else-if="entry.error && !exportData" class="card">
      <p class="error">{{ entry.error }}</p>
      <button type="button" @click="retryStore">Retry</button>
      <RouterLink :to="{ name: 'campaigns' }" class="back">Back to your worlds</RouterLink>
    </div>
    <p v-else-if="exportData && !model" class="muted">Loading the world…</p>

    <template v-else-if="exportData && model">
      <div class="graph-shell">
        <div class="toolbar">
          <div class="filters">
            <select
              v-model="kindFilter"
              :aria-label="'Filter by entity type'"
              title="Filter by entity type"
            >
              <option value="">All types</option>
              <option v-for="kind in kinds" :key="kind" :value="kind">{{ kind }}</option>
            </select>
            <select
              v-model="edgeTypeFilter"
              aria-label="Filter by relationship type"
              title="Filter by relationship type"
            >
              <option value="">All relationships</option>
              <option v-for="type in edgeTypes" :key="type" :value="type">{{ type }}</option>
            </select>
<label class="lbl-toggle">
              <input v-model="labelsVisible" type="checkbox" aria-label="Show edge labels" />
              <span>Labels</span>
            </label>
          </div>
          <div class="zoom">
            <button type="button" class="btn" title="Zoom in" aria-label="Zoom in" @click="candidateRef?.zoomIn()">+</button>
            <button type="button" class="btn" title="Zoom out" aria-label="Zoom out" @click="candidateRef?.zoomOut()">−</button>
            <button type="button" class="btn" title="Fit view" aria-label="Fit view" @click="candidateRef?.fitView()">⤢</button>
            <button type="button" class="btn" title="Reset view" aria-label="Reset view" @click="candidateRef?.resetView()">⟲</button>
          </div>
        </div>

        <div
          class="canvas"
          tabindex="0"
          role="group"
          aria-label="Relationship graph canvas — arrow keys pan, + and − zoom, f fits, r resets"
          @keydown="onCanvasKeydown"
        >
          <div v-if="model.focusMissing" class="state-card">
            <p class="error">That entity is not in this world.</p>
            <p class="muted">The link may be stale — open its web from the world view instead.</p>
            <RouterLink :to="{ name: 'world', params: { id: campaignId } }" class="back">
              Back to the world
            </RouterLink>
          </div>

          <div v-else-if="model.nodes.length === 0" class="state-card">
            <p class="muted">This world is still empty — nothing has been built yet.</p>
            <RouterLink :to="{ name: 'build-in', params: { id: campaignId } }" class="cta">
              Open build-in
            </RouterLink>
          </div>

          <div v-else-if="rendererError" class="state-card">
            <p class="error">The graph renderer failed.</p>
            <p class="muted">Something went wrong while drawing the web.</p>
            <button type="button" class="cta" @click="retryRenderer">Retry</button>
          </div>

          <div v-else-if="renderNodes.length === 0" class="state-card">
            <p class="muted">No entities match the current filter.</p>
            <button v-if="filtersActive" type="button" class="cta" @click="clearFilters">
              Reset filters
            </button>
          </div>

          <template v-else>
            <VueFlowGraph
              :key="`renderer-${rendererKey}`"
              ref="candidateRef"
              :nodes="renderNodes"
              :edges="renderEdges"
              :focus-id="model.focusId"
              :labels-visible="labelsVisible"
              :node-name-by-id="nodeNameById"
              @refocus="onRefocus"
            />
            <div v-if="noRelationships" class="notice">
              No relationships — this entity stands alone.
            </div>
            <div v-else-if="noFilteredRelationships" class="notice">
              No relationships match the current filter.
              <button type="button" class="link" @click="clearFilters">Reset filters</button>
            </div>
          </template>
        </div>

        <div class="hud">
          <span>
            <b>{{ renderNodes.length }} entities</b> · <b>{{ renderEdges.length }} relationships</b>
          </span>
          <span
            v-if="model.truncation && !filtersActive"
            class="truncation"
          >
            showing {{ NEIGHBOR_CAP }} of {{ model.totalNeighbors }} connected entities, plus the
            focused entity
          </span>
          <span class="hint">drag to pan · scroll or pinch to zoom · click a node to refocus</span>
        </div>
      </div>
    </template>
    <p v-else class="muted">Loading the world…</p>
  </section>
</template>

<style scoped>
.graph-view {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 0;
}
.crumbs {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 12.5px;
  color: #6b7280;
}
.crumbs a {
  color: #6b7280;
}
.crumbs b {
  color: #1c2330;
  font-weight: 600;
}
.crumbs .sep {
  color: #c3c8d0;
}
.graph-shell {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  border: 1px solid #d8dbe0;
  border-radius: 10px;
  overflow: hidden;
  background: #ffffff;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 7px 14px;
  border-bottom: 1px solid #d8dbe0;
  background: #ffffff;
}
.filters {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.filters select,
.filters .btn,
.zoom .btn {
  font: 12.5px system-ui;
  color: #1c2330;
  background: #fff;
  border: 1px solid #d8dbe0;
  border-radius: 7px;
  padding: 4px 9px;
}
.lbl-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #d8dbe0;
  border-radius: 7px;
  padding: 4px 9px;
  font-size: 12.5px;
  color: #6b7280;
  cursor: pointer;
}
.lbl-toggle input {
  accent-color: #b45309;
  margin: 0;
}
.candidate-switch {
  color: #6b7280 !important;
}
.zoom {
  display: flex;
  gap: 4px;
}
.zoom .btn {
  width: 28px;
  height: 26px;
  display: grid;
  place-items: center;
  font-size: 14px;
  cursor: pointer;
  line-height: 1;
}
.zoom .btn:hover,
.filters .btn:hover {
  background: #e8eefc;
}
.canvas {
  position: relative;
  flex: 1;
  min-height: 0;
  background: #f4f5f7;
  outline: none;
}
.canvas:focus-visible {
  box-shadow: inset 0 0 0 2px #b45309;
}
.state-card {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  padding: 24px;
  background: #f4f5f7;
}
.notice {
  position: absolute;
  left: 50%;
  top: 14px;
  transform: translateX(-50%);
  background: #fff8e6;
  border: 1px solid #f0d9a8;
  border-radius: 8px;
  color: #7a5c12;
  font-size: 12.5px;
  padding: 8px 14px;
  z-index: 10;
  display: flex;
  gap: 8px;
  align-items: center;
  box-shadow: 0 2px 6px rgba(20, 30, 50, 0.08);
}
.hud {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 8px 14px;
  border-top: 1px solid #d8dbe0;
  background: #ffffff;
  font-size: 12.5px;
  color: #6b7280;
}
.hud b {
  color: #1c2330;
}
.hud .truncation {
  color: #7a5c12;
}
.hud .hint {
  margin-left: auto;
}
.error {
  color: #b91c1c;
}
.muted {
  color: #6b7280;
}
.card {
  border: 1px solid #d8dbe0;
  border-radius: 10px;
  padding: 16px;
  background: #ffffff;
}
</style>