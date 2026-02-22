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
from shapely.geometry import Polygon
import shapely
import sqlalchemy
from sqlalchemy import text
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, IRELAND_BOUNDARY_FILE, IRELAND_COUNTIES_FILE,
    TILE_SIZE_M, GRID_CRS_ITM, GRID_CRS_WGS84,
)

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
        gdf = gdf[["geometry"]].to_crs(GRID_CRS_ITM)
    else:
        print(f"  Boundary file not found at {IRELAND_BOUNDARY_FILE}")
        print("  Using hard-coded Option B polygon (±20 km accuracy — OK for synthetic data)")
        poly = Polygon(IRELAND_POLYGON_WGS84)
        gdf = gpd.GeoDataFrame({"geometry": [poly]}, crs=GRID_CRS_WGS84)
        gdf = gdf.to_crs(GRID_CRS_ITM)

    # Simplify boundary — 500m tolerance in ITM is invisible at 5 km tile scale
    # but massively reduces vertex count (GADM coastlines have 50k+ vertices).
    # make_valid() fixes self-intersections that simplify() can introduce.
    gdf["geometry"] = shapely.make_valid(gdf.geometry.simplify(500).values)
    return gdf


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
    # prepare() builds an STRtree index — huge speedup for complex boundaries
    shapely.prepare(boundary_union)
    pts = shapely.points(cx_flat, cy_flat)
    within_mask = shapely.within(pts, boundary_union)
    within_idx = np.where(within_mask)[0]

    print(f"  {len(within_idx):,} tiles pass centroid-clip to Ireland boundary")

    # Vectorized tile construction — all geometry built in C, no Python loop
    row_indices = within_idx // n_cols
    col_indices = within_idx % n_cols

    x_vals = x_starts[col_indices]
    y_vals = y_starts[row_indices]
    half = tile_size_m / 2

    boxes = shapely.box(x_vals, y_vals, x_vals + tile_size_m, y_vals + tile_size_m)
    centroids = shapely.points(x_vals + half, y_vals + half)
    grid_refs = [f"R{r:04d}C{c:04d}" for r, c in zip(row_indices, col_indices)]

    return gpd.GeoDataFrame(
        {
            "geometry": boxes,
            "centroid": centroids,
            "row": row_indices,
            "col": col_indices,
            "grid_ref": grid_refs,
        },
        crs=GRID_CRS_ITM,
    )


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


def _voronoi_county_assign(grid_gdf: gpd.GeoDataFrame, mask=None) -> list:
    """
    Assign county names via nearest-centroid Voronoi (fallback).

    mask: boolean array or None — if given, only assign for True rows.
    Returns a list of county name strings (length = len(grid_gdf) if mask is
    None, else len(mask.sum())).
    """
    county_names = list(COUNTY_CENTROIDS.keys())
    county_coords = np.array(list(COUNTY_CENTROIDS.values()))  # (26, 2) (lng, lat)

    sub_gdf = grid_gdf if mask is None else grid_gdf[mask]
    centroid_arr = np.asarray(sub_gdf["centroid"].values)
    lng = shapely.get_x(centroid_arr)
    lat = shapely.get_y(centroid_arr)
    tile_coords = np.column_stack([lng, lat])

    diff = tile_coords[:, np.newaxis, :] - county_coords[np.newaxis, :, :]
    dist_sq = (diff ** 2).sum(axis=2)
    nearest_idx = np.argmin(dist_sq, axis=1)
    return [county_names[i] for i in nearest_idx]


