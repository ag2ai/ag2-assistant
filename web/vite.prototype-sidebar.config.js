import { defineConfig, mergeConfig } from 'vite'
import appConfig from './vite.config.js'

// Throwaway UI host; GETs and replay streams use the existing local gateway.
export default mergeConfig(appConfig, defineConfig({
  plugins: [{
    name: 'sidebar-prototype-read-only',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url?.startsWith('/api') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
          response.writeHead(403, { 'Content-Type': 'application/json' })
          response.end(JSON.stringify({ error: 'This prototype only reads application data.' }))
          return
        }
        next()
      })
    },
  }],
  server: {
    host: '127.0.0.1', port: 8888, strictPort: true,
    open: '/app/profile-2/screens/s/english-demo.screen.yaml?variant=A',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8878', ws: true, changeOrigin: true,
        configure: proxy => {
          proxy.on('proxyReq', request => request.setHeader('origin', 'http://127.0.0.1:8878'))
          proxy.on('proxyReqWs', request => request.setHeader('origin', 'http://127.0.0.1:8878'))
        },
      },
    },
  },
}))
