"""Single-dataset ranking endpoint."""

from __future__ import annotations

import httpx
import psycopg


def test_rank_calfire_counties_2023_matches_sql(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT TRIM(part) AS grp, COUNT(*)::bigint AS n
            FROM wildfire.calfire_incidents,
                 LATERAL unnest(string_to_array(
                     COALESCE(NULLIF(TRIM(county), ''), '(unknown)'), ',')) AS part
            WHERE (incident_type IS NULL OR incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat'))
              AND EXTRACT(YEAR FROM date_only_created) = 2023
            GROUP BY 1
            ORDER BY n DESC, grp ASC
            """
        )
        expected = list(cur.fetchall())
        cur.execute(
            """
            SELECT COUNT(*) FROM wildfire.calfire_incidents
            WHERE (incident_type IS NULL OR incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat'))
              AND EXTRACT(YEAR FROM date_only_created) = 2023
              AND county LIKE '%,%'
            """
        )
        multi = int(cur.fetchone()[0])
    assert expected, "warehouse has no 2023 CAL FIRE default rows"
    # A multi-county incident ranks in every county it lists, never as its own
    # "Monterey, San Luis Obispo" group.
    assert all("," not in row[0] for row in expected)

    r = data_client.get(
        "/rank",
        params={
            "dataset": "calfire_incidents",
            "group_by": "county",
            "metric": "count",
            "year": 2023,
            "limit": 10,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["total"] == len(expected)
    assert body["meta"]["returned"] == len(body["data"])
    assert body["meta"]["limit"] == 10
    assert body["kind"] == "ranking"
    assert body["metric"] == "calfire_incident_count"
    assert "top" not in (body["meta"].get("empty_reason") or "").lower()
    assert body["meta"]["multi_county_incidents"] == multi

    cutoff_value = expected[min(10, len(expected)) - 1][1]
    include = [row for row in expected if row[1] >= cutoff_value][:25]
    assert [row["group_value"] for row in body["data"]] == [row[0] for row in include]
    assert [row["metric_value"] for row in body["data"]] == [int(row[1]) for row in include]
    if len([row for row in expected if row[1] >= cutoff_value]) > 10:
        assert body["meta"]["tie_extended"] is True


def test_rank_rejects_us_ignitions_and_epss_utility(data_client: httpx.Client):
    us = data_client.get(
        "/rank",
        params={"dataset": "us_ignitions", "group_by": "state", "year": 2024},
    )
    assert us.status_code == 400
    assert "state" in us.json()["detail"].lower()

    epss = data_client.get(
        "/rank",
        params={"dataset": "epss_outages", "group_by": "utility", "year": 2024},
    )
    assert epss.status_code == 400
    assert "pge" in epss.json()["detail"].lower() or "utility" in epss.json()["detail"].lower()


def test_rank_empty_reason_not_zero_everywhere(data_client: httpx.Client):
    r = data_client.get(
        "/rank",
        params={
            "dataset": "calfire_incidents",
            "group_by": "county",
            "year": 1901,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    reason = body["meta"].get("empty_reason") or ""
    assert reason
    assert "0 incidents everywhere" not in reason.lower()
