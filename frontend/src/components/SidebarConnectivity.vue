<!--
  FILE: frontend/src/components/SidebarConnectivity.vue
  Role: Sidebar content for the Connectivity & Transport sort.
  Agent boundary: Frontend — Sidebar (Connectivity sort) (§5.5, §6.4, §10)
  Dependencies: TileConnectivity interface from @/types; receives :data prop from Sidebar.vue
  Output: Rendered connectivity detail panel
  How to test: Select Connectivity sort, click any tile

  Displays (from ARCHITECTURE.md §5.5):
    - Connectivity score (0–100)
    - Distance to INEX Dublin + INEX Cork (km)
    - ComReg broadband coverage tier
    - Distance to nearest motorway junction + road name
    - Distance to nearest national primary road
    - Distance to nearest rail freight terminal
    - Note: no public fibre GIS data — ComReg broadband is the best proxy (D6)
-->
<template>
  <div class="sidebar-connectivity">
    <section class="section">
      <h3 class="section__title">Internet Exchanges <a href="https://www.peeringdb.com/ix/48" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row" v-if="data.inex_dublin_km !== null">
        <span class="kv-row__label">INEX Dublin</span>
        <span class="kv-row__value">{{ data.inex_dublin_km?.toFixed(1) }} km</span>
      </div>
      <div class="kv-row" v-if="data.inex_cork_km !== null">
        <span class="kv-row__label">INEX Cork</span>
        <span class="kv-row__value">{{ data.inex_cork_km?.toFixed(1) }} km</span>
      </div>
      <div class="kv-row">
        <span class="kv-row__label">IX distance score</span>
        <span class="kv-row__value">{{ data.ix_distance?.toFixed(0) ?? '—' }}/100</span>
      </div>
    </section>

    <section class="section">
      <h3 class="section__title">Broadband <a href="https://datamaps-comreg.hub.arcgis.com" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">Coverage tier</span>
        <span class="kv-row__value">{{ data.broadband_tier ?? '—' }}</span>
      </div>
      <div class="kv-row">
        <span class="kv-row__label">Broadband score</span>
        <span class="kv-row__value">{{ data.broadband?.toFixed(0) ?? '—' }}/100</span>
      </div>
      <div class="fibre-note">
        No public GIS fibre route data exists for Ireland. ComReg broadband coverage is the best available proxy.
      </div>
    </section>

    <section class="section">
      <h3 class="section__title">Road Access <a href="https://www.openstreetmap.org" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row" v-if="data.nearest_motorway_junction_km !== null">
        <span class="kv-row__label">Nearest motorway junction</span>
        <span class="kv-row__value">{{ data.nearest_motorway_junction_km?.toFixed(1) }} km</span>
      </div>
      <div class="kv-row" v-if="data.nearest_motorway_junction_name">
        <span class="kv-row__label">Junction name</span>
        <span class="kv-row__value">{{ data.nearest_motorway_junction_name }}</span>
      </div>
      <div class="kv-row" v-if="data.nearest_national_road_km !== null">
        <span class="kv-row__label">Nearest national primary road</span>
        <span class="kv-row__value">{{ data.nearest_national_road_km?.toFixed(1) }} km</span>
      </div>
      <div class="kv-row">
        <span class="kv-row__label">Road access score</span>
        <span class="kv-row__value">{{ data.road_access?.toFixed(0) ?? '—' }}/100</span>
      </div>
    </section>

    <!-- Grid Access (moved from Energy, P2-22) -->
    <section class="section">
      <h3 class="section__title">Grid Access <a href="https://www.openstreetmap.org" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">Grid proximity score</span>
        <span class="kv-row__value">{{ data.grid_proximity?.toFixed(0) ?? '—' }}/100</span>
      </div>
    </section>

    <section class="section">
      <h3 class="section__title">Rail Freight</h3>
      <div class="kv-row" v-if="data.nearest_rail_freight_km !== null">
        <span class="kv-row__label">Nearest rail freight terminal</span>
        <span class="kv-row__value">{{ data.nearest_rail_freight_km?.toFixed(1) }} km</span>
      </div>
      <div class="no-data" v-else>No rail freight data available</div>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { TileConnectivity } from '@/types'

defineProps<{ data: TileConnectivity }>()
</script>

<style scoped>
.sidebar-connectivity { display: flex; flex-direction: column; gap: 20px; }
.section__title { font-size: 11px; font-weight: 600; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }
.source-link { font-size: 10px; font-weight: 400; color: rgba(255,255,255,0.2); text-decoration: none; text-transform: none; letter-spacing: 0; flex-shrink: 0; }
.source-link:hover { color: rgba(255,255,255,0.6); }
.kv-row { display: flex; justify-content: space-between; font-size: 13px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
.kv-row__label { color: rgba(255,255,255,0.5); }
.kv-row__value { color: white; font-weight: 500; }
.fibre-note { font-size: 11px; color: rgba(255,255,255,0.3); font-style: italic; margin-top: 8px; line-height: 1.4; }
.no-data { font-size: 12px; color: rgba(255,255,255,0.3); font-style: italic; padding: 6px 0; }
</style>
