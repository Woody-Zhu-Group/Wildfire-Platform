# Wildfire Services

Modular service layer for wildfire risk research. The first service wraps an existing **cNHPP** (convolutional Non-homogeneous Poisson Process) ignition model and exposes historical cell/date risk scores via FastAPI.

## Layout

```text
shared/                         # cross-service utilities (paths, db)
db/                             # PostGIS schema + loaders (map layers + risk grid)
tests/                          # live API verification suite
frontend/                       # local UI (map + Planning Tool; python frontend/serve.py)
docs/                           # GitHub Pages copy (map-only; needs .nojekyll)
services/data_query/            # read API over warehouse tables
services/visualization/         # styled GeoJSON / time series / detail
services/comparison/            # cross-utility / region / period metrics
services/agent/                 # local-LLM routing feasibility harness
services/gpu_control/           # optional EC2/Ollama control on port 8005
services/risk_forecasting/
  models.py                     # HPP / NHPP / cNHPP (do not modify lightly)
  grid_data_prep.py             # grid data loaders (do not modify lightly)
  adjacency.py                  # rebuild W from grid_cells.csv
  fit_model.py                  # fit + persist xi/beta
  predictor.py                  # trailing-window scoring
  app.py                        # FastAPI service
  data/                         # local data (large files gitignored)
  artifacts/                    # cnhpp_params.npz after fit
  legacy/                       # superseded circuit-level code (reference only)
```

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### PostGIS warehouse (map layers + grid)

See [`db/README.md`](db/README.md). Requires Docker. Default DB port is **5433** (so local Windows Postgres on 5432 is not shadowed).

```bash
cp .env.example .env
docker compose up -d
# PowerShell: $env:PYTHONPATH = "."
python -m db.loaders
```

Source GeoJSON/CSV is read from the sibling `dataset_demo/assets/data` repo (read-only).

### Data query API

Read endpoints over the warehouse (`services/data_query/`):

```bash
# PowerShell: $env:PYTHONPATH = "."
uvicorn services.data_query.app:app --reload --app-dir .
```

- Docs: http://localhost:8000/docs  
- Examples: `/ignitions`, `/us-ignitions`, `/epss/outages`, `/psps/events`, `/calfire/incidents`, `/circuits`, `/hftd`, `/iou-territories`, `/spatial/point`, `/spatial/summary`, `/rank`  
- Common params: `utility`, `year`, `county`, `start_date`, `end_date`, `bbox`, `format=json|geojson`, `geometry=true|false`, `limit`, `offset`. CPUC `county` is inferred at load from lat/lon against Census TIGER California polygons (`wildfire.counties`). `/spatial/point` returns that county. Circuit IDs are TEXT 9-digit zero-padded — never numeric.  
- CAL FIRE defaults to `incident_type in (Wildfire, Fire)` (use `untyped` / `all`). Year-to-year CAL FIRE **count** comparisons are a map-feed artifact (2023→2024 listed 133→611; posting threshold dropped, median acres 70→43). That is not a 4.6× fire year — Redbook counts rose ~10%; warehouse acres still track Redbook ~95–97%. See [`analysis/calfire-2024-jump.md`](analysis/calfire-2024-jump.md).  
- `GET /rank` is single-dataset top-N (`group_by` county|utility|circuit); it rejects `us_ignitions` and EPSS-by-utility. Say “top N of M” and keep ties.  
- Verification: `python tests/report_results.py` (data_query :8000, risk :8001)

**Ignition counts — two definitions:** `utility=` filters use the CSV **attribute** tag; `/spatial/summary` uses **polygon containment**. For PGE 2024 these differ by 4 rows (inside territory but not tagged PGE). See `services/visualization/README.md`.

### Visualization API

Styled GeoJSON / time series / territory / detail for agents and UIs (`services/visualization/`):

```bash
uvicorn services.visualization.app:app --port 8002 --app-dir .
```

- Docs: http://localhost:8002/docs  
- `/map-layer` (EPSS = circuit **lines**; `us_ignitions` is red `#dc2626`, off-by-default in the UI), `/time-series`, `/utility-territory`, `/event-detail`

### Comparison API

```bash
uvicorn services.comparison.app:app --port 8003 --app-dir .
```

- Docs: http://localhost:8003/docs
- `/compare-utilities`, `/compare-regions`, `/compare-periods` — see [`services/comparison/README.md`](services/comparison/README.md)

### Agent prototype

Read-only single-exchange router over all four services:

```bash
uvicorn services.agent.app:app --port 8004 --app-dir .
```

