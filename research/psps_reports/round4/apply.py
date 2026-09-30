"""Apply the round 4 decisions to the round 3 queue and dataset (only after a passing test).

Sources of answers for each of the 286 queue items:
- queue run (runs/claude_queue.jsonl, runs/sol_queue.jsonl);
- the test run, for the categorical queue items on the test reports (same question and options);
- none, for the 3 redlines, which go straight to unresolved.

Rules:
- An item is accepted (model_review_agreed) when both answers are valid and identical; otherwise unresolved.
- A field's review_method is `unflagged` when no queue item covers it, `unresolved` when any covering item
  is unresolved (the pipeline value stays and the flag `<field>:model_review_disagreement` is added), and
  `model_review_agreed` otherwise. "all fields" and "table pages" items cover every field; "numeric fields"
  items cover the four numbers. A field whose agreed items give two different values is also unresolved.
- CONTRADICTION: notes from either reviewer on these items are appended to round3/contradictions.csv as
  "round 4 model-found, unchecked".

    python research/psps_reports/round4/apply.py
"""

from __future__ import annotations

import csv
import json
from datetime import datetime

import packets as pk
import score as sc
import verify as vf

HERE = pk.HERE
RUNS = HERE / "runs"
DURATION = "event_duration_hours"


def covered_fields(item: dict) -> list[str]:
    if item["field"] in ("all fields", "table pages"):
        return pk.FIELDS
    if item["field"] == "numeric fields":
        return pk.NUM
    return [item["field"]]


def hours(a: str, b: str) -> str:
    if not a or not b:
        return ""
    fmt = "%Y-%m-%d %H:%M"
    return str(round((datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() / 3600, 2))


def reviewed_items() -> list[dict]:
    queue_rows = {(r["report_id"], r["item"]): r for r in sc.evaluate("queue", "")}
    test_rows = {(r["report_id"], r["field"]): r for r in sc.evaluate("test", "")}
    out = []
    for q in pk.queue():
        base = {"item": q["item"], "report_id": q["report_id"], "utility": q["utility"], "field": q["field"], "reason": q["reason"]}
        if pk.is_redline(q):
            out.append({**base, "answer_source": "not asked (redline)", "status": "unresolved", "r1_check": "not_asked_redline", "r2_check": "not_asked_redline"})
            continue
        if (q["report_id"], q["item"]) in queue_rows:
            r, source = queue_rows[(q["report_id"], q["item"])], "queue run"
        else:
            r, source = test_rows[(q["report_id"], q["field"])], "test run (same question)"
        keep = {k: v for k, v in r.items() if k.startswith(("r1_", "r2_")) or k == "status"}
        out.append({**base, "answer_source": source, **keep})
    return out


def corrections(canonical: str) -> dict[str, str]:
    if not canonical or canonical == "no change":
        return {}
    out = {}
    for part in canonical.split("; "):
        f, v = part.split("=", 1)
        out[f] = "" if v == "null" else v
    return out


def main() -> None:
    items = reviewed_items()
    cols = ["item", "report_id", "utility", "field", "reason", "answer_source", "status"]
    rest = sorted({k for r in items for k in r} - set(cols))
    with open(HERE / "reviewed_queue.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols + rest)
        w.writeheader()
        w.writerows(items)

    by_report: dict[str, list] = {}
    for r in items:
        by_report.setdefault(r["report_id"], []).append(r)
    dataset = pk.read_csv(pk.R3 / "dataset.csv")
    changed = 0
    for row in dataset:
        mine = by_report.get(row["report_id"], [])
        method = {f: "unflagged" for f in pk.FIELDS}
        proposed: dict[str, set] = {}
        unresolved_notes = []
        for r in mine:
            fields = covered_fields(r)
            if r["status"] != "model_review_agreed":
                for f in fields:
                    method[f] = "unresolved"
                unresolved_notes.append(f"item {r['item']} ({r['field']}): R1={r.get('r1_value')} [{r.get('r1_check')}]; R2={r.get('r2_value')} [{r.get('r2_check')}]")
                continue
            for f in fields:
                if method[f] == "unflagged":
                    method[f] = "model_review_agreed"
            value = r["r1_canonical"]
            if r["field"] in pk.FIELDS:
                proposed.setdefault(r["field"], set()).add("" if value == "null" else value)
            else:
                for f, v in corrections(value).items():
                    proposed.setdefault(f, set()).add(v)
        for f, values in proposed.items():
            if f in method and method[f] == "unresolved":
                continue
            if len(values) > 1:
                if f in method:
                    method[f] = "unresolved"
                unresolved_notes.append(f"{f}: agreed items give different values {sorted(values)}")
                continue
            v = values.pop()
            if row.get(f) != v:
                row[f] = v
                changed += 1
        row[DURATION] = hours(row["first_deenergization"], row["last_restoration"]) if row["first_deenergization"] and row["last_restoration"] else row[DURATION]
        flags = [x for x in row["flags"].split(";") if x.strip()] if row["flags"] else []
        flags += [f"{f}:model_review_disagreement" for f in pk.FIELDS if method[f] == "unresolved"]
        row["flags"] = ";".join(flags)
        for f in pk.FIELDS:
            row[f"{f}_review_method"] = method[f]
        row["model_review_unresolved"] = " | ".join(unresolved_notes)
    with open(HERE / "dataset_reviewed.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(dataset[0]))
        w.writeheader()
        w.writerows(dataset)

    contra = []
    for r in items:
        for name, label in (("r1", "Reviewer 1 (Opus)"), ("r2", "Reviewer 2 (Sol)")):
            note = (r.get(f"{name}_note") or "").strip()
            if note.upper().startswith("CONTRADICTION:"):
                contra.append({"set": "round 4 model-found, unchecked", "report_id": r["report_id"], "field": r["field"],
                               "value_a": "", "source_a": f"{label}, queue item {r['item']}", "value_b": "", "source_b": "", "note": note})
    path = pk.R3 / "contradictions.csv"
    existing = pk.read_csv(path)
    existing = [e for e in existing if e["set"] != "round 4 model-found, unchecked"]  # rerunnable
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(existing[0]))
        w.writeheader()
        w.writerows(existing + contra)
    status = [r["status"] for r in items]
    print(f"queue items {len(items)}: agreed {status.count('model_review_agreed')}, unresolved {status.count('unresolved')}; "
          f"dataset values changed {changed}; contradictions appended {len(contra)}")


if __name__ == "__main__":
    main()
