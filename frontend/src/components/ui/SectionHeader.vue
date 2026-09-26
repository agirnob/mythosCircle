<script setup lang="ts">
/** Section header (§10) — medium section title + optional meta + actions. */
defineProps<{ title: string; meta?: string }>()
</script>

<template>
  <div class="mc-section-header">
    <div>
      <h2 class="mc-section-title">{{ title }}</h2>
      <p v-if="meta" class="mc-section-meta">{{ meta }}</p>
    </div>
    <div v-if="$slots.actions" class="mc-section-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.mc-section-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--mc-gap);
  margin: 1.5rem 0 0.75rem;
}
.mc-section-title {
  margin: 0;
  font-size: var(--mc-section-title-size);
  font-weight: 600;
}
.mc-section-meta {
  margin: 0.25rem 0 0;
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-section-actions {
  display: flex;
  gap: var(--mc-gap-sm);
}
</style>
