import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from './stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
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
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.hydrated) {
    await auth.hydrate()
  }
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { next: to.fullPath } }
  }
  if ((to.name === 'login' || to.name === 'register') && auth.isAuthenticated) {
    return { name: 'campaigns' }
  }
  return true
})

export default router
