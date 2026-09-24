"""SQL aggregates for comparison metrics."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row

from services.shared.calfire_county import (
    county_match_sql,
    county_overlap_sql,
    multi_county_sql,
)
from services.shared.dataset_registry import (
    calfire_default_type_sql,
    covered_utilities,
    dataset_coverage_gap,
)

ScopeKind = Literal["utility", "county", "hftd"]


def _calfire_type_sql(alias: str = "c") -> str:
    return calfire_default_type_sql(f"{alias}.incident_type")


def territory_km2(conn: psycopg.Connection, utility: str) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ST_Area(geom::geography) / 1e6
            FROM wildfire.iou_territories WHERE utility = %s
            """,
            (utility,),
        )
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None


def hftd_km2(conn: psycopg.Connection, tier: str) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ST_Area(geom::geography) / 1e6
            FROM wildfire.hftd_tiers WHERE tier = %s
            """,
            (tier,),
        )
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None


def circuit_count_utility_attribute(conn: psycopg.Connection, utility: str) -> int | None:
    """The circuits inventory gives a denominator only for the utilities it has rows for."""
    if utility not in covered_utilities("circuits"):
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM wildfire.circuits")
        return int(cur.fetchone()[0])


def circuit_count_spatial(
    conn: psycopg.Connection,
    *,
    kind: Literal["utility", "hftd"],
    region_id: str,
) -> int | None:
    if kind == "utility":
        region_sql = "SELECT geom FROM wildfire.iou_territories WHERE utility = %s"
    else:
        region_sql = "SELECT geom FROM wildfire.hftd_tiers WHERE tier = %s"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH region AS ({region_sql})
            SELECT count(*) FROM wildfire.circuits c, region r
            WHERE c.geom IS NOT NULL AND ST_Intersects(c.geom, r.geom)
            """,
            (region_id,),
        )
        return int(cur.fetchone()[0])


def ignition_count(
    conn: psycopg.Connection,
    *,
    scope: ScopeKind,
    scope_id: str,
    start: date,
    end: date,
    definition: str,
) -> tuple[int | None, str | None]:
    if scope == "county":
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM wildfire.cpuc_ignitions
                WHERE lower(county) = lower(%s) AND event_date BETWEEN %s AND %s
                """,
                (scope_id, start, end),
            )
            return int(cur.fetchone()[0]), None
    with conn.cursor() as cur:
        if scope == "utility" and definition == "attribute":
            cur.execute(
                """
                SELECT count(*) FROM wildfire.cpuc_ignitions
                WHERE utility = %s AND event_date BETWEEN %s AND %s
                """,
                (scope_id, start, end),
            )
            return int(cur.fetchone()[0]), None
        if scope == "utility":
            region_sql = "SELECT geom FROM wildfire.iou_territories WHERE utility = %s"
        else:
            region_sql = "SELECT geom FROM wildfire.hftd_tiers WHERE tier = %s"
        cur.execute(
            f"""
            WITH region AS ({region_sql})
            SELECT count(*) FROM wildfire.cpuc_ignitions i, region r
            WHERE ST_Within(i.geom, r.geom)
              AND i.event_date BETWEEN %s AND %s
            """,
            (scope_id, start, end),
        )
        return int(cur.fetchone()[0]), None


def epss_outage_count(
    conn: psycopg.Connection,
    *,
    scope: ScopeKind,
    scope_id: str,
    start: date,
    end: date,
) -> tuple[int | None, str | None]:
    with conn.cursor() as cur:
        if scope == "utility":
            cur.execute(
                """
                SELECT count(*) FROM wildfire.epss_outages
                WHERE start_date BETWEEN %s AND %s
                """,
                (start, end),
            )
            return int(cur.fetchone()[0]), None
        if scope == "county":
            cur.execute(
                """
                SELECT count(*) FROM wildfire.epss_outages
                WHERE lower(county) = lower(%s)
                  AND start_date BETWEEN %s AND %s
                """,
                (scope_id, start, end),
            )
            return int(cur.fetchone()[0]), None
        # HFTD spatial
        cur.execute(
            """
            WITH region AS (SELECT geom FROM wildfire.hftd_tiers WHERE tier = %s)
            SELECT count(*) FROM wildfire.epss_outages e, region r
            WHERE ST_Within(e.geom, r.geom)
              AND e.start_date BETWEEN %s AND %s
            """,
            (scope_id, start, end),
        )
        return int(cur.fetchone()[0]), None


def calfire_incident_count(
    conn: psycopg.Connection,
    *,
    scope: ScopeKind,
    scope_id: str,
    start: date,
    end: date,
    definition: str,
) -> tuple[int | None, str | None]:
    with conn.cursor() as cur:
        if scope == "county":
            cur.execute(
                f"""
                SELECT count(*) FROM wildfire.calfire_incidents c
                WHERE {county_match_sql("c.county")}
                  AND c.date_only_created BETWEEN %s AND %s
                  AND {_calfire_type_sql()}
                """,
                (scope_id, start, end),
            )
            return int(cur.fetchone()[0]), None
        if scope == "utility" and definition == "attribute":
            cur.execute(
                f"""
                SELECT count(*) FROM wildfire.calfire_incidents c
                WHERE c.utility = %s
                  AND c.date_only_created BETWEEN %s AND %s
                  AND {_calfire_type_sql()}
                """,
                (scope_id, start, end),
            )
            return int(cur.fetchone()[0]), None
        if scope == "utility":
            region_sql = "SELECT geom FROM wildfire.iou_territories WHERE utility = %s"
        else:
            region_sql = "SELECT geom FROM wildfire.hftd_tiers WHERE tier = %s"
        cur.execute(
            f"""
            WITH region AS ({region_sql})
            SELECT count(*) FROM wildfire.calfire_incidents c, region r
            WHERE ST_Within(c.geom, r.geom)
              AND c.date_only_created BETWEEN %s AND %s
              AND {_calfire_type_sql()}
            """,
            (scope_id, start, end),
        )
        return int(cur.fetchone()[0]), None


def calfire_multi_county_count(
    conn: psycopg.Connection,
    *,
    counties: list[str],
    ranges: list[tuple[date, date]],
) -> int:
    """Distinct default-type CAL FIRE incidents that list several counties,
    list at least one of ``counties``, and fall in any of ``ranges``."""
    if not counties or not ranges:
        return 0
    date_sql = " OR ".join("c.date_only_created BETWEEN %s AND %s" for _ in ranges)
    params: list[Any] = [[name.lower() for name in counties]]
    for start, end in ranges:
        params.extend([start, end])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*) FROM wildfire.calfire_incidents c
            WHERE {multi_county_sql("c.county")}
              AND {county_overlap_sql("c.county")}
              AND ({date_sql})
              AND {_calfire_type_sql()}
            """,
            params,
        )
        return int(cur.fetchone()[0])


