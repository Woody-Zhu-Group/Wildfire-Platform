# AWS migration audit: Wildfire Services

> Historical record (2026-09-24): audit of the tree as of 2026-08-21. Its findings are left as written, but these statements no longer match the current code:
>
> - The agent's model tier is OpenRouter (GPT-6 Luna). The local Ollama/qwen path, `model_setup.py`, the GPU control service (`services/gpu_control/`, port 8005), its systemd unit, and the frontend GPU strip were removed on 2026-09-24 (see `docs/OPENROUTER.md`). Every Ollama, `AGENT_MODEL*`, `AGENT_NUM_CTX`, and GPU statement below is history.
> - The risk API now has CORS (`services/risk_forecasting/app.py` 61-67) and serves `/surface`, `/observed`, `/observed-training` and `/metrics` besides `/predict` (`app.py` 149-303). The website calls `/surface` and `/observed-training` directly (`website/src/api.ts` 105, 111), so "the frontend does not call risk" (sections 4.3, 9.1, blocker 12) is out of date. See `services/risk_forecasting/README.md`.
> - The agent no longer needs a live model to start: lifespan catches model and warmup failures and still binds 8004 (`services/agent/app.py` 33-45). Section 5.7 and blocker 3 are out of date.
> - systemd units now exist in `deploy/systemd/` for data query, risk forecasting, visualization, comparison, agent, and frontend, with uvicorn bound to `0.0.0.0` (see `deploy/systemd/SYSTEMD_SETUP.md`). Section 8 and blockers 4 and 5 are out of date on this point.
> - The current website is a Vite/TypeScript app in `website/` (`website/package.json`) built into `docs/`, with default API bases under CloudFront `/api/...` (`website/src/api.ts` 9-12). Section 4 and "no `package.json`" describe only the older `frontend/` app.
> - Agent defaults changed: `AGENT_LLM_PROVIDER` is `openrouter` only, with `OPENROUTER_API_KEY` and `AGENT_ALLOW_REMOTE_PROVIDER=true` required (`services/agent/config.py`). The loopback rule for backend URLs is still there (`config.py` 231-232). `services/agent/eval/cases.json` now has 107 cases.
> - `requirements.txt` now also lists `boto3` and `typesafe-sdk`.
> - File:line references below point to the 2026-08-21 tree and may have moved.

Read-only inventory of `C:\AI Coding Projects\Wildfire Services` as of 2026-08-21. No services were started for this audit. Sizes are from the local working tree. Where something was not observed on disk or in code, this report says **unclear**.

Git: branch `main`, in sync with `origin/main` (`2f06813`), clean working tree.

---

## 1. Service inventory

There are **five FastAPI apps** (not four): data query **8000**, risk **8001**, visualization **8002**, comparison **8003**, agent **8004**. Plus a static frontend on **8765**, PostGIS in Docker on host **5433**, and several one-shot scripts. Uvicorn’s default bind is `127.0.0.1` unless `--host` is passed; `app.py` files do not bind a port themselves.

| Name | Directory | Exact start command (as documented / used locally) | Port | Responsibility | Status |
| --- | --- | --- | --- | --- | --- |
| Data query API | `services/data_query/` | `uvicorn services.data_query.app:app --reload --app-dir .` (tests say `--port 8000`) | **8000** (uvicorn default; not set in `app.py`) | Read-only HTTP over PostGIS warehouse tables (ignitions, EPSS, PSPS, CAL FIRE, circuits, HFTD, IOU, US ignitions, spatial point/summary). | Functional (endpoints + tests exist; live process not checked this audit). |
| Risk forecasting API | `services/risk_forecasting/` | `uvicorn services.risk_forecasting.app:app --reload --app-dir .` (README); tests: `--port 8001 --app-dir .` | **8001** (convention only; not in `app.py`) | Historical place-based cNHPP ignition probability (`GET /predict`) from fitted params + local weather/veg files. | Functional if gitignored covariate files and `grid_W.pkl` are present; **no CORS**. |
| Visualization API | `services/visualization/` | `uvicorn services.visualization.app:app --port 8002 --app-dir .` | **8002** | Styled GeoJSON map layers, time series, utility territory, event detail for the Historical Map and agent. | Functional. |
| Comparison API | `services/comparison/` | `uvicorn services.comparison.app:app --port 8003 --app-dir .` | **8003** | Cross-utility / region / period metric comparison (SQL aggregations). | Functional. Frontend does not call it directly; the agent does. |
| Agent API | `services/agent/` | `uvicorn services.agent.app:app --port 8004 --app-dir .` | **8004** (comment in `app.py` line 1; port only via CLI) | Single-exchange Ask router: deterministic rules then optional local LLM + six HTTP tools over the other four APIs. | Functional **prototype** only while Ollama is up; **startup requires the model**. Map/Planning Tool do not need it. |
| Frontend static server | `frontend/` | `python frontend/serve.py` or `python frontend/serve.py --port 8765` | **8765**, bind **`127.0.0.1`** (`frontend/serve.py` 47–48) | Serves `index.html` + assets; mounts sibling `dataset_demo` at `/dataset_demo/` for Planning Tool plots. | Functional if sibling repo exists. |
| PostGIS warehouse | `docker-compose.yml` | `docker compose up -d` | Host **5433** → container **5432** | Postgres 16 + PostGIS 16-3.4; applies `db/schema.sql` on first init. | Functional as Docker service; not an application process in this repo. |
| Warehouse loaders | `db/loaders/` | `python -m db.loaders` | n/a (client of 5433) | Truncate/load all warehouse tables from `dataset_demo` + risk grid + optional US extract. | Functional one-shot. |
| Agent eval runner | `services/agent/eval/` | `python -m services.agent.eval.runner` (plus `--models` / `--thinking` / `--modes` / `--case-ids` / `--run-tag`) | Uses live **8000–8004** + Ollama **11434** | Scores `cases.json` against the running agent. | Functional harness; not a user-facing service. |
| Live API report | `tests/report_results.py` | `python tests/report_results.py` | Hits **8000** and **8001** | Prints verification counts vs CSV/SQL. | Test helper. |
| Fit cNHPP | `services/risk_forecasting/fit_model.py` | `python -m services.risk_forecasting.fit_model` | n/a | Writes `artifacts/cnhpp_params.npz`. | One-shot; not needed at API runtime if npz exists. |
| Rebuild adjacency | `services/risk_forecasting/adjacency.py` | `python -m services.risk_forecasting.adjacency` | n/a | Writes `data/grid_W.pkl`. | One-shot. |
| HRRR grid extract | `services/risk_forecasting/prep_hrrr_grid.py` | `python prep_hrrr_grid.py --grid ... --hrrr ... --year ... --out ...` (standalone; docstring) | n/a | Builds `grid_weather_YYYY.csv` from pre-extracted HRRR CSVs. **Not used by the API.** | Local extract script. |
| US ignitions extract | `db/loaders/extract_us_ignitions.py` | `python -m db.loaders.extract_us_ignitions` (also called from loader) | n/a | Reads gitignored `Wildfire_Dataset.csv`, writes gitignored extract CSV. | One-shot. |
| Analysis scripts | `analysis/`, `data/north_america/` | `python analysis/calfire_2024_jump.py`, etc. | n/a | Offline reports. | Not runtime. |

