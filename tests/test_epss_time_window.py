"""EPSS date semantics against disposable PostgreSQL (AGGREGATE_TEST_DSN).

Geometry serialization is stubbed; these tests exercise actual SQL filtering,
joins, grouping and embedded outage rows, without any spatial predicates.
"""

import os
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient

from services.comparison.queries import epss_outage_count
from services.data_query import queries as dq
from services.visualization import queries as vq
from services.visualization.app import app, get_conn


@pytest.fixture
def epss_db():
    dsn = os.environ.get("AGGREGATE_TEST_DSN")
    if not dsn:
        pytest.skip("AGGREGATE_TEST_DSN must name a disposable PostgreSQL database")
    with psycopg.connect(dsn) as conn:
        try:
            # Refuse a database that already contains a warehouse.
            conn.execute("CREATE SCHEMA wildfire")
            conn.execute("SET LOCAL search_path TO wildfire, public")
            conn.execute("CREATE FUNCTION wildfire.st_asgeojson(text) RETURNS text LANGUAGE sql AS 'SELECT $1'")
            conn.execute("""
                CREATE TABLE wildfire.circuits (
                    circuit_id TEXT PRIMARY KEY, division TEXT, substation TEXT, geom TEXT);
                CREATE TABLE wildfire.epss_outages (
                    id INTEGER PRIMARY KEY, circuit_id TEXT, circuit TEXT, year INTEGER,
                    start_date DATE, end_date DATE, county TEXT, cause TEXT, outage_type TEXT,
                    division TEXT, customer_minutes BIGINT, restoration_min INTEGER,
                    medical_baseline INTEGER, life_support INTEGER, schools INTEGER,
                    hospitals INTEGER, geom TEXT);
                INSERT INTO wildfire.circuits VALUES ('043371102', 'North', 'Test', NULL);
                INSERT INTO wildfire.epss_outages
                    (id, circuit_id, circuit, year, start_date, end_date, county, cause)
                VALUES
                    (1, '043371102', 'Test', 2023, '2022-12-31', '2023-01-01', 'Butte', 'Unknown'),
                    (2, '043371102', 'Test', 2023, '2023-01-01', '2023-01-02', 'Butte', 'Unknown'),
                    (3, '043371102', 'Test', 2023, '2023-12-31', '2024-01-01', 'Butte', 'Unknown'),
                    (4, '043371102', 'Test', 2024, '2024-01-01', '2024-01-01', 'Butte', 'Unknown'),
                    (5, '043371102', 'Test', 2024, '2024-02-29', '2024-02-29', 'Butte', 'Unknown');
            """)
            yield conn
        finally:
            conn.rollback()


@pytest.mark.parametrize("year,start,end", [
    (2022, None, None), (2023, None, None), (2024, None, None),
    (2023, date(2023, 6, 1), None),
    (2023, date(2022, 1, 1), date(2024, 12, 31)),
    (2023, date(2024, 1, 1), date(2024, 12, 31)),
    (None, date(2022, 12, 31), date(2023, 1, 1)),
])
def test_every_epss_query_uses_the_same_start_date_population(epss_db, year, start, end):
    expected = epss_db.execute("""
        SELECT id FROM wildfire.epss_outages
        WHERE (%s::int IS NULL OR extract(year FROM start_date) = %s)
          AND (%s::date IS NULL OR start_date >= %s)
          AND (%s::date IS NULL OR start_date <= %s) ORDER BY id
    """, (year, year, start, start, end, end)).fetchall()
    expected_ids = [row[0] for row in expected]
    window = dict(year=year, start_date=start, end_date=end)
    rows, total, _ = dq.query_epss(
        epss_db, **window, circuit_id=None, utility=None, county=None,
        outage_type=None, cause=None, bbox=None, limit=100, offset=0,
    )
    assert [row["id"] for row in rows] == expected_ids
    assert total == len(expected_ids)
    if year == 2022:
        assert rows[0]["year"] == 2023  # Preserve the source attribute.

    sql, _, params = dq._rank_epss_sql(**window, county=None)
    assert sum(row[1] for row in epss_db.execute(sql, params).fetchall()) == total
    map_rows, _, _ = vq.map_epss_circuits(
        epss_db, **window, utility=None, county=None, outage_type=None,
        cause=None, bbox=None, limit=100, offset=0, include_outages=True,
    )
    assert sum(row["event_count"] for row in map_rows) == total
    assert [outage["id"] for row in map_rows for outage in row["outages"]] == expected_ids
    if year == 2022:
        assert map_rows[0]["years"] == [2022]
    assert len(vq.time_series_dates(
        epss_db, "epss", **window, utility=None, county=None, incident_type=None,
    )) == total
    assert [row["id"] for row in vq._epss_outages_for_circuit(
        epss_db, "043371102", **window,
    )] == expected_ids

    # The comparison API and measured coverage already count by start_date.
    if year is not None and start is None and end is None:
        count, reason = epss_outage_count(
            epss_db, scope="utility", scope_id="PGE",
            start=date(year, 1, 1), end=date(year, 12, 31),
        )
        assert count == total and reason is None


@pytest.mark.parametrize("interval", ["daily", "weekly", "monthly"])
def test_time_series_api_keeps_year_when_explicit_dates_are_also_present(epss_db, interval):
    app.dependency_overrides[get_conn] = lambda: epss_db
    client = TestClient(app)
    try:
        response = client.get("/time-series", params={
            "dataset": "epss", "year": 2023, "interval": interval,
            "start_date": "2022-01-01", "end_date": "2024-12-31",
        })
        assert response.status_code == 200
        assert response.json()["meta"]["total_events"] == 2
    finally:
        client.close()
        del app.dependency_overrides[get_conn]
