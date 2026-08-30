import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      // The single FastAPI process (see deploy/mythoscircle.service).
      // ws: true upgrades /api/ws/jobs (AD-17) through the same proxy.
      '/api': { target: 'http://127.0.0.1:8000', ws: true },
    },
  },
  test: {
    environment: 'node',
  },
})