There is **no** `package.json`, **no** Node/npm app, **no** Dockerfile for the Python APIs (Compose only runs PostGIS).

Ollama (`http://127.0.0.1:11434`) is an **external** process, not in this repository.

---

## 2. Data dependencies (most important)

Paths below are as written in code. Relative paths resolve from **repo root** when `uvicorn … --app-dir .` is used, except `frontend/serve.py` which `os.chdir`s into `frontend/`.

### 2.1 Data query (`services/data_query/`): runtime

No CSV/GeoJSON reads at request time. Every request opens a new Postgres connection.

| Resource | Path as written | File:line | Format | Local size | When |
| --- | --- | --- | --- | --- | --- |
| `.env` | `REPO_ROOT / ".env"` (`C:\AI Coding Projects\Wildfire Services\.env` via `Path(__file__).parents[1]`) | `shared/db.py` 13, 20–21 | dotenv | present locally; **gitignored**; size not required | Once per process (`load_dotenv`) |
| Postgres | `POSTGRES_HOST` default `"localhost"`, port `"5433"`, db `"wildfire"` or `DATABASE_URL` | `shared/db.py` 84–89, 48–54, 105 | PostgreSQL/PostGIS | Docker volume `wildfire_pgdata`: **unclear** (not measured) | Connection at startup probe + **every request** (`get_conn` opens/closes) |
| Default demo dir (settings only; API does not read files) | `(REPO_ROOT.parent / "dataset_demo" / "assets" / "data").resolve()` | `shared/db.py` 69 | directory | 20.4 MB (+ subdirs; see 2.8) | Settings object at import/`get_settings()` |

Startup: `SELECT PostGIS_Version()` (`services/data_query/app.py` 38). Tables queried per endpoint in `services/data_query/queries.py` (`wildfire.*`).

### 2.2 Visualization (`services/visualization/`): runtime

Same as data query: **PostGIS only** at request time (`shared/db.py` + `services/visualization/queries.py`). No local GeoJSON.

`GET /map-layer` default `limit=5000`, max **20000** (`services/visualization/app.py` 31–32, 182).

### 2.3 Comparison (`services/comparison/`): runtime

PostGIS only. No local files.

### 2.4 Risk forecasting (`services/risk_forecasting/`): runtime

Defaults from `shared/paths.py` / `services/risk_forecasting/config.py`:

- `DATA_DIR` = `RISK_FORECASTING_DATA_DIR` or `{RISK_FORECASTING_ROOT}/data` or `services/risk_forecasting/data`
- `ARTIFACTS_DIR` = `RISK_FORECASTING_ARTIFACTS_DIR` or `{root}/artifacts`
- These are read after `shared/paths.py` loads the repo `.env` (the process environment wins); relative values resolve from the repo root.
- `GRID_CSV` = `DATA_DIR / "grid_cells.csv"` (`config.py` 14)
- `GRID_W_PKL` = `DATA_DIR / "grid_W.pkl"` (`config.py` 15)
- `PARAMS_PATH` = `ARTIFACTS_DIR / "cnhpp_params.npz"` (`config.py` 16)

| Resource | Path as written | File:line | Format | Local size | When |
| --- | --- | --- | --- | --- | --- |
| Fitted params | `ARTIFACTS_DIR / "cnhpp_params.npz"` | `config.py` 16; loaded `predictor.py` 150–178 | NumPy `.npz` | **2084 bytes** | **Once at startup** (`app.py` 32 `load_fitted_model()`) |
| Grid cells | `DATA_DIR / "grid_cells.csv"` | `config.py` 14; `predictor.py` 164–181 | CSV | 0.03 MB | Startup |
| Adjacency | `DATA_DIR / "grid_W.pkl"` | `config.py` 15; `predictor.py` 166–189 | pickle / scipy CSR | 0.05 MB | Startup |
| Weather by year | `data_dir / f"grid_weather_{year}.csv"` | `predictor.py` 215, 256 | CSV | 25.8–29.3 MB each (2020–2025); plus `grid_weather_2020.csv.bak_corrupt_dec` 27.8 MB **not referenced by code** | First use per year, then **in-process cache** (`_year_raw_cache`, `_weather_dates_cache`) |
| Vegetation by year | `data_dir / f"daily_gridded_CA_{year}.nc"` | `predictor.py` 216 | NetCDF | ~11.5 MB × 6 years (2020–2025) | Same as weather (via `grid_data_prep.load_vegetation_for_year`, `grid_data_prep.py` 209 `nc.Dataset`) |
| Coverage scan | `Path(data_dir).glob("grid_weather_*.csv")` | `predictor.py` 107 | CSV glob | same weather files | First coverage-end lookup, cached |
| PostGIS (place resolve) | same DSN as other APIs | `place.py` 8, 79+ `connect()` | PostgreSQL | n/a | **Every** `/predict` that uses `lat`+`lon`, `county`, or `utility` (not `cell_id` alone) |

`events_YYYY.csv` (0.02–0.03 MB × 6) are **gitignored** and used by `fit_model.py`, not by `GET /predict`.

`circuit_midpoints.csv` (0.21 MB) is in the data dir; **unclear** whether the live predict path reads it (not referenced from `predictor.py` / `app.py`).

Risk data dir total on disk: **267.09 MB**.

### 2.5 Agent (`services/agent/`): runtime

| Resource | Path as written | File:line | Format | Local size | When |
| --- | --- | --- | --- | --- | --- |
| `.env` | `load_dotenv(REPO_ROOT / ".env")` | `config.py` 13 | dotenv | gitignored | Import |
| Sibling APIs | defaults `http://127.0.0.1:8000` … `:8003` | `config.py` 52–55, 97–108 | HTTP JSON | n/a | Every tool call (`tools.py` 412–470) |
| Ollama / model | `AGENT_MODEL_BASE_URL` default `http://127.0.0.1:11434/v1` | `config.py` 26, 61–63; `provider.py` 118–133, 152–169, 303–305, 405–410 | HTTP | model weights **not in this repo** | **Startup** (`app.py` 33–35 `ensure_runtime_model` + `ensure_context_loaded`) and every **model-path** Ask |
| Artifacts | in-memory `ArtifactStore` | `app.py` 24; TTL env | n/a | n/a | Per successful viz tool |

No local CSV/NetCDF reads. Eval runner **writes** `services/agent/eval/runs/...` (`runner.py` 29, 116–118, 733–734).

