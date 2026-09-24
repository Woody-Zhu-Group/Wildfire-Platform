"""FastAPI ignition-risk predict service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.risk_forecasting.config import DATA_DIR, lookback_days_from_env
from services.risk_forecasting.evaluate_metrics import (
    MetricsMismatch,
    load_verified_rows,
)
from services.risk_forecasting.observed import (
    observed_surface,
    observed_training_surface,
)
from services.risk_forecasting.place import PlaceNotFound, resolve_place
from services.risk_forecasting.predictor import (
    AGGREGATION,
    AGGREGATION_NOTE,
    CoverageError,
    FittedModel,
    score_place,
    score_surface,
)
from services.risk_forecasting.readiness import Readiness, check_readiness

_model: Optional[FittedModel] = None
_load_error: Optional[str] = None
_readiness: Optional[Readiness] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _load_error, _readiness
    print("[API] Startup: loading fitted model and running a trial prediction ...")
    _readiness = check_readiness(DATA_DIR, lookback_days_from_env())
    # /predict and /surface keep their old gate: they need only the fitted
    # model, and report missing covariates per request.
    _model = _readiness.model
    _load_error = None if _model is not None else _readiness.error
    if _readiness.ready:
        print(
            f"[API] Startup complete. Trial prediction for {_readiness.trial_date} "
            f"took {_readiness.trial_ms} ms."
        )
    else:
        print(f"[API] Startup WARNING: not ready ({_readiness.stage}): {_readiness.error}")
    yield
    print("[API] Shutdown.")


app = FastAPI(
    title="Wildfire Risk Forecasting",
    description=(
        "Historical place-based ignition risk from a fitted cNHPP model. "
        "Scores dates with local weather/vegetation files only; no live HRRR."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PlaceScope(BaseModel):
    type: str
    name: str


class PredictResponse(BaseModel):
    date: date
    risk: float = Field(
        ...,
        description="P(≥1 ignition) for the requested place: 1 - exp(-sum(λ))",
    )
    expected_count: float
    xi: float
    lookback_days: int
    aggregation: str = AGGREGATION
    aggregation_note: str = AGGREGATION_NOTE
    cell_count: int
    scope: PlaceScope
    local_percentile: float
    statewide_percentile: float
    local_period: str
    local_n: int
    cell_id: Optional[int] = None
    cell_ids: Optional[list[int]] = None
    intensity: Optional[float] = Field(
        None, description="Single-cell Poisson intensity λ"
    )
    mean_intensity: Optional[float] = None
    includes_cell_461: bool = False


class SurfaceCell(BaseModel):
    cell_id: int
    lat: float
    lon: float
    risk: float = Field(
        ...,
        description="P(≥1 ignition) for this cell: 1 - exp(-λ)",
    )
    expected_count: float
    intensity: float = Field(..., description="Single-cell Poisson intensity λ")


class SurfaceResponse(BaseModel):
    date: date
    lookback_days: int
    cells: list[SurfaceCell]


class ObservedCell(BaseModel):
    cell_id: int
    lat: float
    lon: float
    observed_count: int


class ObservedResponse(BaseModel):
    date: date
    cells: list[ObservedCell]


class ModelMetrics(BaseModel):
    model: str
    log_likelihood: float
    top5_precision: Optional[float] = Field(
        ..., description="Null when not applicable; see not_applicable_reason"
    )
    top1_precision: Optional[float] = Field(
        ..., description="Null when not applicable; see not_applicable_reason"
    )
    lift_top5: Optional[float] = Field(
        ..., description="Null when not applicable; see not_applicable_reason"
    )
    auc: float = Field(
        ...,
        description="Secondary ranking diagnostic; not a headline Poisson count metric",
    )
    not_applicable_reason: Optional[str] = Field(
        None,
        description="Why top-k precision and lift are null (a model with one intensity for every cell)",
    )


class MetricsResponse(ModelMetrics):
    xi: float
    train_years: list[int]
    eval_year: int
    eval_start: date
    eval_end: date
    params_sha256: str = Field(
        ..., description="sha256 of artifacts/cnhpp_params.npz the row was scored from"
    )
    baselines: list[ModelMetrics] = Field(
        default_factory=list,
        description="HPP and NHPP scored on the same evaluation year and training years",
    )


def _optional_float(value: str) -> Optional[float]:
    return float(value) if value not in ("", None) else None


def _model_metrics(row: dict[str, str]) -> dict[str, Any]:
    return {
        "model": row["model"],
        "log_likelihood": float(row["log_likelihood"]),
        "top5_precision": _optional_float(row["top5%_precision"]),
        "top1_precision": _optional_float(row["top1%_precision"]),
        "lift_top5": _optional_float(row["lift_top5%"]),
        "auc": float(row["AUC"]),
        "not_applicable_reason": row.get("not_applicable_reason") or None,
    }


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    detail: Optional[str] = None
    ready: bool = False
    failed_stage: Optional[str] = Field(
        None,
        description="fitted_params, covariates, or trial_prediction when not ready",
    )
    trial_date: Optional[date] = None
    trial_ms: Optional[float] = None
    checked_at: Optional[str] = None


@app.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Startup check failed"}},
)
def health():
    """Cached startup readiness. 200 only if a trial prediction ran at startup."""
    state = _readiness
    if state is None:
        body = HealthResponse(
            status="degraded",
            model_loaded=False,
            detail="Startup check has not run",
        )
        return JSONResponse(status_code=503, content=body.model_dump(mode="json"))
    body = HealthResponse(
        status="ok" if state.ready else "degraded",
        model_loaded=state.model is not None,
        detail=state.error,
        ready=state.ready,
        failed_stage=None if state.ready else state.stage,
        trial_date=state.trial_date,
        trial_ms=state.trial_ms,
        checked_at=state.checked_at,
    )
    if not state.ready:
        return JSONResponse(status_code=503, content=body.model_dump(mode="json"))
    return body


@app.get("/metrics", response_model=MetricsResponse)
def metrics() -> MetricsResponse:
    """Return the persisted cNHPP evaluation row; no model recomputation.

    Refuses (503) a table that was not scored from the committed
    artifacts/cnhpp_params.npz, rather than serving stale numbers.
    """
    try:
        rows = load_verified_rows()
    except MetricsMismatch as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    row = rows["cnhpp"]
    return MetricsResponse(
        **_model_metrics(row),
        baselines=[
            ModelMetrics(**_model_metrics(rows[name]))
            for name in ("hpp", "nhpp")
            if name in rows
        ],
        xi=float(row["xi"]),
        train_years=[int(year) for year in row["train_years"].split(";")],
        eval_year=int(row["eval_year"]),
        eval_start=date.fromisoformat(row["eval_start"]),
        eval_end=date.fromisoformat(row["eval_end"]),
        params_sha256=row["params_sha256"],
    )


@app.get("/predict", response_model=PredictResponse)
def predict(
    date: date = Query(..., description="Historical date YYYY-MM-DD"),
    cell_id: Optional[int] = Query(None, description="Grid cell ID"),
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    county: Optional[str] = Query(None, description="County name (Census TIGER)"),
    utility: Optional[str] = Query(None, description="PGE, SCE, or SDGE"),
    lookback_days: Optional[int] = Query(
        None,
        ge=1,
        description="Trailing window length (default LOOKBACK_DAYS env / 90)",
    ),
) -> PredictResponse:
    if _model is None:
        raise HTTPException(
            status_code=503,
            detail=_load_error
            or "Fitted parameters not available. Run fit_model first.",
        )

    lb = lookback_days if lookback_days is not None else lookback_days_from_env()
    print(
        f"[API] /predict date={date} cell_id={cell_id} lat={lat} lon={lon} "
        f"county={county!r} utility={utility!r} lookback_days={lb}"
    )

    try:
        place = resolve_place(
            cell_id=cell_id,
            lat=lat,
            lon=lon,
            county=county,
            utility=utility,
            known_cell_ids=_model.cell_id_to_idx.keys(),
        )
        scored = score_place(
            _model,
            place,
            on_date=date,
            data_dir=DATA_DIR,
            lookback_days=lb,
        )
    except PlaceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CoverageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload: dict[str, Any] = scored.as_response()
    return PredictResponse.model_validate(payload)


@app.get("/surface", response_model=SurfaceResponse)
def surface(
    date: date = Query(..., description="Historical date YYYY-MM-DD"),
    lookback_days: Optional[int] = Query(
        None,
        ge=1,
        description="Trailing window length (default LOOKBACK_DAYS env / 90)",
    ),
) -> SurfaceResponse:
    if _model is None:
        raise HTTPException(
            status_code=503,
            detail=_load_error
            or "Fitted parameters not available. Run fit_model first.",
        )

    lb = lookback_days if lookback_days is not None else lookback_days_from_env()
    print(f"[API] /surface date={date} lookback_days={lb}")

    try:
        payload = score_surface(_model, date, DATA_DIR, lb)
    except CoverageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SurfaceResponse.model_validate(payload)


@app.get("/observed", response_model=ObservedResponse)
def observed(
    date: date = Query(..., description="Historical date YYYY-MM-DD"),
) -> ObservedResponse:
    """Polygon containment against live warehouse CPUC ignitions.

    ``ST_Contains(grid_cells.geom, cpuc_ignitions.geom)`` for the given date.
    Points outside every 0.24° cell are dropped. Use ``/observed-training``
    for residuals against the fitted cNHPP evaluation.
    """
    print(f"[API] /observed date={date}")
    return ObservedResponse.model_validate(observed_surface(date))


@app.get("/observed-training", response_model=ObservedResponse)
def observed_training(
    date: date = Query(..., description="Historical date YYYY-MM-DD"),
) -> ObservedResponse:
    """Nearest SW-corner snap, matching how the model training set was built.

    Assigns each warehouse CPUC point with ``snap_events_to_grid`` (same
    method as ``events_YYYY.csv``). Every point lands in a cell. Use this
    endpoint for residuals against the model's own evaluation.
    """
    print(f"[API] /observed-training date={date}")
    return ObservedResponse.model_validate(observed_training_surface(date))
