<script setup lang="ts">
/**
 * Knowledge chip (v3 Tier-2b; AD-29) — ONE per-secret party-knowledge
 * toggle, `secret` <-> `known`, flippable either direction at any time.
 * The record never changes — only the marker moves; this component is
 * presentational: the parent owns the API call.
 *
 * State is announced as TEXT, never color-only (panel §accessibility):
 * the label always reads the marker's state. The control is a switch
 * (role=switch) so a screen reader hears "on/off" semantics on top of
 * the state text.
 */
import { FIELD_LABELS } from '../profile/profile'

defineProps<{
  /** The closed-set field name (secret/rumor/party_hook) — the label. */
  field: string
  known: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  /** Fired on click; the parent computes the target state and calls the
   * knowledge-toggle route (the DM's gesture IS the target state). */
  toggle: []
}>()

/** The story-field display label — profile.ts owns the AR19 label map;
 * the knowledge-chip closed set reads from the same home. */
function knowledgeFieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field
}
</script>

<template>
  <button
    type="button"
    class="mc-knowledge-chip"
    :class="known ? 'mc-knowledge-chip-known' : 'mc-knowledge-chip-secret'"
    :disabled="disabled"
    role="switch"
    :aria-checked="known"
    @click="emit('toggle')"
  >
    <span class="mc-knowledge-chip-label">{{ knowledgeFieldLabel(field) }}</span>
    <span class="mc-knowledge-chip-state">{{ known ? 'known by party' : 'secret' }}</span>
  </button>
</template>

<style scoped>
.mc-knowledge-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.3rem 0.6rem;
  border-radius: 999px;
  border: 1px solid var(--mc-border);
  background: var(--mc-surface);
  color: var(--mc-text-primary);
  font-size: var(--mc-meta-size);
  letter-spacing: 0.04em;
  cursor: pointer;
}
.mc-knowledge-chip-label {
  font-weight: 600;
}
.mc-knowledge-chip-state {
  color: var(--mc-text-muted);
}
.mc-knowledge-chip-known {
  border-color: var(--mc-canonical);
}
.mc-knowledge-chip-known .mc-knowledge-chip-state {
  color: var(--mc-canonical);
}
.mc-knowledge-chip-secret .mc-knowledge-chip-state {
  color: var(--mc-text-muted);
}
.mc-knowledge-chip[disabled] {
  cursor: default;
  opacity: 0.6;
}
</style>
