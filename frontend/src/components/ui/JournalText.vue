<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { JournalReference } from '../../api/journal'

const props = defineProps<{
  text: string
  field: 'headline' | 'context'
  references: JournalReference[]
  campaignId: string
  liveEntityIds: string[]
}>()
const pieces = computed(() => {
  const chars = Array.from(props.text)
  const parts: { text: string; entityId?: string }[] = []
  let offset = 0
  for (const reference of props.references
    .filter((item) => item.field === props.field && item.start != null && item.end != null)
    .sort((a, b) => a.start! - b.start!)) {
    if (reference.start! < offset || reference.end! > chars.length) continue
    if (reference.start! > offset)
      parts.push({ text: chars.slice(offset, reference.start!).join('') })
    parts.push({
      text: reference.label,
      entityId: props.liveEntityIds.includes(reference.entity_id) ? reference.entity_id : undefined,
    })
    offset = reference.end!
  }
  parts.push({ text: chars.slice(offset).join('') })
  return parts
})
</script>

<template>
  <template v-for="(part, index) in pieces" :key="index">
    <RouterLink
      v-if="part.entityId"
      class="mc-journal-link"
      :to="{ name: 'entity', params: { id: campaignId, entityId: part.entityId } }"
      >{{ part.text }}</RouterLink
    >
    <template v-else>{{ part.text }}</template>
  </template>
</template>

<style scoped>
.mc-journal-link {
  color: var(--mc-canonical);
  text-decoration: none;
  border-bottom: 1px solid var(--mc-border);
}
</style>
