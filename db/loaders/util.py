"""Shared helpers for PostGIS loaders."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg

from services.shared.dataset_registry import UTILITY_CODES


def connect(dsn: str) -> psycopg.Connection:
    """Open a connection from a DSN string (prefer shared.db.connect for new code)."""
    return psycopg.connect(dsn)


def apply_schema(conn: psycopg.Connection, schema_sql: Path) -> None:
    sql = schema_sql.read_text(encoding="utf-8")
    # Works with autocommit True (loaders) or an explicit transaction.
    with conn.cursor() as cur:
        cur.execute(sql)
    if not conn.autocommit:
        conn.commit()
    print(f"  applied schema: {schema_sql}")


def truncate(conn: psycopg.Connection, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
    # Caller commits with the insert transaction.


def normalize_circuit_id(value: Any) -> str:
    """Pad circuit IDs to 9 digits. Never treat as numeric."""
    s = str(value if value is not None else "").strip()
    if not s:
        raise ValueError("empty circuit_id")
    # Strip accidental .0 from floaty exports, then zfill.
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.isdigit():
        s = s.zfill(9)
    if len(s) != 9 or not s.isdigit():
        raise ValueError(f"circuit_id must be 9 digits after zfill, got {value!r} -> {s!r}")
    return s


def parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s == "":
        return None
    if s in {"y", "yes", "true", "t", "1"}:
        return True
    if s in {"n", "no", "false", "f", "0"}:
        return False
    raise ValueError(f"unrecognized boolean value: {value!r}")


def blank_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def parse_date(value: Any) -> date | None:
    value = blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    s = str(value).strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    return date.fromisoformat(s[:10])


def parse_date_null_sentinel(value: Any, sentinel: date = date(1970, 1, 1)) -> date | None:
    d = parse_date(value)
    if d == sentinel:
        return None
    return d


def parse_timestamptz(value: Any) -> datetime | None:
    value = blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_timestamptz_null_sentinel(
    value: Any, sentinel: date = date(1970, 1, 1)
) -> datetime | None:
    dt = parse_timestamptz(value)
    if dt is not None and dt.date() == sentinel:
        return None
    return dt


def parse_time_hhmm(value: Any) -> time | None:
    value = blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, time):
        return value
    s = str(value).strip()
    parts = s.split(":")
    if len(parts) < 2:
        raise ValueError(f"bad time: {value!r}")
    return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)


def as_multi_linestring_geojson(geom: dict) -> dict:
    if geom["type"] == "LineString":
        return {"type": "MultiLineString", "coordinates": [geom["coordinates"]]}
    if geom["type"] == "MultiLineString":
        return geom
    raise ValueError(f"expected LineString/MultiLineString, got {geom.get('type')}")


def as_multi_polygon_geojson(geom: dict) -> dict:
    if geom["type"] == "Polygon":
        return {"type": "MultiPolygon", "coordinates": [geom["coordinates"]]}
    if geom["type"] == "MultiPolygon":
        return geom
    raise ValueError(f"expected Polygon/MultiPolygon, got {geom.get('type')}")


def geojson_geom_sql(alias: str = "g") -> str:
    """Expression: GeoJSON text param -> geometry(4326)."""
    return f"ST_SetSRID(ST_GeomFromGeoJSON({alias}), 4326)"


def point_sql(lon_param: str, lat_param: str) -> str:
    return f"ST_SetSRID(ST_MakePoint({lon_param}, {lat_param}), 4326)"


def print_step(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def print_counts(label: str, **counts: Any) -> None:
    parts = ", ".join(f"{k}={v}" for k, v in counts.items())
    print(f"  {label}: {parts}")


def table_count(conn: psycopg.Connection, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return int(cur.fetchone()[0])


def report_orphans(
    label: str,
    orphan_ids: Sequence[str],
    sample_n: int = 15,
) -> None:
    n = len(orphan_ids)
    if n == 0:
        print(f"  {label}: 0 orphans")
        return
    sample = list(orphan_ids)[:sample_n]
    print(f"  WARNING {label}: {n} orphan circuit_id(s)")
    print(f"    sample: {sample}")


class UnknownPspsUtilityError(ValueError):
    """A PSPS source IOU spelling that matches no warehouse utility code."""


def normalize_psps_utility(iou_raw: str) -> str:
    """Map a PSPS source IOU spelling to its warehouse utility code, or raise.

    An unknown spelling fails the load instead of passing through, because a
    passed-through value would never match a utility filter and would read
    as zero events for that utility.
    """
    s = (iou_raw or "").strip().upper().replace("&", "")
    # PG&E -> PGE, SDG&E -> SDGE after & removal; also handle spaced forms
    s = s.replace(" ", "")
    # Each warehouse code, keyed by its upper-case form ("LIBERTY" -> "Liberty").
    mapping = {code.upper(): code for code in UTILITY_CODES}
    if s not in mapping:
        raise UnknownPspsUtilityError(
            f"unknown PSPS IOU spelling {iou_raw!r}; known codes: "
            f"{', '.join(UTILITY_CODES)}"
        )
    return mapping[s]


def load_geojson(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def executemany_values(
    conn: psycopg.Connection,
    sql: str,
    rows: Iterable[Sequence[Any]],
) -> int:
    rows = list(rows)
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)
