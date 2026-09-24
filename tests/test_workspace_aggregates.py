"""Aggregation regression tests against an explicitly configured disposable Postgres.

Run with AGGREGATE_TEST_DSN. Each test rolls back its fixture schema; it never
uses the project's warehouse settings or loads source datasets.
"""

from __future__ import annotations

import os
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient

from services.data_query.app import app, get_conn


@pytest.mark.parametrize("path", ["/grouped-counts", "/summary", "/regional-series"])
def test_each_workspace_aggregate_route_is_registered_once(path):
    routes = [route for route in app.routes if getattr(route, "path", None) == path]
    assert len(routes) == 1, f"Duplicate {path} handlers would hide the merged implementation"


@pytest.fixture
def aggregate_db():
    dsn = os.environ.get("AGGREGATE_TEST_DSN")
    if not dsn:
        pytest.skip("AGGREGATE_TEST_DSN must name a disposable PostgreSQL database")
    with psycopg.connect(dsn) as conn:
        try:
            # Fail if this schema already exists, rather than touching a warehouse.
            conn.execute("CREATE SCHEMA wildfire")
            conn.execute("""
                CREATE TABLE wildfire.cpuc_ignitions (
                    id BIGSERIAL PRIMARY KEY, event_date DATE NOT NULL,
                    year SMALLINT NOT NULL DEFAULT 2024, utility TEXT NOT NULL, county TEXT);
                CREATE TABLE wildfire.epss_outages (
                    id BIGSERIAL PRIMARY KEY, start_date DATE NOT NULL,
                    circuit_id TEXT NOT NULL, county TEXT, cause TEXT, division TEXT);
                CREATE TABLE wildfire.calfire_incidents (
                    incident_id TEXT PRIMARY KEY, date_only_created DATE,
                    incident_type TEXT, acres_burned DOUBLE PRECISION, county TEXT, utility TEXT);
                CREATE TABLE wildfire.psps_events (
                    event_name TEXT PRIMARY KEY, deenergization_start_date DATE,
                    customers_deenergized INTEGER, utility TEXT NOT NULL);
                CREATE TABLE wildfire.us_ignitions (id BIGSERIAL PRIMARY KEY, event_date DATE NOT NULL);
            """)
            yield conn
        finally:
            conn.rollback()


@pytest.fixture
def aggregate_client(aggregate_db):
    app.dependency_overrides[get_conn] = lambda: aggregate_db
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        del app.dependency_overrides[get_conn]


SCOPE = {"start_date": "2024-01-01", "end_date": "2024-12-31"}


def read(client, path, **params):
    response = client.get(path, params={**SCOPE, **params})
    assert response.status_code == 200, response.text
    return response.json()


def metric_values(body):
    return {item["id"]: (item["value"], item["missing"]) for item in body["metrics"]}


def insert_outages(conn, records):
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO wildfire.epss_outages (start_date, circuit_id, county, cause, division) VALUES (%s,%s,%s,%s,%s)", records,
        )


def test_groups_return_every_county_beyond_the_rank_limit(aggregate_db, aggregate_client):
    with aggregate_db.cursor() as cur:
        cur.executemany("INSERT INTO wildfire.cpuc_ignitions (event_date,utility,county) VALUES ('2024-06-01','PGE',%s)", [(f"County {n}",) for n in range(60)])
    body = read(aggregate_client, "/grouped-counts", dataset="cpuc_ignitions", group_by="county")
    assert body["total"] == 60
    assert len(body["rows"]) == 60
    assert {row["key"] for row in body["rows"]} == {f"County {n}" for n in range(60)}
    assert sum(row["value"] for row in body["rows"]) == 60
    ranked = read(aggregate_client, "/rank", dataset="cpuc_ignitions", group_by="county", limit=25)
    assert len(ranked["results"]) == 25


