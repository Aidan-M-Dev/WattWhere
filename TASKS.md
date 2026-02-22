# HackEurope — Implementation Task Roadmap (Phase 2)

> **How to use this document**
> Phase 1 is complete — the stack runs, synthetic data is seeded, all API routes work,
> and the frontend renders Ireland with interactive choropleth tiles and sidebar panels.
> Each Phase 2 task can be handed verbatim to a separate Claude Code instance.
> They are sized for a single focused session. Dependencies are explicit.

---

## Dependency graph

```
[Phase 1 complete — all services healthy, synthetic data in DB]

Grid (tiles table populated)
    ├── P2-01 Energy pipeline      ─────┐
    ├── P2-02 Environment pipeline ─────┤
    ├── P2-03 Cooling pipeline     ─────┤──► P2-05 (overall/compute_composite.py, runs last)
    ├── P2-04 Connectivity pipeline─────┤
    └── P2-05 Planning pipeline    ─────┘

P2-06 Real grid boundary
    └── regenerates tiles → re-run P2-01 through P2-05 after

P2-07 API caching         (independent)
P2-08 /api/tile/{id}/all  (independent)

Frontend (all independent of pipeline):
    P2-09 Mobile layout
    P2-10 Pin icons + popups
    P2-11 Admin weights panel
    P2-12 Onboarding tooltip
    P2-13 Error boundary + offline
    P2-14 Map style polish
    P2-15 Figma design

Deployment (after frontend is polished):
    P2-16 Production Docker build
    P2-17 Martin cache invalidation
    P2-18 IDA sites data entry
```

P2-01 through P2-05 are independent of each other and can run in parallel once
the tiles table exists. `overall/compute_composite.py` (part of P2-05) runs last,
after ALL five sort pipelines complete.

---

## Phase 2 — Post-MVP Improvements

*Each task is self-contained and can be given to a separate agent session.*
*All pipeline tasks require the tiles table to be populated (Phase 1 complete).*

---

## Pipeline

---

### P2-02 · Environment Pipeline

**Goal**: Replace synthetic `environment_scores` with real environmental constraint data
from NPWS designated sites, OPW flood extents, and GSI landslide susceptibility.
Hard exclusion logic is production-critical — test it carefully.

#### Dependencies

No new packages beyond those already in `pipeline/requirements.txt`
(`geopandas`, `shapely`, `sqlalchemy`, `psycopg2`, `tqdm`, `pandas`).
Verify `geopandas >= 0.12` for `sjoin_nearest` and `overlay` support.

#### Read first
- `pipeline/environment/ingest.py` — all 9 function skeletons
- `pipeline/config.py` — `NPWS_SAC_FILE`, `NPWS_SPA_FILE`, `NPWS_NHA_FILE`,
  `OPW_FLOOD_CURRENT_FILE`, `OPW_FLOOD_FUTURE_FILE`, `GSI_LANDSLIDE_FILE`
- `ireland-data-sources.md` §4, §10 — download URLs, OPW CC BY-NC-ND licence
- `sql/tables.sql` — `environment_scores`, `tile_designation_overlaps`, `pins_environment`
- `ARCHITECTURE.md` §5.3 — hard exclusion rules, scoring caps, penalty logic

#### Deliverables

**1. Download source data** (into `/data/environment/`):

- **NPWS SAC/SPA/NHA**: from data.gov.ie NPWS datasets. GeoJSON or Shapefile.
  Convert to GeoPackage: `ogr2ogr -f GPKG npws_sac.gpkg input.shp`.
  Save as `npws_sac.gpkg`, `npws_spa.gpkg`, `npws_nha.gpkg`.
- **OPW NIFM flood extents**: current and future flood probability zones.
  Download from data.gov.ie OPW NIFM datasets. Licence: CC BY-NC-ND —
  add a comment in `ingest.py` noting this.
  Save as `opw_flood_current.gpkg`, `opw_flood_future.gpkg`.
- **GSI landslide susceptibility**: from data.gov.ie GSI.
  Save as `gsi_landslide_susceptibility.gpkg`.

**2. Implement `load_tiles(engine)`** — same pattern as P2-01 (`read_postgis`, to EPSG:2157).

**3. Implement `compute_designation_overlaps(tiles, sac, spa, nha)`**

Reproject all inputs to EPSG:2157. For each designation type, compute intersection
area ratio per tile using `gpd.overlay(tiles, designation, how='intersection')`:
```python
overlay = gpd.overlay(tiles[["tile_id","geometry"]], sac[["geometry","NAME"]], how="intersection")
overlay["pct_overlap"] = (overlay.geometry.area / (TILE_SIZE_M ** 2)) * 100
```

Group by `tile_id`. Mark boolean flags: `intersects_sac`, `intersects_spa`, `intersects_nha`.

`designation_overlap` score:
- SAC or SPA overlap → `0`
- NHA only → `max(0, 20 * (1 - pct_overlap/100))`
- No overlap → `100`

Build the `tile_designation_overlaps` row list: one row per (tile, named designation)
with columns `designation_name`, `designation_type`, `pct_overlap`.

**4. Implement `compute_flood_risk(tiles, flood_current, flood_future)`**

Boolean intersection check for each flood layer:
- `intersects_current_flood` → hard exclusion
- `intersects_future_flood` → penalty only

`flood_risk` score:
- Current flood zone: `0`
- Future flood zone only: `40`
- No flood zone: `100`

**5. Implement `compute_landslide_risk(tiles, landslide)`**

Spatial majority join: dominant GSI susceptibility class per tile wins.
Score mapping: `{"none": 100, "low": 70, "medium": 40, "high": 10}`.
GSI field name varies — check with `landslide.columns` and normalise class strings to lowercase.

**6. Implement `compose_environment_scores(designation_df, flood_df, landslide_df)`**

Merge all three on `tile_id`. Apply priority logic top-down:
```python
scores["has_hard_exclusion"] = (
    designation_df["intersects_sac"] |
    designation_df["intersects_spa"] |
    flood_df["intersects_current_flood"]
)
scores["score"] = 100.0
# Apply caps in order (worst wins)
scores.loc[landslide_df["landslide_susceptibility"] == "medium", "score"] -= 30
scores.loc[flood_df["intersects_future_flood"],   "score"] = scores["score"].clip(max=40)
scores.loc[designation_df["intersects_nha"],      "score"] = scores["score"].clip(max=20)
scores.loc[scores["has_hard_exclusion"],           "score"] = 0
scores["score"] = scores["score"].clip(0, 100).round(2)
```

Set `exclusion_reason` to `'SAC overlap'`, `'SPA overlap'`, or `'Current flood zone'`
(first match) for excluded tiles; `None` for all others.

**7. Implement `upsert_environment_scores(df, engine)`**

`ON CONFLICT (tile_id) DO UPDATE` all columns. Use 500-row `execute_values` batches.

**8. Implement `upsert_designation_overlaps(designation_df, engine)`**

Delete-then-insert (not upsert — overlap list is replaced wholesale per tile):
```python
# Step 1: DELETE FROM tile_designation_overlaps WHERE tile_id = ANY(%(ids)s)
# Step 2: INSERT INTO tile_designation_overlaps ...
```
Process in 500-tile batches to avoid huge `ANY(...)` lists.

**9. Implement `upsert_pins_environment(sac, spa, nha, flood_current, engine)`**

Compute polygon centroids for each designation type:
- `type='sac'`: SAC polygon centroids
- `type='spa'`: SPA polygon centroids
- `type='nha'`: NHA polygon centroids
- `type='flood_zone'`: OPW current flood zone centroids (one pin per zone polygon)

Assign `tile_id` via `ST_Within` query. `tile_id` may be `NULL` for coastal designations.

**10. Implement `main()`** — orchestrate all steps in sequence.

**11. Verify source links in `SidebarEnvironment.vue`**

Each section title already has a `source ↗` link (added alongside P2-01). Confirm the
URLs match the actual data providers used:
- Protected Area Designations → `https://npws.ie/maps-and-data/designated-site-data/download-boundary-data`
- Flood Risk → `https://www.floodinfo.ie`
- Landslide Susceptibility → `https://www.gsi.ie/en-ie/data-and-maps/Pages/default.aspx`

Update any URL if a different source was used.

