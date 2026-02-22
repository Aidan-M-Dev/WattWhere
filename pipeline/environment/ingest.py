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
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import sqlalchemy
from shapely.strtree import STRtree
from sqlalchemy import text
from tqdm import tqdm
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, NPWS_SAC_FILE, NPWS_SPA_FILE, NPWS_NHA_FILE,
    OPW_FLOOD_CURRENT_FILE, OPW_FLOOD_FUTURE_FILE, GSI_LANDSLIDE_FILE,
    GRID_CRS_ITM, TILE_SIZE_M,
)

TILE_SIZE_M2 = float(TILE_SIZE_M ** 2)


# ── Column-discovery helpers ─────────────────────────────────────────────────────

def _find_col(gdf: gpd.GeoDataFrame, candidates: list[str]) -> str | None:
    """Return the first column name from candidates that exists in gdf."""
    for c in candidates:
        if c in gdf.columns:
            return c
    # Case-insensitive fallback
    lower_cols = {c.lower(): c for c in gdf.columns}
    for c in candidates:
        if c.lower() in lower_cols:
            return lower_cols[c.lower()]
    return None


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


# ── Core ─────────────────────────────────────────────────────────────────────────

def load_tiles(engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """Load tiles from DB in EPSG:2157 for spatial overlay operations."""
    tiles = gpd.read_postgis(
        "SELECT tile_id, geom FROM tiles",
        engine,
        geom_col="geom",
        crs="EPSG:4326",
    )
    tiles = tiles.rename_geometry("geometry")
    return tiles.to_crs(GRID_CRS_ITM)


def _prep_vector(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Reproject to EPSG:2157, explode MultiPolygons, fix invalid geometries.
    Returns a clean GeoDataFrame ready for intersection operations.
    """
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs(GRID_CRS_ITM)
    gdf = gdf.explode(index_parts=False).reset_index(drop=True)
    gdf["geometry"] = gdf.geometry.buffer(0)  # fix invalid geometries
    gdf = gdf[gdf.geometry.is_valid & ~gdf.geometry.is_empty].reset_index(drop=True)
    return gdf


def _compute_type_overlaps(
    tiles: gpd.GeoDataFrame,
    des_gdf: gpd.GeoDataFrame,
    des_type: str,
    name_col: str | None,
    code_col: str | None,
) -> list[dict]:
    """
    Compute tile-level intersection rows for one designation type.

    Uses STRtree to pre-filter candidates before exact intersection —
    much faster than gpd.overlay() on large polygon datasets.

    Returns list of dicts with keys:
      tile_id, designation_type, designation_name, designation_id, pct_overlap
    """
    if len(des_gdf) == 0:
        return []

    des_geoms = des_gdf.geometry.values.tolist()
    tree = STRtree(des_geoms)
    rows = []

    for tile_row in tqdm(
        tiles.itertuples(), total=len(tiles), desc=f"  {des_type}", leave=False
    ):
        tile_id = int(tile_row.tile_id)
        tile_geom = tile_row.geometry

        # Bounding-box candidates via STRtree
        candidate_idxs = tree.query(tile_geom, predicate="intersects")
        if len(candidate_idxs) == 0:
            continue

        for idx in candidate_idxs:
            des_geom = des_geoms[idx]
            if not tile_geom.intersects(des_geom):
                continue
            intersection = tile_geom.intersection(des_geom)
            if intersection.is_empty:
                continue

            pct = min(100.0, float(intersection.area / TILE_SIZE_M2) * 100.0)
            des_row = des_gdf.iloc[idx]

            name = str(des_row[name_col]) if name_col else f"Unknown {des_type}"
            if not name or name == "nan":
                name = f"Unknown {des_type}"
            code = str(des_row[code_col]) if code_col and code_col in des_row.index else None
            if code == "nan":
                code = None

            rows.append({
                "tile_id": tile_id,
                "designation_type": des_type,
                "designation_name": name,
                "designation_id": code,
                "pct_overlap": round(pct, 2),
            })

    return rows


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
      - 'designations' column (list of dicts for tile_designation_overlaps table)

    Scoring (from ARCHITECTURE.md §5.3):
      - SAC or SPA overlap  → 0
      - NHA only            → max(0, 20 * (1 - pct_overlap/100))
      - No overlap          → 100

    Uses STRtree for efficient pre-filtering.
    """
    tiles = tiles.to_crs(GRID_CRS_ITM) if tiles.crs.to_epsg() != 2157 else tiles

    sac_clean = _prep_vector(sac)
    spa_clean = _prep_vector(spa)
    nha_clean = _prep_vector(nha)

    # Discover field names
    sac_name = _find_col(sac_clean, ["SITE_NAME", "SiteName", "NAME", "Name", "name", "SAC_NAME"])
    sac_code = _find_col(sac_clean, ["SITE_CODE", "SiteCode", "SITECODE", "CODE", "Code"])
    spa_name = _find_col(spa_clean, ["SITE_NAME", "SiteName", "NAME", "Name", "name", "SPA_NAME"])
    spa_code = _find_col(spa_clean, ["SITE_CODE", "SiteCode", "SITECODE", "CODE", "Code"])
    nha_name = _find_col(nha_clean, ["SITE_NAME", "SiteName", "NAME", "Name", "name", "NHA_NAME"])
    nha_code = _find_col(nha_clean, ["SITE_CODE", "SiteCode", "SITECODE", "CODE", "Code"])

    # Distinguish NHA from pNHA if a TYPE/STATUS column exists
    nha_type_col = _find_col(nha_clean, ["SITE_TYPE", "SiteType", "TYPE", "Type", "STATUS", "Status"])
    pnha_mask = pd.Series(False, index=nha_clean.index)
    if nha_type_col:
        pnha_mask = nha_clean[nha_type_col].astype(str).str.lower().str.contains("pnha|proposed")

    pnha_clean = nha_clean[pnha_mask].reset_index(drop=True) if pnha_mask.any() else nha_clean.iloc[0:0]
    nha_only_clean = nha_clean[~pnha_mask].reset_index(drop=True)

    # Compute overlaps for each designation type
    print("  Computing SAC overlaps...")
    sac_rows = _compute_type_overlaps(tiles, sac_clean, "SAC", sac_name, sac_code)
    print(f"    {len(sac_rows)} SAC overlap records")

    print("  Computing SPA overlaps...")
    spa_rows = _compute_type_overlaps(tiles, spa_clean, "SPA", spa_name, spa_code)
    print(f"    {len(spa_rows)} SPA overlap records")

    print("  Computing NHA overlaps...")
    nha_rows = _compute_type_overlaps(tiles, nha_only_clean, "NHA", nha_name, nha_code)
    print(f"    {len(nha_rows)} NHA overlap records")

    print("  Computing pNHA overlaps...")
    pnha_rows = _compute_type_overlaps(tiles, pnha_clean, "pNHA", nha_name, nha_code)
    print(f"    {len(pnha_rows)} pNHA overlap records")

    all_rows = sac_rows + spa_rows + nha_rows + pnha_rows

    # Group by tile_id
    sac_tiles = {r["tile_id"] for r in sac_rows}
    spa_tiles = {r["tile_id"] for r in spa_rows}
    nha_tiles = {r["tile_id"] for r in nha_rows}
    pnha_tiles = {r["tile_id"] for r in pnha_rows}

    # Cumulative pct per tile per type (for scoring)
    nha_pct_by_tile: dict[int, float] = defaultdict(float)
    for r in nha_rows + pnha_rows:
        nha_pct_by_tile[r["tile_id"]] = min(
            100.0, nha_pct_by_tile[r["tile_id"]] + r["pct_overlap"]
        )

    # Group designation rows by tile_id
    designations_by_tile: dict[int, list] = defaultdict(list)
    for r in all_rows:
        designations_by_tile[r["tile_id"]].append(r)

    # Build result DataFrame — one row per tile
    records = []
    for tile_row in tiles.itertuples():
        tile_id = int(tile_row.tile_id)
        is_sac = tile_id in sac_tiles
        is_spa = tile_id in spa_tiles
        is_nha = tile_id in nha_tiles
        is_pnha = tile_id in pnha_tiles

        sac_or_spa = is_sac or is_spa
        nha_overlap = is_nha or is_pnha

        if sac_or_spa:
            des_score = 0.0
        elif nha_overlap:
            nha_pct = nha_pct_by_tile.get(tile_id, 100.0)
            des_score = max(0.0, 20.0 * (1.0 - nha_pct / 100.0))
        else:
            des_score = 100.0

        records.append({
            "tile_id": tile_id,
            "intersects_sac": is_sac,
            "intersects_spa": is_spa,
            "intersects_nha": is_nha,
            "intersects_pnha": is_pnha,
            "designation_overlap": round(des_score, 2),
            "designations": designations_by_tile[tile_id],
        })

    return pd.DataFrame(records)


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

    Uses STRtree for efficient boolean intersection checks.
    """
    tiles = tiles.to_crs(GRID_CRS_ITM) if tiles.crs.to_epsg() != 2157 else tiles

    flood_cur_clean = _prep_vector(flood_current)
    flood_fut_clean = _prep_vector(flood_future)

    def _boolean_intersect(tiles_gdf: gpd.GeoDataFrame, zone_gdf: gpd.GeoDataFrame, label: str) -> set[int]:
        """Return set of tile_ids that intersect any polygon in zone_gdf."""
        if len(zone_gdf) == 0:
            return set()
        zone_geoms = zone_gdf.geometry.values.tolist()
        tree = STRtree(zone_geoms)
        hit_tiles = set()
        for tile_row in tqdm(
            tiles_gdf.itertuples(), total=len(tiles_gdf), desc=f"  Flood {label}", leave=False
        ):
            tile_geom = tile_row.geometry
            candidates = tree.query(tile_geom, predicate="intersects")
            if len(candidates) > 0:
                # At least one candidate → confirm exact intersection
                for idx in candidates:
                    if tile_geom.intersects(zone_geoms[idx]):
                        hit_tiles.add(int(tile_row.tile_id))
                        break
        return hit_tiles

    print("  Computing current flood zone intersections...")
    current_tiles = _boolean_intersect(tiles, flood_cur_clean, "current")
    print(f"    {len(current_tiles)} tiles intersect current flood zone")

    print("  Computing future flood zone intersections...")
    future_tiles = _boolean_intersect(tiles, flood_fut_clean, "future")
    print(f"    {len(future_tiles)} tiles intersect future flood zone")

    records = []
    for tile_row in tiles.itertuples():
        tile_id = int(tile_row.tile_id)
        is_current = tile_id in current_tiles
        is_future = tile_id in future_tiles

        if is_current:
            flood_score = 0.0
        elif is_future:
            flood_score = 40.0
        else:
            flood_score = 100.0

        records.append({
            "tile_id": tile_id,
            "intersects_current_flood": is_current,
            "intersects_future_flood": is_future,
            "flood_risk": flood_score,
        })

    return pd.DataFrame(records)


def compute_landslide_risk(
    tiles: gpd.GeoDataFrame,
    landslide: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    For each tile, extract landslide susceptibility (none/low/medium/high)
    via spatial majority join.
    Derive landslide_risk score: none=100, low=70, medium=40, high=10.

    GSI field name varies — checked dynamically and normalised to lowercase.
    """
    SCORE_MAP = {"none": 100.0, "low": 70.0, "medium": 40.0, "high": 10.0}
    DEFAULT_CLASS = "none"

    tiles = tiles.to_crs(GRID_CRS_ITM) if tiles.crs.to_epsg() != 2157 else tiles
    ls_clean = _prep_vector(landslide)

    # GSI field name varies — check for known candidates
    susc_col = _find_col(
        ls_clean,
        # GSI field confirmed as LSSUSCLASS (IE_GSI_Landslide_Susceptibility_Classification_50K)
        ["LSSUSCLASS", "LSSUSDESC", "SUSCEPTIBI", "Susceptibility", "SUSC_CLASS",
         "SUSC_RATIN", "CLASS", "Susc_Class", "landslide_susceptibility", "HAZARD", "Hazard"],
    )
    if susc_col is None:
        print("  Warning: could not find susceptibility column in landslide GDF")
        print(f"  Available columns: {list(ls_clean.columns)}")
        # Fall back to 'none' for all tiles
        return pd.DataFrame({
            "tile_id": tiles["tile_id"].values,
            "landslide_susceptibility": DEFAULT_CLASS,
            "landslide_risk": 100.0,
        })

    print(f"  Using susceptibility column: '{susc_col}'")

    # Normalise susceptibility values to lowercase
    ls_clean["_susc_norm"] = (
        ls_clean[susc_col]
        .astype(str)
        .str.lower()
        .str.strip()
        .fillna(DEFAULT_CLASS)
    )

    # Map to canonical classes
    def _canonical(val: str) -> str:
        # GSI LSSUSCLASS uses numeric codes: 1=Low, 2=Medium, 3=High
        if val in ("3", "high"):
            return "high"
        if val in ("2", "medium", "moderate"):
            return "medium"
        if val in ("1", "low"):
            return "low"
        if "high" in val:
            return "high"
        if "medium" in val or "moderate" in val:
            return "medium"
        if "low" in val:
            return "low"
        return "none"

    ls_clean["_susc_norm"] = ls_clean["_susc_norm"].apply(_canonical)

    # STRtree majority join: for each tile, find the dominant susceptibility class
    ls_geoms = ls_clean.geometry.values.tolist()
    tree = STRtree(ls_geoms)

    records = []
    for tile_row in tqdm(
        tiles.itertuples(), total=len(tiles), desc="  Landslide", leave=False
    ):
        tile_id = int(tile_row.tile_id)
        tile_geom = tile_row.geometry

        candidates = tree.query(tile_geom, predicate="intersects")
        if len(candidates) == 0:
            records.append({
                "tile_id": tile_id,
                "landslide_susceptibility": DEFAULT_CLASS,
                "landslide_risk": SCORE_MAP[DEFAULT_CLASS],
            })
            continue

        # Area-weighted majority vote
        class_area: dict[str, float] = defaultdict(float)
        for idx in candidates:
            ls_geom = ls_geoms[idx]
            if not tile_geom.intersects(ls_geom):
                continue
            intersection = tile_geom.intersection(ls_geom)
            if intersection.is_empty:
                continue
            cls = ls_clean.iloc[idx]["_susc_norm"]
            class_area[cls] += intersection.area

        if not class_area:
            dominant = DEFAULT_CLASS
        else:
            dominant = max(class_area, key=class_area.__getitem__)

        records.append({
            "tile_id": tile_id,
            "landslide_susceptibility": dominant,
            "landslide_risk": SCORE_MAP.get(dominant, SCORE_MAP[DEFAULT_CLASS]),
        })

    return pd.DataFrame(records)


def compose_environment_scores(
    designation_df: pd.DataFrame,
    flood_df: pd.DataFrame,
    landslide_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compose final environment_scores from sub-metric DataFrames.

    Scoring logic (from ARCHITECTURE.md §5.3), applied top-down (worst wins):
      1. Start at 100
      2. Landslide medium → −30 penalty
      3. Future flood     → cap at 40
      4. NHA/pNHA overlap → cap at 20
      5. SAC/SPA overlap OR current flood → hard exclusion, score = 0

    Returns DataFrame matching environment_scores table schema.
    """
    scores = designation_df[
        ["tile_id", "intersects_sac", "intersects_spa",
         "intersects_nha", "intersects_pnha",
         "designation_overlap"]
    ].copy()

    scores = scores.merge(
        flood_df[["tile_id", "intersects_current_flood", "intersects_future_flood", "flood_risk"]],
        on="tile_id", how="left",
    )
    scores = scores.merge(
        landslide_df[["tile_id", "landslide_susceptibility", "landslide_risk"]],
        on="tile_id", how="left",
    )

    # Fill missing flood/landslide data (tiles not covered by those layers)
    scores["intersects_current_flood"] = scores["intersects_current_flood"].fillna(False)
    scores["intersects_future_flood"] = scores["intersects_future_flood"].fillna(False)
    scores["flood_risk"] = scores["flood_risk"].fillna(100.0)
    scores["landslide_susceptibility"] = scores["landslide_susceptibility"].fillna("none")
    scores["landslide_risk"] = scores["landslide_risk"].fillna(100.0)

    scores["has_hard_exclusion"] = (
        scores["intersects_sac"]
        | scores["intersects_spa"]
        | scores["intersects_current_flood"]
    )

    # Build composite score applying penalties in priority order
    scores["score"] = 100.0

    # Landslide medium → −30
    scores.loc[scores["landslide_susceptibility"] == "medium", "score"] -= 30.0

    # Future flood zone only → cap at 40
    scores.loc[scores["intersects_future_flood"], "score"] = (
        scores.loc[scores["intersects_future_flood"], "score"].clip(upper=40.0)
    )

    # NHA/pNHA → cap at 20
    nha_mask = scores["intersects_nha"] | scores["intersects_pnha"]
    scores.loc[nha_mask, "score"] = scores.loc[nha_mask, "score"].clip(upper=20.0)

    # Hard exclusion → score = 0
    scores.loc[scores["has_hard_exclusion"], "score"] = 0.0

    scores["score"] = scores["score"].clip(0.0, 100.0).round(2)

    # Build exclusion_reason (first match wins)
    def _exclusion_reason(row: pd.Series) -> str | None:
        if not row["has_hard_exclusion"]:
            return None
        if row["intersects_sac"]:
            return "SAC overlap"
        if row["intersects_spa"]:
            return "SPA overlap"
        if row["intersects_current_flood"]:
            return "Current flood zone"
        return "Hard exclusion"

    scores["exclusion_reason"] = scores.apply(_exclusion_reason, axis=1)

    return scores


def upsert_environment_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """Upsert environment_scores. ON CONFLICT (tile_id) DO UPDATE. Returns row count."""
    sql = """
        INSERT INTO environment_scores (
            tile_id, score, designation_overlap, flood_risk, landslide_risk,
            has_hard_exclusion, exclusion_reason,
            intersects_sac, intersects_spa, intersects_nha, intersects_pnha,
            intersects_current_flood, intersects_future_flood,
            landslide_susceptibility
        ) VALUES %s
        ON CONFLICT (tile_id) DO UPDATE SET
            score                   = EXCLUDED.score,
            designation_overlap     = EXCLUDED.designation_overlap,
            flood_risk              = EXCLUDED.flood_risk,
            landslide_risk          = EXCLUDED.landslide_risk,
            has_hard_exclusion      = EXCLUDED.has_hard_exclusion,
            exclusion_reason        = EXCLUDED.exclusion_reason,
            intersects_sac          = EXCLUDED.intersects_sac,
            intersects_spa          = EXCLUDED.intersects_spa,
            intersects_nha          = EXCLUDED.intersects_nha,
            intersects_pnha         = EXCLUDED.intersects_pnha,
            intersects_current_flood = EXCLUDED.intersects_current_flood,
            intersects_future_flood  = EXCLUDED.intersects_future_flood,
            landslide_susceptibility = EXCLUDED.landslide_susceptibility
    """

    cols = [
        "tile_id", "score", "designation_overlap", "flood_risk", "landslide_risk",
        "has_hard_exclusion", "exclusion_reason",
        "intersects_sac", "intersects_spa", "intersects_nha", "intersects_pnha",
        "intersects_current_flood", "intersects_future_flood",
        "landslide_susceptibility",
    ]

    rows = [tuple(_to_py(row[c]) for c in cols) for _, row in df.iterrows()]

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        batch_size = 500
        for i in tqdm(range(0, len(rows), batch_size), desc="Upserting environment_scores"):
            execute_values(cur, sql, rows[i: i + batch_size])
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
        pg_conn.close()

    return len(rows)


def upsert_designation_overlaps(designation_df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Replace tile_designation_overlaps for each affected tile.
    Delete-then-insert (not upsert — overlap list is replaced wholesale per tile).
    Processed in 500-tile batches to avoid huge ANY(...) lists.
    Returns total rows inserted.
    """
    # Explode the 'designations' list column into individual rows
    overlap_rows: list[dict] = []
    for _, row in designation_df.iterrows():
        for d in (row.get("designations") or []):
            overlap_rows.append(d)

    if not overlap_rows:
        print("  No designation overlaps to insert.")
        return 0

    # Collect affected tile_ids and pre-group rows for efficient batch access
    affected_tile_ids = list({r["tile_id"] for r in overlap_rows})
    rows_by_tile: dict[int, list] = defaultdict(list)
    for r in overlap_rows:
        rows_by_tile[r["tile_id"]].append(r)

    pg_conn = engine.raw_connection()
    total_inserted = 0
    try:
        cur = pg_conn.cursor()
        batch_size = 500

        for i in tqdm(
            range(0, len(affected_tile_ids), batch_size),
            desc="Upserting designation overlaps",
        ):
            batch_ids = affected_tile_ids[i: i + batch_size]

            # Step 1: Delete existing overlaps for this batch of tiles
            cur.execute(
                "DELETE FROM tile_designation_overlaps WHERE tile_id = ANY(%s)",
                (batch_ids,),
            )

            # Step 2: Insert fresh overlaps for this batch (pre-grouped for speed)
            batch_rows = [r for tid in batch_ids for r in rows_by_tile.get(tid, [])]
            if batch_rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO tile_designation_overlaps
                        (tile_id, designation_type, designation_name, designation_id, pct_overlap)
                    VALUES %s
                    """,
                    [
                        (
                            r["tile_id"],
                            r["designation_type"],
                            r["designation_name"],
                            r.get("designation_id"),
                            r["pct_overlap"],
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
      - Flood zone indicator points (type='flood_zone') — one per zone polygon

    Assigns tile_id via ST_Within. tile_id may be NULL for coastal designations.
    Returns count of pins inserted.
    """
    pin_rows: list[dict] = []

    def _add_pins(gdf: gpd.GeoDataFrame, pin_type: str, name_candidates: list[str],
                  code_candidates: list[str]) -> None:
        if len(gdf) == 0:
            return
        gdf_wgs = gdf.to_crs("EPSG:4326")
        name_col = _find_col(gdf_wgs, name_candidates)
        code_col = _find_col(gdf_wgs, code_candidates)
        area_col = _find_col(gdf_wgs, ["AREA_HA", "Area_ha", "AREA", "area_ha"])

        for _, row in gdf_wgs.iterrows():
            geom = row.geometry
            # Dissolve to centroid — use representative_point for complex shapes
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                centroid = geom.representative_point()
            else:
                centroid = geom.centroid

            name = str(row[name_col]) if name_col else f"{pin_type.upper()} Site"
            if not name or name == "nan":
                name = f"{pin_type.upper()} Site"

            code = str(row[code_col]) if code_col else None
            if code == "nan":
                code = None

            area = _to_py(row[area_col]) if area_col else None

            pin_rows.append({
                "lng": float(centroid.x),
                "lat": float(centroid.y),
                "name": name,
                "type": pin_type,
                "designation_id": code,
                "area_ha": float(area) if area is not None else None,
            })

    _add_pins(
        _prep_vector(sac), "sac",
        ["SITE_NAME", "SiteName", "NAME", "Name"],
        ["SITE_CODE", "SiteCode", "CODE"],
    )
    _add_pins(
        _prep_vector(spa), "spa",
        ["SITE_NAME", "SiteName", "NAME", "Name"],
        ["SITE_CODE", "SiteCode", "CODE"],
    )
    _add_pins(
        _prep_vector(nha), "nha",
        ["SITE_NAME", "SiteName", "NAME", "Name"],
        ["SITE_CODE", "SiteCode", "CODE"],
    )
    _add_pins(
        _prep_vector(flood_current), "flood_zone",
        ["ZONE_NAME", "NAME", "Name", "ZONE", "Zone"],
        [],
    )

    if not pin_rows:
        print("  No environment pins to insert.")
        return 0

    # Delete existing environment pins and re-insert (idempotent)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pins_environment"))

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO pins_environment (geom, name, type, designation_id, area_ha)
            VALUES %s
            """,
            [
                (
                    f"SRID=4326;POINT({r['lng']} {r['lat']})",
                    r["name"],
                    r["type"],
                    r["designation_id"],
                    r["area_ha"],
                )
                for r in pin_rows
            ],
            template="(ST_GeomFromEWKT(%s), %s, %s, %s, %s)",
        )

        # Assign tile_id via ST_Within spatial join
        cur.execute("""
            UPDATE pins_environment p
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


def main() -> None:
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

    OPW flood data: CC BY-NC-ND licence — see SidebarEnvironment.vue for UI notice.
    """
    print("=" * 60)
    print("Starting environment ingest...")
    print("=" * 60)

    # ── Check source files exist ───────────────────────────────────────────────
    required = [
        NPWS_SAC_FILE, NPWS_SPA_FILE, NPWS_NHA_FILE,
        OPW_FLOOD_CURRENT_FILE, OPW_FLOOD_FUTURE_FILE, GSI_LANDSLIDE_FILE,
    ]
    missing = [p for p in required if not p.exists()]
    if missing:
        for p in missing:
            print(f"  ERROR: missing source file: {p}")
        print("\nRun: python environment/download_sources.py")
        raise SystemExit(1)

    engine = sqlalchemy.create_engine(DB_URL)

    # ── Step 1: Load tiles ─────────────────────────────────────────────────────
    print("\n[1/7] Loading tiles from database...")
    tiles = load_tiles(engine)
    print(f"  Loaded {len(tiles)} tiles")

    # ── Step 2: Load NPWS designations ────────────────────────────────────────
    print("\n[2/7] Loading NPWS designated sites...")
    sac = gpd.read_file(str(NPWS_SAC_FILE))
    spa = gpd.read_file(str(NPWS_SPA_FILE))
    nha = gpd.read_file(str(NPWS_NHA_FILE))
    print(f"  SAC: {len(sac)} polygons | SPA: {len(spa)} polygons | NHA: {len(nha)} polygons")

    # ── Step 3: Load OPW flood extents ────────────────────────────────────────
    print("\n[3/7] Loading OPW NIFM flood extents...")
    flood_current = gpd.read_file(str(OPW_FLOOD_CURRENT_FILE))
    flood_future = gpd.read_file(str(OPW_FLOOD_FUTURE_FILE))
    print(f"  Current: {len(flood_current)} polygons | Future: {len(flood_future)} polygons")

    # ── Step 4: Load GSI landslide susceptibility ─────────────────────────────
    print("\n[4/7] Loading GSI landslide susceptibility...")
    landslide = gpd.read_file(str(GSI_LANDSLIDE_FILE))
    print(f"  GSI: {len(landslide)} polygons | columns: {list(landslide.columns)}")

    # ── Step 5: Compute sub-metrics ───────────────────────────────────────────
    print("\n[5/7] Computing designation overlaps...")
    designation_df = compute_designation_overlaps(tiles, sac, spa, nha)
    excl_count = (
        designation_df["intersects_sac"].sum() + designation_df["intersects_spa"].sum()
    )
    print(f"  SAC tiles: {designation_df['intersects_sac'].sum()} | "
          f"SPA tiles: {designation_df['intersects_spa'].sum()} | "
          f"NHA tiles: {designation_df['intersects_nha'].sum()} | "
          f"pNHA tiles: {designation_df['intersects_pnha'].sum()}")

    print("\n  Computing flood risk...")
    flood_df = compute_flood_risk(tiles, flood_current, flood_future)
    print(f"  Current flood tiles: {flood_df['intersects_current_flood'].sum()} | "
          f"Future flood tiles: {flood_df['intersects_future_flood'].sum()}")

    print("\n  Computing landslide risk...")
    landslide_df = compute_landslide_risk(tiles, landslide)
    print(f"  Landslide susceptibility counts:")
    for cls, cnt in landslide_df["landslide_susceptibility"].value_counts().items():
        print(f"    {cls}: {cnt}")

    # ── Step 6: Compose environment scores ────────────────────────────────────
    print("\n[6/7] Composing environment scores...")
    scores_df = compose_environment_scores(designation_df, flood_df, landslide_df)
    hard_excl = scores_df["has_hard_exclusion"].sum()
    print(f"  Score: min={scores_df['score'].min():.2f}, max={scores_df['score'].max():.2f}, "
          f"mean={scores_df['score'].mean():.2f}")
    print(f"  Hard exclusions: {hard_excl} tiles ({100*hard_excl/len(scores_df):.1f}%)")

    # Sanity check: all hard exclusions must have score = 0
    bad = (scores_df["has_hard_exclusion"] & (scores_df["score"] != 0)).sum()
    if bad > 0:
        print(f"  WARNING: {bad} hard exclusion tiles have score != 0 — check logic!")

    # ── Step 7: Upsert tables ─────────────────────────────────────────────────
    print("\n[7/7] Upserting database tables...")

    n_scores = upsert_environment_scores(scores_df, engine)
    print(f"  Upserted {n_scores} rows into environment_scores")

    n_overlaps = upsert_designation_overlaps(designation_df, engine)
    print(f"  Inserted {n_overlaps} rows into tile_designation_overlaps")

    n_pins = upsert_pins_environment(sac, spa, nha, flood_current, engine)
    print(f"  Inserted {n_pins} rows into pins_environment")

    print("\n" + "=" * 60)
    print(f"Environment ingest complete:")
    print(f"  {n_scores} tiles scored | {hard_excl} hard exclusions")
    print(f"  {n_overlaps} designation overlap records")
    print(f"  {n_pins} environment pins")
    print("\nNext step: run overall/compute_composite.py")
    print("  docker compose restart martin")
    print("=" * 60)


if __name__ == "__main__":
    main()
