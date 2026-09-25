"""The CAL FIRE default counts every incident except the non-wildfire types.

Decision of 2026-09-24 (docs/DATA_CHANGE_CALFIRE_DEFAULT.md): the default used
to be ``incident_type IN ('Wildfire', 'Fire')``, which dropped the 1,234
incidents with no type recorded. It is now every incident except the stored
types in ``CALFIRE_NON_WILDFIRE_INCIDENT_TYPES`` (Earthquake, Flood, Hazmat),
and every CAL FIRE result reports how many of the incidents it counted have no
type (``untyped_incidents_counted``).

The warehouse tests call each service's query functions directly and assert
the values measured on 2026-09-24 as well as independent SQL. The expected
default SQL is spelled out here on purpose rather than imported, so a registry
change that drops NULL rows (``NOT IN`` alone does) fails these tests.
"""

from __future__ import annotations

from datetime import date

import pytest

from services.comparison import queries as cq
from services.data_query import queries as dq
from services.shared import dataset_registry as reg
from services.visualization import queries as vq

INDEPENDENT_DEFAULT = (
    "(c.incident_type IS NULL OR c.incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat'))"
)

# Default counts by created year after the change (the table in
# docs/DATA_CHANGE_CALFIRE_DEFAULT.md), and incidents with no type in each.
AFTER_BY_YEAR = {
    2009: (1, 0), 2013: (141, 141), 2014: (76, 75), 2015: (99, 97), 2016: (155, 155),
    2017: (429, 418), 2018: (302, 274), 2019: (263, 56), 2020: (259, 2), 2021: (186, 14),
    2022: (150, 0), 2023: (133, 0), 2024: (611, 0), 2025: (555, 0), 2026: (381, 0),
}


def _records(conn, *, year=None, county=None, utility=None, incident_type=None):
    return dq.query_calfire(
        conn,
        utility=utility,
        include_untagged=False,
        county=county,
        year=year,
        start_date=None,
        end_date=None,
        min_acres=None,
        incident_type=incident_type,
        limit=1,
        offset=0,
    )


def _scalar(conn, sql: str, params: tuple = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


# ---------------------------------------------------------------- registry


def test_registry_default_sql_keeps_untyped_rows():
    sql = reg.calfire_default_type_sql("c.incident_type")
    assert sql == INDEPENDENT_DEFAULT
    assert reg.CALFIRE_NON_WILDFIRE_INCIDENT_TYPES == ("Earthquake", "Flood", "Hazmat")
    assert reg.calfire_counted_by_default(None) is True
    assert reg.calfire_counted_by_default("Wildfire") is True
    assert reg.calfire_counted_by_default("Fire") is True
    assert reg.calfire_counted_by_default("Flood") is False


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, (INDEPENDENT_DEFAULT, [], "default_wildfire")),
        ("all", ("TRUE", [], "all")),
        ("untyped", ("c.incident_type IS NULL", [], "untyped")),
        ("Wildfire,Fire", ("c.incident_type = ANY(%s)", [["Wildfire", "Fire"]], "explicit")),
    ],
)
def test_registry_filter_fragment(value, expected):
    assert reg.calfire_incident_type_filter("c.incident_type", value) == expected


# ---------------------------------------------------------------- warehouse


def test_statewide_default_is_every_incident_but_the_four_non_wildfire_rows(db_conn):
    _rows, total, extra = dq.query_calfire(
        db_conn, utility=None, include_untagged=False, county=None, year=None,
        start_date=None, end_date=None, min_acres=None, incident_type=None, limit=1, offset=0,
    )
    table = _scalar(db_conn, "SELECT count(*) FROM wildfire.calfire_incidents")
    assert total == 3743 == table - 4
    assert extra["untyped_incidents_counted"] == 1234
    assert extra["incident_type_mode"] == "default_wildfire"


@pytest.mark.parametrize("year", sorted(AFTER_BY_YEAR))
def test_default_count_by_year(db_conn, year):
    expected_total, expected_untyped = AFTER_BY_YEAR[year]
    _rows, total, extra = _records(db_conn, year=year)
    assert (total, extra["untyped_incidents_counted"]) == (expected_total, expected_untyped)
    assert total == _scalar(
        db_conn,
        f"SELECT count(*) FROM wildfire.calfire_incidents c WHERE {INDEPENDENT_DEFAULT} "
        "AND EXTRACT(YEAR FROM c.date_only_created) = %s",
        (year,),
    )


def test_old_default_is_still_reachable_as_an_explicit_type_list(db_conn):
    # 2017: 11 Wildfire/Fire incidents under the old default, 429 now.
    _rows, total, extra = _records(db_conn, year=2017, incident_type="Wildfire,Fire")
    assert total == 11
    assert extra["untyped_incidents_counted"] == 0
    assert extra["incident_type_mode"] == "explicit"


