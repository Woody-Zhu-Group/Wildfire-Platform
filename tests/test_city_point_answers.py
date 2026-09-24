"""City center point answers on the rebuilt HFTD and IOU geometry (read-only).

The 30 answers that changed with PR #45 are listed in docs/DATA_CHANGE_HFTD_IOU.md.
Here they are read the way a city route reads them: /spatial/point at the
Census 2025 Gazetteer internal point with the shoreline snap on.
"""

from __future__ import annotations

import psycopg
import pytest

from services.agent.places import city_point
from services.data_query.queries import (
    SHORE_SNAP_COUNTY_M,
    SHORE_SNAP_IOU_M,
    spatial_point,
)


def _read(conn, name, *, snap=True):
    point = city_point(name)
    assert point is not None, name
    return spatial_point(conn, point.lat, point.lon, snap_shoreline=snap)


IOU_CHANGED = {
    "Albany": "PGE",  # 3.5 m off the shoreline; snapped (was PGE on the old geometry too)
    "Anaheim": None,  # Anaheim Public Utilities, a hole in SCE
    "Azusa": None,
    "Banning": None,
    "Big Bear Lake": "BVES",  # was inside both BVES and SCE
    "Colton": None,
    "Foster City": "PGE",
    "Malibu": "SCE",
    "Pismo Beach": "PGE",
    "Redondo Beach": "SCE",
    "Riverside": None,
    "West Hollywood": "SCE",
}
HFTD_CHANGED = {
    "Anderson": None,
    "Auburn": None,
    "Banning": "Tier 2",  # was inside both tiers
    "Colfax": None,
    "Dunsmuir": "Tier 3",
    "Grass Valley": None,
    "Jackson": None,
    "Lompoc": None,
    "Loyalton": None,
    "Placerville": "Tier 2",
    "Redding": None,
    "Scotts Valley": None,
    "Shasta Lake": None,
    "Sutter Creek": None,
    "Tehachapi": "Tier 3",
    "Truckee": "Tier 3",
    "Ukiah": None,
    "Willits": None,
}


def test_the_changed_answers_cover_all_30_cities():
    assert len(IOU_CHANGED) + len(HFTD_CHANGED) == 30


@pytest.mark.parametrize("name,utility", sorted(IOU_CHANGED.items()))
def test_iou_answers_on_the_rebuilt_geometry(db_conn: psycopg.Connection, name, utility):
    assert _read(db_conn, name)["iou"]["utility"] == utility


@pytest.mark.parametrize("name,tier", sorted(HFTD_CHANGED.items()))
def test_hftd_answers_on_the_rebuilt_geometry(db_conn, name, tier):
    assert _read(db_conn, name)["hftd_tier"] == tier


@pytest.mark.parametrize(
    "name,utility,distance",
    [
        ("Albany", "PGE", 3.5),
        ("South San Francisco", "PGE", 8.6),
        ("Avalon", "SCE", 31.2),
        ("Morro Bay", "PGE", 41.8),
    ],
)
def test_shoreline_city_points_snap_to_the_one_nearby_territory(db_conn, name, utility, distance):
    assert _read(db_conn, name, snap=False)["iou"]["utility"] is None
    body = _read(db_conn, name)
    assert body["iou"]["utility"] == utility
    assert body["meta"]["shoreline_snap"]["snapped"]["iou"] == pytest.approx(distance, abs=0.2)
    assert distance < SHORE_SNAP_IOU_M


@pytest.mark.parametrize(
    "name",
    # Outside every IOU by 265.8 m and 572.1 m: municipal utilities, not shoreline.
    ["Los Angeles", "Vernon"]
    # Inside an SCE hole: never snapped, however close the edge.
    + ["Anaheim", "Colton", "Azusa", "Riverside", "Banning"]
    + ["Burbank", "Pasadena", "Glendale"],
)
def test_points_clearly_outside_a_territory_are_never_snapped(db_conn, name):
    body = _read(db_conn, name)
    assert body["iou"]["utility"] is None
    assert "iou" not in body["meta"]["shoreline_snap"]["snapped"]


@pytest.mark.parametrize(
    "name,county",
    [("Coronado", "San Diego"), ("Santa Monica", "Los Angeles"),
     ("Manhattan Beach", "Los Angeles"), ("Monterey", "Monterey")],
)
def test_shoreline_city_points_snap_to_the_one_nearby_county(db_conn, name, county):
    assert _read(db_conn, name, snap=False)["county"] is None
    body = _read(db_conn, name)
    assert body["county"] == county
    assert body["meta"]["shoreline_snap"]["snapped"]["county"] < SHORE_SNAP_COUNTY_M


def test_hftd_tiers_and_grid_cells_are_never_snapped(db_conn):
    # Paradise's center is 47.6 m outside Tier 3; Coronado's is outside the grid.
    assert _read(db_conn, "Paradise")["hftd_tier"] is None
    assert _read(db_conn, "Coronado")["grid_cell"]["cell_id"] is None


def test_without_the_flag_nothing_changes(db_conn):
    body = _read(db_conn, "Albany", snap=False)
    assert body["iou"]["utility"] is None
    assert "shoreline_snap" not in body["meta"]
