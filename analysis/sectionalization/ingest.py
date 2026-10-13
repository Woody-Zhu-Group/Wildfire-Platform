"""Ingest the sectionalization optimization grid into compact, verified tables.

Run from the repository root (POSIX shell)::

    PYTHONPATH=. python -m analysis.sectionalization.ingest \
        --grid-dir /Volumes/RyanSSD2/optimization_results_grid \
        --prediction-dir <Sectionalization-California>/outputs/prediction_results_grid/a0p4__exp5__train20__cal200__grp5__49ac8c7c07 \
        --circuit-metadata <Sectionalization-California>/outputs/circuit_metadata.csv

Inputs are read only. For every run (setup x replication x method) it reads the
stored plan, re-solves the realized PSPS recourse exactly against the test-period
ignitions, and checks the result against the grid's ``eval_cost``. Outputs:

* ``analysis/sectionalization/data/runs.csv``: one row per run, parameters and metrics.
* ``analysis/sectionalization/data/circuits.csv``: the 803 study circuits.
* ``analysis/sectionalization/data/decisions.csv.gz``: per-run decision flags.
* ``analysis/sectionalization/data/ingest_report.json``: completeness and the audit.
* ``website/data/optimization_grid/``: the same data shaped for the workspace views.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.sectionalization.model import (
    COVERAGE_COLUMN,
    METHOD_BY_GRID_LABEL,
    METHODS,
    PARAMETERS,
    encode_decisions,
    fill_sectionalization,
    realized_recourse,
    setup_id,
)

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent / "data"
WEB_DIR = ROOT / "website" / "data" / "optimization_grid"
GEOMETRY_TOLERANCE_DEG = 0.004  # about 400 m; the map is a statewide overview
COORD_DECIMALS = 4


def _read_setup(run_dir: Path) -> dict | None:
    path = run_dir / "grid_setup.json"
    if not path.exists():
        return None
    setup = json.loads(path.read_text())
    if setup.get("kind") != "optimization_grid_setup":
        return None
    opt = setup["optimization"]
    values = {p.key: float(opt[p.grid_key]) for p in PARAMETERS}
    values["delta"] = float(opt["delta"])
    values["alpha"] = float(opt["alpha"])
    return {
        "setup_id": setup_id(values),
        "slug": run_dir.name,
        "prediction_setup": setup["prediction_setup_slug"],
        "n_experiments": int(setup["prediction"]["n_experiments"]),
        "time_limit_sec": float(opt.get("time_limit_sec", np.nan)),
        **values,
    }


def _rdp(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Ramer-Douglas-Peucker simplification of one polyline."""

    if len(points) < 3:
        return points
    keep = np.zeros(len(points), dtype=bool)
    keep[[0, -1]] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue
        a, b = points[start], points[end]
        segment = b - a
        length = float(np.hypot(*segment))
        inner = points[start + 1:end]
        if length == 0.0:
            dist = np.hypot(*(inner - a).T)
        else:
            dist = np.abs(segment[0] * (inner[:, 1] - a[1]) - segment[1] * (inner[:, 0] - a[0])) / length
        index = int(np.argmax(dist))
        if dist[index] > tolerance:
            mid = start + 1 + index
            keep[mid] = True
            stack.extend([(start, mid), (mid, end)])
    return points[keep]


def simplify_wkt_lines(wkt: str, tolerance: float) -> list[list[float]]:
    """Parse a (MULTI)LINESTRING and return simplified parts as flat lon/lat lists."""

    parts, largest, largest_span = [], None, -1.0
    for body in re.findall(r"\(([^()]+)\)", wkt):
        coords = np.array([[float(v) for v in pair.split()[:2]] for pair in body.split(",")])
        if len(coords) < 2:
            continue
        simple = _rdp(coords, tolerance)
        span = float(np.ptp(simple, axis=0).max())
        flat = [round(float(v), COORD_DECIMALS) for v in simple.ravel()]
        if span > largest_span:
            largest, largest_span = flat, span
        if len(simple) == 2 and span < tolerance:
            continue  # sub-tolerance fragment, invisible at map scale
        parts.append(flat)
    if not parts and largest is not None:
        parts.append(largest)  # a compact circuit keeps its largest fragment so it stays on the map
    return parts


