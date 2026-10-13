# Sectionalization and fast-trip planning study

An analysis module for the optimization grid behind Chen, Zhu, and Sioshansi,
[Large-Scale Resilience Planning for Wildfire-Prone Electricity-System via Adaptive Robust Optimization](https://arxiv.org/abs/2604.01232).
It ingests the grid's raw results, re-checks every realized cost, summarizes how
the eight planning methods behave across budgets, reliability caps, and
fast-trip assumptions, and feeds the workspace's **Planning study** panels.
Findings: [FINDINGS.md](FINDINGS.md).

## The model in one paragraph

For each of 803 PG&E distribution circuits in HFTD Tier 2 or 3, a plan chooses
which circuits to sectionalize (`x`) and which to give fast-trip settings (`y`,
only on sectionalized circuits); after the test-year ignitions `u` are realized,
PSPS shuts off sectionalized circuits (`z`). Exposure is
`sum_i p_i u_i (1 - E y_i)(1 - z_i)` with `p_i` the population served and `E`
the fast-trip effectiveness. The reliability cap is a SAIFI limit,
`sum_i p_i (G y_i + delta z_i) <= W * sum_i p_i`. The budgets are
`sum x <= C n` and `sum y <= B n`. The definitions follow the code that
produced the grid (`Sectionalization-California/optimization/robust.py` and
`optimization.py`); `model.py` restates them.

| Key | Parameter | Grid values |
|---|---|---|
| C | Sectionalization budget (share of circuits) | 0.2, 0.3, 0.4 |
| B | Fast-trip budget (share of circuits) | 0.1, 0.2, 0.3 |
| W | SAIFI cap (interruptions per customer-year) | 0.1, 0.2, 0.3 |
| E | Fast-trip effectiveness (`alpha_effective`) | 0.6, 0.7, 0.8, 0.9 |
| G | Fast-trip SAIFI factor (`gamma`) | 0.8, 0.9, 0.95 |

`delta = 1` and the miscoverage level `alpha = 0.4` are fixed. The grid is not
a full factorial: C 20% was run only with B 10%, and E 60% only in that block.
The 108 setups with C in {0.3, 0.4}, B in {0.2, 0.3}, and E in {0.7, 0.8, 0.9}
form a balanced factorial, which `analyze.py` uses for parameter effects.

## Run

From the repository root. `ingest` reads the raw grid (34 GB on the external
drive) and needs pandas, numpy, and scipy (`requirements.txt`); `figures` also
needs matplotlib (`requirements-analysis.txt`).

```sh
PYTHONPATH=. python -m analysis.sectionalization.ingest \
  --grid-dir /Volumes/RyanSSD2/optimization_results_grid \
  --prediction-dir <Sectionalization-California>/outputs/prediction_results_grid/a0p4__exp5__train20__cal200__grp5__49ac8c7c07 \
  --circuit-metadata <Sectionalization-California>/outputs/circuit_metadata.csv
PYTHONPATH=. python -m analysis.sectionalization.analyze
PYTHONPATH=. python -m analysis.sectionalization.figures
```

The grid names two prediction setups (`...49ac8c7c07` and `...9a2294b515`).
They differ only in the file paths they were read from; their uncertainty sets
match to 1e-13 and their test outcomes reproduce the same realized costs, so
one prediction directory serves the whole grid. Ingest takes about 7 minutes,
analysis under a minute.

## Outputs

| File | What it holds |
|---|---|
| `data/runs.csv` | One row per run (setup x replication x method): parameters, realized exposure as recorded (`exposure`) and re-solved (`exposure_exact`), the budget-filled variant (`exposure_filled`, `sectionalized_filled`), decision counts, SAIFI used by fast-trip and PSPS, set width and coverage, solve time |
| `data/circuits.csv` | The 803 circuits: platform circuit ID (9-digit), name, division, HFTD tier, population, centroid |
| `data/decisions.csv.gz` | Per-run decisions, one hex flag per circuit: 1 sectionalized, 2 fast-trip, 4 planned PSPS, 8 test-year PSPS |
| `data/outcomes.csv` | Test-year ignitions and the prediction model's means, by replication and circuit |
| `data/ingest_report.json` | Completeness, the skipped setup, and the realized-cost audit |
| `results.json` | Every number in FINDINGS.md |
| `figures/*.png` | The figures in FINDINGS.md |
| `website/data/optimization_grid/` | The same data shaped for the workspace (`study.json`, `circuits.json`, one `decisions/<setup>.json` per setup) |

Setup ids read `C30-B20-W20-E80-G90` (values in percent).

## Checks built in

- **Realized-cost audit.** For every run, ingest re-solves the test-year PSPS
  response with HiGHS (`model.realized_recourse`, relative gap 1e-6) and
  compares it with the grid's `eval_cost` (Gurobi at its default 1e-4 gap). The
  largest difference is 1.0e-4 relative, and HiGHS is never worse.
- **Shutoff counts are not unique.** Many circuits tie on exposure per
  customer, so optimal PSPS sets can differ by dozens of circuits at the same
  exposure (906 runs differ from the grid's count, by up to 33). Compare
  exposure or SAIFI shares, not shutoff counts.
- **Skipped setup.** `C20-B10-W30-E70-G90` was interrupted: replication 0 has
  all methods, replication 1 has four, and its comparison file predates the
  realized-cost columns. It is listed in `ingest_report.json` and left out.
- **Budget fill.** `model.fill_sectionalization` completes a plan's unused
  sectionalization budget by predicted exposure (information available at
  planning time). Sectionalization is free in the model beyond its budget, so
  this can never raise exposure; tests check that on random instances.

Tests: `tests/test_sectionalization_analysis.py` (no data files needed).
