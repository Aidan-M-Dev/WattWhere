"""
FILE: pipeline/energy/ingest.py
Role: Ingest energy data sources and compute energy_scores for all tiles.
Agent boundary: Pipeline — Energy sort (§5.2, §8, §10)
Dependencies:
  - tiles table must be populated (run grid/generate_grid.py first)
  - config.py: WIND_ATLAS_FILE, SOLAR_ATLAS_FILE, OSM_POWER_FILE, SEAI_WIND_FARMS_FILE
  - Raw source files present in DATA_ROOT/energy/
  - See ireland-data-sources.md §2–§3 for source formats and download URLs
Output:
  - Populates energy_scores table (upsert — idempotent)
  - Populates pins_energy table (upsert)
  - Writes metric_ranges rows for wind_speed_100m, solar_ghi, renewable_pct
How to test:
  python energy/ingest.py
  psql $DATABASE_URL -c "SELECT COUNT(*), AVG(score), AVG(renewable_pct) FROM energy_scores;"

Data sources used (details in ireland-data-sources.md):
  Wind speed: Global Wind Atlas GeoTIFF — 100m hub height, ~1 km resolution
  Solar GHI:  Global Solar Atlas GeoTIFF — kWh/m²/yr, ~1 km resolution
  Grid infra: OSM power=substation and power=line (Geofabrik Ireland extract)
  Renewable:  SEAI connected wind farms CSV + hardcoded thermal/hydro/solar generators

ARCHITECTURE RULES:
  - Store raw values (m/s, kWh/m²/yr) in energy_scores columns.
  - DO NOT pre-normalise — normalisation is done in tile_heatmap SQL function.
  - grid_proximity (0–100) IS pre-normalised (inverse distance score).
  - renewable_score = renewable_pct (linear 0–100).
  - Write metric_ranges for wind_speed_100m, solar_ghi, renewable_pct.
  - Set grid_low_confidence = true where nearest_substation_km > 20.
  - All spatial operations in EPSG:2157 for metric accuracy.
"""

import sys
from pathlib import Path
import numpy as np
import geopandas as gpd
import rasterio
import pandas as pd
import sqlalchemy
from sqlalchemy import text
from shapely.geometry import Point
from shapely.strtree import STRtree
import shapely
from tqdm import tqdm
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, WIND_ATLAS_FILE, SOLAR_ATLAS_FILE, OSM_POWER_FILE,
    SEAI_WIND_FARMS_FILE, OSM_GENERATORS_FILE, GRID_CRS_ITM, GRID_CRS_WGS84
)

from rasterstats import zonal_stats as rasterstats_zonal_stats


def _load_osm_power(path: Path) -> gpd.GeoDataFrame:
    """
    Load OSM power GeoPackage, handling both single-layer and multi-layer files.
    OSM Geofabrik exports use layers: 'lines', 'points', 'multipolygons'.
    """
    try:
        import fiona
        available_layers = fiona.listlayers(str(path))
    except Exception:
        available_layers = []

    power_values = {"substation", "line", "cable", "tower"}
    gdfs = []

    if "lines" in available_layers:
        gdf = gpd.read_file(str(path), layer="lines")
        if "power" in gdf.columns:
            gdf = gdf[gdf["power"].isin(power_values)]
            gdfs.append(gdf)

    if "points" in available_layers:
        gdf = gpd.read_file(str(path), layer="points")
        if "power" in gdf.columns:
            gdf = gdf[gdf["power"].isin(power_values)]
            gdfs.append(gdf)

    if "multipolygons" in available_layers:
        gdf = gpd.read_file(str(path), layer="multipolygons")
        if "power" in gdf.columns:
            gdf = gdf[gdf["power"].isin(power_values)]
            gdfs.append(gdf)

    if not gdfs:
        # Single-layer GeoPackage (filtered extract)
        gdf = gpd.read_file(str(path))
        if "power" in gdf.columns:
            gdf = gdf[gdf["power"].isin(power_values)]
        gdfs.append(gdf)

    combined = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)
    return combined.to_crs(GRID_CRS_ITM)


