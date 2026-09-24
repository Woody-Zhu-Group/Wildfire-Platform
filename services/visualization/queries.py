"""SQL helpers for visualization endpoints."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from services.shared.calfire_county import county_match_sql, multi_county_count_sql
from services.shared.dataset_registry import calfire_default_type_sql
from services.shared.epss_causes import cause_display_sql, cause_filter_sql, cause_variants
from services.visualization.styles import acres_radius_hint


def _bbox_sql(alias: str, bbox: tuple[float, float, float, float] | None, params: list) -> str:
    if bbox is None:
        return ""
    params.extend(bbox)
    return f" AND {alias}.geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"


def map_us_ignitions(
    conn: psycopg.Connection,
    *,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    bbox: tuple[float, float, float, float] | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
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
    where_sql = " AND ".join(where) + _bbox_sql("u", bbox, params)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT count(*) AS c FROM wildfire.us_ignitions u WHERE {where_sql}",
            params,
        )
        total = int(cur.fetchone()["c"])
        cur.execute(
            f"""
            SELECT u.id, u.event_date, u.year, u.latitude, u.longitude,
                   ST_AsGeoJSON(u.geom) AS geom
            FROM wildfire.us_ignitions u
            WHERE {where_sql}
            ORDER BY u.event_date, u.id
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = list(cur.fetchall())
    return rows, total