### 2.6 Frontend (`frontend/`): browser runtime

Served from `frontend/`; `data-base-path="."` and `data-plots-base="../../dataset_demo/assets/website_plots"` in `frontend/index.html` 28–29. `serve.py` maps `/dataset_demo/` to sibling checkout (`serve.py` 26–27, 33–36).

**Always loaded (Historical Map HDWI):**

| Resource | Path as written | File:line | Format | Local size | When |
| --- | --- | --- | --- | --- | --- |
| Grid cells JSON | `` `${basePath}/assets/data/weather_anim` `` + `/grid_cells.json` | `sect-fasttrip-psps.js` 357, 3958 | JSON | 48.4 KB | Map init / weather layer |
| HDWI year cubes | `` `${WEATHER_ANIM_BASE}/weather_anim_${year}.json` `` | `sect-fasttrip-psps.js` 3969 | JSON | 0.75–0.82 MB × 6 years | When that year is shown |

Frontend `assets/data` total: **4.73 MB**. There are **no** copied CPUC/EPSS/CAL FIRE/HFTD GeoJSON/CSV files in this slim frontend (only `weather_anim/`).

**If visualization API is missing** (`USE_VIS_API` false), code still `fetch`es these (they **404** in this tree):

- `` `${basePath}/assets/data/cpuc_fire_incidents_combined.csv` ``: `sect-fasttrip-psps.js` 191
- `` `.../epss_outages.csv` ``: 214
- `` `.../calfire_incidents.csv` ``: 236
- `` `.../psps_events.geojson` ``: 255
- `` `.../epss_circuits.geojson` ``: 272
- `` `.../iou_territories.geojson` ``: 273
- `` `.../psps_event_circuits.json` ``: 274
- `` `.../hftd.geojson` ``: 294

With `historical-vis-api.js` present, `USE_VIS_API` is true and year layers come from `:8002`. Static fallback is dead in this checkout.

**Planning Tool (sibling `dataset_demo`, not this git repo):**

| Resource | Path as written | File:line | Format | Local size | When |
| --- | --- | --- | --- | --- | --- |
| Manifest | `` `${plotsBasePath}/manifest.json` `` → `/dataset_demo/assets/website_plots/manifest.json` | `sect-fasttrip-psps.js` 9, 5007–5008 | JSON | small | Page load |
| Planning CSV | manifest `csvFiles[0]` = `grid_plots/merged_planning_grid.csv` | `sect-fasttrip-psps.js` 4495, 5029; sibling `manifest.json` | CSV | **437,582 bytes** (~0.42 MB) | Page load |
| Method maps | `grid_plots/{folder}/exp_0_{slug}/map.png` | `sect-fasttrip-psps.js` 4255–4256, 4295, 4930 | PNG | parent `grid_plots/` **827.2 MB**; `website_plots/` **2090.59 MB** | When sliders change |
| Other plot trees | `plots/`, `historical plots/` | same plots base | PNG etc. | 1237.9 MB + 14.6 MB | If selected via CSV/manifest |

`frontend/serve.py` 26: `SIBLING_DEMO = REPO_ROOT.parent / "dataset_demo"`: requires a sibling directory named exactly `dataset_demo`.

### 2.7 Loaders (not API runtime; required to populate RDS)

Default source: `DATASET_DEMO_DATA_DIR` or `../dataset_demo/assets/data` (`shared/db.py` 69–72, `.env.example` 12).

| File | Loader | Format | Size on sibling disk |
| --- | --- | --- | --- |
| `cpuc_fire_incidents_combined.csv` | `db/loaders/load_cpuc.py` 22 | CSV | 0.21 MB |
| `cpuc_ignitions.csv` | `load_cpuc.py` 71 | CSV | 0.22 MB |
| `epss_outages.csv` | `load_epss.py` 32 | CSV | 1.31 MB |
| `epss_circuits.geojson` | `load_circuits.py` 22 | GeoJSON | 2.01 MB |
| `psps_events.geojson` | `load_psps.py` 26 | GeoJSON | 3.52 MB |
| `psps_event_circuits.json` | `load_psps.py` 79 | JSON | 0.03 MB |
| `calfire_incidents.csv` | `load_calfire.py` 31 | CSV | 1.32 MB |
| `hftd.geojson` | `load_hftd.py` 21 | GeoJSON | 1.13 MB |
| `iou_territories.geojson` | `load_iou.py` 21 | GeoJSON | 0.05 MB |
| `weather_anim/grid_cells.json` | `load_grid.py` 16 (optional row/col) | JSON | in 4.73 MB `weather_anim/` dir |
| `grid_cells.csv` | `load_grid.py` 44 from `risk_forecasting_data_dir` | CSV | 0.03 MB |
| `data/north_america/us_ignitions_extracted.csv` | `extract_us_ignitions.py` 17; `load_us_ignitions.py` 32 | CSV | 3.87 MB (gitignored) |
| `data/north_america/Wildfire_Dataset.csv` | `extract_us_ignitions.py` 16 | CSV | **1079.80 MB** (gitignored) |
| `db/schema.sql` | Docker init + `apply_schema` | SQL | n/a | Once at DB init / load |
| `db/schema_us_ignitions.sql` | `load_us_ignitions.py` 15 | SQL | n/a | Load |
| Census county zip | URLs in `load_counties.py` 27–28; cache `REPO_ROOT / "data" / "boundaries" / "cb_2023_us_county_500k.zip"` (`load_counties.py` 30–31) | shapefile zip | gitignored patterns; **unclear** if cached locally this audit (dir has `.gitkeep`) |

`dataset_demo/assets/data` total **20.4 MB** including `cache/`, `iou_shapes/`, `weather_anim/`.

### 2.8 Absolute / machine-specific paths

| Location | Path | Notes |
| --- | --- | --- |
| `analysis/calfire_2024_jump.py` **22** | `Path(r"C:\AI Coding Projects\dataset_demo")` | Hardcoded Windows absolute path. Analysis script only; not imported by APIs. |
| `analysis/calfire_2024_jump_results.json` 4 | `"C:\\AI Coding Projects\\dataset_demo\\assets\\data\\calfire_incidents.csv"` | Generated output. |
| `data/north_america/_inventory_results.json` 2 | `"C:\\AI Coding Projects\\Wildfire Services\\data\\north_america\\Wildfire_Dataset.csv"` | Generated output. |

