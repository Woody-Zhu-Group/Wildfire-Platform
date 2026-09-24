"""Risk /health reports the cached startup check: 200 only when a trial prediction ran."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from services.risk_forecasting import app as risk_app
from services.risk_forecasting.readiness import (
    STAGE_COVARIATES,
    STAGE_FITTED_PARAMS,
    STAGE_TRIAL_PREDICTION,
    check_readiness,
)

CELLS = 5
FAKE_MODEL = SimpleNamespace(cell_id_to_idx={cell: cell for cell in range(CELLS)})
TRIAL_DATE = date(2025, 12, 31)


def _loader():
    return FAKE_MODEL


def _last_date(_data_dir: Path) -> date:
    return TRIAL_DATE


def _predictor(values):
    calls = []

    def predictor(model, on_date, data_dir, lookback_days, *, verbose):
        calls.append((on_date, lookback_days))
        return np.asarray(values, dtype=np.float64)

    predictor.calls = calls
    return predictor


def _raise(exc):
    def fail(*_args, **_kwargs):
        raise exc

    return fail


# check_readiness stages


def test_ready_when_params_covariates_and_trial_prediction_succeed():
    predictor = _predictor([0.01] * CELLS)
    state = check_readiness(
        Path("data"), 90, loader=_loader, last_date=_last_date, predictor=predictor
    )
    assert state.ready and state.stage == "ready" and state.error is None
    assert state.model is FAKE_MODEL
    assert state.trial_date == TRIAL_DATE
    assert state.trial_ms is not None and state.trial_ms >= 0
    assert predictor.calls == [(TRIAL_DATE, 90)]


def test_missing_params_fail_at_fitted_params():
    state = check_readiness(
        Path("data"),
        90,
        loader=_raise(FileNotFoundError("Missing fitted parameters: cnhpp_params.npz")),
        last_date=_last_date,
        predictor=_predictor([0.01] * CELLS),
    )
    assert not state.ready and state.stage == STAGE_FITTED_PARAMS
    assert state.model is None
    assert "Missing fitted parameters: cnhpp_params.npz" in state.error


def test_missing_covariates_fail_at_covariates_but_keep_the_model():
    state = check_readiness(
        Path("data"),
        90,
        loader=_loader,
        last_date=_raise(FileNotFoundError("No grid_weather_YYYY.csv files under data")),
        predictor=_predictor([0.01] * CELLS),
    )
    assert not state.ready and state.stage == STAGE_COVARIATES
    assert state.model is FAKE_MODEL
    assert "No grid_weather_YYYY.csv files" in state.error


@pytest.mark.parametrize(
    "predictor, message",
    [
        (_raise(ValueError("lookback window incomplete")), "lookback window incomplete"),
        (_predictor([0.01, np.nan, 0.01, 0.01, 0.01]), "1 non-finite"),
        (_predictor([0.01, -0.5, 0.01, 0.01, 0.01]), "negative"),
        (_predictor([0.01, 0.01]), "shape"),
    ],
)
def test_bad_trial_prediction_fails_at_trial_prediction(predictor, message):
    state = check_readiness(
        Path("data"), 90, loader=_loader, last_date=_last_date, predictor=predictor
    )
    assert not state.ready and state.stage == STAGE_TRIAL_PREDICTION
    assert message in state.error
    assert state.trial_date == TRIAL_DATE


# /health over the app


@pytest.fixture
def client_with(monkeypatch):
    def make(**overrides):
        kwargs = {
            "loader": _loader,
            "last_date": _last_date,
            "predictor": _predictor([0.01] * CELLS),
        }
        kwargs.update(overrides)
        real = check_readiness

        def fake_check(data_dir, lookback_days):
            return real(data_dir, lookback_days, **kwargs)

        monkeypatch.setattr(risk_app, "check_readiness", fake_check)
        return TestClient(risk_app.app), kwargs["predictor"]

    return make


def test_health_200_when_ready(client_with):
    client, _ = client_with()
    with client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["ready"] is True
    assert body["failed_stage"] is None
    assert body["detail"] is None
    assert body["trial_date"] == "2025-12-31"


def test_health_503_with_reason_when_params_fail(client_with):
    client, _ = client_with(loader=_raise(FileNotFoundError("Missing adjacency pickle: grid_W.pkl")))
    with client:
        response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["model_loaded"] is False
    assert body["ready"] is False
    assert body["failed_stage"] == "fitted_params"
    assert "Missing adjacency pickle: grid_W.pkl" in body["detail"]


def test_health_503_when_covariates_fail_and_predict_keeps_its_old_gate(client_with):
    client, _ = client_with(last_date=_raise(FileNotFoundError("No grid_weather_YYYY.csv files")))
    with client:
        response = client.get("/health")
        assert response.status_code == 503
        body = response.json()
        assert body["model_loaded"] is True
        assert body["failed_stage"] == "covariates"
        assert "No grid_weather_YYYY.csv files" in body["detail"]
        assert risk_app._model is FAKE_MODEL
        assert risk_app._load_error is None


def test_health_503_when_trial_prediction_fails(client_with):
    client, _ = client_with(predictor=_predictor([np.inf] * CELLS))
    with client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["failed_stage"] == "trial_prediction"


def test_predict_503_detail_is_the_load_error_when_params_fail(client_with):
    client, _ = client_with(loader=_raise(FileNotFoundError("Missing fitted parameters: x.npz")))
    with client:
        response = client.get("/predict", params={"date": "2024-08-15", "cell_id": 400})
    assert response.status_code == 503
    assert "Missing fitted parameters: x.npz" in response.json()["detail"]


def test_health_is_cached_and_does_not_predict_per_request(client_with):
    client, predictor = client_with()
    with client:
        for _ in range(5):
            assert client.get("/health").status_code == 200
    assert len(predictor.calls) == 1
