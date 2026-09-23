"""Reload only the IOU and HFTD polygons, in one transaction, behind the gate.

    python -m db.loaders.rebuild_boundaries --fetch-only  # seed data/boundaries/*.esri.json
    python -m db.loaders.rebuild_boundaries               # load from the cache
    python -m db.loaders.rebuild_boundaries --refresh     # re-download, then load

Sources are read and checked before connecting to the database, so a
missing cache with no network changes nothing. The schema is applied first
(idempotent; adds the geom_source and publisher columns on an existing
database). If any check fails, both tables keep their previous rows.
"""

from __future__ import annotations

import argparse
import sys

from db.loaders import load_boundaries
from db.loaders.arcgis_polygons import GeometryGateError
from db.loaders.util import apply_schema, print_step
from shared.db import connect, get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download from CPUC")
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="download and check the sources into data/boundaries, then stop",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        prepared = load_boundaries.prepare(
            settings, refresh=args.refresh or args.fetch_only
        )
    except GeometryGateError as exc:
        print(f"ERROR: {exc}")
        return 1
    if args.fetch_only:
        print("Sources cached and checked; no database changes made.")
        return 0

    print_step("Rebuild HFTD and IOU polygons")
    print(f"  DSN {settings.safe_target} user={settings.user}")
    conn = connect(settings, autocommit=True)
    try:
        apply_schema(conn, settings.schema_sql)
        try:
            load_boundaries.apply(conn, prepared)
        except GeometryGateError as exc:
            print(f"ERROR: {exc}")
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
