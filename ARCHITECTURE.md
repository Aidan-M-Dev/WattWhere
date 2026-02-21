# Ireland Data Centre Suitability Platform — Architecture & Agent Guide

> **Purpose of this document:** This is the canonical reference for any Claude Code agent working on this project. Read this before writing any code. It defines the stack, architecture, every data sort, how components interact, and how each sort must be presented on the map.
>
> **For data sources, formats, URLs, and provider details**, see `ireland-data-sources.md` in the repository root. This document references those sources but does not duplicate them.

---

## 1. What This Platform Does

This is a map-based analytical tool for evaluating **data centre siting suitability** across Ireland. The country is divided into a grid of ~5 km² tiles. Each tile is scored across multiple dimensions — energy, connectivity, environmental constraints, etc. Users switch between these dimensions ("sorts") to see how different factors vary geographically, and can drill into individual sub-metrics within each sort to isolate specific signals. Clicking any tile reveals its detailed data in a sidebar.

---

## 2. Stack

| Concern | Technology | Role |
|---|---|---|
| Frontend framework | Vue 3 + Vite | SPA shell, state management, component structure |
| Map renderer | MapLibre GL JS (via `vue-maplibre-gl`) | All map rendering — choropleth fills, markers, interactions |
| Vector tile server | Martin | Serves MVT tiles directly from PostGIS |
| API server | FastAPI (Python) | Non-spatial queries, aggregations, metadata |
| Spatial database | PostgreSQL + PostGIS | All geospatial data storage and spatial queries |

**There is no Deck.gl in this project.** All visualisation uses MapLibre's native layer types. Deck.gl was deliberately excluded to keep the stack simple and the bundle small.

**Terminology note:** The tile colour layer is a **choropleth** (a `type: "fill"` layer in MapLibre terms, driven by a score property). Do not confuse this with MapLibre's `type: "heatmap"` layer, which is a point-density renderer. Throughout this document, "heatmap" means "the choropleth colour layer that reflects the active metric."

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Vue 3 Frontend                      │
│                                                      │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ DataBar  │  │  MapView     │  │  Sidebar      │  │
│  │(sort +   │  │  (MapLibre)  │  │  (tile data)  │  │
│  │ metric)  │  └──────┬───────┘  └───────┬───────┘  │
│  └────┬─────┘         │                  │           │
│       │          MapLegend               │           │
│       └───────┬───────┴──────────────────┘           │
│               │                                      │
│         Pinia Store                                   │
│   (activeSort, activeMetric, selectedTile, ...)      │
└───────────────┬──────────────────────────────────────┘
                │
        ┌───────┴────────┐
        │                │
   ┌────▼─────┐    ┌─────▼──────┐
   │  Martin  │    │  FastAPI   │
   │  :3000   │    │  :8000     │
   │ (MVT     │    │ (JSON API) │
   │  tiles)  │    │            │
   └────┬─────┘    └─────┬──────┘
        │                │
        └───────┬────────┘
                │
        ┌───────▼────────┐
        │   PostgreSQL   │
        │   + PostGIS    │
        └────────────────┘
