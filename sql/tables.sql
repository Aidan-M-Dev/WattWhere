-- ============================================================
-- EXTENSIONS
-- ============================================================
CREATE EXTENSION IF NOT EXISTS postgis;

-- ============================================================
-- COUNTIES (reference table)
-- ============================================================
CREATE TABLE counties (
    county_name TEXT PRIMARY KEY
);

INSERT INTO counties (county_name) VALUES
  ('Carlow'),('Cavan'),('Clare'),('Cork'),('Donegal'),('Dublin'),
  ('Galway'),('Kerry'),('Kildare'),('Kilkenny'),('Laois'),('Leitrim'),
  ('Limerick'),('Longford'),('Louth'),('Mayo'),('Meath'),('Monaghan'),
  ('Offaly'),('Roscommon'),('Sligo'),('Tipperary'),('Waterford'),
  ('Westmeath'),('Wexford'),('Wicklow');

-- ============================================================
-- TILES (the base grid — ~14,000 rows)
-- ============================================================
CREATE TABLE tiles (
    tile_id   SERIAL          PRIMARY KEY,
    geom      GEOMETRY(Polygon, 4326) NOT NULL,
    centroid  GEOMETRY(Point,   4326) NOT NULL,
    county    TEXT            NOT NULL REFERENCES counties(county_name),
    grid_ref  TEXT,                          -- human-readable e.g. 'R012C034'
    area_km2  NUMERIC(8,4)    NOT NULL DEFAULT 5.0
);

-- ============================================================
-- METRIC RANGES (pipeline writes these; Martin reads them for normalisation)
-- ============================================================
CREATE TABLE metric_ranges (
    sort       TEXT         NOT NULL,
    metric     TEXT         NOT NULL,
    min_val    NUMERIC      NOT NULL,
    max_val    NUMERIC      NOT NULL,
    unit       TEXT         NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (sort, metric)
);

-- ============================================================
-- COMPOSITE WEIGHTS (single-row — enforced by CHECK constraint)
-- ============================================================
CREATE TABLE composite_weights (
    id           INTEGER      PRIMARY KEY DEFAULT 1,
    energy       NUMERIC(5,4) NOT NULL DEFAULT 0.25,
    connectivity NUMERIC(5,4) NOT NULL DEFAULT 0.25,
    environment  NUMERIC(5,4) NOT NULL DEFAULT 0.20,
    cooling      NUMERIC(5,4) NOT NULL DEFAULT 0.15,
    planning     NUMERIC(5,4) NOT NULL DEFAULT 0.15,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT single_row           CHECK (id = 1),
    CONSTRAINT weights_sum_to_one   CHECK (
        ROUND(energy + connectivity + environment + cooling + planning, 4) = 1.0
    )
);

INSERT INTO composite_weights DEFAULT VALUES;

-- ============================================================
-- IDA SITES (shared — used by pins_overall and pins_planning via API JOIN)
-- ============================================================
CREATE TABLE ida_sites (
    ida_site_id  SERIAL                 PRIMARY KEY,
    geom         GEOMETRY(Point, 4326)  NOT NULL,
    name         TEXT                   NOT NULL,
    county       TEXT                   REFERENCES counties(county_name),
    address      TEXT,
    site_type    TEXT,                  -- 'industrial', 'technology_park', etc.
    tile_id      INTEGER                REFERENCES tiles(tile_id)
);

-- ============================================================
-- SCORE TABLES
-- ============================================================

CREATE TABLE energy_scores (
    tile_id                       INTEGER       PRIMARY KEY REFERENCES tiles(tile_id) ON DELETE CASCADE,
    score                         NUMERIC(5,2)  NOT NULL CHECK (score BETWEEN 0 AND 100),
    wind_speed_50m                NUMERIC(6,3),           -- m/s raw; sidebar only
    wind_speed_100m               NUMERIC(6,3),           -- m/s raw; heatmap sub-metric
    wind_speed_150m               NUMERIC(6,3),           -- m/s raw; sidebar only
    solar_ghi                     NUMERIC(8,3),           -- kWh/m²/yr raw; heatmap sub-metric
    grid_proximity                NUMERIC(5,2)  CHECK (grid_proximity BETWEEN 0 AND 100),  -- pre-normalised 0–100
    nearest_transmission_line_km  NUMERIC(8,3),
    nearest_substation_km         NUMERIC(8,3),
    nearest_substation_name       TEXT,
    nearest_substation_voltage    TEXT,
    grid_low_confidence           BOOLEAN       NOT NULL DEFAULT false  -- true if nearest infra > 20 km
);

