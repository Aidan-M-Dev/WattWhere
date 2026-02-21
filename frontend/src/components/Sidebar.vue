<!--
  FILE: frontend/src/components/Sidebar.vue
  Role: Slide-in panel — container for sort-specific sidebar sub-components.
        Never renders data directly — delegates to SidebarOverall, SidebarEnergy, etc.
  Agent boundary: Frontend — Sidebar container (§6.4, §10)
  Dependencies:
    - useSuitabilityStore: sidebarOpen, selectedTileData, activeSort, loading, error
    - SidebarOverall, SidebarEnergy, SidebarEnvironment, SidebarCooling,
      SidebarConnectivity, SidebarPlanning
  Output: Displays tile detail; dispatches closeSidebar() to store
  How to test: Click a tile → sidebar slides in with correct sort sub-component

  States: Closed | Loading (skeleton) | Open (data) | Error (inline + retry)
  Width: 380px desktop, full-width on mobile (<768px)
  Header: county name + grid_ref; close button top-right
  ARCHITECTURE RULE: One .vue file per sort — no generic conditional renderer.
-->
<template>
  <!-- Sidebar panel (v-show not v-if — keeps sub-component state alive) -->
  <aside
    class="sidebar"
    :class="{
      'sidebar--open': store.sidebarOpen,
      'sidebar--mobile': isMobile,
    }"
    role="complementary"
    aria-label="Tile detail panel"
  >
    <!-- Header -->
    <div class="sidebar__header">
      <div class="sidebar__header-text">
        <span class="sidebar__county">{{ tileBase?.county ?? '—' }}</span>
        <span class="sidebar__ref" v-if="tileBase?.grid_ref">{{ tileBase.grid_ref }}</span>
      </div>
      <button
        class="sidebar__close"
        aria-label="Close sidebar"
        @click="store.closeSidebar()"
      >
        <X :size="18" />
      </button>
    </div>

    <!-- Score ring (shown whenever tile data is loaded) -->
    <div class="sidebar__ring-wrap" v-if="store.selectedTileData && !store.loading">
      <svg class="sidebar__ring" viewBox="0 0 120 120" aria-hidden="true">
        <!-- Track -->
        <circle cx="60" cy="60" r="48" class="ring-track" />
        <!-- Filled arc: circumference = 2π×48 ≈ 301.6 -->
        <circle
          cx="60" cy="60" r="48"
          class="ring-fill"
          :stroke-dasharray="`${(tileBase?.score ?? 0) / 100 * 301.6} 301.6`"
          transform="rotate(-90 60 60)"
          stroke-linecap="round"
        />
      </svg>
      <span class="sidebar__ring-value">{{ (tileBase?.score ?? 0).toFixed(0) }}<span class="sidebar__ring-pct">%</span></span>
    </div>

    <!-- Body: Loading skeleton -->
    <div class="sidebar__body" v-if="store.loading && !store.selectedTileData">
      <div class="skeleton-block" v-for="i in 4" :key="i" />
    </div>

    <!-- Body: Error state -->
    <div class="sidebar__body sidebar__error" v-else-if="store.error">
      <AlertCircle :size="32" class="sidebar__error-icon" />
      <p>{{ store.error }}</p>
      <button class="retry-btn" @click="retry">Retry</button>
    </div>

    <!-- Body: Sort-specific sub-component (ARCHITECTURE.md §10 rule 8) -->
    <div class="sidebar__body" v-else-if="store.selectedTileData">
      <SidebarOverall
        v-if="store.activeSort === 'overall'"
        :data="store.selectedTileData as TileOverall"
      />
      <SidebarEnergy
        v-else-if="store.activeSort === 'energy'"
        :data="store.selectedTileData as TileEnergy"
      />
      <SidebarEnvironment
        v-else-if="store.activeSort === 'environment'"
        :data="store.selectedTileData as TileEnvironment"
      />
      <SidebarCooling
        v-else-if="store.activeSort === 'cooling'"
        :data="store.selectedTileData as TileCooling"
      />
      <SidebarConnectivity
        v-else-if="store.activeSort === 'connectivity'"
        :data="store.selectedTileData as TileConnectivity"
      />
      <SidebarPlanning
        v-else-if="store.activeSort === 'planning'"
        :data="store.selectedTileData as TilePlanning"
      />
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { X, AlertCircle } from 'lucide-vue-next'
import { useSuitabilityStore } from '@/stores/suitability'
import type {
  TileBase, TileOverall, TileEnergy, TileEnvironment,
  TileCooling, TileConnectivity, TilePlanning,
} from '@/types'
import SidebarOverall from '@/components/SidebarOverall.vue'
import SidebarEnergy from '@/components/SidebarEnergy.vue'
import SidebarEnvironment from '@/components/SidebarEnvironment.vue'
import SidebarCooling from '@/components/SidebarCooling.vue'
import SidebarConnectivity from '@/components/SidebarConnectivity.vue'
import SidebarPlanning from '@/components/SidebarPlanning.vue'

