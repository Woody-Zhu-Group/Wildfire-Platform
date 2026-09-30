"""Round 2 extraction pipeline for PSPS post-event reports.

Changes from round 1 (the round 1 scripts are left as they were):
- Page selection always includes the summary and event-table pages, anchors on
  the standard template section headers (plus the page after each, since
  tables spill over), then fills up to MAX_PAGES by keyword score.
- A question with no page that matches its topic is flagged no_matching_page
  and Jev is not called.
- Every Jev question has a not_stated option. Wind is now a Choice
  (met, not_met, not_stated) instead of a Noul.
- The Luna prompt and model are unchanged. The quote check is looser: the
  value's digits must appear on the cited page.

    python research/psps_reports/round2/pipeline.py extract --sources round2/sample.csv
    python research/psps_reports/round2/pipeline.py select --sources ...   # page selection only, no API
    python research/psps_reports/round2/pipeline.py jev --sources ... --out runs/jev_v2_new15.jsonl
    python research/psps_reports/round2/pipeline.py luna --sources ... --out runs/luna_new15.jsonl

Spend across every round 2 call is tracked in runs/spend.json and the
pipeline stops before it passes SPEND_CAP_USD.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from common import (  # noqa: E402
    JEV_PRICE_IN,
    LUNA_MODEL,
    LUNA_PRICE_IN,
    LUNA_PRICE_OUT,
    format_pages,
    is_garbled,
    load_env,
)

SPEND_CAP_USD = 1.0
MAX_PAGES = 8
MAX_CHARS_PER_PAGE = 6000
RUNS = HERE / "runs"

PREAMBLE = (
    "The state holds selected pages from a California electric utility's Public Safety "
    "Power Shutoff (PSPS) post-event report filed with the CPUC. Each page starts with a "
    "[Page N] marker. These are excerpts, not the whole report. Answer only from these "
    "pages. If these pages do not say, choose not_stated."
)

SUMMARY_ANCHORS = [
    r"PSPS Event Summary",
    r"Customers Notified and De-?\s?energized",
    r"At A Glance",
    r"Executive Summary",
    r"Summary and Overview",
    r"maximum numbers? of customers notified",
]

FIELDS: dict[str, dict] = {
    "mbl_advance_notice": {
        "anchors": [r"positive or\s+affirmative\s+notification", r"Medical Baseline[^.]{0,60}(notif|contact)"],
        "patterns": [
            (r"medical baseline|\bMBL\b", 2),
            (r"positive notification|affirmative notification|in-person|door", 2),
            (r"not (be )?(reached|contacted|notified)|unsuccessful|unable to (reach|contact)|did not receive|not attempted", 1),
        ],
        "require": r"medical baseline|\bMBL\b",
        "instructions": (
            "Did every Medical Baseline (MBL) customer who lost power in this event have a "
            "notification delivered before their power was shut off? A delivered notification "
            "or a door-hanger visit counts even if the customer did not confirm receipt. MBL "
            "customers who were in scope but never lost power do not count."
        ),
        "criteria": {
            "all_notified": (
                "The pages say every de-energized MBL customer had a notification delivered, or a "
                "successful positive notification, before de-energization, or report zero MBL "
                "notification failures."
            ),
            "some_not_notified": (
                "The pages say at least one de-energized MBL customer had no notification attempted "
                "or delivered before de-energization."
            ),
            "not_stated": "The report pages shown do not say whether de-energized MBL customers were notified in advance.",
        },
    },
    "wind_threshold_cited": {
        "anchors": [
            r"Decision[- ]Making Process",
            r"Decision criteria and\s+(detailed )?thresholds",
            r"factors considered in (the|its) decision",
        ],
        "patterns": [(r"threshold", 3), (r"alert speed", 3), (r"gust", 1), (r"mph", 1), (r"wind speed", 1)],
        "require": r"threshold|criteria|alert speed|wind speed|gust",
        "instructions": (
            "Does the report state that observed or forecast wind speeds or gusts met or exceeded "
            "the utility's de-energization threshold or wind criterion in an area that was "
            "de-energized?"
        ),
        "criteria": {
            "met": (
                "The pages say wind speeds or gusts met, reached, or exceeded a de-energization "
                "threshold, alert speed, or wind criterion for at least one de-energized circuit or area."
            ),
            "not_met": (
                "The pages say wind speeds did not reach the threshold in any de-energized area, "
                "for example power was shut off for other reasons."
            ),
            "not_stated": (
                "The pages describe winds, forecasts, or the threshold process but never say that "
                "winds met a threshold in a de-energized area."
            ),
        },
    },
    "complaints_reported": {
        "anchors": [r"Complaints\s+(and|&)\s+Claims", r"number and nature of complaints", r"complaints received"],
        "patterns": [(r"complaint", 3), (r"\bclaims?\b", 1)],
        "require": r"complaint",
        "instructions": "How many complaints does the report say were received about this PSPS event?",
        "criteria": {
            "one_or_more": "The pages report one or more complaints about this event.",
            "zero": "The pages say no complaints (zero) were received about this event.",
            "not_stated": (
                "The report pages shown do not give a complaint count for this event, or defer it "
                "to another report."
            ),
        },
    },
    "claims_reported": {
        "anchors": [r"Complaints\s+(and|&)\s+Claims", r"number and nature of complaints", r"claims (filed|received)"],
        "patterns": [(r"\bclaims?\b", 3), (r"complaint", 1)],
        "require": r"\bclaims?\b",
        "instructions": (
            "How many claims does the report say were filed against the utility because of "
            "this PSPS event?"
        ),
        "criteria": {
            "one_or_more": "The pages report one or more claims filed because of this event.",
            "zero": "The pages say no claims (zero) were filed because of this event.",
            "not_stated": (
                "The report pages shown do not give a claim count for this event, or defer it to "
                "another report."
            ),
        },
    },
    "canceled_after_notice": {
        "anchors": [r"\bCancell?ed\b", r"removed? .{0,30}from scope", r"cancellation notice"],
        "patterns": [
            (r"cancel", 2),
            (r"removed? .{0,30}from scope|out of scope", 2),
            (r"not (ultimately )?de-?energized", 2),
            (r"event avoided|all clear", 1),
        ],
        "require": r"cancel|scope|not (ultimately )?de-?energized",
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
                "The pages say every notified customer was de-energized, or report a cancelled "
                "count of zero."
            ),
            "not_stated": "The report pages shown do not say whether any notified customers were not de-energized.",
        },
    },
}


# ---------------------------------------------------------------- utilities

def read_sources(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def pages_path(report_id: str) -> Path:
    if report_id.startswith("r2_"):
        return HERE / "pages" / f"{report_id}.jsonl"
    return HERE.parent / "pages" / f"{report_id}.jsonl"


def load_report(report_id: str) -> list[dict]:
    with open(pages_path(report_id), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def spend_state() -> dict:
    path = RUNS / "spend.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"jev_usd": 0.0, "luna_usd": 0.0}


def add_spend(kind: str, usd: float) -> float:
    state = spend_state()
    state[kind] = round(state[kind] + usd, 6)
    RUNS.mkdir(exist_ok=True)
    (RUNS / "spend.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state["jev_usd"] + state["luna_usd"]


def total_spend() -> float:
    state = spend_state()
    return state["jev_usd"] + state["luna_usd"]


# ---------------------------------------------------------------- extract

def cmd_extract(sources: list[dict]) -> None:
    import fitz  # PyMuPDF

    (HERE / "raw").mkdir(exist_ok=True)
    (HERE / "pages").mkdir(exist_ok=True)
    for row in sources:
        pdf_path = HERE / "raw" / f"{row['report_id']}.pdf"
        if not pdf_path.exists():
            request = urllib.request.Request(row["source_url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=180) as response:
                pdf_path.write_bytes(response.read())
        doc = fitz.open(pdf_path)
        pages = [{"page": i, "text": p.get_text()} for i, p in enumerate(doc, start=1)]
        with open(pages_path(row["report_id"]), "w", encoding="utf-8") as fh:
            for page in pages:
                fh.write(json.dumps(page) + "\n")
        garbled = sum(is_garbled(p["text"]) for p in pages)
        empty = sum(len(p["text"].strip()) < 40 for p in pages)
        print(f"{row['report_id']} pages={len(pages)} garbled={garbled} near_empty={empty}")


# ---------------------------------------------------------------- page selection

def is_toc(text: str) -> bool:
    return len(re.findall(r"\.{8,}|_{8,}", text)) >= 5


def anchor_pages(pages: list[dict], anchors: list[str], limit: int) -> list[int]:
    """Earliest body pages that match an anchor, each with the page after it."""
    found: list[int] = []
    usable = {p["page"] for p in pages if not is_garbled(p["text"]) and not is_toc(p["text"])}
    for page in pages:
        if page["page"] not in usable:
            continue
        if any(re.search(a, page["text"], re.I) for a in anchors):
            for n in (page["page"], page["page"] + 1):
                if n in usable and n not in found:
                    found.append(n)
            if len(found) >= limit:
                break
    return found[:limit]


def keyword_score(text: str, field: dict) -> float:
    if not re.search(field["require"], text, re.I):
        return 0.0
    score = 0.0
    for pattern, weight in field["patterns"]:
        score += weight * min(len(re.findall(pattern, text, re.I)), 5)
    if re.search(r"script|\[(date|time|customer)", text, re.I):
        score *= 0.3
    return score


def select_pages(pages: list[dict], field: dict) -> dict:
    by_number = {p["page"]: p for p in pages}
    summary = anchor_pages(pages, SUMMARY_ANCHORS, limit=3)
    sections = anchor_pages(pages, field["anchors"], limit=4)
    chosen: list[int] = []
    for n in summary + sections:
        if n not in chosen:
            chosen.append(n)
    half = len(pages) / 2
    scored = sorted(
        (
            (keyword_score(p["text"], field) * (0.5 if p["page"] > half else 1.0), p["page"])
            for p in pages
            if not is_garbled(p["text"])
        ),
        key=lambda item: (-item[0], item[1]),
    )
    for score, n in scored:
        if len(chosen) >= MAX_PAGES:
            break
        if score > 0 and n not in chosen:
            chosen.append(n)
    chosen = sorted(chosen[:MAX_PAGES])
    matched = [n for n in chosen if re.search(field["require"], by_number[n]["text"], re.I)]
    return {
        "pages": chosen,
        "summary_pages": summary,
        "section_pages": sections,
        "matched_pages": matched,
        "no_matching_page": not matched,
        "report_text_broken": sum(is_garbled(p["text"]) for p in pages) > len(pages) / 2,
    }


def cmd_select(sources: list[dict]) -> None:
    for row in sources:
        pages = load_report(row["report_id"])
        for name, field in FIELDS.items():
            sel = select_pages(pages, field)
            flag = " NO_MATCH" if sel["no_matching_page"] else ""
            print(row["report_id"], name, sel["pages"], "summary", sel["summary_pages"], "sections", sel["section_pages"], flag)


# ---------------------------------------------------------------- Jev

def cmd_jev(sources: list[dict], out: Path) -> None:
    load_env()
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("TYPESAFE_API_KEY is not set; stopping.")
    from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

    records = []
    tokens = 0
    with TypeSafeClient(model="jev-latest", timeout=60, retry=RetryPolicy(max_retries=2, timeout=60)) as client:
        for row in sources:
            pages = load_report(row["report_id"])
            by_number = {p["page"]: p for p in pages}
            for name, field in FIELDS.items():
                sel = select_pages(pages, field)
                record = {"report_id": row["report_id"], "field": name, **sel, "answer": None}
                if sel["no_matching_page"]:
                    record["note"] = "no page matched the topic; flagged for review without a Jev call"
                    records.append(record)
                    print(row["report_id"], name, "NO_MATCH", flush=True)
                    continue
                if total_spend() >= SPEND_CAP_USD:
                    raise SystemExit(f"STOP: round 2 spend ${total_spend():.3f} reached the cap")
                state = (
                    f"{PREAMBLE}\nUtility: {row['utility']}.\n\n"
                    + format_pages([by_number[n] for n in sel["pages"]], MAX_CHARS_PER_PAGE)
                )
                question = Choice(instructions=field["instructions"], criteria=field["criteria"])
                response = client.system_one(state=state, questions={name: question}, model="jev-latest")
                raw = response.model_dump(mode="json")
                used = (raw.get("usage") or {}).get("input_tokens") or 0
                tokens += used
                add_spend("jev_usd", used * JEV_PRICE_IN)
                record.update(answer=raw["answers"][name], model=raw.get("model"), input_tokens=used)
                records.append(record)
                print(row["report_id"], name, sel["pages"], json.dumps(raw["answers"][name]), flush=True)
    with open(out, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    print(f"Jev calls {sum(r['answer'] is not None for r in records)} tokens {tokens} "
          f"spend ${tokens * JEV_PRICE_IN:.4f}; round 2 total ${total_spend():.4f}")


# ---------------------------------------------------------------- Luna

def value_on_page(item: dict, pages_by_number: dict[int, str]) -> bool | None:
    """Looser quote check: the value's digits appear on the cited page."""
    value, page = item.get("value"), item.get("page")
    if value is None:
        return None
    if not isinstance(page, int) or page not in pages_by_number:
        return False
    text = pages_by_number[page]
    if isinstance(value, int):
        digits = re.sub(r"[,\s]", "", text)
        return str(value) in digits
    # Timestamps: accept the time (with or without a leading zero) on the page.
    match = re.search(r"(\d{1,2}):(\d{2})$", str(value))
    if not match:
        return False
    hour, minute = int(match.group(1)), match.group(2)
    hour12 = hour % 12 or 12
    options = [f"{hour}:{minute}", f"{hour:02d}:{minute}", f"{hour:02d}{minute}", f"{hour12}:{minute}"]
    return any(option in text for option in options)


