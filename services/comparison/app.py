"""FastAPI comparison service — utilities, regions, and periods."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Generator, Optional

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from services.comparison import metrics, queries
from services.data_query.filters import (
    parse_county,
    parse_date_param,
    parse_tier,
    parse_utility,
    validate_date_range,
)
from services.shared.calfire_county import MULTI_COUNTY_NOTE, multi_county_meta
from services.shared.dataset_registry import CALFIRE_DEFAULT_INCIDENT_TYPES, HFTD_TIERS
from shared.db import connect, get_settings

_db_ok: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db_ok
    settings = get_settings()
    print(f"[comparison] Startup: connecting to {settings.safe_target} ...")
    try:
        with connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT PostGIS_Version()")
                _db_ok = f"postgis {cur.fetchone()[0]}"
        print(f"[comparison] Startup OK ({_db_ok})")
    except Exception as exc:  # noqa: BLE001
        _db_ok = None
        print(f"[comparison] Startup WARNING: DB unavailable: {exc}")
    yield
    print("[comparison] Shutdown.")


app = FastAPI(
    title="Wildfire Comparison",
    description=(
        "Compare metrics across utilities, counties/HFTD tiers, or two date ranges. "
        "EPSS is PG&E-only (null + reason, never zero). Ignition counts use an explicit "
        "attribute vs spatial definition."
    ),
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
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}") from exc
    try:
        yield conn
    finally:
        conn.close()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    qs = str(request.query_params)
    print(f"[comparison] {request.method} {request.url.path}" + (f"?{qs}" if qs else ""))
    response = await call_next(request)
    print(f"[comparison] → {response.status_code} {request.url.path}")
    return response


def _require_dates(start_date: str | None, end_date: str | None) -> tuple[date, date]:
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    if start is None or end is None:
        raise HTTPException(
            status_code=400, detail="start_date and end_date are required (YYYY-MM-DD)"
        )
    validate_date_range(start, end)
    return start, end


def _parse_metric_param(metric: str) -> str:
    try:
        return metrics.parse_metric(metric)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _parse_normalize_param(normalize: str | None) -> str:
    try:
        return metrics.parse_normalize(normalize)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _metric_for_scope(
    conn: psycopg.Connection,
    *,
    metric: str,
    scope: queries.ScopeKind,
    scope_id: str,
    start: date,
    end: date,
    normalize: str,
    ignition_definition: str,
) -> dict[str, Any]:
    raw, reason = queries.raw_metric(
        conn,
        metric,
        scope=scope,
        scope_id=scope_id,
        start=start,
        end=end,
        ignition_definition=ignition_definition,
    )
    if raw is None:
        return metrics.result_row(scope_id, value=None, raw_value=None, reason=reason)

    denom, denom_reason = queries.normalization_denominator(
        conn, scope=scope, scope_id=scope_id, normalize=normalize
    )
    return metrics.apply_normalization(
        raw,
        key=scope_id,
        normalize=normalize,
        denominator=denom,
        denom_reason=denom_reason,
    )


CALFIRE_METRICS = frozenset({"calfire_incident_count", "acres_burned"})


def _calfire_multi_county_meta(
    conn: psycopg.Connection,
    *,
    metric: str,
    counties: list[str],
    ranges: list[tuple[date, date]],
) -> dict[str, Any]:
    """Multi-county meta for a county-scoped CAL FIRE compare, else empty."""
    if metric not in CALFIRE_METRICS or not counties:
        return {}
    count = queries.calfire_multi_county_count(conn, counties=counties, ranges=ranges)
    return multi_county_meta(count)


def _base_meta(
    *,
    metric: str,
    normalize: str,
    ignition_definition: str,
    filters: dict[str, Any],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "metric": metric,
        "normalize": normalize,
        "ignition_definition": ignition_definition,
        "calfire_incident_types": list(CALFIRE_DEFAULT_INCIDENT_TYPES),
        "epss_scope": "PGE-only",
        "area_method": "ST_Area(geom::geography)/1e6 km2",
        "filters": filters,
        "notes": notes or [],
    }


@app.get("/health")
def health(conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
    return {
        "status": "ok",
        "database": get_settings().safe_target,
        "detail": _db_ok,
        "metrics": sorted(metrics.METRICS),
        "definitions": {
            "ignitions_attribute": "Filter/count by utility column (default for utility compares).",
            "ignitions_spatial": "ST_Within IOU or HFTD polygon (default for HFTD compares).",
            "epss": "PG&E-only; non-PGE utilities return null with reason, not zero.",
            "calfire": "Default incident_type IN (Wildfire, Fire); untyped excluded.",
            "no_cpz": "No Circuit Protection Zone polygons in this warehouse.",
        },
    }


@app.get("/compare-utilities")
def compare_utilities(
    utilities: str = Query(..., description="Comma-separated utility codes"),
    metric: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    normalize: Optional[str] = Query("none"),
    ignition_definition: Optional[str] = Query(
        "attribute", description="attribute (default) | spatial"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    m = _parse_metric_param(metric)
    norm = _parse_normalize_param(normalize)
    try:
        ign_def = metrics.parse_ignition_definition(ignition_definition, default="attribute")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    start, end = _require_dates(start_date, end_date)

    keys: list[str] = []
    for part in utilities.split(","):
        part = part.strip()
        if not part:
            continue
        util = parse_utility(part, allow_untagged=False)
        assert util is not None
        keys.append(util)
    if not keys:
        raise HTTPException(status_code=400, detail="utilities must list at least one utility")

    results = [
        _metric_for_scope(
            conn,
            metric=m,
            scope="utility",
            scope_id=u,
            start=start,
            end=end,
            normalize=norm,
            ignition_definition=ign_def,
        )
        for u in keys
    ]
    return {
        "metric": m,
        "normalize": norm,
        "results": results,
        "meta": _base_meta(
            metric=m,
            normalize=norm,
            ignition_definition=ign_def,
            filters={
                "utilities": keys,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        ),
    }


@app.get("/compare-regions")
def compare_regions(
    region_type: str = Query(..., description="county | hftd"),
    regions: str = Query(..., description="Comma-separated county names or HFTD tiers"),
    metric: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    normalize: Optional[str] = Query("none"),
    ignition_definition: Optional[str] = Query(
        None,
        description="Default: spatial for hftd, attribute unused for county ignitions",
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    rt = region_type.strip().lower()
    if rt not in {"county", "hftd"}:
        raise HTTPException(status_code=400, detail="region_type must be county|hftd")
    m = _parse_metric_param(metric)
    norm = _parse_normalize_param(normalize)
    default_def = "spatial" if rt == "hftd" else "attribute"
    try:
        ign_def = metrics.parse_ignition_definition(ignition_definition, default=default_def)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    start, end = _require_dates(start_date, end_date)

    keys: list[str] = []
    for part in regions.split(","):
        part = part.strip()
        if not part:
            continue
        if rt == "hftd":
            keys.append(parse_tier(part) or part)
        else:
            keys.append(parse_county(part) or part)
    if not keys:
        raise HTTPException(status_code=400, detail="regions must list at least one region")
    if rt == "hftd":
        for k in keys:
            if k not in HFTD_TIERS:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown HFTD tier {k!r}; allowed: {sorted(HFTD_TIERS)}",
                )

    scope: queries.ScopeKind = "county" if rt == "county" else "hftd"
    multi_meta = (
        _calfire_multi_county_meta(conn, metric=m, counties=keys, ranges=[(start, end)])
        if scope == "county"
        else {}
    )
    results = [
        _metric_for_scope(
            conn,
            metric=m,
            scope=scope,
            scope_id=k,
            start=start,
            end=end,
            normalize=norm,
            ignition_definition=ign_def,
        )
        for k in keys
    ]
    return {
        "metric": m,
        "normalize": norm,
        "results": results,
        "meta": _base_meta(
            metric=m,
            normalize=norm,
            ignition_definition=ign_def,
            filters={
                "region_type": rt,
                "regions": keys,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
            notes=[
                "County compares use attribute county on EPSS/CAL FIRE and "
                "load-time Census PIP county on CPUC ignitions; PSPS has no "
                "county column and returns null with reason."
            ]
            + ([MULTI_COUNTY_NOTE] if multi_meta else []),
        )
        | multi_meta,
    }


@app.get("/compare-periods")
def compare_periods(
    scope_type: str = Query(..., description="utility | county | hftd"),
    scope: str = Query(...),
    metric: str = Query(...),
    period_a_start: str = Query(...),
    period_a_end: str = Query(...),
    period_b_start: str = Query(...),
    period_b_end: str = Query(...),
    normalize: Optional[str] = Query("none"),
    ignition_definition: Optional[str] = Query(None),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    st = scope_type.strip().lower()
    if st not in {"utility", "county", "hftd"}:
        raise HTTPException(status_code=400, detail="scope_type must be utility|county|hftd")
    m = _parse_metric_param(metric)
    norm = _parse_normalize_param(normalize)
    default_def = "spatial" if st == "hftd" else "attribute"
    try:
        ign_def = metrics.parse_ignition_definition(ignition_definition, default=default_def)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    a_start, a_end = _require_dates(period_a_start, period_a_end)
    # parse_date_param via _require_dates uses fixed names — remap for period b
    b_start = parse_date_param(period_b_start, "period_b_start")
    b_end = parse_date_param(period_b_end, "period_b_end")
    if b_start is None or b_end is None:
        raise HTTPException(status_code=400, detail="period_b_start and period_b_end required")
    validate_date_range(b_start, b_end)

    if st == "utility":
        scope_id = parse_utility(scope, allow_untagged=False)
        assert scope_id is not None
        qscope: queries.ScopeKind = "utility"
    elif st == "hftd":
        scope_id = parse_tier(scope)
        assert scope_id is not None
        qscope = "hftd"
    else:
        scope_id = parse_county(scope)
        if scope_id is None:
            raise HTTPException(status_code=400, detail="scope must name a county")
        qscope = "county"

    period_a = _metric_for_scope(
        conn,
        metric=m,
        scope=qscope,
        scope_id=scope_id,
        start=a_start,
        end=a_end,
        normalize=norm,
        ignition_definition=ign_def,
    )
    period_b = _metric_for_scope(
        conn,
        metric=m,
        scope=qscope,
        scope_id=scope_id,
        start=b_start,
        end=b_end,
        normalize=norm,
        ignition_definition=ign_def,
    )
    multi_meta = (
        _calfire_multi_county_meta(
            conn, metric=m, counties=[scope_id], ranges=[(a_start, a_end), (b_start, b_end)]
        )
        if qscope == "county"
        else {}
    )
    # Re-key for clarity
    period_a = {**period_a, "key": "period_a", "scope": scope_id}
    period_b = {**period_b, "key": "period_b", "scope": scope_id}

    if period_a["value"] is None or period_b["value"] is None:
        delta: dict[str, Any] = {
            "value": None,
            "reason": "Cannot compute delta when either period value is null",
        }
    else:
        delta = {
            "value": float(period_b["value"]) - float(period_a["value"]),
            "reason": None,
        }

    return {
        "metric": m,
        "normalize": norm,
        "scope_type": st,
        "scope": scope_id,
        "period_a": {
            **period_a,
            "start_date": a_start.isoformat(),
            "end_date": a_end.isoformat(),
        },
        "period_b": {
            **period_b,
            "start_date": b_start.isoformat(),
            "end_date": b_end.isoformat(),
        },
        "delta": delta,
        "meta": _base_meta(
            metric=m,
            normalize=norm,
            ignition_definition=ign_def,
            filters={
                "scope_type": st,
                "scope": scope_id,
                "period_a": [a_start.isoformat(), a_end.isoformat()],
                "period_b": [b_start.isoformat(), b_end.isoformat()],
            },
            notes=[MULTI_COUNTY_NOTE] if multi_meta else None,
        )
        | multi_meta,
    }
