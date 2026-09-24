# Wildfire PostGIS warehouse

Local Postgres + PostGIS for map-layer and risk-grid data. Schema and loaders live here; source CSVs/GeoJSON stay in the sibling `dataset_demo/` repo (read-only), except the HFTD and IOU polygons, which come from cached CPUC sources in `data/boundaries/`.

## Quick start

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or any Docker engine) so `docker compose` can run PostGIS. A bare local Postgres install is not enough unless PostGIS is installed separately.

```bash
# from the repo root
cp .env.example .env          # edit paths/passwords if needed
docker compose up -d          # add --wait to block until the healthcheck passes
pip install -r requirements.txt

# from repo root so `db` is importable
export PYTHONPATH=.           # cmd: set PYTHONPATH=.   PowerShell: $env:PYTHONPATH = "."
python -m db.loaders
```

Parse-only smoke check (no Docker / DB required):

```bash
python -m db.loaders.dry_run_parse
```

Expected console output from a full load: per-table read/clean/insert counts, orphan warnings (if any), then a final validation summary.

## Configuration

| Variable | Purpose |
|----------|---------|
| `POSTGRES_HOST/PORT/DB/USER/PASSWORD` | Connection (also used by Compose + `shared/db.py`). Default host port is **5433** so a local Windows Postgres on 5432 does not intercept connections. Settings are loaded from the repo-root `.env` via `python-dotenv`. |
| `DATABASE_URL` | Optional full DSN override |
| `DATASET_DEMO_DATA_DIR` | Path to `dataset_demo/assets/data` (default: sibling `../dataset_demo/assets/data`; relative paths resolve from the repo root) |
| `RISK_FORECASTING_DATA_DIR` | Path containing `grid_cells.csv` (default `services/risk_forecasting/data`) |
| `GRID_CELL_SPACING_DEG` | Default `0.24` |

## Tables (`wildfire` schema)

| Table | Source |
|-------|--------|
| `circuits` | `epss_circuits.geojson` (deduped) |
| `epss_outages` | `epss_outages.csv` |
| `psps_events` | `psps_events.geojson` |
| `psps_event_circuits` | `psps_event_circuits.json` |
| `counties` | Census TIGER 1:500k cartographic counties (`cb_2023_us_county_500k.zip`, downloaded to `data/boundaries/` on first load), California (`STATEFP=06`) only |
| `cpuc_ignitions` | `cpuc_fire_incidents_combined.csv` (county tagged at load via `ST_Covers`) |
| `cpuc_ignitions_with_time` | `cpuc_ignitions.csv` |
| `us_ignitions` | FireCastRL `Wildfire_Dataset.csv` → `us_ignitions_extracted.csv` (gitignored; CONUS all-cause IRWIN sample, not comparable to CPUC). Same DDL is in `schema.sql` and `db/schema_us_ignitions.sql` (applied by `ensure_table` on existing DBs) |
| `calfire_incidents` | `calfire_incidents.csv` |
| `hftd_tiers` | CPUC `CPUC_High_Fire_Threat_District` FeatureServer, Esri JSON cached in `data/boundaries/cpuc_hftd.esri.json` (no CPZ in source) |
| `iou_territories` | CPUC `IOU_Service_Territories` FeatureServer, Esri JSON cached in `data/boundaries/cpuc_iou_service_territories.esri.json` |
| `grid_cells` | `services/risk_forecasting/data/grid_cells.csv` (optional row/col from `dataset_demo` `weather_anim/grid_cells.json`) |

HFTD and IOU polygons: both tables load together in one transaction (`db/loaders/load_boundaries.py`). Holes are kept by reading ring orientation, and the load is refused unless every geometry is valid and within 0.1% of the publisher's `Shape__Area`. The source Esri JSON must be in `data/boundaries/` before loading; seed it with `python -m db.loaders.rebuild_boundaries --fetch-only` on a machine with network access (the files are gitignored). If the cache is missing and CPUC cannot be reached, `load_all` keeps the previous boundary rows, loads every other table, and exits non-zero. `geom_source` holds the old simplified dataset_demo geometry for audit (NULL where dataset_demo is absent). The load tests use a small committed fixture, `tests/fixtures/boundaries/*.esri.json` (rebuild with `python tests/fixtures/boundaries/make_fixture.py`), so they never need the cache or the network. The dataset_demo `hftd.geojson` and `iou_territories.geojson` files are no longer loaded; the visualization map layers simplify the stored geometry for display. See `docs/DATA_CHANGE_HFTD_IOU.md`.

