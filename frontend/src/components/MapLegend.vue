<!--
  FILE: frontend/src/components/MapLegend.vue
  Role: Map legend — gradient bar with min/max labels matching active ramp.
        Positioned fixed bottom-left inside MapView.vue.
  Agent boundary: Frontend — MapLegend (§6.5, §10)
  Dependencies:
    - useSuitabilityStore: activeSort, activeMetric, activeMetricMeta, metricRange
    - @/types: COLOR_RAMPS, TEMPERATURE_RAMP, ColorRamp
  Output: Visual legend; fetches metric-range from store (not directly from API)
  How to test: Switch sorts and sub-metrics — gradient + labels should update reactively

  Colour ramp per sort (ARCHITECTURE.md §5):
    overall      → Sequential green   #f7fcf5 → #00441b
    energy       → Sequential yellow-red  #ffffcc → #bd0026
    environment  → Diverging blue-orange  #d73027 → #ffffbf → #4575b4
    cooling      → Sequential blue   #f7fbff → #08306b
    connectivity → Sequential purple  #fcfbfd → #3f007d
    planning     → Sequential orange  #fff5eb → #7f2704

  temperature sub-metric: note "Lower = better for cooling" shown below bar.
  Raw sub-metrics (wind_speed_100m, solar_ghi, temperature, rainfall):
    → min/max from store.metricRange (fetched by store.setActiveMetric())
  Normalised 0–100: show "0" and "100"
-->
<template>
  <div class="legend" v-if="store.activeSortMeta">
    <!-- Metric label -->
    <div class="legend__label">{{ metricLabel }}</div>

    <!-- Gradient bar -->
    <div class="legend__gradient-row">
      <span class="legend__min">{{ minLabel }}</span>
      <div
        class="legend__bar"
        :style="{ background: gradientCss }"
        :title="`${minLabel} → ${maxLabel}`"
      />
      <span class="legend__max">{{ maxLabel }}</span>
    </div>

    <!-- Diverging midpoint label (environment sort) -->
    <div class="legend__mid" v-if="isDiverging">
      <span>Neutral</span>
    </div>

    <!-- Temperature inversion note -->
    <div class="legend__note" v-if="isTemperature">
      Lower = better for cooling
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useSuitabilityStore } from '@/stores/suitability'
import { COLOR_RAMPS } from '@/types'

const store = useSuitabilityStore()

// ── Computed ──────────────────────────────────────────────────

const isTemperature = computed(() => store.activeMetric === 'temperature')

const isDiverging = computed(() =>
  COLOR_RAMPS[store.activeSort]?.type === 'diverging'
)

const metricLabel = computed(() =>
  store.activeMetricMeta?.label ?? 'Score'
)

/** True for raw sub-metrics that have actual data ranges */
const isRawMetric = computed(() =>
  ['wind_speed_100m', 'solar_ghi', 'temperature', 'rainfall'].includes(store.activeMetric)
)

const unit = computed(() =>
  store.activeMetricMeta?.unit ?? '0–100'
)

const minLabel = computed(() => {
  if (isRawMetric.value && store.metricRange) {
    // For all raw metrics, left end = minimum raw value.
    // Temperature inverted: min °C = cold = dark blue = left = best for cooling.
    return `${store.metricRange.min.toFixed(1)} ${store.metricRange.unit}`
  }
  return '0'
})

const maxLabel = computed(() => {
  if (isRawMetric.value && store.metricRange) {
    // For all raw metrics, right end = maximum raw value.
    // Temperature inverted: max °C = warm = light = right = poor for cooling.
    return `${store.metricRange.max.toFixed(1)} ${store.metricRange.unit}`
  }
  return '100'
})

/** CSS linear-gradient from the active sort's colour ramp stops */
const gradientCss = computed(() => {
  const ramp = COLOR_RAMPS[store.activeSort]
  if (!ramp) return 'linear-gradient(to right, #ccc, #666)'

  // Temperature ramp: dark blue on left = cold = high cooling score.
  // Invert by keeping positions (0%, 50%, 100%) but reversing the colour order.
  // Simply reversing the stop array produces descending positions (invalid CSS).
  let stops: [number, string][]
  if (isTemperature.value) {
    const reversedColors = [...ramp.stops].map(([, c]) => c).reverse()
    stops = ramp.stops.map(([pos], i) => [pos, reversedColors[i]])
  } else {
    stops = ramp.stops
  }

  const parts = stops.map(([pct, color]) => `${color} ${pct}%`)
  return `linear-gradient(to right, ${parts.join(', ')})`
})
</script>

<style scoped>
.legend {
  background: rgba(15, 15, 26, 0.88);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  padding: 10px 14px;
  min-width: 200px;
  max-width: 240px;
  backdrop-filter: blur(4px);
}

.legend__label {
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.7);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.legend__gradient-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.legend__bar {
  flex: 1;
  height: 12px;
  border-radius: 3px;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.legend__min,
.legend__max {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.55);
  white-space: nowrap;
}

.legend__mid {
  text-align: center;
  margin-top: 2px;
}

.legend__mid span {
  font-size: 9px;
  color: rgba(255, 255, 255, 0.4);
}

.legend__note {
  margin-top: 4px;
  font-size: 10px;
  color: rgba(100, 200, 255, 0.8);
  font-style: italic;
}
</style>
