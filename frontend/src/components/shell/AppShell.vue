<script setup lang="ts">
/**
 * Application shell (§7) — campaign/workspace navigation.
 *
 * Reuses existing routes only; no new backend behavior. The WORLD / CREATE /
 * REVIEW / TOOLS grouping is conceptual (§6): World = what exists, Create =
 * what the DM wants to introduce, Review = staged proposals, Tools = graph.
 * Export stays inside the world view until the dedicated export stage.
 */
import { computed } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const campaignId = computed(() => route.params.id as string | undefined)

async function logout() {
  await auth.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <div class="mc-shell">
    <aside class="mc-sidebar" aria-label="Campaign workspace">
      <RouterLink :to="{ name: 'campaigns' }" class="mc-brand">mythosCircle</RouterLink>
      <template v-if="campaignId">
        <nav class="mc-nav-group" aria-label="Campaign">
          <p class="mc-nav-title">Campaign</p>
          <RouterLink :to="{ name: 'overview', params: { id: campaignId } }" class="mc-nav-link">
            Overview
          </RouterLink>
          <RouterLink :to="{ name: 'tonight', params: { id: campaignId } }" class="mc-nav-link">
            Tonight
          </RouterLink>
          <RouterLink :to="{ name: 'world', params: { id: campaignId } }" class="mc-nav-link">
            World
          </RouterLink>
        </nav>
        <nav class="mc-nav-group" aria-label="Create">
          <p class="mc-nav-title">Create</p>
          <RouterLink :to="{ name: 'ask', params: { id: campaignId } }" class="mc-nav-link">
            Ask the World
          </RouterLink>
          <RouterLink
            :to="{ name: 'add-character', params: { id: campaignId } }"
            class="mc-nav-link"
          >
            Add character
          </RouterLink>
          <RouterLink :to="{ name: 'build-in', params: { id: campaignId } }" class="mc-nav-link">
            Guided Build
          </RouterLink>
        </nav>
        <nav class="mc-nav-group" aria-label="Review">
          <p class="mc-nav-title">Review</p>
          <RouterLink :to="{ name: 'candidates', params: { id: campaignId } }" class="mc-nav-link">
            Proposals
          </RouterLink>
        </nav>
        <nav class="mc-nav-group" aria-label="Tools">
          <p class="mc-nav-title">Tools</p>
          <RouterLink :to="{ name: 'graph', params: { id: campaignId } }" class="mc-nav-link">
            Graph
          </RouterLink>
        </nav>
      </template>
      <template v-else>
        <nav class="mc-nav-group" aria-label="Create">
          <p class="mc-nav-title">Create</p>
          <RouterLink :to="{ name: 'forge-global' }" class="mc-nav-link">Forge</RouterLink>
        </nav>
      </template>
      <div class="mc-account">
        <span v-if="auth.isAuthenticated" class="mc-account-email">{{ auth.account?.email }}</span>
        <button v-if="auth.isAuthenticated" type="button" class="mc-btn-ghost" @click="logout">
          Log out
        </button>
      </div>
    </aside>
    <div class="mc-main">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.mc-shell {
  display: flex;
  min-height: 100vh;
  max-width: var(--mc-shell-width);
  margin: 0 auto;
}
.mc-sidebar {
  width: var(--mc-sidebar-width);
  flex: none;
  border-right: 1px solid var(--mc-border);
  padding: 1.25rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  background: var(--mc-surface);
}
.mc-brand {
  font-weight: 700;
  font-size: 1.1rem;
  color: var(--mc-text-primary);
  text-decoration: none;
}
.mc-nav-group {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.mc-nav-title {
  margin: 0 0 0.25rem;
  font-size: var(--mc-meta-size);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--mc-text-muted);
}
.mc-nav-link {
  color: var(--mc-text-secondary);
  text-decoration: none;
  padding: 0.35rem 0.5rem;
  border-radius: var(--mc-radius-sm);
}
.mc-nav-link:hover {
  color: var(--mc-text-primary);
  background: var(--mc-surface-raised);
}
.mc-nav-link.router-link-active {
  color: var(--mc-text-primary);
  background: var(--mc-surface-raised);
}
.mc-account {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.mc-account-email {
  font-size: 0.85rem;
  color: var(--mc-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
}
.mc-btn-ghost {
  background: transparent;
  border: 1px solid var(--mc-border);
  color: var(--mc-text-secondary);
  border-radius: var(--mc-radius-sm);
  padding: 0.4rem 0.6rem;
  cursor: pointer;
}
.mc-btn-ghost:hover {
  color: var(--mc-text-primary);
}
.mc-main {
  flex: 1;
  min-width: 0;
  padding: 1.5rem;
}
@media (max-width: 800px) {
  .mc-shell {
    flex-direction: column;
  }
  .mc-sidebar {
    width: auto;
    border-right: none;
    border-bottom: 1px solid var(--mc-border);
  }
}
</style>
