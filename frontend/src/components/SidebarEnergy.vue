<!--
  FILE: frontend/src/components/SidebarEnergy.vue
  Role: Sidebar content for the Energy sort — wind, solar, grid proximity detail.
  Agent boundary: Frontend — Sidebar (Energy sort) (§5.2, §6.4, §10)
  Dependencies: TileEnergy interface from @/types; receives :data prop from Sidebar.vue
  Output: Rendered energy suitability detail panel
  How to test: Select Energy sort, click any tile

  Displays (from ARCHITECTURE.md §5.2):
    - Energy score (0–100)
    - Mean wind speed at 50m, 100m, 150m (m/s)
    - Solar GHI (kWh/m²/yr)
    - Distance to nearest transmission line + substation (km)
    - Nearest substation name + voltage
    - Low-confidence flag if nearest infrastructure > 20 km
    - Link to EirGrid Smart Grid Dashboard (external, not embedded data)
-->
<template>
  <div class="sidebar-energy">
    <!-- Low-confidence flag -->
    <div class="warning-banner" v-if="data.grid_low_confidence">
      <AlertTriangle :size="14" />
      Grid data low confidence — nearest infrastructure &gt;20 km
    </div>

    <section class="section">
      <h3 class="section__title">Wind Speed <a href="https://globalwindatlas.info" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row" v-if="data.wind_speed_50m !== null">
        <span class="kv-row__label">At 50m</span>
        <span class="kv-row__value">{{ data.wind_speed_50m?.toFixed(1) }} m/s</span>
      </div>
      <div class="kv-row" v-if="data.wind_speed_100m !== null">
        <span class="kv-row__label">At 100m</span>
        <span class="kv-row__value">{{ data.wind_speed_100m?.toFixed(1) }} m/s</span>
      </div>
      <div class="kv-row" v-if="data.wind_speed_150m !== null">
        <span class="kv-row__label">At 150m</span>
        <span class="kv-row__value">{{ data.wind_speed_150m?.toFixed(1) }} m/s</span>
      </div>
    </section>

    <section class="section">
      <h3 class="section__title">Solar Irradiance <a href="https://power.larc.nasa.gov" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">GHI</span>
        <span class="kv-row__value">
          {{ data.solar_ghi !== null ? `${data.solar_ghi?.toFixed(0)} kWh/m²/yr` : '—' }}
        </span>
      </div>
    </section>

    <section class="section">
      <h3 class="section__title">Grid Infrastructure <a href="https://www.openstreetmap.org" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">Grid proximity score</span>
        <span class="kv-row__value">{{ data.grid_proximity?.toFixed(0) ?? '—' }}</span>
      </div>
      <div class="kv-row" v-if="data.nearest_transmission_line_km !== null">
        <span class="kv-row__label">Nearest transmission line</span>
        <span class="kv-row__value">{{ data.nearest_transmission_line_km?.toFixed(1) }} km</span>
      </div>
      <div class="kv-row" v-if="data.nearest_substation_km !== null">
        <span class="kv-row__label">Nearest substation</span>
        <span class="kv-row__value">{{ data.nearest_substation_km?.toFixed(1) }} km</span>
      </div>
      <div class="kv-row" v-if="data.nearest_substation_name">
        <span class="kv-row__label">Substation name</span>
        <span class="kv-row__value">{{ data.nearest_substation_name }}</span>
      </div>
      <div class="kv-row" v-if="data.nearest_substation_voltage">
        <span class="kv-row__label">Voltage level</span>
        <span class="kv-row__value">{{ data.nearest_substation_voltage }}</span>
      </div>
    </section>

    <section class="section">
      <a
        href="https://www.smartgriddashboard.com"
        target="_blank"
        rel="noopener noreferrer"
        class="external-link"
      >
        EirGrid Smart Grid Dashboard ↗
      </a>
      <p class="note">Real-time generation data (external link — not embedded)</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { AlertTriangle } from 'lucide-vue-next'
import type { TileEnergy } from '@/types'

defineProps<{ data: TileEnergy }>()
</script>

<style scoped>
.sidebar-energy { display: flex; flex-direction: column; gap: 20px; }
.warning-banner { display: flex; align-items: center; gap: 6px; background: rgba(253, 141, 60, 0.12); border: 1px solid rgba(253, 141, 60, 0.3); border-radius: 6px; padding: 8px 12px; font-size: 12px; color: #fd8d3c; }
.section__title { font-size: 11px; font-weight: 600; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }
.source-link { font-size: 10px; font-weight: 400; color: rgba(255,255,255,0.2); text-decoration: none; text-transform: none; letter-spacing: 0; flex-shrink: 0; }
.source-link:hover { color: rgba(255,255,255,0.6); }
.kv-row { display: flex; justify-content: space-between; font-size: 13px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
.kv-row__label { color: rgba(255,255,255,0.5); }
.kv-row__value { color: white; font-weight: 500; }
.external-link { font-size: 13px; color: #6baed6; text-decoration: none; }
.external-link:hover { text-decoration: underline; }
.note { font-size: 11px; color: rgba(255,255,255,0.3); margin-top: 4px; }
</style>
