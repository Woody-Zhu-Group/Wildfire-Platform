"""Build pilot.csv, validation.csv, and validation_summary.md from stored runs.

No API calls. Reads runs/jev_raw.jsonl, runs/llm_raw.jsonl, sources.csv, and
the hand-checked gold_labels.csv.

    python research/psps_reports/build_pilot.py
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from statistics import mean

from common import HERE, JEV_PRICE_IN, load_sources

JEV_FIELDS = [
    "mbl_advance_notice",
    "wind_threshold_cited",
    "complaints_reported",
    "claims_reported",
    "canceled_after_notice",
]
LLM_FIELDS = [
    "customers_deenergized",
    "first_deenergization",
    "last_restoration",
    "counties_deenergized",
]


def read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def jev_value(answer: dict | None) -> tuple[str, float | None]:
    """Label and confidence. A Noul's confidence is max(p, 1 - p), never raw p."""
    if answer is None:
        return "not_stated", None
    if answer["type"] == "noul":
        p = float(answer["noul"])
        return ("true" if p >= 0.5 else "false"), round(max(p, 1 - p), 3)
    return answer["choice"], round(float(answer["confidence"]), 3)


def hours_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    fmt = "%Y-%m-%d %H:%M"
    delta = datetime.strptime(end, fmt) - datetime.strptime(start, fmt)
    return round(delta.total_seconds() / 3600, 2)