def assign_counties(grid_gdf: gpd.GeoDataFrame, engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """
    Assign county name to each tile.

    Option A: PostGIS spatial join against OSi county boundary file when available.
              Tiles whose centroid falls outside every county polygon (coastal fringe)
              are assigned by Voronoi fallback.
    Option B (fallback): Nearest-centroid Voronoi from COUNTY_CENTROIDS — used when
              IRELAND_COUNTIES_FILE is absent.
    """
    if IRELAND_COUNTIES_FILE.exists():
        print(f"  Using county boundary file: {IRELAND_COUNTIES_FILE}")
        counties = gpd.read_file(IRELAND_COUNTIES_FILE).to_crs(GRID_CRS_WGS84)
        # Simplify county boundaries — 0.005° ≈ 500m, fine for 5 km tiles
        counties["geometry"] = shapely.make_valid(counties.geometry.simplify(0.005).values)

        # Identify the county name column (OSi: COUNTY/COUNTY_NAME; GADM: NAME_1)
        name_col_candidates = [
            c for c in counties.columns
            if "county" in c.lower() or c in ("NAME", "NAME_1")
        ]
        if not name_col_candidates:
            print("  Warning: could not identify county name column — falling back to Voronoi")
        else:
            name_col = name_col_candidates[0]
            print(f"  County name column: '{name_col}'")

            # Normalise county names: strip leading "County " prefix if present
            # Note: inline flag (?i) must precede the pattern anchor in Python 3.12+
            counties = counties.copy()
            counties[name_col] = counties[name_col].str.replace(
                r"(?i)^county\s+", "", regex=True
            ).str.strip()

            # Build a centroid-only GDF for the spatial join
            centroid_gdf = gpd.GeoDataFrame(
                index=grid_gdf.index,
                geometry=grid_gdf["centroid"].values,
                crs=GRID_CRS_WGS84,
            )
            joined = gpd.sjoin(
                centroid_gdf,
                counties[["geometry", name_col]],
                how="left",
                predicate="within",
            )
            # Drop duplicate matches (centroids that fall on county boundaries)
            joined = joined[~joined.index.duplicated(keep="first")]

            result = grid_gdf.copy()
            county_series = joined[name_col].reindex(result.index)
            n_unmatched = county_series.isna().sum()

            if n_unmatched > 0:
                print(f"  {n_unmatched} tiles unmatched in spatial join — applying Voronoi fallback")
                unmatched_mask = county_series.isna().to_numpy()
                voronoi_names = _voronoi_county_assign(result, mask=unmatched_mask)
                county_series = county_series.copy()
                county_series.iloc[unmatched_mask] = voronoi_names

            result["county"] = county_series.values
            return result

    # Option B: Voronoi fallback (no county file)
    print("  County boundary file not found — using nearest-centroid Voronoi assignment")
    result = grid_gdf.copy()
    result["county"] = _voronoi_county_assign(grid_gdf)
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

        # Vectorized WKT generation — single C call instead of per-row .wkt
        geom_wkts = shapely.to_wkt(grid_gdf.geometry.values)
        centroid_wkts = shapely.to_wkt(np.asarray(grid_gdf["centroid"].values))
        counties = grid_gdf["county"].values
        grid_refs = grid_gdf["grid_ref"].values

        records = [
            (geom_wkts[i], centroid_wkts[i], counties[i], grid_refs[i], 5.0)
            for i in range(len(grid_gdf))
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


def upsert_counties(counties_gdf: gpd.GeoDataFrame, name_col: str, engine: sqlalchemy.Engine) -> None:
    """
    Upsert county boundaries into the counties table.

    Adds a geom column (GEOMETRY(MultiPolygon, 4326)) if not present — the base
    schema only requires county_name TEXT PRIMARY KEY. Normalises county names to
    strip any leading "County " prefix so they match the FK reference in tiles.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE counties ADD COLUMN IF NOT EXISTS "
            "geom GEOMETRY(MultiPolygon, 4326)"
        ))

    from shapely.geometry import MultiPolygon as MPolygon

    with engine.begin() as conn:
        for _, row in counties_gdf.iterrows():
            county_name = str(row[name_col]).strip()
            if county_name.lower().startswith("county "):
                county_name = county_name[7:].strip()

            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == "Polygon":
                geom = MPolygon([geom])

            conn.execute(text("""
                INSERT INTO counties (county_name, geom)
                VALUES (:county_name, ST_GeomFromText(:wkt, 4326))
                ON CONFLICT (county_name) DO UPDATE SET geom = EXCLUDED.geom
            """), {"county_name": county_name, "wkt": geom.wkt})

    print(f"  Upserted {len(counties_gdf)} county geometries into counties table")


def main():
    """
    Full grid generation pipeline:
      1. Load Ireland boundary (Option A file or Option B hard-coded)
      2. Generate grid in EPSG:2157
      3. Reproject to EPSG:4326
      4. Assign counties (spatial join or Voronoi fallback)
      5. Load to DB (TRUNCATE CASCADE + batch insert)
      6. Populate counties table with geometries (if county file present)
    """
    print("=" * 60)
    print("Grid generation — Ireland 5 km² tile grid")
    print("=" * 60)

    engine = sqlalchemy.create_engine(DB_URL)

    print("\n[1/6] Loading Ireland boundary...")
    boundary = load_ireland_boundary()

    print("\n[2/6] Generating grid in EPSG:2157 (ITM)...")
    grid_itm = generate_grid_itm(boundary, TILE_SIZE_M)

    print("\n[3/6] Reprojecting to EPSG:4326 (WGS84)...")
    grid_wgs84 = reproject_to_wgs84(grid_itm)

    print("\n[4/6] Assigning counties...")
    grid_final = assign_counties(grid_wgs84, engine)

    print("  County tile counts:")
    county_counts = grid_final["county"].value_counts().sort_index()
    for county, count in county_counts.items():
        print(f"    {county:<14}: {count:>5}")
    print(f"  Counties present: {len(county_counts)} / 26")

    print("\n[5/6] Loading to database...")
    n = load_tiles_to_db(grid_final, engine)

    print("\n[6/6] Populating counties table...")
    if IRELAND_COUNTIES_FILE.exists():
        counties_gdf = gpd.read_file(IRELAND_COUNTIES_FILE).to_crs(GRID_CRS_WGS84)
        name_col_candidates = [
            c for c in counties_gdf.columns
            if "county" in c.lower() or c in ("NAME", "NAME_1")
        ]
        if name_col_candidates:
            upsert_counties(counties_gdf, name_col_candidates[0], engine)
        else:
            print("  Skipped: could not identify county name column")
    else:
        print("  Skipped: county boundary file not present")

    engine.dispose()
    print(f"\nGrid generation complete: {n:,} tiles inserted.")
    print("Run seed_synthetic.py next to populate score tables.")


if __name__ == "__main__":
    main()
