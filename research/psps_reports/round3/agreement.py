"""Agreement between the two reviewers on the 40 overlap items, once both files are filled in.

Compares decision_value (trimmed, lower case) item by item and prints the
agreement rate, Cohen's kappa for the categorical fields, and the list of
disagreements to adjudicate. No API calls.

    python research/psps_reports/round3/agreement.py
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name: str) -> dict[str, dict]:
    rows = csv.DictReader(open(HERE / name, encoding="utf-8"))
    return {r["item"]: r for r in rows if r["overlap"] == "yes"}


def norm(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(left[k] * right.get(k, 0) for k in left) / (n * n)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def main() -> None:
    a, b = load("review_reviewer_A.csv"), load("review_reviewer_B.csv")
    done = [i for i in a if norm(a[i]["decision_value"]) and norm(b[i]["decision_value"])]
    agree = [i for i in done if norm(a[i]["decision_value"]) == norm(b[i]["decision_value"])]
    print(f"overlap items: {len(a)}; both decided: {len(done)}; agree: {len(agree)}"
          + (f" ({len(agree) / len(done):.0%})" if done else ""))
    categorical = [(norm(a[i]["decision_value"]), norm(b[i]["decision_value"])) for i in done
                   if a[i]["field"] in ("mbl_advance_notice", "wind_threshold_cited", "complaints_reported", "claims_reported", "canceled_after_notice")]
    k = kappa(categorical)
    if k is not None:
        print(f"Cohen's kappa on {len(categorical)} categorical items: {k:.2f}")
    for i in done:
        if i not in agree:
            print(f"  item {i} {a[i]['report_id']} {a[i]['field']}: A={a[i]['decision_value']!r} B={b[i]['decision_value']!r}")


if __name__ == "__main__":
    main()
