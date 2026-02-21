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
  - County assignment: nearest-centroid Voronoi from known county centroids (fallback).
  - This script is idempotent: truncate tiles (and all FK children) before re-inserting.
    Use TRUNCATE tiles CASCADE to also clear all sort tables.
"""

import sys
from pathlib import Path

import numpy as np
import geopandas as gpd
from shapely.geometry import box, Point, Polygon
import shapely
import sqlalchemy
from sqlalchemy import text
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_URL, IRELAND_BOUNDARY_FILE, TILE_SIZE_M, GRID_CRS_ITM, GRID_CRS_WGS84

# ---------------------------------------------------------------------------
# Option B: Hard-coded ~25-vertex approximation of the Republic of Ireland
# boundary in EPSG:4326. Accuracy ±20 km — acceptable for synthetic testing.
# Traces the coast clockwise from NE (Dundalk) and the land border with NI.
# ---------------------------------------------------------------------------
IRELAND_POLYGON_WGS84 = [
    (-6.2,  54.1),   # NE  — Dundalk / NI border entry
    (-6.0,  53.9),   # E   — Louth coast
    (-5.9,  53.5),   # E   — Meath coast
    (-6.0,  53.2),   # E   — Dublin bay
    (-6.1,  52.9),   # E   — Wicklow coast
    (-6.2,  52.6),   # SE  — Wicklow / Wexford
    (-6.4,  52.2),   # SE  — Wexford (Hook Head area)
    (-7.0,  52.0),   # S   — Waterford harbour
    (-7.8,  52.0),   # S   — W Waterford / E Cork
    (-8.5,  51.6),   # S   — Cork coast (Old Head)
    (-9.5,  51.4),   # SW  — Cork / Kerry (Mizen Head)
    (-10.4, 51.7),   # W   — Kerry peninsulas
    (-10.5, 52.1),   # W   — Kerry (Slea Head / Loop Head)
    (-10.2, 52.5),   # W   — Clare / N Kerry
    (-9.8,  53.0),   # W   — Galway Bay south
    (-9.6,  53.2),   # W   — Galway Bay
    (-10.0, 53.5),   # W   — Connemara coast
    (-10.0, 53.8),   # W   — Mayo (Clew Bay)
    (-10.1, 54.0),   # NW  — Mayo (Erris Head)
    (-9.0,  54.5),   # NW  — Sligo / N Donegal coast
    (-8.2,  55.0),   # N   — Donegal coast
    (-7.5,  55.3),   # N   — Donegal (Malin Head)
    (-7.2,  55.0),   # N   — Donegal / Derry (Lough Foyle)
    (-7.5,  54.5),   # NI border — Fermanagh
    (-7.0,  54.3),   # NI border — Monaghan / Tyrone
    (-6.5,  54.2),   # NI border — Monaghan / Armagh
    (-6.2,  54.1),   # Close polygon (back to start)
]

# Approximate centroids of the 26 counties of the Republic of Ireland (WGS84).
# Used for Voronoi-style county assignment when no county boundary file is available.
COUNTY_CENTROIDS = {
    "Carlow":    (-6.93, 52.73),
    "Cavan":     (-7.36, 53.99),
    "Clare":     (-8.98, 52.90),
    "Cork":      (-8.94, 51.96),
    "Donegal":   (-8.11, 54.65),
    "Dublin":    (-6.27, 53.35),
    "Galway":    (-8.93, 53.36),
    "Kerry":     (-9.70, 52.16),
    "Kildare":   (-6.77, 53.16),
    "Kilkenny":  (-7.25, 52.65),
    "Laois":     (-7.33, 52.99),
    "Leitrim":   (-8.00, 54.08),
    "Limerick":  (-8.62, 52.50),
    "Longford":  (-7.80, 53.73),
    "Louth":     (-6.49, 53.92),
    "Mayo":      (-9.42, 53.85),
    "Meath":     (-6.66, 53.61),
    "Monaghan":  (-6.97, 54.25),
    "Offaly":    (-7.72, 53.27),
    "Roscommon": (-8.18, 53.76),
    "Sligo":     (-8.53, 54.16),
    "Tipperary": (-7.94, 52.47),
    "Waterford": (-7.62, 52.26),
    "Westmeath": (-7.50, 53.53),
    "Wexford":   (-6.55, 52.34),
    "Wicklow":   (-6.40, 52.96),
}


def load_ireland_boundary() -> gpd.GeoDataFrame:
    """
    Load Ireland national boundary in EPSG:2157 (ITM).

    Option A: Read from IRELAND_BOUNDARY_FILE if it exists.
    Option B (fallback): Use hard-coded IRELAND_POLYGON_WGS84 polygon.
    """
    if IRELAND_BOUNDARY_FILE.exists():
        print(f"  Loading boundary from {IRELAND_BOUNDARY_FILE}")
        gdf = gpd.read_file(IRELAND_BOUNDARY_FILE)
        return gdf[["geometry"]].to_crs(GRID_CRS_ITM)

    print(f"  Boundary file not found at {IRELAND_BOUNDARY_FILE}")
    print("  Using hard-coded Option B polygon (±20 km accuracy — OK for synthetic data)")
    poly = Polygon(IRELAND_POLYGON_WGS84)
    gdf = gpd.GeoDataFrame({"geometry": [poly]}, crs=GRID_CRS_WGS84)
    return gdf.to_crs(GRID_CRS_ITM)


def generate_grid_itm(boundary_gdf: gpd.GeoDataFrame, tile_size_m: int) -> gpd.GeoDataFrame:
    """
    Generate a regular grid of square tiles over Ireland's bounding box.
    Filter to tiles whose centroid falls within the national boundary.

    Returns GeoDataFrame in EPSG:2157 with columns:
      geometry (Polygon), centroid (Point), row, col, grid_ref
    """
    bounds = boundary_gdf.total_bounds  # (minx, miny, maxx, maxy) in ITM
    boundary_union = boundary_gdf.union_all()

    minx, miny, maxx, maxy = bounds
    # Snap to tile_size_m grid so tiles align cleanly
    minx = (int(minx) // tile_size_m) * tile_size_m
    miny = (int(miny) // tile_size_m) * tile_size_m

    x_starts = np.arange(minx, maxx + tile_size_m, tile_size_m, dtype=float)
    y_starts = np.arange(miny, maxy + tile_size_m, tile_size_m, dtype=float)
    n_cols = len(x_starts)
    n_rows = len(y_starts)

    print(f"  Bounding box: {n_cols} cols × {n_rows} rows = {n_cols * n_rows:,} candidates")

    # Build centroid coordinates for every candidate tile (vectorized meshgrid)
    cx_grid, cy_grid = np.meshgrid(
        x_starts + tile_size_m / 2,
        y_starts + tile_size_m / 2,
    )
    cx_flat = cx_grid.flatten()
    cy_flat = cy_grid.flatten()

    # Vectorised within-boundary check using shapely 2.x
    pts = shapely.points(cx_flat, cy_flat)
    within_mask = shapely.within(pts, boundary_union)
    within_idx = np.where(within_mask)[0]

    print(f"  {len(within_idx):,} tiles pass centroid-clip to Ireland boundary")

    tiles = []
    for flat_idx in within_idx:
        row_i = int(flat_idx // n_cols)
        col_j = int(flat_idx % n_cols)
        x = float(x_starts[col_j])
        y = float(y_starts[row_i])
        cx = x + tile_size_m / 2
        cy = y + tile_size_m / 2
        tiles.append(
            {
                "geometry": box(x, y, x + tile_size_m, y + tile_size_m),
                "centroid": Point(cx, cy),
                "row": row_i,
                "col": col_j,
                "grid_ref": f"R{row_i:04d}C{col_j:04d}",
            }
        )

    return gpd.GeoDataFrame(tiles, crs=GRID_CRS_ITM)


def reproject_to_wgs84(grid_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Reproject grid tiles and centroids from EPSG:2157 to EPSG:4326.
    The centroid column is recomputed from the reprojected polygon geometry.
    """
    reprojected = grid_gdf.to_crs(GRID_CRS_WGS84).copy()
    # Recompute centroid from the reprojected polygon (more correct than
    # transforming the ITM centroid point separately)
    reprojected["centroid"] = reprojected.geometry.centroid
    return reprojected


