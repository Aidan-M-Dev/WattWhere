"""
FILE: pipeline/environment/ingest.py
Role: Ingest environmental constraint data and compute environment_scores.
Agent boundary: Pipeline — Environment sort (§5.3, §8, §10)
Dependencies:
  - tiles table must be populated (run grid/generate_grid.py first)
  - config.py: NPWS_SAC_FILE, NPWS_SPA_FILE, NPWS_NHA_FILE,
                OPW_FLOOD_CURRENT_FILE, OPW_FLOOD_FUTURE_FILE, GSI_LANDSLIDE_FILE
  - See ireland-data-sources.md §4, §10 for source formats and download URLs
Output:
  - Populates environment_scores table (upsert — idempotent)
  - Populates tile_designation_overlaps table (delete+insert per tile)
  - Populates pins_environment table (upsert)
How to test:
  python environment/ingest.py
  psql $DATABASE_URL -c "SELECT COUNT(*), SUM(has_hard_exclusion::int) FROM environment_scores;"
  # Expect: many rows, some exclusions (SAC/SPA/flood tiles)

ARCHITECTURE RULES (from ARCHITECTURE.md §5.3 + §10):
  - Tiles overlapping SAC or SPA: has_hard_exclusion=true, exclusion_reason='SAC overlap' etc.
  - Tiles overlapping current flood extent: has_hard_exclusion=true, exclusion_reason='Current flood zone'
  - Tiles overlapping NHA/pNHA: heavy penalty (score capped at 20), NOT hard exclusion
  - Tiles overlapping future flood extent: score capped at 40, NOT hard exclusion
  - Scoring: 100 = no constraints; penalties applied top-down (worst wins)
  - designation_overlap score: 100 = no protected area, 0 = SAC/SPA overlap
  - flood_risk score: 100 = no flood risk, 0 = current flood zone
  - landslide_risk score: 100 = no susceptibility, medium=-30, high=hard penalty
  - OPW flood data: CC BY-NC-ND licence (non-commercial only — flag in UI, not here)
"""

import sys
from pathlib import Path
import geopandas as gpd
import pandas as pd
import sqlalchemy
from sqlalchemy import text
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, NPWS_SAC_FILE, NPWS_SPA_FILE, NPWS_NHA_FILE,
    OPW_FLOOD_CURRENT_FILE, OPW_FLOOD_FUTURE_FILE, GSI_LANDSLIDE_FILE,
    GRID_CRS_ITM
)


def load_tiles(engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """Load tiles from DB in EPSG:2157 for spatial overlay operations."""
    # TODO: implement
    raise NotImplementedError("Load tiles from DB")


def compute_designation_overlaps(
    tiles: gpd.GeoDataFrame,
    sac: gpd.GeoDataFrame,
    spa: gpd.GeoDataFrame,
    nha: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    For each tile, compute:
      - intersects_sac, intersects_spa, intersects_nha, intersects_pnha (bool)
      - designation_overlap score (0–100, 100 = no overlap)
      - List of overlapping designations with % tile coverage (for tile_designation_overlaps table)

    Args:
        tiles: Tile GeoDataFrame in EPSG:2157
        sac, spa, nha: Protected area polygons in EPSG:2157

    Returns:
        DataFrame with tile_id, intersects_* booleans, designation_overlap score,
        and 'designations' column (list of dicts for tile_designation_overlaps).

    TODO: implement using gpd.overlay() or STRtree for efficient intersection.
    Compute pct_overlap = intersection_area / tile_area * 100.
    """
    # TODO: implement
    raise NotImplementedError("Compute designation overlaps per tile")


def compute_flood_risk(
    tiles: gpd.GeoDataFrame,
    flood_current: gpd.GeoDataFrame,
    flood_future: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    For each tile, determine flood zone intersections.
      - intersects_current_flood (bool) → hard exclusion
      - intersects_future_flood (bool) → penalty cap at 40
      - flood_risk score (0–100, 100 = no flood risk)

    TODO: implement using spatial join / overlay.
    """
    # TODO: implement
    raise NotImplementedError("Compute flood risk per tile")


def compute_landslide_risk(
    tiles: gpd.GeoDataFrame,
    landslide: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    For each tile, extract landslide susceptibility (none/low/medium/high).
    Derive landslide_risk score (100 = none, 70 = low, 40 = medium, 10 = high).

    TODO: implement using spatial join.
    """
    # TODO: implement
    raise NotImplementedError("Compute landslide risk per tile")


def compose_environment_scores(
    designation_df: pd.DataFrame,
    flood_df: pd.DataFrame,
    landslide_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compose final environment_scores from sub-metric DataFrames.

    Scoring logic (from ARCHITECTURE.md §5.3):
      - SAC or SPA overlap OR current flood → hard exclusion, score = 0
      - NHA/pNHA overlap → cap at 20
      - Future flood → cap at 40
      - Landslide medium → −30 penalty
      - No overlaps → 100

    Returns:
        DataFrame matching environment_scores table schema.

    TODO: implement — merge sub-metric frames, apply priority penalty logic.
    """
    # TODO: implement
    raise NotImplementedError("Compose environment scores from sub-metrics")


def upsert_environment_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """Upsert environment_scores. Returns row count."""
    # TODO: implement
    raise NotImplementedError("Upsert environment scores")


def upsert_designation_overlaps(designation_df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Delete existing overlaps for each tile, then insert new ones.
    (Not upsert — designation list is replaced wholesale.)

    TODO: implement using DELETE WHERE tile_id IN (...) then batch INSERT.
    """
    # TODO: implement
    raise NotImplementedError("Upsert tile designation overlaps")


def upsert_pins_environment(
    sac: gpd.GeoDataFrame,
    spa: gpd.GeoDataFrame,
    nha: gpd.GeoDataFrame,
    flood_current: gpd.GeoDataFrame,
    engine: sqlalchemy.Engine,
) -> int:
    """
    Load environment pins:
      - SAC boundary centroids (type='sac')
      - SPA boundary centroids (type='spa')
      - NHA/pNHA centroids (type='nha', 'pnha')
      - Flood zone indicator points (type='flood_zone')

    Assign tile_id via ST_Within. tile_id may be NULL for coastal designations.

    TODO: implement — compute centroid of each designation polygon,
    assign type, assign tile_id, upsert to pins_environment.
    """
    # TODO: implement
    raise NotImplementedError("Upsert environment pins")


def main():
    """
    Environment ingest pipeline:
      1. Load tiles
      2. Load NPWS designations (SAC, SPA, NHA)
      3. Load OPW flood extents
      4. Load GSI landslide susceptibility
      5. Compute designation overlaps + flood risk + landslide risk
      6. Compose final scores
      7. Upsert environment_scores + tile_designation_overlaps + pins_environment

    Run AFTER: grid/generate_grid.py
    Run BEFORE: overall/compute_composite.py (hard exclusions propagate to overall)
    """
    print("Starting environment ingest...")
    engine = sqlalchemy.create_engine(DB_URL)

    # TODO: implement
    raise NotImplementedError("Implement main() pipeline steps")


if __name__ == "__main__":
    main()
