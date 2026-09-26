<script setup lang="ts">
/**
 * Dial picker (AD-36) — one active elaboration level for the entity,
 * chosen from the kinds registry's closed dial set. The dial is a TOP-
 * LEVEL record key: it survives export, import, and re-rolls. A record
 * predating the field reads as absent — the control renders a muted
 * "unspecified" option, NEVER an error.
 */
defineProps<{
  /** The registry's dial levels (KindsResponse.dial_levels), order kept. */
  levels: string[]
  current: string | null | undefined
  disabled?: boolean
}>()

defineEmits<{
  /** The newly chosen level; the parent PATCHes it onto the record. */
  change: [value: string]
}>()
</script>

<template>
  <label class="mc-dial">
    <span class="mc-dial-label">Dial</span>
    <select
      class="mc-dial-select"
      :disabled="disabled"
      :value="current ?? ''"
      @change="(event) => $emit('change', (event.target as HTMLSelectElement).value)"
    >
      <option value="" disabled :selected="!current">unspecified</option>
      <option v-for="level in levels" :key="level" :value="level" :selected="current === level">
        {{ level }}
      </option>
    </select>
  </label>
</template>

<style scoped>
.mc-dial {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}
.mc-dial-label {
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-dial-select {
  background: var(--mc-input);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  color: var(--mc-text-primary);
  padding: 0.3rem 0.5rem;
}
</style>