def load_circuits(metadata_csv: Path, units: list[str], tolerance: float) -> tuple[pd.DataFrame, list[dict]]:
    meta = pd.read_csv(metadata_csv, dtype={"circuitid": str})
    meta["unit"] = meta["unit"].astype(str)
    meta = meta.set_index("unit")
    missing = [u for u in units if u not in meta.index]
    if missing:
        raise ValueError(f"{len(missing)} study circuits are missing from {metadata_csv}: {missing[:5]}")
    meta = meta.loc[units]
    circuits = pd.DataFrame({
        "index": np.arange(len(units)),
        "unit": units,
        "circuit_id": meta["circuitid"].str.zfill(9).to_numpy(),
        "name": meta["circuitnam"].astype(str).to_numpy(),
        "substation": meta["substati_1"].astype(str).to_numpy(),
        "division": meta["division"].astype(str).to_numpy(),
        "hftd_tier": meta["hftd_tier"].astype(str).to_numpy(),
        "population": meta["population"].astype(float).to_numpy(),
        "lon": meta["circuit_lon"].astype(float).round(5).to_numpy(),
        "lat": meta["circuit_lat"].astype(float).round(5).to_numpy(),
    })
    geometry = []
    for unit, wkt in zip(units, meta["geometry"].astype(str)):
        lines = simplify_wkt_lines(wkt, tolerance)
        if not lines:
            raise ValueError(f"circuit {unit} has no drawable geometry after simplification")
        geometry.append(lines)
    return circuits, geometry


RANKING_RULES = {
    # Sectionalize the circuits with the highest predicted population-weighted exposure.
    "rank_exposure": lambda population, mean: population * mean,
    # Sectionalize the circuits with the highest predicted ignition rate, ignoring population.
    "rank_rate": lambda population, mean: mean,
}


def ranking_baselines(setups: list[dict], skipped: list[dict], population: np.ndarray,
                      truth: dict[int, np.ndarray], predicted: dict[int, np.ndarray]) -> pd.DataFrame:
    """Simple plans the grid did not run, scored the way the grid scores every plan.

    Each fills the whole sectionalization budget by a predicted ranking, uses no
    fast-trip, and gets the same optimal test-year PSPS response
    (``realized_recourse``) as the grid's methods. They use only the prediction
    model's means, the same information every method's plan is built from.
    """

    skipped_ids = {item["setup_id"] for item in skipped}
    none = np.zeros(len(population), dtype=int)
    rows = []
    for setup in setups:
        if setup["setup_id"] in skipped_ids:
            continue
        slots = int(np.floor(setup["C"] * len(population) + 1e-9))
        for exp in sorted(truth):
            row = {"setup_id": setup["setup_id"], "exp": exp}
            for rule, priority in RANKING_RULES.items():
                x = fill_sectionalization(none, priority(population, predicted[exp]), slots)
                result = realized_recourse(population, truth[exp], x, none, effectiveness=setup["E"], gamma=setup["G"],
                                           delta=setup["delta"], w_cap=setup["W"] * float(population.sum()))
                row[f"{rule}_exposure"] = result.exposure
                row[f"{rule}_saifi_psps"] = float(np.dot(setup["delta"] * population, result.z)) / float(population.sum())
            rows.append(row)
    return pd.DataFrame(rows)