#### Tests / acceptance criteria

```bash
docker compose --profile pipeline run --rm pipeline python environment/ingest.py

# Hard exclusion split
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT has_hard_exclusion, COUNT(*) FROM environment_scores GROUP BY has_hard_exclusion;"
# Expect: TRUE rows present (SAC/SPA/flood tiles), FALSE is the majority

# All hard exclusions must have score = 0
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT COUNT(*) FROM environment_scores WHERE has_hard_exclusion = true AND score != 0;"
# Expect: 0

# Designation overlaps populated
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT designation_type, COUNT(*) FROM tile_designation_overlaps GROUP BY designation_type;"
# Expect: rows for sac, spa, nha types

# After overall composite runs: hard exclusion tiles must have overall score = 0
# (verify after running overall/compute_composite.py in P2-05)
```

#### Common issues to pre-empt
- NPWS and OPW files may mix Polygons and MultiPolygons. Run
  `gdf.explode(index_parts=False)` before overlay to normalise geometry types.
- `gpd.overlay()` with large polygon datasets is slow. Use STRtree to filter
  candidates before full intersection: check bounding box overlap first.
- OPW and GSI files may be in EPSG:2157 or EPSG:4326 — always call `.to_crs("EPSG:2157")`
  before any spatial operation involving distances.
- OPW CC BY-NC-ND licence: add a comment in `ingest.py` and ensure the UI footer
  note in `SidebarEnvironment.vue` remains visible after this pipeline runs.

---

### P2-03 · Cooling Pipeline

**Goal**: Replace synthetic `cooling_scores` with real climate and water resource data
from Met Éireann grids, the EPA river network, OPW hydrometric stations, and GSI
aquifer data. After this task, Martin serves real temperature/rainfall choropleth tiles.

#### Dependencies

Same as P2-01: `rasterstats`, `rasterio`, `geopandas`, `numpy`, `sqlalchemy`,
`psycopg2`, `tqdm`. All must be in `pipeline/requirements.txt`.

#### Read first
- `pipeline/cooling/ingest.py` — all 10 function skeletons
- `pipeline/config.py` — `MET_EIREANN_TEMP_FILE`, `MET_EIREANN_RAIN_FILE`,
  `EPA_RIVERS_FILE`, `OPW_HYDRO_FILE`, `GSI_AQUIFER_FILE`
- `ireland-data-sources.md` §6, §7 — Met Éireann contact details, EPA/OPW sources
- `sql/tables.sql` — `cooling_scores`, `pins_cooling`, `metric_ranges`
- `ARCHITECTURE.md` §5.4 — scoring weights, temperature inversion rule

#### Deliverables

**1. Download source data** (into `/data/cooling/`):

- **Met Éireann climate grids**: contact climatology@met.ie for 1 km gridded
  mean annual temperature (°C) and rainfall (mm/yr) datasets.
  **Fallback**: use E-OBS gridded climate data from the Copernicus Climate Data Store
  (free, no contact required). See `ireland-data-sources.md §7`.
  Save as `met_temperature_grid.tif`, `met_rainfall_grid.tif`.
- **EPA river network**: WFD waterbodies GeoPackage from data.gov.ie (EPA).
  Save as `epa_river_network.gpkg`.
- **OPW hydrometric stations**: station locations from waterlevel.ie.
  Save as `opw_hydrometric_stations.gpkg`.
- **GSI aquifer productivity**: bedrock aquifer productivity from data.gov.ie (GSI).
  Save as `gsi_aquifer_productivity.gpkg`.

**2. Implement `extract_temperature_stats(tiles)` and `extract_rainfall_stats(tiles)`**

Same zonal-statistics approach as P2-01 `extract_raster_zonal_stats()`.
Temperature: zonal mean (°C). Rainfall: zonal mean (mm/yr).
**Store raw values — do NOT invert temperature here.** Temperature inversion
(lower = better for cooling) is handled inside the Martin `tile_heatmap` SQL function.

**3. Implement `compute_free_cooling_hours(temperature_series)`**

Linear approximation from mean annual temperature:
```python
# Ireland mean ≈10°C → roughly 7,000 free-cooling hrs/yr
free_hours = (8760 * (18 - temp_series) / 18).clip(0, 8760).round(0).astype(int)
```
Add a code comment noting this is an approximation from annual mean temperature,
not hourly MÉRA reanalysis data. See `ireland-data-sources.md §7` for MÉRA details.

**4. Implement `compute_water_proximity(tiles, rivers_lakes)`**

Nearest EPA waterbody per tile centroid using `sjoin_nearest()`.
Log-inverse score (`MAX_DIST_KM = 50` — Ireland has a dense river network):
```python
water_proximity = np.clip(100 * (1 - np.log1p(dist_km) / np.log1p(50)), 0, 100)
```
Extract `nearest_waterbody_name` from the EPA `WB_NAME` column (or equivalent).

**5. Implement `compute_aquifer_productivity(tiles, aquifer)`**

Spatial majority join against GSI aquifer productivity polygons.
Map the GSI productivity class string → 0–100:
```python
CLASS_MAP = {"high": 90, "moderate": 65, "low": 35, "negligible": 10, "none": 0}
```
The GSI field name varies (`PROD_SC`, `Productivity`, etc.) — check with
`aquifer.columns` and normalise class strings to lowercase before mapping.

**6. Implement `compute_cooling_scores(...)`**

Min-max normalise temperature and rainfall across all tiles:
```python
temp_norm  = 100 * (temp - temp.min())    / (temp.max()    - temp.min())
rain_norm  = 100 * (rain - rain.min())    / (rain.max()    - rain.min())
```

Composite (temperature stored as raw °C; Martin SQL handles the inversion for the
colour ramp, but the composite score here must treat lower temperature as better):
```python
score = 0.40 * (100 - temp_norm) + 0.35 * water_proximity + 0.25 * rain_norm
```
Clamp to `[0, 100]`, round to 2 decimal places.

**7. Implement `write_metric_ranges(temp_series, rainfall_series, engine)`**

```python
ranges = [
    ("cooling", "temperature", float(temp_series.min()), float(temp_series.max()), "°C"),
    ("cooling", "rainfall",    float(rain_series.min()), float(rain_series.max()), "mm/yr"),
]
# INSERT INTO metric_ranges ... ON CONFLICT (sort, metric) DO UPDATE SET min_val, max_val
```

**8. Implement `upsert_cooling_scores(df, engine)` and `upsert_pins_cooling(...)`**

Pins:
- `type='hydrometric_station'`: OPW station locations with name and mean flow.
- `type='waterbody'`: EPA river/lake centroids — filter to significant waterbodies
  (e.g. surface area > 1 km² or Strahler order ≥ 3 if available).
- `type='met_station'`: Met Éireann synoptic station coordinates if available
  (coordinates from `ireland-data-sources.md §7`).

**9. Implement `main()`** — orchestrate all steps. Remove `raise NotImplementedError`.

**10. Verify source links in `SidebarCooling.vue`**

Each section title already has a `source ↗` link (added alongside P2-01). Confirm the
URLs match the actual data providers used:
- Climate → `https://met.ie/climate/available-data`
- Water Resources → `https://gis.epa.ie/GetData/Download`
- Hydrometric Station → `https://waterlevel.ie`
- Groundwater → `https://www.gsi.ie/en-ie/data-and-maps/Pages/default.aspx`

Update any URL if a different source was used.

#### Tests / acceptance criteria

```bash
docker compose --profile pipeline run --rm pipeline python cooling/ingest.py

docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT MIN(temperature), MAX(temperature), AVG(temperature),
         MIN(rainfall),    MAX(rainfall),    AVG(score)
  FROM cooling_scores;"
# Expect: temperature ~8–13°C, rainfall ~700–2500 mm/yr, score spread

docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT * FROM metric_ranges WHERE sort = 'cooling';"
# Expect: 2 rows (temperature, rainfall) with real data ranges

docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT MIN(free_cooling_hours), MAX(free_cooling_hours) FROM cooling_scores;"
# Expect: values in range ~4000–7500 hrs/yr
```

#### Common issues to pre-empt
- Met Éireann GeoTIFFs may be in Irish Transverse Mercator (EPSG:2157) —
  reproject tile geometries to match before running zonal stats.
