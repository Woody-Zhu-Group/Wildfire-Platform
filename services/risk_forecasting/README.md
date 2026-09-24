# Historical risk forecasting service

FastAPI service (port **8001**) that scores historical ignition risk on the 824-cell California grid (0.24 degree spacing) with a fitted cNHPP (convolutional non-homogeneous Poisson process) model. It wraps `models.py` and `grid_data_prep.py` without changing them.

It is a hindcast service. It scores dates for which local weather and vegetation files exist. There is no live HRRR ingest and no forecast path.

The website's risk surface and residual map panels call `/surface` and `/observed-training`. The agent calls `/predict` (`risk_forecast`) and `/surface` (the router-only statewide surface).

## Run

From the repo root:

```bash
uvicorn services.risk_forecasting.app:app --port 8001 --app-dir .
```

On Windows (PowerShell), set the module path and console encoding first. `models.py` prints the `ξ` character, which fails on a non-UTF-8 console:

```powershell
$env:PYTHONPATH = "."
$env:PYTHONIOENCODING = "utf-8"
uvicorn services.risk_forecasting.app:app --port 8001 --app-dir .
```

There is no systemd unit for this service in `deploy/systemd/`.

At startup the app loads the fitted parameters, `grid_cells.csv` and `grid_W.pkl`. If any of them is missing, the process still starts: `/health` reports `degraded` with the load error, and `/predict` and `/surface` return 503. The service never fits the model on its own.

`/observed`, `/observed-training` and `/metrics` do not use the loaded model.

## Endpoints

All endpoints are `GET`. CORS allows every origin.

### `GET /health`

Returns `status` (`ok` or `degraded`), `model_loaded`, and `detail` (the load error when degraded).

### `GET /predict`

Scores one place on one date.

| Parameter | Notes |
|---|---|
| `date` | Required, `YYYY-MM-DD` |
| `cell_id` | Grid cell ID |
| `lat` + `lon` | Both required together. The point must fall inside a grid cell polygon (`ST_Contains`). |
| `county` | Census TIGER county name. `Sacramento` and `Sacramento County` both match (case-insensitive). Cells are those whose polygon intersects the county. |
| `utility` | `PGE`, `SCE` or `SDGE`. Cells are those whose polygon intersects the IOU territory. |
| `lookback_days` | Optional, at least 1. Default `LOOKBACK_DAYS` or 90. |

Give exactly one of `cell_id`, `lat`+`lon`, `county` or `utility`. `lat`+`lon`, `county` and `utility` are resolved in the PostGIS warehouse (`wildfire.grid_cells`, `wildfire.counties`, `wildfire.iou_territories`), so they need the database. `cell_id` alone does not.

Response fields:

| Field | Meaning |
|---|---|
| `risk` | P(at least one ignition) for the place: `1 - exp(-sum(λ))` over its cells, treated as independent Poisson cells |
| `expected_count` | `sum(λ)` over the place's cells |
| `xi`, `lookback_days` | Fitted memory parameter and the window used |
| `aggregation`, `aggregation_note` | `p_at_least_one` and a text description of the formula |
| `cell_count`, `scope` | Number of cells and `{type, name}` (`cell`, `point`, `county` or `utility`) |
| `cell_id`, `intensity` | Only for a single-cell place. `intensity` is λ. |
| `cell_ids` | Listed only when the place has 24 cells or fewer |
| `mean_intensity` | Only for multi-cell places |
| `local_percentile`, `local_period`, `local_n` | The place's P(at least one) against the same place on every day of the same calendar month in 2020 to 2025 whose full lookback window has weather rows. `local_n` is the number of reference days. |
| `statewide_percentile` | The place's mean λ against all 824 cell λ values on the same date |
| `includes_cell_461` | True when cell 461 (no vegetation data, see caveats) is one of the scored cells |

Errors: 404 for an unknown cell, county or utility, or a point outside the grid; 400 for an invalid parameter combination, a date outside coverage, or missing input files; 503 when the model is not loaded.

The first request for a given calendar month and lookback also scores every eligible day of that month in 2020 to 2025 to build `local_percentile`. That makes the first call slow. Covariates and λ vectors are cached in process memory after that.

### `GET /surface`

Parameters: `date` (required) and `lookback_days` (optional). Scores all 824 cells in one forward pass. Returns `date`, `lookback_days` and `cells`, where each cell has `cell_id`, `lat`, `lon`, `risk` (`1 - exp(-λ)`), `expected_count` and `intensity` (both equal to λ).

### `GET /observed`

Parameter: `date`. Counts warehouse CPUC ignitions (`wildfire.cpuc_ignitions`) for that date per grid cell polygon with `ST_Contains`. Points outside every cell are dropped. Returns `date` and `cells` (`cell_id`, `lat`, `lon`, `observed_count`).

### `GET /observed-training`

Parameter: `date`. Same response shape as `/observed`, but each warehouse CPUC point is assigned to the nearest grid SW corner with `grid_data_prep.snap_events_to_grid`, the same method that built the training `events_YYYY.csv` files. Every point lands in a cell. Use this one for residuals against the model.

