import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies /api to Flask so the SSE telemetry stream and the MJPEG
// camera preview are same-origin (no CORS pre-flight on long-lived connections).
export default defineConfig(({ isSsrBuild }) => ({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: isSsrBuild
        ? {}
        : {
            // Charts are only needed on two screens - keep them out of the entry chunk.
            manualChunks: { charts: ['recharts'], vendor: ['react', 'react-dom', 'react-router-dom'] },
          },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5000',
        changeOrigin: true,
        // Long-lived streams must not be buffered or timed out by the proxy.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            if (String(proxyRes.headers['content-type'] || '').includes('event-stream')) {
              proxyRes.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
    },
  },
}))
