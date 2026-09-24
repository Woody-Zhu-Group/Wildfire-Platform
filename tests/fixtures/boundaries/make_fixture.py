"""Build the small Esri JSON boundary fixture used by the load tests.

    python tests/fixtures/boundaries/make_fixture.py   (repo root, needs PostGIS)

The fixture has every expected feature (six utilities, two tiers) as small
squares in Esri ring orientation (clockwise outer, counterclockwise hole),
plus the cases the loader must handle:
- a PG&E hole at the Suisun override point with the override's area, which
  the override turns into area;
- an SCE hole that stays a hole;
- a Tier 2 sliver hole of about 0.002 m2, CPUC's smallest real sliver, which
  must be kept.

Shape__Area is computed with the same PostGIS path the load gate uses, then
written in each layer's native units, so the gate passes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from db.loaders.arcgis_polygons import (
    AREA_UNIT_M2,
    HFTD_LAYER,
    IOU_LAYER,
    build_geometry,
    features_to_rows,
    ring_area_km2,
)
from shared.db import connect, get_settings

HERE = Path(__file__).parent


def outer(x0, y0, x1, y1):
    """Clockwise square with y pointing north."""
    return [[x0, y0], [x0, y1], [x1, y1], [x1, y0], [x0, y0]]


def hole(x0, y0, x1, y1):
    """Counterclockwise square."""
    return list(reversed(outer(x0, y0, x1, y1)))


def square_hole_with_area(cx, cy, km2):
    """A counterclockwise square centered on (cx, cy) with the given spherical area."""
    side_deg = math.sqrt(km2) / 111.0
    for _ in range(50):
        half_y = side_deg / 2
        half_x = side_deg / 2 / math.cos(math.radians(cy))
        ring = hole(cx - half_x, cy - half_y, cx + half_x, cy + half_y)
        area = ring_area_km2(ring)
        if abs(area - km2) / km2 < 1e-6:
            return ring
        side_deg *= math.sqrt(km2 / area)
    return ring


def sliver_hole(x, y, m2):
    """A thin counterclockwise triangle of about the given area in m2."""
    width = 1e-4  # degrees
    height = 2 * (m2 / 1e6) / (width * 111.0 * 111.0 * math.cos(math.radians(y)))
    return [[x, y], [x + width, y], [x, y + height], [x, y]]


def hftd_features():
    tier2 = [
        outer(-121.0, 39.0, -120.9, 39.1),
        hole(-120.97, 39.03, -120.95, 39.05),
        sliver_hole(-120.92, 39.08, 0.002),
    ]
    tier3 = [outer(-120.8, 39.0, -120.7, 39.1)]
    return [
        {"attributes": {"OBJECTID": 1, "HFTD": "Tier 2", "Shape__Length": 1.0}, "geometry": {"rings": tier2}},
        {"attributes": {"OBJECTID": 2, "HFTD": "Tier 3", "Shape__Length": 1.0}, "geometry": {"rings": tier3}},
    ]


def iou_features():
    (override,) = IOU_LAYER.ring_overrides
    pge = [
        outer(-122.30, 37.90, -121.80, 38.25),
        square_hole_with_area(override.lon, override.lat, override.expected_area_km2),
    ]
    sce = [outer(-118.0, 34.0, -117.8, 34.2), hole(-117.95, 34.05, -117.90, 34.10)]
    names = {
        "PG&E": ("Pacific Gas & Electric Company", pge),
        "SCE": ("Southern California Edison Company", sce),
        "PacifiCorp": ("Pacific Power", [outer(-122.6, 41.6, -122.5, 41.7)]),
        "SDG&E": ("San Diego Gas & Electric Company", [outer(-117.1, 32.8, -117.0, 32.9)]),
        "LU": ("Liberty Utilities", [outer(-120.1, 39.2, -120.0, 39.3)]),
        "BVES": ("Bear Valley Electric Services", [outer(-116.95, 34.23, -116.90, 34.26)]),
    }
    return [
        {"attributes": {"OBJECTID": i, "UtilityID": key, "Name": name}, "geometry": {"rings": rings}}
        for i, (key, (name, rings)) in enumerate(names.items(), start=1)
    ]


def payload(layer, wkid, features):
    return {
        "source_url": f"fixture:{layer.url}",
        "fetched_at": "2026-09-24T00:00:00+00:00",
        "layer_name": f"fixture {layer.name}",
        "native_wkid": wkid,
        "data_last_edit": "2026-09-24T00:00:00+00:00",
        "features": features,
    }


def fill_areas(cur, layer, data):
    """Set Shape__Area to the EPSG:3310 area the gate will measure."""
    unit = AREA_UNIT_M2[data["native_wkid"]]
    for feature in data["features"]:
        feature["attributes"]["Shape__Area"] = 1.0  # placeholder for features_to_rows
    rows = features_to_rows(data, layer)
    by_key = {row["key"]: row for row in rows}
    for feature in data["features"]:
        row = by_key[str(feature["attributes"][layer.key_field])]
        geom, _ = build_geometry(cur, row["polygons"])
        cur.execute("SELECT ST_Area(ST_Transform(%s::geometry, 3310))", (geom,))
        feature["attributes"]["Shape__Area"] = float(cur.fetchone()[0]) / unit


def main() -> None:
    hftd = payload(HFTD_LAYER, 3310, hftd_features())
    iou = payload(IOU_LAYER, 102599, iou_features())
    with connect(get_settings()) as conn, conn.cursor() as cur:
        fill_areas(cur, HFTD_LAYER, hftd)
        fill_areas(cur, IOU_LAYER, iou)
        conn.rollback()
    for layer, data in ((HFTD_LAYER, hftd), (IOU_LAYER, iou)):
        path = HERE / f"{layer.name}.esri.json"
        path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print("wrote", path)


if __name__ == "__main__":
    main()
