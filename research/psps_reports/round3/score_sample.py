"""Score the frozen 10-report accuracy sample against hand-checked gold.

Gold (gold_sample10.csv) was written from the report text before any pipeline
output for these reports was looked at. Values with "|" accept alternatives;
"null" means the report does not state the value.

A field counts as flagged when any review_queue.csv item covers it: its own
item (low confidence, no matching page, conflicting sources), or an event-level
item (partial correction or unreadable table page covers every field; redline
or truncated text covers the numeric fields).

    python research/psps_reports/round3/score_sample.py
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
JEV = ["mbl_advance_notice", "wind_threshold_cited", "complaints_reported", "claims_reported", "canceled_after_notice"]
NUM = ["customers_deenergized", "first_deenergization", "last_restoration", "counties_deenergized"]


def read(name):
    with open(HERE / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def text(v):
    return "null" if v in (None, "", "None") else str(v)


def hours(a, b):
    if a in (None, "", "null", "None") or b in (None, "", "null", "None"):
        return "null"
    fmt = "%Y-%m-%d %H:%M"
    return str(round((datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() / 3600, 2))


def main() -> None:
    sample = [r["report_id"] for r in read("accuracy_sample.csv")]
    data = {r["report_id"]: r for r in read("dataset.csv")}
    gold = {(r["report_id"], r["field"]): r for r in read("gold_sample10.csv")}
    queue = read("review_queue.csv")

    def flagged(rid, field):
        for q in queue:
            if q["report_id"] != rid:
                continue
            if q["field"] == field or q["field"] in ("all fields", "table pages"):
                return q["reason"]
            if q["field"] == "numeric fields" and field in NUM + ["event_duration_hours"]:
                return q["reason"]
        return ""

    rows = []
    for rid in sample:
        d = data[rid]
        for field in JEV + NUM:
            g = gold[(rid, field)]
            value = text(d[field]) if field in NUM else d[field]
            ok = value in set(g["gold"].split("|"))
            rows.append({"report_id": rid, "field": field, "extractor": "jev" if field in JEV else "luna/workbook",
                         "extracted": value, "gold": g["gold"], "correct": int(ok), "gold_certain": g["certain"],
                         "jev_confidence": d.get(f"{field}_confidence", "") if field in JEV else "",
                         "source": d.get(f"{field}_source", ""), "flagged": flagged(rid, field), "note": g["note"]})
        got = hours(d["first_deenergization"], d["last_restoration"])
        options = {hours(a, b) for a in gold[(rid, "first_deenergization")]["gold"].split("|") for b in gold[(rid, "last_restoration")]["gold"].split("|")}
        rows.append({"report_id": rid, "field": "event_duration_hours", "extractor": "code", "extracted": got, "gold": "|".join(sorted(options)),
                     "correct": int(got in options), "gold_certain": "yes" if all(gold[(rid, f)]["certain"] == "yes" for f in ("first_deenergization", "last_restoration")) else "no",
                     "jev_confidence": "", "source": "computed", "flagged": flagged(rid, "event_duration_hours"), "note": ""})
    with open(HERE / "validation_sample10.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    lines = ["| Field | Right | Right (certain gold) | Flagged | Errors not flagged |", "|---|---|---|---|---|"]
    for field in JEV + NUM + ["event_duration_hours"]:
        group = [r for r in rows if r["field"] == field]
        certain = [r for r in group if r["gold_certain"] == "yes"]
        missed = [r for r in group if not r["correct"] and not r["flagged"]]
        lines.append(f"| {field} | {sum(r['correct'] for r in group)}/{len(group)} | {sum(r['correct'] for r in certain)}/{len(certain)} | "
                     f"{sum(bool(r['flagged']) for r in group)} | {len(missed)} |")
    errors = [r for r in rows if not r["correct"] and r["field"] != "event_duration_hours"]
    unflagged = [r for r in rows if not r["flagged"] and r["field"] != "event_duration_hours"]
    unflagged_wrong = [r for r in unflagged if not r["correct"]]
    jev_rows = [r for r in rows if r["extractor"] == "jev"]
    answered = [r for r in jev_rows if r["jev_confidence"] not in ("", None)]
    bands = []
    for low, high, label in ((0.9, 1.01, "0.9 to 1.0"), (0.7, 0.9, "0.7 to 0.9"), (0.0, 0.7, "below 0.7")):
        band = [r for r in answered if low <= float(r["jev_confidence"]) < high]
        if band:
            bands.append(f"| {label} | {len(band)} | {sum(r['correct'] for r in band)}/{len(band)} | {mean(float(r['jev_confidence']) for r in band):.2f} |")
    summary = [
        "# Accuracy sample (10 never-read reports, seed 20260924)", "",
        *lines, "",
        f"Field values checked (excluding derived duration): {len([r for r in rows if r['field'] != 'event_duration_hours'])}. "
        f"Errors: {len(errors)}. Flagged by the review rule: {sum(bool(r['flagged']) for r in errors)}. Errors the rule missed: {len(unflagged_wrong)}.",
        f"Unflagged values: {len(unflagged)}, of which {len(unflagged) - len(unflagged_wrong)} right ({(len(unflagged) - len(unflagged_wrong)) / max(len(unflagged), 1):.1%}).",
        "", "Jev calibration on the sample:", "", "| Confidence | Answers | Right | Mean confidence |", "|---|---|---|---|", *bands, "",
        "Errors:", "",
        *[f"- {r['report_id']} {r['field']}: extracted {r['extracted']}, gold {r['gold']} (certain {r['gold_certain']}; flagged: {r['flagged'] or 'no'}; confidence {r['jev_confidence']})" for r in errors],
    ]
    (HERE / "accuracy_sample_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