def test_untyped_and_all_modes_are_unchanged(db_conn):
    _rows, untyped_total, extra = _records(db_conn, incident_type="untyped")
    assert untyped_total == 1234 == extra["untyped_incidents_counted"]
    _rows, all_total, extra = _records(db_conn, incident_type="all")
    assert all_total == 3747
    assert extra["untyped_incidents_counted"] == 1234


def test_the_2024_earthquake_is_excluded_from_humboldt(db_conn):
    _rows, default_total, _ = _records(db_conn, year=2024, county="Humboldt")
    _rows, all_total, _ = _records(db_conn, year=2024, county="Humboldt", incident_type="all")
    assert (default_total, all_total) == (8, 9)


def test_county_year_that_gains_untyped_incidents(db_conn):
    # Butte 2018: 1 Wildfire/Fire incident before, 12 now (11 untyped).
    _rows, total, extra = _records(db_conn, year=2018, county="Butte")
    assert total == 12
    assert extra["untyped_incidents_counted"] == 11


def test_utility_attribute_count_all_years(db_conn):
    _rows, total, extra = _records(db_conn, utility="PGE")
    assert total == 2254
    assert extra["untyped_incidents_counted"] == 2254 - 1482
    # A utility tag filter counts only tagged incidents.
    assert extra["untagged_incidents_counted"] == 0


def test_untagged_incidents_counted_when_the_result_includes_them(db_conn):
    _rows, total, extra = _records(db_conn, utility="untagged")
    assert total == 281 == extra["untagged_incidents_counted"]
    _rows, total, extra = dq.query_calfire(
        db_conn, utility="SCE", include_untagged=True, county=None, year=2020,
        start_date=None, end_date=None, min_acres=None, incident_type=None, limit=1, offset=0,
    )
    assert total == 79
    assert extra["untagged_incidents_counted"] == 26
    _rows, _total, extra = _records(db_conn)
    assert extra["untagged_incidents_counted"] == 281


def test_rank_reports_untyped_counted(db_conn):
    rows, meta = dq.query_rank(
        db_conn, dataset="calfire_incidents", group_by="county", metric="count",
        utility=None, include_untagged=False, county=None, year=2017,
        start_date=None, end_date=None, incident_type=None, limit=25,
    )
    assert meta["untyped_incidents_counted"] == 418
    assert rows, "2017 has CAL FIRE incidents"


def test_grouped_counts_and_summary_report_untyped_counted(db_conn):
    start, end = date(2017, 1, 1), date(2017, 12, 31)
    grouped = dq.query_grouped_counts(
        db_conn, dataset="calfire_incidents", group_by="utility",
        utility=None, county=None, start_date=start, end_date=end,
    )
    assert grouped["total"] == 429
    assert grouped["untyped_incidents_counted"] == 418
    summary = dq.query_summary(
        db_conn, dataset="calfire_incidents", utility=None, county=None,
        start_date=start, end_date=end,
    )
    assert summary["total"] == 429
    assert summary["untyped_incidents_counted"] == 418
    acres = next(m for m in summary["metrics"] if m["id"] == "acres")
    assert round(acres["value"]) == 1449419


def test_spatial_summary_counts_untyped_incidents_in_the_territory(db_conn):
    out = dq.spatial_summary(
        db_conn, utility="PGE", hftd_tier=None,
        start_date=date(2017, 1, 1), end_date=date(2017, 12, 31),
    )
    assert out["counts"]["calfire_incidents"] == 257
    assert out["meta"]["untyped_incidents_counted"] == 249
    # Every CAL FIRE incident inside PG&E territory in 2017 carries a tag.
    assert out["meta"]["untagged_incidents_counted"] == 0


def test_visualization_map_and_time_series_filters(db_conn):
    _rows, total = vq.map_calfire(
        db_conn, utility=None, county=None, year=2017, start_date=None, end_date=None,
        min_acres=None, incident_type=None, bbox=None, limit=1, offset=0,
    )
    assert total == 429
    assert vq.calfire_missing_counts(
        db_conn, utility=None, county=None, year=2017, start_date=None, end_date=None,
        incident_type=None,
    ) == {"untyped_incidents_counted": 418, "untagged_incidents_counted": 31}
    # The two undated untyped rows count in an undated map, not a time series.
    undated = vq.calfire_missing_counts(
        db_conn, utility=None, county=None, year=None, start_date=None, end_date=None,
        incident_type=None,
    )
    assert undated == {"untyped_incidents_counted": 1234, "untagged_incidents_counted": 281}
    dated = vq.calfire_missing_counts(
        db_conn, dated_only=True, utility=None, county=None, year=None, start_date=None,
        end_date=None, incident_type=None,
    )
    assert dated["untyped_incidents_counted"] == 1232


