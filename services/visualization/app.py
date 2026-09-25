"""FastAPI visualization service — GeoJSON layers, time series, territory, detail."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Generator, Optional
from urllib.parse import unquote

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from services.data_query.filters import (
    parse_bbox,
    parse_cause,
    parse_county,
    parse_date_param,
    parse_incident_type,
    parse_outage_type,
    parse_tier,
    parse_utility,
    validate_date_range,
)
from services.shared.calfire_county import multi_county_meta
from services.shared.dataset_registry import (
    CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM,
    US_IGNITIONS_META_VISUALIZATION,
    parse_viz_dataset,
)
from services.visualization import aggregations, queries
from services.visualization.styles import (
    DATASETS,
    IOU_STYLE,
    STATEWIDE_CENTER,
    style_for,
)
from shared.db import connect, get_settings

MAP_DEFAULT_LIMIT = 5000
MAP_MAX_LIMIT = 20000

_db_ok: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_ok
    settings = get_settings()
    print(f"[visualization] Startup: connecting to {settings.safe_target} ...")
    try:
        with connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT PostGIS_Version()")
                _db_ok = f"postgis {cur.fetchone()[0]}"
        print(f"[visualization] Startup OK ({_db_ok})")
    except Exception as exc:  # noqa: BLE001
        _db_ok = None
        print(f"[visualization] Startup WARNING: DB unavailable: {exc}")
    yield
    print("[visualization] Shutdown.")


app = FastAPI(
    title="Wildfire Visualization",
    description=(
        "Map layers, time series, and detail payloads matching dataset_demo conventions. "
        "Ignition counts: attribute utility tagging vs spatial containment are different "
        "definitions — see /health and README."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Local frontend (other origin/port) calls this API in the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn() -> Generator[psycopg.Connection, None, None]:
    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    try:
        yield conn
    finally:
        conn.close()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    qs = str(request.query_params)
    print(f"[visualization] {request.method} {request.url.path}" + (f"?{qs}" if qs else ""))
    response = await call_next(request)
    print(f"[visualization] → {response.status_code} {request.url.path}")
    return response


def _parse_dataset(value: str) -> str:
    try:
        return parse_viz_dataset(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# Discrepancy: notes text differs from data_query US_IGNITIONS_META.
US_IGNITIONS_META = US_IGNITIONS_META_VISUALIZATION


@app.get("/health")
def health(conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
    return {
        "status": "ok",
        "database": get_settings().safe_target,
        "detail": _db_ok,
        "definitions": {
            "ignitions_attribute": (
                "Count/filter by cpuc_ignitions.utility column "
                "(what /map-layer and /time-series use for utility=)."
            ),
            "ignitions_spatial": (
                "Count points inside IOU polygon via ST_Within "
                "(data_query /spatial/summary). For PGE 2024 these differ by 4 rows."
            ),
            "us_ignitions": (
                "CONUS all-cause IRWIN-derived sample from FireCastRL — "
                "not utility-attributed and not a complete census."
            ),
        },
    }


@app.get("/map-layer")
def map_layer(
    dataset: str = Query(..., description="ignitions|us_ignitions|epss|psps|calfire|hftd"),
    utility: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    outage_type: Optional[str] = Query(None),
    cause: Optional[str] = Query(None),
    min_acres: Optional[float] = Query(None, ge=0),
    incident_type: Optional[str] = Query(None),
    tier: Optional[str] = Query(None, description="HFTD Tier 2|Tier 3"),
    bbox: Optional[str] = Query(None),
    limit: int = Query(MAP_DEFAULT_LIMIT, ge=1, le=MAP_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    include_outages: bool = Query(
        False,
        description="EPSS only: embed filtered outage rows on each circuit feature",
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    ds = _parse_dataset(dataset)
    if ds == "circuits":
        raise HTTPException(status_code=400, detail="use dataset=epss for circuit lines")
    util = parse_utility(utility) if utility else None
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    bb = parse_bbox(bbox)
    t = parse_tier(tier)
    county = parse_county(county)
    if ds == "epss":
        outage_type = parse_outage_type(conn, outage_type)
        cause = parse_cause(conn, cause)
    if ds == "calfire":
        incident_type = parse_incident_type(conn, incident_type)

    if ds == "us_ignitions" and (utility or county):
        raise HTTPException(
            status_code=400,
            detail="us_ignitions have no utility/county attributes; use year/date/bbox filters.",
        )

    extra_meta: dict[str, Any] = {}
    if ds == "ignitions":
        rows, total = queries.map_ignitions(
            conn,
            utility=util,
            year=year,
            start_date=start,
            end_date=end,
            county=county,
            bbox=bb,
            limit=limit,
            offset=offset,
        )
        style = style_for("ignitions")
        fc = queries.rows_to_feature_collection(rows, id_field="id")
        extra_meta["utility_filter_definition"] = "attribute"
    elif ds == "us_ignitions":
        rows, total = queries.map_us_ignitions(
            conn,
            year=year,
            start_date=start,
            end_date=end,
            bbox=bb,
            limit=limit,
            offset=offset,
        )
        style = style_for("us_ignitions")
        fc = queries.rows_to_feature_collection(rows, id_field="id")
        extra_meta.update(US_IGNITIONS_META)
    elif ds == "epss":
        rows, total, notes = queries.map_epss_circuits(
            conn,
            utility=util,
            year=year,
            start_date=start,
            end_date=end,
            county=county,
            outage_type=outage_type,
            cause=cause,
            bbox=bb,
            limit=limit,
            offset=offset,
            include_outages=include_outages,
        )
        style = style_for("epss")
        fc = queries.rows_to_feature_collection(rows, id_field="circuit_id")
        extra_meta.update(notes)
    elif ds == "psps":
        rows, total = queries.map_psps(
            conn,
            utility=util,
            year=year,
            start_date=start,
            end_date=end,
            limit=limit,
            offset=offset,
        )
        style = style_for("psps")
        fc = queries.rows_to_feature_collection(rows, id_field="event_name")
    elif ds == "calfire":
        rows, total = queries.map_calfire(
            conn,
            utility=util,
            county=county,
            year=year,
            start_date=start,
            end_date=end,
            min_acres=min_acres,
            incident_type=incident_type,
            bbox=bb,
            limit=limit,
            offset=offset,
        )
        style = style_for("calfire")
        fc = queries.rows_to_feature_collection(rows, id_field="incident_id")
        extra_meta["incident_type_default"] = CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM
        calfire_filters = {
            "utility": util,
            "county": county,
            "year": year,
            "start_date": start,
            "end_date": end,
            "min_acres": min_acres,
            "incident_type": incident_type,
            "bbox": bb,
        }
        extra_meta.update(queries.calfire_missing_counts(conn, **calfire_filters))
        extra_meta.update(queries.calfire_untagged_excluded(conn, **calfire_filters))
        if county is not None:
            extra_meta.update(
                multi_county_meta(queries.calfire_multi_county_count(conn, **calfire_filters))
            )
    elif ds == "hftd":
        rows = queries.map_hftd(conn, tier=t)
        total = len(rows)
        # Per-feature tier styles; top-level style is generic/selected
        style = style_for("hftd", tier=t)
        features = []
        for row in rows:
            tier_name = row["tier"]
            props = {
                "tier": tier_name,
                "objectid": row["objectid"],
                "style": style_for("hftd", tier=tier_name),
            }
            geom = json.loads(row["geom"]) if row.get("geom") else None
            features.append(
                {"type": "Feature", "geometry": geom, "properties": props, "id": tier_name}
            )
        fc = {"type": "FeatureCollection", "features": features}
        offset = 0
        limit = total
    else:
        raise HTTPException(status_code=400, detail=f"unsupported dataset {ds}")

    return {
        "dataset": ds,
        "style": style,
        "geojson": fc,
        "meta": {
            "total": total,
            "returned": len(fc["features"]),
            "limit": limit,
            "offset": offset,
            "truncated": len(fc["features"]) < total,
            "filters": {
                "utility": util,
                "year": year,
                "start_date": start.isoformat() if start else None,
                "end_date": end.isoformat() if end else None,
                "county": county,
                "tier": t,
                "incident_type": incident_type,
                "bbox": bbox,
            },
            **extra_meta,
        },
    }


@app.get("/time-series")
def time_series(
    dataset: str = Query(..., description="ignitions|us_ignitions|epss|psps|calfire"),
    interval: str = Query("weekly", description="daily|weekly|monthly"),
    year: Optional[int] = Query(None, description="Required for weekly (website year chart)"),
    utility: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    incident_type: Optional[str] = Query(None),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    ds = _parse_dataset(dataset)
    if ds not in {"ignitions", "us_ignitions", "epss", "psps", "calfire"}:
        raise HTTPException(
            status_code=400,
            detail="time-series supports ignitions|us_ignitions|epss|psps|calfire",
        )
    county = parse_county(county)
    if ds == "us_ignitions" and (utility or county):
        raise HTTPException(
            status_code=400,
            detail="us_ignitions have no utility/county attributes",
        )
    iv = interval.strip().lower()
    if iv not in {"daily", "weekly", "monthly"}:
        raise HTTPException(status_code=400, detail="interval must be daily|weekly|monthly")
    util = parse_utility(utility) if utility else None
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    if ds == "calfire":
        incident_type = parse_incident_type(conn, incident_type)

    if iv == "weekly" and year is None:
        raise HTTPException(
            status_code=400,
            detail="year is required for weekly interval (matches website calendar-week bins)",
        )
    if iv in {"daily", "monthly"} and year is not None and start is None and end is None:
        start = date(year, 1, 1)
        end = date(year, 12, 31)

    dates = queries.time_series_dates(
        conn,
        ds,
        utility=util,
        year=year if iv == "weekly" else None,
        start_date=start,
        end_date=end,
        county=county,
        incident_type=incident_type,
    )
    # For weekly, also restrict to year even if start/end passed
    if iv == "weekly" and year is not None:
        dates = [d for d in dates if d.year == year]

    try:
        buckets = aggregations.aggregate_dates(
            dates,
            interval=iv,  # type: ignore[arg-type]
            year=year,
            start=start,
            end=end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    style = style_for(ds)
    meta: dict[str, Any] = {
        "filters": {
            "utility": util,
            "year": year,
            "start_date": start.isoformat() if start else None,
            "end_date": end.isoformat() if end else None,
            "county": county,
            "incident_type": incident_type or (
                CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM if ds == "calfire" else None
            ),
        },
        "binning": "website_calendar_weeks" if iv == "weekly" else iv,
        "total_events": sum(b["count"] for b in buckets),
    }
    if ds == "us_ignitions":
        meta.update(US_IGNITIONS_META)
    else:
        meta["utility_filter_definition"] = "attribute"
    if ds == "calfire":
        calfire_filters = {
            "utility": util,
            "county": county,
            "year": year if iv == "weekly" else None,
            "start_date": start,
            "end_date": end,
            "incident_type": incident_type,
        }
        meta.update(queries.calfire_missing_counts(conn, dated_only=True, **calfire_filters))
        meta.update(queries.calfire_untagged_excluded(conn, dated_only=True, **calfire_filters))
        if county is not None:
            meta.update(
                multi_county_meta(queries.calfire_multi_county_count(conn, **calfire_filters))
            )
    return {
        "dataset": ds,
        "interval": iv,
        "color": style["color"],
        "buckets": buckets,
        "meta": meta,
    }


@app.get("/utility-territory")
def utility_territory(
    utility: str = Query(...),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    util = parse_utility(utility, allow_untagged=False)
    assert util is not None
    row = queries.utility_territory(conn, util)
    if row is None:
        raise HTTPException(status_code=404, detail=f"utility territory not found: {util}")

    geom = json.loads(row["geom"]) if row.get("geom") else None
    bounds = {
        "min_lon": row["min_lon"],
        "min_lat": row["min_lat"],
        "max_lon": row["max_lon"],
        "max_lat": row["max_lat"],
    }
    return {
        "utility": row["utility"],
        "utility_name": row["utility_name"],
        "style": IOU_STYLE,
        "geojson": {
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "utility": row["utility"],
                "utility_name": row["utility_name"],
            },
        },
        "bounds": bounds,
        "suggested_view": {
            "center": [row["center_lat"], row["center_lon"]],
            "zoom_hint": None,
            "statewide_fallback_center": STATEWIDE_CENTER,
        },
    }


@app.get("/event-detail")
def event_detail(
    dataset: str = Query(..., description="ignitions|us_ignitions|epss|psps|calfire|circuits"),
    id: str = Query(..., description="Record id (see README for per-dataset keys)"),
    year: Optional[int] = Query(
        None, description="circuits: filter embedded EPSS outages to this year"
    ),
    start_date: Optional[str] = Query(None, description="circuits: outage start_date >= "),
    end_date: Optional[str] = Query(None, description="circuits: outage start_date <= "),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    ds = _parse_dataset(dataset)
    record_id = unquote(id)
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    try:
        row = queries.event_detail(
            conn,
            ds,
            record_id,
            year=year,
            start_date=start,
            end_date=end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid id for {ds}: {record_id}") from exc

    if row is None:
        raise HTTPException(status_code=404, detail=f"{ds} record not found: {record_id}")

    geom = json.loads(row["geom"]) if row.get("geom") else None
    nested_keys = {"outages", "affected_circuits"}
    props = {k: queries._jsonable(v) for k, v in row.items() if k != "geom" and k not in nested_keys}
    style_key = ds if ds in DATASETS else "epss"
    style = style_for(style_key) if ds != "circuits" else {
        "color": "#7c3aed",
        "weight": 2.5,
        "geometry_type": "MultiLineString",
    }

    payload: dict[str, Any] = {
        "dataset": ds,
        "id": record_id,
        "style": style,
        "attributes": props,
        "detail_fields": queries.detail_field_list(ds, row),
        "geometry": geom,
    }
    if ds == "circuits":
        payload["outages"] = queries._jsonable(row.get("outages") or [])
    if ds == "psps":
        payload["affected_circuits"] = queries._jsonable(row.get("affected_circuits") or [])
    return payload