def test_cause_groups_and_unavailable_epss_utilities(aggregate_db, aggregate_client):
    insert_outages(aggregate_db, [("2024-01-01", "043371102", "Marin", cause, "North Bay") for cause in ["Unknown", None, "Weather", "Unknown"]])
    causes = read(aggregate_client, "/grouped-counts", dataset="epss_outages", group_by="cause")
    assert {row["key"]: row["value"] for row in causes["rows"]} == {"Unknown": 2, "Not recorded": 1, "Weather": 1}
    utilities = read(aggregate_client, "/grouped-counts", dataset="epss_outages", group_by="utility")
    assert {row["key"]: row["value"] for row in utilities["rows"]} == {"PG&E": 4, "SCE": None, "SDG&E": None}


def test_filters_apply_before_counting_and_preserve_attribute_definition(aggregate_db, aggregate_client):
    with aggregate_db.cursor() as cur:
        cur.executemany("INSERT INTO wildfire.cpuc_ignitions (event_date,utility,county) VALUES (%s,%s,%s)", [
            ("2023-12-31", "PGE", "Marin"), ("2024-01-01", "PGE", "Marin"),
            ("2024-12-31", "PGE", "Sonoma"), ("2024-01-01", "SCE", "Marin"),
        ])
    body = read(aggregate_client, "/summary", dataset="cpuc_ignitions", utility="PG&E", county="marin")
    assert metric_values(body) == {"events": (1, 0), "counties": (1, 0), "utilities": (1, 0)}
    # PR #76: an unknown county is a 400 with close matches, never 0 rows and never a widened filter.
    injected = aggregate_client.get("/summary", params={**SCOPE, "dataset": "cpuc_ignitions", "county": "Marin' OR TRUE --"})
    assert injected.status_code == 400
    assert "unknown county" in injected.json()["detail"] and "Did you mean Marin?" in injected.json()["detail"]
    groups = read(aggregate_client, "/grouped-counts", dataset="cpuc_ignitions", group_by="county", county="Marin")
    assert groups["rows"] == [{"key": "Marin", "code": "Marin", "label": "Marin", "value": 2}]


def test_calfire_default_types_missing_acres_and_distinct_split_counties(aggregate_db, aggregate_client):
    with aggregate_db.cursor() as cur:
        cur.executemany("INSERT INTO wildfire.calfire_incidents VALUES (%s,'2024-06-01',%s,%s,%s,%s)", [
            ("a", "Wildfire", 0, "Los Angeles, Ventura", "SCE"),
            ("b", "Fire", 7, "Los Angeles", None), ("c", "Fire", None, None, None),
            ("d", "Flood", 100, "Marin", None), ("e", None, 200, "Marin", None),
        ])
    body = read(aggregate_client, "/summary", dataset="calfire_incidents")
    assert metric_values(body) == {"events": (3, 0), "acres": (7, 1), "counties": (2, 1)}
    groups = read(aggregate_client, "/grouped-counts", dataset="calfire_incidents", group_by="county")
    # PR #81: a multi-county incident counts in every county it lists, so the rows
    # sum above the incident total and the response says how many such incidents there are.
    assert {row["key"]: row["value"] for row in groups["rows"]} == {"Los Angeles": 2, "Ventura": 1, "Not recorded": 1}
    assert groups["total"] == 3
    assert groups["multi_county_incidents"] == 1
    assert "every county it lists" in groups["note"]


def test_empty_populations_stay_zero_and_all_missing_fields_stay_null(aggregate_db, aggregate_client):
    aggregate_db.execute("INSERT INTO wildfire.calfire_incidents VALUES ('a','2024-01-01','Fire',NULL,NULL,NULL)")
    assert metric_values(read(aggregate_client, "/summary", dataset="calfire_incidents")) == {"events": (1, 0), "acres": (None, 1), "counties": (None, 1)}
    empty = read(aggregate_client, "/summary", dataset="calfire_incidents", start_date="1901-01-01", end_date="1901-12-31")
    assert all(value == (0, 0) for value in metric_values(empty).values())


def test_psps_totals_sum_customer_events_and_count_distinct_utilities(aggregate_db, aggregate_client):
    aggregate_db.execute("INSERT INTO wildfire.psps_events VALUES ('a','2024-01-01',50,'PGE'),('b','2024-01-02',50,'PGE'),('c','2024-01-03',NULL,'SCE')")
    assert metric_values(read(aggregate_client, "/summary", dataset="psps_events")) == {"events": (3, 0), "customers": (100, 1), "utilities": (2, 0)}