def load_tiles(engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """
    Load tiles from DB into a GeoDataFrame in EPSG:2157 for spatial operations.

    Returns:
        GeoDataFrame with tile_id, geom (EPSG:2157), centroid (EPSG:4326).
    """
    tiles = gpd.read_postgis(
        "SELECT tile_id, geom, centroid FROM tiles",
        engine,
        geom_col="geom",
        crs="EPSG:4326",
    )
    return tiles.to_crs(GRID_CRS_ITM)


def extract_raster_zonal_stats(
    tiles: gpd.GeoDataFrame,
    raster_path: Path,
    stat: str = "mean",
) -> pd.Series:
    """
    Compute zonal statistics (mean or max) for each tile from a GeoTIFF raster.

    Args:
        tiles: GeoDataFrame of tile polygons in EPSG:2157
        raster_path: Path to GeoTIFF
        stat: 'mean' or 'max'

    Returns:
        Series indexed by tile_id with extracted values (NaN if no data).
    """
    with rasterio.open(str(raster_path)) as src:
        raster_crs = src.crs
        nodata_val = src.nodata

    # Reproject tile geometries to match raster CRS before extraction
    raster_epsg = raster_crs.to_epsg()
    if raster_epsg:
        tiles_reproj = tiles.to_crs(f"EPSG:{raster_epsg}")
    else:
        tiles_reproj = tiles.to_crs(raster_crs.to_wkt())

    results = rasterstats_zonal_stats(
        tiles_reproj.geometry,
        str(raster_path),
        stats=[stat],
        nodata=nodata_val,
    )

    values = [r[stat] if r[stat] is not None else np.nan for r in results]
    return pd.Series(values, index=tiles["tile_id"], name=stat)


def compute_grid_proximity(
    tiles: gpd.GeoDataFrame,
    osm_power: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Compute grid proximity metrics from OSM power infrastructure.
    Returns pre-normalised 0–100 score + raw distance columns.

    Args:
        tiles: Tile GeoDataFrame in EPSG:2157
        osm_power: OSM power features in EPSG:2157

    Returns:
        DataFrame with columns:
          tile_id, grid_proximity (0–100), nearest_transmission_line_km,
          nearest_substation_km, nearest_substation_name, nearest_substation_voltage,
          grid_low_confidence (bool)
    """
    MAX_DIST_KM = 100.0

    substations = osm_power[osm_power["power"] == "substation"].copy()
    lines = osm_power[osm_power["power"].isin(["line", "cable"])].copy()

    # Normalise substation geometries: polygons → centroids (Point only for distance calcs)
    if len(substations) > 0:
        poly_mask = substations.geometry.geom_type != "Point"
        if poly_mask.any():
            substations.loc[poly_mask, "geometry"] = substations.loc[poly_mask].geometry.centroid

    # Build tile centroid GeoDataFrame for spatial joins (distances from centroid)
    centroids_gdf = gpd.GeoDataFrame(
        {"tile_id": tiles["tile_id"].values},
        geometry=tiles.geometry.centroid,
        crs=GRID_CRS_ITM,
    )

    result = pd.DataFrame({"tile_id": tiles["tile_id"].values})

    # ── Nearest substation ─────────────────────────────────────────────────────
    if len(substations) > 0:
        keep_cols = ["geometry"]
        for col in ("name", "voltage"):
            if col in substations.columns:
                keep_cols.append(col)
        subs_clean = substations[keep_cols].reset_index(drop=True)

        joined_sub = gpd.sjoin_nearest(
            centroids_gdf,
            subs_clean,
            how="left",
            distance_col="sub_dist_m",
        )
        # Drop duplicates from ties — keep the nearest (first row per tile)
        joined_sub = joined_sub.drop_duplicates(subset="tile_id", keep="first")

        sub_merged = result.merge(
            joined_sub[["tile_id", "sub_dist_m"]
                       + [c for c in ("name", "voltage") if c in joined_sub.columns]],
            on="tile_id",
            how="left",
        )
        result["nearest_substation_km"] = sub_merged["sub_dist_m"] / 1000
        result["nearest_substation_name"] = sub_merged.get("name")
        result["nearest_substation_voltage"] = sub_merged.get("voltage")
    else:
        result["nearest_substation_km"] = np.nan
        result["nearest_substation_name"] = None
        result["nearest_substation_voltage"] = None

    # ── Nearest transmission line ──────────────────────────────────────────────
    if len(lines) > 0:
        lines_clean = lines[["geometry"]].reset_index(drop=True)
        joined_line = gpd.sjoin_nearest(
            centroids_gdf,
            lines_clean,
            how="left",
            distance_col="line_dist_m",
        )
        joined_line = joined_line.drop_duplicates(subset="tile_id", keep="first")
        line_merged = result.merge(
            joined_line[["tile_id", "line_dist_m"]],
            on="tile_id",
            how="left",
        )
        result["nearest_transmission_line_km"] = line_merged["line_dist_m"] / 1000
    else:
        result["nearest_transmission_line_km"] = np.nan

    # ── Log-inverse proximity score (from substation distance) ─────────────────
    dist_km = result["nearest_substation_km"].fillna(MAX_DIST_KM).clip(0, MAX_DIST_KM)
    result["grid_proximity"] = np.clip(
        100 * (1 - np.log1p(dist_km) / np.log1p(MAX_DIST_KM)), 0, 100
    ).round(2)

    # Low confidence where nearest substation is > 20 km away
    result["grid_low_confidence"] = (
        result["nearest_substation_km"].isna() | result["nearest_substation_km"].gt(20)
    )

    return result


# ── Renewable energy constants and helpers ────────────────────────────────────

SEARCH_RADIUS_M = 50_000  # 50 km radius for distance-weighted generator aggregation
RENEWABLE_FUELS = {"wind", "solar", "hydro", "biomass", "biogas"}

# Known major non-wind generators in Ireland
# Sources: EirGrid Generation Capacity Statement, ESB, public records
# Format: (name, fuel, capacity_mw, easting_itm, northing_itm)
KNOWN_GENERATORS = [
    ("Moneypoint", "coal", 915, 498600, 654800),
    ("Poolbeg (Dublin Bay Power)", "gas", 480, 720800, 733900),
    ("Aghada", "gas", 431, 586200, 575200),
    ("Huntstown 1 & 2", "gas", 740, 710600, 742100),
    ("Dublin Bay Power (Ringsend)", "gas", 415, 719400, 733200),
    ("Tynagh", "gas", 400, 575800, 707200),
    ("Whitegate", "gas", 445, 584200, 564300),
    ("Great Island", "gas", 431, 672500, 611900),
    ("Edenderry", "peat", 128, 664000, 725800),
    ("Lough Ree Power", "peat", 100, 603300, 755100),
    ("West Offaly Power", "peat", 137, 618300, 718200),
    ("Tarbert", "oil", 588, 496600, 648400),
    ("Rhode", "gas", 104, 652700, 723200),
    ("Ardnacrusha", "hydro", 86, 557500, 661600),
    ("Turlough Hill", "hydro", 292, 707200, 699000),
    ("Erne (Ballyshannon)", "hydro", 65, 587300, 858600),
    ("Liffey Scheme (Poulaphouca)", "hydro", 30, 694500, 714200),
    ("Lee Scheme (Inniscarra/Carrigadrohid)", "hydro", 27, 548600, 572400),
    ("Cathaleens Falls", "hydro", 45, 588000, 860000),
    ("Lisheen Solar Farm", "solar", 30, 525200, 667500),
    ("Millvale Solar Farm", "solar", 21, 650000, 680000),
    ("Hortland Solar Farm", "solar", 25, 696000, 738000),
]

# OSM generator:source tag → fuel type mapping
_OSM_SOURCE_TO_FUEL = {
    "wind": "wind",
    "solar": "solar",
    "hydro": "hydro",
    "biomass": "biomass",
    "biogas": "biogas",
    "gas": "gas",
    "coal": "coal",
    "oil": "oil",
    "diesel": "oil",
    "nuclear": "nuclear",
    "waste": "biomass",
    "geothermal": "geothermal",
}

# Default capacity (MW) per fuel type when OSM tag is missing
_DEFAULT_CAPACITY_MW = {
    "wind": 3.0,
    "solar": 0.05,
    "hydro": 5.0,
    "biomass": 2.0,
    "gas": 50.0,
    "coal": 100.0,
    "oil": 50.0,
}

# Deduplication distance: OSM generators within this range of a SEAI/KNOWN gen are dropped
_DEDUP_RADIUS_M = 500


def _parse_seai_wind_csv(path: Path) -> pd.DataFrame:
    """
    Parse SEAI connected wind farm CSV into a DataFrame.
    Returns DataFrame with columns: name, fuel, capacity_mw, easting, northing
    """
    df = pd.read_csv(path)

    col_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if "windfarm" in cl or cl == "name":
            col_map[c] = "name"
        elif "mec" in cl and "mw" in cl:
            col_map[c] = "capacity_mw"
        elif "installed" in cl and "capacity" in cl:
            col_map[c] = "installed_mw"
        elif "nat_grid_e" in cl or "easting" in cl:
            col_map[c] = "easting"
        elif "nat_grid_n" in cl or "northing" in cl:
            col_map[c] = "northing"
        elif "status" in cl:
            col_map[c] = "status"

    df = df.rename(columns=col_map)

    if "status" in df.columns:
        df = df[df["status"].str.lower().str.contains("connect", na=False)]

    if "capacity_mw" not in df.columns and "installed_mw" in df.columns:
        df["capacity_mw"] = df["installed_mw"]

    df["capacity_mw"] = pd.to_numeric(df["capacity_mw"], errors="coerce")
    df["easting"] = pd.to_numeric(df["easting"], errors="coerce")
    df["northing"] = pd.to_numeric(df["northing"], errors="coerce")

    df = df.dropna(subset=["easting", "northing", "capacity_mw"])
    df = df[df["capacity_mw"] > 0]

    return pd.DataFrame({
        "name": df["name"].values,
        "fuel": "wind",
        "capacity_mw": df["capacity_mw"].values,
        "easting": df["easting"].values,
        "northing": df["northing"].values,
    })


def _parse_osm_capacity(output_tag: str) -> float | None:
    """Parse generator:output:electricity tag like '2 MW' or '500 kW' to MW."""
    if not output_tag or not isinstance(output_tag, str):
        return None
    output_tag = output_tag.strip().lower()
    try:
        if "mw" in output_tag:
            return float(output_tag.replace("mw", "").strip())
        elif "kw" in output_tag:
            return float(output_tag.replace("kw", "").strip()) / 1000
        elif "w" in output_tag:
            return float(output_tag.replace("w", "").strip()) / 1_000_000
        else:
            return float(output_tag)
    except (ValueError, TypeError):
        return None


def _load_osm_generators() -> pd.DataFrame:
    """
    Load OSM generators GeoPackage and return a DataFrame with
    name, fuel, capacity_mw, easting, northing (EPSG:2157).
    """
    if not OSM_GENERATORS_FILE.exists():
        return pd.DataFrame(columns=["name", "fuel", "capacity_mw", "easting", "northing"])

    gdf = gpd.read_file(str(OSM_GENERATORS_FILE))
    gdf = gdf.to_crs(GRID_CRS_ITM)

    # Map generator:source to fuel type
    src_col = "generator_source" if "generator_source" in gdf.columns else None
    if src_col is None:
        print("  WARNING: OSM generators file has no generator_source column")
        return pd.DataFrame(columns=["name", "fuel", "capacity_mw", "easting", "northing"])

    gdf["fuel"] = gdf[src_col].str.lower().str.strip().map(_OSM_SOURCE_TO_FUEL)
    # Drop rows with unmapped or missing fuel type
    gdf = gdf.dropna(subset=["fuel"])

    # Parse capacity
    out_col = "generator_output" if "generator_output" in gdf.columns else None
    if out_col:
        gdf["capacity_mw"] = gdf[out_col].apply(_parse_osm_capacity)
    else:
        gdf["capacity_mw"] = np.nan

    # Fill missing capacity with defaults per fuel type
    for fuel, default_cap in _DEFAULT_CAPACITY_MW.items():
        mask = (gdf["fuel"] == fuel) & gdf["capacity_mw"].isna()
        gdf.loc[mask, "capacity_mw"] = default_cap

    # Remaining NaN → 1 MW generic default
    gdf["capacity_mw"] = gdf["capacity_mw"].fillna(1.0)

    # Use centroid for polygon/line geometries
    non_point = gdf.geometry.geom_type != "Point"
    if non_point.any():
        gdf.loc[non_point, "geometry"] = gdf.loc[non_point].geometry.centroid

    return pd.DataFrame({
        "name": gdf.get("name", pd.Series(dtype=str)).values,
        "fuel": gdf["fuel"].values,
        "capacity_mw": gdf["capacity_mw"].values,
        "easting": gdf.geometry.x.values,
        "northing": gdf.geometry.y.values,
    })


def build_generator_gdf() -> gpd.GeoDataFrame:
    """
    Combine SEAI wind farms + known generators + OSM generators
    into a single GeoDataFrame in EPSG:2157. OSM generators are
    deduplicated against SEAI/KNOWN by 500m proximity (prefer SEAI/KNOWN).
    """
    # ── SEAI wind farms ───────────────────────────────────────────────────────
    if SEAI_WIND_FARMS_FILE.exists():
        wind_df = _parse_seai_wind_csv(SEAI_WIND_FARMS_FILE)
        print(f"  SEAI: {len(wind_df)} connected wind farms, "
              f"total {wind_df['capacity_mw'].sum():.0f} MW")
    else:
        print("  WARNING: SEAI wind farm CSV not found — using known generators only")
        wind_df = pd.DataFrame(columns=["name", "fuel", "capacity_mw", "easting", "northing"])

    # ── Known generators ──────────────────────────────────────────────────────
    known_df = pd.DataFrame(KNOWN_GENERATORS,
                            columns=["name", "fuel", "capacity_mw", "easting", "northing"])

    # ── OSM generators ────────────────────────────────────────────────────────
    osm_df = _load_osm_generators()
    print(f"  OSM generators: {len(osm_df)} features, "
          f"total {osm_df['capacity_mw'].sum():.0f} MW")

    # ── Combine authoritative sources (SEAI + KNOWN) ──────────────────────────
    auth_df = pd.concat([wind_df, known_df], ignore_index=True)
    auth_geom = [Point(row.easting, row.northing) for _, row in auth_df.iterrows()]
    auth_gdf = gpd.GeoDataFrame(auth_df, geometry=auth_geom, crs=GRID_CRS_ITM)

    # ── Deduplicate OSM against authoritative sources ─────────────────────────
    if len(osm_df) > 0 and len(auth_gdf) > 0:
        osm_geom = [Point(row.easting, row.northing) for _, row in osm_df.iterrows()]
        osm_gdf = gpd.GeoDataFrame(osm_df, geometry=osm_geom, crs=GRID_CRS_ITM)

        auth_tree = STRtree(auth_gdf.geometry.values)
        osm_buffers = osm_gdf.geometry.buffer(_DEDUP_RADIUS_M)
        dup_osm_idxs, _ = auth_tree.query(osm_buffers.values, predicate="contains")
        dup_osm_set = set(dup_osm_idxs)

        osm_df_deduped = osm_df.iloc[
            [i for i in range(len(osm_df)) if i not in dup_osm_set]
        ].reset_index(drop=True)
        n_dropped = len(osm_df) - len(osm_df_deduped)
        if n_dropped > 0:
            print(f"  OSM dedup: dropped {n_dropped} generators within "
                  f"{_DEDUP_RADIUS_M}m of SEAI/KNOWN sources")
        osm_df = osm_df_deduped

    # ── Merge all sources ─────────────────────────────────────────────────────
    all_gen = pd.concat([auth_df.drop(columns=["geometry"], errors="ignore"),
                         osm_df], ignore_index=True)
    geometry = [Point(row.easting, row.northing) for _, row in all_gen.iterrows()]
    gdf = gpd.GeoDataFrame(all_gen, geometry=geometry, crs=GRID_CRS_ITM)
    gdf["is_renewable"] = gdf["fuel"].isin(RENEWABLE_FUELS)

    total_mw = gdf["capacity_mw"].sum()
    renewable_mw = gdf.loc[gdf["is_renewable"], "capacity_mw"].sum()
    print(f"  Total generators: {len(gdf)} ({total_mw:.0f} MW)")
    print(f"  Renewable: {gdf['is_renewable'].sum()} ({renewable_mw:.0f} MW, "
          f"{100*renewable_mw/total_mw:.1f}% nationally)")

    return gdf


def compute_renewable_scores(
    tiles: gpd.GeoDataFrame,
    generators: gpd.GeoDataFrame,
    wind_stats: pd.Series,
    solar_stats: pd.Series,
    grid_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Hybrid renewable scoring combining:
      A) Distance-weighted installed capacity (50 km radius, linear decay)
      B) Resource potential from wind/solar rasters (100% tile coverage)
      C) Grid proximity (from grid_df)

    Returns DataFrame with: tile_id, renewable_pct, renewable_score,
    renewable_capacity_mw, fossil_capacity_mw
    """
    print(f"  Computing renewable penetration (radius={SEARCH_RADIUS_M/1000:.0f} km, "
          f"distance-weighted + resource potential)...")

    tile_ids = tiles["tile_id"].values
    tile_centroids = tiles.geometry.centroid
    n_tiles = len(tile_ids)

    # ── Part A: Distance-weighted installed capacity ──────────────────────────
    gen_geoms = generators.geometry.values
    gen_capacities = generators["capacity_mw"].values
    gen_is_renewable = generators["is_renewable"].values

    gen_tree = STRtree(gen_geoms)

    # Buffer centroids and bulk-query
    buffers = tile_centroids.buffer(SEARCH_RADIUS_M)
    tile_idxs, gen_idxs = gen_tree.query(buffers.values, predicate="contains")

    # Compute actual distances for each (tile, generator) pair
    tile_centroid_arr = tile_centroids.values
    pair_dists = shapely.distance(
        tile_centroid_arr[tile_idxs],
        gen_geoms[gen_idxs],
    )

    # Linear decay weight: 1 at distance=0, 0 at distance=SEARCH_RADIUS_M
    weights = np.maximum(0, 1 - pair_dists / SEARCH_RADIUS_M)

    # Weighted capacity aggregation
    weighted_renewable_cap = np.zeros(n_tiles)
    weighted_fossil_cap = np.zeros(n_tiles)
    weighted_total_cap = np.zeros(n_tiles)
    raw_renewable_cap = np.zeros(n_tiles)
    raw_fossil_cap = np.zeros(n_tiles)

    pair_caps = gen_capacities[gen_idxs]
    pair_renewable = gen_is_renewable[gen_idxs]
    weighted_caps = pair_caps * weights

    np.add.at(weighted_total_cap, tile_idxs, weighted_caps)
    np.add.at(weighted_renewable_cap, tile_idxs,
              np.where(pair_renewable, weighted_caps, 0))
    np.add.at(weighted_fossil_cap, tile_idxs,
              np.where(~pair_renewable, weighted_caps, 0))
    # Raw (unweighted) for reporting
    np.add.at(raw_renewable_cap, tile_idxs,
              np.where(pair_renewable, pair_caps, 0))
    np.add.at(raw_fossil_cap, tile_idxs,
              np.where(~pair_renewable, pair_caps, 0))

    # renewable_pct based on weighted capacity (0 where no generators)
    has_generators = weighted_total_cap > 0
    renewable_pct = np.where(
        has_generators,
        weighted_renewable_cap / weighted_total_cap * 100,
        0.0,
    )
    installed_renewable_pct = np.clip(renewable_pct, 0, 100)

    n_no_gen = (~has_generators).sum()
    print(f"  Part A (installed capacity): {has_generators.sum()} tiles with generators, "
          f"{n_no_gen} without")

    # ── Part B: Resource potential from rasters ───────────────────────────────
    wind = wind_stats.reindex(tile_ids).fillna(wind_stats.median()).values
    solar = solar_stats.reindex(tile_ids).fillna(solar_stats.median()).values

    wind_min, wind_max = wind.min(), wind.max()
    solar_min, solar_max = solar.min(), solar.max()

    wind_range = wind_max - wind_min
    solar_range = solar_max - solar_min

    wind_norm = (100 * (wind - wind_min) / wind_range) if wind_range > 0 else np.full(n_tiles, 50.0)
    solar_norm = (100 * (solar - solar_min) / solar_range) if solar_range > 0 else np.full(n_tiles, 50.0)

    resource_potential = 0.65 * wind_norm + 0.35 * solar_norm
    print(f"  Part B (resource potential): min={resource_potential.min():.1f}, "
          f"max={resource_potential.max():.1f}, mean={resource_potential.mean():.1f}")

    # ── Part C: Hybrid renewable_score ────────────────────────────────────────
    grid_proximity = grid_df.set_index("tile_id")["grid_proximity"].reindex(tile_ids).fillna(0).values

    renewable_score = (
        0.50 * resource_potential
        + 0.30 * installed_renewable_pct
        + 0.20 * grid_proximity
    )
    renewable_score = np.clip(np.round(renewable_score), 0, 100).astype(int)

    df = pd.DataFrame({
        "tile_id": tile_ids,
        "renewable_pct": np.round(renewable_pct, 1),
        "renewable_score": renewable_score,
        "renewable_capacity_mw": np.round(raw_renewable_cap, 1),
        "fossil_capacity_mw": np.round(raw_fossil_cap, 1),
    })

    print(f"  Hybrid score: min={df['renewable_score'].min()}, "
          f"max={df['renewable_score'].max()}, "
          f"mean={df['renewable_score'].mean():.1f}, "
          f"stddev={df['renewable_score'].std():.1f}")
    print(f"  renewable_pct: min={df['renewable_pct'].min():.1f}%, "
          f"max={df['renewable_pct'].max():.1f}%, "
          f"mean={df['renewable_pct'].mean():.1f}%")

    return df


def compute_energy_scores(
    wind_stats: pd.Series,
    solar_stats: pd.Series,
    grid_df: pd.DataFrame,
    renewable_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute composite energy score from all 4 sub-metrics.

    score = 0.30 * wind_norm + 0.25 * solar_norm + 0.25 * grid_proximity + 0.20 * renewable_score

    Args:
        wind_stats: Series[tile_id -> wind_speed_100m m/s]
        solar_stats: Series[tile_id -> solar_ghi kWh/m^2/yr]
        grid_df: DataFrame from compute_grid_proximity()
        renewable_df: DataFrame from compute_renewable_scores()

    Returns:
        DataFrame matching energy_scores table schema.
    """
    df = grid_df.copy().set_index("tile_id")

    # Align wind and solar on tile_id
    wind = wind_stats.reindex(df.index)
    solar = solar_stats.reindex(df.index)

    # Fill NaN with median before normalisation (graceful degradation)
    wind = wind.fillna(wind.median())
    solar = solar.fillna(solar.median())

    # Min-max normalise wind and solar across all tiles
    wind_range = wind.max() - wind.min()
    solar_range = solar.max() - solar.min()

    wind_norm = 100 * (wind - wind.min()) / wind_range if wind_range > 0 else pd.Series(50.0, index=wind.index)
    solar_norm = 100 * (solar - solar.min()) / solar_range if solar_range > 0 else pd.Series(50.0, index=solar.index)

    # Align renewable score
    renew = renewable_df.set_index("tile_id")["renewable_score"].reindex(df.index).fillna(50)

    # Composite score with all 4 factors
    score = (
        0.30 * wind_norm
        + 0.25 * solar_norm
        + 0.25 * df["grid_proximity"]
        + 0.20 * renew
    )
    score = score.clip(0, 100).round(2)

    # Derived wind columns
    wind_speed_50m = (wind * 0.85).round(3)
    wind_speed_150m = (wind * 1.10).round(3)

    # Align renewable data columns
    renew_aligned = renewable_df.set_index("tile_id").reindex(df.index)

    result = pd.DataFrame({
        "tile_id": df.index,
        "score": score.values,
        "wind_speed_100m": wind.round(3).values,
        "wind_speed_50m": wind_speed_50m.values,
        "wind_speed_150m": wind_speed_150m.values,
        "solar_ghi": solar.round(3).values,
        "grid_proximity": df["grid_proximity"].values,
        "nearest_transmission_line_km": df["nearest_transmission_line_km"].values,
        "nearest_substation_km": df["nearest_substation_km"].values,
        "nearest_substation_name": df["nearest_substation_name"].values,
        "nearest_substation_voltage": df["nearest_substation_voltage"].values,
        "grid_low_confidence": df["grid_low_confidence"].values,
        "renewable_pct": renew_aligned["renewable_pct"].values,
        "renewable_score": renew_aligned["renewable_score"].values,
        "renewable_capacity_mw": renew_aligned["renewable_capacity_mw"].values,
        "fossil_capacity_mw": renew_aligned["fossil_capacity_mw"].values,
    })
    return result.reset_index(drop=True)


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


def upsert_energy_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Upsert energy_scores table. ON CONFLICT(tile_id) DO UPDATE.

    Returns:
        Number of rows upserted.
    """
    sql = """
        INSERT INTO energy_scores (
            tile_id, score, wind_speed_100m, wind_speed_50m, wind_speed_150m,
            solar_ghi, grid_proximity, nearest_transmission_line_km,
            nearest_substation_km, nearest_substation_name,
            nearest_substation_voltage, grid_low_confidence,
            renewable_pct, renewable_score, renewable_capacity_mw, fossil_capacity_mw
        ) VALUES %s
        ON CONFLICT (tile_id) DO UPDATE SET
            score                        = EXCLUDED.score,
            wind_speed_100m              = EXCLUDED.wind_speed_100m,
            wind_speed_50m               = EXCLUDED.wind_speed_50m,
            wind_speed_150m              = EXCLUDED.wind_speed_150m,
            solar_ghi                    = EXCLUDED.solar_ghi,
            grid_proximity               = EXCLUDED.grid_proximity,
            nearest_transmission_line_km = EXCLUDED.nearest_transmission_line_km,
            nearest_substation_km        = EXCLUDED.nearest_substation_km,
            nearest_substation_name      = EXCLUDED.nearest_substation_name,
            nearest_substation_voltage   = EXCLUDED.nearest_substation_voltage,
            grid_low_confidence          = EXCLUDED.grid_low_confidence,
            renewable_pct                = EXCLUDED.renewable_pct,
            renewable_score              = EXCLUDED.renewable_score,
            renewable_capacity_mw        = EXCLUDED.renewable_capacity_mw,
            fossil_capacity_mw           = EXCLUDED.fossil_capacity_mw
    """

    cols = [
        "tile_id", "score", "wind_speed_100m", "wind_speed_50m", "wind_speed_150m",
        "solar_ghi", "grid_proximity", "nearest_transmission_line_km",
        "nearest_substation_km", "nearest_substation_name",
        "nearest_substation_voltage", "grid_low_confidence",
        "renewable_pct", "renewable_score", "renewable_capacity_mw", "fossil_capacity_mw",
    ]

    rows = [tuple(_to_py(row[c]) for c in cols) for _, row in df.iterrows()]

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        batch_size = 2000
        for i in tqdm(range(0, len(rows), batch_size), desc="Upserting energy_scores"):
            execute_values(cur, sql, rows[i : i + batch_size])
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
        pg_conn.close()

    return len(rows)


def upsert_pins_energy(
    osm_power: gpd.GeoDataFrame,
    engine: sqlalchemy.Engine,
) -> int:
    """
    Load energy pins from OSM power data into pins_energy table.

    Pin types:
      - wind_farm: OSM features with generator:source=wind
      - transmission_node: power=substation with voltage >= 110 kV
      - substation: other power=substation features

    Returns:
        Number of pins inserted.
    """
    # OSM power is in EPSG:2157; reproject to WGS84 for storage
    osm_wgs84 = osm_power.to_crs(GRID_CRS_WGS84)

    pin_rows = []

    # ── Wind farms ─────────────────────────────────────────────────────────────
    gen_col = next((c for c in osm_wgs84.columns if "generator" in c.lower() and "source" in c.lower()), None)
    if gen_col:
        wind_farms = osm_wgs84[osm_wgs84[gen_col].str.lower().eq("wind")]
    else:
        wind_farms = osm_wgs84.iloc[0:0]  # empty — no wind farm tag found

    for _, row in wind_farms.iterrows():
        geom = row.geometry
        if geom.geom_type != "Point":
            geom = geom.centroid
        pin_rows.append({
            "lng": geom.x, "lat": geom.y,
            "name": row.get("name") or "Wind Farm",
            "type": "wind_farm",
            "capacity_mw": None,
            "voltage_kv": None,
            "osm_id": str(row.get("osm_id", "") or ""),
            "operator": row.get("operator"),
        })

    # ── Substations ────────────────────────────────────────────────────────────
    substations = osm_wgs84[osm_wgs84["power"] == "substation"].copy()
    for _, row in substations.iterrows():
        geom = row.geometry
        if geom.geom_type != "Point":
            geom = geom.centroid

        voltage_str = str(row.get("voltage") or "")
        try:
            voltage_v = int(voltage_str.split(";")[0].strip()) if voltage_str else 0
        except ValueError:
            voltage_v = 0

        pin_type = "transmission_node" if voltage_v >= 110_000 else "substation"
        voltage_kv = round(voltage_v / 1000, 1) if voltage_v > 0 else None

        pin_rows.append({
            "lng": geom.x, "lat": geom.y,
            "name": row.get("name") or f"{pin_type.replace('_', ' ').title()}",
            "type": pin_type,
            "capacity_mw": None,
            "voltage_kv": voltage_kv,
            "osm_id": str(row.get("osm_id", "") or ""),
            "operator": row.get("operator"),
        })

    if not pin_rows:
        print("  No energy pins to insert.")
        return 0

    # Delete existing energy pins and re-insert (idempotent)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pins_energy"))

    # Insert pins and assign tile_id via ST_Within
    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO pins_energy (geom, name, type, capacity_mw, voltage_kv, osm_id, operator)
            VALUES %s
            """,
            [
                (
                    f"SRID=4326;POINT({r['lng']} {r['lat']})",
                    r["name"],
                    r["type"],
                    r["capacity_mw"],
                    r["voltage_kv"],
                    r["osm_id"] or None,
                    r["operator"],
                )
                for r in pin_rows
            ],
            template="(ST_GeomFromEWKT(%s), %s, %s, %s, %s, %s, %s)",
        )

        # Assign tile_id via ST_Within spatial join
        cur.execute("""
            UPDATE pins_energy p
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


def write_metric_ranges(
    wind_stats: pd.Series,
    solar_stats: pd.Series,
    renewable_df: pd.DataFrame,
    engine: sqlalchemy.Engine,
) -> None:
    """
    Write min/max to metric_ranges table for wind_speed_100m, solar_ghi, renewable_pct.
    These values are read by the Martin tile_heatmap function for normalisation.
    """
    ranges = [
        ("energy", "wind_speed_100m", float(wind_stats.min()), float(wind_stats.max()), "m/s"),
        ("energy", "solar_ghi", float(solar_stats.min()), float(solar_stats.max()), "kWh/m²/yr"),
        ("energy", "renewable_pct", float(renewable_df["renewable_pct"].min()),
         float(renewable_df["renewable_pct"].max()), "%"),
    ]

    with engine.begin() as conn:
        for sort, metric, min_val, max_val, unit in ranges:
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
                {"sort": sort, "metric": metric, "min_val": min_val, "max_val": max_val, "unit": unit},
            )
    print(f"  Metric ranges written: wind [{ranges[0][2]:.2f}–{ranges[0][3]:.2f} m/s], "
          f"solar [{ranges[1][2]:.0f}–{ranges[1][3]:.0f} kWh/m²/yr], "
          f"renewable [{ranges[2][2]:.1f}–{ranges[2][3]:.1f}%]")


def main():
    """
    Energy ingest pipeline:
      1. Load tiles from DB
      2. Extract wind speed (zonal mean from GeoTIFF)
      3. Extract solar GHI (zonal mean from GeoTIFF)
      4. Compute grid proximity from OSM power data
      5. Build generator dataset + compute renewable penetration
      6. Compute composite energy scores (all 4 factors)
      7. Upsert energy_scores
      8. Write metric_ranges for wind + solar + renewable
      9. Upsert pins_energy

    Run AFTER: grid/generate_grid.py
    Run BEFORE: overall/compute_composite.py
    """
    print("=" * 60)
    print("Starting energy ingest...")
    print("=" * 60)

    # ── Check source files exist before doing any work ─────────────────────────
    missing = [p for p in (WIND_ATLAS_FILE, SOLAR_ATLAS_FILE, OSM_POWER_FILE) if not p.exists()]
    if missing:
        for p in missing:
            print(f"  ERROR: missing source file: {p}")
        print("\nDownload instructions: see ireland-data-sources.md §2–§3")
        raise SystemExit(1)

    if not SEAI_WIND_FARMS_FILE.exists():
        print(f"  WARNING: SEAI wind farm CSV not found at {SEAI_WIND_FARMS_FILE}")
        print("  Renewable scores will use known generators only (less accurate).")
        print("  Run: python energy/download_sources.py")

    if not OSM_GENERATORS_FILE.exists():
        print(f"  WARNING: OSM generators file not found at {OSM_GENERATORS_FILE}")
        print("  Renewable scores will not include OSM generators (less spatial coverage).")
        print("  Run: python energy/download_sources.py")

    engine = sqlalchemy.create_engine(DB_URL)

    # ── Step 1: Load tiles ─────────────────────────────────────────────────────
    print("\n[1/9] Loading tiles from database...")
    tiles = load_tiles(engine)
    print(f"  Loaded {len(tiles)} tiles")

    # ── Step 2: Wind speed ─────────────────────────────────────────────────────
    print(f"\n[2/9] Extracting wind speed (100m) from raster...")
    wind_stats = extract_raster_zonal_stats(tiles, WIND_ATLAS_FILE, stat="mean")
    print(f"  Wind: min={wind_stats.min():.2f}, max={wind_stats.max():.2f}, "
          f"mean={wind_stats.mean():.2f} m/s  (NaN: {wind_stats.isna().sum()})")

    # ── Step 3: Solar GHI ──────────────────────────────────────────────────────
    print(f"\n[3/9] Extracting solar GHI from raster...")
    solar_stats = extract_raster_zonal_stats(tiles, SOLAR_ATLAS_FILE, stat="mean")
    print(f"  Solar: min={solar_stats.min():.1f}, max={solar_stats.max():.1f}, "
          f"mean={solar_stats.mean():.1f} kWh/m²/yr  (NaN: {solar_stats.isna().sum()})")

    # ── Step 4: OSM power / grid proximity ────────────────────────────────────
    print(f"\n[4/9] Loading OSM power infrastructure...")
    osm_power = _load_osm_power(OSM_POWER_FILE)
    print(f"  Loaded {len(osm_power)} OSM power features")

    print(f"\n[5/9] Computing grid proximity...")
    grid_df = compute_grid_proximity(tiles, osm_power)
    low_conf = grid_df["grid_low_confidence"].sum()
    print(f"  Grid proximity: avg={grid_df['grid_proximity'].mean():.1f}, "
          f"low-confidence tiles={low_conf}")

    # ── Step 5: Renewable penetration ──────────────────────────────────────────
    print(f"\n[6/9] Building generator dataset + computing renewable penetration...")
    generators = build_generator_gdf()
    renewable_df = compute_renewable_scores(
        tiles, generators, wind_stats, solar_stats, grid_df
    )

    # ── Step 6: Compute energy scores (all 4 factors) ──────────────────────────
    print(f"\n[7/9] Computing composite energy scores...")
    scores_df = compute_energy_scores(wind_stats, solar_stats, grid_df, renewable_df)
    print(f"  Score: min={scores_df['score'].min():.2f}, max={scores_df['score'].max():.2f}, "
          f"mean={scores_df['score'].mean():.2f}")

    # ── Step 7: Upsert energy_scores ──────────────────────────────────────────
    print(f"\n[8/9] Upserting energy_scores...")
    n = upsert_energy_scores(scores_df, engine)
    print(f"  Upserted {n} rows into energy_scores")

    # ── Step 8: Metric ranges ──────────────────────────────────────────────────
    wind_valid = wind_stats.dropna()
    solar_valid = solar_stats.dropna()
    print(f"\n[8.5/9] Writing metric ranges...")
    write_metric_ranges(wind_valid, solar_valid, renewable_df, engine)

    # ── Step 9: Upsert pins_energy ────────────────────────────────────────────
    print(f"\n[9/9] Upserting energy pins...")
    n_pins = upsert_pins_energy(osm_power, engine)
    print(f"  Inserted {n_pins} energy pins")

    print("\n" + "=" * 60)
    print(f"Energy ingest complete: {n} tiles scored, {n_pins} pins inserted")
    print(f"  Renewable: avg {scores_df['renewable_pct'].mean():.1f}%")
    print("Next step: restart Martin to serve updated tiles:")
    print("  docker compose restart martin")
    print("=" * 60)


if __name__ == "__main__":
    main()
