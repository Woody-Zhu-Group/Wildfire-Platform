"""Shared filter parsing and validation for the data query API."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException, Query

from services.shared.counties import UnknownCountyError, normalize_county

KNOWN_UTILITIES = frozenset(
    {"PGE", "SCE", "SDGE", "PACIFICORP", "Liberty", "BVES"}
)
HFTD_TIERS = frozenset({"Tier 2", "Tier 3"})
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
RANK_DEFAULT_LIMIT = 10
RANK_MAX_LIMIT = 25


def parse_circuit_id(value: str) -> str:
    s = str(value).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.isdigit():
        s = s.zfill(9)
    if len(s) != 9 or not s.isdigit():
        raise HTTPException(
            status_code=400,
            detail=f"circuit_id must be a 9-digit string (leading zeros preserved); got {value!r}",
        )
    return s


def parse_utility(value: str | None, *, allow_untagged: bool = True) -> str | None:
    if value is None or value.strip() == "":
        return None
    raw = value.strip()
    # Accept common ampersand, spacing, and full-name forms.
    key = (
        raw.upper()
        .replace("&", "")
        .replace(".", "")
        .replace("-", "")
        .replace(",", "")
        .replace(" ", "")
    )
    mapping = {
        "PGE": "PGE",
        "PACIFICGASANDELECTRIC": "PGE",
        "PACIFICGASELECTRIC": "PGE",
        "SCE": "SCE",
        "SOUTHERNCALIFORNIAEDISON": "SCE",
        "EDISON": "SCE",
        "SDGE": "SDGE",
        "SANDIEGOGASANDELECTRIC": "SDGE",
        "SANDIEGOGASELECTRIC": "SDGE",
        "PACIFICORP": "PACIFICORP",
        "PACIFICPOWER": "PACIFICORP",
        "LIBERTY": "Liberty",
        "LIBERTYUTILITIES": "Liberty",
        "BVES": "BVES",
        "BEARVALLEY": "BVES",
        "BEARVALLEYELECTRIC": "BVES",
        "BEARVALLEYELECTRICSERVICE": "BVES",
        "BEARVALLEYELECTRICSERVICES": "BVES",
        "UNTAGGED": "untagged",
    }
    if key not in mapping:
        # "Pacific Gas & Electric Company", "Liberty Utilities Inc": drop a
        # trailing corporate word and try again.
        for suffix in ("COMPANY", "CORPORATION", "UTILITIES", "UTILITY", "INC", "CORP"):
            if key.endswith(suffix) and key[: -len(suffix)] in mapping:
                key = key[: -len(suffix)]
                break
    if key not in mapping:
        allowed = sorted(KNOWN_UTILITIES) + (["untagged"] if allow_untagged else [])
        raise HTTPException(
            status_code=400,
            detail=f"unknown utility {value!r}; allowed: {', '.join(allowed)}",
        )
    resolved = mapping[key]
    if resolved == "untagged" and not allow_untagged:
        raise HTTPException(status_code=400, detail="utility=untagged is not valid here")
    return resolved


def parse_date_param(value: str | None, name: str) -> date | None:
    if value is None or value.strip() == "":
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{name} must be YYYY-MM-DD; got {value!r}",
        ) from exc


def validate_date_range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=400,
            detail=f"start_date ({start}) must be <= end_date ({end})",
        )


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None or value.strip() == "":
        return None
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 4:
        raise HTTPException(
            status_code=400,
            detail="bbox must be min_lon,min_lat,max_lon,max_lat",
        )
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="bbox values must be numbers") from exc
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(
            status_code=400,
            detail="bbox requires min_lon < max_lon and min_lat < max_lat",
        )
    return min_lon, min_lat, max_lon, max_lat


def parse_tier(value: str | None) -> str | None:
    """Resolve "Tier 2", "tier 3", "T2", "2", or "HFTD Tier 3" to the stored value.

    Anything else is a 400, never an empty result.
    """
    if value is None or value.strip() == "":
        return None
    key = value.strip().lower().replace("hftd", "").replace("-", " ").replace("_", " ")
    key = " ".join(key.split())
    for prefix in ("tier ", "tier", "t "):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    if key.startswith("t") and key[1:].strip().isdigit():
        key = key[1:].strip()
    resolved = {"2": "Tier 2", "3": "Tier 3"}.get(key.strip())
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=f"tier must be one of {sorted(HFTD_TIERS)}; got {value!r}",
        )
    return resolved


def parse_county(value: str | None) -> str | None:
    """Resolve a county filter to the canonical warehouse name, or 400.

    "Butte County", "butte", and "LA" all resolve. A value that matches no
    California county is rejected with the closest names, because an
    unmatched exact-match filter used to return 0 rows and read as a real
    count.
    """
    if value is None or value.strip() == "":
        return None
    try:
        return normalize_county(value)
    except UnknownCountyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def parse_pagination(
    limit: int | None = None,
    offset: int | None = None,
) -> tuple[int, int]:
    lim = DEFAULT_LIMIT if limit is None else limit
    off = 0 if offset is None else offset
    if lim < 1 or lim > MAX_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"limit must be between 1 and {MAX_LIMIT}",
        )
    if off < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    return lim, off


def parse_format(value: str | None) -> str:
    fmt = (value or "json").strip().lower()
    if fmt not in {"json", "geojson"}:
        raise HTTPException(status_code=400, detail="format must be json or geojson")
    return fmt


def parse_bool_flag(value: bool | None, default: bool = True) -> bool:
    if value is None:
        return default
    return bool(value)


def common_list_params(
    year: int | None = Query(None),
    start_date: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    end_date: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    bbox: str | None = Query(None, description="min_lon,min_lat,max_lon,max_lat"),
    format: str | None = Query("json", description="json | geojson"),
    geometry: bool = Query(True, description="Include geometry in response"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    start = parse_date_param(start_date, "start_date")
    end = parse_date_param(end_date, "end_date")
    validate_date_range(start, end)
    return {
        "year": year,
        "start_date": start,
        "end_date": end,
        "bbox": parse_bbox(bbox),
        "format": parse_format(format),
        "geometry": geometry,
        "limit": limit,
        "offset": offset,
    }
