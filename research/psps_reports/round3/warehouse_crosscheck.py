"""Cross-check round 3 dataset.csv against the warehouse table wildfire.psps_events.

Read-only queries against the local PostGIS warehouse (loaded from
dataset_demo psps_events.geojson). No API calls.

Matching: same utility, and the event windows overlap within one day. The
dataset window runs from the first de-energization date to the last
restoration date; when those are null it falls back to the event date in the
CPUC listing label. The warehouse window runs from the earlier of
first_date_of_poc and deenergization_start_date to full_restoration_date. Pairs
are matched one to one, closest start date first.

Compared: customers de-energized, first shutoff date, last restoration date
(the warehouse has dates only, no times), and counties. Warehouse counties are
the counties whose area overlaps the event polygon by at least 1 percent of
the polygon's area.

    python research/psps_reports/round3/warehouse_crosscheck.py
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
sys.path.insert(0, str(REPO))

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
UTIL = {"PGE": "PG&E", "SCE": "SCE", "SDGE": "SDG&E"}


def label_date(label: str) -> date | None:
    m = re.search(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2})(?:\s*[-–]\s*(?:[A-Z][a-z]+\.?\s*)?\d{1,2})?,?\s+(20\d\d)", label)
    if not m:
        return None
    month = MONTHS.get(m.group(1).lower()[:3])
    return date(int(m.group(3)), month, int(m.group(2))) if month else None


def as_date(value: str) -> date | None:
    if not value or value in ("None", "null"):
        return None
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def main() -> None:
    from shared.db import connect

    dataset = list(csv.DictReader(open(HERE / "dataset.csv", encoding="utf-8")))
    with connect() as conn:
        conn.read_only = True
        cur = conn.cursor()
        cur.execute(
            """
            select e.event_name, e.utility, e.first_date_of_poc, e.deenergization_start_date,
                   e.full_restoration_date, e.customers_deenergized,
                   (select count(*) from wildfire.counties c
                     where st_intersects(c.geom, e.geom)
                       and st_area(st_intersection(c.geom, st_makevalid(e.geom))) >= 0.01 * st_area(st_makevalid(e.geom))) as n_counties,
                   (select string_agg(c.name, '; ' order by c.name) from wildfire.counties c
                     where st_intersects(c.geom, e.geom)
                       and st_area(st_intersection(c.geom, st_makevalid(e.geom))) >= 0.01 * st_area(st_makevalid(e.geom))) as county_names
            from wildfire.psps_events e
            where e.utility in ('PGE', 'SCE', 'SDGE')
            """
        )
        warehouse = [dict(zip(["event_name", "utility", "poc", "start", "end", "customers", "n_counties", "county_names"], r)) for r in cur.fetchall()]

    events = []
    for d in dataset:
        start = as_date(d["first_deenergization"]) or label_date(d["label"])
        end = as_date(d["last_restoration"]) or start
        events.append({**d, "_start": start, "_end": end})

    pairs = []
    for w in warehouse:
        w_start = min(x for x in (w["poc"], w["start"]) if x)
        w_end = w["end"] or w_start
        for e in events:
            if e["utility"] != UTIL[w["utility"]] or not e["_start"]:
                continue
            if e["_start"] <= w_end + timedelta(days=1) and w_start <= e["_end"] + timedelta(days=1):
                pairs.append((abs((e["_start"] - (w["start"] or w_start)).days), e["report_id"], w["event_name"]))
    pairs.sort()
    used_e, used_w, matched = set(), set(), {}
    for _, rid, name in pairs:
        if rid in used_e or name in used_w:
            continue
        used_e.add(rid)
        used_w.add(name)
        matched[name] = rid

    by_id = {e["report_id"]: e for e in events}
    rows = []
    for w in sorted(warehouse, key=lambda w: (w["utility"], w["start"] or w["poc"])):
        rid = matched.get(w["event_name"])
        e = by_id.get(rid) if rid else None
        row = {
            "match": "both" if e else "warehouse_only",
            "utility": UTIL[w["utility"]],
            "report_id": rid or "",
            "warehouse_event": w["event_name"],
            "dataset_customers": to_int(e["customers_deenergized"]) if e else "",
            "warehouse_customers": w["customers"],
            "customers_diff": "",
            "customers_diff_pct": "",
            "dataset_first_date": e["_start"] if e else "",
            "warehouse_first_date": w["start"],
            "first_date_diff_days": "",
            "dataset_last_date": as_date(e["last_restoration"]) if e else "",
            "warehouse_last_date": w["end"],
            "last_date_diff_days": "",
            "dataset_counties": to_int(e["counties_deenergized"]) if e else "",
            "warehouse_counties": w["n_counties"],
            "dataset_county_names": e["counties_deenergized_names"] if e else "",
            "warehouse_county_names": w["county_names"] or "",
            "dataset_time_source": e["first_deenergization_source"] if e else "",
            "dataset_first_is_listing_date": (not as_date(e["first_deenergization"])) if e else "",
        }
        if e:
            dc, wc = row["dataset_customers"], w["customers"]
            if dc is not None and wc is not None:
                row["customers_diff"] = dc - wc
                row["customers_diff_pct"] = round(100 * (dc - wc) / wc, 2) if wc else ""
            # Compare first dates only when the dataset has a stated time, not the listing-date fallback.
            if as_date(e["first_deenergization"]) and w["start"]:
                row["first_date_diff_days"] = (row["dataset_first_date"] - w["start"]).days
            if row["dataset_last_date"] and w["end"]:
                row["last_date_diff_days"] = (row["dataset_last_date"] - w["end"]).days
        rows.append(row)

    first = min(w["start"] or w["poc"] for w in warehouse)
    last = max(w["end"] or w["start"] for w in warehouse)
    for e in events:
        if e["report_id"] in used_e:
            continue
        customers = to_int(e["customers_deenergized"])
        in_window = bool(e["_start"]) and first <= e["_start"] <= last
        rows.append({
            "match": ("dataset_only_in_window" if in_window else "dataset_only_outside_window") + ("" if customers else "_no_shutoff"),
            "utility": e["utility"], "report_id": e["report_id"], "warehouse_event": "",
            "dataset_customers": customers if customers is not None else "", "warehouse_customers": "",
            "customers_diff": "", "customers_diff_pct": "",
            "dataset_first_date": e["_start"] or "", "warehouse_first_date": "", "first_date_diff_days": "",
            "dataset_last_date": as_date(e["last_restoration"]) or "", "warehouse_last_date": "", "last_date_diff_days": "",
            "dataset_counties": to_int(e["counties_deenergized"]) if e["counties_deenergized"] not in ("", "None") else "",
            "warehouse_counties": "", "dataset_county_names": e["counties_deenergized_names"], "warehouse_county_names": "",
            "dataset_time_source": e["first_deenergization_source"],
            "dataset_first_is_listing_date": not as_date(e["first_deenergization"]),
        })

    with open(HERE / "warehouse_crosscheck.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    both = [r for r in rows if r["match"] == "both"]
    print(f"warehouse IOU events {len(warehouse)} ({first} to {last}); matched {len(both)}; "
          f"warehouse only {sum(r['match'] == 'warehouse_only' for r in rows)}")
    from collections import Counter
    print(Counter(r["match"] for r in rows))
    cust = [r for r in both if r["customers_diff"] != ""]
    print("customers exact", sum(r["customers_diff"] == 0 for r in cust), "of", len(cust))
    print("first date exact", sum(r["first_date_diff_days"] == 0 for r in both if r["first_date_diff_days"] != ""),
          "of", sum(r["first_date_diff_days"] != "" for r in both))
    print("last date exact", sum(r["last_date_diff_days"] == 0 for r in both if r["last_date_diff_days"] != ""),
          "of", sum(r["last_date_diff_days"] != "" for r in both))
    print("counties exact", sum(r["dataset_counties"] == r["warehouse_counties"] for r in both if r["dataset_counties"] != ""),
          "of", sum(r["dataset_counties"] != "" for r in both))


if __name__ == "__main__":
    main()