```

### Request flow

1. **Map tiles (geometry + choropleth colour):** Vue/MapLibre → Martin → PostGIS. Martin serves a function source accepting `sort` and `metric` query parameters to control which column drives tile fill colour.
2. **Marker/pin data:** Vue → FastAPI `GET /api/pins?sort={sort}` → PostGIS. Returns GeoJSON FeatureCollection of point features relevant to the active sort. Pins are determined by sort only, not by sub-metric.
3. **Tile detail (sidebar):** Vue → FastAPI `GET /api/tile/{tile_id}?sort={sort}` → PostGIS. Returns the full data payload for a single tile.

---

## 4. The Grid: 5 km² Tile System

Ireland is covered by a **pre-computed grid of ~5 km² square tiles**. A 5 km² square has sides of ~2.236 km.

Generate the grid in **EPSG:2157** (Irish Transverse Mercator — the projection used by almost all Irish government spatial data, per the data sources doc), then store in **EPSG:4326** for web serving. Clip to the national boundary so only tiles whose centroid falls within Ireland are retained.

The `tiles` table stores the grid geometry, centroid, and county assignment. Each sort has its own table keyed on `tile_id`, and each sort's pins have a separate point table. Schema details are generated separately — the key constraint is that every sort table must foreign-key to `tiles(tile_id)` and every pin table must include a `geom GEOMETRY(Point, 4326)` column and a `tile_id` reference.

**`tile_id` format:** Integer primary key, auto-assigned during grid generation. This is the canonical identifier used in all API routes (`/api/tile/12345`) and in the frontend store. Do not use lat/lng pairs or grid coordinates as the public-facing tile identifier.

---

## 5. Data Sorts

A **sort** is a thematic data layer. Selecting a sort controls three things simultaneously:
1. What colour each tile gets on the map (choropleth fill, driven by the active metric)
2. What pins/markers appear
3. What data the sidebar shows when a tile is clicked

Each sort exposes a set of **sub-metrics** — the individual signals that make up its composite score. Any sub-metric can be selected to independently drive the choropleth fill. **Switching sub-metric updates tile colours and the legend only; it does not refetch pins or clear the selected tile.**

The `GET /api/sorts` endpoint returns sub-metric metadata for each sort so the frontend DataBar can be built dynamically (see §7).

---

### 5.1 Sort: Overall Suitability

**What it measures:** Composite weighted score combining all other sorts into a single "how suitable is this tile for a data centre" answer.

**Heatmap:**
- **Metric:** Composite suitability score (0–100).
- **Colour ramp:** Sequential green. `#f7fcf5` (unsuitable) → `#00441b` (highly suitable).
- **Normalisation:** Min-max across all tiles.
- **Score composition:** Weighted sum of the individual sort scores. Suggested initial weights: Energy 25%, Connectivity 25%, Environmental Constraints 20%, Cooling & Water 15%, Planning & Zoning 15%. These weights should be configurable in the backend, not hardcoded in the frontend.
- **Tiles within exclusion zones** (protected areas, high-probability flood zones) receive a hard score of 0 regardless of other factors.

**Sub-metric heatmap options:**

