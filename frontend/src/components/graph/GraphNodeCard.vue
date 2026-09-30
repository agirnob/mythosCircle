<script setup lang="ts">
/**
 * Story 7.1 — Vue Flow node card (mockup-7-1 contract, whole-world edition):
 * white card, avatar (portrait when the store has one, else the name
 * initial), name, kind chip (color is reinforcement — the chip text carries
 * the meaning), node-attached focus = thicker border + soft halo; under an
 * active focus, non-1-hop cards render dimmed (greyed but visible).
 *
 * Connector handles are hidden but per-edge: each incident edge gets its own
 * handle id with a distinct vertical offset, so parallel/reciprocal edges
 * anchor at separate border points and curve apart.
 */
import { computed, ref } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import type { NodeProps } from '@vue-flow/core'

import type { GraphRenderNode } from './graphModel'
import { GRAPH_NODE_WIDTH, GRAPH_NODE_HEIGHT, kindStyle } from './graphModel'

interface CardData {
  node: GraphRenderNode
  focused: boolean
  dimmed: boolean
  outHandles: Array<{ edgeId: string; pct: number; position: Position }>
  inHandles: Array<{ edgeId: string; pct: number; position: Position }>
}

const props = defineProps<NodeProps & { data: CardData }>()

const card = computed(() => props.data as CardData)
const style = computed(() => kindStyle(card.value.node.kind))
const root = ref<{ dispatchEvent(event: { bubbles: boolean }): boolean } | null>(null)

/** Enter/Space on the focused card must refocus exactly like a click. The
 * click path runs through Vue Flow's node-click pipeline (drag guard
 * included), so a bubbling synthetic click is the honest activation. */
function activate() {
  root.value?.dispatchEvent(new globalThis.MouseEvent('click', { bubbles: true }))
}
</script>

<template>
  <div
    ref="root"
    class="graph-node"
    :class="[
      `kind-${card.node.kind}`,
      { focus: card.focused, dim: card.dimmed, selected: props.selected, dragging: props.dragging },
    ]"
    :style="{
      width: `${GRAPH_NODE_WIDTH}px`,
      height: `${GRAPH_NODE_HEIGHT}px`,
      borderColor: card.focused ? 'var(--mc-warning)' : style.color,
      boxShadow: card.focused ? '0 0 14px rgba(231, 184, 102, 0.35)' : undefined,
    }"
    role="button"
    tabindex="0"
    :aria-label="`${card.node.name}, ${card.node.kind}${card.focused ? ', focused' : ''}; click to refocus its web`"
    :title="card.node.name"
    @keydown.enter.prevent="activate"
    @keydown.space.prevent="activate"
  >
    <span v-if="card.focused" class="halo" aria-hidden="true"></span>
    <img
      v-if="card.node.portraitUrl"
      class="avatar"
      :src="card.node.portraitUrl"
      :alt="`${card.node.name} portrait`"
    />
    <span v-else class="avatar initial" :style="{ backgroundColor: style.background, color: style.color }" aria-hidden="true">
      {{ card.node.initial }}
    </span>
    <span class="name" aria-hidden="true">{{ card.node.name }}</span>
    <span class="chip" :style="{ backgroundColor: style.background, color: style.color }" aria-hidden="true">
      {{ card.node.kind }}
    </span>
    <Handle
      v-for="h in card.outHandles"
      :id="`out-${h.edgeId}`"
      :key="`out-${h.edgeId}`"
      type="source"
      :position="h.position"
      :style="{ ...(h.position === Position.Left || h.position === Position.Right ? { top: `${h.pct}%` } : { left: `${h.pct}%` }) }"
      class="graph-handle"
    />
    <Handle
      v-for="h in card.inHandles"
      :id="`in-${h.edgeId}`"
      :key="`in-${h.edgeId}`"
      type="target"
      :position="h.position"
      :style="{ ...(h.position === Position.Left || h.position === Position.Right ? { top: `${h.pct}%` } : { left: `${h.pct}%` }) }"
      class="graph-handle"
    />
  </div>
</template>

<style scoped>
.graph-node {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--mc-surface-raised);
  border: 1.3px solid var(--mc-border-bright);
  border-radius: 10px;
  font: 600 13px/1.3 system-ui, -apple-system, 'Segoe UI', sans-serif;
  color: var(--mc-text-primary);
  cursor: pointer;
  box-sizing: border-box;
  padding: 10px 8px 8px;
  gap: 2px;
  user-select: none;
}
/* Whole-world dimming: non-1-hop cards under an active focus (grey, visible). */
.graph-node.dim {
  opacity: 0.25;
}
.graph-node.focus {
  border-width: 2.6px;
}
.graph-node .halo {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 132px;
  height: 132px;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: rgba(231, 184, 102, 0.16);
  pointer-events: none;
  z-index: -1;
}
.graph-node .avatar {
  position: absolute;
  left: 8px;
  top: 50%;
  transform: translateY(-50%);
  width: 24px;
  height: 24px;
  border-radius: 50%;
  object-fit: cover;
  border: 1px solid var(--mc-border-bright);
  background: var(--mc-surface);
}
.graph-node .avatar.initial {
  display: grid;
  place-items: center;
  font: 700 11px/1 system-ui, sans-serif;
}
.graph-node .name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 2px;
}
.graph-node .chip {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 9px;
  font: 600 10px/1.4 system-ui, sans-serif;
  text-transform: capitalize;
}
.graph-handle {
  visibility: hidden;
}
</style>
