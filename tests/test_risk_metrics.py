"""Issue #60: /metrics serves only a table scored from the committed params.

Issue #63: analysis.py imports without matplotlib or scikit-learn.
These tests need no data files and no running service.
"""

from __future__ import annotations

import builtins
import csv
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import mannwhitneyu

from services.risk_forecasting import evaluate_metrics as em

REPO_ROOT = Path(__file__).resolve().parents[1]
PARAMS = REPO_ROOT / "services" / "risk_forecasting" / "artifacts" / "cnhpp_params.npz"
METRICS = REPO_ROOT / "services" / "risk_forecasting" / "outputs" / "metrics_table.csv"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_committed_table_matches_committed_params():
    params = np.load(PARAMS)
    row = em.load_verified_cnhpp_row(METRICS, PARAMS)
    assert row["params_sha256"] == em.params_sha256(PARAMS)
    assert float(row["log_likelihood"]) == pytest.approx(float(params["val_log_likelihood"]), rel=1e-9)
    assert float(row["xi"]) == pytest.approx(float(params["xi"]))
    assert row["train_years"] == ";".join(str(int(y)) for y in params["train_years"])
    assert int(row["eval_year"]) == int(params["val_year"])


def test_committed_table_scores_nhpp_and_cnhpp_separately():
    by_model = {row["model"]: row for row in _rows(METRICS)}
    assert by_model["NHPP"]["log_likelihood"] != by_model["cNHPP"]["log_likelihood"]
    assert by_model["NHPP"]["AUC"] != by_model["cNHPP"]["AUC"]


def test_the_stale_table_from_issue_60_is_refused(tmp_path):
    stale = tmp_path / "metrics_table.csv"
    stale.write_text(
        "model,log_likelihood,top5%_precision,top1%_precision,AUC,lift_top5%\n"
        "HPP,-4634.669281005859,0.0006436135978000117,0.0001481042654028436,0.5,1.438171521484068\n"
        "NHPP,-4390.004989905644,0.0014335030132818442,0.0020734597156398106,0.7717641674854097,3.203200206941788\n"
        "cNHPP,-4390.004989905644,0.0014335030132818442,0.0020734597156398106,0.7717641674854097,3.203200206941788\n",
        encoding="utf-8",
    )
    with pytest.raises(em.MetricsMismatch, match="not produced from the committed"):
        em.load_verified_cnhpp_row(stale, PARAMS)


def test_a_wrong_log_likelihood_is_refused_even_with_the_right_hash(tmp_path):
    rows = _rows(METRICS)
    for row in rows:
        if row["model"] == "cNHPP":
            row["log_likelihood"] = "-4390.0"
    table = tmp_path / "metrics_table.csv"
    _write(table, rows)
    with pytest.raises(em.MetricsMismatch, match="does not match the committed validation"):
        em.load_verified_cnhpp_row(table, PARAMS)


def test_identical_nhpp_and_cnhpp_rows_are_refused(tmp_path):
    rows = _rows(METRICS)
    cnhpp = next(row for row in rows if row["model"] == "cNHPP")
    for row in rows:
        if row["model"] == "NHPP":
            for col in ("log_likelihood", "top5%_precision", "top1%_precision", "AUC", "lift_top5%"):
                row[col] = cnhpp[col]
    table = tmp_path / "metrics_table.csv"
    _write(table, rows)
    with pytest.raises(em.MetricsMismatch, match="identical"):
        em.load_verified_cnhpp_row(table, PARAMS)


def test_missing_table_is_refused(tmp_path):
    with pytest.raises(em.MetricsMismatch, match="missing or unreadable"):
        em.load_verified_cnhpp_row(tmp_path / "absent.csv", PARAMS)


def test_metrics_endpoint_returns_503_for_a_stale_table(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from services.risk_forecasting import app as app_module

    stale = tmp_path / "metrics_table.csv"
    stale.write_text("model,log_likelihood\ncNHPP,-4390.0\n", encoding="utf-8")
    monkeypatch.setattr(
        app_module,
        "load_verified_cnhpp_row",
        lambda: em.load_verified_cnhpp_row(stale, PARAMS),
    )
    client = TestClient(app_module.app)
    response = client.get("/metrics")
    assert response.status_code == 503
    assert "evaluate_metrics" in response.json()["detail"]


def test_metrics_endpoint_serves_the_verified_row_with_its_provenance():
    from fastapi.testclient import TestClient

    from services.risk_forecasting.app import app

    body = TestClient(app).get("/metrics").json()
    params = np.load(PARAMS)
    assert body["model"] == "cNHPP"
    assert body["log_likelihood"] == pytest.approx(float(params["val_log_likelihood"]), rel=1e-9)
    assert body["xi"] == pytest.approx(float(params["xi"]))
    assert body["train_years"] == [int(y) for y in params["train_years"]]
    assert body["eval_year"] == int(params["val_year"])
    assert body["params_sha256"] == em.params_sha256(PARAMS)


# ---- Issue #63 ---------------------------------------------------------------


def test_binary_auc_matches_the_pairwise_definition_with_ties():
    from services.risk_forecasting.analysis import binary_auc

    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=400)
    score = rng.integers(0, 12, size=400).astype(float)  # many ties
    pos, neg = score[y == 1], score[y == 0]
    pairwise = (
        (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    ) / (len(pos) * len(neg))
    assert binary_auc(y, score) == pytest.approx(pairwise, rel=1e-12)
    u = mannwhitneyu(pos, neg, alternative="two-sided").statistic
    assert binary_auc(y, score) == pytest.approx(u / (len(pos) * len(neg)), rel=1e-12)
    assert np.isnan(binary_auc(np.zeros(5), np.arange(5)))


def test_analysis_imports_without_matplotlib_or_sklearn(monkeypatch):
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in {"matplotlib", "sklearn"}:
            raise ImportError(f"blocked {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    monkeypatch.delitem(sys.modules, "services.risk_forecasting.analysis", raising=False)
    module = importlib.import_module("services.risk_forecasting.analysis")
    assert callable(module.all_metrics)
    with pytest.raises(ImportError, match="requirements-analysis.txt"):
        module.plot_monthly(None)
