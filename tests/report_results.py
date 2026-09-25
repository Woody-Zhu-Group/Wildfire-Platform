"""Run verification tests and print a pass/fail table."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JUNIT = REPO_ROOT / "tests" / "_results.xml"

CASE_LABELS = {
    "test_unfiltered_totals_match_tables": "Unfiltered totals match table counts",
    "test_calfire_default_matches_sql_excluding_non_wildfire": "CAL FIRE default == SQL excluding non-wildfire types",
    "test_calfire_all_matches_table": "CAL FIRE incident_type=all == table count",
    "test_epss_api_matches_deduped_csv_and_db": "EPSS by year: API == deduped CSV/DB",
    "test_epss_inventory_year_totals_vs_api": "EPSS by year: inventory (incl. dupe) vs API",
    "test_cpuc_counts_by_utility_match_csv": "CPUC ignitions by utility (CSV + API)",
    "test_calfire_untyped_returns_exactly_null_types": "CAL FIRE untyped == 1234",
    "test_calfire_untagged_returns_exactly_282": "CAL FIRE untagged == 282",
    "test_circuit_leading_zero_round_trip": "Leading-zero circuit 043371102 round-trip",
    "test_example_circuit_012041102_is_absent": "Example circuit 012041102 absent (documented)",
    "test_psps_orphans_returned_with_null_geometry": "PSPS orphans null geometry (17 IDs)",
    "test_pagination_epss_year_2021_no_dupes_or_drops": "Pagination EPSS 2021 no dupes/drops",
    "test_pagination_epss_year_2024_full_coverage": "Pagination EPSS 2024 full coverage",
    "test_geojson_format_valid_feature_collection": "GeoJSON ignitions FeatureCollection",
    "test_geojson_psps_polygons": "GeoJSON PSPS polygons",
    "test_spatial_point_known_coordinates": "spatial/point known CA coords",
    "test_spatial_summary_matches_st_within_sql": "spatial/summary == SQL ST_Within",
    "test_params_file_present_on_disk": "Risk params file on disk",
    "test_health_model_loaded": "Risk /health model_loaded",
    "test_predict_varies_by_cell_and_date": "Risk /predict varies by cell/date",
    "test_predict_invalid_inputs": "Risk /predict invalid inputs",
    "test_health": "Comparison /health",
    "test_compare_utilities_ignitions_pge_sce_2024": "Compare utilities PGE/SCE ignitions 2024",
    "test_epss_null_for_sce": "Compare EPSS null for SCE",
    "test_epss_to_ignition_ratio_null_components": "Compare EPSS/ignition ratio null components",
    "test_compare_regions_hftd_spatial": "Compare regions HFTD spatial ignitions",
    "test_compare_periods_delta": "Compare periods PGE ignitions delta",
}


def main() -> int:
    if JUNIT.exists():
        JUNIT.unlink()
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-v",
        "--tb=short",
        f"--junitxml={JUNIT}",
    ]
    print("Running:", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), text=True)
    print()

    if not JUNIT.exists():
        print("ERROR: junit xml not written")
        return 1

    root = ET.parse(JUNIT).getroot()
    # pytest may nest testsuites
    cases = root.findall(".//testcase")
    rows: list[tuple[str, str, str]] = []
    for case in cases:
        name = case.attrib.get("name", "")
        base = name.split("[")[0]
        label = CASE_LABELS.get(base, base)
        if case.find("failure") is not None:
            status = "FAILED"
            detail = (case.find("failure").attrib.get("message") or "")[:120]
        elif case.find("error") is not None:
            status = "ERROR"
            detail = (case.find("error").attrib.get("message") or "")[:120]
        elif case.find("skipped") is not None:
            status = "SKIPPED"
            detail = ""
        else:
            status = "PASSED"
            detail = ""
        rows.append((label, status, detail))

    print("=" * 96)
    print(f"{'Case':<58} {'Result':<8} Detail")
    print("-" * 96)
    for label, status, detail in rows:
        print(f"{label:<58} {status:<8} {detail}")
    print("=" * 96)
    passed = sum(1 for _, s, _ in rows if s == "PASSED")
    failed = sum(1 for _, s, _ in rows if s in ("FAILED", "ERROR"))
    skipped = sum(1 for _, s, _ in rows if s == "SKIPPED")
    print(f"TOTAL: {passed} passed, {failed} failed, {skipped} skipped, {len(rows)} cases")
    return 0 if failed == 0 and proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
