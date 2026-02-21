/**
 * Tests for MapLegend.vue
 * Uses @vue/test-utils + vitest.
 * useSuitabilityStore is mocked — no real Pinia or API calls.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import MapLegend from './MapLegend.vue'
import type { SortMeta, SortType, MetricMeta, MetricRange } from '@/types'

// ── Mock store ────────────────────────────────────────────────────

const mockStore = vi.hoisted(() => ({
  activeSort: 'overall' as SortType,
  activeMetric: 'score',
  activeSortMeta: {
    key: 'overall' as SortType,
    label: 'Overall',
    icon: 'BarChart3',
    description: '',
    metrics: [{ key: 'score', label: 'Overall Score', unit: '0–100', isDefault: true }],
  } as SortMeta | null,
  activeMetricMeta: {
    key: 'score',
    label: 'Overall Score',
    unit: '0–100',
    isDefault: true,
  } as MetricMeta | null,
  metricRange: null as MetricRange | null,
}))

vi.mock('@/stores/suitability', () => ({
  useSuitabilityStore: () => mockStore,
}))

// ── Helper ────────────────────────────────────────────────────────

function makeSortMeta(key: SortType): SortMeta {
  return { key, label: key, icon: '', description: '', metrics: [] }
}

// ── Tests ─────────────────────────────────────────────────────────

describe('MapLegend', () => {
  beforeEach(() => {
    mockStore.activeSort = 'overall'
    mockStore.activeMetric = 'score'
    mockStore.activeSortMeta = makeSortMeta('overall')
    mockStore.activeMetricMeta = { key: 'score', label: 'Overall Score', unit: '0–100', isDefault: true }
    mockStore.metricRange = null
  })

  it('gradient CSS contains correct hex colours for overall sort', () => {
    const wrapper = mount(MapLegend)
    const style = wrapper.find('.legend__bar').attributes('style') ?? ''
    // overall ramp: #f7fcf5 (low) → #74c476 (mid) → #00441b (high)
    expect(style).toContain('#f7fcf5')
    expect(style).toContain('#74c476')
    expect(style).toContain('#00441b')
  })

  it('gradient CSS reverses colour stops for temperature metric (dark blue at 0%)', () => {
    mockStore.activeSort = 'cooling'
    mockStore.activeMetric = 'temperature'
    mockStore.activeSortMeta = makeSortMeta('cooling')
    mockStore.activeMetricMeta = {
      key: 'temperature',
      label: 'Mean annual temperature',
      unit: '°C',
      isDefault: false,
    }
    const wrapper = mount(MapLegend)
    const style = wrapper.find('.legend__bar').attributes('style') ?? ''
    // Cooling ramp reversed: dark blue (#08306b) at 0%, light (#f7fbff) at 100%
    expect(style).toContain('#08306b 0%')
    expect(style).toContain('#f7fbff 100%')
    // The normal direction (light at 0%) must NOT appear
    expect(style).not.toContain('#f7fbff 0%')
  })

  it('isDiverging shows mid-point label only for environment sort', () => {
    const sorts: SortType[] = ['overall', 'energy', 'environment', 'cooling', 'connectivity', 'planning']
    for (const sort of sorts) {
      mockStore.activeSort = sort
      mockStore.activeSortMeta = makeSortMeta(sort)
      const wrapper = mount(MapLegend)
      if (sort === 'environment') {
        expect(wrapper.find('.legend__mid').exists(), `${sort} should show mid label`).toBe(true)
        expect(wrapper.find('.legend__mid').text()).toContain('Neutral')
      } else {
        expect(wrapper.find('.legend__mid').exists(), `${sort} should not show mid label`).toBe(false)
      }
    }
  })

  it('minLabel shows "0" for normalised metric (no metricRange)', () => {
    mockStore.activeMetric = 'score'
    mockStore.metricRange = null
    const wrapper = mount(MapLegend)
    expect(wrapper.find('.legend__min').text()).toBe('0')
  })

  it('minLabel shows actual min value for raw metric when metricRange is set', () => {
    mockStore.activeSort = 'energy'
    mockStore.activeMetric = 'wind_speed_100m'
    mockStore.activeSortMeta = makeSortMeta('energy')
    mockStore.activeMetricMeta = {
      key: 'wind_speed_100m',
      label: 'Wind speed at 100m',
      unit: 'm/s',
      isDefault: false,
    }
    mockStore.metricRange = { min: 3.2, max: 12.8, unit: 'm/s' }
    const wrapper = mount(MapLegend)
    expect(wrapper.find('.legend__min').text()).toBe('3.2 m/s')
    expect(wrapper.find('.legend__max').text()).toBe('12.8 m/s')
  })

  it('temperature note is shown only for temperature metric', () => {
    // Non-temperature: note absent
    mockStore.activeMetric = 'score'
    const wrapper = mount(MapLegend)
    expect(wrapper.find('.legend__note').exists()).toBe(false)
  })

  it('temperature note is shown when activeMetric is temperature', () => {
    mockStore.activeSort = 'cooling'
    mockStore.activeMetric = 'temperature'
    mockStore.activeSortMeta = makeSortMeta('cooling')
    mockStore.activeMetricMeta = {
      key: 'temperature',
      label: 'Mean annual temperature',
      unit: '°C',
      isDefault: false,
    }
    const wrapper = mount(MapLegend)
    expect(wrapper.find('.legend__note').exists()).toBe(true)
    expect(wrapper.find('.legend__note').text()).toContain('Lower = better')
  })
})