- E-OBS data is on a 0.1° grid (much coarser than 1 km) — zonal mean will still
  work but will be less spatially precise. Note this in a code comment.
- GSI aquifer file may contain `NULL` productivity values — handle with
  `.fillna("none")` before class mapping.

---

### P2-04 · Connectivity Pipeline

**Goal**: Replace synthetic `connectivity_scores` with real ComReg broadband coverage,
OSM road distances, and hardcoded IXP coordinates. After this task, Martin serves
real connectivity choropleth tiles.

#### Dependencies

Add to `pipeline/requirements.txt`:
```
pyproj>=3.6.0
```
All other imports (`geopandas`, `numpy`, `pandas`, `sqlalchemy`, `psycopg2`, `tqdm`,
`shapely`) must be present. `pyproj` is typically installed as a geopandas dependency
but declare it explicitly for the CRS transform step.

#### Read first
- `pipeline/connectivity/ingest.py` — all 7 function skeletons
- `pipeline/config.py` — `COMREG_BROADBAND_FILE`, `OSM_ROADS_FILE`,
  `INEX_DUBLIN_COORDS`, `INEX_CORK_COORDS`
- `ireland-data-sources.md` §8 — ComReg data hub URL, OSM roads via Geofabrik
- `sql/tables.sql` — `connectivity_scores`, `pins_connectivity`
- `ARCHITECTURE.md` §5.5 — scoring weights, D6 (no public fibre data for Ireland)

#### Deliverables

**1. Download source data** (into `/data/connectivity/`):

- **ComReg broadband**: download the broadband coverage GeoPackage from the ComReg
  data hub. Coverage tiers: `UFBB` (≥100 Mbps), `SFBB` (≥30 Mbps), `FBB` (≥10 Mbps),
  `BB` (<10 Mbps). See `ireland-data-sources.md §8` for the URL.
  Save as `comreg_broadband.gpkg`.
- **OSM roads**: from Geofabrik Ireland extract. Filter to
  `highway=motorway`, `motorway_link`, `primary` using `osmium tags-filter`.
  Save as `osm_ireland_roads.gpkg`.

IXP coordinates (`INEX_DUBLIN_COORDS`, `INEX_CORK_COORDS`) are already in
`config.py` — no download required.

**2. Implement `compute_ix_distances(tiles)`**

Convert IXP coordinates from EPSG:4326 to EPSG:2157 using `pyproj`:
```python
from pyproj import Transformer
t = Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)
dublin_itm = Point(*t.transform(*INEX_DUBLIN_COORDS))
cork_itm   = Point(*t.transform(*INEX_CORK_COORDS))
```

Compute distance from each tile centroid (already in EPSG:2157) to both IXPs.
Store `inex_dublin_km` and `inex_cork_km`. Log-inverse score using the closer IXP
(`MAX_DIST_KM = 300` — Donegal to Dublin is ~300 km):
```python
min_km = np.minimum(dublin_km, cork_km)
ix_distance = np.clip(100 * (1 - np.log1p(min_km) / np.log1p(300)), 0, 100)
```

**3. Implement `compute_broadband(tiles, comreg)`**

Spatial majority join: which ComReg tier covers the largest fraction of each tile?
Use `gpd.overlay(tiles, comreg, how='intersection')`, compute area fractions per tier,
assign the majority tier. Map to 0–100:
```python
TIER_SCORE = {"UFBB": 95, "SFBB": 72, "FBB": 45, "BB": 17}
```
Tiles with no ComReg coverage: `broadband = 0`, `broadband_tier = None`.

**4. Implement `compute_road_access(tiles, roads)`**

Filter OSM roads to motorway and national primary.
Use `sjoin_nearest()` from tile centroid to road line geometries (geopandas ≥ 0.12
supports nearest join to LineString geometries). Distance in EPSG:2157, stored as km.

Log-inverse score (`MAX_DIST_KM = 50`):
```python
road_access = np.clip(100 * (1 - np.log1p(dist_km) / np.log1p(50)), 0, 100)
```

Extract `nearest_motorway_junction_name` from OSM `ref` or `name` tag.
Set `nearest_rail_freight_km = None` — no rail freight GIS data for Ireland
(see `ireland-data-sources.md §8`). This is a known gap (ARCHITECTURE.md §5.5).

**5. Implement `compute_connectivity_scores(ix_df, broadband_df, road_df)`**

```python
# Rail placeholder: 15% weight assigned to 0 until rail data available
score = (0.35 * broadband + 0.30 * ix_distance + 0.20 * road_access + 0.15 * 0)
score = score.clip(0, 100).round(2)
```

**6. Implement `upsert_connectivity_scores(df, engine)`** — same upsert pattern as P2-01.

**7. Implement `upsert_pins_connectivity(engine)`**

Three pin types:
- `type='internet_exchange'`: insert INEX Dublin and INEX Cork as Point features
  using coordinates from `config.py`. Two rows total — check they don't already exist.
- `type='motorway_junction'`: OSM nodes with `highway=motorway_junction`. Cluster
  junctions within 500m of each other to avoid pin overload (keep representative point).
- `type='broadband_area'`: centroids of the top-50 largest UFBB coverage polygons
  by area. Skip if ComReg has no UFBB polygons.

**8. Implement `main()`** — orchestrate all steps.

**9. Verify source links in `SidebarConnectivity.vue`**

Each section title already has a `source ↗` link (added alongside P2-01). Confirm the
URLs match the actual data providers used:
- Internet Exchanges → `https://www.peeringdb.com/ix/48`
- Broadband → `https://datamaps-comreg.hub.arcgis.com`
- Road Access → `https://www.openstreetmap.org`

Update any URL if a different source was used.

#### Tests / acceptance criteria

```bash
docker compose --profile pipeline run --rm pipeline python connectivity/ingest.py

docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT MIN(inex_dublin_km), MAX(inex_dublin_km),
         MIN(score), MAX(score), AVG(score)
  FROM connectivity_scores;"
# Expect: Dublin distances span ~0–350 km; scores spread across 0–100

docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT type, COUNT(*) FROM pins_connectivity GROUP BY type;"
# Expect: internet_exchange=2, plus motorway and broadband rows
```

#### Common issues to pre-empt
- ComReg broadband file may contain millions of small polygons. Call `gdf.buffer(0)`
  to fix any invalid geometries before overlay, and ensure a spatial index is built.
- `sjoin_nearest` to LineString geometries requires geopandas ≥ 0.12.
  If road lines are split into many short segments, it may be slow — merge collinear
  segments with `roads.dissolve()` before the join.
- The fibre limitation note (ARCHITECTURE.md §11 D6) must remain in
  `SidebarConnectivity.vue`. Do not remove it now that real ComReg data is loaded.

---

### P2-05 · Planning Pipeline + Overall Composite

**Goal**: Replace synthetic `planning_scores` with real MyPlan GZT zoning, national
planning applications, and CSO population data. Then implement and run
`overall/compute_composite.py` to produce real composite scores from all 5 sort tables.

**Dependency**: `overall/compute_composite.py` must run AFTER P2-01, P2-02, P2-03,
P2-04, and P2-05 planning ingest all complete successfully.

#### Dependencies

Planning ingest: `geopandas`, `numpy`, `pandas`, `sqlalchemy`, `psycopg2`, `tqdm`.
Overall composite: `pandas`, `sqlalchemy`, `psycopg2` (no spatial ops needed).
All must be in `pipeline/requirements.txt`.

#### Read first
- `pipeline/planning/ingest.py` — all 9 function skeletons
- `pipeline/overall/compute_composite.py` — all 5 function skeletons
- `pipeline/config.py` — `MYPLAN_ZONING_FILE`, `PLANNING_APPLICATIONS_FILE`,
  `CSO_POPULATION_FILE`
- `ireland-data-sources.md` §5, §9 — MyPlan, planning applications, CSO sources
- `sql/tables.sql` — `planning_scores`, `tile_planning_applications`, `overall_scores`
- `ARCHITECTURE.md` §5.6 (planning scoring logic), §5.1 (hard exclusion propagation)

#### Deliverables — Planning pipeline

**1. Download source data** (into `/data/planning/`):