No `C:\`, `/Users/`, or `/home/` in FastAPI service modules. Defaults use `Path(__file__).resolve().parents[1]` (relocatable) plus the **sibling name** `dataset_demo`.

### 2.9 Total data footprint (runtime vs load vs optional)

Approximate, this machine:

| Bucket | Size | Needed on AWS for |
| --- | --- | --- |
| PostGIS warehouse | **unclear** (Docker volume not measured). Source inputs ~20 MB `dataset_demo/assets/data` + grid CSV + US extract 3.87 MB. | RDS after one load. Raw files can live on S3 and be loaded once. |
| Risk covariates (`services/risk_forecasting/data`) | **267 MB** (weather CSV + NetCDF + pkl + events) | EC2 disk or EFS/S3 mount for `:8001`. Weather+NC ~ **246 MB** of that. |
| Fitted params | **2 KB** (`cnhpp_params.npz`, git-tracked) | With the API. |
| Frontend static in this repo | **5.13 MB** (mostly HDWI JSON) | S3. |
| Planning Tool plots (`dataset_demo/assets/website_plots`) | **2090.59 MB** | S3 if Planning Tool ships. `grid_plots` 827 MB + `plots` 1238 MB. |
| FireCastRL raw CSV | **1079.80 MB** | Loaders only, not API runtime. |
| Ollama GGUF weights | **not in repo** | GPU instance. |
| Agent eval runs (already in git) | 4.26 MB | Not production runtime. |

**Minimum to serve Historical Map + risk + Ask (no Planning Tool, no re-extract):** RDS (loaded) + ~267 MB risk files + 5 MB frontend + four FastAPI processes + Ollama if Ask is enabled.

**If Planning Tool is in scope:** add ~2.1 GB of PNGs/CSV from sibling `dataset_demo`.

---

## 3. Hardcoded URLs and network assumptions

`0.0.0.0` does **not** appear in application code. Frontend and uvicorn docs assume **loopback**. Binding stays `127.0.0.1` unless operators pass `--host`.

### 3.1 Frontend (breaks in a browser that is not on the EC2 localhost)

| File | Line | Context |
| --- | --- | --- |
| `frontend/assets/js/api-config.js` | 5 | `window.WILDFIRE_API_BASE = "http://127.0.0.1:8002";` |
| `frontend/assets/js/api-config.js` | 11 | `window.WILDFIRE_AGENT_BASE = "http://127.0.0.1:8004";` |
| `frontend/assets/js/api-config.js` | 17 | `window.WILDFIRE_DATA_QUERY_BASE = "http://127.0.0.1:8000";` |
| `frontend/assets/js/historical-vis-api.js` | 9 | fallback `"http://127.0.0.1:8002"` |
| `frontend/assets/js/historical-agent-api.js` | 7 | fallback `"http://127.0.0.1:8004"` |
| `frontend/assets/js/sect-fasttrip-psps.js` | 2822 | fallback `"http://127.0.0.1:8000"` for record-table refetch |
| `frontend/serve.py` | 48 | `--bind` default `"127.0.0.1"` |
| `frontend/serve.py` | 12, 62 | prints `http://{bind}:{port}/index.html` |

### 3.2 Agent config (rejects non-loopback backends unless overridden)

| File | Line | Context |
| --- | --- | --- |
| `services/agent/config.py` | 26 | default `model_base_url = "http://127.0.0.1:11434/v1"` |
| `services/agent/config.py` | 52–55 | defaults `:8000`–`:8003` on `127.0.0.1` |
| `services/agent/config.py` | 61–63 | `AGENT_MODEL_BASE_URL` default same Ollama URL |
| `services/agent/config.py` | 97–108 | `DATA_QUERY_BASE_URL`, `RISK_FORECASTING_BASE_URL`, `VISUALIZATION_BASE_URL`, `COMPARISON_BASE_URL` |
| `services/agent/config.py` | 124–136 | **`validate()`: remote model blocked unless `AGENT_ALLOW_REMOTE_PROVIDER`; all four backend URLs `must remain a loopback URL`** (`127.0.0.1` / `localhost` / `::1` only) |
| `services/agent/config.py` | 159–161 | `_is_loopback` |
| `services/agent/model_setup.py` | 38 | Ollama alias path only if `"127.0.0.1:11434" not in settings.model_base_url` is false |

### 3.3 Shared DB / Compose / env example

| File | Line | Context |
| --- | --- | --- |
| `shared/db.py` | 84 | `POSTGRES_HOST` default `"localhost"` |
| `shared/db.py` | 85 | `POSTGRES_PORT` default `"5433"` |
| `.env.example` | 2–9 | `localhost`, `5433`, commented `DATABASE_URL=postgresql://wildfire:wildfire@localhost:5433/wildfire` |
| `.env.example` | 22, 34–37 | Ollama and four API bases on `127.0.0.1` |
| `docker-compose.yml` | 12 | `"${POSTGRES_PORT:-5433}:5432"` |

### 3.4 Tests

| File | Line | Context |
| --- | --- | --- |
| `tests/conftest.py` | 17–19 | `http://127.0.0.1:8000`, `http://127.0.0.1:8001` |
| `tests/test_comparison.py` | 10 | `http://127.0.0.1:8003` |
| `tests/agent/test_streaming.py` | 168 | `base_url="http://test"` (ASGI test, not a real host) |

### 3.5 External HTTPS in product UI / loaders (CDN, tiles, census; not localhost)

| File | Line | Context |
| --- | --- | --- |
| `frontend/index.html` | 8–10 | Google Fonts |
| `frontend/index.html` | 13–16, 480–482 | unpkg Leaflet + markercluster CSS/JS |
| `frontend/index.html` | 43 | `https://arxiv.org/abs/2604.01232` |
| `frontend/index.html` | 478 | jsdelivr PapaParse |
| `frontend/index.html` | 479 | jsdelivr Plotly 2.35.2 |
| `frontend/index.html` | 483–484 | jsdelivr d3-array, d3-contour |
| `frontend/assets/js/sect-fasttrip-psps.js` | 1287 | `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png` |
| `frontend/assets/js/conformal_demo.js` | 16, 19 | cdnjs KaTeX 0.16.9 |
| `db/loaders/load_counties.py` | 27–28 | Census TIGER zip HTTPS |
| `data/north_america/_us_ignitions_state_breakdown.py` | 22 | GitHub raw us-states GeoJSON (offline script) |

### 3.6 Ports hardcoded in docs / help strings (not bind logic)

8000, 8001, 8002, 8003, 8004, 5433, 8765, 11434, 5500 appear throughout `README.md`, `AGENTS.md`, `frontend/README.md`, service READMEs, `historical-agent-panel.js` 86 and 699, `sect-fasttrip-psps.js` 603.

`README.md` 116 and `frontend/README.md` 38: Live Server URL `http://127.0.0.1:5500/Wildfire%20Services/frontend/`.

### 3.7 Eval JSON noise

`services/agent/eval/diagnostic_*_results.json` contain many `https://errors.pydantic.dev/2.13/...` strings inside captured validation errors. Not used at production runtime.

---

## 4. Frontend

### 4.1 Stack and build

**Static HTML/CSS/JS.** No React/Vue/Svelte app in `frontend/`. No `package.json`, no bundler, no build command, no `dist/`.