def test_comparison_counts_and_untyped(db_conn):
    period = {"start": date(2017, 1, 1), "end": date(2017, 12, 31)}
    attribute = cq.calfire_incident_count(
        db_conn, scope="utility", scope_id="PGE", definition="attribute", **period
    )
    assert attribute == (257, None)
    # An attribute utility scope counts only incidents tagged with it.
    assert cq.calfire_missing_counts(
        db_conn, scope="utility", scope_id="PGE", definition="attribute", **period
    ) == {"untyped_incidents_counted": 257 - 8, "untagged_incidents_counted": 0}
    spatial = cq.calfire_incident_count(
        db_conn, scope="utility", scope_id="PGE", definition="spatial", **period
    )
    assert spatial == (257, None)
    assert cq.calfire_missing_counts(
        db_conn, scope="utility", scope_id="PGE", definition="spatial", **period
    ) == {"untyped_incidents_counted": 249, "untagged_incidents_counted": 0}

    acres, _ = cq.acres_burned(
        db_conn, scope="county", scope_id="Butte", definition="attribute",
        start=date(2018, 1, 1), end=date(2018, 12, 31),
    )
    assert round(acres) == _scalar(
        db_conn,
        f"SELECT COALESCE(SUM(acres_burned), 0)::bigint FROM wildfire.calfire_incidents c "
        f"WHERE {INDEPENDENT_DEFAULT} AND EXTRACT(YEAR FROM date_only_created) = 2018 "
        "AND 'Butte' = ANY(string_to_array(replace(county, ', ', ','), ','))",
    )


# ------------------------------------------------ plain utility filter


PLAIN_FILTER_CASES = [
    # (utility, year, counted by the tag, untagged in the same year)
    ("PGE", 2017, 257, 31),
    ("SCE", 2017, 96, 31),
    ("PGE", 2020, 162, 26),
    ("SCE", 2020, 53, 26),
]


def _sql_plain_filter(conn, utility: str, year: int, county: str | None = None) -> tuple[int, int]:
    county_sql = (
        " AND lower(%s) = ANY(string_to_array(lower(replace(c.county, ', ', ',')), ','))"
        if county
        else ""
    )
    extra = (county,) if county else ()
    counted = _scalar(
        conn,
        f"SELECT count(*) FROM wildfire.calfire_incidents c WHERE {INDEPENDENT_DEFAULT} "
        f"AND c.utility = %s AND EXTRACT(YEAR FROM c.date_only_created) = %s{county_sql}",
        (utility, year, *extra),
    )
    untagged = _scalar(
        conn,
        f"SELECT count(*) FROM wildfire.calfire_incidents c WHERE {INDEPENDENT_DEFAULT} "
        f"AND c.utility IS NULL AND EXTRACT(YEAR FROM c.date_only_created) = %s{county_sql}",
        (year, *extra),
    )
    return counted, untagged


@pytest.mark.parametrize("utility,year,counted,untagged", PLAIN_FILTER_CASES)
def test_plain_utility_filter_reports_untagged_incidents_left_out(
    db_conn, utility, year, counted, untagged
):
    assert _sql_plain_filter(db_conn, utility, year) == (counted, untagged)

    _rows, total, extra = _records(db_conn, utility=utility, year=year)
    assert total == counted
    assert extra["untagged_incidents_counted"] == 0
    assert extra["untagged_incidents_excluded"] == untagged

    _rows, meta = dq.query_rank(
        db_conn, dataset="calfire_incidents", group_by="county", metric="count",
        utility=utility, include_untagged=False, county=None, year=year,
        start_date=None, end_date=None, incident_type=None, limit=25,
    )
    assert meta["untagged_incidents_excluded"] == untagged

    filters = dict(utility=utility, county=None, year=year, start_date=None, end_date=None,
                   incident_type=None)
    _rows, map_total = vq.map_calfire(db_conn, min_acres=None, bbox=None, limit=1, offset=0, **filters)
    assert map_total == counted
    assert vq.calfire_untagged_excluded(db_conn, **filters) == {"untagged_incidents_excluded": untagged}
    assert vq.calfire_untagged_excluded(db_conn, dated_only=True, **filters) == {
        "untagged_incidents_excluded": untagged
    }


def test_plain_utility_filter_scope_follows_the_county(db_conn):
    counted, untagged = _sql_plain_filter(db_conn, "PGE", 2017, county="Butte")
    _rows, total, extra = _records(db_conn, utility="PGE", year=2017, county="Butte")
    assert (total, extra["untagged_incidents_excluded"]) == (counted, untagged)


def test_other_utility_scopes_do_not_report_an_excluded_figure(db_conn):
    for kwargs in ({"utility": "untagged"}, {"utility": None}):
        _rows, _total, extra = _records(db_conn, year=2020, **kwargs)
        assert "untagged_incidents_excluded" not in extra
    _rows, _total, extra = dq.query_calfire(
        db_conn, utility="SCE", include_untagged=True, county=None, year=2020,
        start_date=None, end_date=None, min_acres=None, incident_type=None, limit=1, offset=0,
    )
    assert "untagged_incidents_excluded" not in extra
    assert extra["untagged_incidents_counted"] == 26
