"""Orchestrate schema apply + all table loads + validation summary."""

from __future__ import annotations

import sys

from db.loaders import (
    load_boundaries,
    load_calfire,
    load_circuits,
    load_counties,
    load_cpuc,
    load_epss,
    load_grid,
    load_psps,
    load_us_ignitions,
)
from db.loaders.arcgis_polygons import GeometryGateError
from db.loaders.util import apply_schema, print_step
from db.loaders.validate import run_validation
from shared.db import connect, get_settings


def main() -> int:
    settings = get_settings()
    print_step("Wildfire PostGIS load")
    print(f"  DSN {settings.safe_target} user={settings.user}")
    print(f"  dataset_demo data: {settings.dataset_demo_data_dir}")
    print(f"  risk grid data:    {settings.risk_forecasting_data_dir}")

    if not settings.dataset_demo_data_dir.is_dir():
        print(f"ERROR: DATASET_DEMO_DATA_DIR not found: {settings.dataset_demo_data_dir}")
        return 1

    try:
        # autocommit so each loader's transaction() commits for real (otherwise
        # nested savepoints can roll back on connection close).
        conn = connect(settings, autocommit=True)
    except Exception as exc:  # noqa: BLE001 — surface connection errors clearly
        print(f"ERROR: could not connect to Postgres: {exc}")
        print("  Hint: docker compose up -d  (wait for healthy)")
        return 1

    try:
        print_step("Apply schema")
        apply_schema(conn, settings.schema_sql)

        counts: dict[str, int] = {}
        # IOU and HFTD load together from CPUC sources. A missing cache with no
        # network, or a failed gate, leaves both tables as they were and does
        # not stop the other tables from loading.
        boundary_error: str | None = None
        try:
            counts.update(load_boundaries.load(conn, settings))
        except GeometryGateError as exc:
            boundary_error = str(exc)
            print(f"  ERROR boundaries not loaded, previous rows kept: {exc}")
        counts["counties"] = load_counties.load(conn, settings)
        counts["circuits"] = load_circuits.load(conn, settings)
        counts["grid_cells"] = load_grid.load(conn, settings)
        counts["cpuc_ignitions"] = load_cpuc.load_combined(conn, settings)
        counts["cpuc_ignitions_with_time"] = load_cpuc.load_with_time(conn, settings)
        counts["calfire_incidents"] = load_calfire.load(conn, settings)
        counts["epss_outages"] = load_epss.load(conn, settings)
        counts["psps_events"] = load_psps.load_events(conn, settings)
        counts["psps_event_circuits"] = load_psps.load_event_circuits(conn, settings)
        try:
            counts["us_ignitions"] = load_us_ignitions.load(conn, settings)
        except FileNotFoundError as exc:
            print(f"  SKIP us_ignitions: {exc}")

        run_validation(conn)

        print_step("LOAD COMPLETE — row counts")
        for name, n in counts.items():
            print(f"  {name}: {n}")
        if boundary_error:
            print(
                "ERROR: iou_territories and hftd_tiers were not reloaded. "
                "See docs/DATA_CHANGE_HFTD_IOU.md, Deploy notes."
            )
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
