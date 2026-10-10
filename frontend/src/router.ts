import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from './stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/admin',
      name: 'admin',
      component: () => import('./views/AdminView.vue'),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue') },
    { path: '/register', name: 'register', component: () => import('./views/RegisterView.vue') },
    {
      path: '/',
      name: 'campaigns',
      component: () => import('./views/CampaignsView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/campaigns/:id/build-in',
      name: 'build-in',
      component: () => import('./views/BuildInView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/campaigns/:id/candidates',
      name: 'candidates',
      // Compatibility route: Ask the World owns the primary proposal queue.
      // Old saved links are sent to the current review surface.
      redirect: (to) => ({ name: 'ask', params: { id: to.params.id } }),
      meta: { requiresAuth: true },
    },
    {
      // Campaign overview previews each entity kind.
      path: '/campaigns/:id/overview',
      name: 'overview',
      component: () => import('./views/NewWorldView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/campaigns/:id/world',
      name: 'world',
      component: () => import('./views/NewWorldView.vue'),
      props: { mode: 'directory' },
      meta: { requiresAuth: true },
    },
    {
      // v3 Tonight: the run-state summary + recent-changes feed on its
      // own page (owner feed clash 2026-09-27: the overview caps its
      // preview; this page carries the full feed).
      path: '/campaigns/:id/tonight',
      name: 'tonight',
      component: () => import('./views/TonightView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // Rebuild Stage 3: DM-readable entity detail (NewEntityView).
      path: '/campaigns/:id/entities/:entityId',
      name: 'entity',
      component: () => import('./views/NewEntityView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // Browsable collection for one entity kind. This keeps overview
      // sections from dropping users into the legacy mixed world surface.
      path: '/campaigns/:id/entities/kind/:kind',
      name: 'entity-section',
      component: () => import('./views/EntitySectionView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // Rebuild Stage 4: plain-language ask with staged proposals (NewAskView).
      path: '/campaigns/:id/ask',
      name: 'ask',
      component: () => import('./views/NewAskView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // Story 7.1: the relationship web (reads the world store only). The
      // ?focus=<entity-id> query is the entity deep-link; the raw ULID is
      // never rendered — the view shows a human-readable breadcrumb.
      path: '/campaigns/:id/graph',
      name: 'graph',
      component: () => import('./components/graph/GraphView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/forge',
      name: 'forge-global',
      component: () => import('./views/CharacterForgeView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/campaigns/:id/forge',
      name: 'forge',
      component: () => import('./views/CharacterForgeView.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/campaigns/:id/add-character',
      name: 'add-character',
      component: () => import('./views/AddCharacterView.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.hydrated) {
    try {
      await auth.hydrate()
    } catch {
      // A non-401 hydrate failure (e.g. server down) falls through to the
      // normal isAuthenticated check instead of aborting navigation.
    }
  }
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { next: to.fullPath } }
  }
  if (to.meta.requiresAdmin) {
    try {
      await auth.hydrate()
    } catch {
      auth.denyAdmin()
    }
    if (!auth.isAuthenticated) return { name: 'login', query: { next: to.fullPath } }
    if (!auth.isAdmin) return { name: 'campaigns', query: { notice: 'admin-access-denied' } }
  }
  if ((to.name === 'login' || to.name === 'register') && auth.isAuthenticated) {
    return { name: 'campaigns' }
  }
  return true
})

export default router
