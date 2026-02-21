/**
 * Tests for DataBar.vue
 * Uses @vue/test-utils + vitest.
 * useSuitabilityStore is mocked — no real Pinia or API calls.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import DataBar from './DataBar.vue'
import type { SortMeta, SortType } from '@/types'

// ── Mock data ─────────────────────────────────────────────────────

const MOCK_SORTS_META: SortMeta[] = [
  {
    key: 'overall',
    label: 'Overall',
    icon: 'BarChart3',
    description: 'Composite score',
    metrics: [{ key: 'score', label: 'Overall composite', unit: '0–100', isDefault: true }],
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
  {
    key: 'environment',
    label: 'Constraints',
    icon: 'ShieldAlert',
    description: 'Environmental constraints',
    metrics: [
      { key: 'score', label: 'Constraint score', unit: '0–100', isDefault: true },
      { key: 'designation_overlap', label: 'Designation severity', unit: '0–100', isDefault: false },
      { key: 'flood_risk', label: 'Flood risk', unit: '0–100', isDefault: false },
    ],
  },
  {
    key: 'cooling',
    label: 'Cooling',
    icon: 'Thermometer',
    description: 'Cooling conditions',
    metrics: [{ key: 'score', label: 'Cooling score', unit: '0–100', isDefault: true }],
  },
  {
    key: 'connectivity',
    label: 'Connectivity',
    icon: 'Globe',
    description: 'Connectivity',
    metrics: [{ key: 'score', label: 'Connectivity score', unit: '0–100', isDefault: true }],
  },
  {
    key: 'planning',
    label: 'Planning',
    icon: 'Map',
    description: 'Planning conditions',
    metrics: [{ key: 'score', label: 'Planning score', unit: '0–100', isDefault: true }],
  },
]

// ── Mock store ────────────────────────────────────────────────────
// vi.hoisted() ensures the object is created before vi.mock() runs (vi.mock is hoisted).

const mockStore = vi.hoisted(() => ({
  sortsMeta: [] as SortMeta[],
  activeSort: 'overall' as SortType,
  activeMetric: 'score',
  activeSortMeta: null as SortMeta | null,
  loading: false,
  setActiveSort: vi.fn<[SortType], Promise<void>>(),
  setActiveMetric: vi.fn<[string], Promise<void>>(),
}))

vi.mock('@/stores/suitability', () => ({
  useSuitabilityStore: () => mockStore,
}))

// ── Tests ─────────────────────────────────────────────────────────

describe('DataBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.sortsMeta = [...MOCK_SORTS_META]
    mockStore.activeSort = 'overall'
    mockStore.activeMetric = 'score'
    mockStore.activeSortMeta = MOCK_SORTS_META.find(s => s.key === 'overall') ?? null
    mockStore.loading = false
  })

  it('renders correct number of sort tabs (6) when sortsMeta is populated', () => {
    const wrapper = mount(DataBar)
    expect(wrapper.findAll('.sort-tab')).toHaveLength(6)
  })

  it('renders sort tab labels from sortsMeta', () => {
    const wrapper = mount(DataBar)
    const labels = wrapper.findAll('.sort-tab__label').map(el => el.text())
    expect(labels).toContain('Overall')
    expect(labels).toContain('Energy')
    expect(labels).toContain('Constraints')
    expect(labels).toContain('Cooling')
    expect(labels).toContain('Connectivity')
    expect(labels).toContain('Planning')
  })

  it('clicking a sort tab calls store.setActiveSort() with correct key', async () => {
    const wrapper = mount(DataBar)
    // index 1 = energy (activeSort is 'overall', so the guard won't skip)
    await wrapper.findAll('.sort-tab')[1].trigger('click')
    expect(mockStore.setActiveSort).toHaveBeenCalledWith('energy')
  })

  it('active sort tab has sort-tab--active class; others do not', () => {
    mockStore.activeSort = 'energy'
    const wrapper = mount(DataBar)
    const tabs = wrapper.findAll('.sort-tab')
    // energy is index 1
    expect(tabs[1].classes()).toContain('sort-tab--active')
    expect(tabs[0].classes()).not.toContain('sort-tab--active')
    expect(tabs[2].classes()).not.toContain('sort-tab--active')
  })

  it('secondary row renders metric pills for the active sort', () => {
    // energy has 3 metrics (score + wind_speed_100m + solar_ghi)
    mockStore.activeSort = 'energy'
    mockStore.activeSortMeta = MOCK_SORTS_META.find(s => s.key === 'energy') ?? null
    const wrapper = mount(DataBar)
    expect(wrapper.findAll('.metric-pill')).toHaveLength(3)
  })

  it('clicking a metric pill calls store.setActiveMetric() with correct key', async () => {
    mockStore.activeSort = 'energy'
    mockStore.activeSortMeta = MOCK_SORTS_META.find(s => s.key === 'energy') ?? null
    const wrapper = mount(DataBar)
    // index 1 = wind_speed_100m (activeMetric is 'score', so guard won't skip)
    await wrapper.findAll('.metric-pill')[1].trigger('click')
    expect(mockStore.setActiveMetric).toHaveBeenCalledWith('wind_speed_100m')
  })

  it('loading state renders 6 skeleton tabs when loading=true and sortsMeta=[]', () => {
    mockStore.loading = true
    mockStore.sortsMeta = []
    mockStore.activeSortMeta = null
    const wrapper = mount(DataBar)
    expect(wrapper.find('.databar-skeleton').exists()).toBe(true)
    expect(wrapper.findAll('.skeleton-tab')).toHaveLength(6)
  })

  it('does not render skeleton when sortsMeta is populated', () => {
    const wrapper = mount(DataBar)
    expect(wrapper.find('.databar-skeleton').exists()).toBe(false)
  })
})