The deterministic tier handles fully specified reads, maps, comparisons,
rankings, and risk lookups before invoking local Qwen3 through Ollama. Seven
grouped HTTP tools, strict validation, response-contract checks, bounded
retries, payload summarization, and deterministic caveat injection prevent
ungrounded answers.

Run the staged 4B baseline with:

```bash
python -m services.agent.eval.runner --models qwen3:4b --thinking off --modes prompt
```

See [`services/agent/README.md`](services/agent/README.md) and the explicit
[`services/agent/SECURITY.md`](services/agent/SECURITY.md) threat boundary.

The agent binds `:8004` even when Ollama is down. Deterministic routes
(counts, maps, rankings) keep working; model-tier questions return a clear
offline sentence (HTTP 200), not a 500. `/health` stays a cheap `/v1/models`
probe and does not warm the model. The website Ask panel uses SSE
`POST /ask/stream`; leave `POST /ask` unchanged for eval. Relative dates and
ranges like `2021 to 2025` / `August 2023 to September 2024` are resolved in
the harness, not by the model.

### Optional legacy GPU control

The current test deployment uses the CPU model at `AGENT_MODEL_BASE_URL`.
`gpu_control` remains available for a future explicitly configured EC2/Ollama
resource; it is not on the agent request path.

```bash
uvicorn services.gpu_control.app:app --port 8005 --app-dir .
```

`GPU_INSTANCE_ID`, `GPU_OLLAMA_URL`, and `GPU_MODEL` have no defaults. When
they are absent, status reports disabled and EC2 is not called. An explicitly
configured service also requires `X-GPU-Control-Token` for start/stop. See
[`services/gpu_control/README.md`](services/gpu_control/README.md).

### Frontend (Historical Map) and GitHub Pages

Local UI: [`frontend/`](frontend/README.md) (map **and** Planning Tool). GitHub Pages: [`docs/`](docs/README.md) (map-only; Planning Tool removed).

```bash
# visualization :8002 (and other APIs as needed)
python frontend/serve.py
# Open http://127.0.0.1:8765/index.html
```

`serve.py` mounts sibling `dataset_demo` at `/dataset_demo/` so Planning Tool PNGs resolve. Do **not** run `python -m http.server` from `frontend/` — those plots 404. Pages API bases are CloudFront; local `frontend/assets/js/api-config.js` stays on 127.0.0.1. Verification notes: [`frontend/VERIFICATION.md`](frontend/VERIFICATION.md).

US Ignitions (`wildfire.us_ignitions`) are an IRWIN/FireCastRL all-cause sample (33,457 positives; not a census; not for cNHPP). Map color is **`#dc2626`**. Not comparable to CPUC or CAL FIRE. See [`docs/dataset-comparison-cpuc-calfire-us.md`](docs/dataset-comparison-cpuc-calfire-us.md).

Place (or keep) local data under `services/risk_forecasting/data/`:

| File | Tracked? |
|------|----------|
| `grid_cells.csv` | yes |
| `circuit_midpoints.csv` | yes |
| `grid_weather_YYYY.csv` | no |
| `events_YYYY.csv` | no |
| `daily_gridded_CA_YYYY.nc` | no |
| `grid_W.pkl` | no (rebuilt by fit / adjacency) |

## Fit the model

Default train years: **2020–2023** (2024 held out).

```bash
# from repo root
python -m services.risk_forecasting.adjacency   # rebuild W only
python -m services.risk_forecasting.fit_model   # rebuild W + fit + persist
```

Artifacts land in `services/risk_forecasting/artifacts/cnhpp_params.npz`.

Adjacency sanity check after rebuild: **nnz ≈ 3922**, **avg neighbors ≈ 3.8**.

## Run the API

```bash
uvicorn services.risk_forecasting.app:app --reload --app-dir .
```

- `GET /health`
- `GET /predict` — exactly one place: `cell_id` **or** `lat`+`lon` **or** `county` **or** `utility` (PGE/SCE/SDGE), plus required `date`
- Optional: `&lookback_days=30` (default **90**, overridable via `LOOKBACK_DAYS`)

The model outputs Poisson intensity λ. The primary `risk` field is **P(≥1 ignition)** for the requested place: `1 - exp(-sum(λ_i))` (independent cells; documented because cNHPP vs NHPP OOS ΔLL is a statistical tie). `expected_count` is `sum(λ)` so large territories that saturate near 1 stay interpretable. Single-cell `intensity` is λ; multi-cell responses include `mean_intensity`. Place cells come from warehouse polygons (`wildfire.grid_cells` ∩ counties / IOU territories). A batch wrapper scores all 824 cells in one forward pass.

Also returned: `local_percentile` (this place’s P(≥1) vs the same place on complete-window days in that calendar month, 2020–2025) and `statewide_percentile` (this place’s mean cell intensity vs all 824 cells on that date).

