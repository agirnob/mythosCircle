import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import { setUnauthorizedHandler } from './api/client'
import { useAuthStore } from './stores/auth'
import router from './router'

const app = createApp(App)
app.use(createPinia())
// Single generic 401 (AR29): clear the session and send the user to login.
setUnauthorizedHandler(() => {
  const auth = useAuthStore()
  auth.account = null
  void router.push({ name: 'login' })
})
app.use(router)
app.mount('#app')