- **MyPlan GZT zoning**: GeoPackage from MyPlan.ie (account required). Filter to
  the Republic of Ireland 26 counties. Save as `myplan_gzt_zoning.gpkg`.
- **Planning applications**: from data.gov.ie planning datasets or individual local
  authority planning portals. Filter to application type `data centre`, `industrial`,
  `technology`. Save as `planning_applications.gpkg`.
- **CSO Small Area stats 2022**: boundary files from data.gov.ie (CSO Census 2022).
  Save as `cso_small_area_stats.gpkg`.

**2. Implement `compute_zoning_overlay(tiles, zoning)`**

Overlay tiles with zoning polygons (area-weighted). The MyPlan GZT layer uses
category codes (e.g. `E1`=Enterprise, `B1`=Industrial, `M`=Mixed Use, `A`=Agricultural,
`R`=Residential). Build a mapping from `ireland-data-sources.md §9`.

Compute 6 percentage columns: `pct_industrial`, `pct_enterprise`, `pct_mixed_use`,
`pct_agricultural`, `pct_residential`, `pct_other` (sum = 100).

`zoning_tier` scoring:
```python
ie_pct = pct_industrial + pct_enterprise
tier = 10  # default (unzoned/agri)
if pct_residential > 50:
    tier = min(tier, 10)
elif ie_pct > 50:
    tier = 80 + (ie_pct / 100) * 20
elif pct_mixed_use > 30:
    tier = 50 + (pct_mixed_use / 100) * 20
elif pct_agricultural > 50:
    tier = 10 + (pct_agricultural / 100) * 20
```

**3. Implement `compute_planning_applications(tiles, applications)`**

Spatial join applications to tiles for applications that fall within each tile.
Filter to data centre / industrial application types.

`planning_precedent` score: tiles within 10 km of any DC planning application
receive a bonus. Use a 10 km buffer:
```python
buffered = tiles.copy()
buffered["geometry"] = tiles.geometry.buffer(10_000)  # EPSG:2157, metres
has_dc_nearby = gpd.sjoin(buffered, dc_apps, how="left", predicate="intersects")
```
Score: `+40` for proximity to any DC application, `+20` additional if status is `'granted'`
(max `planning_precedent = 60`).

Build per-tile application list for `tile_planning_applications`:
one row per application with `app_ref`, `app_type`, `status`, `app_date`.

**4. Implement `compute_population_density(tiles, cso_pop)`**

Area-weighted population sum per tile from CSO Small Area boundaries:
`pop_density = total_persons / tile_area_km2` (5 km² nominal).

**5. Implement `compute_nearest_ida_km(tiles, engine)`**

Load `ida_sites` via `gpd.read_postgis()`. If the table is empty (IDA sites not yet
entered — see P2-18), return `pd.Series(dtype=float)` with all `NaN`. Handle gracefully.

**6. Implement `compose_planning_scores(zoning_df, planning_df, pop_density, ida_km)`**

```python
score = zoning_df["zoning_tier"].copy()
score += np.where(planning_df["planning_precedent"] > 40, 10, 0)
score -= np.where(zoning_df["pct_residential"] > 0, 20, 0)
score = score.clip(0, 100).round(2)
```

**7. Implement `upsert_planning_scores(df, engine)`** — same upsert pattern as P2-01.

**8. Implement `upsert_planning_applications(planning_df, engine)`**

Delete-then-insert (same pattern as `tile_designation_overlaps` in P2-02).
Delete all rows for affected `tile_id`s, then insert fresh list.

**9. Implement `upsert_pins_planning(zoning, applications, engine)`**

- `type='zoning_parcel'`: centroids of Industrial/Enterprise parcels.
  Cluster to ~500 representative pins by dissolving small adjacent parcels.
- `type='planning_application'`: centroids of recent DC planning applications.
  Do NOT insert IDA sites here — managed separately via P2-18.

**10. Implement `main()`** for planning ingest.

#### Deliverables — Overall composite (`pipeline/overall/compute_composite.py`)

**Run AFTER all five sort pipeline tasks complete.**

**11. Implement `load_weights(engine)`**

```python
with engine.connect() as conn:
    row = conn.execute(text(
        "SELECT energy, connectivity, environment, cooling, planning "
        "FROM composite_weights WHERE id = 1"
    )).fetchone()
return dict(row._mapping)
```
Always read from DB — do not use `DEFAULT_WEIGHTS` from `config.py`.

**12. Implement `load_sort_scores(engine)`**

Execute the INNER JOIN query from the docstring across all 5 sort tables.
Log a warning if the result count differs from `SELECT COUNT(*) FROM tiles`.
Missing tiles (not in all 5 tables) will not receive an overall score.

**13. Implement `compute_overall_scores(scores_df, weights)`**

```python
score = (
    scores_df["energy_score"]        * weights["energy"]      +
    scores_df["environment_score"]   * weights["environment"] +
    scores_df["cooling_score"]       * weights["cooling"]     +
    scores_df["connectivity_score"]  * weights["connectivity"]+
    scores_df["planning_score"]      * weights["planning"]
)
score = np.where(scores_df["has_hard_exclusion"], 0, score)
return score.clip(0, 100).round(2)
```

**14. Implement `compute_nearest_data_centre_km(engine)`**

Use the lateral join SQL from the docstring. If `pins_overall` has no `data_centre`
type pins, return an empty `pd.Series` with all `NaN` (acceptable until P2-18).

**15. Implement `upsert_overall_scores(df, engine)`** — `ON CONFLICT (tile_id) DO UPDATE`.

**16. Implement `main()`** for overall composite. After completion, remind the user
to restart Martin: `docker compose restart martin`.

**17. Verify source links in `SidebarPlanning.vue`**

Each section title already has a `source ↗` link (added alongside P2-01). Confirm the
URLs match the actual data providers used:
- Zoning Breakdown → `https://myplan.ie`
- Planning Applications → `https://data.gov.ie`
- Context (IDA sites) → `https://www.idaireland.com/locate-in-ireland/available-properties`

Update any URL if a different source was used.

#### Tests / acceptance criteria

```bash
docker compose --profile pipeline run --rm pipeline python planning/ingest.py
docker compose --profile pipeline run --rm pipeline python overall/compute_composite.py

# Planning data
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT AVG(pct_industrial), AVG(pct_residential), AVG(zoning_tier), AVG(score)
  FROM planning_scores;"

# Overall scores — hard exclusions must propagate
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT MIN(score), MAX(score), AVG(score),
         COUNT(*) FILTER (WHERE score = 0) AS zero_count,
         COUNT(*) AS total
  FROM overall_scores;"
# Expect: zero_count matches environment_scores.has_hard_exclusion count

# Weights applied correctly (spot check 5 non-excluded tiles)
docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT o.score,
         ROUND((e.score*0.25 + env.score*0.20 + c.score*0.15
                + cn.score*0.25 + p.score*0.15)::numeric, 2) AS expected
  FROM overall_scores o
  JOIN energy_scores e       ON e.tile_id = o.tile_id
  JOIN environment_scores env ON env.tile_id = o.tile_id
  JOIN cooling_scores c      ON c.tile_id = o.tile_id
  JOIN connectivity_scores cn ON cn.tile_id = o.tile_id
  JOIN planning_scores p     ON p.tile_id = o.tile_id
  WHERE env.has_hard_exclusion = false LIMIT 5;"
# Expect: score ≈ expected (within rounding; weights from DB may differ)

docker compose restart martin
```

---

### P2-07 · Response Caching

**Goal**: Add in-memory response caching to FastAPI routes that serve slowly-changing
data. Reduce database load and improve response times for `/api/sorts`,
`/api/metric-range`, and `/api/pins`.

#### Dependencies

Add to `api/requirements.txt`:
```
fastapi-cache2>=0.2.1
```
No Redis required — use the in-memory backend.

#### Read first
- `api/main.py` — lifespan, middleware, router mounts
- `api/routes/sorts.py`, `api/routes/metric_range.py`, `api/routes/pins.py`
- `api/routes/weights.py` — admin key pattern (reuse for cache invalidation endpoint)

#### Deliverables

**1. Initialise cache in `api/main.py`**

```python
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    FastAPICache.init(InMemoryBackend())
    yield
```

**2. Apply `@cache()` decorators**