National ignitions: place `data/north_america/Wildfire_Dataset.csv` locally, then `python -m db.loaders.extract_us_ignitions`. `python -m db.loaders` re-runs the extract on every full load and skips `us_ignitions` with a message if the source CSV is missing. Both the 1.13 GB source and the extracted CSV are gitignored.

All geometries are EPSG:4326 with GIST indexes. Circuit IDs are `TEXT` (9 digits, leading zeros preserved). There are **no FKs** from outages/PSPS links to `circuits`; orphans are reported at load and in the final validation step.

## Measured dataset coverage

After validation, `load_all` measures what was just loaded and writes `shared/dataset_coverage.json` (`db/loaders/coverage.py`): for each dataset, its first and last row date, row count, rows per calendar year (`years`; a year with no rows is absent, and rows with no date are in no year), which utilities it has rows for with each one's first and last date, row count, and rows per year, and its rows with no utility (what an `untagged` filter can count), with the same fields. EPSS outages and circuits have no utility column; their loaders read PG&E's published files, so their rows are attributed to that source utility (`SOURCE_UTILITY` in `load_epss.py` and `load_circuits.py`) and their dates and counts are measured. Coverage is measured on exactly the rows each count reads. A dataset whose counts can read different rows has query definitions in the registry (`DatasetSpec.query_definitions`: a WHERE predicate each, the default first). CAL FIRE has three, one per agent `incident_type_mode`: the default incident types (`CALFIRE_DEFAULT_INCIDENT_TYPES` in `services/shared/naming.py`), all types, and untyped. The loader measures each one: the entry's top level is the default definition's measurement, with its name and predicate in `definition` and `where`, and the others sit under `definitions`. The loader holds no predicate of its own, so a change to a definition in the registry is measured on the next run; until the file is regenerated, the registry refuses to read coverage measured for other definitions (any coverage read raises and names the command below). The file is committed and generated: never edit it by hand. Regenerate it against the current warehouse, without reloading, with:

```bash
python -m db.loaders.coverage
```

The registry (`services/shared/dataset_registry.py`), every service, the agent, and the website read this file; none declares coverage. `tests/agent/test_measured_coverage.py` re-measures the warehouse (skipped when it is not reachable) and fails if the committed file differs, so a reload that changes coverage must be committed with its regenerated file. As loaded on 2026-09-24: CPUC ignitions have rows for PacifiCorp (from 2025-04-24), PG&E, SCE, and SDG&E, 2020-01-01 to 2025-12-24; PSPS events for PG&E, SCE, SDG&E, and Liberty (from 2024-11-11), 2021-10-11 to 2025-11-09; EPSS outages for PG&E, 2021-11-01 to 2025-11-15; CAL FIRE incidents of the default incident types 2009-05-24 to 2026-08-03 (2,509 rows), with one row in 2009 and none from 2010 to 2013, and none for Bear Valley (all 141 CAL FIRE rows in 2013, and Bear Valley's three rows, have no incident type, so only `incident_type_mode` all or untyped counts them); circuits for PG&E. `tests/agent/test_measured_coverage.py` also checks that data_query's CAL FIRE count for each definition, year, and utility equals the measured rows.

The year counts are what an offer is checked against: the agent offers an alternative only where the dataset has rows for that utility in the asked period, not where its date window merely overlaps it (SDG&E's PSPS window spans 2022, but it has no 2022 rows). A calendar year in which a dataset has no rows at all (CAL FIRE 2010 to 2012) is not covered for any utility. The time resolver's first year (`DATA_YEAR_MIN`) and the website's year list (`WORKSPACE_YEARS`) are derived from these counts as well.

## Idempotency

Each loader runs `TRUNCATE … RESTART IDENTITY CASCADE` then re-inserts inside a transaction. Safe to re-run.

Loaders open the DB connection with `autocommit=True` so each table load commits (a prior bug left nested savepoints uncommitted on close, so data vanished after the loader process exited).

## AWS RDS later

Same `schema.sql` and loaders work against RDS Postgres with PostGIS enabled. Point `DATABASE_URL` / `POSTGRES_*` at the instance and skip Compose.
