"""
FILE: pipeline/seed_synthetic.py
Role: Populate all sort tables + metric_ranges with synthetic data for dev/testing.
Run: python seed_synthetic.py
Safe to re-run (TRUNCATE + INSERT each sort table).
Data is random but respects all DB CHECK constraints (0–100 ranges, etc.)

Requires tiles table to be populated first (run grid/generate_grid.py).
Uses numpy.random.seed(42) for reproducibility.
"""

import math
import sys
from pathlib import Path

import numpy as np
import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_URL, INEX_DUBLIN_COORDS, INEX_CORK_COORDS

np.random.seed(42)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(DB_URL)


def haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6371.0
    dlng = math.radians(lng2 - lng1)
    dlat = math.radians(lat2 - lat1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return 2.0 * R * math.asin(math.sqrt(min(a, 1.0)))


def get_tiles(conn):
    """Return list of (tile_id, centroid_lng, centroid_lat) for all tiles."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tile_id, ST_X(centroid), ST_Y(centroid) FROM tiles ORDER BY tile_id"
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Energy scores
# ---------------------------------------------------------------------------

def seed_energy(conn, tiles) -> dict:
    n = len(tiles)
    tile_ids = [t[0] for t in tiles]

    wind_100m = np.random.uniform(4.0, 12.0, n)
    solar_ghi = np.random.uniform(900.0, 1200.0, n)
    grid_proximity = np.random.uniform(0.0, 100.0, n)
    nearest_substation_km = np.random.uniform(0.5, 35.0, n)
    score = np.random.uniform(0.0, 100.0, n)

    records = []
    for i in range(n):
        w = float(wind_100m[i])
        sub_km = float(nearest_substation_km[i])
        records.append(
            (
                tile_ids[i],
                round(float(score[i]), 2),
                round(w * 0.85, 3),         # wind_speed_50m
                round(w, 3),                # wind_speed_100m
                round(w * 1.10, 3),         # wind_speed_150m
                round(float(solar_ghi[i]), 3),
                round(float(grid_proximity[i]), 2),
                None,                       # nearest_transmission_line_km
                round(sub_km, 3),
                None,                       # nearest_substation_name
                None,                       # nearest_substation_voltage
                sub_km > 20.0,              # grid_low_confidence
            )
        )

    with conn.cursor() as cur:
        cur.execute("TRUNCATE energy_scores")
        execute_values(
            cur,
            """
            INSERT INTO energy_scores (
                tile_id, score,
                wind_speed_50m, wind_speed_100m, wind_speed_150m,
                solar_ghi, grid_proximity,
                nearest_transmission_line_km, nearest_substation_km,
                nearest_substation_name, nearest_substation_voltage,
                grid_low_confidence
            ) VALUES %s
            """,
            records,
            page_size=1000,
        )
    conn.commit()
    print(f"  energy_scores:       {n:,} rows")
    return {"score": score}


# ---------------------------------------------------------------------------
# Environment scores
# ---------------------------------------------------------------------------

def seed_environment(conn, tiles) -> dict:
    n = len(tiles)
    tile_ids = [t[0] for t in tiles]

    rng = np.random.random(n)
    sac_mask = rng < 0.05                            # ~5% SAC hard exclusions
    flood_mask = (rng >= 0.05) & (rng < 0.08)       # ~3% flood hard exclusions
    hard_mask = sac_mask | flood_mask

    # Normal tiles: score 40–100; excluded tiles: score 0
    base_score = np.random.uniform(40.0, 100.0, n)
    score = np.where(hard_mask, 0.0, base_score)

    designation_overlap = np.where(hard_mask, 0.0, np.random.uniform(40.0, 100.0, n))
    flood_risk = np.where(flood_mask, 0.0, np.random.uniform(40.0, 100.0, n))
    landslide_risk = np.random.uniform(40.0, 100.0, n)

    susceptibility = np.random.choice(
        ["none", "low", "medium", "high"],
        size=n,
        p=[0.60, 0.25, 0.12, 0.03],
    )

    records = []
    for i in range(n):
        if sac_mask[i]:
            exc_reason = "SAC overlap"
        elif flood_mask[i]:
            exc_reason = "Current flood zone"
        else:
            exc_reason = None

        records.append(
            (
                tile_ids[i],
                round(float(score[i]), 2),
                round(float(designation_overlap[i]), 2),
                round(float(flood_risk[i]), 2),
                round(float(landslide_risk[i]), 2),
                bool(hard_mask[i]),
                exc_reason,
                bool(sac_mask[i]),      # intersects_sac
                False,                  # intersects_spa
                False,                  # intersects_nha
                False,                  # intersects_pnha
                bool(flood_mask[i]),    # intersects_current_flood
                False,                  # intersects_future_flood
                str(susceptibility[i]),
            )
        )

    with conn.cursor() as cur:
        cur.execute("TRUNCATE environment_scores")
        execute_values(
            cur,
            """
            INSERT INTO environment_scores (
                tile_id, score,
                designation_overlap, flood_risk, landslide_risk,
                has_hard_exclusion, exclusion_reason,
                intersects_sac, intersects_spa, intersects_nha, intersects_pnha,
                intersects_current_flood, intersects_future_flood,
                landslide_susceptibility
            ) VALUES %s
            """,
            records,
            page_size=1000,
        )
    conn.commit()
    print(
        f"  environment_scores:  {n:,} rows  "
        f"({int(sac_mask.sum())} SAC, {int(flood_mask.sum())} flood exclusions)"
    )
    return {"score": score, "hard_mask": hard_mask}


# ---------------------------------------------------------------------------
# Cooling scores
# ---------------------------------------------------------------------------

def seed_cooling(conn, tiles) -> dict:
    n = len(tiles)
    tile_ids = [t[0] for t in tiles]

    temperature = np.random.uniform(8.5, 13.5, n)
    rainfall = np.random.uniform(700.0, 2500.0, n)
    water_proximity = np.random.uniform(20.0, 100.0, n)
    aquifer_productivity = np.random.uniform(10.0, 90.0, n)
    # Rough free-cooling hours estimate: hours/yr below 18°C
    free_cooling_hours = np.array(
        [int(8760 * (14.0 - float(t)) / 14.0) for t in temperature], dtype=float
    )
    score = np.random.uniform(40.0, 90.0, n)

    records = [
        (
            tile_ids[i],
            round(float(score[i]), 2),
            round(float(temperature[i]), 2),
            round(float(water_proximity[i]), 2),
            round(float(rainfall[i]), 2),
            round(float(aquifer_productivity[i]), 2),
            float(free_cooling_hours[i]),
            None,   # nearest_waterbody_name
            None,   # nearest_waterbody_km
            None,   # nearest_hydrometric_station_name
            None,   # nearest_hydrometric_flow_m3s
            None,   # aquifer_productivity_rating
        )
        for i in range(n)
    ]

    with conn.cursor() as cur:
        cur.execute("TRUNCATE cooling_scores")
        execute_values(
            cur,
            """
            INSERT INTO cooling_scores (
                tile_id, score,
                temperature, water_proximity, rainfall, aquifer_productivity,
                free_cooling_hours,
                nearest_waterbody_name, nearest_waterbody_km,
                nearest_hydrometric_station_name, nearest_hydrometric_flow_m3s,
                aquifer_productivity_rating
            ) VALUES %s
            """,
            records,
            page_size=1000,
        )
    conn.commit()
    print(f"  cooling_scores:      {n:,} rows")
    return {"score": score}


# ---------------------------------------------------------------------------
# Connectivity scores
# ---------------------------------------------------------------------------

def seed_connectivity(conn, tiles) -> dict:
    n = len(tiles)
    tile_ids = [t[0] for t in tiles]

    inex_dub_lng, inex_dub_lat = INEX_DUBLIN_COORDS
    inex_cor_lng, inex_cor_lat = INEX_CORK_COORDS

    inex_dublin_km = np.array(
        [haversine_km(t[1], t[2], inex_dub_lng, inex_dub_lat) for t in tiles]
    )
    inex_cork_km = np.array(
        [haversine_km(t[1], t[2], inex_cor_lng, inex_cor_lat) for t in tiles]
    )

    # Inverse log-distance score — 0 km → 100, ~300 km → 0
    min_ix_km = np.minimum(inex_dublin_km, inex_cork_km)
    ix_distance = np.clip(
        100.0 * (1.0 - np.log(1.0 + min_ix_km) / math.log(301.0)),
        0.0,
        100.0,
    )

    broadband = np.random.uniform(20.0, 95.0, n)
    road_access = np.random.uniform(30.0, 95.0, n)
    score = np.random.uniform(30.0, 90.0, n)

    records = [
        (
            tile_ids[i],
            round(float(score[i]), 2),
            round(float(broadband[i]), 2),
            round(float(ix_distance[i]), 2),
            round(float(road_access[i]), 2),
            round(float(inex_dublin_km[i]), 3),
            round(float(inex_cork_km[i]), 3),
            None,   # broadband_tier
            None,   # nearest_motorway_junction_km
            None,   # nearest_motorway_junction_name
            None,   # nearest_national_road_km
            None,   # nearest_rail_freight_km
        )
        for i in range(n)
    ]

    with conn.cursor() as cur:
        cur.execute("TRUNCATE connectivity_scores")
        execute_values(
            cur,
            """
            INSERT INTO connectivity_scores (
                tile_id, score,
                broadband, ix_distance, road_access,
                inex_dublin_km, inex_cork_km,
                broadband_tier,
                nearest_motorway_junction_km, nearest_motorway_junction_name,
                nearest_national_road_km, nearest_rail_freight_km
            ) VALUES %s
            """,
            records,
            page_size=1000,
        )
    conn.commit()
    print(f"  connectivity_scores: {n:,} rows")
    return {"score": score}


# ---------------------------------------------------------------------------
# Planning scores
# ---------------------------------------------------------------------------

def seed_planning(conn, tiles) -> dict:
    n = len(tiles)
    tile_ids = [t[0] for t in tiles]

    pct_industrial = np.random.uniform(0.0, 25.0, n)
    pct_enterprise = np.random.uniform(0.0, 25.0, n)
    pct_residential = np.random.uniform(0.0, 10.0, n)
    pct_mixed_use = np.random.uniform(0.0, 15.0, n)
    pct_other = np.random.uniform(0.0, 5.0, n)
    # Agricultural absorbs the remainder (floor at 0)
    pct_agricultural = np.maximum(
        0.0,
        100.0 - pct_industrial - pct_enterprise - pct_residential - pct_mixed_use - pct_other,
    )

    zoning_tier = np.clip((pct_industrial + pct_enterprise) * 0.9, 0.0, 100.0)
    planning_precedent = np.random.uniform(0.0, 60.0, n)
    score = np.clip(zoning_tier * 0.7 + planning_precedent * 0.3, 0.0, 100.0)

    records = [
        (
            tile_ids[i],
            round(float(score[i]), 2),
            round(float(zoning_tier[i]), 2),
            round(float(planning_precedent[i]), 2),
            round(float(pct_industrial[i]), 2),
            round(float(pct_enterprise[i]), 2),
            round(float(pct_mixed_use[i]), 2),
            round(float(pct_agricultural[i]), 2),
            round(float(pct_residential[i]), 2),
            round(float(pct_other[i]), 2),
            None,   # nearest_ida_site_km
            None,   # population_density_per_km2
            None,   # county_dev_plan_ref
        )
        for i in range(n)
    ]

    with conn.cursor() as cur:
        cur.execute("TRUNCATE planning_scores")
        execute_values(
            cur,
            """
            INSERT INTO planning_scores (
                tile_id, score,
                zoning_tier, planning_precedent,
                pct_industrial, pct_enterprise, pct_mixed_use,
                pct_agricultural, pct_residential, pct_other,
                nearest_ida_site_km, population_density_per_km2, county_dev_plan_ref
            ) VALUES %s
            """,
            records,
            page_size=1000,
        )
    conn.commit()
    print(f"  planning_scores:     {n:,} rows")
    return {"score": score}


# ---------------------------------------------------------------------------
# Overall scores (computed from other sorts + weights from DB)
# ---------------------------------------------------------------------------

def seed_overall(
    conn,
    tiles,
    energy_data: dict,
    env_data: dict,
    cooling_data: dict,
    conn_data: dict,
    plan_data: dict,
) -> None:
    n = len(tiles)
    tile_ids = [t[0] for t in tiles]

    # Read weights from DB (single row enforced by CHECK constraint)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT energy, connectivity, environment, cooling, planning "
            "FROM composite_weights WHERE id = 1"
        )
        row = cur.fetchone()

    if row:
        w_energy, w_conn, w_env, w_cool, w_plan = [float(v) for v in row]
    else:
        w_energy, w_conn, w_env, w_cool, w_plan = 0.25, 0.25, 0.20, 0.15, 0.15

    e_s = energy_data["score"]
    env_s = env_data["score"]
    c_s = cooling_data["score"]
    cn_s = conn_data["score"]
    p_s = plan_data["score"]
    hard_mask = env_data["hard_mask"]

    weighted = (
        e_s * w_energy
        + env_s * w_env
        + c_s * w_cool
        + cn_s * w_conn
        + p_s * w_plan
    )
    overall_score = np.where(hard_mask, 0.0, np.clip(weighted, 0.0, 100.0))

    records = [
        (
            tile_ids[i],
            round(float(overall_score[i]), 2),
            round(float(e_s[i]), 2),
            round(float(env_s[i]), 2),
            round(float(c_s[i]), 2),
            round(float(cn_s[i]), 2),
            round(float(p_s[i]), 2),
            bool(hard_mask[i]),
            "SAC or flood zone" if hard_mask[i] else None,
            None,   # nearest_data_centre_km
        )
        for i in range(n)
    ]

    with conn.cursor() as cur:
        cur.execute("TRUNCATE overall_scores")
        execute_values(
            cur,
            """
            INSERT INTO overall_scores (
                tile_id, score,
                energy_score, environment_score, cooling_score,
                connectivity_score, planning_score,
                has_hard_exclusion, exclusion_reason,
                nearest_data_centre_km
            ) VALUES %s
            """,
            records,
            page_size=1000,
        )
    conn.commit()
    print(
        f"  overall_scores:      {n:,} rows  "
        f"({int(hard_mask.sum())} zero-scored exclusions)"
    )


# ---------------------------------------------------------------------------
# Metric ranges (for Martin normalisation)
# ---------------------------------------------------------------------------

def seed_metric_ranges(conn) -> None:
    ranges = [
        ("energy",  "wind_speed_100m", 4.0,   12.5,   "m/s"),
        ("energy",  "solar_ghi",       900.0, 1250.0, "kWh/m\u00b2/yr"),
        ("cooling", "temperature",     8.5,   13.5,   "\u00b0C"),
        ("cooling", "rainfall",        700.0, 2500.0, "mm/yr"),
    ]

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO metric_ranges (sort, metric, min_val, max_val, unit)
            VALUES %s
            ON CONFLICT (sort, metric) DO UPDATE SET
                min_val    = EXCLUDED.min_val,
                max_val    = EXCLUDED.max_val,
                unit       = EXCLUDED.unit,
                updated_at = now()
            """,
            ranges,
        )
    conn.commit()
    print(f"  metric_ranges:       {len(ranges)} rows upserted")


