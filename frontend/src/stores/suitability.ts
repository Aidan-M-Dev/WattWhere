/**
 * FILE: frontend/src/stores/suitability.ts
 * Role: Pinia store — SINGLE SOURCE OF TRUTH for all app state.
 *       Every component reads from and dispatches to this store.
 *       Components MUST NOT fetch data independently.
 * Agent boundary: Frontend — Store layer (§6.1, §10)
 * Dependencies: GET /api/sorts, GET /api/pins, GET /api/tile, GET /api/metric-range
 * Output: Reactive state consumed by MapView, DataBar, Sidebar, MapLegend
 * How to test: Open browser devtools → Vue tab → useSuitabilityStore()
 *
 * State transitions (from ARCHITECTURE.md §6.1):
 *   Sort change   → reset metric to 'score', fetch pins, update Martin URL, close sidebar
 *   Metric change → update Martin URL + legend only (NO pin refetch, NO sidebar close)
 *   Tile click    → fetch tile detail, open sidebar
 *   Close sidebar → clear selectedTile, close sidebar
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  SortType,
  SortMeta,
  TileData,
  MetricRange,
  SuitabilityState,
  CustomMetric,
} from '@/types'
import { FeatureCollection, Point } from 'geojson'
import { useToast } from '@/composables/useToast'

// ── API base URL ──────────────────────────────────────────────
// Proxied via Vite dev server (/api → http://api:8000)
const API_BASE = '/api'

export const useSuitabilityStore = defineStore('suitability', () => {
  const { push: pushToast } = useToast()

  // ── State ───────────────────────────────────────────────────

  const activeSort = ref<SortType>('overall')
  const activeMetric = ref<string>('score')
  const sortsMeta = ref<SortMeta[]>([])
  const selectedTileId = ref<number | null>(null)
  const selectedTileData = ref<TileData | null>(null)
  const sidebarOpen = ref<boolean>(false)
  const pins = ref<FeatureCollection<Point>>({ type: 'FeatureCollection', features: [] })
  const metricRange = ref<MetricRange | null>(null)
  const loading = ref<boolean>(false)
  const pinsLoading = ref<boolean>(false)
  const error = ref<string | null>(null)

  // ── Custom combination builder state ──────────────────────
  const customMetrics = ref<CustomMetric[]>([])
  const customBuilderOpen = ref(false)
  const customMetricsApplied = ref<CustomMetric[]>([])
  const customTileScores = ref<Record<string, number>>({})

  // ── Computed ────────────────────────────────────────────────

  /** Active sort's full metadata from sortsMeta */
  const activeSortMeta = computed(() =>
    sortsMeta.value.find(s => s.key === activeSort.value) ?? null
  )

  /** Active metric's full metadata */
  const activeMetricMeta = computed(() =>
    activeSortMeta.value?.metrics.find(m => m.key === activeMetric.value) ?? null
  )

  /** Flattened list of all sub-metrics across all sorts (excludes 'score' composites and 'overall') */
  const allAvailableMetrics = computed(() => {
    const result: Array<{ sort: string; sortLabel: string; metric: string; label: string; unit: string }> = []
    for (const s of sortsMeta.value) {
      if (s.key === 'overall' || s.key === 'custom') continue
      for (const m of s.metrics) {
        if (m.key === 'score') continue
        result.push({ sort: s.key, sortLabel: s.label, metric: m.key, label: m.label, unit: m.unit })
      }
    }
    return result
  })

  /**
   * Martin MVT tile URL — reactive, rebuilt whenever sort or metric changes.
   * Must be absolute: MapLibre fetches tiles inside a WebWorker which has no
   * base URL, so relative paths like /tiles/... fail to construct a Request.
   * window.location.origin gives http://localhost in dev, the real host in prod.
   */
  const martinTileUrl = computed(() => {
    if (activeSort.value === 'custom') {
      // Use overall/score for geometry — fill-color overridden by client-side blend
      return `${window.location.origin}/tiles/tile_heatmap/{z}/{x}/{y}?sort=overall&metric=score`
    }
    return `${window.location.origin}/tiles/tile_heatmap/{z}/{x}/{y}?sort=${activeSort.value}&metric=${activeMetric.value}`
  })

  // ── Actions ─────────────────────────────────────────────────

  /**
   * App initialisation — called once on mount in App.vue.
   * Fetches sort metadata and initial pins for the default sort (overall).
   */
  async function init() {
    await fetchSortsMeta()
    await fetchPins(activeSort.value)
  }

  /**
   * Fetch sort+metric metadata from GET /api/sorts.
   * Populates sortsMeta — DataBar is driven entirely by this.
   */
  async function fetchSortsMeta() {
    loading.value = true
    error.value = null
    try {
      const response = await fetch(`${API_BASE}/sorts`)
      if (!response.ok) throw new Error(`/api/sorts returned ${response.status}`)
      const data = await response.json()
      sortsMeta.value = data
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load sorts metadata'
      pushToast({ id: 'sorts-error', message: 'Failed to load sort metadata — API may be down', type: 'error' })
    } finally {
      loading.value = false
    }
  }

  /**
   * Fetch GeoJSON pins for a sort from GET /api/pins?sort={sort}.
   * Pins are sort-level — metric changes never trigger this.
   */
  async function fetchPins(sort: SortType) {
    pinsLoading.value = true
    try {
      const response = await fetch(`${API_BASE}/pins?sort=${sort}`)
      if (!response.ok) throw new Error(`/api/pins returned ${response.status}`)
      const data = await response.json()
      pins.value = data
    } catch (err) {
      console.error('Failed to fetch pins:', err)
      // Non-fatal — map still usable without pins
    } finally {
      pinsLoading.value = false
    }
  }

  /**
   * Fetch tile detail for sidebar from GET /api/tile/{id}?sort={sort}.
   * Opens the sidebar on success.
   */
  async function fetchTileDetail(tileId: number, sort: SortType) {
    loading.value = true
    error.value = null
    try {
      const response = await fetch(`${API_BASE}/tile/${tileId}?sort=${sort}`)
      if (!response.ok) throw new Error(`/api/tile returned ${response.status}`)
      const data = await response.json()
      selectedTileData.value = data
      sidebarOpen.value = true
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load tile data'
      pushToast({ id: 'tile-error', message: 'Failed to load tile detail — API may be down', type: 'error' })
      sidebarOpen.value = true  // still open sidebar to show error state
    } finally {
      loading.value = false
    }
  }

  /**
   * Fetch metric range for raw sub-metrics (legend min/max labels).
   * Called from MapLegend when activeMetric changes to a raw-value metric.
   * Normalised 0–100 metrics do not need this call.
   */
  async function fetchMetricRange(sort: SortType, metric: string) {
    try {
      const response = await fetch(`${API_BASE}/metric-range?sort=${sort}&metric=${metric}`)
      if (!response.ok) throw new Error(`/api/metric-range returned ${response.status}`)
      const data = await response.json()
      metricRange.value = data
    } catch (err) {
      console.error('Failed to fetch metric range:', err)
      metricRange.value = null
    }
  }

  // ── State transition actions ─────────────────────────────────

  /**
   * User selects a sort from DataBar (primary row).
   * Resets metric to 'score', refetches pins, updates Martin URL, clears tile.
   */
  async function setActiveSort(sort: SortType) {
    if (sort === activeSort.value) {
      // Clicking custom again toggles the builder panel
      if (sort === 'custom') customBuilderOpen.value = !customBuilderOpen.value
      return
    }
    activeSort.value = sort
    activeMetric.value = 'score'
    selectedTileId.value = null
    selectedTileData.value = null
    sidebarOpen.value = false
    metricRange.value = null

    if (sort === 'custom') {
      customBuilderOpen.value = true
      pins.value = { type: 'FeatureCollection', features: [] }
    } else {
      customBuilderOpen.value = false
      await fetchPins(sort)
    }
  }

  /**
   * User selects a sub-metric from DataBar (secondary row).
   * Updates Martin URL and legend ONLY. No pin refetch, no sidebar close.
   */
  async function setActiveMetric(metric: string) {
    if (metric === activeMetric.value) return
    activeMetric.value = metric
    // Fetch range only for raw sub-metrics (wind_speed_100m, solar_ghi, temperature, rainfall)
    const rawMetrics = ['wind_speed_100m', 'solar_ghi', 'temperature', 'rainfall']
    if (rawMetrics.includes(metric)) {
      await fetchMetricRange(activeSort.value, metric)
    } else {
      metricRange.value = null
    }
  }

  /**
   * User clicks a tile on the map.
   * Fetches tile detail, opens sidebar.
   * Custom mode uses /api/tile/{id}/all for all-sorts data.
   */
  async function setSelectedTile(tileId: number) {
    selectedTileId.value = tileId
    if (activeSort.value === 'custom') {
      await fetchTileDetailAll(tileId)
    } else {
      await fetchTileDetail(tileId, activeSort.value)
    }
  }

  /**
   * Fetch all-sorts tile data for custom mode sidebar.
   * Calls GET /api/tile/{id}/all — returns data from all 6 sorts.
   */
  async function fetchTileDetailAll(tileId: number) {
    loading.value = true
    error.value = null
    try {
      const response = await fetch(`${API_BASE}/tile/${tileId}/all`)
      if (!response.ok) throw new Error(`/api/tile/${tileId}/all returned ${response.status}`)
      const data = await response.json()
      selectedTileData.value = data
      sidebarOpen.value = true
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load tile data'
      pushToast({ id: 'tile-error', message: 'Failed to load tile detail', type: 'error' })
      sidebarOpen.value = true
    } finally {
      loading.value = false
    }
  }

  // ── Custom combination builder actions ────────────────────

  function addCustomMetric(item: { sort: string; metric: string; sortLabel: string; label: string; unit: string }) {
    if (customMetrics.value.some(cm => cm.sort === item.sort && cm.metric === item.metric)) return
    customMetrics.value.push({ ...item, weight: 50 })
  }

  function removeCustomMetric(sort: string, metric: string) {
    customMetrics.value = customMetrics.value.filter(cm => !(cm.sort === sort && cm.metric === metric))
  }

  function setCustomWeight(sort: string, metric: string, weight: number) {
    const cm = customMetrics.value.find(c => c.sort === sort && c.metric === metric)
    if (cm) cm.weight = weight
  }

  /** Copy working selection to applied state — triggers map recomputation */
  function applyCustomComposite() {
    customMetricsApplied.value = customMetrics.value.map(cm => ({ ...cm }))
  }

  /** Store normalised 0–100 scores per metric for the clicked tile (set by MapView) */
  function setCustomTileScores(values: Map<string, number>) {
    const obj: Record<string, number> = {}
    values.forEach((v, k) => { obj[k] = v })
    customTileScores.value = obj
  }

  /** User clicks close on sidebar or clicks empty map area. */
  function closeSidebar() {
    selectedTileId.value = null
    selectedTileData.value = null
    sidebarOpen.value = false
    error.value = null
  }

  /** Alias for closeSidebar — triggered on map empty-area click */
  function clearSelection() {
    closeSidebar()
  }

  return {
    // State (read-only refs — use actions to mutate)
    activeSort,
    activeMetric,
    sortsMeta,
    selectedTileId,
    selectedTileData,
    sidebarOpen,
    pins,
    metricRange,
    loading,
    pinsLoading,
    error,
    // Custom combination builder state
    customMetrics,
    customBuilderOpen,
    customMetricsApplied,
    customTileScores,
    // Computed
    activeSortMeta,
    activeMetricMeta,
    martinTileUrl,
    allAvailableMetrics,
    // Actions
    init,
    fetchSortsMeta,
    fetchPins,
    fetchTileDetail,
    fetchMetricRange,
    setActiveSort,
    setActiveMetric,
    setSelectedTile,
    closeSidebar,
    clearSelection,
    // Custom combination builder actions
    addCustomMetric,
    removeCustomMetric,
    setCustomWeight,
    applyCustomComposite,
    setCustomTileScores,
  }
})