def main() -> None:
    sources = {row["report_id"]: row for row in load_sources()}
    jev = {(r["report_id"], r["field"]): r for r in read_jsonl(HERE / "runs" / "jev_raw.jsonl")}
    llm = {r["report_id"]: r for r in read_jsonl(HERE / "runs" / "llm_raw.jsonl")}
    with open(HERE / "gold_labels.csv", newline="", encoding="utf-8") as fh:
        gold = {(r["report_id"], r["field"]): r for r in csv.DictReader(fh)}

    pilot_rows = []
    validation_rows = []
    for report_id, source in sources.items():
        row = {
            "report_id": report_id,
            "utility": source["utility"],
            "event_year": source["event_year"],
            "source_url": source["source_url"],
        }
        for field in JEV_FIELDS:
            record = jev.get((report_id, field))
            value, confidence = jev_value(record["answer"] if record else None)
            row[field] = value
            row[f"{field}_confidence"] = confidence
            row[f"{field}_pages_shown"] = ";".join(str(p) for p in (record or {}).get("pages_shown", []))
            g = gold[(report_id, field)]
            validation_rows.append({
                "report_id": report_id,
                "field": field,
                "extractor": "jev",
                "extracted": value,
                "extracted_page": "",
                "gold": g["gold"],
                "gold_pages": g["gold_pages"],
                "correct": int(value == g["gold"]),
                "gold_certain": g["certain"],
                "jev_confidence": confidence,
                "pages_shown": row[f"{field}_pages_shown"],
                "gold_page_shown": int(bool(set(g["gold_pages"].split(";")) & set(row[f"{field}_pages_shown"].split(";")))),
                "llm_page_ok": "",
                "note": g["note"],
            })

        fields = llm[report_id]["fields"]
        checks = llm[report_id]["checks"]
        for field in LLM_FIELDS:
            item = fields.get(field) or {}
            value = item.get("value")
            row[field] = value
            row[f"{field}_page"] = item.get("page")
            row[f"{field}_quote_on_page"] = checks.get(field, {}).get("quote_on_page")
            g = gold[(report_id, field)]
            validation_rows.append({
                "report_id": report_id,
                "field": field,
                "extractor": "luna",
                "extracted": value,
                "extracted_page": item.get("page"),
                "gold": g["gold"],
                "gold_pages": g["gold_pages"],
                "correct": int(str(value) == g["gold"]),
                "gold_certain": g["certain"],
                "jev_confidence": "",
                "pages_shown": "",
                "gold_page_shown": "",
                "llm_page_ok": g["llm_page_ok"],
                "note": g["note"],
            })
        row["counties_deenergized_names"] = "; ".join(fields.get("counties_deenergized", {}).get("names") or [])
        row["event_duration_hours"] = hours_between(row["first_deenergization"], row["last_restoration"])
        gold_hours = hours_between(
            gold[(report_id, "first_deenergization")]["gold"],
            gold[(report_id, "last_restoration")]["gold"],
        )
        both_certain = all(
            gold[(report_id, f)]["certain"] == "yes" for f in ("first_deenergization", "last_restoration")
        )
        validation_rows.append({
            "report_id": report_id,
            "field": "event_duration_hours",
            "extractor": "code (from Luna timestamps)",
            "extracted": row["event_duration_hours"],
            "extracted_page": "",
            "gold": gold_hours,
            "gold_pages": "",
            "correct": int(row["event_duration_hours"] == gold_hours),
            "gold_certain": "yes" if both_certain else "no",
            "jev_confidence": "",
            "pages_shown": "",
            "gold_page_shown": "",
            "llm_page_ok": "",
            "note": "First de-energization to last restoration, computed in code.",
        })
        pilot_rows.append(row)

    with open(HERE / "pilot.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(pilot_rows[0]))
        writer.writeheader()
        writer.writerows(pilot_rows)
    with open(HERE / "validation.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(validation_rows[0]))
        writer.writeheader()
        writer.writerows(validation_rows)

    # Summary table.
    order = JEV_FIELDS + LLM_FIELDS + ["event_duration_hours"]
    lines = [
        "| Field | Extractor | Right (all 10) | Right (certain gold only) | Mean Jev confidence when right | Mean Jev confidence when wrong |",
        "|---|---|---|---|---|---|",
    ]
    for field in order:
        rows = [r for r in validation_rows if r["field"] == field]
        certain = [r for r in rows if r["gold_certain"] == "yes"]
        right = [r for r in rows if r["correct"]]
        wrong = [r for r in rows if not r["correct"]]

        def conf(group):
            values = [r["jev_confidence"] for r in group if isinstance(r["jev_confidence"], float)]
            return f"{mean(values):.2f} (n={len(values)})" if values else "n/a"

        lines.append(
            f"| {field} | {rows[0]['extractor']} | {len(right)}/{len(rows)} | "
            f"{sum(r['correct'] for r in certain)}/{len(certain)} | "
            f"{conf(right) if rows[0]['extractor'] == 'jev' else ''} | "
            f"{conf(wrong) if rows[0]['extractor'] == 'jev' else ''} |"
        )

    jev_rows = [r for r in validation_rows if r["extractor"] == "jev"]
    llm_rows = [r for r in validation_rows if r["extractor"] == "luna"]
    jev_tokens = sum(r.get("input_tokens", 0) for r in jev.values())
    luna_cost = sum(r["cost_usd"] for r in llm.values())
    luna_in = sum(r["prompt_tokens"] or 0 for r in llm.values())
    luna_out = sum(r["completion_tokens"] or 0 for r in llm.values())
    buckets = []
    for low, high in ((0.9, 1.01), (0.7, 0.9), (0.0, 0.7)):
        group = [r for r in jev_rows if isinstance(r["jev_confidence"], float) and low <= r["jev_confidence"] < high]
        if group:
            buckets.append(f"| {low:.1f} to {min(high, 1.0):.1f} | {len(group)} | {sum(r['correct'] for r in group)}/{len(group)} |")
    summary = [
        "# Validation summary",
        "",
        "Generated by build_pilot.py from runs/jev_raw.jsonl, runs/llm_raw.jsonl, and gold_labels.csv.",
        "",
        *lines,
        "",
        f"Jev overall: {sum(r['correct'] for r in jev_rows)}/{len(jev_rows)} right. "
        f"Luna overall: {sum(r['correct'] for r in llm_rows)}/{len(llm_rows)} values right; "
        f"cited page supports the gold value in {sum(r['llm_page_ok'] == 'yes' for r in llm_rows)}/{len(llm_rows)}.",
        "",
        "Jev accuracy by confidence band (the one call with no retrieved pages has no confidence and is excluded):",
        "",
        "| Confidence | Answers | Right |",
        "|---|---|---|",
        *buckets,
        "",
        f"Cost: Jev {len(jev)} calls, {jev_tokens} input tokens, ${jev_tokens * JEV_PRICE_IN:.4f}. "
        f"Luna {len(llm)} calls, {luna_in} input and {luna_out} output tokens, ${luna_cost:.4f}. "
        f"Total ${jev_tokens * JEV_PRICE_IN + luna_cost:.4f}.",
    ]
    (HERE / "validation_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
