"""Polygon layers from ArcGIS FeatureServers, with ring roles kept.

The GeoJSON export of an ArcGIS layer (f=geojson) can drop hole roles and
write every ring as its own outer polygon. The HFTD and IOU layers in
dataset_demo were built that way, so holes (towns outside a tier, municipal
utilities inside SCE) were loaded as extra shells. Esri JSON (f=json) keeps
the rings as published: clockwise rings are outer boundaries and
counterclockwise rings are holes.

This module reads Esri JSON (from a cache in data/boundaries, or from the
FeatureServer), assigns each hole to the smallest outer ring that contains
it, and builds one GeoJSON Polygon per outer ring. PostGIS unions the
polygons, so overlapping or nested outer rings count once, and only calls
ST_MakeValid on a polygon that is still invalid. The load gate then requires
every geometry to be valid and its area to match the publisher's area
attribute within 0.1%.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psycopg

from shared.db import REPO_ROOT

CACHE_DIR = REPO_ROOT / "data" / "boundaries"
AREA_TOLERANCE = 0.001  # 0.1% of the publisher's area
SLIVER_M2 = 1.0  # rings smaller than this are reported, not changed

# Square meters per squared unit of the layer's native spatial reference, the
# CRS its Shape__Area attribute is measured in. The area check projects our
# geometry to EPSG:3310 (California Albers), which uses the same Albers
# parameters as both layers below.
_US_SURVEY_FOOT_M = 1200 / 3937
AREA_UNIT_M2 = {
    3310: 1.0,  # NAD83 California Albers, meters
    102599: _US_SURVEY_FOOT_M**2,  # WGS 1984 California Teale Albers, US feet
}
_AUTHALIC_RADIUS_KM = 6371.0072


class GeometryGateError(RuntimeError):
    """A source or rebuilt geometry fails a check; nothing is loaded."""


class SourceUnavailable(GeometryGateError):
    """No usable cache and the publisher could not be reached."""


@dataclass(frozen=True)
class RingOverride:
    """A published hole that the evidence says is part of the area.

    Exactly one counterclockwise ring of feature ``key`` must contain
    (lon, lat) and have a spherical area within ``tolerance`` of
    ``expected_area_km2``. That ring is treated as an outer ring. Anything
    else (no match, several matches, a different area, or no feature with
    this key) stops the load, so no other ring can be flipped.
    """

    key: str
    lon: float
    lat: float
    expected_area_km2: float
    tolerance: float
    reason: str


@dataclass(frozen=True)
class Layer:
    name: str
    url: str
    key_field: str
    expected_keys: frozenset[str]
    area_field: str = "Shape__Area"
    ring_overrides: tuple[RingOverride, ...] = ()

    @property
    def cache_path(self) -> Path:
        return CACHE_DIR / f"{self.name}.esri.json"


HFTD_LAYER = Layer(
    name="cpuc_hftd",
    url=(
        "https://services2.arcgis.com/VofPZYDe2pLxSP5G/ArcGIS/rest/services/"
        "CPUC_High_Fire_Threat_District/FeatureServer/0"
    ),
    key_field="HFTD",
    expected_keys=frozenset({"Tier 2", "Tier 3"}),
)
IOU_LAYER = Layer(
    name="cpuc_iou_service_territories",
    url=(
        "https://services2.arcgis.com/VofPZYDe2pLxSP5G/arcgis/rest/services/"
        "IOU_Service_Territories/FeatureServer/0"
    ),
    key_field="UtilityID",
    expected_keys=frozenset({"PG&E", "SCE", "PacifiCorp", "SDG&E", "LU", "BVES"}),
    ring_overrides=(
        RingOverride(
            key="PG&E",
            lon=-122.0402,
            lat=38.0816,
            # Spherical area; 181.33 km2 in EPSG:3310.
            expected_area_km2=181.22,
            tolerance=0.005,
            reason=(
                "181 km2 ring over Suisun Marsh and Grizzly Island is marked as a "
                "hole in the Esri JSON, but the layer's own Shape__Area counts it, "
                "and it holds 23 PG&E EPSS outages and 1 PG&E circuit. Checked "
                "2026-09-23 against layer data edited 2026-01-09."
            ),
        ),
    ),
)
LAYERS = (IOU_LAYER, HFTD_LAYER)


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


def ring_area_km2(ring: list[list[float]]) -> float:
    """Area of a lon/lat ring on the authalic sphere, in km2."""
    total = 0.0
    closed = ring if ring[0] == ring[-1] else ring + [ring[0]]
    for (l1, p1), (l2, p2) in zip(closed, closed[1:]):
        total += math.radians(l2 - l1) * (
            2 + math.sin(math.radians(p1)) + math.sin(math.radians(p2))
        )
    return abs(total) * _AUTHALIC_RADIUS_KM**2 / 2


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


def _ring_inside(inner: list[list[float]], outer: list[list[float]]) -> bool:
    """A ring is inside another when most of its sampled vertices are.

    Several vertices are tested because a hole can touch its outer boundary.
    """
    step = max(1, (len(inner) - 1) // 7)
    samples = inner[: len(inner) - 1 : step][:7] or inner[:1]
    hits = sum(point_in_ring(pt[0], pt[1], outer) for pt in samples)
    return hits * 2 > len(samples)


@dataclass
class RingReport:
    """What happened to one feature's rings, printed by the loader."""

    rings: int = 0
    dropped_short: int = 0
    slivers: int = 0
    nested_outers: int = 0
    overridden: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.rings} rings"]
        if self.dropped_short:
            parts.append(f"{self.dropped_short} dropped (fewer than 4 points)")
        if self.slivers:
            parts.append(f"{self.slivers} slivers under {SLIVER_M2:g} m2 kept")
        if self.nested_outers:
            parts.append(f"{self.nested_outers} outer rings nested in another outer (unioned)")
        if self.overridden:
            parts.append("override: " + "; ".join(self.overridden))
        return ", ".join(parts)


