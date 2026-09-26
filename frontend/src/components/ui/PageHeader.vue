<script setup lang="ts">
/** Page header (§10) — large page title + optional description + actions. */
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <header class="mc-page-header">
    <div>
      <h1 class="mc-page-title">{{ title }}</h1>
      <p v-if="description" class="mc-page-description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="mc-page-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.mc-page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--mc-gap);
  margin-bottom: 1.5rem;
}
.mc-page-title {
  margin: 0;
  font-size: var(--mc-page-title-size);
  font-weight: 700;
}
.mc-page-description {
  margin: 0.5rem 0 0;
  color: var(--mc-text-secondary);
  font-size: var(--mc-body-size);
}
.mc-page-actions {
  display: flex;
  gap: var(--mc-gap-sm);
  flex: none;
}
</style>
