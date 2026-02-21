"""
FILE: pipeline/connectivity/ingest.py
Role: Ingest connectivity and transport data, compute connectivity_scores.
Agent boundary: Pipeline — Connectivity sort (§5.5, §8, §10)
Dependencies:
  - tiles table populated (grid/generate_grid.py)
  - config.py: COMREG_BROADBAND_FILE, OSM_ROADS_FILE, INEX_DUBLIN_COORDS, INEX_CORK_COORDS
  - See ireland-data-sources.md §8 for source formats
Output:
  - Populates connectivity_scores table (upsert — idempotent)
  - Populates pins_connectivity table (upsert)
How to test:
  python connectivity/ingest.py
  psql $DATABASE_URL -c "SELECT MIN(inex_dublin_km), MAX(inex_dublin_km), AVG(score) FROM connectivity_scores;"

DATA LIMITATION NOTE (ARCHITECTURE.md §11 D6):
  No public GIS fibre route data exists for Ireland.
  ComReg broadband coverage (polygon data by coverage tier) is the best proxy.
  Do NOT claim this represents fibre availability. The sidebar flags this.

Scoring weights:
  35% broadband (from ComReg coverage tier → 0–100)
  30% ix_distance (inverse log-distance to nearest IXP, pre-normalised 0–100)
  20% road_access (inverse distance to nearest motorway, pre-normalised 0–100)
  15% rail access (inverse distance to nearest rail freight terminal, 0–100)

IXP coordinates (no GIS download — hardcoded from PeeringDB):
  INEX Dublin: INEX_DUBLIN_COORDS from config.py
  INEX Cork:   INEX_CORK_COORDS from config.py
"""

import sys
from pathlib import Path
import numpy as np
import geopandas as gpd
import pandas as pd
import sqlalchemy
from shapely.geometry import Point
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, COMREG_BROADBAND_FILE, OSM_ROADS_FILE,
    INEX_DUBLIN_COORDS, INEX_CORK_COORDS, GRID_CRS_ITM, GRID_CRS_WGS84
)


def load_tiles(engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """Load tiles in EPSG:2157."""
    # TODO: implement
    raise NotImplementedError("Load tiles")


def compute_ix_distances(tiles: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Compute distance from each tile centroid to INEX Dublin and INEX Cork.
    Returns DataFrame with tile_id, inex_dublin_km, inex_cork_km, ix_distance (0–100 score).

    Distance calculation in EPSG:2157 (metric), stored as km.
    ix_distance score: log-inverse normalisation using the closer IXP.
      score = 100 * max(0, 1 - log(1 + min_km) / log(1 + 300))
    (300 km is effective maximum — tiles in Donegal are ~300 km from Dublin)

    IXP coordinates in EPSG:4326, must be reprojected to EPSG:2157 for distance.

    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Compute IXP distances")


def compute_broadband(tiles: gpd.GeoDataFrame, comreg: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Assign ComReg broadband coverage tier to each tile (majority overlay).
    Map tier to 0–100 score:
      'UFBB' (Ultra Fast >= 100 Mbps): 90–100
      'SFBB' (Superfast >= 30 Mbps):   65–80
      'FBB'  (Fast >= 10 Mbps):        35–55
      'BB'   (Basic < 10 Mbps):        10–25
      No coverage:                     0–5

    Returns DataFrame with tile_id, broadband (0–100), broadband_tier (str).

    TODO: implement — spatial join tiles with ComReg polygons, majority class.
    """
    # TODO: implement
    raise NotImplementedError("Compute broadband scores from ComReg data")


def compute_road_access(tiles: gpd.GeoDataFrame, roads: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Compute distance to nearest motorway junction and national primary road.
    Returns DataFrame with tile_id, road_access (0–100), nearest_motorway_junction_km,
    nearest_motorway_junction_name, nearest_national_road_km.

    Filter OSM roads: highway=motorway_junction, highway=national_primary.
    Distance in EPSG:2157.

    TODO: implement — nearest feature from road network.
    """
    # TODO: implement
    raise NotImplementedError("Compute road access scores")


def compute_connectivity_scores(
    ix_df: pd.DataFrame,
    broadband_df: pd.DataFrame,
    road_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compose connectivity_scores. Weights:
      35% broadband + 30% ix_distance + 20% road_access + 15% (placeholder rail)
    Rail data placeholder: set nearest_rail_freight_km=NULL until rail data available.

    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Compute connectivity scores")


def upsert_connectivity_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """Upsert connectivity_scores. Returns row count."""
    # TODO: implement
    raise NotImplementedError("Upsert connectivity scores")


def upsert_pins_connectivity(engine: sqlalchemy.Engine) -> int:
    """
    Load connectivity pins:
      - IXP points (type='internet_exchange'): INEX Dublin + INEX Cork from config.py
      - Motorway junctions (type='motorway_junction'): from OSM roads
      - ComReg high-speed broadband areas (type='broadband_area'): centroid of UFBB zones

    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Upsert connectivity pins")


def main():
    """
    Connectivity ingest pipeline:
      1. Load tiles
      2. Compute IXP distances (from hardcoded coordinates in config)
      3. Compute broadband scores from ComReg data
      4. Compute road access from OSM roads
      5. Compute composite connectivity scores
      6. Upsert connectivity_scores + pins_connectivity

    Run AFTER: grid/generate_grid.py
    Run BEFORE: overall/compute_composite.py
    """
    print("Starting connectivity ingest...")
    engine = sqlalchemy.create_engine(DB_URL)

    # TODO: implement
    raise NotImplementedError("Implement main() pipeline steps")


if __name__ == "__main__":
    main()
