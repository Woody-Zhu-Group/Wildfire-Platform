"""Mark fields settled only by a whole-event item that left them unchanged (run after hand_labels.py).

Decided after the PR #29 review on 2026-09-28. apply.py (frozen) marks every field that an agreed
whole-event item covers ("all fields", "table pages", "numeric fields") as `model_review_agreed`. Those
items ask whether a correction letter or a table page changes the event's values, and the reviewers
answer with the current values in view. When the agreed answer leaves a field unchanged, no reviewer
answered that field on its own, so its review_method becomes `model_review_no_change`.

Rules:
- A field stays `model_review_agreed` when an agreed item asks about it directly, or when an agreed
  whole-event correction names it.
- `hand_labeled`, `unresolved`, and `unflagged` fields are not touched, and no value changes.
- The script is idempotent.

This does not touch the test scoring or calibration_results.md.

    python research/psps_reports/round4/no_change.py
"""

from __future__ import annotations

import csv
from collections import Counter

import apply as ap
import packets as pk

HERE = pk.HERE
AGREED = "model_review_agreed"
NO_CHANGE = "model_review_no_change"


def answered_fields(items: list[dict]) -> dict[str, set[str]]:
    """report_id -> fields an agreed item asked about directly or an agreed correction named."""
    out: dict[str, set[str]] = {}
    for r in items:
        if r["status"] != AGREED:
            continue
        fields = out.setdefault(r["report_id"], set())
        if r["field"] in pk.FIELDS:
            fields.add(r["field"])
        else:
            fields |= set(ap.corrections(r["r1_canonical"]))
    return out


def main() -> None:
    items = pk.read_csv(HERE / "reviewed_queue.csv")
    answered = answered_fields(items)
    whole_event = {r["report_id"] for r in items if r["status"] == AGREED and r["field"] not in pk.FIELDS}
    path = HERE / "dataset_reviewed.csv"
    rows = pk.read_csv(path)
    marked = 0
    for row in rows:
        rid = row["report_id"]
        for f in pk.FIELDS:
            if row[f"{f}_review_method"] == AGREED and f not in answered.get(rid, set()):
                assert rid in whole_event, f"{rid} {f} is agreed with no item behind it"
                row[f"{f}_review_method"] = NO_CHANGE
                marked += 1
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    methods = Counter(row[f"{f}_review_method"] for row in rows for f in pk.FIELDS)
    events = len({row["report_id"] for row in rows if any(row[f"{f}_review_method"] == NO_CHANGE for f in pk.FIELDS)})
    print(f"marked {marked} fields {NO_CHANGE}; {events} events; review_method counts {dict(sorted(methods.items()))}")


if __name__ == "__main__":
    main()
