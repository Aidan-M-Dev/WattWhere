# HackEurope — Implementation Task Roadmap

> **How to use this document**
> Each Phase 1 task can be handed verbatim to a separate Claude Code instance.
> They are sized for a single focused session. Dependencies are explicit — tasks with
> no listed dependencies can start immediately and run in parallel.
> Once all Phase 1 tasks are done, the platform has core interactive functionality.
> Phase 2 lists post-MVP improvements in less detail.

---

## Dependency graph

```
TASK-01 (Docker stack)
    └── TASK-02 (Grid + Seed data)
            ├── TASK-03 (FastAPI routes)  ──────────────────────┐
            │       └── TASK-04 (MapView + Store)               │
            │               ├── TASK-05 (DataBar + Legend)      │
            │               └── TASK-06 (Sidebar components) ───┘
            └── [runs independently after TASK-02]
```

Tasks 03–06 can overlap significantly once TASK-02 produces data.
A frontend agent can begin TASK-04 against the stub API while TASK-03 is in progress —
the store's `TODO: implement` bodies make the integration surface explicit.

---

## Phase 1 — Core Functionality

---

### COMPLETED TASK-01 · Docker Stack Bootstrap

**Goal**: Every service starts, passes its healthcheck, and the stack is usable as a
development environment. No application logic is written here — just infrastructure.

#### Read first
- `docker-compose.yml` — all 5 services, volumes, health checks
- `api/main.py` and `api/db.py` — FastAPI lifespan and pool init
- `api/Dockerfile` — Python build
- `frontend/Dockerfile` — Node dev/build/prod stages
- `martin/config.yaml` — function source config, CORS
- `sql/tables.sql`, `sql/indexes.sql`, `sql/functions.sql` — applied automatically via
  `docker-entrypoint-initdb.d/` mounts in the db service

#### Deliverables

1. **`.env` file**: Copy `.env.example` → `.env`, fill in `DB_PASSWORD=dev`,
   `ADMIN_KEY=devkey`.

2. **`docker-compose.yml` fix (if needed)**: Verify the `db` healthcheck, the
   `api` volume mount (`./api:/app`), and the `martin` `DATABASE_URL` env var are
   all wired correctly. The `DATABASE_URL` env var must override the hard-coded value
   in `martin/config.yaml`.

3. **`api/db.py`**: `init_pool()` must actually create the pool. The skeleton is
   complete — verify it works end-to-end (no `NotImplementedError` remains in db.py).

4. **`api/main.py`**: Confirm the `/health` endpoint returns `200 {"status": "ok"}`.
   Confirm `lifespan` calls `init_pool()` without error when DB is available.

5. **`api/routes/__init__.py`**: Must be an empty file (already created). Confirm imports
   in `main.py` resolve (`from routes import sorts, pins, tile, metric_range, weights`).

6. **Martin startup**: `docker compose logs martin` shows Martin connected to PostgreSQL
   and registered the `tile_heatmap` function source. Martin will log a warning if the
   `tiles` table is empty — that is expected at this stage.

7. **Frontend dev server**: `npm install` completes, `npm run dev` starts, Vite proxy
   config resolves `/api/` and `/tiles/` correctly (verify in `vite.config.ts`).

8. **`pipeline/` does NOT start by default** (it uses `profiles: [pipeline]`) — confirm
   `docker compose up` does not attempt to start the pipeline service.

#### Tests / acceptance criteria

Run these commands after `docker compose up -d`:

```bash
# 1. All services up
docker compose ps
# Expect: db=healthy, martin=running, api=running, frontend=running, nginx=running

# 2. DB has all 19 tables
docker compose exec db psql -U hackeurope -d hackeurope -c "\dt" | wc -l
# Expect: >= 21 lines (header + 19 tables + footer)

# 3. tile_heatmap function exists
docker compose exec db psql -U hackeurope -d hackeurope \
  -c "SELECT proname FROM pg_proc WHERE proname = 'tile_heatmap';"
# Expect: 1 row

# 4. API health
curl -s http://localhost:8000/health
# Expect: {"status":"ok"}

# 5. Martin responds (tile may be empty, but server must 200 or return empty MVT)
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost:3000/tile_heatmap/6/31/21?sort=overall&metric=score"
# Expect: 200

# 6. Frontend dev server
curl -s -o /dev/null -w "%{http_code}" http://localhost:5173
# Expect: 200
```

#### Common issues to pre-empt
- `martin/config.yaml` uses `${DATABASE_URL}` — Martin must read this from the env var,
  not the literal string. Check Martin docs for env var interpolation syntax; may need
  `connection_string: "postgresql://hackeurope:${DB_PASSWORD}@db:5432/hackeurope"` with
  explicit host instead.
- FastAPI import error if `routes/` directory has no `__init__.py` — already created,
  just verify it's present.