```python
from fastapi_cache.decorator import cache

# sorts.py — sort metadata never changes at runtime; long TTL
@router.get("/sorts")
@cache(expire=3600)           # 1 hour
async def get_sorts(): ...

# metric_range.py — changes only after pipeline re-run
@router.get("/metric-range")
@cache(expire=1800)           # 30 minutes
async def get_metric_range(sort: str, metric: str): ...

# pins.py — GeoJSON per sort; moderate TTL
@router.get("/pins")
@cache(expire=600)            # 10 minutes
async def get_pins(sort: SortType): ...
```

Do NOT cache `/api/tile/{tile_id}` (per-tile cardinality too high) or
`GET /api/weights` (must reflect live DB state for the admin panel).

**3. Create `api/routes/admin.py`** (new file)

```python
from fastapi import APIRouter, Header, HTTPException
from fastapi_cache import FastAPICache

router = APIRouter()

async def _check_admin_key(key: str = Header(alias="X-Admin-Key")):
    import os
    if key != os.environ.get("ADMIN_KEY", ""):
        raise HTTPException(status_code=401, detail="Invalid admin key")

@router.post("/admin/invalidate-cache")
async def invalidate_cache(admin_key: str = Header(alias="X-Admin-Key")):
    await _check_admin_key(admin_key)
    await FastAPICache.clear()
    return {"status": "cache cleared"}
```

Mount `admin` router in `main.py`: `app.include_router(admin.router, prefix="/api")`.

**4. Update `api/tests/test_routes.py`**

Add tests:
- `GET /api/sorts` called twice → both return 200 with identical payloads
- `POST /api/admin/invalidate-cache` without key → 401
- `POST /api/admin/invalidate-cache` with correct key → 200, `{"status": "cache cleared"}`

#### Tests / acceptance criteria

```bash
cd api && pytest tests/ -v
# All existing + new cache tests must pass

# Live smoke test
curl -s -X POST -H "X-Admin-Key: devkey" \
  http://localhost:8000/api/admin/invalidate-cache
# Expect: {"status":"cache cleared"}

curl -s -o /dev/null -w "%{http_code}" -X POST \
  http://localhost:8000/api/admin/invalidate-cache
# Expect: 401
```

---

### P2-08 · `GET /api/tile/{id}/all` Endpoint

**Goal**: Implement the endpoint that returns data across all 6 sorts for a single
tile in one response. Useful for comparison views and data export.
(Deferred in Phase 1 per ARCHITECTURE.md §11 D5.)

#### Dependencies

No new packages. All `_get_*` helper functions already exist in `api/routes/tile.py`.

#### Read first
- `api/routes/tile.py` — all 6 `_get_*` functions (already implemented in Phase 1)
- `ARCHITECTURE.md` §7 (`GET /api/tile/{tile_id}/all` spec), §11 D5

#### Deliverables

**1. Add the endpoint to `api/routes/tile.py`**

```python
@router.get("/tile/{tile_id}/all")
async def get_tile_all(tile_id: int, conn=Depends(get_conn)):
    """Return data from all 6 sorts for a single tile in one response."""
    dispatch = {
        "overall":      _get_overall,
        "energy":       _get_energy,
        "environment":  _get_environment,
        "cooling":      _get_cooling,
        "connectivity": _get_connectivity,
        "planning":     _get_planning,
    }
    results = {}
    found_any = False
    for sort, fn in dispatch.items():
        try:
            results[sort] = await fn(tile_id, conn)
            found_any = True
        except HTTPException as e:
            results[sort] = None if e.status_code == 404 else (_ for _ in ()).throw(e)
    if not found_any:
        raise HTTPException(status_code=404, detail="Tile not found in any sort table")
    return results
```

**2. Add tests to `api/tests/test_routes.py`**

```python
async def test_tile_all_returns_all_sorts():
    r = await client.get("/api/tile/1/all")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {
        "overall", "energy", "environment", "cooling", "connectivity", "planning"
    }
    assert data["overall"]["tile_id"] == 1

async def test_tile_all_not_found():
    r = await client.get("/api/tile/99999/all")
    assert r.status_code == 404
```

#### Tests / acceptance criteria

```bash
curl -s "http://localhost:8000/api/tile/1/all" | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert set(data.keys()) == {'overall','energy','environment','cooling','connectivity','planning'}
print('all sorts present, tile_id =', data['overall']['tile_id'])
"
```

---

## Frontend

---

### P2-10 · Pin Icons + Popups

**Goal**: Replace placeholder circle pins with sort-specific Lucide SVG icons loaded
as a MapLibre sprite. Clicking a pin opens a styled popup showing the pin's name,
type, and a key metric.

#### Dependencies

Add to `frontend/package.json` devDependencies:
```json
"@mapbox/spritezero-cli": "^7.0.0"
```
(CLI tool for generating MapLibre sprite sheets from SVG files.)

#### Read first
- `ARCHITECTURE.md` §6.2 (pin layer implementation notes — circle vs symbol)
- `frontend/src/components/MapView.vue` — `setupLayers()`, `pins-unclustered` layer
- `frontend/src/types/index.ts` — pin type strings used per sort

#### Deliverables

**1. Export Lucide SVGs for each pin type**

Required icons (from ARCHITECTURE.md §5.1–§5.6):
- Overall: `server`, `building`
- Energy: `wind`, `zap`, `circle-dot`
- Environment: `shield`, `bird`, `tree-pine`, `droplets`
- Cooling: `gauge`, `droplet`, `thermometer`
- Connectivity: `globe`, `circle-dot`, `wifi`
- Planning: `factory`, `file-text`

Copy the SVG source for each icon from the `lucide-static` package
(install: `npm install -D lucide-static`). Save each as
`frontend/public/sprites/svg/{type}.svg` where `{type}` matches the pin's
`type` property value (e.g. `data_centre.svg`, `wind_farm.svg`).

**2. Generate the sprite sheet**

```bash
cd frontend/public/sprites
npx @mapbox/spritezero-cli sprite svg/
# Outputs: sprite.json, sprite.png, sprite@2x.png
```

**3. Add sprite to the MapLibre style**

In `MapView.vue` `initMap()`, after fetching the base style, inject the sprite:
```typescript
const styleRes = await fetch(BASE_STYLE_URL)
const style = await styleRes.json()
style.sprite = `${window.location.origin}/sprites/sprite`
map = new maplibregl.Map({ style, center: [...], zoom: 6.5 })
```

If the base style already defines a sprite (Liberty/Positron do), use
`map.addImage()` after `map.on('load')` to add each custom icon individually —
this avoids overwriting the base style's icon set:
```typescript
const img = await map.loadImage(`/sprites/svg/${type}.svg`)
map.addImage(type, img.data)
```
Prefer `addImage()` — it is more reliable with third-party base styles.

**4. Replace `type: "circle"` with `type: "symbol"` for unclustered pins**

In `setupLayers()`:
```typescript
map.addLayer({
  id: 'pins-unclustered',
  type: 'symbol',
  source: 'pins',
  filter: ['!', ['has', 'point_count']],
  layout: {
    'icon-image': ['get', 'type'],   // must match addImage() name
    'icon-size': 0.75,
    'icon-allow-overlap': true,
    'icon-anchor': 'bottom',
  }
})
```

**5. Implement pin click popup**

```typescript
map.on('click', 'pins-unclustered', (e) => {
  const feat = e.features![0]
  const coords = (feat.geometry as GeoJSON.Point).coordinates as [number, number]
  const { name, type, ...props } = feat.properties!
  new maplibregl.Popup({ closeButton: true, maxWidth: '260px' })
    .setLngLat(coords)
    .setHTML(`
      <div class="pin-popup">
        <strong>${name ?? type.replace(/_/g, ' ')}</strong>
        <div class="pin-popup__type">${type.replace(/_/g, ' ')}</div>
        ${buildPopupExtra(type, props)}
      </div>
    `)
    .addTo(map)
})
map.on('mouseenter', 'pins-unclustered', () => { map.getCanvas().style.cursor = 'pointer' })
map.on('mouseleave', 'pins-unclustered', () => { map.getCanvas().style.cursor = '' })
```

