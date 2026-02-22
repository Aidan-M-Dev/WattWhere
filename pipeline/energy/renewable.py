"""
FILE: pipeline/energy/renewable.py
Role: Fetch renewable generation data from SEAI + known generators, compute
      per-tile renewable penetration scores, and update energy_scores.
Agent boundary: Pipeline — Energy sort (§5.2, §8)
Dependencies:
  - tiles table populated
  - energy_scores rows exist (run ingest.py first — this script UPDATEs rows)
  - Internet access for SEAI wind farm CSV download
Output:
  - Updates energy_scores: renewable_pct, renewable_score, renewable_capacity_mw, fossil_capacity_mw
  - Writes metric_ranges row for 'renewable_pct'
How to test:
  python energy/renewable.py
  psql $DATABASE_URL -c "SELECT AVG(renewable_pct), AVG(renewable_score) FROM energy_scores;"

Data sources:
  - SEAI Wind Farm CSV (seaiopendata.blob.core.windows.net) — ~300 connected wind farms with ITM coords + capacity
  - Known major thermal, hydro, and solar generators (hardcoded — stable, public knowledge)

ARCHITECTURE RULES:
  - Store raw renewable_pct in energy_scores (not pre-normalised).
  - renewable_score = renewable_pct (linear 0–100).
  - Write metric_ranges for 'renewable_pct'.
"""

import sys
from pathlib import Path
from io import StringIO
import numpy as np
import pandas as pd
import geopandas as gpd
import sqlalchemy
from sqlalchemy import text
from shapely.geometry import Point
from pyproj import Transformer
import requests
from tqdm import tqdm
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_URL, GRID_CRS_ITM, GRID_CRS_WGS84

# ── Constants ─────────────────────────────────────────────────
SEAI_WIND_CSV_URL = "https://seaiopendata.blob.core.windows.net/wind/WindFarmsConnectedJune2022.csv"
SEARCH_RADIUS_M = 25_000  # 25 km radius for generator aggregation

# ── Known major non-wind generators in Ireland ────────────────
# Sources: EirGrid Generation Capacity Statement, ESB, public records
# Format: (name, fuel, capacity_mw, easting_itm, northing_itm)
KNOWN_GENERATORS = [
    # Major thermal / gas
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
    # Hydro
    ("Ardnacrusha", "hydro", 86, 557500, 661600),
    ("Turlough Hill", "hydro", 292, 707200, 699000),
    ("Erne (Ballyshannon)", "hydro", 65, 587300, 858600),
    ("Liffey Scheme (Poulaphouca)", "hydro", 30, 694500, 714200),
    ("Lee Scheme (Inniscarra/Carrigadrohid)", "hydro", 27, 548600, 572400),
    ("Cathaleens Falls", "hydro", 45, 588000, 860000),
    # Solar (known larger installations)
    ("Lisheen Solar Farm", "solar", 30, 525200, 667500),
    ("Millvale Solar Farm", "solar", 21, 650000, 680000),
    ("Hortland Solar Farm", "solar", 25, 696000, 738000),
]

RENEWABLE_FUELS = {"wind", "solar", "hydro", "biomass", "biogas"}


