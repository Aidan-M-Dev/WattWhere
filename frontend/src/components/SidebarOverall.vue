<!--
  FILE: frontend/src/components/SidebarOverall.vue
  Role: Sidebar content for the Overall sort — composite score breakdown,
        strengths, limiting factors, exclusion status, nearest data centre.
  Agent boundary: Frontend — Sidebar (Overall sort) (§5.1, §6.4, §10)
  Dependencies: TileOverall interface from @/types; receives :data prop from Sidebar.vue
  Output: Rendered overall suitability detail panel
  How to test: Select Overall sort, click any tile — this component should render

  Displays (from ARCHITECTURE.md §5.1):
    - Overall score (0–100) with visual bar
    - Sub-score breakdown: energy (25%), connectivity (25%), environment (20%),
      cooling (15%), planning (15%) — weights from data.weights, not hardcoded
    - Top 3 contributing strengths (TODO: derive from sub-scores)
    - Top 3 limiting factors (TODO: derive from sub-scores)
    - Hard exclusion status (boolean + reason if applicable)
    - Distance to nearest existing data centre (km)
-->
<template>
  <div class="sidebar-overall">
    <!-- Hard exclusion banner -->
    <div class="exclusion-banner" v-if="data.has_hard_exclusion">
      <ShieldAlert :size="14" />
      Hard exclusion: {{ data.exclusion_reason ?? 'Protected area or flood zone overlap' }}
    </div>

    <!-- Sub-score breakdown -->
    <section class="section">
      <h3 class="section__title">Score Breakdown <a href="https://investigates.thejournal.ie/data-centres" target="_blank" rel="noopener" class="source-link">source ↗</a></h3>
      <div class="sub-scores">
        <div
          v-for="sub in subScores"
          :key="sub.key"
          class="sub-score-row"
        >
          <div class="sub-score-row__header">
            <span class="sub-score-row__label">{{ sub.label }}</span>
            <span class="sub-score-row__weight">{{ (sub.weight * 100).toFixed(0) }}%</span>
            <span class="sub-score-row__value">{{ sub.value?.toFixed(0) ?? '—' }}</span>
          </div>
          <div class="sub-score-bar">
            <div
              class="sub-score-bar__fill"
              :style="{ width: `${sub.value ?? 0}%`, background: sub.color }"
            />
          </div>
        </div>
      </div>
    </section>

    <!-- Nearest data centre -->
    <section class="section" v-if="data.nearest_data_centre_km !== null">
      <h3 class="section__title">Nearest Data Centre</h3>
      <div class="kv-row">
        <span class="kv-row__label">Distance</span>
        <span class="kv-row__value">{{ data.nearest_data_centre_km?.toFixed(1) }} km</span>
      </div>
    </section>

    <!-- TODO: Top 3 strengths + limiting factors
         Derive by sorting sub-scores and comparing to national medians.
         Requires additional data from API or compute client-side from sub-scores. -->
    <section class="section">
      <h3 class="section__title">Strengths & Constraints</h3>
      <p class="placeholder-note">TODO: implement top-3 strengths + limiting factors</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ShieldAlert } from 'lucide-vue-next'
import type { TileOverall } from '@/types'

const props = defineProps<{ data: TileOverall }>()

// ── Sub-score rows (each uses its own sort colour) ────────────
const subScores = computed(() => [
  {
    key: 'energy',
    label: 'Energy',
    value: props.data.energy_score,
    weight: props.data.weights?.energy ?? 0.25,
    color: '#fee000',
  },
  {
    key: 'connectivity',
    label: 'Connectivity',
    value: props.data.connectivity_score,
    weight: props.data.weights?.connectivity ?? 0.25,
    color: '#a78bfa',
  },
  {
    key: 'environment',
    label: 'Constraints',
    value: props.data.environment_score,
    weight: props.data.weights?.environment ?? 0.20,
    color: '#2cb549',
  },
  {
    key: 'cooling',
    label: 'Cooling',
    value: props.data.cooling_score,
    weight: props.data.weights?.cooling ?? 0.15,
    color: '#38bdf8',
  },
  {
    key: 'planning',
    label: 'Planning',
    value: props.data.planning_score,
    weight: props.data.weights?.planning ?? 0.15,
    color: '#fb923c',
  },
])
</script>

<style scoped>
.sidebar-overall { display: flex; flex-direction: column; gap: 20px; }

.exclusion-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(231, 76, 60, 0.15);
  border: 1px solid rgba(231, 76, 60, 0.4);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 12px;
  color: #e74c3c;
}

.section__title { font-size: 11px; font-weight: 600; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }
.source-link { font-size: 10px; font-weight: 400; color: rgba(255,255,255,0.2); text-decoration: none; text-transform: none; letter-spacing: 0; flex-shrink: 0; }
.source-link:hover { color: rgba(255,255,255,0.6); }

.sub-score-row { margin-bottom: 10px; }
.sub-score-row__header { display: flex; align-items: center; margin-bottom: 4px; }
.sub-score-row__label { flex: 1; font-size: 13px; color: rgba(255,255,255,0.8); }
.sub-score-row__weight { font-size: 10px; color: rgba(255,255,255,0.35); margin-right: 8px; }
.sub-score-row__value { font-size: 13px; font-weight: 600; color: white; }

.sub-score-bar { height: 4px; background: rgba(255,255,255,0.1); border-radius: 2px; overflow: hidden; }
.sub-score-bar__fill { height: 100%; border-radius: 2px; transition: width 0.4s ease; }

.kv-row { display: flex; justify-content: space-between; font-size: 13px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
.kv-row__label { color: rgba(255,255,255,0.5); }
.kv-row__value { color: white; font-weight: 500; }

.placeholder-note { font-size: 12px; color: rgba(255,255,255,0.25); font-style: italic; }
</style>
