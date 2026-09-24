# Wildfire PostGIS warehouse

Local Postgres + PostGIS for map-layer and risk-grid data. Schema and loaders live here; source CSVs/GeoJSON stay in the sibling `dataset_demo/` repo (read-only).

## Quick start

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or any Docker engine) so `docker compose` can run PostGIS. A bare local Postgres install is not enough unless PostGIS is installed separately.

```bash
# from Wildfire Services/
cp .env.example .env          # edit paths/passwords if needed
docker compose up -d          # waits until healthy
pip install -r requirements.txt

# from repo root so `db` is importable
set PYTHONPATH=.              # Windows PowerShell: $env:PYTHONPATH = "."
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
| `DATASET_DEMO_DATA_DIR` | Path to `dataset_demo/assets/data` |
| `RISK_FORECASTING_DATA_DIR` | Path containing `grid_cells.csv` |
| `GRID_CELL_SPACING_DEG` | Default `0.24` |

## Tables (`wildfire` schema)

| Table | Source |
|-------|--------|
| `circuits` | `epss_circuits.geojson` (deduped) |
| `epss_outages` | `epss_outages.csv` |
| `psps_events` | `psps_events.geojson` |
| `psps_event_circuits` | `psps_event_circuits.json` |
| `counties` | Census TIGER 1:500k cartographic counties, California (`STATEFP=06`) only |
| `cpuc_ignitions` | `cpuc_fire_incidents_combined.csv` (county tagged at load via `ST_Covers`) |
| `cpuc_ignitions_with_time` | `cpuc_ignitions.csv` |
| `us_ignitions` | FireCastRL `Wildfire_Dataset.csv` → `us_ignitions_extracted.csv` (gitignored; CONUS all-cause IRWIN sample, not comparable to CPUC) |
| `calfire_incidents` | `calfire_incidents.csv` |
| `hftd_tiers` | CPUC `CPUC_High_Fire_Threat_District` FeatureServer, Esri JSON cached in `data/boundaries/cpuc_hftd.esri.json` (no CPZ in source) |
| `iou_territories` | CPUC `IOU_Service_Territories` FeatureServer, Esri JSON cached in `data/boundaries/cpuc_iou_service_territories.esri.json` |
| `grid_cells` | `services/risk_forecasting/data/grid_cells.csv` |

HFTD and IOU polygons: both tables load together in one transaction (`db/loaders/load_boundaries.py`). Holes are kept by reading ring orientation, and the load is refused unless every geometry is valid and within 0.1% of the publisher's `Shape__Area`. The source Esri JSON must be in `data/boundaries/` before loading; seed it with `python -m db.loaders.rebuild_boundaries --fetch-only` on a machine with network access (the files are gitignored). If the cache is missing and CPUC cannot be reached, `load_all` keeps the previous boundary rows, loads every other table, and exits non-zero. `geom_source` holds the old simplified dataset_demo geometry for audit (NULL where dataset_demo is absent). The dataset_demo `hftd.geojson` and `iou_territories.geojson` files are no longer loaded; the visualization map layers simplify the stored geometry for display. See `docs/DATA_CHANGE_HFTD_IOU.md`.

National ignitions: place `data/north_america/Wildfire_Dataset.csv` locally, then `python -m db.loaders.extract_us_ignitions` (or let `load_us_ignitions` extract on first load). Both the 1.13 GB source and the extracted CSV are gitignored.

All geometries are EPSG:4326 with GIST indexes. Circuit IDs are `TEXT` (9 digits, leading zeros preserved). There are **no FKs** from outages/PSPS links to `circuits`; orphans are reported at load and in the final validation step.

## Idempotency

Each loader `TRUNCATE … RESTART IDENTITY` then re-inserts inside a transaction. Safe to re-run.

Loaders open the DB connection with `autocommit=True` so each table load commits (a prior bug left nested savepoints uncommitted on close, so data vanished after the loader process exited).

## AWS RDS later

Same `schema.sql` and loaders work against RDS Postgres with PostGIS enabled — point `DATABASE_URL` / `POSTGRES_*` at the instance and skip Compose.
