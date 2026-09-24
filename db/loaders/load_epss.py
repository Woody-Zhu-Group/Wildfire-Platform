"""Load EPSS outages; report circuit orphans (no FK)."""

from __future__ import annotations

import csv

import psycopg

from db.loaders.config import Settings
from db.loaders.util import (
    blank_to_none,
    normalize_circuit_id,
    parse_date,
    print_counts,
    print_step,
    report_orphans,
    table_count,
    truncate,
)
from services.shared.dataset_registry import EPSS_UNKNOWN_CAUSE, EPSS_UNKNOWN_CAUSE_SOURCE_SPELLINGS


def _normalize_cause(cause: str | None) -> str | None:
    cause = blank_to_none(cause)
    if cause is None:
        return None
    if cause.strip().lower() in EPSS_UNKNOWN_CAUSE_SOURCE_SPELLINGS:
        return EPSS_UNKNOWN_CAUSE
    return cause


def load(conn: psycopg.Connection, settings: Settings) -> int:
    path = settings.dataset_demo_data_dir / "epss_outages.csv"
    print_step(f"epss_outages ← {path}")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        raw = list(reader)
    print_counts("read", rows=len(raw))

    seen: set[tuple] = set()
    deduped: list[dict] = []
    for r in raw:
        key = tuple(r.get(c, "") for c in fieldnames)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    dropped = len(raw) - len(deduped)
    print_counts("dedupe", kept=len(deduped), exact_duplicates_dropped=dropped)

    with conn.cursor() as cur:
        cur.execute("SELECT circuit_id FROM wildfire.circuits")
        known_circuits = {row[0] for row in cur.fetchall()}

    rows = []
    orphans: set[str] = set()
    unknown_cause_normalized = 0
    for r in deduped:
        cid = normalize_circuit_id(r["circuit_id"])
        if cid not in known_circuits:
            orphans.add(cid)
        if (r.get("cause") or "").strip() == "Unknown Cause":
            unknown_cause_normalized += 1
        rows.append(
            (
                cid,
                r["circuit"],  # source `name` dropped (always equal to circuit)
                int(r["year"]),
                parse_date(r["date"]),
                parse_date(r["end_date"]),
                blank_to_none(r.get("county")),
                _normalize_cause(r.get("cause")),
                blank_to_none(r.get("outage_type")),
                blank_to_none(r.get("division")),
                int(r["customer_minutes"]),
                int(r["restoration_min"]),
                int(r["medical_baseline"]),
                int(r["life_support"]),
                int(r["schools"]),
                int(r["hospitals"]),
                float(r["lon"]),
                float(r["lat"]),
            )
        )

    print_counts("cause_normalize", unknown_cause_to_unknown=unknown_cause_normalized)
    report_orphans("epss_outages → circuits", sorted(orphans))

    with conn.transaction():
        truncate(conn, "wildfire.epss_outages")
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO wildfire.epss_outages (
                  circuit_id, circuit, year, start_date, end_date, county, cause,
                  outage_type, division, customer_minutes, restoration_min,
                  medical_baseline, life_support, schools, hospitals, geom
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                )
                """,
                rows,
            )
        inserted = len(rows)

    final = table_count(conn, "wildfire.epss_outages")
    print_counts("loaded", inserted=inserted, table_count=final)
    return final
