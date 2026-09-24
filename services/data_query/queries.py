"""SQL query helpers for the data query service."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from services.shared.calfire_county import (
    MULTI_COUNTY_NOTE,
    county_group_join_sql,
    county_match_sql,
    multi_county_count_sql,
    multi_county_meta,
)
from services.shared.epss_causes import cause_display_sql, cause_filter_sql, cause_variants
from services.shared.dataset_registry import (
    ALLOWED_RANK_PAIRS,
    CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM,
    GROUP_BY_FIELDS,
    GROUPED_DATASETS,
    MISSING_LABEL_RANK,
    NOT_RECORDED,
    SUMMARY_METRIC_IDS,
    UTILITY_DISPLAY_LABELS,
    WORKSPACE_UTILITIES,
    calfire_default_type_sql,
    group_code_and_label,
)

_CALFIRE_DEFAULT_TYPE_SQL = calfire_default_type_sql("c.incident_type")


def _fetch_page(
    conn: psycopg.Connection,
    select_sql: str,
    count_sql: str,
    params: list[Any],
    *,
    limit: int | None,
    offset: int | None,
) -> tuple[list[dict[str, Any]], int]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(count_sql, params)
        total = int(cur.fetchone()["count"])
        if limit is not None:
            page_sql = select_sql + " LIMIT %s OFFSET %s"
            cur.execute(page_sql, params + [limit, offset or 0])
        else:
            cur.execute(select_sql, params)
        rows = list(cur.fetchall())
    return rows, total


def _bbox_clause(alias: str, bbox: tuple[float, float, float, float] | None, params: list) -> str:
    if bbox is None:
        return ""
    min_lon, min_lat, max_lon, max_lat = bbox
    params.extend([min_lon, min_lat, max_lon, max_lat])
    return f" AND {alias}.geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"


def table_counts(conn: psycopg.Connection) -> dict[str, int]:
    tables = [
        "circuits",
        "epss_outages",
        "psps_events",
        "psps_event_circuits",
        "cpuc_ignitions",
        "calfire_incidents",
        "hftd_tiers",
        "iou_territories",
        "counties",
        "grid_cells",
    ]
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for t in tables:
            cur.execute(f"SELECT count(*) FROM wildfire.{t}")
            out[t] = int(cur.fetchone()[0])
    return out


def null_utility_count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM wildfire.calfire_incidents WHERE utility IS NULL"
        )
        return int(cur.fetchone()[0])


def null_incident_type_count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM wildfire.calfire_incidents WHERE incident_type IS NULL"
        )
        return int(cur.fetchone()[0])


# ---- Ignitions (CPUC combined) ----

def query_ignitions(
    conn: psycopg.Connection,
    *,
    utility: str | None,
    include_untagged: bool,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    county: str | None,
    bbox: tuple[float, float, float, float] | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    where = ["TRUE"]
    params: list[Any] = []
    if utility == "untagged":
        where.append("i.utility IS NULL")
    elif utility is not None:
        if include_untagged:
            where.append("(i.utility = %s OR i.utility IS NULL)")
            params.append(utility)
        else:
            where.append("i.utility = %s")
            params.append(utility)
    if year is not None:
        where.append("i.year = %s")
        params.append(year)
    if start_date is not None:
        where.append("i.event_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("i.event_date <= %s")
        params.append(end_date)
    if county is not None:
        where.append("lower(i.county) = lower(%s)")
        params.append(county)
    where_sql = " AND ".join(where) + _bbox_clause("i", bbox, params)

    select_sql = f"""
        SELECT i.id, i.utility, i.event_date, i.year, i.source_file, i.county,
               ST_Y(i.geom)::double precision AS latitude,
               ST_X(i.geom)::double precision AS longitude,
               ST_AsGeoJSON(i.geom) AS _geom_geojson
        FROM wildfire.cpuc_ignitions i
        WHERE {where_sql}
        ORDER BY i.event_date, i.id
    """
    count_sql = f"SELECT count(*) AS count FROM wildfire.cpuc_ignitions i WHERE {where_sql}"
    return _fetch_page(conn, select_sql, count_sql, params, limit=limit, offset=offset)


def query_us_ignitions(
    conn: psycopg.Connection,
    *,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    bbox: tuple[float, float, float, float] | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    where = ["TRUE"]
    params: list[Any] = []
    if year is not None:
        where.append("u.year = %s")
        params.append(year)
    if start_date is not None:
        where.append("u.event_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("u.event_date <= %s")
        params.append(end_date)
    where_sql = " AND ".join(where) + _bbox_clause("u", bbox, params)
    select_sql = f"""
        SELECT u.id, u.event_date, u.year, u.latitude, u.longitude,
               u.pr, u.rmax, u.rmin, u.sph, u.srad, u.tmmn, u.tmmx, u.vs,
               u.bi, u.fm100, u.fm1000, u.erc, u.etr, u.pet, u.vpd,
               ST_AsGeoJSON(u.geom) AS _geom_geojson
        FROM wildfire.us_ignitions u
        WHERE {where_sql}
        ORDER BY u.event_date, u.id
    """
    count_sql = f"SELECT count(*) AS count FROM wildfire.us_ignitions u WHERE {where_sql}"
    return _fetch_page(conn, select_sql, count_sql, params, limit=limit, offset=offset)


# ---- EPSS ----

def query_epss(
    conn: psycopg.Connection,
    *,
    circuit_id: str | None,
    utility: str | None,
    county: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    outage_type: str | None,
    cause: str | None,
    bbox: tuple[float, float, float, float] | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    notes: dict[str, Any] = {"dataset_utility": "PGE"}
    # EPSS is PGE-only. Other utilities → empty.
    if utility is not None and utility not in ("PGE", "untagged"):
        notes["empty_reason"] = f"EPSS outages are PG&E-only; utility={utility} matches nothing"
        return [], 0, notes
    if utility == "untagged":
        notes["empty_reason"] = "EPSS rows always have implicit utility PGE; untagged matches nothing"
        return [], 0, notes

    where = ["TRUE"]
    params: list[Any] = []
    if circuit_id is not None:
        where.append("e.circuit_id = %s")
        params.append(circuit_id)
    if county is not None:
        where.append("lower(e.county) = lower(%s)")
        params.append(county)
    if year is not None:
        where.append("e.year = %s")
        params.append(year)
    if start_date is not None:
        where.append("e.start_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("e.start_date <= %s")
        params.append(end_date)
    if outage_type is not None:
        where.append("e.outage_type = %s")
        params.append(outage_type)
    if cause is not None:
        where.append(cause_filter_sql("e.cause"))
        params.append(cause_variants(cause))
    where_sql = " AND ".join(where) + _bbox_clause("e", bbox, params)

    select_sql = f"""
        SELECT e.id, e.circuit_id, e.circuit, e.year, e.start_date, e.end_date,
               e.county, {cause_display_sql("e.cause")} AS cause, e.outage_type, e.division,
               e.customer_minutes, e.restoration_min,
               e.medical_baseline, e.life_support, e.schools, e.hospitals,
               ST_AsGeoJSON(e.geom) AS _geom_geojson
        FROM wildfire.epss_outages e
        WHERE {where_sql}
        ORDER BY e.start_date, e.id
    """
    count_sql = f"SELECT count(*) AS count FROM wildfire.epss_outages e WHERE {where_sql}"
    rows, total = _fetch_page(conn, select_sql, count_sql, params, limit=limit, offset=offset)
    return rows, total, notes


# ---- PSPS ----

def query_psps_events(
    conn: psycopg.Connection,
    *,
    utility: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    where = ["TRUE"]
    params: list[Any] = []
    if utility == "untagged":
        where.append("FALSE")  # all events have utility
    elif utility is not None:
        where.append("p.utility = %s")
        params.append(utility)
    if year is not None:
        where.append("p.year = %s")
        params.append(year)
    if start_date is not None:
        where.append("p.deenergization_start_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("p.deenergization_start_date <= %s")
        params.append(end_date)
    where_sql = " AND ".join(where)

    select_sql = f"""
        SELECT p.event_name, p.utility, p.iou_raw, p.first_date_of_poc,
               p.deenergization_start_date, p.full_restoration_date,
               p.de_energization, p.customers_deenergized, p.year,
               ST_AsGeoJSON(p.geom) AS _geom_geojson
        FROM wildfire.psps_events p
        WHERE {where_sql}
        ORDER BY p.deenergization_start_date NULLS LAST, p.event_name
    """
    count_sql = f"SELECT count(*) AS count FROM wildfire.psps_events p WHERE {where_sql}"
    return _fetch_page(conn, select_sql, count_sql, params, limit=limit, offset=offset)


def query_psps_event_circuits(
    conn: psycopg.Connection, event_name: str
) -> list[dict[str, Any]] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT 1 FROM wildfire.psps_events WHERE event_name = %s",
            (event_name,),
        )
        if cur.fetchone() is None:
            return None
        cur.execute(
            """
            SELECT pec.event_name, pec.circuit_id, pec.circuit_name,
                   (c.circuit_id IS NULL) AS geometry_missing,
                   ST_AsGeoJSON(c.geom) AS _geom_geojson
            FROM wildfire.psps_event_circuits pec
            LEFT JOIN wildfire.circuits c ON c.circuit_id = pec.circuit_id
            WHERE pec.event_name = %s
            ORDER BY pec.circuit_id
            """,
            (event_name,),
        )
        return list(cur.fetchall())


# ---- CAL FIRE ----

def query_calfire(
    conn: psycopg.Connection,
    *,
    utility: str | None,
    include_untagged: bool,
    county: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    min_acres: float | None,
    incident_type: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    where = ["TRUE"]
    params: list[Any] = []
    type_mode = "default_wildfire"

    if incident_type is None or incident_type.strip() == "":
        where.append(_CALFIRE_DEFAULT_TYPE_SQL)
        type_mode = "default_wildfire"
    elif incident_type.strip().lower() == "all":
        type_mode = "all"
    elif incident_type.strip().lower() == "untyped":
        where.append("c.incident_type IS NULL")
        type_mode = "untyped"
    else:
        where.append("c.incident_type = %s")
        params.append(incident_type.strip())
        type_mode = "explicit"

    if utility == "untagged":
        where.append("c.utility IS NULL")
    elif utility is not None:
        if include_untagged:
            where.append("(c.utility = %s OR c.utility IS NULL)")
            params.append(utility)
        else:
            where.append("c.utility = %s")
            params.append(utility)

    if county is not None:
        where.append(county_match_sql("c.county"))
        params.append(county)
    if year is not None:
        where.append("EXTRACT(YEAR FROM c.date_only_created) = %s")
        params.append(year)
    if start_date is not None:
        where.append("c.date_only_created >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("c.date_only_created <= %s")
        params.append(end_date)
    if min_acres is not None:
        where.append("c.acres_burned >= %s")
        params.append(min_acres)

    where_sql = " AND ".join(where)
    select_sql = f"""
        SELECT c.incident_id, c.incident_name, c.incident_type, c.acres_burned,
               c.containment, c.county, c.location, c.utility,
               c.date_only_created, c.date_created, c.is_final, c.is_active,
               c.is_calfire_incident, c.incident_url,
               ST_AsGeoJSON(c.geom) AS _geom_geojson
        FROM wildfire.calfire_incidents c
        WHERE {where_sql}
        ORDER BY c.date_only_created NULLS LAST, c.incident_id
    """
    count_sql = (
        f"SELECT count(*) AS count FROM wildfire.calfire_incidents c WHERE {where_sql}"
    )
    rows, total = _fetch_page(conn, select_sql, count_sql, params, limit=limit, offset=offset)
    extra = {
        "incident_type_mode": type_mode,
        "null_incident_type_count": null_incident_type_count(conn),
        "null_utility_records_in_table": null_utility_count(conn),
    }
    if county is not None:
        extra.update(_calfire_multi_county_meta(conn, where_sql, params))
    return rows, total, extra


def _calfire_multi_county_meta(
    conn: psycopg.Connection, where_sql: str, params: list[Any]
) -> dict[str, Any]:
    """Multi-county meta for a county-scoped CAL FIRE result (alias c)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {multi_county_count_sql('c.county')} "
            f"FROM wildfire.calfire_incidents c WHERE {where_sql}",
            params,
        )
        count = int(cur.fetchone()[0] or 0)
    return multi_county_meta(count)