CREATE TABLE environment_scores (
    tile_id                 INTEGER       PRIMARY KEY REFERENCES tiles(tile_id) ON DELETE CASCADE,
    score                   NUMERIC(5,2)  NOT NULL CHECK (score BETWEEN 0 AND 100),
    designation_overlap     NUMERIC(5,2)  CHECK (designation_overlap BETWEEN 0 AND 100),  -- 100 = no overlap
    flood_risk              NUMERIC(5,2)  CHECK (flood_risk BETWEEN 0 AND 100),           -- 100 = no flood risk
    landslide_risk          NUMERIC(5,2)  CHECK (landslide_risk BETWEEN 0 AND 100),       -- 100 = no susceptibility
    has_hard_exclusion      BOOLEAN       NOT NULL DEFAULT false,
    exclusion_reason        TEXT,         -- NULL unless has_hard_exclusion; e.g. 'SAC overlap' / 'Current flood zone'
    intersects_sac          BOOLEAN       NOT NULL DEFAULT false,
    intersects_spa          BOOLEAN       NOT NULL DEFAULT false,
    intersects_nha          BOOLEAN       NOT NULL DEFAULT false,
    intersects_pnha         BOOLEAN       NOT NULL DEFAULT false,
    intersects_current_flood BOOLEAN      NOT NULL DEFAULT false,
    intersects_future_flood  BOOLEAN      NOT NULL DEFAULT false,
    landslide_susceptibility TEXT         CHECK (landslide_susceptibility IN ('none','low','medium','high'))
);

CREATE TABLE cooling_scores (
    tile_id                           INTEGER       PRIMARY KEY REFERENCES tiles(tile_id) ON DELETE CASCADE,
    score                             NUMERIC(5,2)  NOT NULL CHECK (score BETWEEN 0 AND 100),
    temperature                       NUMERIC(5,2),           -- °C raw (mean annual); heatmap sub-metric — INVERTED ramp
    water_proximity                   NUMERIC(5,2)  CHECK (water_proximity BETWEEN 0 AND 100),
    rainfall                          NUMERIC(8,2),           -- mm/yr raw; heatmap sub-metric
    aquifer_productivity              NUMERIC(5,2)  CHECK (aquifer_productivity BETWEEN 0 AND 100),
    free_cooling_hours                NUMERIC(6,0),           -- hours/yr estimate (hours below 18°C)
    nearest_waterbody_name            TEXT,
    nearest_waterbody_km              NUMERIC(8,3),
    nearest_hydrometric_station_name  TEXT,
    nearest_hydrometric_flow_m3s      NUMERIC(10,3),          -- NULL if unavailable
    aquifer_productivity_rating       TEXT         CHECK (aquifer_productivity_rating IN ('high','moderate','low','negligible','none'))
);

CREATE TABLE connectivity_scores (
    tile_id                        INTEGER       PRIMARY KEY REFERENCES tiles(tile_id) ON DELETE CASCADE,
    score                          NUMERIC(5,2)  NOT NULL CHECK (score BETWEEN 0 AND 100),
    broadband                      NUMERIC(5,2)  CHECK (broadband BETWEEN 0 AND 100),
    ix_distance                    NUMERIC(5,2)  CHECK (ix_distance BETWEEN 0 AND 100),   -- pre-normalised inverse log-distance
    road_access                    NUMERIC(5,2)  CHECK (road_access BETWEEN 0 AND 100),   -- pre-normalised inverse distance
    inex_dublin_km                 NUMERIC(8,3),
    inex_cork_km                   NUMERIC(8,3),
    broadband_tier                 TEXT,          -- ComReg tier label
    nearest_motorway_junction_km   NUMERIC(8,3),
    nearest_motorway_junction_name TEXT,
    nearest_national_road_km       NUMERIC(8,3),
    nearest_rail_freight_km        NUMERIC(8,3)
);

