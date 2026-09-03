<script setup lang="ts">
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

async function logout() {
  await auth.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <div class="shell">
    <header>
      <RouterLink :to="{ name: 'campaigns' }" class="brand">mythosCircle</RouterLink>
      <nav v-if="auth.isAuthenticated">
        <span class="account">{{ auth.account?.email }}</span>
        <button type="button" @click="logout">Log out</button>
      </nav>
    </header>
    <main>
      <!-- Keyed by fullPath: a param-only change (world/A -> world/B)
           remounts the view instead of reusing a stale instance. -->
      <RouterView :key="route.fullPath" />
    </main>
  </div>
</template>

<style>
:root {
  color-scheme: dark;
}
body {
  margin: 0;
  font-family: system-ui, sans-serif;
  background: #14161a;
  color: #e8e6e3;
}
.shell {
  max-width: 960px;
  margin: 0 auto;
  padding: 1rem;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.5rem;
}
.brand {
  font-weight: 700;
  color: inherit;
  text-decoration: none;
}
nav {
  display: flex;
  gap: 1rem;
  align-items: center;
}
button,
input,
textarea {
  font: inherit;
}
.card {
  background: #1d2026;
  border: 1px solid #2c3038;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1rem;
}
.error {
  color: #ff7b72;
}
.muted {
  color: #9aa0a6;
}
.cta {
  white-space: nowrap;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  background: #2f6feb;
  color: #fff;
  text-decoration: none;
}
.cta.secondary {
  background: transparent;
  border: 1px solid #2c3038;
  color: #9aa0a6;
}
.mono {
  font-family: ui-monospace, monospace;
  font-size: 0.85rem;
}
.small {
  font-size: 0.85rem;
}
.back {
  display: inline-block;
  margin-top: 0.5rem;
  color: #2f6feb;
  text-decoration: none;
}
.lore {
  border-left: 3px solid #2c3038;
  padding-left: 0.75rem;
  white-space: pre-wrap;
}
</style>
