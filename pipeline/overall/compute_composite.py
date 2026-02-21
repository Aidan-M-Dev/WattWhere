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
import pandas as pd
import sqlalchemy
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_URL


def load_weights(engine: sqlalchemy.Engine) -> dict[str, float]:
    """
    Load current composite weights from composite_weights table.
    Always read from DB — do not use DEFAULT_WEIGHTS from config.py.

    Returns:
        Dict with keys: energy, connectivity, environment, cooling, planning.

    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Load weights from composite_weights table")


def load_sort_scores(engine: sqlalchemy.Engine) -> pd.DataFrame:
    """
    JOIN all 5 sort tables to get per-tile scores.
    Only tiles present in ALL 5 sort tables are included (INNER JOIN).
    Tiles missing from any sort table will not have an overall score — log a warning.

    Returns:
        DataFrame with columns: tile_id, energy_score, environment_score,
        cooling_score, connectivity_score, planning_score, has_hard_exclusion, exclusion_reason.

    TODO: implement using:
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
    # TODO: implement
    raise NotImplementedError("Load all sort scores")


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

    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Compute overall composite scores")


def compute_nearest_data_centre_km(engine: sqlalchemy.Engine) -> pd.Series:
    """
    For each tile centroid, compute distance to nearest pin in pins_overall
    where type = 'data_centre'.

    Returns Series[tile_id → km] (NULL / NaN if no data centre pins exist).

    TODO: implement — PostGIS query or geopandas nearest spatial join.
    SQL option:
      SELECT t.tile_id,
             MIN(ST_Distance(ST_Transform(t.centroid, 2157),
                             ST_Transform(p.geom, 2157))) / 1000 AS km
      FROM tiles t
      CROSS JOIN LATERAL (SELECT geom FROM pins_overall WHERE type = 'data_centre'
                          ORDER BY t.centroid <-> geom LIMIT 1) p
      GROUP BY t.tile_id
    """
    # TODO: implement
    raise NotImplementedError("Compute nearest data centre distance")


def upsert_overall_scores(df: pd.DataFrame, engine: sqlalchemy.Engine) -> int:
    """
    Upsert overall_scores. ON CONFLICT(tile_id) DO UPDATE all columns.
    Returns row count.

    TODO: implement
    """
    # TODO: implement
    raise NotImplementedError("Upsert overall scores")


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
    print("Computing overall composite scores...")
    engine = sqlalchemy.create_engine(DB_URL)

    # TODO: implement
    # weights = load_weights(engine)
    # print(f"Weights: {weights}")
    # scores_df = load_sort_scores(engine)
    # print(f"Loaded {len(scores_df)} tiles with all sort scores")
    # overall_df = compute_overall_scores(scores_df, weights)
    # dc_km = compute_nearest_data_centre_km(engine)
    # overall_df = overall_df.merge(dc_km.rename('nearest_data_centre_km'), on='tile_id', how='left')
    # n = upsert_overall_scores(overall_df, engine)
    # print(f"Overall scores computed: {n} tiles")
    # print("Done. Restart Martin to flush tile cache: docker compose restart martin")

    raise NotImplementedError("Implement main() pipeline steps")


if __name__ == "__main__":
    main()