def map_ignitions(
    conn: psycopg.Connection,
    *,
    utility: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    county: str | None,
    bbox: tuple[float, float, float, float] | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    where = ["TRUE"]
    params: list[Any] = []
    if utility == "untagged":
        where.append("i.utility IS NULL")
    elif utility is not None:
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
    where_sql = " AND ".join(where) + _bbox_sql("i", bbox, params)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT count(*) AS c FROM wildfire.cpuc_ignitions i WHERE {where_sql}",
            params,
        )
        total = int(cur.fetchone()["c"])
        cur.execute(
            f"""
            SELECT i.id, i.utility, i.event_date, i.year, i.source_file, i.county,
                   ST_AsGeoJSON(i.geom) AS geom
            FROM wildfire.cpuc_ignitions i
            WHERE {where_sql}
            ORDER BY i.event_date, i.id
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = list(cur.fetchall())
    return rows, total


def map_epss_circuits(
    conn: psycopg.Connection,
    *,
    utility: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    county: str | None,
    outage_type: str | None,
    cause: str | None,
    bbox: tuple[float, float, float, float] | None,
    limit: int,
    offset: int,
    include_outages: bool = False,
) -> tuple[list[dict], int, dict[str, Any]]:
    """Aggregate EPSS events onto circuit line geometries (website behavior)."""
    notes: dict[str, Any] = {"render_as": "circuit_lines", "dataset_utility": "PGE"}
    if utility is not None and utility not in ("PGE",):
        notes["empty_reason"] = f"EPSS is PG&E-only; utility={utility} matches nothing"
        return [], 0, notes

    where = ["TRUE"]
    params: list[Any] = []
    if year is not None:
        where.append("e.year = %s")
        params.append(year)
    if start_date is not None:
        where.append("e.start_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("e.start_date <= %s")
        params.append(end_date)
    if county is not None:
        where.append("lower(e.county) = lower(%s)")
        params.append(county)
    if outage_type is not None:
        where.append("e.outage_type = %s")
        params.append(outage_type)
    if cause is not None:
        where.append(cause_filter_sql("e.cause"))
        params.append(cause_variants(cause))
    where_sql = " AND ".join(where)

    bbox_clause = ""
    bbox_params: list[Any] = []
    if bbox is not None:
        bbox_clause = (
            " AND (c.geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326) OR c.geom IS NULL)"
        )
        bbox_params = list(bbox)

    outages_select = ""
    if include_outages:
        # Embed filtered outage rows so the website day scrubber / popup can match static CSV.
        outages_select = """,
                   (
                     SELECT COALESCE(json_agg(o ORDER BY o->>'start_date', o->>'id'), '[]'::json)
                     FROM (
                       SELECT json_build_object(
                         'id', e2.id,
                         'circuit_id', e2.circuit_id,
                         'circuit', e2.circuit,
                         'year', e2.year,
                         'start_date', e2.start_date,
                         'end_date', e2.end_date,
                         'county', e2.county,
                         'cause', """ + cause_display_sql("e2.cause") + """,
                         'outage_type', e2.outage_type,
                         'division', e2.division,
                         'customer_minutes', e2.customer_minutes,
                         'restoration_min', e2.restoration_min,
                         'medical_baseline', e2.medical_baseline,
                         'life_support', e2.life_support,
                         'schools', e2.schools,
                         'hospitals', e2.hospitals
                       ) AS o
                       FROM wildfire.epss_outages e2
                       WHERE e2.circuit_id = a.circuit_id
                         AND """ + where_sql.replace("e.", "e2.") + """
                     ) sub
                   ) AS outages"""
        notes["include_outages"] = True

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            WITH filtered AS (
              SELECT e.circuit_id FROM wildfire.epss_outages e WHERE {where_sql}
            ),
            agg AS (
              SELECT circuit_id FROM filtered GROUP BY circuit_id
            )
            SELECT count(*) AS c
            FROM agg a
            LEFT JOIN wildfire.circuits c ON c.circuit_id = a.circuit_id
            WHERE TRUE {bbox_clause}
            """,
            params + bbox_params,
        )
        total = int(cur.fetchone()["c"])

        # When embedding outages, filter params are referenced twice (agg + subquery).
        row_params = list(params) + list(bbox_params) + [limit, offset]
        if include_outages:
            row_params = list(params) + list(params) + list(bbox_params) + [limit, offset]

        cur.execute(
            f"""
            WITH filtered AS (
              SELECT e.circuit_id, e.circuit, e.year, e.start_date
              FROM wildfire.epss_outages e
              WHERE {where_sql}
            ),
            agg AS (
              SELECT circuit_id,
                     max(circuit) AS circuit_name,
                     count(*)::int AS event_count,
                     min(start_date) AS first_event,
                     max(start_date) AS last_event,
                     array_agg(DISTINCT year ORDER BY year) AS years
              FROM filtered
              GROUP BY circuit_id
            )
            SELECT a.circuit_id, a.circuit_name, a.event_count, a.first_event, a.last_event,
                   a.years, c.division, c.substation,
                   (c.circuit_id IS NULL) AS geometry_missing,
                   ST_AsGeoJSON(c.geom) AS geom
                   {outages_select}
            FROM agg a
            LEFT JOIN wildfire.circuits c ON c.circuit_id = a.circuit_id
            WHERE TRUE {bbox_clause}
            ORDER BY a.event_count DESC, a.circuit_id
            LIMIT %s OFFSET %s
            """,
            row_params,
        )
        rows = list(cur.fetchall())

    missing = sum(1 for r in rows if r.get("geometry_missing"))
    notes["circuits_missing_geometry_in_page"] = missing
    return rows, total, notes