def fetch_seai_wind_farms() -> pd.DataFrame:
    """
    Download SEAI connected wind farm CSV and parse into a DataFrame.
    Returns DataFrame with columns: name, fuel, capacity_mw, easting, northing
    """
    print(f"  Fetching SEAI wind farm data from {SEAI_WIND_CSV_URL}...")
    resp = requests.get(SEAI_WIND_CSV_URL, timeout=30)
    resp.raise_for_status()

    df = pd.read_csv(StringIO(resp.text))

    # Normalise column names
    col_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if "windfarm" in cl or "name" in cl:
            col_map[c] = "name"
        elif "mec" in cl and "mw" in cl:
            col_map[c] = "capacity_mw"
        elif "installed" in cl and "capacity" in cl:
            col_map[c] = "installed_mw"
        elif "nat_grid_e" in cl or "easting" in cl:
            col_map[c] = "easting"
        elif "nat_grid_n" in cl or "northing" in cl:
            col_map[c] = "northing"
        elif "county" in cl:
            col_map[c] = "county"
        elif "status" in cl:
            col_map[c] = "status"

    df = df.rename(columns=col_map)

    # Filter to connected only
    if "status" in df.columns:
        df = df[df["status"].str.lower().str.contains("connect", na=False)]

    # Use MEC (Maximum Export Capacity) as primary, fall back to installed
    if "capacity_mw" not in df.columns and "installed_mw" in df.columns:
        df["capacity_mw"] = df["installed_mw"]

    # Parse capacity — handle text like "6.45"
    df["capacity_mw"] = pd.to_numeric(df["capacity_mw"], errors="coerce")
    df["easting"] = pd.to_numeric(df["easting"], errors="coerce")
    df["northing"] = pd.to_numeric(df["northing"], errors="coerce")

    # Drop rows without coordinates or capacity
    df = df.dropna(subset=["easting", "northing", "capacity_mw"])
    df = df[df["capacity_mw"] > 0]

    result = pd.DataFrame({
        "name": df["name"].values,
        "fuel": "wind",
        "capacity_mw": df["capacity_mw"].values,
        "easting": df["easting"].values,
        "northing": df["northing"].values,
    })

    print(f"  Loaded {len(result)} connected wind farms, total {result['capacity_mw'].sum():.0f} MW")
    return result


def build_generator_gdf() -> gpd.GeoDataFrame:
    """
    Combine SEAI wind farms with known thermal/hydro/solar generators
    into a single GeoDataFrame in EPSG:2157.
    """
    # Fetch wind farms from SEAI API
    wind_df = fetch_seai_wind_farms()

    # Build known generators dataframe
    known_df = pd.DataFrame(KNOWN_GENERATORS, columns=["name", "fuel", "capacity_mw", "easting", "northing"])

    # Combine
    all_gen = pd.concat([wind_df, known_df], ignore_index=True)

    # Create geometry from ITM coordinates
    geometry = [Point(row.easting, row.northing) for _, row in all_gen.iterrows()]
    gdf = gpd.GeoDataFrame(all_gen, geometry=geometry, crs=GRID_CRS_ITM)

    gdf["is_renewable"] = gdf["fuel"].isin(RENEWABLE_FUELS)

    total_mw = gdf["capacity_mw"].sum()
    renewable_mw = gdf.loc[gdf["is_renewable"], "capacity_mw"].sum()
    print(f"  Total generators: {len(gdf)} ({total_mw:.0f} MW)")
    print(f"  Renewable: {gdf['is_renewable'].sum()} ({renewable_mw:.0f} MW, "
          f"{100*renewable_mw/total_mw:.1f}% nationally)")

    return gdf


def load_tile_centroids(engine: sqlalchemy.Engine) -> gpd.GeoDataFrame:
    """Load tile centroids in EPSG:2157 for spatial proximity analysis."""
    tiles = gpd.read_postgis(
        "SELECT tile_id, centroid FROM tiles",
        engine,
        geom_col="centroid",
        crs=GRID_CRS_WGS84,
    )
    return tiles.to_crs(GRID_CRS_ITM)