def acres_burned(
    conn: psycopg.Connection,
    *,
    scope: ScopeKind,
    scope_id: str,
    start: date,
    end: date,
    definition: str,
) -> tuple[float | None, str | None]:
    with conn.cursor() as cur:
        if scope == "county":
            cur.execute(
                f"""
                SELECT COALESCE(SUM(c.acres_burned), 0) FROM wildfire.calfire_incidents c
                WHERE {county_match_sql("c.county")}
                  AND c.date_only_created BETWEEN %s AND %s
                  AND {_calfire_type_sql()}
                """,
                (scope_id, start, end),
            )
            return float(cur.fetchone()[0]), None
        if scope == "utility" and definition == "attribute":
            cur.execute(
                f"""
                SELECT COALESCE(SUM(c.acres_burned), 0) FROM wildfire.calfire_incidents c
                WHERE c.utility = %s
                  AND c.date_only_created BETWEEN %s AND %s
                  AND {_calfire_type_sql()}
                """,
                (scope_id, start, end),
            )
            return float(cur.fetchone()[0]), None
        if scope == "utility":
            region_sql = "SELECT geom FROM wildfire.iou_territories WHERE utility = %s"
        else:
            region_sql = "SELECT geom FROM wildfire.hftd_tiers WHERE tier = %s"
        cur.execute(
            f"""
            WITH region AS ({region_sql})
            SELECT COALESCE(SUM(c.acres_burned), 0)
            FROM wildfire.calfire_incidents c, region r
            WHERE ST_Within(c.geom, r.geom)
              AND c.date_only_created BETWEEN %s AND %s
              AND {_calfire_type_sql()}
            """,
            (scope_id, start, end),
        )
        return float(cur.fetchone()[0]), None


def psps_event_count(
    conn: psycopg.Connection,
    *,
    scope: ScopeKind,
    scope_id: str,
    start: date,
    end: date,
) -> tuple[int | None, str | None]:
    if scope == "county":
        return None, "PSPS events have no county column"
    with conn.cursor() as cur:
        if scope == "utility":
            cur.execute(
                """
                SELECT count(*) FROM wildfire.psps_events
                WHERE utility = %s
                  AND deenergization_start_date BETWEEN %s AND %s
                """,
                (scope_id, start, end),
            )
            return int(cur.fetchone()[0]), None
        cur.execute(
            """
            WITH region AS (SELECT geom FROM wildfire.hftd_tiers WHERE tier = %s)
            SELECT count(*) FROM wildfire.psps_events p, region r
            WHERE ST_Intersects(p.geom, r.geom)
              AND p.deenergization_start_date BETWEEN %s AND %s
            """,
            (scope_id, start, end),
        )
        return int(cur.fetchone()[0]), None


