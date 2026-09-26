<script setup lang="ts">
/**
 * Friendly product error (§25) — maps the backend envelope into a
 * human-readable state with the reassurance that existing world state is
 * unchanged. Callers pass the already-mapped message.
 */
defineProps<{ title?: string; message: string }>()
</script>

<template>
  <div class="mc-error" role="alert">
    <p class="mc-error-title">{{ title ?? 'Something went wrong.' }}</p>
    <p class="mc-error-message">{{ message }}</p>
    <p class="mc-error-reassurance">Your existing world is unchanged.</p>
    <div v-if="$slots.actions" class="mc-error-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.mc-error {
  border: 1px solid var(--mc-danger);
  border-radius: var(--mc-radius);
  padding: 1rem 1.25rem;
  background: var(--mc-surface);
}
.mc-error-title {
  margin: 0;
  font-weight: 600;
}
.mc-error-message {
  margin: 0.5rem 0 0;
  color: var(--mc-text-secondary);
}
.mc-error-reassurance {
  margin: 0.5rem 0 0;
  font-size: 0.85rem;
  color: var(--mc-text-muted);
}
.mc-error-actions {
  margin-top: 0.75rem;
  display: flex;
  gap: var(--mc-gap-sm);
}
</style>
