"""Summarize the sectionalization grid into the numbers behind FINDINGS.md.

Run from the repository root after ``ingest``::

    PYTHONPATH=. python -m analysis.sectionalization.analyze

Reads ``analysis/sectionalization/data/`` and writes
``analysis/sectionalization/results.json``. Every comparison is paired: two
methods are compared on the same setup and the same replication (the same
synthetic training, calibration, and test draw), so differences are not
confounded by the ignition draw.

Exposure is population-weighted ignitions left after fast-trip and PSPS
(``sum p_i u_i (1 - e y_i)(1 - z_i)``). "Risk reduction" is the share of the
unmitigated exposure (``sum p_i u_i``) that a plan removes. "Gain of A over B"
is ``1 - exposure_A / exposure_B``: the share of B's remaining exposure that A
removes; positive means A is better.
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.sectionalization.model import METHODS, decode_decisions

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
RESULTS = HERE / "results.json"
PROPOSED = "group_conformal_random"
ROBUST = [m.slug for m in METHODS if m.family == "robust"]
FACTORIAL = {"C": [0.3, 0.4], "B": [0.2, 0.3], "W": [0.1, 0.2, 0.3], "E": [0.7, 0.8, 0.9], "G": [0.8, 0.9, 0.95]}
REFERENCE_SETUP = {"C": 0.3, "B": 0.2, "W": 0.2, "E": 0.8, "G": 0.9}


def _r(value, digits: int = 4):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    return round(float(value), digits)


def describe(values: pd.Series, digits: int = 4) -> dict:
    values = pd.Series(values, dtype=float).dropna()
    if values.empty:
        return {"n": 0}
    return {
        "n": int(len(values)),
        "mean": _r(values.mean(), digits),
        "median": _r(values.median(), digits),
        "p10": _r(values.quantile(0.10), digits),
        "p90": _r(values.quantile(0.90), digits),
        "min": _r(values.min(), digits),
        "max": _r(values.max(), digits),
    }


def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    runs = pd.read_csv(DATA_DIR / "runs.csv")
    circuits = pd.read_csv(DATA_DIR / "circuits.csv", dtype={"circuit_id": str})
    decisions = pd.read_csv(DATA_DIR / "decisions.csv.gz", dtype={"flags": str})
    runs = runs[runs["complete_setup"]].copy()
    n = len(circuits)
    runs["c_slots"] = np.floor(runs["C"] * n + 1e-9).astype(int)
    runs["b_slots"] = np.floor(runs["B"] * n + 1e-9).astype(int)
    runs["c_utilization"] = runs["sectionalized"] / runs["c_slots"]
    runs["b_utilization"] = runs["fast_trip"] / runs["b_slots"]
    for part in ("fast_trip", "psps_planned", "psps_realized"):
        runs[f"cap_share_{part}"] = runs[f"saifi_{part}"] / runs["W"]
    runs["plan_to_realized"] = runs["planned_exposure"] / runs["exposure"]
    return runs, circuits, decisions


def exposure_table(runs: pd.DataFrame) -> pd.DataFrame:
    return runs.pivot_table(index=["setup_id", "exp"], columns="method", values="exposure")


def gain(table: pd.DataFrame, a: str, b: str) -> pd.Series:
    return 1.0 - table[a] / table[b]


def method_summary(runs: pd.DataFrame) -> list[dict]:
    table = exposure_table(runs)
    ranks = table.rank(axis=1, method="average")
    best = table.eq(table.min(axis=1), axis=0)
    best_share = best.div(best.sum(axis=1), axis=0)  # ties share the win
    rows = []
    for method in METHODS:
        r = runs[runs["method"] == method.slug]
        row = {
            "method": method.slug,
            "label": method.label,
            "runs": int(len(r)),
            "risk_reduction": describe(r["risk_reduction"]),
            "gain_vs_planning_only": describe(gain(table, method.slug, "planning_only")),
            "gain_vs_co_optimized": describe(gain(table, method.slug, "co_optimized")),
            "mean_rank_of_8": _r(ranks[method.slug].mean(), 3),
            "best_share": _r(best_share[method.slug].mean(), 4),
            "sectionalized": describe(r["sectionalized"], 1),
            "sectionalization_budget_used": describe(r["c_utilization"], 3),
            "fast_trip": describe(r["fast_trip"], 1),
            "fast_trip_budget_used": describe(r["b_utilization"], 3),
            "cap_share_fast_trip": describe(r["cap_share_fast_trip"], 3),
            "cap_share_psps_planned": describe(r["cap_share_psps_planned"], 3),
            "cap_share_psps_realized": describe(r["cap_share_psps_realized"], 3),
            "psps_realized_circuits": describe(r["psps_realized_exact"], 1),
            "plan_to_realized_exposure": describe(r["plan_to_realized"], 3),
            "timed_out_share": _r(r["timed_out"].mean(), 4),
            "solve_seconds": describe(r["elapsed_sec"], 2),
            "tier3_share_sectionalized": describe(r["tier3_share_sectionalized"], 3),
        }
        if method.slug in ROBUST:
            covered = r.drop_duplicates("exp").sort_values("exp")["set_covered"]
            row["set_covered_by_replication"] = [int(v) for v in covered]
            row["circuit_bound_width"] = describe(r["circuit_width"], 3)
        rows.append(row)
    return rows


def paired_against(runs: pd.DataFrame, reference: str = PROPOSED) -> dict:
    table = exposure_table(runs)
    out = {}
    for method in METHODS:
        if method.slug == reference:
            continue
        g = gain(table, reference, method.slug)
        per_setup = 1.0 - table[reference].groupby(level="setup_id").mean() / table[method.slug].groupby(level="setup_id").mean()
        out[method.slug] = {
            "gain": describe(g),
            "share_of_runs_better": _r((g > 1e-9).mean()),
            "share_of_runs_tied": _r((g.abs() <= 1e-9).mean()),
            "share_of_setups_better_on_average": _r((per_setup > 0).mean()),
            "median_gain_by_replication": [_r(v) for v in g.groupby(level="exp").median()],
        }
    return out


def factorial_block(runs: pd.DataFrame) -> pd.DataFrame:
    mask = np.ones(len(runs), dtype=bool)
    for key, levels in FACTORIAL.items():
        mask &= runs[key].round(4).isin(levels)
    block = runs[mask]
    expected = int(np.prod([len(v) for v in FACTORIAL.values()]))
    if block["setup_id"].nunique() != expected:
        raise ValueError(f"factorial block has {block['setup_id'].nunique()} setups, expected {expected}")
    return block


def main_effects(block: pd.DataFrame) -> dict:
    """Mean of each metric at each level of each parameter, averaged over the rest."""

    table = exposure_table(block)
    filled = block.pivot_table(index=["setup_id", "exp"], columns="method", values="exposure_filled")
    params = block.drop_duplicates(["setup_id"]).set_index("setup_id")[list(FACTORIAL)]
    gains = pd.DataFrame({
        "gain_vs_co_optimized": gain(table, PROPOSED, "co_optimized"),
        "gain_vs_planning_only": gain(table, PROPOSED, "planning_only"),
        "gain_vs_bonferroni": gain(table, PROPOSED, "bonferroni"),
        "filled_gain_vs_co_optimized": gain(filled, PROPOSED, "co_optimized"),
    }).reset_index().join(params, on="setup_id")

    def effects(frame: pd.DataFrame, column: str) -> dict:
        result = {}
        for key in FACTORIAL:
            levels = frame.groupby(key)[column].mean()
            result[key] = {"levels": {f"{lvl:g}": _r(v) for lvl, v in levels.items()},
                           "range": _r(levels.max() - levels.min())}
        return result

    out = {"setups": int(block["setup_id"].nunique()), "runs": int(len(block)), "parameters": FACTORIAL,
           "risk_reduction": {}, "fast_trip": {}, "cap_share_fast_trip": {}, "sectionalized": {}}
    for method in METHODS:
        r = block[block["method"] == method.slug]
        out["risk_reduction"][method.slug] = effects(r, "risk_reduction")
        out["fast_trip"][method.slug] = effects(r, "fast_trip")
        out["cap_share_fast_trip"][method.slug] = effects(r, "cap_share_fast_trip")
        out["sectionalized"][method.slug] = effects(r, "sectionalized")
    out["proposed_gains"] = {column: effects(gains, column) for column in
                             ("gain_vs_co_optimized", "gain_vs_planning_only", "gain_vs_bonferroni",
                              "filled_gain_vs_co_optimized")}
    # Two-way view of the gain over planning-only: SAIFI cap by fast-trip effectiveness.
    two_way = gains.groupby(["W", "E"])["gain_vs_planning_only"].mean().unstack("E")
    out["gain_vs_planning_only_by_W_and_E"] = {f"W={w:g}": {f"E={e:g}": _r(v) for e, v in row.items()}
                                               for w, row in two_way.iterrows()}
    return out


def budget_value(block: pd.DataFrame) -> dict:
    """Matched change in risk reduction from one budget step, all else equal."""

    out = {}
    for param, low, high, others in (("C", 0.3, 0.4, ["B", "W", "E", "G"]), ("B", 0.2, 0.3, ["C", "W", "E", "G"])):
        index = [*others, "exp", "method"]
        lo = block[block[param].round(4) == low].set_index(index)["risk_reduction"]
        hi = block[block[param].round(4) == high].set_index(index)["risk_reduction"]
        delta = (hi - lo).dropna()
        out[f"{param}_{low:g}_to_{high:g}"] = {
            method.slug: describe(delta.xs(method.slug, level="method")) for method in METHODS
        }
    return out


def allocation(runs: pd.DataFrame) -> dict:
    """Share of the SAIFI cap each method spends on fast-trip and on PSPS, by cap."""

    out = {}
    for method in METHODS:
        r = runs[runs["method"] == method.slug]
        by_w = r.groupby("W")[["cap_share_fast_trip", "cap_share_psps_planned", "cap_share_psps_realized"]].mean()
        out[method.slug] = {f"{w:g}": {k: _r(v, 3) for k, v in row.items()} for w, row in by_w.iterrows()}
    return out


def computation(runs: pd.DataFrame) -> dict:
    out = {}
    for slug in ("group_conformal_random", "group_conformal_fixed", "group_conformal_oracle"):
        r = runs[runs["method"] == slug]
        out[slug] = {key: {f"{lvl:g}": _r(v, 3) for lvl, v in r.groupby(key)["timed_out"].mean().items()}
                     for key in ("C", "B", "W")}
    table = exposure_table(runs)
    timed = runs[runs["method"] == PROPOSED].set_index(["setup_id", "exp"])["timed_out"]
    g = gain(table, PROPOSED, "co_optimized")
    out["proposed_gain_vs_co_optimized_when_timed_out"] = describe(g[timed.reindex(g.index).astype(bool)])
    out["proposed_gain_vs_co_optimized_when_solved"] = describe(g[~timed.reindex(g.index).astype(bool)])
    return out


def _flags(decisions: pd.DataFrame) -> dict[tuple[str, str, int], dict[str, np.ndarray]]:
    return {(row.setup_id, row.method, int(row.exp)): decode_decisions(row.flags)
            for row in decisions.itertuples(index=False)}


def stability(runs: pd.DataFrame, flags: dict) -> dict:
    """How much the chosen circuits move across replications and across methods."""

    def jaccard(a: np.ndarray, b: np.ndarray) -> float:
        union = np.logical_or(a, b).sum()
        return float(np.logical_and(a, b).sum() / union) if union else np.nan

    setups = sorted(runs["setup_id"].unique())
    exps = sorted(runs["exp"].unique())
    out: dict = {"across_replications": {}, "overlap_with_proposed": {}}
    for method in METHODS:
        for kind in ("x", "y"):
            values = [jaccard(flags[(s, method.slug, a)][kind], flags[(s, method.slug, b)][kind])
                      for s in setups for a, b in combinations(exps, 2)]
            out["across_replications"].setdefault(method.slug, {})[kind] = _r(np.nanmean(values), 3)
        if method.slug != PROPOSED:
            values = [jaccard(flags[(s, PROPOSED, e)]["x"], flags[(s, method.slug, e)]["x"]) for s in setups for e in exps]
            out["overlap_with_proposed"][method.slug] = _r(np.nanmean(values), 3)
    return out


def core_circuits(runs: pd.DataFrame, flags: dict, circuits: pd.DataFrame) -> dict:
    """Circuits the proposed method sectionalizes in nearly every setup and replication."""

    keys = [(s, PROPOSED, int(e)) for s, e in runs[runs["method"] == PROPOSED][["setup_id", "exp"]].itertuples(index=False)]
    x = np.mean([flags[k]["x"] for k in keys], axis=0)
    y = np.mean([flags[k]["y"] for k in keys], axis=0)
    z = np.mean([flags[k]["z_realized"] for k in keys], axis=0)
    frame = circuits.assign(x_share=x, y_share=y, psps_share=z)
    core = frame[frame["x_share"] >= 0.9]
    never = frame[frame["x_share"] == 0]
    tier3_base = float((circuits["hftd_tier"] == "Tier 3").mean())
    return {
        "runs": len(keys),
        "core_threshold": 0.9,
        "core_circuits": int(len(core)),
        "core_tier3_share": _r((core["hftd_tier"] == "Tier 3").mean(), 3),
        "all_circuits_tier3_share": _r(tier3_base, 3),
        "core_population_share": _r(core["population"].sum() / circuits["population"].sum(), 3),
        "core_top_divisions": {k: int(v) for k, v in core["division"].value_counts().head(6).items()},
        "never_sectionalized": int(len(never)),
        "sectionalization_share_quantiles": {q: _r(np.quantile(x, q), 3) for q in (0.25, 0.5, 0.75, 0.9)},
        "fast_trip_ever_share": _r((y > 0).mean(), 3),
        "psps_ever_share": _r((z > 0).mean(), 3),
    }


def budget_fill(runs: pd.DataFrame) -> dict:
    """Unused sectionalization budget, completed by predicted exposure.

    Sectionalization has no cost in the model besides its budget and only adds
    PSPS options, so a plan that leaves budget unused can be completed for free
    (``model.fill_sectionalization``, run by ``ingest``: circuits in order of the
    predicted exposure ``p_i * mu_i``, then the realized PSPS response re-solved).
    """

    out = {"note": budget_fill.__doc__.split("\n\n")[1].replace("\n", " ").strip()}
    for method in METHODS:
        r = runs[runs["method"] == method.slug]
        f = r[r["sectionalized"] < r["c_slots"]]
        entry = {"runs_with_unused_budget": int(len(f)), "share_of_runs": _r(len(f) / len(r), 3)}
        if len(f):
            entry["circuits_added"] = describe(f["sectionalized_filled"] - f["sectionalized"], 1)
            entry["exposure_cut"] = describe(1.0 - f["exposure_filled"] / f["exposure_exact"])
        out[method.slug] = entry
    exact = runs.pivot_table(index=["setup_id", "exp"], columns="method", values="exposure_exact")
    filled = runs.pivot_table(index=["setup_id", "exp"], columns="method", values="exposure_filled")
    baseline = runs.groupby(["setup_id", "exp"])["exposure_baseline"].first()
    out["after_fill"] = {}
    for method in METHODS:
        entry = {"risk_reduction": describe(1.0 - filled[method.slug] / baseline)}
        if method.slug != PROPOSED:
            g = 1.0 - filled[PROPOSED] / filled[method.slug]
            entry["proposed_gain"] = describe(g)
            entry["proposed_better_share"] = _r((g > 1e-9).mean())
            entry["share_of_setups_better_on_average"] = _r(
                (filled[PROPOSED].groupby(level="setup_id").mean() < filled[method.slug].groupby(level="setup_id").mean()).mean())
        out["after_fill"][method.slug] = entry
    # How much of the proposed method's lead over co-optimized remains once both are filled.
    before_gap = (1.0 - exact[PROPOSED] / exact["co_optimized"]).median()
    after_gap = (1.0 - filled[PROPOSED] / filled["co_optimized"]).median()
    out["lead_over_co_optimized_kept_after_fill"] = _r(after_gap / before_gap if before_gap else np.nan, 3)
    return out


def ranking_baselines(runs: pd.DataFrame) -> dict:
    """Simple ranking plans (ingest.ranking_baselines) against every grid method, paired by setup and replication."""

    ranking = pd.read_csv(DATA_DIR / "ranking_baselines.csv").set_index(["setup_id", "exp"])
    first = runs.drop_duplicates(["setup_id", "exp"]).set_index(["setup_id", "exp"])
    ranking = ranking.loc[first.index]
    baseline = first["exposure_baseline"]
    out = {"note": "Fill the whole sectionalization budget by a predicted ranking, no fast-trip, same test-year PSPS response.",
           "rules": {}}
    for rule, label in (("rank_exposure", "predicted population-weighted exposure p_i * mu_i"), ("rank_rate", "predicted ignition rate mu_i")):
        rr = 1.0 - ranking[f"{rule}_exposure"] / baseline
        out["rules"][rule] = {
            "ranks_by": label,
            "risk_reduction": describe(rr),
            "risk_reduction_by_W": {f"{w:g}": _r(v) for w, v in rr.groupby(first["W"]).mean().items()},
            "cap_share_psps": _r((ranking[f"{rule}_saifi_psps"] / first["W"]).mean(), 3),
        }
    solved = exposure_table(runs)
    filled = runs.pivot_table(index=["setup_id", "exp"], columns="method", values="exposure_filled")
    reference = ranking["rank_exposure_exposure"]
    out["methods_vs_rank_exposure"] = {}
    for method in METHODS:
        entry = {}
        for name, table in (("solved", solved), ("filled", filled)):
            g = 1.0 - table[method.slug] / reference.reindex(table.index)
            entry[name] = {"gain": describe(g), "better_share": _r((g > 1e-9).mean())}
        out["methods_vs_rank_exposure"][method.slug] = entry
    return out


def fast_trip_threshold(runs: pd.DataFrame) -> dict:
    """Fast-trip use by effectiveness E and SAIFI factor G.

    One fast-trip setting removes ``E`` of a circuit's exposure for ``G`` units of
    SAIFI; one PSPS shutoff removes all of it for ``delta`` units, but only where
    and when it is used. The ratio ``E / (G / delta)`` is fast-trip's exposure
    removed per unit of reliability relative to a shutoff.
    """

    out = {"note": fast_trip_threshold.__doc__.split("\n\n")[1].replace("\n", " ").strip(), "methods": {}}
    for slug in (PROPOSED, "co_optimized", "bonferroni"):
        r = runs[runs["method"] == slug].assign(ratio=lambda d: d["E"] * d["delta"] / d["G"])
        cells = r.groupby(["E", "G"]).agg(fast_trip=("fast_trip", "mean"), ratio=("ratio", "first"),
                                          share_using=("fast_trip", lambda v: (v > 0).mean()))
        out["methods"][slug] = [{"E": _r(e, 2), "G": _r(g, 2), "ratio": _r(row.ratio, 3),
                                 "mean_fast_trip": _r(row.fast_trip, 2), "share_of_runs_using": _r(row.share_using, 3)}
                                for (e, g), row in cells.iterrows()]
    return out


def reference_setup(runs: pd.DataFrame) -> dict:
    mask = np.ones(len(runs), dtype=bool)
    for key, value in REFERENCE_SETUP.items():
        mask &= runs[key].round(4) == value
    r = runs[mask]
    out = {"setup_id": r["setup_id"].iloc[0], "parameters": REFERENCE_SETUP, "methods": {}}
    for method in METHODS:
        m = r[r["method"] == method.slug]
        out["methods"][method.slug] = {
            "risk_reduction_mean": _r(m["risk_reduction"].mean()),
            "risk_reduction_by_replication": [_r(v) for v in m.sort_values("exp")["risk_reduction"]],
            "sectionalized_mean": _r(m["sectionalized"].mean(), 1),
            "fast_trip_mean": _r(m["fast_trip"].mean(), 1),
            "cap_share_fast_trip_mean": _r(m["cap_share_fast_trip"].mean(), 3),
            "cap_share_psps_realized_mean": _r(m["cap_share_psps_realized"].mean(), 3),
        }
    return out


def analyze() -> dict:
    runs, circuits, decisions = load()
    flags = _flags(decisions)
    block = factorial_block(runs)
    report = json.loads((DATA_DIR / "ingest_report.json").read_text())
    results = {
        "inputs": {
            "complete_setups": int(runs["setup_id"].nunique()),
            "runs": int(len(runs)),
            "replications": int(runs["exp"].nunique()),
            "circuits": int(len(circuits)),
            "skipped_setups": report.get("skipped_setups", []),
            "audit": report.get("audit", {}),
            "unmitigated_exposure_by_replication": [_r(v, 1) for v in runs.groupby("exp")["exposure_baseline"].first()],
        },
        "methods": method_summary(runs),
        "proposed_vs_each_method": paired_against(runs),
        "main_effects": main_effects(block),
        "budget_value": budget_value(block),
        "reliability_allocation": allocation(runs),
        "computation": computation(runs),
        "stability": stability(runs, flags),
        "core_circuits": core_circuits(runs, flags, circuits),
        "budget_fill": budget_fill(runs),
        "ranking_baselines": ranking_baselines(runs),
        "fast_trip_threshold": fast_trip_threshold(runs),
        "reference_setup": reference_setup(runs),
    }
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")
    return results


def main() -> None:
    results = analyze()
    for row in results["methods"]:
        rr = row["risk_reduction"]
        print(f"{row['label']:36s} risk reduction median {rr['median']:.3f} "
              f"| gain vs planning-only {row['gain_vs_planning_only'].get('median', float('nan')):+.3f} "
              f"| gain vs co-optimized {row['gain_vs_co_optimized'].get('median', float('nan')):+.3f} "
              f"| best in {row['best_share']:.1%}")
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    main()
