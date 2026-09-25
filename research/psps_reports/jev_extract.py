"""Categorical fields from PSPS post-event reports, answered by Jev.

For each report and field, keyword scoring picks the top pages, and Jev gets
only those pages plus one small question with mutually exclusive options.
Raw Jev responses go to runs/jev_raw.jsonl so scoring never needs a rerun.

    python research/psps_reports/jev_extract.py --dry-run   # retrieval only
    python research/psps_reports/jev_extract.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from common import (
    HERE,
    JEV_PRICE_IN,
    SPEND_CAP_USD,
    format_pages,
    is_garbled,
    load_env,
    load_pages,
    load_sources,
)

TOP_K = 5
MAX_CHARS_PER_PAGE = 6000

PREAMBLE = (
    "The state holds selected pages from a California electric utility's Public Safety "
    "Power Shutoff (PSPS) post-event report filed with the CPUC. Each page starts with a "
    "[Page N] marker. Answer only from these pages."
)

# Each field: retrieval patterns (regex, weight), a required pattern, and one question.
FIELDS: dict[str, dict] = {
    "mbl_advance_notice": {
        "patterns": [
            (r"medical baseline|\bMBL\b", 2),
            (r"positive notification|affirmative notification|in-person|door", 2),
            (r"not (be )?(reached|contacted|notified)|unsuccessful|unable to (reach|contact)|did not receive", 1),
        ],
        "require": r"medical baseline|\bMBL\b",
        "kind": "choice",
        "instructions": (
            "Were all Medical Baseline (MBL) customers who lost power in this event "
            "successfully notified before their power was shut off?"
        ),
        "criteria": {
            "all_notified": (
                "The pages say every de-energized Medical Baseline customer was notified or "
                "successfully reached before de-energization, or report zero Medical Baseline "
                "notification failures."
            ),
            "some_not_notified": (
                "The pages say at least one de-energized Medical Baseline customer was not "
                "notified or not reached before de-energization."
            ),
            "not_stated": (
                "The pages do not say whether de-energized Medical Baseline customers were "
                "notified before de-energization."
            ),
        },
    },
    "wind_threshold_cited": {
        "patterns": [(r"threshold", 3), (r"gust", 1), (r"mph", 1), (r"wind speed", 1)],
        "require": r"threshold|criteria",
        "kind": "noul",
        "instructions": (
            "The report states that observed or forecast wind speeds or gusts met or exceeded "
            "the utility's de-energization threshold for at least one circuit or area."
        ),
        "criteria": {
            "true": "The pages say wind speeds or gusts met, reached, or exceeded a de-energization or PSPS threshold.",
            "false": "The pages do not say that wind speeds or gusts met a de-energization or PSPS threshold.",
        },
    },
    "complaints_reported": {
        "patterns": [(r"complaint", 3), (r"\bclaims?\b", 1)],
        "require": r"complaint",
        "kind": "choice",
        "instructions": "How many complaints does the report say were received about this PSPS event?",
        "criteria": {
            "one_or_more": "The pages report one or more complaints about this event.",
            "zero": "The pages say no complaints (zero) were received about this event.",
            "not_stated": "The pages do not report how many complaints were received.",
        },
    },
    "claims_reported": {
        "patterns": [(r"\bclaims?\b", 3), (r"complaint", 1)],
        "require": r"\bclaims?\b",
        "kind": "choice",
        "instructions": (
            "How many claims does the report say were filed against the utility because of "
            "this PSPS event?"
        ),
        "criteria": {
            "one_or_more": "The pages report one or more claims filed because of this event.",
            "zero": "The pages say no claims (zero) were filed because of this event.",
            "not_stated": "The pages do not report how many claims were filed.",
        },
    },
    "canceled_after_notice": {
        "patterns": [
            (r"cancel", 2),
            (r"removed? .{0,30}from scope|out of scope", 2),
            (r"not (ultimately )?de-?energized", 2),
            (r"event avoided|all clear", 1),
        ],
        "require": r"cancel|scope|not (ultimately )?de-?energized",
        "kind": "choice",
        "instructions": (
            "Were any customers or areas notified of a possible power shutoff in this event "
            "but then not de-energized?"
        ),
        "criteria": {
            "yes": (
                "The pages say some notified customers, circuits, or areas were not de-energized: "
                "a cancellation, a removal from scope, an event avoided notice, or a cancelled "
                "count greater than zero."
            ),
            "no": (
                "The pages say every notified customer was de-energized, or that no cancellation "
                "notices were sent."
            ),
            "not_stated": "The pages do not say whether any notified customers were not de-energized.",
        },
    },
}


def score_page(text: str, field: dict) -> float:
    if not re.search(field["require"], text, re.I):
        return 0.0
    score = 0.0
    for pattern, weight in field["patterns"]:
        score += weight * min(len(re.findall(pattern, text, re.I)), 5)
    # Customer notification script appendices repeat "cancel" and "scope" without facts.
    if re.search(r"script|\[(date|time|customer)", text, re.I):
        score *= 0.3
    return score


def retrieve(pages: list[dict], field: dict) -> list[dict]:
    # The back half of a report is mostly appendices (circuit lists, scripts,
    # notification logs), so its pages count half.
    half = len(pages) / 2
    scored = [
        (score_page(p["text"], field) * (0.5 if p["page"] > half else 1.0), p)
        for p in pages
        if not is_garbled(p["text"])
    ]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(key=lambda item: (-item[0], item[1]["page"]))
    chosen = [p for _, p in scored[:TOP_K]]
    return sorted(chosen, key=lambda p: p["page"])


def build_question(field: dict):
    from typesafe_sdk import Choice, Noul

    if field["kind"] == "choice":
        return Choice(instructions=field["instructions"], criteria=field["criteria"])
    return Noul(instructions=field["instructions"], criteria=field["criteria"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()
    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    out_path = runs / "jev_raw.jsonl"
    tokens = 0
    records = []

    client = None
    if not args.dry_run:
        if not os.environ.get("TYPESAFE_API_KEY"):
            print("TYPESAFE_API_KEY is not set; stopping.")
            return 1
        from typesafe_sdk import RetryPolicy, TypeSafeClient

        client = TypeSafeClient(
            model="jev-latest", timeout=60, retry=RetryPolicy(max_retries=2, timeout=60)
        )

    for source in load_sources():
        report_id = source["report_id"]
        pages = load_pages(report_id)
        for name, field in FIELDS.items():
            chosen = retrieve(pages, field)
            page_ids = [p["page"] for p in chosen]
            if args.dry_run:
                print(report_id, name, page_ids)
                continue
            if not chosen:
                record = {
                    "report_id": report_id,
                    "field": name,
                    "pages_shown": [],
                    "answer": None,
                    "note": "no page matched retrieval; recorded as not_stated without a Jev call",
                }
                records.append(record)
                print(report_id, name, "no pages", flush=True)
                continue
            state = (
                f"{PREAMBLE}\nUtility: {source['utility']}. Report: {report_id}.\n\n"
                + format_pages(chosen, MAX_CHARS_PER_PAGE)
            )
            started = time.perf_counter()
            response = client.system_one(
                state=state, questions={name: build_question(field)}, model="jev-latest"
            )
            raw = response.model_dump(mode="json")
            used = (raw.get("usage") or {}).get("input_tokens") or 0
            tokens += used
            record = {
                "report_id": report_id,
                "field": name,
                "pages_shown": page_ids,
                "answer": raw["answers"][name],
                "model": raw.get("model"),
                "input_tokens": used,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
            records.append(record)
            print(report_id, name, page_ids, json.dumps(record["answer"]), flush=True)
            if tokens * JEV_PRICE_IN >= SPEND_CAP_USD:
                print(f"STOP Jev spend ${tokens * JEV_PRICE_IN:.3f}")
                break

    if client is not None:
        client.close()
        with open(out_path, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")
        print(f"Jev calls {len(records)} input tokens {tokens} spend ${tokens * JEV_PRICE_IN:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