def map_psps(
    conn: psycopg.Connection,
    *,
    utility: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    where = ["TRUE"]
    params: list[Any] = []
    if utility is not None and utility != "untagged":
        where.append("p.utility = %s")
        params.append(utility)
    elif utility == "untagged":
        where.append("FALSE")
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
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT count(*) AS c FROM wildfire.psps_events p WHERE {where_sql}",
            params,
        )
        total = int(cur.fetchone()["c"])
        cur.execute(
            f"""
            SELECT p.event_name, p.utility, p.iou_raw, p.deenergization_start_date,
                   p.full_restoration_date, p.customers_deenergized, p.year,
                   ST_AsGeoJSON(p.geom) AS geom
            FROM wildfire.psps_events p
            WHERE {where_sql}
            ORDER BY p.deenergization_start_date NULLS LAST, p.event_name
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = list(cur.fetchall())
    return rows, total


def map_calfire(
    conn: psycopg.Connection,
    *,
    utility: str | None,
    county: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    min_acres: float | None,
    incident_type: str | None,
    bbox: tuple[float, float, float, float] | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    where_sql, params = _calfire_where(
        utility=utility,
        county=county,
        year=year,
        start_date=start_date,
        end_date=end_date,
        min_acres=min_acres,
        incident_type=incident_type,
        bbox=bbox,
    )

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM wildfire.calfire_incidents c WHERE {where_sql}",
            params,
        )
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"""
            SELECT c.incident_id, c.incident_name, c.incident_type, c.acres_burned,
                   c.containment, c.county, c.utility, c.date_only_created,
                   ST_AsGeoJSON(c.geom) AS geom
            FROM wildfire.calfire_incidents c
            WHERE {where_sql}
            ORDER BY c.date_only_created NULLS LAST, c.incident_id
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = list(cur.fetchall())
    for row in rows:
        row["radius_hint"] = acres_radius_hint(row.get("acres_burned"))
    return rows, total


def _calfire_where(
    *,
    utility: str | None,
    county: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    incident_type: str | None,
    min_acres: float | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> tuple[str, list[Any]]:
    """CAL FIRE WHERE clause (alias c) shared by the map, time series, and meta.

    incident_type is None for the Wildfire/Fire default, "all", "untyped", or
    a stored value the route already resolved.
    """
    where = ["TRUE"]
    params: list[Any] = []
    if incident_type is None or incident_type.strip() == "":
        where.append(calfire_default_type_sql("c.incident_type"))
    elif incident_type.strip().lower() == "all":
        pass
    elif incident_type.strip().lower() == "untyped":
        where.append("c.incident_type IS NULL")
    else:
        where.append("c.incident_type = %s")
        params.append(incident_type.strip())

    if utility == "untagged":
        where.append("c.utility IS NULL")
    elif utility is not None:
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
    return " AND ".join(where) + _bbox_sql("c", bbox, params), params


def calfire_multi_county_count(conn: psycopg.Connection, **filters: Any) -> int:
    """How many CAL FIRE incidents matching ``filters`` list several counties."""
    where_sql, params = _calfire_where(**filters)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {multi_county_count_sql('c.county')} "
            f"FROM wildfire.calfire_incidents c WHERE {where_sql}",
            params,
        )
        return int(cur.fetchone()[0] or 0)


# Map display only: the stored HFTD and IOU polygons are full resolution for
# spatial queries, which is about three times the old web-map payload. Map
# layers draw a simplified copy, kept MultiPolygon like the stored type; no
# count or containment uses it.
MAP_SIMPLIFY_DEGREES = 0.001
MAP_COORD_DECIMALS = 5
_MAP_GEOJSON = (
    f"ST_AsGeoJSON(ST_Multi(ST_SimplifyPreserveTopology(geom, {MAP_SIMPLIFY_DEGREES})), "
    f"{MAP_COORD_DECIMALS})"
)


def map_hftd(conn: psycopg.Connection, *, tier: str | None) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        if tier:
            cur.execute(
                f"""
                SELECT tier, objectid, {_MAP_GEOJSON} AS geom
                FROM wildfire.hftd_tiers WHERE tier = %s
                """,
                (tier,),
            )
        else:
            cur.execute(
                f"SELECT tier, objectid, {_MAP_GEOJSON} AS geom "
                "FROM wildfire.hftd_tiers ORDER BY tier"
            )
        return list(cur.fetchall())


def time_series_dates(
    conn: psycopg.Connection,
    dataset: str,
    *,
    utility: str | None,
    year: int | None,
    start_date: date | None,
    end_date: date | None,
    county: str | None,
    incident_type: str | None,
) -> list[date]:
    with conn.cursor() as cur:
        if dataset == "ignitions":
            where = ["TRUE"]
            params: list[Any] = []
            if utility and utility != "untagged":
                where.append("utility = %s")
                params.append(utility)
            elif utility == "untagged":
                where.append("utility IS NULL")
            if year is not None:
                where.append("year = %s")
                params.append(year)
            if start_date:
                where.append("event_date >= %s")
                params.append(start_date)
            if end_date:
                where.append("event_date <= %s")
                params.append(end_date)
            if county:
                where.append("lower(county) = lower(%s)")
                params.append(county)
            cur.execute(
                f"SELECT event_date FROM wildfire.cpuc_ignitions WHERE {' AND '.join(where)}",
                params,
            )
        elif dataset == "epss":
            where = ["TRUE"]
            params = []
            if utility and utility not in ("PGE", None):
                return []
            if year is not None:
                where.append("year = %s")
                params.append(year)
            if start_date:
                where.append("start_date >= %s")
                params.append(start_date)
            if end_date:
                where.append("start_date <= %s")
                params.append(end_date)
            if county:
                where.append("lower(county) = lower(%s)")
                params.append(county)
            cur.execute(
                f"SELECT start_date FROM wildfire.epss_outages WHERE {' AND '.join(where)}",
                params,
            )
        elif dataset == "psps":
            where = ["TRUE"]
            params = []
            if utility and utility != "untagged":
                where.append("utility = %s")
                params.append(utility)
            if year is not None:
                where.append("year = %s")
                params.append(year)
            if start_date:
                where.append("deenergization_start_date >= %s")
                params.append(start_date)
            if end_date:
                where.append("deenergization_start_date <= %s")
                params.append(end_date)
            cur.execute(
                f"SELECT deenergization_start_date FROM wildfire.psps_events WHERE {' AND '.join(where)}",
                params,
            )
        elif dataset == "calfire":
            where_sql, params = _calfire_where(
                utility=utility or None,
                county=county or None,
                year=year,
                start_date=start_date or None,
                end_date=end_date or None,
                incident_type=incident_type,
            )
            cur.execute(
                f"SELECT c.date_only_created FROM wildfire.calfire_incidents c WHERE {where_sql}",
                params,
            )
        elif dataset == "us_ignitions":
            where = ["TRUE"]
            params = []
            if year is not None:
                where.append("year = %s")
                params.append(year)
            if start_date:
                where.append("event_date >= %s")
                params.append(start_date)
            if end_date:
                where.append("event_date <= %s")
                params.append(end_date)
            cur.execute(
                f"SELECT event_date FROM wildfire.us_ignitions WHERE {' AND '.join(where)}",
                params,
            )
        else:
            raise ValueError(f"unsupported dataset for time-series: {dataset}")
        return [row[0] for row in cur.fetchall() if row[0] is not None]


def utility_territory(conn: psycopg.Connection, utility: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        # Simplified outline for drawing; bounds and center use full geometry.
        cur.execute(
            f"""
            SELECT utility, utility_name,
                   {_MAP_GEOJSON} AS geom,
                   ST_XMin(geom) AS min_lon, ST_YMin(geom) AS min_lat,
                   ST_XMax(geom) AS max_lon, ST_YMax(geom) AS max_lat,
                   ST_Y(ST_Centroid(geom)) AS center_lat,
                   ST_X(ST_Centroid(geom)) AS center_lon
            FROM wildfire.iou_territories
            WHERE utility = %s
            """,
            (utility,),
        )
        return cur.fetchone()


def _epss_outages_for_circuit(
    conn: psycopg.Connection,
    circuit_id: str,
    *,
    year: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    where = ["circuit_id = %s"]
    params: list[Any] = [circuit_id]
    if year is not None:
        where.append("year = %s")
        params.append(year)
    if start_date is not None:
        where.append("start_date >= %s")
        params.append(start_date)
    if end_date is not None:
        where.append("start_date <= %s")
        params.append(end_date)
    where_sql = " AND ".join(where)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT id, circuit_id, circuit, year, start_date, end_date, county,
                   {cause_display_sql("cause")} AS cause, outage_type, division,
                   customer_minutes, restoration_min,
                   medical_baseline, life_support, schools, hospitals
            FROM wildfire.epss_outages
            WHERE {where_sql}
            ORDER BY start_date NULLS LAST, id
            """,
            params,
        )
        return list(cur.fetchall())


def _psps_affected_circuits(conn: psycopg.Connection, event_name: str) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT pec.circuit_id, pec.circuit_name,
                   (c.circuit_id IS NULL) AS geometry_missing
            FROM wildfire.psps_event_circuits pec
            LEFT JOIN wildfire.circuits c ON c.circuit_id = pec.circuit_id
            WHERE pec.event_name = %s
            ORDER BY pec.circuit_name NULLS LAST, pec.circuit_id
            """,
            (event_name,),
        )
        return list(cur.fetchall())


def event_detail(
    conn: psycopg.Connection,
    dataset: str,
    record_id: str,
    *,
    year: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        if dataset == "ignitions":
            cur.execute(
                """
                SELECT id, utility, event_date, year, source_file, county,
                       ST_AsGeoJSON(geom) AS geom
                FROM wildfire.cpuc_ignitions WHERE id = %s
                """,
                (int(record_id),),
            )
        elif dataset == "us_ignitions":
            cur.execute(
                """
                SELECT id, event_date, year, latitude, longitude,
                       pr, rmax, rmin, sph, srad, tmmn, tmmx, vs,
                       bi, fm100, fm1000, erc, etr, pet, vpd,
                       ST_AsGeoJSON(geom) AS geom
                FROM wildfire.us_ignitions WHERE id = %s
                """,
                (int(record_id),),
            )
        elif dataset == "epss":
            cur.execute(
                f"""
                SELECT id, circuit_id, circuit, year, start_date, end_date, county,
                       {cause_display_sql("cause")} AS cause, outage_type, division,
                       customer_minutes, restoration_min,
                       medical_baseline, life_support, schools, hospitals,
                       ST_AsGeoJSON(geom) AS geom
                FROM wildfire.epss_outages WHERE id = %s
                """,
                (int(record_id),),
            )
        elif dataset == "psps":
            cur.execute(
                """
                SELECT event_name, utility, iou_raw, first_date_of_poc,
                       deenergization_start_date, full_restoration_date,
                       de_energization, customers_deenergized, year,
                       ST_AsGeoJSON(geom) AS geom
                FROM wildfire.psps_events WHERE event_name = %s
                """,
                (record_id,),
            )
        elif dataset == "calfire":
            cur.execute(
                """
                SELECT incident_id, incident_name, incident_type, acres_burned, containment,
                       control, county, location, administrative_unit, cooperating_agencies,
                       utility, date_created, date_only_created, date_last_update,
                       date_extinguished, date_only_extinguished,
                       is_final, is_active, is_calfire_incident, notification_desired,
                       incident_url, ST_AsGeoJSON(geom) AS geom
                FROM wildfire.calfire_incidents WHERE incident_id = %s
                """,
                (record_id,),
            )
        elif dataset == "circuits":
            cid = record_id.strip()
            if cid.isdigit():
                cid = cid.zfill(9)
            cur.execute(
                """
                SELECT circuit_id, circuit_name, division, substation,
                       ST_AsGeoJSON(geom) AS geom
                FROM wildfire.circuits WHERE circuit_id = %s
                """,
                (cid,),
            )
        else:
            raise ValueError(dataset)
        row = cur.fetchone()

    if dataset == "circuits":
        cid = record_id.strip()
        if cid.isdigit():
            cid = cid.zfill(9)
        outages = _epss_outages_for_circuit(
            conn, cid, year=year, start_date=start_date, end_date=end_date
        )
        if row is None:
            if not outages:
                return None
            # Orphan EPSS circuit_id (no GNA geometry row) — still return outages.
            name = outages[0].get("circuit")
            row = {
                "circuit_id": cid,
                "circuit_name": name,
                "division": outages[0].get("division"),
                "substation": None,
                "geom": None,
                "geometry_missing": True,
            }
        row["outages"] = outages
        return row

    if row is None:
        return None

    if dataset == "psps":
        row["affected_circuits"] = _psps_affected_circuits(conn, row["event_name"])

    return row


def rows_to_feature_collection(rows: list[dict], *, id_field: str | None = None) -> dict:
    features = []
    for row in rows:
        props = {k: _jsonable(v) for k, v in row.items() if k != "geom"}
        geom_txt = row.get("geom")
        geometry = json.loads(geom_txt) if geom_txt else None
        if geometry is None:
            props.setdefault("geometry_missing", True)
        feat: dict[str, Any] = {
            "type": "Feature",
            "geometry": geometry,
            "properties": props,
        }
        if id_field and id_field in props:
            feat["id"] = props[id_field]
        features.append(feat)
    return {"type": "FeatureCollection", "features": features}


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    # psycopg array / JSON already decoded
    return value


# Website popup field order
DETAIL_FIELDS = {
    "ignitions": [
        ("Date", "event_date"),
        ("Utility", "utility"),
        ("County", "county"),
        ("Year", "year"),
        ("Source file", "source_file"),
        ("ID", "id"),
    ],
    "us_ignitions": [
        ("Date", "event_date"),
        ("Year", "year"),
        ("Latitude", "latitude"),
        ("Longitude", "longitude"),
        ("Tmax (K)", "tmmx"),
        ("Tmin (K)", "tmmn"),
        ("Wind (m/s)", "vs"),
        ("SPFH", "sph"),
        ("VPD (kPa)", "vpd"),
        ("fm100 (%)", "fm100"),
        ("ID", "id"),
    ],
    "epss": [
        ("Circuit", "circuit"),
        ("Circuit ID", "circuit_id"),
        ("County", "county"),
        ("Division", "division"),
        ("Cause", "cause"),
        ("Outage Type", "outage_type"),
        ("Start", "start_date"),
        ("End", "end_date"),
        ("Customer Minutes", "customer_minutes"),
        ("Restoration (min)", "restoration_min"),
        ("Medical Baseline", "medical_baseline"),
        ("Life Support", "life_support"),
        ("Schools", "schools"),
        ("Hospitals", "hospitals"),
    ],
    "psps": [
        ("Event", "event_name"),
        ("Utility", "utility"),
        ("IOU raw", "iou_raw"),
        ("De-energization start", "deenergization_start_date"),
        ("Full restoration", "full_restoration_date"),
        ("Customers de-energized", "customers_deenergized"),
        ("Year", "year"),
    ],
    "calfire": [
        ("Name", "incident_name"),
        ("County", "county"),
        ("Utility", "utility"),
        ("Acres burned", "acres_burned"),
        ("Containment", "containment"),
        ("Date", "date_only_created"),
        ("Type", "incident_type"),
        ("URL", "incident_url"),
    ],
    "circuits": [
        ("Circuit ID", "circuit_id"),
        ("Name", "circuit_name"),
        ("Division", "division"),
        ("Substation", "substation"),
    ],
}


def detail_field_list(dataset: str, row: dict) -> list[dict[str, Any]]:
    out = []
    for label, key in DETAIL_FIELDS.get(dataset, []):
        val = row.get(key)
        if val is None or val == "":
            continue
        if key in ("medical_baseline", "life_support", "schools", "hospitals") and val == 0:
            continue
        if key == "containment":
            try:
                val = f"{float(val)}%"
            except (TypeError, ValueError):
                pass
        out.append({"label": label, "field": key, "value": _jsonable(val)})
    return out
