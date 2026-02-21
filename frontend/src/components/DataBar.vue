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
    </div>

    <!-- Secondary row: Sub-metric pills (active sort's metrics) -->
    <div class="databar-metrics" role="tablist" aria-label="Sub-metrics" v-if="activeMetrics.length">
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
}

function getIcon(iconName: string): Component {
  return ICON_MAP[iconName] ?? BarChart3
}

// ── Computed ──────────────────────────────────────────────────

/** Sub-metrics for the currently active sort, from sortsMeta */
const activeMetrics = computed(() =>
  store.activeSortMeta?.metrics ?? []
)

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
  background: #0f0f1a;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  user-select: none;
}

/* Primary row */
.databar-sorts {
  display: flex;
  gap: 2px;
  padding: 6px 12px 4px;
  overflow-x: auto;
  scrollbar-width: none;  /* hide scrollbar on mobile */
}

.databar-sorts::-webkit-scrollbar {
  display: none;
}

.sort-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: rgba(255, 255, 255, 0.6);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s;
}

.sort-tab:hover {
  background: rgba(255, 255, 255, 0.08);
  color: white;
}

.sort-tab--active {
  background: rgba(255, 255, 255, 0.15);
  color: white;
  font-weight: 700;
}

/* Secondary row */
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
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 99px;
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
  cursor: pointer;
  font-size: 11px;
  white-space: nowrap;
  transition: border-color 0.12s, color 0.12s, background 0.12s;
}

.metric-pill:hover {
  border-color: rgba(255, 255, 255, 0.5);
  color: rgba(255, 255, 255, 0.85);
}

.metric-pill--active {
  border-color: rgba(255, 255, 255, 0.7);
  background: rgba(255, 255, 255, 0.12);
  color: white;
}

.metric-pill__unit {
  font-size: 10px;
  opacity: 0.6;
}

/* Skeleton loading */
.databar-skeleton {
  display: flex;
  gap: 4px;
  padding: 6px 12px;
}

.skeleton-tab {
  width: 90px;
  height: 32px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.08);
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 0.8; }
}
</style>
