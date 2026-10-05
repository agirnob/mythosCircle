<script setup lang="ts">
/**
 * Tonight feed (v3 Tier-1; AD-35) — renders the revisions feed's
 * display-ready event lines VERBATIM. Verb commits and their take-backs
 * both arrive as `action: "edited"` (AD-27: the feed never distinguishes
 * undo from edit) — this component renders what the API sends, it never
 * re-derives the verdict. Groups lines under their revision (one save =
 * one revision, possibly several events).
 */
import type { components } from '../../api/schema'

defineProps<{
  revisions: components['schemas']['RevisionSummary'][]
  allowPromotion?: boolean
}>()
defineEmits<{ promote: [eventId: string, names: string[], details: string[]] }>()

/** A short display stamp — `YYYY-MM-DD HH:MM` in the DM's local time. */
function shortTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
</script>

<template>
  <ol v-if="revisions.length > 0" class="mc-feed">
    <li v-for="revision in revisions" :key="revision.revision_id" class="mc-feed-revision">
      <ul class="mc-feed-events">
        <li
          v-for="(event, eventIndex) in revision.events"
          :key="revision.revision_id + ':' + eventIndex"
          class="mc-feed-line"
        >
          <span class="mc-feed-action">{{ event.action }}</span>
          <span class="mc-feed-target">{{ event.target_names.join(', ') }}</span>
          <span class="mc-feed-kind">{{ event.kind }}</span>
          <time class="mc-feed-time" :datetime="event.created_at">
            {{ shortTime(event.created_at) }}
          </time>
          <ul v-if="event.details?.length" class="mc-feed-details">
            <li v-for="(detail, detailIndex) in event.details" :key="detailIndex">
              {{ detail }}
            </li>
          </ul>
          <button
            v-if="event.source_event_id && !event.entry_id && ['session', 'knowledge'].includes(event.kind)"
            type="button"
            class="mc-feed-promote"
            :disabled="!allowPromotion"
            @click="
              $emit('promote', event.source_event_id, event.target_names, event.details ?? [])
            "
          >
            Copy to journal
          </button>
        </li>
      </ul>
    </li>
  </ol>
  <p v-else class="mc-muted">No changes yet.</p>
</template>

<style scoped>
.mc-feed-promote {
  border: 0;
  padding: 0.25rem 0;
  background: none;
  color: var(--mc-canonical);
  font: inherit;
  font-size: 0.75rem;
  cursor: pointer;
}
.mc-feed-promote:disabled {
  opacity: 0.45;
}
.mc-feed {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--mc-gap-sm);
  border-left: 1px solid var(--mc-border);
  padding-left: 0.85rem;
}
.mc-feed-revision {
  margin: 0;
}
.mc-feed-events {
  list-style: none;
  margin: 0;
  padding: 0;
}
.mc-feed-line {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  align-items: baseline;
  font-size: 0.85rem;
}
.mc-feed-line::before {
  content: '';
  position: absolute;
  left: -1.15rem;
  width: 0.45rem;
  height: 0.45rem;
  border: 2px solid var(--mc-canonical);
  border-radius: 50%;
  background: var(--mc-app-bg);
}
.mc-feed-action {
  font-weight: 600;
  color: var(--mc-text-secondary);
}
.mc-feed-target {
  color: var(--mc-text-primary);
}
.mc-feed-kind {
  color: var(--mc-text-muted);
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.mc-feed-time {
  color: var(--mc-text-muted);
  font-size: var(--mc-meta-size);
  white-space: nowrap;
}
.mc-feed-details {
  flex-basis: 100%;
  min-width: 0;
  margin: 0 0 0.45rem;
  padding-left: 1rem;
  color: var(--mc-text-secondary);
  overflow-wrap: anywhere;
}
</style>
