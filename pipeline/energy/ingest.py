"""
FILE: pipeline/energy/ingest.py
Role: Ingest energy data sources and compute energy_scores for all tiles.
Agent boundary: Pipeline — Energy sort (§5.2, §8, §10)
Dependencies:
  - tiles table must be populated (run grid/generate_grid.py first)
  - config.py: WIND_ATLAS_FILE, SOLAR_ATLAS_FILE, OSM_POWER_FILE
  - Raw source files present in DATA_ROOT/energy/
  - See ireland-data-sources.md §2–§3 for source formats and download URLs
Output:
  - Populates energy_scores table (upsert — idempotent)
  - Populates pins_energy table (upsert)
  - Writes metric_ranges rows for wind_speed_100m and solar_ghi (used by Martin normalisation)
How to test:
  python energy/ingest.py
  psql $DATABASE_URL -c "SELECT COUNT(*), AVG(score) FROM energy_scores;"

Data sources used (details in ireland-data-sources.md):
  Wind speed: Global Wind Atlas GeoTIFF — 100m hub height, ~1 km resolution
  Solar GHI:  Global Solar Atlas GeoTIFF — kWh/m²/yr, ~1 km resolution
  Grid infra: OSM power=substation and power=line (Geofabrik Ireland extract)

ARCHITECTURE RULES:
  - Store raw values (m/s, kWh/m²/yr) in energy_scores columns.
  - DO NOT pre-normalise — normalisation is done in tile_heatmap SQL function.
  - grid_proximity (0–100) IS pre-normalised (inverse distance score).
  - Write metric_ranges for wind_speed_100m and solar_ghi.
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
from tqdm import tqdm
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, WIND_ATLAS_FILE, SOLAR_ATLAS_FILE, OSM_POWER_FILE,
    GRID_CRS_ITM, GRID_CRS_WGS84
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


def compute_energy_scores(
    wind_stats: pd.Series,
    solar_stats: pd.Series,
    grid_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute composite energy score from sub-metrics.

    Initial score = 0.30 * wind_norm + 0.25 * solar_norm + 0.25 * grid_proximity + 0.20 * 50 (placeholder)
    The renewable_score component (0.20 weight) is applied later by renewable.py which
    recomputes the composite with actual renewable data.

    Args:
        wind_stats: Series[tile_id → wind_speed_100m m/s]
        solar_stats: Series[tile_id → solar_ghi kWh/m²/yr]
        grid_df: DataFrame from compute_grid_proximity()

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

    # Composite score (renewable placeholder = 50 until renewable.py runs)
    score = (
        0.30 * wind_norm
        + 0.25 * solar_norm
        + 0.25 * df["grid_proximity"]
        + 0.20 * 50  # placeholder — renewable.py recomputes with real data
    )
    score = score.clip(0, 100).round(2)

    # Derived wind columns
    wind_speed_50m = (wind * 0.85).round(3)
    wind_speed_150m = (wind * 1.10).round(3)

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
            nearest_substation_voltage, grid_low_confidence
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
            grid_low_confidence          = EXCLUDED.grid_low_confidence
    """

    cols = [
        "tile_id", "score", "wind_speed_100m", "wind_speed_50m", "wind_speed_150m",
        "solar_ghi", "grid_proximity", "nearest_transmission_line_km",
        "nearest_substation_km", "nearest_substation_name",
        "nearest_substation_voltage", "grid_low_confidence",
    ]

    rows = [tuple(_to_py(row[c]) for c in cols) for _, row in df.iterrows()]

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        batch_size = 500
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
    engine: sqlalchemy.Engine,
) -> None:
    """
    Write min/max to metric_ranges table for wind_speed_100m and solar_ghi.
    These values are read by the Martin tile_heatmap function for normalisation.
    """
    ranges = [
        ("energy", "wind_speed_100m", float(wind_stats.min()), float(wind_stats.max()), "m/s"),
        ("energy", "solar_ghi", float(solar_stats.min()), float(solar_stats.max()), "kWh/m²/yr"),
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
          f"solar [{ranges[1][2]:.0f}–{ranges[1][3]:.0f} kWh/m²/yr]")


