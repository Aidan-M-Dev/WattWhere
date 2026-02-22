"""
FILE: pipeline/overall/compute_composite.py
Role: Compute overall_scores by combining all 5 sort scores with weights.
      This is the LAST pipeline step — all sort tables must be populated first.
Agent boundary: Pipeline — Overall composite (§5.1, §8, §10)
Dependencies:
  - ALL sort tables populated:
      energy_scores, environment_scores, cooling_scores,
      connectivity_scores, planning_scores
  - composite_weights table populated (seeded by sql/tables.sql)
Output:
  - Populates overall_scores table (upsert — replaces all rows)
  - overall_scores.score = weighted sum of all 5 sort scores
  - hard exclusion propagation: tiles with has_hard_exclusion in environment_scores → score = 0
How to test:
  python overall/compute_composite.py
  psql $DATABASE_URL -c "SELECT MIN(score), MAX(score), AVG(score), COUNT(*) FROM overall_scores;"
  # After restart Martin to flush cache: docker compose restart martin

ARCHITECTURE RULES (§5.1, §10 rule 10):
  - Tiles with environment_scores.has_hard_exclusion = true → overall score = 0, regardless of others.
    These are SAC/SPA overlaps and current flood zone tiles.
  - Weights from composite_weights table (NOT from config.py DEFAULT_WEIGHTS).
    Always read from DB at runtime so PUT /api/weights changes are reflected.
  - overall_scores also stores individual sort sub-scores for the sidebar breakdown.
  - nearest_data_centre_km: distance from tile centroid to nearest pin in pins_overall
    where type = 'data_centre'. Set to NULL if pins_overall is empty.
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd
import sqlalchemy
from sqlalchemy import text
from tqdm import tqdm
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_URL


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


def load_weights(engine: sqlalchemy.Engine) -> dict[str, float]:
    """
    Load current composite weights from composite_weights table.
    Always read from DB — do not use DEFAULT_WEIGHTS from config.py.

    Returns:
        Dict with keys: energy, connectivity, environment, cooling, planning.
    """
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT energy, connectivity, environment, cooling, planning "
            "FROM composite_weights WHERE id = 1"
        )).fetchone()

    if row is None:
        raise RuntimeError(
            "composite_weights table is empty. Run sql/tables.sql to seed it."
        )

    return dict(row._mapping)


def load_sort_scores(engine: sqlalchemy.Engine) -> pd.DataFrame:
    """
    JOIN all 5 sort tables to get per-tile scores.
    Only tiles present in ALL 5 sort tables are included (INNER JOIN).
    Tiles missing from any sort table will not have an overall score — log a warning.

    Returns:
        DataFrame with columns: tile_id, energy_score, environment_score,
        cooling_score, connectivity_score, planning_score, has_hard_exclusion, exclusion_reason.
    """
    sql = """
        SELECT t.tile_id,
               e.score AS energy_score,
               env.score AS environment_score,
               c.score AS cooling_score,
               cn.score AS connectivity_score,
               p.score AS planning_score,
               env.has_hard_exclusion,
               env.exclusion_reason
        FROM tiles t
        INNER JOIN energy_scores e ON e.tile_id = t.tile_id
        INNER JOIN environment_scores env ON env.tile_id = t.tile_id
        INNER JOIN cooling_scores c ON c.tile_id = t.tile_id
        INNER JOIN connectivity_scores cn ON cn.tile_id = t.tile_id
        INNER JOIN planning_scores p ON p.tile_id = t.tile_id
    """

    scores_df = pd.read_sql(sql, engine)

    # Check for missing tiles
    with engine.connect() as conn:
        total_tiles = conn.execute(text("SELECT COUNT(*) FROM tiles")).scalar()

    if len(scores_df) != total_tiles:
        missing = total_tiles - len(scores_df)
        print(f"  WARNING: {missing} tiles are missing from at least one sort table "
              f"({len(scores_df)}/{total_tiles} tiles have all 5 scores)")

    return scores_df


def compute_overall_scores(
    scores_df: pd.DataFrame,
    weights: dict[str, float],
) -> pd.DataFrame:
    """
    Compute weighted composite score for each tile.

    Formula:
      score = (energy_score * w_energy) + (environment_score * w_environment) +
              (cooling_score * w_cooling) + (connectivity_score * w_connectivity) +
              (planning_score * w_planning)
      if has_hard_exclusion: score = 0

    Round to 2 decimal places. Clamp to [0, 100].

    Returns:
        DataFrame matching overall_scores table schema (minus nearest_data_centre_km).
    """
    score = (
        scores_df["energy_score"]       * float(weights["energy"]) +
        scores_df["environment_score"]  * float(weights["environment"]) +
        scores_df["cooling_score"]      * float(weights["cooling"]) +
        scores_df["connectivity_score"] * float(weights["connectivity"]) +
        scores_df["planning_score"]     * float(weights["planning"])
    )

    # Hard exclusion propagation: tiles with has_hard_exclusion → score = 0
    score = np.where(scores_df["has_hard_exclusion"], 0, score)
    score = pd.Series(score).clip(0, 100).round(2)

    result = pd.DataFrame({
        "tile_id": scores_df["tile_id"],
        "score": score,
        "energy_score": scores_df["energy_score"],
        "environment_score": scores_df["environment_score"],
        "cooling_score": scores_df["cooling_score"],
        "connectivity_score": scores_df["connectivity_score"],
        "planning_score": scores_df["planning_score"],
        "has_hard_exclusion": scores_df["has_hard_exclusion"],
        "exclusion_reason": scores_df["exclusion_reason"],
    })

    return result


def compute_nearest_data_centre_km(engine: sqlalchemy.Engine) -> pd.Series:
    """
    For each tile centroid, compute distance to nearest pin in pins_overall
    where type = 'data_centre'.

    Returns Series[tile_id → km] (NULL / NaN if no data centre pins exist).
    """
    # Check if any data centre pins exist
    with engine.connect() as conn:
        dc_count = conn.execute(
            text("SELECT COUNT(*) FROM pins_overall WHERE type = 'data_centre'")
        ).scalar()

    if dc_count == 0:
        print("  No data_centre pins in pins_overall — returning NaN for nearest_data_centre_km")
        with engine.connect() as conn:
            tile_ids = pd.read_sql("SELECT tile_id FROM tiles", conn)
        return pd.Series(np.nan, index=tile_ids["tile_id"], name="nearest_data_centre_km")

    # Use PostGIS lateral join for efficient nearest-neighbour
    sql = """
        SELECT t.tile_id,
               MIN(ST_Distance(
                   ST_Transform(t.centroid, 2157),
                   ST_Transform(p.geom, 2157)
               )) / 1000.0 AS km
        FROM tiles t
        CROSS JOIN LATERAL (
            SELECT geom
            FROM pins_overall
            WHERE type = 'data_centre'
            ORDER BY t.centroid <-> geom
            LIMIT 1
        ) p
        GROUP BY t.tile_id
    """

    dc_km = pd.read_sql(sql, engine)
    result = dc_km.set_index("tile_id")["km"]
    result.name = "nearest_data_centre_km"
    return result


def upsert_overall_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Upsert overall_scores. ON CONFLICT(tile_id) DO UPDATE all columns.
    Returns row count.
    """
    sql = """
        INSERT INTO overall_scores (
            tile_id, score, energy_score, environment_score,
            cooling_score, connectivity_score, planning_score,
            has_hard_exclusion, exclusion_reason, nearest_data_centre_km
        ) VALUES %s
        ON CONFLICT (tile_id) DO UPDATE SET
            score                  = EXCLUDED.score,
            energy_score           = EXCLUDED.energy_score,
            environment_score      = EXCLUDED.environment_score,
            cooling_score          = EXCLUDED.cooling_score,
            connectivity_score     = EXCLUDED.connectivity_score,
            planning_score         = EXCLUDED.planning_score,
            has_hard_exclusion     = EXCLUDED.has_hard_exclusion,
            exclusion_reason       = EXCLUDED.exclusion_reason,
            nearest_data_centre_km = EXCLUDED.nearest_data_centre_km,
            computed_at            = now()
    """

    cols = [
        "tile_id", "score", "energy_score", "environment_score",
        "cooling_score", "connectivity_score", "planning_score",
        "has_hard_exclusion", "exclusion_reason", "nearest_data_centre_km",
    ]

    rows = [tuple(_to_py(row[c]) for c in cols) for _, row in df.iterrows()]

    pg_conn = engine.raw_connection()
    try:
        cur = pg_conn.cursor()
        batch_size = 500
        for i in tqdm(range(0, len(rows), batch_size), desc="Upserting overall_scores"):
            execute_values(cur, sql, rows[i: i + batch_size])
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
        pg_conn.close()

    return len(rows)


