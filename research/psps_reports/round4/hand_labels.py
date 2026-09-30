"""Put the gold labels into dataset_reviewed.csv (run after apply.py; decided after the test, on 2026-09-28).

The gold labels were written by a Claude Opus 5.5 session in rounds 1 to 3, not by a person (round5/AUDIT_PLAN.md);
the file and column names still say "hand label". Known errors should not stay in the dataset. For every field
with a gold label, the value becomes the gold value and its review_method becomes `model_labeled`
(named `hand_labeled` until 2026-09-29):
- test gold: the kept rows of test_gold.csv (rows excluded there, including all of r2_pge_2017_2019, are skipped);
- round 1 gold: gold_labels.csv on the 10 pilot reports, converted to round 3 rules the same way as round 2
  (ROUND1_DIFFERENCES below): only mechanical mappings, and every row a non-mechanical difference touches is
  excluded. The result is written to round1_gold_converted.csv.

Rules:
- The previous value is kept in `<field>_value_before_hand_label` (filled only for hand-labeled fields).
- A gold "a|b" accepts either value: the current value stays if it is one of them, otherwise the first is used.
- A gold "null" becomes an empty cell, as in the rest of the dataset.
- Exception: a gold "null" does not replace a value taken from a utility Excel workbook. The gold labels were
  written from the PDF, and "null" there means the PDF does not state the value; the workbook does (round 3
  method step 6). These fields keep their value and review_method and are listed as skipped.
- The round 4 flag `<field>:model_review_disagreement` is removed from a hand-labeled field; pipeline flags stay.
- The event duration is recomputed when a time changes. County names are not changed.
- REPORT_STATES_BOTH: a field whose hand label was dropped by the conversion, where the report itself states
  both the old and the hand-labeled value, keeps its previous value, becomes `unresolved` with the flag
  `<field>:contradiction_in_report`, and is added to round3/contradictions.csv.

This does not touch the test scoring or calibration_results.md.

    python research/psps_reports/round4/hand_labels.py
"""

from __future__ import annotations

import csv
import json

import apply as ap
import packets as pk

HERE = pk.HERE

# Label-rule differences between round 1 (README.md, "Label rules I applied") and round 3
# (round3/README.md, round3/REVIEW_GUIDE.md), and how each is handled.
ROUND1_DIFFERENCES = [
    {"id": "R1", "rule": "Wind was yes/no (true/false); round 3 has met, not_met, and not_stated.",
     "handling": "mechanical: true -> met. Not mechanical: false could be not_met or not_stated, so every false row is excluded."},
    {"id": "R2", "rule": "Wind: round 3 counts only a threshold met in a de-energized area, and treats an implied threshold ('no longer met', 'failed to reach') as not_stated.",
     "handling": "not mechanical: exclude true rows whose note rests on an implication or does not tie a wind threshold to de-energized areas."},
    {"id": "R3", "rule": "Times: round 1 used the earliest and latest times in the circuit table; round 3 uses a circuit table only when it lists every de-energized circuit, or a sentence, and null for a partial table.",
     "handling": "not mechanical: whether each table is complete needs the report, so every first and last time row is excluded."},
    {"id": "R4", "rule": "MBL: round 1 had two options; round 3 adds not_stated and not_applicable (nobody de-energized), and counts no advance notice as some_not_notified.",
     "handling": "no row changes: no round 1 event had zero customers, and every round 1 MBL note records the statement it rests on."},
    {"id": "R5", "rule": "Cancellation: round 3 adds the notified-versus-de-energized gap rule, no when everyone notified was de-energized or Cancelled = 0, and not_stated.",
     "handling": "no row changes: the gap rule only adds yes, and the one no row records notified 583, de-energized 583, cancelled 0."},
    {"id": "R6", "rule": "Complaints and claims: round 1 had no written rule; round 3 spells out deferred, not-applicable, and channel-limited counts.",
     "handling": "no row changes: every round 1 note agrees with the round 3 rule (the deferred count is not_stated)."},
    {"id": "R7", "rule": "Customers: round 3 uses the summary-table total when a report gives two. Counties: only counties where power was cut.",
     "handling": "no row changes: no pilot report has a recorded customer-total contradiction, and the SCE Nov 2021 county label already leaves out Kern (in scope only)."},
    {"id": "R8", "rule": "Document: round 3 uses the latest full amendment.",
     "handling": "no row changes: all 10 pilot PDFs are byte-identical to the files round 3 used."},
]
ROUND1_EXCLUDED_ROWS = {
    ("sce_2021_11_24", "wind_threshold_cited"): "R2: the note says fire weather conditions met SCE's thresholds; it does not tie a wind threshold to de-energized areas.",
    ("sce_2025_09_02", "wind_threshold_cited"): "R2: the note rests on an implication (winds 'no longer met' the criteria on September 10).",
}
# Checked on the report pages (round3/pages): the report states both values.
REPORT_STATES_BOTH = {
    ("pge__pge_oct_21_23_2020_psps_post_event_report", "first_deenergization"): {
        "value_a": "2020-10-21 14:42", "source_a": "PDF p75, transmission line table (Butt Valley-Caribou 115kV line)",
        "value_b": "2020-10-21 17:33", "source_b": "PDF p73, Appendix A distribution circuit table (Pit No 7 1101)",
        "note": "The earliest distribution circuit is 17:33, but a transmission line was de-energized at 14:42. "
                "The round 1 hand label (17:33) was dropped by the round 3 conversion (R3); the dataset keeps 14:42, unresolved."},
}
CONTRADICTION_SET = "round 4 old-gold-label check"  # "round 4 hand-label check" until 2026-09-29


