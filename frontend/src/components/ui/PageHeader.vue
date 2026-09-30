<script setup lang="ts">
/** Page header (§10) — large page title + optional description + actions. */
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <header class="mc-page-header">
    <div>
      <h1 class="mc-page-title mc-display-title">{{ title }}</h1>
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
  margin-bottom: 2rem;
  padding-bottom: 1.25rem;
  border-bottom: 1px solid var(--mc-border);
}
.mc-page-title {
  margin: 0;
  font-size: var(--mc-page-title-size);
  font-weight: 700;
  line-height: 1.05;
}
.mc-page-description {
  margin: 0.5rem 0 0;
  color: var(--mc-text-secondary);
  font-size: var(--mc-body-size);
}
.mc-page-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--mc-gap-sm);
  flex: none;
}
@media (max-width: 620px) {
  .mc-page-header {
    flex-direction: column;
  }
  .mc-page-actions {
    width: 100%;
  }
}
</style>
