<!--
  FILE: frontend/src/App.vue
  Role: Root component — mounts store, lays out MapView + Sidebar + DataBar (bottom).
  Agent boundary: Frontend — layout shell
  Dependencies: useSuitabilityStore (must call init()), MapView, DataBar, Sidebar, MapLegend
  Output: Full-screen SPA layout

  Layout structure:
    ┌─────────────────────────────────────┐
    │                          │          │
    │  MapView                 │ Sidebar  │
    │  (MapLibre choropleth)   │ (slide)  │
    │                          │          │
    ├─────────────────────────────────────┤
    │ DataBar (sort tiles + sub-metric)   │
    └─────────────────────────────────────┘
-->
<template>
  <div class="app-shell" :style="{ '--color-accent': accentColor }">
    <!-- Main content area: map + sidebar side by side -->
    <div class="app-main">
      <MapView class="app-map" />
      <img src="/logo.svg" alt="WattWhere" class="app-logo" />
      <CustomBuilder class="app-custom-builder" />
      <Sidebar class="app-sidebar" />
    </div>

    <!-- DataBar: sort tiles + sub-metric pills — fixed at bottom -->
    <DataBar class="app-databar" />

    <!-- Toast notifications (error/warning/info) — fixed top-right overlay -->
    <ToastContainer />

    <!-- First-time visitor onboarding — handles own show/hide via localStorage -->
    <OnboardingTooltip />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useSuitabilityStore } from '@/stores/suitability'
import { useToast } from '@/composables/useToast'
import DataBar from '@/components/DataBar.vue'
import MapView from '@/components/MapView.vue'
import Sidebar from '@/components/Sidebar.vue'
import CustomBuilder from '@/components/CustomBuilder.vue'
import ToastContainer from '@/components/ToastContainer.vue'
import OnboardingTooltip from '@/components/OnboardingTooltip.vue'

const store = useSuitabilityStore()
const { push: pushToast, remove: removeToast } = useToast()

// ── Offline / online detection ─────────────────────────────────
function onOffline() {
  pushToast({ id: 'offline', message: 'No internet connection', type: 'warning' })
}
function onOnline() {
  removeToast('offline')
}

onMounted(async () => {
  window.addEventListener('offline', onOffline)
  window.addEventListener('online', onOnline)
  await store.init()
})

onUnmounted(() => {
  window.removeEventListener('offline', onOffline)
  window.removeEventListener('online', onOnline)
})

// ── Per-sort accent colours (from Figma SVG) ────────────────
const SORT_COLORS: Record<string, string> = {
  overall:      '#488bff',
  energy:       '#fee000',
  environment:  '#2cb549',
  cooling:      '#38bdf8',
  connectivity: '#a78bfa',
  planning:     '#fb923c',
  custom:       '#8b6af0',
}

const accentColor = computed(() => SORT_COLORS[store.activeSort] ?? '#488bff')
</script>

<style>
/* Global reset */
*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html, body, #app {
  height: 100%;
  width: 100%;
  overflow: hidden;
  font-family: system-ui, -apple-system, sans-serif;
  background: var(--color-bg);
  color: var(--color-text);
}

/* App shell: full-height flex column */
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100%;
}

/* Main area: map + sidebar, fills all space above DataBar */
.app-main {
  display: flex;
  flex: 1;
  position: relative;
  overflow: hidden;
}

/* Map fills available width */
.app-map {
  flex: 1;
  position: relative;
}

/* Logo: overlaid top-left of the map */
.app-logo {
  position: absolute;
  top: 16px;
  left: 16px;
  width: 400px;
  height: auto;
  z-index: 150;
  pointer-events: none;
}

/* Custom Builder: absolute positioned right, below sidebar z-index */
.app-custom-builder {
  position: absolute;
  top: 0;
  right: 0;
  height: 100%;
  z-index: 190;
}

/* Sidebar: absolute positioned, slides in from right (above custom builder) */
.app-sidebar {
  position: absolute;
  top: 0;
  right: 0;
  height: 100%;
  z-index: 200;
}

/* DataBar: fixed height at bottom */
.app-databar {
  flex-shrink: 0;
  z-index: 100;
}
</style>
