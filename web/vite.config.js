import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// Builds the SPA into the gateway's static dir so FastAPI serves it (deploy stays
// Python-only — Node is needed only to build). Served under /app/.
export default defineConfig({
  plugins: [svelte()],
  base: '/app/',
  build: {
    outDir: '../src/assistant/gateway/static/app',
    emptyOutDir: true,
    rollupOptions: {
      // three.js (WebGPU build, ~1MB) is loaded from CDN at runtime via the
      // index.html importmap — don't bundle it, so the committed SPA stays small
      // and under the repo's large-file guard. Dev still resolves it locally.
      external: [/^three($|\/)/],
    },
  },
  server: {
    // dev: proxy API + WebSockets to the running gateway. The gateway rejects
    // cross-origin browser requests (_origin_ok in gateway/app.py). Same-origin
    // REST GETs send no Origin header so they pass, but browsers ALWAYS send an
    // Origin on the WebSocket handshake (here: the dev server's localhost:5173+),
    // which wouldn't match the gateway host → 403, so the event stream never
    // connects and chats render empty. Rewrite the Origin on proxied requests to
    // the target so the dev server's stream is accepted. Dev-only; prod is served
    // same-origin by the gateway and never hits this.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8800',
        ws: true,
        changeOrigin: true,
        configure: (proxy) => {
          const origin = 'http://127.0.0.1:8800'
          proxy.on('proxyReq', (proxyReq) => proxyReq.setHeader('origin', origin))
          proxy.on('proxyReqWs', (proxyReq) => proxyReq.setHeader('origin', origin))
        },
      },
    },
  },
})