def customers_deenergized(
    conn: psycopg.Connection,
    *,
    scope: ScopeKind,
    scope_id: str,
    start: date,
    end: date,
) -> tuple[int | None, str | None]:
    if scope == "county":
        return None, "PSPS events have no county column"
    with conn.cursor() as cur:
        if scope == "utility":
            cur.execute(
                """
                SELECT COALESCE(SUM(customers_deenergized), 0) FROM wildfire.psps_events
                WHERE utility = %s
                  AND deenergization_start_date BETWEEN %s AND %s
                """,
                (scope_id, start, end),
            )
            return int(cur.fetchone()[0]), None
        cur.execute(
            """
            WITH region AS (SELECT geom FROM wildfire.hftd_tiers WHERE tier = %s)
            SELECT COALESCE(SUM(p.customers_deenergized), 0)
            FROM wildfire.psps_events p, region r
            WHERE ST_Intersects(p.geom, r.geom)
              AND p.deenergization_start_date BETWEEN %s AND %s
            """,
            (scope_id, start, end),
        )
        return int(cur.fetchone()[0]), None


def raw_metric(
    conn: psycopg.Connection,
    metric: str,
    *,
    scope: ScopeKind,
    scope_id: str,
    start: date,
    end: date,
    ignition_definition: str,
) -> tuple[float | int | None, str | None]:
    from services.comparison.metrics import (
        METRIC_DATASETS,
        REASON_COMPONENT_NULL,
        REASON_ZERO_IGNITIONS,
    )

    # A value outside measured coverage (a utility or a period the dataset
    # has no rows for) is null with its reason, never a zero. A ratio needs
    # both of its datasets covered.
    datasets = (
        ("epss_outages", "cpuc_ignitions")
        if metric == "epss_to_ignition_ratio"
        else (METRIC_DATASETS[metric],)
    )
    for dataset in datasets:
        gap = coverage_gap(dataset, scope=scope, scope_id=scope_id, start=start, end=end)
        if gap is not None:
            return None, gap["reason"]

    if metric == "ignition_count":
        return ignition_count(
            conn,
            scope=scope,
            scope_id=scope_id,
            start=start,
            end=end,
            definition=ignition_definition,
        )
    if metric == "epss_outage_count":
        return epss_outage_count(
            conn, scope=scope, scope_id=scope_id, start=start, end=end
        )
    if metric == "calfire_incident_count":
        return calfire_incident_count(
            conn,
            scope=scope,
            scope_id=scope_id,
            start=start,
            end=end,
            definition=ignition_definition,
        )
    if metric == "acres_burned":
        return acres_burned(
            conn,
            scope=scope,
            scope_id=scope_id,
            start=start,
            end=end,
            definition=ignition_definition,
        )
    if metric == "psps_event_count":
        return psps_event_count(
            conn, scope=scope, scope_id=scope_id, start=start, end=end
        )
    if metric == "customers_deenergized":
        return customers_deenergized(
            conn, scope=scope, scope_id=scope_id, start=start, end=end
        )
    if metric == "epss_to_ignition_ratio":
        epss, epss_reason = epss_outage_count(
            conn, scope=scope, scope_id=scope_id, start=start, end=end
        )
        ign, ign_reason = ignition_count(
            conn,
            scope=scope,
            scope_id=scope_id,
            start=start,
            end=end,
            definition=ignition_definition,
        )
        if epss is None:
            return None, epss_reason or REASON_COMPONENT_NULL
        if ign is None:
            return None, ign_reason or REASON_COMPONENT_NULL
        if ign == 0:
            return None, REASON_ZERO_IGNITIONS
        return float(epss) / float(ign), None
    raise ValueError(metric)


def coverage_gap(
    dataset: str,
    *,
    scope: ScopeKind,
    scope_id: str,
    start: date,
    end: date,
) -> dict[str, Any] | None:
    """The measured coverage gap for one comparison value, or None when covered."""
    utilities = [scope_id] if scope == "utility" else []
    return dataset_coverage_gap(dataset, utilities, start, end)


def normalization_denominator(
    conn: psycopg.Connection,
    *,
    scope: ScopeKind,
    scope_id: str,
    normalize: str,
) -> tuple[float | int | None, str | None]:
    from services.comparison.metrics import (
        REASON_CIRCUITS_SCOPE,
        REASON_NO_COUNTY_AREA,
    )

    if normalize == "none":
        return None, None
    if normalize == "per_km2":
        if scope == "county":
            return None, REASON_NO_COUNTY_AREA
        if scope == "utility":
            km2 = territory_km2(conn, scope_id)
            return km2, None if km2 is not None else "Utility territory not found"
        km2 = hftd_km2(conn, scope_id)
        return km2, None if km2 is not None else "HFTD tier not found"
    # per_circuit
    if scope == "county":
        return None, REASON_CIRCUITS_SCOPE
    if scope == "utility":
        n = circuit_count_utility_attribute(conn, scope_id)
        if n is None:
            return None, REASON_CIRCUITS_SCOPE
        return n, None
    n = circuit_count_spatial(conn, kind="hftd", region_id=scope_id)
    return n, None


def utility_exists(conn: psycopg.Connection, utility: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM wildfire.iou_territories WHERE utility = %s",
            (utility,),
        )
        return cur.fetchone() is not None
