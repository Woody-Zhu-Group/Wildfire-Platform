"""
HPP vs NHPP vs cNHPP on corrected data, with uncertainty on OOS ΔLL.

Default holdouts: 2022, 2023, and 2024, each trained on the other years in
2020-2024 (Dec 2-31 2020 always excluded). Pass --holdouts to change them.
For each holdout year:
  - fit HPP / NHPP / cNHPP on the other years (cNHPP ξ by train LL)
  - score Poisson LL on the holdout year
  - day-blocked bootstrap of (cNHPP − NHPP) val LL to get SE / p-value

Usage:
  python -m services.risk_forecasting.compare_models
  python -m services.risk_forecasting.compare_models --holdouts 2020,2021,2022,2023,2024
"""

from __future__ import annotations

import argparse
import sys
import pickle
from typing import Sequence

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

from services.risk_forecasting import grid_data_prep as gdp
from services.risk_forecasting.config import DATA_DIR, GRID_CSV, GRID_W_PKL
from services.risk_forecasting.fit_model import (
    _require_file,
    _standardize,
    load_events,
    load_year_bundle,
)
from services.risk_forecasting.models import (
    _mrnn_forward,
    fit_cnhpp,
    fit_hpp,
    fit_nhpp,
    poisson_ll,
)


ALL_YEARS = [2020, 2021, 2022, 2023, 2024]


def daily_poisson_ll(log_lambda_nt: np.ndarray, E: np.ndarray) -> np.ndarray:
    """
    Per-day Poisson LL contributions.
    log_lambda_nt, E: (N, T)  →  returns (T,)
    ll_t = Σ_i E_it h_it − Σ_i exp(h_it)
    """
    h = log_lambda_nt.astype(np.float64)
    return (E * h).sum(axis=0) - np.exp(np.clip(h, -20, 20)).sum(axis=0)


def bootstrap_delta(
    delta_daily: np.ndarray,
    n_boot: int = 5000,
    seed: int = 0,
) -> dict:
    """Day-blocked bootstrap of sum(delta_daily)."""
    rng = np.random.default_rng(seed)
    t = len(delta_daily)
    observed = float(delta_daily.sum())
    # resample days with replacement
    idx = rng.integers(0, t, size=(n_boot, t))
    samples = delta_daily[idx].sum(axis=1)
    se = float(samples.std(ddof=1))
    # two-sided p: how often |boot| is at least as large as |obs| under
    # centering at 0 via sign-flip / observed-centered null
    # Use percentile CI and one-sided P(Δ≤0), two-sided via sign symmetry
    p_le_0 = float(np.mean(samples <= 0))
    p_ge_0 = float(np.mean(samples >= 0))
    p_two = float(2 * min(p_le_0, p_ge_0))
    p_two = min(p_two, 1.0)
    ci_lo, ci_hi = np.percentile(samples, [2.5, 97.5])
    # also SE from day-level: naive SE = sqrt(T)*sd(daily) for iid days
    naive_se = float(delta_daily.std(ddof=1) * np.sqrt(t))
    return {
        "delta": observed,
        "se_boot": se,
        "se_naive": naive_se,
        "ci95": (float(ci_lo), float(ci_hi)),
        "p_boot_le_0": p_le_0,
        "p_boot_two": p_two,
        "n_boot": n_boot,
        "n_days": t,
        "mean_daily_delta": float(delta_daily.mean()),
        "std_daily_delta": float(delta_daily.std(ddof=1)),
    }


