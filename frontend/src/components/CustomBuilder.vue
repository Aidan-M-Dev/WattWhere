<!--
  FILE: frontend/src/components/CustomBuilder.vue
  Role: Right-side panel for building custom metric combinations.
        Users search across all sort metrics, select ones they care about,
        assign weights, and apply to see a live custom heatmap.
  Agent boundary: Frontend — Custom Builder (P2-21)
  Dependencies:
    - useSuitabilityStore: customMetrics, customBuilderOpen, allAvailableMetrics
  Output: Dispatches addCustomMetric, removeCustomMetric, setCustomWeight, applyCustomComposite
-->
<template>
  <aside class="custom-builder" :class="{ 'custom-builder--open': isOpen }">
    <div class="builder-content">
      <!-- Header -->
      <div class="builder-header">
        <div>
          <h2 class="builder-title">Custom Blend</h2>
          <span class="builder-subtitle">Select metrics and assign weights</span>
        </div>
        <button class="builder-close" aria-label="Close builder" @click="store.customBuilderOpen = false">
          <X :size="18" />
        </button>
      </div>

      <!-- Search -->
      <div class="builder-search-wrap">
        <Search :size="14" class="search-icon" />
        <input
          ref="searchInputEl"
          v-model="searchQuery"
          type="text"
          class="search-input"
          placeholder="Search metrics..."
          @focus="dropdownOpen = true"
        />
      </div>

      <!-- Search results dropdown -->
      <div class="search-dropdown" v-if="dropdownOpen && filteredMetrics.length">
        <button
          v-for="m in filteredMetrics"
          :key="`${m.sort}-${m.metric}`"
          class="dropdown-item"
          @click="onAdd(m)"
        >
          <span class="dropdown-sort">{{ m.sortLabel }}</span>
          <span class="dropdown-arrow">&rarr;</span>
          <span class="dropdown-label">{{ m.label }}</span>
        </button>
      </div>

      <!-- Selected metrics -->
      <div class="selected-section" v-if="store.customMetrics.length">
        <h3 class="section-label">Selected ({{ store.customMetrics.length }})</h3>
        <div class="selected-list">
          <div
            v-for="(cm, i) in store.customMetrics"
            :key="`${cm.sort}-${cm.metric}`"
            class="selected-card"
          >
            <div class="card-top">
              <div class="card-info">
                <span class="card-sort-badge">{{ cm.sortLabel }}</span>
                <span class="card-metric-name">{{ cm.label }}</span>
              </div>
              <button class="card-remove" aria-label="Remove metric" @click="store.removeCustomMetric(cm.sort, cm.metric)">
                <X :size="14" />
              </button>
            </div>
            <div class="card-slider-row">
              <input
                type="range"
                min="1"
                max="100"
                :value="cm.weight"
                class="weight-slider"
                @input="(e: Event) => store.setCustomWeight(cm.sort, cm.metric, Number((e.target as HTMLInputElement).value))"
              />
              <span class="weight-label">{{ effectiveWeights[i] }}%</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Empty state -->
      <div class="empty-state" v-else>
        <SlidersHorizontal :size="28" class="empty-icon" />
        <p class="empty-text">Add metrics from the search above to build your custom combination.</p>
      </div>
    </div>

    <!-- Footer with Apply button -->
    <div class="builder-footer" v-if="store.customMetrics.length">
      <button class="apply-btn" @click="onApply">
        Apply ({{ store.customMetrics.length }} metric{{ store.customMetrics.length > 1 ? 's' : '' }})
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { X, Search, SlidersHorizontal } from 'lucide-vue-next'
import { useSuitabilityStore } from '@/stores/suitability'

const store = useSuitabilityStore()

const searchQuery = ref('')
const dropdownOpen = ref(false)
const searchInputEl = ref<HTMLInputElement | null>(null)

const isOpen = computed(() => store.activeSort === 'custom' && store.customBuilderOpen)

// ── Filtered metrics (exclude already-selected) ─────────────

const filteredMetrics = computed(() => {
  const selected = new Set(store.customMetrics.map(cm => `${cm.sort}:${cm.metric}`))
  const available = store.allAvailableMetrics.filter(m => !selected.has(`${m.sort}:${m.metric}`))
  const q = searchQuery.value.toLowerCase().trim()
  if (!q) return available
  return available.filter(m => {
    const text = `${m.sortLabel} ${m.label} ${m.metric}`.toLowerCase()
    return text.includes(q)
  })
})