def cmd_luna(sources: list[dict], out: Path) -> None:
    load_env()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set; numeric fields skipped.")
        return
    from llm_extract import call_luna

    records = []
    for row in sources:
        if total_spend() >= SPEND_CAP_USD:
            raise SystemExit(f"STOP: round 2 spend ${total_spend():.3f} reached the cap")
        pages = load_report(row["report_id"])
        by_number = {p["page"]: p["text"] for p in pages}
        fields, usage = call_luna(format_pages(pages))
        cost = usage.get("cost")
        if cost is None:
            cost = usage.get("prompt_tokens", 0) * LUNA_PRICE_IN + usage.get("completion_tokens", 0) * LUNA_PRICE_OUT
        add_spend("luna_usd", float(cost))
        checks = {name: {"value_on_page": value_on_page(item, by_number)} for name, item in fields.items()}
        records.append({
            "report_id": row["report_id"], "model": LUNA_MODEL, "fields": fields, "checks": checks,
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "cost_usd": round(float(cost), 5),
        })
        print(row["report_id"], json.dumps(fields), json.dumps(checks), f"${float(cost):.4f}", flush=True)
    with open(out, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    print(f"round 2 total ${total_spend():.4f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["extract", "select", "jev", "luna"])
    parser.add_argument("--sources", required=True, help="CSV with report_id, utility, source_url")
    parser.add_argument("--out")
    args = parser.parse_args()
    sources = read_sources(Path(args.sources) if Path(args.sources).is_absolute() else HERE.parent.parent.parent / args.sources)
    RUNS.mkdir(exist_ok=True)
    if args.command == "extract":
        cmd_extract(sources)
    elif args.command == "select":
        cmd_select(sources)
    elif args.command == "jev":
        cmd_jev(sources, RUNS / args.out)
    else:
        cmd_luna(sources, RUNS / args.out)


if __name__ == "__main__":
    main()