- `npm install` may fail if Node version < 20 — the Dockerfile uses Node 22; locally
  use `nvm use 22` or run via Docker.

---

### COMPLETED TASK-02 · Tile Grid + Synthetic Data Seed

**Goal**: Populate the database with a realistic ~14,000-tile Ireland grid and plausible
synthetic scores for all 6 sort tables. This is the fastest path to a working map.
Real pipeline data ingestion is Phase 2 — synthetic data is specifically designed to
be replaced without any schema changes.

#### Read first
- `ARCHITECTURE.md` §4 (grid spec: EPSG:2157, 5 km² tiles, centroid-clip to Ireland)
- `ARCHITECTURE.md` §5 (score ranges and field meanings per sort)
- `sql/tables.sql` — all 19 table schemas (tile_id, score ranges, CHECK constraints)
- `sql/functions.sql` — `tile_heatmap` function; it reads `metric_ranges` for normalisation
- `pipeline/config.py` — `TILE_SIZE_M`, `GRID_CRS_ITM`, DB config
- `pipeline/grid/generate_grid.py` — skeleton to implement

#### Deliverables

**1. Implement `pipeline/grid/generate_grid.py`**

Complete all 5 functions (`load_ireland_boundary`, `generate_grid_itm`,
`reproject_to_wgs84`, `assign_counties`, `load_tiles_to_db`) and `main()`.

Ireland boundary approach (pick the most reliable option available):
- **Option A** (preferred): Download the OSi Ireland boundary from
  `https://data-osi.opendata.arcgis.com/` in GeoJSON or GPKG, place at
  `DATA_ROOT/grid/ireland_boundary.gpkg`. Use `geopandas.read_file()`.
- **Option B** (fallback for offline/CI): Hard-code a rough Ireland polygon in
  EPSG:4326 using ~20 vertices (`shapely.geometry.Polygon([...])`) and reproject.
  Accuracy: ±20 km on coasts — acceptable for synthetic testing.

For `assign_counties`: use a county boundaries file from data.gov.ie, or if unavailable,
use a Voronoi-partition approach from known county centroids (coarse but functional).

Idempotency requirement: `TRUNCATE tiles CASCADE` before re-inserting.

**2. Create `pipeline/seed_synthetic.py`** (new file)

This script populates ALL score tables with synthetic data. Run it after the grid exists.

```python
# pipeline/seed_synthetic.py
# Role: Populate all sort tables + metric_ranges with synthetic data for dev/testing.
# Run: python seed_synthetic.py
# Safe to re-run (TRUNCATE + INSERT each sort table).
# Data is random but respects all DB CHECK constraints (0–100 ranges, etc.)
```

The script must:

a) **Read all tile_ids** from the `tiles` table.

b) **Populate `energy_scores`** for all tiles:
   - `score`: random 0–100
   - `wind_speed_100m`: random 4.0–12.0 (m/s) — Ireland typical range
   - `wind_speed_50m`: `wind_speed_100m * 0.85`
   - `wind_speed_150m`: `wind_speed_100m * 1.10`
   - `solar_ghi`: random 900–1200 (kWh/m²/yr)
   - `grid_proximity`: random 0–100
   - `nearest_substation_km`: random 0.5–35.0
   - `grid_low_confidence`: `True` if `nearest_substation_km > 20`

c) **Populate `environment_scores`** for all tiles:
   - ~5% of tiles: `has_hard_exclusion=True`, `score=0`, `exclusion_reason='SAC overlap'`
   - ~3% of tiles: `intersects_current_flood=True`, `score=0`, `has_hard_exclusion=True`
   - All other tiles: random `score` 40–100
   - `designation_overlap`, `flood_risk`, `landslide_risk`: random 40–100
   - `landslide_susceptibility`: weighted random ('none'×60%, 'low'×25%, 'medium'×12%, 'high'×3%)

d) **Populate `cooling_scores`** for all tiles:
   - `temperature`: random 8.5–13.5 (°C) — Ireland range
   - `rainfall`: random 700–2500 (mm/yr) — Ireland range (west wetter)
   - `water_proximity`: random 20–100
   - `aquifer_productivity`: random 10–90
   - `free_cooling_hours`: `int(8760 * (14 - temperature) / 14)` — rough estimate
   - `score`: random 40–90

e) **Populate `connectivity_scores`** for all tiles:
   - `inex_dublin_km`: computed from tile centroid to (-6.2603, 53.3498) using
     PostGIS `ST_Distance` or approximate Haversine formula
   - `inex_cork_km`: computed from tile centroid to (-8.4694, 51.8969)
   - `ix_distance`: `max(0, 100 * (1 - log(1 + min(inex_dublin_km, inex_cork_km)) / log(301)))`
   - `broadband`: random 20–95
   - `road_access`: random 30–95
   - `score`: random 30–90

