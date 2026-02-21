/**
 * FILE: frontend/vite.config.ts
 * Role: Vite build configuration + dev server proxy rules.
 * Agent boundary: Frontend build tooling
 * Dependencies: package.json dependencies installed
 * Output: Dev server on :5173; production build in dist/
 * How to test: npm run dev → http://localhost:5173
 *
 * Proxy rules (dev only — nginx handles in production):
 *   /api/   → http://api:8000  (FastAPI)
 *   /tiles/ → http://martin:3000  (Martin MVT)
 */

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // FastAPI — strip /api prefix since FastAPI mounts at /api/
      '/api': {
        target: 'http://api:8000',
        changeOrigin: true,
      },
      // Martin tile server — strip /tiles prefix
      '/tiles': {
        target: 'http://martin:3000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/tiles/, ''),
      },
    },
  },

  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        // Code split: vendor (maplibre/vue) vs app code
        manualChunks: {
          'maplibre': ['maplibre-gl'],
          'vue-vendor': ['vue', 'pinia'],
        },
      },
    },
  },
})
