// @vitest-environment happy-dom

/**
 * Unit tests for the Sidebar component.
 *
 * Tests cover: visibility states (open/closed), loading skeleton, error + retry,
 * sort-specific sub-component delegation, and close button interaction.
 *
 * Uses Vitest + @vue/test-utils + Pinia testing helpers.
 * fetch() is mocked globally so no real HTTP requests are made.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { useSuitabilityStore } from '@/stores/suitability'
import Sidebar from '@/components/Sidebar.vue'
import SidebarOverall from '@/components/SidebarOverall.vue'
import SidebarEnergy from '@/components/SidebarEnergy.vue'
import type { TileOverall, TileEnergy } from '@/types'

// ── Mock lucide-vue-next ─────────────────────────────────────────
// Lucide icons are external components that don't need real rendering in tests.
// We replace them with simple stub <span> elements to avoid import errors.
vi.mock('lucide-vue-next', () => ({
  X: { template: '<span data-testid="icon-x" />' },
  AlertCircle: { template: '<span data-testid="icon-alert" />' },
  AlertTriangle: { template: '<span data-testid="icon-alert-triangle" />' },
  ShieldAlert: { template: '<span data-testid="icon-shield" />' },
}))

// ── Mock data ────────────────────────────────────────────────────

/** Minimal TileOverall matching the type shape for sidebar rendering */
const MOCK_TILE_OVERALL: TileOverall = {
  tile_id: 42,
  county: 'Dublin',
  grid_ref: 'O12',
  centroid: [-6.26, 53.35],
  score: 72,
  energy_score: 80,
  environment_score: 65,
  cooling_score: 55,
  connectivity_score: 90,
  planning_score: 40,
  has_hard_exclusion: false,
  exclusion_reason: null,
  nearest_data_centre_km: 5.2,
  weights: {
    energy: 0.25,
    environment: 0.20,
    cooling: 0.15,
    connectivity: 0.25,
    planning: 0.15,
  },
}

/** Minimal TileEnergy for testing energy sub-component delegation */
const MOCK_TILE_ENERGY: TileEnergy = {
  tile_id: 42,
  county: 'Dublin',
  grid_ref: 'O12',
  centroid: [-6.26, 53.35],
  score: 68,
  wind_speed_50m: 5.1,
  wind_speed_100m: 7.3,
  wind_speed_150m: 8.9,
  solar_ghi: 950,
  // grid_proximity moved to TileConnectivity (P2-22)
  nearest_transmission_line_km: 3.4,
  nearest_substation_km: 8.1,
  nearest_substation_name: 'Poolbeg',
  nearest_substation_voltage: '220kV',
  grid_low_confidence: false,
  renewable_score: 45,
  renewable_pct: 45.2,
  renewable_capacity_mw: 320,
  fossil_capacity_mw: 388,
}

// ── Helpers ──────────────────────────────────────────────────────

/**
 * Mounts the Sidebar component with a fresh Pinia store.
 * Returns both the wrapper (for DOM assertions) and the store (for state manipulation).
 *
 * Why `global.plugins = [pinia]`?
 * Vue Test Utils mounts components in isolation — they don't inherit the app-level
 * Pinia plugin. We must pass it explicitly so `useSuitabilityStore()` inside
 * Sidebar.vue connects to the same Pinia instance we're testing against.
 */
function mountSidebar() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSuitabilityStore()

  // Mock fetch to prevent real HTTP calls (e.g. from retry())
  vi.stubGlobal('fetch', vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(MOCK_TILE_OVERALL) })
  ))

  const wrapper = mount(Sidebar, {
    global: {
      plugins: [pinia],
    },
  })

  return { wrapper, store }
}

// ── Tests ────────────────────────────────────────────────────────