# ---- Circuits / HFTD / IOU ----

def query_circuits(
    conn: psycopg.Connection,
    *,
    circuit_id: str | None,
    division: str | None,
    substation: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    where = ["TRUE"]
    params: list[Any] = []
    if circuit_id is not None:
        where.append("c.circuit_id = %s")
        params.append(circuit_id)
    if division is not None:
        where.append("lower(c.division) = lower(%s)")
        params.append(division)
    if substation is not None:
        where.append("lower(c.substation) = lower(%s)")
        params.append(substation)
    where_sql = " AND ".join(where)
    select_sql = f"""
        SELECT c.circuit_id, c.circuit_name, c.division, c.substation,
               ST_AsGeoJSON(c.geom) AS _geom_geojson
        FROM wildfire.circuits c
        WHERE {where_sql}
        ORDER BY c.circuit_id
    """
    count_sql = f"SELECT count(*) AS count FROM wildfire.circuits c WHERE {where_sql}"
    return _fetch_page(conn, select_sql, count_sql, params, limit=limit, offset=offset)


def get_circuit(conn: psycopg.Connection, circuit_id: str) -> dict[str, Any] | None:
    rows, _ = query_circuits(
        conn, circuit_id=circuit_id, division=None, substation=None, limit=1, offset=0
    )
    return rows[0] if rows else None


# /hftd and /iou-territories return the full stored geometry by default: the
# same polygons every count and point answer uses, so a downloaded boundary
# matches the numbers. A client that only draws can pass `simplify` (degrees)
# for a smaller payload; the map layers use 0.001.
SIMPLIFY_MAX_DEGREES = 0.01


def _boundary_geojson(alias: str, simplify: float | None) -> tuple[str, tuple]:
    if simplify is None:
        return f"ST_AsGeoJSON({alias}.geom)", ()
    return (
        f"ST_AsGeoJSON(ST_Multi(ST_SimplifyPreserveTopology({alias}.geom, %s)), 5)",
        (simplify,),
    )


def query_hftd(
    conn: psycopg.Connection, *, tier: str | None, simplify: float | None = None
) -> list[dict[str, Any]]:
    geom_sql, geom_params = _boundary_geojson("h", simplify)
    where, where_params = ("WHERE h.tier = %s", (tier,)) if tier is not None else ("", ())
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT h.tier, h.objectid, h.shape_length, h.shape_area,
                   {geom_sql} AS _geom_geojson
            FROM wildfire.hftd_tiers h
            {where}
            ORDER BY h.tier
            """,
            geom_params + where_params,
        )
        return list(cur.fetchall())


def query_iou(
    conn: psycopg.Connection, *, utility: str | None, simplify: float | None = None
) -> list[dict[str, Any]]:
    geom_sql, geom_params = _boundary_geojson("i", simplify)
    where, where_params = (
        ("WHERE i.utility = %s", (utility,)) if utility is not None else ("", ())
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT i.utility, i.utility_name,
                   {geom_sql} AS _geom_geojson
            FROM wildfire.iou_territories i
            {where}
            ORDER BY i.utility
            """,
            geom_params + where_params,
        )
        return list(cur.fetchall())


