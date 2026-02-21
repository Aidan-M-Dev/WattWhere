"""
FILE: pipeline/config.py
Role: Central configuration for pipeline scripts — DB connection, source file paths.
Agent boundary: Pipeline layer (§8, §10)
Dependencies: DATABASE_URL env var; raw source files mounted at /data/
Output: Constants imported by all pipeline ingest scripts
How to test: python -c "from config import DB_URL; print(DB_URL)"

Source file paths reference ireland-data-sources.md for origin details.
Raw data files are expected at /data/{category}/{filename}.
Pipeline scripts are idempotent (upsert not insert) — safe to re-run.

ARCHITECTURE RULE: Do not hardcode source URLs in pipeline scripts.
This config file is where paths/URLs live. Reference ireland-data-sources.md
for full source metadata (provider, format, licence, download URL).
"""

import os
from pathlib import Path

# ── Database ──────────────────────────────────────────────────
DB_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql://hackeurope:hackeurope@db:5432/hackeurope"
)

# ── Data root ─────────────────────────────────────────────────
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "/data"))

# ── Grid ──────────────────────────────────────────────────────
IRELAND_BOUNDARY_FILE = DATA_ROOT / "grid" / "ireland_boundary.gpkg"
# Ireland national boundary in EPSG:2157 (ITM) — source: OSi / CSO
# See ireland-data-sources.md for download URL

# Grid parameters
TILE_SIZE_M = 2236  # √5,000,000 m — gives ~5 km² tiles in EPSG:2157
GRID_CRS_ITM = "EPSG:2157"
GRID_CRS_WGS84 = "EPSG:4326"

# ── Energy sources ────────────────────────────────────────────
# See ireland-data-sources.md §2–§3 for full details
WIND_ATLAS_FILE = DATA_ROOT / "energy" / "wind_speed_100m.tif"
# Global Wind Atlas GeoTIFF — wind speed at 100m, 1 km resolution
# Alternative: SEAI Wind Atlas (contact SEAI for access)

SOLAR_ATLAS_FILE = DATA_ROOT / "energy" / "solar_ghi.tif"
# Global Solar Atlas GeoTIFF — GHI in kWh/m²/yr

OSM_POWER_FILE = DATA_ROOT / "energy" / "osm_ireland_power.gpkg"
# OSM power infrastructure: power=substation, power=line, power=tower
# Extract from Geofabrik Ireland dump, filter power=*

# ── Environment sources ───────────────────────────────────────
# See ireland-data-sources.md §4, §10
NPWS_SAC_FILE = DATA_ROOT / "environment" / "npws_sac.gpkg"
NPWS_SPA_FILE = DATA_ROOT / "environment" / "npws_spa.gpkg"
NPWS_NHA_FILE = DATA_ROOT / "environment" / "npws_nha.gpkg"
# NPWS designated sites — download from data.gov.ie NPWS datasets

OPW_FLOOD_CURRENT_FILE = DATA_ROOT / "environment" / "opw_flood_current.gpkg"
OPW_FLOOD_FUTURE_FILE = DATA_ROOT / "environment" / "opw_flood_future.gpkg"
# OPW National Indicative Flood Mapping — CC BY-NC-ND licence

GSI_LANDSLIDE_FILE = DATA_ROOT / "environment" / "gsi_landslide_susceptibility.gpkg"
# GSI landslide susceptibility — data.gov.ie

# ── Cooling sources ───────────────────────────────────────────
# See ireland-data-sources.md §6, §7
MET_EIREANN_TEMP_FILE = DATA_ROOT / "cooling" / "met_temperature_grid.tif"
MET_EIREANN_RAIN_FILE = DATA_ROOT / "cooling" / "met_rainfall_grid.tif"
# Met Éireann 1 km climate grids — request from Met Éireann

EPA_RIVERS_FILE = DATA_ROOT / "cooling" / "epa_river_network.gpkg"
# EPA river network (Water Framework Directive waterbodies)

OPW_HYDRO_FILE = DATA_ROOT / "cooling" / "opw_hydrometric_stations.gpkg"
# OPW hydrometric station locations and mean flow — waterlevel.ie

GSI_AQUIFER_FILE = DATA_ROOT / "cooling" / "gsi_aquifer_productivity.gpkg"
# GSI groundwater productivity — data.gov.ie

# ── Connectivity sources ──────────────────────────────────────
# See ireland-data-sources.md §8
COMREG_BROADBAND_FILE = DATA_ROOT / "connectivity" / "comreg_broadband.gpkg"
# ComReg broadband coverage — download from ComReg data hub

OSM_ROADS_FILE = DATA_ROOT / "connectivity" / "osm_ireland_roads.gpkg"
# OSM roads: motorway, national primary — from Geofabrik Ireland

# IXP coordinates (static — no GIS download exists)
INEX_DUBLIN_COORDS = (-6.2603, 53.3498)   # INEX Dublin, Citywest
INEX_CORK_COORDS = (-8.4694, 51.8969)     # INEX Cork, Cork City
# Source: PeeringDB (ireland-data-sources.md §8)

# ── Planning sources ──────────────────────────────────────────
# See ireland-data-sources.md §5, §9
MYPLAN_ZONING_FILE = DATA_ROOT / "planning" / "myplan_gzt_zoning.gpkg"
# MyPlan GZT development plan zoning — download from MyPlan.ie

PLANNING_APPLICATIONS_FILE = DATA_ROOT / "planning" / "planning_applications.gpkg"
# National Planning Applications — data.gov.ie / individual LA portals

CSO_POPULATION_FILE = DATA_ROOT / "planning" / "cso_small_area_stats.gpkg"
# CSO Small Area Population Statistics 2022

# ── Scoring weights (should match composite_weights in DB) ────
DEFAULT_WEIGHTS = {
    "energy": 0.25,
    "connectivity": 0.25,
    "environment": 0.20,
    "cooling": 0.15,
    "planning": 0.15,
}
