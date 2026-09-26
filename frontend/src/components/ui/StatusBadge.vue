<script setup lang="ts">
/**
 * Status badge (§12 + §24) — the canonical/proposed distinction plus the
 * FIFO queue states. Never color-only: every variant renders a text label.
 */
defineProps<{ variant: 'canon' | 'proposal' | 'queued' | 'generating' | 'failed' | 'neutral' }>()
</script>

<template>
  <span class="mc-badge" :class="`mc-badge-${variant}`">
    <slot />
  </span>
</template>

<style scoped>
.mc-badge {
  display: inline-block;
  font-size: var(--mc-meta-size);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 0.2rem 0.5rem;
  border-radius: 999px;
  border: 1px solid var(--mc-border);
  color: var(--mc-text-secondary);
}
.mc-badge-canon {
  border-color: var(--mc-canonical);
  color: var(--mc-canonical);
}
.mc-badge-proposal {
  border-color: var(--mc-proposal);
  color: var(--mc-proposal);
}
.mc-badge-queued,
.mc-badge-generating {
  border-color: var(--mc-warning);
  color: var(--mc-warning);
}
.mc-badge-failed {
  border-color: var(--mc-danger);
  color: var(--mc-danger);
}
.mc-badge-neutral {
  border-color: var(--mc-border);
  color: var(--mc-text-muted);
}
</style>