# ---- Spatial ----

# Shoreline snap (opt in, used for Census city center points). Some city
# points sit just off the mapped coastline of a layer. Measured on the 483
# incorporated places: every IOU miss on a shoreline is at most 41.8 m (Morro
# Bay), while the nearest city truly outside every IOU is 265.8 m away (Los
# Angeles, LADWP). The county layer is 1:500k Census cartographic boundaries,
# generalized at the coast; its shoreline misses reach 142 m (Coronado).
SHORE_SNAP_IOU_M = 50.0
SHORE_SNAP_COUNTY_M = 150.0


def _shore_snap(cur, lat: float, lon: float, *, iou_missing: bool, county_missing: bool) -> dict:
    """Nearest IOU and county for a point that no polygon contains.

    Snaps only when exactly one polygon of that layer is within the distance.
    An IOU point inside an outer boundary but in a hole (a municipal utility
    such as Anaheim) is never snapped. HFTD tiers and grid cells are never
    snapped: a tier edge is a regulatory boundary, and a missing grid cell
    means the model has no cell there.
    """
    snapped: dict[str, Any] = {}
    point = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"
    if iou_missing:
        cur.execute(
            f"""
            WITH pt AS (SELECT {point} AS g)
            SELECT
              (SELECT bool_or(ST_Contains(ST_MakePolygon(ST_ExteriorRing(d.geom)), pt.g))
                 FROM wildfire.iou_territories i, ST_Dump(i.geom) d) AS in_hole,
              (SELECT json_agg(json_build_object(
                         'utility', i.utility, 'utility_name', i.utility_name,
                         'distance_m', ST_Distance(i.geom::geography, pt.g::geography)))
                 FROM wildfire.iou_territories i
                 WHERE ST_DWithin(i.geom::geography, pt.g::geography, %s)) AS near
            FROM pt
            """,
            (lon, lat, SHORE_SNAP_IOU_M),
        )
        row = cur.fetchone()
        near = row["near"] or []
        if not row["in_hole"] and len(near) == 1:
            snapped["iou"] = near[0]
    if county_missing:
        cur.execute(
            f"""
            WITH pt AS (SELECT {point} AS g)
            SELECT json_agg(json_build_object(
                     'county', c.name,
                     'distance_m', ST_Distance(c.geom::geography, pt.g::geography))) AS near
            FROM wildfire.counties c, pt
            WHERE ST_DWithin(c.geom::geography, pt.g::geography, %s)
            """,
            (lon, lat, SHORE_SNAP_COUNTY_M),
        )
        near = cur.fetchone()["near"] or []
        if len(near) == 1:
            snapped["county"] = near[0]
    return snapped