def test_epss_event_and_distinct_circuit_counts(aggregate_db, aggregate_client):
    insert_outages(aggregate_db, [("2024-01-01", circuit, county, None, None) for circuit, county in [("043371102", "Marin"), ("043371102", "Marin"), ("012041102", None)]])
    assert metric_values(read(aggregate_client, "/summary", dataset="epss_outages")) == {"events": (3, 0), "circuits": (2, 0), "counties": (1, 1)}


def test_national_summary_is_sample_record_count_only(aggregate_db, aggregate_client):
    aggregate_db.execute("INSERT INTO wildfire.us_ignitions (event_date) VALUES ('2024-01-01'),('2023-01-01')")
    assert metric_values(read(aggregate_client, "/summary", dataset="us_ignitions")) == {"events": (1, 0)}


def test_upstream_summary_keeps_optional_dates(aggregate_db, aggregate_client):
    aggregate_db.execute("INSERT INTO wildfire.us_ignitions (event_date) VALUES ('2024-01-01'),('2023-01-01')")
    response = aggregate_client.get("/summary", params={"dataset": "us_ignitions"})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == aggregate_db.execute("SELECT COUNT(*) FROM wildfire.us_ignitions").fetchone()[0] == 2


def test_upstream_missing_group_attribute_is_not_recorded(aggregate_db, aggregate_client):
    aggregate_db.execute("INSERT INTO wildfire.cpuc_ignitions (event_date,utility) VALUES ('2024-01-01','PGE'),('2024-02-01','SCE')")
    total = aggregate_db.execute("SELECT COUNT(*) FROM wildfire.cpuc_ignitions").fetchone()[0]
    # PR #3 groups a dataset without a cause column under an explicit missing label.
    body = read(aggregate_client, "/grouped-counts", dataset="cpuc_ignitions", group_by="cause")
    assert body == {"rows": [{"key": "Not recorded", "code": "Not recorded", "label": "Not recorded", "value": total}], "total": total}


def test_upstream_non_pge_epss_filter_matches_the_existing_record_api(aggregate_db, aggregate_client):
    insert_outages(aggregate_db, [("2024-01-01", "043371102", "Marin", "Unknown", "North Bay")])
    assert aggregate_db.execute("SELECT COUNT(*) FROM wildfire.epss_outages").fetchone()[0] == 1
    # Upstream follows the record API's empty-population convention here. The
    # browser separately blocks this unsupported scope before requesting data.
    body = read(aggregate_client, "/summary", dataset="epss_outages", utility="SCE")
    assert metric_values(body) == {"events": (0, 0), "circuits": (0, 0), "counties": (0, 0)}
    groups = read(aggregate_client, "/grouped-counts", dataset="epss_outages", utility="SCE", group_by="utility")
    assert groups == {"rows": [{"key": "SCE", "code": "SCE", "label": "SCE", "value": None}], "total": 0}
    assert read(aggregate_client, "/regional-series", interval="monthly", utility="SCE") == {"series": [], "total": 0}


