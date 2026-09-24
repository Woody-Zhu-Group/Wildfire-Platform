"""End-of-load referential health checks."""

from __future__ import annotations

import psycopg

from db.loaders.util import print_step, table_count
from services.shared.dataset_registry import (
    CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM,
    calfire_default_type_sql,
)


def _orphan_circuit_ids(
    conn: psycopg.Connection, from_table: str, column: str = "circuit_id"
) -> list[str]:
    sql = f"""
        SELECT DISTINCT t.{column}
        FROM {from_table} t
        LEFT JOIN wildfire.circuits c ON c.circuit_id = t.{column}
        WHERE c.circuit_id IS NULL
        ORDER BY 1
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [row[0] for row in cur.fetchall()]


def run_validation(conn: psycopg.Connection) -> None:
    print_step("VALIDATION — referential health")

    tables = [
        "wildfire.circuits",
        "wildfire.epss_outages",
        "wildfire.psps_events",
        "wildfire.psps_event_circuits",
        "wildfire.cpuc_ignitions",
        "wildfire.cpuc_ignitions_with_time",
        "wildfire.calfire_incidents",
        "wildfire.hftd_tiers",
        "wildfire.iou_territories",
        "wildfire.counties",
        "wildfire.grid_cells",
    ]
    print("  Table counts:")
    for t in tables:
        print(f"    {t}: {table_count(conn, t)}")

    # Circuit orphans
    epss_orphans = _orphan_circuit_ids(conn, "wildfire.epss_outages")
    psps_orphans = _orphan_circuit_ids(conn, "wildfire.psps_event_circuits")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM wildfire.epss_outages e
            LEFT JOIN wildfire.circuits c ON c.circuit_id = e.circuit_id
            WHERE c.circuit_id IS NULL
            """
        )
        epss_orphan_rows = int(cur.fetchone()[0])
        cur.execute(
            """
            SELECT count(*) FROM wildfire.psps_event_circuits p
            LEFT JOIN wildfire.circuits c ON c.circuit_id = p.circuit_id
            WHERE c.circuit_id IS NULL
            """
        )
        psps_orphan_rows = int(cur.fetchone()[0])

        # PSPS event_name gaps both ways
        cur.execute(
            """
            SELECT count(DISTINCT p.event_name)
            FROM wildfire.psps_event_circuits p
            LEFT JOIN wildfire.psps_events e ON e.event_name = p.event_name
            WHERE e.event_name IS NULL
            """
        )
        pec_missing_events = int(cur.fetchone()[0])
        cur.execute(
            """
            SELECT count(*)
            FROM wildfire.psps_events e
            LEFT JOIN wildfire.psps_event_circuits p ON p.event_name = e.event_name
            WHERE e.utility = 'PGE' AND p.event_name IS NULL
            """
        )
        pge_events_without_circuits = int(cur.fetchone()[0])

        # Circuits never referenced by EPSS
        cur.execute(
            """
            SELECT count(*)
            FROM wildfire.circuits c
            LEFT JOIN wildfire.epss_outages e ON e.circuit_id = c.circuit_id
            WHERE e.id IS NULL
            """
        )
        circuits_without_epss = int(cur.fetchone()[0])

        # CPUC membership gap (approximate via rounded lat/lon/date)
        cur.execute(
            """
            WITH a AS (
              SELECT round(ST_Y(geom)::numeric, 5) AS lat,
                     round(ST_X(geom)::numeric, 5) AS lon,
                     event_date
              FROM wildfire.cpuc_ignitions
            ),
            b AS (
              SELECT round(ST_Y(geom)::numeric, 5) AS lat,
                     round(ST_X(geom)::numeric, 5) AS lon,
                     event_date
              FROM wildfire.cpuc_ignitions_with_time
            )
            SELECT
              (SELECT count(*) FROM a) AS combined_n,
              (SELECT count(*) FROM b) AS timed_n,
              (SELECT count(*) FROM (
                  SELECT lat, lon, event_date FROM a
                  EXCEPT
                  SELECT lat, lon, event_date FROM b
              ) x) AS combined_only,
              (SELECT count(*) FROM (
                  SELECT lat, lon, event_date FROM b
                  EXCEPT
                  SELECT lat, lon, event_date FROM a
              ) y) AS timed_only
            """
        )
        combined_n, timed_n, combined_only, timed_only = cur.fetchone()

        cur.execute(
            """
            SELECT
              count(*) FILTER (WHERE county IS NOT NULL) AS tagged,
              count(*) FILTER (WHERE county IS NULL) AS untagged
            FROM wildfire.cpuc_ignitions
            """
        )
        cpuc_county_tagged, cpuc_county_untagged = cur.fetchone()

        # CAL FIRE quick health. Typed rows outside the registry's default
        # incident types (Wildfire and Fire), the same default every service uses.
        cur.execute(
            f"""
            SELECT
              count(*) FILTER (WHERE utility IS NULL) AS null_utility,
              count(*) FILTER (WHERE date_only_created IS NULL) AS null_created_date,
              count(*) FILTER (WHERE incident_type IS NOT NULL
                                   AND NOT {calfire_default_type_sql("incident_type")})
                AS non_default_typed,
              count(*) FILTER (WHERE incident_type IS NULL) AS null_type
            FROM wildfire.calfire_incidents
            """
        )
        null_util, null_created, non_wf, null_type = cur.fetchone()

        # Invalid circuit_id lengths (should be impossible given CHECKs)
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM wildfire.circuits
                 WHERE length(circuit_id) <> 9) AS bad_circuits,
              (SELECT count(*) FROM wildfire.epss_outages
                 WHERE length(circuit_id) <> 9) AS bad_epss,
              (SELECT count(*) FROM wildfire.psps_event_circuits
                 WHERE length(circuit_id) <> 9) AS bad_psps
            """
        )
        bad_c, bad_e, bad_p = cur.fetchone()

        # Leading-zero share in circuits
        cur.execute(
            """
            SELECT
              count(*) FILTER (WHERE circuit_id LIKE '0%') AS leading_zero,
              count(*) AS total
            FROM wildfire.circuits
            """
        )
        lz, total_c = cur.fetchone()

    print()
    print("  Circuit referential gaps:")
    print(
        f"    epss_outages orphans: {len(epss_orphans)} distinct IDs "
        f"({epss_orphan_rows} rows)"
    )
    if epss_orphans:
        print(f"      sample: {epss_orphans[:15]}")
    print(
        f"    psps_event_circuits orphans: {len(psps_orphans)} distinct IDs "
        f"({psps_orphan_rows} rows)"
    )
    if psps_orphans:
        print(f"      sample: {psps_orphans[:15]}")
    print(f"    circuits with no EPSS outages: {circuits_without_epss}")

    print()
    print("  PSPS event linkage:")
    print(f"    psps_event_circuits rows with unknown event_name: {pec_missing_events}")
    print(f"    PGE psps_events with no circuit list: {pge_events_without_circuits}")

    print()
    print("  CPUC dual-table membership (lat/lon/date rounded to 5 decimals):")
    print(f"    cpuc_ignitions rows: {combined_n}")
    print(f"    cpuc_ignitions_with_time rows: {timed_n}")
    print(f"    combined-only keys: {combined_only}")
    print(f"    timed-only keys: {timed_only}")
    print(
        f"    county inferred: {cpuc_county_tagged} resolved, "
        f"{cpuc_county_untagged} outside all county polygons"
    )

    print()
    print("  CAL FIRE notes:")
    print(f"    null utility: {null_util}")
    print(f"    null date_only_created (includes nulled 1970 sentinels): {null_created}")
    print(
        f"    incident_type outside the {CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM} default: {non_wf}"
    )
    print(f"    null incident_type: {null_type}")

    print()
    print("  Circuit ID integrity:")
    print(f"    bad length counts — circuits/epss/psps: {bad_c}/{bad_e}/{bad_p}")
    pct = (100.0 * lz / total_c) if total_c else 0.0
    print(f"    circuits with leading zero: {lz}/{total_c} ({pct:.1f}%)")

    issues = (
        len(epss_orphans)
        + len(psps_orphans)
        + pec_missing_events
        + bad_c
        + bad_e
        + bad_p
    )
    print()
    if issues == 0:
        print("  RESULT: no orphan circuit/event gaps; CHECK constraints clean.")
    else:
        print(f"  RESULT: {issues} referential/integrity issue group(s) — see warnings above.")
