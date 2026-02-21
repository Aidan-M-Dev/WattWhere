/**
 * Unit tests for the suitability Pinia store.
 *
 * Uses Vitest + Pinia's testing helpers. No DOM or component mounting needed —
 * the store is pure reactive state + async actions.
 *
 * fetch() is mocked globally so no real HTTP requests are made.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useSuitabilityStore } from './suitability'
import type { SortMeta } from '@/types'

// ── Mock data ──────────────────────────────────────────────────

/** Minimal sortsMeta matching the shape from GET /api/sorts */
const MOCK_SORTS_META: SortMeta[] = [
  {
    key: 'overall',
    label: 'Overall',
    icon: 'BarChart3',
    description: 'Composite score',
    metrics: [
      { key: 'score', label: 'Overall composite', unit: '0–100', isDefault: true },
      { key: 'energy_score', label: 'Energy sub-score', unit: '0–100', isDefault: false },
    ],
  },
  {
    key: 'energy',
    label: 'Energy',
    icon: 'Zap',
    description: 'Energy potential',
    metrics: [
      { key: 'score', label: 'Energy Score', unit: '0–100', isDefault: true },
      { key: 'wind_speed_100m', label: 'Wind speed at 100m', unit: 'm/s', isDefault: false },
      { key: 'solar_ghi', label: 'Solar irradiance', unit: 'kWh/m²/yr', isDefault: false },
    ],
  },
]

const MOCK_PINS = {
  type: 'FeatureCollection' as const,
  features: [
    {
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [-7.6, 53.4] },
      properties: { pin_id: 1, tile_id: 100, name: 'Test Pin', type: 'data_centre' },
    },
  ],
}

const MOCK_METRIC_RANGE = { min: 3.2, max: 12.8, unit: 'm/s' }

// ── Helper: mock fetch ─────────────────────────────────────────

/**
 * Creates a mock fetch that returns different data based on the URL.
 * vi.stubGlobal replaces the global fetch for the duration of each test.
 */
function mockFetch() {
  return vi.fn((url: string) => {
    if (url.includes('/sorts')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(MOCK_SORTS_META),
      })
    }
    if (url.includes('/pins')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(MOCK_PINS),
      })
    }
    if (url.includes('/tile/')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ tile_id: 42, county: 'Dublin', score: 72 }),
      })
    }
    if (url.includes('/metric-range')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(MOCK_METRIC_RANGE),
      })
    }
    return Promise.resolve({ ok: false, status: 404 })
  })
}

// ── Tests ──────────────────────────────────────────────────────

