"""Freeze the round 2 clean test sample.

Builds the pool of original PG&E, SCE, and SDG&E post-event reports from
snapshots of the two CPUC pages (cpuc_pages/, saved 2026-09-23), removes the
10 pilot reports and every other report opened in round 1, then draws one
report per utility and era cell with a fixed seed.

    python research/psps_reports/round2/sample.py

Writes pool.csv (every eligible report) and sample.csv (the 15 picks).
"""

from __future__ import annotations

import csv
import html
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "https://www.cpuc.ca.gov"
SEED = 20260923

ERAS = [
    ("2017-2019", range(2017, 2020)),
    ("2020", range(2020, 2021)),
    ("2021-2022", range(2021, 2023)),
    ("2023-2024", range(2023, 2025)),
    ("2025-2026", range(2025, 2027)),
]
UTILITIES = ["PG&E", "SCE", "SDG&E"]

# The pilot 10, plus the three PG&E 2023 files opened in round 1 while replacing
# the broken report. None of these may enter the clean test.
SEEN = {
    "pge-oct-21-23-2020-psps-post-event-report.pdf",
    "pge-october-11-2021-psps-post-event-report.pdf",
    "pge-post-event-report-9-20-2023.pdf",
    "r1812005pge1202025-psps-postevent-report02042025.pdf",
    "r1812005-sce-psps-postevent-report-for-112421.pdf",
    "r1812005-sce-psps-post-event-report-for-11-20-23-de-energization-event.pdf",
    "r1812005-sce-psps-post-event-for-september-2-2025-de-energization.pdf",
    "sdge-sept-8-9-2020-psps-post-event-report.pdf",
    "r1812005-sdge-psps-postevent-report-112426.pdf",
    "r1812005-sdge-psps-postevent-report-nov-68-2024.pdf",
    "pge-post-event-report-9302023-event.pdf",
    "pge-psps-report-8-30-2023-event.pdf",
    "r1812005coverpge12152023-psps-postevent-report01020224.pdf",
}
SKIP_TEXT = re.compile(r"amend|correction|attachment|sed review|^\s*t?\s*$", re.I)
HEADING = re.compile(r"<(?:h\d|strong)[^>]*>\s*(PG&amp;E|SCE|SDG&amp;E|SDGE|PacifiCorp|Liberty|BVES)\s*</(?:h\d|strong)>", re.I)


OTHER_UTILITIES = ("liberty", "calpeco", "pacificorp", "bves", "bear valley")


def utility_from(text: str, filename: str, heading: str | None) -> str | None:
    probe = f"{text} {filename}".lower()
    if any(k in probe for k in OTHER_UTILITIES):
        return None
    for name, keys in (("SDG&E", ("sdg&e", "sdge")), ("PG&E", ("pg&e", "pge")), ("SCE", ("sce",))):
        if any(k in probe for k in keys):
            return name
    if heading:
        heading = html.unescape(heading).upper().replace("SDGE", "SDG&E")
        return {"PG&E": "PG&E", "SCE": "SCE", "SDG&E": "SDG&E"}.get(heading)
    return None


def build_pool() -> list[dict]:
    pool: dict[str, dict] = {}
    for page in ("current", "archive"):
        source = (HERE / "cpuc_pages" / f"{page}.html").read_text(encoding="utf-8", errors="replace")
        headings = [(m.start(), m.group(1)) for m in HEADING.finditer(source)]
        for m in re.finditer(r'<a [^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', source, re.I | re.S):
            href = m.group(1)
            text = html.unescape(" ".join(re.sub(r"<[^>]+>", "", m.group(2)).split()))
            low = href.lower()
            if not ("post-event" in low or "newsupdates/2020" in low or "/psps/2019/" in low):
                continue
            if "post-season" in low or "pre-season" in low or SKIP_TEXT.search(text) or "amend" in low:
                continue
            filename = href.rsplit("/", 1)[-1]
            if filename in SEEN:
                continue
            heading = None
            for pos, name in headings:
                if pos < m.start():
                    heading = name
            utility = utility_from(text, filename, heading)
            years = re.findall(r"20[12]\d", text)
            if utility not in UTILITIES or not years:
                continue
            url = href if href.startswith("http") else BASE + href
            pool.setdefault(url, {
                "utility": utility,
                "event_year": int(years[-1]),
                "label": text,
                "filename": filename,
                "listing": page,
                "source_url": url,
            })
    return sorted(pool.values(), key=lambda r: (r["utility"], r["event_year"], r["filename"]))


def main() -> None:
    pool = build_pool()
    rng = random.Random(SEED)
    picks = []
    for utility in UTILITIES:
        for era, years in ERAS:
            cell = [r for r in pool if r["utility"] == utility and r["event_year"] in years]
            if not cell:
                raise SystemExit(f"empty cell {utility} {era}")
            pick = dict(rng.choice(cell))
            pick["era"] = era
            pick["cell_size"] = len(cell)
            slug = {"PG&E": "pge", "SCE": "sce", "SDG&E": "sdge"}[utility]
            pick["report_id"] = f"r2_{slug}_{era.replace('-', '_')}"
            picks.append(pick)
    fields = ["utility", "event_year", "label", "filename", "listing", "source_url"]
    with open(HERE / "pool.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(pool)
    with open(HERE / "sample.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["report_id", "era", "cell_size", *fields])
        writer.writeheader()
        writer.writerows(picks)
    print(f"pool {len(pool)} reports, seed {SEED}")
    for p in picks:
        print(p["report_id"], p["event_year"], p["cell_size"], p["label"], p["filename"])


if __name__ == "__main__":
    main()
