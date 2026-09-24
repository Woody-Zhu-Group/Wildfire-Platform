"""Startup readiness check for the risk API.

Runs once at startup and is cached. /health reads the cached result and never
runs a prediction per request.

Stages, in order (the first failure stops the check):
- fitted_params: fitted params, grid CSV, and adjacency pickle load
  (``load_fitted_model``).
- covariates: at least one grid_weather_YYYY.csv is readable and gives a last
  covariate date.
- trial_prediction: one full-grid prediction on that last covariate date with
  the default lookback returns a finite, non-negative intensity per cell.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from services.risk_forecasting.predictor import (
    FittedModel,
    last_covariate_date,
    load_fitted_model,
    predict_grid,
)

STAGE_FITTED_PARAMS = "fitted_params"
STAGE_COVARIATES = "covariates"
STAGE_TRIAL_PREDICTION = "trial_prediction"
STAGE_READY = "ready"


@dataclass(frozen=True)
class Readiness:
    ready: bool
    stage: str
    model: Optional[FittedModel] = None
    error: Optional[str] = None
    trial_date: Optional[date] = None
    trial_ms: Optional[float] = None
    checked_at: Optional[str] = None


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def check_readiness(
    data_dir: Path,
    lookback_days: int,
    *,
    loader: Callable[[], FittedModel] = load_fitted_model,
    last_date: Callable[[Path], date] = last_covariate_date,
    predictor: Callable[..., Any] = predict_grid,
    clock: Callable[[], float] = time.perf_counter,
) -> Readiness:
    checked_at = datetime.now(timezone.utc).isoformat()

    try:
        model = loader()
    except Exception as exc:  # noqa: BLE001
        return Readiness(
            ready=False,
            stage=STAGE_FITTED_PARAMS,
            error=f"Fitted params, grid, or adjacency did not load: {_describe(exc)}",
            checked_at=checked_at,
        )

    try:
        trial_date = last_date(Path(data_dir))
    except Exception as exc:  # noqa: BLE001
        return Readiness(
            ready=False,
            stage=STAGE_COVARIATES,
            model=model,
            error=f"Covariate data did not load from {data_dir}: {_describe(exc)}",
            checked_at=checked_at,
        )

    started = clock()
    try:
        lambdas = np.asarray(
            predictor(model, trial_date, Path(data_dir), lookback_days, verbose=False),
            dtype=np.float64,
        )
    except Exception as exc:  # noqa: BLE001
        return Readiness(
            ready=False,
            stage=STAGE_TRIAL_PREDICTION,
            model=model,
            error=f"Trial prediction for {trial_date} failed: {_describe(exc)}",
            trial_date=trial_date,
            trial_ms=round((clock() - started) * 1000.0, 1),
            checked_at=checked_at,
        )
    trial_ms = round((clock() - started) * 1000.0, 1)

    expected = len(model.cell_id_to_idx)
    problem = None
    if lambdas.shape != (expected,):
        problem = f"returned shape {lambdas.shape}, expected ({expected},)"
    elif not np.all(np.isfinite(lambdas)):
        problem = f"returned {int(np.sum(~np.isfinite(lambdas)))} non-finite intensities"
    elif np.any(lambdas < 0):
        problem = "returned negative intensities"
    if problem:
        return Readiness(
            ready=False,
            stage=STAGE_TRIAL_PREDICTION,
            model=model,
            error=f"Trial prediction for {trial_date} {problem}",
            trial_date=trial_date,
            trial_ms=trial_ms,
            checked_at=checked_at,
        )

    return Readiness(
        ready=True,
        stage=STAGE_READY,
        model=model,
        trial_date=trial_date,
        trial_ms=trial_ms,
        checked_at=checked_at,
    )
