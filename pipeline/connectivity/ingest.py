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
from sqlalchemy import text
from shapely.geometry import Point
from pyproj import Transformer
from tqdm import tqdm
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, COMREG_BROADBAND_FILE, OSM_ROADS_FILE,
    INEX_DUBLIN_COORDS, INEX_CORK_COORDS, GRID_CRS_ITM, GRID_CRS_WGS84
)

# Broadband tier → score mapping (from ARCHITECTURE.md §5.5 / task spec)
TIER_SCORE = {"UFBB": 95, "SFBB": 72, "FBB": 45, "BB": 17}

# Max distance constants for log-inverse scoring
IX_MAX_DIST_KM = 300.0   # Donegal to Dublin is ~300 km
ROAD_MAX_DIST_KM = 50.0  # Ireland has dense road network


def _to_py(val):
    """Convert numpy scalar / NaN to a Python native type for psycopg2."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.floating):
        return float(val)
    if isinstance(val, np.bool_):
        return bool(val)
    return val


def _find_col(gdf: gpd.GeoDataFrame, candidates: list[str]) -> str | None:
    """Return first matching column (case-insensitive fallback)."""
    for c in candidates:
        if c in gdf.columns:
            return c
    lower_cols = {c.lower(): c for c in gdf.columns}
    for c in candidates:
        if c.lower() in lower_cols:
            return lower_cols[c.lower()]
    return None


def load_tiles(engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """Load tiles from DB into a GeoDataFrame in EPSG:2157 for spatial operations."""
    tiles = gpd.read_postgis(
        "SELECT tile_id, geom, centroid FROM tiles",
        engine,
        geom_col="geom",
        crs="EPSG:4326",
    )
    tiles = tiles.to_crs(GRID_CRS_ITM)
    # Rename geometry column for consistency with geopandas conventions
    tiles = tiles.rename_geometry("geometry")
    return tiles


def compute_ix_distances(tiles: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Compute distance from each tile centroid to INEX Dublin and INEX Cork.
    Returns DataFrame with tile_id, inex_dublin_km, inex_cork_km, ix_distance (0–100 score).

    Distance calculation in EPSG:2157 (metric), stored as km.
    ix_distance score: log-inverse normalisation using the closer IXP.
      score = 100 * max(0, 1 - log(1 + min_km) / log(1 + 300))
    """
    # Convert IXP coordinates from EPSG:4326 to EPSG:2157
    t = Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)
    dublin_x, dublin_y = t.transform(*INEX_DUBLIN_COORDS)
    cork_x, cork_y = t.transform(*INEX_CORK_COORDS)

    dublin_itm = Point(dublin_x, dublin_y)
    cork_itm = Point(cork_x, cork_y)

    # Compute distances from tile centroids (already in EPSG:2157) to each IXP
    centroids = tiles.geometry.centroid
    dublin_dist_m = centroids.distance(dublin_itm)
    cork_dist_m = centroids.distance(cork_itm)

    dublin_km = (dublin_dist_m / 1000).round(3)
    cork_km = (cork_dist_m / 1000).round(3)

    # Log-inverse score using the closer IXP
    min_km = np.minimum(dublin_km, cork_km)
    ix_distance = np.clip(
        100 * (1 - np.log1p(min_km) / np.log1p(IX_MAX_DIST_KM)), 0, 100
    ).round(2)

    return pd.DataFrame({
        "tile_id": tiles["tile_id"].values,
        "inex_dublin_km": dublin_km.values,
        "inex_cork_km": cork_km.values,
        "ix_distance": ix_distance.values,
    })


