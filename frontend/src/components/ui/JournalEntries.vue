<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { JournalEntry } from '../../api/journal'
import JournalText from './JournalText.vue'
const props = defineProps<{
  entries: JournalEntry[]
  campaignId: string
  liveEntityIds: string[]
  busy?: boolean
  idPrefix?: string
}>()
defineEmits<{
  edit: [entry: JournalEntry]
  insert: [entry: JournalEntry, position: number]
  move: [entry: JournalEntry, position: number]
  remove: [entry: JournalEntry]
  correct: [entry: JournalEntry]
}>()
const rows = computed(() => [...props.entries].sort((a, b) => a.position - b.position))
function before(index: number) {
  return Math.max(
    1,
    Math.ceil(((rows.value[index - 1]?.position ?? 0) + rows.value[index]!.position) / 2),
  )
}
function movePosition(index: number, direction: -1 | 1) {
  if (direction === -1)
    return Math.max(
      1,
      Math.ceil(((rows.value[index - 2]?.position ?? 0) + rows.value[index - 1]!.position) / 2),
    )
  return Math.ceil(
    (rows.value[index + 1]!.position +
      (rows.value[index + 2]?.position ?? rows.value[index + 1]!.position + 2048)) /
      2,
  )
}
</script>

<template>
  <ol class="mc-journal">
    <li
      v-for="(entry, index) in rows"
      :id="`${idPrefix ?? 'story'}-${entry.id}`"
      :key="entry.id"
      class="mc-journal-entry"
      tabindex="-1"
    >
      <span class="mc-story-number">{{ index + 1 }}</span>
      <div class="mc-story-content">
        <div class="mc-entry-title">
          <h3>
            <JournalText
              :text="entry.headline"
              field="headline"
              :references="entry.references"
              :campaign-id="campaignId"
              :live-entity-ids="liveEntityIds"
            />
          </h3>
          <button
            type="button"
            class="mc-text-button"
            :disabled="busy"
            :aria-label="`Edit event: ${entry.headline}`"
            @click="$emit('edit', entry)"
          >
            {{ entry.context ? 'Edit' : 'Add context' }}
          </button>
        </div>
        <p v-if="entry.context" class="mc-story-context">
          <JournalText
            :text="entry.context"
            field="context"
            :references="entry.references"
            :campaign-id="campaignId"
            :live-entity-ids="liveEntityIds"
          />
        </p>
        <div class="mc-entry-meta">
          <span v-if="entry.action_revision_id">{{
            entry.corrected ? 'Action taken back' : 'Consequence'
          }}</span
          ><span v-else-if="entry.source_event_id">Recalled from Changes</span
          ><template
            v-for="(reference, referenceIndex) in entry.references.filter(
              (item) => item.field == null,
            )"
            :key="referenceIndex"
            ><RouterLink
              v-if="liveEntityIds.includes(reference.entity_id)"
              :to="{ name: 'entity', params: { id: campaignId, entityId: reference.entity_id } }"
              class="mc-link"
              >{{ reference.label }}</RouterLink
            ><span v-else>{{ reference.label }}</span></template
          >
        </div>
        <details class="mc-story-tools">
          <summary :aria-label="`Story controls for ${entry.headline}`">Story controls</summary>
          <div>
            <button
              type="button"
              class="mc-text-button"
              :disabled="busy"
              @click="$emit('insert', entry, before(index))"
            >
              Insert event before</button
            ><button
              type="button"
              class="mc-text-button"
              :disabled="busy || index === 0"
              @click="$emit('move', entry, movePosition(index, -1))"
            >
              Move earlier</button
            ><button
              type="button"
              class="mc-text-button"
              :disabled="busy || index === rows.length - 1"
              @click="$emit('move', entry, movePosition(index, 1))"
            >
              Move later</button
            ><button
              v-if="entry.action_revision_id && !entry.corrected"
              type="button"
              class="mc-text-button"
              :disabled="busy"
              @click="$emit('correct', entry)"
            >
              Take back action</button
            ><button
              type="button"
              class="mc-text-button"
              :disabled="busy"
              @click="$emit('remove', entry)"
            >
              Remove from story
            </button>
          </div>
          <p>Removing changes the story only. Take back action reverses its linked consequence.</p>
        </details>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.mc-journal {
  list-style: none;
  margin: 0;
  padding: 0;
}
.mc-journal-entry {
  display: grid;
  grid-template-columns: 1.5rem minmax(0, 1fr);
  gap: 0.8rem;
  padding: 1.3rem 0;
  position: relative;
}
.mc-journal-entry:not(:last-child)::after {
  content: '';
  position: absolute;
  left: 0.7rem;
  top: 3.1rem;
  bottom: 0;
  width: 1px;
  background: var(--mc-border);
}
.mc-story-number {
  border: 1px solid var(--mc-border);
  border-radius: 50%;
  width: 1.4rem;
  height: 1.4rem;
  display: grid;
  place-items: center;
  font-size: 0.7rem;
  color: var(--mc-text-muted);
}
.mc-entry-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.6rem;
}
h3 {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.mc-text-button {
  border: 0;
  background: none;
  color: var(--mc-canonical);
  padding: 0.15rem 0;
  font: inherit;
  font-size: 0.75rem;
  cursor: pointer;
}
.mc-entry-title button {
  flex-shrink: 0;
}
.mc-story-context {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.7;
  color: var(--mc-text-secondary);
  margin: 0.6rem 0;
  max-width: 44rem;
}
.mc-entry-meta {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
  color: var(--mc-canonical);
  font-size: 0.75rem;
}
.mc-story-tools {
  margin-top: 0.6rem;
  color: var(--mc-text-muted);
  font-size: 0.7rem;
}
.mc-story-tools summary {
  cursor: pointer;
}
.mc-story-tools > div {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-top: 0.5rem;
}
.mc-text-button:disabled {
  opacity: 0.45;
  cursor: default;
}
</style>