**Coverage:** weather/vegetation files run through **2025-12-31**. There is **no live HRRR ingest** and no forecast path. Dates after that end (or missing year files) return HTTP 400: `Covariates end 2025-12-31 and no forecast ingestion exists. This service scores historical dates only.` Dec 2–31 2020 were dropped (corrupt HRRR export), not covered by the 2025 message.

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `RISK_FORECASTING_DATA_DIR` | `services/risk_forecasting/data` | Data root |
| `RISK_FORECASTING_ARTIFACTS_DIR` | `services/risk_forecasting/artifacts` | Params root |
| `TRAIN_YEARS` | `2020,2021,2022,2023` | Fit years |
| `LOOKBACK_DAYS` | `90` | Trailing window for `/predict` |

## Known issues

### `grid_data_prep.load_year()` is broken

`load_year()` passes an integer `N` into `load_weather_for_year(..., grid_df)`, which expects a DataFrame. **Do not call `load_year()`.** The fit and predict wrappers call `load_weather_for_year` / `load_vegetation_for_year` directly instead. `prepare_multiyear()` also expects a single combined events CSV; this repo uses per-year `events_YYYY.csv` files, so the service layers concatenate those itself.

`models.py` and `grid_data_prep.py` are left unchanged on purpose; new code wraps them.

### Cell 461 vegetation is all-NaN

Grid cell `461` has all-NaN `NDVI` and `fm100` in every available vegetation NetCDF year. The fit/predict wrappers mean-fill those values from the training column means, so **predictions for cell 461 are not meaningfully data-driven on vegetation** (weather covariates still apply). Related: cells `71`, `439`, and `521` have all-NaN `fm100` only. Excluding those four cells from a refit does not materially change coefficients (they hold 0 train events).

### SPFH coefficient is a covariate-semantics issue

After fixing Dec 2020 weather, cNHPP still fits a **positive** SPFH coefficient. That is not a data bug: specific humidity is not a dryness measure (warm air holds more moisture). Train TMP–SPFH correlation is moderate (~0.31 overall, ~0.44 within-cell; summer slightly negative). The eventual fix is to replace SPFH with **VPD or RH**, consistent with the lab’s fire-weather work.

### fm100 is largely redundant with TMP

Train Pearson(TMP, fm100) ≈ **−0.64**. Once TMP is estimated correctly (β ≈ +0.55), fm100’s coefficient collapses toward zero (~−0.02) because temperature already carries the warm/dry seasonal signal. That shrink is collinearity, not NaN dilution or a loader bug.

### December 2020 weather in `grid_weather_2020.csv`

`California_HRRR_daily_2020_01.csv` has a mid-file column shift for 2020-12-02…12-31 (TMP holds SPFH-scale values; real Kelvin sits under `Total Cloud Cover`). No clean same-hour (01Z) replacement exists. Other hours (06/12/18Z) are clean but systematically colder by ~2–6 K vs 01Z in November (~0.3–0.7× daily TMP std), so they were **not** substituted.

**Resolution:** those 30 days are removed from `grid_weather_2020.csv` and excluded from training. `prep_hrrr_grid.py` now rejects any day whose median TMP is outside 200–330 K. Fit selects `xi` by **2024 validation** log-likelihood (`VAL_YEAR`, default 2024).

## HPP vs NHPP vs cNHPP (corrected data)

Leave-one-year-out on 2022/2023/2024 (train = other years in 2020–2024; Dec 2–31 2020 excluded). cNHPP ξ selected by train LL. Uncertainty on ΔLL = cNHPP − NHPP via day-blocked bootstrap (5000 resamples).

| Holdout | NHPP val LL | cNHPP val LL | ΔLL | Bootstrap SE | 95% CI | P(Δ≤0) |
|---------|-------------|--------------|-----|--------------|--------|--------|
| 2022 | −4246.0 | −4248.6 | **−2.6** | 7.9 | [−20.6, +9.8] | 0.58 |
| 2023 | −3419.9 | −3424.0 | **−4.2** | 10.7 | [−28.0, +11.2] | 0.61 |
| 2024 | −4877.6 | −4873.9 | **+3.7** | 5.6 | [−9.0, +12.3] | 0.24 |

**Verdict: tie.** ΔLL flips sign across years, every 95% CI covers 0, and \|ΔLL\| is ≪ SE (~0.1% of \|NHPP LL\|). Fixing Dec 2020 does not change the prior finding that spatial memory adds little on this 824-cell weather grid. Rerun: `python -m services.risk_forecasting.compare_models`.

## Scope

Historical dates only for years with local covariate files. No live HRRR ingestion in this service.