CREATE TABLE planning_scores (
    tile_id                    INTEGER       PRIMARY KEY REFERENCES tiles(tile_id) ON DELETE CASCADE,
    score                      NUMERIC(5,2)  NOT NULL CHECK (score BETWEEN 0 AND 100),
    zoning_tier                NUMERIC(5,2)  CHECK (zoning_tier BETWEEN 0 AND 100),
    planning_precedent         NUMERIC(5,2)  CHECK (planning_precedent BETWEEN 0 AND 100),
    pct_industrial             NUMERIC(5,2)  NOT NULL DEFAULT 0 CHECK (pct_industrial BETWEEN 0 AND 100),
    pct_enterprise             NUMERIC(5,2)  NOT NULL DEFAULT 0 CHECK (pct_enterprise BETWEEN 0 AND 100),
    pct_mixed_use              NUMERIC(5,2)  NOT NULL DEFAULT 0 CHECK (pct_mixed_use BETWEEN 0 AND 100),
    pct_agricultural           NUMERIC(5,2)  NOT NULL DEFAULT 0 CHECK (pct_agricultural BETWEEN 0 AND 100),
    pct_residential            NUMERIC(5,2)  NOT NULL DEFAULT 0 CHECK (pct_residential BETWEEN 0 AND 100),
    pct_other                  NUMERIC(5,2)  NOT NULL DEFAULT 0 CHECK (pct_other BETWEEN 0 AND 100),
    nearest_ida_site_km        NUMERIC(8,3),  -- denormalised for query speed; source of truth is ida_sites
    population_density_per_km2 NUMERIC(10,3),
    county_dev_plan_ref        TEXT
);

