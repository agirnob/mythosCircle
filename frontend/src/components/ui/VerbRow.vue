<script setup lang="ts">
/**
 * Verb row (v3 Tier-2a; AD-26/28, EXPERIENCE Flow 6) — the four
 * consequence-verb affordances rendered from the entity's CURRENT session
 * image. The image is open (the AD-27 scar `{"hp": -12}` is a verb), so
 * this component is presentational: each affordance emits its delta and
 * the parent fires the session-verb route.
 *
 * Take-back IS the reverse verb (the DM's gesture is the target state):
 * an active affordance's next click fires the inverse delta —
 * `{defeated: false}` for the active Defeated chip — one revision, fully
 * deterministic from the session image, never a revision-hunting undo
 * (the feed cannot identify which revision set a state, and a
 * take-back-of-a-take-back would re-apply it). Double-fire safety stays
 * the backend's no-op.
 */
const props = defineProps<{
  /** The entity's current session image (absent keys = not applied). */
  session: Record<string, unknown>
  disabled?: boolean
}>()

defineEmits<{
  /** One verb delta; parent POSTs session-verb. */
  fire: [update: Record<string, unknown>]
}>()

/** A session-state flag reads as active when truthy — absent/false is
 * "not applied". */
function isActive(value: unknown): boolean {
  return value === true
}

/** The toggle-style verbs: click emits the INVERSE of the displayed
 * state, so the second click of an active affordance takes it back. */
function toggle(key: string) {
  return { [key]: !isActive(props.session[key]) }
}
</script>

<template>
  <div class="mc-verb-row">
    <button
      type="button"
      class="mc-verb"
      :class="{ 'mc-verb-active': isActive(session.defeated) }"
      :disabled="disabled"
      @click="$emit('fire', toggle('defeated'))"
    >
      {{ isActive(session.defeated) ? 'Defeated — take back' : 'Mark defeated' }}
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
.mc-verb[disabled] {
  cursor: default;
  opacity: 0.6;
}
</style>