def spatial_point(
    conn: psycopg.Connection, lat: float, lon: float, *, snap_shoreline: bool = False
) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              (SELECT i.utility FROM wildfire.iou_territories i
                 WHERE ST_Contains(i.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                 LIMIT 1) AS iou_utility,
              (SELECT i.utility_name FROM wildfire.iou_territories i
                 WHERE ST_Contains(i.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                 LIMIT 1) AS iou_utility_name,
              (SELECT h.tier FROM wildfire.hftd_tiers h
                 WHERE ST_Contains(h.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                 LIMIT 1) AS hftd_tier,
              (SELECT g.cell_id FROM wildfire.grid_cells g
                 WHERE ST_Contains(g.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                 LIMIT 1) AS grid_cell_id,
              (SELECT g.row FROM wildfire.grid_cells g
                 WHERE ST_Contains(g.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                 LIMIT 1) AS grid_row,
              (SELECT g.col FROM wildfire.grid_cells g
                 WHERE ST_Contains(g.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                 LIMIT 1) AS grid_col,
              (SELECT c.name FROM wildfire.counties c
                 WHERE ST_Covers(c.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                 ORDER BY c.geoid
                 LIMIT 1) AS county_name
            """,
            (lon, lat, lon, lat, lon, lat, lon, lat, lon, lat, lon, lat, lon, lat),
        )
        row = cur.fetchone() or {}
        snapped: dict[str, Any] = {}
        if snap_shoreline and (row.get("iou_utility") is None or row.get("county_name") is None):
            snapped = _shore_snap(
                cur, lat, lon,
                iou_missing=row.get("iou_utility") is None,
                county_missing=row.get("county_name") is None,
            )
    if "iou" in snapped:
        row["iou_utility"] = snapped["iou"]["utility"]
        row["iou_utility_name"] = snapped["iou"]["utility_name"]
    if "county" in snapped:
        row["county_name"] = snapped["county"]["county"]
    county_name = row.get("county_name")
    meta: dict[str, Any] = {
        "county_unavailable": False,
        "county_source": "census_tiger_pip",
    }
    if snap_shoreline:
        meta["shoreline_snap"] = {
            "iou_limit_m": SHORE_SNAP_IOU_M,
            "county_limit_m": SHORE_SNAP_COUNTY_M,
            "snapped": {
                layer: round(float(value["distance_m"]), 1) for layer, value in snapped.items()
            },
        }
    return {
        "lat": lat,
        "lon": lon,
        "iou": {
            "utility": row.get("iou_utility"),
            "utility_name": row.get("iou_utility_name"),
        },
        "hftd_tier": row.get("hftd_tier"),
        "grid_cell": {
            "cell_id": row.get("grid_cell_id"),
            "row": row.get("grid_row"),
            "col": row.get("grid_col"),
        },
        "county": county_name,
        "meta": meta,
    }


def spatial_summary(
    conn: psycopg.Connection,
    *,
    utility: str | None,
    hftd_tier: str | None,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    if (utility is None) == (hftd_tier is None):
        raise ValueError("Provide exactly one of utility or hftd_tier")

    if utility is not None:
        region_kind = "utility"
        region_id = utility
        region_sql = "SELECT geom FROM wildfire.iou_territories WHERE utility = %s"
        region_param: Any = utility
    else:
        region_kind = "hftd_tier"
        region_id = hftd_tier
        region_sql = "SELECT geom FROM wildfire.hftd_tiers WHERE tier = %s"
        region_param = hftd_tier

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(region_sql, (region_param,))
        if cur.fetchone() is None:
            raise KeyError(f"unknown region {region_kind}={region_id}")

        cur.execute(
            f"""
            WITH region AS (
              {region_sql}
            )
            SELECT
              (SELECT count(*) FROM wildfire.cpuc_ignitions i, region r
                 WHERE ST_Within(i.geom, r.geom)
                   AND i.event_date BETWEEN %s AND %s) AS ignitions,
              (SELECT count(*) FROM wildfire.epss_outages e, region r
                 WHERE ST_Within(e.geom, r.geom)
                   AND e.start_date BETWEEN %s AND %s) AS epss_outages,
              (SELECT count(*) FROM wildfire.calfire_incidents c, region r
                 WHERE ST_Within(c.geom, r.geom)
                   AND c.date_only_created BETWEEN %s AND %s
                   AND {_CALFIRE_DEFAULT_TYPE_SQL}) AS calfire_incidents
            """,
            (
                region_param,
                start_date,
                end_date,
                start_date,
                end_date,
                start_date,
                end_date,
            ),
        )
        counts = cur.fetchone() or {}

    return {
        "region": {"kind": region_kind, "id": region_id},
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "counts": {
            "ignitions": int(counts.get("ignitions") or 0),
            "epss_outages": int(counts.get("epss_outages") or 0),
            "calfire_incidents": int(counts.get("calfire_incidents") or 0),
        },
        "meta": {
            "calfire_incident_type_default": CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM,
            "calfire_counts_use_spatial_containment": True,
            "null_incident_type_count": null_incident_type_count(conn),
            "null_utility_records_in_table": null_utility_count(conn),
        },
    }


# ---- Ranking (single-dataset GROUP BY / top-N) ----

RANK_HARD_CAP = 25
_UNKNOWN_GROUP = MISSING_LABEL_RANK


class RankQueryError(ValueError):
    """Invalid ranking request; the route converts this to HTTP 400."""


def query_rank(
    conn: psycopg.Connection,
    *,
    dataset: str,
    group_by: str,
    metric: str,
    utility: str | None,
    include_untagged: bool,
    county: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    incident_type: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return ordered {group_value, metric_value} rows plus ranking meta.

    Ties at the requested cutoff are included (DENSE_RANK). If that would
    exceed RANK_HARD_CAP, extra tied rows are dropped and ties_cut is set.
    """
    pair = (dataset, group_by, metric)
    if pair not in ALLOWED_RANK_PAIRS:
        raise RankQueryError(
            f"ranking is not available for dataset={dataset!r} "
            f"group_by={group_by!r} metric={metric!r}"
        )
    if group_by == "county" and county is not None:
        raise RankQueryError(
            "county filter cannot be combined with group_by=county"
        )
    if limit < 1 or limit > RANK_HARD_CAP:
        raise RankQueryError(f"limit must be between 1 and {RANK_HARD_CAP}")

    extra: dict[str, Any] = {}
    if dataset == "cpuc_ignitions":
        select_sql, count_sql, params = _rank_cpuc_sql(
            group_by=group_by,
            utility=utility,
            include_untagged=include_untagged,
            year=year,
            start_date=start_date,
            end_date=end_date,
            county=county,
        )
    elif dataset == "calfire_incidents":
        select_sql, count_sql, params, extra = _rank_calfire_sql(
            conn,
            metric=metric,
            utility=utility,
            include_untagged=include_untagged,
            year=year,
            start_date=start_date,
            end_date=end_date,
            incident_type=incident_type,
        )
        extra["null_incident_type_count"] = null_incident_type_count(conn)
        extra["null_utility_records_in_table"] = null_utility_count(conn)
    else:
        empty_notes = _epss_rank_empty(utility)
        if empty_notes is not None:
            return [], {
                "total": 0,
                "returned": 0,
                "limit": limit,
                "dataset": dataset,
                "group_by": group_by,
                "metric": metric,
                "tie_extended": False,
                "ties_cut": False,
                "empty_reason": empty_notes,
                **extra,
            }
        select_sql, count_sql, params = _rank_epss_sql(
            year=year,
            start_date=start_date,
            end_date=end_date,
            county=county,
        )

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(count_sql, params)
        total_groups = int(cur.fetchone()["count"])
        cur.execute(select_sql, params)
        ranked = list(cur.fetchall())

    if total_groups == 0:
        empty_reason = "No rows matched these filters, so there is no ranking."
        if dataset == "us_ignitions":
            empty_reason = (
                "No rows matched these filters. The us_ignitions table is empty "
                "or has no rankable state attribute."
            )
        return [], {
            "total": 0,
            "returned": 0,
            "limit": limit,
            "dataset": dataset,
            "group_by": group_by,
            "metric": metric,
            "tie_extended": False,
            "ties_cut": False,
            "empty_reason": empty_reason,
            **extra,
        }

    ties_cut = False
    if ranked:
        cutoff_index = min(limit, len(ranked)) - 1
        cutoff_value = ranked[cutoff_index]["metric_value"]
        ranked = [row for row in ranked if row["metric_value"] >= cutoff_value]
    if len(ranked) > RANK_HARD_CAP:
        hard_value = ranked[RANK_HARD_CAP - 1]["metric_value"]
        ties_cut = any(
            row["metric_value"] == hard_value for row in ranked[RANK_HARD_CAP:]
        )
        ranked = ranked[:RANK_HARD_CAP]

    rows = []
    for row in ranked:
        item = {
            "group_value": row["group_value"],
            "metric_value": _json_number(row["metric_value"]),
            **group_code_and_label(group_by, str(row["group_value"])),
        }
        if dataset == "epss_outages":
            item["division"] = row.get("division")
            item["circuit_name"] = row.get("circuit_name")
        rows.append(item)

    return rows, {
        "total": total_groups,
        "returned": len(rows),
        "limit": limit,
        "dataset": dataset,
        "group_by": group_by,
        "metric": metric,
        "tie_extended": len(rows) > limit,
        "ties_cut": ties_cut,
        "empty_reason": None,
        **extra,
    }


def _json_number(value: Any) -> int | float:
    if value is None:
        return 0
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if hasattr(value, "as_integer_ratio") and float(value).is_integer():
        return int(value)
    return int(value) if isinstance(value, int) else float(value)


def _rank_cpuc_sql(
    *,
    group_by: str,
    utility: str | None,
    include_untagged: bool,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    county: str | None,
) -> tuple[str, str, list[Any]]:
    where = ["TRUE"]
    params: list[Any] = []
    if utility == "untagged":
        where.append("i.utility IS NULL")
    elif utility is not None:
        if include_untagged:
            where.append("(i.utility = %s OR i.utility IS NULL)")
            params.append(utility)
        else:
            where.append("i.utility = %s")
            params.append(utility)
    if year is not None:
        where.append("i.year = %s")
        params.append(year)
    if start_date is not None:
        where.append("i.event_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("i.event_date <= %s")
        params.append(end_date)
    if county is not None:
        where.append("lower(i.county) = lower(%s)")
        params.append(county)
    where_sql = " AND ".join(where)
    group_expr = (
        f"COALESCE(NULLIF(TRIM(i.{group_by}), ''), '{_UNKNOWN_GROUP}')"
    )
    groups_sql = f"""
        SELECT {group_expr} AS group_value, COUNT(*)::bigint AS metric_value
        FROM wildfire.cpuc_ignitions i
        WHERE {where_sql}
        GROUP BY 1
    """
    return _rank_wrap_sql(groups_sql, extra_cols=(), params=params)


def _rank_calfire_sql(
    conn: psycopg.Connection,
    *,
    metric: str,
    utility: str | None,
    include_untagged: bool,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    incident_type: str | None,
) -> tuple[str, str, list[Any], dict[str, Any]]:
    where = ["TRUE"]
    params: list[Any] = []
    type_mode = "default_wildfire"
    if incident_type is None or incident_type.strip() == "":
        where.append(_CALFIRE_DEFAULT_TYPE_SQL)
    elif incident_type.strip().lower() == "all":
        type_mode = "all"
    elif incident_type.strip().lower() == "untyped":
        where.append("c.incident_type IS NULL")
        type_mode = "untyped"
    else:
        where.append("c.incident_type = %s")
        params.append(incident_type.strip())
        type_mode = "explicit"
    if utility == "untagged":
        where.append("c.utility IS NULL")
    elif utility is not None:
        if include_untagged:
            where.append("(c.utility = %s OR c.utility IS NULL)")
            params.append(utility)
        else:
            where.append("c.utility = %s")
            params.append(utility)
    if year is not None:
        where.append("EXTRACT(YEAR FROM c.date_only_created) = %s")
        params.append(year)
    if start_date is not None:
        where.append("c.date_only_created >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("c.date_only_created <= %s")
        params.append(end_date)
    where_sql = " AND ".join(where)
    agg = (
        "COALESCE(SUM(c.acres_burned), 0)"
        if metric == "acres_burned"
        else "COUNT(*)::bigint"
    )
    # CAL FIRE ranks only by county. An incident that lists several counties
    # is counted, with its full acreage, in each one (see calfire_county).
    groups_sql = f"""
        SELECT cty.county AS group_value, {agg} AS metric_value
        FROM wildfire.calfire_incidents c
        {county_group_join_sql("c.county", unknown=_UNKNOWN_GROUP)}
        WHERE {where_sql}
        GROUP BY 1
    """
    extra = {
        "incident_type_mode": type_mode,
        **_calfire_multi_county_meta(conn, where_sql, params),
    }
    select_sql, count_sql, params = _rank_wrap_sql(groups_sql, extra_cols=(), params=params)
    return select_sql, count_sql, params, extra


def _epss_rank_empty(utility: str | None) -> str | None:
    if utility is not None and utility not in ("PGE", "untagged"):
        return f"EPSS outages are PG&E-only; utility={utility} matches nothing"
    if utility == "untagged":
        return "EPSS rows always have implicit utility PGE; untagged matches nothing"
    return None


def _rank_epss_sql(
    *,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    county: str | None,
) -> tuple[str, str, list[Any]]:
    where = ["TRUE"]
    params: list[Any] = []
    if county is not None:
        where.append("lower(e.county) = lower(%s)")
        params.append(county)
    if year is not None:
        where.append("e.year = %s")
        params.append(year)
    if start_date is not None:
        where.append("e.start_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("e.start_date <= %s")
        params.append(end_date)
    where_sql = " AND ".join(where)
    groups_sql = f"""
        SELECT e.circuit_id AS group_value,
               COUNT(*)::bigint AS metric_value,
               MAX(e.circuit) AS circuit_name,
               MAX(COALESCE(c.division, e.division)) AS division
        FROM wildfire.epss_outages e
        LEFT JOIN wildfire.circuits c ON c.circuit_id = e.circuit_id
        WHERE {where_sql}
        GROUP BY e.circuit_id
    """
    return _rank_wrap_sql(
        groups_sql, extra_cols=("circuit_name", "division"), params=params
    )


def _rank_wrap_sql(
    groups_sql: str,
    *,
    extra_cols: tuple[str, ...],
    params: list[Any] | None = None,
) -> tuple[str, str, list[Any]]:
    extras = "".join(f", g.{col}" for col in extra_cols)
    select_sql = f"""
        SELECT g.group_value, g.metric_value{extras}
        FROM ({groups_sql}) g
        ORDER BY g.metric_value DESC, g.group_value ASC
    """
    count_sql = f"SELECT count(*) AS count FROM ({groups_sql}) groups"
    return select_sql, count_sql, list(params or [])


# ---- Workspace aggregates (grouped-counts / summary / regional-series) ----

# Catalog constants (NOT_RECORDED, WORKSPACE_UTILITIES, GROUPED_DATASETS,
# GROUP_BY_FIELDS, SUMMARY_METRIC_IDS) come from services.shared.dataset_registry.
REGIONAL_INTERVALS = frozenset({"daily", "weekly", "monthly", "quarterly"})


class AggregateQueryError(ValueError):
    """Invalid aggregate request; the route converts this to HTTP 400."""


def _utility_display_expr(col: str) -> str:
    whens = "".join(
        f"WHEN '{code}' THEN '{label}' " for code, label in UTILITY_DISPLAY_LABELS.items()
    )
    return (
        "COALESCE("
        f"CASE NULLIF(BTRIM({col}), '') "
        f"{whens}"
        f"ELSE NULLIF(BTRIM({col}), '') END, "
        f"'{NOT_RECORDED}')"
    )


def _text_group_expr(col: str) -> str:
    return f"COALESCE(NULLIF(BTRIM({col}), ''), '{NOT_RECORDED}')"


def _metric_number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, int):
        return int(value)
    number = float(value)
    if number.is_integer() and abs(number) < 2**53:
        return int(number)
    return number


def _sort_grouped_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -(row["value"] if row["value"] is not None else -1),
            row["key"],
        ),
    )


def _pad_utility_rows(
    counts: dict[str, int],
    *,
    dataset: str,
    utility_filter: str | None,
) -> list[dict[str, Any]]:
    if utility_filter:
        seed = [_utility_display_label(utility_filter)]
    else:
        seed = list(WORKSPACE_UTILITIES)
    keys = list(dict.fromkeys([*seed, *sorted(counts)]))
    rows: list[dict[str, Any]] = []
    for key in keys:
        if dataset == "epss_outages" and key != UTILITY_DISPLAY_LABELS["PGE"]:
            rows.append({"key": key, "value": None})
        else:
            rows.append({"key": key, "value": int(counts.get(key, 0))})
    return _sort_grouped_rows(rows)


def _utility_display_label(code: str) -> str:
    return UTILITY_DISPLAY_LABELS.get(code, code)


def _cpuc_aggregate_where(
    *,
    utility: str | None,
    county: str | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, list[Any]]:
    where = ["TRUE"]
    params: list[Any] = []
    if utility == "untagged":
        where.append("i.utility IS NULL")
    elif utility is not None:
        where.append("i.utility = %s")
        params.append(utility)
    if county is not None:
        where.append("lower(i.county) = lower(%s)")
        params.append(county)
    if start_date is not None:
        where.append("i.event_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("i.event_date <= %s")
        params.append(end_date)
    return " AND ".join(where), params


def _calfire_aggregate_where(
    *,
    utility: str | None,
    county: str | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, list[Any]]:
    where = [_CALFIRE_DEFAULT_TYPE_SQL]
    params: list[Any] = []
    if utility == "untagged":
        where.append("c.utility IS NULL")
    elif utility is not None:
        where.append("c.utility = %s")
        params.append(utility)
    if county is not None:
        where.append(county_match_sql("c.county"))
        params.append(county)
    if start_date is not None:
        where.append("c.date_only_created >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("c.date_only_created <= %s")
        params.append(end_date)
    return " AND ".join(where), params


def _epss_aggregate_where(
    *,
    county: str | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, list[Any]]:
    where = ["TRUE"]
    params: list[Any] = []
    if county is not None:
        where.append("lower(e.county) = lower(%s)")
        params.append(county)
    if start_date is not None:
        where.append("e.start_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("e.start_date <= %s")
        params.append(end_date)
    return " AND ".join(where), params


def _psps_aggregate_where(
    *,
    utility: str | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, list[Any]]:
    where = ["TRUE"]
    params: list[Any] = []
    if utility == "untagged":
        where.append("FALSE")
    elif utility is not None:
        where.append("p.utility = %s")
        params.append(utility)
    if start_date is not None:
        where.append("p.deenergization_start_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("p.deenergization_start_date <= %s")
        params.append(end_date)
    return " AND ".join(where), params


def _us_aggregate_where(
    *,
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, list[Any]]:
    where = ["TRUE"]
    params: list[Any] = []
    if start_date is not None:
        where.append("u.event_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("u.event_date <= %s")
        params.append(end_date)
    return " AND ".join(where), params


def _dataset_from_sql(dataset: str) -> tuple[str, str]:
    return {
        "cpuc_ignitions": ("wildfire.cpuc_ignitions i", "i"),
        "calfire_incidents": ("wildfire.calfire_incidents c", "c"),
        "epss_outages": ("wildfire.epss_outages e", "e"),
        "psps_events": ("wildfire.psps_events p", "p"),
        "us_ignitions": ("wildfire.us_ignitions u", "u"),
    }[dataset]


def _aggregate_where(
    dataset: str,
    *,
    utility: str | None,
    county: str | None,
    start_date: date | None,
    end_date: date | None,
) -> tuple[str, list[Any]]:
    if dataset == "cpuc_ignitions":
        return _cpuc_aggregate_where(
            utility=utility, county=county, start_date=start_date, end_date=end_date
        )
    if dataset == "calfire_incidents":
        return _calfire_aggregate_where(
            utility=utility, county=county, start_date=start_date, end_date=end_date
        )
    if dataset == "epss_outages":
        return _epss_aggregate_where(
            county=county, start_date=start_date, end_date=end_date
        )
    if dataset == "psps_events":
        return _psps_aggregate_where(
            utility=utility, start_date=start_date, end_date=end_date
        )
    return _us_aggregate_where(start_date=start_date, end_date=end_date)


def _validate_aggregate_request(
    dataset: str,
    *,
    utility: str | None,
    county: str | None,
) -> None:
    if dataset not in GROUPED_DATASETS:
        raise AggregateQueryError(
            f"unknown dataset {dataset!r}; allowed: {', '.join(sorted(GROUPED_DATASETS))}"
        )
    if dataset == "us_ignitions" and (utility or county):
        raise AggregateQueryError(
            "us_ignitions does not support county or utility filters"
        )
    if dataset == "psps_events" and county:
        raise AggregateQueryError("PSPS county filtering is not available")


def _group_expr(dataset: str, group_by: str) -> str | None:
    """SQL expression for the group key, or None when the column does not exist."""
    columns = {
        ("cpuc_ignitions", "county"): "i.county",
        ("cpuc_ignitions", "utility"): "i.utility",
        ("calfire_incidents", "county"): "c.county",
        ("calfire_incidents", "utility"): "c.utility",
        ("epss_outages", "county"): "e.county",
        ("epss_outages", "cause"): "e.cause",
        ("psps_events", "utility"): "p.utility",
    }
    col = columns.get((dataset, group_by))
    if col is None:
        return None
    if group_by == "utility":
        return _utility_display_expr(col)
    if group_by == "cause":
        return _text_group_expr(cause_display_sql(col))
    return _text_group_expr(col)


def query_grouped_counts(
    conn: psycopg.Connection,
    *,
    dataset: str,
    group_by: str,
    utility: str | None,
    county: str | None,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, Any]:
    _validate_aggregate_request(dataset, utility=utility, county=county)
    if group_by not in GROUP_BY_FIELDS:
        raise AggregateQueryError(
            f"group_by must be one of {', '.join(sorted(GROUP_BY_FIELDS))}"
        )

    empty_epss = dataset == "epss_outages" and _epss_rank_empty(utility)
    from_sql, _alias = _dataset_from_sql(dataset)
    where_sql, params = _aggregate_where(
        dataset,
        utility=utility,
        county=county,
        start_date=start_date,
        end_date=end_date,
    )

    extra: dict[str, Any] = {}
    calfire_by_county = dataset == "calfire_incidents" and group_by == "county"
    if empty_epss:
        total = 0
        grouped: list[tuple[str, int]] = []
    elif calfire_by_county:
        grouped, total = _calfire_grouped_by_county(conn, where_sql, params, county=county)
    else:
        group_expr = _group_expr(dataset, group_by)
        with conn.cursor(row_factory=dict_row) as cur:
            if group_expr is None:
                cur.execute(
                    f"SELECT COUNT(*)::bigint AS total FROM {from_sql} WHERE {where_sql}",
                    params,
                )
                total = int(cur.fetchone()["total"])
                grouped = [(NOT_RECORDED, total)] if total else []
            else:
                cur.execute(
                    f"""
                    SELECT {group_expr} AS key, COUNT(*)::bigint AS value
                    FROM {from_sql}
                    WHERE {where_sql}
                    GROUP BY 1
                    """,
                    params,
                )
                grouped = [(str(row["key"]), int(row["value"])) for row in cur.fetchall()]
                total = sum(value for _key, value in grouped)

    if group_by == "utility":
        if dataset == "epss_outages":
            counts = {UTILITY_DISPLAY_LABELS["PGE"]: total} if total and not empty_epss else {}
        else:
            counts = dict(grouped)
        rows = _pad_utility_rows(
            counts, dataset=dataset, utility_filter=utility
        )
    else:
        rows = _sort_grouped_rows(
            [{"key": key, "value": value} for key, value in grouped]
        )

    rows = [{**row, **group_code_and_label(group_by, row["key"])} for row in rows]
    if dataset == "calfire_incidents" and (county is not None or calfire_by_county):
        extra = _calfire_multi_county_meta(conn, where_sql, params)
        if calfire_by_county:
            extra["note"] = MULTI_COUNTY_NOTE
    return {"rows": rows, "total": total, **extra}


def _calfire_grouped_by_county(
    conn: psycopg.Connection,
    where_sql: str,
    params: list[Any],
    *,
    county: str | None,
) -> tuple[list[tuple[str, int]], int]:
    """CAL FIRE counts per listed county; total is the incident count.

    A multi-county incident adds one to each county it lists, so the rows can
    sum to more than ``total``. With a county filter only that county's row is
    kept, the same single row the other datasets return.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT cty.county AS key, COUNT(*)::bigint AS value
            FROM wildfire.calfire_incidents c
            {county_group_join_sql("c.county", unknown=NOT_RECORDED)}
            WHERE {where_sql}
            GROUP BY 1
            """,
            params,
        )
        grouped = [(str(row["key"]), int(row["value"])) for row in cur.fetchall()]
        cur.execute(
            f"SELECT COUNT(*)::bigint AS total FROM wildfire.calfire_incidents c WHERE {where_sql}",
            params,
        )
        total = int(cur.fetchone()["total"])
    if county is not None:
        grouped = [(key, value) for key, value in grouped if key.lower() == county.lower()]
    return grouped, total


def _distinct_split_count_sql(column: str) -> str:
    return f"""
        (SELECT COUNT(DISTINCT BTRIM(part))
           FROM filtered f,
                LATERAL unnest(string_to_array(f.{column}, ',')) AS part
          WHERE f.{column} IS NOT NULL
            AND BTRIM(f.{column}) <> ''
            AND BTRIM(part) <> '')
    """


def query_summary(
    conn: psycopg.Connection,
    *,
    dataset: str,
    utility: str | None,
    county: str | None,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, Any]:
    _validate_aggregate_request(dataset, utility=utility, county=county)
    metric_ids = SUMMARY_METRIC_IDS[dataset]
    if dataset == "epss_outages" and _epss_rank_empty(utility):
        return {
            "total": 0,
            "metrics": [
                {"id": metric_id, "value": 0, "missing": 0} for metric_id in metric_ids
            ],
        }

    from_sql, alias = _dataset_from_sql(dataset)
    where_sql, params = _aggregate_where(
        dataset,
        utility=utility,
        county=county,
        start_date=start_date,
        end_date=end_date,
    )

    selects = ["COUNT(*)::bigint AS total"]
    if "counties" in metric_ids:
        selects.append(
            f"COUNT(*) FILTER (WHERE {alias}.county IS NULL OR BTRIM({alias}.county) = '')"
            " AS counties_missing"
        )
        selects.append(f"{_distinct_split_count_sql('county')} AS counties_value")
    if "utilities" in metric_ids:
        selects.append(
            f"COUNT(*) FILTER (WHERE {alias}.utility IS NULL OR BTRIM({alias}.utility) = '')"
            " AS utilities_missing"
        )
        selects.append(
            f"COUNT(DISTINCT NULLIF(BTRIM({alias}.utility), '')) AS utilities_value"
        )
    if "circuits" in metric_ids:
        selects.append(
            f"COUNT(*) FILTER (WHERE {alias}.circuit_id IS NULL OR BTRIM({alias}.circuit_id) = '')"
            " AS circuits_missing"
        )
        selects.append(
            f"COUNT(DISTINCT NULLIF(BTRIM({alias}.circuit_id), '')) AS circuits_value"
        )
    if "acres" in metric_ids:
        selects.append(
            f"COUNT(*) FILTER (WHERE {alias}.acres_burned IS NULL) AS acres_missing"
        )
        selects.append(f"SUM({alias}.acres_burned) AS acres_value")
    if "customers" in metric_ids:
        selects.append(
            f"COUNT(*) FILTER (WHERE {alias}.customers_deenergized IS NULL)"
            " AS customers_missing"
        )
        selects.append(f"SUM({alias}.customers_deenergized) AS customers_value")

    sql = f"""
        WITH filtered AS (
            SELECT * FROM {from_sql} WHERE {where_sql}
        )
        SELECT {", ".join(selects)}
        FROM filtered {alias}
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone() or {}

    total = int(row.get("total") or 0)
    metrics: list[dict[str, Any]] = [{"id": "events", "value": total, "missing": 0}]
    for metric_id in metric_ids:
        if metric_id == "events":
            continue
        raw_value = row.get(f"{metric_id}_value")
        missing = int(row.get(f"{metric_id}_missing") or 0)
        if metric_id in {"counties", "utilities", "circuits"}:
            value = None if total > 0 and missing == total else int(raw_value or 0)
        elif total == 0:
            value = 0
        else:
            # acres / customers: all-null → null (matches client-side reduce)
            value = _metric_number(raw_value)
        metrics.append({"id": metric_id, "value": value, "missing": missing})
    out: dict[str, Any] = {"total": total, "metrics": metrics}
    if dataset == "calfire_incidents" and county is not None:
        out.update(_calfire_multi_county_meta(conn, where_sql, params))
    return out


def query_regional_series(
    conn: psycopg.Connection,
    *,
    start_date: date,
    end_date: date,
    utility: str | None,
    county: str | None,
    interval: str,
) -> dict[str, Any]:
    if interval not in REGIONAL_INTERVALS:
        raise AggregateQueryError(
            f"interval must be one of {', '.join(sorted(REGIONAL_INTERVALS))}"
        )
    from services.visualization.aggregations import interval_bin_meta

    template = interval_bin_meta(start_date, end_date, interval)
    day_to_idx: dict[date, int] = {}
    for index, bucket in enumerate(template):
        cursor = date.fromisoformat(bucket["start"])
        bucket_end = date.fromisoformat(bucket["end"])
        while cursor <= bucket_end:
            day_to_idx[cursor] = index
            cursor += timedelta(days=1)

    if _epss_rank_empty(utility):
        return {"series": [], "total": 0}

    where_sql, params = _epss_aggregate_where(
        county=county, start_date=start_date, end_date=end_date
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT COALESCE(NULLIF(BTRIM(e.division), ''), '{NOT_RECORDED}') AS name,
                   e.start_date AS day,
                   COUNT(*)::bigint AS n
            FROM wildfire.epss_outages e
            WHERE {where_sql}
            GROUP BY 1, 2
            """,
            params,
        )
        grouped = list(cur.fetchall())

    regions: dict[str, list[dict[str, Any]]] = {}
    region_totals: dict[str, int] = {}
    for row in grouped:
        name = str(row["name"])
        day = row["day"]
        if isinstance(day, str):
            day = date.fromisoformat(day)
        if day not in day_to_idx:
            continue
        if name not in regions:
            regions[name] = [
                {"start": bucket["start"], "end": bucket["end"], "count": 0}
                for bucket in template
            ]
            region_totals[name] = 0
        count = int(row["n"])
        regions[name][day_to_idx[day]]["count"] += count
        region_totals[name] += count

    series = [
        {"name": name, "buckets": regions[name], "total": region_totals[name]}
        for name in regions
    ]
    series.sort(key=lambda item: (-item["total"], item["name"]))
    return {"series": series, "total": sum(item["total"] for item in series)}