CREATE TABLE overall_scores (
    tile_id               INTEGER       PRIMARY KEY REFERENCES tiles(tile_id) ON DELETE CASCADE,
    score                 NUMERIC(5,2)  NOT NULL CHECK (score BETWEEN 0 AND 100),
    energy_score          NUMERIC(5,2)  CHECK (energy_score BETWEEN 0 AND 100),
    environment_score     NUMERIC(5,2)  CHECK (environment_score BETWEEN 0 AND 100),
    cooling_score         NUMERIC(5,2)  CHECK (cooling_score BETWEEN 0 AND 100),
    connectivity_score    NUMERIC(5,2)  CHECK (connectivity_score BETWEEN 0 AND 100),
    planning_score        NUMERIC(5,2)  CHECK (planning_score BETWEEN 0 AND 100),
    has_hard_exclusion    BOOLEAN       NOT NULL DEFAULT false,
    exclusion_reason      TEXT,
    nearest_data_centre_km NUMERIC(8,3),
    computed_at           TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- ============================================================
-- PIN TABLES
-- (tile_id is nullable: pins near coast/boundaries may fall outside any tile)
-- IDA sites NOT stored here — API JOINs ida_sites for sorts that need them
-- ============================================================

CREATE TABLE pins_overall (
    pin_id      SERIAL                 PRIMARY KEY,
    tile_id     INTEGER                REFERENCES tiles(tile_id),
    geom        GEOMETRY(Point, 4326)  NOT NULL,
    name        TEXT                   NOT NULL,
    type        TEXT                   NOT NULL,  -- 'data_centre'
    operator    TEXT,
    dc_status   TEXT,                             -- 'operating', 'under_construction', 'planning_permission'
    capacity_mw NUMERIC(8,2),
    source_url  TEXT
);

CREATE TABLE pins_energy (
    pin_id      SERIAL                 PRIMARY KEY,
    tile_id     INTEGER                REFERENCES tiles(tile_id),
    geom        GEOMETRY(Point, 4326)  NOT NULL,
    name        TEXT                   NOT NULL,
    type        TEXT                   NOT NULL,  -- 'wind_farm', 'transmission_node', 'substation'
    capacity_mw NUMERIC(8,2),                     -- wind farms
    voltage_kv  NUMERIC(8,2),                     -- substations/transmission
    osm_id      TEXT,                             -- OSM-sourced pins
    operator    TEXT
);

CREATE TABLE pins_environment (
    pin_id         SERIAL                 PRIMARY KEY,
    tile_id        INTEGER                REFERENCES tiles(tile_id),
    geom           GEOMETRY(Point, 4326)  NOT NULL,
    name           TEXT                   NOT NULL,
    type           TEXT                   NOT NULL,  -- 'sac', 'spa', 'nha', 'pnha', 'flood_zone'
    designation_id TEXT,                             -- NPWS official site code
    area_ha        NUMERIC(12,2)                     -- total designation area
);

CREATE TABLE pins_cooling (
    pin_id         SERIAL                 PRIMARY KEY,
    tile_id        INTEGER                REFERENCES tiles(tile_id),
    geom           GEOMETRY(Point, 4326)  NOT NULL,
    name           TEXT                   NOT NULL,
    type           TEXT                   NOT NULL,  -- 'hydrometric_station', 'waterbody', 'met_station'
    station_id     TEXT,                             -- OPW station ID
    mean_flow_m3s  NUMERIC(10,3),                    -- hydrometric stations only
    waterbody_type TEXT                              -- 'river', 'lake'
);

CREATE TABLE pins_connectivity (
    pin_id     SERIAL                 PRIMARY KEY,
    tile_id    INTEGER                REFERENCES tiles(tile_id),
    geom       GEOMETRY(Point, 4326)  NOT NULL,
    name       TEXT                   NOT NULL,
    type       TEXT                   NOT NULL,  -- 'internet_exchange', 'motorway_junction', 'broadband_area'
    ix_asn     INTEGER,                           -- AS number (IXPs only)
    road_ref   TEXT,                              -- road identifier for junctions
    ix_details TEXT                               -- freeform IXP metadata
);

CREATE TABLE pins_planning (
    pin_id     SERIAL                 PRIMARY KEY,
    tile_id    INTEGER                REFERENCES tiles(tile_id),
    geom       GEOMETRY(Point, 4326)  NOT NULL,
    name       TEXT                   NOT NULL,
    type       TEXT                   NOT NULL,  -- 'zoning_parcel', 'planning_application'
    app_ref    TEXT,                             -- planning application reference
    app_status TEXT,                             -- 'granted', 'refused', 'pending', 'withdrawn'
    app_date   DATE,
    app_type   TEXT                              -- 'data_centre', 'industrial', etc.
);

-- ============================================================
-- CHILD DETAIL TABLES
-- ============================================================

-- Protected areas intersecting each tile (rendered as list in environment sidebar)
CREATE TABLE tile_designation_overlaps (
    id               SERIAL       PRIMARY KEY,
    tile_id          INTEGER      NOT NULL REFERENCES tiles(tile_id) ON DELETE CASCADE,
    designation_type TEXT         NOT NULL CHECK (designation_type IN ('SAC','SPA','NHA','pNHA')),
    designation_name TEXT         NOT NULL,
    designation_id   TEXT,        -- NPWS official site code
    pct_overlap      NUMERIC(5,2) NOT NULL CHECK (pct_overlap BETWEEN 0 AND 100)
);

-- Planning applications within each tile (rendered as list in planning sidebar)
CREATE TABLE tile_planning_applications (
    id        SERIAL  PRIMARY KEY,
    tile_id   INTEGER NOT NULL REFERENCES tiles(tile_id) ON DELETE CASCADE,
    app_ref   TEXT    NOT NULL,
    name      TEXT,
    status    TEXT    NOT NULL,  -- 'granted', 'refused', 'pending', 'withdrawn', 'other'
    app_date  DATE,
    app_type  TEXT               -- type of development
);