const store = useSuitabilityStore()

// ── Responsive ─────────────────────────────────────────────────
const isMobile = ref(window.innerWidth < 768)
const onResize = () => { isMobile.value = window.innerWidth < 768 }
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => window.removeEventListener('resize', onResize))

// ── Computed ───────────────────────────────────────────────────
const tileBase = computed(() => store.selectedTileData as TileBase | null)

// ── Actions ────────────────────────────────────────────────────
async function retry() {
  if (!store.selectedTileId) return
  await store.fetchTileDetail(store.selectedTileId, store.activeSort)
}
</script>

<style scoped>
.sidebar {
  width: 0;
  height: 100%;
  background: var(--color-surface);
  background-image: var(--pattern-grid);
  border-left: 1px solid var(--color-border);
  overflow: hidden;
  transition: width 0.25s ease;
  display: flex;
  flex-direction: column;
}

.sidebar--open {
  width: 380px;
}

/* Mobile: full width sheet */
@media (max-width: 768px) {
  .sidebar--open {
    width: 100vw;
  }
}

/* Header */
.sidebar__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.sidebar__header-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar__county {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text);
}

.sidebar__ref {
  font-size: 11px;
  color: var(--color-text-muted);
  font-family: monospace;
}

.sidebar__close {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 4px;
  border-radius: var(--radius-sm);
  transition: color 0.12s, background 0.12s;
  flex-shrink: 0;
}

.sidebar__close:hover {
  color: var(--color-text);
  background: rgba(255, 255, 255, 0.08);
}

/* Score ring */
.sidebar__ring-wrap {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px 0 12px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--color-border);
}

.sidebar__ring {
  width: 110px;
  height: 110px;
}

.ring-track {
  fill: none;
  stroke: rgba(255, 255, 255, 0.08);
  stroke-width: 10;
}

.ring-fill {
  fill: none;
  stroke: var(--color-accent);
  stroke-width: 10;
  transition: stroke-dasharray 0.5s ease;
}

.sidebar__ring-value {
  position: absolute;
  font-size: 28px;
  font-weight: 800;
  color: var(--color-accent);
  line-height: 1;
}

.sidebar__ring-pct {
  font-size: 14px;
  font-weight: 600;
  opacity: 0.7;
}

/* Body */
.sidebar__body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

/* Loading skeleton */
.skeleton-block {
  height: 60px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  margin-bottom: 12px;
  animation: pulse 1.4s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 0.8; }
}

/* Error state */
.sidebar__error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  text-align: center;
  color: rgba(255, 255, 255, 0.6);
}

.sidebar__error-icon {
  color: #e74c3c;
}

.retry-btn {
  padding: 8px 20px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 6px;
  background: transparent;
  color: white;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.12s;
}

.retry-btn:hover {
  background: rgba(255, 255, 255, 0.1);
}
</style>