def ingest(grid_dir: Path, prediction_dir: Path, metadata_csv: Path, *, tolerance: float) -> dict:
    started = time.time()
    outcomes = pd.read_csv(prediction_dir / "test_outcomes.csv")
    outcomes["unit"] = outcomes["unit"].astype(str)
    coverage_log = pd.read_csv(prediction_dir / "experiment_log.csv").set_index("exp_id")
    prediction_slug = prediction_dir.name

    setups = [s for s in (_read_setup(d) for d in sorted(grid_dir.iterdir()) if d.is_dir()) if s]
    if not setups:
        raise ValueError(f"no optimization setups found in {grid_dir}")

    units: list[str] | None = None
    population = None
    truth: dict[int, np.ndarray] = {}
    predicted: dict[int, np.ndarray] = {}
    rows: list[dict] = []
    decisions: list[tuple[str, str, int, str]] = []
    skipped: list[dict] = []
    required = {"exp_id", "method", "method_slug", "eval_cost", "x_sum", "y_sum", "z_sum", "z_star_sum",
                "worst_value", "avg_circuit_bound_width", "avg_group_bound_width", "elapsed_sec", "timed_out", "gap"}
    audit = {"max_rel_exposure_diff": 0.0, "exact_lower_than_recorded": 0, "exact_higher_than_recorded": 0,
             "shutoff_count_differs": 0, "max_shutoff_count_diff": 0, "recourse_not_proven_optimal": 0}
    total_runs = 0

    for setup in setups:
        comparison_path = grid_dir / setup["slug"] / "all_comparison.csv"
        comparison = pd.read_csv(comparison_path) if comparison_path.exists() else pd.DataFrame()
        missing_columns = sorted(required - set(comparison.columns))
        if missing_columns:
            # An interrupted run leaves an older-format comparison file without realized costs.
            skipped.append({"slug": setup["slug"], "setup_id": setup["setup_id"],
                            "reason": "all_comparison.csv lacks " + ", ".join(missing_columns)})
            continue
        for record in comparison.itertuples(index=False):
            method = METHOD_BY_GRID_LABEL[record.method]
            exp = int(record.exp_id)
            run_dir = grid_dir / setup["slug"] / f"exp_{exp}_{record.method_slug}"
            solution = pd.read_csv(run_dir / "solution.csv")
            solution["unit"] = solution["unit"].astype(str)
            if units is None:
                units = solution["unit"].tolist()
                population = solution["p_i"].to_numpy(float)
                for e, group in outcomes.groupby("exp_id"):
                    indexed = group.set_index("unit")
                    truth[int(e)] = indexed["true_y"].reindex(units).fillna(0.0).to_numpy(float)
                    predicted[int(e)] = indexed["test_mu"].reindex(units).fillna(0.0).to_numpy(float)
            elif solution["unit"].tolist() != units:
                raise ValueError(f"circuit order differs in {run_dir}")
            if not np.allclose(solution["p_i"].to_numpy(float), population):
                raise ValueError(f"population differs in {run_dir}")

            x = solution["x_star"].to_numpy(int)
            y = solution["y_star"].to_numpy(int)
            z_plan = solution["z_final"].to_numpy(int)
            if int(x.sum()) != int(record.x_sum) or int(y.sum()) != int(record.y_sum):
                raise ValueError(f"stored solution does not match all_comparison.csv in {run_dir}")
            total_population = float(population.sum())
            w_cap = setup["W"] * total_population
            recourse = realized_recourse(
                population, truth[exp], x, y,
                effectiveness=setup["E"], gamma=setup["G"], delta=setup["delta"], w_cap=w_cap,
            )
            slots = int(np.floor(setup["C"] * len(population) + 1e-9))
            if int(x.sum()) < slots:
                x_filled = fill_sectionalization(x, population * predicted[exp], slots)
                filled = realized_recourse(
                    population, truth[exp], x_filled, y,
                    effectiveness=setup["E"], gamma=setup["G"], delta=setup["delta"], w_cap=w_cap,
                )
                audit["recourse_not_proven_optimal"] += int(not filled.proven_optimal)
                exposure_filled, sectionalized_filled = filled.exposure, int(x_filled.sum())
            else:
                exposure_filled, sectionalized_filled = recourse.exposure, int(x.sum())
            audit["recourse_not_proven_optimal"] += int(not recourse.proven_optimal)
            total_runs += 1
            if total_runs % 500 == 0:
                print(f"  {total_runs} runs read ({time.time() - started:.0f} s)", flush=True)
            recorded = float(record.eval_cost)
            rel = abs(recourse.exposure - recorded) / max(recorded, 1.0)
            audit["max_rel_exposure_diff"] = max(audit["max_rel_exposure_diff"], rel)
            if recourse.exposure < recorded - 1e-6 * max(recorded, 1.0):
                audit["exact_lower_than_recorded"] += 1
            if recourse.exposure > recorded + 1e-6 * max(recorded, 1.0):
                audit["exact_higher_than_recorded"] += 1
            count_diff = abs(int(recourse.z.sum()) - int(record.z_star_sum))
            audit["shutoff_count_differs"] += int(count_diff > 0)
            audit["max_shutoff_count_diff"] = max(audit["max_shutoff_count_diff"], count_diff)

            baseline = float(np.dot(population, truth[exp]))
            tier3 = None  # filled after circuits load
            covered = None
            if method in COVERAGE_COLUMN:
                covered = int(coverage_log.loc[exp, COVERAGE_COLUMN[method]] == 0)
            robust_plan = method not in ("co_optimized", "planning_only")
            rows.append({
                "setup_id": setup["setup_id"], "slug": setup["slug"], "prediction_setup": setup["prediction_setup"],
                **{p.key: setup[p.key] for p in PARAMETERS}, "delta": setup["delta"], "alpha": setup["alpha"],
                "exp": exp, "method": method,
                "exposure": recorded,
                "exposure_exact": recourse.exposure,
                "exposure_baseline": baseline,
                "risk_reduction": 1.0 - recorded / baseline,
                "exposure_filled": exposure_filled,
                "sectionalized_filled": sectionalized_filled,
                "risk_reduction_filled": 1.0 - exposure_filled / baseline,
                "planned_exposure": float(record.worst_value) if method != "planning_only" else np.nan,
                "sectionalized": int(record.x_sum),
                "fast_trip": int(record.y_sum),
                "psps_planned": int(record.z_sum),
                "psps_realized": int(record.z_star_sum),
                "psps_realized_exact": int(recourse.z.sum()),
                "saifi_fast_trip": float(np.dot(setup["G"] * population, y)) / total_population,
                "saifi_psps_planned": float(np.dot(setup["delta"] * population, z_plan)) / total_population,
                "saifi_psps_realized": float(np.dot(setup["delta"] * population, recourse.z)) / total_population,
                "population_sectionalized": float(np.dot(population, x)) / total_population,
                "population_fast_trip": float(np.dot(population, y)) / total_population,
                "circuit_width": float(record.avg_circuit_bound_width) if robust_plan else np.nan,
                "group_width": float(record.avg_group_bound_width) if robust_plan else np.nan,
                "set_covered": covered,
                "elapsed_sec": float(record.elapsed_sec),
                "timed_out": bool(record.timed_out),
                "gap": float(record.gap),
                "tier3": tier3,
            })
            decisions.append((setup["setup_id"], method, exp, encode_decisions(x, y, z_plan, recourse.z)))

    assert units is not None
    ranking = ranking_baselines(setups, skipped, population, truth, predicted)
    circuits, geometry = load_circuits(metadata_csv, units, tolerance)
    tier3_mask = (circuits["hftd_tier"] == "Tier 3").to_numpy()
    runs = pd.DataFrame(rows)
    flags = {(s, m, e): code for s, m, e, code in decisions}
    tier3_x, tier3_y = [], []
    for row in runs.itertuples(index=False):
        code = flags[(row.setup_id, row.method, row.exp)]
        values = np.array([int(ch, 16) for ch in code])
        x, y = (values & 1) > 0, (values & 2) > 0
        tier3_x.append(float(tier3_mask[x].mean()) if x.any() else np.nan)
        tier3_y.append(float(tier3_mask[y].mean()) if y.any() else np.nan)
    runs = runs.drop(columns="tier3").assign(tier3_share_sectionalized=tier3_x, tier3_share_fast_trip=tier3_y)

    per_setup = runs.groupby("setup_id").agg(runs=("exp", "size"), exps=("exp", "nunique"))
    complete = per_setup.index[(per_setup["exps"] == 5) & (per_setup["runs"] == 5 * len(METHODS))]
    runs["complete_setup"] = runs["setup_id"].isin(complete)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    runs.to_csv(DATA_DIR / "runs.csv", index=False, float_format="%.10g")
    circuits.to_csv(DATA_DIR / "circuits.csv", index=False)
    # Test-period ignitions and the prediction model's means, by replication and circuit.
    position = {unit: i for i, unit in enumerate(units)}
    outcomes_out = outcomes[outcomes["unit"].isin(position)].assign(circuit=lambda d: d["unit"].map(position))
    outcomes_out[["exp_id", "circuit", "true_y", "test_mu"]].rename(columns={"exp_id": "exp"}).sort_values(
        ["exp", "circuit"]).to_csv(DATA_DIR / "outcomes.csv", index=False, float_format="%.10g")
    with gzip.open(DATA_DIR / "decisions.csv.gz", "wt", encoding="utf-8") as handle:
        pd.DataFrame(decisions, columns=["setup_id", "method", "exp", "flags"]).to_csv(handle, index=False)
    ranking.to_csv(DATA_DIR / "ranking_baselines.csv", index=False, float_format="%.10g")

    report = {
        "source_grid_dir": str(grid_dir),
        "prediction_dir": prediction_slug,
        "prediction_setups_in_grid": sorted({s["prediction_setup"] for s in setups}),
        "setups": len(setups),
        "setups_ingested": int(len(per_setup)),
        "complete_setups": int(len(complete)),
        "incomplete_setups": sorted(set(per_setup.index) - set(complete)),
        "skipped_setups": skipped,
        "runs": int(len(runs)),
        "circuits": len(units),
        "methods": [m.slug for m in METHODS],
        "audit": {
            **audit,
            "note": ("exposure_exact re-solves the realized PSPS recourse with HiGHS at a 1e-6 relative MIP "
                     "gap. The grid's eval_cost came from Gurobi at its default 1e-4 relative gap, so small "
                     "differences, and different shutoff counts at the same exposure, are expected."),
        },
        "timed_out_runs": int(runs["timed_out"].sum()),
        "elapsed_sec": round(time.time() - started, 1),
    }
    (DATA_DIR / "ingest_report.json").write_text(json.dumps(report, indent=2) + "\n")
    write_web(runs, circuits, geometry, decisions, report, ranking)
    return report


