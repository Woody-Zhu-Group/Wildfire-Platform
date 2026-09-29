"""Put hand labels into dataset_reviewed.csv (run after apply.py; decided after the test, on 2026-09-28).

Known errors should not stay in the dataset. For every field with a hand label, the value becomes the
gold value and its review_method becomes `hand_labeled`:
- test gold: the kept rows of test_gold.csv (rows excluded there, including all of r2_pge_2017_2019, are skipped);
- round 1 gold: gold_labels.csv on the 10 pilot reports, with the development mapping (wind true -> met,
  false -> not_stated).

Rules:
- The previous value is kept in `<field>_value_before_hand_label` (filled only for hand-labeled fields).
- A gold "a|b" accepts either value: the current value stays if it is one of them, otherwise the first is used.
- A gold "null" becomes an empty cell, as in the rest of the dataset.
- Exception: a gold "null" does not replace a value taken from a utility Excel workbook. The gold labels were
  written from the PDF, and "null" there means the PDF does not state the value; the workbook does (round 3
  method step 6). These fields keep their value and review_method and are listed as skipped.
- The round 4 flag `<field>:model_review_disagreement` is removed from a hand-labeled field; pipeline flags stay.
- The event duration is recomputed when a time changes. County names are not changed.

This does not touch the test scoring or calibration_results.md.

    python research/psps_reports/round4/hand_labels.py
"""

from __future__ import annotations

import csv

import apply as ap
import packets as pk
import score as sc

HERE = pk.HERE


def gold_values() -> dict[tuple[str, str], tuple[str, str]]:
    """(report_id, field) -> (gold, source)."""
    out = {k: (g["gold"], "round 1 gold (development mapping)") for k, g in sc.dev_gold().items()}
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
            row[f"{f}_review_method"] = "hand_labeled"
            flags = [x for x in flags if x != f"{f}:model_review_disagreement"]
            labeled += 1
        row["flags"] = ";".join(flags)
        if times_changed:
            row[ap.DURATION] = ap.hours(row["first_deenergization"], row["last_restoration"])
    cols = list(rows[0])
    for c in before_cols:
        if c not in cols:
            cols.append(c)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"hand-labeled fields {labeled}; values changed by hand labels {changed}; skipped (workbook value, PDF gold null) {len(skipped)}")
    for line in skipped:
        print("  skipped:", line)


if __name__ == "__main__":
    main()