`frontend/assets/js/conformal_demo.js` contains JSX-like fragments for an in-page demo; it is loaded as a classic script and is not a compiled React app. **Unclear** whether that demo fully runs without a transpile step; Planning Tool + Historical Map do not depend on compiling it.

### 4.2 Asset size

| Item | Size |
| --- | --- |
| `frontend/` total | 5.13 MB |
| `frontend/assets/` | 5.07 MB |
| HDWI JSON (`assets/data/weather_anim/`) | 4.73 MB |
| JS+CSS (excluding JSON) | ~0.4 MB |
| Planning Tool plots (sibling, not in this repo) | 2090.59 MB |

### 4.3 Backend endpoints the frontend calls

Base URLs from `api-config.js` (currently loopback). Paths concatenated in JS:

**Visualization (`WILDFIRE_API_BASE`, default `http://127.0.0.1:8002`): `historical-vis-api.js`:**

- `GET /health` (line 38)
- `GET /map-layer?dataset=…&limit=20000&offset=0&year=…` plus `incident_type` for calfire, `include_outages=true` for epss (41–48)
- `GET /time-series?dataset=…&interval=weekly&…` (51–57)
- `GET /utility-territory?utility=…` (60–61)
- `GET /event-detail?dataset=…&id=…` (64–65)

Datasets used from the map loader (`sect-fasttrip-psps.js` 614–621): `ignitions`, `epss`, `calfire`, `psps`, `hftd`, `us_ignitions`.

**Agent (`WILDFIRE_AGENT_BASE`, default `http://127.0.0.1:8004`): `historical-agent-api.js`:**

- `GET /health` (10)
- `POST /ask/stream` (22)
- `GET /artifacts/{ref}` (84)

**Data query (`WILDFIRE_DATA_QUERY_BASE`, default `http://127.0.0.1:8000`): `sect-fasttrip-psps.js` 2152–2163, 2894:**

Record-table refetch only:

- `/ignitions`, `/us-ignitions`, `/epss/outages`, `/psps/events`, `/calfire/incidents`, `/circuits`, `/hftd`

The frontend does **not** call risk `:8001` or comparison `:8003`. Those are agent-side.

### 4.4 Data files loaded directly (not via backend)

**In this repo, only HDWI:** `frontend/assets/data/weather_anim/grid_cells.json` (48.4 KB) and `weather_anim_2020.json`–`2025.json` (0.75–0.82 MB each).

**Sibling Planning Tool:** `manifest.json`, `grid_plots/merged_planning_grid.csv` (0.42 MB), and PNG maps under `grid_plots/` (hundreds of MB).

Static CPUC/EPSS/etc. URLs exist in JS but the files are **absent**; live map uses the visualization API.

### 4.5 Third-party / CDN / tiles

See §3.5. Map tiles: OpenStreetMap. Fonts: Google. Scripts: unpkg Leaflet, jsdelivr Plotly/PapaParse/d3, cdnjs KaTeX (conformal demo).

---

## 5. LLM / agent integration

### 5.1 Model and how it is called

- **Configured model:** `AGENT_MODEL` default **`qwen3:4b`** (`config.py` 28, 65).
- **Provider string:** must be `openai_compatible` (`config.py` 114–115).
- **Default endpoint:** Ollama OpenAI shim `http://127.0.0.1:11434/v1` plus **native** Ollama `http://127.0.0.1:11434` (`provider.py` 118–133).
- **API key:** `AGENT_MODEL_API_KEY` default `"ollama"` sent as `Authorization: Bearer …` (`provider.py` 122).
- Not HuggingFace, not vLLM in-repo, not a hosted SaaS unless env is changed.

Thinking-off on Qwen3 creates a local Ollama alias `{model}-agent-nothink` via `POST /api/show` and `POST /api/create` (`model_setup.py` 23–77).

### 5.2 Exact invocation code path

1. `services/agent/app.py` 31–35: lifespan: `ensure_runtime_model` then `OpenAICompatibleProvider.ensure_context_loaded()`.
2. `model_setup.py` 44–70: Ollama `/api/show`, `/api/create`.
3. `provider.py` 148–181: warmup `POST /api/generate` (unload) and `POST /api/chat`.
4. Ask: `app.py` 138–142 `orchestrator.ask` → `orchestrator.py` 112 `route_question`.
5. Model loop uses `provider.complete` (`provider.py` 196–246):
   - constrained tool routing → `_complete_native_tool_envelope` → native `/api/chat` (`provider.py` 217–226, 405–410)
   - constrained synthesis → `_complete_native_structured` → native `/api/chat` with `format` JSON schema (`331+`)
   - else `_complete_openai` → `{base}/chat/completions` (`268–305`)
6. Health: `provider.py` 434–458 `GET {base}/models`.

### 5.3 Configurable vs hardcoded

Configurable via env (see §6). Defaults are loopback Ollama. **`AGENT_PROVIDER` cannot be anything except `openai_compatible`.** Remote URLs require `AGENT_ALLOW_REMOTE_PROVIDER=true`. Backend service URLs **cannot** be non-loopback even then (`config.py` 129–136).

### 5.4 Swapping to self-hosted vLLM vs a hosted API

**Not a config-only change** if you keep current `structured_mode=constrained` (the `.env.example` default).

Would need to change, concretely:

1. `AgentSettings.validate` loopback rule for `DATA_QUERY_*` / risk / viz / comparison if those APIs are on private DNS, not `127.0.0.1` (`config.py` 129–136).
2. `AGENT_ALLOW_REMOTE_PROVIDER=true` and `AGENT_MODEL_BASE_URL` / `AGENT_MODEL` / `AGENT_MODEL_API_KEY` for the GPU box or vendor.
3. **`OpenAICompatibleProvider.ensure_context_loaded`** (`provider.py` 148–169) always hits Ollama-native `/api/generate` and `/api/chat`. vLLM/OpenAI do not implement those. Lifespan would throw unless this is skipped or rewritten.
4. Default **constrained** mode uses native `/api/chat` `format=` (`_complete_native_structured`, `_complete_native_tool_envelope`). vLLM would need `structured_mode=prompt` and the OpenAI `/chat/completions` path only, plus testing that tool calls work (`tool_choice` is already known-missing on Ollama).
5. `ensure_runtime_model` Ollama `/api/create` alias is Qwen3+Ollama-specific (`model_setup.py` 34–38). Harmless if URL is not `127.0.0.1:11434` or model is not `qwen3:*`.
6. Hosted APIs often ignore Ollama `options.num_ctx` on `/chat/completions` (`provider.py` 289–290).

OpenAI-compatible `/v1` client code already exists (`_complete_openai`). The **startup warmup and constrained decoder are Ollama-native.**

### 5.5 Routing logic

