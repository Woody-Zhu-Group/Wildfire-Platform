"""Split review_queue.csv between two reviewers with a 40-item overlap.

- Overlap: whole reports are drawn at random (seed 20260926) until they hold
  40 items; if the last report overshoots, only its first items by queue order
  are kept so the overlap is exactly 40. Both reviewers review these
  independently, which gives an agreement rate.
- The remaining reports are split between reviewers A and B, largest report
  first, each going to whoever has fewer items, so each report's items stay with
  one reviewer.
- Each reviewer file is sorted by report, then field, so a reviewer works one
  PDF at a time.
- Reviewer files leave out Jev's answer and confidence so the reviewer decides
  first. Those are in review_answer_key.csv (item, answer, confidence), to be
  opened only after the reviewer has recorded a decision. Items where the
  flag itself carries values (conflicting sources) keep their detail column,
  because the question cannot be asked without it.

    python research/psps_reports/round3/split_queue.py
"""

from __future__ import annotations

import csv
import random
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20260926
OVERLAP = 40

REVIEWER_FIELDS = [
    "item", "overlap", "report_id", "utility", "event_year", "field", "reason", "question", "options",
    "page_refs", "document_url", "detail",
    "reviewer", "decision_value", "decision_pages", "matches_jev", "notes", "minutes_spent",
]


def main() -> None:
    queue = list(csv.DictReader(open(HERE / "review_queue.csv", encoding="utf-8")))
    by_report: "OrderedDict[str, list[dict]]" = OrderedDict()
    for row in sorted(queue, key=lambda r: (r["report_id"], int(r["item"]))):
        by_report.setdefault(row["report_id"], []).append(row)

    reports = list(by_report)
    random.Random(SEED).shuffle(reports)
    overlap_items: list[dict] = []
    overlap_reports = []
    for rid in reports:
        if len(overlap_items) >= OVERLAP:
            break
        take = by_report[rid][: OVERLAP - len(overlap_items)]
        overlap_items += take
        overlap_reports.append(rid)
    overlap_ids = {r["item"] for r in overlap_items}

    rest = [(rid, [r for r in by_report[rid] if r["item"] not in overlap_ids]) for rid in reports]
    rest = [(rid, rows) for rid, rows in rest if rows]
    rest.sort(key=lambda kv: (-len(kv[1]), kv[0]))
    halves = {"A": [], "B": []}
    for rid, rows in rest:
        target = "A" if len(halves["A"]) <= len(halves["B"]) else "B"
        halves[target] += rows

    for name, rows in halves.items():
        rows = rows + overlap_items
        rows.sort(key=lambda r: (r["report_id"], r["field"], int(r["item"])))
        with open(HERE / f"review_reviewer_{name}.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=REVIEWER_FIELDS)
            writer.writeheader()
            for r in rows:
                out = {k: r.get(k, "") for k in REVIEWER_FIELDS}
                out["overlap"] = "yes" if r["item"] in overlap_ids else "no"
                out["reviewer"] = name
                # For conflicting sources the workbook value is part of the question (in detail);
                # for Jev items the answer is withheld here.
                writer.writerow(out)
        print(f"reviewer {name}: {len(rows)} items ({len(rows) - len(overlap_items)} own + {len(overlap_items)} overlap), "
              f"{len({r['report_id'] for r in rows})} reports, {len(rows) * 2 / 60:.1f} to {len(rows) * 3 / 60:.1f} hours")

    with open(HERE / "review_answer_key.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["item", "report_id", "field", "jev_answer", "jev_confidence"])
        writer.writeheader()
        for r in sorted(queue, key=lambda r: int(r["item"])):
            writer.writerow({"item": r["item"], "report_id": r["report_id"], "field": r["field"],
                             "jev_answer": r["answer"], "jev_confidence": r["confidence"]})
    print(f"overlap: {len(overlap_items)} items from {len(overlap_reports)} reports; seed {SEED}")


if __name__ == "__main__":
    main()