def esri_rings_to_polygons(
    rings: list[list[list[float]]],
    overrides: tuple[RingOverride, ...] = (),
) -> tuple[list[list[list[list[float]]]], RingReport]:
    """Group Esri rings into GeoJSON Polygon coordinate arrays.

    - Rings with fewer than 4 points are dropped and counted.
    - A ring with exactly zero area raises.
    - Each hole goes to the smallest outer ring containing it. A hole with no
      containing outer ring raises.
    - An outer ring nested directly inside another outer ring (not inside one
      of its holes) is kept as its own polygon and counted. PostGIS unions it
      with its container, which matches Esri's nonzero winding: the nested
      area counts once, and a hole inside only the nested ring is covered by
      the container.
    - Each override must match exactly one hole by point and area; that hole
      becomes an outer ring.
    """
    report = RingReport(rings=len(rings))
    usable = []
    for ring in rings:
        if len(ring) < 4:
            report.dropped_short += 1
            continue
        if signed_area(ring) == 0.0:
            raise GeometryGateError(f"ring at {ring[0]} has zero area")
        if ring_area_km2(ring) * 1e6 < SLIVER_M2:
            report.slivers += 1
        usable.append(ring)
    outers = [ring for ring in usable if is_outer_ring(ring)]
    holes = [ring for ring in usable if not is_outer_ring(ring)]

    for item in overrides:
        matched = [
            hole
            for hole in holes
            if point_in_ring(item.lon, item.lat, hole)
            and abs(ring_area_km2(hole) - item.expected_area_km2)
            <= item.tolerance * item.expected_area_km2
        ]
        if len(matched) != 1:
            raise GeometryGateError(
                f"ring override for {item.key} at ({item.lon}, {item.lat}) with "
                f"{item.expected_area_km2} km2 matched {len(matched)} holes, expected 1"
            )
        holes = [hole for hole in holes if hole is not matched[0]]
        outers.append(list(reversed(matched[0])))
        report.overridden.append(
            f"{item.key} hole of {ring_area_km2(matched[0]):,.2f} km2 at "
            f"({item.lon}, {item.lat}) treated as area"
        )

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
            raise GeometryGateError(f"hole at {hole[0]} is not inside any outer ring")
        owner = min(candidates, key=lambda index: outer_areas[index])
        polygons[owner].append(hole)

    # Nested outers: inside another outer ring and not inside any of its holes.
    for index, outer in enumerate(outers):
        for other, container in enumerate(polygons):
            if other == index or outer_areas[other] <= outer_areas[index]:
                continue
            if not _bbox_contains(outer_boxes[other], outer_boxes[index]):
                continue
            if _ring_inside(outer, container[0]) and not any(
                _ring_inside(outer, hole) for hole in container[1:]
            ):
                report.nested_outers += 1
                break
    return polygons, report


# ---- Sources ------------------------------------------------------------


def _get_json(client: httpx.Client, url: str, params: dict[str, str]) -> dict:
    response = client.get(url, params=params)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise RuntimeError(f"ArcGIS error from {url}: {body['error']}")
    return body


