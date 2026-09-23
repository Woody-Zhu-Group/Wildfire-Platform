"""Only the map-layer queries simplify HFTD and IOU geometry.

Every count and containment query must use the full stored geometry. The
static tests read the service source; the DB tests are read-only.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import date
from pathlib import Path

import psycopg
import pytest

from services.comparison import queries as comparison
from services.data_query import queries as data_query
from services.visualization import queries as visualization

SERVICES = Path(__file__).resolve().parents[1] / "services"
MAP_FUNCTIONS = {"map_hftd", "utility_territory"}


def _service_sources():
    return {path: path.read_text(encoding="utf-8") for path in SERVICES.rglob("*.py")}


def test_simplification_appears_only_in_the_map_expression():
    hits = [
        (path.relative_to(SERVICES).as_posix(), line.strip())
        for path, text in _service_sources().items()
        for line in text.splitlines()
        if re.search(r"ST_Simplify", line, re.I)
    ]
    assert hits == [
        (
            "visualization/queries.py",
            'f"ST_AsGeoJSON(ST_Multi(ST_SimplifyPreserveTopology(geom, {MAP_SIMPLIFY_DEGREES})), "',
        )
    ]


def test_only_the_map_layer_functions_use_the_simplified_expression():
    users = {
        name
        for name, func in inspect.getmembers(visualization, inspect.isfunction)
        if func.__module__ == visualization.__name__ and "_MAP_GEOJSON" in inspect.getsource(func)
    }
    assert users == MAP_FUNCTIONS
    others = [
        path.relative_to(SERVICES).as_posix()
        for path, text in _service_sources().items()
        if "_MAP_GEOJSON" in text and path.name != "queries.py"
    ]
    assert others == []


def test_no_service_reads_the_audit_geometry():
    assert [p for p, text in _service_sources().items() if "geom_source" in text] == []


# ---- Live, read-only ---------------------------------------------------------------


def test_map_outputs_are_simplified_but_bounds_use_full_geometry(db_conn: psycopg.Connection):
    with db_conn.cursor() as cur:
        cur.execute("SELECT tier, ST_NPoints(geom) FROM wildfire.hftd_tiers")
        full_points = dict(cur.fetchall())
    for row in visualization.map_hftd(db_conn, tier=None):
        geom = json.loads(row["geom"])
        points = sum(len(ring) for poly in geom["coordinates"] for ring in poly)
        assert points < full_points[row["tier"]]
    territory = visualization.utility_territory(db_conn, "SCE")
    with db_conn.cursor() as cur:
        cur.execute(
            """SELECT ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom),
                      ST_NPoints(geom)
               FROM wildfire.iou_territories WHERE utility = 'SCE'"""
        )
        xmin, ymin, xmax, ymax, points = cur.fetchone()
    assert (territory["min_lon"], territory["min_lat"], territory["max_lon"], territory["max_lat"]) == (
        xmin, ymin, xmax, ymax
    )
    simplified = json.loads(territory["geom"])
    assert sum(len(r) for p in simplified["coordinates"] for r in p) < points


def _full_count(cur, table, key, region, sql_predicate):
    cur.execute(
        f"""SELECT count(*) FROM wildfire.cpuc_ignitions i, {table} r
            WHERE r.{key} = %s AND {sql_predicate}(i.geom, r.geom)
              AND i.event_date BETWEEN '2024-01-01' AND '2024-12-31'""",
        (region,),
    )
    return int(cur.fetchone()[0])


@pytest.mark.parametrize(
    "kind,region,table,key",
    [
        ("utility", "PGE", "wildfire.iou_territories", "utility"),
        ("hftd", "Tier 2", "wildfire.hftd_tiers", "tier"),
    ],
)
def test_counts_match_the_full_geometry(db_conn, kind, region, table, key):
    start, end = date(2024, 1, 1), date(2024, 12, 31)
    with db_conn.cursor() as cur:
        expected = _full_count(cur, table, key, region, "ST_Within")
    summary = data_query.spatial_summary(
        db_conn,
        utility=region if kind == "utility" else None,
        hftd_tier=region if kind == "hftd" else None,
        start_date=start,
        end_date=end,
    )
    assert summary["counts"]["ignitions"] == expected
    value, _ = comparison.ignition_count(
        db_conn, scope="utility" if kind == "utility" else "hftd", scope_id=region,
        start=start, end=end, definition="spatial",
    )
    assert value == expected


def test_point_containment_uses_the_full_geometry(db_conn):
    # Albany's shoreline point is 3.5 m outside the full PG&E polygon. The
    # simplified map outline would cover it, so a match here would mean a
    # point lookup had switched to simplified geometry.
    body = data_query.spatial_point(db_conn, 37.890650, -122.318116)
    assert body["iou"]["utility"] is None
    with db_conn.cursor() as cur:
        cur.execute(
            """SELECT ST_Contains(ST_SimplifyPreserveTopology(geom, 0.001),
                                  ST_SetSRID(ST_MakePoint(-122.318116, 37.890650), 4326))
               FROM wildfire.iou_territories WHERE utility = 'PGE'"""
        )
        assert cur.fetchone()[0] is True
