<!--
  FILE: frontend/src/components/SidebarCooling.vue
  Role: Sidebar content for the Cooling & Climate sort.
  Agent boundary: Frontend — Sidebar (Cooling sort) (§5.4, §6.4, §10)
  Dependencies: TileCooling interface from @/types; receives :data prop from Sidebar.vue
  Output: Rendered cooling suitability detail panel
  How to test: Select Cooling sort, click any tile

  Displays (from ARCHITECTURE.md §5.4):
    - Cooling score (0–100)
    - Mean annual temperature (°C) — note: lower is better
    - Free-cooling hours estimate (hours/yr below 18°C)
    - Distance to nearest significant waterbody (km) + name
    - Nearest hydrometric station: mean flow rate (m³/s) if available
    - Annual rainfall (mm)
    - Aquifer productivity rating
-->
<template>
  <div class="sidebar-cooling">
    <section class="section">
      <h3 class="section__title">Climate <a href="https://met.ie/climate/available-data" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">Mean annual temperature</span>
        <span class="kv-row__value">
          {{ data.temperature !== null ? `${data.temperature?.toFixed(1)}°C` : '—' }}
          <span class="kv-row__hint" v-if="data.temperature !== null">(lower = better)</span>
        </span>
      </div>
      <div class="kv-row">
        <span class="kv-row__label">Free-cooling hours/yr</span>
        <span class="kv-row__value">
          {{ data.free_cooling_hours !== null ? `${data.free_cooling_hours?.toFixed(0)} hrs` : '—' }}
        </span>
      </div>
      <div class="kv-row">
        <span class="kv-row__label">Annual rainfall</span>
        <span class="kv-row__value">
          {{ data.rainfall !== null ? `${data.rainfall?.toFixed(0)} mm/yr` : '—' }}
        </span>
      </div>
    </section>

    <section class="section">
      <h3 class="section__title">Water Resources <a href="https://gis.epa.ie/GetData/Download" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">Water proximity score</span>
        <span class="kv-row__value">{{ data.water_proximity?.toFixed(0) ?? '—' }}/100</span>
      </div>
      <div class="kv-row" v-if="data.nearest_waterbody_name">
        <span class="kv-row__label">Nearest waterbody</span>
        <span class="kv-row__value">{{ data.nearest_waterbody_name }}</span>
      </div>
      <div class="kv-row" v-if="data.nearest_waterbody_km !== null">
        <span class="kv-row__label">Distance</span>
        <span class="kv-row__value">{{ data.nearest_waterbody_km?.toFixed(1) }} km</span>
      </div>
    </section>

    <section class="section">
      <h3 class="section__title">Hydrometric Station <a href="https://waterlevel.ie" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row" v-if="data.nearest_hydrometric_station_name">
        <span class="kv-row__label">Station</span>
        <span class="kv-row__value">{{ data.nearest_hydrometric_station_name }}</span>
      </div>
      <div class="kv-row" v-if="data.nearest_hydrometric_flow_m3s !== null">
        <span class="kv-row__label">Mean flow</span>
        <span class="kv-row__value">{{ data.nearest_hydrometric_flow_m3s?.toFixed(2) }} m³/s</span>
      </div>
      <div class="no-data" v-if="!data.nearest_hydrometric_station_name">No nearby station data</div>
    </section>

    <section class="section">
      <h3 class="section__title">Groundwater <a href="https://www.gsi.ie/en-ie/data-and-maps/Pages/default.aspx" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">Aquifer productivity</span>
        <span class="kv-row__value">{{ data.aquifer_productivity_rating ?? '—' }}</span>
      </div>
      <div class="kv-row">
        <span class="kv-row__label">Productivity score</span>
        <span class="kv-row__value">{{ data.aquifer_productivity?.toFixed(0) ?? '—' }}/100</span>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import type { TileCooling } from '@/types'

defineProps<{ data: TileCooling }>()
</script>

<style scoped>
.sidebar-cooling { display: flex; flex-direction: column; gap: 20px; }
.section__title { font-size: 11px; font-weight: 600; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }
.source-link { font-size: 10px; font-weight: 400; color: rgba(255,255,255,0.2); text-decoration: none; text-transform: none; letter-spacing: 0; flex-shrink: 0; }
.source-link:hover { color: rgba(255,255,255,0.6); }
.kv-row { display: flex; justify-content: space-between; align-items: center; font-size: 13px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
.kv-row__label { color: rgba(255,255,255,0.5); }
.kv-row__value { color: white; font-weight: 500; display: flex; align-items: center; gap: 6px; }
.kv-row__hint { font-size: 10px; color: #6baed6; font-weight: 400; }
.no-data { font-size: 12px; color: rgba(255,255,255,0.3); font-style: italic; padding: 6px 0; }
</style>
