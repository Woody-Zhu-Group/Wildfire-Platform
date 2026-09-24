# Data query service

FastAPI read API over the `wildfire` PostGIS schema.

## Run

```bash
# DB up + loaded first
docker compose up -d
python -m db.loaders

# PowerShell
$env:PYTHONPATH = "."
uvicorn services.data_query.app:app --reload --app-dir .
```

Open http://localhost:8000/docs

Connection settings come from repo-root `.env` via `shared/db.py` (default port **5433**).

## Endpoints (summary)

| Path | Notes |
|------|--------|
| `GET /health` | DB ping + table counts |
| `GET /ignitions` | CPUC combined; `county=` is Census name from point-in-polygon |
| `GET /us-ignitions` | FireCastRL CONUS all-cause sample (CA-heavy: ≈40% overall / ≈59% of 2024); `year`, `start_date`, `end_date`, `bbox` only. `state=` returns 400 (no state polygons loaded); there is no `utility` or `county` parameter |
| `GET /epss/outages` | PGE-only; paginated. Extra filters: `circuit_id`, `county`, `outage_type`, `cause` |
| `GET /psps/events` | Event polygons; `utility`, `year`, date range |
| `GET /psps/events/{event_name}/circuits` | `{event_name:path}` so names like `PGE PSPS Event 10/11/21` work; orphans return `geometry: null` |
| `GET /calfire/incidents` | Default types `Wildfire`,`Fire` only; `incident_type=untyped\|all` (or one exact type); `min_acres`, `county` |
| `GET /circuits` / `GET /circuits/{id}` | List filters: `circuit_id`, `division`, `substation`. IDs are 9-digit TEXT (leading zeros kept) |
| `GET /hftd` | Optional `tier=Tier 2\|Tier 3`. Full stored geometry by default (the polygons every count and point answer uses); `simplify=<degrees>` (0 < s <= 0.01) returns a display-only simplified outline and records it in `meta.geometry_simplified_degrees` |
| `GET /iou-territories` | Optional `utility`. Same `simplify` option as `/hftd` |
| `GET /spatial/point` | `lat`, `lon` (required). IOU + HFTD + grid cell + county (Census TIGER PIP) |
| `GET /spatial/summary` | Counts inside utility **or** HFTD polygon. Exactly one of `utility` / `hftd_tier`; `start_date` and `end_date` required |
| `GET /rank` | Single-dataset top-N (`dataset=cpuc_ignitions\|calfire_incidents\|epss_outages`, `group_by=county\|utility\|circuit`, `metric=count\|acres_burned`, default limit 10, cap 25). Allowed pairs: CPUC by county or utility (count), CAL FIRE by county (count or acres), EPSS by circuit (count). Ties at the cutoff are included. Not US-by-state or EPSS-by-utility (both 400). |
| `GET /grouped-counts` | All-group counts (`dataset`, `group_by=cause\|utility\|county`). Missing labels are `Not recorded`. EPSS-by-utility returns `null` for SCE/SDG&E, not 0. |
| `GET /summary` | Filtered row count plus dataset-specific metrics (events always; acres/customers/circuits/counties/utilities per dataset, from `SUMMARY_METRIC_IDS` in `services/shared/dataset_registry.py`). |
| `GET /regional-series` | EPSS-only division time series. `interval=daily\|weekly\|monthly\|quarterly`; every bucket in `[start_date, end_date]` is present, including zeros. |

Common query params: `utility`, `year`, `start_date`, `end_date`, `bbox`, `format=json|geojson`, `geometry=true|false`, `limit` (default 100, max 1000), `offset`.

Workspace aggregate routes accept `utility` and `county` where the dataset
supports them. They return geometry-free JSON and have no pagination or top-N
truncation. `/summary` and `/grouped-counts` support
`cpuc_ignitions`, `epss_outages`, `calfire_incidents`, `psps_events`, and
`us_ignitions`, with optional `start_date` and `end_date`; `/grouped-counts`
additionally requires `group_by`. `/regional-series` is EPSS-only and requires
both dates and `interval`. These routes use the implementation merged in upstream
PR #3; each path is registered once. Missing group attributes are returned as
`Not recorded`.

CAL FIRE aggregates use the existing Wildfire/Fire definition. Utility filtering
is attribute-based. Unsupported geographic filters return 400. EPSS utility
comparisons return null for SCE/SDG&E, never zero. Summary `metrics` contain
`id`, `value`, and `missing`; labels and units stay in the frontend. Empty
populations yield zero; an all-missing metric in a nonempty population yields
null. PSPS customers are summed across events, not deduplicated people.
Distinct county summaries split comma-separated source values, while county
grouping retains the original field as a category, matching prior workspace
behavior. Regional grouping uses `epss_outages.division`, including a
`Not recorded` group, and fills event-free bins with zero. All bins are clipped
to the requested range; weekly bins reset on January 1, including short year-end
bins. Existing `/rank` behavior and its 25-row cap are unchanged.

As in the existing EPSS record API, a direct non-PG&E utility filter returns an
empty aggregate population. The workspace rejects that unavailable combination
before making a request, so it is not presented as an observed zero count.

The workspace (`website/`) uses these routes only when `VITE_DATA_QUERY_URL` is
set (`website/.env.production` and `.env.development` point at the deployed
service; `.env.example` points at `http://127.0.0.1:8000`). Verify a new
deployment's responses against the warehouse before pointing the variable at it.
With that variable unset, the workspace retains its existing Visualization API
record path and browser calculations. Configured aggregate-service failures stay
visible without switching data sources. No schema changes, reloaders or model
fitting are required for this migration.

Regression coverage uses real PostgreSQL with controlled fixtures:

```powershell
# Use a disposable database with no wildfire schema; never point this at the warehouse.
$env:AGGREGATE_TEST_DSN = 'postgresql://user:password@127.0.0.1:5432/empty_test_db'
python -m pytest tests/test_workspace_aggregates.py -q
```

Tests create the fixture schema inside a transaction and roll it back after each
case. They fail if the schema already exists. Frontend aggregate-request tests
verify full category results, total consistency and no GeoJSON fallback in
configured service mode. The Node suite also covers the default record path,
deployment compatibility, missing values and calendar calculations.

Special tokens: `utility=untagged`, `incident_type=untyped`, `include_untagged=true`.

Year-to-year CAL FIRE **count** comparisons are the incident-map feed, not the Redbook census (2023→2024 listed 133→611 is a posting-threshold drop, not occurrence; median acres 70→43). Warehouse acres still track Redbook ~95–97%. See [`analysis/calfire-2024-jump.md`](../../analysis/calfire-2024-jump.md).

## Ignition counts: attribute vs spatial

| Question style | Endpoint / filter | Definition |
|----------------|-------------------|------------|
| “Tagged as PGE” | `/ignitions?utility=PGE` | `utility` column |
| “Inside PGE territory” | `/spatial/summary?utility=PGE` | `ST_Within` IOU polygon |

For **PGE 2024** these are **532** (attribute) vs **536** (spatial). The gap is points inside the polygon without a PGE tag. Document which definition you use when answering agents or stakeholders.