# ---------------------------------------------------------------------------
# IDA sites (3 sample entries)
# ---------------------------------------------------------------------------

def seed_ida_sites(conn) -> None:
    sites = [
        (
            "SRID=4326;POINT(-6.2603 53.3498)",
            "IDA Dublin Business & Technology Park",
            "Dublin",
            "Citywest Business Campus, Dublin 24",
            "technology_park",
        ),
        (
            "SRID=4326;POINT(-8.4694 51.8969)",
            "IDA Cork Business & Technology Park",
            "Cork",
            "Model Farm Road, Cork",
            "technology_park",
        ),
        (
            "SRID=4326;POINT(-9.0568 53.2707)",
            "IDA Galway Technology Park",
            "Galway",
            "Mervue, Galway",
            "technology_park",
        ),
    ]

    with conn.cursor() as cur:
        cur.execute("TRUNCATE ida_sites CASCADE")
        execute_values(
            cur,
            """
            INSERT INTO ida_sites (geom, name, county, address, site_type)
            VALUES %s
            """,
            sites,
            template="(ST_GeomFromEWKT(%s), %s, %s, %s, %s)",
        )
    conn.commit()
    print(f"  ida_sites:           {len(sites)} rows")


# ---------------------------------------------------------------------------
# Sample pins (2 overall data-centre pins)
# ---------------------------------------------------------------------------

