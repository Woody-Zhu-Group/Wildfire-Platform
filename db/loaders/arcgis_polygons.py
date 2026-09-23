"""Polygon layers from ArcGIS FeatureServers, with ring roles kept.

The GeoJSON export of an ArcGIS layer (f=geojson) can drop hole roles and
write every ring as its own outer polygon. The HFTD and IOU layers in
dataset_demo were built that way, so holes (towns outside a tier, municipal
utilities inside SCE) were loaded as extra shells. Esri JSON (f=json) keeps
the rings as published: clockwise rings are outer boundaries and
counterclockwise rings are holes.

This module fetches Esri JSON, assigns each hole to the smallest outer ring
that contains it, and builds one GeoJSON Polygon per outer ring. PostGIS
unions the polygons (so overlapping outer rings count once) and only calls
ST_MakeValid on a polygon that is still invalid. The load gate then requires
every geometry to be valid and its area to match the publisher's area
attribute within 0.1%.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psycopg

from shared.db import REPO_ROOT

CACHE_DIR = REPO_ROOT / "data" / "boundaries"
AREA_TOLERANCE = 0.001  # 0.1% of the publisher's area

# Square meters per squared unit of the layer's native spatial reference, the
# CRS its Shape__Area attribute is measured in. The area check projects our
# geometry to EPSG:3310 (California Albers), which uses the same Albers
# parameters as both layers below.
_US_SURVEY_FOOT_M = 1200 / 3937
AREA_UNIT_M2 = {
    3310: 1.0,  # NAD83 California Albers, meters
    102599: _US_SURVEY_FOOT_M**2,  # WGS 1984 California Teale Albers, US feet
}


@dataclass(frozen=True)
class RingOverride:
    """A published hole that the evidence says is part of the area.

    The counterclockwise ring of feature ``key`` that contains (lon, lat) is
    treated as an outer ring. The area gate still checks the result against
    the publisher's area attribute.
    """

    key: str
    lon: float
    lat: float
    reason: str


@dataclass(frozen=True)
class Layer:
    name: str
    url: str
    key_field: str
    area_field: str = "Shape__Area"
    ring_overrides: tuple[RingOverride, ...] = ()


HFTD_LAYER = Layer(
    name="cpuc_hftd",
    url=(
        "https://services2.arcgis.com/VofPZYDe2pLxSP5G/ArcGIS/rest/services/"
        "CPUC_High_Fire_Threat_District/FeatureServer/0"
    ),
    key_field="HFTD",
)
IOU_LAYER = Layer(
    name="cpuc_iou_service_territories",
    url=(
        "https://services2.arcgis.com/VofPZYDe2pLxSP5G/arcgis/rest/services/"
        "IOU_Service_Territories/FeatureServer/0"
    ),
    key_field="UtilityID",
    ring_overrides=(
        RingOverride(
            key="PG&E",
            lon=-122.0402,
            lat=38.0816,
            reason=(
                "181 km2 ring over Suisun Marsh and Grizzly Island is marked as a "
                "hole in the Esri JSON, but the layer's own Shape__Area counts it, "
                "and it holds 23 PG&E EPSS outages and 1 PG&E circuit. Checked "
                "2026-09-23 against layer data edited 2026-01-09."
            ),
        ),
    ),
)


class GeometryGateError(RuntimeError):
    """A rebuilt geometry is invalid or its area does not match the publisher."""


# ---- Rings --------------------------------------------------------------


def signed_area(ring: list[list[float]]) -> float:
    """Shoelace area. Positive when counterclockwise with y pointing north."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += x1 * y2 - x2 * y1
    if ring and ring[0] != ring[-1]:
        (x1, y1), (x2, y2) = ring[-1], ring[0]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def is_outer_ring(ring: list[list[float]]) -> bool:
    """Esri rule: clockwise rings are outer boundaries, counterclockwise are holes."""
    return signed_area(ring) < 0