f) **Populate `planning_scores`** for all tiles:
   - `pct_industrial`, `pct_enterprise`: random 0–30 each
   - `pct_agricultural`: 100 - industrial - enterprise - 10 (remainder)
   - `pct_residential`: random 0–15
   - `pct_mixed_use`, `pct_other`: fill remaining to ~100
   - `zoning_tier`: `(pct_industrial + pct_enterprise) * 0.9`
   - `planning_precedent`: random 0–60
   - `score`: derived from zoning + precedent

g) **Populate `overall_scores`** for all tiles:
   - Fetch weights from `composite_weights` table
   - `score = (energy * 0.25) + (environment * 0.25) + (cooling * 0.15) + (connectivity * 0.25) + (planning * 0.15)`
   - Tiles with `has_hard_exclusion=True` in environment → `score = 0`
   - Copy sub-scores from each sort table

h) **Populate `metric_ranges`** with Ireland-realistic values:
   ```sql
   ('energy', 'wind_speed_100m', 4.0, 12.5, 'm/s')
   ('energy', 'solar_ghi', 900.0, 1250.0, 'kWh/m²/yr')
   ('cooling', 'temperature', 8.5, 13.5, '°C')
   ('cooling', 'rainfall', 700.0, 2500.0, 'mm/yr')
   ```
   Use `INSERT ... ON CONFLICT DO UPDATE`.

i) **Insert 3 sample IDA sites** into `ida_sites` (Dublin, Cork, Galway approximate coords).

j) **Insert 2 sample pins** into `pins_overall` with `type='data_centre'` (Dublin/Cork coords).

#### Tests / acceptance criteria

```bash
# Run the seed script (inside pipeline container or locally with DB_URL set)
docker compose --profile pipeline run --rm pipeline python seed_synthetic.py

# Grid
docker compose exec db psql -U hackeurope -d hackeurope \
  -c "SELECT COUNT(*) FROM tiles;"
# Expect: 10000–16000

docker compose exec db psql -U hackeurope -d hackeurope \
  -c "SELECT county, COUNT(*) FROM tiles GROUP BY county ORDER BY county;"
# Expect: all 26 counties present with plausible tile counts

# Score tables
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT
    (SELECT COUNT(*) FROM energy_scores)       AS energy,
    (SELECT COUNT(*) FROM environment_scores)  AS env,
    (SELECT COUNT(*) FROM cooling_scores)      AS cooling,
    (SELECT COUNT(*) FROM connectivity_scores) AS connectivity,
    (SELECT COUNT(*) FROM planning_scores)     AS planning,
    (SELECT COUNT(*) FROM overall_scores)      AS overall,
    (SELECT COUNT(*) FROM tiles)               AS tiles;
"
# Expect: all 6 counts equal tiles count

# Ranges
docker compose exec db psql -U hackeurope -d hackeurope \
  -c "SELECT * FROM metric_ranges;"
# Expect: 4 rows (wind, solar, temperature, rainfall)

# Exclusions — some tiles must have hard_exclusion
docker compose exec db psql -U hackeurope -d hackeurope \
  -c "SELECT COUNT(*) FROM overall_scores WHERE score = 0;"
# Expect: > 0 and < (SELECT COUNT(*) FROM overall_scores) * 0.15

# Martin now serves non-empty tiles
curl -s "http://localhost:3000/tile_heatmap/6/31/21?sort=overall&metric=score" \
  | wc -c
# Expect: > 100 bytes (non-empty MVT)
```

#### Notes
- `seed_synthetic.py` uses `numpy.random.seed(42)` for reproducibility.
- All `Decimal`/`NUMERIC` columns must be cast to Python `float` before insert.
- Use `asyncpg` or `psycopg2` for batch inserts (not pandas `to_sql` with defaults —
  it is slow at 14k rows with geometry). Use `executemany` with 1000-row batches.

---

### COMPLETED TASK-03 · FastAPI Routes Implementation

**Goal**: All 5 route files return correct, validated responses backed by real DB queries.
No more `raise NotImplementedError` or `return {}` stubs.

#### Read first
- `ARCHITECTURE.md` §7 (full API specification — endpoint shapes, rules)
- `api/routes/sorts.py` — already complete (static metadata, no DB needed)
- `api/routes/tile.py` — 6 sort-specific `_get_*` functions, mostly scaffolded
- `api/routes/pins.py` — SQL queries already written, need testing
- `api/routes/metric_range.py` — reads `metric_ranges` table
- `api/routes/weights.py` — reads/writes `composite_weights`, admin key check
- `api/db.py` — `get_conn()` dependency pattern
- `sql/tables.sql` — exact column names for all queries

#### Deliverables

**1. `api/routes/sorts.py`** — already complete. Verify it returns a list of 6 `SortMeta`
objects with correct structure. No changes expected unless import fails.

