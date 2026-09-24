"""Load deduped circuit line geometries."""

from __future__ import annotations

import json

import psycopg

from db.loaders.config import Settings
from db.loaders.util import (
    as_multi_linestring_geojson,
    load_geojson,
    normalize_circuit_id,
    print_counts,
    print_step,
    table_count,
    truncate,
)

# The source files are PG&E's EPSS publication, and the table has no utility
# column: every row loaded here is PG&E's. db.loaders.coverage attributes the
# measured rows and dates to this utility.
SOURCE_UTILITY = "PGE"


def load(conn: psycopg.Connection, settings: Settings) -> int:
    path = settings.dataset_demo_data_dir / "epss_circuits.geojson"
    print_step(f"circuits ← {path}")
    data = load_geojson(path)
    features = data["features"]
    print_counts("read", features=len(features))

    seen: dict[str, tuple] = {}
    dupes = 0
    for feat in features:
        props = feat["properties"]
        cid = normalize_circuit_id(props["circuit_id"])
        if cid in seen:
            dupes += 1
            continue
        geom = as_multi_linestring_geojson(feat["geometry"])
        seen[cid] = (
            cid,
            props["circuit_name"],
            props["division"],
            props["substation"],
            json.dumps(geom),
        )

    rows = list(seen.values())
    print_counts("dedupe", unique=len(rows), duplicate_features_dropped=dupes)

    with conn.transaction():
        truncate(conn, "wildfire.circuits")
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO wildfire.circuits
                  (circuit_id, circuit_name, division, substation, geom)
                VALUES (
                  %s, %s, %s, %s,
                  ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                )
                """,
                rows,
            )
        inserted = len(rows)

    final = table_count(conn, "wildfire.circuits")
    print_counts("loaded", inserted=inserted, table_count=final)
    return final