def test_rank_and_grouped_counts_add_registry_code_and_label_without_changing_keys(aggregate_db, aggregate_client):
    # Issue #89: /rank keys stay codes and /grouped-counts keys stay labels; both gain code and label.
    with aggregate_db.cursor() as cur:
        cur.executemany(
            "INSERT INTO wildfire.cpuc_ignitions (event_date,utility,county) VALUES ('2024-06-01',%s,%s)",
            [("PGE", "Butte")] * 3 + [("SDGE", "San Diego")] * 2 + [("SCE", "Kern")],
        )
    ranked = read(aggregate_client, "/rank", dataset="cpuc_ignitions", group_by="utility")
    expected = [("PGE", "PGE", "PG&E", 3), ("SDGE", "SDGE", "SDG&E", 2), ("SCE", "SCE", "SCE", 1)]
    assert [(r["key"], r["code"], r["label"], r["value"]) for r in ranked["results"]] == expected
    assert [(r["group_value"], r["code"], r["label"], r["metric_value"]) for r in ranked["data"]] == expected
    grouped = read(aggregate_client, "/grouped-counts", dataset="cpuc_ignitions", group_by="utility")
    assert [(r["key"], r["code"], r["label"], r["value"]) for r in grouped["rows"]] == [
        ("PG&E", "PGE", "PG&E", 3), ("SDG&E", "SDGE", "SDG&E", 2), ("SCE", "SCE", "SCE", 1)]
    counties = read(aggregate_client, "/rank", dataset="cpuc_ignitions", group_by="county")
    assert [(r["key"], r["code"], r["label"]) for r in counties["results"]] == [(c, c, c) for c in ("Butte", "San Diego", "Kern")]
    grouped_counties = read(aggregate_client, "/grouped-counts", dataset="cpuc_ignitions", group_by="county")
    assert all(r["key"] == r["code"] == r["label"] for r in grouped_counties["rows"])


def test_regional_counts_fill_empty_periods_and_keep_unknown_divisions(aggregate_db, aggregate_client):
    insert_outages(aggregate_db, [(day, "043371102", "Marin", None, division) for day, division in [
        ("2024-01-01", "Sierra"), ("2024-01-01", "Sierra"), ("2024-03-31", "Sierra"), ("2024-03-31", None), ("2023-12-31", "Sierra"),
    ]])
    body = read(aggregate_client, "/regional-series", end_date="2024-03-31", interval="monthly")
    series = {row["name"]: row for row in body["series"]}
    assert body["total"] == 4
    assert [row["count"] for row in series["Sierra"]["buckets"]] == [2, 0, 1]
    assert series["Not recorded"]["total"] == 1
    cross = read(aggregate_client, "/regional-series", start_date="2023-12-31", end_date="2024-01-02", interval="weekly")
    assert cross["series"][0]["buckets"] == [{"start": "2023-12-31", "end": "2023-12-31", "count": 1}, {"start": "2024-01-01", "end": "2024-01-02", "count": 2}]


@pytest.mark.parametrize("interval", ["daily", "weekly", "monthly", "quarterly"])
def test_regional_bins_conserve_events_and_clip_leap_year_boundaries(aggregate_db, aggregate_client, interval):
    dates = ["2024-02-29", "2024-03-01", "2024-12-29", "2024-12-30", "2024-12-31", "2025-01-01", "2025-01-02"]
    insert_outages(aggregate_db, [(day, "043371102", "Marin", None, "North Bay") for day in dates])
    body = read(aggregate_client, "/regional-series", start_date=dates[0], end_date=dates[-1], interval=interval)
    buckets = body["series"][0]["buckets"]
    assert sum(row["count"] for row in buckets) == len(dates) == body["total"]
    assert buckets[0]["start"] == dates[0] and buckets[-1]["end"] == dates[-1]
    for previous, current in zip(buckets, buckets[1:]):
        assert (date.fromisoformat(current["start"]) - date.fromisoformat(previous["end"])).days == 1
    if interval == "weekly":
        assert buckets[-2:] == [{"start": "2024-12-30", "end": "2024-12-31", "count": 2}, {"start": "2025-01-01", "end": "2025-01-02", "count": 2}]


@pytest.mark.parametrize("path,params", [
    ("/summary", {"dataset": "us_ignitions", "county": "Marin"}),
    ("/summary", {"dataset": "psps_events", "county": "Marin"}),
    ("/summary", {"dataset": "unknown"}),
    ("/summary", {"dataset": "cpuc_ignitions", "end_date": "2023-01-01"}),
    ("/grouped-counts", {"dataset": "epss_outages", "group_by": "cause; DROP SCHEMA wildfire"}),
    ("/regional-series", {"interval": "invalid"}),
])
def test_unsupported_aggregate_requests_fail_clearly(aggregate_client, path, params):
    response = aggregate_client.get(path, params={**SCOPE, **params})
    assert response.status_code == 400, response.text
