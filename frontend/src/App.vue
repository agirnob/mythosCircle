<script setup lang="ts">
import { useRoute } from 'vue-router'

import AppShell from './components/shell/AppShell.vue'
import { useAuthStore } from './stores/auth'

const route = useRoute()
const auth = useAuthStore()
</script>

<template>
  <!-- Stage-1 shell (§7): sidebar workspace around the existing routes. -->
  <AppShell v-if="auth.isAuthenticated">
    <!-- Keyed by fullPath: a param-only change remounts the view. -->
    <RouterView :key="route.fullPath" />
  </AppShell>
  <RouterView v-else :key="route.fullPath" />
</template>

<style>
.mc-btn {
  display: inline-block;
  white-space: nowrap;
  padding: 0.55rem 0.9rem;
  border-radius: var(--mc-radius-sm);
  background: var(--mc-interactive);
  color: #151619;
  font-weight: 600;
  text-decoration: none;
  border: 1px solid transparent;
  cursor: pointer;
}
.mc-btn:hover {
  filter: brightness(1.08);
}
.mc-btn-secondary {
  background: transparent;
  border-color: var(--mc-border);
  color: var(--mc-text-secondary);
  font-weight: 500;
}
.mc-btn-secondary:hover {
  color: var(--mc-text-primary);
}
.mc-input,
.mc-textarea {
  width: 100%;
  background: var(--mc-input);
  border: 1px solid var(--mc-border);
  border-radius: var(--mc-radius-sm);
  color: var(--mc-text-primary);
  padding: 0.6rem 0.75rem;
}
.mc-link {
  color: var(--mc-interactive);
  text-decoration: none;
}
.mc-muted {
  color: var(--mc-text-muted);
}
</style>