def _bbox(ring: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [pt[0] for pt in ring]
    ys = [pt[1] for pt in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_contains(outer: tuple, inner: tuple) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    """Even-odd ray casting."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < cross:
                inside = not inside
        j = i
    return inside


def _ring_inside(hole: list[list[float]], outer: list[list[float]]) -> bool:
    """A hole is inside an outer ring when most of its sampled vertices are.

    Several vertices are tested because a hole can touch its outer boundary.
    """
    step = max(1, (len(hole) - 1) // 7)
    samples = hole[: len(hole) - 1 : step][:7] or hole[:1]
    hits = sum(point_in_ring(pt[0], pt[1], outer) for pt in samples)
    return hits * 2 > len(samples)


def esri_rings_to_polygons(
    rings: list[list[list[float]]],
    force_outer_points: tuple[tuple[float, float], ...] = (),
) -> list[list[list[list[float]]]]:
    """Group Esri rings into GeoJSON Polygon coordinate arrays.

    Each hole goes to the smallest outer ring containing it. A hole with no
    containing outer ring raises, rather than being dropped or turned into
    an outer ring. A hole containing one of ``force_outer_points`` is treated
    as an outer ring (see RingOverride); each point must match exactly one hole.
    """
    usable = [ring for ring in rings if len(ring) >= 4]
    outers = [ring for ring in usable if is_outer_ring(ring)]
    holes = [ring for ring in usable if not is_outer_ring(ring)]
    for lon, lat in force_outer_points:
        matched = [hole for hole in holes if point_in_ring(lon, lat, hole)]
        if len(matched) != 1:
            raise GeometryGateError(
                f"ring override at ({lon}, {lat}) matched {len(matched)} holes, expected 1"
            )
        holes.remove(matched[0])
        outers.append(list(reversed(matched[0])))
    if not outers:
        raise GeometryGateError("feature has no clockwise (outer) ring")
    outer_boxes = [_bbox(ring) for ring in outers]
    outer_areas = [abs(signed_area(ring)) for ring in outers]
    polygons: list[list[list[list[float]]]] = [[ring] for ring in outers]
    for hole in holes:
        box = _bbox(hole)
        candidates = [
            index
            for index, outer_box in enumerate(outer_boxes)
            if _bbox_contains(outer_box, box) and _ring_inside(hole, outers[index])
        ]
        if not candidates:
            raise GeometryGateError(
                f"hole at {hole[0]} is not inside any outer ring"
            )
        owner = min(candidates, key=lambda index: outer_areas[index])
        polygons[owner].append(hole)
    return polygons


# ---- Fetch --------------------------------------------------------------


def _get_json(client: httpx.Client, url: str, params: dict[str, str]) -> dict:
    response = client.get(url, params=params)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise RuntimeError(f"ArcGIS error from {url}: {body['error']}")
    return body


def fetch_layer(layer: Layer, *, refresh: bool = False) -> dict[str, Any]:
    """Esri JSON features in EPSG:4326 plus layer metadata, cached on disk."""
    cache = CACHE_DIR / f"{layer.name}.esri.json"
    if cache.exists() and cache.stat().st_size > 0 and not refresh:
        print(f"  using cached {cache}")
        return json.loads(cache.read_text(encoding="utf-8"))
    headers = {"User-Agent": "Mozilla/5.0 (compatible; WildfireServices/1.0)"}
    with httpx.Client(timeout=300.0, follow_redirects=True, headers=headers) as client:
        meta = _get_json(client, layer.url, {"f": "json"})
        features: list[dict] = []
        offset = 0
        while True:
            page = _get_json(
                client,
                f"{layer.url}/query",
                {
                    "where": "1=1",
                    "outFields": "*",
                    "f": "json",
                    "outSR": "4326",
                    "returnGeometry": "true",
                    "resultOffset": str(offset),
                    "resultRecordCount": "1000",
                },
            )
            batch = page.get("features") or []
            features.extend(batch)
            if not page.get("exceededTransferLimit") or not batch:
                break
            offset += len(batch)
    edited_ms = (meta.get("editingInfo") or {}).get("dataLastEditDate")
    payload = {
        "source_url": layer.url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "layer_name": meta.get("name"),
        "native_wkid": (meta.get("extent") or {}).get("spatialReference", {}).get("latestWkid")
        or (meta.get("extent") or {}).get("spatialReference", {}).get("wkid"),
        "data_last_edit": (
            datetime.fromtimestamp(edited_ms / 1000, timezone.utc).isoformat()
            if edited_ms
            else None
        ),
        "features": features,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(payload), encoding="utf-8")
    print(f"  downloaded {len(features)} features from {layer.url}")
    return payload


def area_unit_m2(native_wkid: int | None) -> float:
    if native_wkid not in AREA_UNIT_M2:
        raise GeometryGateError(
            f"publisher layer is in wkid {native_wkid}; its area attribute units "
            "are not known here. Review before loading."
        )
    return AREA_UNIT_M2[native_wkid]


def features_to_rows(payload: dict[str, Any], layer: Layer) -> list[dict[str, Any]]:
    """One row per feature: key, attributes, polygons, and publisher area in m2."""
    unit = area_unit_m2(payload.get("native_wkid"))
    rows = []
    for feature in payload["features"]:
        attrs = feature["attributes"]
        rings = (feature.get("geometry") or {}).get("rings") or []
        overrides = tuple(
            (item.lon, item.lat) for item in layer.ring_overrides
            if item.key == attrs[layer.key_field]
        )
        for item in layer.ring_overrides:
            if item.key == attrs[layer.key_field]:
                print(f"  ring override {item.key} at ({item.lon}, {item.lat}): {item.reason}")
        area_attr = attrs.get(layer.area_field)
        if area_attr is None:
            raise GeometryGateError(
                f"{attrs.get(layer.key_field)} has no {layer.area_field} attribute"
            )
        rows.append(
            {
                "key": attrs[layer.key_field],
                "attributes": attrs,
                "polygons": esri_rings_to_polygons(rings, overrides),
                "publisher_area_m2": float(area_attr) * unit,
            }
        )
    return rows


# ---- PostGIS ------------------------------------------------------------

# Union the per-outer-ring polygons; repair a polygon only if it is invalid.
BUILD_GEOMETRY_SQL = """
WITH parts AS (
  SELECT ST_SetSRID(ST_GeomFromGeoJSON(value::text), 4326) AS g
  FROM jsonb_array_elements(%s::jsonb)
)
SELECT
  ST_Multi(ST_CollectionExtract(ST_Union(
    CASE WHEN ST_IsValid(g) THEN g ELSE ST_MakeValid(g) END
  ), 3)),
  count(*) FILTER (WHERE NOT ST_IsValid(g))
FROM parts
"""


def build_geometry(cur: psycopg.Cursor, polygons: list) -> tuple[bytes, int]:
    """Return (EWKB geometry, number of polygons that needed ST_MakeValid)."""
    coords = [{"type": "Polygon", "coordinates": polygon} for polygon in polygons]
    cur.execute(BUILD_GEOMETRY_SQL, (json.dumps(coords),))
    geom, repaired = cur.fetchone()
    return geom, int(repaired)


def gate_failures(rows: list[dict[str, Any]], tolerance: float = AREA_TOLERANCE) -> list[str]:
    """Rows that fail the load gate. Each row has key, valid, area_m2, publisher_area_m2."""
    failures = []
    for row in rows:
        if not row["valid"]:
            failures.append(f"{row['key']}: geometry is not valid ({row.get('reason')})")
            continue
        publisher = row["publisher_area_m2"]
        if not publisher or publisher <= 0:
            failures.append(f"{row['key']}: publisher area is missing or zero")
            continue
        relative = abs(row["area_m2"] - publisher) / publisher
        if relative > tolerance:
            failures.append(
                f"{row['key']}: area {row['area_m2'] / 1e6:,.1f} km2 differs from the "
                f"publisher's {publisher / 1e6:,.1f} km2 by {relative:.3%} "
                f"(limit {tolerance:.1%})"
            )
    return failures


def check_table(cur: psycopg.Cursor, table: str, key_column: str) -> list[dict[str, Any]]:
    cur.execute(
        f"""
        SELECT {key_column}, ST_IsValid(geom), ST_IsValidReason(geom),
               ST_Area(ST_Transform(geom, 3310)), publisher_area_m2
        FROM {table} ORDER BY 1
        """
    )
    return [
        {
            "key": key,
            "valid": valid,
            "reason": reason,
            "area_m2": float(area),
            "publisher_area_m2": float(publisher) if publisher is not None else None,
        }
        for key, valid, reason, area, publisher in cur.fetchall()
    ]


def enforce_gate(cur: psycopg.Cursor, table: str, key_column: str) -> list[dict[str, Any]]:
    """Raise inside the load transaction so a failed gate rolls the load back."""
    rows = check_table(cur, table, key_column)
    for row in rows:
        diff = (row["area_m2"] - row["publisher_area_m2"]) / row["publisher_area_m2"]
        print(
            f"  gate {row['key']}: valid={row['valid']} "
            f"area={row['area_m2'] / 1e6:,.1f} km2 "
            f"publisher={row['publisher_area_m2'] / 1e6:,.1f} km2 ({diff:+.4%})"
        )
    failures = gate_failures(rows)
    if failures:
        raise GeometryGateError(
            f"{table} failed the geometry gate; nothing was loaded:\n  "
            + "\n  ".join(failures)
        )
    return rows


def dataset_demo_geometries(path: Path, key_field: str) -> dict[str, dict[str, Any]]:
    """The previous, simplified web-map geometry, kept in geom_source for audit."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for feature in data["features"]:
        geom = feature["geometry"]
        if geom["type"] == "Polygon":
            geom = {"type": "MultiPolygon", "coordinates": [geom["coordinates"]]}
        out[str(feature["properties"][key_field])] = {
            "geom": geom,
            "properties": feature["properties"],
        }
    return out
