import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Builds the SPA into the gateway's static dir so FastAPI serves it (deploy stays
// Python-only — Node is needed only to build). Served under /app/.
export default defineConfig({
  plugins: [svelte()],
  base: '/app/',
  build: {
    outDir: '../src/agclaw/gateway/static/app',
    emptyOutDir: true,
  },
  server: {
    // dev: proxy API + WebSockets to the running gateway
    proxy: {
      '/api': { target: 'http://127.0.0.1:8800', ws: true, changeOrigin: true },
    },
  },
})
