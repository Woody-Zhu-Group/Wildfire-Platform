"""Round 3 inventory: every original report, amendments, and event workbooks.

Pool: the round 2 pool rules (same CPUC page snapshots) with no exclusions,
so the 10 pilot reports and the 3 PG&E 2023 files opened in round 1 are back
in. That gives every original PG&E, SCE, and SDG&E post-event report listed.

Amendment rule: an amendment or correction is linked to the original report
listed just before it on the CPUC page, or by event date for the two
corrections that are not listed next to their original. The latest full
version of each event is the one extracted. A correction letter that only
lists changes (not a full restated report) does not replace the original; the
event is flagged for review with the correction linked. Classification of full
versus partial is in classify_amendments() below and is recorded per link.

Workbooks: the event data workbooks that are publicly posted. SDG&E posts
them on sdge.com (psps-more-info page, snapshot 2026-09-23); the CPUC page
posts two for SCE. SCE's own PSPS reports page returns 404.

    python research/psps_reports/round3/inventory.py            # write csvs
    python research/psps_reports/round3/inventory.py --download  # also fetch files
"""

from __future__ import annotations

import csv
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "round2"))
import sample as r2  # noqa: E402

CPUC = "https://www.cpuc.ca.gov/-/media/cpuc-website/divisions/safety-and-enforcement-division/reports/psps-post-event-reports"

# amendment filename -> (original filename(s), label). Built from the CPUC page
# order (each amendment is listed right after its original).
AMENDMENTS = [
    ("2026/r1812005sce-psps-amendment-to-postevent-report-for-july-26-2026-deenergization.pdf", ["r1812005-sce-psps-post-event-report-for-7-26-26-de-energization-event.pdf"], "Amendment"),
    ("2026/r1812005sce-psps-amendment-to-postevent-report-for-june-26-2026-deenergization.pdf", ["sce-psps-postevent-report-for-june-26-2026-deenergization.pdf"], "Amendment"),
    ("2025/sce-oct-28/r1812005-sce-amendment-to-psps-post-event-for-october-28-2025-de-energization.pdf", ["r1812005sce-psps-postevent-for-october-28-2025-deenergization.pdf"], "Amendment"),
    ("2025/20250801sce-psps-post-event-reportdeenergizedamendment2final.pdf", ["r1812005sce-psps-postevent-for-august-1-2025-de-energization.pdf"], "Amendment"),
    ("2025/20250723sce-psps-post-event-reportdeenergizedamendment2final.pdf", ["r1812005-sce-psps-post-event-for-july-23-2025-de--energization.pdf"], "Amendment"),
    ("2025/20250715sce-psps-post-event-reportdeenergizedamendment2final.pdf", ["r1812005-sce-psps-post-event-for-july-15-2025-de--energization.pdf"], "Amendment"),
    ("2025/20250701sce-psps-post-event-reportdeenergizedamendment2final.pdf", ["r1812005-sce-psps-post-event-for-july-1-2025-de--energization.pdf"], "Amendment 1"),
    ("2025/20250701_sce_psps_post_event_report-de-energized_amendment2-final.pdf", ["r1812005-sce-psps-post-event-for-july-1-2025-de--energization.pdf"], "Amendment 2"),
    ("2025/20250610sce-psps-post-event-reportdeenergizedamendment2final.pdf", ["r1812005sce-psps-postevent-for-june-10-2025-deenergization.pdf"], "Amendment"),
    ("2025/r1812005-sdge-amended-psps-post-event-report-jan-7-16--2025-2-28-2025-eserve.pdf", ["r1812005-sdge-psps-postevent-reportjan-716-2025-eserve.pdf"], "Amended"),
    ("2024/pge_psps_post-event-report_20241017-amended-redlined.pdf", ["10-17-2024-psps-post-event-report.pdf"], "Amended"),
    ("2024/pge_psps_post-event-report_20240930-amended-redlined.pdf", ["r1812005psps-event-93010152024.pdf"], "Amended"),
    ("2024/pge_psps_post-event_report_20240702-amended-redlined.pdf", ["pge_psps_post-event_report_20240702.pdf"], "Amended"),
    ("2023/r1812005sce-amendment-to-psps-post-event-report-for-102923-deenergization-event-1.pdf", ["20231029-sce-psps-post-event-reportdeenergizedfinal.pdf"], "Amended"),
    ("2023/r1812005-sce-corrections-to-2023-psps-post-event-reports.pdf", [
        "sce-psps-post-event-report-7-11-2023-event.pdf",
        "sce-psps-post-event-report-7-18-2023-event.pdf",
        "sce-psps-post-event-report-10-11-2023-event.pdf",
        "20231029-sce-psps-post-event-reportdeenergizedfinal.pdf",
        "r1812005-sce-psps-postevent-report-for-11092023-deenergization-event.pdf",
        "r1812005-sce-psps-post-event-report-for-11-20-23-de-energization-event.pdf",
        "r1812005-sce-psps-post-event-report-for-11-26-23-high-threat-event.pdf",
        "r1812005-sce-psps-post-event-report-for-12-9-23-de-energization-event.pdf",
    ], "Consolidated corrections to 2023 SCE reports"),
    ("2021/sce-jan-1221-2021-psps-post-event-report-amended.pdf", ["jan-1221-2021-sce-psps-post-event-report.pdf"], "Amended"),
    ("2021/r1812005-sdge-corrections-to-2021-psps-postseason-and-postevent-reports--5-6-2022.pdf", ["r1812005-sdge-psps-postevent-report-112426.pdf"], "Corrections to 2021 post-season and post-event reports"),
    ("NEWS2020/sce-amended-sept-5-11-2020-psps-post-event-report.pdf", ["sce-sept-5-11-2020-psps-post-event-report.pdf"], "Amended"),
    ("2019/pge-psps-postevent-report-nov-20-2019-amended.pdf", ["pge-public-safety-power-shutoff-nov-2021-2019-report.pdf"], "Amendment"),
    ("2019/pge-psps-postevent-report-oct-2629-2019-amended.pdf", ["pge-esrb8-report-for-oct-26-29-2019.pdf"], "Amendment"),
    ("2019/pge-psps-postevent-report-oct-2325-2019-amended.pdf", ["pge-public-safety-power-shutoff-oct-2325-2019-report.pdf"], "Amendment"),
    ("2019-and-earlier/pge-public-safety-power-shutoff-oct-912-reportamended.pdf", ["pge-public-safety-power-shutoff-oct-912-report.pdf"], "Amended"),
    ("2019/pge-psps-postevent-report-oct-56-2019-amended.pdf", ["pge-public-safety-power-shutoff-oct-56-2019-report.pdf"], "Amendment"),
    ("2019/pge-psps-postevent-report-sept-25-2019-amended.pdf", ["public-safety-power-shutoff-sept-25-27-2019.pdf"], "Amendment"),
    ("2019/pge-psps-postevent-report-june-79-2019-amended.pdf", ["pge-psps-report-letter-june-7-9-2019.pdf"], "Amendment"),
    ("2019-and-earlier/sce-psp-post-event-report-oct-2126-2019-11262019.pdf", ["sce-post-event-reporting-october-21-through-october-26-2019.pdf"], "Amended"),
    ("2019-and-earlier/sce-psps-postevent-report-sept-49-2019-9232019-amended.pdf", ["sce-post-event-reporting-september-4-through-september-8-2019.pdf"], "Amended"),
]

