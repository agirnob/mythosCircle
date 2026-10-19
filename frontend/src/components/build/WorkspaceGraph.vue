<script setup lang="ts">
import { computed } from 'vue'
import type { WorkspaceNode } from '../../stores/workspace'
import GraphView from '../graph/GraphView.vue'
import type { GraphOverlay } from '../graph/graphModel'

const props = defineProps<{
  campaignId: string
  nodes: WorkspaceNode[]
  selectedId: string | null
}>()
defineEmits<{ select: [id: string | null] }>()

// Adapt draft identities only. Layout, cards, filters and navigation belong to GraphView.
const overlay = computed<GraphOverlay>(() => ({
  nodes: props.nodes.map((node) => ({
    id: node.id,
    kind: node.kind,
    name: node.name || `Unnamed ${node.kind}`,
    hasPortrait: false,
    planned: true,
  })),
  edges: props.nodes.flatMap((node) => [
    ...(node.parent_id
      ? [
          {
            id: `workspace-placement:${node.id}`,
            src: node.id,
            dst: node.parent_id,
            type: node.kind === 'place' ? 'part_of' : 'located_in',
            label: node.kind === 'place' ? 'part of' : 'located in',
            counter: 0,
            planned: true,
          },
        ]
      : []),
    ...(node.faction_id
      ? [
          {
            id: `workspace-membership:${node.id}`,
            src: node.id,
            dst: node.faction_id,
            type: 'member_of',
            label: 'member of',
            counter: 0,
            planned: true,
          },
        ]
      : []),
  ]),
}))
</script>
<template>
  <section class="workspace-graph" aria-label="World and planned additions graph">
    <h3>Connections around this world</h3>
    <GraphView
      embedded
      :campaign-id="campaignId"
      :overlay="overlay"
      :selected-id="selectedId"
      @select="$emit('select', $event)"
    />
  </section>
</template>
<style scoped>
.workspace-graph {
  margin-top: 1rem;
  min-width: 0;
}
</style>