**2. `api/routes/pins.py`** — the SQL queries are written in the skeleton. Remove any
remaining stubs, execute the queries, and return the `FeatureCollection`. The
`json.loads(row["feature"])` pattern must produce valid GeoJSON features.
Edge case: if a pin table is empty (no data yet), return an empty `features: []` —
do NOT raise 404.

**3. `api/routes/tile.py`** — the `_get_*` helper functions are scaffolded.
Implement each fully:
- `_get_overall`: fetches `overall_scores JOIN composite_weights` ✓ (skeleton complete)
- `_get_energy`: fetches `energy_scores` ✓ (skeleton complete)
- `_get_environment`: fetches `environment_scores` + `tile_designation_overlaps` ✓
- `_get_cooling`: fetches `cooling_scores` ✓
- `_get_connectivity`: fetches `connectivity_scores` ✓
- `_get_planning`: fetches `planning_scores` + `tile_planning_applications` ✓

All `_get_*` functions are already written — the main task is to verify the column
names match `sql/tables.sql` exactly, remove any `raise NotImplementedError` lines,
and handle `asyncpg.Record` → Python dict conversion via the `_f()` helper pattern.

One issue to fix: `asyncpg` returns `Decimal` for `NUMERIC` columns. The `_f(val)`
helper already handles this. Verify `app_date` returns `.isoformat()` not a raw date.

**4. `api/routes/metric_range.py`** — remove `# TODO: implement` comment, confirm
the SQL query runs. The skeleton is complete.

**5. `api/routes/weights.py`** — remove `# TODO: implement` comments. Both GET and PUT
are fully written in the skeleton. Verify `ADMIN_KEY` env var is read at request-time
(not at import-time). The `Depends(_check_admin_key)` pattern is already implemented.

**6. Write `api/tests/test_routes.py`** (new file):

```python
# api/tests/test_routes.py
# Uses httpx.AsyncClient with the FastAPI test client.
# Requires: pytest, httpx, pytest-asyncio
# Run: pytest api/tests/ -v
```

Write tests for:
- `GET /api/sorts` → status 200, list of 6 items, each has `key`/`label`/`metrics`
- `GET /api/pins?sort=overall` → status 200, `type=FeatureCollection`
- `GET /api/pins?sort=invalid` → status 422 (validation error)
- `GET /api/tile/1?sort=overall` → status 200, has `tile_id`/`county`/`score`
- `GET /api/tile/1?sort=energy` → status 200, has `wind_speed_100m`
- `GET /api/tile/1?sort=environment` → status 200, has `designations` list
- `GET /api/tile/99999?sort=overall` → status 404
- `GET /api/metric-range?sort=energy&metric=wind_speed_100m` → status 200, has `min`/`max`/`unit`
- `GET /api/metric-range?sort=energy&metric=score` → status 400 (not a raw metric)
- `GET /api/weights` → status 200, weights sum to ~1.0
- `PUT /api/weights` without header → status 401
- `PUT /api/weights` with wrong key → status 401
- `PUT /api/weights` with correct key + valid weights → status 200

Add `api/tests/__init__.py` and `api/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
```

Add `pytest` and `httpx` and `pytest-asyncio` to `api/requirements.txt`.

#### Tests / acceptance criteria

```bash
# Install test deps and run
cd api && pip install pytest httpx pytest-asyncio
pytest tests/ -v

# All 12 test functions should pass.
# Expect: 12 passed, 0 failed

# Smoke test the live API
curl -s http://localhost:8000/api/sorts | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert len(data) == 6, f'Expected 6 sorts, got {len(data)}'
assert all('metrics' in s for s in data), 'Missing metrics'
print('sorts OK')
"

curl -s "http://localhost:8000/api/tile/1?sort=overall" | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert 'score' in data, 'Missing score'
assert 'weights' in data, 'Missing weights'
print('tile OK:', data['county'], data['score'])
"
```

---

### COMPLETED TASK-04 · MapView + Pinia Store

**Goal**: The map renders Ireland with choropleth tile colouring from Martin MVT tiles,
pins are visible, hover and click interactions work, and switching sort/metric updates
the map reactively. The store is the single source of truth.

#### Read first
- `ARCHITECTURE.md` §6.1 (store state transitions — **read this carefully**)
- `ARCHITECTURE.md` §6.2 (MapView layer spec — choropleth fill, hover, selected tile)
- `frontend/src/stores/suitability.ts` — all actions and state are declared; most TODO bodies
- `frontend/src/components/MapView.vue` — layers are declared; watchers are TODO stubs
- `frontend/src/types/index.ts` — `COLOR_RAMPS`, all tile types
- Martin tile URL: `/tiles/tile_heatmap/{z}/{x}/{y}?sort={sort}&metric={metric}`
  (proxied by Vite; in prod: `http://martin:3000/...`)

