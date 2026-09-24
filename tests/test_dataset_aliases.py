"""Endpoints that take a dataset accept every registry alias.

County and utility filters already accepted their aliases; the data_query
aggregate and rank endpoints took only canonical keys, and the visualization
parser missed "cal fire", "wildfire_incidents" and a few others that
``to_canonical`` accepts. Unknown names still fail with each endpoint's own
message. No DB: the query functions are replaced.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.data_query import app as dq
from services.data_query.filters import parse_dataset
from services.shared.dataset_registry import (
    ALIASES,
    ALLOWED_RANK_PAIRS,
    DATASETS,
    GROUPED_DATASETS,
    parse_viz_dataset,
)

RANK_DATASETS = sorted({dataset for dataset, _, _ in ALLOWED_RANK_PAIRS})


def test_every_dataset_key_resolves_to_itself():
    # A canonical key that were another dataset's alias would change what an
    # existing caller gets.
    for key in DATASETS:
        assert parse_dataset(key) == key


def test_parse_dataset_folds_aliases_and_passes_unknown_names_through():
    assert parse_dataset("cal fire") == "calfire_incidents"
    assert parse_dataset(" EPSS ") == "epss_outages"
    assert parse_dataset("US ignitions") == "us_ignitions"
    assert parse_dataset("Wildfire_Incidents") == "calfire_incidents"
    assert parse_dataset(" Nope ") == "nope"


@pytest.fixture
def client():
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


@pytest.mark.parametrize(
    "alias", sorted(a for a, key in ALIASES.items() if key in RANK_DATASETS)
)
def test_rank_accepts_every_alias(client, monkeypatch, alias):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(dq.queries, "query_rank", _recorder(seen, ([], {"total": 0})))
    response = client.get("/rank", params={"dataset": alias, "group_by": "county"})
    assert response.status_code == 200, response.text
    assert seen["dataset"] == ALIASES[alias]
    assert response.json()["meta"]["filters"]["dataset"] == ALIASES[alias]


def test_rank_calfire_alias_echoes_the_default_incident_types(client, monkeypatch):
    monkeypatch.setattr(dq.queries, "query_rank", _recorder({}, ([], {"total": 0})))
    response = client.get("/rank", params={"dataset": "cal fire", "group_by": "county"})
    assert response.json()["meta"]["filters"]["incident_type"] == "Wildfire,Fire"


@pytest.mark.parametrize("alias", ["us ignitions", "national_ignitions"])
def test_rank_us_ignitions_aliases_get_the_us_ignitions_refusal(client, alias):
    response = client.get("/rank", params={"dataset": alias, "group_by": "county"})
    assert response.status_code == 400
    assert "us_ignitions has no state attribute" in response.json()["detail"]


def test_rank_epss_alias_gets_the_epss_utility_refusal(client):
    response = client.get("/rank", params={"dataset": "epss", "group_by": "utility"})
    assert response.status_code == 400
    assert "rows only for PG&E" in response.json()["detail"]


@pytest.mark.parametrize("path, query", [
    ("/grouped-counts", "query_grouped_counts"),
    ("/summary", "query_summary"),
])
@pytest.mark.parametrize(
    "alias", sorted(a for a, key in ALIASES.items() if key in GROUPED_DATASETS)
)
def test_grouped_counts_and_summary_accept_every_alias(client, monkeypatch, path, query, alias):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(dq.queries, query, _recorder(seen, {}))
    params = {"dataset": alias}
    if path == "/grouped-counts":
        params["group_by"] = "county"
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    assert seen["dataset"] == ALIASES[alias]


def test_unknown_dataset_keeps_the_query_layer_message(client):
    # The real query functions reject it before touching the connection.
    response = client.get("/grouped-counts", params={"dataset": "Wildfires", "group_by": "county"})
    assert response.status_code == 400
    assert "wildfires" in response.json()["detail"]


def test_visualization_parser_accepts_every_alias_with_a_viz_key():
    for alias, key in ALIASES.items():
        viz_key = DATASETS[key].viz_key
        if viz_key:
            assert parse_viz_dataset(alias) == viz_key, alias
    assert parse_viz_dataset("cal fire") == "calfire"
    assert parse_viz_dataset("wildfire_incidents") == "calfire"
    assert parse_viz_dataset("hftd_tiers") == "hftd"
    with pytest.raises(ValueError):
        parse_viz_dataset("iou_territories")
    with pytest.raises(ValueError):
        parse_viz_dataset("wildfires")


def test_visualization_endpoint_accepts_cal_fire(monkeypatch):
    from services.visualization import app as viz

    viz.app.dependency_overrides[viz.get_conn] = lambda: object()
    try:
        client = TestClient(viz.app)
        called: list[str] = []

        def fake_calfire(conn, **kwargs):
            called.append("calfire")
            return [], 0

        monkeypatch.setattr(viz.queries, "map_calfire", fake_calfire)
        response = client.get("/map-layer", params={"dataset": "cal fire", "year": 2024})
        assert response.status_code == 200, response.text
        assert called == ["calfire"]
        assert client.get("/map-layer", params={"dataset": "wildfires"}).status_code == 400
    finally:
        viz.app.dependency_overrides.clear()
