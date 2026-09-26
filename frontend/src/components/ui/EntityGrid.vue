<script setup lang="ts">
/** Responsive entity grid (§13) — stacks on narrow screens (§26). */
</script>

<template>
  <div class="mc-entity-grid">
    <slot />
  </div>
</template>

<style scoped>
.mc-entity-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: var(--mc-gap);
}
</style>