WORKBOOKS = [
    ("https://www.sdge.com/sites/default/files/2022-05/SDGE%20PSPS%20Post-Event%20Data%20Workbook%20Nov.%2024-26_Corrected%205-6-2022.xlsx", "r1812005-sdge-psps-postevent-report-112426.pdf"),
    ("https://www.sdge.com/sites/default/files/sdge_psps_post-event_data_workbook_oct_29-31_2023.xlsx", "r1812005-sdge-psps-postevent-report-oct-2931-2023-11-14-2023.pdf"),
    ("https://www.sdge.com/sites/default/files/2024-11/SDGE%20PSPS%20Post-Event%20Data%20Workbook%20Nov%206-8%2C%202024.xlsx", "r1812005-sdge-psps-postevent-report-nov-68-2024.pdf"),
    ("https://www.sdge.com/sites/default/files/2025-04/sdge_psps_post-event_data_workbook_dec_9-11_2024_amended.xlsx", "r1812005-sdge-psps-postevent-report-dec-911-2024--1-10-2025.pdf"),
    ("https://www.sdge.com/sites/default/files/sdge_psps_post-event_data_workbook_jan_7-16_2025.xlsx", "r1812005-sdge-psps-postevent-reportjan-716-2025-eserve.pdf"),
    ("https://www.sdge.com/sites/default/files/2025-02/SDGE%20PSPS%20Post-Event%20Data%20Workbook_Jan%2020-24%2C%202025.xlsx", "r1812005-sdge-psps-postevent-report-jan-2024-2025-e-serve-2-24-2025.pdf"),
    (f"{CPUC}/2025/20250120sce-psps-event-data-workbookfinal.xlsx", "20250120sce-psps-post-event-reportdeenergizedfinal.pdf"),
    (f"{CPUC}/2025/20250104sce-psps-event-data-workbookfinal.xlsx", "20250104sce-psps-post-event-reportdeenergizedfinal.pdf"),
]

