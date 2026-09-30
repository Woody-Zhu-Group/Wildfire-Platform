"""Mark agreed number and time values that no reviewer quote supports (run after no_change.py).

Decided after the second review of PR #29 on 2026-09-29. The frozen quote check (verify.py) confirms only
that a quote appears on a cited page, not that it supports the value. DESIGN.md expected a workbook-versus-PDF
time item answered with the workbook time to end unresolved, because the workbook is not in the packet and no
quote could contain that time. Queue item 5 passed anyway: both reviewers chose the workbook time, and each
quoted a sentence from a cited page that does not contain it.

Rule: an agreed item whose value is a time or a number must have that value, in some written form, inside at
least one reviewer's quote. Otherwise the item becomes `unresolved` in reviewed_queue.csv (column `post_check`),
and the field it covers in dataset_reviewed.csv becomes `unresolved` with the flag
`<field>:agreed_value_not_in_quote` and a note in `model_review_unresolved`. The value itself is not changed.
Categorical answers are not checked (a quote cannot contain a category name). The script is idempotent.

This does not touch the frozen code, the test scoring, or calibration_results.md.

    python research/psps_reports/round4/quote_support.py
"""

from __future__ import annotations

import csv
import re
from datetime import datetime

import packets as pk

HERE = pk.HERE
CHECK = "agreed_value_not_in_quote"


def read(path) -> tuple[list[dict], list[str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        return list(rd), list(rd.fieldnames)


def write(path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def forms(value: str) -> list[str] | None:
    """Written forms of a time or number value, or None when the value is neither."""
    v = value.strip()
    try:
        t = datetime.strptime(v, "%Y-%m-%d %H:%M")
    except ValueError:
        t = None
    if t:
        h12 = t.hour % 12 or 12
        return [f"{t.hour:02d}:{t.minute:02d}", f"{t.hour}:{t.minute:02d}", f"{t.hour:02d}{t.minute:02d}",
                f"{h12}:{t.minute:02d} {'a' if t.hour < 12 else 'p'}", f"{h12}:{t.minute:02d}{'a' if t.hour < 12 else 'p'}"]
    if re.fullmatch(r"\d+", v):
        return [v, f"{int(v):,}"]
    return None


def supported(value: str, quotes: list[str]) -> bool:
    text = " ".join(re.sub(r"\s+", " ", q or "").lower().replace(".", "") for q in quotes)
    return any(re.search(rf"(?<![\d:]){re.escape(f.lower())}", text) for f in forms(value) or [])


def main() -> None:
    items, qcols = read(HERE / "reviewed_queue.csv")
    if "post_check" not in qcols:
        qcols.append("post_check")
    failed = {}
    for r in items:
        r.setdefault("post_check", "")
        if r["post_check"] == CHECK or (r["status"] == "model_review_agreed" and r["field"] in pk.FIELDS
                                        and forms(r["r1_canonical"]) and not supported(r["r1_canonical"], [r["r1_quote"], r["r2_quote"]])):
            r["status"], r["post_check"] = "unresolved", CHECK
            failed[(r["report_id"], r["field"])] = r
    write(HERE / "reviewed_queue.csv", items, qcols)

    rows, dcols = read(HERE / "dataset_reviewed.csv")
    for row in rows:
        for (rid, f), item in failed.items():
            if row["report_id"] != rid:
                continue
            row[f"{f}_review_method"] = "unresolved"
            flags = [x for x in row["flags"].split(";") if x.strip()] if row["flags"] else []
            if f"{f}:{CHECK}" not in flags:
                row["flags"] = ";".join(flags + [f"{f}:{CHECK}"])
            note = (f"item {item['item']} ({f}): both reviewers agreed on {item['r1_canonical']}, but neither quote contains it; "
                    "the quote check only confirms a quote is on a cited page")
            if note not in row["model_review_unresolved"]:
                row["model_review_unresolved"] = " | ".join(x for x in (row["model_review_unresolved"], note) if x)
    write(HERE / "dataset_reviewed.csv", rows, dcols)
    print(f"agreed number or time items without a supporting quote: {len(failed)} "
          f"({', '.join('item ' + i['item'] for i in failed.values()) or 'none'})")


if __name__ == "__main__":
    main()
