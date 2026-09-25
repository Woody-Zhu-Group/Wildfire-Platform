"""SQL-grounded tests for workspace aggregation endpoints."""

from __future__ import annotations

from datetime import date

import httpx
import psycopg

from services.visualization.aggregations import interval_bin_meta


def _assert_grouped_contract(body: dict) -> None:
    assert isinstance(body.get("total"), int) and body["total"] >= 0
    assert isinstance(body.get("rows"), list)
    keys = [row["key"] for row in body["rows"]]
    assert all(isinstance(key, str) for key in keys)
    assert len(keys) == len(set(keys))
    for row in body["rows"]:
        assert row["value"] is None or (isinstance(row["value"], int) and row["value"] >= 0)
    assert sum(row["value"] or 0 for row in body["rows"]) == body["total"]


def _assert_summary_contract(body: dict, metric_ids: tuple[str, ...]) -> None:
    assert isinstance(body.get("total"), int) and body["total"] >= 0
    ids = [metric["id"] for metric in body["metrics"]]
    assert ids == list(metric_ids)
    events = next(metric for metric in body["metrics"] if metric["id"] == "events")
    assert events["value"] == body["total"]
    assert events["missing"] == 0
    for metric in body["metrics"]:
        assert 0 <= metric["missing"] <= body["total"]


def test_interval_bins_cover_requested_range():
    start, end = date(2024, 1, 1), date(2024, 12, 31)
    for interval in ("daily", "weekly", "monthly", "quarterly"):
        buckets = interval_bin_meta(start, end, interval)
        assert buckets[0]["start"] == "2024-01-01"
        assert buckets[-1]["end"] == "2024-12-31"
        assert all(bucket["count"] == 0 for bucket in buckets)
    quarterly = interval_bin_meta(start, end, "quarterly")
    assert [bucket["start"] for bucket in quarterly] == [
        "2024-01-01",
        "2024-04-01",
        "2024-07-01",
        "2024-10-01",
    ]
    assert [bucket["end"] for bucket in quarterly] == [
        "2024-03-31",
        "2024-06-30",
        "2024-09-30",
        "2024-12-31",
    ]
    weekly = interval_bin_meta(date(2024, 1, 3), date(2024, 1, 10), "weekly")
    assert weekly[0]["start"] == "2024-01-03"
    assert weekly[-1]["end"] == "2024-01-10"