#### Deliverables

**1. Complete `frontend/src/stores/suitability.ts`**

All `// TODO: implement` bodies in the store are already structured correctly — the
task is to fill in the fetch calls and verify state transitions.

- `fetchSortsMeta()`: already written — confirm `fetch('/api/sorts')` works and
  sets `sortsMeta.value`.
- `fetchPins()`: already written — confirm `fetch('/api/pins?sort=...')` works and
  sets `pins.value`.
- `fetchTileDetail()`: already written — confirm it populates `selectedTileData.value`
  and sets `sidebarOpen.value = true`.
- `fetchMetricRange()`: already written — confirm it sets `metricRange.value`.
- **State transition actions** — `setActiveSort`, `setActiveMetric`, `setSelectedTile`,
  `closeSidebar`, `clearSelection`: all are written; verify the side effects (metric reset,
  pin refetch, no pin refetch on metric switch) are triggered correctly.

One gap: `setActiveMetric` currently comments out the martin URL update:
```typescript
// TODO: martinTileUrl computed auto-updates — trigger map source refresh in MapView
```
The computed `martinTileUrl` already updates reactively. The watcher in `MapView.vue`
must call `map.getSource('tiles-mvt').setTiles([newUrl])` — implement this watcher.

**2. Complete `frontend/src/components/MapView.vue`**

Most layer setup and interaction handlers are already written. Key gaps to implement:

a) **`buildColorExpression()`** — the interpolate expression is partially written.
   Verify it produces valid MapLibre `ExpressionSpecification` from `COLOR_RAMPS[sort].stops`.
   The stops are `[value, color]` pairs; MapLibre interpolate expects `[stop, color, stop, color...]`.

b) **Watch `martinTileUrl`** — when it changes, update the source tiles and repaint:
   ```typescript
   const source = map.getSource('tiles-mvt') as maplibregl.VectorTileSource
   source.setTiles([newUrl])
   map.setPaintProperty('tiles-fill', 'fill-color', buildColorExpression())
   ```

c) **Watch `pins`** — when pins GeoJSON changes, update the source:
   ```typescript
   const src = map.getSource('pins') as maplibregl.GeoJSONSource
   src.setData(newPins)
   ```

d) **Pin cluster layers** — add these 3 layers in `setupLayers()`:
   - `pins-clusters`: `type: "circle"`, filter `['has', 'point_count']`, radius 18,
     colour `rgba(255,255,255,0.15)`, stroke white 1px
   - `pins-labels`: `type: "symbol"`, filter `['has', 'point_count']`,
     `text-field: '{point_count_abbreviated}'`, white text
   - `pins-unclustered`: `type: "symbol"` (or `circle` as placeholder),
     filter `['!', ['has', 'point_count']]`, radius 6, colour based on `['get', 'type']`

e) **Sidebar map pan** — when a tile is selected and the sidebar is open, call:
   ```typescript
   map.easeTo({ padding: { right: 380 } })
   ```
   Reset padding to 0 on sidebar close.

f) **Base map style** — replace the demo tiles URL with a better free option:
   ```typescript
   style: 'https://tiles.openfreemap.org/styles/liberty'
   ```
   or use OSM Bright from MapTiler free tier (requires a free API key set in `.env`).

**3. Write `frontend/src/stores/suitability.test.ts`** (new file)

Use Vitest (add to `package.json` devDependencies: `vitest`, `@vue/test-utils`).

Write tests for:
- `setActiveSort('energy')` → `activeSort === 'energy'`, `activeMetric === 'score'`,
  `selectedTileId === null`, `sidebarOpen === false`
- `setActiveMetric('wind_speed_100m')` → `activeMetric === 'wind_speed_100m'`,
  `activeSort` is unchanged, `sidebarOpen` is unchanged
- `closeSidebar()` → `sidebarOpen === false`, `selectedTileId === null`
- `martinTileUrl` computed → contains `sort=overall&metric=score` initially
- `martinTileUrl` after `setActiveSort('energy')` → contains `sort=energy&metric=score`

Mock `fetch` using `vi.stubGlobal('fetch', ...)`.

Add `"test": "vitest run"` to `package.json` scripts.

#### Tests / acceptance criteria

```bash
# Unit tests
cd frontend && npm run test
# Expect: 5+ tests passing

# Visual smoke test (manual)
# 1. Open http://localhost:5173
# 2. Map should render Ireland with green choropleth tiles
# 3. Hover over tiles → highlight border appears
# 4. Click a tile → console should log tile_id (sidebar not wired yet)
# 5. Open browser Vue devtools → useSuitabilityStore() → verify state structure
# 6. Manually call: store.setActiveSort('energy') in console
#    → martinTileUrl should change to include sort=energy
```

---

### COMPLETED TASK-05 · DataBar + MapLegend

