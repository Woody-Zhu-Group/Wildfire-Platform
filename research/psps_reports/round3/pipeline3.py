"""Round 3 pipeline: full run over every original report (latest version per event).

Stages (each writes to runs/ and can be rerun; finished items are skipped):
  versions   pick the document to extract for each event (amendment rule)
  extract    page text for each chosen document
  images     detect image or vector-drawn table pages, render, transcribe with Luna
  jev        five categorical questions per event (round 2 questions plus fixes)
  luna       four numbers per event from the full text (with transcriptions)
  workbooks  circuit-level times from event workbooks where they exist

Fixes over round 2 (designed on the 25 reports read in rounds 1 and 2):
- cancellation page matching includes "weren't turned off", "no longer at
  risk", "did not de-energize", "event avoided", and similar phrasings;
- the MBL question has a not_applicable option for events with no customers
  de-energized;
- image-only and vector-drawn tables are transcribed; unreadable pages are
  flagged;
- the Luna prompt forbids times taken from status updates, partial circuit
  tables, or event open and close times;
- workbook circuit times are preferred over PDF times.

Spend for round 3 is tracked in runs/spend.json; every stage stops at $6.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "round2"))

import pipeline as p2  # noqa: E402  round 2 pipeline (page selection helpers)
from common import JEV_PRICE_IN, LUNA_PRICE_IN, LUNA_PRICE_OUT, format_pages, is_garbled, load_env  # noqa: E402

import image_pages  # noqa: E402
import workbooks as wb  # noqa: E402

SPEND_CAP_USD = 6.0
RUNS = Path(os.environ.get("PSPS_R3_RUNS", HERE / "runs"))
PAGES = HERE / "pages"
LUNA_MODEL = "openai/gpt-6-luna"
MAX_LUNA_CHARS = 1_500_000
_lock = threading.Lock()

# ------------------------------------------------------------------ questions

CANCEL_REQUIRE = (
    r"cancel|scope|not\s+(ultimately\s+)?de-?\s?energized|did\s+not\s+de-?\s?energize"
    r"|(were|was)(n[’']t|\s+not)\s+(turned|shut)\s+off|no\s+longer\s+(at\s+risk|in\s+scope|be\s+impacted)"
    r"|event\s+avoided|de-?\s?energization\s+was\s+(ultimately\s+)?not\s+required|potentially\s+(affected|impacted)"
)

FIELDS = json.loads(json.dumps(p2.FIELDS))  # deep copy of the round 2 questions
FIELDS["canceled_after_notice"]["require"] = CANCEL_REQUIRE
FIELDS["canceled_after_notice"]["anchors"] = p2.FIELDS["canceled_after_notice"]["anchors"] + [
    r"no\s+longer\s+at\s+risk", r"(were|was)(n[’']t|\s+not)\s+(turned|shut)\s+off", r"did\s+not\s+de-?\s?energize",
]
FIELDS["canceled_after_notice"]["patterns"] = p2.FIELDS["canceled_after_notice"]["patterns"] + [
    (r"no\s+longer\s+at\s+risk|(were|was)(n[’']t|\s+not)\s+(turned|shut)\s+off|did\s+not\s+de-?\s?energize", 3),
]
FIELDS["mbl_advance_notice"]["instructions"] += (
    " If the pages say no customers were de-energized in this event, choose not_applicable."
)
FIELDS["mbl_advance_notice"]["criteria"]["not_applicable"] = (
    "The pages say no customers were de-energized in this event, so no MBL customer lost power."
)
FIELDS["mbl_advance_notice"]["patterns"].append((r"no (circuits|customers) were (pro-?actively )?de-?energized|did not de-?energize", 3))

QUESTION_HASH = hashlib.sha256(json.dumps(FIELDS, sort_keys=True).encode()).hexdigest()[:12]

LUNA_SYSTEM = (
    "You extract numbers from California utility Public Safety Power Shutoff (PSPS) "
    "post-event reports. Every page starts with a [Page N] marker; text marked "
    "[Image transcription] was read from a table image on that page. Use only facts stated in "
    "the report. For every value, give the page number where it is stated and a short quote "
    "copied exactly from that page. If the report does not state a value, use null for value, "
    "page, and quote. Return JSON only."
)
LUNA_TASK = """Extract these fields for the de-energization event this report covers.

1. customers_deenergized: total customers whose power was shut off in this event, as the
   report states it. Integer. Use 0 if the report says no customers were de-energized.
2. first_deenergization: when the first customers were de-energized, "YYYY-MM-DD HH:MM",
   24-hour local time.
3. last_restoration: when power was restored to the last de-energized customers, same format.
4. counties_deenergized: number of counties where customers were de-energized (not counties
   only in scope). Integer, plus the county names in "names".

