<!--
  FILE: frontend/src/components/SidebarCustom.vue
  Role: Sidebar content for Custom sort — shows selected metrics with raw values
        from the /api/tile/{id}/all response and normalised scores from vector tiles.
  Agent boundary: Frontend — Sidebar (Custom sort) (P2-21)
  Dependencies: useSuitabilityStore (customMetrics, customTileScores); :data prop from Sidebar.vue
  Output: Rendered custom metric detail panel
-->
<template>
  <div class="sidebar-custom">
    <section class="section" v-if="store.customMetrics.length">
      <h3 class="section__title">Custom Composite</h3>

      <!-- Weighted composite score -->
      <div class="composite-row" v-if="compositeScore !== null">
        <span class="composite-label">Weighted Score</span>
        <span class="composite-value">{{ compositeScore.toFixed(0) }}<span class="composite-unit">/100</span></span>
      </div>

      <!-- Selected metrics with values -->
      <div class="metric-list">
        <div
          v-for="cm in store.customMetrics"
          :key="`${cm.sort}-${cm.metric}`"
          class="metric-item"
        >
          <div class="metric-item__header">
            <span class="metric-item__sort">{{ cm.sortLabel }}</span>
            <span class="metric-item__label">{{ cm.label }}</span>
          </div>
          <div class="metric-item__values">
            <span class="metric-item__raw" v-if="getRawValue(cm.sort, cm.metric) !== null">
              {{ formatRaw(getRawValue(cm.sort, cm.metric)!, cm.unit) }}
            </span>
            <span class="metric-item__score" v-if="getNormScore(cm.sort, cm.metric) !== null">
              {{ getNormScore(cm.sort, cm.metric)!.toFixed(0) }}
            </span>
          </div>
          <div class="metric-item__bar">
            <div
              class="metric-item__bar-fill"
              :style="{ width: `${getNormScore(cm.sort, cm.metric) ?? 0}%` }"
            />
          </div>
        </div>
      </div>
    </section>

    <section class="section" v-else>
      <p class="empty-note">No custom metrics selected. Open the builder to configure.</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useSuitabilityStore } from '@/stores/suitability'

const store = useSuitabilityStore()

const props = defineProps<{ data: Record<string, any> }>()

/** Get the raw value from the all-sorts API response */
function getRawValue(sort: string, metric: string): number | null {
  const sortData = props.data?.[sort]
  if (!sortData) return null
  const val = sortData[metric]
  return val != null ? Number(val) : null
}

/** Get the normalised 0–100 score captured from vector tile sources on click */
function getNormScore(sort: string, metric: string): number | null {
  const key = `${sort}:${metric}`
  const val = store.customTileScores[key]
  return val != null ? val : null
}

/** Compute weighted composite from normalised scores */
const compositeScore = computed(() => {
  const metrics = store.customMetrics
  if (!metrics.length) return null
  const totalWeight = metrics.reduce((s, cm) => s + cm.weight, 0)
  if (totalWeight === 0) return null

  let weightedSum = 0
  let hasAny = false
  for (const cm of metrics) {
    const score = getNormScore(cm.sort, cm.metric)
    if (score != null) {
      weightedSum += score * (cm.weight / totalWeight)
      hasAny = true
    }
  }
  return hasAny ? weightedSum : null
})

function formatRaw(val: number, unit: string): string {
  if (unit === '°C') return `${val.toFixed(1)}${unit}`
  if (unit === 'm/s') return `${val.toFixed(1)} ${unit}`
  if (unit === 'kWh/m²/yr') return `${val.toFixed(0)} ${unit}`
  if (unit === 'mm/yr') return `${val.toFixed(0)} ${unit}`
  if (unit === '€/m²') return `€${val.toFixed(0)}/m²`
  if (unit === '0–100') return `${val.toFixed(0)}/100`
  return `${val.toFixed(1)} ${unit}`
}
</script>

<style scoped>
.sidebar-custom { display: flex; flex-direction: column; gap: 16px; }

.section__title {
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.4);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 10px;
}

.composite-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  margin-bottom: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.composite-label {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.6);
}

.composite-value {
  font-size: 22px;
  font-weight: 800;
  color: var(--color-accent);
}

.composite-unit {
  font-size: 12px;
  font-weight: 500;
  opacity: 0.6;
}

.metric-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.metric-item {
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.metric-item:last-child {
  border-bottom: none;
}

.metric-item__header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.metric-item__sort {
  font-size: 10px;
  font-weight: 600;
  color: var(--color-accent);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.metric-item__label {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.8);
}

.metric-item__values {
  display: flex;
  justify-content: space-between;
  margin-bottom: 4px;
}

.metric-item__raw {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.5);
}

.metric-item__score {
  font-size: 13px;
  font-weight: 600;
  color: white;
}

.metric-item__bar {
  height: 4px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  overflow: hidden;
}

.metric-item__bar-fill {
  height: 100%;
  background: var(--color-accent);
  border-radius: 2px;
  transition: width 0.4s ease;
}

.empty-note {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.25);
  font-style: italic;
}
</style>
