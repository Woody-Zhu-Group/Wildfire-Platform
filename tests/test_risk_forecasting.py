"""Risk forecasting API and place-aggregation checks."""

from __future__ import annotations

import csv
from datetime import date
from math import exp
from pathlib import Path

import pytest

from services.risk_forecasting.config import DATA_DIR, GRID_CSV
from services.risk_forecasting.observed import observed_training_year_total
from services.risk_forecasting.place import PlaceNotFound, resolve_place
from services.risk_forecasting.predictor import (
    AGGREGATION,
    coverage_end_message,
    dropped_dec_2020_message,
    last_covariate_date,
    percentile_rank,
    poisson_at_least_one,
    raise_if_unscoreable,
    CoverageError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PARAMS = REPO_ROOT / "services" / "risk_forecasting" / "artifacts" / "cnhpp_params.npz"


@pytest.fixture(scope="module")
def risk_api():
    """In-process app so tests exercise new code without a stale :8001."""
    from fastapi.testclient import TestClient

    from services.risk_forecasting.app import app

    with TestClient(app) as client:
        yield client


def test_params_file_present_on_disk():
    assert PARAMS.is_file(), f"missing {PARAMS}"
    assert PARAMS.stat().st_size > 0


def test_covariate_coverage_end_is_2025_12_31():
    end = last_covariate_date(DATA_DIR)
    assert end == date(2025, 12, 31), f"expected 2025-12-31 from weather files, got {end}"


def test_aggregation_p_at_least_one():
    assert poisson_at_least_one([0.0]) == 0.0
    lam = 0.00465
    p = poisson_at_least_one([lam])
    assert abs(p - (1.0 - exp(-lam))) < 1e-15
    assert abs(p - lam) < 2e-5
    multi = poisson_at_least_one([0.01, 0.02, 0.03])
    assert abs(multi - (1.0 - exp(-0.06))) < 1e-15
    assert percentile_rank(3.0, [1.0, 2.0, 3.0, 4.0]) == 75.0


def test_coverage_messages_are_specific():
    end = last_covariate_date(DATA_DIR)
    with pytest.raises(CoverageError, match="2025-12-31") as future:
        raise_if_unscoreable(date(2026, 8, 15), DATA_DIR)
    assert "no forecast ingestion" in str(future.value)
    assert str(future.value) == coverage_end_message(end)

    with pytest.raises(CoverageError, match="corrupt HRRR") as dropped:
        raise_if_unscoreable(date(2020, 12, 15), DATA_DIR)
    assert "2025-12-31" not in str(dropped.value)
    assert str(dropped.value) == dropped_dec_2020_message()


def test_place_resolution_county_utility_point(db_conn):
    county = resolve_place(county="Sacramento County")
    assert county.scope_type == "county"
    assert county.scope_name == "Sacramento County"
    assert county.cell_count > 1
    bare = resolve_place(county="Sacramento")
    assert bare.cell_ids == county.cell_ids

    with pytest.raises(PlaceNotFound, match="Unknown county"):
        resolve_place(county="Atlantis")

    pge = resolve_place(utility="PGE")
    assert pge.scope_type == "utility"
    assert pge.scope_name == "PGE"
    assert pge.cell_count > 1

    with pytest.raises(PlaceNotFound, match="Unknown utility"):
        resolve_place(utility="NOT_AN_IOU")

    point = resolve_place(lat=38.58, lon=-121.49)
    assert point.scope_type == "point"
    assert point.cell_count == 1

    with pytest.raises(PlaceNotFound, match="outside the California risk grid"):
        resolve_place(lat=10.0, lon=-10.0)

    cell = resolve_place(cell_id=400, known_cell_ids=range(824))
    assert cell.cell_ids == (400,)
    with pytest.raises(PlaceNotFound):
        resolve_place(cell_id=99999, known_cell_ids=range(824))


def test_health_model_loaded(risk_api):
    r = risk_api.get("/health")
    body = r.json()
    if not body.get("model_loaded"):
        pytest.fail(
            f"risk model not loaded: {body.get('detail')}. "
            f"Params file exists={PARAMS.is_file()} size="
            f"{PARAMS.stat().st_size if PARAMS.is_file() else 'n/a'}"
        )
    assert r.status_code == 200, (
        f"risk /health -> {r.status_code}, failed_stage={body.get('failed_stage')}: "
        f"{body.get('detail')}"
    )
    assert body["status"] == "ok"


def test_risk_api_allows_cross_origin_browser_requests(risk_api):
    response = risk_api.get(
        "/health",
        headers={"Origin": "https://woody-zhu-group.github.io"},
    )
    # /health is 503 when the model did not load; CORS must hold either way.
    assert response.status_code in (200, 503)
    assert response.headers.get("access-control-allow-origin") == "*"
    preflight = risk_api.options(
        "/health",
        headers={
            "Origin": "https://woody-zhu-group.github.io",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"


def test_metrics_match_persisted_cnhpp_row(risk_api):
    metrics_path = (
        REPO_ROOT
        / "services"
        / "risk_forecasting"
        / "outputs"
        / "metrics_table.csv"
    )
    with metrics_path.open(newline="", encoding="utf-8") as handle:
        expected = next(
            row
            for row in csv.DictReader(handle)
            if row["model"].strip().lower() == "cnhpp"
        )

    response = risk_api.get("/metrics")
    assert response.status_code == 200, response.text
    body = response.json()
    provenance = {"xi", "train_years", "eval_year", "eval_start", "eval_end", "params_sha256"}
    assert provenance <= set(body)
    assert body["params_sha256"] == expected["params_sha256"]
    body = {k: v for k, v in body.items() if k not in provenance}
    assert body == {
        "model": expected["model"],
        "log_likelihood": float(expected["log_likelihood"]),
        "top5_precision": float(expected["top5%_precision"]),
        "top1_precision": float(expected["top1%_precision"]),
        "lift_top5": float(expected["lift_top5%"]),
        "auc": float(expected["AUC"]),
    }
    assert "deviance" not in body
    assert "mae" not in body


def test_predict_single_cell_uses_p_at_least_one(risk_api):
    health = risk_api.get("/health").json()
    if not health.get("model_loaded"):
        pytest.fail(f"cannot test /predict — model not loaded: {health.get('detail')}")

    r = risk_api.get("/predict", params={"cell_id": 400, "date": "2024-08-15"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cell_id"] == 400
    assert body["date"] == "2024-08-15"
    assert body["aggregation"] == AGGREGATION
    assert "1 - exp(-sum(lambda))" in body["aggregation_note"]
    assert body["cell_count"] == 1
    assert body["scope"] == {"type": "cell", "name": "cell 400"}
    intensity = body["intensity"]
    assert intensity is not None
    assert abs(body["expected_count"] - intensity) < 1e-12
    assert abs(body["risk"] - (1.0 - exp(-intensity))) < 1e-12
    assert 0 <= body["local_percentile"] <= 100
    assert 0 <= body["statewide_percentile"] <= 100
    assert body["local_period"].startswith("August")
    assert body["local_n"] >= 1
    assert body["includes_cell_461"] is False


def test_predict_varies_by_cell_and_date(risk_api):
    health = risk_api.get("/health").json()
    if not health.get("model_loaded"):
        pytest.fail(f"cannot test /predict — model not loaded: {health.get('detail')}")

    r1 = risk_api.get("/predict", params={"cell_id": 0, "date": "2024-07-15"})
    r2 = risk_api.get("/predict", params={"cell_id": 100, "date": "2024-07-15"})
    r3 = risk_api.get("/predict", params={"cell_id": 0, "date": "2024-01-15"})

    for label, r in [("cell0 Jul", r1), ("cell100 Jul", r2), ("cell0 Jan", r3)]:
        assert r.status_code == 200, f"{label}: {r.status_code} {r.text}"

    v1, v2, v3 = r1.json()["risk"], r2.json()["risk"], r3.json()["risk"]
    assert v1 != v2 or v1 != v3, (
        f"expected different risk across cells/dates; got cell0_jul={v1}, "
        f"cell100_jul={v2}, cell0_jan={v3}"
    )


def test_predict_invalid_inputs(risk_api):
    health = risk_api.get("/health").json()
    if not health.get("model_loaded"):
        pytest.fail(f"cannot test /predict errors — model not loaded: {health.get('detail')}")

    r = risk_api.get("/predict", params={"cell_id": -1, "date": "2024-07-15"})
    assert r.status_code in (400, 404), f"expected 400/404 for bad cell, got {r.status_code} {r.text}"

    r = risk_api.get("/predict", params={"cell_id": 0, "date": "1900-01-01"})
    assert r.status_code in (400, 404), (
        f"expected 400/404 for out-of-range date, got {r.status_code} {r.text}"
    )

    r = risk_api.get("/predict", params={"cell_id": 99999, "date": "2024-07-15"})
    assert r.status_code in (400, 404), (
        f"expected 400/404 for huge cell_id, got {r.status_code} {r.text}"
    )


def test_predict_coverage_errors(risk_api):
    health = risk_api.get("/health").json()
    if not health.get("model_loaded"):
        pytest.fail(f"cannot test coverage — model not loaded: {health.get('detail')}")

    future = risk_api.get("/predict", params={"cell_id": 400, "date": "2026-08-15"})
    assert future.status_code == 400, future.text
    detail = future.json()["detail"]
    assert "2025-12-31" in detail
    assert "no forecast ingestion" in detail

    dropped = risk_api.get("/predict", params={"cell_id": 400, "date": "2020-12-15"})
    assert dropped.status_code == 400, dropped.text
    dropped_detail = dropped.json()["detail"]
    assert "corrupt HRRR" in dropped_detail
    assert "2025-12-31" not in dropped_detail


def test_predict_county_aggregation(risk_api, db_conn):
    health = risk_api.get("/health").json()
    if not health.get("model_loaded"):
        pytest.fail(f"cannot test county /predict — model not loaded: {health.get('detail')}")

    r = risk_api.get(
        "/predict",
        params={"county": "Sacramento County", "date": "2024-08-15"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["aggregation"] == AGGREGATION
    assert body["cell_count"] > 1
    assert body["scope"]["type"] == "county"
    assert body["scope"]["name"] == "Sacramento County"
    assert "cell_id" not in body or body.get("cell_id") is None
    assert body["mean_intensity"] is not None
    assert abs(body["risk"] - (1.0 - exp(-body["expected_count"]))) < 1e-12

    unknown = risk_api.get(
        "/predict",
        params={"county": "Atlantis", "date": "2024-08-15"},
    )
    assert unknown.status_code == 404, unknown.text
    assert "Unknown county" in unknown.json()["detail"]

    both = risk_api.get(
        "/predict",
        params={"cell_id": 400, "county": "Sacramento", "date": "2024-08-15"},
    )
    assert both.status_code == 400, both.text


def test_surface_has_all_grid_cells_and_matches_predict(risk_api, db_conn):
    health = risk_api.get("/health").json()
    if not health.get("model_loaded"):
        pytest.fail(f"cannot test /surface — model not loaded: {health.get('detail')}")

    surface = risk_api.get("/surface", params={"date": "2024-07-15"})
    assert surface.status_code == 200, surface.text
    body = surface.json()
    assert body["date"] == "2024-07-15"
    assert body["lookback_days"] == 90
    cells = body["cells"]
    assert len(cells) == 824

    ids = [cell["cell_id"] for cell in cells]
    assert len(ids) == len(set(ids))

    with db_conn.cursor() as cur:
        cur.execute("SELECT cell_id FROM wildfire.grid_cells ORDER BY cell_id")
        warehouse_ids = [int(row[0]) for row in cur.fetchall()]
    assert sorted(ids) == warehouse_ids
    assert len(warehouse_ids) == 824

    sample = next(cell for cell in cells if cell["cell_id"] == 400)
    assert abs(sample["expected_count"] - sample["intensity"]) < 1e-12
    assert abs(sample["risk"] - (1.0 - exp(-sample["intensity"]))) < 1e-12

    predict = risk_api.get(
        "/predict",
        params={"cell_id": sample["cell_id"], "date": "2024-07-15"},
    )
    assert predict.status_code == 200, predict.text
    predicted = predict.json()
    assert predicted["cell_id"] == sample["cell_id"]
    assert abs(predicted["risk"] - sample["risk"]) < 1e-12
    assert abs(predicted["intensity"] - sample["intensity"]) < 1e-12
    assert abs(predicted["expected_count"] - sample["expected_count"]) < 1e-12


def test_surface_coverage_errors(risk_api):
    health = risk_api.get("/health").json()
    if not health.get("model_loaded"):
        pytest.fail(f"cannot test /surface coverage — model not loaded: {health.get('detail')}")

    future = risk_api.get("/surface", params={"date": "2026-08-15"})
    assert future.status_code == 400, future.text
    detail = future.json()["detail"]
    assert "2025-12-31" in detail
    assert "no forecast ingestion" in detail

    dropped = risk_api.get("/surface", params={"date": "2020-12-15"})
    assert dropped.status_code == 400, dropped.text
    dropped_detail = dropped.json()["detail"]
    assert "corrupt HRRR" in dropped_detail
    assert "2025-12-31" not in dropped_detail


def test_observed_has_all_grid_cells(risk_api, db_conn):
    r = risk_api.get("/observed", params={"date": "2024-07-05"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == "2024-07-05"
    cells = body["cells"]
    assert len(cells) == 824

    ids = [cell["cell_id"] for cell in cells]
    assert len(ids) == len(set(ids))
    with db_conn.cursor() as cur:
        cur.execute("SELECT cell_id FROM wildfire.grid_cells ORDER BY cell_id")
        warehouse_ids = [int(row[0]) for row in cur.fetchall()]
        cur.execute(
            """
            SELECT COUNT(*) FROM wildfire.cpuc_ignitions
            WHERE event_date = DATE '2024-07-05'
            """
        )
        warehouse_day = int(cur.fetchone()[0])
    assert sorted(ids) == warehouse_ids
    assert all(cell["observed_count"] >= 0 for cell in cells)
    assert sum(cell["observed_count"] for cell in cells) == warehouse_day
    occupied = {cell["cell_id"]: cell["observed_count"] for cell in cells if cell["observed_count"]}
    assert occupied == {43: 1, 90: 1, 113: 2, 161: 1, 299: 1, 332: 1, 358: 1, 445: 1}


def test_observed_postgis_vs_training_csv(risk_api):
    """Polygon ST_Contains vs the training CSV nearest-SW-corner snap.

    Same 9 CPUC events on 2024-07-05; cell assignment differs because
    events_YYYY.csv snaps to nearest grid SW-corner, not polygon interior.
    """
    csv_path = DATA_DIR / "events_2024.csv"
    if not csv_path.is_file():
        pytest.skip(f"training events CSV not available: {csv_path}")

    import pandas as pd

    from services.risk_forecasting import grid_data_prep as gdp

    body = risk_api.get("/observed", params={"date": "2024-07-05"}).json()
    postgis = {
        cell["cell_id"]: cell["observed_count"]
        for cell in body["cells"]
        if cell["observed_count"]
    }

    grid = gdp.load_grid(str(GRID_CSV))
    events = gdp.load_fire_events(str(csv_path))
    day = events[events["date"] == pd.Timestamp("2024-07-05")]
    snapped = gdp.snap_events_to_grid(day, grid)
    csv_counts = {}
    for cell_id in snapped["cell_id"].astype(int):
        csv_counts[cell_id] = csv_counts.get(cell_id, 0) + 1

    assert sum(postgis.values()) == 9
    assert sum(csv_counts.values()) == 9
    assert len(day) == 9
    assert postgis != csv_counts, (
        "expected polygon vs nearest-SW-corner mismatch on 2024-07-05; "
        f"got identical {postgis}"
    )


def test_observed_training_matches_events_csv(risk_api):
    csv_path = DATA_DIR / "events_2024.csv"
    if not csv_path.is_file():
        pytest.skip(f"training events CSV not available: {csv_path}")

    import pandas as pd

    from services.risk_forecasting import grid_data_prep as gdp

    r = risk_api.get("/observed-training", params={"date": "2024-07-05"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == "2024-07-05"
    cells = body["cells"]
    assert len(cells) == 824
    ids = [cell["cell_id"] for cell in cells]
    assert len(ids) == len(set(ids))

    training = {
        cell["cell_id"]: cell["observed_count"]
        for cell in cells
        if cell["observed_count"]
    }
    assert sum(training.values()) == 9

    grid = gdp.load_grid(str(GRID_CSV))
    events = gdp.load_fire_events(str(csv_path))
    day = events[events["date"] == pd.Timestamp("2024-07-05")]
    snapped = gdp.snap_events_to_grid(day, grid)
    csv_counts = {}
    for cell_id in snapped["cell_id"].astype(int):
        csv_counts[cell_id] = csv_counts.get(cell_id, 0) + 1

    assert len(day) == 9
    assert training == csv_counts
    assert training == {44: 1, 90: 1, 114: 2, 161: 1, 332: 1, 367: 1, 392: 1, 468: 1}

    assert observed_training_year_total(2024) == 741
    assert observed_training_year_total(2024) != 724
