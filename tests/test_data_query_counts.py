"""Unfiltered and source-cross-checked filtered counts."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import httpx
import psycopg

from tests.conftest import sql_count

# From inventory / website source (pre-loader exact-dupe drop).
INVENTORY_EPSS_BY_YEAR = {
    2021: 9,
    2022: 2336,
    2023: 2164,
    2024: 2788,
    2025: 2355,
}


def _epss_csv_counts(path: Path) -> tuple[Counter, Counter, list[dict]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys()) if rows else []
    raw = Counter(int(r["year"]) for r in rows)
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for row in rows:
        key = tuple(row.get(c, "") for c in fields)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    ded = Counter(int(r["year"]) for r in deduped)
    return raw, ded, deduped


def test_unfiltered_totals_match_tables(data_client: httpx.Client, db_conn: psycopg.Connection):
    expected = {
        "/ignitions": sql_count(db_conn, "SELECT count(*) FROM wildfire.cpuc_ignitions"),
        "/epss/outages": sql_count(db_conn, "SELECT count(*) FROM wildfire.epss_outages"),
        "/psps/events": sql_count(db_conn, "SELECT count(*) FROM wildfire.psps_events"),
        "/circuits": sql_count(db_conn, "SELECT count(*) FROM wildfire.circuits"),
        "/hftd": sql_count(db_conn, "SELECT count(*) FROM wildfire.hftd_tiers"),
        "/iou-territories": sql_count(db_conn, "SELECT count(*) FROM wildfire.iou_territories"),
    }
    r = data_client.get("/epss/outages", params={"limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["total"] > 0, "EPSS total is 0 — empty warehouse / commit bug?"
    assert body["data"], "EPSS returned total>0 but empty data page"

    for path, exp in expected.items():
        r = data_client.get(path, params={"limit": 1, "geometry": False})
        assert r.status_code == 200, (path, r.text)
        got = r.json()["meta"]["total"]
        assert got == exp, f"{path}: expected {exp}, got {got}"


def test_calfire_default_matches_sql_excluding_non_wildfire(
    data_client: httpx.Client, db_conn: psycopg.Connection
):
    expected = sql_count(
        db_conn,
        """
        SELECT count(*) FROM wildfire.calfire_incidents
        WHERE (incident_type IS NULL OR incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat'))
        """,
    )
    r = data_client.get("/calfire/incidents", params={"limit": 1, "geometry": False})
    assert r.status_code == 200
    got = r.json()["meta"]["total"]
    null_types = r.json()["meta"]["null_incident_type_count"]
    assert null_types == 1234
    # The default counts every untyped incident and says so in meta.
    assert r.json()["meta"]["untyped_incidents_counted"] == 1234
    assert r.json()["meta"]["filters"]["incident_type"] == "not Earthquake,Flood,Hazmat"
    assert got == 3743
    assert got == expected, (
        f"CAL FIRE default total API={got} SQL(not Earthquake/Flood/Hazmat)={expected} "
        f"(null_incident_type_count={null_types})"
    )


def test_calfire_all_matches_table(data_client: httpx.Client, db_conn: psycopg.Connection):
    expected = sql_count(db_conn, "SELECT count(*) FROM wildfire.calfire_incidents")
    r = data_client.get(
        "/calfire/incidents",
        params={"incident_type": "all", "limit": 1, "geometry": False},
    )
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == expected


def test_epss_api_matches_deduped_csv_and_db(
    data_client: httpx.Client, demo_data_dir: Path, db_conn: psycopg.Connection
):
    """API year totals must match loader semantics (exact-dupe dropped)."""
    _raw, ded, _ = _epss_csv_counts(demo_data_dir / "epss_outages.csv")
    assert sum(ded.values()) == sql_count(db_conn, "SELECT count(*) FROM wildfire.epss_outages")
    for year, exp in sorted(ded.items()):
        r = data_client.get(
            "/epss/outages",
            params={"year": year, "limit": 1, "geometry": False},
        )
        assert r.status_code == 200
        got = r.json()["meta"]["total"]
        assert got == exp, f"API EPSS year={year}: deduped CSV={exp}, API={got}"


def test_epss_inventory_year_totals_vs_api(
    data_client: httpx.Client, demo_data_dir: Path
):
    """Inventory hard-coded year totals vs live API (flags post-dedupe drift)."""
    raw, ded, _ = _epss_csv_counts(demo_data_dir / "epss_outages.csv")
    assert sum(INVENTORY_EPSS_BY_YEAR.values()) == 9652
    assert dict(raw) == INVENTORY_EPSS_BY_YEAR, (
        f"Raw CSV years drifted from inventory: csv={dict(raw)} "
        f"inventory={INVENTORY_EPSS_BY_YEAR}"
    )

    mismatches = []
    for year, inv in INVENTORY_EPSS_BY_YEAR.items():
        api = data_client.get(
            "/epss/outages",
            params={"year": year, "limit": 1, "geometry": False},
        ).json()["meta"]["total"]
        if api != inv:
            mismatches.append(
                f"year={year}: inventory/rawCSV={inv}, dedupedCSV={ded[year]}, API={api}"
            )
    assert not mismatches, (
        "EPSS year totals differ from inventory after loader exact-dupe drop: "
        + "; ".join(mismatches)
    )


def test_cpuc_counts_by_utility_match_csv(
    data_client: httpx.Client, demo_data_dir: Path
):
    expected = {"PGE": 2774, "SCE": 824, "SDGE": 133, "PACIFICORP": 14}
    with (demo_data_dir / "cpuc_fire_incidents_combined.csv").open(
        newline="", encoding="utf-8"
    ) as f:
        csv_counts = Counter(r["utility"] for r in csv.DictReader(f))
    for util, exp in expected.items():
        assert csv_counts[util] == exp, f"CSV {util}: {csv_counts[util]} != {exp}"
        r = data_client.get(
            "/ignitions",
            params={"utility": util, "limit": 1, "geometry": False},
        )
        assert r.status_code == 200
        got = r.json()["meta"]["total"]
        assert got == exp, f"API ignitions utility={util}: expected {exp}, got {got}"


def test_ignitions_county_matches_sql(data_client: httpx.Client, db_conn: psycopg.Connection):
    sql = sql_count(
        db_conn,
        """
        SELECT count(*) FROM wildfire.cpuc_ignitions
        WHERE lower(county) = 'sacramento' AND year = 2023
        """,
    )
    r = data_client.get(
        "/ignitions",
        params={"county": "Sacramento", "year": 2023, "limit": 1, "geometry": False},
    )
    assert r.status_code == 200
    body = r.json()
    got = body["meta"]["total"]
    assert got == sql
    assert got > 0
    row = body["data"][0]
    assert isinstance(row.get("latitude"), (int, float))
    assert isinstance(row.get("longitude"), (int, float))