def fit_and_score_split(
    train_years: Sequence[int],
    val_year: int,
    grid_df: pd.DataFrame,
    w: csr_matrix,
    events: pd.DataFrame,
    n_boot: int,
) -> dict:
    print("\n" + "=" * 60)
    print(f"HOLDOUT {val_year}  |  train={list(train_years)}")
    print("=" * 60)

    train_x, train_e = [], []
    for year in train_years:
        x, e, _ = load_year_bundle(DATA_DIR, year, grid_df, events)
        train_x.append(x)
        train_e.append(e)

    x_train_raw = np.concatenate(train_x, axis=0)
    e_train = np.concatenate(train_e, axis=1)
    x_val_raw, e_val, _ = load_year_bundle(DATA_DIR, val_year, grid_df, events)

    x_train, means, stds = _standardize(x_train_raw)
    x_val, _, _ = _standardize(x_val_raw, means=means, stds=stds)

    print(
        f"[DATA] TRAIN T={x_train.shape[0]} events={int(e_train.sum())}  "
        f"VAL T={x_val.shape[0]} events={int(e_val.sum())}"
    )

    hpp = fit_hpp(e_train)
    nhpp = fit_nhpp(x_train, e_train)
    cnhpp = fit_cnhpp(x_train, e_train, w, verbose=False)

    # Val intensities (N, T)
    hpp_val_h = np.full(e_val.shape, np.log(hpp.lambda_hat), dtype=np.float64)
    nhpp_val_h = (x_val @ nhpp.beta).T
    cnhpp_val_h = _mrnn_forward(cnhpp.xi, cnhpp.beta, x_val, w).T

    hpp_val_ll = float(poisson_ll(hpp_val_h, e_val))
    nhpp_val_ll = float(poisson_ll(nhpp_val_h, e_val))
    cnhpp_val_ll = float(poisson_ll(cnhpp_val_h, e_val))

    nhpp_daily = daily_poisson_ll(nhpp_val_h, e_val)
    cnhpp_daily = daily_poisson_ll(cnhpp_val_h, e_val)
    # sanity: sums match totals
    assert abs(nhpp_daily.sum() - nhpp_val_ll) < 1e-3
    assert abs(cnhpp_daily.sum() - cnhpp_val_ll) < 1e-3

    delta_daily = cnhpp_daily - nhpp_daily
    boot = bootstrap_delta(delta_daily, n_boot=n_boot)

    print("\n[RESULT]")
    print(f"  HPP   val LL = {hpp_val_ll:10.3f}")
    print(f"  NHPP  val LL = {nhpp_val_ll:10.3f}")
    print(
        f"  cNHPP val LL = {cnhpp_val_ll:10.3f}  "
        f"(ξ={cnhpp.xi:.1f} train-selected)"
    )
    print(
        f"  ΔLL (cNHPP−NHPP) = {boot['delta']:+.3f}  "
        f"({100 * boot['delta'] / abs(nhpp_val_ll):+.3f}% of |NHPP LL|)"
    )
    print(
        f"  day-bootstrap SE = {boot['se_boot']:.3f}  "
        f"95% CI [{boot['ci95'][0]:+.3f}, {boot['ci95'][1]:+.3f}]"
    )
    print(
        f"  P(Δ≤0) = {boot['p_boot_le_0']:.3f}  "
        f"two-sided ≈ {boot['p_boot_two']:.3f}  "
        f"(n_boot={boot['n_boot']}, n_days={boot['n_days']})"
    )
    print(
        f"  daily ΔLL: mean={boot['mean_daily_delta']:+.4f}  "
        f"sd={boot['std_daily_delta']:.4f}"
    )
    z = boot["delta"] / boot["se_boot"] if boot["se_boot"] > 0 else float("nan")
    print(f"  z ≈ Δ/SE = {z:+.2f}")

    return {
        "val_year": val_year,
        "train_years": list(train_years),
        "hpp_val_ll": hpp_val_ll,
        "nhpp_val_ll": nhpp_val_ll,
        "cnhpp_val_ll": cnhpp_val_ll,
        "cnhpp_xi": float(cnhpp.xi),
        "train_events": int(e_train.sum()),
        "val_events": int(e_val.sum()),
        **{f"boot_{k}": v for k, v in boot.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--holdouts",
        default="2022,2023,2024",
        help="Comma-separated holdout years",
    )
    ap.add_argument("--n-boot", type=int, default=5000)
    args = ap.parse_args()
    holdouts = [int(x) for x in args.holdouts.split(",") if x.strip()]

    print("=" * 60)
    print("HPP / NHPP / cNHPP  —  LOYO + day-bootstrap ΔLL")
    print("holdouts=", holdouts, " n_boot=", args.n_boot)
    print("=" * 60)

    grid_df = gdp.load_grid(str(_require_file(GRID_CSV, "grid_cells.csv")))
    with open(GRID_W_PKL, "rb") as f:
        w = pickle.load(f)
    if not isinstance(w, csr_matrix):
        w = csr_matrix(w)

    events = load_events(DATA_DIR, ALL_YEARS, grid_df)

    rows = []
    for val_year in holdouts:
        train_years = [y for y in ALL_YEARS if y != val_year]
        rows.append(
            fit_and_score_split(
                train_years, val_year, grid_df, w, events, args.n_boot
            )
        )

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(
        f"{'holdout':>8s}  {'NHPP val':>10s}  {'cNHPP val':>10s}  "
        f"{'ΔLL':>8s}  {'SE':>6s}  {'95% CI':>18s}  {'P(Δ≤0)':>7s}  {'ξ':>4s}"
    )
    for r in rows:
        ci = r["boot_ci95"]
        print(
            f"{r['val_year']:8d}  {r['nhpp_val_ll']:10.2f}  "
            f"{r['cnhpp_val_ll']:10.2f}  {r['boot_delta']:+8.2f}  "
            f"{r['boot_se_boot']:6.2f}  "
            f"[{ci[0]:+.1f},{ci[1]:+.1f}]  "
            f"{r['boot_p_boot_le_0']:7.3f}  {r['cnhpp_xi']:4.1f}"
        )

    signs = [np.sign(r["boot_delta"]) for r in rows]
    if len(set(signs)) > 1:
        print(
            "\nSign of ΔLL flips across holdouts → difference is not stable; "
            "treat as a tie."
        )
    else:
        # check whether any CI excludes 0
        any_sig = any(
            (r["boot_ci95"][0] > 0) or (r["boot_ci95"][1] < 0) for r in rows
        )
        if not any_sig:
            print(
                "\nΔLL sign is stable but every 95% CI covers 0 → "
                "not distinguishable from a tie."
            )
        else:
            print(
                "\nAt least one holdout has 95% CI excluding 0; "
                "inspect that year before claiming a real gap."
            )

    out = DATA_DIR.parent / "outputs" / "model_comparison.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    flat = []
    for r in rows:
        flat.append(
            {
                "val_year": r["val_year"],
                "train_years": ";".join(map(str, r["train_years"])),
                "hpp_val_ll": r["hpp_val_ll"],
                "nhpp_val_ll": r["nhpp_val_ll"],
                "cnhpp_val_ll": r["cnhpp_val_ll"],
                "delta_ll": r["boot_delta"],
                "se_boot": r["boot_se_boot"],
                "ci95_lo": r["boot_ci95"][0],
                "ci95_hi": r["boot_ci95"][1],
                "p_le_0": r["boot_p_boot_le_0"],
                "p_two": r["boot_p_boot_two"],
                "cnhpp_xi": r["cnhpp_xi"],
                "val_events": r["val_events"],
            }
        )
    pd.DataFrame(flat).to_csv(out, index=False)
    print(f"\n[OUT] Wrote {out}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
