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
import shapely
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
    Reproject to EPSG:2157, make_valid, simplify to 250 m tolerance.
    Skips explode — STRtree handles MultiPolygons natively.
    250 m tolerance is plenty for ~2.2 km tile sides.
    """
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs(GRID_CRS_ITM)
    gdf["geometry"] = shapely.make_valid(gdf.geometry.values)
    gdf["geometry"] = shapely.simplify(gdf.geometry.values, tolerance=250.0)
    gdf["geometry"] = shapely.make_valid(gdf.geometry.values)  # re-fix after simplify
    gdf = gdf[~gdf.geometry.is_empty].reset_index(drop=True)
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

    Uses Shapely 2.x bulk STRtree.query + vectorized intersection/area —
    all heavy geometry work happens in C, no Python per-tile loop.

    Returns list of dicts with keys:
      tile_id, designation_type, designation_name, designation_id, pct_overlap
    """
    if len(des_gdf) == 0:
        return []

    tree = STRtree(des_gdf.geometry.values)
    tile_idxs, des_idxs = tree.query(tiles.geometry.values, predicate="intersects")

    if len(tile_idxs) == 0:
        return []

    # Vectorized intersection + area (single C-level pass)
    isect = shapely.intersection(
        tiles.geometry.values[tile_idxs],
        des_gdf.geometry.values[des_idxs],
    )
    areas = shapely.area(isect)

    # Filter out empty intersections
    mask = areas > 0
    tile_idxs = tile_idxs[mask]
    des_idxs = des_idxs[mask]
    areas = areas[mask]

    # Pre-extract name/code arrays to avoid slow iloc per row
    tile_ids = tiles["tile_id"].values
    names = des_gdf[name_col].astype(str).values if name_col else None
    codes = (
        des_gdf[code_col].astype(str).values
        if code_col and code_col in des_gdf.columns
        else None
    )

    rows = []
    for i in range(len(tile_idxs)):
        pct = min(100.0, float(areas[i] / TILE_SIZE_M2) * 100.0)

        name = names[des_idxs[i]] if names is not None else f"Unknown {des_type}"
        if not name or name == "nan":
            name = f"Unknown {des_type}"
        code = codes[des_idxs[i]] if codes is not None else None
        if code == "nan":
            code = None

        rows.append({
            "tile_id": int(tile_ids[tile_idxs[i]]),
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


def _prep_flood_vector(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Fast-path prep for flood data: reproject, make_valid,
    simplify to 250 m tolerance (matches 5 km grid precision), drop empties.
    Skips explode — STRtree handles MultiPolygons natively.
    """
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    gdf = gdf.to_crs(GRID_CRS_ITM)
    gdf["geometry"] = shapely.make_valid(gdf.geometry.values)
    gdf["geometry"] = shapely.simplify(gdf.geometry.values, tolerance=250.0)
    gdf["geometry"] = shapely.make_valid(gdf.geometry.values)  # re-fix after simplify
    gdf = gdf[~gdf.geometry.is_empty].reset_index(drop=True)
    return gdf


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

    flood_cur_clean = _prep_flood_vector(flood_current)
    flood_fut_clean = _prep_flood_vector(flood_future)

    def _boolean_intersect(tiles_gdf: gpd.GeoDataFrame, zone_gdf: gpd.GeoDataFrame) -> set[int]:
        """Return set of tile_ids that intersect any polygon in zone_gdf.
        Uses Shapely 2.x bulk query — single C-level call instead of Python loop."""
        if len(zone_gdf) == 0:
            return set()
        tree = STRtree(zone_gdf.geometry.values)
        tile_idxs, _ = tree.query(tiles_gdf.geometry.values, predicate="intersects")
        return set(tiles_gdf["tile_id"].values[tile_idxs].astype(int))

    print("  Computing current flood zone intersections...")
    current_tiles = _boolean_intersect(tiles, flood_cur_clean)
    print(f"    {len(current_tiles)} tiles intersect current flood zone")

    print("  Computing future flood zone intersections...")
    future_tiles = _boolean_intersect(tiles, flood_fut_clean)
    print(f"    {len(future_tiles)} tiles intersect future flood zone")

    # Vectorized result building (no Python per-tile loop)
    tile_ids = tiles["tile_id"].values.astype(int)
    is_current = np.isin(tile_ids, np.array(list(current_tiles), dtype=int)) if current_tiles else np.zeros(len(tile_ids), dtype=bool)
    is_future = np.isin(tile_ids, np.array(list(future_tiles), dtype=int)) if future_tiles else np.zeros(len(tile_ids), dtype=bool)

    flood_score = np.full(len(tile_ids), 100.0)
    flood_score[is_future] = 40.0
    flood_score[is_current] = 0.0

    return pd.DataFrame({
        "tile_id": tile_ids,
        "intersects_current_flood": is_current,
        "intersects_future_flood": is_future,
        "flood_risk": flood_score,
    })


def compute_landslide_risk(
    tiles: gpd.GeoDataFrame,
    landslide: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    For each tile, determine worst-case landslide susceptibility class
    via boolean intersection (no area weighting — 5 km² tiles don't need it).
    Derive landslide_risk score: none=100, low=70, medium=40, high=10.
    """
    SCORE_MAP = {"none": 100.0, "low": 70.0, "medium": 40.0, "high": 10.0}
    CLASS_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}
    DEFAULT_CLASS = "none"

    tiles = tiles.to_crs(GRID_CRS_ITM) if tiles.crs.to_epsg() != 2157 else tiles

    # GSI field name varies — check for known candidates (before reprojecting)
    susc_col = _find_col(
        landslide,
        ["LSSUSCLASS", "LSSUSDESC", "SUSCEPTIBI", "Susceptibility", "SUSC_CLASS",
         "SUSC_RATIN", "CLASS", "Susc_Class", "landslide_susceptibility", "HAZARD", "Hazard"],
    )
    if susc_col is None:
        print("  Warning: could not find susceptibility column in landslide GDF")
        print(f"  Available columns: {list(landslide.columns)}")
        return pd.DataFrame({
            "tile_id": tiles["tile_id"].values,
            "landslide_susceptibility": DEFAULT_CLASS,
            "landslide_risk": 100.0,
        })

    print(f"  Using susceptibility column: '{susc_col}'")

    # Normalise to canonical classes BEFORE heavy geometry work
    def _canonical(val: str) -> str:
        v = str(val).lower().strip()
        if v in ("3", "high") or "high" in v:
            return "high"
        if v in ("2", "medium", "moderate") or "medium" in v or "moderate" in v:
            return "medium"
        if v in ("1", "low") or "low" in v:
            return "low"
        return "none"

    landslide["_susc"] = landslide[susc_col].apply(_canonical)
    landslide["_rank"] = landslide["_susc"].map(CLASS_RANK)

    # Drop "none" class early — no need to process them
    landslide = landslide[landslide["_rank"] > 0].copy()
    print(f"  After dropping 'none' class: {len(landslide)} polygons")

    # Dissolve per class, then reproject + simplify — collapses millions of rows to 3
    print("  Dissolving per susceptibility class...")
    dissolved = landslide[["_rank", "geometry"]].dissolve(by="_rank").reset_index()
    print(f"  Dissolved to {len(dissolved)} class geometries")

    ls_clean = _prep_flood_vector(dissolved)
    ls_clean["_rank"] = dissolved["_rank"].values[:len(ls_clean)]

    # Split by class and boolean-intersect each (worst wins)
    all_tile_ids = tiles["tile_id"].values.astype(int)
    worst_rank = np.zeros(len(all_tile_ids), dtype=int)  # 0 = none

    for cls, rank in [("high", 3), ("medium", 2), ("low", 1)]:
        subset = ls_clean[ls_clean["_rank"] == rank]
        if len(subset) == 0:
            continue
        tree = STRtree(subset.geometry.values)
        hit_idxs, _ = tree.query(tiles.geometry.values, predicate="intersects")
        worst_rank[hit_idxs] = np.maximum(worst_rank[hit_idxs], rank)

    rank_to_class = {0: "none", 1: "low", 2: "medium", 3: "high"}
    classes = np.array([rank_to_class[r] for r in worst_rank])

    return pd.DataFrame({
        "tile_id": all_tile_ids,
        "landslide_susceptibility": classes,
        "landslide_risk": np.array([SCORE_MAP[c] for c in classes]),
    })


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
        batch_size = 2000
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
        batch_size = 2000

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
