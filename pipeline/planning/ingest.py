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
    CSO_POPULATION_FILE, PPR_FILE, OSM_SETTLEMENTS_FILE, GRID_CRS_ITM
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


def compute_land_pricing(
    tiles: gpd.GeoDataFrame,
    ppr_path: Path,
    settlements_path: Path,
) -> pd.DataFrame:
    """
    Compute per-tile land pricing metrics from Property Price Register transactions.

    Pipeline:
      1. Load PPR CSV (individual sale transactions)
      2. Parse price and property size to estimate €/m²
      3. Geocode addresses via OSM settlement point matching
      4. Spatial join geocoded transactions to tiles
      5. Compute per-tile median price/m² and transaction count

    Returns DataFrame with tile_id, avg_price_per_sqm_eur, transaction_count.
    """
    # ── Load PPR ───────────────────────────────────────────────
    print("  Loading PPR CSV...")
    # PPR CSV uses €-prefixed prices and Irish date format
    ppr = pd.read_csv(ppr_path, encoding="latin-1")

    # Normalise column names — PPR headers sometimes have \ufeff BOM or extra spaces
    ppr.columns = ppr.columns.str.strip().str.replace("\ufeff", "")

    # Detect price column
    price_col = None
    for c in ppr.columns:
        if "price" in c.lower() and "not full" not in c.lower():
            price_col = c
            break
    if price_col is None:
        print("  WARNING: No price column found in PPR. Skipping land pricing.")
        return pd.DataFrame({"tile_id": tiles["tile_id"], "avg_price_per_sqm_eur": np.nan, "transaction_count": 0})

    # Clean price: remove € symbol, commas, convert to float
    ppr["_price"] = (
        ppr[price_col]
        .astype(str)
        .str.replace("€", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
    )
    ppr["_price"] = pd.to_numeric(ppr["_price"], errors="coerce")
    ppr = ppr.dropna(subset=["_price"])
    ppr = ppr[ppr["_price"] > 0]

    # Parse date and filter to recent years (last 3 years for relevance)
    date_col = _find_col(ppr, ["Date of Sale (dd/mm/yyyy)", "Date of Sale", "SALE_DATE", "Date"])
    if date_col:
        ppr["_date"] = pd.to_datetime(ppr[date_col], dayfirst=True, errors="coerce")
        cutoff = ppr["_date"].max() - pd.DateOffset(years=3)
        before = len(ppr)
        ppr = ppr[ppr["_date"] >= cutoff]
        print(f"  Filtered to last 3 years: {before} → {len(ppr)} transactions")

    # Estimate price per m² from Property Size Description
    size_col = _find_col(ppr, ["Property Size Description", "SIZE_DESC", "Property_Size"])
    if size_col:
        # Map size categories to midpoint m² estimates
        size_map = {
            "greater than or equal to 38 sq metres and less than 125 sq metres": 80.0,
            "greater than or equal to 125 sq metres": 180.0,
            "less than 38 sq metres": 25.0,
            "greater than 125 sq metres": 180.0,
        }
        ppr["_sqm"] = ppr[size_col].astype(str).str.strip().str.lower().map(
            {k.lower(): v for k, v in size_map.items()}
        )
        # Default to 100 m² for unmapped sizes
        ppr["_sqm"] = ppr["_sqm"].fillna(100.0)
    else:
        ppr["_sqm"] = 100.0  # default assumption

    ppr["_price_per_sqm"] = ppr["_price"] / ppr["_sqm"]

    # Remove extreme outliers (< 1st percentile, > 99th percentile)
    p1, p99 = ppr["_price_per_sqm"].quantile([0.01, 0.99])
    ppr = ppr[(ppr["_price_per_sqm"] >= p1) & (ppr["_price_per_sqm"] <= p99)]
    print(f"  After outlier removal: {len(ppr)} transactions, price/m² range: €{p1:.0f}–€{p99:.0f}")

    # ── Geocode via OSM settlement matching ────────────────────
    # Detect address column
    addr_col = _find_col(ppr, ["Address", "ADDRESS", "address"])
    county_col = _find_col(ppr, ["County", "COUNTY", "county"])

    if settlements_path.exists():
        print("  Loading OSM settlement points for geocoding...")
        settlements = gpd.read_file(str(settlements_path))
        if settlements.crs is None or settlements.crs.to_epsg() != 2157:
            settlements = settlements.to_crs(GRID_CRS_ITM)
        if settlements.geometry.name != "geometry":
            settlements = settlements.rename_geometry("geometry")

        name_col_s = _find_col(settlements, ["name", "NAME", "Name"])
        if name_col_s is None:
            name_col_s = settlements.columns[0]

        # Build lookup: lowercase settlement name → ITM point
        settlement_lookup: dict[str, object] = {}
        for _, srow in settlements.iterrows():
            sname = str(srow[name_col_s]).strip().lower()
            if sname and sname != "nan":
                settlement_lookup[sname] = srow.geometry

        print(f"  Settlement lookup: {len(settlement_lookup)} entries")

        # Pre-build substring index once (avoids O(n*m) per-row scanning)
        substring_idx = _build_substring_index(settlement_lookup)

        # Match PPR addresses to settlements
        matched_points = []
        for _, row in ppr.iterrows():
            addr = str(row[addr_col]) if addr_col else ""
            pt = _geocode_address(addr, settlement_lookup, substring_idx)
            matched_points.append(pt)

        ppr["_geom"] = matched_points
        geocoded = ppr.dropna(subset=["_geom"])
        print(f"  Geocoded {len(geocoded)} of {len(ppr)} transactions ({len(geocoded)/len(ppr)*100:.0f}%)")
    else:
        print("  WARNING: OSM settlements file not found — falling back to county centroid geocoding")
        geocoded = ppr.copy()
        geocoded["_geom"] = None
        settlement_lookup = {}

    # Fallback: county centroid for ungeocodable records
    if county_col and len(geocoded) < len(ppr):
        county_centroids = _get_county_centroids(tiles)
        ungeocodable = ppr[ppr.index.isin(geocoded.index) == False].copy()
        fallback_points = []
        for _, row in ungeocodable.iterrows():
            county = str(row[county_col]).strip()
            pt = county_centroids.get(county.lower())
            fallback_points.append(pt)
        ungeocodable["_geom"] = fallback_points
        ungeocodable = ungeocodable.dropna(subset=["_geom"])
        geocoded = pd.concat([geocoded, ungeocodable], ignore_index=True)
        print(f"  After county fallback: {len(geocoded)} geocoded transactions total")

    if len(geocoded) == 0:
        print("  WARNING: No transactions geocoded. Returning empty land pricing.")
        return pd.DataFrame({"tile_id": tiles["tile_id"], "avg_price_per_sqm_eur": np.nan, "transaction_count": 0})

    # ── Spatial join to tiles ──────────────────────────────────
    print("  Spatial joining transactions to tiles...")
    gdf = gpd.GeoDataFrame(
        geocoded,
        geometry=geocoded["_geom"].tolist(),
        crs=GRID_CRS_ITM,
    )

    joined = gpd.sjoin(gdf, tiles[["tile_id", "geometry"]], how="inner", predicate="within")

    # Aggregate per tile
    tile_stats = joined.groupby("tile_id").agg(
        avg_price_per_sqm_eur=("_price_per_sqm", "median"),
        transaction_count=("_price_per_sqm", "count"),
    ).reset_index()

    # Merge back to all tiles
    result = tiles[["tile_id"]].merge(tile_stats, on="tile_id", how="left")
    result["avg_price_per_sqm_eur"] = result["avg_price_per_sqm_eur"].astype(float)
    result["transaction_count"] = result["transaction_count"].fillna(0).astype(int)

    # For tiles with no direct transactions, interpolate from neighbours (IDW)
    has_data = result["avg_price_per_sqm_eur"].notna()
    missing = ~has_data
    if missing.any() and has_data.sum() > 10:
        print(f"  Interpolating {missing.sum()} tiles with no direct transactions...")
        result = _interpolate_missing_prices(result, tiles)

    print(f"  Land pricing: {has_data.sum()} tiles with direct data, "
          f"median €{result['avg_price_per_sqm_eur'].median():.0f}/m²")

    return result


def _build_substring_index(settlement_lookup: dict) -> dict[str, object]:
    """
    Build a reverse index mapping every 4+ char settlement name to its geometry.
    Used for fast substring matching without O(n*m) nested loops.
    Longer names are checked first (most specific match wins).
    """
    # Sort by length descending so longer/more-specific names take priority
    sorted_names = sorted(settlement_lookup.keys(), key=len, reverse=True)
    return {name: settlement_lookup[name] for name in sorted_names if len(name) >= 4}


def _geocode_address(address: str, settlement_lookup: dict,
                     substring_index: dict | None = None) -> object | None:
    """
    Match an address string to an OSM settlement point.
    Extracts town/city/village names from the address and looks up in the settlement dict.
    Uses pre-built substring_index for fast substring matching.
    Returns ITM point geometry or None.
    """
    if not address or address == "nan":
        return None

    # PPR addresses are comma-separated: "Unit 5, Main Street, Killarney, Co. Kerry"
    # Try matching each address component against settlements
    parts = [p.strip().lower() for p in address.split(",")]

    # Try parts from right to left (more specific → less specific)
    # Skip the last part if it looks like a county
    for part in reversed(parts):
        if part.startswith("co.") or part.startswith("county"):
            continue
        # Clean common prefixes
        cleaned = part.strip()
        if cleaned in settlement_lookup:
            return settlement_lookup[cleaned]

    # Try substrings via pre-built index (avoids O(n*m) inner loop)
    if substring_index is not None:
        addr_lower = address.lower()
        for sname, sgeom in substring_index.items():
            if sname in addr_lower:
                return sgeom

    return None


def _get_county_centroids(tiles: gpd.GeoDataFrame) -> dict:
    """Get approximate county centroids from tile data (in ITM). Returns {county_lower: Point}."""
    # tiles has a 'centroid' column in WGS84, but geometry is in ITM
    # Use tile centroids grouped by county
    # Need to join county info — load from DB
    try:
        from sqlalchemy import create_engine, text as sa_text
        engine = create_engine(DB_URL)
        with engine.connect() as conn:
            rows = conn.execute(sa_text(
                "SELECT DISTINCT county, ST_X(ST_Transform(ST_Centroid(ST_Collect(geom)), 2157)) AS x, "
                "ST_Y(ST_Transform(ST_Centroid(ST_Collect(geom)), 2157)) AS y "
                "FROM tiles GROUP BY county"
            ))
            from shapely.geometry import Point as ShapelyPoint
            return {r[0].lower(): ShapelyPoint(r[1], r[2]) for r in rows}
    except Exception:
        return {}


def _interpolate_missing_prices(result: pd.DataFrame, tiles: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Fill missing tile prices using inverse-distance weighted interpolation
    from nearby tiles that have data. Simple IDW with k=5 nearest neighbours.
    """
    from shapely.geometry import Point as ShapelyPoint

    # Get tile centroids in ITM
    centroids = tiles.set_index("tile_id").geometry.centroid

    has_data = result.set_index("tile_id")
    known = has_data[has_data["avg_price_per_sqm_eur"].notna()]
    unknown = has_data[has_data["avg_price_per_sqm_eur"].isna()]

    if len(known) == 0 or len(unknown) == 0:
        return result

    # Build arrays for vectorised distance computation
    known_xy = np.array([(centroids[tid].x, centroids[tid].y) for tid in known.index if tid in centroids.index])
    known_prices = known.loc[[tid for tid in known.index if tid in centroids.index], "avg_price_per_sqm_eur"].values

    for tid in unknown.index:
        if tid not in centroids.index:
            continue
        cx, cy = centroids[tid].x, centroids[tid].y
        dists = np.sqrt((known_xy[:, 0] - cx) ** 2 + (known_xy[:, 1] - cy) ** 2)
        # k nearest
        k = min(5, len(dists))
        nearest_idx = np.argpartition(dists, k)[:k]
        nearest_dists = dists[nearest_idx]
        nearest_prices = known_prices[nearest_idx]
        # IDW weights (avoid division by zero)
        weights = 1.0 / np.maximum(nearest_dists, 100.0)
        interpolated = np.average(nearest_prices, weights=weights)
        result.loc[result["tile_id"] == tid, "avg_price_per_sqm_eur"] = interpolated

    return result


def compose_planning_scores(
    zoning_df: pd.DataFrame,
    planning_df: pd.DataFrame,
    pop_density: pd.Series,
    ida_km: pd.Series,
    land_pricing_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Compose planning_scores from sub-metrics.

    Final score formula (capped 0–100):
      base = 0.6 * zoning_tier + 0.4 * land_price_score (if available)
             else zoning_tier alone
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

    # Merge land pricing if available
    if land_pricing_df is not None:
        result = result.merge(
            land_pricing_df[["tile_id", "avg_price_per_sqm_eur", "transaction_count"]],
            on="tile_id", how="left",
        )
        # Normalise price to 0–100 INVERTED (lower price = higher score)
        price_vals = result["avg_price_per_sqm_eur"]
        price_min = price_vals.min()
        price_max = price_vals.max()
        if price_max > price_min:
            result["land_price_score"] = (
                100 - (price_vals - price_min) / (price_max - price_min) * 100
            ).round(0).astype("Int16")
        else:
            result["land_price_score"] = pd.array([50] * len(result), dtype="Int16")
    else:
        result["avg_price_per_sqm_eur"] = np.nan
        result["transaction_count"] = 0
        result["land_price_score"] = pd.array([pd.NA] * len(result), dtype="Int16")

    # Compute final score
    has_land_price = result["land_price_score"].notna()
    base_score = result["zoning_tier"].copy()
    # Blend zoning_tier with land_price_score where available
    base_score = np.where(
        has_land_price,
        0.6 * result["zoning_tier"] + 0.4 * result["land_price_score"].fillna(0),
        result["zoning_tier"],
    )
    score = pd.Series(base_score, index=result.index)
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
            nearest_ida_site_km, population_density_per_km2,
            land_price_score, avg_price_per_sqm_eur, transaction_count
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
            population_density_per_km2 = EXCLUDED.population_density_per_km2,
            land_price_score           = EXCLUDED.land_price_score,
            avg_price_per_sqm_eur      = EXCLUDED.avg_price_per_sqm_eur,
            transaction_count          = EXCLUDED.transaction_count
    """

    cols = [
        "tile_id", "score", "zoning_tier", "planning_precedent",
        "pct_industrial", "pct_enterprise", "pct_mixed_use",
        "pct_agricultural", "pct_residential", "pct_other",
        "nearest_ida_site_km", "population_density_per_km2",
        "land_price_score", "avg_price_per_sqm_eur", "transaction_count",
    ]

    rows = [tuple(_to_py(row[c]) for c in cols) for _, row in df.iterrows()]

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        batch_size = 2000
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
        batch_size = 2000

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

        # Cluster parcels within 2km using STRtree (avoids O(n^2) distance loop)
        from shapely.strtree import STRtree as _STRtree
        ie_itm = ie_parcels.copy()
        cluster_dist = 2000  # 2 km minimum spacing

        # Pre-compute all centroids
        parcel_centroids = []
        for _, row in ie_itm.iterrows():
            geom = row.geometry
            if geom.geom_type in ("Polygon", "MultiPolygon"):
                parcel_centroids.append(geom.representative_point())
            else:
                parcel_centroids.append(geom.centroid)

        if len(parcel_centroids) > 0:
            tree = _STRtree(parcel_centroids)
            consumed = np.zeros(len(parcel_centroids), dtype=bool)
            selected_indices = []

            for idx in range(len(parcel_centroids)):
                if consumed[idx]:
                    continue
                neighbours = tree.query(parcel_centroids[idx].buffer(cluster_dist))
                consumed[neighbours] = True
                selected_indices.append(idx)
                if len(selected_indices) >= 500:
                    break

            # Batch convert selected centroids to WGS84
            selected_pts = [parcel_centroids[i] for i in selected_indices]
            if selected_pts:
                wgs_pts = gpd.GeoSeries(selected_pts, crs=GRID_CRS_ITM).to_crs("EPSG:4326")
                for j, idx in enumerate(selected_indices):
                    row = ie_itm.iloc[idx]
                    centroid_wgs = wgs_pts.iloc[j]
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


def write_land_price_metric_ranges(scores_df: pd.DataFrame, engine: sqlalchemy.Engine) -> None:
    """Write min/max for avg_price_per_sqm_eur to metric_ranges for Martin normalisation."""
    price_vals = scores_df["avg_price_per_sqm_eur"].dropna()
    if len(price_vals) == 0:
        print("  No land price data — skipping metric_ranges write")
        return

    min_val = float(price_vals.min())
    max_val = float(price_vals.max())

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO metric_ranges (sort, metric, min_val, max_val, unit)
                VALUES (:sort, :metric, :min_val, :max_val, :unit)
                ON CONFLICT (sort, metric) DO UPDATE SET
                    min_val    = EXCLUDED.min_val,
                    max_val    = EXCLUDED.max_val,
                    unit       = EXCLUDED.unit,
                    updated_at = now()
            """),
            {"sort": "planning", "metric": "avg_price_per_sqm_eur",
             "min_val": min_val, "max_val": max_val, "unit": "€/m²"},
        )
    print(f"  Metric range written: avg_price_per_sqm_eur [{min_val:.0f}–{max_val:.0f} €/m²]")


def main():
    """
    Planning ingest pipeline:
      1. Load tiles
      2. Overlay MyPlan GZT zoning
      3. Spatial join planning applications
      4. Compute population density from CSO
      5. Compute nearest IDA site distance
      6. Compute land pricing from PPR
      7. Compose planning scores
      8. Upsert planning_scores + tile_planning_applications + pins_planning
      9. Write metric_ranges for land pricing

    Run AFTER: grid/generate_grid.py
    Run BEFORE: overall/compute_composite.py
    """
    print("=" * 60)
    print("Starting planning ingest...")
    print("=" * 60)

    # ── Check source files exist ───────────────────────────────────────────
    required = [MYPLAN_ZONING_FILE, PLANNING_APPLICATIONS_FILE, CSO_POPULATION_FILE]
    missing = [p for p in required if not p.exists()]
    if missing:
        for p in missing:
            print(f"  ERROR: missing source file: {p}")
        print("\nRun first: python planning/download_sources.py")
        print("See ireland-data-sources.md §5, §9 for manual download instructions.")
        raise SystemExit(1)

    has_ppr = PPR_FILE.exists()
    if not has_ppr:
        print(f"  WARNING: PPR file not found at {PPR_FILE}")
        print("  Land pricing will be skipped. Download from propertypriceregister.ie")

    engine = sqlalchemy.create_engine(DB_URL)

    # ── Step 1: Load tiles ─────────────────────────────────────────────────
    print("\n[1/11] Loading tiles from database...")
    tiles = load_tiles(engine)
    print(f"  Loaded {len(tiles)} tiles")

    # ── Step 2: Load and overlay zoning ────────────────────────────────────
    print("\n[2/11] Loading MyPlan GZT zoning data...")
    zoning = gpd.read_file(str(MYPLAN_ZONING_FILE))
    print(f"  Loaded {len(zoning)} zoning polygons")

    print("\n[3/11] Computing zoning overlay...")
    zoning_df = compute_zoning_overlay(tiles, zoning)
    print(f"  Zoning tier: min={zoning_df['zoning_tier'].min():.1f}, "
          f"max={zoning_df['zoning_tier'].max():.1f}, mean={zoning_df['zoning_tier'].mean():.1f}")
    print(f"  Avg pct_industrial={zoning_df['pct_industrial'].mean():.1f}, "
          f"pct_residential={zoning_df['pct_residential'].mean():.1f}")

    # ── Step 3: Planning applications ──────────────────────────────────────
    print("\n[4/11] Loading planning applications...")
    applications = gpd.read_file(str(PLANNING_APPLICATIONS_FILE))
    print(f"  Loaded {len(applications)} planning applications")

    print("\n[5/11] Computing planning applications overlay...")
    planning_df = compute_planning_applications(tiles, applications)
    print(f"  Planning precedent: min={planning_df['planning_precedent'].min():.1f}, "
          f"max={planning_df['planning_precedent'].max():.1f}, "
          f"tiles with precedent: {(planning_df['planning_precedent'] > 0).sum()}")

    # ── Step 4: Population density ─────────────────────────────────────────
    print("\n[6/11] Loading CSO population data...")
    cso_pop = gpd.read_file(str(CSO_POPULATION_FILE))
    print(f"  Loaded {len(cso_pop)} small areas")

    print("\n[7/11] Computing population density...")
    pop_density = compute_population_density(tiles, cso_pop)
    print(f"  Population density: min={pop_density.min():.1f}, "
          f"max={pop_density.max():.1f}, mean={pop_density.mean():.1f} per km²")

    # ── Step 5: Nearest IDA site ───────────────────────────────────────────
    print("\n[8/11] Computing nearest IDA site distance...")
    ida_km = compute_nearest_ida_km(tiles, engine)
    if ida_km.isna().all():
        print("  IDA sites not yet populated — skipping distance calculation")
    else:
        print(f"  IDA distance: min={ida_km.min():.1f}, max={ida_km.max():.1f} km")

    # ── Step 6: Land pricing from PPR ──────────────────────────────────────
    land_pricing_df = None
    if has_ppr:
        print("\n[9/11] Computing land pricing from PPR...")
        land_pricing_df = compute_land_pricing(tiles, PPR_FILE, OSM_SETTLEMENTS_FILE)
    else:
        print("\n[9/11] Skipping land pricing (no PPR data)")

    # ── Step 7: Compose scores ─────────────────────────────────────────────
    print("\n[10/11] Composing planning scores...")
    scores_df = compose_planning_scores(zoning_df, planning_df, pop_density, ida_km, land_pricing_df)
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

    # ── Write metric_ranges for land pricing ──────────────────────────────
    print("\n[11/11] Writing metric ranges...")
    write_land_price_metric_ranges(scores_df, engine)

    print("\n" + "=" * 60)
    print(f"Planning ingest complete: {n} tiles scored, {n_apps} applications, {n_pins} pins")
    if has_ppr:
        lp_count = (scores_df["land_price_score"].notna()).sum()
        print(f"  Land pricing: {lp_count} tiles with price data")
    print("Next step: run overall/compute_composite.py (after all sort pipelines complete)")
    print("=" * 60)


if __name__ == "__main__":
    main()
