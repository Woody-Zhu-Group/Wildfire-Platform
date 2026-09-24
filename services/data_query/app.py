"""FastAPI read service over the wildfire PostGIS warehouse."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Generator, Optional
from urllib.parse import unquote

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from services.data_query import queries
from services.data_query.filters import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    RANK_DEFAULT_LIMIT,
    RANK_MAX_LIMIT,
    parse_cause,
    parse_circuit_id,
    parse_county,
    parse_dataset,
    parse_date_param,
    parse_format,
    parse_incident_type,
    parse_outage_type,
    parse_tier,
    parse_utility,
    validate_date_range,
)
from services.data_query.filters import parse_bbox as parse_bbox_filter
from services.data_query.geo import respond
from services.shared.dataset_registry import (
    CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM,
    US_IGNITIONS_META_DATA_QUERY,
)
from shared.db import connect, get_settings

_db_ok: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_ok
    settings = get_settings()
    print(f"[data_query] Startup: connecting to {settings.safe_target} ...")
    try:
        with connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT PostGIS_Version()")
                ver = cur.fetchone()[0]
            _db_ok = f"postgis {ver}"
        print(f"[data_query] Startup OK ({_db_ok})")
    except Exception as exc:  # noqa: BLE001
        _db_ok = None
        print(f"[data_query] Startup WARNING: DB unavailable: {exc}")
    yield
    print("[data_query] Shutdown.")


app = FastAPI(
    title="Wildfire Data Query",
    description="Read endpoints over map-layer tables in PostGIS.",
    version="0.1.0",
    lifespan=lifespan,
)

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
        raise HTTPException(
            status_code=503,
            detail=f"Database unavailable: {exc}",
        ) from exc
    try:
        yield conn
    finally:
        conn.close()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    qs = str(request.query_params)
    print(f"[data_query] {request.method} {request.url.path}" + (f"?{qs}" if qs else ""))
    response = await call_next(request)
    print(f"[data_query] → {response.status_code} {request.url.path}")
    return response


@app.get("/health")
def health(conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    try:
        counts = queries.table_counts(conn)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "ok",
        "database": get_settings().safe_target,
        "detail": _db_ok,
        "tables": counts,
    }


@app.get("/rank")
def rank(
    dataset: str = Query(
        ...,
        description="cpuc_ignitions | calfire_incidents | epss_outages",
    ),
    group_by: str = Query(..., description="county | utility | circuit"),
    metric: str = Query("count", description="count | acres_burned"),
    utility: Optional[str] = Query(None),
    include_untagged: bool = Query(False),
    county: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    incident_type: Optional[str] = Query(
        None,
        description="CAL FIRE only. Default Wildfire|Fire. untyped | all.",
    ),
    limit: int = Query(RANK_DEFAULT_LIMIT, ge=1, le=RANK_MAX_LIMIT),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Single-dataset top-N ranking. Does not mix warehouse datasets."""
    dataset_key = parse_dataset(dataset)
    group_key = group_by.strip().lower()
    metric_key = metric.strip().lower()
    if dataset_key == "us_ignitions":
        raise HTTPException(
            status_code=400,
            detail=(
                "us_ignitions has no state attribute; ranking by state is "
                "not available"
            ),
        )
    if dataset_key == "epss_outages" and group_key == "utility":
        raise HTTPException(
            status_code=400,
            detail=(
                "EPSS outages are PG&E-only; there is no utility dimension "
                "to rank"
            ),
        )
    util = parse_utility(utility) if utility else None
    county = parse_county(county)
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    if dataset_key == "calfire_incidents":
        incident_type = parse_incident_type(conn, incident_type)
    try:
        rows, extra = queries.query_rank(
            conn,
            dataset=dataset_key,
            group_by=group_key,
            metric=metric_key,
            utility=util,
            include_untagged=include_untagged,
            county=county,
            year=year,
            start_date=start,
            end_date=end,
            incident_type=incident_type,
            limit=limit,
        )
    except queries.RankQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    canvas_metric = {
        ("cpuc_ignitions", "count"): "ignition_count",
        ("calfire_incidents", "count"): "calfire_incident_count",
        ("calfire_incidents", "acres_burned"): "acres_burned",
        ("epss_outages", "count"): "epss_outage_count",
    }.get((dataset_key, metric_key), metric_key)
    envelope = respond(
        rows,
        total=int(extra.get("total") or 0),
        limit=limit,
        offset=None,
        filters={
            "dataset": dataset_key,
            "group_by": group_key,
            "metric": metric_key,
            "utility": util,
            "include_untagged": include_untagged or None,
            "county": county,
            "year": year,
            "start_date": start,
            "end_date": end,
            "incident_type": (
                incident_type
                if incident_type is not None
                else (CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM if dataset_key == "calfire_incidents" else None)
            ),
        },
        fmt="json",
        include_geometry=False,
        extra_meta={
            k: v
            for k, v in extra.items()
            if k not in {"total", "returned", "limit"}
        },
    )
    envelope["kind"] = "ranking"
    envelope["metric"] = canvas_metric
    envelope["results"] = [
        {
            "key": row["group_value"],
            "value": row["metric_value"],
            "reason": extra.get("empty_reason"),
        }
        for row in rows
    ]
    return envelope


