"""CAL FIRE county filters include incidents that list several counties (#78).

``calfire_incidents.county`` can hold "Shasta, Tehama". A county filter, a
county ranking, a county grouping, and a county comparison all count such an
incident in every county it lists, and every county-scoped result reports how
many of its incidents list more than one county.

The warehouse tests call the query functions directly (no running service)
and check them against independent SQL. The endpoint tests replace the
connection and the query functions.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from services.comparison import queries as cq
from services.data_query import queries as dq
from services.shared.calfire_county import MULTI_COUNTY_NOTE, county_match_sql
from services.visualization import queries as vq

YEAR_2020 = (date(2020, 1, 1), date(2020, 12, 31))

# Independent of services.shared.calfire_county: plain string_to_array + TRIM.
SPLIT_COUNT_SQL = """
    SELECT COUNT(*) FROM wildfire.calfire_incidents c
    WHERE (c.incident_type IS NULL OR c.incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat'))
      AND EXTRACT(YEAR FROM c.date_only_created) = %s
      AND EXISTS (
        SELECT 1 FROM unnest(string_to_array(c.county, ',')) AS part
        WHERE lower(TRIM(part)) = lower(%s)
      )
"""


def _scalar(conn, sql: str, params: tuple = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


def _records(conn, county: str | None, year: int) -> tuple[list[dict], int, dict]:
    return dq.query_calfire(
        conn,
        utility=None,
        include_untagged=False,
        county=county,
        year=year,
        start_date=None,
        end_date=None,
        min_acres=None,
        incident_type=None,
        limit=100,
        offset=0,
    )


# ---------------------------------------------------------------- warehouse


def test_shasta_2020_includes_the_two_shasta_tehama_incidents(db_conn):
    rows, total, extra = _records(db_conn, "Shasta", 2020)
    assert total == 6
    assert extra["multi_county_incidents"] == 2
    assert extra["multi_county_rule"] == "counted_in_each_listed_county"
    assert sorted(row["county"] for row in rows).count("Shasta, Tehama") == 2


def test_every_county_year_with_a_multi_county_incident_matches_split_sql(db_conn):
    """The filter equals an independent split-list count for every affected county-year."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT TRIM(part), EXTRACT(YEAR FROM date_only_created)::int
            FROM wildfire.calfire_incidents,
                 LATERAL unnest(string_to_array(county, ',')) AS part
            WHERE county LIKE '%,%' AND (incident_type IS NULL OR incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat'))
            """
        )
        pairs = [(name, year) for name, year in cur.fetchall() if name != "Mexico"]
    assert len(pairs) > 50
    for county, year in pairs:
        _rows, total, extra = _records(db_conn, county, year)
        assert total == _scalar(db_conn, SPLIT_COUNT_SQL, (year, county)), (county, year)
        assert extra["multi_county_incidents"] >= 1, (county, year)


def test_statewide_count_is_unchanged_and_carries_no_multi_county_meta(db_conn):
    _rows, total, extra = _records(db_conn, None, 2020)
    expected = _scalar(
        db_conn,
        "SELECT COUNT(*) FROM wildfire.calfire_incidents WHERE (incident_type IS NULL OR incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat')) "
        "AND EXTRACT(YEAR FROM date_only_created) = 2020",
    )
    assert total == expected
    assert "multi_county_incidents" not in extra


def test_rank_counts_a_multi_county_incident_in_each_listed_county(db_conn):
    rows, meta = dq.query_rank(
        db_conn,
        dataset="calfire_incidents",
        group_by="county",
        metric="count",
        utility=None,
        include_untagged=False,
        county=None,
        year=2020,
        start_date=None,
        end_date=None,
        incident_type=None,
        limit=25,
    )
    assert all("," not in row["group_value"] for row in rows)
    assert meta["multi_county_incidents"] == 13
    by_county = {row["group_value"]: row["metric_value"] for row in rows}
    for county, value in by_county.items():
        if county != "(unknown)":
            assert value == _scalar(db_conn, SPLIT_COUNT_SQL, (2020, county)), county


def test_grouped_counts_total_is_incidents_and_rows_can_exceed_it(db_conn):
    grouped = dq.query_grouped_counts(
        db_conn,
        dataset="calfire_incidents",
        group_by="county",
        utility=None,
        county=None,
        start_date=YEAR_2020[0],
        end_date=YEAR_2020[1],
    )
    _rows, statewide, _extra = _records(db_conn, None, 2020)
    assert grouped["total"] == statewide
    assert sum(row["value"] for row in grouped["rows"]) > grouped["total"]
    assert all("," not in row["key"] for row in grouped["rows"])
    assert grouped["multi_county_incidents"] == 13
    assert grouped["note"] == MULTI_COUNTY_NOTE


def test_grouped_counts_with_a_county_filter_keep_only_that_county(db_conn):
    grouped = dq.query_grouped_counts(
        db_conn,
        dataset="calfire_incidents",
        group_by="county",
        utility=None,
        county="Tehama",
        start_date=YEAR_2020[0],
        end_date=YEAR_2020[1],
    )
    assert grouped["rows"] == [{"key": "Tehama", "code": "Tehama", "label": "Tehama", "value": 8}]
    assert grouped["total"] == 8
    assert grouped["multi_county_incidents"] == 4


def test_summary_with_a_county_filter_reports_multi_county(db_conn):
    summary = dq.query_summary(
        db_conn,
        dataset="calfire_incidents",
        utility=None,
        county="Shasta",
        start_date=YEAR_2020[0],
        end_date=YEAR_2020[1],
    )
    assert summary["total"] == 6
    assert summary["multi_county_incidents"] == 2


def test_cpuc_grouped_counts_are_untouched(db_conn):
    grouped = dq.query_grouped_counts(
        db_conn,
        dataset="cpuc_ignitions",
        group_by="county",
        utility=None,
        county=None,
        start_date=YEAR_2020[0],
        end_date=YEAR_2020[1],
    )
    assert "multi_county_incidents" not in grouped
    assert sum(row["value"] for row in grouped["rows"]) == grouped["total"]


def test_visualization_map_and_time_series_match_the_data_query(db_conn):
    _rows, total = vq.map_calfire(
        db_conn,
        utility=None,
        county="Shasta",
        year=2020,
        start_date=None,
        end_date=None,
        min_acres=None,
        incident_type=None,
        bbox=None,
        limit=100,
        offset=0,
    )
    assert total == 6
    dates = vq.time_series_dates(
        db_conn,
        "calfire",
        utility=None,
        year=None,
        start_date=YEAR_2020[0],
        end_date=YEAR_2020[1],
        county="Shasta",
        incident_type=None,
    )
    assert len(dates) == 6
    assert (
        vq.calfire_multi_county_count(
            db_conn,
            utility=None,
            county="Shasta",
            year=2020,
            start_date=None,
            end_date=None,
            incident_type=None,
        )
        == 2
    )


def test_comparison_county_metrics_include_multi_county_incidents(db_conn):
    count, reason = cq.calfire_incident_count(
        db_conn, scope="county", scope_id="Shasta", start=YEAR_2020[0], end=YEAR_2020[1], definition="attribute"
    )
    assert (count, reason) == (6, None)
    acres, _ = cq.acres_burned(
        db_conn, scope="county", scope_id="Shasta", start=YEAR_2020[0], end=YEAR_2020[1], definition="attribute"
    )
    expected_acres = _scalar(
        db_conn,
        """
        SELECT COALESCE(SUM(acres_burned), 0)::bigint FROM wildfire.calfire_incidents
        WHERE (incident_type IS NULL OR incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat'))
          AND EXTRACT(YEAR FROM date_only_created) = 2020
          AND 'Shasta' = ANY(string_to_array(replace(county, ', ', ','), ','))
        """,
    )
    assert round(acres) == expected_acres
    # Zogg and Point list both Shasta and Tehama; they are counted once here.
    assert cq.calfire_multi_county_count(db_conn, counties=["Shasta", "Tehama"], ranges=[YEAR_2020]) == 4


# ---------------------------------------------------------------- no database


def test_county_match_sql_binds_one_parameter():
    fragment = county_match_sql("c.county")
    assert fragment.count("%s") == 1
    assert "lower(%s) = ANY(" in fragment


def _comparison_client(monkeypatch, seen: dict[str, Any]):
    from services.comparison import app as cmp

    cmp.app.dependency_overrides[cmp.get_conn] = lambda: object()
    monkeypatch.setattr(cmp, "_metric_for_scope", lambda conn, **kw: {"scope": {"id": kw["scope_id"]}, "value": 1})

    def fake_count(conn, *, counties, ranges):
        seen["counties"] = counties
        seen["ranges"] = ranges
        return 3

    monkeypatch.setattr(cmp.queries, "calfire_multi_county_count", fake_count)

    def fake_missing(conn, *, scope, scope_id, start, end, definition):
        seen.setdefault("untyped_calls", []).append((scope, scope_id, start, end))
        return {"untyped_incidents_counted": 2, "untagged_incidents_counted": 1}

    monkeypatch.setattr(cmp.queries, "calfire_missing_counts", fake_missing)
    return cmp, TestClient(cmp.app)


def test_compare_regions_reports_multi_county_for_calfire_county_scopes(monkeypatch):
    seen: dict[str, Any] = {}
    cmp, client = _comparison_client(monkeypatch, seen)
    try:
        params = {"region_type": "county", "regions": "Shasta,Tehama", "start_date": "2020-01-01", "end_date": "2020-12-31"}
        body = client.get("/compare-regions", params={**params, "metric": "calfire_incident_count"}).json()
        assert body["meta"]["multi_county_incidents"] == 3
        assert MULTI_COUNTY_NOTE in body["meta"]["notes"]
        assert seen["counties"] == ["Shasta", "Tehama"]
        # Untyped incidents counted: 2 per county, summed over both counties.
        assert body["meta"]["untyped_incidents_counted"] == 4
        assert body["meta"]["untagged_incidents_counted"] == 2
        assert [call[1] for call in seen["untyped_calls"]] == ["Shasta", "Tehama"]
        seen.clear()
        body = client.get("/compare-regions", params={**params, "metric": "ignition_count"}).json()
        assert "multi_county_incidents" not in body["meta"]
        assert "untyped_incidents_counted" not in body["meta"]
        assert seen == {}
    finally:
        cmp.app.dependency_overrides.clear()


def test_compare_periods_reports_multi_county_across_both_periods(monkeypatch):
    seen: dict[str, Any] = {}
    cmp, client = _comparison_client(monkeypatch, seen)
    try:
        body = client.get(
            "/compare-periods",
            params={
                "scope_type": "county", "scope": "Shasta County", "metric": "acres_burned",
                "period_a_start": "2020-01-01", "period_a_end": "2020-12-31",
                "period_b_start": "2021-01-01", "period_b_end": "2021-12-31",
            },
        ).json()
        assert body["meta"]["multi_county_incidents"] == 3
        assert seen["counties"] == ["Shasta"]
        assert seen["ranges"] == [YEAR_2020, (date(2021, 1, 1), date(2021, 12, 31))]
        # Both periods count, so both periods' untyped incidents are reported.
        assert body["meta"]["untyped_incidents_counted"] == 4
        assert [call[2:] for call in seen["untyped_calls"]] == [
            YEAR_2020, (date(2021, 1, 1), date(2021, 12, 31))
        ]
    finally:
        cmp.app.dependency_overrides.clear()