def assign_counties(grid_gdf: gpd.GeoDataFrame, engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """
    Assign county name to each tile via nearest-centroid Voronoi partition.

    Uses known county centroids (COUNTY_CENTROIDS above) and simple Euclidean
    distance in WGS84 — acceptable coarse assignment for synthetic data.
    For production, replace with a PostGIS spatial join against OSi county boundaries.
    """
    county_names = list(COUNTY_CENTROIDS.keys())
    county_coords = np.array(list(COUNTY_CENTROIDS.values()))  # (26, 2) — (lng, lat)

    # Tile centroid coordinates — grid_gdf is already in WGS84 at this point
    lng = grid_gdf["centroid"].apply(lambda p: p.x).to_numpy()
    lat = grid_gdf["centroid"].apply(lambda p: p.y).to_numpy()
    tile_coords = np.column_stack([lng, lat])  # (N, 2)

    # Broadcast: (N, 1, 2) − (1, 26, 2) → (N, 26, 2) → squared distances (N, 26)
    diff = tile_coords[:, np.newaxis, :] - county_coords[np.newaxis, :, :]
    dist_sq = (diff ** 2).sum(axis=2)  # (N, 26)
    nearest_idx = np.argmin(dist_sq, axis=1)  # (N,)

    result = grid_gdf.copy()
    result["county"] = [county_names[i] for i in nearest_idx]
    return result


def load_tiles_to_db(grid_gdf: gpd.GeoDataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Truncate existing tiles (CASCADE to all FK children) and load the new grid.

    Uses psycopg2 execute_values for efficient batch inserts with PostGIS
    ST_GeomFromText for both polygon and centroid geometry columns.
    """
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tiles CASCADE"))
    print("  Truncated tiles and all dependent tables (CASCADE)")

    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()

        records = [
            (
                row.geometry.wkt,       # tile polygon WKT
                row["centroid"].wkt,    # centroid point WKT
                row["county"],
                row["grid_ref"],
                5.0,                    # area_km2 (nominal)
            )
            for _, row in grid_gdf.iterrows()
        ]

        execute_values(
            cursor,
            """
            INSERT INTO tiles (geom, centroid, county, grid_ref, area_km2)
            VALUES %s
            """,
            records,
            template=(
                "(ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s, %s, %s)"
            ),
            page_size=500,
        )

        raw_conn.commit()
        cursor.close()
        n = len(records)
        print(f"  Inserted {n:,} tiles")
        return n
    finally:
        raw_conn.close()


def main():
    """
    Full grid generation pipeline:
      1. Load Ireland boundary (Option A file or Option B hard-coded)
      2. Generate grid in EPSG:2157
      3. Reproject to EPSG:4326
      4. Assign counties (Voronoi from centroids)
      5. Load to DB (TRUNCATE CASCADE + batch insert)
    """
    print("=" * 60)
    print("Grid generation — Ireland 5 km² tile grid")
    print("=" * 60)

    engine = sqlalchemy.create_engine(DB_URL)

    print("\n[1/5] Loading Ireland boundary...")
    boundary = load_ireland_boundary()

    print("\n[2/5] Generating grid in EPSG:2157 (ITM)...")
    grid_itm = generate_grid_itm(boundary, TILE_SIZE_M)

    print("\n[3/5] Reprojecting to EPSG:4326 (WGS84)...")
    grid_wgs84 = reproject_to_wgs84(grid_itm)

    print("\n[4/5] Assigning counties (nearest-centroid Voronoi)...")
    grid_final = assign_counties(grid_wgs84, engine)

    print("  County tile counts:")
    county_counts = grid_final["county"].value_counts().sort_index()
    for county, count in county_counts.items():
        print(f"    {county:<14}: {count:>5}")
    print(f"  Counties present: {len(county_counts)} / 26")

    print("\n[5/5] Loading to database...")
    n = load_tiles_to_db(grid_final, engine)

    engine.dispose()
    print(f"\nGrid generation complete: {n:,} tiles inserted.")
    print("Run seed_synthetic.py next to populate score tables.")


if __name__ == "__main__":
    main()
