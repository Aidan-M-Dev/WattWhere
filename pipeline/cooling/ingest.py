"""
FILE: pipeline/cooling/ingest.py
Role: Ingest climate and water resource data, compute cooling_scores.
Agent boundary: Pipeline — Cooling sort (§5.4, §8, §10)
Dependencies:
  - tiles table populated (grid/generate_grid.py)
  - config.py: MET_EIREANN_TEMP_FILE, MET_EIREANN_RAIN_FILE, EPA_RIVERS_FILE,
                OPW_HYDRO_FILE, GSI_AQUIFER_FILE
  - See ireland-data-sources.md §6, §7 for source formats
Output:
  - Populates cooling_scores table (upsert — idempotent)
  - Populates pins_cooling table (upsert)
  - Writes metric_ranges rows for temperature and rainfall (used by Martin)
How to test:
  python cooling/ingest.py
  psql $DATABASE_URL -c "SELECT MIN(temperature), MAX(temperature), AVG(score) FROM cooling_scores;"

ARCHITECTURE RULES:
  - temperature stored as raw °C (NOT inverted). The tile_heatmap SQL function
    inverts it: 100 - normalised_score. Do NOT pre-invert here.
  - rainfall stored as raw mm/yr.
  - Write metric_ranges for temperature and rainfall (for legend display).
  - free_cooling_hours: estimate hours/yr below 18°C from monthly temperature grid.
    Approximation: sum months where mean_temp < 18, scale to hours.
    (More accurate: use MÉRA reanalysis hourly data if available.)
  - water_proximity: 0–100 inverse distance score to nearest EPA river/lake.
  - aquifer_productivity: 0–100 from GSI bedrock classification.
  - Composite: 40% temperature + 35% water_proximity + 25% rainfall score.
"""

import sys
from pathlib import Path
import numpy as np
import geopandas as gpd
import pandas as pd
import rasterio
import sqlalchemy
from sqlalchemy import text
from tqdm import tqdm
import psycopg2
from psycopg2.extras import execute_values
from rasterstats import zonal_stats as rasterstats_zonal_stats

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    DB_URL, MET_EIREANN_TEMP_FILE, MET_EIREANN_RAIN_FILE,
    EPA_RIVERS_FILE, OPW_HYDRO_FILE, GSI_AQUIFER_FILE,
    GRID_CRS_ITM, GRID_CRS_WGS84, TILE_SIZE_M
)