Rules for the two times:
- Use a time the report states as the first de-energization or the final restoration of the
  whole event, or the earliest and latest times in a circuit table that lists every
  de-energized circuit.
- Do not use times from notification logs, status updates ("as of 3:02 pm, 4 circuits"),
  Emergency Operations Center activation or deactivation, or "the event ended at" statements.
- If the circuit table lists only some circuits (for example it says it continues in an
  attachment or workbook) and no statement covers the whole event, return null.
- If no customers were de-energized, return null for both times.

Return exactly this JSON shape:
{
  "customers_deenergized": {"value": int|null, "page": int|null, "quote": str|null},
  "first_deenergization": {"value": str|null, "page": int|null, "quote": str|null},
  "last_restoration": {"value": str|null, "page": int|null, "quote": str|null},
  "counties_deenergized": {"value": int|null, "names": [str], "page": int|null, "quote": str|null}
}
"""

# ------------------------------------------------------------------ spend

def _read_spend() -> dict:
    path = RUNS / "spend.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"jev_usd": 0.0, "luna_text_usd": 0.0, "luna_vision_usd": 0.0}


def spend() -> dict:
    # Read under the lock: a concurrent write once left a half-written file (fix
    # made after the first full-run attempt crashed; see README).
    with _lock:
        return _read_spend()


def add_spend(kind: str, usd: float) -> float:
    with _lock:
        state = _read_spend()
        state[kind] = round(state.get(kind, 0.0) + usd, 6)
        RUNS.mkdir(exist_ok=True)
        tmp = RUNS / "spend.json.tmp"
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(RUNS / "spend.json")
        return sum(v for v in state.values())


def check_cap() -> None:
    total = sum(spend().values())
    if total >= SPEND_CAP_USD:
        raise SystemExit(f"STOP: round 3 spend ${total:.3f} reached the ${SPEND_CAP_USD} cap")


# ------------------------------------------------------------------ openrouter

def openrouter(body: dict, kind: str) -> tuple[str, dict]:
    check_cap()
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                payload = json.load(response)
            break
        except Exception:  # noqa: BLE001
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    usage = payload.get("usage") or {}
    cost = usage.get("cost")
    if cost is None:
        cost = usage.get("prompt_tokens", 0) * LUNA_PRICE_IN + usage.get("completion_tokens", 0) * LUNA_PRICE_OUT
    add_spend(kind, float(cost))
    if "choices" not in payload:
        raise RuntimeError(f"OpenRouter returned no choices: {json.dumps(payload.get('error', payload))[:400]}")
    return payload["choices"][0]["message"]["content"], usage


# ------------------------------------------------------------------ versions

def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def cmd_versions() -> None:
    """Choose the document per event. Full amendments replace the original; the last listed wins."""
    import fitz

    pool = read_csv(HERE / "pool.csv")
    links = read_csv(HERE / "amendment_links.csv")
    rows = []
    for link in links:
        original = HERE / "raw" / f"{link['original_report_id']}.pdf"
        amend = HERE / "raw_amend" / link["amendment_file"]
        o_pages = fitz.open(original).page_count if original.exists() else None
        a_pages = fitz.open(amend).page_count
        consolidated = link["amendment_label"].lower().startswith(("consolidated", "corrections"))
        if o_pages is None:
            # The original link on the CPUC page is dead; the amendment is the only version.
            kind = "full"
        else:
            kind = "full" if (a_pages >= 0.5 * o_pages and not consolidated) else "partial"
        rows.append({**link, "original_pages": o_pages, "amendment_pages": a_pages,
                     "redlined": "redline" in link["amendment_file"].lower(), "kind": kind})
    write_csv(HERE / "amendments.csv", rows)
    chosen = []
    for report in pool:
        mine = [r for r in rows if r["original_report_id"] == report["report_id"]]
        full = [r for r in mine if r["kind"] == "full"]
        latest = full[-1] if full else None
        chosen.append({
            "report_id": report["report_id"],
            "utility": report["utility"],
            "event_year": report["event_year"],
            "label": report["label"],
            "original_url": report["source_url"],
            "document": f"raw_amend/{latest['amendment_file']}" if latest else f"raw/{report['report_id']}.pdf",
            "document_url": latest["amendment_url"] if latest else report["source_url"],
            "version": latest["amendment_label"] if latest else "original",
            "redlined": latest["redlined"] if latest else False,
            "partial_corrections": " ; ".join(r["amendment_url"] for r in mine if r["kind"] == "partial"),
            "read_before": report["read_before"],
            "original_available": (HERE / "raw" / f"{report['report_id']}.pdf").exists(),
        })
    write_csv(HERE / "versions.csv", chosen)
    print(f"events {len(chosen)}; using an amendment {sum(c['version'] != 'original' for c in chosen)}; "
          f"with partial corrections {sum(bool(c['partial_corrections']) for c in chosen)}")


# ------------------------------------------------------------------ extract and images

def load_pages(report_id: str) -> list[dict]:
    with open(PAGES / f"{report_id}.jsonl", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def cmd_extract(events: list[dict]) -> None:
    import fitz

    PAGES.mkdir(exist_ok=True)
    for event in events:
        out = PAGES / f"{event['report_id']}.jsonl"
        if out.exists():
            continue
        doc = fitz.open(HERE / event["document"])
        with open(out, "w", encoding="utf-8") as fh:
            for i, page in enumerate(doc, start=1):
                fh.write(json.dumps({"page": i, "text": page.get_text()}) + "\n")
    print("extracted", len(events))


PRIORITY = re.compile(r"summary|at a glance|notified|positive|notification|circuits de-?energized|de-?energized|complaint|claim|cancel", re.I)


def cmd_images(events: list[dict], workers: int) -> None:
    load_env()
    out_path = RUNS / "images.jsonl"
    done = {(r["report_id"], r["page"]) for r in map(json.loads, open(out_path, encoding="utf-8"))} if out_path.exists() else set()
    jobs = []
    skipped = []
    for event in events:
        found = image_pages.detect(HERE / event["document"])
        # Only tables that bear on the extracted fields are transcribed (summary,
        # notification, circuit, complaint, claim, cancellation tables) plus scanned
        # pages. Other image tables (thresholds, risk tools, lessons learned) are
        # logged as skipped, not flagged.
        wanted = [f for f in found if f["kind"] == "scanned_page" or PRIORITY.search(f["caption"])]
        others = [f for f in found if f not in wanted]
        for f in wanted[: image_pages.MAX_PER_REPORT]:
            if (event["report_id"], f["page"]) not in done:
                jobs.append((event, f))
        for f in wanted[image_pages.MAX_PER_REPORT:]:
            skipped.append({"report_id": event["report_id"], "page": f["page"], "kind": f["kind"], "caption": f["caption"],
                            "status": "not_transcribed_cap", "text": ""})
        for f in others:
            skipped.append({"report_id": event["report_id"], "page": f["page"], "kind": f["kind"], "caption": f["caption"],
                            "status": "skipped_not_relevant", "text": ""})

    def run(job):
        event, f = job
        text, usage = image_pages.transcribe(HERE / event["document"], f["page"], HERE / "images", lambda body: openrouter(body, "luna_vision_usd"))
        status = "unreadable" if text.strip().upper().startswith("UNREADABLE") else "transcribed"
        return {"report_id": event["report_id"], "page": f["page"], "kind": f["kind"], "caption": f["caption"], "status": status, "text": text if status == "transcribed" else ""}

    with open(out_path, "a", encoding="utf-8") as fh, cf.ThreadPoolExecutor(workers) as pool:
        for record in pool.map(run, jobs):
            fh.write(json.dumps(record) + "\n")
            fh.flush()
        for record in skipped:
            if (record["report_id"], record["page"]) not in done:
                fh.write(json.dumps(record) + "\n")
    print(f"image pages transcribed {len(jobs)}, over cap {len(skipped)}; spend {spend()}")


def pages_with_images(report_id: str, images: dict) -> list[dict]:
    pages = load_pages(report_id)
    for page in pages:
        extra = images.get((report_id, page["page"]))
        if extra:
            page["text"] = page["text"] + "\n[Image transcription]\n" + extra
    return pages


def load_images() -> dict:
    path = RUNS / "images.jsonl"
    if not path.exists():
        return {}
    return {(r["report_id"], r["page"]): r["text"] for r in map(json.loads, open(path, encoding="utf-8")) if r["status"] == "transcribed"}


# ------------------------------------------------------------------ jev

def cmd_jev(events: list[dict], workers: int) -> None:
    load_env()
    from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

    out_path = RUNS / "jev.jsonl"
    done = {(r["report_id"], r["field"]) for r in map(json.loads, open(out_path, encoding="utf-8"))} if out_path.exists() else set()
    images = load_images()
    jobs = [(e, name) for e in events for name in FIELDS if (e["report_id"], name) not in done]

    def run(job):
        event, name = job
        field = FIELDS[name]
        pages = pages_with_images(event["report_id"], images)
        by_number = {p["page"]: p for p in pages}
        sel = p2.select_pages(pages, field)
        record = {"report_id": event["report_id"], "field": name, **sel, "answer": None, "question_hash": QUESTION_HASH}
        if sel["no_matching_page"]:
            return record
        check_cap()
        state = f"{p2.PREAMBLE}\nUtility: {event['utility']}.\n\n" + format_pages([by_number[n] for n in sel["pages"]], p2.MAX_CHARS_PER_PAGE)
        with TypeSafeClient(model="jev-latest", timeout=90, retry=RetryPolicy(max_retries=3, timeout=90)) as client:
            response = client.system_one(state=state, questions={name: Choice(instructions=field["instructions"], criteria=field["criteria"])}, model="jev-latest")
        raw = response.model_dump(mode="json")
        used = (raw.get("usage") or {}).get("input_tokens") or 0
        add_spend("jev_usd", used * JEV_PRICE_IN)
        record.update(answer=raw["answers"][name], model=raw.get("model"), input_tokens=used)
        return record

    with open(out_path, "a", encoding="utf-8") as fh, cf.ThreadPoolExecutor(workers) as pool:
        for record in pool.map(run, jobs):
            fh.write(json.dumps(record) + "\n")
            fh.flush()
    print(f"jev done {len(jobs)}; spend {spend()}")


# ------------------------------------------------------------------ luna

def cmd_luna(events: list[dict], workers: int) -> None:
    load_env()
    out_path = RUNS / "luna.jsonl"
    done = {r["report_id"] for r in map(json.loads, open(out_path, encoding="utf-8"))} if out_path.exists() else set()
    images = load_images()
    jobs = [e for e in events if e["report_id"] not in done]

    def run(event):
        pages = pages_with_images(event["report_id"], images)
        # Oversized reports (one 395-page SDG&E report exceeded the context
        # window in the full run): keep pages in order up to a character budget,
        # record where the text was cut, and flag the event for review.
        truncated_after = None
        if sum(len(p["text"]) for p in pages) > MAX_LUNA_CHARS:
            kept, used = [], 0
            for page in pages:
                if used + len(page["text"]) > MAX_LUNA_CHARS:
                    break
                kept.append(page)
                used += len(page["text"])
            truncated_after = kept[-1]["page"]
            pages = kept
        body = {
            "model": LUNA_MODEL, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": LUNA_SYSTEM},
                         {"role": "user", "content": f"{LUNA_TASK}\n\nREPORT:\n{format_pages(pages)}"}],
            "usage": {"include": True},
        }
        content, usage = openrouter(body, "luna_text_usd")
        content = re.sub(r"^```(json)?|```$", "", content.strip()).strip()
        try:
            fields = json.loads(content)
        except json.JSONDecodeError:
            fields = {"parse_error": content[:500]}
        by_number = {p["page"]: p["text"] for p in pages}
        checks = {name: {"value_on_page": p2.value_on_page(item, by_number)} for name, item in fields.items() if isinstance(item, dict)}
        return {"report_id": event["report_id"], "fields": fields, "checks": checks, "truncated_after_page": truncated_after,
                "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"), "cost_usd": usage.get("cost")}

    with open(out_path, "a", encoding="utf-8") as fh, cf.ThreadPoolExecutor(workers) as pool:
        for record in pool.map(run, jobs):
            fh.write(json.dumps(record) + "\n")
            fh.flush()
    print(f"luna done {len(jobs)}; spend {spend()}")


# ------------------------------------------------------------------ workbooks

def cmd_workbooks(events: list[dict]) -> None:
    books = {r["report_id"]: r for r in read_csv(HERE / "workbooks.csv")}
    out = []
    for event in events:
        book = books.get(event["report_id"])
        if not book:
            continue
        path = HERE / "workbooks" / f"{event['report_id']}.xlsx"
        parsed = wb.circuit_times(path) if path.exists() else {}
        out.append({"report_id": event["report_id"], "workbook_url": book["workbook_url"], **parsed})
    with open(RUNS / "workbook_times.jsonl", "w", encoding="utf-8") as fh:
        for row in out:
            fh.write(json.dumps(row) + "\n")
    print("workbooks parsed", len(out))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["versions", "extract", "images", "jev", "luna", "workbooks"])
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--only", help="comma separated report_ids")
    args = parser.parse_args()
    RUNS.mkdir(exist_ok=True)
    if args.stage == "versions":
        cmd_versions()
        return
    events = read_csv(HERE / "versions.csv")
    if args.only:
        keep = set(args.only.split(","))
        events = [e for e in events if e["report_id"] in keep]
    {"extract": lambda: cmd_extract(events), "images": lambda: cmd_images(events, args.workers),
     "jev": lambda: cmd_jev(events, args.workers), "luna": lambda: cmd_luna(events, args.workers),
     "workbooks": lambda: cmd_workbooks(events)}[args.stage]()


if __name__ == "__main__":
    main()
