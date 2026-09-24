"""
Regenerate outputs/metrics_table.csv from the committed fit.

The table scores HPP, NHPP, and cNHPP on the committed fit's validation year
(2024), each trained on the committed fit's training years (2020-2023):
  - cNHPP uses the committed xi and beta from artifacts/cnhpp_params.npz and
    the committed standardization. Its validation log likelihood must
    reproduce the one stored in the params file, or nothing is written.
  - HPP and NHPP are fit on the same training years with models.fit_hpp and
    models.fit_nhpp, as compare_models.py does.
  - Precision, AUC, and lift come from analysis.all_metrics, the code that
    wrote the earlier table.

Every row records the params file's sha256, the training years, the
evaluation year, and the evaluation window, and GET /metrics refuses a table
whose hash or cNHPP log likelihood does not match the committed params.

Usage (needs the per-year weather, vegetation, and event files):
  python -m services.risk_forecasting.evaluate_metrics
"""

from __future__ import annotations

import hashlib
import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

from services.risk_forecasting import grid_data_prep as gdp
from services.risk_forecasting.analysis import all_metrics
from services.risk_forecasting.config import DATA_DIR, GRID_CSV, GRID_W_PKL, PARAMS_PATH
from services.risk_forecasting.fit_model import (
    _require_file,
    _standardize,
    load_events,
    load_year_bundle,
)
from services.risk_forecasting.models import _mrnn_forward, fit_hpp, fit_nhpp, poisson_ll

METRICS_PATH = Path(__file__).resolve().parent / "outputs" / "metrics_table.csv"
# Relative tolerance for "the table was produced from these params".
LL_REL_TOL = 1e-6


def params_sha256(path: Path = PARAMS_PATH) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ll_matches(value: float, reference: float, rel_tol: float = LL_REL_TOL) -> bool:
    return abs(value - reference) <= rel_tol * max(1.0, abs(reference))


class MetricsMismatch(ValueError):
    """The metrics table was not produced from the committed params."""


_METRIC_COLUMNS = ("log_likelihood", "top5%_precision", "top1%_precision", "AUC", "lift_top5%")


def load_verified_cnhpp_row(
    metrics_path: Path = METRICS_PATH, params_path: Path = PARAMS_PATH
) -> dict[str, str]:
    """The cNHPP row, only if the table matches the committed params.

    Raises MetricsMismatch when the file or row is missing, the table records
    no params hash or a different one, the cNHPP log likelihood differs from
    the params file's validation log likelihood, or the cNHPP row repeats the
    NHPP row while the committed xi is not 0 (the stale table in issue #60).
    """
    fix = "regenerate it with python -m services.risk_forecasting.evaluate_metrics"
    try:
        rows = {
            row["model"].strip().lower(): row
            for row in pd.read_csv(metrics_path, dtype=str).to_dict("records")
        }
    except (FileNotFoundError, KeyError, pd.errors.EmptyDataError) as exc:
        raise MetricsMismatch(f"{metrics_path} is missing or unreadable; {fix}") from exc
    cnhpp, nhpp = rows.get("cnhpp"), rows.get("nhpp")
    if cnhpp is None:
        raise MetricsMismatch(f"{metrics_path} has no cNHPP row; {fix}")
    recorded = cnhpp.get("params_sha256")
    committed = params_sha256(params_path)
    if not isinstance(recorded, str) or recorded != committed:
        raise MetricsMismatch(
            f"{metrics_path.name} was not produced from the committed "
            f"{Path(params_path).name} (recorded params hash {recorded!r}, "
            f"committed {committed}); {fix}"
        )
    params = np.load(params_path)
    committed_ll = float(params["val_log_likelihood"])
    if not ll_matches(float(cnhpp["log_likelihood"]), committed_ll):
        raise MetricsMismatch(
            f"cNHPP log likelihood {cnhpp['log_likelihood']} in {metrics_path.name} "
            f"does not match the committed validation log likelihood {committed_ll}; {fix}"
        )
    if (
        nhpp is not None
        and float(params["xi"]) != 0.0
        and all(nhpp.get(col) == cnhpp.get(col) for col in _METRIC_COLUMNS)
    ):
        raise MetricsMismatch(
            f"cNHPP and NHPP rows in {metrics_path.name} are identical although the "
            f"committed xi is {float(params['xi'])}; {fix}"
        )
    return cnhpp


