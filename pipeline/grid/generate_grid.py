"""
FILE: pipeline/grid/generate_grid.py
Role: One-time grid generation — creates ~14,000 5 km² tiles covering Ireland.
Agent boundary: Pipeline — Grid generation (§4, §8, §10)
Dependencies:
  - config.py (IRELAND_BOUNDARY_FILE, TILE_SIZE_M, DB_URL)
  - ireland_boundary.gpkg in DATA_ROOT/grid/ (Ireland national boundary in EPSG:2157)
  - sql/tables.sql already applied (tiles table exists)
Output:
  - Populates tiles table with (geom, centroid, county, grid_ref, area_km2)
  - All geometries stored as EPSG:4326
  - ~14,000 rows expected (tiles whose centroid falls within Ireland boundary)
How to test:
  python grid/generate_grid.py
  psql $DATABASE_URL -c "SELECT COUNT(*) FROM tiles;"
  # Expect ~13,000–15,000

ARCHITECTURE RULES:
  - Generate grid in EPSG:2157 (ITM), reproject to EPSG:4326 before storage.
  - Clip to Ireland: only tiles whose CENTROID falls within the national boundary.
  - tile_id is SERIAL (auto-assigned by DB) — do not pre-compute IDs.
  - grid_ref format: 'R{row:04d}C{col:04d}' — human-readable but not the PK.
  - County assignment: PostGIS spatial join after insert (or during insert via CTE).
  - This script is idempotent: truncate tiles (and all FK children) before re-inserting.
    Use TRUNCATE tiles CASCADE to also clear all sort tables.
"""

import geopandas as gpd
import pandas as pd
from shapely.geometry import box, Point
from pyproj import Transformer
import sqlalchemy
from sqlalchemy import text
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_URL, IRELAND_BOUNDARY_FILE, TILE_SIZE_M, GRID_CRS_ITM, GRID_CRS_WGS84


def load_ireland_boundary() -> gpd.GeoDataFrame:
    """
    Load Ireland national boundary in EPSG:2157 (ITM).

    Returns:
        GeoDataFrame with single national boundary polygon in EPSG:2157.

    TODO: implement — load IRELAND_BOUNDARY_FILE, ensure EPSG:2157 CRS.
    If boundary is in WGS84, reproject: gdf.to_crs(GRID_CRS_ITM)
    """
    # TODO: implement
    raise NotImplementedError("Load Ireland boundary from IRELAND_BOUNDARY_FILE")


def generate_grid_itm(boundary_gdf: gpd.GeoDataFrame, tile_size_m: int) -> gpd.GeoDataFrame:
    """
    Generate a regular grid of square tiles over Ireland's bounding box.
    Filter to tiles whose centroid falls within the national boundary.

    Args:
        boundary_gdf: Ireland boundary in EPSG:2157
        tile_size_m: Tile side length in metres (2236 for ~5 km²)

    Returns:
        GeoDataFrame of tile polygons in EPSG:2157, with centroid column.
        Columns: geometry (polygon), centroid (point), row, col, grid_ref

    TODO: implement using:
        bounds = boundary_gdf.total_bounds  # (minx, miny, maxx, maxy)
        Iterate rows/cols, create box(x, y, x+tile_size_m, y+tile_size_m)
        Filter: centroid.within(boundary_union)
    """
    # TODO: implement
    raise NotImplementedError("Generate grid tiles in EPSG:2157")


def reproject_to_wgs84(grid_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Reproject grid tiles and centroids from EPSG:2157 to EPSG:4326.

    Args:
        grid_gdf: Grid in EPSG:2157 (geometry=polygon, centroid=point)

    Returns:
        GeoDataFrame with geometry and centroid in EPSG:4326.

    TODO: implement — use grid_gdf.to_crs(GRID_CRS_WGS84)
    """
    # TODO: implement
    raise NotImplementedError("Reproject grid to EPSG:4326")


def assign_counties(grid_gdf: gpd.GeoDataFrame, engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """
    Assign county name to each tile via spatial join with county boundaries.
    County boundaries loaded from PostGIS or from a source file.

    Args:
        grid_gdf: Grid tiles in EPSG:4326
        engine: SQLAlchemy engine

    Returns:
        grid_gdf with 'county' column populated.

    TODO: implement — either use a county boundaries GeoJSON/GPKG or
    derive counties via reverse geocoding centroid against a county layer.
    OSi county boundaries available from data.gov.ie.
    """
    # TODO: implement
    raise NotImplementedError("Assign counties to tiles via spatial join")


def load_tiles_to_db(grid_gdf: gpd.GeoDataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Truncate existing tiles and reload from the generated grid.
    Uses TRUNCATE CASCADE to also clear all sort tables (FK children).

    Args:
        grid_gdf: Final grid in EPSG:4326 with county, grid_ref columns.
        engine: SQLAlchemy engine

    Returns:
        Number of tiles inserted.

    TODO: implement using:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE tiles CASCADE"))
        grid_gdf.to_postgis("tiles", engine, if_exists="append", index=False,
                             dtype={"geom": ..., "centroid": ...})
    WARNING: TRUNCATE CASCADE will delete all existing sort scores + pins.
    Only run this script when rebuilding from scratch.
    """
    # TODO: implement
    raise NotImplementedError("Load tiles to database")


def main():
    """
    Full grid generation pipeline:
      1. Load Ireland boundary
      2. Generate grid in EPSG:2157
      3. Reproject to EPSG:4326
      4. Assign counties
      5. Load to DB

    Run order: this script FIRST, before any ingest scripts.
    """
    print("Starting grid generation...")

    engine = sqlalchemy.create_engine(DB_URL)

    # TODO: implement — call each function in sequence
    # boundary = load_ireland_boundary()
    # grid_itm = generate_grid_itm(boundary, TILE_SIZE_M)
    # grid_wgs84 = reproject_to_wgs84(grid_itm)
    # grid_with_counties = assign_counties(grid_wgs84, engine)
    # n = load_tiles_to_db(grid_with_counties, engine)
    # print(f"Generated {n} tiles")

    raise NotImplementedError("Implement main() pipeline steps")


if __name__ == "__main__":
    main()
