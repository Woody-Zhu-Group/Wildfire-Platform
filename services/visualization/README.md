# Visualization service

FastAPI helpers that mirror `dataset_demo` map/chart conventions: styled GeoJSON layers, time-series buckets, utility territory bounds, and click-to-inspect detail.

## Run

```bash
# PostGIS up + loaded
docker compose up -d
python -m db.loaders

$env:PYTHONPATH = "."
uvicorn services.visualization.app:app --port 8002 --app-dir .
```

Docs: http://127.0.0.1:8002/docs

## Endpoints

| Path | Purpose |
|------|---------|
| `GET /health` | DB ping + ignition definition notes |
| `GET /map-layer` | Styled GeoJSON for ignitions / us_ignitions / EPSS / PSPS / CAL FIRE / HFTD (`dataset=circuits` returns 400; use `epss`) |
| `GET /time-series` | daily \| weekly \| monthly count buckets for ignitions / us_ignitions / EPSS / PSPS / CAL FIRE |
| `GET /utility-territory` | `utility` (required). IOU polygon + bbox + suggested center |
| `GET /event-detail` | Full attributes + website-ordered detail fields |

CORS is enabled (`*`) so a local frontend on another port can call this API.

### `/map-layer`

- **EPSS** returns **circuit lines** (aggregated event counts), not outage points.
- **EPSS** `include_outages=true`: embed filtered outage rows on each circuit feature (day scrubber / popups).
- Missing circuit geometries → features with `geometry: null` (not dropped).
- Filters: `utility`, `year`, `start_date`, `end_date`, `county`, `outage_type`, `cause`, `min_acres`, `incident_type`, `tier` (HFTD), `bbox`, `limit`, `offset`.
- Default `limit=5000` (max 20000). The UIs request 20000.
- `us_ignitions` style color is **`#dc2626`**. GitHub Pages `docs/` hardcodes that red; local `frontend/` still hardcodes teal `#0f766e` on the layer swatch until that copy is synced.

### `/event-detail` extras

| dataset | id | Nested |
|---------|-----|--------|
| circuits | 9-digit `circuit_id` | `outages` (optional `year` / date filters) |
| psps | `event_name` | `affected_circuits` |

### `/time-series`

- **weekly** uses website calendar bins: week 0 = Jan 1–7 of `year` (required).
- **daily** / **monthly** use `start_date`/`end_date`, or the full `year` if only `year` is set.
- CAL FIRE defaults to `Wildfire`/`Fire` types (same as data_query).

### `/event-detail` id keys

| dataset | id |
|---------|-----|
| ignitions | integer `id` |
| epss | integer `id` |
| psps | `event_name` |
| calfire | `incident_id` |
| us_ignitions | integer `id` |
| circuits | `circuit_id` (9-digit, zero-padded) |

## Ignition counting: two definitions

“How many PG&E ignitions in 2024?” has **two defensible answers**:

| Definition | Where used | PGE 2024 example |
|------------|------------|------------------|
| **Attribute**: `cpuc_ignitions.utility = 'PGE'` | This service (`/map-layer`, `/time-series` with `utility=`), data_query `/ignitions?utility=` | **532** |
| **Spatial**: point inside PGE IOU polygon (`ST_Within`) | data_query `/spatial/summary?utility=PGE` | **536** |

The 4-row gap is ignitions that fall inside PGE’s territory polygon but are **not** tagged `PGE` in the CSV (other utility or untagged). Neither answer is wrong; agents should say which definition they used.

## Styles (website)

| Dataset | Color |
|---------|-------|
| Ignitions | `#c0440e` |
| US Ignitions (IRWIN / all-cause) | `#dc2626` |
| EPSS | `#7c3aed` |
| CAL FIRE | `#b91c1c` (acres via bubble size, not a second hue) |
| PSPS | `#1d6fa5` (fillOpacity 0.25) |
| HFTD | `#d97706` for both tiers; Tier 3 uses higher fillOpacity (0.38 vs 0.16) |

`us_ignitions` is a CONUS FireCastRL sample (not a census, not utility-attributed). Meta always includes `not_comparable_to=cpuc_ignitions` and `sample_geography` (California ≈40% overall / ≈59% of 2024). `utility` or `county` filters on `/map-layer` or `/time-series` return 400.
