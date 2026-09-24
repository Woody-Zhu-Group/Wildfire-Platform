"""Unit tests for ring roles, completeness, overrides, sources, and the gate. No DB."""

from __future__ import annotations

import json

import httpx
import pytest

from db.loaders import arcgis_polygons as ap
from db.loaders.arcgis_polygons import (
    AREA_UNIT_M2,
    GeometryGateError,
    Layer,
    RingOverride,
    SourceUnavailable,
    area_unit_m2,
    check_complete,
    dataset_demo_geometries,
    esri_rings_to_polygons,
    features_to_rows,
    gate_failures,
    is_outer_ring,
    load_source,
    ring_area_km2,
    signed_area,
)


def square(x0, y0, size, *, clockwise):
    """Closed square ring. Esri outer rings are clockwise, holes counterclockwise."""
    ccw = [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]
    return list(reversed(ccw)) if clockwise else ccw


def polygons_of(rings, overrides=()):
    polygons, _ = esri_rings_to_polygons(rings, overrides)
    return polygons


# ---- Ring roles -----------------------------------------------------------


def test_signed_area_sign_follows_orientation():
    assert signed_area(square(0, 0, 2, clockwise=False)) == pytest.approx(4.0)
    assert signed_area(square(0, 0, 2, clockwise=True)) == pytest.approx(-4.0)


def test_clockwise_rings_are_outer_and_counterclockwise_rings_are_holes():
    assert is_outer_ring(square(0, 0, 1, clockwise=True))
    assert not is_outer_ring(square(0, 0, 1, clockwise=False))


def test_ring_area_km2_is_close_to_a_projected_area():
    # A 0.1 degree square at 38N is about 11.1 km by 8.77 km.
    ring = square(-122.0, 38.0, 0.1, clockwise=True)
    assert ring_area_km2(ring) == pytest.approx(11.12 * 8.77, rel=0.01)


def test_a_hole_is_attached_to_its_outer_ring_not_made_a_shell():
    outer = square(0, 0, 10, clockwise=True)
    hole = square(2, 2, 3, clockwise=False)
    assert polygons_of([outer, hole]) == [[outer, hole]]


def test_a_hole_goes_to_the_smallest_outer_ring_containing_it():
    # Outer, hole, island inside the hole, hole inside the island.
    outer = square(0, 0, 100, clockwise=True)
    big_hole = square(10, 10, 80, clockwise=False)
    island = square(20, 20, 60, clockwise=True)
    small_hole = square(40, 40, 10, clockwise=False)
    polygons, report = esri_rings_to_polygons([outer, big_hole, island, small_hole])
    assert polygons == [[outer, big_hole], [island, small_hole]]
    # An island inside a hole is not a nested outer.
    assert report.nested_outers == 0


def test_separate_outer_rings_stay_separate_polygons():
    a = square(0, 0, 1, clockwise=True)
    b = square(5, 5, 1, clockwise=True)
    assert polygons_of([a, b]) == [[a], [b]]


def test_a_hole_touching_its_outer_boundary_is_still_assigned():
    outer = square(0, 0, 10, clockwise=True)
    hole = [[0, 2], [3, 2], [3, 5], [0, 5], [0, 2]]  # shares the left edge
    assert not is_outer_ring(hole)
    assert polygons_of([outer, hole]) == [[outer, hole]]


def test_an_outer_nested_directly_in_another_outer_is_counted_and_kept_for_union():
    outer = square(0, 0, 10, clockwise=True)
    nested = square(2, 2, 3, clockwise=True)
    polygons, report = esri_rings_to_polygons([outer, nested])
    assert polygons == [[outer], [nested]]
    assert report.nested_outers == 1
    assert "nested" in report.summary()


def test_a_hole_outside_every_outer_ring_fails_loudly():
    with pytest.raises(GeometryGateError, match="not inside any outer ring"):
        esri_rings_to_polygons([square(0, 0, 1, clockwise=True), square(5, 5, 1, clockwise=False)])


def test_a_feature_with_no_outer_ring_fails_loudly():
    with pytest.raises(GeometryGateError, match="no clockwise"):
        esri_rings_to_polygons([square(0, 0, 1, clockwise=False)])


