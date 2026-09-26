<script setup lang="ts">
/**
 * Verb row (v3 Tier-2a; AD-26/28, EXPERIENCE Flow 6) — the four
 * consequence-verb affordances rendered from the entity's CURRENT session
 * image. The image is open (the AD-27 scar `{"hp": -12}` is a verb), so
 * this component is presentational: each affordance emits its delta and
 * the parent fires the session-verb route; double-fire safety is the
 * backend's no-op (a repeated gesture commits nothing).
 *
 * Take-it-back (AD-27) is the per-transaction rewind: the parent resolves
 * the verb's own revision from the feed and calls undo — this component
 * only announces that a take-back is possible.
 */
defineProps<{
  /** The entity's current session image (absent keys = not applied). */
  session: Record<string, unknown>
  /** Whether the newest session revision for this entity can be undone
   * (its revision id resolves from the feed in the parent). */
  canTakeBack: boolean
  disabled?: boolean
}>()

defineEmits<{
  /** One verb delta; parent POSTs session-verb. */
  fire: [update: Record<string, unknown>]
  /** Take-it-back: parent undoes the verb's own revision (AD-27). */
  'take-back': []
}>()

/** A session-state flag reads as active when truthy — absent/false is
 * "not applied". Boolean affordances only; scalar deltas (hp, strings)
 * stay the parent's concern. */
function isActive(value: unknown): boolean {
  return value === true
}
</script>

<template>
  <div class="mc-verb-row">
    <button
      type="button"
      class="mc-verb"
      :class="{ 'mc-verb-active': isActive(session.defeated) }"
      :disabled="disabled"
      @click="$emit('fire', { defeated: true })"
    >
      {{ isActive(session.defeated) ? 'Defeated' : 'Mark defeated' }}
    </button>
    <button
      type="button"
      class="mc-verb"
      :class="{ 'mc-verb-active': isActive(session.allegiance) }"
      :disabled="disabled"
      @click="$emit('fire', { allegiance: !isActive(session.allegiance) })"
    >
      {{ isActive(session.allegiance) ? 'Allegiance flipped' : 'Flip allegiance' }}
    </button>
    <button
      type="button"
      class="mc-verb"
      :class="{ 'mc-verb-active': isActive(session.thread) }"
      :disabled="disabled"
      @click="$emit('fire', { thread: !isActive(session.thread) })"
    >
      {{ isActive(session.thread) ? 'Thread resolved' : 'Resolve thread' }}
    </button>
    <button
      type="button"
      class="mc-verb"
      :class="{ 'mc-verb-active': isActive(session.item) }"
      :disabled="disabled"
      @click="$emit('fire', { item: !isActive(session.item) })"
    >
      {{ isActive(session.item) ? 'Item spent' : 'Spend item' }}
    </button>
    <button
      v-if="canTakeBack"
      type="button"
      class="mc-verb mc-verb-take-back"
      :disabled="disabled"
      @click="$emit('take-back')"
    >
      Take it back
    </button>
  </div>
</template>

<style scoped>
.mc-verb-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--mc-gap-sm);
}
.mc-verb {
  padding: 0.35rem 0.7rem;
  border-radius: 999px;
  border: 1px solid var(--mc-border);
  background: var(--mc-surface);
  color: var(--mc-text-secondary);
  font-size: var(--mc-meta-size);
  letter-spacing: 0.04em;
  cursor: pointer;
}
.mc-verb-active {
  border-color: var(--mc-canonical);
  color: var(--mc-canonical);
}
.mc-verb-take-back {
  border-color: var(--mc-warning);
  color: var(--mc-warning);
}
.mc-verb[disabled] {
  cursor: default;
  opacity: 0.6;
}
</style>
