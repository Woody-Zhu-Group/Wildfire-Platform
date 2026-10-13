"""Planning model, method catalog, and the exact realized-recourse solver.

The study is the coordinated sectionalization, fast-trip, and PSPS problem in
Chen, Zhu, and Sioshansi, "Large-Scale Resilience Planning for Wildfire-Prone
Electricity-System via Adaptive Robust Optimization" (arXiv:2604.01232). The
definitions below follow the code that produced the grid
(``Sectionalization-California/optimization/robust.py`` and ``optimization.py``),
which is the ground truth for what each stored number means:

* ``x_i`` sectionalize circuit i, ``y_i`` enable fast-trip on it (``y <= x``),
  ``z_i`` de-energize it in a PSPS event (``z <= x``); all binary.
* Exposure: ``sum_i p_i * u_i * (1 - e * y_i) * (1 - z_i)``, where ``p_i`` is
  the population served, ``u_i`` the ignition count, and ``e`` the fast-trip
  effectiveness (``alpha_effective`` in the grid).
* Reliability (a SAIFI cap):
  ``sum_i p_i * (gamma * y_i + delta * z_i) <= W * sum_i p_i``.
* Budgets: ``sum x <= C * n`` and ``sum y <= B * n``.

The planning methods choose ``x`` and ``y``. The evaluation then observes the
test-period ignitions ``u`` and re-solves the PSPS recourse for ``z``; that
realized exposure is the grid's ``eval_cost``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


@dataclass(frozen=True)
class Method:
    slug: str
    label: str
    short: str
    family: str  # "robust", "deterministic", or "planning"
    description: str


# Order is the display order everywhere (tables, figures, the website).
METHODS: tuple[Method, ...] = (
    Method("group_conformal_random", "Group conformal, random groups", "GC random", "robust",
           "Proposed: adaptive robust plan over a group conformal set with random circuit groups."),
    Method("group_conformal_fixed", "Group conformal, fixed groups", "GC fixed", "robust",
           "Adaptive robust plan; circuit groups follow fixed service regions."),
    Method("group_conformal_oracle", "Group conformal, oracle groups", "GC oracle", "robust",
           "Adaptive robust plan; groups built from the simulator's spatial blocks."),
    Method("bonferroni", "Bonferroni", "Bonferroni", "robust",
           "Adaptive robust plan; per-circuit conformal bounds at a Bonferroni-corrected level."),
    Method("maxrank", "Max-rank", "Max-rank", "robust",
           "Adaptive robust plan; per-circuit bounds with the max-rank multiple-testing correction."),
    Method("ci", "Normal CI", "CI", "robust",
           "Adaptive robust plan; per-circuit normal-approximation intervals, Bonferroni split."),
    Method("co_optimized", "Co-optimized (no uncertainty set)", "Co-optimized", "deterministic",
           "Plans and PSPS optimized together against the predicted mean ignitions only."),
    Method("planning_only", "Planning only", "Planning only", "planning",
           "Sectionalization and fast-trip placed on the highest predicted hazard; PSPS decided later."),
)
METHOD_BY_SLUG = {method.slug: method for method in METHODS}
METHOD_BY_GRID_LABEL = {
    "Group Conformal (random)": "group_conformal_random",
    "Group Conformal (fixed)": "group_conformal_fixed",
    "Group Conformal (oracle)": "group_conformal_oracle",
    "Bonferroni": "bonferroni",
    "MaxRank": "maxrank",
    "CI": "ci",
    "Co-Optimized": "co_optimized",
    "Planning-only": "planning_only",
}
# experiment_log.csv column that records whether a method's set missed the outcome.
COVERAGE_COLUMN = {slug: f"violated_{slug}" for slug in METHOD_BY_SLUG if METHOD_BY_SLUG[slug].family == "robust"}


@dataclass(frozen=True)
class Parameter:
    key: str
    grid_key: str
    label: str
    unit: str
    description: str


# The swept optimization parameters, in the order used for setup ids.
PARAMETERS: tuple[Parameter, ...] = (
    Parameter("C", "C_budget", "Sectionalization budget", "share of circuits",
              "Largest share of the study circuits that can be sectionalized."),
    Parameter("B", "B_budget", "Fast-trip budget", "share of circuits",
              "Largest share of the study circuits that can carry fast-trip settings."),
    Parameter("W", "W_cap_fraction", "SAIFI cap", "interruptions per customer-year",
              "Cap on mitigation interruptions per customer from fast-trip and PSPS combined."),
    Parameter("E", "alpha_effective", "Fast-trip effectiveness", "share of ignition risk removed",
              "Share of a protected circuit's ignition exposure that fast-trip removes."),
    Parameter("G", "gamma", "Fast-trip SAIFI factor", "activations per circuit-year",
              "Reliability cost of fast-trip relative to one PSPS event on the same circuit."),
)
FIXED_PARAMETERS = ("delta", "alpha")  # PSPS SAIFI factor and the miscoverage level; constant in the grid.


def setup_id(values: dict[str, float]) -> str:
    """Stable, readable id such as ``C30-B20-W10-E70-G90`` (values in percent)."""

    return "-".join(f"{p.key}{round(float(values[p.key]) * 100):02d}" for p in PARAMETERS)


def exposure(p: np.ndarray, u: np.ndarray, y: np.ndarray, z: np.ndarray, effectiveness: float) -> float:
    """Population-weighted ignition exposure left after fast-trip and PSPS."""

    return float(np.sum(p * u * (1.0 - effectiveness * y) * (1.0 - z)))


@dataclass(frozen=True)
class Recourse:
    exposure: float
    z: np.ndarray  # realized PSPS decisions, aligned with the inputs
    capacity: float  # reliability budget left for PSPS after fast-trip, in SAIFI-weighted population
    candidates: int  # circuits that could usefully be shut off
    proven_optimal: bool  # False only if the solver stopped at its time limit


# The grid's own recourse solves used Gurobi's default 1e-4 relative gap. A zero
# gap is needlessly slow on knapsacks with many near-tied circuits, so the audit
# uses a gap 100 times tighter than the one it checks.
RECOURSE_MIP_GAP = 1e-6
RECOURSE_TIME_LIMIT_SEC = 20.0


def realized_recourse(
    p: np.ndarray,
    u: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    *,
    effectiveness: float,
    gamma: float,
    delta: float,
    w_cap: float,
) -> Recourse:
    """Optimal PSPS response to realized ignitions ``u`` for a fixed plan.

    This is ``solve_recourse_given_u`` from the study code, solved with HiGHS
    (relative gap ``RECOURSE_MIP_GAP``) instead of Gurobi. It is a 0/1 knapsack:
    shutting off circuit i removes
    ``p_i * u_i * (1 - e * y_i)`` exposure and uses ``delta * p_i`` of the
    reliability budget left after fast-trip. Only sectionalized circuits with a
    positive removable exposure are candidates; every other ``z_i`` is 0 in some
    optimal solution, so restricting to candidates does not change the optimum.

    Ties are common (many circuits share the same exposure per customer), so the
    returned ``z`` is one optimal shutoff set. The optimal exposure is unique;
    the number of circuits shut off, and in rare cases the budget used, can
    differ between optimal solutions, so compare exposure, not shutoff counts.
    """

    p = np.asarray(p, dtype=float)
    u = np.asarray(u, dtype=float)
    x = np.asarray(x, dtype=int)
    y = np.asarray(y, dtype=int)
    if not (len(p) == len(u) == len(x) == len(y)):
        raise ValueError("p, u, x, and y must have the same length")
    if np.any(y > x):
        raise ValueError("fast-trip requires sectionalization (y <= x)")

    removable = p * u * (1.0 - effectiveness * y)
    capacity = float(w_cap - np.dot(gamma * p, y))
    z = np.zeros(len(p), dtype=int)
    proven = True
    candidates = np.flatnonzero((x == 1) & (removable > 0) & (delta * p <= capacity + 1e-9))
    if len(candidates) and capacity > 0:
        weights = delta * p[candidates]
        if weights.sum() <= capacity + 1e-9:
            z[candidates] = 1
        else:
            result = milp(
                -removable[candidates],
                constraints=[LinearConstraint(weights[None, :], -np.inf, capacity)],
                integrality=np.ones(len(candidates)),
                bounds=Bounds(0, 1),
                options={"mip_rel_gap": RECOURSE_MIP_GAP, "time_limit": RECOURSE_TIME_LIMIT_SEC},
            )
            if result.x is None:
                raise RuntimeError(f"recourse MILP failed: {result.message}")
            z[candidates] = np.round(result.x).astype(int)
            proven = result.status == 0
    return Recourse(
        exposure=exposure(p, u, y, z, effectiveness),
        z=z,
        capacity=capacity,
        candidates=int(len(candidates)),
        proven_optimal=proven,
    )


def fill_sectionalization(x: np.ndarray, priority: np.ndarray, slots: int) -> np.ndarray:
    """Complete a plan's unused sectionalization budget, highest priority first.

    Sectionalization has no cost in the model beyond its budget and only adds
    PSPS options (``z <= x``), so completing the budget can never raise the
    realized exposure. The study's optimal plans are indifferent to circuits
    they do not plan to shut off and often leave budget unused. ``priority`` is
    information available at planning time (the predicted exposure
    ``p_i * mu_i``); ties keep the circuit order.
    """

    x = np.asarray(x, dtype=int).copy()
    missing = int(slots) - int(x.sum())
    if missing <= 0:
        return x
    order = np.argsort(-np.asarray(priority, dtype=float), kind="stable")
    free = order[x[order] == 0][:missing]
    x[free] = 1
    return x


# Decision flags packed into one hexadecimal character per circuit.
FLAG_SECTIONALIZED = 1
FLAG_FAST_TRIP = 2
FLAG_PSPS_PLANNED = 4
FLAG_PSPS_REALIZED = 8


def encode_decisions(x: Iterable[int], y: Iterable[int], z_planned: Iterable[int], z_realized: Iterable[int]) -> str:
    x, y, zp, zr = (np.asarray(list(v), dtype=int) for v in (x, y, z_planned, z_realized))
    flags = x * FLAG_SECTIONALIZED + y * FLAG_FAST_TRIP + zp * FLAG_PSPS_PLANNED + zr * FLAG_PSPS_REALIZED
    if flags.min(initial=0) < 0 or flags.max(initial=0) > 15:
        raise ValueError("decision vectors must be 0/1")
    return "".join(format(int(flag), "x") for flag in flags)


def decode_decisions(encoded: str) -> dict[str, np.ndarray]:
    flags = np.array([int(ch, 16) for ch in encoded], dtype=int)
    return {
        "x": (flags & FLAG_SECTIONALIZED > 0).astype(int),
        "y": (flags & FLAG_FAST_TRIP > 0).astype(int),
        "z_planned": (flags & FLAG_PSPS_PLANNED > 0).astype(int),
        "z_realized": (flags & FLAG_PSPS_REALIZED > 0).astype(int),
    }
