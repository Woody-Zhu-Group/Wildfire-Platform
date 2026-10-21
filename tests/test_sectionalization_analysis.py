"""Sectionalization study module: recourse solver, decision encoding, geometry.

These tests need no data files, services, or solver license.
"""

from __future__ import annotations

from itertools import product

import numpy as np
import pandas as pd
import pytest

from analysis.sectionalization import analyze
from analysis.sectionalization.ingest import _rdp, simplify_wkt_lines
from analysis.sectionalization.model import (
    METHODS,
    PARAMETERS,
    decode_decisions,
    encode_decisions,
    exposure,
    fill_sectionalization,
    realized_recourse,
    setup_id,
)


def brute_force_recourse(p, u, x, y, effectiveness, gamma, delta, w_cap):
    best = np.inf
    for z in product([0, 1], repeat=len(p)):
        z = np.array(z)
        if np.any(z > x):
            continue
        if np.dot(gamma * p, y) + np.dot(delta * p, z) > w_cap + 1e-9:
            continue
        best = min(best, exposure(p, u, y, z, effectiveness))
    return best


@pytest.mark.parametrize("seed", range(25))
def test_recourse_matches_brute_force(seed):
    rng = np.random.default_rng(seed)
    n = 9
    p = rng.integers(100, 5000, n).astype(float)
    u = rng.integers(0, 4, n).astype(float)
    x = rng.integers(0, 2, n)
    y = x * rng.integers(0, 2, n)
    effectiveness, gamma, delta = 0.8, 0.9, 1.0
    w_cap = float(rng.uniform(0.1, 0.6)) * p.sum()
    if np.dot(gamma * p, y) > w_cap:
        y[:] = 0
    result = realized_recourse(p, u, x, y, effectiveness=effectiveness, gamma=gamma, delta=delta, w_cap=w_cap)
    assert result.proven_optimal
    assert result.exposure == pytest.approx(brute_force_recourse(p, u, x, y, effectiveness, gamma, delta, w_cap), rel=1e-6)
    assert np.all(result.z <= x)
    assert np.dot(gamma * p, y) + np.dot(delta * p, result.z) <= w_cap + 1e-6


def test_recourse_rejects_fast_trip_without_sectionalization():
    with pytest.raises(ValueError, match="y <= x"):
        realized_recourse(np.ones(2), np.ones(2), np.array([0, 1]), np.array([1, 1]),
                          effectiveness=0.5, gamma=0.9, delta=1.0, w_cap=10.0)


def test_recourse_with_no_capacity_shuts_nothing_off():
    p, u = np.array([10.0, 20.0]), np.array([1.0, 2.0])
    result = realized_recourse(p, u, np.array([1, 1]), np.array([1, 1]), effectiveness=0.5, gamma=1.0, delta=1.0, w_cap=30.0)
    assert result.z.sum() == 0
    assert result.exposure == pytest.approx(exposure(p, u, np.array([1, 1]), np.zeros(2), 0.5))


def test_fill_completes_the_budget_by_priority_and_never_removes():
    x = np.array([1, 0, 0, 0, 1, 0])
    priority = np.array([0.0, 5.0, 1.0, 9.0, 0.0, 5.0])
    filled = fill_sectionalization(x, priority, slots=4)
    assert filled.tolist() == [1, 1, 0, 1, 1, 0]  # 9.0 first, then the first of the tied 5.0s
    assert np.all(filled >= x)
    assert fill_sectionalization(x, priority, slots=2).tolist() == x.tolist()


@pytest.mark.parametrize("seed", range(10))
def test_filling_never_raises_realized_exposure(seed):
    rng = np.random.default_rng(100 + seed)
    n = 10
    p = rng.integers(100, 5000, n).astype(float)
    u = rng.integers(0, 3, n).astype(float)
    x = (rng.random(n) < 0.3).astype(int)
    y = x * (rng.random(n) < 0.3).astype(int)
    w_cap = 0.4 * p.sum()
    kwargs = dict(effectiveness=0.8, gamma=0.9, delta=1.0, w_cap=w_cap)
    before = realized_recourse(p, u, x, y, **kwargs).exposure
    after = realized_recourse(p, u, fill_sectionalization(x, p * rng.random(n), slots=8), y, **kwargs).exposure
    assert after <= before + 1e-9


def test_decision_flags_round_trip():
    x = np.array([1, 1, 0, 1, 0])
    y = np.array([1, 0, 0, 0, 0])
    zp = np.array([0, 1, 0, 1, 0])
    zr = np.array([1, 0, 0, 1, 0])
    code = encode_decisions(x, y, zp, zr)
    assert code == "b50d0"  # 1 sectionalized + 2 fast-trip + 4 planned PSPS + 8 realized PSPS
    decoded = decode_decisions(code)
    for key, value in {"x": x, "y": y, "z_planned": zp, "z_realized": zr}.items():
        assert decoded[key].tolist() == value.tolist()


def test_setup_id_is_stable_and_ordered():
    values = {"C": 0.3, "B": 0.2, "W": 0.1, "E": 0.7, "G": 0.95}
    assert setup_id(values) == "C30-B20-W10-E70-G95"
    assert [p.key for p in PARAMETERS] == ["C", "B", "W", "E", "G"]


def test_methods_have_unique_slugs_and_one_proposed_reference():
    slugs = [m.slug for m in METHODS]
    assert len(slugs) == len(set(slugs)) == 8
    assert analyze.PROPOSED in slugs
    assert {m.family for m in METHODS} == {"robust", "deterministic", "planning"}


def test_rdp_keeps_endpoints_and_drops_collinear_points():
    line = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.001], [4.0, 0.0]])
    simple = _rdp(line, tolerance=0.01)
    assert simple[0].tolist() == [0.0, 0.0] and simple[-1].tolist() == [4.0, 0.0]
    assert len(simple) == 2


def test_compact_circuit_keeps_its_largest_fragment():
    # Every fragment is shorter than the tolerance; the circuit must still be drawable.
    wkt = "MULTILINESTRING ((-120.0 39.0, -120.0001 39.0001), (-120.0 39.0, -120.0003 39.0002))"
    parts = simplify_wkt_lines(wkt, tolerance=0.004)
    assert len(parts) == 1
    assert parts[0] == [-120.0, 39.0, -120.0003, 39.0002]


def test_linestring_and_multilinestring_parse():
    assert simplify_wkt_lines("LINESTRING (-121 38, -121.1 38.2)", 0.004) == [[-121.0, 38.0, -121.1, 38.2]]
    parts = simplify_wkt_lines("MULTILINESTRING ((-121 38, -121.1 38.2), (-122 37, -122.2 37.1))", 0.004)
    assert len(parts) == 2


def test_gain_is_share_of_the_other_methods_exposure_removed():
    table = pd.DataFrame({"a": [50.0, 100.0], "b": [100.0, 100.0]})
    assert analyze.gain(table, "a", "b").tolist() == [0.5, 0.0]


def test_describe_ignores_missing_values():
    summary = analyze.describe(pd.Series([1.0, np.nan, 3.0]))
    assert summary["n"] == 2 and summary["mean"] == 2.0 and summary["min"] == 1.0