### `GET /metrics`

Returns the `cNHPP` row of `outputs/metrics_table.csv`: `model`, `log_likelihood`, `top5_precision`, `top1_precision`, `lift_top5`, `auc`, and where it came from: `xi`, `train_years`, `eval_year`, `eval_start`, `eval_end`, and `params_sha256`. Nothing is recomputed.

It returns 503, naming the problem and the command to fix it, unless the table was scored from the committed `artifacts/cnhpp_params.npz`: the row's `params_sha256` must equal that file's sha256, its log-likelihood must equal the file's `val_log_likelihood`, and its metrics must differ from the NHPP row while `xi` is not 0. A missing file or row is also a 503.

The committed table scores HPP, NHPP, and cNHPP on 2024 (366 days, 741 events), each trained on 2020 to 2023; cNHPP uses the committed `xi = 0.2` and `beta`:

| Model | Log-likelihood | Top 5% precision | Top 1% precision | AUC | Lift, top 5% |
|---|---|---|---|---|---|
| HPP | -5204.19 | 0.00019 | 0.0 | 0.500 | 0.08 |
| NHPP | -4877.62 | 0.00534 | 0.00299 | 0.759 | 2.18 |
| cNHPP | -4873.63 | 0.00486 | 0.00349 | 0.760 | 1.98 |

HPP's precision and lift come from ties: every cell has the same intensity, so its "top" cells are an arbitrary slice. The cNHPP and NHPP log-likelihoods differ by about 4 on 2024, inside the day-bootstrap noise in `outputs/model_comparison.csv`; treat them as a tie (see the caveats below). The table that was here before issue #60 came from the last run of an earlier session, before the restructure (see `DATA_STATUS.md`), whose parameters were not kept. Its cNHPP and NHPP rows were identical (log-likelihood -4390.0, AUC 0.7718), which is what cNHPP gives at xi = 0.0, where it reduces exactly to NHPP, so that run's xi search chose 0.0. It did not describe the committed fit.

## Coverage

- A date is scoreable only if `grid_weather_YYYY.csv` and `daily_gridded_CA_YYYY.nc` exist for its year, the date is not after the latest date in any `grid_weather_*.csv`, and every day in the lookback window has weather rows. Otherwise the API returns 400. After the last covariate date the message is `Covariates end YYYY-MM-DD and no forecast ingestion exists.`
- 2020-12-02 through 2020-12-31 are always rejected (corrupt HRRR export, see caveats), including when they fall inside a later date's lookback window.
- NaN covariate values in the window are filled with the training means stored in the parameters file.

## Files

| Path | Tracked in git | Used by |
|---|---|---|
| `artifacts/cnhpp_params.npz` | yes | API startup (required) |
| `data/grid_cells.csv` | yes (824 cells) | API startup, adjacency, fit, loaders |
| `data/circuit_midpoints.csv` | yes | `legacy/` circuit-level code only |
| `data/grid_W.pkl` | no (`*.pkl`) | API startup (required), `compare_models`, `evaluate_metrics` |
| `data/grid_weather_YYYY.csv` | no | `/predict`, `/surface`, fit, compare, audit |
| `data/daily_gridded_CA_YYYY.nc` | no (`*.nc`) | `/predict`, `/surface`, fit, compare, audit |
| `data/events_YYYY.csv` | no | fit, compare, and `evaluate_metrics` only |
| `outputs/metrics_table.csv` | yes | `/metrics`; written by `evaluate_metrics` |
| `outputs/model_comparison.csv` | yes | written by `compare_models` |
| `outputs/covariate_audit.json` | yes | written by `audit_covariates` |
| `outputs/monthly_performance.csv` | yes | not read by the service |

A fresh clone has only the tracked files. Weather, vegetation and event files must be supplied separately, and `grid_W.pkl` must be rebuilt (below) before the API can load the model.

`cnhpp_params.npz` stores `xi`, `beta` (intercept plus five covariates), `means` and `stds` (standardization), `train_years`, `val_year`, `train_log_likelihood`, `val_log_likelihood`, `log_likelihood` (equal to the validation value) and `converged`. The committed file has `xi = 0.2`, train years 2020 to 2023 and validation year 2024.

Covariate order is TMP, SPFH, wind_speed, NDVI, fm100, after the intercept.

## Environment variables

| Variable | Default | Read by |
|---|---|---|
| `RISK_FORECASTING_ROOT` | `services/risk_forecasting` | `shared/paths.py` |
| `RISK_FORECASTING_DATA_DIR` | `{root}/data` | `shared/paths.py` (also `shared/db.py` for the grid loader) |
| `RISK_FORECASTING_ARTIFACTS_DIR` | `{root}/artifacts` | `shared/paths.py` |
| `LOOKBACK_DAYS` | `90` | `config.py`, read on each request that omits `lookback_days` |
| `TRAIN_YEARS` | `2020,2021,2022,2023` | `config.py`, used by `fit_model` |
| `VAL_YEAR` | `2024` | `fit_model.py` |