describe('useSuitabilityStore', () => {
  let store: ReturnType<typeof useSuitabilityStore>

  beforeEach(() => {
    // Create a fresh Pinia instance for each test so state doesn't leak between tests.
    // setActivePinia tells Pinia "use this instance" for any defineStore() calls.
    setActivePinia(createPinia())
    store = useSuitabilityStore()
    // Replace the global fetch with our mock
    vi.stubGlobal('fetch', mockFetch())
  })

  // ── Default state ──────────────────────────────────────────

  it('has correct initial state', () => {
    // The store should start with 'overall' sort, 'score' metric, no selection
    expect(store.activeSort).toBe('overall')
    expect(store.activeMetric).toBe('score')
    expect(store.selectedTileId).toBeNull()
    expect(store.sidebarOpen).toBe(false)
    expect(store.sortsMeta).toEqual([])
    expect(store.pins.features).toEqual([])
    expect(store.metricRange).toBeNull()
  })

  // ── martinTileUrl computed ─────────────────────────────────

  it('martinTileUrl contains sort=overall&metric=score initially', () => {
    // The computed URL template should reflect the current activeSort and activeMetric
    expect(store.martinTileUrl).toContain('sort=overall')
    expect(store.martinTileUrl).toContain('metric=score')
  })

  it('martinTileUrl updates after setActiveSort', async () => {
    // Changing the sort should update the URL to include the new sort key
    await store.setActiveSort('energy')
    expect(store.martinTileUrl).toContain('sort=energy')
    expect(store.martinTileUrl).toContain('metric=score')  // metric resets to 'score'
  })

  it('martinTileUrl updates after setActiveMetric', async () => {
    // Changing just the metric should update the URL metric param
    // but keep the sort unchanged
    await store.setActiveMetric('wind_speed_100m')
    expect(store.martinTileUrl).toContain('sort=overall')
    expect(store.martinTileUrl).toContain('metric=wind_speed_100m')
  })

  // ── setActiveSort ──────────────────────────────────────────

  it('setActiveSort resets metric, clears selection, and fetches pins', async () => {
    // Simulate having a selected tile and open sidebar
    store.selectedTileId = 42
    store.sidebarOpen = true
    store.activeMetric = 'wind_speed_100m'

    await store.setActiveSort('energy')

    // Sort should change
    expect(store.activeSort).toBe('energy')
    // Metric always resets to 'score' on sort change
    expect(store.activeMetric).toBe('score')
    // Selection and sidebar should be cleared
    expect(store.selectedTileId).toBeNull()
    expect(store.sidebarOpen).toBe(false)
    // fetch should have been called for pins with the new sort
    expect(fetch).toHaveBeenCalledWith('/api/pins?sort=energy')
  })

  it('setActiveSort is a no-op when same sort is selected', async () => {
    // Calling setActiveSort with the current sort should do nothing
    await store.setActiveSort('overall')
    // fetch should NOT have been called (no pin refetch needed)
    expect(fetch).not.toHaveBeenCalled()
  })

  // ── setActiveMetric ────────────────────────────────────────

  it('setActiveMetric updates metric without changing sort or sidebar', async () => {
    // Set up a selected tile and open sidebar
    store.selectedTileId = 42
    store.sidebarOpen = true

    await store.setActiveMetric('wind_speed_100m')

    // Metric should update
    expect(store.activeMetric).toBe('wind_speed_100m')
    // Sort should NOT change
    expect(store.activeSort).toBe('overall')
    // Sidebar and selection should NOT be affected
    expect(store.sidebarOpen).toBe(true)
    expect(store.selectedTileId).toBe(42)
  })

  it('setActiveMetric fetches metric range for raw sub-metrics', async () => {
    // wind_speed_100m is a "raw" metric — the store should fetch its min/max range
    await store.setActiveMetric('wind_speed_100m')
    expect(fetch).toHaveBeenCalledWith('/api/metric-range?sort=overall&metric=wind_speed_100m')
    expect(store.metricRange).toEqual(MOCK_METRIC_RANGE)
  })

  it('setActiveMetric clears metric range for normalised metrics', async () => {
    // First set a raw metric so metricRange is populated
    await store.setActiveMetric('wind_speed_100m')
    expect(store.metricRange).not.toBeNull()

    // Now switch to a normalised metric (energy_score is 0–100, not raw)
    await store.setActiveMetric('energy_score')
    // metricRange should be cleared since normalised metrics don't need it
    expect(store.metricRange).toBeNull()
  })

  it('setActiveMetric is a no-op when same metric is selected', async () => {
    await store.setActiveMetric('score')
    expect(fetch).not.toHaveBeenCalled()
  })

  // ── closeSidebar ───────────────────────────────────────────

  it('closeSidebar clears selection and closes sidebar', () => {
    store.selectedTileId = 42
    store.sidebarOpen = true
    store.error = 'some error'

    store.closeSidebar()

    expect(store.sidebarOpen).toBe(false)
    expect(store.selectedTileId).toBeNull()
    expect(store.selectedTileData).toBeNull()
    expect(store.error).toBeNull()
  })

  // ── init ───────────────────────────────────────────────────

  it('init fetches sorts metadata and initial pins', async () => {
    await store.init()

    // sortsMeta should be populated from the mock
    expect(store.sortsMeta).toEqual(MOCK_SORTS_META)
    // fetch should have been called for both sorts and pins
    expect(fetch).toHaveBeenCalledWith('/api/sorts')
    expect(fetch).toHaveBeenCalledWith('/api/pins?sort=overall')
  })

  // ── activeSortMeta computed ────────────────────────────────

  it('activeSortMeta returns the correct sort metadata after init', async () => {
    await store.init()

    // activeSortMeta is a computed that finds the current sort in sortsMeta
    expect(store.activeSortMeta).not.toBeNull()
    expect(store.activeSortMeta?.key).toBe('overall')
    expect(store.activeSortMeta?.metrics.length).toBe(2)
  })

  it('activeSortMeta returns null before init (sortsMeta empty)', () => {
    // Before init, sortsMeta is empty, so activeSortMeta should be null
    expect(store.activeSortMeta).toBeNull()
  })

  // ── setSelectedTile ────────────────────────────────────────

  it('setSelectedTile fetches tile detail and opens sidebar', async () => {
    await store.setSelectedTile(42)

    expect(store.selectedTileId).toBe(42)
    expect(fetch).toHaveBeenCalledWith('/api/tile/42?sort=overall')
    expect(store.sidebarOpen).toBe(true)
    expect(store.selectedTileData).toEqual({ tile_id: 42, county: 'Dublin', score: 72 })
  })
})
