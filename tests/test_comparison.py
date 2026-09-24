"""Live tests for the comparison service (:8003)."""

from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("COMPARISON_BASE", "http://127.0.0.1:8003")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=60.0) as c:
        try:
            r = c.get("/health")
        except httpx.ConnectError as exc:
            pytest.skip(f"comparison service not running at {BASE}: {exc}")
        if r.status_code != 200:
            pytest.skip(f"comparison /health -> {r.status_code}")
        yield c


def test_health(client: httpx.Client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ignition_count" in body["metrics"]


def test_compare_utilities_ignitions_pge_sce_2024(client: httpx.Client):
    r = client.get(
        "/compare-utilities",
        params={
            "utilities": "PGE,SCE",
            "metric": "ignition_count",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "ignition_definition": "attribute",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["ignition_definition"] == "attribute"
    by_key = {row["key"]: row for row in body["results"]}
    assert by_key["PGE"]["value"] == 532
    # Issue #89: key stays the code; code and label come from the naming registry.
    assert (by_key["PGE"]["code"], by_key["PGE"]["label"]) == ("PGE", "PG&E")
    assert (by_key["SCE"]["code"], by_key["SCE"]["label"]) == ("SCE", "SCE")
    assert by_key["SCE"]["value"] is not None
    assert by_key["SCE"]["reason"] is None


def test_epss_null_for_sce(client: httpx.Client):
    r = client.get(
        "/compare-utilities",
        params={
            "utilities": "PGE,SCE",
            "metric": "epss_outage_count",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert r.status_code == 200
    by_key = {row["key"]: row for row in r.json()["results"]}
    assert by_key["PGE"]["value"] is not None and by_key["PGE"]["value"] > 0
    assert by_key["SCE"]["value"] is None
    assert "PG&E" in (by_key["SCE"]["reason"] or "") or "PGE" in (by_key["SCE"]["reason"] or "")


def test_epss_to_ignition_ratio_null_components(client: httpx.Client):
    r = client.get(
        "/compare-utilities",
        params={
            "utilities": "BVES",
            "metric": "epss_to_ignition_ratio",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert r.status_code == 200
    row = r.json()["results"][0]
    assert row["value"] is None
    assert row["reason"]


def test_compare_regions_hftd_spatial(client: httpx.Client):
    r = client.get(
        "/compare-regions",
        params={
            "region_type": "hftd",
            "regions": "Tier 2,Tier 3",
            "metric": "ignition_count",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["ignition_definition"] == "spatial"
    assert len(body["results"]) == 2
    assert all(row["value"] is not None for row in body["results"])


def test_compare_periods_delta(client: httpx.Client):
    r = client.get(
        "/compare-periods",
        params={
            "scope_type": "utility",
            "scope": "PGE",
            "metric": "ignition_count",
            "period_a_start": "2023-01-01",
            "period_a_end": "2023-12-31",
            "period_b_start": "2024-01-01",
            "period_b_end": "2024-12-31",
            "ignition_definition": "attribute",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["period_a"]["value"] is not None
    assert body["period_b"]["value"] == 532
    assert body["delta"]["value"] == body["period_b"]["value"] - body["period_a"]["value"]
