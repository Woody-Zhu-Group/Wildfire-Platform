"""Live DB checks on the rebuilt HFTD and IOU geometry (read-only)."""

from __future__ import annotations

import psycopg
import pytest

from db.loaders.arcgis_polygons import AREA_TOLERANCE, build_geometry, check_table, gate_failures


@pytest.mark.parametrize(
    "table,key", [("wildfire.hftd_tiers", "tier"), ("wildfire.iou_territories", "utility")]
)
def test_every_geometry_is_valid_and_matches_the_publisher_area(
    db_conn: psycopg.Connection, table: str, key: str
):
    with db_conn.cursor() as cur:
        rows = check_table(cur, table, key)
    assert rows
    assert gate_failures(rows, AREA_TOLERANCE) == []


@pytest.mark.parametrize(
    "table", ["wildfire.hftd_tiers", "wildfire.iou_territories"]
)
def test_rebuilt_rows_keep_the_old_geometry_and_the_source(db_conn, table):
    with db_conn.cursor() as cur:
        cur.execute(
            f"""SELECT count(*) FILTER (WHERE geom_source IS NULL),
                       count(*) FILTER (WHERE source_url IS NULL),
                       count(*) FILTER (WHERE publisher_area_m2 IS NULL)
                FROM {table}"""
        )
        assert cur.fetchone() == (0, 0, 0)


def test_holes_are_holes_not_extra_shells(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            """SELECT tier, (SELECT sum(ST_NumInteriorRings(d.geom))
                             FROM ST_Dump(geom) d)
               FROM wildfire.hftd_tiers ORDER BY tier"""
        )
        holes = dict(cur.fetchall())
        # Esri JSON has 436 Tier 2 and 30 Tier 3 counterclockwise rings.
        assert holes["Tier 2"] > 400
        assert holes["Tier 3"] > 20
        cur.execute(
            """SELECT (SELECT sum(ST_NumInteriorRings(d.geom)) FROM ST_Dump(geom) d)
               FROM wildfire.iou_territories WHERE utility = 'SCE'"""
        )
        assert cur.fetchone()[0] >= 5


@pytest.mark.parametrize(
    "lon,lat,utility",
    [
        (-117.9145, 33.8366, None),  # Anaheim: Anaheim Public Utilities, a hole in SCE
        (-117.9076, 34.1336, None),  # Azusa: Azusa Light & Water
        (-116.9114, 34.2439, "BVES"),  # Big Bear Lake: BVES, a hole in SCE
        (-118.2437, 34.0522, None),  # Los Angeles: LADWP
        (-121.8375, 39.7285, "PGE"),  # Chico
    ],
)
def test_municipal_utility_holes_are_outside_the_iou(db_conn, lon, lat, utility):
    with db_conn.cursor() as cur:
        cur.execute(
            """SELECT min(utility) FROM wildfire.iou_territories
               WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))""",
            (lon, lat),
        )
        assert cur.fetchone()[0] == utility


def test_build_geometry_unions_outer_rings_and_keeps_holes(db_conn):
    # Two overlapping outer rings count once; the hole stays a hole.
    a = [[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]
    b = [[1, 0], [1, 2], [3, 2], [3, 0], [1, 0]]
    hole = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8], [0.2, 0.2]]
    with db_conn.cursor() as cur:
        geom, repaired = build_geometry(cur, [[a, hole], [b]])
        cur.execute("SELECT ST_IsValid(%s::geometry), ST_Area(%s::geometry)", (geom, geom))
        valid, area = cur.fetchone()
    db_conn.rollback()
    assert repaired == 0
    assert valid
    assert area == pytest.approx(6.0 - 0.36)
