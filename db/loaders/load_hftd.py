"""Load HFTD Tier 2/3 polygons from CPUC's FeatureServer, with holes kept.

dataset_demo/assets/data/hftd.geojson is a simplified web-map layer whose
holes were written as separate outer polygons. It is kept only in geom_source
for audit; the map still draws from it. See docs/DATA_CHANGE_HFTD_IOU.md.
"""

from __future__ import annotations

import json
from datetime import datetime

import psycopg

from db.loaders.arcgis_polygons import (
    HFTD_LAYER,
    build_geometry,
    dataset_demo_geometries,
    enforce_gate,
    features_to_rows,
    fetch_layer,
)
from db.loaders.config import Settings
from db.loaders.util import print_counts, print_step, table_count, truncate


def load(conn: psycopg.Connection, settings: Settings, *, refresh: bool = False) -> int:
    print_step(f"hftd_tiers <- {HFTD_LAYER.url} (Esri JSON, rings by orientation)")
    print("  NOTE: no CPZ data in dataset_demo (known gap); loading HFTD tiers only.")
    payload = fetch_layer(HFTD_LAYER, refresh=refresh)
    rows = features_to_rows(payload, HFTD_LAYER)
    print_counts("read", features=len(rows))
    previous = dataset_demo_geometries(
        settings.dataset_demo_data_dir / "hftd.geojson", "HFTD"
    )
    edited = payload.get("data_last_edit")

    with conn.transaction():
        truncate(conn, "wildfire.hftd_tiers")
        with conn.cursor() as cur:
            for row in rows:
                attrs = row["attributes"]
                geom, repaired = build_geometry(cur, row["polygons"])
                if repaired:
                    print(f"  {row['key']}: ST_MakeValid repaired {repaired} polygon(s)")
                source = previous.get(str(row["key"]))
                cur.execute(
                    """
                    INSERT INTO wildfire.hftd_tiers
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
            enforce_gate(cur, "wildfire.hftd_tiers", "tier")

    final = table_count(conn, "wildfire.hftd_tiers")
    print_counts("loaded", inserted=len(rows), table_count=final)
    return final
