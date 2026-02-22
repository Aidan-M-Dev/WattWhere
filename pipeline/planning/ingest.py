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
from collections import defaultdict
from pathlib import Path
import numpy as np
import geopandas as gpd
import pandas as pd
import sqlalchemy
from sqlalchemy import text
from tqdm import tqdm
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, MYPLAN_ZONING_FILE, PLANNING_APPLICATIONS_FILE,
    CSO_POPULATION_FILE, GRID_CRS_ITM
)

# Zoning category mapping — MyPlan GZT codes to our categories
ZONING_MAP = {
    "Industrial": "industrial",
    "I1": "industrial",
    "I2": "industrial",
    "Enterprise": "enterprise",
    "E1": "enterprise",
    "E2": "enterprise",
    "Mixed Use": "mixed_use",
    "M": "mixed_use",
    "M1": "mixed_use",
    "Agricultural": "agricultural",
    "A": "agricultural",
    "A1": "agricultural",
    "Residential": "residential",
    "R1": "residential",
    "R2": "residential",
    "R": "residential",
}


def _to_py(val):
    """Convert numpy scalar / NaN to Python native type for psycopg2."""
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
    """Load tiles from DB in EPSG:2157 for spatial overlay operations."""
    tiles = gpd.read_postgis(
        "SELECT tile_id, geom, centroid FROM tiles",
        engine,
        geom_col="geom",
        crs="EPSG:4326",
    )
    tiles = tiles.rename_geometry("geometry")
    return tiles.to_crs(GRID_CRS_ITM)