**Goal**: Sort tabs and sub-metric pills are fully interactive. Switching sort triggers
map update. Switching metric triggers legend update. MapLegend displays correct gradient
and labels for the active sort+metric.

#### Read first
- `ARCHITECTURE.md` §6.3 (DataBar spec — primary + secondary rows, mobile)
- `ARCHITECTURE.md` §6.5 (MapLegend spec — gradient, raw vs normalised labels, temperature note)
- `frontend/src/components/DataBar.vue` — icon map, tab rendering, event handlers
- `frontend/src/components/MapLegend.vue` — gradient CSS computed property
- `frontend/src/stores/suitability.ts` — `setActiveSort`, `setActiveMetric` actions
- `frontend/src/types/index.ts` — `COLOR_RAMPS`, `TEMPERATURE_RAMP`, `SortType`

#### Deliverables

**1. Complete `frontend/src/components/DataBar.vue`**

The skeleton is mostly complete. Gaps to fix:

a) The `getIcon()` function maps icon name strings to Lucide components. Verify this
   works with Vue's `<component :is="...">` pattern. The icon names in `sorts.py`
   (`"BarChart3"`, `"Zap"`, `"ShieldAlert"`, `"Thermometer"`, `"Globe"`, `"Map"`) must
   match keys in `ICON_MAP`.

b) Verify `onSortClick` and `onMetricClick` call `await store.setActiveSort()` and
   `await store.setActiveMetric()` correctly.

c) Loading state: when `store.loading && !store.sortsMeta.length`, show 6 skeleton tabs.
   This is already templated — verify the condition is correct.

d) Sort tabs must show the active visual state (`sort-tab--active` class). Verify
   `:class="{ 'sort-tab--active': store.activeSort === sort.key }"` works correctly
   given `sort.key` is the string from API and `activeSort` is the store ref.

e) Secondary row (metric pills) only appears when `activeMetrics.length > 0`. Verify
   `store.activeSortMeta` is computed correctly (it depends on `sortsMeta` being populated).

**2. Complete `frontend/src/components/MapLegend.vue`**

The skeleton computed properties are declared. Gaps to fix:

a) **`gradientCss`** — the computed is partially written. Verify it generates a valid CSS
   linear-gradient string from `COLOR_RAMPS[sort].stops`. Test with environment sort
   (diverging — must produce a 3-stop gradient).

b) **Temperature inversion** — when `activeMetric === 'temperature'`, the gradient must
   be reversed (dark blue on left = cold = high cooling score). The `isTemperature`
   computed is declared; verify the gradient reversal logic in `gradientCss`.

c) **Raw metric labels** — `minLabel` and `maxLabel` use `store.metricRange` when it
   exists (for wind, solar, temperature, rainfall). Verify `store.metricRange` is
   populated correctly by `store.setActiveMetric()` → `store.fetchMetricRange()`.

d) **Diverging midpoint** — `isDiverging` computed must return true only for environment
   sort. Verify it shows the `legend__mid` element with "Neutral" label.

**3. Write `frontend/src/components/DataBar.test.ts`** (new file)

```typescript
// Tests using @vue/test-utils + vitest
// Mock useSuitabilityStore with a minimal mock store
```

Write tests for:
- DataBar renders correct number of sort tabs (6) when `sortsMeta` is populated
- Clicking a sort tab calls `store.setActiveSort()` with correct arg
- Active sort tab has `sort-tab--active` class
- Secondary row renders metrics for active sort
- Clicking a metric pill calls `store.setActiveMetric()` with correct key
- Loading state renders skeleton tabs when `loading=true` and `sortsMeta=[]`

**4. Write `frontend/src/components/MapLegend.test.ts`** (new file)

Write tests for:
- Gradient CSS contains correct hex colours for 'overall' sort
- Gradient CSS reverses stops for 'temperature' metric
- `isDiverging` is true only for 'environment' sort
- `minLabel` shows "0" for normalised metric, actual value for raw metric
- Temperature note is shown only for 'temperature' metric

#### Tests / acceptance criteria

```bash
cd frontend && npm run test
# Expect: all DataBar + MapLegend tests passing (8+ tests total)

# Visual smoke test (manual):
# 1. Reload http://localhost:5173
# 2. DataBar renders 6 sort tabs (Overall, Energy, Constraints, Cooling, Connectivity, Planning)
# 3. Click "Energy" → active tab changes, secondary row shows Wind/Solar/Grid pills
# 4. Click "Wind speed at 100m" pill → legend updates to show m/s range from API
# 5. Click "Constraints" sort → secondary row shows Designation/Flood/Landslide pills
#    → gradient should be blue-orange (diverging)
# 6. Click "Cooling" then "Mean annual temperature" → legend shows "Lower = better" note
```

---

### COMPLETED TASK-06 · Sidebar Components

