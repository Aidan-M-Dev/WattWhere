<!--
  FILE: frontend/src/components/SidebarPlanning.vue
  Role: Sidebar content for the Planning & Zoning sort.
  Agent boundary: Frontend — Sidebar (Planning sort) (§5.6, §6.4, §10)
  Dependencies: TilePlanning interface from @/types; receives :data prop from Sidebar.vue
  Output: Rendered planning suitability detail panel
  How to test: Select Planning sort, click any tile

  Displays (from ARCHITECTURE.md §5.6):
    - Planning score (0–100)
    - Zoning breakdown (% per category: Industrial, Enterprise, Mixed Use,
      Agricultural, Residential, Other) — as a visual stacked bar
    - List of planning applications within the tile (ref, status, date, type)
    - Nearest IDA industrial site (name + distance)
    - Population density (per km²) — objection risk / workforce proxy
    - County Development Plan reference
-->
<template>
  <div class="sidebar-planning">
    <!-- Zoning breakdown -->
    <section class="section">
      <h3 class="section__title">Zoning Breakdown <a href="https://myplan.ie" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <!-- Stacked bar -->
      <div class="zoning-bar">
        <div v-for="z in zoningSegments" :key="z.key"
          class="zoning-bar__segment"
          :style="{ width: `${z.pct}%`, background: z.color }"
          :title="`${z.label}: ${z.pct.toFixed(0)}%`"
        />
      </div>
      <!-- Legend -->
      <div class="zoning-legend">
        <div v-for="z in zoningSegments" :key="z.key" class="zoning-legend__item">
          <span class="zoning-legend__dot" :style="{ background: z.color }" />
          <span class="zoning-legend__label">{{ z.label }}</span>
          <span class="zoning-legend__pct">{{ z.pct.toFixed(0) }}%</span>
        </div>
      </div>
    </section>

    <!-- Planning applications -->
    <section class="section">
      <h3 class="section__title">Planning Applications <a href="https://data.gov.ie" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div v-if="data.planning_applications?.length">
        <div
          v-for="app in data.planning_applications"
          :key="app.app_ref"
          class="app-row"
        >
          <div class="app-row__header">
            <span class="app-row__ref">{{ app.app_ref }}</span>
            <span class="app-row__status" :class="`status--${app.status}`">{{ app.status }}</span>
          </div>
          <div class="app-row__meta">
            {{ app.app_type ?? 'Development' }} · {{ app.app_date ?? 'No date' }}
          </div>
        </div>
      </div>
      <div class="no-data" v-else>No planning applications in this tile</div>
    </section>

    <!-- Land Pricing -->
    <section class="section" v-if="data.land_price_score !== null || data.avg_price_per_sqm_eur !== null">
      <h3 class="section__title">Land Pricing <a href="https://www.propertypriceregister.ie" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row" v-if="data.land_price_score !== null">
        <span class="kv-row__label">Price score</span>
        <span class="kv-row__value">{{ data.land_price_score }}<span class="kv-row__unit">/100</span></span>
      </div>
      <div class="kv-row" v-if="data.avg_price_per_sqm_eur !== null">
        <span class="kv-row__label">Avg property price</span>
        <span class="kv-row__value">{{ formatEur(data.avg_price_per_sqm_eur) }}/m²</span>
      </div>
      <div class="kv-row" v-if="data.transaction_count !== null && data.transaction_count > 0">
        <span class="kv-row__label">Transactions</span>
        <span class="kv-row__value">{{ data.transaction_count.toLocaleString() }}</span>
      </div>
    </section>

    <!-- IDA sites + population -->
    <section class="section">
      <h3 class="section__title">Context <a href="https://www.idaireland.com/locate-in-ireland/available-properties" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="kv-row" v-if="data.nearest_ida_site_km !== null">
        <span class="kv-row__label">Nearest IDA site</span>
        <span class="kv-row__value">{{ data.nearest_ida_site_km?.toFixed(1) }} km</span>
      </div>
      <div class="kv-row" v-if="data.population_density_per_km2 !== null">
        <span class="kv-row__label">Population density</span>
        <span class="kv-row__value">{{ data.population_density_per_km2?.toFixed(0) }} /km²</span>
      </div>
      <div class="kv-row" v-if="data.county_dev_plan_ref">
        <span class="kv-row__label">County Dev Plan</span>
        <span class="kv-row__value">{{ data.county_dev_plan_ref }}</span>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { TilePlanning } from '@/types'

const props = defineProps<{ data: TilePlanning }>()

function formatEur(val: number): string {
  return '€' + val.toLocaleString('en-IE', { maximumFractionDigits: 0 })
}

// Zoning segment config (colour matches planning sort ramp family)
const zoningSegments = computed(() => [
  { key: 'industrial',  label: 'Industrial',   pct: props.data.pct_industrial,  color: '#7f2704' },
  { key: 'enterprise',  label: 'Enterprise',   pct: props.data.pct_enterprise,  color: '#d94801' },
  { key: 'mixed_use',   label: 'Mixed Use',    pct: props.data.pct_mixed_use,   color: '#fd8d3c' },
  { key: 'agricultural',label: 'Agricultural', pct: props.data.pct_agricultural,color: '#fdbe85' },
  { key: 'residential', label: 'Residential',  pct: props.data.pct_residential, color: '#fff5eb' },
  { key: 'other',       label: 'Other',        pct: props.data.pct_other,       color: '#888' },
].filter(z => z.pct > 0))
</script>

<style scoped>
.sidebar-planning { display: flex; flex-direction: column; gap: 20px; }
.section__title { font-size: 11px; font-weight: 600; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }
.source-link { font-size: 10px; font-weight: 400; color: rgba(255,255,255,0.2); text-decoration: none; text-transform: none; letter-spacing: 0; flex-shrink: 0; }
.source-link:hover { color: rgba(255,255,255,0.6); }

.zoning-bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; gap: 1px; margin-bottom: 12px; }
.zoning-bar__segment { height: 100%; transition: width 0.4s; }
.zoning-legend { display: flex; flex-direction: column; gap: 5px; }
.zoning-legend__item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.zoning-legend__dot { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
.zoning-legend__label { flex: 1; color: rgba(255,255,255,0.6); }
.zoning-legend__pct { color: white; font-weight: 500; }

.app-row { padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
.app-row__header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 3px; }
.app-row__ref { font-size: 12px; font-family: monospace; color: rgba(255,255,255,0.7); }
.app-row__status { font-size: 10px; font-weight: 600; text-transform: uppercase; padding: 1px 6px; border-radius: 3px; }
.status--granted { background: rgba(74,198,121,0.2); color: #74c476; }
.status--refused { background: rgba(231,76,60,0.2); color: #e74c3c; }
.status--pending { background: rgba(253,141,60,0.2); color: #fd8d3c; }
.status--withdrawn { background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.4); }
.status--other { background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.4); }
.app-row__meta { font-size: 11px; color: rgba(255,255,255,0.35); }

.no-data { font-size: 12px; color: rgba(255,255,255,0.3); font-style: italic; padding: 6px 0; }
.kv-row { display: flex; justify-content: space-between; font-size: 13px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
.kv-row__label { color: rgba(255,255,255,0.5); }
.kv-row__value { color: white; font-weight: 500; }
.kv-row__unit { font-weight: 400; color: rgba(255,255,255,0.35); font-size: 11px; }
</style>