def fetch_layer(layer: Layer, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Download Esri JSON features in EPSG:4326 plus layer metadata, and cache it."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; WildfireServices/1.0)"}
    with httpx.Client(
        timeout=300.0, follow_redirects=True, headers=headers, transport=transport
    ) as client:
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
    extent_sr = (meta.get("extent") or {}).get("spatialReference") or {}
    edited_ms = (meta.get("editingInfo") or {}).get("dataLastEditDate")
    payload = {
        "source_url": layer.url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "layer_name": meta.get("name"),
        "native_wkid": extent_sr.get("latestWkid") or extent_sr.get("wkid"),
        "data_last_edit": (
            datetime.fromtimestamp(edited_ms / 1000, timezone.utc).isoformat()
            if edited_ms
            else None
        ),
        "features": features,
    }
    check_complete(payload, layer)
    layer.cache_path.parent.mkdir(parents=True, exist_ok=True)
    layer.cache_path.write_text(json.dumps(payload), encoding="utf-8")
    print(f"  downloaded {len(features)} features from {layer.url} to {layer.cache_path}")
    return payload


def load_source(
    layer: Layer, *, refresh: bool = False, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """The layer's Esri JSON from cache, or from the publisher when needed.

    Raises SourceUnavailable with the fix when there is no usable cache and
    the publisher cannot be reached. Called before any table is touched.
    """
    cache = layer.cache_path
    if not refresh and cache.exists() and cache.stat().st_size > 0:
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceUnavailable(f"cache {cache} is not valid JSON: {exc}") from exc
        check_complete(payload, layer)
        print(f"  using cached {cache} (fetched {payload.get('fetched_at')})")
        return payload
    try:
        return fetch_layer(layer, transport=transport)
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, GeometryGateError):
            raise
        reason = "refresh requested" if refresh else f"no cache at {cache}"
        raise SourceUnavailable(
            f"{layer.name}: {reason}, and {layer.url} could not be reached "
            f"({type(exc).__name__}: {exc}). Seed the cache on a machine with "
            "network access (python -m db.loaders.rebuild_boundaries --fetch-only) "
            f"and copy {cache.name} into {cache.parent}."
        ) from exc


def check_complete(payload: dict[str, Any], layer: Layer) -> None:
    """The feature set must be exactly the expected keys, once each."""
    keys = [
        str((feature.get("attributes") or {}).get(layer.key_field))
        for feature in payload.get("features") or []
    ]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    missing = sorted(layer.expected_keys - set(keys))
    extra = sorted(set(keys) - layer.expected_keys)
    if duplicates or missing or extra:
        raise GeometryGateError(
            f"{layer.name} features are not the expected set: missing {missing}, "
            f"unexpected {extra}, duplicated {duplicates}"
        )


def area_unit_m2(native_wkid: int | None) -> float:
    if native_wkid not in AREA_UNIT_M2:
        raise GeometryGateError(
            f"publisher layer is in wkid {native_wkid}; its area attribute units "
            "are not known here. Review before loading."
        )
    return AREA_UNIT_M2[native_wkid]


def features_to_rows(payload: dict[str, Any], layer: Layer) -> list[dict[str, Any]]:
    """One row per feature: key, attributes, polygons, report, publisher area in m2."""
    check_complete(payload, layer)
    unit = area_unit_m2(payload.get("native_wkid"))
    override_keys = {item.key for item in layer.ring_overrides}
    unknown = sorted(override_keys - layer.expected_keys)
    if unknown:
        raise GeometryGateError(f"ring overrides name features that do not exist: {unknown}")
    rows = []
    for feature in payload["features"]:
        attrs = feature["attributes"]
        key = str(attrs[layer.key_field])
        rings = (feature.get("geometry") or {}).get("rings") or []
        area_attr = attrs.get(layer.area_field)
        if area_attr is None:
            raise GeometryGateError(f"{key} has no {layer.area_field} attribute")
        overrides = tuple(item for item in layer.ring_overrides if item.key == key)
        polygons, report = esri_rings_to_polygons(rings, overrides)
        rows.append(
            {
                "key": key,
                "attributes": attrs,
                "polygons": polygons,
                "report": report,
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
        publisher = row["publisher_area_m2"]
        diff = (row["area_m2"] - publisher) / publisher if publisher else float("nan")
        print(
            f"  gate {row['key']}: valid={row['valid']} "
            f"area={row['area_m2'] / 1e6:,.2f} km2 "
            f"publisher={(publisher or 0) / 1e6:,.2f} km2 ({diff:+.4%})"
        )
    failures = gate_failures(rows)
    if failures:
        raise GeometryGateError(
            f"{table} failed the geometry gate; nothing was loaded:\n  "
            + "\n  ".join(failures)
        )
    return rows


def dataset_demo_geometries(path: Path, key_field: str) -> dict[str, dict[str, Any]]:
    """The previous, simplified web-map geometry, kept in geom_source for audit.

    Returns {} when dataset_demo is absent (EC2), so geom_source is NULL there.
    """
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
