"""Load IOU territories and HFTD tiers together, in one transaction.

Order of work:
1. Read both sources (cache or publisher) and check that each has exactly
   the expected features. A missing cache with no network stops here, before
   any table is touched.
2. Build the rings for every feature (no database needed).
3. In one transaction: truncate both tables, insert both, and run the
   validity and area gate on both. Any failure rolls back both tables, so
   new IOU rows never sit beside old HFTD rows.

TRUNCATE takes an ACCESS EXCLUSIVE lock, so queries that read either table
wait until the transaction commits (4.0 seconds on the local warehouse,
measured 2026-09-23).
"""

from __future__ import annotations

import httpx
import psycopg

from db.loaders import load_hftd, load_iou
from db.loaders.arcgis_polygons import enforce_gate
from db.loaders.config import Settings
from db.loaders.util import print_counts, print_step, table_count


def prepare(
    settings: Settings, *, refresh: bool = False, transport: httpx.BaseTransport | None = None
) -> dict[str, dict]:
    print_step("Boundaries: read sources and build rings (no table touched yet)")
    return {
        "iou": load_iou.prepare(settings, refresh=refresh, transport=transport),
        "hftd": load_hftd.prepare(settings, refresh=refresh, transport=transport),
    }


def apply(
    conn: psycopg.Connection, prepared: dict[str, dict], *, schema: str = "wildfire"
) -> dict[str, int]:
    print_step(f"Boundaries: replace {schema}.{load_iou.TABLE} and {schema}.{load_hftd.TABLE}")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                f"TRUNCATE TABLE {schema}.{load_iou.TABLE}, {schema}.{load_hftd.TABLE}"
            )
            load_iou.insert(cur, prepared["iou"], schema=schema)
            load_hftd.insert(cur, prepared["hftd"], schema=schema)
            enforce_gate(cur, f"{schema}.{load_iou.TABLE}", load_iou.KEY_COLUMN)
            enforce_gate(cur, f"{schema}.{load_hftd.TABLE}", load_hftd.KEY_COLUMN)
    counts = {
        load_iou.TABLE: table_count(conn, f"{schema}.{load_iou.TABLE}"),
        load_hftd.TABLE: table_count(conn, f"{schema}.{load_hftd.TABLE}"),
    }
    print_counts("loaded", **counts)
    return counts


def load(
    conn: psycopg.Connection,
    settings: Settings,
    *,
    refresh: bool = False,
    schema: str = "wildfire",
) -> dict[str, int]:
    return apply(conn, prepare(settings, refresh=refresh), schema=schema)
