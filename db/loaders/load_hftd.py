"""HFTD Tier 2/3 polygons from CPUC's FeatureServer, with holes kept.

Loaded together with IOU territories in one transaction by
db/loaders/load_boundaries.py. dataset_demo/assets/data/hftd.geojson is the
old simplified geometry, kept only in geom_source for audit when present.
See docs/DATA_CHANGE_HFTD_IOU.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import psycopg

from db.loaders.arcgis_polygons import (
    HFTD_LAYER,
    build_geometry,
    dataset_demo_geometries,
    features_to_rows,
    load_source,
)
from db.loaders.config import Settings

TABLE = "hftd_tiers"
KEY_COLUMN = "tier"


def prepare(
    settings: Settings, *, refresh: bool = False, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """Source, completeness, and rings. Touches no table."""
    payload = load_source(HFTD_LAYER, refresh=refresh, transport=transport)
    rows = features_to_rows(payload, HFTD_LAYER)
    for row in rows:
        print(f"  {row['key']}: {row['report'].summary()}")
    return {
        "payload": payload,
        "rows": rows,
        "previous": dataset_demo_geometries(
            settings.dataset_demo_data_dir / "hftd.geojson", "HFTD"
        ),
    }


def insert(cur: psycopg.Cursor, prepared: dict[str, Any], *, schema: str = "wildfire") -> None:
    payload = prepared["payload"]
    edited = payload.get("data_last_edit")
    for row in prepared["rows"]:
        attrs = row["attributes"]
        geom, repaired = build_geometry(cur, row["polygons"])
        if repaired:
            print(f"  {row['key']}: ST_MakeValid repaired {repaired} polygon(s)")
        source = prepared["previous"].get(row["key"])
        cur.execute(
            f"""
            INSERT INTO {schema}.{TABLE}
              (tier, objectid, shape_length, shape_area, geom, geom_source,
               publisher_area_m2, source_url, source_edited_at)
            VALUES (
              %s, %s, %s, %s, %s,
              ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
              %s, %s, %s
            )
            """,
            (
                row["key"],
                attrs.get("OBJECTID"),
                attrs.get("Shape__Length") or attrs.get("Shape_Leng"),
                attrs.get("Shape__Area"),
                geom,
                json.dumps(source["geom"]) if source else None,
                row["publisher_area_m2"],
                payload["source_url"],
                datetime.fromisoformat(edited) if edited else None,
            ),
        )
