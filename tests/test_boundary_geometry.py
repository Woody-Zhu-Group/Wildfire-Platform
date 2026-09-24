"""Live DB checks on the rebuilt HFTD and IOU geometry.

Reads the warehouse tables. The load tests write only to a throwaway schema
that is dropped afterwards; they never touch wildfire.*.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from db.loaders import arcgis_polygons, load_boundaries
from db.loaders.arcgis_polygons import (
    AREA_TOLERANCE,
    GeometryGateError,
    build_geometry,
    check_table,
    gate_failures,
)
from shared.db import connect, get_settings


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


@pytest.mark.parametrize("table", ["wildfire.hftd_tiers", "wildfire.iou_territories"])
def test_rebuilt_rows_record_their_source(db_conn, table):
    # geom_source may be NULL: EC2 has no dataset_demo to take it from.
    with db_conn.cursor() as cur:
        cur.execute(
            f"""SELECT count(*) FILTER (WHERE source_url IS NULL),
                       count(*) FILTER (WHERE publisher_area_m2 IS NULL)
                FROM {table}"""
        )
        assert cur.fetchone() == (0, 0)


def test_expected_features_are_all_present(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT array_agg(tier ORDER BY tier) FROM wildfire.hftd_tiers")
        assert cur.fetchone()[0] == ["Tier 2", "Tier 3"]
        cur.execute("SELECT array_agg(utility ORDER BY utility) FROM wildfire.iou_territories")
        assert cur.fetchone()[0] == ["BVES", "Liberty", "PACIFICORP", "PGE", "SCE", "SDGE"]


def test_holes_are_holes_not_extra_shells(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            """SELECT tier, (SELECT sum(ST_NumInteriorRings(d.geom)) FROM ST_Dump(geom) d)
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
        (-122.0402, 38.0816, "PGE"),  # Suisun Marsh ring, the PG&E override
    ],
)
def test_municipal_utility_holes_are_outside_the_iou(db_conn, lon, lat, utility):
    with db_conn.cursor() as cur:
        cur.execute(
            """SELECT array_agg(utility ORDER BY utility) FROM wildfire.iou_territories
               WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))""",
            (lon, lat),
        )
        found = cur.fetchone()[0]
    assert found == ([utility] if utility else None)


def _area(cur, geom):
    cur.execute("SELECT ST_IsValid(%s::geometry), ST_Area(%s::geometry)", (geom, geom))
    return cur.fetchone()


def test_build_geometry_unions_outer_rings_and_keeps_holes(db_conn):
    a = [[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]
    b = [[1, 0], [1, 2], [3, 2], [3, 0], [1, 0]]
    hole = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8], [0.2, 0.2]]
    with db_conn.cursor() as cur:
        geom, repaired = build_geometry(cur, [[a, hole], [b]])
        valid, area = _area(cur, geom)
    db_conn.rollback()
    assert repaired == 0 and valid
    assert area == pytest.approx(6.0 - 0.36)


def test_build_geometry_absorbs_an_outer_nested_in_another_outer(db_conn):
    # Esri nonzero winding: a nested outer counts once, and a hole that only
    # the nested outer has stays covered by the container.
    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
    nested = [[2, 2], [2, 5], [5, 5], [5, 2], [2, 2]]
    nested_hole = [[3, 3], [4, 3], [4, 4], [3, 4], [3, 3]]
    with db_conn.cursor() as cur:
        geom, _ = build_geometry(cur, [[outer], [nested, nested_hole]])
        valid, area = _area(cur, geom)
    db_conn.rollback()
    assert valid
    assert area == pytest.approx(100.0)


# ---- Load into a throwaway schema -----------------------------------------------


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "boundaries"


@pytest.fixture
def fixture_sources(monkeypatch, tmp_path):
    """Load from the committed fixture, never the network or data/boundaries.

    The fixture (tests/fixtures/boundaries, built by make_fixture.py) has every
    expected feature, the PG&E override hole, an SCE hole, and a CPUC-sized
    sliver. dataset_demo is pointed at an empty folder, so geom_source is NULL
    as on EC2.
    """
    monkeypatch.setattr(arcgis_polygons, "CACHE_DIR", FIXTURE_DIR)
    return SimpleNamespace(dataset_demo_data_dir=tmp_path / "no_dataset_demo")


@pytest.fixture
def scratch_schema():
    schema = f"test_boundaries_{uuid.uuid4().hex[:8]}"
    conn = connect(get_settings(), autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA {schema}")
            for table in ("iou_territories", "hftd_tiers"):
                cur.execute(
                    f"CREATE TABLE {schema}.{table} (LIKE wildfire.{table} INCLUDING ALL)"
                )
                cur.execute(f"INSERT INTO {schema}.{table} SELECT * FROM wildfire.{table}")
        yield conn, schema
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.close()


def _fingerprint(conn, schema):
    with conn.cursor() as cur:
        out = {}
        for table, key in (("iou_territories", "utility"), ("hftd_tiers", "tier")):
            cur.execute(
                f"SELECT {key}, md5(ST_AsBinary(geom)) FROM {schema}.{table} ORDER BY 1"
            )
            out[table] = cur.fetchall()
        return out


def test_a_gate_failure_rolls_back_both_tables(scratch_schema, fixture_sources):
    conn, schema = scratch_schema
    before = _fingerprint(conn, schema)
    prepared = load_boundaries.prepare(fixture_sources)
    broken = copy.deepcopy(prepared)
    # HFTD is inserted after IOU, so a failure there must also undo IOU.
    broken["hftd"]["rows"][0]["publisher_area_m2"] *= 1.01
    with pytest.raises(GeometryGateError, match="failed the geometry gate"):
        load_boundaries.apply(conn, broken, schema=schema)
    assert _fingerprint(conn, schema) == before


def test_a_clean_load_replaces_both_tables_together(scratch_schema, fixture_sources):
    conn, schema = scratch_schema
    counts = load_boundaries.apply(conn, load_boundaries.prepare(fixture_sources), schema=schema)
    assert counts == {"iou_territories": 6, "hftd_tiers": 2}
    with conn.cursor() as cur:
        for table, key in (("iou_territories", "utility"), ("hftd_tiers", "tier")):
            assert gate_failures(check_table(cur, f"{schema}.{table}", key)) == []
            # No dataset_demo here, as on EC2.
            cur.execute(f"SELECT count(*) FROM {schema}.{table} WHERE geom_source IS NOT NULL")
            assert cur.fetchone()[0] == 0
        holes = {}
        for table, key in (("iou_territories", "utility"), ("hftd_tiers", "tier")):
            cur.execute(
                f"""SELECT {key}, (SELECT sum(ST_NumInteriorRings(d.geom)) FROM ST_Dump(geom) d)
                    FROM {schema}.{table}"""
            )
            holes.update(dict(cur.fetchall()))
    # SCE keeps its hole; the PG&E override hole became area; Tier 2 keeps its
    # hole and the 0.002 m2 sliver.
    assert holes["SCE"] == 1
    assert holes["PGE"] == 0
    assert holes["Tier 2"] == 2