def test_short_rings_are_dropped_and_counted():
    outer = square(0, 0, 10, clockwise=True)
    short = [[1, 1], [2, 2], [1, 1]]
    polygons, report = esri_rings_to_polygons([outer, short])
    assert polygons == [[outer]]
    assert report.rings == 2 and report.dropped_short == 1
    assert "1 dropped" in report.summary()


def test_a_zero_area_ring_is_rejected():
    outer = square(0, 0, 10, clockwise=True)
    collinear = [[1, 1], [2, 2], [3, 3], [1, 1]]
    with pytest.raises(GeometryGateError, match="zero area"):
        esri_rings_to_polygons([outer, collinear])


def test_slivers_are_counted_not_changed():
    outer = square(-122.0, 38.0, 0.1, clockwise=True)
    sliver = square(-121.95, 38.05, 0.000001, clockwise=False)  # about 0.01 m2
    polygons, report = esri_rings_to_polygons([outer, sliver])
    assert polygons == [[outer, sliver]]
    assert report.slivers == 1


# ---- Ring overrides -------------------------------------------------------


def _override(**changes):
    base = dict(key="A", lon=-121.97, lat=38.03, expected_area_km2=0.0, tolerance=0.005, reason="test")
    base.update(changes)
    return RingOverride(**base)


def _area_case():
    outer = square(-122.0, 38.0, 0.1, clockwise=True)
    hole = square(-121.98, 38.02, 0.02, clockwise=False)
    return outer, hole, ring_area_km2(hole)


def test_a_ring_override_turns_exactly_one_published_hole_into_area():
    outer, hole, area = _area_case()
    polygons, report = esri_rings_to_polygons([outer, hole], (_override(expected_area_km2=area),))
    assert polygons == [[outer], [list(reversed(hole))]]
    assert report.overridden and report.nested_outers == 1


def test_a_ring_override_that_matches_no_hole_by_point_raises():
    outer, hole, area = _area_case()
    with pytest.raises(GeometryGateError, match="matched 0 holes"):
        esri_rings_to_polygons([outer, hole], (_override(lon=-121.91, lat=38.09, expected_area_km2=area),))


def test_a_ring_override_with_the_wrong_area_raises():
    outer, hole, area = _area_case()
    with pytest.raises(GeometryGateError, match="matched 0 holes"):
        esri_rings_to_polygons([outer, hole], (_override(expected_area_km2=area * 1.02),))


def test_a_ring_override_naming_no_feature_raises():
    layer = Layer(
        name="t", url="u", key_field="K", expected_keys=frozenset({"A"}),
        ring_overrides=(_override(key="Z"),),
    )
    payload = {"native_wkid": 3310, "features": [_feature("A")]}
    with pytest.raises(GeometryGateError, match="do not exist"):
        features_to_rows(payload, layer)


def test_the_pge_override_is_the_only_one_and_is_documented():
    assert ap.HFTD_LAYER.ring_overrides == ()
    (override,) = ap.IOU_LAYER.ring_overrides
    assert override.key == "PG&E"
    assert override.tolerance <= 0.01
    assert "Shape__Area" in override.reason and "EPSS" in override.reason


# ---- Completeness -----------------------------------------------------------


def _feature(key, area=1.0e10):
    return {
        "attributes": {"K": key, "Shape__Area": area},
        "geometry": {"rings": [square(0, 0, 1, clockwise=True)]},
    }


LAYER = Layer(name="t", url="https://example.test/FeatureServer/0", key_field="K",
              expected_keys=frozenset({"A", "B"}))


@pytest.mark.parametrize(
    "keys,message",
    [
        (["A"], "missing \\['B'\\]"),
        (["A", "B", "C"], "unexpected \\['C'\\]"),
        (["A", "B", "B"], "duplicated \\['B'\\]"),
    ],
)
def test_the_feature_set_must_be_exactly_the_expected_keys(keys, message):
    payload = {"native_wkid": 3310, "features": [_feature(k) for k in keys]}
    with pytest.raises(GeometryGateError, match=message):
        check_complete(payload, LAYER)
    with pytest.raises(GeometryGateError, match=message):
        features_to_rows(payload, LAYER)


def test_the_real_layers_expect_every_utility_and_both_tiers():
    assert ap.HFTD_LAYER.expected_keys == {"Tier 2", "Tier 3"}
    assert ap.IOU_LAYER.expected_keys == {"PG&E", "SCE", "PacifiCorp", "SDG&E", "LU", "BVES"}


# ---- Area units and the gate ------------------------------------------------