**Goal**: Clicking a tile opens the Sidebar with correct sort-specific data.
All 6 `SidebarX.vue` components render real data from the store. Loading and error
states work. Map pans to keep the selected tile visible.

#### Read first
- `ARCHITECTURE.md` §5 (each sort's sidebar spec — exact fields to display)
- `ARCHITECTURE.md` §6.4 (Sidebar container spec — states, width, header)
- `frontend/src/components/Sidebar.vue` — container, delegation pattern, error/loading
- `frontend/src/components/SidebarOverall.vue` through `SidebarPlanning.vue` — all present
- `frontend/src/types/index.ts` — `TileOverall`, `TileEnergy`, etc. prop types
- `frontend/src/stores/suitability.ts` — `selectedTileData`, `sidebarOpen`, `loading`, `error`

#### Deliverables

**1. `frontend/src/components/Sidebar.vue`** — the container is complete.

Key things to verify/fix:

a) **Transition animation** — the `width: 0 → 380px` CSS transition must be smooth.
   Test that opening/closing animates without layout jank (check for `overflow: hidden`
   on the parent `.app-main`).

b) **`retry()` function** — calls `store.fetchTileDetail(store.selectedTileId, store.activeSort)`.
   Verify `store.selectedTileId` is `number | null` (not string) — the `Path(...)`
   parameter in FastAPI expects an integer.

c) **Type assertions** — `store.selectedTileData as TileOverall` etc. are safe given
   `activeSort` is the discriminant. Verify TypeScript does not complain (`npm run type-check`).

**2. `SidebarOverall.vue`** — the `subScores` computed is implemented. Verify it handles
null sub-scores gracefully (backend may return `null` if a sort table entry is missing).
The `scoreColor` computed is complete.

**3. `SidebarEnergy.vue`** — review that all fields from `TileEnergy` are rendered.
The `grid_low_confidence` warning banner must appear when the flag is true.
The EirGrid external link must have `rel="noopener noreferrer"`.

**4. `SidebarEnvironment.vue`** — the designation list requires `data.designations`
to be an array. Verify that the API route returns this (it's in `_get_environment()`
in `tile.py`). The OPW licence notice must appear in all cases (not conditional).

**5. `SidebarCooling.vue`** — verify temperature shows `(lower = better)` hint inline.
Free cooling hours should show `toFixed(0)` not `toFixed(2)`.

**6. `SidebarConnectivity.vue`** — the fibre disclaimer note must always show (not
conditionally). This is an architecture rule (D6 — no public fibre data for Ireland).

**7. `SidebarPlanning.vue`** — the zoning stacked bar filters out 0% segments.
Verify `zoningSegments` computed filters correctly (`z.pct > 0`). The planning
application status badge classes (`status--granted`, `status--refused`, etc.)
must match exactly the values returned by the API (`planning_scores.status` column
has CHECK constraint `IN ('granted','refused','pending','withdrawn','other')`).
Add `status--other` class to the style block.

**8. Write `frontend/src/components/Sidebar.test.ts`** (new file)

Write tests for:
- Sidebar is not visible when `sidebarOpen = false`
- Sidebar is visible (`width=380px`) when `sidebarOpen = true`
- Loading state shows skeleton blocks when `loading = true` and `selectedTileData = null`
- Error state shows error message + retry button when `error` is set
- `SidebarOverall` renders when `activeSort = 'overall'` and `selectedTileData` set
- `SidebarEnergy` renders when `activeSort = 'energy'`
- Close button click calls `store.closeSidebar()`

#### Tests / acceptance criteria

```bash
cd frontend && npm run test
# Expect: all Sidebar tests passing (7+ tests)

# Integration smoke test (manual):
# 1. Load http://localhost:5173 with stack running + seed data in DB
# 2. Click a tile on the map
# 3. Sidebar slides in from right
#    → Header shows county name
#    → Loading skeleton briefly visible
#    → Data appears for Overall sort
# 4. Click "Energy" sort tab
#    → Sidebar closes (clears selected tile per ARCHITECTURE.md §6.1)
# 5. Click same tile again
#    → Energy sidebar appears with wind/solar/grid data
# 6. Click close (X) button → sidebar closes, tile highlight disappears
# 7. Click empty ocean area → sidebar closes if open

# Type-check passes
cd frontend && npm run type-check
# Expect: 0 errors
```

---

## Phase 1 Completion Criteria

All of the following must be true before claiming Phase 1 is done:

