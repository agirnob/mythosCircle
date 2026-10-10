<script setup lang="ts">
/**
 * Application shell (§7) — campaign/workspace navigation.
 *
 * Reuses existing routes only; no new backend behavior. The WORLD / CREATE /
 * REVIEW / TOOLS grouping is conceptual (§6): World = what exists, Create =
 * what the DM wants to introduce, Review = staged proposals, Tools = graph.
 * Campaign exports live in the World directory; entity exports live on each
 * entity detail page.
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
    <a href="#main-content" class="mc-skip-link">Skip to content</a>
    <aside class="mc-sidebar" aria-label="Campaign workspace">
      <RouterLink :to="{ name: 'campaigns' }" class="mc-brand">
        <span class="mc-brand-mark" aria-hidden="true">✦</span>
        <span>mythos<span>Circle</span></span>
      </RouterLink>
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
      <nav v-if="auth.isAdmin" class="mc-nav-group" aria-label="Administration">
        <RouterLink :to="{ name: 'admin' }" class="mc-nav-link">Admin users</RouterLink>
      </nav>
      <div class="mc-account">
        <span v-if="auth.isAuthenticated" class="mc-account-email">{{ auth.account?.email }}</span>
        <button v-if="auth.isAuthenticated" type="button" class="mc-btn-ghost" @click="logout">
          Log out
        </button>
      </div>
    </aside>
    <main id="main-content" class="mc-main" tabindex="-1">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.mc-shell {
  display: flex;
  min-height: 100vh;
  width: 100%;
  max-width: none;
  margin: 0 auto;
  background: linear-gradient(90deg, rgba(13, 23, 36, 0.92), transparent 45%);
}
.mc-sidebar {
  width: var(--mc-sidebar-width);
  flex: none;
  border-right: 1px solid var(--mc-border);
  padding: 1.5rem 1rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  background: rgba(10, 18, 30, 0.8);
  backdrop-filter: blur(18px);
}
.mc-brand {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  font-weight: 700;
  font-size: 1.1rem;
  color: var(--mc-text-primary);
  text-decoration: none;
  letter-spacing: 0.01em;
}
.mc-brand > span:last-child {
  font-family: var(--mc-display-font);
  font-size: 1.25rem;
}
.mc-brand > span:last-child span {
  color: var(--mc-interactive-bright);
}
.mc-brand-mark {
  display: grid;
  width: 1.9rem;
  height: 1.9rem;
  place-items: center;
  border: 1px solid rgba(169, 148, 255, 0.7);
  border-radius: 8px;
  background: linear-gradient(145deg, rgba(139, 108, 255, 0.35), rgba(33, 188, 205, 0.16));
  color: var(--mc-interactive-bright);
  box-shadow: 0 0 24px var(--mc-glow-violet);
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
  background: var(--mc-surface-hover);
}
.mc-nav-link.router-link-active {
  color: var(--mc-text-primary);
  background: linear-gradient(90deg, rgba(139, 108, 255, 0.25), rgba(139, 108, 255, 0.06));
  box-shadow: inset 2px 0 0 var(--mc-interactive);
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
  border-color: var(--mc-border-bright);
  background: var(--mc-surface-raised);
}
.mc-main {
  flex: 1;
  min-width: 0;
  padding: clamp(1.25rem, 3vw, 2.5rem);
  overflow: hidden;
}
@media (max-width: 800px) {
  .mc-shell {
    flex-direction: column;
    background: none;
  }
  .mc-sidebar {
    width: auto;
    display: block;
    padding: 1rem;
    border-right: none;
    border-bottom: 1px solid var(--mc-border);
    backdrop-filter: none;
  }
  .mc-brand {
    margin-bottom: 1rem;
  }
  .mc-nav-group {
    display: flex;
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem;
    margin-top: 0.5rem;
  }
  .mc-nav-title {
    width: 100%;
    margin-bottom: 0;
  }
  .mc-nav-link {
    padding: 0.38rem 0.55rem;
  }
  .mc-account {
    margin-top: 1rem;
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
  }
  .mc-account .mc-btn-ghost {
    width: auto;
  }
  .mc-main {
    padding: 1.25rem 1rem 2rem;
  }
}
</style>