def main():
    """
    Overall composite computation pipeline:
      1. Load composite weights from DB
      2. Load all 5 sort scores (INNER JOIN)
      3. Compute weighted composite score + apply hard exclusions
      4. Compute nearest_data_centre_km from pins_overall
      5. Upsert overall_scores

    Run AFTER: ALL sort ingest scripts complete.
    After this: restart Martin (docker compose restart martin) to flush tile cache.
    """
    print("=" * 60)
    print("Computing overall composite scores...")
    print("=" * 60)

    engine = sqlalchemy.create_engine(DB_URL)

    # ── Step 1: Load weights ──────────────────────────────────────────────
    print("\n[1/5] Loading composite weights from DB...")
    weights = load_weights(engine)
    print(f"  Weights: {weights}")
    w_sum = sum(float(v) for v in weights.values())
    print(f"  Sum: {w_sum:.4f}")
    if abs(w_sum - 1.0) > 0.001:
        print(f"  WARNING: Weights do not sum to 1.0 (sum={w_sum:.4f})")

    # ── Step 2: Load sort scores ──────────────────────────────────────────
    print("\n[2/5] Loading all sort scores (INNER JOIN across 5 tables)...")
    scores_df = load_sort_scores(engine)
    print(f"  Loaded {len(scores_df)} tiles with all 5 sort scores")

    if len(scores_df) == 0:
        print("\n  ERROR: No tiles have scores in all 5 sort tables.")
        print("  Ensure all sort pipelines have completed successfully.")
        raise SystemExit(1)

    exclusion_count = scores_df["has_hard_exclusion"].sum()
    print(f"  Hard exclusions: {exclusion_count} tiles")

    # ── Step 3: Compute composite scores ──────────────────────────────────
    print("\n[3/5] Computing weighted composite scores...")
    overall_df = compute_overall_scores(scores_df, weights)
    print(f"  Score: min={overall_df['score'].min():.2f}, "
          f"max={overall_df['score'].max():.2f}, "
          f"mean={overall_df['score'].mean():.2f}")
    zero_count = (overall_df["score"] == 0).sum()
    print(f"  Tiles with score=0: {zero_count} "
          f"(should match hard exclusion count: {exclusion_count})")

    # ── Step 4: Nearest data centre distance ──────────────────────────────
    print("\n[4/5] Computing nearest data centre distance...")
    dc_km = compute_nearest_data_centre_km(engine)
    overall_df = overall_df.merge(
        dc_km.rename("nearest_data_centre_km").reset_index(),
        on="tile_id",
        how="left",
    )
    if not dc_km.isna().all():
        print(f"  Data centre distance: min={dc_km.min():.1f}, "
              f"max={dc_km.max():.1f} km")
    else:
        print("  No data centre pins — nearest_data_centre_km will be NULL")

    # ── Step 5: Upsert ────────────────────────────────────────────────────
    print("\n[5/5] Upserting overall_scores...")
    n = upsert_overall_scores(overall_df, engine)
    print(f"  Upserted {n} rows into overall_scores")

    print("\n" + "=" * 60)
    print(f"Overall composite complete: {n} tiles scored")
    print(f"  Score range: {overall_df['score'].min():.2f} – {overall_df['score'].max():.2f}")
    print(f"  Hard exclusions (score=0): {zero_count}")
    print("\nRestart Martin to flush tile cache:")
    print("  docker compose restart martin")
    print("=" * 60)


if __name__ == "__main__":
    main()
