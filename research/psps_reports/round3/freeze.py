"""Freeze the round 3 run before it starts.

Writes manifest.json (pipeline version: file hashes, question hash, review rule,
event list hash) and accuracy_sample.csv (10 events never read in rounds 1 or 2,
drawn with a fixed seed). Committed and pushed before any full-run API call.

    python research/psps_reports/round3/freeze.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SEED = 20260924
FILES = [
    HERE / "pipeline3.py", HERE / "image_pages.py", HERE / "workbooks.py", HERE / "inventory.py",
    HERE.parent / "round2" / "pipeline.py", HERE.parent / "common.py", HERE.parent / "llm_extract.py",
]

REVIEW_RULE = [
    "Jev confidence below 0.9",
    "no page matched the question topic (Jev not called)",
    "a relevant table page was image-only and could not be transcribed (unreadable, or over the per-report cap)",
    "conflicting sources: workbook and PDF times differ by more than 30 minutes; a partial correction was filed for the event; the extracted version is a redline (struck text may be read)",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> None:
    import pipeline3

    events = list(csv.DictReader(open(HERE / "versions.csv", encoding="utf-8")))
    unread = sorted(e["report_id"] for e in events if not e["read_before"])
    picks = random.Random(SEED).sample(unread, 10)
    by_id = {e["report_id"]: e for e in events}
    with open(HERE / "accuracy_sample.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["report_id", "utility", "event_year", "label", "document_url", "version"])
        writer.writeheader()
        for rid in picks:
            e = by_id[rid]
            writer.writerow({k: e[k] for k in ["report_id", "utility", "event_year", "label", "document_url", "version"]})
    manifest = {
        "pipeline_version": "round3-v1",
        "question_hash": pipeline3.QUESTION_HASH,
        "file_sha256_16": {str(p.relative_to(HERE.parent)): sha(p) for p in FILES},
        "events": len(events),
        "events_using_amendment": sum(e["version"] != "original" for e in events),
        "events_file_sha256_16": sha(HERE / "versions.csv"),
        "models": {"jev": "jev-latest", "luna": pipeline3.LUNA_MODEL},
        "spend_cap_usd": pipeline3.SPEND_CAP_USD,
        "review_rule": REVIEW_RULE,
        "accuracy_sample": {"seed": SEED, "population": len(unread), "report_ids": picks},
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
