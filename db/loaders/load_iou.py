"""Load IOU service territory polygons from CPUC's FeatureServer, with holes kept.

The publisher is CPUC's IOU_Service_Territories layer
(IOU_Service_Territory_20240812). Its Esri JSON keeps hole roles, so SCE's
municipal-utility holes (Anaheim, Riverside, Colton, Banning, Azusa) and
the BVES area (Big Bear Lake) stay holes. dataset_demo/assets/data/
iou_territories.geojson is a simplified web-map layer kept only in
geom_source for audit. See docs/DATA_CHANGE_HFTD_IOU.md.
"""

from __future__ import annotations

import json
from datetime import datetime

import psycopg

from db.loaders.arcgis_polygons import (
    IOU_LAYER,
    GeometryGateError,
    build_geometry,
    dataset_demo_geometries,
    enforce_gate,
    features_to_rows,
    fetch_layer,
)
from db.loaders.config import Settings
from db.loaders.util import print_counts, print_step, table_count, truncate

# Publisher UtilityID to the warehouse utility code the services use.
UTILITY_CODES = {
    "PG&E": "PGE",
    "SCE": "SCE",
    "PacifiCorp": "PACIFICORP",
    "SDG&E": "SDGE",
    "LU": "Liberty",
    "BVES": "BVES",
}


def load(conn: psycopg.Connection, settings: Settings, *, refresh: bool = False) -> int:
    print_step(f"iou_territories <- {IOU_LAYER.url} (Esri JSON, rings by orientation)")
    payload = fetch_layer(IOU_LAYER, refresh=refresh)
    rows = features_to_rows(payload, IOU_LAYER)
    print_counts("read", features=len(rows))
    unknown = sorted(str(row["key"]) for row in rows if row["key"] not in UTILITY_CODES)
    if unknown:
        raise GeometryGateError(f"unknown publisher UtilityID values: {unknown}")
    previous = dataset_demo_geometries(
        settings.dataset_demo_data_dir / "iou_territories.geojson", "utility"
    )
    edited = payload.get("data_last_edit")

    with conn.transaction():
        truncate(conn, "wildfire.iou_territories")
        with conn.cursor() as cur:
            for row in rows:
                code = UTILITY_CODES[row["key"]]
                geom, repaired = build_geometry(cur, row["polygons"])
                if repaired:
                    print(f"  {code}: ST_MakeValid repaired {repaired} polygon(s)")
                source = previous.get(code)
                cur.execute(
                    """
                    INSERT INTO wildfire.iou_territories
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
            enforce_gate(cur, "wildfire.iou_territories", "utility")

    final = table_count(conn, "wildfire.iou_territories")
    print_counts("loaded", inserted=len(rows), table_count=final)
    return final
