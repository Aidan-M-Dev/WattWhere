-- ============================================================
-- SPATIAL INDEXES (GIST) — critical for Martin tile queries
-- ============================================================
CREATE INDEX tiles_geom_gist               ON tiles             USING GIST (geom);
CREATE INDEX tiles_centroid_gist           ON tiles             USING GIST (centroid);
CREATE INDEX ida_sites_geom_gist           ON ida_sites         USING GIST (geom);
CREATE INDEX pins_overall_geom_gist        ON pins_overall      USING GIST (geom);
CREATE INDEX pins_energy_geom_gist         ON pins_energy       USING GIST (geom);
CREATE INDEX pins_environment_geom_gist    ON pins_environment  USING GIST (geom);
CREATE INDEX pins_cooling_geom_gist        ON pins_cooling      USING GIST (geom);
CREATE INDEX pins_connectivity_geom_gist   ON pins_connectivity USING GIST (geom);
CREATE INDEX pins_planning_geom_gist       ON pins_planning     USING GIST (geom);

-- ============================================================
-- REGULAR INDEXES
-- ============================================================
CREATE INDEX tiles_county_idx                        ON tiles                       (county);
CREATE INDEX ida_sites_tile_idx                      ON ida_sites                   (tile_id);
CREATE INDEX pins_overall_tile_idx                   ON pins_overall                (tile_id);
CREATE INDEX pins_energy_tile_idx                    ON pins_energy                 (tile_id);
CREATE INDEX pins_environment_tile_idx               ON pins_environment             (tile_id);
CREATE INDEX pins_cooling_tile_idx                   ON pins_cooling                (tile_id);
CREATE INDEX pins_connectivity_tile_idx              ON pins_connectivity            (tile_id);
CREATE INDEX pins_planning_tile_idx                  ON pins_planning               (tile_id);
CREATE INDEX tile_designation_overlaps_tile_idx      ON tile_designation_overlaps   (tile_id);
CREATE INDEX tile_designation_overlaps_type_idx      ON tile_designation_overlaps   (designation_type);
CREATE INDEX tile_planning_applications_tile_idx     ON tile_planning_applications  (tile_id);
CREATE INDEX tile_planning_applications_appref_idx   ON tile_planning_applications  (app_ref);
CREATE INDEX environment_scores_exclusion_idx        ON environment_scores          (has_hard_exclusion) WHERE has_hard_exclusion = true;
CREATE INDEX overall_scores_exclusion_idx            ON overall_scores              (has_hard_exclusion) WHERE has_hard_exclusion = true;
