"""County, tier, and utility filters resolve to canonical values or fail with 400.

No database: the query functions are replaced with recorders, and the
connection dependency is overridden.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.data_query import app as dq
from services.data_query.filters import parse_county, parse_tier, parse_utility
from services.shared.counties import (
    CALIFORNIA_COUNTIES,
    UnknownCountyError,
    county_suggestions,
    normalize_county,
)


def test_there_are_58_canonical_counties():
    assert len(CALIFORNIA_COUNTIES) == 58
    assert len(set(CALIFORNIA_COUNTIES)) == 58


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Butte County", "Butte"),
        ("butte", "Butte"),
        ("BUTTE COUNTY", "Butte"),
        ("  Butte  county ", "Butte"),
        ("Butte Co.", "Butte"),
        ("Los Angeles County", "Los Angeles"),
        ("los angeles", "Los Angeles"),
        ("LA", "Los Angeles"),
        ("L.A.", "Los Angeles"),
        ("San Luis Obispo Co", "San Luis Obispo"),
        ("SLO", "San Luis Obispo"),
        ("el dorado county", "El Dorado"),
        ("San Bernadino", "San Bernardino"),
        ("Contra  Costa", "Contra Costa"),
        ("SAN LUIS OBISPO", "San Luis Obispo"),
    ],
)
def test_normalize_county_resolves_known_spellings(value, expected):
    assert normalize_county(value) == expected


@pytest.mark.parametrize(
    "value,suggestion",
    [
        ("Buttes", "Butte"),
        ("Buttte", "Butte"),
        ("Sacremento", "Sacramento"),
        ("Shasta, Tehama", "Shasta"),
        ("Orange County CA", "Orange"),
    ],
)
def test_normalize_county_rejects_unknown_values_with_close_matches(value, suggestion):
    with pytest.raises(UnknownCountyError) as info:
        normalize_county(value)
    assert suggestion in info.value.suggestions
    assert "Did you mean" in str(info.value)
    assert repr(value) in str(info.value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("SB", ["San Benito", "San Bernardino", "Santa Barbara"]),
        ("S.B.", ["San Benito", "San Bernardino", "Santa Barbara"]),
        ("SC", ["Santa Clara", "Santa Cruz"]),
    ],
)
def test_an_abbreviation_that_fits_several_counties_is_rejected_naming_all_of_them(value, expected):
    """No alias may resolve an ambiguous abbreviation; the error lists every candidate."""
    with pytest.raises(UnknownCountyError) as info:
        normalize_county(value)
    assert info.value.suggestions == expected
    for name in expected:
        assert name in str(info.value)


def test_no_alias_is_the_initials_of_more_than_one_county():
    from services.shared.counties import COUNTY_ALIASES

    def initials(name: str) -> str:
        return "".join(word[0] for word in name.lower().split())

    for alias in COUNTY_ALIASES:
        compact = alias.replace(" ", "")
        matches = [name for name in CALIFORNIA_COUNTIES if initials(name) == compact]
        assert len(matches) <= 1, (alias, matches)


def test_normalize_county_never_returns_a_non_canonical_name():
    for value in ("Xyz", "", "   ", "County"):
        with pytest.raises(UnknownCountyError):
            normalize_county(value)
    assert county_suggestions("Xyz") == []


def test_parse_county_maps_unknown_to_400_and_empty_to_none():
    assert parse_county(None) is None
    assert parse_county("") is None
    assert parse_county("Butte County") == "Butte"
    with pytest.raises(HTTPException) as info:
        parse_county("Buttes")
    assert info.value.status_code == 400
    assert "Butte" in info.value.detail


@pytest.mark.parametrize(
    "value,expected",
    [("Tier 2", "Tier 2"), ("tier 3", "Tier 3"), ("T2", "Tier 2"), ("2", "Tier 2"),
     ("HFTD Tier 3", "Tier 3"), ("tier2", "Tier 2"), ("Tier-3", "Tier 3")],
)
def test_parse_tier_resolves_spellings(value, expected):
    assert parse_tier(value) == expected


@pytest.mark.parametrize("value", ["Tier 4", "Tier", "two", "1"])
def test_parse_tier_rejects_unknown_tiers_with_the_same_suggestion_text(value):
    with pytest.raises(HTTPException) as info:
        parse_tier(value)
    assert info.value.status_code == 400
    assert info.value.detail.startswith(f"unknown tier {value!r}")
    assert "Did you mean Tier 2 or Tier 3?" in info.value.detail


@pytest.mark.parametrize(
    "value,expected",
    [
        ("PG&E", "PGE"), ("Pacific Gas and Electric", "PGE"),
        ("Pacific Gas & Electric Company", "PGE"), ("Southern California Edison", "SCE"),
        ("SDG&E", "SDGE"), ("San Diego Gas & Electric", "SDGE"),
        ("Bear Valley Electric Service", "BVES"), ("Liberty Utilities", "Liberty"),
        ("PacifiCorp", "PACIFICORP"), ("Pacific Power", "PACIFICORP"), ("liberty", "Liberty"),
    ],
)
def test_parse_utility_resolves_full_names(value, expected):
    assert parse_utility(value) == expected


def test_parse_utility_rejects_unknown_with_400_and_the_same_suggestion_text():
    with pytest.raises(HTTPException) as info:
        parse_utility("Edison International")
    assert info.value.status_code == 400
    assert info.value.detail.startswith("unknown utility 'Edison International'")
    assert "Did you mean" in info.value.detail and "PGE" in info.value.detail


# ---------------------------------------------------------------- endpoints


@pytest.fixture
def client(monkeypatch):
    dq.app.dependency_overrides[dq.get_conn] = lambda: object()
    try:
        yield TestClient(dq.app)
    finally:
        dq.app.dependency_overrides.clear()


def _recorder(seen: dict[str, Any], result: Any):
    def fake(conn, **kwargs):
        seen.update(kwargs)
        return result

    return fake


ENDPOINTS = [
    ("/calfire/incidents", "query_calfire", ([], 0, {}), {}),
    ("/ignitions", "query_ignitions", ([], 0), {}),
    ("/epss/outages", "query_epss", ([], 0, {}), {}),
    ("/rank", "query_rank", ([], {"total": 0}), {"dataset": "calfire_incidents", "group_by": "utility"}),
    ("/grouped-counts", "query_grouped_counts", {"ok": True}, {"dataset": "calfire_incidents", "group_by": "cause"}),
    ("/summary", "query_summary", {"ok": True}, {"dataset": "calfire_incidents"}),
    (
        "/regional-series",
        "query_regional_series",
        {"ok": True},
        {"interval": "monthly", "start_date": "2024-01-01", "end_date": "2024-12-31"},
    ),
]


@pytest.mark.parametrize("path,query_fn,result,extra", ENDPOINTS)
@pytest.mark.parametrize("value", ["Butte County", "butte", "Butte"])
def test_every_county_endpoint_queries_the_canonical_name(client, monkeypatch, path, query_fn, result, extra, value):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(dq.queries, query_fn, _recorder(seen, result))
    response = client.get(path, params={"county": value, **extra})
    assert response.status_code == 200, response.text
    assert seen["county"] == "Butte"


@pytest.mark.parametrize("path,query_fn,result,extra", ENDPOINTS)
def test_every_county_endpoint_rejects_an_unknown_county(client, monkeypatch, path, query_fn, result, extra):
    called = []
    monkeypatch.setattr(dq.queries, query_fn, lambda conn, **kwargs: called.append(kwargs) or result)
    response = client.get(path, params={"county": "Buttes", **extra})
    assert response.status_code == 400, response.text
    assert "Butte" in response.json()["detail"]
    assert called == [], "the query must not run for an unknown county"


def test_rank_rejects_an_unknown_tier_and_utility_before_querying(client, monkeypatch):
    called = []
    monkeypatch.setattr(dq.queries, "query_rank", lambda conn, **kwargs: called.append(kwargs) or ([], {"total": 0}))
    bad_utility = client.get("/rank", params={"dataset": "cpuc_ignitions", "group_by": "county", "utility": "Edison Intl"})
    assert bad_utility.status_code == 400
    bad_tier = client.get("/spatial/summary", params={"hftd_tier": "Tier 4", "start_date": "2024-01-01", "end_date": "2024-12-31"})
    assert bad_tier.status_code == 400
    assert called == []


def test_visualization_endpoints_normalize_and_reject_counties(monkeypatch):
    from services.visualization import app as viz

    viz.app.dependency_overrides[viz.get_conn] = lambda: object()
    try:
        client = TestClient(viz.app)
        seen: dict[str, Any] = {}

        def fake_dates(conn, ds, **kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(viz.queries, "time_series_dates", fake_dates)
        monkeypatch.setattr(viz.queries, "calfire_multi_county_count", lambda conn, **kwargs: 0)
        ok = client.get("/time-series", params={"dataset": "calfire", "interval": "monthly", "year": 2024, "county": "Butte County"})
        assert ok.status_code == 200, ok.text
        assert seen["county"] == "Butte"
        bad = client.get("/time-series", params={"dataset": "calfire", "interval": "monthly", "year": 2024, "county": "Buttes"})
        assert bad.status_code == 400 and "Butte" in bad.json()["detail"]
        bad_map = client.get("/map-layer", params={"dataset": "calfire", "year": 2024, "county": "Buttes"})
        assert bad_map.status_code == 400 and "Butte" in bad_map.json()["detail"]
    finally:
        viz.app.dependency_overrides.clear()


def test_comparison_regions_normalize_and_reject_counties(monkeypatch):
    from services.comparison import app as cmp

    cmp.app.dependency_overrides[cmp.get_conn] = lambda: object()
    try:
        client = TestClient(cmp.app)
        scopes: list[str] = []

        def fake_metric(conn, **kwargs):
            scopes.append(kwargs["scope_id"])
            return {"scope": {"id": kwargs["scope_id"]}, "value": 0}

        monkeypatch.setattr(cmp, "_metric_for_scope", fake_metric)
        monkeypatch.setattr(cmp, "_calfire_multi_county_meta", lambda conn, **kwargs: {})
        ok = client.get(
            "/compare-regions",
            params={"region_type": "county", "regions": "Butte County,shasta", "metric": "calfire_incident_count",
                    "start_date": "2020-01-01", "end_date": "2020-12-31"},
        )
        assert ok.status_code == 200, ok.text
        assert scopes == ["Butte", "Shasta"]
        bad = client.get(
            "/compare-regions",
            params={"region_type": "county", "regions": "Butte,Buttes", "metric": "calfire_incident_count",
                    "start_date": "2020-01-01", "end_date": "2020-12-31"},
        )
        assert bad.status_code == 400 and "Butte" in bad.json()["detail"]
        assert scopes == ["Butte", "Shasta"], "no metric ran for the rejected request"
    finally:
        cmp.app.dependency_overrides.clear()
