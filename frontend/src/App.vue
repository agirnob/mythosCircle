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
    <!-- Keep query navigation in the view; remount for path or account changes. -->
    <RouterView :key="`${auth.account?.id ?? 'anonymous'}:${route.path}`" />
  </AppShell>
  <RouterView
    v-else-if="!route.meta.requiresAuth"
    :key="`${auth.account?.id ?? 'anonymous'}:${route.path}`"
  />
</template>

<style>
.mc-btn {
  display: inline-block;
  white-space: nowrap;
  padding: 0.55rem 0.9rem;
  border-radius: var(--mc-radius-sm);
  background: linear-gradient(135deg, var(--mc-interactive-bright), var(--mc-interactive));
  color: #fff;
  font-weight: 600;
  text-decoration: none;
  border: 1px solid transparent;
  cursor: pointer;
  box-shadow: 0 8px 22px var(--mc-glow-violet);
}
.mc-btn:hover {
  filter: brightness(1.1);
  transform: translateY(-1px);
}
.mc-btn-secondary {
  background: rgba(19, 30, 46, 0.72);
  border-color: var(--mc-border);
  color: var(--mc-text-secondary);
  font-weight: 500;
  box-shadow: none;
}
.mc-btn-secondary:hover {
  color: var(--mc-text-primary);
  border-color: var(--mc-border-bright);
}
.mc-input,
.mc-textarea {
  width: 100%;
  background: var(--mc-input);
  border: 1px solid var(--mc-border-bright);
  border-radius: var(--mc-radius-sm);
  color: var(--mc-text-primary);
  padding: 0.6rem 0.75rem;
}
.mc-input:focus,
.mc-textarea:focus {
  border-color: var(--mc-interactive);
  box-shadow: 0 0 0 3px var(--mc-glow-violet);
  outline: none;
}
.mc-link {
  color: var(--mc-interactive-bright);
  text-decoration: none;
}
.mc-link:hover {
  color: #fff;
}
.mc-muted {
  color: var(--mc-text-muted);
}
</style>