// ── Effective weights (normalised to 100%) ──────────────────

const effectiveWeights = computed(() => {
  const total = store.customMetrics.reduce((s, cm) => s + cm.weight, 0)
  if (total === 0) return store.customMetrics.map(() => 0)
  return store.customMetrics.map(cm => Math.round(cm.weight / total * 100))
})

// ── Handlers ────────────────────────────────────────────────

function onAdd(m: { sort: string; metric: string; sortLabel: string; label: string; unit: string }) {
  store.addCustomMetric(m)
  searchQuery.value = ''
  dropdownOpen.value = false
}

function onApply() {
  store.applyCustomComposite()
}

// Close dropdown when builder closes
watch(isOpen, (open) => {
  if (!open) {
    dropdownOpen.value = false
    searchQuery.value = ''
  }
})
</script>

<style scoped>
.custom-builder {
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

.custom-builder--open {
  width: 340px;
}

.builder-content {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* Header */
.builder-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.builder-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--color-text);
  margin: 0;
}

.builder-subtitle {
  font-size: 11px;
  color: var(--color-text-muted);
}

.builder-close {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 4px;
  border-radius: var(--radius-sm);
  transition: color 0.12s, background 0.12s;
}

.builder-close:hover {
  color: var(--color-text);
  background: rgba(255, 255, 255, 0.08);
}

/* Search */
.builder-search-wrap {
  position: relative;
  padding: 12px 16px;
  flex-shrink: 0;
}

.search-icon {
  position: absolute;
  left: 28px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--color-text-muted);
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 8px 12px 8px 32px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text);
  font-size: 13px;
  outline: none;
  transition: border-color 0.12s;
}

.search-input::placeholder {
  color: var(--color-text-muted);
}

.search-input:focus {
  border-color: var(--color-accent);
}

/* Search results dropdown */
.search-dropdown {
  max-height: 200px;
  overflow-y: auto;
  margin: 0 16px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 12px;
  background: none;
  border: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  color: var(--color-text);
  cursor: pointer;
  font-size: 12px;
  text-align: left;
  transition: background 0.1s;
}

.dropdown-item:hover {
  background: rgba(255, 255, 255, 0.06);
}

.dropdown-item:last-child {
  border-bottom: none;
}

.dropdown-sort {
  color: var(--color-accent);
  font-weight: 600;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.dropdown-arrow {
  color: var(--color-text-muted);
  font-size: 10px;
}

.dropdown-label {
  color: var(--color-text);
}

/* Selected metrics section */
.selected-section {
  padding: 0 16px;
  flex: 1;
}

.section-label {
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.4);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 10px;
}

.selected-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.selected-card {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--radius-md);
  padding: 10px 12px;
}

.card-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 8px;
}

.card-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.card-sort-badge {
  font-size: 10px;
  font-weight: 600;
  color: var(--color-accent);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.card-metric-name {
  font-size: 13px;
  color: var(--color-text);
}

.card-remove {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
  transition: color 0.12s, background 0.12s;
}

.card-remove:hover {
  color: #e74c3c;
  background: rgba(231, 76, 60, 0.15);
}

/* Weight slider */
.card-slider-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.weight-slider {
  flex: 1;
  height: 4px;
  -webkit-appearance: none;
  appearance: none;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  outline: none;
}

.weight-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  background: var(--color-accent);
  border-radius: 50%;
  cursor: pointer;
}

.weight-slider::-moz-range-thumb {
  width: 14px;
  height: 14px;
  background: var(--color-accent);
  border: none;
  border-radius: 50%;
  cursor: pointer;
}

.weight-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text);
  min-width: 36px;
  text-align: right;
}

/* Empty state */
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  gap: 12px;
}

.empty-icon {
  color: rgba(255, 255, 255, 0.15);
}

.empty-text {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.3);
  text-align: center;
  line-height: 1.5;
}

/* Footer */
.builder-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}

.apply-btn {
  width: 100%;
  padding: 10px;
  background: var(--color-accent);
  color: #000;
  border: none;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: opacity 0.15s;
}

.apply-btn:hover {
  opacity: 0.9;
}
</style>