`services/agent/routing.py` `route_question` runs **before** the model (`orchestrator.py` 112–191).

- Paths: `deterministic` (fixed tool calls), `clarification`, `unsupported`, or `model` (candidate tools only).
- Deterministic examples: explicit dataset+year counts, maps, trends, spatial point, county/utility/cell risk with one calendar day, comparisons with full slots.
- Relative dates resolved in harness (`time_resolve.py`), not by the model.
- Location phrasing routes to map even if the question also sounds like a count (`AGENTS.md` / routing comments).
- If the question wants risk and the model path never called `risk_forecast`, orchestrator refuses a count substitute (`orchestrator.py` 291–314).
- `AGENT_DISABLE_DETERMINISTIC_ROUTING` can force the model path (`orchestrator.py` 114–120).

Most Ask traffic can be answered **without** the LLM **if the agent process is running**. Today the process **will not start** without the LLM (next subsection).

### 5.6 Evaluation suite

- Location: `services/agent/eval/`
- Cases: `cases.json`: **62** `"id"` entries (grew past the original 27).
- Run: `python -m services.agent.eval.runner` with `--models`, `--thinking`, `--modes`, `--case-ids`, `--run-tag`, `--fresh`, `--disable-deterministic` (`runner.py` 1048–1074).
- Preflight requires live `/health` on 8000–8003 **and** `{model_base}/models` containing the model (`runner.py` 49–72).
- Stop gate: 50% model-tier routing (`STOP_THRESHOLD = 0.50`, `MIN_MODEL_CASES_FOR_STOP = 5`) (`runner.py` 34–35).
- Artifacts: `eval/runs/<cell>/trajectories.jsonl`, `*.raw.json.gz`, `summary.json`.

### 5.7 If the LLM backend is completely unavailable

**Historical Map and Planning Tool keep working** if visualization `:8002`, data query (for record tables), PostGIS, and `frontend/serve.py` are up. The Ask panel is written to survive agent failure:

- `historical-agent-panel.js` 61–91: failed `/health` or `model.available === false` → banner “Map still works” / “Agent model unavailable”, Ask input **disabled**.

**The agent process itself is not isolated at startup.** `app.py` 33–35:

```text
runtime_settings = await ensure_runtime_model(settings)
provider = OpenAICompatibleProvider(runtime_settings)
context_info = await provider.ensure_context_loaded()
```

`ensure_context_loaded` (`provider.py` 159–170) `raise_for_status()` on native `/api/chat`. If Ollama is down, **FastAPI lifespan fails** and **nothing listens on 8004**. There is no “skip model, serve deterministic-only” flag.

If 8004 were somehow up with a dead model: `GET /health` returns `model.available: false` (`provider.py` 452–457); frontend still disables Ask. Deterministic `POST /ask` is never exposed without a successful lifespan.

**Requirement “site must work fully with LLM off”:** map/planning already can; **Ask cannot**, and **agent uvicorn cannot stay up** without code changes (out of scope for this audit).

---

## 6. Configuration and secrets

### 6.1 Environment variables read in code

| Variable | File:line | Default if unset |
| --- | --- | --- |
| `POSTGRES_HOST` | `shared/db.py` 84 | `localhost` |
| `POSTGRES_PORT` | `shared/db.py` 85 | `5433` |
| `POSTGRES_DB` | `shared/db.py` 86 | `wildfire` |
| `POSTGRES_USER` | `shared/db.py` 87 | `wildfire` |
| `POSTGRES_PASSWORD` | `shared/db.py` 88 | `wildfire` |
| `DATABASE_URL` | `shared/db.py` 89 | unset (build DSN from parts) |
| `DATASET_DEMO_DATA_DIR` | `shared/db.py` 72 | `{repo.parent}/dataset_demo/assets/data` |
| `RISK_FORECASTING_DATA_DIR` | `shared/db.py` 73; `shared/paths.py` 36 | `services/risk_forecasting/data` |
| `GRID_CELL_SPACING_DEG` | `shared/db.py` 92 | `0.24` |
| `RISK_FORECASTING_ROOT` | `shared/paths.py` 32 | `services/risk_forecasting` |
| `RISK_FORECASTING_ARTIFACTS_DIR` | `shared/paths.py` 40 | `{root}/artifacts` |
| `TRAIN_YEARS` | `services/risk_forecasting/config.py` 24 | `2020,2021,2022,2023` (empty → that list) |
| `LOOKBACK_DAYS` | `config.py` 31 | `90` |
| `VAL_YEAR` | `fit_model.py` 54 | `2024` |
| `AGENT_PROVIDER` | `services/agent/config.py` 60 | `openai_compatible` |
| `AGENT_MODEL_BASE_URL` | 61–63 | `http://127.0.0.1:11434/v1` |
| `AGENT_MODEL_API_KEY` | 64 | `ollama` |
| `AGENT_MODEL` | 65 | `qwen3:4b` |
| `AGENT_MODEL_RUNTIME` | 66 | unset |
| `AGENT_THINKING` | 67 | `off` |
| `AGENT_STRUCTURED_MODE` | 68–70 | `constrained` |
| `AGENT_SYNTHESIS_THINKING` | 71 | false |
| `AGENT_TIMEOUT_SECONDS` | 73–74 | `900` (`.env.example` says `300`) |
| `AGENT_MAX_COMPLETION_TOKENS` | 76 | 1800 |
| `AGENT_MAX_ROUTING_TOKENS` | 78 | 900 |
| `AGENT_MAX_SYNTHESIS_TOKENS` | 80 | 1200 |
| `AGENT_MAX_TOOL_STEPS` | 82 | 5 |
| `AGENT_MAX_VALIDATION_RETRIES` | 84 | 2 |
| `AGENT_NUM_CTX` | 86 | 32768 |
| `AGENT_SYNTHESIS_TIMEOUT_SECONDS` | 88 | 180 |
| `AGENT_SEED` | 90 | 42 |
| `AGENT_TEMPERATURE` | 91 | 0 |
| `AGENT_ALLOW_REMOTE_PROVIDER` | 92 | false |
| `AGENT_DISABLE_DETERMINISTIC_ROUTING` | 93–95 | false |
| `AGENT_ARTIFACT_TTL_SECONDS` | 96 | 900 |
| `DATA_QUERY_BASE_URL` | 97–99 | `http://127.0.0.1:8000` |
| `RISK_FORECASTING_BASE_URL` | 100–102 | `http://127.0.0.1:8001` |
| `VISUALIZATION_BASE_URL` | 103–105 | `http://127.0.0.1:8002` |
| `COMPARISON_BASE_URL` | 106–108 | `http://127.0.0.1:8003` |
| `DATA_QUERY_BASE_URL` | `tests/conftest.py` 16–17 | same |
| `RISK_BASE_URL` | `tests/conftest.py` 19 | `http://127.0.0.1:8001` |
| `COMPARISON_BASE` | `tests/test_comparison.py` 10 | `http://127.0.0.1:8003` |
| Compose `POSTGRES_*` | `docker-compose.yml` 6–12 | same defaults |

