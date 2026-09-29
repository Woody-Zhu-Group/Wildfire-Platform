"""Round 5: a human audit of the PSPS dataset (AUDIT_PLAN.md).

    python research/psps_reports/round5/audit.py sample   # sample.csv (committed before the sheet is built)
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
R3 = ROOT / "round3"
R4 = ROOT / "round4"
sys.path.insert(0, str(R4))

import packets as pk  # noqa: E402

SEED = 20260929
SIZES = {"queue": 30, "unflagged": 15, "old_test_label": 15}
SAMPLE_COLUMNS = ["audit_id", "group", "report_id", "field", "queue_item", "gold_set"]


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


def populations() -> dict[str, dict[tuple[str, str], dict]]:
    """Each group's population: (report_id, field) -> source ids."""
    dataset = read_csv(R4 / "dataset_reviewed.csv")
    agreed_items = {(r["report_id"], r["field"]): r["item"] for r in read_csv(R4 / "reviewed_queue.csv")
                    if r["status"] == "model_review_agreed" and r["field"] in pk.FIELDS}
    queue, unflagged = {}, {}
    for row in dataset:
        for f in pk.FIELDS:
            key, method = (row["report_id"], f), row[f"{f}_review_method"]
            if method == "model_review_agreed":
                queue[key] = {"queue_item": agreed_items[key]}  # KeyError means an agreed field with no item
            elif method == "unflagged":
                unflagged[key] = {}
    old = {(r["report_id"], r["field"]): {"gold_set": r["gold_set"]}
           for r in read_csv(R4 / "test_gold.csv") if r["excluded"] == "no"}
    return {"queue": queue, "unflagged": unflagged, "old_test_label": old}


def cmd_sample() -> None:
    pops = populations()
    rng = random.Random(SEED)
    taken: set[tuple[str, str]] = set()
    rows = []
    for group, n in SIZES.items():
        keys = sorted(k for k in pops[group] if k not in taken)
        for key in rng.sample(keys, n):
            taken.add(key)
            rows.append({"group": group, "report_id": key[0], "field": key[1], "queue_item": "", "gold_set": "",
                         **pops[group][key]})
        print(f"{group}: {n} of {len(keys)}")
    rows.sort(key=lambda r: (r["report_id"], pk.FIELDS.index(r["field"])))
    for i, r in enumerate(rows, start=1):
        r["audit_id"] = f"A{i:02d}"
    write_csv(HERE / "sample.csv", rows, SAMPLE_COLUMNS)
    print(f"sample.csv: {len(rows)} rows on {len({r['report_id'] for r in rows})} reports")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["sample"])
    args = ap.parse_args()
    {"sample": cmd_sample}[args.command]()


if __name__ == "__main__":
    main()