def test_grouped_counts_cpuc_county_2024_matches_sql(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(NULLIF(BTRIM(county), ''), 'Not recorded') AS key,
                   COUNT(*)::bigint AS value
            FROM wildfire.cpuc_ignitions
            WHERE event_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
            GROUP BY 1
            """
        )
        expected = {row[0]: int(row[1]) for row in cur.fetchall()}
        total = sum(expected.values())
    r = data_client.get(
        "/grouped-counts",
        params={
            "dataset": "cpuc_ignitions",
            "group_by": "county",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_grouped_contract(body)
    assert body["total"] == total
    assert {row["key"]: row["value"] for row in body["rows"]} == expected
    assert "Not recorded" in expected or all(
        row["key"] != "Not recorded" for row in body["rows"]
    )


def test_grouped_counts_epss_utility_uses_null_not_zero(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)::bigint
            FROM wildfire.epss_outages
            WHERE start_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
            """
        )
        total = int(cur.fetchone()[0])
    r = data_client.get(
        "/grouped-counts",
        params={
            "dataset": "epss_outages",
            "group_by": "utility",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_grouped_contract(body)
    by_key = {row["key"]: row["value"] for row in body["rows"]}
    assert body["total"] == total == 2787
    assert by_key["PG&E"] == total
    assert by_key["SCE"] is None
    assert by_key["SDG&E"] is None


def test_grouped_counts_epss_cause_matches_sql(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(NULLIF(BTRIM(cause), ''), 'Not recorded') AS key,
                   COUNT(*)::bigint AS value
            FROM wildfire.epss_outages
            WHERE start_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
            GROUP BY 1
            """
        )
        expected = {row[0]: int(row[1]) for row in cur.fetchall()}
    r = data_client.get(
        "/grouped-counts",
        params={
            "dataset": "epss_outages",
            "group_by": "cause",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_grouped_contract(body)
    assert {row["key"]: row["value"] for row in body["rows"]} == expected


def test_summary_cpuc_matches_sql(data_client: httpx.Client, db_conn: psycopg.Connection):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)::bigint AS total,
                   COUNT(*) FILTER (
                     WHERE county IS NULL OR BTRIM(county) = ''
                   ) AS counties_missing,
                   COUNT(DISTINCT NULLIF(BTRIM(county), '')) AS counties_value,
                   COUNT(*) FILTER (
                     WHERE utility IS NULL OR BTRIM(utility) = ''
                   ) AS utilities_missing,
                   COUNT(DISTINCT NULLIF(BTRIM(utility), '')) AS utilities_value
            FROM wildfire.cpuc_ignitions
            WHERE event_date BETWEEN DATE '2020-01-01' AND DATE '2024-12-31'
            """
        )
        expected = cur.fetchone()
    r = data_client.get(
        "/summary",
        params={
            "dataset": "cpuc_ignitions",
            "start_date": "2020-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_summary_contract(body, ("events", "counties", "utilities"))
    by_id = {metric["id"]: metric for metric in body["metrics"]}
    assert body["total"] == int(expected[0])
    assert by_id["counties"]["missing"] == int(expected[1])
    assert by_id["counties"]["value"] == int(expected[2])
    assert by_id["utilities"]["missing"] == int(expected[3])
    assert by_id["utilities"]["value"] == int(expected[4])


def test_summary_calfire_acres_and_metrics(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            WITH filtered AS (
              SELECT * FROM wildfire.calfire_incidents
              WHERE (incident_type IS NULL OR incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat'))
                AND date_only_created BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
            )
            SELECT COUNT(*)::bigint AS total,
                   SUM(acres_burned) AS acres,
                   COUNT(*) FILTER (WHERE acres_burned IS NULL) AS acres_missing,
                   COUNT(*) FILTER (
                     WHERE county IS NULL OR BTRIM(county) = ''
                   ) AS counties_missing,
                   (SELECT COUNT(DISTINCT BTRIM(part))
                      FROM filtered f,
                           LATERAL unnest(string_to_array(f.county, ',')) AS part
                     WHERE f.county IS NOT NULL
                       AND BTRIM(f.county) <> ''
                       AND BTRIM(part) <> '') AS counties_value
            FROM filtered
            """
        )
        expected = cur.fetchone()
    r = data_client.get(
        "/summary",
        params={
            "dataset": "calfire_incidents",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_summary_contract(body, ("events", "acres", "counties"))
    by_id = {metric["id"]: metric for metric in body["metrics"]}
    assert body["total"] == int(expected[0]) == by_id["events"]["value"]
    acres = expected[1]
    if acres is None:
        assert by_id["acres"]["value"] is None
    else:
        assert abs(float(by_id["acres"]["value"]) - float(acres)) < 1e-6
    assert by_id["acres"]["missing"] == int(expected[2])
    assert by_id["counties"]["missing"] == int(expected[3])
    assert by_id["counties"]["value"] == int(expected[4])


def test_summary_us_ignitions_events_only(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*)::bigint FROM wildfire.us_ignitions")
        total = int(cur.fetchone()[0])
    r = data_client.get("/summary", params={"dataset": "us_ignitions"})
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_summary_contract(body, ("events",))
    assert body["total"] == total
    assert len(body["metrics"]) == 1


def test_summary_rejects_us_ignitions_county_filter(data_client: httpx.Client):
    r = data_client.get(
        "/summary",
        params={"dataset": "us_ignitions", "county": "Marin"},
    )
    assert r.status_code == 400


def test_regional_series_epss_2024_monthly_matches_sql(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(NULLIF(BTRIM(division), ''), 'Not recorded') AS name,
                   COUNT(*)::bigint AS n
            FROM wildfire.epss_outages
            WHERE start_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
            GROUP BY 1
            """
        )
        expected = {row[0]: int(row[1]) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT COUNT(*)::bigint FROM wildfire.epss_outages
            WHERE start_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
            """
        )
        total = int(cur.fetchone()[0])
    r = data_client.get(
        "/regional-series",
        params={
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "interval": "monthly",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == total == 2787
    names = [region["name"] for region in body["series"]]
    assert len(names) == len(set(names))
    assert {region["name"]: region["total"] for region in body["series"]} == expected
    for region in body["series"]:
        assert region["buckets"]
        assert region["buckets"][0]["start"] == "2024-01-01"
        assert region["buckets"][-1]["end"] == "2024-12-31"
        assert len(region["buckets"]) == 12
        assert sum(bucket["count"] for bucket in region["buckets"]) == region["total"]
    assert sum(region["total"] for region in body["series"]) == body["total"]


def test_regional_series_quarterly_marin_covers_range(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)::bigint FROM wildfire.epss_outages
            WHERE start_date BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
              AND lower(county) = lower('Marin')
            """
        )
        total = int(cur.fetchone()[0])
    r = data_client.get(
        "/regional-series",
        params={
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "interval": "quarterly",
            "county": "Marin",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == total
    for region in body["series"]:
        assert region["buckets"][0]["start"] == "2024-01-01"
        assert region["buckets"][-1]["end"] == "2024-12-31"
        assert len(region["buckets"]) == 4


def test_grouped_counts_rejects_bad_group_by(data_client: httpx.Client):
    r = data_client.get(
        "/grouped-counts",
        params={"dataset": "cpuc_ignitions", "group_by": "circuit"},
    )
    assert r.status_code == 400