| Key | Label | Unit | Colour ramp |
|---|---|---|---|
| `score` | Overall composite (default) | 0–100 | Green (this sort's ramp) |
| `energy_score` | Energy sub-score | 0–100 | Yellow-red (§5.2 ramp) |
| `environment_score` | Constraints sub-score | 0–100 | Diverging blue-orange (§5.3 ramp) |
| `cooling_score` | Cooling sub-score | 0–100 | Blue (§5.4 ramp) |
| `connectivity_score` | Connectivity sub-score | 0–100 | Purple (§5.5 ramp) |
| `planning_score` | Planning sub-score | 0–100 | Orange (§5.6 ramp) |

When a sub-sort metric is selected here, that sort's colour ramp applies. Pins remain the Overall sort's pins.

**Pins:**
| Pin type | Icon | Source (see data sources doc) |
|---|---|---|
| Existing data centre | Server icon | The Journal Investigates / DataCenterMap.com |
| IDA industrial site | Building icon | IDA Ireland (manual entry — no GIS download exists) |

**Sidebar:**
- Overall score with breakdown showing each sub-score and its weight
- Top 3 contributing strengths for this tile
- Top 3 limiting factors for this tile
- Whether any hard exclusion applies (and which one)
- Distance to nearest existing data centre

---

### 5.2 Sort: Energy Potential

**What it measures:** Renewable energy generation potential and proximity to grid infrastructure.

**Heatmap:**
- **Metric:** Energy potential score (0–100). Weighted: 35% wind speed at 100m, 30% solar GHI, 35% distance to nearest transmission line/substation (inverse — closer is better).
- **Colour ramp:** Sequential yellow-red. `#ffffcc` (low potential) → `#bd0026` (high potential).
- **Normalisation:** Min-max per sub-metric, then weighted combination.

**Sub-metric heatmap options:**

| Key | Label | Unit | Notes |
|---|---|---|---|
| `score` | Energy composite (default) | 0–100 | Yellow-red ramp |
| `wind_speed_100m` | Wind speed at 100m | m/s (raw; legend shows m/s) | Min-max normalised for colour. Yellow-red ramp. |
| `solar_ghi` | Solar irradiance (GHI) | kWh/m²/yr (raw; legend shows kWh/m²/yr) | Min-max normalised for colour. Yellow-red ramp. |
| `grid_proximity` | Grid proximity score | 0–100 (inverse distance, pre-normalised) | Yellow-red ramp. |

**Pins:**
| Pin type | Icon | Source doc reference |
|---|---|---|
| Existing wind farm | Turbine icon | §2 — SEAI Wind Farms Locations |
| Transmission line node | Zap icon | §3 — OSM power infrastructure (`power=substation`, `power=line`) |
| EirGrid substation | Circle-dot icon | §3 — OSM power infrastructure |

**Sidebar:**
- Energy score with sub-score breakdown
- Mean wind speed at 50m, 100m, 150m (from Global Wind Atlas / SEAI grids)
- Global Horizontal Irradiance — GHI (kWh/m²/year) from Global Solar Atlas
- Distance to nearest transmission line (km) and substation (km)
- Nearest substation name and voltage level (if available from OSM tags)
- EirGrid real-time generation context (link to Smart Grid Dashboard, not embedded data)

**Key data note:** Grid infrastructure is the weakest data layer (see §3 of data sources doc). OSM `power=*` tags are the best available option but will have gaps. Flag tiles where the nearest detected infrastructure is >20 km as low-confidence.

---

### 5.3 Sort: Environmental Constraints

**What it measures:** Degree to which environmental and natural hazard designations restrict or prohibit development.

**Heatmap:**
- **Metric:** Constraint severity score. Inverted so that **high values = fewer constraints = more suitable**. Score 0–100 where 100 = no constraints, 0 = hard exclusion zone.
- **Colour ramp:** Diverging blue-orange. `#d73027` (heavily constrained / excluded) → `#ffffbf` (moderate) → `#4575b4` (unconstrained). **Note:** A blue-orange diverging ramp is used here rather than red-green to ensure accessibility for red-green colourblind users (~8% of men). Do not revert to a red-green ramp.
- **Normalisation:** Fixed scale 0–100.
- **Scoring logic:**
  - Tile overlaps SAC/SPA: hard exclusion → score 0
  - Tile overlaps NHA/pNHA: heavy penalty → score capped at 20
  - Tile overlaps current flood extent (NIFM): hard exclusion → score 0
  - Tile overlaps future flood extent: penalty → score capped at 40
  - Tile overlaps landslide susceptibility zone (medium/high): penalty → −30 points
  - No overlaps: score 100

**Sub-metric heatmap options:**

| Key | Label | Unit | Notes |
|---|---|---|---|
| `score` | Constraint composite (default) | 0–100 | Diverging blue-orange ramp |
| `designation_overlap` | Designation severity | 0–100 (100 = no protected area overlap) | Diverging blue-orange ramp |
| `flood_risk` | Flood risk (inverted) | 0–100 (100 = no flood risk) | Diverging blue-orange ramp |
| `landslide_risk` | Landslide risk (inverted) | 0–100 (100 = no susceptibility) | Diverging blue-orange ramp |

**Pins:**
| Pin type | Icon | Source doc reference |
|---|---|---|
| SAC boundary centroid | Shield icon | §4 — NPWS designated site data |
| SPA boundary centroid | Bird icon | §4 — NPWS |
| NHA/pNHA centroid | Tree icon | §4 — NPWS |
| Flood zone indicator | Water icon | §10 — OPW NIFM flood extents |

**Sidebar:**
- Constraint score
- List of designated areas intersecting the tile (name, type, % of tile covered)
- Flood risk: whether tile intersects current or future flood extents, which flood map zones
- Landslide susceptibility rating for the tile
- Explicit statement if any hard exclusion applies
- Link to OPW flood viewer for the tile area

**Licensing note:** OPW flood data is CC BY-NC-ND (see §10 of data sources). Fine for non-commercial use; flag for any commercial deployment.

---

### 5.4 Sort: Cooling & Climate

**What it measures:** Suitability of the local climate and water resources for data centre cooling.

**Heatmap:**
- **Metric:** Cooling suitability score (0–100). Weighted: 40% mean annual temperature (lower = better, supports free cooling), 35% proximity to water source (river/lake), 25% annual rainfall (higher = better for evaporative supplementary cooling).
- **Colour ramp:** Sequential blue. `#f7fbff` (poor cooling conditions) → `#08306b` (excellent).
- **Normalisation:** Min-max per sub-metric.

**Sub-metric heatmap options:**

| Key | Label | Unit | Notes |
|---|---|---|---|
| `score` | Cooling composite (default) | 0–100 | Blue ramp |
| `temperature` | Mean annual temperature | °C raw (legend shows °C) | **Ramp inverted:** lower temperature (better for cooling) maps to the dark/high-score end. `#08306b` (~5°C, excellent) → `#f7fbff` (~12°C, poor). Min-max normalised. |
| `water_proximity` | Water proximity score | 0–100 | Blue ramp |
| `rainfall` | Annual rainfall | mm/yr raw (legend shows mm) | Min-max normalised for colour. Blue ramp. |
| `aquifer_productivity` | Aquifer productivity | 0–100 | Blue ramp |

**Pins:**
| Pin type | Icon | Source doc reference |
|---|---|---|
| OPW hydrometric station | Gauge icon | §6 — OPW waterlevel.ie |
| Major river/lake | Droplet icon | §6 — EPA river network & WFD waterbodies |
| Met Éireann synoptic station | Thermometer icon | §7 — Met Éireann |

**Sidebar:**
- Cooling score with sub-score breakdown
- Mean annual temperature (°C) — from Met Éireann 1 km grid
- Number of free-cooling hours estimate (hours/year below 18°C, derived from MÉRA reanalysis if available, otherwise from gridded monthly data)
- Distance to nearest significant waterbody (km) and name
- Nearest hydrometric station: mean flow rate (m³/s) if available
- Annual rainfall (mm) from Met Éireann grid
- Aquifer productivity rating from GSI bedrock maps (if the tile sits on a productive aquifer, note it — relevant for groundwater cooling)

---

### 5.5 Sort: Connectivity & Transport

**What it measures:** Digital connectivity, internet exchange proximity, and physical transport access.

**Heatmap:**
- **Metric:** Connectivity score (0–100). Weighted: 35% broadband coverage quality (from ComReg), 30% distance to nearest internet exchange point (Dublin INEX, Cork), 20% distance to nearest motorway/national primary road, 15% distance to nearest rail freight terminal.
- **Colour ramp:** Sequential purple. `#fcfbfd` (poor connectivity) → `#3f007d` (excellent).
- **Normalisation:** Min-max per sub-metric. Distance metrics use inverse scaling with diminishing returns (log transform) since going from 5 km to 10 km from an IX matters far more than 200 km to 205 km.

**Sub-metric heatmap options:**

| Key | Label | Unit | Notes |
|---|---|---|---|
| `score` | Connectivity composite (default) | 0–100 | Purple ramp |
| `broadband` | Broadband coverage quality | 0–100 | Purple ramp |
| `ix_distance` | IX distance score | 0–100 (inverse log-distance, pre-normalised) | Purple ramp |
| `road_access` | Road access score | 0–100 (inverse distance, pre-normalised) | Purple ramp |

**Pins:**
| Pin type | Icon | Source doc reference |
|---|---|---|
| Internet exchange point | Globe icon | §8 — PeeringDB (INEX Dublin, INEX Cork) |
| Motorway junction | CircleDot icon | §8 — TII national roads / OSM |
| ComReg high-speed broadband area | Wifi icon | §8 — ComReg data map hub |

**Sidebar:**
- Connectivity score with sub-score breakdown
- Distance to INEX Dublin (km) and INEX Cork (km)
- ComReg broadband coverage tier for the tile area
- Distance to nearest motorway junction (km) and road name
- Distance to nearest national primary road (km)
- Distance to nearest rail station with freight capacity (km)
- Note on fibre: no public GIS fibre route data exists (see §8 of data sources). ComReg broadband coverage is used as the best proxy.

---

### 5.6 Sort: Planning & Zoning

**What it measures:** How favourable the local planning and zoning context is for data centre development.

**Heatmap:**
- **Metric:** Planning suitability score (0–100). Based primarily on zoning designation overlap.
- **Colour ramp:** Sequential orange. `#fff5eb` (unfavourable zoning) → `#7f2704` (ideal zoning).
- **Scoring logic:**
  - Tile contains Industrial/Enterprise zoned land (GZT): base score 80–100 depending on % coverage
  - Tile contains Mixed Use zoned land: base score 50–70
  - Tile is unzoned / agricultural: base score 10–30
  - Tile is Residential zoned: score capped at 10
  - Bonus +10 if existing data centre planning applications within 10 km (signals precedent)
  - Penalty −20 if tile is within 500m of Residential zoning (noise/objection risk)

**Sub-metric heatmap options:**

| Key | Label | Unit | Notes |
|---|---|---|---|
| `score` | Planning composite (default) | 0–100 | Orange ramp |
| `zoning_tier` | Zoning tier score | 0–100 (based on best zoning category present) | Orange ramp |
| `planning_precedent` | Planning precedent score | 0–100 (proximity to previous data centre applications) | Orange ramp |

**Pins:**
| Pin type | Icon | Source doc reference |
|---|---|---|
| Industrial/Enterprise zoned parcel | Factory icon | §9 — MyPlan GZT Development Plan Zoning |
| Recent data centre planning application | FileText icon | §9 — National Planning Applications |
| IDA property | Building icon | §9 — IDA Ireland (manual, no GIS) |

**Sidebar:**
- Planning score
- Zoning breakdown: % of tile under each zoning category (Industrial, Enterprise, Mixed Use, Agricultural, Residential, Other)
- List of relevant planning applications within the tile (application ref, status, date, type)
- Nearest IDA industrial site (name, distance)
- Population density of the tile and surrounding area (from CSO Small Area stats, §5 of data sources) — relevant for planning objection risk and workforce availability
- County Development Plan reference

---

## 6. Frontend Components — Specification

### 6.1 Pinia Store: `useSuitabilityStore`

Single source of truth. Every component reads from and writes to this store.

```typescript
type SortType =
  | 'overall'
  | 'energy'
  | 'environment'
  | 'cooling'
  | 'connectivity'
  | 'planning';

interface SortMeta {
  key: SortType;
  label: string;
  icon: string;
  description: string;
  metrics: MetricMeta[];
}

interface MetricMeta {
  key: string;      // e.g. 'score', 'wind_speed_100m'
  label: string;    // e.g. 'Wind speed at 100m'
  unit: string;     // e.g. 'm/s', '0–100'
  isDefault: boolean;
}

interface SuitabilityState {
  activeSort: SortType;
  activeMetric: string;           // 'score' by default; sort-specific key
  sortsMeta: SortMeta[];          // populated from GET /api/sorts on app init
  selectedTileId: string | null;
  selectedTileData: TileData | null;
  sidebarOpen: boolean;
  pins: GeoJSON.FeatureCollection;
  loading: boolean;
  error: string | null;
}
```

**State transitions:**

| User action | Store mutation | Side effects |
|---|---|---|
| App initialisation | — | Fetch `GET /api/sorts` → populate `sortsMeta`. |
| Selects sort from DataBar (primary row) | `setActiveSort(sort)` | Reset `activeMetric` to `'score'`. Fetch new pins. Update Martin tile URL params. Clear `selectedTile`. Close sidebar. |
| Selects sub-metric from DataBar (secondary row) | `setActiveMetric(metric)` | Update Martin tile URL `metric` param. Update legend. Do **not** refetch pins. Do **not** clear `selectedTile`. Do **not** close sidebar. |
| Clicks a tile on map | `setSelectedTile(tileId)` | Fetch tile detail from API. Open sidebar. |
| Clicks "close" on sidebar | `closeSidebar()` | Clear `selectedTile`. Close sidebar. |
| Clicks empty area on map | `clearSelection()` | Clear `selectedTile`. Close sidebar. |

### 6.2 Component: `MapView`

Uses `vue-maplibre-gl` to render a MapLibre GL JS instance.

**Layers (bottom to top):**

1. **Base map** — Muted OSM or Ordnance Survey Ireland tiles. Subdued so data layers dominate.
2. **Choropleth tile layer** — `type: "fill"`. Source: Martin vector tiles. Fill colour driven by `["interpolate", ["linear"], ["get", "value"], ...]` using the active sort+metric's colour ramp. Martin always returns the active metric's value as the `value` property. Fill opacity: 0.65. Stroke: `#ffffff`, 0.3 opacity, 1px.
3. **Pin layer** — `type: "symbol"`. Source: GeoJSON from store. Icons loaded into map sprite per pin type. Clustering enabled at zoom < 10, `clusterRadius: 50`.
4. **Hover highlight** — `type: "fill"` filtered to hovered tile. Transparent fill, `#ffffff` stroke, 2px, 0.7 opacity. Only active when no tile is selected.
5. **Selected tile highlight** — `type: "fill"` filtered to the selected `tile_id`. Fill: `rgba(255, 255, 255, 0.15)`. Stroke: `#ffffff`, 1.5px, 0.85 opacity. Subtle but visible — allows users to see which tile the open sidebar refers to, particularly after the map pans.

**Interactions:**

- **Hover:** Update hover highlight filter, cursor → `pointer`.
- **Click (tile):** Extract `tile_id`, dispatch `setSelectedTile(tileId)`.
- **Click (pin):** Show MapLibre popup with name, type, key metric.
- **Click (empty):** Dispatch `clearSelection()`.

**Error state:** If Martin tiles fail to load, show a non-blocking toast notification ("Map data unavailable — check server"). If the sidebar API call fails, show an inline error state within the sidebar (not a full-page error).

**Martin tile URL construction:**
```
http://martin:3000/tile_heatmap/{z}/{x}/{y}?sort={activeSort}&metric={activeMetric}
```
Both parameters must be included. When `activeMetric` is `'score'`, Martin returns the composite sort score. The tile URL must be rebuilt reactively whenever either parameter changes.

**Initial view:** `center: [-7.6, 53.4]`, `zoom: 6.5` (all of Ireland).

### 6.3 Component: `DataBar`

Horizontal bar below any app header. Two rows:

**Primary row:** Sort selector — one active sort at a time. Labels, icons, active state.

| Sort key | Label | Icon (Lucide) |
|---|---|---|
| `overall` | Overall | BarChart3 |
| `energy` | Energy | Zap |
| `environment` | Constraints | ShieldAlert |
| `cooling` | Cooling | Thermometer |
| `connectivity` | Connectivity | Globe |
| `planning` | Planning | Map |

Active sort: filled background, bold label.

**Secondary row:** Sub-metric selector — appears immediately below the primary row. Shows metric pills for the active sort, derived from `sortsMeta[activeSort].metrics`. The `score` (composite) pill is always first and is the default selection on sort change. Selecting a different pill dispatches `setActiveMetric(key)`.

Secondary row styling: compact pill/chip style, less prominent than primary sort tabs. Active pill: outlined or lightly filled.

Sort switch must feel instant — tile colours and pins update without full page reload. Sub-metric switch updates tile colours only — even faster, no pin fetch.

**Mobile (<768px):** Primary row scrolls horizontally. Secondary row wraps below or is accessible via a chevron-expand. Do not collapse into a dropdown unless absolutely necessary for space.

### 6.4 Component: `Sidebar`

Slides in from the right. 380px on desktop, full-width sheet on mobile (<768px).

**States:** Closed (default), Loading (skeleton), Open (data displayed), Error (inline error message with retry button).

**Header:** County name + tile grid reference. Close button (X) top-right.

**Body:** Dynamic sub-component per sort:
- `SidebarOverall.vue`
- `SidebarEnergy.vue`
- `SidebarEnvironment.vue`
- `SidebarCooling.vue`
- `SidebarConnectivity.vue`
- `SidebarPlanning.vue`

Each sub-component receives tile data as a prop and renders exactly the fields listed in that sort's sidebar specification above.

**Map interaction:** On sidebar open, if the selected tile would be obscured by the sidebar panel, the map should `easeTo` to shift the view so the tile remains visible.

### 6.5 Component: `MapLegend`

Positioned fixed at the **bottom-left** of the map viewport, above the MapLibre attribution bar. Always visible when the map is displayed.

**Content:**
- Current metric label (e.g. "Energy Score", "Wind speed at 100m")
- Horizontal colour gradient bar (~180px wide), rendered as a CSS linear-gradient matching the active ramp
- Min label (left end) and max label (right end) with units
  - For normalised 0–100 metrics: "0" and "100"
  - For raw-value sub-metrics (wind speed m/s, temperature °C, rainfall mm, solar GHI kWh/m²/yr): show the actual data min/max from the tile dataset, fetched via `GET /api/metric-range?sort={sort}&metric={metric}` on metric change
- For diverging ramps: also show the midpoint label ("Neutral" or the actual midpoint value) below the centre of the gradient
- For the temperature sub-metric: a note indicating "Lower = better for cooling"

**Reactivity:** Rebuilds whenever `activeSort` or `activeMetric` changes.

---

## 7. API Specification

### FastAPI Endpoints

```
GET  /api/sorts
  → List of available sorts with full metadata including sub-metrics.
  → Shape:
    [
      {
        "key": "energy",
        "label": "Energy",
        "icon": "Zap",
        "description": "...",
        "metrics": [
          { "key": "score", "label": "Energy Score", "unit": "0–100", "isDefault": true },
          { "key": "wind_speed_100m", "label": "Wind speed at 100m", "unit": "m/s", "isDefault": false },
          { "key": "solar_ghi", "label": "Solar irradiance (GHI)", "unit": "kWh/m²/yr", "isDefault": false },
          { "key": "grid_proximity", "label": "Grid proximity score", "unit": "0–100", "isDefault": false }
        ]
      },
      ...
    ]

GET  /api/pins?sort={sort}
  → GeoJSON FeatureCollection of pins for the active sort.
  → Properties: pin_id, tile_id, name, type, plus sort-specific fields.
  → Note: no metric parameter — pins are sort-level, not metric-level.

GET  /api/tile/{tile_id}?sort={sort}
  → Full tile data for the sidebar. Shape varies per sort.
  → Always includes: tile_id, county, centroid [lng, lat], score, plus all sort-specific fields.

GET  /api/tile/{tile_id}/all
  → Data across ALL sorts for a single tile. Defer this to post-MVP.

GET  /api/metric-range?sort={sort}&metric={metric}
  → Returns { "min": float, "max": float, "unit": string } for the given metric.
  → Used by MapLegend to display actual data ranges for raw-value sub-metrics.
  → Cached — does not recompute on every request.

GET  /api/weights
  → Current composite score weights. Returns { energy: 0.25, connectivity: 0.25, ... }

PUT  /api/weights
  → (Admin) Update composite score weights. Triggers re-computation of overall sort.
  → Requires X-Admin-Key header. Key configured via environment variable ADMIN_KEY.
  → Returns 401 if key missing or incorrect.
```

### Martin Configuration

Martin exposes a PostGIS function source accepting `sort` and `metric` parameters:

```
GET http://martin:3000/tile_heatmap/{z}/{x}/{y}?sort={activeSort}&metric={activeMetric}
```

The underlying PostGIS function joins `tiles` with the relevant sort table and returns the requested metric column as a `value` feature property. The column is selected via a `CASE` statement on the `metric` parameter — **do not use dynamic SQL or `EXECUTE` for this**; enumerate all valid metric columns explicitly to prevent injection.

Example function signature (pseudocode):
```sql
CREATE OR REPLACE FUNCTION tile_heatmap(z int, x int, y int, sort text, metric text)
RETURNS bytea AS $$
  SELECT ST_AsMVT(q, 'tile_heatmap', 4096, 'geom')
  FROM (
    SELECT
      t.geom,
      t.tile_id,
      CASE
        WHEN sort = 'energy' AND metric = 'score'           THEN e.score
        WHEN sort = 'energy' AND metric = 'wind_speed_100m' THEN e.wind_speed_100m
        WHEN sort = 'energy' AND metric = 'solar_ghi'       THEN e.solar_ghi
        WHEN sort = 'energy' AND metric = 'grid_proximity'  THEN e.grid_proximity
        -- ... all other sort+metric combinations
        ELSE NULL
      END AS value
    FROM tiles t
    LEFT JOIN energy_scores e ON e.tile_id = t.tile_id
    -- LEFT JOIN other sort tables as needed
    WHERE t.geom && ST_TileEnvelope(z, x, y)
  ) q
$$ LANGUAGE sql STABLE PARALLEL SAFE;
```

**Martin cache note:** Martin caches vector tiles in memory. If sort table data is updated by the pipeline, restart Martin or flush its tile cache to avoid serving stale colours.

---

## 8. Data Pipeline

Refer to `ireland-data-sources.md` for all source URLs, formats, and provider details. The pipeline ingests from those sources, transforms into the tile grid, and loads into the database.

### Pipeline structure

```
/pipeline/
  grid/generate_grid.py        — One-time grid generation in EPSG:2157, stored as 4326
  energy/ingest.py             — Wind Atlas + Solar Atlas + OSM power infra
  environment/ingest.py        — NPWS boundaries + OPW flood + GSI landslide
  cooling/ingest.py            — Met Éireann grids + EPA rivers + OPW hydro stations
  connectivity/ingest.py       — ComReg + OSM roads + TII + PeeringDB
  planning/ingest.py           — MyPlan GZT + National Planning Apps + CSO population
  overall/compute_composite.py — Reads all sort scores, applies weights, writes overall
```

### Pipeline rules

1. All spatial joins use the tile grid as the reference frame. Point data → `ST_Within`. Polygon data → `ST_Intersection` area ratio. Raster data → zonal statistics (mean/max per tile).
2. All source data in ITM (EPSG:2157) must be reprojected to EPSG:4326 before loading.
3. Distance calculations (to grid, to IX, to roads) should be computed in EPSG:2157 for metric accuracy, then stored as km.
4. Each ingest script is idempotent — re-running it upserts, never duplicates.
5. The `overall/compute_composite.py` script runs last, after all sort tables are populated.
6. Use `geopandas`, `rasterio` (for GeoTIFF wind/solar data), `shapely`, and `sqlalchemy`.
7. Each sort table must store **both** the composite sort score **and** all constituent sub-metric values as separate columns. The Martin function reads these columns directly. Sub-metric values should be stored as raw values (m/s, mm, °C) — normalisation for colour is computed in the Martin SQL function, not pre-stored.

---

## 9. Deployment

```
docker-compose.yml
├── frontend      (Vite dev / nginx prod, port 5173/80)
├── api           (FastAPI via uvicorn, port 8000)
├── martin        (Martin tile server, port 3000)
├── db            (PostgreSQL 16 + PostGIS 3.4, port 5432)
└── pipeline      (Scheduled batch, not always running)
```

Nginx in production reverse-proxies: `/` → frontend, `/api/` → FastAPI, `/tiles/` → Martin.

---

## 10. Agent Task Boundaries

Identify which layer you are working on and stay within it.

| Task domain | Files you touch | You depend on | You provide to |
|---|---|---|---|
| **Database / schema** | `sql/`, migrations | Nothing | API, Martin, Pipeline |
| **Pipeline** | `pipeline/` | Schema, raw sources (see data sources doc) | Populated sort tables with sub-metric columns |
| **API** | `api/` | Schema, sort tables | Frontend (JSON) |
| **Martin config** | `martin.yaml`, SQL functions | Schema, sort tables | Frontend (MVT tiles) |
| **Frontend — MapView** | `src/components/MapView.vue` | Martin (tiles), API (pins), Store | User |
| **Frontend — DataBar** | `src/components/DataBar.vue` | Store (`sortsMeta`, `activeSort`, `activeMetric`) | Store mutations |
| **Frontend — MapLegend** | `src/components/MapLegend.vue` | Store (`activeSort`, `activeMetric`, `sortsMeta`), API (`/api/metric-range`) | User |
| **Frontend — Sidebar** | `src/components/Sidebar*.vue` | Store, API (tile data) | User |
| **Frontend — Store** | `src/stores/suitability.ts` | API | All frontend components |

### Key rules for all agents

1. **Never bypass Martin for geometry.** FastAPI serves data. Martin serves tiles. Don't fetch GeoJSON tile geometry from FastAPI.
2. **The store is the single source of truth.** Components read from and dispatch to the store. They do not fetch data independently.
3. **Colour ramps are defined per sort, sub-metrics inherit the sort ramp.** Do not invent new ramps for sub-metrics unless explicitly specified above (temperature inversion).
4. **Selected tile gets a subtle highlight.** The selected tile layer uses a light white fill overlay (0.15 opacity) and a slightly more opaque white stroke. This is distinct from the hover highlight and is never removed while the sidebar is open.
5. **One sort active at a time. One metric active at a time.** The UI never shows multiple sorts or multiple metrics simultaneously.
6. **Pin clustering at low zoom.** Cluster below zoom 10.
7. **Pins are sort-level, not metric-level.** Switching sub-metric must never trigger a pin refetch.
8. **Sort-specific sidebar sub-components.** One `.vue` file per sort. Do not build a generic conditional renderer.
9. **Refer to `ireland-data-sources.md` for source details.** Do not hardcode source URLs in pipeline scripts — load them from config or reference the doc.
10. **Exclusion zones are hard zeros.** Any tile overlapping an SAC, SPA, or current flood extent gets an overall score of 0. This is not negotiable in the scoring logic.
11. **`GET /api/sorts` is the source of truth for DataBar content.** The frontend must not hardcode sort or sub-metric lists. On app init, fetch sorts and populate `sortsMeta` in the store.
12. **The Martin SQL function enumerates metric columns explicitly.** No dynamic SQL. All valid `sort`+`metric` combinations are listed in a CASE statement.
13. **Sub-metric raw values are stored in sort tables, not pre-normalised.** The Martin function handles min-max normalisation via `(value - min) / (max - min)` using pre-computed range values stored in a separate `metric_ranges` table populated by the pipeline.

---

## 11. Known Design Gaps & Decisions

This section documents deliberate design choices and known technical debts. Agents must not silently work around these — either implement them as specified or flag them.

**D1 — Sub-metric normalisation in Martin:** The Martin SQL function must perform min-max normalisation on raw sub-metric values to map them to the 0–1 colour ramp input range. The pipeline's `compute_composite.py` should pre-compute and store `(min, max)` for every raw sub-metric in a `metric_ranges(sort, metric, min_val, max_val)` table. Martin reads this table in its SQL function via a subquery. This avoids computing range on every tile request.

**D2 — Tile resolution vs zoom:** At high zoom levels (>12), the 5 km² tiles become visually large blocks. The platform is designed for strategic/regional analysis, not site-level precision. Do not attempt to implement a finer grid — acknowledge this limitation in any UI tooltip or onboarding.

**D3 — IDA Ireland data is manual-entry only.** No GIS download exists. IDA site locations must be manually geocoded and loaded into the database. This data will go stale and must be periodically reviewed.

**D4 — `PUT /api/weights` is admin-only** with X-Admin-Key header auth. This is intentionally minimal for the hackathon. A production version would require proper auth (OAuth2, session tokens). Do not expose this endpoint without the key check in place.

**D5 — `GET /api/tile/{tile_id}/all` is deferred.** Do not implement it as part of the MVP. Remove it from the API router if it adds complexity. Revisit post-hackathon for an export/comparison feature.

**D6 — No fibre route data exists publicly for Ireland.** ComReg broadband coverage is the best available proxy. The connectivity sort must note this limitation in the sidebar and not claim to represent fibre availability.

**D7 — OPW flood data licence.** CC BY-NC-ND. Non-commercial use only. Any commercial deployment of this platform must replace this dataset or obtain a separate licence. Flag this in the UI footer for any production deployment.
