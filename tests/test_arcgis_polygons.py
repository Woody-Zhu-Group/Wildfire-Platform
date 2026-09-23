"""Unit tests for ring roles, hole assignment, area units, and the load gate. No DB."""

from __future__ import annotations

import pytest

from db.loaders.arcgis_polygons import (
    AREA_UNIT_M2,
    GeometryGateError,
    Layer,
    area_unit_m2,
    esri_rings_to_polygons,
    features_to_rows,
    gate_failures,
    is_outer_ring,
    signed_area,
)


def square(x0, y0, size, *, clockwise):
    """Closed square ring. Esri outer rings are clockwise, holes counterclockwise."""
    ccw = [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]
    return list(reversed(ccw)) if clockwise else ccw


def test_signed_area_sign_follows_orientation():
    assert signed_area(square(0, 0, 2, clockwise=False)) == pytest.approx(4.0)
    assert signed_area(square(0, 0, 2, clockwise=True)) == pytest.approx(-4.0)


def test_clockwise_rings_are_outer_and_counterclockwise_rings_are_holes():
    assert is_outer_ring(square(0, 0, 1, clockwise=True))
    assert not is_outer_ring(square(0, 0, 1, clockwise=False))


def test_a_hole_is_attached_to_its_outer_ring_not_made_a_shell():
    outer = square(0, 0, 10, clockwise=True)
    hole = square(2, 2, 3, clockwise=False)
    polygons = esri_rings_to_polygons([outer, hole])
    assert polygons == [[outer, hole]]


def test_a_hole_goes_to_the_smallest_outer_ring_containing_it():
    # Outer, hole, island inside the hole, hole inside the island.
    outer = square(0, 0, 100, clockwise=True)
    big_hole = square(10, 10, 80, clockwise=False)
    island = square(20, 20, 60, clockwise=True)
    small_hole = square(40, 40, 10, clockwise=False)
    polygons = esri_rings_to_polygons([outer, big_hole, island, small_hole])
    assert polygons == [[outer, big_hole], [island, small_hole]]


def test_separate_outer_rings_stay_separate_polygons():
    a = square(0, 0, 1, clockwise=True)
    b = square(5, 5, 1, clockwise=True)
    assert esri_rings_to_polygons([a, b]) == [[a], [b]]


def test_a_hole_touching_its_outer_boundary_is_still_assigned():
    outer = square(0, 0, 10, clockwise=True)
    # Shares the left edge with the outer ring.
    hole = [[0, 2], [3, 2], [3, 5], [0, 5], [0, 2]]
    assert not is_outer_ring(hole)
    assert esri_rings_to_polygons([outer, hole]) == [[outer, hole]]


def test_a_hole_outside_every_outer_ring_fails_loudly():
    outer = square(0, 0, 1, clockwise=True)
    stray = square(5, 5, 1, clockwise=False)
    with pytest.raises(GeometryGateError, match="not inside any outer ring"):
        esri_rings_to_polygons([outer, stray])


def test_a_feature_with_no_outer_ring_fails_loudly():
    with pytest.raises(GeometryGateError, match="no clockwise"):
        esri_rings_to_polygons([square(0, 0, 1, clockwise=False)])


def test_area_units_follow_the_publisher_spatial_reference():
    assert area_unit_m2(3310) == 1.0
    # California Teale Albers in US survey feet.
    assert area_unit_m2(102599) == pytest.approx(0.09290341161, rel=1e-9)
    with pytest.raises(GeometryGateError, match="wkid 4326"):
        area_unit_m2(4326)
    assert set(AREA_UNIT_M2) == {3310, 102599}


def test_features_to_rows_converts_the_publisher_area_to_square_meters():
    layer = Layer(name="t", url="u", key_field="K")
    payload = {
        "native_wkid": 102599,
        "features": [
            {
                "attributes": {"K": "A", "Shape__Area": 1000.0},
                "geometry": {"rings": [square(0, 0, 1, clockwise=True)]},
            }
        ],
    }
    (row,) = features_to_rows(payload, layer)
    assert row["key"] == "A"
    assert row["publisher_area_m2"] == pytest.approx(92.90341161, rel=1e-9)
    payload["features"][0]["attributes"].pop("Shape__Area")
    with pytest.raises(GeometryGateError, match="Shape__Area"):
        features_to_rows(payload, layer)


def _row(key="Tier 2", valid=True, area=100.0, publisher=100.0):
    return {"key": key, "valid": valid, "reason": "Valid Geometry" if valid else "Self-intersection",
            "area_m2": area, "publisher_area_m2": publisher}


def test_gate_passes_valid_geometry_within_a_tenth_of_a_percent():
    assert gate_failures([_row(area=100.09), _row(area=99.91)]) == []


def test_gate_fails_invalid_geometry():
    (failure,) = gate_failures([_row(valid=False)])
    assert "not valid" in failure and "Self-intersection" in failure


def test_gate_fails_an_area_more_than_a_tenth_of_a_percent_off():
    (failure,) = gate_failures([_row(area=100.2)])
    assert "0.200%" in failure
    # The old dataset_demo Tier 2 geometry summed 167,125 km2 against 149,985.
    (failure,) = gate_failures([_row(area=167_125e6, publisher=149_984.7e6)])
    assert "11.4" in failure


def test_gate_fails_a_missing_publisher_area():
    (failure,) = gate_failures([_row(publisher=None)])
    assert "publisher area" in failure


def test_a_ring_override_turns_one_published_hole_into_area():
    outer = square(0, 0, 10, clockwise=True)
    hole = square(2, 2, 3, clockwise=False)
    polygons = esri_rings_to_polygons([outer, hole], force_outer_points=((3.0, 3.0),))
    assert polygons == [[outer], [list(reversed(hole))]]
    with pytest.raises(GeometryGateError, match="matched 0 holes"):
        esri_rings_to_polygons([outer, hole], force_outer_points=((8.0, 8.0),))


def test_the_pge_override_is_the_only_one_and_is_documented():
    from db.loaders.arcgis_polygons import HFTD_LAYER, IOU_LAYER

    assert HFTD_LAYER.ring_overrides == ()
    (override,) = IOU_LAYER.ring_overrides
    assert override.key == "PG&E"
    assert "Shape__Area" in override.reason and "EPSS" in override.reason
