-- Wildfire Services spatial warehouse (Postgres + PostGIS).
-- Applied on first container init via docker-entrypoint-initdb.d.
-- Safe to re-run: uses IF NOT EXISTS / DROP IF EXISTS only where needed.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS wildfire;

COMMENT ON SCHEMA wildfire IS
  'Map-layer and risk-grid warehouse loaded from dataset_demo + risk_forecasting. '
  'No CPZ polygons ship in dataset_demo (known gap; HFTD Tier 2/3 only).';

-- ---------------------------------------------------------------------------
-- circuits (deduped EPSS / GNA line geometry used by the map)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.circuits (
  circuit_id   TEXT PRIMARY KEY
               CHECK (circuit_id ~ '^[0-9]{9}$'),
  circuit_name TEXT NOT NULL,
  division     TEXT NOT NULL,
  substation   TEXT NOT NULL,
  geom         geometry(MultiLineString, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS circuits_geom_gix ON wildfire.circuits USING GIST (geom);
CREATE INDEX IF NOT EXISTS circuits_division_idx ON wildfire.circuits (division);

COMMENT ON TABLE wildfire.circuits IS
  'PG&E circuit line geometries from epss_circuits.geojson (EPSS/GNA subset used by the map; '
  '822 unique IDs after dedupe). Full statewide GNA is not in dataset_demo. '
  'circuit_id is TEXT with leading zeros — never cast to numeric.';

COMMENT ON COLUMN wildfire.circuits.circuit_id IS
  'Zero-padded 9-digit PG&E circuit ID (TEXT). ~44% begin with 0.';

-- ---------------------------------------------------------------------------
-- epss_outages (no FK to circuits — orphans reported at load / validation)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.epss_outages (
  id                 BIGSERIAL PRIMARY KEY,
  circuit_id         TEXT NOT NULL
                     CHECK (circuit_id ~ '^[0-9]{9}$'),
  circuit            TEXT NOT NULL,
  year               SMALLINT NOT NULL,
  start_date         DATE NOT NULL,
  end_date           DATE NOT NULL,
  county             TEXT,
  cause              TEXT,
  outage_type        TEXT,
  division           TEXT,
  customer_minutes   BIGINT,
  restoration_min    INTEGER,
  medical_baseline   INTEGER,
  life_support       INTEGER,
  schools            INTEGER,
  hospitals          INTEGER,
  geom               geometry(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS epss_outages_circuit_id_idx ON wildfire.epss_outages (circuit_id);
CREATE INDEX IF NOT EXISTS epss_outages_year_idx ON wildfire.epss_outages (year);
CREATE INDEX IF NOT EXISTS epss_outages_start_date_idx ON wildfire.epss_outages (start_date);
CREATE INDEX IF NOT EXISTS epss_outages_geom_gix ON wildfire.epss_outages USING GIST (geom);

COMMENT ON TABLE wildfire.epss_outages IS
  'PG&E EPSS / fast-trip outage events from epss_outages.csv. '
  'Redundant source column name (always equal to circuit) is dropped. '
  'cause values Unknown Cause normalized to Unknown. '
  'No FK to circuits — referential gaps are reported by loaders.';

-- ---------------------------------------------------------------------------
-- psps_events
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.psps_events (
  event_name                 TEXT PRIMARY KEY,
  utility                    TEXT NOT NULL,
  iou_raw                    TEXT NOT NULL,
  first_date_of_poc          DATE,
  deenergization_start_date  DATE,
  full_restoration_date      DATE,
  de_energization            BOOLEAN,
  customers_deenergized      INTEGER,
  year                       SMALLINT,
  geom                       geometry(MultiPolygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS psps_events_utility_idx ON wildfire.psps_events (utility);
CREATE INDEX IF NOT EXISTS psps_events_year_idx ON wildfire.psps_events (year);
CREATE INDEX IF NOT EXISTS psps_events_geom_gix ON wildfire.psps_events USING GIST (geom);

COMMENT ON TABLE wildfire.psps_events IS
  'PSPS de-energization polygons. event_name kept exact for join to psps_event_circuits.json. '
  'utility is normalized from IOU (PGE/PG&E→PGE, SDGE/SDG&E→SDGE); iou_raw preserves source IOU.';

-- ---------------------------------------------------------------------------
-- psps_event_circuits (no FK — orphans reported)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.psps_event_circuits (
  event_name   TEXT NOT NULL,
  circuit_id   TEXT NOT NULL
               CHECK (circuit_id ~ '^[0-9]{9}$'),
  circuit_name TEXT,
  PRIMARY KEY (event_name, circuit_id)
);

CREATE INDEX IF NOT EXISTS psps_event_circuits_circuit_id_idx
  ON wildfire.psps_event_circuits (circuit_id);

COMMENT ON TABLE wildfire.psps_event_circuits IS
  'PG&E circuits associated with PSPS events (psps_event_circuits.json). '
  'event_name matches psps_events.event_name exactly. No FKs — orphans reported at validation.';

-- ---------------------------------------------------------------------------
-- cpuc_ignitions (primary map source: combined CSV with utility)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.cpuc_ignitions (
  id          BIGSERIAL PRIMARY KEY,
  utility     TEXT NOT NULL,
  event_date  DATE NOT NULL,
  year        SMALLINT NOT NULL,
  source_file TEXT,
  county      TEXT,
  geom        geometry(Point, 4326) NOT NULL
);

-- Existing warehouses created before county inference: add the column in place.
ALTER TABLE wildfire.cpuc_ignitions ADD COLUMN IF NOT EXISTS county TEXT;

CREATE INDEX IF NOT EXISTS cpuc_ignitions_utility_idx ON wildfire.cpuc_ignitions (utility);
CREATE INDEX IF NOT EXISTS cpuc_ignitions_year_idx ON wildfire.cpuc_ignitions (year);
CREATE INDEX IF NOT EXISTS cpuc_ignitions_event_date_idx ON wildfire.cpuc_ignitions (event_date);
CREATE INDEX IF NOT EXISTS cpuc_ignitions_county_idx ON wildfire.cpuc_ignitions (county);
CREATE INDEX IF NOT EXISTS cpuc_ignitions_geom_gix ON wildfire.cpuc_ignitions USING GIST (geom);

COMMENT ON TABLE wildfire.cpuc_ignitions IS
  'Primary CPUC ignition layer from cpuc_fire_incidents_combined.csv (what the map uses). '
  'Related to but not identical with cpuc_ignitions_with_time: membership differs by ~180 '
  'records each way; no reconciliation is performed. Prefer this table for utility-tagged analyses. '
  'county is derived at load time by point-in-polygon against wildfire.counties (Census TIGER); '
  'NULL means the point fell outside all loaded county polygons.';

COMMENT ON COLUMN wildfire.cpuc_ignitions.county IS
  'Census county NAME (no "County" suffix), e.g. Sacramento. Spatial, not a source CSV column.';

-- ---------------------------------------------------------------------------
-- cpuc_ignitions_with_time (secondary: has time-of-day, no utility)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.cpuc_ignitions_with_time (
  id          BIGSERIAL PRIMARY KEY,
  event_date  DATE NOT NULL,
  event_time  TIME,
  year        SMALLINT NOT NULL,
  label       TEXT,
  geom        geometry(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS cpuc_ignitions_with_time_year_idx
  ON wildfire.cpuc_ignitions_with_time (year);
CREATE INDEX IF NOT EXISTS cpuc_ignitions_with_time_event_date_idx
  ON wildfire.cpuc_ignitions_with_time (event_date);
CREATE INDEX IF NOT EXISTS cpuc_ignitions_with_time_geom_gix
  ON wildfire.cpuc_ignitions_with_time USING GIST (geom);

COMMENT ON TABLE wildfire.cpuc_ignitions_with_time IS
  'Secondary CPUC ignition points from cpuc_ignitions.csv. Includes time-of-day but no utility tag. '
  'Membership is not a subset of cpuc_ignitions; keep both and do not merge.';

-- ---------------------------------------------------------------------------
-- calfire_incidents
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.calfire_incidents (
  incident_id              TEXT PRIMARY KEY,
  incident_name            TEXT,
  incident_type            TEXT,
  acres_burned             DOUBLE PRECISION,
  containment              DOUBLE PRECISION,
  control                  TEXT,
  county                   TEXT,
  location                 TEXT,
  administrative_unit      TEXT,
  cooperating_agencies     TEXT,
  utility                  TEXT,
  date_created             TIMESTAMPTZ,
  date_only_created        DATE,
  date_last_update         TIMESTAMPTZ,
  date_extinguished        TIMESTAMPTZ,
  date_only_extinguished   DATE,
  is_final                 BOOLEAN,
  is_active                BOOLEAN,
  is_calfire_incident      BOOLEAN,
  notification_desired     BOOLEAN,
  incident_url             TEXT,
  geom                     geometry(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS calfire_incidents_type_idx
  ON wildfire.calfire_incidents (incident_type);
CREATE INDEX IF NOT EXISTS calfire_incidents_utility_idx
  ON wildfire.calfire_incidents (utility);
CREATE INDEX IF NOT EXISTS calfire_incidents_date_only_created_idx
  ON wildfire.calfire_incidents (date_only_created);
CREATE INDEX IF NOT EXISTS calfire_incidents_geom_gix
  ON wildfire.calfire_incidents USING GIST (geom);

COMMENT ON TABLE wildfire.calfire_incidents IS
  'CAL FIRE incident points. 1970-01-01 sentinel dates loaded as NULL. '
  'Blank utility tags left NULL (not imputed). Mixed Y/N and True/False encodings '
  'normalized to boolean columns. Non-wildfire incident_type values (Flood, Earthquake, Hazmat) '
  'are retained — filter in queries. Source column incident_administrative_unit_url dropped (all null).';

-- ---------------------------------------------------------------------------
-- hftd_tiers (no CPZ in source repo)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.hftd_tiers (
  tier          TEXT PRIMARY KEY,
  objectid      INTEGER,
  shape_length  DOUBLE PRECISION,
  shape_area    DOUBLE PRECISION,
  geom          geometry(MultiPolygon, 4326) NOT NULL
);

-- Rebuilt geometry audit columns (added after first release; idempotent).
ALTER TABLE wildfire.hftd_tiers ADD COLUMN IF NOT EXISTS geom_source geometry(MultiPolygon, 4326);
ALTER TABLE wildfire.hftd_tiers ADD COLUMN IF NOT EXISTS publisher_area_m2 DOUBLE PRECISION;
ALTER TABLE wildfire.hftd_tiers ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE wildfire.hftd_tiers ADD COLUMN IF NOT EXISTS source_edited_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS hftd_tiers_geom_gix ON wildfire.hftd_tiers USING GIST (geom);

COMMENT ON TABLE wildfire.hftd_tiers IS
  'CPUC High Fire Threat District Tier 2 / Tier 3 polygons, rebuilt from the CPUC '
  'FeatureServer Esri JSON with rings by orientation (holes kept). The loader refuses '
  'a geometry that is invalid or whose EPSG:3310 area is more than 0.1% from '
  'publisher_area_m2. geom_source is the old simplified dataset_demo hftd.geojson '
  'geometry, kept for audit only (invalid; do not query it; NULL where dataset_demo '
  'was absent at load, as on EC2). '
  'KNOWN GAP: no CPZ (Circuit Protection Zone) data. '
  'shape_area / shape_length are the publisher attributes in EPSG:3310 units.';

-- ---------------------------------------------------------------------------
-- iou_territories
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.iou_territories (
  utility      TEXT PRIMARY KEY,
  utility_name TEXT NOT NULL,
  geom         geometry(MultiPolygon, 4326) NOT NULL
);

-- Rebuilt geometry audit columns (added after first release; idempotent).
ALTER TABLE wildfire.iou_territories ADD COLUMN IF NOT EXISTS geom_source geometry(MultiPolygon, 4326);
ALTER TABLE wildfire.iou_territories ADD COLUMN IF NOT EXISTS publisher_area_m2 DOUBLE PRECISION;
ALTER TABLE wildfire.iou_territories ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE wildfire.iou_territories ADD COLUMN IF NOT EXISTS source_edited_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS iou_territories_geom_gix ON wildfire.iou_territories USING GIST (geom);

COMMENT ON TABLE wildfire.iou_territories IS
  'CPUC IOU_Service_Territories (IOU_Service_Territory_20240812), rebuilt from Esri JSON '
  'with rings by orientation, so municipal-utility holes stay holes. Same 0.1% area gate '
  'as hftd_tiers. geom_source is the old simplified dataset_demo geometry (audit only; NULL where '
  'dataset_demo was absent at load).';


-- ---------------------------------------------------------------------------
-- counties (Census TIGER / cartographic boundary; CA only for now)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.counties (
  geoid    TEXT PRIMARY KEY,
  name     TEXT NOT NULL,
  statefp  TEXT NOT NULL,
  geom     geometry(MultiPolygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS counties_geom_gix ON wildfire.counties USING GIST (geom);
CREATE INDEX IF NOT EXISTS counties_name_idx ON wildfire.counties (lower(name));

COMMENT ON TABLE wildfire.counties IS
  'US county polygons from Census cartographic boundary files (TIGER-derived). '
  'Loader currently inserts California (STATEFP=06) only. name is Census NAME '
  '(no "County" suffix) so filters match CAL FIRE / agent county slots.';

-- ---------------------------------------------------------------------------
-- grid_cells (824-cell risk / weather grid)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.grid_cells (
  cell_id  INTEGER PRIMARY KEY,
  row      INTEGER,
  col      INTEGER,
  lat      DOUBLE PRECISION NOT NULL,
  lon      DOUBLE PRECISION NOT NULL,
  geom     geometry(Polygon, 4326) NOT NULL,
  centroid geometry(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS grid_cells_geom_gix ON wildfire.grid_cells USING GIST (geom);
CREATE INDEX IF NOT EXISTS grid_cells_centroid_gix ON wildfire.grid_cells USING GIST (centroid);

COMMENT ON TABLE wildfire.grid_cells IS
  '824-cell California risk grid (0.24° spacing) from risk_forecasting/data/grid_cells.csv. '
  'lat/lon are the SW corner of each cell; geom is the 0.24°×0.24° polygon; centroid is derived. '
  'Cells are not equal-area in EPSG:4326 — do not use ST_Area(geom) for acres/hectares without '
  'casting to geography or a projected CRS.';

-- ---------------------------------------------------------------------------
-- us_ignitions (FireCastRL / IRWIN-derived CONUS sample)
-- Same DDL as schema_us_ignitions.sql (kept separate for ensure_table on existing DBs).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS wildfire.us_ignitions (
  id          BIGSERIAL PRIMARY KEY,
  event_date  DATE NOT NULL,
  year        SMALLINT NOT NULL,
  latitude    DOUBLE PRECISION NOT NULL,
  longitude   DOUBLE PRECISION NOT NULL,
  pr          DOUBLE PRECISION,
  rmax        DOUBLE PRECISION,
  rmin        DOUBLE PRECISION,
  sph         DOUBLE PRECISION,
  srad        DOUBLE PRECISION,
  tmmn        DOUBLE PRECISION,
  tmmx        DOUBLE PRECISION,
  vs          DOUBLE PRECISION,
  bi          DOUBLE PRECISION,
  fm100       DOUBLE PRECISION,
  fm1000      DOUBLE PRECISION,
  erc         DOUBLE PRECISION,
  etr         DOUBLE PRECISION,
  pet         DOUBLE PRECISION,
  vpd         DOUBLE PRECISION,
  geom        geometry(Point, 4326) NOT NULL,
  UNIQUE (latitude, longitude, event_date)
);

CREATE INDEX IF NOT EXISTS us_ignitions_year_idx ON wildfire.us_ignitions (year);
CREATE INDEX IF NOT EXISTS us_ignitions_event_date_idx ON wildfire.us_ignitions (event_date);
CREATE INDEX IF NOT EXISTS us_ignitions_geom_gix ON wildfire.us_ignitions USING GIST (geom);

COMMENT ON TABLE wildfire.us_ignitions IS
  'IRWIN-derived ignition points from the FireCastRL / Kaggle US Wildfire Dataset (2014–2025). '
  'One row per positive 75-day sequence; ignition date is the first Yes day. '
  'CONUS classification sample — not a complete census of US ignitions. '
  'Not utility-attributed; NOT comparable to wildfire.cpuc_ignitions (utility-caused only). '
  'Synthesized negatives and Int16-sentinel (32767) sequences excluded at extract. '
  'Temperatures tmmn/tmmx are Kelvin (GRIDMET).';