def compute_broadband(tiles: gpd.GeoDataFrame, comreg: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Assign ComReg broadband coverage tier to each tile (majority overlay).
    Map tier to 0–100 score using TIER_SCORE mapping.
    Returns DataFrame with tile_id, broadband (0–100), broadband_tier (str).
    """
    # Ensure both are in EPSG:2157
    if comreg.crs is None or comreg.crs.to_epsg() != 2157:
        comreg = comreg.to_crs(GRID_CRS_ITM)

    # Fix any invalid geometries
    comreg = comreg.copy()
    comreg["geometry"] = comreg.geometry.buffer(0)

    # Detect the tier column
    tier_col = _find_col(comreg, ["BB_TIER", "TIER", "COVERAGE_TIER", "broadband_tier"])
    if tier_col is None:
        print("  WARNING: No broadband tier column found in ComReg data.")
        print(f"  Available columns: {list(comreg.columns)}")
        # Return all zeros
        return pd.DataFrame({
            "tile_id": tiles["tile_id"].values,
            "broadband": np.zeros(len(tiles)),
            "broadband_tier": [None] * len(tiles),
        })

    # Normalise tier values to uppercase
    comreg["_tier"] = comreg[tier_col].astype(str).str.strip().str.upper()
    # Keep only recognised tiers
    valid_tiers = set(TIER_SCORE.keys())
    comreg = comreg[comreg["_tier"].isin(valid_tiers)].copy()

    if len(comreg) == 0:
        print("  WARNING: No valid broadband tier polygons after filtering.")
        return pd.DataFrame({
            "tile_id": tiles["tile_id"].values,
            "broadband": np.zeros(len(tiles)),
            "broadband_tier": [None] * len(tiles),
        })

    # Spatial overlay: intersection of tiles with ComReg polygons
    tiles_simple = tiles[["tile_id", "geometry"]].copy()
    comreg_simple = comreg[["_tier", "geometry"]].copy()

    print(f"  Running spatial overlay ({len(tiles_simple)} tiles × {len(comreg_simple)} ComReg polygons)...")
    try:
        overlay = gpd.overlay(tiles_simple, comreg_simple, how="intersection")
    except Exception as e:
        print(f"  WARNING: Overlay failed ({e}), falling back to spatial join.")
        # Fallback: centroid-based spatial join
        centroids_gdf = gpd.GeoDataFrame(
            {"tile_id": tiles["tile_id"].values},
            geometry=tiles.geometry.centroid,
            crs=GRID_CRS_ITM,
        )
        joined = gpd.sjoin(centroids_gdf, comreg_simple, how="left", predicate="within")
        joined = joined.drop_duplicates(subset="tile_id", keep="first")

        result = pd.DataFrame({"tile_id": tiles["tile_id"].values})
        merged = result.merge(
            joined[["tile_id", "_tier"]], on="tile_id", how="left"
        )
        merged["broadband_tier"] = merged["_tier"]
        merged["broadband"] = merged["_tier"].map(TIER_SCORE).fillna(0).round(2)
        return merged[["tile_id", "broadband", "broadband_tier"]]

    # Compute area of each intersection fragment
    overlay["frag_area"] = overlay.geometry.area

    # Group by tile_id and tier, sum areas
    tier_areas = (
        overlay.groupby(["tile_id", "_tier"])["frag_area"]
        .sum()
        .reset_index()
    )

    # For each tile, pick the tier with the largest total area (majority)
    idx_max = tier_areas.groupby("tile_id")["frag_area"].idxmax()
    majority = tier_areas.loc[idx_max, ["tile_id", "_tier"]].copy()

    # Map to scores
    result = pd.DataFrame({"tile_id": tiles["tile_id"].values})
    merged = result.merge(majority, on="tile_id", how="left")
    merged["broadband_tier"] = merged["_tier"]
    merged["broadband"] = merged["_tier"].map(TIER_SCORE).fillna(0).round(2)

    return merged[["tile_id", "broadband", "broadband_tier"]]


def compute_road_access(tiles: gpd.GeoDataFrame, roads: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Compute distance to nearest motorway/trunk road and nearest national primary road.
    Returns DataFrame with tile_id, road_access (0–100), nearest_motorway_junction_km,
    nearest_motorway_junction_name, nearest_national_road_km.
    """
    # Ensure roads are in EPSG:2157
    if roads.crs is None or roads.crs.to_epsg() != 2157:
        roads = roads.to_crs(GRID_CRS_ITM)

    # Build tile centroid GeoDataFrame
    centroids_gdf = gpd.GeoDataFrame(
        {"tile_id": tiles["tile_id"].values},
        geometry=tiles.geometry.centroid,
        crs=GRID_CRS_ITM,
    )

    result = pd.DataFrame({"tile_id": tiles["tile_id"].values})

    highway_col = _find_col(roads, ["highway", "Highway", "HIGHWAY"])
    if highway_col is None:
        print("  WARNING: No 'highway' column found in roads data.")
        result["nearest_motorway_junction_km"] = np.nan
        result["nearest_motorway_junction_name"] = None
        result["nearest_national_road_km"] = np.nan
        result["road_access"] = 0.0
        return result

    # ── Nearest motorway junction (point features) ──────────────────────────
    junctions = roads[roads[highway_col] == "motorway_junction"].copy()
    if len(junctions) > 0:
        # Cluster junctions within 500m to avoid pin overload
        keep_cols = ["geometry"]
        name_col = _find_col(junctions, ["name", "Name", "NAME"])
        ref_col = _find_col(junctions, ["ref", "Ref", "REF"])
        if name_col:
            keep_cols.append(name_col)
        if ref_col:
            keep_cols.append(ref_col)

        junctions_clean = junctions[keep_cols].reset_index(drop=True)

        joined = gpd.sjoin_nearest(
            centroids_gdf, junctions_clean,
            how="left", distance_col="junction_dist_m",
        )
        joined = joined.drop_duplicates(subset="tile_id", keep="first")

        merged = result.merge(
            joined[["tile_id", "junction_dist_m"]
                   + [c for c in (name_col, ref_col) if c and c in joined.columns]],
            on="tile_id", how="left",
        )
        result["nearest_motorway_junction_km"] = (merged["junction_dist_m"] / 1000).round(3)

        # Build junction name from name or ref
        if name_col and name_col in merged.columns:
            result["nearest_motorway_junction_name"] = merged[name_col]
        elif ref_col and ref_col in merged.columns:
            result["nearest_motorway_junction_name"] = merged[ref_col]
        else:
            result["nearest_motorway_junction_name"] = None
    else:
        # No junction points — fall back to nearest motorway line
        result["nearest_motorway_junction_km"] = np.nan
        result["nearest_motorway_junction_name"] = None

    # ── Nearest motorway/trunk road (line features) ─────────────────────────
    motorways = roads[roads[highway_col].isin(["motorway", "motorway_link", "trunk"])].copy()
    if len(motorways) > 0:
        # Dissolve collinear segments to speed up sjoin_nearest
        motorway_lines = motorways[["geometry"]].reset_index(drop=True)

        joined_road = gpd.sjoin_nearest(
            centroids_gdf, motorway_lines,
            how="left", distance_col="motorway_dist_m",
        )
        joined_road = joined_road.drop_duplicates(subset="tile_id", keep="first")

        merged_road = result.merge(
            joined_road[["tile_id", "motorway_dist_m"]],
            on="tile_id", how="left",
        )
        motorway_km = (merged_road["motorway_dist_m"] / 1000).round(3)

        # Use motorway distance if junction distance is missing
        if result["nearest_motorway_junction_km"].isna().all():
            result["nearest_motorway_junction_km"] = motorway_km
    else:
        motorway_km = pd.Series(np.nan, index=result.index)

    # ── Nearest national primary road ───────────────────────────────────────
    primaries = roads[roads[highway_col] == "primary"].copy()
    if len(primaries) > 0:
        primary_lines = primaries[["geometry"]].reset_index(drop=True)

        joined_primary = gpd.sjoin_nearest(
            centroids_gdf, primary_lines,
            how="left", distance_col="primary_dist_m",
        )
        joined_primary = joined_primary.drop_duplicates(subset="tile_id", keep="first")

        merged_primary = result.merge(
            joined_primary[["tile_id", "primary_dist_m"]],
            on="tile_id", how="left",
        )
        result["nearest_national_road_km"] = (merged_primary["primary_dist_m"] / 1000).round(3)
    else:
        result["nearest_national_road_km"] = np.nan

    # ── Road access score (log-inverse from closest major road) ─────────────
    # Use the minimum of motorway distance and national primary distance
    junction_km = result["nearest_motorway_junction_km"].fillna(ROAD_MAX_DIST_KM)
    national_km = result["nearest_national_road_km"].fillna(ROAD_MAX_DIST_KM)
    min_road_km = np.minimum(junction_km, national_km).clip(0, ROAD_MAX_DIST_KM)

    result["road_access"] = np.clip(
        100 * (1 - np.log1p(min_road_km) / np.log1p(ROAD_MAX_DIST_KM)), 0, 100
    ).round(2)

    return result


def compute_connectivity_scores(
    ix_df: pd.DataFrame,
    broadband_df: pd.DataFrame,
    road_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compose connectivity_scores. Weights:
      35% broadband + 30% ix_distance + 20% road_access + 15% (placeholder rail = 0)
    Rail data placeholder: set nearest_rail_freight_km=NULL until rail data available.
    """
    # Merge all sub-metric dataframes on tile_id
    df = ix_df.merge(broadband_df, on="tile_id", how="outer")
    df = df.merge(road_df, on="tile_id", how="outer")

    # Fill NaN scores with 0 for composite calculation
    broadband = df["broadband"].fillna(0)
    ix_distance = df["ix_distance"].fillna(0)
    road_access = df["road_access"].fillna(0)

    # Rail placeholder: 15% weight assigned to 0 until rail data available
    score = (0.35 * broadband + 0.30 * ix_distance + 0.20 * road_access + 0.15 * 0)
    score = score.clip(0, 100).round(2)

    df["score"] = score.values
    # Rail freight: NULL placeholder (no data available — ireland-data-sources.md §8)
    df["nearest_rail_freight_km"] = None

    # Select columns matching connectivity_scores table schema
    return df[[
        "tile_id", "score", "broadband", "ix_distance", "road_access",
        "inex_dublin_km", "inex_cork_km", "broadband_tier",
        "nearest_motorway_junction_km", "nearest_motorway_junction_name",
        "nearest_national_road_km", "nearest_rail_freight_km",
    ]]


def upsert_connectivity_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """Upsert connectivity_scores. ON CONFLICT(tile_id) DO UPDATE. Returns row count."""
    sql = """
        INSERT INTO connectivity_scores (
            tile_id, score, broadband, ix_distance, road_access,
            inex_dublin_km, inex_cork_km, broadband_tier,
            nearest_motorway_junction_km, nearest_motorway_junction_name,
            nearest_national_road_km, nearest_rail_freight_km
        ) VALUES %s
        ON CONFLICT (tile_id) DO UPDATE SET
            score                        = EXCLUDED.score,
            broadband                    = EXCLUDED.broadband,
            ix_distance                  = EXCLUDED.ix_distance,
            road_access                  = EXCLUDED.road_access,
            inex_dublin_km               = EXCLUDED.inex_dublin_km,
            inex_cork_km                 = EXCLUDED.inex_cork_km,
            broadband_tier               = EXCLUDED.broadband_tier,
            nearest_motorway_junction_km = EXCLUDED.nearest_motorway_junction_km,
            nearest_motorway_junction_name = EXCLUDED.nearest_motorway_junction_name,
            nearest_national_road_km     = EXCLUDED.nearest_national_road_km,
            nearest_rail_freight_km      = EXCLUDED.nearest_rail_freight_km
    """

    cols = [
        "tile_id", "score", "broadband", "ix_distance", "road_access",
        "inex_dublin_km", "inex_cork_km", "broadband_tier",
        "nearest_motorway_junction_km", "nearest_motorway_junction_name",
        "nearest_national_road_km", "nearest_rail_freight_km",
    ]

    rows = [tuple(_to_py(row[c]) for c in cols) for _, row in df.iterrows()]

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        batch_size = 2000
        for i in tqdm(range(0, len(rows), batch_size), desc="Upserting connectivity_scores"):
            execute_values(cur, sql, rows[i : i + batch_size])
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
        pg_conn.close()

    return len(rows)


def upsert_pins_connectivity(
    roads: gpd.GeoDataFrame | None,
    comreg: gpd.GeoDataFrame | None,
    engine: sqlalchemy.Engine,
) -> int:
    """
    Load connectivity pins:
      - IXP points (type='internet_exchange'): INEX Dublin + INEX Cork from config.py
      - Motorway junctions (type='motorway_junction'): from OSM roads
      - ComReg high-speed broadband areas (type='broadband_area'): centroid of UFBB zones

    Returns number of pins inserted.
    """
    pin_rows = []

    # ── IXP pins (static from config) ──────────────────────────────────────
    pin_rows.append({
        "lng": INEX_DUBLIN_COORDS[0],
        "lat": INEX_DUBLIN_COORDS[1],
        "name": "INEX Dublin",
        "type": "internet_exchange",
        "ix_asn": 2128,  # INEX AS number
        "road_ref": None,
        "ix_details": "Internet Neutral Exchange, Citywest, Dublin",
    })
    pin_rows.append({
        "lng": INEX_CORK_COORDS[0],
        "lat": INEX_CORK_COORDS[1],
        "name": "INEX Cork",
        "type": "internet_exchange",
        "ix_asn": 2128,
        "road_ref": None,
        "ix_details": "Internet Neutral Exchange, Cork City",
    })

    # ── Motorway junction pins from OSM ────────────────────────────────────
    if roads is not None:
        highway_col = _find_col(roads, ["highway", "Highway", "HIGHWAY"])
        if highway_col:
            junctions = roads[roads[highway_col] == "motorway_junction"].copy()
            if len(junctions) > 0:
                # Convert to WGS84 for storage
                junctions_wgs = junctions.to_crs(GRID_CRS_WGS84)

                # Cluster junctions within 500m to avoid pin overload
                junctions_itm = junctions.to_crs(GRID_CRS_ITM) if junctions.crs.to_epsg() != 2157 else junctions
                junctions_wgs = junctions_wgs.copy()
                junctions_wgs["_itm_geom"] = junctions_itm.geometry.values

                # Cluster junctions within 500m using STRtree (avoids O(n^2) distance loop)
                from shapely.strtree import STRtree
                import shapely

                # Extract ITM centroids for clustering
                itm_points = []
                for _, row in junctions_wgs.iterrows():
                    g = row["_itm_geom"]
                    itm_points.append(g.centroid if g.geom_type != "Point" else g)

                # Greedy clustering via spatial index
                added_mask = np.zeros(len(itm_points), dtype=bool)
                junction_pin_count = 0
                if len(itm_points) > 0:
                    tree = STRtree(itm_points)
                    # For each point, find all neighbours within 500m
                    for idx in range(len(itm_points)):
                        if added_mask[idx]:
                            continue
                        # Mark all points within 500m as "consumed"
                        neighbours = tree.query(itm_points[idx].buffer(500))
                        added_mask[neighbours] = True

                        row = junctions_wgs.iloc[idx]
                        geom = row.geometry
                        if geom.geom_type != "Point":
                            geom = geom.centroid

                        j_name_col = _find_col(junctions_wgs, ["name", "Name", "NAME"])
                        j_ref_col = _find_col(junctions_wgs, ["ref", "Ref", "REF"])

                        junction_name = None
                        road_ref = None
                        if j_name_col and pd.notna(row.get(j_name_col)):
                            junction_name = str(row[j_name_col])
                        if j_ref_col and pd.notna(row.get(j_ref_col)):
                            road_ref = str(row[j_ref_col])

                        pin_rows.append({
                            "lng": geom.x,
                            "lat": geom.y,
                            "name": junction_name or road_ref or "Motorway Junction",
                            "type": "motorway_junction",
                            "ix_asn": None,
                            "road_ref": road_ref,
                            "ix_details": None,
                        })
                        junction_pin_count += 1

                print(f"  Motorway junctions: {len(junctions)} total → {junction_pin_count} after clustering")

    # ── Broadband area pins (top-50 UFBB polygons by area) ─────────────────
    if comreg is not None:
        tier_col = _find_col(comreg, ["BB_TIER", "TIER", "COVERAGE_TIER", "broadband_tier"])
        if tier_col:
            comreg_copy = comreg.copy()
            comreg_copy["_tier"] = comreg_copy[tier_col].astype(str).str.strip().str.upper()
            ufbb = comreg_copy[comreg_copy["_tier"] == "UFBB"].copy()

            if len(ufbb) > 0:
                # Ensure in ITM for area calculation
                if ufbb.crs is None or ufbb.crs.to_epsg() != 2157:
                    ufbb = ufbb.to_crs(GRID_CRS_ITM)
                ufbb["_area"] = ufbb.geometry.area
                top50 = ufbb.nlargest(50, "_area")

                # Convert centroids to WGS84
                top50_wgs = top50.to_crs(GRID_CRS_WGS84)
                for _, row in top50_wgs.iterrows():
                    centroid = row.geometry.centroid
                    pin_rows.append({
                        "lng": centroid.x,
                        "lat": centroid.y,
                        "name": "UFBB Coverage Area",
                        "type": "broadband_area",
                        "ix_asn": None,
                        "road_ref": None,
                        "ix_details": None,
                    })
                print(f"  UFBB broadband areas: top {len(top50)} by area")
            else:
                print("  No UFBB polygons found in ComReg data — skipping broadband pins")
        else:
            print("  No tier column in ComReg data — skipping broadband pins")

    if not pin_rows:
        print("  No connectivity pins to insert.")
        return 0

    # Delete existing connectivity pins and re-insert (idempotent)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pins_connectivity"))

    # Insert pins and assign tile_id via ST_Within
    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO pins_connectivity (geom, name, type, ix_asn, road_ref, ix_details)
            VALUES %s
            """,
            [
                (
                    f"SRID=4326;POINT({r['lng']} {r['lat']})",
                    r["name"],
                    r["type"],
                    r["ix_asn"],
                    r["road_ref"],
                    r["ix_details"],
                )
                for r in pin_rows
            ],
            template="(ST_GeomFromEWKT(%s), %s, %s, %s, %s, %s)",
        )

        # Assign tile_id via ST_Within spatial join
        cur.execute("""
            UPDATE pins_connectivity p
            SET tile_id = (
                SELECT t.tile_id FROM tiles t
                WHERE ST_Within(p.geom, t.geom)
                LIMIT 1
            )
            WHERE tile_id IS NULL
        """)
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
        pg_conn.close()

    return len(pin_rows)


def main():
    """
    Connectivity ingest pipeline:
      1. Load tiles
      2. Compute IXP distances (from hardcoded coordinates in config)
      3. Load ComReg broadband data and compute broadband scores
      4. Load OSM roads and compute road access
      5. Compute composite connectivity scores
      6. Upsert connectivity_scores
      7. Upsert pins_connectivity

    Run AFTER: grid/generate_grid.py
    Run BEFORE: overall/compute_composite.py

    Required source files (see ireland-data-sources.md §8):
      /data/connectivity/comreg_broadband.gpkg   — ComReg broadband coverage
      /data/connectivity/osm_ireland_roads.gpkg  — OSM roads (motorway + primary)
    """
    print("=" * 60)
    print("Starting connectivity ingest...")
    print("=" * 60)

    # ── Check source files exist ───────────────────────────────────────────
    missing = [p for p in (COMREG_BROADBAND_FILE, OSM_ROADS_FILE) if not p.exists()]
    if missing:
        for p in missing:
            print(f"  ERROR: missing source file: {p}")
        print("\nRun first: python connectivity/download_sources.py")
        print("See ireland-data-sources.md §8 for manual download instructions.")
        raise SystemExit(1)

    engine = sqlalchemy.create_engine(DB_URL)

    # ── Step 1: Load tiles ─────────────────────────────────────────────────
    print("\n[1/7] Loading tiles from database...")
    tiles = load_tiles(engine)
    print(f"  Loaded {len(tiles)} tiles")

    # ── Step 2: IXP distances ──────────────────────────────────────────────
    print("\n[2/7] Computing IXP distances...")
    ix_df = compute_ix_distances(tiles)
    print(f"  Dublin: min={ix_df['inex_dublin_km'].min():.1f}, max={ix_df['inex_dublin_km'].max():.1f} km")
    print(f"  Cork:   min={ix_df['inex_cork_km'].min():.1f}, max={ix_df['inex_cork_km'].max():.1f} km")
    print(f"  IX score: min={ix_df['ix_distance'].min():.1f}, max={ix_df['ix_distance'].max():.1f}")

    # ── Step 3: Broadband ──────────────────────────────────────────────────
    print("\n[3/7] Loading ComReg broadband data...")
    comreg = gpd.read_file(str(COMREG_BROADBAND_FILE))
    print(f"  Loaded {len(comreg)} ComReg polygons")

    print("\n[4/7] Computing broadband scores...")
    broadband_df = compute_broadband(tiles, comreg)
    tier_counts = broadband_df["broadband_tier"].value_counts(dropna=False)
    print(f"  Tier distribution: {dict(tier_counts)}")
    print(f"  Broadband score: min={broadband_df['broadband'].min():.1f}, "
          f"max={broadband_df['broadband'].max():.1f}, mean={broadband_df['broadband'].mean():.1f}")

    # ── Step 4: Road access ────────────────────────────────────────────────
    print("\n[5/7] Loading OSM roads...")
    roads = gpd.read_file(str(OSM_ROADS_FILE))
    print(f"  Loaded {len(roads)} road features")
    if "highway" in roads.columns:
        print(f"  Highway types: {dict(roads['highway'].value_counts())}")

    print("\n[6/7] Computing road access...")
    road_df = compute_road_access(tiles, roads)
    print(f"  Road access score: min={road_df['road_access'].min():.1f}, "
          f"max={road_df['road_access'].max():.1f}, mean={road_df['road_access'].mean():.1f}")

    # ── Step 5: Composite scores ───────────────────────────────────────────
    print("\n[7/7] Computing composite connectivity scores...")
    scores_df = compute_connectivity_scores(ix_df, broadband_df, road_df)
    print(f"  Score: min={scores_df['score'].min():.2f}, max={scores_df['score'].max():.2f}, "
          f"mean={scores_df['score'].mean():.2f}")

    # ── Step 6: Upsert ─────────────────────────────────────────────────────
    print("\nUpserting connectivity_scores...")
    n = upsert_connectivity_scores(scores_df, engine)
    print(f"  Upserted {n} rows into connectivity_scores")

    # ── Step 7: Pins ───────────────────────────────────────────────────────
    print("\nUpserting connectivity pins...")
    # Roads need to be in ITM for clustering
    roads_itm = roads.to_crs(GRID_CRS_ITM)
    n_pins = upsert_pins_connectivity(roads_itm, comreg, engine)
    print(f"  Inserted {n_pins} connectivity pins")

    print("\n" + "=" * 60)
    print(f"Connectivity ingest complete: {n} tiles scored, {n_pins} pins inserted")
    print("Next step: restart Martin to serve updated tiles:")
    print("  docker compose restart martin")
    print("=" * 60)


if __name__ == "__main__":
    main()