- [ ] `docker compose up -d` → all services healthy
- [ ] `~14,000 tiles` in DB with scores for all 6 sorts
- [ ] `GET /api/sorts` returns 6 sorts with full metric metadata
- [ ] `GET /api/tile/1?sort=energy` returns energy data with correct fields
- [ ] Martin serves non-empty MVT tiles at `/tile_heatmap/6/31/21?sort=overall&metric=score`
- [ ] Frontend compiles without TypeScript errors (`npm run type-check`)
- [ ] All pytest tests pass (`pytest api/tests/ -v`)
- [ ] All vitest tests pass (`npm run test`)
- [ ] Map renders Ireland choropleth coloured by overall score
- [ ] Switching sort → tiles recolour, pins update
- [ ] Switching sub-metric → tiles recolour, legend updates, pins unchanged
- [ ] Clicking tile → sidebar opens with correct data
- [ ] Sidebar close → tile highlight removed

---

## Phase 2 — Post-MVP Improvements

*These tasks are not required for basic functionality. Implement after Phase 1 is complete.*
*Each can be a separate agent session; dependencies are noted briefly.*

### Pipeline — Real Data Ingestion
Each is independent once the grid exists. Run in parallel.

- **P2-01 · Energy pipeline**: Implement `pipeline/energy/ingest.py` fully.
  Source: Global Wind Atlas GeoTIFF (download from globalwindatlas.info),
  Global Solar Atlas (globalsolaratlas.info), OSM Geofabrik Ireland power extract.
  Replaces synthetic energy_scores. Write metric_ranges with real data ranges.

- **P2-02 · Environment pipeline**: Implement `pipeline/environment/ingest.py`.
  Source: NPWS designated sites from data.gov.ie, OPW NIFM flood extents (CC BY-NC-ND),
  GSI landslide susceptibility. Hard exclusion logic is critical — test carefully.

- **P2-03 · Cooling pipeline**: Implement `pipeline/cooling/ingest.py`.
  Source: Met Éireann 1 km climate grids (contact Met Éireann for access),
  EPA WFD river network (data.gov.ie), OPW hydrometric stations (waterlevel.ie).

- **P2-04 · Connectivity pipeline**: Implement `pipeline/connectivity/ingest.py`.
  Source: ComReg broadband coverage (comreg.ie data hub), OSM Geofabrik roads,
  hardcoded IXP coordinates from config.py.

- **P2-05 · Planning pipeline**: Implement `pipeline/planning/ingest.py`.
  Source: MyPlan GZT zoning (myplan.ie), National Planning Applications (data.gov.ie),
  CSO Small Area Statistics 2022 (data.gov.ie).

- **P2-06 · Grid with real boundary**: Replace Option B polygon in `generate_grid.py`
  with a proper OSi national boundary file. Ensure correct county assignment.

### API
- **P2-07 · Response caching**: Add `fastapi-cache2` with in-memory backend to
  `/api/sorts` (permanent), `/api/metric-range` (until pipeline restart), `/api/pins`
  (10 min TTL).

- **P2-08 · `GET /api/tile/{id}/all`**: Return data across all sorts for one tile
  (deferred per ARCHITECTURE.md §11 D5). Useful for export/comparison feature.

### Frontend
- **P2-09 · Mobile responsive layout**: DataBar secondary row expand/collapse on small
  screens. Sidebar becomes bottom sheet on mobile. Touch-friendly tile click targets.

- **P2-10 · Pin icons + popups**: Replace placeholder circles with Lucide icon SVGs
  loaded as MapLibre sprite. Implement proper popup on pin click (name, type, key metric).

- **P2-11 · Admin weights panel**: Small form in the app (hidden behind a key input)
  that calls `PUT /api/weights`. Updates weights and shows a "Restart Martin to apply"
  notice.

- **P2-12 · Onboarding tooltip**: First-visit tooltip explaining the 5 km² tile
  resolution limitation (ARCHITECTURE.md §11 D2) and data centre use-case framing.

- **P2-13 · Error boundary + offline state**: Toast notifications for tile load failures
  (currently wired but not styled). Offline detection. Martin unavailable fallback.

- **P2-14 · Map style polish**: Replace demo tiles with a proper muted basemap.
  Options: OpenFreeMap Liberty style, MapTiler free tier, or Ordnance Survey Ireland tiles.

- **P2-15 · Figma design implementation**: Once Figma file is exported, apply design
  tokens to all component `<style scoped>` blocks. See design agent prompt pattern in
  the main conversation for how to give an agent the Figma context effectively.

### Deployment
- **P2-16 · Production Docker build**: Switch frontend compose target from `dev` to `prod`.
  Configure nginx to serve built assets with cache headers. Add SSL via Let's Encrypt.

- **P2-17 · Martin cache invalidation**: Document and script the workflow for
  post-pipeline Martin restart. Add a `/api/admin/invalidate-cache` endpoint
  (admin key protected) that `docker restart`s Martin via the Docker socket.

- **P2-18 · IDA sites data entry**: Create a small admin script or CSV import for
  manually adding IDA industrial site locations to the `ida_sites` table.
  IDA does not provide GIS downloads (ARCHITECTURE.md §11 D3).
