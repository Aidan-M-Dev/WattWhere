<!--
  FILE: frontend/src/App.vue
  Role: Root component — mounts store, lays out DataBar + MapView + Sidebar + Legend.
  Agent boundary: Frontend — layout shell
  Dependencies: useSuitabilityStore (must call init()), MapView, DataBar, Sidebar, MapLegend
  Output: Full-screen SPA layout
  How to test: npm run dev — all 4 child components should render

  Layout structure:
    ┌─────────────────────────────────────┐
    │ DataBar (sort + sub-metric tabs)    │
    ├─────────────────────────────────────┤
    │                          │          │
    │  MapView                 │ Sidebar  │
    │  (MapLibre choropleth)   │ (slide)  │
    │                          │          │
    │ [MapLegend]              │          │
    └─────────────────────────────────────┘
-->
<template>
  <div class="app-shell">
    <!-- DataBar: sort tabs + sub-metric pills -->
    <DataBar class="app-databar" />

    <!-- Main content area: map + sidebar side by side -->
    <div class="app-main">
      <!-- Map takes remaining space; sidebar overlays or pushes it -->
      <MapView class="app-map" />

      <!-- Sidebar slides in from right on tile click -->
      <Sidebar class="app-sidebar" />
    </div>

    <!-- Map legend: fixed bottom-left inside map viewport -->
    <!-- Rendered inside MapView to position relative to map bounds -->
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useSuitabilityStore } from '@/stores/suitability'
import DataBar from '@/components/DataBar.vue'
import MapView from '@/components/MapView.vue'
import Sidebar from '@/components/Sidebar.vue'

const store = useSuitabilityStore()

// Initialise on mount: fetch sorts metadata + initial pins
onMounted(async () => {
  await store.init()
})
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
  background: #1a1a2e;
  color: #e8e8e8;
}

/* App shell: full-height flex column */
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
}

/* DataBar: fixed height at top */
.app-databar {
  flex-shrink: 0;
  z-index: 100;
}

/* Main area: map + sidebar, fills remaining height */
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

/* Sidebar: absolute positioned, slides in from right */
.app-sidebar {
  position: absolute;
  top: 0;
  right: 0;
  height: 100%;
  z-index: 200;
  /* Width and visibility controlled by Sidebar.vue itself */
}
</style>
