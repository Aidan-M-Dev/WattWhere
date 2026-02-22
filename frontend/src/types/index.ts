/**
 * FILE: frontend/src/types/index.ts
 * Role: ALL TypeScript types and interfaces for the platform.
 *       This is the single source of type truth — import from here everywhere.
 * Agent boundary: Frontend — shared types (used by store, all components)
 * Dependencies: none (pure types)
 * Output: Type definitions consumed by store and all components
 * How to test: vue-tsc --noEmit (any type error surfaced project-wide)
 */

import type { FeatureCollection, Point, Feature } from 'geojson'

// ── Sort types ────────────────────────────────────────────────

/** The 6 thematic data sorts. Matches keys returned by GET /api/sorts */
export type SortType =
  | 'overall'
  | 'energy'
  | 'environment'
  | 'cooling'
  | 'connectivity'
  | 'planning'

/** A single sub-metric within a sort (e.g. wind_speed_100m in energy sort) */
export interface MetricMeta {
  key: string        // e.g. 'wind_speed_100m', 'score'
  label: string      // e.g. 'Wind speed at 100m'
  unit: string       // e.g. 'm/s', '0–100', 'kWh/m²/yr'
  isDefault: boolean // true for the 'score' (composite) metric
}

/** Full metadata for one sort — shape returned by GET /api/sorts items */
export interface SortMeta {
  key: SortType
  label: string       // e.g. 'Energy'
  icon: string        // Lucide icon name e.g. 'Zap'
  description: string
  metrics: MetricMeta[]
}

/** Colour ramp definition for each sort */
export interface ColorRamp {
  type: 'sequential' | 'diverging'
  stops: [number, string][]  // [[0, '#hex'], [50, '#hex'], [100, '#hex']]
  /** For temperature: true means lower raw value = higher display score */
  inverted?: boolean
}

// ── Pinia store state ─────────────────────────────────────────

/** Root Pinia store state shape */
export interface SuitabilityState {
  activeSort: SortType
  activeMetric: string              // 'score' by default; sort-specific key
  sortsMeta: SortMeta[]             // populated from GET /api/sorts on app init
  selectedTileId: number | null
  selectedTileData: TileData | null
  sidebarOpen: boolean
  pins: FeatureCollection<Point, PinProperties>
  metricRange: MetricRange | null   // for raw sub-metric legend display
  loading: boolean
  pinsLoading: boolean
  error: string | null
}

// ── Metric range (legend) ─────────────────────────────────────

/** Response from GET /api/metric-range?sort=X&metric=Y */
export interface MetricRange {
  min: number
  max: number
  unit: string
}

// ── Pin GeoJSON properties ────────────────────────────────────

/** Properties on every pin Feature (all sorts) */
export interface PinProperties {
  pin_id: number
  tile_id: number | null
  name: string
  /** Sort-specific type string e.g. 'wind_farm', 'data_centre', 'sac' */
  type: string
  [key: string]: unknown  // sort-specific additional fields
}

// ── Tile data (sidebar payload) ───────────────────────────────
// Each sort returns a different shape from GET /api/tile/{id}?sort=X

/** Fields common to all sort tile responses */
export interface TileBase {
  tile_id: number
  county: string
  grid_ref: string | null
  centroid: [number, number]  // [lng, lat]
  score: number               // composite sort score 0–100
}

export interface TileOverall extends TileBase {
  energy_score: number | null
  environment_score: number | null
  cooling_score: number | null
  connectivity_score: number | null
  planning_score: number | null
  has_hard_exclusion: boolean
  exclusion_reason: string | null
  nearest_data_centre_km: number | null
  weights: {
    energy: number
    environment: number
    cooling: number
    connectivity: number
    planning: number
  }
}

export interface TileEnergy extends TileBase {
  wind_speed_50m: number | null
  wind_speed_100m: number | null
  wind_speed_150m: number | null
  solar_ghi: number | null
  grid_proximity: number | null
  nearest_transmission_line_km: number | null
  nearest_substation_km: number | null
  nearest_substation_name: string | null
  nearest_substation_voltage: string | null
  grid_low_confidence: boolean
}