@app.get("/grouped-counts")
def grouped_counts(
    dataset: str = Query(
        ...,
        description="cpuc_ignitions | calfire_incidents | epss_outages | psps_events | us_ignitions",
    ),
    group_by: str = Query(..., description="cause | utility | county"),
    utility: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """All-group counts for the workspace client (not a top-N ranking)."""
    dataset_key = parse_dataset(dataset)
    group_key = group_by.strip().lower()
    util = parse_utility(utility) if utility else None
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    county_filter = parse_county(county)
    try:
        return queries.query_grouped_counts(
            conn,
            dataset=dataset_key,
            group_by=group_key,
            utility=util,
            county=county_filter,
            start_date=start,
            end_date=end,
        )
    except queries.AggregateQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/summary")
def summary(
    dataset: str = Query(
        ...,
        description="cpuc_ignitions | calfire_incidents | epss_outages | psps_events | us_ignitions",
    ),
    utility: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Filtered totals plus the dataset's workspace summary metrics."""
    dataset_key = parse_dataset(dataset)
    util = parse_utility(utility) if utility else None
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    county_filter = parse_county(county)
    try:
        return queries.query_summary(
            conn,
            dataset=dataset_key,
            utility=util,
            county=county_filter,
            start_date=start,
            end_date=end,
        )
    except queries.AggregateQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/regional-series")
def regional_series(
    interval: str = Query(..., description="daily | weekly | monthly | quarterly"),
    start_date: str = Query(..., description="YYYY-MM-DD inclusive"),
    end_date: str = Query(..., description="YYYY-MM-DD inclusive"),
    utility: Optional[str] = Query(None),
    county: Optional[str] = Query(None),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """EPSS outages bucketed by division across a gap-filled interval."""
    util = parse_utility(utility) if utility else None
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    if start is None or end is None:
        raise HTTPException(
            status_code=400, detail="start_date and end_date are required"
        )
    validate_date_range(start, end)
    county_filter = parse_county(county)
    try:
        return queries.query_regional_series(
            conn,
            start_date=start,
            end_date=end,
            utility=util,
            county=county_filter,
            interval=interval.strip().lower(),
        )
    except queries.AggregateQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/ignitions")
def ignitions(
    utility: Optional[str] = Query(None),
    include_untagged: bool = Query(False),
    county: Optional[str] = Query(None, description="Census county name, e.g. Sacramento"),
    year: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    format: str = Query("json"),
    geometry: bool = Query(True),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    util = parse_utility(utility)
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    bb = parse_bbox_filter(bbox)
    fmt = parse_format(format)
    county_filter = parse_county(county)

    rows, total = queries.query_ignitions(
        conn,
        utility=util,
        include_untagged=include_untagged,
        year=year,
        start_date=start,
        end_date=end,
        county=county_filter,
        bbox=bb,
        limit=limit,
        offset=offset,
    )
    filters = {
        "utility": util,
        "include_untagged": include_untagged or None,
        "county": county_filter,
        "year": year,
        "start_date": start,
        "end_date": end,
        "bbox": bbox,
    }
    extra = {}
    if util and util != "untagged":
        # Ignitions always have utility tags; still surface pattern consistency for CAL FIRE sibling
        pass
    return respond(
        rows,
        total=total,
        limit=limit,
        offset=offset,
        filters=filters,
        fmt=fmt,
        include_geometry=geometry,
        extra_meta=extra or None,
    )


# Discrepancy: notes text differs from visualization US_IGNITIONS_META.
US_IGNITIONS_META = US_IGNITIONS_META_DATA_QUERY


@app.get("/us-ignitions")
def us_ignitions(
    year: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    bbox: Optional[str] = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    state: Optional[str] = Query(
        None,
        description="Not supported in v1 — use bbox (no state polygons loaded)",
    ),
    format: str = Query("json"),
    geometry: bool = Query(True),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    if state is not None and state.strip() != "":
        raise HTTPException(
            status_code=400,
            detail=(
                "state filter is not available (no US state boundary layer loaded); "
                "use bbox=min_lon,min_lat,max_lon,max_lat instead"
            ),
        )
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    bb = parse_bbox_filter(bbox)
    fmt = parse_format(format)
    rows, total = queries.query_us_ignitions(
        conn,
        year=year,
        start_date=start,
        end_date=end,
        bbox=bb,
        limit=limit,
        offset=offset,
    )
    return respond(
        rows,
        total=total,
        limit=limit,
        offset=offset,
        filters={
            "year": year,
            "start_date": start,
            "end_date": end,
            "bbox": bbox,
        },
        fmt=fmt,
        include_geometry=geometry,
        extra_meta=dict(US_IGNITIONS_META),
    )


@app.get("/epss/outages")
def epss_outages(
    circuit_id: Optional[str] = Query(None),
    utility: Optional[str] = Query(None, description="PGE only; other utilities return empty"),
    county: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    outage_type: Optional[str] = Query(None),
    cause: Optional[str] = Query(None),
    bbox: Optional[str] = Query(None),
    format: str = Query("json"),
    geometry: bool = Query(True),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    cid = parse_circuit_id(circuit_id) if circuit_id else None
    util = parse_utility(utility) if utility else None
    county = parse_county(county)
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    bb = parse_bbox_filter(bbox)
    fmt = parse_format(format)
    outage_type = parse_outage_type(conn, outage_type)
    cause = parse_cause(conn, cause)

    rows, total, notes = queries.query_epss(
        conn,
        circuit_id=cid,
        utility=util,
        county=county,
        year=year,
        start_date=start,
        end_date=end,
        outage_type=outage_type,
        cause=cause,
        bbox=bb,
        limit=limit,
        offset=offset,
    )
    return respond(
        rows,
        total=total,
        limit=limit,
        offset=offset,
        filters={
            "circuit_id": cid,
            "utility": util,
            "county": county,
            "year": year,
            "start_date": start,
            "end_date": end,
            "outage_type": outage_type,
            "cause": cause,
            "bbox": bbox,
        },
        fmt=fmt,
        include_geometry=geometry,
        extra_meta=notes,
    )


@app.get("/psps/events")
def psps_events(
    utility: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    format: str = Query("json"),
    geometry: bool = Query(True),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    util = parse_utility(utility, allow_untagged=True) if utility else None
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    fmt = parse_format(format)

    rows, total = queries.query_psps_events(
        conn,
        utility=util,
        year=year,
        start_date=start,
        end_date=end,
        limit=limit,
        offset=offset,
    )
    return respond(
        rows,
        total=total,
        limit=limit,
        offset=offset,
        filters={
            "utility": util,
            "year": year,
            "start_date": start,
            "end_date": end,
        },
        fmt=fmt,
        include_geometry=geometry,
    )


@app.get("/psps/events/{event_name:path}/circuits")
def psps_event_circuits(
    event_name: str,
    format: str = Query("json"),
    geometry: bool = Query(True),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    # :path allows dates like "10/11/21" inside EventName.
    name = unquote(event_name)
    fmt = parse_format(format)
    rows = queries.query_psps_event_circuits(conn, name)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"PSPS event not found: {name}")
    missing = sum(1 for r in rows if r.get("geometry_missing"))
    return respond(
        rows,
        total=len(rows),
        limit=None,
        offset=None,
        filters={"event_name": name},
        fmt=fmt,
        include_geometry=geometry,
        extra_meta={
            "circuits_missing_geometry": missing,
            "note": (
                "Some PSPS circuit IDs have no geometry in wildfire.circuits; "
                "those are returned with geometry=null."
            ),
        },
    )


@app.get("/calfire/incidents")
def calfire_incidents(
    utility: Optional[str] = Query(None),
    include_untagged: bool = Query(
        False, description="With a named utility, also include utility IS NULL rows"
    ),
    county: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    min_acres: Optional[float] = Query(None, ge=0),
    incident_type: Optional[str] = Query(
        None,
        description="Default Wildfire|Fire only. Use 'untyped' for NULL types, 'all' for no filter.",
    ),
    format: str = Query("json"),
    geometry: bool = Query(True),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    util = parse_utility(utility) if utility else None
    county = parse_county(county)
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    fmt = parse_format(format)
    incident_type = parse_incident_type(conn, incident_type)

    rows, total, extra = queries.query_calfire(
        conn,
        utility=util,
        include_untagged=include_untagged,
        county=county,
        year=year,
        start_date=start,
        end_date=end,
        min_acres=min_acres,
        incident_type=incident_type,
        limit=limit,
        offset=offset,
    )
    if util and util != "untagged" and not include_untagged:
        extra["null_utility_excluded_by_filter"] = True
    return respond(
        rows,
        total=total,
        limit=limit,
        offset=offset,
        filters={
            "utility": util,
            "include_untagged": include_untagged or None,
            "county": county,
            "year": year,
            "start_date": start,
            "end_date": end,
            "min_acres": min_acres,
            "incident_type": incident_type or CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM,
        },
        fmt=fmt,
        include_geometry=geometry,
        extra_meta=extra,
    )


@app.get("/circuits")
def circuits(
    circuit_id: Optional[str] = Query(None),
    division: Optional[str] = Query(None),
    substation: Optional[str] = Query(None),
    format: str = Query("json"),
    geometry: bool = Query(True),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    cid = parse_circuit_id(circuit_id) if circuit_id else None
    fmt = parse_format(format)
    rows, total = queries.query_circuits(
        conn,
        circuit_id=cid,
        division=division,
        substation=substation,
        limit=limit,
        offset=offset,
    )
    return respond(
        rows,
        total=total,
        limit=limit,
        offset=offset,
        filters={"circuit_id": cid, "division": division, "substation": substation},
        fmt=fmt,
        include_geometry=geometry,
    )


@app.get("/circuits/{circuit_id}")
def circuit_by_id(
    circuit_id: str,
    format: str = Query("json"),
    geometry: bool = Query(True),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    cid = parse_circuit_id(circuit_id)
    fmt = parse_format(format)
    row = queries.get_circuit(conn, cid)
    if row is None:
        raise HTTPException(status_code=404, detail=f"circuit not found: {cid}")
    return respond(
        [row],
        total=1,
        limit=None,
        offset=None,
        filters={"circuit_id": cid},
        fmt=fmt,
        include_geometry=geometry,
    )


@app.get("/hftd")
def hftd(
    tier: Optional[str] = Query(None, description="Tier 2 or Tier 3"),
    format: str = Query("json"),
    geometry: bool = Query(True),
    simplify: Optional[float] = Query(
        None,
        gt=0,
        le=queries.SIMPLIFY_MAX_DEGREES,
        description=(
            "Optional display simplification in degrees (ST_SimplifyPreserveTopology, "
            "5-decimal coordinates). Omit for the full stored geometry that counts and "
            "point answers use. The map layers use 0.001."
        ),
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    t = parse_tier(tier)
    fmt = parse_format(format)
    rows = queries.query_hftd(conn, tier=t, simplify=simplify)
    return respond(
        rows,
        total=len(rows),
        limit=None,
        offset=None,
        filters={"tier": t},
        fmt=fmt,
        include_geometry=geometry,
        extra_meta={
            "note": "No CPZ data in warehouse (known gap).",
            "geometry_simplified_degrees": simplify,
        },
    )


@app.get("/iou-territories")
def iou_territories(
    utility: Optional[str] = Query(None),
    format: str = Query("json"),
    geometry: bool = Query(True),
    simplify: Optional[float] = Query(
        None,
        gt=0,
        le=queries.SIMPLIFY_MAX_DEGREES,
        description=(
            "Optional display simplification in degrees (ST_SimplifyPreserveTopology, "
            "5-decimal coordinates). Omit for the full stored geometry that counts and "
            "point answers use. The map layers use 0.001."
        ),
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    util = parse_utility(utility, allow_untagged=False) if utility else None
    fmt = parse_format(format)
    rows = queries.query_iou(conn, utility=util, simplify=simplify)
    return respond(
        rows,
        total=len(rows),
        limit=None,
        offset=None,
        filters={"utility": util},
        fmt=fmt,
        include_geometry=geometry,
        extra_meta={"geometry_simplified_degrees": simplify},
    )


@app.get("/spatial/point")
def spatial_point(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    snap_shoreline: bool = Query(
        False,
        description=(
            "For a point just off a mapped coastline (a Census city center), use the "
            "nearest IOU within 50 m (never from inside a hole) and the nearest county "
            "within 150 m, when exactly one is in range. HFTD and grid cell never snap."
        ),
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    return queries.spatial_point(conn, lat=lat, lon=lon, snap_shoreline=snap_shoreline)


@app.get("/spatial/summary")
def spatial_summary(
    utility: Optional[str] = Query(None),
    hftd_tier: Optional[str] = Query(None, description="Tier 2 or Tier 3"),
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    util = parse_utility(utility, allow_untagged=False) if utility else None
    tier = parse_tier(hftd_tier)
    if (util is None) == (tier is None):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of utility or hftd_tier",
        )
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="start_date and end_date are required")
    validate_date_range(start, end)
    try:
        return queries.spatial_summary(
            conn,
            utility=util,
            hftd_tier=tier,
            start_date=start,
            end_date=end,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
