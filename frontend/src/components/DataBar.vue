<!--
  FILE: frontend/src/components/DataBar.vue
  Role: Horizontal control bar — sort tabs (primary row) + sub-metric pills (secondary row).
        Content driven ENTIRELY by store.sortsMeta from GET /api/sorts — never hardcoded.
  Agent boundary: Frontend — DataBar (§6.3, §10)
  Dependencies:
    - useSuitabilityStore: sortsMeta (populated on init), activeSort, activeMetric
    - lucide-vue-next icons
  Output: Dispatches setActiveSort() and setActiveMetric() to store
  How to test: sortsMeta must be populated (init() called in App.vue)

  Primary row: one tab per sort (icon + label). Active sort: filled bg, bold.
  Secondary row: metric pills for active sort. First pill = 'score' (default).
  Mobile (<768px): primary row scrolls horizontally.

  ARCHITECTURE RULE: DataBar must never hardcode sort or metric lists.
  GET /api/sorts is the canonical source — sortsMeta drives rendering.
-->
<template>
  <nav class="databar">
    <!-- Primary row: Sort tabs -->
    <div class="databar-sorts" role="tablist" aria-label="Data sorts">
      <button
        v-for="sort in store.sortsMeta"
        :key="sort.key"
        class="sort-tab"
        :class="{ 'sort-tab--active': store.activeSort === sort.key }"
        role="tab"
        :aria-selected="store.activeSort === sort.key"
        :title="sort.description"
        @click="onSortClick(sort.key as SortType)"
      >
        <component :is="getIcon(sort.icon)" :size="16" class="sort-tab__icon" />
        <span class="sort-tab__label">{{ sort.label }}</span>
      </button>

      <!-- Custom combination tab (fixed, not from API) -->
      <button
        class="sort-tab"
        :class="{ 'sort-tab--active': store.activeSort === 'custom' }"
        role="tab"
        :aria-selected="store.activeSort === 'custom'"
        title="Build a custom metric combination"
        @click="onSortClick('custom' as SortType)"
      >
        <SlidersHorizontal :size="16" class="sort-tab__icon" />
        <span class="sort-tab__label">Custom</span>
      </button>
    </div>

    <!-- Secondary row: Sub-metric pills (active sort's metrics) -->
    <div class="databar-metrics" role="tablist" aria-label="Sub-metrics" v-if="activeMetrics.length && store.activeSort !== 'custom'">
      <button
        v-for="metric in activeMetrics"
        :key="metric.key"
        class="metric-pill"
        :class="{ 'metric-pill--active': store.activeMetric === metric.key }"
        role="tab"
        :aria-selected="store.activeMetric === metric.key"
        :title="`${metric.label} (${metric.unit})`"
        @click="onMetricClick(metric.key)"
      >
        {{ metric.label }}
        <span class="metric-pill__unit">{{ metric.unit }}</span>
      </button>
    </div>

    <!-- Loading state: skeleton tabs while sortsMeta loads -->
    <div v-if="store.loading && !store.sortsMeta.length" class="databar-skeleton">
      <div v-for="i in 6" :key="i" class="skeleton-tab" />
    </div>
  </nav>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  BarChart3, Zap, ShieldAlert, Thermometer, Globe, Map as MapIcon,
  SlidersHorizontal,
} from 'lucide-vue-next'
import type { Component } from 'vue'
import { useSuitabilityStore } from '@/stores/suitability'
import type { SortType } from '@/types'

const store = useSuitabilityStore()

// ── Icon map ──────────────────────────────────────────────────
// Maps Lucide icon name strings (from API) to imported components.
// Add new icons here if new sort types are added.
const ICON_MAP: Record<string, Component> = {
  BarChart3,
  Zap,
  ShieldAlert,
  Thermometer,
  Globe,
  Map: MapIcon,
  SlidersHorizontal,
}

function getIcon(iconName: string): Component {
  return ICON_MAP[iconName] ?? BarChart3
}

// ── Computed ──────────────────────────────────────────────────

/** Sub-metrics for the currently active sort, from sortsMeta.
 *  For Overall, hide the per-sort sub-score pills (visible in their own tabs). */
const activeMetrics = computed(() => {
  const metrics = store.activeSortMeta?.metrics ?? []
  if (store.activeSort === 'overall') {
    return metrics.filter(m => !m.key.endsWith('_score'))
  }
  return metrics
})

// ── Handlers ──────────────────────────────────────────────────

async function onSortClick(sort: SortType) {
  if (sort === store.activeSort) return
  // Store handles: reset metric, fetch pins, update Martin URL, close sidebar
  await store.setActiveSort(sort)
}

async function onMetricClick(metric: string) {
  if (metric === store.activeMetric) return
  // Store handles: update Martin URL + legend only
  await store.setActiveMetric(metric)
}
</script>

<style scoped>
.databar {
  background: var(--color-surface);
  background-image: var(--pattern-grid);
  border-top: 1px solid var(--color-border);
  user-select: none;
}

/* Primary row: square sort tiles */
.databar-sorts {
  display: flex;
  justify-content: flex-start;
  gap: 6px;
  padding: 8px 12px 4px;
  overflow-x: auto;
  scrollbar-width: none;
}

.databar-sorts::-webkit-scrollbar {
  display: none;
}

.sort-tab {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 8px 14px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  color: var(--color-text-muted);
  cursor: pointer;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.sort-tab:hover {
  background: color-mix(in srgb, var(--color-accent) 10%, var(--color-surface-2));
  color: var(--color-text);
}

.sort-tab--active {
  background: color-mix(in srgb, var(--color-accent) 15%, var(--color-surface-2));
  color: var(--color-accent);
  border-color: color-mix(in srgb, var(--color-accent) 40%, transparent);
  font-weight: 700;
}

.sort-tab__icon {
  flex-shrink: 0;
}

/* Secondary row: metric pills */
.databar-metrics {
  display: flex;
  gap: 6px;
  padding: 4px 12px 8px;
  overflow-x: auto;
  scrollbar-width: none;
}

.databar-metrics::-webkit-scrollbar {
  display: none;
}

.metric-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border: 1px solid var(--color-border);
  border-radius: 99px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  font-size: 11px;
  white-space: nowrap;
  transition: border-color 0.12s, color 0.12s, background 0.12s;
}

.metric-pill:hover {
  border-color: rgba(255, 255, 255, 0.3);
  color: var(--color-text);
}

.metric-pill--active {
  border-color: color-mix(in srgb, var(--color-accent) 60%, transparent);
  background: color-mix(in srgb, var(--color-accent) 12%, transparent);
  color: var(--color-accent);
}

.metric-pill__unit {
  font-size: 10px;
  opacity: 0.6;
}

/* Skeleton loading */
.databar-skeleton {
  display: flex;
  gap: 6px;
  padding: 8px 12px;
}

.skeleton-tab {
  width: 70px;
  height: 52px;
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 0.8; }
}
</style>