def convert_round1() -> list[dict]:
    to_r3 = {pilot: rid for rid, pilot in pk.dev_reports().items()}
    rows = []
    for g in pk.read_csv(pk.ROOT / "gold_labels.csv"):
        rid1, field, gold = g["report_id"], g["field"], g["gold"]
        mapping, reason = "", ""
        if field == "wind_threshold_cited" and gold == "false":
            reason = "R1: false could be not_met or not_stated under round 3."
        elif (rid1, field) in ROUND1_EXCLUDED_ROWS:
            reason = ROUND1_EXCLUDED_ROWS[(rid1, field)]
        elif field in ("first_deenergization", "last_restoration"):
            reason = "R3: round 3 needs a circuit table that lists every de-energized circuit, or a sentence."
        elif field == "wind_threshold_cited" and gold == "true":
            gold, mapping = "met", "R1: true -> met"
        rows.append({"report_id": to_r3[rid1], "gold_set": "round1", "gold_report_id": rid1, "field": field, "gold": gold,
                     "gold_original": g["gold"], "certain": g["certain"], "mapping": mapping,
                     "excluded": "yes" if reason else "no", "exclusion_reason": reason, "note": g["note"]})
    with open(HERE / "round1_gold_converted.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (HERE / "round1_rule_differences.json").write_text(json.dumps(ROUND1_DIFFERENCES, indent=2), encoding="utf-8")
    return rows


def gold_values() -> dict[tuple[str, str], tuple[str, str]]:
    """(report_id, field) -> (gold, source)."""
    out = {(r["report_id"], r["field"]): (r["gold"], "round1_gold_converted.csv") for r in convert_round1() if r["excluded"] == "no"}
    for r in pk.read_csv(HERE / "test_gold.csv"):
        if r["excluded"] == "no":
            key = (r["report_id"], r["field"])
            assert key not in out, f"two hand labels for {key}"
            out[key] = (r["gold"], f"test_gold.csv ({r['gold_set']})")
    return out


def main() -> None:
    path = HERE / "dataset_reviewed.csv"
    rows = pk.read_csv(path)
    if "mbl_advance_notice_value_before_hand_label" in rows[0]:
        raise SystemExit("STOP: hand labels are already in; rerun apply.py first")
    gold = gold_values()
    before_cols = [f"{f}_value_before_hand_label" for f in pk.FIELDS]
    changed, labeled, skipped = 0, 0, []
    for row in rows:
        times_changed = False
        flags = [x for x in row["flags"].split(";") if x.strip()] if row["flags"] else []
        for f in pk.FIELDS:
            row.setdefault(f"{f}_value_before_hand_label", "")
            if (row["report_id"], f) not in gold:
                continue
            g, _ = gold[(row["report_id"], f)]
            options = ["" if o.strip() == "null" else o.strip() for o in g.split("|")]
            if options == [""] and row.get(f"{f}_source", "").startswith("Excel"):
                skipped.append(f"{row['report_id']} {f} (kept {row[f]} from {row[f + '_source']})")
                continue
            value = row[f] if row[f] in options else options[0]
            row[f"{f}_value_before_hand_label"] = row[f]
            if value != row[f]:
                changed += 1
                times_changed |= f in ("first_deenergization", "last_restoration")
                row[f] = value
            row[f"{f}_review_method"] = "model_labeled"
            flags = [x for x in flags if x != f"{f}:model_review_disagreement"]
            labeled += 1
        row["flags"] = ";".join(flags)
        if times_changed:
            row[ap.DURATION] = ap.hours(row["first_deenergization"], row["last_restoration"])

    contradictions = []
    for row in rows:
        for (rid, f), c in REPORT_STATES_BOTH.items():
            if row["report_id"] != rid:
                continue
            assert row[f"{f}_review_method"] != "model_labeled", f"{rid} {f} still has a gold label"
            row[f"{f}_review_method"] = "unresolved"
            flags = [x for x in row["flags"].split(";") if x.strip()] if row["flags"] else []
            row["flags"] = ";".join(flags + [f"{f}:contradiction_in_report"])
            contradictions.append({"set": CONTRADICTION_SET, "report_id": rid, "field": f, **c})
    path_c = pk.R3 / "contradictions.csv"
    existing = [e for e in pk.read_csv(path_c) if e["set"] != CONTRADICTION_SET]
    with open(path_c, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(existing[0]))
        w.writeheader()
        w.writerows(existing + contradictions)

    cols = list(rows[0])
    for c in before_cols:
        if c not in cols:
            cols.append(c)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"gold-labeled fields {labeled}; values changed by gold labels {changed}; skipped (workbook value, PDF gold null) {len(skipped)}; "
          f"report states both values {len(contradictions)}")
    for line in skipped:
        print("  skipped:", line)


if __name__ == "__main__":
    main()