`python-dotenv` loads repo-root `.env` (`shared/db.py` 17–31; `agent/config.py` 13). Compose also reads `.env` for substitution.

Documented but **not read by Python:** `PYTHONPATH`, `PYTHONIOENCODING` (operator notes in `AGENTS.md`).

### 6.2 Settings files (keys only)

- **`.env.example`** keys: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL` (commented), `DATASET_DEMO_DATA_DIR`, `RISK_FORECASTING_DATA_DIR` (commented), `GRID_CELL_SPACING_DEG`, `AGENT_PROVIDER`, `AGENT_MODEL_BASE_URL`, `AGENT_MODEL_API_KEY`, `AGENT_MODEL`, `AGENT_THINKING`, `AGENT_STRUCTURED_MODE`, `AGENT_SYNTHESIS_THINKING`, `AGENT_TIMEOUT_SECONDS`, `AGENT_MAX_TOOL_STEPS`, `AGENT_MAX_VALIDATION_RETRIES`, `DATA_QUERY_BASE_URL`, `RISK_FORECASTING_BASE_URL`, `VISUALIZATION_BASE_URL`, `COMPARISON_BASE_URL`.
- **`.env`**: present locally, **gitignored**, never in `git log --all -- .env`. Values not copied here.
- **`services/agent/config.py`**: `AgentSettings` dataclass (keys in §6.1).
- **`shared/db.py`**: `Settings` dataclass.
- **`services/risk_forecasting/config.py`**: paths + train years / lookback.
- No `settings.py` Django-style module.

### 6.3 Credentials in git / history

- **No** AWS keys, `sk-` tokens, or private keys found in tracked source.
- **Default local DB password `wildfire`** is in `.env.example` and `docker-compose.yml` (not a production secret, but it is committed).
- **Default model key `"ollama"`** is committed as a dummy Bearer token.
- Git LFS: client installed; **`git lfs ls-files` empty**: LFS not used.
- `.env` not in history.

### 6.4 Database connection

Yes. `shared/db.py` `connect()` → `psycopg.connect(settings.dsn)`.

Default DSN: `postgresql://wildfire:wildfire@localhost:5433/wildfire`.

Used by: data_query, visualization, comparison, `place.py` (risk), all `db/loaders`.

Risk `/predict` with only `cell_id` does not need DB; county/utility/lat-lon does.

---

## 7. Dependencies and runtime

### 7.1 Python version

**Not specified** in the repo (no `.python-version`, `runtime.txt`, or `requires-python`). Local interpreter observed for this audit: **Python 3.13.5**. Whether 3.11/3.12 is sufficient is **unclear** (no CI config in repo).

### 7.2 `requirements.txt` (only dependency file)

```
fastapi>=0.110
uvicorn[standard]>=0.27
numpy>=1.24
scipy>=1.10
pandas>=2.0
netCDF4>=1.6
pydantic>=2.0
psycopg[binary]>=3.1
python-dotenv>=1.0
httpx>=0.27
pyshp>=2.3
pytest>=8.0
```

No `pyproject.toml`, no `package.json`.

### 7.3 System libraries

| Dependency | Notes |
| --- | --- |
| **PostGIS** | Via Docker image `postgis/postgis:16-3.4` (GEOS/PROJ/GDAL **inside the container**). RDS must be **Postgres with PostGIS**. |
| **netCDF4** | Needs NetCDF-C/HDF5 if a manylinux/AL2023 wheel is not available. Flag for Amazon Linux 2023 / Ubuntu: install `netcdf`/`libhdf5` **or** confirm a binary wheel. Used at risk predict time (`grid_data_prep.py` `import netCDF4` / `nc.Dataset`). |
| **numpy / scipy** | Usually pip wheels. scipy used for sparse adjacency and optimize in fit. |
| **psycopg[binary]** | Bundles libpq; extra `libpq` often unnecessary. |
| **pyshp** | Pure Python shapefile reader for county loader only, **not GDAL**. |
| **pygrib / eccodes** | **Not in this repo.** `prep_hrrr_grid.py` reads already-extracted HRRR **CSV**. |
| **GDAL Python bindings** | **Not in requirements.** |

Unpinned `>=` ranges: reproducible EC2 images may want a lockfile. No version was identified as uniquely broken on AL2023; **netCDF4 + Python 3.13** is the combination most likely to need a wheel check.

`services/risk_forecasting/models.py` console output uses a `ξ` character; on Windows operators set `PYTHONIOENCODING=utf-8`. Linux default UTF-8 is usually fine.

---

## 8. Process management and persistence

| Mechanism | Present? |
| --- | --- |
| Dockerfile (apps) | **No** |
| docker-compose | **Yes**: PostGIS only (`restart: unless-stopped`) |
| systemd / Procfile / supervisor | **No** |
| How apps are kept running | **Manually** in terminals (`uvicorn`, `python frontend/serve.py`, Ollama separately). Documented in README / AGENTS.md. |
| Logging | `print(...)` to **stdout** (startup, per-request middleware on data_query/viz/comparison, JSON lines for agent model events). **No** `logging` module, no log files, no rotation. |
| Scheduled jobs / cron / Celery | **None** in repo. |
| Persistence | Docker volume `wildfire_pgdata`; risk file caches are **in-process RAM** (`predictor.py` `_year_raw_cache` etc.) and die with the worker; agent artifacts in memory with TTL. |

---

## 9. CORS, auth, and security

### 9.1 CORS

| Service | CORS |
| --- | --- |
| data_query | `allow_origins=["*"]`, `allow_credentials=False`, methods/headers `*` (`app.py` 56–62) |
| visualization | same (`app.py` 67–73) |
| comparison | same (`app.py` 55–61) |
| agent | same (`app.py` 72–78) |
| **risk** | **No CORS middleware.** Browser calls from an S3 origin to `:8001` would be blocked. Today the browser does not call risk. |

### 9.2 Authentication / rate limiting

**None.** No API keys, sessions, or rate limits on any FastAPI app. `Depends(get_conn)` is a DB session, not auth.

Agent security model (`services/agent/SECURITY.md`): trusted local user, loopback, read-only tools. Explicitly **not** hardened for public deployment.

### 9.3 Expensive public-abuse surfaces (if exposed)

