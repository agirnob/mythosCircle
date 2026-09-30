<script setup lang="ts">
/** Empty state (§31 polish) — consistent nothing-here-yet surface. */
defineProps<{ title: string; body?: string }>()
</script>

<template>
  <div class="mc-empty">
    <p class="mc-empty-title">{{ title }}</p>
    <p v-if="body" class="mc-empty-body">{{ body }}</p>
    <div v-if="$slots.actions" class="mc-empty-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.mc-empty {
  border: 1px dashed var(--mc-border-bright);
  border-radius: var(--mc-radius);
  padding: 2.5rem 1.5rem;
  text-align: center;
  background:
    radial-gradient(circle at 50% 0%, rgba(139, 108, 255, 0.12), transparent 55%),
    var(--mc-surface);
}
.mc-empty-title {
  margin: 0;
  font-weight: 600;
  font-family: var(--mc-display-font);
  font-size: 1.25rem;
}
.mc-empty-body {
  margin: 0.5rem 0 0;
  color: var(--mc-text-secondary);
}
.mc-empty-actions {
  margin-top: 1rem;
  display: flex;
  gap: var(--mc-gap-sm);
  justify-content: center;
}
</style>
