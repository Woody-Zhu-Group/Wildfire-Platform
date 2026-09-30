"""Score round 2 against hand-checked gold. No API calls.

Two sets, reported separately:
- clean15: the 15 frozen, never-read reports (gold_new15.csv).
- orig10: the round 1 pilot reports, rerun with the round 2 Jev pipeline. Gold is
  round 1 gold_labels.csv, with the wind field mapped to the new options
  (true -> met, false -> not_stated, since the round 1 false label meant "the
  report never says wind met a threshold"). Luna was not rerun (same prompt and
  model), so its round 1 outputs are rescored. This set was used to design
  both rounds and is not clean.

A gold value may list accepted alternatives with "|" where the report
contradicts itself; "null" means the report does not state the value.

    python research/psps_reports/round2/score.py
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
PILOT = HERE.parent

JEV_FIELDS = ["mbl_advance_notice", "wind_threshold_cited", "complaints_reported", "claims_reported", "canceled_after_notice"]
LLM_FIELDS = ["customers_deenergized", "first_deenergization", "last_restoration", "counties_deenergized"]
WIND_MAP = {"true": "met", "false": "not_stated"}
REVIEW_BELOW = 0.9


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def accepted(gold: str) -> set[str]:
    return set(gold.split("|"))


def as_text(value) -> str:
    return "null" if value is None else str(value)


def hours(start, end):
    if start in (None, "null") or end in (None, "null"):
        return None
    fmt = "%Y-%m-%d %H:%M"
    return round((datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds() / 3600, 2)


def score_set(name: str, jev_rows: list[dict], luna_rows: list[dict], gold: dict, luna_check_key: str) -> tuple[list[dict], list[str]]:
    rows = []
    for rec in jev_rows:
        g = gold[(rec["report_id"], rec["field"])]
        answer = rec.get("answer")
        flagged_no_match = answer is None
        value = None if flagged_no_match else answer["choice"]
        confidence = None if flagged_no_match else round(float(answer["confidence"]), 3)
        correct = (not flagged_no_match) and value in accepted(g["gold"])
        rows.append({
            "set": name, "report_id": rec["report_id"], "field": rec["field"], "extractor": "jev",
            "extracted": "NO_MATCHING_PAGE" if flagged_no_match else value, "gold": g["gold"],
            "correct": int(correct), "gold_certain": g["certain"], "jev_confidence": confidence,
            "flagged_for_review": int(flagged_no_match or confidence < REVIEW_BELOW),
            "pages_shown": ";".join(map(str, rec.get("pages", []))), "gold_pages": g["gold_pages"],
            "value_on_cited_page": "", "note": g["note"],
        })
    for rec in luna_rows:
        fields = rec["fields"]
        for field in LLM_FIELDS:
            g = gold[(rec["report_id"], field)]
            value = (fields.get(field) or {}).get("value")
            check = (rec.get("checks", {}).get(field) or {}).get(luna_check_key)
            rows.append({
                "set": name, "report_id": rec["report_id"], "field": field, "extractor": "luna",
                "extracted": as_text(value), "gold": g["gold"],
                "correct": int(as_text(value) in accepted(g["gold"])), "gold_certain": g["certain"],
                "jev_confidence": "", "flagged_for_review": "", "pages_shown": "",
                "gold_pages": g["gold_pages"], "value_on_cited_page": "" if check is None else int(bool(check)),
                "note": g["note"],
            })
        start, end = fields["first_deenergization"].get("value"), fields["last_restoration"].get("value")
        got = hours(start, end)
        gold_options = {
            hours(a, b)
            for a in accepted(gold[(rec["report_id"], "first_deenergization")]["gold"])
            for b in accepted(gold[(rec["report_id"], "last_restoration")]["gold"])
        }
        certain = all(gold[(rec["report_id"], f)]["certain"] == "yes" for f in ("first_deenergization", "last_restoration"))
        rows.append({
            "set": name, "report_id": rec["report_id"], "field": "event_duration_hours", "extractor": "code",
            "extracted": as_text(got), "gold": "|".join(as_text(h) for h in sorted(gold_options, key=lambda h: (h is None, h or 0))),
            "correct": int(got in gold_options), "gold_certain": "yes" if certain else "no",
            "jev_confidence": "", "flagged_for_review": "", "pages_shown": "", "gold_pages": "",
            "value_on_cited_page": "", "note": "Computed from the Luna timestamps.",
        })

    lines = [f"### {name}", "", "| Field | Extractor | Right | Right (certain gold) | Flagged no page | Mean conf right | Mean conf wrong |", "|---|---|---|---|---|---|---|"]
    for field in JEV_FIELDS + LLM_FIELDS + ["event_duration_hours"]:
        group = [r for r in rows if r["field"] == field]
        certain = [r for r in group if r["gold_certain"] == "yes"]
        right = [r for r in group if r["correct"]]
        wrong = [r for r in group if not r["correct"] and r["extracted"] != "NO_MATCHING_PAGE"]
        no_match = sum(r["extracted"] == "NO_MATCHING_PAGE" for r in group)

        def conf(rs):
            vals = [r["jev_confidence"] for r in rs if isinstance(r["jev_confidence"], float)]
            return f"{mean(vals):.2f} (n={len(vals)})" if vals else "n/a"

        is_jev = group[0]["extractor"] == "jev"
        lines.append(
            f"| {field} | {group[0]['extractor']} | {len(right)}/{len(group)} | "
            f"{sum(r['correct'] for r in certain)}/{len(certain)} | {no_match if is_jev else ''} | "
            f"{conf(right) if is_jev else ''} | {conf(wrong) if is_jev else ''} |"
        )

    jev = [r for r in rows if r["extractor"] == "jev"]
    luna = [r for r in rows if r["extractor"] == "luna"]
    answered = [r for r in jev if r["extracted"] != "NO_MATCHING_PAGE"]
    lines += ["", f"Jev: {sum(r['correct'] for r in jev)}/{len(jev)} right; {len(jev) - len(answered)} flagged with no matching page. "
              f"Luna: {sum(r['correct'] for r in luna)}/{len(luna)} right."]
    checks = [r for r in luna if r["value_on_cited_page"] != ""]
    if checks:
        lines.append(f"Automatic check, value found on the cited page: {sum(r['value_on_cited_page'] for r in checks)}/{len(checks)} non-null Luna values.")
    lines += ["", "Calibration (answered Jev questions):", "", "| Confidence | Answers | Right | Mean confidence |", "|---|---|---|---|"]
    for low, high, label in ((0.9, 1.01, "0.9 to 1.0"), (0.7, 0.9, "0.7 to 0.9"), (0.5, 0.7, "0.5 to 0.7"), (0.0, 0.5, "below 0.5")):
        band = [r for r in answered if low <= r["jev_confidence"] < high]
        if band:
            lines.append(f"| {label} | {len(band)} | {sum(r['correct'] for r in band)}/{len(band)} | {mean(r['jev_confidence'] for r in band):.2f} |")
    misses = [r for r in jev if not r["correct"]]
    caught = [r for r in misses if r["flagged_for_review"]]
    flagged = [r for r in jev if r["flagged_for_review"]]
    lines += [
        "",
        f"Review rule (Jev below {REVIEW_BELOW} or no matching page): flags {len(flagged)}/{len(jev)} questions "
        f"and catches {len(caught)}/{len(misses)} misses. Misses it lets through: "
        + (", ".join(f"{r['report_id']} {r['field']} ({r['extracted']} at {r['jev_confidence']}, gold {r['gold']})" for r in misses if not r["flagged_for_review"]) or "none")
        + ".",
        "",
    ]
    return rows, lines


def main() -> None:
    # clean15
    gold15 = {(r["report_id"], r["field"]): r for r in read_csv(HERE / "gold_new15.csv")}
    rows15, lines15 = score_set(
        "clean15 (15 frozen reports, read only after the run)",
        read_jsonl(HERE / "runs" / "jev_v2_new15.jsonl"),
        read_jsonl(HERE / "runs" / "luna_new15.jsonl"),
        gold15, "value_on_page",
    )
    # orig10
    gold10 = {}
    for r in read_csv(PILOT / "gold_labels.csv"):
        r = dict(r)
        if r["field"] == "wind_threshold_cited":
            r["gold"] = WIND_MAP[r["gold"]]
        gold10[(r["report_id"], r["field"])] = r
    rows10, lines10 = score_set(
        "orig10 (pilot reports, round 2 Jev pipeline; used for design, not clean)",
        read_jsonl(HERE / "runs" / "jev_v2_orig10.jsonl"),
        read_jsonl(PILOT / "runs" / "llm_raw.jsonl"),
        gold10, "quote_on_page",
    )
    # Round 1 Jev on the same 10, same review rule, for comparison.
    r1 = [r for r in read_csv(PILOT / "validation.csv") if r["extractor"] == "jev"]
    r1_misses = [r for r in r1 if r["correct"] == "0"]
    r1_flag = lambda r: r["jev_confidence"] == "" or float(r["jev_confidence"]) < REVIEW_BELOW
    comparison = [
        "### orig10, round 1 Jev (for comparison, round 1 gold and options)",
        "",
        f"Jev {sum(r['correct'] == '1' for r in r1)}/{len(r1)} right. Same review rule flags "
        f"{sum(r1_flag(r) for r in r1)}/{len(r1)} and catches {sum(r1_flag(r) for r in r1_misses)}/{len(r1_misses)} misses.",
        "",
    ]
    fields = list(rows15[0])
    for name, rows in (("validation_clean15.csv", rows15), ("validation_orig10_v2.csv", rows10)):
        with open(HERE / name, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    spend = json.loads((HERE / "runs" / "spend.json").read_text(encoding="utf-8"))
    summary = ["# Round 2 summary", "", "Generated by score.py.", "", *lines15, *lines10, *comparison,
               f"Round 2 spend: Jev ${spend['jev_usd']:.4f}, Luna ${spend['luna_usd']:.4f}, total ${spend['jev_usd'] + spend['luna_usd']:.4f}."]
    (HERE / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