def main():
    """
    Energy ingest pipeline:
      1. Load tiles from DB
      2. Extract wind speed (zonal mean from GeoTIFF)
      3. Extract solar GHI (zonal mean from GeoTIFF)
      4. Compute grid proximity from OSM power data
      5. Compute composite energy scores
      6. Upsert energy_scores
      7. Upsert pins_energy
      8. Write metric_ranges for wind + solar

    Run AFTER: grid/generate_grid.py
    Run BEFORE: overall/compute_composite.py

    Required source files (see ireland-data-sources.md §2–§3):
      /data/energy/wind_speed_100m.tif  — Global Wind Atlas GeoTIFF (100m hub height)
      /data/energy/solar_ghi.tif        — Global Solar Atlas GeoTIFF (kWh/m²/yr)
      /data/energy/osm_ireland_power.gpkg — OSM power infrastructure (Geofabrik)
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

    engine = sqlalchemy.create_engine(DB_URL)

    # ── Step 1: Load tiles ─────────────────────────────────────────────────────
    print("\n[1/8] Loading tiles from database...")
    tiles = load_tiles(engine)
    print(f"  Loaded {len(tiles)} tiles")

    # ── Step 2: Wind speed ─────────────────────────────────────────────────────
    print(f"\n[2/8] Extracting wind speed (100m) from raster...")
    wind_stats = extract_raster_zonal_stats(tiles, WIND_ATLAS_FILE, stat="mean")
    print(f"  Wind: min={wind_stats.min():.2f}, max={wind_stats.max():.2f}, "
          f"mean={wind_stats.mean():.2f} m/s  (NaN: {wind_stats.isna().sum()})")

    # ── Step 3: Solar GHI ──────────────────────────────────────────────────────
    print(f"\n[3/8] Extracting solar GHI from raster...")
    solar_stats = extract_raster_zonal_stats(tiles, SOLAR_ATLAS_FILE, stat="mean")
    print(f"  Solar: min={solar_stats.min():.1f}, max={solar_stats.max():.1f}, "
          f"mean={solar_stats.mean():.1f} kWh/m²/yr  (NaN: {solar_stats.isna().sum()})")

    # ── Step 4: OSM power / grid proximity ────────────────────────────────────
    print(f"\n[4/8] Loading OSM power infrastructure...")
    osm_power = _load_osm_power(OSM_POWER_FILE)
    print(f"  Loaded {len(osm_power)} OSM power features")

    print(f"\n[5/8] Computing grid proximity...")
    grid_df = compute_grid_proximity(tiles, osm_power)
    low_conf = grid_df["grid_low_confidence"].sum()
    print(f"  Grid proximity: avg={grid_df['grid_proximity'].mean():.1f}, "
          f"low-confidence tiles={low_conf}")

    # ── Step 5: Compute energy scores ─────────────────────────────────────────
    print(f"\n[6/8] Computing composite energy scores...")
    scores_df = compute_energy_scores(wind_stats, solar_stats, grid_df)
    print(f"  Score: min={scores_df['score'].min():.2f}, max={scores_df['score'].max():.2f}, "
          f"mean={scores_df['score'].mean():.2f}")

    # ── Step 6: Upsert energy_scores ──────────────────────────────────────────
    print(f"\n[7/8] Upserting energy_scores...")
    n = upsert_energy_scores(scores_df, engine)
    print(f"  Upserted {n} rows into energy_scores")

    # ── Step 7: Metric ranges ──────────────────────────────────────────────────
    # Use the original wind/solar stats (before NaN fill) for true min/max
    wind_valid = wind_stats.dropna()
    solar_valid = solar_stats.dropna()
    print(f"\n[7.5/8] Writing metric ranges...")
    write_metric_ranges(wind_valid, solar_valid, engine)

    # ── Step 8: Upsert pins_energy ────────────────────────────────────────────
    print(f"\n[8/8] Upserting energy pins...")
    n_pins = upsert_pins_energy(osm_power, engine)
    print(f"  Inserted {n_pins} energy pins")

    # ── Step 9: Renewable energy penetration ─────────────────────────────────
    print(f"\n[9/9] Running renewable energy pipeline...")
    try:
        from energy.renewable import main as renewable_main
        renewable_main()
    except Exception as e:
        print(f"  WARNING: Renewable pipeline failed (non-fatal): {e}")
        print("  You can run it separately: python energy/renewable.py")

    print("\n" + "=" * 60)
    print(f"Energy ingest complete: {n} tiles scored, {n_pins} pins inserted")
    print("Next step: restart Martin to serve updated tiles:")
    print("  docker compose restart martin")
    print("=" * 60)


if __name__ == "__main__":
    main()