- `GET /map-layer?limit=20000`: up to 20k GeoJSON features per dataset (`visualization/app.py` 31–32; frontend always requests `limit=20000`).
- `GET /ignitions` etc. with `limit` up to **1000** (`filters.py` 15, `MAX_LIMIT`).
- `GET /predict`: first call for a date can load a full year of weather CSV (~29 MB) + NetCDF (~11.5 MB) into RAM; percentile logic can pull **2020–2025** (`predictor.py` `LOCAL_PERCENTILE_YEARS`). Repeated distinct years → ~246 MB mapped into caches **per worker**.
- `POST /ask` / `/ask/stream`: can invoke the LLM (CPU/GPU minutes) and fan out to all four APIs. Timeouts exist (`AGENT_TIMEOUT_SECONDS`, synthesis 180s) but **no queue / auth / quota**.
- County loader HTTP to Census is **offline CLI**, not an HTTP route.
- No write endpoints on the public APIs (read-only SQL + predict). Loaders are local CLI.

---

## 10. Git state

| Item | Value |
| --- | --- |
| Branch | `main` tracking `origin/main` |
| Uncommitted changes | **None** (`git status` clean) |
| HEAD | `2f06813`: “Ship the warehouse, remaining services, Historical Map frontend, and place-based historical risk.” |
| Working tree size (all files, including gitignored) | **1380.32 MB** |
| `.git` directory | **6.25 MB** |
| Tracked files > 10 MB | **None** |
| Git LFS | Client present; **no LFS-tracked files** |
| Gitignored large data still on disk | `Wildfire_Dataset.csv` 1079.80 MB; risk `grid_weather_*.csv` + `*.nc` (~246 MB); `*.pkl` |

Eval run gzip/jsonl **are** tracked (few MB). Planning Tool PNGs live in sibling `dataset_demo`, not this git repo.

---

## Migration blockers

Priority order. Concrete, not “fix paths.”

1. **`frontend/assets/js/api-config.js` lines 5, 11, 17** hardcode `http://127.0.0.1:8002`, `:8004`, and `:8000`. Browsers loading the UI from S3 or a public ALB will call the user’s laptop, not EC2. The same fallbacks exist in `historical-vis-api.js` 9, `historical-agent-api.js` 7, `sect-fasttrip-psps.js` 2822.

2. **`services/agent/config.py` 129–136** refuse to start if `DATA_QUERY_BASE_URL`, `RISK_FORECASTING_BASE_URL`, `VISUALIZATION_BASE_URL`, or `COMPARISON_BASE_URL` are not loopback. Splitting APIs across hosts or using AWS private DNS **fails validation** even with a correct URL. GPU/hosting the model on another instance additionally needs `AGENT_ALLOW_REMOTE_PROVIDER=true` (`config.py` 124–128) **and** changes to Ollama-native startup (`provider.py` 148–170).

3. **Agent lifespan requires a live LLM** (`app.py` 33–35 → `provider.py` 159–170 `raise_for_status` on Ollama `/api/chat`). “LLM off” means **port 8004 does not stay up**. The map can still work; Ask cannot. Meeting “fully works with LLM off” needs a startup skip that does not exist.

4. **Uvicorn is not bound to `0.0.0.0`.** Documented commands omit `--host`. Default is `127.0.0.1`, so an ALB/target group on EC2 will not reach the apps until bind address changes. `frontend/serve.py` 48 also binds **`127.0.0.1` only**.

5. **No app containers / process manager.** Only PostGIS has Compose `restart`. Production needs systemd, ECS, or similar for five uvicorn workers + optional static nginx/S3. Logs are stdout `print` only.

6. **Postgres is `localhost:5433` with password `wildfire`.** RDS will need `POSTGRES_HOST` / `DATABASE_URL` and a real secret. Compose publishes 5433 to avoid Windows 5432; RDS will be 5432 (or a custom port) on a private hostname. Schema must include PostGIS (`CREATE EXTENSION postgis` in `db/schema.sql` 5).

7. **Risk files are gitignored and required.** `predictor.py` 215–223 will `FileNotFoundError` on `services/risk_forecasting/data/grid_weather_{year}.csv` and `daily_gridded_CA_{year}.nc` if those ~246 MB are not copied to the instance (or `RISK_FORECASTING_DATA_DIR` pointed at EFS/S3 mount). `grid_W.pkl` (0.05 MB, gitignored) is required at startup (`predictor.py` 166–169). `cnhpp_params.npz` is in git (2 KB).

8. **`GET /predict` county/utility/lat-lon needs PostGIS** (`place.py` + `shared/db.py`). A risk-only box without RDS (or a replica of `wildfire.grid_cells` / counties / IOU tables) cannot resolve places. Cell-id-only predict can run file-only.

9. **Warehouse load depends on sibling `dataset_demo/assets/data`** (`shared/db.py` 69 default; loaders under `db/loaders/load_*.py`). That tree is **not** in this git repo (~20.4 MB + optional 1.08 GB FireCastRL CSV). S3 should hold those objects; `DATASET_DEMO_DATA_DIR` must be set. `python -m db.loaders` must be run once against RDS.

10. **Planning Tool will 404 without sibling plots.** `frontend/index.html` 29 `data-plots-base="../../dataset_demo/assets/website_plots"` and `frontend/serve.py` 26–36 only work with a checkout named `dataset_demo` next to this repo. On S3, those ~2.1 GB of PNGs must be uploaded and the base path rewritten. `python -m http.server` from `frontend/` already 404s plots locally.

11. **HDWI JSON must ship with the static site** (`frontend/assets/data/weather_anim/*.json`, 4.73 MB). They are not served by the APIs.

12. **Risk API has no CORS.** Fine while only the agent calls it on loopback. If the browser ever calls `/predict` on another origin, requests fail.

13. **No authentication.** Putting these ports on a public ALB exposes unbounded `/map-layer?limit=20000`, `/predict` (heavy file/RAM), and `/ask` (GPU). `SECURITY.md` says the agent is loopback-only by design.

14. **OpenStreetMap tiles, Google Fonts, unpkg, jsdelivr** (`index.html` 8–16, 478–484; `sect-fasttrip-psps.js` 1287). EC2/S3 pages need egress to those CDNs, or the assets must be vendored (OSM tile ToS still applies).

15. **`netCDF4` on Amazon Linux 2023 / Ubuntu** may need OS packages if pip has no wheel for the chosen Python. There is no `pygrib`/`GDAL` pip package, but PostGIS RDS must provide spatial ops the loaders/APIs use (`ST_Contains`, GIST, etc.).

16. **In-process caches are not shared.** Multiple gunicorn/uvicorn workers will each load ~year-sized NetCDF/CSV into RAM. A single fat EC2 may be OK; many workers × 246 MB is a sizing issue.

17. **`analysis/calfire_2024_jump.py` line 22** hardcodes `C:\AI Coding Projects\dataset_demo`. Irrelevant to serving the site; will break if that script is run on Linux.

18. **Eval / Ollama `num_ctx=32768`** (`config.py` 86) implies large GPU/CPU RAM on the model host; unrelated to RDS but blocks a naive “small GPU” assumption.

This audit does not recommend architecture; it only lists what the current tree actually does.
