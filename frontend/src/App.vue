<script setup lang="ts">
import { RouterLink, RouterView, useRouter } from 'vue-router'

import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const router = useRouter()

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
      <RouterView />
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
</style>
