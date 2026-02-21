"""
FILE: pipeline/planning/ingest.py
Role: Ingest planning and zoning data, compute planning_scores.
Agent boundary: Pipeline — Planning sort (§5.6, §8, §10)
Dependencies:
  - tiles table populated (grid/generate_grid.py)
  - config.py: MYPLAN_ZONING_FILE, PLANNING_APPLICATIONS_FILE, CSO_POPULATION_FILE
  - See ireland-data-sources.md §5, §9 for source formats
Output:
  - Populates planning_scores table (upsert)
  - Populates tile_planning_applications table (delete+insert per tile)
  - Populates pins_planning table (upsert)
How to test:
  python planning/ingest.py
  psql $DATABASE_URL -c "SELECT AVG(pct_industrial), AVG(score) FROM planning_scores;"

Scoring logic (from ARCHITECTURE.md §5.6):
  - Tile contains Industrial/Enterprise zoning: base 80–100 (by % coverage)
  - Mixed Use: base 50–70
  - Unzoned/agricultural: base 10–30
  - Residential: capped at 10
  - Bonus +10 if planning applications for data centres within 10 km
  - Penalty −20 if within 500m of residential zoning

zoning_tier = 0–100 score based on best zoning category present
planning_precedent = 0–100 score based on proximity to previous DC applications

IDA sites come from ida_sites table (manually entered — DO NOT overwrite from here).
nearest_ida_site_km computed via PostGIS query or geopandas spatial join.
"""

import sys
from pathlib import Path
import numpy as np
import geopandas as gpd
import pandas as pd
import sqlalchemy
from sqlalchemy import text
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, MYPLAN_ZONING_FILE, PLANNING_APPLICATIONS_FILE,
    CSO_POPULATION_FILE, GRID_CRS_ITM
)


def load_tiles(engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """Load tiles in EPSG:2157."""
    # TODO: implement
    raise NotImplementedError("Load tiles")


def compute_zoning_overlay(
    tiles: gpd.GeoDataFrame,
    zoning: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Compute zoning category percentages for each tile via area intersection.

    Zoning categories to extract from MyPlan GZT:
      Industrial, Enterprise, Mixed Use, Agricultural, Residential, Other (everything else)

    Returns DataFrame with tile_id, pct_industrial, pct_enterprise, pct_mixed_use,
    pct_agricultural, pct_residential, pct_other, zoning_tier (0–100).

    zoning_tier formula:
      score = 0
      if industrial + enterprise > 50% of tile: score = 80 + (industrial + enterprise / 100) * 20
      elif mixed_use > 30%: score = 50 + (mixed_use / 100) * 20
      elif agricultural > 50%: score = 10 + (agricultural / 100) * 20
      if residential > 50%: score = min(score, 10)

    TODO: implement using gpd.overlay() + area calculation.
    """
    # TODO: implement
    raise NotImplementedError("Compute zoning overlay percentages")


def compute_planning_applications(
    tiles: gpd.GeoDataFrame,
    applications: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Spatial join planning applications to tiles.
    Compute planning_precedent score: proximity to data centre/industrial applications.

    Also returns per-tile application list for tile_planning_applications table.

    Returns:
      DataFrame with tile_id, planning_precedent (0–100),
      and 'applications' column (list of application dicts).

    DC application bonus: if any DC planning application within 10 km → +10 to score.

    TODO: implement — sjoin applications to tiles + 10km buffer query.
    """
    # TODO: implement
    raise NotImplementedError("Compute planning applications and precedent score")


def compute_population_density(
    tiles: gpd.GeoDataFrame,
    cso_pop: gpd.GeoDataFrame,
) -> pd.Series:
    """
    Compute population density (persons/km²) for each tile from CSO Small Area statistics.
    Zonal weighted sum of population in overlapping small areas.

    Returns Series[tile_id → population_density_per_km2].

    TODO: implement — area-weighted population aggregation.
    """
    # TODO: implement
    raise NotImplementedError("Compute population density per tile")


def compute_nearest_ida_km(tiles: gpd.GeoDataFrame, engine: sqlalchemy.Engine) -> pd.Series:
    """
    Compute distance from each tile centroid to nearest IDA site (from ida_sites table).
    Returns Series[tile_id → distance_km].

    NOTE: IDA sites are manually entered — they may be sparse or empty.
    Handle gracefully (return NULL if no IDA sites in DB).

    TODO: implement — load ida_sites via geopandas.read_postgis(), nearest spatial join.
    """
    # TODO: implement
    raise NotImplementedError("Compute nearest IDA site distance")


def compose_planning_scores(
    zoning_df: pd.DataFrame,
    planning_df: pd.DataFrame,
    pop_density: pd.Series,
    ida_km: pd.Series,
) -> pd.DataFrame:
    """
    Compose planning_scores from sub-metrics.

    Final score formula (capped 0–100):
      base = zoning_tier
      + 10 if planning_precedent > 50 (DC planning history nearby)
      − 20 if any tile within 500m of residential (use pct_residential > 0 as proxy)
      clamp to [0, 100]

    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Compose planning scores")


def upsert_planning_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """Upsert planning_scores."""
    # TODO: implement
    raise NotImplementedError("Upsert planning scores")


def upsert_planning_applications(planning_df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Delete existing applications per tile, insert new ones.
    (Delete+insert pattern, same as tile_designation_overlaps.)
    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Upsert tile planning applications")


def upsert_pins_planning(
    zoning: gpd.GeoDataFrame,
    applications: gpd.GeoDataFrame,
    engine: sqlalchemy.Engine,
) -> int:
    """
    Load planning pins:
      - Industrial/Enterprise zoned parcel centroids (type='zoning_parcel')
      - Recent data centre planning applications (type='planning_application')
      IDA sites NOT loaded here — they are managed manually in ida_sites table.

    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Upsert planning pins")


def main():
    """
    Planning ingest pipeline:
      1. Load tiles
      2. Overlay MyPlan GZT zoning
      3. Spatial join planning applications
      4. Compute population density from CSO
      5. Compute nearest IDA site distance
      6. Compose planning scores
      7. Upsert planning_scores + tile_planning_applications + pins_planning

    Run AFTER: grid/generate_grid.py
    Run BEFORE: overall/compute_composite.py
    """
    print("Starting planning ingest...")
    engine = sqlalchemy.create_engine(DB_URL)

    # TODO: implement
    raise NotImplementedError("Implement main() pipeline steps")


if __name__ == "__main__":
    main()