export interface TileEnvironment extends TileBase {
  designation_overlap: number | null
  flood_risk: number | null
  landslide_risk: number | null
  has_hard_exclusion: boolean
  exclusion_reason: string | null
  intersects_sac: boolean
  intersects_spa: boolean
  intersects_nha: boolean
  intersects_pnha: boolean
  intersects_current_flood: boolean
  intersects_future_flood: boolean
  landslide_susceptibility: 'none' | 'low' | 'medium' | 'high' | null
  /** List of protected areas intersecting this tile */
  designations: DesignationOverlap[]
}

export interface DesignationOverlap {
  designation_type: 'SAC' | 'SPA' | 'NHA' | 'pNHA'
  designation_name: string
  designation_id: string | null
  pct_overlap: number
}

export interface TileCooling extends TileBase {
  temperature: number | null          // °C raw
  water_proximity: number | null
  rainfall: number | null             // mm/yr raw
  aquifer_productivity: number | null
  free_cooling_hours: number | null
  nearest_waterbody_name: string | null
  nearest_waterbody_km: number | null
  nearest_hydrometric_station_name: string | null
  nearest_hydrometric_flow_m3s: number | null
  aquifer_productivity_rating: 'high' | 'moderate' | 'low' | 'negligible' | 'none' | null
}

export interface TileConnectivity extends TileBase {
  broadband: number | null
  ix_distance: number | null
  road_access: number | null
  inex_dublin_km: number | null
  inex_cork_km: number | null
  broadband_tier: string | null
  nearest_motorway_junction_km: number | null
  nearest_motorway_junction_name: string | null
  nearest_national_road_km: number | null
  nearest_rail_freight_km: number | null
}

export interface TilePlanning extends TileBase {
  zoning_tier: number | null
  planning_precedent: number | null
  pct_industrial: number
  pct_enterprise: number
  pct_mixed_use: number
  pct_agricultural: number
  pct_residential: number
  pct_other: number
  nearest_ida_site_km: number | null
  population_density_per_km2: number | null
  county_dev_plan_ref: string | null
  /** Planning applications within this tile */
  planning_applications: PlanningApplication[]
}

export interface PlanningApplication {
  app_ref: string
  name: string | null
  status: string
  app_date: string | null  // ISO date
  app_type: string | null
}

/** Union of all tile data shapes — the store holds this */
export type TileData =
  | TileOverall
  | TileEnergy
  | TileEnvironment
  | TileCooling
  | TileConnectivity
  | TilePlanning

// ── Colour ramp constants (used by MapView + MapLegend) ───────
// Defined here once — import in components, do not redefine per-component

// Dark-mode ramps: dark at 0 → accent colour at 100.
// Colours are pre-multiplied by 0.65 against black (previously fill-opacity was 0.45;
// bumped to 0.65 equivalent for slightly brighter appearance at full opacity).
export const COLOR_RAMPS: Record<SortType, ColorRamp> = {
  overall: {
    type: 'sequential',
    stops: [[0, '#070d0a'], [50, '#11461e'], [100, '#1d762f']],
  },
  energy: {
    type: 'sequential',
    stops: [[0, '#110e00'], [50, '#645600'], [100, '#a59200']],
  },
  environment: {
    type: 'diverging',
    stops: [[0, '#3b0700'], [50, '#261600'], [100, '#1d762f']],
  },
  cooling: {
    type: 'sequential',
    stops: [[0, '#000a11'], [50, '#073b5a'], [100, '#247ba1']],
  },
  connectivity: {
    type: 'sequential',
    stops: [[0, '#080711'], [50, '#3b2667'], [100, '#6d5aa3']],
  },
  planning: {
    type: 'sequential',
    stops: [[0, '#110700'], [50, '#643010'], [100, '#a35f27']],
  },
}

/** Temperature sub-metric uses inverted blue ramp: lower°C = better */
export const TEMPERATURE_RAMP: ColorRamp = {
  type: 'sequential',
  stops: [[0, '#a1a3a6'], [50, '#46718b'], [100, '#051f46']],
  inverted: true,
}
