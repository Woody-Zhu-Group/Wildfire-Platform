"""Spatial point and summary vs SQL ST_Within ground truth."""

from __future__ import annotations

import httpx
import psycopg

from tests.conftest import sql_count


def test_spatial_point_known_coordinates(data_client: httpx.Client, db_conn: psycopg.Connection):
    # Known PGE ignition from warehouse
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT ST_Y(geom), ST_X(geom) FROM wildfire.cpuc_ignitions
            WHERE utility = 'PGE' LIMIT 1
            """
        )
        lat, lon = cur.fetchone()

    r = data_client.get("/spatial/point", params={"lat": lat, "lon": lon})
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["county_unavailable"] is False
    assert body["county"]  # CA ignition should resolve to a Census county name
    assert body["iou"]["utility"] == "PGE"
    assert body["grid_cell"]["cell_id"] is not None

    # Downtown San Francisco — PGE
    r = data_client.get("/spatial/point", params={"lat": 37.7749, "lon": -122.4194})
    assert r.status_code == 200
    sf = r.json()
    assert sf["iou"]["utility"] == "PGE"
    assert sf["county"] == "San Francisco"

    # SCE sample if present
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT ST_Y(geom), ST_X(geom) FROM wildfire.cpuc_ignitions
            WHERE utility = 'SCE' LIMIT 1
            """
        )
        row = cur.fetchone()
    if row:
        r = data_client.get("/spatial/point", params={"lat": row[0], "lon": row[1]})
        assert r.status_code == 200
        assert r.json()["iou"]["utility"] == "SCE"

    # Pacific ocean west of CA — no territory
    r = data_client.get("/spatial/point", params={"lat": 36.0, "lon": -130.0})
    assert r.status_code == 200
    body = r.json()
    assert body["iou"]["utility"] is None
    assert body["hftd_tier"] is None
    assert body["grid_cell"]["cell_id"] is None
    assert body["county"] is None


def _sql_within_counts(
    conn: psycopg.Connection,
    *,
    region_sql: str,
    region_param: str,
    start: str,
    end: str,
) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH region AS ({region_sql})
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
                   AND (c.incident_type IS NULL OR c.incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat'))) AS calfire_incidents
            """,
            (region_param, start, end, start, end, start, end),
        )
        row = cur.fetchone()
    return {
        "ignitions": int(row[0]),
        "epss_outages": int(row[1]),
        "calfire_incidents": int(row[2]),
    }


def test_spatial_summary_matches_st_within_sql(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    start, end = "2024-01-01", "2024-12-31"
    r = data_client.get(
        "/spatial/summary",
        params={"utility": "PGE", "start_date": start, "end_date": end},
    )
    assert r.status_code == 200
    api_counts = r.json()["counts"]

    sql_counts = _sql_within_counts(
        db_conn,
        region_sql="SELECT geom FROM wildfire.iou_territories WHERE utility = %s",
        region_param="PGE",
        start=start,
        end=end,
    )
    assert api_counts == sql_counts, f"API {api_counts} != SQL ST_Within {sql_counts}"

    # Attribute-filter side-by-side (NOT required to match — report via attach)
    attr = {
        "ignitions": data_client.get(
            "/ignitions",
            params={"utility": "PGE", "year": 2024, "limit": 1, "geometry": False},
        ).json()["meta"]["total"],
        "epss_outages": data_client.get(
            "/epss/outages",
            params={"utility": "PGE", "year": 2024, "limit": 1, "geometry": False},
        ).json()["meta"]["total"],
        "calfire_incidents": data_client.get(
            "/calfire/incidents",
            params={"utility": "PGE", "year": 2024, "limit": 1, "geometry": False},
        ).json()["meta"]["total"],
    }
    deltas = {k: attr[k] - sql_counts[k] for k in sql_counts}
    print(
        f"\n[spatial/summary PGE 2024] ST_Within={sql_counts} "
        f"attribute_filters={attr} deltas={deltas}"
    )

    # HFTD Tier 3
    r = data_client.get(
        "/spatial/summary",
        params={"hftd_tier": "Tier 3", "start_date": start, "end_date": end},
    )
    assert r.status_code == 200
    api_tier = r.json()["counts"]
    sql_tier = _sql_within_counts(
        db_conn,
        region_sql="SELECT geom FROM wildfire.hftd_tiers WHERE tier = %s",
        region_param="Tier 3",
        start=start,
        end=end,
    )
    assert api_tier == sql_tier, f"Tier3 API {api_tier} != SQL {sql_tier}"