def compute_zoning_overlay(
    tiles: gpd.GeoDataFrame,
    zoning: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Compute zoning category percentages for each tile via area intersection.

    Returns DataFrame with tile_id, pct_industrial, pct_enterprise, pct_mixed_use,
    pct_agricultural, pct_residential, pct_other, zoning_tier (0–100).
    """
    # Ensure zoning is in ITM for area calculations
    if zoning.crs is None or zoning.crs.to_epsg() != 2157:
        zoning = zoning.to_crs(GRID_CRS_ITM)

    # Ensure valid geometry names
    if zoning.geometry.name != "geometry":
        zoning = zoning.rename_geometry("geometry")

    # Fix invalid geometries
    zoning = zoning.copy()
    zoning["geometry"] = zoning.geometry.buffer(0)

    # Detect category column
    cat_col = _find_col(zoning, ["CATEGORY", "GZT_CODE", "ZONE_TYPE", "ZONING",
                                   "zone_type", "category", "LandUseZoning"])
    if cat_col is None:
        print("  WARNING: No zoning category column found. Using 'Other' for all.")
        zoning["_category"] = "other"
    else:
        # Map zoning codes to our categories
        def _map_category(val):
            if pd.isna(val):
                return "other"
            s = str(val).strip()
            if s in ZONING_MAP:
                return ZONING_MAP[s]
            # Check if the value contains a known keyword
            sl = s.lower()
            if "industrial" in sl:
                return "industrial"
            if "enterprise" in sl:
                return "enterprise"
            if "mixed" in sl:
                return "mixed_use"
            if "agri" in sl:
                return "agricultural"
            if "resid" in sl:
                return "residential"
            return "other"

        zoning["_category"] = zoning[cat_col].apply(_map_category)

    print(f"  Zoning category distribution: {dict(zoning['_category'].value_counts())}")

    # Spatial overlay — intersection to compute area-weighted percentages
    print("  Computing spatial overlay (tiles × zoning)...")
    try:
        overlay = gpd.overlay(
            tiles[["tile_id", "geometry"]],
            zoning[["_category", "geometry"]],
            how="intersection",
            keep_geom_type=False,
        )
    except Exception as e:
        print(f"  WARNING: gpd.overlay failed ({e}), falling back to sjoin")
        # Fallback: simple spatial join (majority category per tile)
        joined = gpd.sjoin(tiles[["tile_id", "geometry"]], zoning[["_category", "geometry"]],
                           how="left", predicate="intersects")
        # Take the most common category per tile
        majority = joined.groupby("tile_id")["_category"].agg(
            lambda x: x.mode().iloc[0] if len(x.mode()) > 0 else "other"
        )
        result = tiles[["tile_id"]].copy()
        result = result.set_index("tile_id")
        for cat in ["industrial", "enterprise", "mixed_use", "agricultural", "residential", "other"]:
            result[f"pct_{cat}"] = 0.0
        for tid, cat in majority.items():
            col = f"pct_{cat}"
            if col in result.columns:
                result.loc[tid, col] = 100.0
        result = result.reset_index()
        # Compute zoning_tier
        result["zoning_tier"] = _compute_zoning_tier(result)
        return result

    # Compute area of each intersection fragment
    overlay["_frag_area"] = overlay.geometry.area

    # Aggregate by tile_id + category
    agg = overlay.groupby(["tile_id", "_category"])["_frag_area"].sum().reset_index()

    # Compute tile total intersected area
    tile_total = agg.groupby("tile_id")["_frag_area"].sum().rename("_total_area")
    agg = agg.merge(tile_total, on="tile_id")
    agg["pct"] = (agg["_frag_area"] / agg["_total_area"] * 100).round(2)

    # Pivot to wide format
    pivot = agg.pivot_table(
        index="tile_id", columns="_category", values="pct", fill_value=0.0
    ).reset_index()

    # Ensure all expected columns exist
    for cat in ["industrial", "enterprise", "mixed_use", "agricultural", "residential", "other"]:
        if cat not in pivot.columns:
            pivot[cat] = 0.0

    # Rename to pct_ prefix
    result = pivot.rename(columns={
        "industrial": "pct_industrial",
        "enterprise": "pct_enterprise",
        "mixed_use": "pct_mixed_use",
        "agricultural": "pct_agricultural",
        "residential": "pct_residential",
        "other": "pct_other",
    })

    # Ensure all tiles are present (tiles with no zoning = all "other")
    all_tiles = tiles[["tile_id"]].copy()
    result = all_tiles.merge(result, on="tile_id", how="left")
    for col in ["pct_industrial", "pct_enterprise", "pct_mixed_use",
                "pct_agricultural", "pct_residential", "pct_other"]:
        result[col] = result[col].fillna(0.0)

    # Tiles with no zoning overlay get 100% "other"
    row_sum = (result["pct_industrial"] + result["pct_enterprise"] +
               result["pct_mixed_use"] + result["pct_agricultural"] +
               result["pct_residential"] + result["pct_other"])
    result.loc[row_sum == 0, "pct_other"] = 100.0

    # Compute zoning_tier score
    result["zoning_tier"] = _compute_zoning_tier(result)

    return result[["tile_id", "pct_industrial", "pct_enterprise", "pct_mixed_use",
                    "pct_agricultural", "pct_residential", "pct_other", "zoning_tier"]]


def _compute_zoning_tier(df: pd.DataFrame) -> pd.Series:
    """
    Compute zoning_tier score (0–100) from zoning percentages.

    Formula (from TASKS.md):
      ie_pct = pct_industrial + pct_enterprise
      tier = 10  # default (unzoned/agri)
      if pct_residential > 50: tier = min(tier, 10)
      elif ie_pct > 50: tier = 80 + (ie_pct / 100) * 20
      elif pct_mixed_use > 30: tier = 50 + (pct_mixed_use / 100) * 20
      elif pct_agricultural > 50: tier = 10 + (pct_agricultural / 100) * 20
    """
    ie_pct = df["pct_industrial"] + df["pct_enterprise"]
    tier = pd.Series(10.0, index=df.index)  # default

    # Agricultural > 50%
    mask_agri = df["pct_agricultural"] > 50
    tier = np.where(mask_agri, 10 + (df["pct_agricultural"] / 100) * 20, tier)

    # Mixed use > 30%
    mask_mixed = df["pct_mixed_use"] > 30
    tier = np.where(mask_mixed, 50 + (df["pct_mixed_use"] / 100) * 20, tier)

    # Industrial + Enterprise > 50% (highest priority non-residential)
    mask_ie = ie_pct > 50
    tier = np.where(mask_ie, 80 + (ie_pct / 100) * 20, tier)

    # Residential > 50% always caps at 10
    mask_res = df["pct_residential"] > 50
    tier = np.where(mask_res, np.minimum(tier, 10), tier)

    return pd.Series(tier, index=df.index).clip(0, 100).round(2)


def compute_planning_applications(
    tiles: gpd.GeoDataFrame,
    applications: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Spatial join planning applications to tiles.
    Compute planning_precedent score: proximity to DC/industrial applications.

    Returns DataFrame with tile_id, planning_precedent (0–60),
    and 'applications' column (list of dicts for tile_planning_applications).
    """
    if applications.crs is None or applications.crs.to_epsg() != 2157:
        applications = applications.to_crs(GRID_CRS_ITM)

    # Detect columns
    ref_col = _find_col(applications, ["APP_REF", "app_ref", "PlanRef", "REF", "Reference"])
    type_col = _find_col(applications, ["APP_TYPE", "app_type", "DevType", "TYPE", "Type"])
    status_col = _find_col(applications, ["STATUS", "status", "Decision", "DECISION"])
    date_col = _find_col(applications, ["APP_DATE", "app_date", "DecDate", "DATE", "Date"])
    name_col = _find_col(applications, ["NAME", "name", "Name", "DESCRIPTION", "Description"])

    # Identify DC-related applications
    if type_col:
        dc_mask = applications[type_col].astype(str).str.lower().str.contains(
            "data.cent|industrial|technolog", regex=True, na=False
        )
    else:
        dc_mask = pd.Series(True, index=applications.index)

    dc_apps = applications[dc_mask].copy()
    print(f"  DC/industrial applications: {len(dc_apps)} of {len(applications)} total")

    # Direct spatial join: applications within tiles
    joined = gpd.sjoin(
        tiles[["tile_id", "geometry"]],
        applications[["geometry"]].assign(_app_idx=range(len(applications))),
        how="left",
        predicate="contains",
    )

    # Build per-tile application lists
    app_lists = defaultdict(list)
    for _, row in joined.dropna(subset=["_app_idx"]).iterrows():
        idx = int(row["_app_idx"])
        app_row = applications.iloc[idx]
        app_lists[row["tile_id"]].append({
            "tile_id": int(row["tile_id"]),
            "app_ref": str(app_row[ref_col]) if ref_col else f"APP-{idx}",
            "name": str(app_row[name_col]) if name_col else None,
            "status": str(app_row[status_col]).lower() if status_col else "other",
            "app_date": str(app_row[date_col]) if date_col else None,
            "app_type": str(app_row[type_col]) if type_col else None,
        })

    # Planning precedent: tiles within 10 km of any DC application
    print("  Computing planning precedent (10 km buffer)...")
    buffered = tiles[["tile_id", "geometry"]].copy()
    buffered["geometry"] = tiles.geometry.buffer(10_000)  # 10 km in EPSG:2157

    if len(dc_apps) > 0:
        has_dc_nearby = gpd.sjoin(
            buffered, dc_apps[["geometry"]], how="left", predicate="intersects"
        )

        # Score: any DC nearby → +40, granted DC nearby → additional +20
        dc_nearby_tiles = has_dc_nearby.dropna(subset=["index_right"]).groupby("tile_id")
        precedent = pd.Series(0.0, index=tiles["tile_id"])

        for tid, group in dc_nearby_tiles:
            precedent[tid] = 40.0

            # Check if any granted applications are nearby
            if status_col:
                app_indices = group["index_right"].astype(int).values
                statuses = dc_apps.iloc[app_indices][status_col].astype(str).str.lower()
                if (statuses == "granted").any():
                    precedent[tid] = 60.0
    else:
        precedent = pd.Series(0.0, index=tiles["tile_id"])

    result = pd.DataFrame({
        "tile_id": tiles["tile_id"],
        "planning_precedent": precedent.reindex(tiles["tile_id"]).fillna(0.0).clip(0, 60).round(2).values,
    })

    # Attach application lists
    result["applications"] = result["tile_id"].apply(lambda tid: app_lists.get(tid, []))

    return result


def compute_population_density(
    tiles: gpd.GeoDataFrame,
    cso_pop: gpd.GeoDataFrame,
) -> pd.Series:
    """
    Compute population density (persons/km²) for each tile from CSO Small Area stats.
    Area-weighted population aggregation.

    Returns Series indexed by tile_id → population_density_per_km2.
    """
    if cso_pop.crs is None or cso_pop.crs.to_epsg() != 2157:
        cso_pop = cso_pop.to_crs(GRID_CRS_ITM)

    # Fix geometry column name
    if cso_pop.geometry.name != "geometry":
        cso_pop = cso_pop.rename_geometry("geometry")

    # Fix invalid geometries
    cso_pop = cso_pop.copy()
    cso_pop["geometry"] = cso_pop.geometry.buffer(0)

    # Detect population column
    pop_col = _find_col(cso_pop, ["TOTAL_POP", "T1_1AGETT", "POPULATION", "Pop",
                                    "Total_Pop", "total_pop", "PERSONS", "persons"])
    if pop_col is None:
        print("  WARNING: No population column found. Using 0 for all tiles.")
        return pd.Series(0.0, index=tiles["tile_id"], name="pop_density")

    cso_pop["_pop"] = pd.to_numeric(cso_pop[pop_col], errors="coerce").fillna(0)
    cso_pop["_sa_area"] = cso_pop.geometry.area  # m²

    # Spatial overlay to compute area-weighted population
    print("  Computing population overlay (tiles × small areas)...")
    try:
        overlay = gpd.overlay(
            tiles[["tile_id", "geometry"]],
            cso_pop[["_pop", "_sa_area", "geometry"]],
            how="intersection",
            keep_geom_type=False,
        )
    except Exception as e:
        print(f"  WARNING: overlay failed ({e}), falling back to sjoin")
        # Fallback: sum population of small areas whose centroids fall in each tile
        cso_centroids = cso_pop.copy()
        cso_centroids["geometry"] = cso_pop.geometry.centroid
        joined = gpd.sjoin(cso_centroids, tiles[["tile_id", "geometry"]],
                           how="inner", predicate="within")
        pop_sum = joined.groupby("tile_id")["_pop"].sum()
        density = pop_sum / 5.0  # 5 km² tiles
        return density.reindex(tiles["tile_id"]).fillna(0.0).rename("pop_density")

    overlay["_frag_area"] = overlay.geometry.area
    # Weight: fraction of the SA that falls in this tile
    overlay["_weight"] = overlay["_frag_area"] / overlay["_sa_area"].clip(lower=1)
    overlay["_weighted_pop"] = overlay["_pop"] * overlay["_weight"]

    pop_per_tile = overlay.groupby("tile_id")["_weighted_pop"].sum()
    density = pop_per_tile / 5.0  # 5 km² tiles

    return density.reindex(tiles["tile_id"]).fillna(0.0).rename("pop_density")


def compute_nearest_ida_km(tiles: gpd.GeoDataFrame, engine: sqlalchemy.Engine) -> pd.Series:
    """
    Compute distance from each tile centroid to nearest IDA site.
    Returns Series[tile_id → distance_km]. NaN if no IDA sites exist.
    """
    try:
        ida = gpd.read_postgis(
            "SELECT ida_site_id, geom FROM ida_sites",
            engine,
            geom_col="geom",
            crs="EPSG:4326",
        )
    except Exception:
        ida = gpd.GeoDataFrame(columns=["ida_site_id", "geom"], geometry="geom")

    if len(ida) == 0:
        print("  IDA sites table is empty — returning NaN for nearest_ida_site_km")
        return pd.Series(np.nan, index=tiles["tile_id"], name="nearest_ida_site_km")

    ida = ida.to_crs(GRID_CRS_ITM)
    if ida.geometry.name != "geometry":
        ida = ida.rename_geometry("geometry")

    # Compute tile centroids in ITM
    centroids = tiles.copy()
    centroids["geometry"] = tiles.geometry.centroid

    # Nearest join
    nearest = gpd.sjoin_nearest(
        centroids[["tile_id", "geometry"]],
        ida[["geometry"]],
        how="left",
        distance_col="dist_m",
    )

    # Take minimum distance per tile (in case of multiple matches)
    min_dist = nearest.groupby("tile_id")["dist_m"].min() / 1000.0  # m → km
    return min_dist.reindex(tiles["tile_id"]).rename("nearest_ida_site_km")


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
      + 10 if planning_precedent > 40 (DC planning history nearby)
      − 20 if pct_residential > 0 (residential zoning present)
      clamp to [0, 100]
    """
    result = zoning_df.copy()

    # Merge planning precedent
    result = result.merge(
        planning_df[["tile_id", "planning_precedent"]],
        on="tile_id", how="left",
    )
    result["planning_precedent"] = result["planning_precedent"].fillna(0)

    # Compute final score
    score = result["zoning_tier"].copy()
    score = score + np.where(result["planning_precedent"] > 40, 10, 0)
    score = score - np.where(result["pct_residential"] > 0, 20, 0)
    result["score"] = score.clip(0, 100).round(2)

    # Add population density and IDA distance
    result["population_density_per_km2"] = pop_density.reindex(result["tile_id"]).values
    result["nearest_ida_site_km"] = ida_km.reindex(result["tile_id"]).values

    return result


def upsert_planning_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """Upsert planning_scores. ON CONFLICT(tile_id) DO UPDATE. Returns row count."""
    sql = """
        INSERT INTO planning_scores (
            tile_id, score, zoning_tier, planning_precedent,
            pct_industrial, pct_enterprise, pct_mixed_use,
            pct_agricultural, pct_residential, pct_other,
            nearest_ida_site_km, population_density_per_km2
        ) VALUES %s
        ON CONFLICT (tile_id) DO UPDATE SET
            score                      = EXCLUDED.score,
            zoning_tier                = EXCLUDED.zoning_tier,
            planning_precedent         = EXCLUDED.planning_precedent,
            pct_industrial             = EXCLUDED.pct_industrial,
            pct_enterprise             = EXCLUDED.pct_enterprise,
            pct_mixed_use              = EXCLUDED.pct_mixed_use,
            pct_agricultural           = EXCLUDED.pct_agricultural,
            pct_residential            = EXCLUDED.pct_residential,
            pct_other                  = EXCLUDED.pct_other,
            nearest_ida_site_km        = EXCLUDED.nearest_ida_site_km,
            population_density_per_km2 = EXCLUDED.population_density_per_km2
    """

    cols = [
        "tile_id", "score", "zoning_tier", "planning_precedent",
        "pct_industrial", "pct_enterprise", "pct_mixed_use",
        "pct_agricultural", "pct_residential", "pct_other",
        "nearest_ida_site_km", "population_density_per_km2",
    ]

    rows = [tuple(_to_py(row[c]) for c in cols) for _, row in df.iterrows()]

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        batch_size = 500
        for i in tqdm(range(0, len(rows), batch_size), desc="Upserting planning_scores"):
            execute_values(cur, sql, rows[i: i + batch_size])
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
        pg_conn.close()

    return len(rows)


def upsert_planning_applications(planning_df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Delete existing applications per tile, insert new ones.
    (Delete+insert pattern, same as tile_designation_overlaps.)
    Returns total rows inserted.
    """
    # Explode the 'applications' list column into individual rows
    app_rows: list[dict] = []
    for _, row in planning_df.iterrows():
        for app in (row.get("applications") or []):
            app_rows.append(app)

    if not app_rows:
        print("  No planning applications to insert.")
        return 0

    affected_tile_ids = list({r["tile_id"] for r in app_rows})
    rows_by_tile: dict[int, list] = defaultdict(list)
    for r in app_rows:
        rows_by_tile[r["tile_id"]].append(r)

    pg_conn = engine.raw_connection()
    total_inserted = 0
    try:
        cur = pg_conn.cursor()
        batch_size = 500

        for i in tqdm(
            range(0, len(affected_tile_ids), batch_size),
            desc="Upserting planning applications",
        ):
            batch_ids = affected_tile_ids[i: i + batch_size]

            # Delete existing applications for this batch
            cur.execute(
                "DELETE FROM tile_planning_applications WHERE tile_id = ANY(%s)",
                (batch_ids,),
            )

            # Insert fresh applications
            batch_rows = [r for tid in batch_ids for r in rows_by_tile.get(tid, [])]
            if batch_rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO tile_planning_applications
                        (tile_id, app_ref, name, status, app_date, app_type)
                    VALUES %s
                    """,
                    [
                        (
                            r["tile_id"],
                            r["app_ref"],
                            r.get("name"),
                            r["status"],
                            r.get("app_date"),
                            r.get("app_type"),
                        )
                        for r in batch_rows
                    ],
                )
                total_inserted += len(batch_rows)

        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
        pg_conn.close()

    return total_inserted


def upsert_pins_planning(
    zoning: gpd.GeoDataFrame,
    applications: gpd.GeoDataFrame,
    engine: sqlalchemy.Engine,
) -> int:
    """
    Load planning pins:
      - Industrial/Enterprise zoned parcel centroids (type='zoning_parcel')
        Cluster to ~500 representative pins by dissolving small adjacent parcels.
      - Recent data centre planning applications (type='planning_application')
      IDA sites NOT loaded here — they are managed manually in ida_sites table.

    Returns count of pins inserted.
    """
    pin_rows: list[dict] = []

    # ── Zoning parcel pins (Industrial/Enterprise) ──────────────────────────
    if zoning.crs is None or zoning.crs.to_epsg() != 2157:
        zoning = zoning.to_crs(GRID_CRS_ITM)

    cat_col = _find_col(zoning, ["CATEGORY", "GZT_CODE", "ZONE_TYPE", "ZONING",
                                   "_category", "category"])
    if cat_col:
        ie_mask = zoning[cat_col].astype(str).str.lower().str.contains(
            "industrial|enterprise|i1|i2|e1|e2", regex=True, na=False
        )
        ie_parcels = zoning[ie_mask].copy()
    else:
        ie_parcels = gpd.GeoDataFrame()

    if len(ie_parcels) > 0:
        # Cluster by dissolving parcels within 500m of each other,
        # then take representative points, limited to ~500 pins
        ie_wgs = ie_parcels.to_crs("EPSG:4326")

        # Simple clustering: skip parcels whose centroids are within 2km of already-added ones
        ie_itm = ie_parcels.copy()
        added_points = []
        cluster_dist = 2000  # 2 km minimum spacing

        for _, row in ie_itm.iterrows():
            geom = row.geometry
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                centroid = geom.representative_point()
            else:
                centroid = geom.centroid

            too_close = False
            for ap in added_points:
                if centroid.distance(ap) < cluster_dist:
                    too_close = True
                    break
            if too_close:
                continue
            added_points.append(centroid)

            # Convert centroid to WGS84
            centroid_wgs = gpd.GeoSeries([centroid], crs=GRID_CRS_ITM).to_crs("EPSG:4326").iloc[0]

            cat_val = str(row[cat_col]) if cat_col else "Industrial/Enterprise"
            pin_rows.append({
                "lng": float(centroid_wgs.x),
                "lat": float(centroid_wgs.y),
                "name": f"{cat_val} Zoned Parcel",
                "type": "zoning_parcel",
                "app_ref": None,
                "app_status": None,
                "app_date": None,
                "app_type": None,
            })

            if len(pin_rows) >= 500:
                break

        print(f"  Zoning parcel pins: {len(ie_parcels)} parcels → {len(pin_rows)} after clustering")
    else:
        print("  No Industrial/Enterprise parcels found for zoning pins")

    # ── Planning application pins ────────────────────────────────────────────
    if len(applications) > 0:
        if applications.crs is None or applications.crs.to_epsg() != 4326:
            apps_wgs = applications.to_crs("EPSG:4326")
        else:
            apps_wgs = applications

        ref_col = _find_col(apps_wgs, ["APP_REF", "app_ref", "PlanRef", "REF"])
        status_col = _find_col(apps_wgs, ["STATUS", "status", "Decision"])
        date_col = _find_col(apps_wgs, ["APP_DATE", "app_date", "DecDate"])
        type_col = _find_col(apps_wgs, ["APP_TYPE", "app_type", "DevType"])
        name_col = _find_col(apps_wgs, ["NAME", "name", "Name", "DESCRIPTION"])

        # Filter to DC/industrial types
        if type_col:
            dc_mask = apps_wgs[type_col].astype(str).str.lower().str.contains(
                "data.cent|industrial|technolog", regex=True, na=False
            )
            dc_apps = apps_wgs[dc_mask]
        else:
            dc_apps = apps_wgs

        for _, row in dc_apps.iterrows():
            geom = row.geometry
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                pt = geom.representative_point()
            elif geom.geom_type == "Point":
                pt = geom
            else:
                pt = geom.centroid

            app_name = str(row[name_col]) if name_col and pd.notna(row.get(name_col)) else "Planning Application"
            if app_name == "nan":
                app_name = "Planning Application"

            pin_rows.append({
                "lng": float(pt.x),
                "lat": float(pt.y),
                "name": app_name,
                "type": "planning_application",
                "app_ref": str(row[ref_col]) if ref_col and pd.notna(row.get(ref_col)) else None,
                "app_status": str(row[status_col]).lower() if status_col and pd.notna(row.get(status_col)) else None,
                "app_date": str(row[date_col]) if date_col and pd.notna(row.get(date_col)) else None,
                "app_type": str(row[type_col]) if type_col and pd.notna(row.get(type_col)) else None,
            })

        print(f"  Planning application pins: {len(dc_apps)}")
    else:
        print("  No planning applications for pins")

    if not pin_rows:
        print("  No planning pins to insert.")
        return 0

    # Delete existing and re-insert (idempotent)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pins_planning"))

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO pins_planning (geom, name, type, app_ref, app_status, app_date, app_type)
            VALUES %s
            """,
            [
                (
                    f"SRID=4326;POINT({r['lng']} {r['lat']})",
                    r["name"],
                    r["type"],
                    r["app_ref"],
                    r["app_status"],
                    r["app_date"],
                    r["app_type"],
                )
                for r in pin_rows
            ],
            template="(ST_GeomFromEWKT(%s), %s, %s, %s, %s, %s::date, %s)",
        )

        # Assign tile_id via ST_Within
        cur.execute("""
            UPDATE pins_planning p
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
    print("=" * 60)
    print("Starting planning ingest...")
    print("=" * 60)

    # ── Check source files exist ───────────────────────────────────────────
    missing = [p for p in (MYPLAN_ZONING_FILE, PLANNING_APPLICATIONS_FILE, CSO_POPULATION_FILE)
               if not p.exists()]
    if missing:
        for p in missing:
            print(f"  ERROR: missing source file: {p}")
        print("\nRun first: python planning/download_sources.py")
        print("See ireland-data-sources.md §5, §9 for manual download instructions.")
        raise SystemExit(1)

    engine = sqlalchemy.create_engine(DB_URL)

    # ── Step 1: Load tiles ─────────────────────────────────────────────────
    print("\n[1/9] Loading tiles from database...")
    tiles = load_tiles(engine)
    print(f"  Loaded {len(tiles)} tiles")

    # ── Step 2: Load and overlay zoning ────────────────────────────────────
    print("\n[2/9] Loading MyPlan GZT zoning data...")
    zoning = gpd.read_file(str(MYPLAN_ZONING_FILE))
    print(f"  Loaded {len(zoning)} zoning polygons")

    print("\n[3/9] Computing zoning overlay...")
    zoning_df = compute_zoning_overlay(tiles, zoning)
    print(f"  Zoning tier: min={zoning_df['zoning_tier'].min():.1f}, "
          f"max={zoning_df['zoning_tier'].max():.1f}, mean={zoning_df['zoning_tier'].mean():.1f}")
    print(f"  Avg pct_industrial={zoning_df['pct_industrial'].mean():.1f}, "
          f"pct_residential={zoning_df['pct_residential'].mean():.1f}")

    # ── Step 3: Planning applications ──────────────────────────────────────
    print("\n[4/9] Loading planning applications...")
    applications = gpd.read_file(str(PLANNING_APPLICATIONS_FILE))
    print(f"  Loaded {len(applications)} planning applications")

    print("\n[5/9] Computing planning applications overlay...")
    planning_df = compute_planning_applications(tiles, applications)
    print(f"  Planning precedent: min={planning_df['planning_precedent'].min():.1f}, "
          f"max={planning_df['planning_precedent'].max():.1f}, "
          f"tiles with precedent: {(planning_df['planning_precedent'] > 0).sum()}")

    # ── Step 4: Population density ─────────────────────────────────────────
    print("\n[6/9] Loading CSO population data...")
    cso_pop = gpd.read_file(str(CSO_POPULATION_FILE))
    print(f"  Loaded {len(cso_pop)} small areas")

    print("\n[7/9] Computing population density...")
    pop_density = compute_population_density(tiles, cso_pop)
    print(f"  Population density: min={pop_density.min():.1f}, "
          f"max={pop_density.max():.1f}, mean={pop_density.mean():.1f} per km²")

    # ── Step 5: Nearest IDA site ───────────────────────────────────────────
    print("\n[8/9] Computing nearest IDA site distance...")
    ida_km = compute_nearest_ida_km(tiles, engine)
    if ida_km.isna().all():
        print("  IDA sites not yet populated — skipping distance calculation")
    else:
        print(f"  IDA distance: min={ida_km.min():.1f}, max={ida_km.max():.1f} km")

    # ── Step 6: Compose scores ─────────────────────────────────────────────
    print("\n[9/9] Composing planning scores...")
    scores_df = compose_planning_scores(zoning_df, planning_df, pop_density, ida_km)
    print(f"  Score: min={scores_df['score'].min():.2f}, max={scores_df['score'].max():.2f}, "
          f"mean={scores_df['score'].mean():.2f}")

    # ── Upsert scores ─────────────────────────────────────────────────────
    print("\nUpserting planning_scores...")
    n = upsert_planning_scores(scores_df, engine)
    print(f"  Upserted {n} rows into planning_scores")

    # ── Upsert applications ───────────────────────────────────────────────
    print("\nUpserting tile_planning_applications...")
    n_apps = upsert_planning_applications(planning_df, engine)
    print(f"  Inserted {n_apps} application rows into tile_planning_applications")

    # ── Upsert pins ───────────────────────────────────────────────────────
    print("\nUpserting planning pins...")
    n_pins = upsert_pins_planning(zoning, applications, engine)
    print(f"  Inserted {n_pins} planning pins")

    print("\n" + "=" * 60)
    print(f"Planning ingest complete: {n} tiles scored, {n_apps} applications, {n_pins} pins")
    print("Next step: run overall/compute_composite.py (after all sort pipelines complete)")
    print("=" * 60)


if __name__ == "__main__":
    main()
