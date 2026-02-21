"""
FILE: pipeline/cooling/ingest.py
Role: Ingest climate and water resource data, compute cooling_scores.
Agent boundary: Pipeline — Cooling sort (§5.4, §8, §10)
Dependencies:
  - tiles table populated (grid/generate_grid.py)
  - config.py: MET_EIREANN_TEMP_FILE, MET_EIREANN_RAIN_FILE, EPA_RIVERS_FILE,
                OPW_HYDRO_FILE, GSI_AQUIFER_FILE
  - See ireland-data-sources.md §6, §7 for source formats
Output:
  - Populates cooling_scores table (upsert — idempotent)
  - Populates pins_cooling table (upsert)
  - Writes metric_ranges rows for temperature and rainfall (used by Martin)
How to test:
  python cooling/ingest.py
  psql $DATABASE_URL -c "SELECT MIN(temperature), MAX(temperature), AVG(score) FROM cooling_scores;"

ARCHITECTURE RULES:
  - temperature stored as raw °C (NOT inverted). The tile_heatmap SQL function
    inverts it: 100 - normalised_score. Do NOT pre-invert here.
  - rainfall stored as raw mm/yr.
  - Write metric_ranges for temperature and rainfall (for legend display).
  - free_cooling_hours: estimate hours/yr below 18°C from monthly temperature grid.
    Approximation: sum months where mean_temp < 18, scale to hours.
    (More accurate: use MÉRA reanalysis hourly data if available.)
  - water_proximity: 0–100 inverse distance score to nearest EPA river/lake.
  - aquifer_productivity: 0–100 from GSI bedrock classification.
  - Composite: 40% temperature + 35% water_proximity + 25% rainfall score.
"""

import sys
from pathlib import Path
import numpy as np
import geopandas as gpd
import pandas as pd
import rasterio
import sqlalchemy
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, MET_EIREANN_TEMP_FILE, MET_EIREANN_RAIN_FILE,
    EPA_RIVERS_FILE, OPW_HYDRO_FILE, GSI_AQUIFER_FILE, GRID_CRS_ITM
)


def load_tiles(engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """Load tiles from DB in EPSG:2157."""
    # TODO: implement
    raise NotImplementedError("Load tiles")


def extract_temperature_stats(tiles: gpd.GeoDataFrame) -> pd.Series:
    """
    Zonal mean of Met Éireann mean annual temperature grid (°C) per tile.
    Returns Series[tile_id → °C raw].
    Source: MET_EIREANN_TEMP_FILE (GeoTIFF, 1 km, EPSG:2157 or EPSG:4326).
    TODO: implement — reproject raster CRS if needed, extract zonal mean.
    """
    # TODO: implement
    raise NotImplementedError("Extract temperature zonal stats")


def extract_rainfall_stats(tiles: gpd.GeoDataFrame) -> pd.Series:
    """
    Zonal mean of Met Éireann annual rainfall grid (mm/yr) per tile.
    Returns Series[tile_id → mm/yr raw].
    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Extract rainfall zonal stats")


def compute_free_cooling_hours(temperature_series: pd.Series) -> pd.Series:
    """
    Estimate free-cooling hours per year (hours below 18°C).
    Approximation from annual mean temperature:
      If mean_temp = 10°C → ~7,000 hrs/yr free cooling
      Calibrate against MÉRA reanalysis if available.

    TODO: implement — linear approximation or lookup table.
    Returns Series[tile_id → estimated_hours].
    """
    # TODO: implement
    raise NotImplementedError("Estimate free cooling hours")


def compute_water_proximity(
    tiles: gpd.GeoDataFrame,
    rivers_lakes: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Compute proximity to nearest EPA river/lake for each tile.
    Returns DataFrame with tile_id, nearest_waterbody_name, nearest_waterbody_km,
    water_proximity (0–100 inverse distance pre-normalised).

    Distance in EPSG:2157 (metres), stored as km.
    Normalisation: log-inverse, similar to grid_proximity in energy ingest.

    TODO: implement — nearest feature spatial join.
    """
    # TODO: implement
    raise NotImplementedError("Compute water proximity")


def compute_aquifer_productivity(
    tiles: gpd.GeoDataFrame,
    aquifer: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Overlay tiles with GSI aquifer productivity polygons.
    Map productivity class to 0–100:
      high=90, moderate=65, low=35, negligible=10, none=0

    Returns DataFrame with tile_id, aquifer_productivity (0–100),
    aquifer_productivity_rating ('high'/'moderate'/'low'/'negligible'/'none').

    TODO: implement — spatial join, majority class per tile.
    """
    # TODO: implement
    raise NotImplementedError("Compute aquifer productivity")


def compute_cooling_scores(
    temp_series: pd.Series,
    rainfall_series: pd.Series,
    water_df: pd.DataFrame,
    aquifer_df: pd.DataFrame,
    free_cooling_df: pd.Series,
) -> pd.DataFrame:
    """
    Compose cooling_scores. Weights:
      40% temperature (normalised, inverted — lower = better)
      35% water_proximity (already 0–100)
      25% rainfall (min-max normalised to 0–100, higher = better)
    NOTE: store temperature as raw °C here; inversion is in Martin SQL.

    TODO: implement — merge DataFrames, normalise, compute weighted sum.
    """
    # TODO: implement
    raise NotImplementedError("Compute cooling scores")


def upsert_cooling_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """Upsert cooling_scores. Returns row count."""
    # TODO: implement
    raise NotImplementedError("Upsert cooling scores")


def upsert_pins_cooling(
    hydro_stations: gpd.GeoDataFrame,
    rivers_lakes: gpd.GeoDataFrame,
    engine: sqlalchemy.Engine,
) -> int:
    """
    Load cooling pins:
      - OPW hydrometric stations (type='hydrometric_station')
      - Major rivers/lakes centroids from EPA (type='waterbody')
      - Met Éireann synoptic stations (type='met_station', if coordinates available)

    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Upsert cooling pins")


def write_metric_ranges(
    temp_series: pd.Series,
    rainfall_series: pd.Series,
    engine: sqlalchemy.Engine,
) -> None:
    """
    Write min/max to metric_ranges for temperature (°C) and rainfall (mm/yr).
    Used by tile_heatmap SQL function for colour ramp normalisation.

    TODO: implement — INSERT INTO metric_ranges ... ON CONFLICT DO UPDATE
    """
    # TODO: implement
    raise NotImplementedError("Write metric ranges for temperature and rainfall")


def main():
    """
    Cooling ingest pipeline:
      1. Load tiles
      2. Extract temperature + rainfall from Met Éireann grids
      3. Compute free cooling hours estimate
      4. Compute water proximity from EPA rivers/lakes
      5. Compute aquifer productivity from GSI
      6. Compute composite cooling scores
      7. Upsert cooling_scores + pins_cooling
      8. Write metric_ranges for temperature + rainfall

    Run AFTER: grid/generate_grid.py
    Run BEFORE: overall/compute_composite.py
    """
    print("Starting cooling ingest...")
    engine = sqlalchemy.create_engine(DB_URL)

    # TODO: implement
    raise NotImplementedError("Implement main() pipeline steps")


if __name__ == "__main__":
    main()