describe('Sidebar', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  // TEST 1: Sidebar closed by default
  it('is not visible when sidebarOpen = false', () => {
    // Default store state has sidebarOpen = false.
    // The sidebar <aside> should exist in DOM (v-show keeps it alive) but have width: 0
    // because the CSS class `sidebar--open` is not applied.
    const { wrapper } = mountSidebar()
    const aside = wrapper.find('aside.sidebar')
    expect(aside.exists()).toBe(true)
    // The `sidebar--open` class controls the 380px width via CSS.
    // Without it, the sidebar is 0-width (visually hidden).
    expect(aside.classes()).not.toContain('sidebar--open')
  })

  // TEST 2: Sidebar visible when open
  it('has sidebar--open class when sidebarOpen = true', () => {
    // When the store's sidebarOpen is true, the sidebar should get the
    // `sidebar--open` CSS class which triggers the width: 380px transition.
    const { wrapper, store } = mountSidebar()
    store.sidebarOpen = true
    // Vue reactivity needs a tick to propagate to the DOM
    return wrapper.vm.$nextTick().then(() => {
      const aside = wrapper.find('aside.sidebar')
      expect(aside.classes()).toContain('sidebar--open')
    })
  })

  // TEST 3: Loading state shows skeleton blocks
  it('shows skeleton blocks when loading = true and no data', () => {
    // When a tile is being fetched, the store sets loading=true and
    // selectedTileData remains null. The sidebar should show 4 skeleton
    // placeholder blocks (pulsing grey bars) as a loading indicator.
    const { wrapper, store } = mountSidebar()
    store.sidebarOpen = true
    store.loading = true
    store.selectedTileData = null

    return wrapper.vm.$nextTick().then(() => {
      const skeletons = wrapper.findAll('.skeleton-block')
      expect(skeletons.length).toBe(4)
    })
  })

  // TEST 4: Error state shows message + retry button
  it('shows error message and retry button when error is set', () => {
    // When the tile detail fetch fails, the store sets error to a string.
    // The sidebar should display that error message and a "Retry" button
    // that will re-attempt the fetch.
    const { wrapper, store } = mountSidebar()
    store.sidebarOpen = true
    store.error = 'Network error'
    store.selectedTileData = null

    return wrapper.vm.$nextTick().then(() => {
      const errorDiv = wrapper.find('.sidebar__error')
      expect(errorDiv.exists()).toBe(true)
      expect(errorDiv.text()).toContain('Network error')
      const retryBtn = wrapper.find('.retry-btn')
      expect(retryBtn.exists()).toBe(true)
      expect(retryBtn.text()).toBe('Retry')
    })
  })

  // TEST 5: SidebarOverall renders for 'overall' sort
  it('renders SidebarOverall when activeSort = overall and data is set', () => {
    // When activeSort is 'overall' and we have tile data, the sidebar
    // container should delegate rendering to the SidebarOverall sub-component.
    // This tests the v-if="store.activeSort === 'overall'" template logic.
    const { wrapper, store } = mountSidebar()
    store.sidebarOpen = true
    store.activeSort = 'overall'
    store.selectedTileData = MOCK_TILE_OVERALL

    return wrapper.vm.$nextTick().then(() => {
      expect(wrapper.findComponent(SidebarOverall).exists()).toBe(true)
    })
  })

  // TEST 6: SidebarEnergy renders for 'energy' sort
  it('renders SidebarEnergy when activeSort = energy', () => {
    // Same delegation pattern as above but for the energy sort.
    // The v-else-if="store.activeSort === 'energy'" branch should activate.
    const { wrapper, store } = mountSidebar()
    store.sidebarOpen = true
    store.activeSort = 'energy' as any
    store.selectedTileData = MOCK_TILE_ENERGY

    return wrapper.vm.$nextTick().then(() => {
      expect(wrapper.findComponent(SidebarEnergy).exists()).toBe(true)
    })
  })

  // TEST 7: Close button calls store.closeSidebar()
  it('calls store.closeSidebar() when close button is clicked', async () => {
    // The close button (X icon, top-right of sidebar header) should
    // dispatch closeSidebar() to the store, which clears the selection
    // and closes the sidebar panel.
    const { wrapper, store } = mountSidebar()
    store.sidebarOpen = true
    store.selectedTileId = 42
    store.selectedTileData = MOCK_TILE_OVERALL

    await wrapper.vm.$nextTick()

    // Spy on the store action so we can verify it was called
    const spy = vi.spyOn(store, 'closeSidebar')
    const closeBtn = wrapper.find('.sidebar__close')
    expect(closeBtn.exists()).toBe(true)

    await closeBtn.trigger('click')
    expect(spy).toHaveBeenCalledOnce()
  })
})