Implement `buildPopupExtra(type, props)` returning type-specific HTML
(e.g. wind speed for `wind_farm`, voltage for `substation`, flow rate for `hydrometric_station`).

**6. Style the popup**

Add to `frontend/src/assets/main.css`:
```css
.maplibregl-popup-content { background: #1e2030; color: #e0e0e0; border-radius: 8px; }
.pin-popup strong        { font-size: 13px; display: block; margin-bottom: 4px; }
.pin-popup__type         { font-size: 11px; opacity: 0.7; text-transform: capitalize; }
```

#### Tests / acceptance criteria

```bash
# Sprites must be present
ls frontend/public/sprites/
# Expect: sprite.json  sprite.png  sprite@2x.png  svg/

# Manual visual test:
# 1. Zoom to level 8+ → pins render as icons, not filled circles
# 2. Hover over pin → cursor changes to pointer
# 3. Click pin → popup appears with name, type, and metric info
# 4. At zoom < 10 → cluster circles appear (unchanged from Phase 1)
# 5. Click cluster → map zooms in to expand it
```

---

### P2-11 · Admin Weights Panel

**Goal**: Add a hidden admin panel in the frontend that allows updating composite
score weights via `PUT /api/weights`. Accessible only via a keyboard shortcut and
admin key. Shows a "restart Martin" notice after saving.

#### Dependencies

No new npm packages.

#### Read first
- `api/routes/weights.py` — PUT endpoint shape (`X-Admin-Key` header, weight JSON body)
- `frontend/src/stores/suitability.ts` — `GET /api/weights` (for reading current weights)
- `ARCHITECTURE.md` §11 D4 (admin key intentionally minimal)

#### Deliverables

**1. Create `frontend/src/components/AdminPanel.vue`** (new file)

Three states: `hidden`, `key-entry`, `form`.

Keyboard trigger: `Ctrl+Shift+A` on `window` `keydown`. Wire in `onMounted` /
`onUnmounted`.

**Key entry state**: text input for the admin key + "Enter" button. On submit,
test the key by calling `GET /api/weights` with the key as `X-Admin-Key` header.
If 401 → show "Invalid key" error. If 200 → transition to `form` state and populate
weight inputs from the response.

**Form state**: one `<input type="number" min="0" max="1" step="0.05">` per sort.
Show live sum of all five weights. Disable "Save" if `|sum - 1.0| > 0.01`.

On save, call `PUT /api/weights` with the updated weights object and `X-Admin-Key`
header. On success, show a persistent notice:

> "Weights updated. Restart Martin to apply: `docker compose restart martin`"

Press Escape at any state → hide the panel.

**2. Mount `AdminPanel` in `App.vue`** (always in DOM, conditionally visible):
```html
<AdminPanel />
```

#### Tests / acceptance criteria

```bash
cd frontend && npm run type-check
# Expect: 0 errors

# Manual test:
# 1. Press Ctrl+Shift+A → key entry form appears
# 2. Enter wrong key → "Invalid key" error shown
# 3. Enter "devkey" → weight form appears with current values
# 4. Adjust weights so they sum to 1.00 → Save button enables
# 5. Click Save → restart notice appears
# 6. Open DevTools Network → confirm PUT /api/weights with correct body
# 7. Press Escape → panel hides
```

---

### P2-12 · Onboarding Tooltip

**Goal**: First-time visitors see a brief introductory tooltip explaining the platform,
the 5 km² tile resolution limitation, and the data centre siting use case. The tooltip
is shown once per browser (localStorage flag) and is dismissible.

#### Dependencies

No new packages.

#### Read first
- `ARCHITECTURE.md` §11 D2 — tile resolution limitation (must be acknowledged in UI)
- `frontend/src/components/MapView.vue` — where tooltip renders above

#### Deliverables

**1. Create `frontend/src/components/OnboardingTooltip.vue`** (new file)

Show condition:
```typescript
const hasSeenTooltip = ref(localStorage.getItem('onboarding_seen') === 'true')
function dismiss() {
  localStorage.setItem('onboarding_seen', 'true')
  hasSeenTooltip.value = true
}
```

Content:
- Title: "Ireland Data Centre Suitability"
- Three bullet points:
  1. "Score ~14,000 grid tiles across 5 thematic sorts — switch sorts to compare factors."
  2. "Tiles are ~5 km² — designed for regional analysis, not site-level precision."
  3. "Click any tile to see a detailed breakdown in the sidebar."
- "Got it" dismiss button.

Design: semi-transparent dark overlay (`rgba(0,0,0,0.5)`) behind a centered card
(max-width 440px). Fade-in animation (200ms CSS transition on opacity).

**2. Mount in `App.vue`**

```html
<OnboardingTooltip />
<!-- Component handles its own show/hide via localStorage -->
```

#### Tests / acceptance criteria

```bash
# Manual test:
# 1. Clear localStorage (DevTools → Application → Storage → Clear All)
# 2. Reload http://localhost:5173
# 3. Onboarding tooltip appears on load
# 4. Click "Got it" → tooltip dismisses
# 5. Reload → tooltip does NOT appear again
# 6. DevTools → localStorage → 'onboarding_seen': 'true'
```

---

### P2-13 · Error Boundary + Offline State

**Goal**: Toast notifications appear when Martin tile fetches fail or the API is
unreachable. The app shows a clear offline state when the network is unavailable.
The sidebar already has an inline error state — wire it up correctly.

#### Dependencies

No new npm packages. Implement a `useToast` composable internally.

#### Read first
- `ARCHITECTURE.md` §6.2 (error state spec — non-blocking toast for Martin failures)
- `frontend/src/components/MapView.vue` — existing `map.on('error', ...)` handler
- `frontend/src/stores/suitability.ts` — `error` state field, `fetchTileDetail()`

#### Deliverables

**1. Create `frontend/src/composables/useToast.ts`** (new file)

```typescript
import { ref } from 'vue'

export interface Toast { id: string; message: string; type: 'error' | 'warning' | 'info' }

const toasts = ref<Toast[]>([])

export function useToast() {
  function push(toast: Toast) {
    if (toasts.value.some(t => t.id === toast.id)) return  // deduplicate
    toasts.value.push(toast)
    if (toast.type !== 'warning') {
      setTimeout(() => remove(toast.id), 4000)              // auto-dismiss after 4s
    }
  }
  function remove(id: string) {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }
  return { toasts, push, remove }
}
```

**2. Create `frontend/src/components/ToastContainer.vue`** (new file)

Renders the active toast list. Position: fixed top-right, `z-index: 1000`.
Max 3 toasts visible at once (oldest dismissed first if exceeded).
Each toast: dismiss button (×), colour coded by type (red=error, amber=warning).
CSS `transition` for slide-in / fade-out.

Mount in `App.vue`:
```html
<ToastContainer />
```

**3. Martin tile error handler in `MapView.vue`**

```typescript
const { push } = useToast()
map.on('error', (e) => {
  if (e.sourceId === 'tiles-mvt') {
    push({
      id: 'martin-error',
      message: 'Map tile data unavailable — check server',
      type: 'error'
    })
  }
})
```

**4. Offline / online detection in `App.vue`**

```typescript
const { push, remove } = useToast()
window.addEventListener('offline', () =>
  push({ id: 'offline', message: 'No internet connection', type: 'warning' })
)
window.addEventListener('online', () => remove('offline'))
```

**5. API fetch error handling in `suitability.ts`**

All `fetch()` calls are already wrapped in try/catch in the store. Verify that:
- On network error, `store.error` is set to a human-readable string.
- The sidebar `retry()` function calls `store.fetchTileDetail(store.selectedTileId, store.activeSort)`.
- Any `store.error` set during a fetch that is not tile-related also pushes a toast.

**6. Write tests in `frontend/src/components/ToastContainer.test.ts`** (new file)

```typescript
it('renders a toast when push() is called')
it('does not duplicate toasts with the same id')
it('auto-dismisses error toasts after 4000ms', async () => { vi.useFakeTimers(); ... })
it('does not auto-dismiss warning toasts')
```

#### Tests / acceptance criteria

