-- ============================================================
-- MARTIN TILE FUNCTION
-- Called by Martin as: GET /tile_heatmap/{z}/{x}/{y}?sort=X&metric=Y
-- query_params JSON is populated by Martin from the URL query string.
--
-- Design rules (from ARCHITECTURE.md §10):
--   - All sort+metric combos enumerated explicitly — NO dynamic SQL
--   - Raw sub-metrics normalised via metric_ranges: (val - min) / (max - min) * 100
--   - Pre-normalised 0–100 values returned as-is
--   - temperature sub-metric INVERTED: lower temp = higher score for cooling
--   - Invalid combos return NULL value (tile renders no colour)
-- ============================================================
CREATE OR REPLACE FUNCTION tile_heatmap(
    z            int,
    x            int,
    y            int,
    query_params json DEFAULT '{}'
)
RETURNS bytea
LANGUAGE plpgsql
STABLE PARALLEL SAFE
AS $$
DECLARE
    v_sort   TEXT  := query_params->>'sort';
    v_metric TEXT  := query_params->>'metric';
    v_result BYTEA;
BEGIN
    SELECT ST_AsMVT(q, 'tile_heatmap', 4096, 'geom')
    INTO v_result
    FROM (
        SELECT
            ST_AsMVTGeom(
                ST_Transform(t.geom, 3857),
                ST_TileEnvelope(z, x, y),
                4096, 64, true
            ) AS geom,
            t.tile_id,
            CASE
                -- ── OVERALL ─────────────────────────────────────────────
                WHEN v_sort = 'overall' AND v_metric = 'score'              THEN o.score
                WHEN v_sort = 'overall' AND v_metric = 'energy_score'       THEN o.energy_score
                WHEN v_sort = 'overall' AND v_metric = 'environment_score'  THEN o.environment_score
                WHEN v_sort = 'overall' AND v_metric = 'cooling_score'      THEN o.cooling_score
                WHEN v_sort = 'overall' AND v_metric = 'connectivity_score' THEN o.connectivity_score
                WHEN v_sort = 'overall' AND v_metric = 'planning_score'     THEN o.planning_score
                WHEN v_sort = 'overall' AND v_metric = 'population_density' THEN
                    (p.population_density_per_km2 - mr.min_val) / NULLIF(mr.max_val - mr.min_val, 0) * 100
                -- ── ENERGY ──────────────────────────────────────────────
                WHEN v_sort = 'energy' AND v_metric = 'score'              THEN e.score
                -- grid_proximity moved to connectivity (P2-22)
                -- WHEN v_sort = 'energy' AND v_metric = 'grid_proximity'     THEN e.grid_proximity
                WHEN v_sort = 'energy' AND v_metric = 'wind_speed_100m'    THEN
                    (e.wind_speed_100m - mr.min_val) / NULLIF(mr.max_val - mr.min_val, 0) * 100
                WHEN v_sort = 'energy' AND v_metric = 'solar_ghi'          THEN
                    (e.solar_ghi - mr.min_val) / NULLIF(mr.max_val - mr.min_val, 0) * 100
                WHEN v_sort = 'energy' AND v_metric = 'renewable'         THEN e.renewable_score
                WHEN v_sort = 'energy' AND v_metric = 'renewable_pct'     THEN
                    CASE WHEN mr.min_val IS NOT NULL AND mr.max_val > mr.min_val
                        THEN ((e.renewable_pct - mr.min_val) / (mr.max_val - mr.min_val) * 100)::int
                        ELSE 0 END
                -- ── ENVIRONMENT ─────────────────────────────────────────
                WHEN v_sort = 'environment' AND v_metric = 'score'               THEN env.score
                WHEN v_sort = 'environment' AND v_metric = 'designation_overlap' THEN env.designation_overlap
                WHEN v_sort = 'environment' AND v_metric = 'water_proximity'    THEN c.water_proximity
                WHEN v_sort = 'environment' AND v_metric = 'aquifer_productivity' THEN c.aquifer_productivity
                -- flood_risk and landslide_risk moved to planning (P2-22)
                -- WHEN v_sort = 'environment' AND v_metric = 'flood_risk'          THEN env.flood_risk
                -- WHEN v_sort = 'environment' AND v_metric = 'landslide_risk'      THEN env.landslide_risk
                -- ── COOLING ─────────────────────────────────────────────
                WHEN v_sort = 'cooling' AND v_metric = 'score'              THEN c.score
                -- water_proximity and aquifer_productivity moved to environment (P2-22)
                -- WHEN v_sort = 'cooling' AND v_metric = 'water_proximity'    THEN c.water_proximity
                -- WHEN v_sort = 'cooling' AND v_metric = 'aquifer_productivity' THEN c.aquifer_productivity
                WHEN v_sort = 'cooling' AND v_metric = 'rainfall'           THEN
                    (c.rainfall - mr.min_val) / NULLIF(mr.max_val - mr.min_val, 0) * 100
                WHEN v_sort = 'cooling' AND v_metric = 'temperature'        THEN
                    -- INVERTED: lower °C = better for cooling = higher score
                    100 - (c.temperature - mr.min_val) / NULLIF(mr.max_val - mr.min_val, 0) * 100
                -- ── CONNECTIVITY ─────────────────────────────────────────
                WHEN v_sort = 'connectivity' AND v_metric = 'score'        THEN cn.score
                WHEN v_sort = 'connectivity' AND v_metric = 'broadband'    THEN cn.broadband
                WHEN v_sort = 'connectivity' AND v_metric = 'ix_distance'  THEN cn.ix_distance
                WHEN v_sort = 'connectivity' AND v_metric = 'road_access'  THEN cn.road_access
                WHEN v_sort = 'connectivity' AND v_metric = 'grid_proximity' THEN e.grid_proximity
                -- ── PLANNING ─────────────────────────────────────────────
                WHEN v_sort = 'planning' AND v_metric = 'score'              THEN p.score
                WHEN v_sort = 'planning' AND v_metric = 'zoning_tier'        THEN p.zoning_tier
                WHEN v_sort = 'planning' AND v_metric = 'planning_precedent' THEN p.planning_precedent
                WHEN v_sort = 'planning' AND v_metric = 'flood_risk'         THEN env.flood_risk
                WHEN v_sort = 'planning' AND v_metric = 'landslide_risk'     THEN env.landslide_risk
                WHEN v_sort = 'planning' AND v_metric = 'land_price'         THEN p.land_price_score
                WHEN v_sort = 'planning' AND v_metric = 'avg_price_per_sqm_eur' THEN
                    -- INVERTED: lower €/m² = better for siting = higher score
                    100 - (p.avg_price_per_sqm_eur - mr.min_val) / NULLIF(mr.max_val - mr.min_val, 0) * 100
                -- ── FALLBACK ─────────────────────────────────────────────
                ELSE NULL
            END AS value
        FROM tiles t
        LEFT JOIN overall_scores      o   ON o.tile_id   = t.tile_id
        LEFT JOIN energy_scores       e   ON e.tile_id   = t.tile_id
        LEFT JOIN environment_scores  env ON env.tile_id = t.tile_id
        LEFT JOIN cooling_scores      c   ON c.tile_id   = t.tile_id
        LEFT JOIN connectivity_scores cn  ON cn.tile_id  = t.tile_id
        LEFT JOIN planning_scores     p   ON p.tile_id   = t.tile_id
        -- metric_ranges joined once; only used for raw sub-metrics
        LEFT JOIN metric_ranges       mr  ON mr.sort = v_sort AND mr.metric = v_metric
        WHERE t.geom && ST_Transform(ST_TileEnvelope(z, x, y), 4326)
    ) q
    WHERE q.geom IS NOT NULL;

    RETURN COALESCE(v_result, ''::bytea);
END;
$$;
