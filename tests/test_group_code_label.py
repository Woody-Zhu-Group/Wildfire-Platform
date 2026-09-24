"""Every /rank and /grouped-counts row carries a registry code and label (issue #89).

The existing ``key`` and ``group_value`` values do not change. These tests run
the real routes and query builders against a fake connection that returns the
rows the SQL would, so they need no database. The same routes run against a
disposable Postgres in ``tests/test_workspace_aggregates.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.data_query.app import app, get_conn
from services.shared.dataset_registry import (
    MISSING_LABEL_RANK,
    NOT_RECORDED,
    UTILITY_CODES,
    UTILITY_DISPLAY_LABELS,
    group_code_and_label,
)


class _Cursor:
    def __init__(self, results: list[list[dict[str, Any]]]):
        self._results = results
        self._current: list[dict[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._current = self._results.pop(0)

    def fetchone(self):
        return self._current[0]

    def fetchall(self):
        return list(self._current)


class _Conn:
    """Answers each execute with the next canned result set, in order."""

    def __init__(self, *results: list[dict[str, Any]]):
        self._results = list(results)

    def cursor(self, row_factory=None):
        return _Cursor(self._results)


@pytest.fixture
def client_for():
    clients = []

    def make(conn):
        app.dependency_overrides[get_conn] = lambda: conn
        client = TestClient(app)
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()
    app.dependency_overrides.pop(get_conn, None)


@pytest.mark.parametrize("code", UTILITY_CODES)
def test_every_registry_utility_code_and_its_label_give_the_same_fields(code):
    label = UTILITY_DISPLAY_LABELS.get(code, code)
    expected = {"code": code, "label": label}
    assert group_code_and_label("utility", code) == expected
    assert group_code_and_label("utility", label) == expected


@pytest.mark.parametrize("group_by", ["county", "circuit", "cause"])
def test_other_groupings_use_the_value_as_code_and_label(group_by):
    for value in ("Butte", "043371102", "Vegetation", "PGE", NOT_RECORDED):
        assert group_code_and_label(group_by, value) == {"code": value, "label": value}


def test_missing_and_unlisted_utility_values_are_their_own_code_and_label():
    for value in (NOT_RECORDED, MISSING_LABEL_RANK, "Some Muni"):
        assert group_code_and_label("utility", value) == {"code": value, "label": value}


def test_rank_by_utility_keeps_codes_as_keys_and_adds_code_and_label(client_for):
    groups = [
        {"group_value": "PGE", "metric_value": 30},
        {"group_value": "SCE", "metric_value": 20},
        {"group_value": "SDGE", "metric_value": 10},
        {"group_value": MISSING_LABEL_RANK, "metric_value": 5},
    ]
    client = client_for(_Conn([{"count": len(groups)}], groups))
    r = client.get(
        "/rank",
        params={"dataset": "cpuc_ignitions", "group_by": "utility", "year": 2024},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    expected = [
        ("PGE", "PGE", "PG&E"),
        ("SCE", "SCE", "SCE"),
        ("SDGE", "SDGE", "SDG&E"),
        (MISSING_LABEL_RANK, MISSING_LABEL_RANK, MISSING_LABEL_RANK),
    ]
    assert [(row["key"], row["code"], row["label"]) for row in body["results"]] == expected
    assert [(row["group_value"], row["code"], row["label"]) for row in body["data"]] == expected
    assert [row["value"] for row in body["results"]] == [30, 20, 10, 5]


def test_rank_by_circuit_uses_the_circuit_id_as_code_and_label(client_for):
    groups = [
        {"group_value": "043371102", "metric_value": 4, "circuit_name": "ALPHA 1102", "division": "North Bay"},
    ]
    client = client_for(_Conn([{"count": 1}], groups))
    r = client.get(
        "/rank",
        params={"dataset": "epss_outages", "group_by": "circuit", "year": 2024},
    )
    assert r.status_code == 200, r.text
    row = r.json()["data"][0]
    assert (row["group_value"], row["code"], row["label"]) == ("043371102",) * 3
    assert row["circuit_name"] == "ALPHA 1102"
    result = r.json()["results"][0]
    assert (result["key"], result["code"], result["label"]) == ("043371102",) * 3


def test_grouped_counts_by_utility_keeps_labels_as_keys_and_adds_code_and_label(client_for):
    grouped = [
        {"key": "PG&E", "value": 7},
        {"key": "SCE", "value": 3},
        {"key": "Liberty", "value": 1},
        {"key": NOT_RECORDED, "value": 2},
    ]
    client = client_for(_Conn(grouped))
    r = client.get(
        "/grouped-counts",
        params={"dataset": "cpuc_ignitions", "group_by": "utility"},
    )
    assert r.status_code == 200, r.text
    rows = {row["key"]: (row["code"], row["label"], row["value"]) for row in r.json()["rows"]}
    assert rows == {
        "PG&E": ("PGE", "PG&E", 7),
        "SCE": ("SCE", "SCE", 3),
        "SDG&E": ("SDGE", "SDG&E", 0),
        "Liberty": ("Liberty", "Liberty", 1),
        NOT_RECORDED: (NOT_RECORDED, NOT_RECORDED, 2),
    }


def test_grouped_counts_by_county_uses_the_county_as_code_and_label(client_for):
    client = client_for(_Conn([{"key": "Butte", "value": 2}, {"key": NOT_RECORDED, "value": 1}]))
    r = client.get(
        "/grouped-counts",
        params={"dataset": "cpuc_ignitions", "group_by": "county"},
    )
    assert r.status_code == 200, r.text
    assert [(row["key"], row["code"], row["label"]) for row in r.json()["rows"]] == [
        ("Butte", "Butte", "Butte"),
        (NOT_RECORDED, NOT_RECORDED, NOT_RECORDED),
    ]


def test_rank_and_grouped_counts_agree_on_code_and_label_for_the_same_utility(client_for):
    rank = client_for(_Conn([{"count": 1}], [{"group_value": "SDGE", "metric_value": 1}])).get(
        "/rank", params={"dataset": "cpuc_ignitions", "group_by": "utility"}
    ).json()["results"][0]
    grouped = client_for(_Conn([{"key": "SDG&E", "value": 1}])).get(
        "/grouped-counts",
        params={"dataset": "cpuc_ignitions", "group_by": "utility", "utility": "SDGE"},
    ).json()["rows"]
    row = next(row for row in grouped if row["code"] == "SDGE")
    assert rank["key"] != row["key"]
    assert (rank["code"], rank["label"]) == (row["code"], row["label"]) == ("SDGE", "SDG&E")


def test_compare_utilities_keeps_code_keys_and_adds_code_and_label(monkeypatch):
    from services.comparison import app as comparison

    def metric_for_scope(conn, *, scope_id, **kwargs):
        return {"key": scope_id, "value": 1, "raw_value": 1, "denominator": None, "reason": None}

    monkeypatch.setattr(comparison, "_metric_for_scope", metric_for_scope)
    comparison.app.dependency_overrides[comparison.get_conn] = lambda: None
    try:
        with TestClient(comparison.app) as client:
            r = client.get(
                "/compare-utilities",
                params={
                    "utilities": "PG&E,SCE,sdge",
                    "metric": "ignition_count",
                    "start_date": "2024-01-01",
                    "end_date": "2024-12-31",
                },
            )
    finally:
        comparison.app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert [(row["key"], row["code"], row["label"]) for row in r.json()["results"]] == [
        ("PGE", "PGE", "PG&E"),
        ("SCE", "SCE", "SCE"),
        ("SDGE", "SDGE", "SDG&E"),
    ]
