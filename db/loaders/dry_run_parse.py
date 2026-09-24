"""Parse all source files without a database (CI / no-Docker smoke check)."""

from __future__ import annotations

import csv
import json
import sys

from db.loaders.config import get_settings
from db.loaders.util import (
    as_multi_linestring_geojson,
    as_multi_polygon_geojson,
    normalize_circuit_id,
    normalize_psps_utility,
    parse_bool,
    parse_date,
    parse_date_null_sentinel,
    parse_time_hhmm,
    parse_timestamptz_null_sentinel,
)


def main() -> int:
    s = get_settings()
    demo = s.dataset_demo_data_dir
    print(f"dry-run parse against {demo}")

    # circuits dedupe
    with (demo / "epss_circuits.geojson").open(encoding="utf-8") as f:
        feats = json.load(f)["features"]
    seen = {}
    dupes = 0
    for feat in feats:
        cid = normalize_circuit_id(feat["properties"]["circuit_id"])
        as_multi_linestring_geojson(feat["geometry"])
        if cid in seen:
            dupes += 1
        else:
            seen[cid] = True
    print(f"  circuits: {len(feats)} features -> {len(seen)} unique, dropped {dupes} dups")

    # epss
    with (demo / "epss_outages.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        raw = list(reader)
    keys = set()
    kept = []
    for r in raw:
        k = tuple(r.get(c, "") for c in fields)
        if k in keys:
            continue
        keys.add(k)
        kept.append(r)
        normalize_circuit_id(r["circuit_id"])
        parse_date(r["date"])
    orphans = {normalize_circuit_id(r["circuit_id"]) for r in kept} - set(seen)
    print(
        f"  epss_outages: {len(raw)} -> {len(kept)} after dedupe, "
        f"orphans vs circuits={len(orphans)}"
    )

    # psps
    with (demo / "psps_events.geojson").open(encoding="utf-8") as f:
        psps = json.load(f)["features"]
    for feat in psps:
        as_multi_polygon_geojson(feat["geometry"])
        normalize_psps_utility(feat["properties"]["IOU"])
    print(f"  psps_events: {len(psps)}")

    with (demo / "psps_event_circuits.json").open(encoding="utf-8") as f:
        pec = json.load(f)
    n_links = sum(len(v) for v in pec.values())
    print(f"  psps_event_circuits: {len(pec)} events, {n_links} links")

    # cpuc
    with (demo / "cpuc_fire_incidents_combined.csv").open(newline="", encoding="utf-8") as f:
        n = sum(1 for _ in csv.DictReader(f))
    print(f"  cpuc_ignitions: {n}")
    with (demo / "cpuc_ignitions.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows[:5]:
        parse_time_hhmm(r["time"])
    print(f"  cpuc_ignitions_with_time: {len(rows)}")

    # calfire
    with (demo / "calfire_incidents.csv").open(newline="", encoding="utf-8") as f:
        cf = list(csv.DictReader(f))
    sent = 0
    for r in cf:
        parse_bool(r["incident_is_final"])
        parse_bool(r["calfire_incident"])
        d = parse_date_null_sentinel(r.get("incident_dateonly_created"))
        if str(r.get("incident_dateonly_created", "")).startswith("1970-01-01"):
            sent += 1
            assert d is None
        parse_timestamptz_null_sentinel(r.get("incident_date_created"))
    print(f"  calfire_incidents: {len(cf)}, sentinel dates={sent}")

    # hftd / iou (CPUC Esri JSON cache, no network) / grid
    from db.loaders.arcgis_polygons import LAYERS, features_to_rows

    for layer in LAYERS:
        if not layer.cache_path.exists():
            print(f"  {layer.name}: no cache at {layer.cache_path} (seed with "
                  "python -m db.loaders.rebuild_boundaries --fetch-only)")
            continue
        payload = json.loads(layer.cache_path.read_text(encoding="utf-8"))
        rows = features_to_rows(payload, layer)
        print(f"  {layer.name}: {len(rows)} features, rings OK")
    with (s.risk_forecasting_data_dir / "grid_cells.csv").open(newline="", encoding="utf-8") as f:
        print(f"  grid_cells: {sum(1 for _ in csv.DictReader(f))}")

    print("dry-run parse OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