```bash
cd frontend && npm run test
# Expect: all existing + new toast tests pass

# Manual test — Martin down:
# 1. docker compose stop martin
# 2. Reload http://localhost:5173
# 3. Within a few seconds → toast "Map tile data unavailable" appears (red)
# 4. Toast auto-dismisses after 4s
# 5. docker compose start martin → tiles reload on next interaction

# Manual test — offline:
# 1. Chrome DevTools → Network → Offline
# 2. Toast "No internet connection" appears immediately (amber)
# 3. Toggle back to Online → amber toast dismisses
```

---

### P2-16 · Production Docker Build

**Goal**: Switch the frontend from Vite dev server to a compiled nginx production
build. Configure static asset caching headers. Verify the full production stack
works end-to-end via nginx reverse proxy.

#### Dependencies

No new packages. Uses the existing `prod` stage in `frontend/Dockerfile` and the
existing `nginx/nginx.conf`.

#### Read first
- `frontend/Dockerfile` — `dev`, `build`, `prod` stages
- `docker-compose.yml` — frontend service `target:` field and volume mounts
- `nginx/nginx.conf` — existing reverse proxy config
- `frontend/vite.config.ts` — chunk splitting, build output directory

#### Deliverables

**1. Switch `docker-compose.yml` frontend service to `prod` target**

```yaml
frontend:
  build:
    context: ./frontend
    target: prod          # was: dev
  # Remove the hot-reload volume mount (./frontend:/app) — not needed in prod
  # Remove ports: 5173 — nginx handles all public traffic on port 80
```

**2. Verify `frontend/Dockerfile` prod stage**

The prod stage should already exist. Confirm it:
1. Uses `node:22-alpine` to run `npm ci && npm run build` (outputs to `dist/`)
2. Copies `dist/` into an `nginx:alpine` image at `/usr/share/nginx/html`

If the prod stage is missing or incomplete:
```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx-prod.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

**3. Create `frontend/nginx-prod.conf`**

```nginx
server {
  listen 80;
  root /usr/share/nginx/html;
  index index.html;

  # JS/CSS/fonts/images — hashed filenames, 1-year immutable cache
  location ~* \.(js|css|woff2?|png|jpg|svg|ico)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
  }

  # SPA fallback — all non-file routes serve index.html
  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

**4. Verify top-level `nginx/nginx.conf` upstreams**

The nginx reverse proxy must reach the prod frontend container (port 80, not 5173):
```nginx
upstream frontend { server frontend:80; }  # was: frontend:5173
```
Verify the `api` and `martin` upstreams are still correct (port 8000, 3000).

**5. SSL via Let's Encrypt (for real deployment only)**

Only needed for public deployment, not local/demo. If required:
- Add a `certbot` service to `docker-compose.yml`
- Add HTTPS server block to `nginx/nginx.conf` with `ssl_certificate` paths
- Add HTTP → HTTPS redirect block

Skip for hackathon/demo environments.

#### Tests / acceptance criteria

```bash
docker compose build frontend
docker compose up -d

# Frontend serves compiled SPA
curl -s -o /dev/null -w "%{http_code}" http://localhost
# Expect: 200

# JS assets have correct cache headers
JS_FILE=$(ls frontend/dist/assets/*.js 2>/dev/null | head -1 | xargs basename)
curl -s -I "http://localhost/assets/$JS_FILE" | grep -i cache-control
# Expect: max-age=31536000, immutable

# API and Martin still reachable through nginx
curl -s http://localhost/api/sorts | python3 -c "import sys,json; print(len(json.load(sys.stdin)), 'sorts')"
# Expect: 6 sorts

curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost/tiles/tile_heatmap/6/31/21?sort=overall&metric=score"
# Expect: 200 or 204 (empty tiles is OK)

# SPA routing works (direct URL navigation)
curl -s -o /dev/null -w "%{http_code}" http://localhost/some/spa/route
# Expect: 200 (served index.html, not 404)
```

#### Common issues to pre-empt
- `npm run build` fails with `Cannot find module` if the Docker build stage doesn't
  run `npm ci` before `COPY . .`. The Dockerfile must install dependencies before copying source.
- Vite generates hashed filenames (`index.A1b2C3.js`). The nginx `location ~*` regex
  matches all `.js` files — this is correct.
- If the frontend container is on port 80 but nginx was proxying to `:5173`, the
  nginx `upstream` block must be updated before `docker compose up`.

---

### P2-17 · Martin Cache Invalidation

**Goal**: After any pipeline re-run, stale Martin tile caches must be flushed.
Add an admin API endpoint that restarts the Martin container, and document the
complete post-pipeline invalidation workflow.

#### Dependencies

The API container needs Docker CLI access. Mount the Docker socket:
```yaml
# docker-compose.yml — api service volumes
volumes:
  - ./api:/app
  - /var/run/docker.sock:/var/run/docker.sock
```

Install `docker` CLI in the API Dockerfile if not already present:
```dockerfile
RUN apt-get install -y docker-cli   # or: apk add docker-cli (alpine)
```

Alternatively, use the `docker` Python SDK: add `docker>=7.0.0` to `api/requirements.txt`
and use `DockerClient` instead of `subprocess`. The Python SDK is more robust.

#### Read first
- `ARCHITECTURE.md` §7 (Martin cache note — "restart Martin or flush tile cache")
- `api/routes/weights.py` — `_check_admin_key` pattern to reuse
- `docker-compose.yml` — Martin service name

#### Deliverables

**1. Mount Docker socket in `docker-compose.yml`**

```yaml
api:
  volumes:
    - ./api:/app
    - /var/run/docker.sock:/var/run/docker.sock
```

Add a security comment: mounting the Docker socket grants root-equivalent access
inside the API container. Acceptable for hackathon; flag for any production hardening.

**2. Add `docker` SDK to `api/requirements.txt`**

```
docker>=7.0.0
```

**3. Create `api/routes/admin.py`** (if not created in P2-07; extend if it was)

```python
import docker
from fastapi import APIRouter, Header, HTTPException

router = APIRouter()

@router.post("/admin/restart-martin")
async def restart_martin(admin_key: str = Header(alias="X-Admin-Key")):
    from api.routes.weights import _check_admin_key   # reuse the guard
    await _check_admin_key(admin_key)
    try:
        client = docker.from_env()
        # Container name: check with `docker ps --format '{{.Names}}'`
        # Typically: hackeurope-martin-1 or hackeurope_martin_1
        container = client.containers.get("hackeurope-martin-1")
        container.restart(timeout=10)
    except docker.errors.NotFound:
        raise HTTPException(404, "Martin container not found — check container name")
    except Exception as e:
        raise HTTPException(500, detail=str(e))
    return {"status": "martin restarting"}
```

Check the actual container name at runtime with `docker ps` and hard-code it,
or pass it as an environment variable `MARTIN_CONTAINER_NAME` in `.env`.

**4. Create `pipeline/PIPELINE_WORKFLOW.md`** (new file)

Document the complete post-pipeline workflow:

```markdown
## Post-pipeline cache invalidation workflow

After any pipeline re-run, tile caches must be flushed:

1. Run sort pipelines (independently, in any order):
   docker compose --profile pipeline run --rm pipeline python energy/ingest.py
   docker compose --profile pipeline run --rm pipeline python environment/ingest.py
   docker compose --profile pipeline run --rm pipeline python cooling/ingest.py
   docker compose --profile pipeline run --rm pipeline python connectivity/ingest.py
   docker compose --profile pipeline run --rm pipeline python planning/ingest.py

2. Run overall composite (after ALL 5 sort pipelines complete):
   docker compose --profile pipeline run --rm pipeline python overall/compute_composite.py

3. Invalidate Martin tile cache:
   curl -X POST -H "X-Admin-Key: $ADMIN_KEY" http://localhost/api/admin/restart-martin
   # Or manually: docker compose restart martin

4. Invalidate FastAPI response cache (if P2-07 is implemented):
   curl -X POST -H "X-Admin-Key: $ADMIN_KEY" http://localhost/api/admin/invalidate-cache
```

**5. Add tests to `api/tests/test_routes.py`**

Mock `docker.from_env()` using `unittest.mock.patch`:
- `POST /api/admin/restart-martin` without key → 401
- `POST /api/admin/restart-martin` with correct key → mock restart succeeds → 200
- `POST /api/admin/restart-martin` with correct key → container not found → 404

#### Tests / acceptance criteria

