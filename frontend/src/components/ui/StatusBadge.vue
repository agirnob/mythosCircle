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
  background: rgba(14, 23, 36, 0.72);
}
.mc-badge-canon {
  border-color: var(--mc-canonical);
  color: var(--mc-canonical);
  background: rgba(94, 208, 193, 0.08);
}
.mc-badge-proposal {
  border-color: var(--mc-proposal);
  color: var(--mc-proposal);
  background: rgba(192, 156, 255, 0.09);
}
.mc-badge-queued,
.mc-badge-generating {
  border-color: var(--mc-warning);
  color: var(--mc-warning);
  background: rgba(231, 184, 102, 0.08);
}
.mc-badge-failed {
  border-color: var(--mc-danger);
  color: var(--mc-danger);
  background: rgba(237, 119, 125, 0.08);
}
.mc-badge-neutral {
  border-color: var(--mc-border);
  color: var(--mc-text-muted);
}
</style>