The path variables are read when `config.py` is imported, after `shared/paths.py` loads the repo `.env`, so they can be set in `.env` or the process environment (the process wins). Relative paths resolve from the repo root, not the working directory. Database settings (`POSTGRES_*`, `DATABASE_URL`) come from `shared/db.py` as for the other services.

## Rebuild adjacency

```bash
python -m services.risk_forecasting.adjacency
```

Builds a 4-connected grid adjacency (N, S, E, W plus a self-loop, row-normalized) from `grid_cells.csv` and writes `data/grid_W.pkl`. It prints `nnz` and average neighbors. Expected: **nnz about 3922** and **about 3.8 average neighbors**. Check those numbers before any fit.

## Fit (research use only)

```bash
python -m services.risk_forecasting.fit_model
```

Needs `grid_weather_YYYY.csv`, `daily_gridded_CA_YYYY.nc` and `events_YYYY.csv` for every training year and the validation year, and exits with a list of missing files otherwise. It rebuilds `grid_W.pkl` first, drops 2020-12-02 to 2020-12-31, standardizes with training-year statistics, then searches `xi` over 0.0 to 0.9 in steps of 0.1. For each `xi` it fits `beta` on the training years (L-BFGS-B) and keeps the `xi` with the best validation-year log-likelihood. The result overwrites `artifacts/cnhpp_params.npz`. Because the validation year selects `xi`, it is not an independent test set for that fit.

The fit loads years directly with `grid_data_prep.load_weather_for_year` and `load_vegetation_for_year` and concatenates per-year event files. Do not call `grid_data_prep.load_year()`: its signature is broken.

## Other scripts

- `python -m services.risk_forecasting.compare_models [--holdouts 2022,2023,2024] [--n-boot 5000]`: for each holdout year, fits HPP, NHPP and cNHPP on the other years of 2020 to 2024 (cNHPP `xi` chosen by training log-likelihood), scores the holdout, and runs a day-blocked bootstrap of the cNHPP minus NHPP log-likelihood gap. Needs an existing `grid_W.pkl`. Writes `outputs/model_comparison.csv`.
- `python -m services.risk_forecasting.evaluate_metrics`: rewrites `outputs/metrics_table.csv` from the committed `cnhpp_params.npz`. It rebuilds the validation year and the training years with the fit's loaders, scores cNHPP with the committed `xi`, `beta`, and standardization, and refuses to write unless that log-likelihood reproduces the file's `val_log_likelihood`. HPP and NHPP are fit on the same training years; precision, AUC, and lift come from `analysis.all_metrics`. Rerun it after every refit, or `/metrics` returns 503. Needs the same data files as the fit and an existing `grid_W.pkl`.
- `python -m services.risk_forecasting.audit_covariates`: summary statistics and corruption checks on 2020 to 2023 raw covariates. Writes `services/risk_forecasting/outputs/covariate_audit.json` relative to the working directory, so run it from the repo root.
- `prep_hrrr_grid.py`: standalone extractor from pre-extracted `California_HRRR_daily*.csv` files to `grid_weather_YYYY.csv` (`date, cell_id, lat, lon, TMP, SPFH, wind_speed`). It rejects any day whose median TMP is outside 200 to 330 K. Example: `python services/risk_forecasting/prep_hrrr_grid.py --grid services/risk_forecasting/data/grid_cells.csv --hrrr "California_HRRR_daily*.csv" --year 2024 --out grid_weather_2024.csv`. The API does not use it.
- `analysis.py` and `legacy/` (`data_prep.py`, `main.py`, `prep_hrrr.py`) are the earlier circuit-level pipeline, kept for reference only. The service does not import them, except that `evaluate_metrics` uses `analysis.py`'s metric functions (`all_metrics`, `topk_precision`, `daily_auc`), which need only `requirements.txt`. Its plots need `matplotlib` (and `geopandas` for the spatial map): `pip install -r requirements-analysis.txt`. AUC is computed in numpy with average ranks for ties, the value `sklearn.metrics.roc_auc_score` gives, so scikit-learn is not needed.

## Scientific caveats

- **cNHPP vs NHPP is a tie.** On the corrected 824-cell weather grid, the out-of-sample log-likelihood gap flips sign across holdout years and every 95% bootstrap interval covers 0 (`outputs/model_comparison.csv`). The spatial memory term adds little here.
- **December 2020 weather was dropped.** In `California_HRRR_daily_2020_01.csv`, 2020-12-02 to 2020-12-31 had TMP holding SPFH-scale values. Those 30 days are excluded rather than replaced with another hour of the day, which would add a systematic temperature offset.
- **SPFH has a positive coefficient.** Specific humidity is not a dryness measure. The intended fix is to replace it with VPD or RH.
- **fm100 is largely redundant with TMP** (training correlation about -0.64), so its coefficient is near zero.
- **Cell 461 has no vegetation data.** NDVI and fm100 are all-NaN in every vegetation file and are mean-filled, so its vegetation terms carry no information. Cells 71, 439 and 521 are all-NaN for fm100 only.
- **Grid resolution.** Risk is per 0.24 degree cell. County and utility scores aggregate every cell that touches the boundary, so edge cells count in full.
