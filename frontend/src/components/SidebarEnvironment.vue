<!--
  FILE: frontend/src/components/SidebarEnvironment.vue
  Role: Sidebar content for the Environmental Constraints sort.
  Agent boundary: Frontend — Sidebar (Environment sort) (§5.3, §6.4, §10)
  Dependencies: TileEnvironment interface from @/types; receives :data prop from Sidebar.vue
  Output: Rendered environmental constraints detail panel
  How to test: Select Environment (Constraints) sort, click any tile

  Displays (from ARCHITECTURE.md §5.3):
    - Constraint score (0–100, 100 = unconstrained)
    - List of designated areas (SAC/SPA/NHA/pNHA) with % overlap
    - Flood risk: current + future flood extent intersections
    - Landslide susceptibility rating
    - Hard exclusion statement if applicable
    - Link to OPW flood viewer

  LICENCE NOTE: OPW flood data is CC BY-NC-ND. Display a notice for any
  commercial deployment. See ARCHITECTURE.md §11 (D7) and §5.3.
-->
<template>
  <div class="sidebar-env">
    <!-- Hard exclusion banner -->
    <div class="exclusion-banner" v-if="data.has_hard_exclusion">
      <ShieldAlert :size="14" />
      Hard exclusion: {{ data.exclusion_reason ?? 'Protected area or flood zone' }}
    </div>

    <!-- Designated areas list -->
    <section class="section">
      <h3 class="section__title">Protected Area Designations <a href="https://npws.ie/maps-and-data/designated-site-data/download-boundary-data" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div v-if="data.designations?.length">
        <div
          v-for="d in data.designations"
          :key="d.designation_id ?? d.designation_name"
          class="designation-row"
        >
          <span class="designation-row__badge" :class="`badge--${d.designation_type.toLowerCase()}`">
            {{ d.designation_type }}
          </span>
          <span class="designation-row__name">{{ d.designation_name }}</span>
          <span class="designation-row__pct">{{ Number(d.pct_overlap).toFixed(0) }}%</span>
        </div>
      </div>
      <div v-else class="no-data">No protected area overlaps</div>
    </section>

    <!-- Flood risk -->
    <section class="section">
      <h3 class="section__title">Flood Risk <a href="https://www.floodinfo.ie" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">Current flood extent</span>
        <span class="kv-row__value" :class="data.intersects_current_flood ? 'danger' : 'safe'">
          {{ data.intersects_current_flood ? 'Yes (hard exclusion)' : 'No' }}
        </span>
      </div>
      <div class="kv-row">
        <span class="kv-row__label">Future flood extent</span>
        <span class="kv-row__value" :class="data.intersects_future_flood ? 'warn' : 'safe'">
          {{ data.intersects_future_flood ? 'Yes (penalty)' : 'No' }}
        </span>
      </div>
      <!-- flood_risk score moved to Planning sort (P2-22) -->
      <a
        href="https://www.floodinfo.ie"
        target="_blank"
        rel="noopener noreferrer"
        class="external-link"
      >View OPW Flood Map ↗</a>
    </section>

    <!-- Landslide -->
    <section class="section">
      <h3 class="section__title">Landslide Susceptibility <a href="https://www.gsi.ie/en-ie/data-and-maps/Pages/default.aspx" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">Susceptibility</span>
        <span class="kv-row__value" :class="landslideClass">
          {{ data.landslide_susceptibility ?? 'none' }}
        </span>
      </div>
      <!-- landslide_risk score moved to Planning sort (P2-22) -->
    </section>

    <!-- Water & Aquifer (moved from Cooling, P2-22) -->
    <section class="section">
      <h3 class="section__title">Water &amp; Aquifer <a href="https://gis.epa.ie/GetData/Download" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row">
        <span class="kv-row__label">Water proximity score</span>
        <span class="kv-row__value">{{ data.water_proximity?.toFixed(0) ?? '—' }}/100</span>
      </div>
      <div class="kv-row">
        <span class="kv-row__label">Aquifer productivity score</span>
        <span class="kv-row__value">{{ data.aquifer_productivity?.toFixed(0) ?? '—' }}/100</span>
      </div>
    </section>

    <!-- OPW licence notice -->
    <div class="licence-notice">
      OPW flood data: CC BY-NC-ND. Non-commercial use only.
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ShieldAlert } from 'lucide-vue-next'
import type { TileEnvironment } from '@/types'

const props = defineProps<{ data: TileEnvironment }>()

const landslideClass = computed(() => {
  const suscep = props.data.landslide_susceptibility
  if (suscep === 'high') return 'danger'
  if (suscep === 'medium') return 'warn'
  return 'safe'
})
</script>

<style scoped>
.sidebar-env { display: flex; flex-direction: column; gap: 20px; }
.score-headline { display: flex; align-items: baseline; justify-content: space-between; }
.score-headline__label { font-size: 13px; color: rgba(255,255,255,0.5); text-transform: uppercase; letter-spacing: 0.05em; }
.score-headline__value { font-size: 36px; font-weight: 800; }
.score-headline__max { font-size: 16px; color: rgba(255,255,255,0.4); font-weight: 400; margin-left: 2px; }
.exclusion-banner { display: flex; align-items: center; gap: 6px; background: rgba(215, 48, 39, 0.15); border: 1px solid rgba(215, 48, 39, 0.4); border-radius: 6px; padding: 8px 12px; font-size: 12px; color: #e74c3c; }
.section__title { font-size: 11px; font-weight: 600; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }
.source-link { font-size: 10px; font-weight: 400; color: rgba(255,255,255,0.2); text-decoration: none; text-transform: none; letter-spacing: 0; flex-shrink: 0; }
.source-link:hover { color: rgba(255,255,255,0.6); }
.designation-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); font-size: 13px; }
.designation-row__badge { padding: 1px 6px; border-radius: 3px; font-size: 10px; font-weight: 700; }
.badge--sac { background: rgba(215,48,39,0.3); color: #e74c3c; }
.badge--spa { background: rgba(69,117,180,0.3); color: #6baed6; }
.badge--nha, .badge--pnha { background: rgba(120,198,121,0.3); color: #74c476; }
.designation-row__name { flex: 1; color: rgba(255,255,255,0.8); }
.designation-row__pct { color: rgba(255,255,255,0.4); font-size: 11px; }
.no-data { font-size: 13px; color: rgba(255,255,255,0.3); font-style: italic; }
.kv-row { display: flex; justify-content: space-between; font-size: 13px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
.kv-row__label { color: rgba(255,255,255,0.5); }
.kv-row__value { font-weight: 500; }
.danger { color: #e74c3c; }
.warn { color: #fd8d3c; }
.safe { color: #74c476; }
.external-link { font-size: 12px; color: #6baed6; text-decoration: none; display: block; margin-top: 8px; }
.external-link:hover { text-decoration: underline; }
.licence-notice { font-size: 10px; color: rgba(255,255,255,0.2); border-top: 1px solid rgba(255,255,255,0.06); padding-top: 8px; }
</style>