def compute_renewable_scores(
    tiles: gpd.GeoDataFrame,
    generators: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """
    For each tile, find all generators within SEARCH_RADIUS_M, sum renewable
    vs total capacity, and compute renewable_pct and renewable_score.

    Returns DataFrame with: tile_id, renewable_pct, renewable_score,
    renewable_capacity_mw, fossil_capacity_mw
    """
    print(f"  Computing renewable penetration (radius={SEARCH_RADIUS_M/1000:.0f} km)...")

    # Buffer tile centroids by search radius
    tile_ids = tiles["tile_id"].values
    tile_geoms = tiles.geometry.values

    results = []

    for i in tqdm(range(len(tile_ids)), desc="  Scoring tiles"):
        tid = tile_ids[i]
        centroid = tile_geoms[i]
        buffer = centroid.buffer(SEARCH_RADIUS_M)

        # Find generators within buffer
        mask = generators.geometry.within(buffer)
        nearby = generators[mask]

        if len(nearby) == 0:
            # No generators within radius — use national average as fallback
            results.append((tid, None, None, None, None))
            continue

        total_mw = nearby["capacity_mw"].sum()
        renewable_mw = nearby.loc[nearby["is_renewable"], "capacity_mw"].sum()
        fossil_mw = total_mw - renewable_mw

        pct = (renewable_mw / total_mw * 100) if total_mw > 0 else 0.0
        score = min(int(round(pct)), 100)

        results.append((tid, round(pct, 1), score, round(renewable_mw, 1), round(fossil_mw, 1)))

    df = pd.DataFrame(results, columns=[
        "tile_id", "renewable_pct", "renewable_score",
        "renewable_capacity_mw", "fossil_capacity_mw",
    ])

    # Fill tiles with no nearby generators using national average
    national_renewable = generators.loc[generators["is_renewable"], "capacity_mw"].sum()
    national_total = generators["capacity_mw"].sum()
    national_pct = round(national_renewable / national_total * 100, 1) if national_total > 0 else 0

    null_mask = df["renewable_pct"].isna()
    if null_mask.any():
        print(f"  {null_mask.sum()} tiles had no generators within radius — using national avg ({national_pct}%)")
        df.loc[null_mask, "renewable_pct"] = national_pct
        df.loc[null_mask, "renewable_score"] = min(int(round(national_pct)), 100)
        df.loc[null_mask, "renewable_capacity_mw"] = 0.0
        df.loc[null_mask, "fossil_capacity_mw"] = 0.0

    scored = df["renewable_pct"].notna().sum()
    print(f"  Scored {scored} tiles: avg renewable {df['renewable_pct'].mean():.1f}%, "
          f"min {df['renewable_pct'].min():.1f}%, max {df['renewable_pct'].max():.1f}%")

    return df


def update_energy_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Update energy_scores with renewable columns.
    Also recomputes the composite energy score with the new 4-factor formula.
    """
    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()

        # First: add columns if they don't exist (idempotent for dev)
        for col_def in [
            "renewable_pct REAL",
            "renewable_score SMALLINT",
            "renewable_capacity_mw REAL",
            "fossil_capacity_mw REAL",
        ]:
            col_name = col_def.split()[0]
            cur.execute(f"""
                DO $$ BEGIN
                    ALTER TABLE energy_scores ADD COLUMN {col_def};
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """)

        # Update renewable columns
        rows = [
            (
                row["renewable_pct"],
                row["renewable_score"],
                row["renewable_capacity_mw"],
                row["fossil_capacity_mw"],
                row["tile_id"],
            )
            for _, row in df.iterrows()
            if row["renewable_pct"] is not None and not pd.isna(row["renewable_pct"])
        ]

        batch_size = 500
        for i in tqdm(range(0, len(rows), batch_size), desc="  Updating energy_scores"):
            batch = rows[i : i + batch_size]
            execute_values(
                cur,
                """
                UPDATE energy_scores AS es SET
                    renewable_pct          = data.renewable_pct,
                    renewable_score        = data.renewable_score,
                    renewable_capacity_mw  = data.renewable_capacity_mw,
                    fossil_capacity_mw     = data.fossil_capacity_mw
                FROM (VALUES %s) AS data(renewable_pct, renewable_score, renewable_capacity_mw, fossil_capacity_mw, tile_id)
                WHERE es.tile_id = data.tile_id
                """,
                batch,
            )

        # Recompute composite energy score with new 4-factor formula:
        # 0.30 * wind_norm + 0.25 * solar_norm + 0.25 * grid_proximity + 0.20 * renewable_score
        # wind_norm and solar_norm are min-max normalised from raw values
        print("  Recomputing composite energy scores with renewable weight...")
        cur.execute("""
            WITH ranges AS (
                SELECT
                    (SELECT min_val FROM metric_ranges WHERE sort = 'energy' AND metric = 'wind_speed_100m') AS wind_min,
                    (SELECT max_val FROM metric_ranges WHERE sort = 'energy' AND metric = 'wind_speed_100m') AS wind_max,
                    (SELECT min_val FROM metric_ranges WHERE sort = 'energy' AND metric = 'solar_ghi') AS solar_min,
                    (SELECT max_val FROM metric_ranges WHERE sort = 'energy' AND metric = 'solar_ghi') AS solar_max
            )
            UPDATE energy_scores es SET
                score = ROUND(LEAST(100, GREATEST(0,
                    0.30 * COALESCE((es.wind_speed_100m - r.wind_min) / NULLIF(r.wind_max - r.wind_min, 0) * 100, 50)
                  + 0.25 * COALESCE((es.solar_ghi - r.solar_min) / NULLIF(r.solar_max - r.solar_min, 0) * 100, 50)
                  + 0.25 * COALESCE(es.grid_proximity, 50)
                  + 0.20 * COALESCE(es.renewable_score, 50)
                ))::numeric, 2)
            FROM ranges r
            WHERE es.renewable_score IS NOT NULL
        """)

        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
        pg_conn.close()

    return len(rows)


def write_metric_ranges(df: pd.DataFrame, engine: sqlalchemy.Engine) -> None:
    """Write metric_ranges entry for renewable_pct."""
    min_val = float(df["renewable_pct"].min())
    max_val = float(df["renewable_pct"].max())

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
            {"sort": "energy", "metric": "renewable_pct", "min_val": min_val, "max_val": max_val, "unit": "%"},
        )
    print(f"  Metric ranges written: renewable_pct [{min_val:.1f}–{max_val:.1f}%]")


def main():
    """
    Renewable energy pipeline:
      1. Fetch wind farm data from SEAI API
      2. Combine with known thermal/hydro/solar generators
      3. Compute per-tile renewable penetration within 25 km radius
      4. Update energy_scores with renewable columns
      5. Recompute composite energy score with 4-factor formula
      6. Write metric_ranges for renewable_pct

    Run AFTER: energy/ingest.py (needs energy_scores rows to exist)
    Run BEFORE: overall/compute_composite.py
    """
    print("=" * 60)
    print("Starting renewable energy pipeline...")
    print("=" * 60)

    engine = sqlalchemy.create_engine(DB_URL)

    # Check energy_scores exists and has rows
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM energy_scores"))
        count = result.scalar()
        if count == 0:
            print("ERROR: energy_scores is empty. Run energy/ingest.py first.")
            raise SystemExit(1)
        print(f"  Found {count} existing energy_scores rows")

    # ── Step 1-2: Build generator dataset ──────────────────────────────────
    print("\n[1/5] Building generator dataset...")
    generators = build_generator_gdf()

    # ── Step 3: Load tile centroids ────────────────────────────────────────
    print("\n[2/5] Loading tile centroids...")
    tiles = load_tile_centroids(engine)
    print(f"  Loaded {len(tiles)} tiles")

    # ── Step 4: Compute renewable scores ───────────────────────────────────
    print("\n[3/5] Computing renewable penetration scores...")
    scores_df = compute_renewable_scores(tiles, generators)

    # ── Step 5: Update energy_scores ───────────────────────────────────────
    print("\n[4/5] Updating energy_scores...")
    n = update_energy_scores(scores_df, engine)
    print(f"  Updated {n} rows")

    # ── Step 6: Write metric ranges ────────────────────────────────────────
    print("\n[5/5] Writing metric ranges...")
    write_metric_ranges(scores_df, engine)

    print("\n" + "=" * 60)
    print(f"Renewable pipeline complete: {n} tiles scored")
    print("Next step: restart Martin to serve updated tiles:")
    print("  docker compose restart martin")
    print("=" * 60)


if __name__ == "__main__":
    main()