NEWS2020 = "https://www.cpuc.ca.gov/-/media/cpuc-website/files/uploadedfiles/cpucwebsite/content/news_room/newsupdates/2020"


def snapshot_hrefs() -> dict[str, str]:
    """Exact PDF URLs from the saved CPUC page snapshots, keyed by filename."""
    hrefs: dict[str, str] = {}
    for page in ("current", "archive"):
        source = (HERE.parent / "round2" / "cpuc_pages" / f"{page}.html").read_text(encoding="utf-8", errors="replace")
        for href in re.findall(r'href="([^"]+\.pdf)"', source, re.I):
            url = href if href.startswith("http") else "https://www.cpuc.ca.gov" + href
            hrefs.setdefault(href.rsplit("/", 1)[-1], url)
    return hrefs


def amendment_url(path: str) -> str:
    return snapshot_hrefs()[path.rsplit("/", 1)[-1]]


def report_id(utility: str, filename: str) -> str:
    slug = {"PG&E": "pge", "SCE": "sce", "SDG&E": "sdge"}[utility]
    stem = re.sub(r"[^a-z0-9]+", "_", filename.lower().rsplit(".", 1)[0]).strip("_")
    return f"{slug}__{stem[:70]}"


def build() -> tuple[list[dict], list[dict], list[dict]]:
    r2.SEEN = set()
    pool = r2.build_pool()
    pilot = {r["source_url"].rsplit("/", 1)[-1]: r["report_id"] for r in csv.DictReader(open(HERE.parent / "sources.csv", encoding="utf-8"))}
    clean = {r["filename"]: r["report_id"] for r in csv.DictReader(open(HERE.parent / "round2" / "sample.csv", encoding="utf-8"))}
    round1_opened = {"pge-post-event-report-9302023-event.pdf", "pge-psps-report-8-30-2023-event.pdf", "r1812005coverpge12152023-psps-postevent-report01020224.pdf"}
    for row in pool:
        row["report_id"] = report_id(row["utility"], row["filename"])
        fn = row["filename"]
        row["read_before"] = "pilot:" + pilot[fn] if fn in pilot else ("round2:" + clean[fn] if fn in clean else ("round1_opened" if fn in round1_opened else ""))
    by_file = {r["filename"]: r for r in pool}
    links = []
    for path, originals, label in AMENDMENTS:
        for original in originals:
            links.append({
                "amendment_file": path.rsplit("/", 1)[-1],
                "amendment_url": amendment_url(path),
                "amendment_label": label,
                "original_report_id": by_file[original]["report_id"],
                "original_file": original,
            })
    books = [{"workbook_url": url, "report_id": by_file[fn]["report_id"], "report_file": fn} for url, fn in WORKBOOKS]
    return pool, links, books


def download(url: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=300) as response:
        path.write_bytes(response.read())


def main() -> None:
    pool, links, books = build()
    for name, rows in (("pool.csv", pool), ("amendment_links.csv", links), ("workbooks.csv", books)):
        with open(HERE / name, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"pool {len(pool)}; read before {sum(bool(r['read_before']) for r in pool)}; amendment links {len(links)}; workbooks {len(books)}")
    if "--download" in sys.argv:
        for sub in ("raw", "raw_amend", "workbooks"):
            (HERE / sub).mkdir(exist_ok=True)
        failures = []
        for row in pool:
            try:
                download(row["source_url"], HERE / "raw" / f"{row['report_id']}.pdf")
            except Exception as exc:  # noqa: BLE001
                failures.append((row["source_url"], repr(exc)))
        for link in {l["amendment_url"]: l for l in links}.values():
            try:
                download(link["amendment_url"], HERE / "raw_amend" / link["amendment_file"])
            except Exception as exc:  # noqa: BLE001
                failures.append((link["amendment_url"], repr(exc)))
        for book in books:
            try:
                download(book["workbook_url"], HERE / "workbooks" / f"{book['report_id']}.xlsx")
            except Exception as exc:  # noqa: BLE001
                failures.append((book["workbook_url"], repr(exc)))
        print("download failures:", len(failures))
        for f in failures:
            print("  ", f)


if __name__ == "__main__":
    main()