def build_table() -> pd.DataFrame:
    params = np.load(PARAMS_PATH)
    xi = float(params["xi"])
    beta = np.asarray(params["beta"], dtype=np.float64)
    means = np.asarray(params["means"], dtype=np.float64)
    stds = np.asarray(params["stds"], dtype=np.float64)
    train_years = [int(y) for y in params["train_years"]]
    val_year = int(params["val_year"])
    committed_val_ll = float(params["val_log_likelihood"])

    grid_df = gdp.load_grid(str(_require_file(GRID_CSV, "grid_cells.csv")))
    with open(_require_file(GRID_W_PKL, "grid_W.pkl"), "rb") as handle:
        w = pickle.load(handle)
    if not isinstance(w, csr_matrix):
        w = csr_matrix(w)

    events = load_events(DATA_DIR, train_years + [val_year], grid_df)
    train_x, train_e = [], []
    for year in train_years:
        x, e, _ = load_year_bundle(DATA_DIR, year, grid_df, events)
        train_x.append(x)
        train_e.append(e)
    x_train_raw = np.concatenate(train_x, axis=0)
    e_train = np.concatenate(train_e, axis=1)
    x_val_raw, e_val, val_dates = load_year_bundle(DATA_DIR, val_year, grid_df, events)

    x_train, fit_means, fit_stds = _standardize(x_train_raw)
    if not (np.allclose(fit_means, means) and np.allclose(fit_stds, stds)):
        raise ValueError(
            "Training data no longer reproduces the committed standardization; "
            "the data files differ from the ones the committed fit used"
        )
    x_val, _, _ = _standardize(x_val_raw, means=means, stds=stds)

    cnhpp_h = _mrnn_forward(xi, beta, x_val, w).T
    cnhpp_ll = float(poisson_ll(cnhpp_h, e_val))
    if not ll_matches(cnhpp_ll, committed_val_ll):
        raise ValueError(
            f"cNHPP validation log likelihood {cnhpp_ll:.6f} does not reproduce the "
            f"committed {committed_val_ll:.6f}; not writing a table"
        )

    hpp = fit_hpp(e_train)
    nhpp = fit_nhpp(x_train, e_train)
    hpp_h = np.full(e_val.shape, np.log(hpp.lambda_hat), dtype=np.float64)
    nhpp_h = (x_val @ nhpp.beta).T

    results = [
        SimpleNamespace(log_lambda=hpp_h, log_likelihood=float(poisson_ll(hpp_h, e_val))),
        SimpleNamespace(log_lambda=nhpp_h, log_likelihood=float(poisson_ll(nhpp_h, e_val))),
        SimpleNamespace(log_lambda=cnhpp_h, log_likelihood=cnhpp_ll),
    ]
    table = all_metrics(e_val, *results)
    table["xi"] = [np.nan, 0.0, xi]
    table["train_years"] = ";".join(map(str, train_years))
    table["eval_year"] = val_year
    table["eval_start"] = val_dates.min().date().isoformat()
    table["eval_end"] = val_dates.max().date().isoformat()
    table["eval_days"] = len(val_dates)
    table["eval_events"] = int(e_val.sum())
    table["params_sha256"] = params_sha256()
    return table


def main() -> None:
    try:
        table = build_table()
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(METRICS_PATH, index=False)
    print(f"[OUT] Wrote {METRICS_PATH}")


if __name__ == "__main__":
    main()
