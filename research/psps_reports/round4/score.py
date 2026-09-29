"""Gold conversion, development scoring, and the one test scoring for round 4.

    python research/psps_reports/round4/score.py gold               # writes test_gold.csv (before the freeze)
    python research/psps_reports/round4/score.py dev --tag _v1      # development set, round 1 gold
    python research/psps_reports/round4/score.py test               # the test run, against test_gold.csv

Comparison rules follow round3/score_sample.py: exact category, exact number,
time to the minute; a gold value "a|b" accepts either; "null" means not stated.
Accuracy is measured on agreed answers (both reviewers valid and identical).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict

import packets as pk
import verify as vf

HERE = pk.HERE
RUNS = HERE / "runs"

# ------------------------------------------------------------------ gold conversion (round 2 -> round 3 rules)

# Rule differences between round2/README.md and round3/README.md + round3/REVIEW_GUIDE.md, and what each does.
RULE_DIFFERENCES = [
    {"id": "D1", "rule": "MBL gains not_applicable when no customers were de-energized (round 3 method step 3; guide).",
     "handling": "mechanical: MBL gold becomes not_applicable wherever gold customers_deenergized is 0"},
    {"id": "D2", "rule": "MBL: SCE 2019 and 2020 track only critical care customers; use what they report (guide).",
     "handling": "not mechanical: exclude SCE 2019-2020 MBL rows whose events de-energized customers"},
    {"id": "D3", "rule": "MBL some_not_notified explicitly includes events where no advance notice could be sent to the customers shut off (guide).",
     "handling": "not mechanical: exclude MBL rows whose note says the PSPS notice came after de-energization"},
    {"id": "D4", "rule": "Cancellation: a gap between customers notified and de-energized means yes, even without a cancel word (guide).",
     "handling": "not mechanical: exclude cancellation rows labeled no or not_stated (the rule can only turn them into yes)"},
    {"id": "D5", "rule": "Times: use a circuit table listing every circuit or a sentence stating the first de-energization; never 'the event began at', notification, or status-update times (guide).",
     "handling": "not mechanical: exclude time rows whose gold came from an introduction start time or a notification"},
    {"id": "D6", "rule": "Wind: PG&E composite scores ('exceeded PSPS guidance', mFPC) are not wind statements; not_stated when nothing was de-energized (guide).",
     "handling": "no row changes: every round 2 PG&E wind gold and every no-shutoff wind gold is already not_stated"},
    {"id": "D7", "rule": "Complaints and claims: 'Not applicable' and deferred counts are not_stated; complaints-only sections leave claims not_stated (guide).",
     "handling": "no row changes: the round 2 gold already follows it (for example SCE Aug 2019 'Not applicable' is not_stated)"},
    {"id": "D8", "rule": "Customers: when a report gives two totals, the reviewer uses the summary-table one (guide).",
     "handling": "no row changes: the gold convention ('a|b' accepts either) is the same in round 3's own gold_sample10.csv"},
    {"id": "D9", "rule": "Document: round 3 extracts the latest full amendment (round 3 method step 1).",
     "handling": "exclude every row of a round 2 report whose document differs from the file round 3 used (r2_pge_2017_2019)"},
]

# Rows the non-mechanical differences touch, found by reading every round 2 gold row and note.
EXCLUDED_ROWS = {
    ("r2_sce_2020", "mbl_advance_notice"): "D2: SCE 2020 tracks only critical care customers; the gold note relies on that subset.",
    ("r2_sdge_2017_2019", "mbl_advance_notice"): "D3: the PSPS message reached MBL customers after de-energization; under round 3 this may be some_not_notified.",
    ("r2_pge_2025_2026", "canceled_after_notice"): "D4: gold no from 'Table 1: canceled 0'; the round 3 gap rule may make it yes.",
    ("r2_sce_2023_2024", "first_deenergization"): "D5: gold is the introduction start time (the table lists 5 of 41 circuits).",
    ("r2_sdge_2025_2026", "last_restoration"): "D5: gold is from a restoration notice; Section 8 gives the day only.",
}


def cmd_gold() -> None:
    rows = []
    r2 = {(g["report_id"], g["field"]): g for g in pk.read_csv(pk.ROOT / "round2" / "gold_new15.csv")}
    r2_to_r3 = {v["read_before"][7:]: rid for rid, v in pk.versions().items() if v["read_before"].startswith("round2:")}
    for (rid2, field), g in sorted(r2.items()):
        gold, mapping, excluded, reason = g["gold"], "", False, ""
        if rid2 in pk.EXCLUDED_TEST_REPORTS:
            excluded, reason = True, "D9: round 3 used the amended PDF, which differs from the file round 2 labeled."
        elif (rid2, field) in EXCLUDED_ROWS:
            excluded, reason = True, EXCLUDED_ROWS[(rid2, field)]
        elif field == "mbl_advance_notice" and r2[(rid2, "customers_deenergized")]["gold"] == "0" and gold != "not_applicable":
            gold, mapping = "not_applicable", f"D1: {g['gold']} -> not_applicable (gold customers_deenergized is 0)"
        rows.append({"report_id": r2_to_r3[rid2], "gold_set": "round2", "gold_report_id": rid2, "field": field, "gold": gold,
                     "gold_original": g["gold"], "certain": g["certain"], "mapping": mapping, "excluded": "yes" if excluded else "no",
                     "exclusion_reason": reason, "note": g["note"]})
    for g in pk.read_csv(pk.R3 / "gold_sample10.csv"):
        rows.append({"report_id": g["report_id"], "gold_set": "round3", "gold_report_id": g["report_id"], "field": g["field"],
                     "gold": g["gold"], "gold_original": g["gold"], "certain": g["certain"], "mapping": "", "excluded": "no",
                     "exclusion_reason": "", "note": g["note"]})
    with open(HERE / "test_gold.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    kept = [r for r in rows if r["excluded"] == "no"]
    qm = queue_matched_keys()
    print(f"test_gold.csv: {len(rows)} rows, {len(kept)} kept, {len(rows) - len(kept)} excluded; "
          f"queue-matched kept {sum((r['report_id'], r['field']) in qm for r in kept)} of {len(qm) + 1} before exclusions")


def queue_matched_keys() -> set[tuple[str, str]]:
    tests = pk.test_reports()
    return {(q["report_id"], q["field"]) for q in pk.queue() if q["field"] in pk.CAT and q["report_id"] in tests}


def dev_gold() -> dict[tuple[str, str], dict]:
    """Round 1 labels on the round 3 report ids; old wind values mapped (true -> met, false -> not_stated)."""
    to_r3 = {pilot: rid for rid, pilot in pk.dev_reports().items()}
    out = {}
    for g in pk.read_csv(pk.ROOT / "gold_labels.csv"):
        gold = g["gold"]
        if g["field"] == "wind_threshold_cited":
            gold = {"true": "met", "false": "not_stated"}[gold]
        out[(to_r3[g["report_id"]], g["field"])] = {"gold": gold, "certain": g["certain"]}
    return out


def test_gold() -> dict[tuple[str, str], dict]:
    return {(r["report_id"], r["field"]): r for r in pk.read_csv(HERE / "test_gold.csv") if r["excluded"] == "no"}


# ------------------------------------------------------------------ answers

def load_run(path) -> dict[str, dict]:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["session"]] = r
    return out


def evaluate(set_name: str, tag: str) -> list[dict]:
    """One row per item with both answers, checks, and the decision."""
    claude = load_run(RUNS / f"claude_{set_name}{tag}.jsonl")
    sol = load_run(RUNS / f"sol_{set_name}{tag}.jsonl")
    rows = []
    for s in pk.sessions(set_name):
        docs = pk.documents(s["report_id"], s["items"])
        answers = {}
        for name, run in (("r1", claude), ("r2", sol)):
            rec = run.get(s["session"]) or {}
            answers[name] = {str(a.get("item")): a for a in rec.get("answers") or [] if isinstance(a, dict)}
        for item in s["items"]:
            row = {"session": s["session"], "report_id": s["report_id"], "item": item["item"], "field": item["field"], "reason": item["reason"]}
            for name in ("r1", "r2"):
                a = answers[name].get(item["item"])
                c = vf.check(a, item, docs)
                row.update({f"{name}_value": (a or {}).get("value"), f"{name}_pages": ";".join(map(str, (a or {}).get("pages") or [])),
                            f"{name}_quote": (a or {}).get("quote"), f"{name}_note": (a or {}).get("note"),
                            f"{name}_search_terms": "; ".join(map(str, (a or {}).get("search_terms") or [])),
                            f"{name}_valid": c["valid"], f"{name}_check": c["reason"], f"{name}_canonical": c["canonical"]})
            row["status"] = vf.decide({"valid": row["r1_valid"], "canonical": row["r1_canonical"]},
                                      {"valid": row["r2_valid"], "canonical": row["r2_canonical"]})
            rows.append(row)
    return rows


def correct(value: str | None, gold: str) -> bool:
    return value is not None and value in {g.strip() for g in gold.split("|")}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def fmt(k: int, n: int) -> str:
    if n == 0:
        return "0/0"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.1f}%, 95% CI {100 * lo:.1f} to {100 * hi:.1f})"


def cmd_dev(tag: str) -> None:
    rows = evaluate("dev", tag)
    gold = dev_gold()
    per = defaultdict(Counter)
    for r in rows:
        g = gold.get((r["report_id"], r["field"]))
        for name in ("r1", "r2"):
            per[name]["valid"] += r[f"{name}_valid"]
            per[name][f"check:{r[f'{name}_check']}"] += 1
            if g:
                per[name]["gold_n"] += 1
                per[name]["right_any"] += correct(r[f"{name}_canonical"], g["gold"])
        if g:
            per["pair"]["n"] += 1
            if r["status"] == "model_review_agreed":
                per["pair"]["agreed"] += 1
                per["pair"]["agreed_right"] += correct(r["r1_canonical"], g["gold"])
    print(f"dev{tag}: {len(rows)} items")
    for name in ("r1", "r2"):
        c = per[name]
        print(f"  {name}: valid {c['valid']}/{len(rows)}, right (any validity) {c['right_any']}/{c['gold_n']}; "
              + ", ".join(f"{k[6:]} {v}" for k, v in sorted(c.items()) if k.startswith("check:")))
    p = per["pair"]
    print(f"  agreed {p['agreed']}/{p['n']} gold items; agreed right {p['agreed_right']}/{p['agreed']}")
    print("  disagreements and errors:")
    for r in rows:
        g = gold.get((r["report_id"], r["field"]))
        wrong = g and r["status"] == "model_review_agreed" and not correct(r["r1_canonical"], g["gold"])
        if r["status"] != "model_review_agreed" or wrong:
            print(f"    {'WRONG ' if wrong else ''}{r['report_id'][:38]} {r['item'][:22]} gold={g['gold'] if g else '-'} "
                  f"r1={r['r1_value']} [{r['r1_check']}] r2={r['r2_value']} [{r['r2_check']}]")
    write_rows(rows, RUNS / f"dev_scored{tag}.csv")


def write_rows(rows: list[dict], path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def cmd_test() -> None:
    rows = evaluate("test", "")
    gold = test_gold()
    qm = queue_matched_keys()
    utility = {rid: v["utility"] for rid, v in pk.versions().items()}
    scored = []
    for r in rows:
        g = gold.get((r["report_id"], r["field"]))
        r["gold"] = g["gold"] if g else ""
        r["gold_certain"] = g["certain"] if g else ""
        r["in_test"] = bool(g)
        r["queue_matched"] = (r["report_id"], r["field"]) in qm
        r["utility"] = utility[r["report_id"]]
        r["agreed_correct"] = correct(r["r1_canonical"], g["gold"]) if g and r["status"] == "model_review_agreed" else ""
        scored.append(r)
    write_rows(scored, RUNS / "test_scored.csv")
    T = [r for r in scored if r["in_test"]]

    def acc(sel):
        ag = [r for r in sel if r["status"] == "model_review_agreed"]
        return sum(bool(r["agreed_correct"]) for r in ag), len(ag)

    qk, qn = acc([r for r in T if r["queue_matched"]])
    ak, an = acc(T)
    bar1 = qn > 0 and qk / qn >= 0.90
    bar2 = an > 0 and ak / an >= 0.95
    lines = ["# Round 4 calibration: test results", "",
             "One test run on the 24 clean test reports after the freeze (`freeze.json`). Gold: `test_gold.csv`.",
             "Accuracy is on agreed answers: both reviewers valid and identical.", "",
             "## Pass bars", "", "| Bar | Needed | Result | Pass |", "|---|---|---|---|",
             f"| Queue-matched items | at least 90% | {fmt(qk, qn)} | {'yes' if bar1 else 'NO'} |",
             f"| All test values | at least 95% | {fmt(ak, an)} | {'yes' if bar2 else 'NO'} |", "",
             f"**Overall: {'PASS' if bar1 and bar2 else 'FAIL'}.**", "", "## Also reported (no bar)", ""]
    agreed = sum(r["status"] == "model_review_agreed" for r in T)
    lines += [f"- Agreement rate: {fmt(agreed, len(T))} of test values agreed.",
              f"- Agreement rate, queue-matched: {fmt(sum(r['status'] == 'model_review_agreed' for r in T if r['queue_matched']), sum(r['queue_matched'] for r in T))}.",
              f"- Valid answers, Reviewer 1 (Opus): {fmt(sum(r['r1_valid'] for r in T), len(T))}.",
              f"- Valid answers, Reviewer 2 (Sol): {fmt(sum(r['r2_valid'] for r in T), len(T))}."]
    ck, cn = acc([r for r in T if r["gold_certain"] == "yes"])
    lines += [f"- Accuracy of agreed answers, certain gold only: {fmt(ck, cn)}.", "",
              "### By field", "", "| Field | Agreed | Agreed correct |", "|---|---|---|"]
    for f in pk.FIELDS:
        sel = [r for r in T if r["field"] == f]
        k, n = acc(sel)
        lines.append(f"| {f} | {n}/{len(sel)} | {fmt(k, n)} |")
    lines += ["", "### By utility", "", "| Utility | Agreed | Agreed correct |", "|---|---|---|"]
    for u in sorted({r["utility"] for r in T}):
        sel = [r for r in T if r["utility"] == u]
        k, n = acc(sel)
        lines.append(f"| {u} | {n}/{len(sel)} | {fmt(k, n)} |")
    lines += ["", "### Why answers were invalid", "", "| Check | Reviewer 1 | Reviewer 2 |", "|---|---|---|"]
    c1, c2 = Counter(r["r1_check"] for r in T), Counter(r["r2_check"] for r in T)
    for k in sorted(set(c1) | set(c2)):
        lines.append(f"| {k} | {c1[k]} | {c2[k]} |")
    lines += ["", "### Agreed but wrong", ""]
    wrong = [r for r in T if r["status"] == "model_review_agreed" and not r["agreed_correct"]]
    lines += [f"- {r['report_id']} `{r['field']}`: agreed {r['r1_canonical']}, gold {r['gold']} (certain {r['gold_certain']})" for r in wrong] or ["- none"]
    (HERE / "calibration_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["gold", "dev", "test"])
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    {"gold": cmd_gold, "dev": lambda: cmd_dev(args.tag), "test": cmd_test}[args.command]()


if __name__ == "__main__":
    main()