def write_web(runs: pd.DataFrame, circuits: pd.DataFrame, geometry: list, decisions: list, report: dict,
              ranking: pd.DataFrame) -> None:
    """Columnar JSON for the workspace: study.json, circuits.json, decisions/<setup>.json."""

    WEB_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_DIR / "decisions").mkdir(exist_ok=True)
    for stale in (WEB_DIR / "decisions").glob("*.json"):
        stale.unlink()

    setup_cols = [p.key for p in PARAMETERS]
    setups = (runs.drop_duplicates("setup_id")
              .sort_values(setup_cols)[["setup_id", *setup_cols, "complete_setup"]]
              .reset_index(drop=True))
    setup_index = {sid: i for i, sid in enumerate(setups["setup_id"])}
    method_index = {m.slug: i for i, m in enumerate(METHODS)}
    ordered = runs.assign(_s=runs["setup_id"].map(setup_index), _m=runs["method"].map(method_index)).sort_values(["_s", "_m", "exp"])

    def col(name: str, digits: int | None) -> list:
        values = ordered[name].to_numpy(float)
        if digits is None:
            return [None if np.isnan(v) else int(round(v)) for v in values]
        return [None if np.isnan(v) else round(float(v), digits) for v in values]

    baseline = runs.groupby("exp")["exposure_baseline"].first().sort_index()
    ranked = ranking.set_index(["setup_id", "exp"])["rank_exposure_exposure"]
    ranking_exposure = [None if (sid, exp) not in ranked.index else int(round(float(ranked.loc[(sid, exp)])))
                        for sid in setups["setup_id"] for exp in baseline.index]
    coverage = (runs.dropna(subset=["set_covered"]).groupby(["method", "exp"])["set_covered"].first()
                .unstack("exp").reindex([m.slug for m in METHODS if m.slug in COVERAGE_COLUMN]))
    study = {
        "version": 1,
        "source": {
            "paper": "https://arxiv.org/abs/2604.01232",
            "grid": report["source_grid_dir"],
            "prediction_setup": report["prediction_dir"],
            "complete_setups": report["complete_setups"],
            "setups": report["setups_ingested"],
            "skipped_setups": len(report["skipped_setups"]),
            "runs": report["runs"],
            "circuits": report["circuits"],
        },
        "methods": [{"slug": m.slug, "label": m.label, "short": m.short, "family": m.family, "description": m.description} for m in METHODS],
        "parameters": [{"key": p.key, "label": p.label, "unit": p.unit, "description": p.description,
                        "values": sorted(float(v) for v in runs[p.key].unique())} for p in PARAMETERS],
        "fixed": {"delta": float(runs["delta"].iloc[0]), "alpha": float(runs["alpha"].iloc[0])},
        "baselineExposure": [round(float(v), 1) for v in baseline.to_numpy()],
        # Not a grid method: every circuit ranked by predicted population-weighted
        # exposure, the budget filled from the top, no fast-trip, the same test-year
        # PSPS response. One value per setup and replication (setup-major).
        "rankingExposure": ranking_exposure,
        "coverage": {slug: [int(v) for v in coverage.loc[slug].to_numpy()] for slug in coverage.index},
        "setups": {
            "id": setups["setup_id"].tolist(),
            **{key: [float(v) for v in setups[key]] for key in setup_cols},
            "complete": [bool(v) for v in setups["complete_setup"]],
        },
        "runs": {
            "setup": ordered["_s"].astype(int).tolist(),
            "method": ordered["_m"].astype(int).tolist(),
            "exp": ordered["exp"].astype(int).tolist(),
            "exposure": col("exposure", None),
            "exposureFilled": col("exposure_filled", None),
            "plannedExposure": col("planned_exposure", None),
            "sectionalized": col("sectionalized", None),
            "sectionalizedFilled": col("sectionalized_filled", None),
            "fastTrip": col("fast_trip", None),
            "pspsPlanned": col("psps_planned", None),
            "pspsRealized": col("psps_realized_exact", None),
            "saifiFastTrip": col("saifi_fast_trip", 5),
            "saifiPspsPlanned": col("saifi_psps_planned", 5),
            "saifiPspsRealized": col("saifi_psps_realized", 5),
            "populationSectionalized": col("population_sectionalized", 4),
            "circuitWidth": col("circuit_width", 3),
            "elapsedSec": col("elapsed_sec", 2),
            "timedOut": [bool(v) for v in ordered["timed_out"]],
        },
    }
    (WEB_DIR / "study.json").write_text(json.dumps(study, separators=(",", ":")) + "\n")

    web_circuits = {
        "unit": circuits["unit"].tolist(),
        "circuitId": circuits["circuit_id"].tolist(),
        "division": circuits["division"].tolist(),
        "tier": [3 if t == "Tier 3" else 2 if t == "Tier 2" else None for t in circuits["hftd_tier"]],
        "population": [round(float(v)) for v in circuits["population"]],
        "lines": geometry,
    }
    (WEB_DIR / "circuits.json").write_text(json.dumps(web_circuits, separators=(",", ":")) + "\n")

    by_setup: dict[str, dict[str, list[str]]] = {}
    for sid, method, exp, code in sorted(decisions, key=lambda d: (d[0], method_index[d[1]], d[2])):
        by_setup.setdefault(sid, {}).setdefault(method, []).append(code)
    for sid, methods in by_setup.items():
        payload = {"setup": sid, "circuits": len(circuits), "methods": methods}
        (WEB_DIR / "decisions" / f"{sid}.json").write_text(json.dumps(payload, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grid-dir", type=Path, required=True)
    parser.add_argument("--prediction-dir", type=Path, required=True)
    parser.add_argument("--circuit-metadata", type=Path, required=True)
    parser.add_argument("--geometry-tolerance", type=float, default=GEOMETRY_TOLERANCE_DEG)
    args = parser.parse_args()
    report = ingest(args.grid_dir, args.prediction_dir, args.circuit_metadata, tolerance=args.geometry_tolerance)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