def load_tiles(engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """Load tiles from DB in EPSG:2157."""
    tiles = gpd.read_postgis(
        "SELECT tile_id, geom, centroid FROM tiles",
        engine,
        geom_col="geom",
        crs="EPSG:4326",
    )
    return tiles.to_crs(GRID_CRS_ITM)


def extract_temperature_stats(tiles: gpd.GeoDataFrame) -> pd.Series:
    """
    Zonal mean of mean annual temperature grid (°C) per tile.
    Returns Series[tile_id → °C raw].
    Source: MET_EIREANN_TEMP_FILE (GeoTIFF, EPSG:4326 from NASA POWER).

    NOTE: NASA POWER T2M is on a ~0.5° grid, interpolated to ~1km.
    For higher accuracy, use Met Éireann's native 1km grid or E-OBS 0.1°
    from Copernicus (see ireland-data-sources.md §7).
    """
    with rasterio.open(str(MET_EIREANN_TEMP_FILE)) as src:
        raster_crs = src.crs
        nodata_val = src.nodata

    raster_epsg = raster_crs.to_epsg()
    if raster_epsg:
        tiles_reproj = tiles.to_crs(f"EPSG:{raster_epsg}")
    else:
        tiles_reproj = tiles.to_crs(raster_crs.to_wkt())

    results = rasterstats_zonal_stats(
        tiles_reproj.geometry,
        str(MET_EIREANN_TEMP_FILE),
        stats=["mean"],
        nodata=nodata_val,
    )

    values = [r["mean"] if r["mean"] is not None else np.nan for r in results]
    return pd.Series(values, index=tiles["tile_id"], name="temperature")


def extract_rainfall_stats(tiles: gpd.GeoDataFrame) -> pd.Series:
    """
    Zonal mean of annual rainfall grid (mm/yr) per tile.
    Returns Series[tile_id → mm/yr raw].
    """
    with rasterio.open(str(MET_EIREANN_RAIN_FILE)) as src:
        raster_crs = src.crs
        nodata_val = src.nodata

    raster_epsg = raster_crs.to_epsg()
    if raster_epsg:
        tiles_reproj = tiles.to_crs(f"EPSG:{raster_epsg}")
    else:
        tiles_reproj = tiles.to_crs(raster_crs.to_wkt())

    results = rasterstats_zonal_stats(
        tiles_reproj.geometry,
        str(MET_EIREANN_RAIN_FILE),
        stats=["mean"],
        nodata=nodata_val,
    )

    values = [r["mean"] if r["mean"] is not None else np.nan for r in results]
    return pd.Series(values, index=tiles["tile_id"], name="rainfall")


def compute_free_cooling_hours(temperature_series: pd.Series) -> pd.Series:
    """
    Estimate free-cooling hours per year (hours below 18°C).
    Linear approximation from mean annual temperature:
      If mean_temp = 10°C → ~7,000 hrs/yr free cooling
      If mean_temp = 18°C → 0 hrs/yr free cooling (threshold)

    NOTE: This is an approximation from annual mean temperature,
    not hourly MÉRA reanalysis data. For more accurate estimates,
    use MÉRA hourly data (see ireland-data-sources.md §7).

    Returns Series[tile_id → estimated_hours].
    """
    free_hours = (8760 * (18 - temperature_series) / 18).clip(0, 8760).round(0).astype(int)
    return free_hours


def compute_water_proximity(
    tiles: gpd.GeoDataFrame,
    rivers_lakes: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Compute proximity to nearest river/lake for each tile centroid.
    Returns DataFrame with tile_id, nearest_waterbody_name, nearest_waterbody_km,
    water_proximity (0–100 inverse distance pre-normalised).

    Distance in EPSG:2157 (metres), stored as km.
    Log-inverse score with MAX_DIST_KM = 50 (Ireland has a dense river network).
    """
    MAX_DIST_KM = 50.0

    # Reproject rivers to EPSG:2157 for distance calculation
    rivers_itm = rivers_lakes.to_crs(GRID_CRS_ITM)

    # Build tile centroid GeoDataFrame
    centroids_gdf = gpd.GeoDataFrame(
        {"tile_id": tiles["tile_id"].values},
        geometry=tiles.geometry.centroid,
        crs=GRID_CRS_ITM,
    )

    # Prepare rivers for sjoin_nearest — keep name for extraction
    name_col = "name" if "name" in rivers_itm.columns else None
    keep_cols = ["geometry"]
    if name_col:
        keep_cols.append(name_col)
    rivers_clean = rivers_itm[keep_cols].copy().reset_index(drop=True)

    # Drop features with null geometry
    rivers_clean = rivers_clean[rivers_clean.geometry.notna()].reset_index(drop=True)

    if len(rivers_clean) == 0:
        print("  WARNING: No river/lake features found. Setting water_proximity to 0.")
        return pd.DataFrame({
            "tile_id": tiles["tile_id"].values,
            "nearest_waterbody_name": None,
            "nearest_waterbody_km": np.nan,
            "water_proximity": 0.0,
        })

    # Nearest waterbody join
    joined = gpd.sjoin_nearest(
        centroids_gdf,
        rivers_clean,
        how="left",
        distance_col="dist_m",
    )
    # Drop duplicates (ties) — keep nearest
    joined = joined.drop_duplicates(subset="tile_id", keep="first")

    dist_km = (joined["dist_m"] / 1000).fillna(MAX_DIST_KM).clip(0, MAX_DIST_KM)

    result = pd.DataFrame({
        "tile_id": tiles["tile_id"].values,
    })

    joined_aligned = result.merge(
        joined[["tile_id", "dist_m"] + ([name_col] if name_col else [])],
        on="tile_id",
        how="left",
    )

    result["nearest_waterbody_km"] = (joined_aligned["dist_m"] / 1000).round(3)
    result["nearest_waterbody_name"] = joined_aligned.get(name_col) if name_col else None

    # Log-inverse proximity score
    dist = result["nearest_waterbody_km"].fillna(MAX_DIST_KM).clip(0, MAX_DIST_KM)
    result["water_proximity"] = np.clip(
        100 * (1 - np.log1p(dist) / np.log1p(MAX_DIST_KM)), 0, 100
    ).round(2)

    return result


def compute_aquifer_productivity(
    tiles: gpd.GeoDataFrame,
    aquifer: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    Overlay tiles with GSI aquifer productivity polygons.
    Map productivity class to 0–100:
      high=90, moderate=65, low=35, negligible=10, none=0

    Returns DataFrame with tile_id, aquifer_productivity (0–100),
    aquifer_productivity_rating ('high'/'moderate'/'low'/'negligible'/'none').
    """
    CLASS_MAP = {"high": 90, "moderate": 65, "low": 35, "negligible": 10, "none": 0}

    # GSI aquifer type codes → productivity mapping
    # Rkc/Rkd = Regionally Important Karst → high
    # Rf = Regionally Important Fissured → high
    # Rg = Regionally Important Gravel → high
    # Ll = Locally Important (sand & gravel) → moderate
    # Lm = Locally Important (fissured) → moderate
    # Pl = Poor (generally unproductive) → low
    # Pu = Poor (generally unproductive except local zones) → negligible
    AQ_CODE_MAP = {
        "rkc": "high", "rkd": "high", "rka": "high",
        "rf": "high", "rg": "high",
        "ll": "moderate", "lm": "moderate", "lg": "moderate",
        "pl": "low", "pu": "negligible",
    }

    if aquifer is None or len(aquifer) == 0:
        print("  WARNING: No aquifer data. Setting productivity to 'none'.")
        return pd.DataFrame({
            "tile_id": tiles["tile_id"].values,
            "aquifer_productivity": 0.0,
            "aquifer_productivity_rating": "none",
        })

    # Reproject aquifer to EPSG:2157
    aquifer_itm = aquifer.to_crs(GRID_CRS_ITM)

    # Discover the productivity/aquifer type column
    prod_col = None
    for col in aquifer_itm.columns:
        col_lower = col.lower()
        if any(kw in col_lower for kw in ("aq_code", "aquifer_c", "rock_code", "aq_type")):
            prod_col = col
            break
    if prod_col is None:
        for col in aquifer_itm.columns:
            col_lower = col.lower()
            if any(kw in col_lower for kw in ("prod", "class", "type", "aquifer")):
                if col.lower() != "geometry":
                    prod_col = col
                    break
    if prod_col is None:
        # Last resort: use first non-geometry text column
        for col in aquifer_itm.columns:
            if col.lower() != "geometry" and aquifer_itm[col].dtype == object:
                prod_col = col
                break

    if prod_col is None:
        print(f"  WARNING: Could not find aquifer classification column. "
              f"Columns: {list(aquifer_itm.columns)}")
        return pd.DataFrame({
            "tile_id": tiles["tile_id"].values,
            "aquifer_productivity": 0.0,
            "aquifer_productivity_rating": "none",
        })

    print(f"  Using aquifer classification column: {prod_col}")
    print(f"  Sample values: {aquifer_itm[prod_col].value_counts().head(10).to_dict()}")

    # Classify aquifer productivity from code
    def _classify(val):
        if pd.isna(val):
            return "none"
        val_str = str(val).strip().lower()
        # Direct class match
        if val_str in CLASS_MAP:
            return val_str
        # Aquifer code match
        if val_str in AQ_CODE_MAP:
            return AQ_CODE_MAP[val_str]
        # Partial match on keywords
        if "high" in val_str or "regionally" in val_str or val_str.startswith("r"):
            return "high"
        if "moderate" in val_str or "locally" in val_str or val_str.startswith("l"):
            return "moderate"
        if "low" in val_str or "poor" in val_str or val_str.startswith("p"):
            return "low"
        if "negligible" in val_str:
            return "negligible"
        return "none"

    aquifer_itm["_prod_class"] = aquifer_itm[prod_col].apply(_classify)

    # Spatial majority join: for each tile, which aquifer class covers the most area?
    # Use overlay to compute intersection areas
    geom_col = tiles.geometry.name  # 'geom' from PostGIS, not 'geometry'
    tiles_simple = tiles[["tile_id", geom_col]].copy()
    if geom_col != "geometry":
        tiles_simple = tiles_simple.rename_geometry("geometry")

    aq_geom_col = aquifer_itm.geometry.name
    aquifer_prep = aquifer_itm[[aq_geom_col, "_prod_class"]].copy()
    if aq_geom_col != "geometry":
        aquifer_prep = aquifer_prep.rename_geometry("geometry")

    try:
        overlay = gpd.overlay(tiles_simple, aquifer_prep, how="intersection")
    except Exception as e:
        print(f"  WARNING: Overlay failed ({e}). Using centroid spatial join instead.")
        # Fallback: point-in-polygon join with tile centroids
        centroids = gpd.GeoDataFrame(
            {"tile_id": tiles["tile_id"].values},
            geometry=tiles.geometry.centroid,
            crs=GRID_CRS_ITM,
        )
        joined = gpd.sjoin(centroids, aquifer_prep, how="left", predicate="within")
        joined = joined.drop_duplicates(subset="tile_id", keep="first")

        result = pd.DataFrame({"tile_id": tiles["tile_id"].values})
        result = result.merge(joined[["tile_id", "_prod_class"]], on="tile_id", how="left")
        result["_prod_class"] = result["_prod_class"].fillna("none")
        result["aquifer_productivity_rating"] = result["_prod_class"]
        result["aquifer_productivity"] = result["_prod_class"].map(CLASS_MAP).fillna(0).round(2)
        return result[["tile_id", "aquifer_productivity", "aquifer_productivity_rating"]]

    overlay["_area"] = overlay.geometry.area

    # For each tile, find the class with the most area
    grouped = overlay.groupby(["tile_id", "_prod_class"])["_area"].sum().reset_index()
    idx_max = grouped.groupby("tile_id")["_area"].idxmax()
    majority = grouped.loc[idx_max][["tile_id", "_prod_class"]]

    result = pd.DataFrame({"tile_id": tiles["tile_id"].values})
    result = result.merge(majority, on="tile_id", how="left")
    result["_prod_class"] = result["_prod_class"].fillna("none")
    result["aquifer_productivity_rating"] = result["_prod_class"]
    result["aquifer_productivity"] = result["_prod_class"].map(CLASS_MAP).fillna(0).round(2)

    return result[["tile_id", "aquifer_productivity", "aquifer_productivity_rating"]]


def compute_cooling_scores(
    temp_series: pd.Series,
    rainfall_series: pd.Series,
    water_df: pd.DataFrame,
    aquifer_df: pd.DataFrame,
    free_cooling_df: pd.Series,
) -> pd.DataFrame:
    """
    Compose cooling_scores. Weights:
      40% temperature (normalised, inverted — lower = better)
      35% water_proximity (already 0–100)
      25% rainfall (min-max normalised to 0–100, higher = better)
    NOTE: store temperature as raw °C here; inversion is in Martin SQL.
    """
    # Build merged DataFrame
    tile_ids = temp_series.index

    # Fill NaN with median for graceful degradation
    temp = temp_series.fillna(temp_series.median())
    rain = rainfall_series.reindex(tile_ids).fillna(rainfall_series.median())

    # Min-max normalise temperature and rainfall across all tiles
    temp_range = temp.max() - temp.min()
    rain_range = rain.max() - rain.min()

    if temp_range > 0:
        temp_norm = 100 * (temp - temp.min()) / temp_range
    else:
        temp_norm = pd.Series(50.0, index=temp.index)

    if rain_range > 0:
        rain_norm = 100 * (rain - rain.min()) / rain_range
    else:
        rain_norm = pd.Series(50.0, index=rain.index)

    # Water proximity — align on tile_id
    water_aligned = water_df.set_index("tile_id").reindex(tile_ids)
    water_prox = water_aligned["water_proximity"].fillna(0)

    # Composite score (temperature inverted: lower temp = better score)
    score = (
        0.40 * (100 - temp_norm)
        + 0.35 * water_prox
        + 0.25 * rain_norm
    )
    score = score.clip(0, 100).round(2)

    # Aquifer data — align
    aquifer_aligned = aquifer_df.set_index("tile_id").reindex(tile_ids)

    # Free cooling hours — align
    free_hours = free_cooling_df.reindex(tile_ids)

    # Hydrometric station data will be joined later via pins (not per-tile)
    # For now, we set nearest_hydrometric columns to NULL
    result = pd.DataFrame({
        "tile_id": tile_ids,
        "score": score.values,
        "temperature": temp.round(2).values,
        "water_proximity": water_prox.round(2).values,
        "rainfall": rain.round(2).values,
        "aquifer_productivity": aquifer_aligned["aquifer_productivity"].fillna(0).round(2).values,
        "free_cooling_hours": free_hours.values,
        "nearest_waterbody_name": water_aligned["nearest_waterbody_name"].values,
        "nearest_waterbody_km": water_aligned["nearest_waterbody_km"].values,
        "nearest_hydrometric_station_name": None,
        "nearest_hydrometric_flow_m3s": None,
        "aquifer_productivity_rating": aquifer_aligned["aquifer_productivity_rating"].fillna("none").values,
    })

    return result.reset_index(drop=True)


def _assign_nearest_hydro(df: pd.DataFrame, tiles: gpd.GeoDataFrame,
                          hydro: gpd.GeoDataFrame) -> pd.DataFrame:
    """Assign nearest hydrometric station name + flow to each tile."""
    if hydro is None or len(hydro) == 0:
        return df

    hydro_itm = hydro.to_crs(GRID_CRS_ITM)

    centroids = gpd.GeoDataFrame(
        {"tile_id": tiles["tile_id"].values},
        geometry=tiles.geometry.centroid,
        crs=GRID_CRS_ITM,
    )

    name_col = "name" if "name" in hydro_itm.columns else None
    flow_col = "mean_flow_m3s" if "mean_flow_m3s" in hydro_itm.columns else None

    keep = ["geometry"]
    if name_col:
        keep.append(name_col)
    if flow_col:
        keep.append(flow_col)

    hydro_clean = hydro_itm[keep].copy().reset_index(drop=True)
    hydro_clean = hydro_clean[hydro_clean.geometry.notna()].reset_index(drop=True)

    if len(hydro_clean) == 0:
        return df

    joined = gpd.sjoin_nearest(centroids, hydro_clean, how="left", distance_col="dist_m")
    joined = joined.drop_duplicates(subset="tile_id", keep="first")

    merged = df.merge(
        joined[["tile_id"] + ([name_col] if name_col else []) + ([flow_col] if flow_col else [])],
        on="tile_id",
        how="left",
        suffixes=("", "_hydro"),
    )

    if name_col and name_col in merged.columns:
        df["nearest_hydrometric_station_name"] = merged[name_col]
    if flow_col and flow_col in merged.columns:
        df["nearest_hydrometric_flow_m3s"] = merged[flow_col]

    return df


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


def upsert_cooling_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """Upsert cooling_scores. Returns row count."""
    sql = """
        INSERT INTO cooling_scores (
            tile_id, score, temperature, water_proximity, rainfall,
            aquifer_productivity, free_cooling_hours,
            nearest_waterbody_name, nearest_waterbody_km,
            nearest_hydrometric_station_name, nearest_hydrometric_flow_m3s,
            aquifer_productivity_rating
        ) VALUES %s
        ON CONFLICT (tile_id) DO UPDATE SET
            score                            = EXCLUDED.score,
            temperature                      = EXCLUDED.temperature,
            water_proximity                  = EXCLUDED.water_proximity,
            rainfall                         = EXCLUDED.rainfall,
            aquifer_productivity             = EXCLUDED.aquifer_productivity,
            free_cooling_hours               = EXCLUDED.free_cooling_hours,
            nearest_waterbody_name           = EXCLUDED.nearest_waterbody_name,
            nearest_waterbody_km             = EXCLUDED.nearest_waterbody_km,
            nearest_hydrometric_station_name = EXCLUDED.nearest_hydrometric_station_name,
            nearest_hydrometric_flow_m3s     = EXCLUDED.nearest_hydrometric_flow_m3s,
            aquifer_productivity_rating      = EXCLUDED.aquifer_productivity_rating
    """

    cols = [
        "tile_id", "score", "temperature", "water_proximity", "rainfall",
        "aquifer_productivity", "free_cooling_hours",
        "nearest_waterbody_name", "nearest_waterbody_km",
        "nearest_hydrometric_station_name", "nearest_hydrometric_flow_m3s",
        "aquifer_productivity_rating",
    ]

    rows = [tuple(_to_py(row[c]) for c in cols) for _, row in df.iterrows()]

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        batch_size = 500
        for i in tqdm(range(0, len(rows), batch_size), desc="Upserting cooling_scores"):
            execute_values(cur, sql, rows[i : i + batch_size])
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
        pg_conn.close()

    return len(rows)


def upsert_pins_cooling(
    hydro_stations: gpd.GeoDataFrame,
    rivers_lakes: gpd.GeoDataFrame,
    engine: sqlalchemy.Engine,
) -> int:
    """
    Load cooling pins:
      - OPW hydrometric stations (type='hydrometric_station')
      - Major rivers/lakes centroids from EPA/OSM (type='waterbody')
      - Met Éireann synoptic stations (type='met_station', hardcoded coords)
    """
    pin_rows = []

    # ── Hydrometric stations ──────────────────────────────────────────────────
    if hydro_stations is not None and len(hydro_stations) > 0:
        hydro_wgs84 = hydro_stations.to_crs(GRID_CRS_WGS84) if hydro_stations.crs != GRID_CRS_WGS84 else hydro_stations

        for _, row in hydro_wgs84.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type != "Point":
                geom = geom.centroid

            station_id = str(row.get("station_id", "") or row.get("ref", "") or "")
            flow = row.get("mean_flow_m3s")

            pin_rows.append({
                "lng": geom.x, "lat": geom.y,
                "name": row.get("name", "Hydrometric Station"),
                "type": "hydrometric_station",
                "station_id": station_id or None,
                "mean_flow_m3s": float(flow) if flow and not pd.isna(flow) else None,
                "waterbody_type": None,
            })

    # ── Major waterbodies ─────────────────────────────────────────────────────
    if rivers_lakes is not None and len(rivers_lakes) > 0:
        rivers_wgs84 = rivers_lakes.to_crs(GRID_CRS_WGS84) if rivers_lakes.crs != GRID_CRS_WGS84 else rivers_lakes

        # Filter to named waterbodies only and deduplicate by name
        named = rivers_wgs84[rivers_wgs84["name"].notna()].copy() if "name" in rivers_wgs84.columns else rivers_wgs84.iloc[0:0]

        # Deduplicate: keep one representative per waterbody name
        if len(named) > 0:
            # Group by name, take centroid of all segments with same name
            unique_names = named["name"].unique()
            water_type_col = "water_type" if "water_type" in named.columns else None

            for wb_name in unique_names:
                subset = named[named["name"] == wb_name]
                # Use centroid of the union of all geometries with this name
                try:
                    union_geom = subset.geometry.union_all()
                    centroid = union_geom.centroid
                except Exception:
                    centroid = subset.geometry.iloc[0].centroid

                wtype = None
                if water_type_col:
                    wtype = subset[water_type_col].mode().iloc[0] if len(subset[water_type_col].mode()) > 0 else "river"

                pin_rows.append({
                    "lng": centroid.x, "lat": centroid.y,
                    "name": wb_name,
                    "type": "waterbody",
                    "station_id": None,
                    "mean_flow_m3s": None,
                    "waterbody_type": wtype,
                })

    # ── Met Éireann synoptic stations (hardcoded major stations) ──────────────
    # Source: Met Éireann station network (met.ie/climate/available-data)
    MET_STATIONS = [
        {"name": "Malin Head", "lat": 55.3717, "lng": -7.3392},
        {"name": "Belmullet", "lat": 54.2271, "lng": -10.0059},
        {"name": "Claremorris", "lat": 53.7228, "lng": -8.9903},
        {"name": "Shannon Airport", "lat": 52.7019, "lng": -8.9247},
        {"name": "Valentia Observatory", "lat": 51.9381, "lng": -10.2397},
        {"name": "Cork Airport", "lat": 51.8413, "lng": -8.4911},
        {"name": "Casement Aerodrome", "lat": 53.3017, "lng": -6.4428},
        {"name": "Dublin Airport", "lat": 53.4264, "lng": -6.2499},
        {"name": "Birr", "lat": 53.0914, "lng": -7.8833},
        {"name": "Mullingar", "lat": 53.5333, "lng": -7.3500},
        {"name": "Knock Airport", "lat": 53.9103, "lng": -8.8186},
        {"name": "Rosslare", "lat": 52.2583, "lng": -6.3308},
        {"name": "Kilkenny", "lat": 52.6706, "lng": -7.2647},
        {"name": "Johnstown Castle", "lat": 52.2931, "lng": -6.4950},
        {"name": "Mount Dillon", "lat": 53.7258, "lng": -8.0272},
    ]

    for station in MET_STATIONS:
        pin_rows.append({
            "lng": station["lng"], "lat": station["lat"],
            "name": station["name"],
            "type": "met_station",
            "station_id": None,
            "mean_flow_m3s": None,
            "waterbody_type": None,
        })

    if not pin_rows:
        print("  No cooling pins to insert.")
        return 0

    # Delete existing cooling pins and re-insert (idempotent)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pins_cooling"))

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        execute_values(
            cur,
            """
            INSERT INTO pins_cooling (geom, name, type, station_id, mean_flow_m3s, waterbody_type)
            VALUES %s
            """,
            [
                (
                    f"SRID=4326;POINT({r['lng']} {r['lat']})",
                    r["name"],
                    r["type"],
                    r["station_id"],
                    r["mean_flow_m3s"],
                    r["waterbody_type"],
                )
                for r in pin_rows
            ],
            template="(ST_GeomFromEWKT(%s), %s, %s, %s, %s, %s)",
        )

        # Assign tile_id via ST_Within spatial join
        cur.execute("""
            UPDATE pins_cooling p
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
    temp_series: pd.Series,
    rainfall_series: pd.Series,
    engine: sqlalchemy.Engine,
) -> None:
    """
    Write min/max to metric_ranges table for temperature (°C) and rainfall (mm/yr).
    Used by tile_heatmap SQL function for colour ramp normalisation.
    """
    ranges = [
        ("cooling", "temperature", float(temp_series.min()), float(temp_series.max()), "°C"),
        ("cooling", "rainfall", float(rainfall_series.min()), float(rainfall_series.max()), "mm/yr"),
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
    print(f"  Metric ranges written: temperature [{ranges[0][2]:.1f}–{ranges[0][3]:.1f} °C], "
          f"rainfall [{ranges[1][2]:.0f}–{ranges[1][3]:.0f} mm/yr]")


def main():
    """
    Cooling ingest pipeline:
      1. Load tiles
      2. Extract temperature + rainfall from climate grids
      3. Compute free cooling hours estimate
      4. Compute water proximity from river/lake network
      5. Compute aquifer productivity from GSI
      6. Compute composite cooling scores
      7. Assign nearest hydrometric station
      8. Upsert cooling_scores + pins_cooling
      9. Write metric_ranges for temperature + rainfall

    Run AFTER: grid/generate_grid.py
    Run BEFORE: overall/compute_composite.py
    """
    print("=" * 60)
    print("Starting cooling ingest...")
    print("=" * 60)

    # ── Check source files exist ──────────────────────────────────────────────
    required = [MET_EIREANN_TEMP_FILE, MET_EIREANN_RAIN_FILE, EPA_RIVERS_FILE]
    optional = [OPW_HYDRO_FILE, GSI_AQUIFER_FILE]

    missing_required = [p for p in required if not p.exists()]
    if missing_required:
        for p in missing_required:
            print(f"  ERROR: missing required source file: {p}")
        print("\nRun: python cooling/download_sources.py")
        raise SystemExit(1)

    missing_optional = [p for p in optional if not p.exists()]
    for p in missing_optional:
        print(f"  WARNING: optional source file missing: {p}")

    engine = sqlalchemy.create_engine(DB_URL)

    # ── Step 1: Load tiles ────────────────────────────────────────────────────
    print("\n[1/9] Loading tiles from database...")
    tiles = load_tiles(engine)
    print(f"  Loaded {len(tiles)} tiles")

    # ── Step 2: Temperature ───────────────────────────────────────────────────
    print(f"\n[2/9] Extracting temperature from raster...")
    temp_stats = extract_temperature_stats(tiles)
    print(f"  Temperature: min={temp_stats.min():.1f}, max={temp_stats.max():.1f}, "
          f"mean={temp_stats.mean():.1f} °C  (NaN: {temp_stats.isna().sum()})")

    # ── Step 3: Rainfall ──────────────────────────────────────────────────────
    print(f"\n[3/9] Extracting rainfall from raster...")
    rain_stats = extract_rainfall_stats(tiles)
    print(f"  Rainfall: min={rain_stats.min():.0f}, max={rain_stats.max():.0f}, "
          f"mean={rain_stats.mean():.0f} mm/yr  (NaN: {rain_stats.isna().sum()})")

    # ── Step 4: Free cooling hours ────────────────────────────────────────────
    print(f"\n[4/9] Computing free cooling hours...")
    free_cooling = compute_free_cooling_hours(temp_stats)
    print(f"  Free cooling hours: min={free_cooling.min()}, max={free_cooling.max()}, "
          f"mean={free_cooling.mean():.0f} hrs/yr")

    # ── Step 5: Water proximity ───────────────────────────────────────────────
    print(f"\n[5/9] Loading river/lake network and computing water proximity...")
    rivers = gpd.read_file(str(EPA_RIVERS_FILE))
    print(f"  Loaded {len(rivers)} river/lake features")
    water_df = compute_water_proximity(tiles, rivers)
    print(f"  Water proximity: avg={water_df['water_proximity'].mean():.1f}, "
          f"max dist={water_df['nearest_waterbody_km'].max():.1f} km")

    # ── Step 6: Aquifer productivity ──────────────────────────────────────────
    print(f"\n[6/9] Computing aquifer productivity...")
    if GSI_AQUIFER_FILE.exists():
        aquifer = gpd.read_file(str(GSI_AQUIFER_FILE))
        # Fix invalid geometries
        aquifer["geometry"] = aquifer.geometry.buffer(0)
        print(f"  Loaded {len(aquifer)} aquifer polygons")
    else:
        aquifer = None
        print("  Skipping (no aquifer data file)")

    aquifer_df = compute_aquifer_productivity(tiles, aquifer)
    if len(aquifer_df) > 0:
        rating_counts = aquifer_df["aquifer_productivity_rating"].value_counts().to_dict()
        print(f"  Aquifer ratings: {rating_counts}")

    # ── Step 7: Compute cooling scores ────────────────────────────────────────
    print(f"\n[7/9] Computing composite cooling scores...")
    scores_df = compute_cooling_scores(temp_stats, rain_stats, water_df, aquifer_df, free_cooling)
    print(f"  Score: min={scores_df['score'].min():.2f}, max={scores_df['score'].max():.2f}, "
          f"mean={scores_df['score'].mean():.2f}")

    # ── Step 7.5: Assign nearest hydrometric station ──────────────────────────
    if OPW_HYDRO_FILE.exists():
        print(f"\n[7.5/9] Assigning nearest hydrometric station...")
        hydro = gpd.read_file(str(OPW_HYDRO_FILE))
        print(f"  Loaded {len(hydro)} hydrometric stations")
        scores_df = _assign_nearest_hydro(scores_df, tiles, hydro)
        hydro_assigned = scores_df["nearest_hydrometric_station_name"].notna().sum()
        print(f"  Assigned station to {hydro_assigned} tiles")
    else:
        hydro = None
        print(f"\n[7.5/9] Skipping hydrometric station assignment (no data file)")

    # ── Step 8: Upsert cooling_scores ─────────────────────────────────────────
    print(f"\n[8/9] Upserting cooling_scores...")
    n = upsert_cooling_scores(scores_df, engine)
    print(f"  Upserted {n} rows into cooling_scores")

    # ── Step 8.5: Metric ranges ───────────────────────────────────────────────
    temp_valid = temp_stats.dropna()
    rain_valid = rain_stats.dropna()
    print(f"\n[8.5/9] Writing metric ranges...")
    write_metric_ranges(temp_valid, rain_valid, engine)

    # ── Step 9: Upsert pins_cooling ───────────────────────────────────────────
    print(f"\n[9/9] Upserting cooling pins...")
    n_pins = upsert_pins_cooling(hydro, rivers, engine)
    print(f"  Inserted {n_pins} cooling pins")

    print("\n" + "=" * 60)
    print(f"Cooling ingest complete: {n} tiles scored, {n_pins} pins inserted")
    print("Next step: restart Martin to serve updated tiles:")
    print("  docker compose restart martin")
    print("=" * 60)


if __name__ == "__main__":
    main()