```bash
# Without key → 401
curl -s -o /dev/null -w "%{http_code}" -X POST \
  http://localhost/api/admin/restart-martin
# Expect: 401

# With key → 200
curl -s -X POST -H "X-Admin-Key: devkey" \
  http://localhost/api/admin/restart-martin
# Expect: {"status":"martin restarting"}

# Martin back up within 10 seconds
sleep 10
curl -s -o /dev/null -w "%{http_code}" \
  "http://localhost/tiles/tile_heatmap/6/31/21?sort=overall&metric=score"
# Expect: 200
```

---

### P2-18 · IDA Sites Data Entry

**Goal**: Populate the `ida_sites` table with real IDA Ireland industrial site
locations. IDA provides no GIS download — sites must be manually geocoded.
Provide a CSV import script and at least 10 key site entries.

#### Dependencies

Add to `pipeline/requirements.txt` (if not already present):
```
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
```
No additional packages needed — the import uses standard library `csv` and sqlalchemy.

#### Read first
- `ARCHITECTURE.md` §11 D3 — IDA data is manual-entry only (no GIS download)
- `sql/tables.sql` — `ida_sites` schema: `site_id`, `name`, `county`, `geom`, `type`,
  `tile_id` (nullable FK to tiles)
- `pipeline/seed_synthetic.py` — 3 sample IDA sites already inserted (will be replaced)

#### Deliverables

**1. Create `pipeline/data/ida_sites.csv`** (new file)

Manually geocode IDA industrial sites from idaireland.com (site list under "Locations"
or "Properties"). Use Google Maps, OSM Nominatim, or Eircode for coordinates.
Minimum 10 sites covering major IDA regions:

```csv
name,county,lat,lng,type,description
IDA Business & Technology Park Athlone,Westmeath,53.4239,-7.9408,industrial,IDA business park
IDA Technology Park Cork,Cork,51.8961,-8.4899,industrial,Cork technology campus
Letterkenny Business Park,Donegal,54.9545,-7.7122,industrial,Donegal IDA park
IDA Industrial Estate Waterford,Waterford,52.2583,-7.1119,industrial,Waterford IDA estate
Galway Technology Park,Galway,53.2814,-9.0494,industrial,Galway technology campus
Shannon Free Zone,Clare,52.7035,-8.9193,industrial,Shannon free zone enterprise area
IDA Business Park Limerick,Limerick,52.6630,-8.6328,industrial,Limerick city campus
Carlow Technology Park,Carlow,52.8400,-6.9259,industrial,Carlow IDA park
Dundalk Technology Park,Louth,53.9976,-6.4003,industrial,Dundalk technology park
Sandyford Business District,Dublin,53.2811,-6.2218,enterprise,Dublin south enterprise zone
```

Add more sites as available from the IDA website. Review and update periodically.

**2. Create `pipeline/import_ida_sites.py`** (new file)

```python
"""
FILE: pipeline/import_ida_sites.py
Role: Import IDA Ireland industrial site locations from CSV into ida_sites table.
      Safe to re-run — uses ON CONFLICT (name) DO UPDATE.
Data: Manually geocoded from IDA Ireland website (idaireland.com).
      No GIS download exists (ARCHITECTURE.md §11 D3).
      Review CSV periodically — IDA site portfolio changes.
Run:  python import_ida_sites.py
"""
import csv
from pathlib import Path
import sqlalchemy
from sqlalchemy import text
from config import DB_URL

IDA_CSV = Path(__file__).parent / "data" / "ida_sites.csv"

def import_ida_sites(engine: sqlalchemy.Engine) -> int:
    with open(IDA_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    with engine.begin() as conn:
        for row in rows:
            conn.execute(text("""
                INSERT INTO ida_sites (name, county, geom, type, description)
                VALUES (
                    :name, :county,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326),
                    :type, :description
                )
                ON CONFLICT (name) DO UPDATE SET
                    county = EXCLUDED.county,
                    geom   = EXCLUDED.geom,
                    type   = EXCLUDED.type
            """), row)
        # Assign tile_id for sites that fall within a tile
        conn.execute(text("""
            UPDATE ida_sites i
            SET tile_id = (
                SELECT tile_id FROM tiles t
                WHERE ST_Within(i.geom, t.geom)
                LIMIT 1
            )
            WHERE tile_id IS NULL
        """))
    return len(rows)

if __name__ == "__main__":
    engine = sqlalchemy.create_engine(DB_URL)
    n = import_ida_sites(engine)
    print(f"Imported {n} IDA sites")
    print("Re-run planning/ingest.py and overall/compute_composite.py to update nearest_ida_site_km")
    print("Then restart Martin: docker compose restart martin")
```

**3. Ensure downstream pipelines are refreshed after import**

After adding IDA sites, these downstream steps must be re-run:
- `pipeline/planning/ingest.py` — recomputes `nearest_ida_site_km` in `planning_scores`
- `pipeline/overall/compute_composite.py` — recomputes `nearest_data_centre_km`
  in `overall_scores` (if any IDA sites are also in `pins_overall`)
- Restart Martin to flush tile cache

Add reminder comments to the end of `import_ida_sites.py` (already in the script above).

#### Tests / acceptance criteria

```bash
docker compose --profile pipeline run --rm pipeline python import_ida_sites.py
# Expect: "Imported N IDA sites" (N >= 10)

docker compose exec db psql -U hackeurope -d hackeurope -c "
  SELECT name, county, tile_id IS NOT NULL AS has_tile FROM ida_sites ORDER BY name;"
# Expect: >= 10 rows; most have tile_id (coastal sites may be NULL)

# Planning sidebar should now show nearest IDA distance
curl -s "http://localhost:8000/api/tile/1?sort=planning" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print('nearest_ida_site_km:', data.get('nearest_ida_site_km'))
"
# Expect: a float distance value (not null) after re-running planning ingest
```

---

## AI Features

---

### P2-19 · AI Executive Summary

**Goal**: Add an "AI Summary" button to the bottom of the sidebar. Clicking it calls a new
`POST /api/tile/{tile_id}/summary?sort={sort}` endpoint that passes the tile's existing
metric data to Claude claude-sonnet-4-6 and returns a 2–3 sentence plain-text executive
summary scoped to the active sort. Summaries are generated on demand (not on tile load)
to avoid unnecessary API calls.

#### Dependencies

Add `anthropic>=0.40.0` to `api/requirements.txt`. Set `ANTHROPIC_API_KEY` as an env var
in the `api` service in `docker-compose.yml` and in `.env.example`.

#### Read first
- `api/routes/tile.py` — the `_get_*` helpers; the summary endpoint reuses them to fetch tile data
- `api/main.py` — router registration pattern; also add `"POST"` to CORS `allow_methods`
- `frontend/src/components/Sidebar.vue` — where to add the button; accesses `store` directly

#### Deliverables

**1. Create `api/routes/summary.py`** — `POST /api/tile/{tile_id}/summary?sort={sort}`.
Reuse the existing `_get_*` helper for the active sort to fetch tile data, then call
`claude-sonnet-4-6` with a prompt instructing it to write 2–3 plain-text sentences for a
non-technical executive audience. Return `{"summary": "<text>"}`.

**2. Register the router in `api/main.py`** — import and `include_router` the new summary
router following the same pattern as the existing routes. Also add `"POST"` to
`allow_methods` in the `CORSMiddleware` config (currently only `GET` and `PUT` are listed,
which would block the frontend fetch).

**3. Add the button to `Sidebar.vue`** — add a `Sparkles`-icon "AI Summary" button at the
bottom of the `sidebar__body` block (inside the `v-else-if="store.selectedTileData"` div,
after the sort-specific component). Wire it to `POST /api/tile/{store.selectedTileId}/summary?sort={store.activeSort}`,
show a "Generating…" disabled state while loading, render the returned text below the button,
and clear the summary whenever `store.selectedTileId` or `store.activeSort` changes.

#### Tests / acceptance criteria

```bash
curl -X POST "http://localhost:8000/api/tile/1/summary?sort=energy"
# Expect: {"summary": "<2-3 sentences, no markdown>"}

cd frontend && npm run type-check
# Expect: 0 errors
```

Manual: click a tile → "AI Summary" button visible → click → summary appears; switching
sort or tile clears the summary immediately.
