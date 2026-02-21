/**
 * FILE: frontend/src/main.ts
 * Role: Vue app entry point — mounts the app, registers Pinia.
 * Agent boundary: Frontend bootstrap
 * Dependencies: App.vue, stores/suitability.ts
 * Output: Vue SPA mounted on #app
 * How to test: npm run dev → http://localhost:5173
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'

// MapLibre CSS imported here as fallback if not loaded via index.html CDN
import 'maplibre-gl/dist/maplibre-gl.css'
import './assets/main.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.mount('#app')
