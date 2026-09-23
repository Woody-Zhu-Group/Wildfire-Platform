"""Reload only the HFTD and IOU polygons, with the geometry gate.

    python -m db.loaders.rebuild_boundaries            # use the cached Esri JSON if present
    python -m db.loaders.rebuild_boundaries --refresh  # fetch fresh from CPUC

Applies db/schema.sql first (idempotent; adds the geom_source and publisher
columns on an existing database). Each table loads in its own transaction, so
a table that fails the gate keeps its previous rows.
"""

from __future__ import annotations

import argparse
import sys

from db.loaders import load_hftd, load_iou
from db.loaders.arcgis_polygons import GeometryGateError
from db.loaders.util import apply_schema, print_step
from shared.db import connect, get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download from CPUC")
    args = parser.parse_args(argv)
    settings = get_settings()
    print_step("Rebuild HFTD and IOU polygons")
    print(f"  DSN {settings.safe_target} user={settings.user}")
    conn = connect(settings, autocommit=True)
    try:
        apply_schema(conn, settings.schema_sql)
        try:
            load_iou.load(conn, settings, refresh=args.refresh)
            load_hftd.load(conn, settings, refresh=args.refresh)
        except GeometryGateError as exc:
            print(f"ERROR: {exc}")
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
