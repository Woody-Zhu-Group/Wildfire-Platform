"""IOU service territory polygons from CPUC's FeatureServer, with holes kept.

The publisher is CPUC's IOU_Service_Territories layer
(IOU_Service_Territory_20240812). Its Esri JSON keeps hole roles, so SCE's
municipal-utility holes (Anaheim, Riverside, Colton, Banning, Azusa) and
the BVES area (Big Bear Lake) stay holes. Loaded together with HFTD in one
transaction by db/loaders/load_boundaries.py. dataset_demo/assets/data/
iou_territories.geojson is the old simplified geometry, kept only in
geom_source for audit when present. See docs/DATA_CHANGE_HFTD_IOU.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import psycopg

from db.loaders.arcgis_polygons import (
    IOU_LAYER,
    build_geometry,
    dataset_demo_geometries,
    features_to_rows,
    load_source,
)
from db.loaders.config import Settings
from services.shared.dataset_registry import IOU_PUBLISHER_UTILITY_CODES

TABLE = "iou_territories"
KEY_COLUMN = "utility"

# Publisher UtilityID to the warehouse utility code the services use.
UTILITY_CODES = IOU_PUBLISHER_UTILITY_CODES
if set(UTILITY_CODES) != IOU_LAYER.expected_keys:
    raise RuntimeError("UTILITY_CODES and IOU_LAYER.expected_keys disagree")


def prepare(
    settings: Settings, *, refresh: bool = False, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """Source, completeness, and rings. Touches no table."""
    payload = load_source(IOU_LAYER, refresh=refresh, transport=transport)
    rows = features_to_rows(payload, IOU_LAYER)
    for row in rows:
        print(f"  {UTILITY_CODES[row['key']]}: {row['report'].summary()}")
    return {
        "payload": payload,
        "rows": rows,
        "previous": dataset_demo_geometries(
            settings.dataset_demo_data_dir / "iou_territories.geojson", "utility"
        ),
    }


def insert(cur: psycopg.Cursor, prepared: dict[str, Any], *, schema: str = "wildfire") -> None:
    payload = prepared["payload"]
    edited = payload.get("data_last_edit")
    for row in prepared["rows"]:
        code = UTILITY_CODES[row["key"]]
        geom, repaired = build_geometry(cur, row["polygons"])
        if repaired:
            print(f"  {code}: ST_MakeValid repaired {repaired} polygon(s)")
        source = prepared["previous"].get(code)
        cur.execute(
            f"""
            INSERT INTO {schema}.{TABLE}
              (utility, utility_name, geom, geom_source,
               publisher_area_m2, source_url, source_edited_at)
            VALUES (
              %s, %s, %s,
              ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
              %s, %s, %s
            )
            """,
            (
                code,
                row["attributes"]["Name"],
                geom,
                json.dumps(source["geom"]) if source else None,
                row["publisher_area_m2"],
                payload["source_url"],
                datetime.fromisoformat(edited) if edited else None,
            ),
        )
