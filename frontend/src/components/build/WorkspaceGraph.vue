<script setup lang="ts">
import { computed } from 'vue'
import type { WorkspaceNode } from '../../stores/workspace'
const props = defineProps<{
  nodes: WorkspaceNode[]
  existing: { id: string; kind: string; name: string }[]
  edges: { src: string; dst: string; type: string }[]
  selectedId: string | null
}>()
defineEmits<{ select: [id: string] }>()
const rows = computed(() => [
  ...props.existing.map((n) => ({ ...n, planned: false })),
  ...props.nodes.map((n) => ({ ...n, planned: true })),
])
const links = computed(() => [
  ...props.edges,
  ...props.nodes.flatMap((n) => [
    ...(n.parent_id
      ? [{ src: n.id, dst: n.parent_id, type: n.kind === 'place' ? 'part_of' : 'located_in' }]
      : []),
    ...(n.faction_id ? [{ src: n.id, dst: n.faction_id, type: 'member_of' }] : []),
  ]),
])
const positions = computed(
  () =>
    new Map(
      rows.value.map((n, index) => [
        n.id,
        { x: 30 + (index % 3) * 290, y: 25 + Math.floor(index / 3) * 110 },
      ]),
    ),
)
const visibleLinks = computed(() =>
  links.value.filter((e) => positions.value.has(e.src) && positions.value.has(e.dst)),
)
const name = (id: string | null | undefined) =>
  rows.value.find((n) => n.id === id)?.name || 'Unnamed entry'
</script>
<template>
  <section class="graph-surface" aria-label="World and planned additions graph">
    <h3>Connections around this world</h3>
    <div class="graph-key"><span>● Existing content</span><span>◇ Planned addition</span></div>
    <div class="workspace-graph-canvas">
      <svg
        role="group"
        aria-label="Interactive world relationships"
        :viewBox="`0 0 900 ${Math.max(140, Math.ceil(rows.length / 3) * 110 + 30)}`"
        width="900"
        :height="Math.max(140, Math.ceil(rows.length / 3) * 110 + 30)"
      >
        <defs>
          <marker
            id="workspace-arrow"
            viewBox="0 0 10 10"
            refX="10"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--mc-text-muted)" />
          </marker>
        </defs>
        <line
          v-for="(edge, index) in visibleLinks"
          :key="`${edge.src}-${edge.dst}-${index}`"
          :x1="positions.get(edge.src)!.x + 110"
          :y1="positions.get(edge.src)!.y + 65"
          :x2="positions.get(edge.dst)!.x + 110"
          :y2="positions.get(edge.dst)!.y + 65"
          stroke="var(--mc-text-muted)"
          stroke-width="1.5"
          marker-end="url(#workspace-arrow)"
        >
          <title>{{ name(edge.src) }} {{ edge.type }} {{ name(edge.dst) }}</title>
        </line>
        <g
          v-for="node in rows"
          :key="node.id"
          role="button"
          tabindex="0"
          :aria-label="`${node.planned ? 'Planned' : 'Existing'} ${node.kind}: ${node.name || 'Model chooses name'}`"
          :aria-pressed="node.id === selectedId"
          :transform="`translate(${positions.get(node.id)!.x}, ${positions.get(node.id)!.y})`"
          @click="$emit('select', node.id)"
          @keydown.enter.prevent="$emit('select', node.id)"
          @keydown.space.prevent="$emit('select', node.id)"
        >
          <rect
            width="240"
            height="76"
            rx="9"
            fill="var(--mc-surface)"
            :stroke="
              node.id === selectedId
                ? 'var(--mc-interactive-bright)'
                : node.planned
                  ? 'var(--mc-proposal)'
                  : 'var(--mc-canonical)'
            "
            :stroke-width="node.id === selectedId ? 3 : 1.5"
            :stroke-dasharray="node.planned ? '5 4' : undefined"
          />
          <text x="12" y="24" fill="var(--mc-text-secondary)" font-size="12">
            {{ node.planned ? '◇ Planned' : '● Existing' }} {{ node.kind }}
          </text>
          <text x="12" y="50" fill="var(--mc-text-primary)" font-size="15">
            {{ (node.name || 'Model chooses name').slice(0, 26) }}
          </text>
          <title>{{ node.name }}</title>
        </g>
      </svg>
    </div>
    <ul aria-label="Readable world relationships">
      <li v-for="(edge, index) in links" :key="`${edge.src}-${edge.dst}-${index}`">
        {{ name(edge.src) }} {{ edge.type.replaceAll('_', ' ') }} {{ name(edge.dst) }}
      </li>
    </ul>
  </section>
</template>