def test_area_units_follow_the_publisher_spatial_reference():
    assert area_unit_m2(3310) == 1.0
    assert area_unit_m2(102599) == pytest.approx(0.09290341161, rel=1e-9)
    with pytest.raises(GeometryGateError, match="wkid 4326"):
        area_unit_m2(4326)
    assert set(AREA_UNIT_M2) == {3310, 102599}


def test_features_to_rows_converts_the_publisher_area_to_square_meters():
    payload = {"native_wkid": 102599, "features": [_feature("A", 1000.0), _feature("B", 1000.0)]}
    rows = features_to_rows(payload, LAYER)
    assert rows[0]["publisher_area_m2"] == pytest.approx(92.90341161, rel=1e-9)
    payload["features"][0]["attributes"].pop("Shape__Area")
    with pytest.raises(GeometryGateError, match="Shape__Area"):
        features_to_rows(payload, LAYER)


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
    (failure,) = gate_failures([_row(area=167_125e6, publisher=149_984.7e6)])
    assert "11.4" in failure


def test_gate_fails_a_missing_publisher_area():
    (failure,) = gate_failures([_row(publisher=None)])
    assert "publisher area" in failure


# ---- Sources: fetch, pagination, cache ---------------------------------------


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "CACHE_DIR", tmp_path)
    return tmp_path


def _mock_publisher(calls):
    pages = {0: ([_feature("A")], True), 1: ([_feature("B")], False)}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/query"):
            batch, more = pages[int(request.url.params["resultOffset"])]
            return httpx.Response(200, json={"features": batch, "exceededTransferLimit": more})
        return httpx.Response(200, json={
            "name": "test layer",
            "extent": {"spatialReference": {"wkid": 3310, "latestWkid": 3310}},
            "editingInfo": {"dataLastEditDate": 1_700_000_000_000},
        })

    return httpx.MockTransport(handler)


def _offline():
    def handler(request):
        raise httpx.ConnectError("no network", request=request)

    return httpx.MockTransport(handler)


def test_fetch_follows_pagination_and_writes_the_cache(cache_dir):
    calls = []
    payload = load_source(LAYER, transport=_mock_publisher(calls))
    assert [f["attributes"]["K"] for f in payload["features"]] == ["A", "B"]
    assert sum("/query" in url for url in calls) == 2
    assert "resultOffset=1" in calls[-1]
    assert payload["native_wkid"] == 3310 and payload["data_last_edit"].startswith("2023-11-14")
    cached = json.loads(LAYER.cache_path.read_text(encoding="utf-8"))
    assert cached["features"] == payload["features"]


def test_a_cached_layer_is_used_without_the_network(cache_dir):
    load_source(LAYER, transport=_mock_publisher([]))
    payload = load_source(LAYER, transport=_offline())
    assert len(payload["features"]) == 2


def test_no_cache_and_no_network_fails_with_the_fix(cache_dir):
    with pytest.raises(SourceUnavailable) as caught:
        load_source(LAYER, transport=_offline())
    message = str(caught.value)
    assert "no cache" in message and "--fetch-only" in message
    assert not LAYER.cache_path.exists()


def test_refresh_without_network_fails_even_with_a_cache(cache_dir):
    load_source(LAYER, transport=_mock_publisher([]))
    with pytest.raises(SourceUnavailable, match="refresh requested"):
        load_source(LAYER, refresh=True, transport=_offline())


def test_an_incomplete_cache_is_refused(cache_dir):
    cache_dir.joinpath("t.esri.json").write_text(
        json.dumps({"native_wkid": 3310, "features": [_feature("A")]}), encoding="utf-8"
    )
    with pytest.raises(GeometryGateError, match="missing \\['B'\\]"):
        load_source(LAYER, transport=_offline())


def test_an_incomplete_download_is_refused_and_not_cached(cache_dir):
    def handler(request):
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"features": [_feature("A")]})
        return httpx.Response(200, json={"extent": {"spatialReference": {"wkid": 3310}}})

    with pytest.raises(GeometryGateError, match="missing"):
        load_source(LAYER, transport=httpx.MockTransport(handler))
    assert not LAYER.cache_path.exists()


def test_dataset_demo_geometry_is_optional(tmp_path):
    # EC2 has no dataset_demo; geom_source is then NULL.
    assert dataset_demo_geometries(tmp_path / "missing.geojson", "HFTD") == {}