def seed_pins_overall(conn) -> None:
    pins = [
        (
            "SRID=4326;POINT(-6.2500 53.3300)",
            "Sample Data Centre Dublin",
            "data_centre",
            "Tech Corp",
            "operating",
            None,   # capacity_mw
        ),
        (
            "SRID=4326;POINT(-8.4500 51.9000)",
            "Sample Data Centre Cork",
            "data_centre",
            "Infra Ltd",
            "operating",
            None,
        ),
    ]

    with conn.cursor() as cur:
        cur.execute("TRUNCATE pins_overall")
        execute_values(
            cur,
            """
            INSERT INTO pins_overall (geom, name, type, operator, dc_status, capacity_mw)
            VALUES %s
            """,
            pins,
            template="(ST_GeomFromEWKT(%s), %s, %s, %s, %s, %s)",
        )
    conn.commit()
    print(f"  pins_overall:        {len(pins)} rows")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Synthetic data seed — all sort tables")
    print("=" * 60)

    print("\nConnecting to database...")
    conn = get_conn()

    print("Reading tiles...")
    tiles = get_tiles(conn)
    n = len(tiles)
    print(f"  Found {n:,} tiles")

    if n == 0:
        print("ERROR: tiles table is empty. Run grid/generate_grid.py first.")
        conn.close()
        sys.exit(1)

    print("\nSeeding sort score tables...")
    energy_data = seed_energy(conn, tiles)
    env_data = seed_environment(conn, tiles)
    cooling_data = seed_cooling(conn, tiles)
    conn_data = seed_connectivity(conn, tiles)
    plan_data = seed_planning(conn, tiles)

    print("\nSeeding composite scores...")
    seed_overall(conn, tiles, energy_data, env_data, cooling_data, conn_data, plan_data)

    print("\nSeeding reference tables...")
    seed_metric_ranges(conn)
    seed_ida_sites(conn)
    seed_pins_overall(conn)

    conn.close()
    print(f"\nSynthetic seed complete — {n:,} tiles across all 6 sort tables.")
    print("Martin tiles should now return non-empty MVT responses.")


if __name__ == "__main__":
    main()
