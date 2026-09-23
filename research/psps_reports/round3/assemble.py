"""Build dataset.csv, review_queue.csv, and contradictions.csv from the round 3 runs.

No API calls.

    python research/psps_reports/round3/assemble.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pipeline3 as p3  # noqa: E402

RUNS = HERE / "runs"
JEV_FIELDS = list(p3.FIELDS)
REVIEW_BELOW = 0.9
CONFLICT_MINUTES = 30
REASON_ORDER = {"conflicting_sources": 0, "unreadable_page": 1, "no_matching_page": 2, "low_confidence": 3}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in open(path, encoding="utf-8")] if path.exists() else []


def minutes_apart(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    fmt = "%Y-%m-%d %H:%M"
    try:
        return abs((datetime.strptime(a, fmt) - datetime.strptime(b, fmt)).total_seconds()) / 60
    except ValueError:
        return None


def hours(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    fmt = "%Y-%m-%d %H:%M"
    try:
        return round((datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() / 3600, 2)
    except ValueError:
        return None


def main() -> None:
    events = p3.read_csv(HERE / "versions.csv")
    jev = {(r["report_id"], r["field"]): r for r in jsonl(RUNS / "jev.jsonl")}
    luna = {r["report_id"]: r for r in jsonl(RUNS / "luna.jsonl")}
    books = {r["report_id"]: r for r in jsonl(RUNS / "workbook_times.jsonl")}
    images = jsonl(RUNS / "images.jsonl")

    dataset, queue, auto_contradictions = [], [], []
    for event in events:
        rid = event["report_id"]
        row = {k: event[k] for k in ("report_id", "utility", "event_year", "label", "version", "document_url", "original_url", "redlined", "partial_corrections")}
        flags = []

        # Jev fields
        for name in JEV_FIELDS:
            rec = jev.get((rid, name))
            answer = rec.get("answer") if rec else None
            value = answer["choice"] if answer else ("NO_MATCHING_PAGE" if rec else "NOT_RUN")
            confidence = round(float(answer["confidence"]), 3) if answer else None
            pages = ";".join(map(str, rec.get("pages", []))) if rec else ""
            row[name] = value
            row[f"{name}_confidence"] = confidence
            row[f"{name}_source"] = f"PDF pages {pages}" if pages else ""
            reason = None
            if rec and not answer:
                reason = "no_matching_page"
            elif confidence is not None and confidence < REVIEW_BELOW:
                reason = "low_confidence"
            if reason:
                flags.append(f"{name}:{reason}")
                queue.append({
                    "reason": reason, "report_id": rid, "utility": event["utility"], "event_year": event["event_year"],
                    "field": name, "question": p3.FIELDS[name]["instructions"],
                    "options": "; ".join(p3.FIELDS[name]["criteria"]), "answer": "" if value == "NO_MATCHING_PAGE" else value,
                    "confidence": confidence, "page_refs": pages, "document_url": event["document_url"], "detail": "",
                })

        # Luna numbers and workbook times
        fields = (luna.get(rid) or {}).get("fields") or {}
        for name in ("customers_deenergized", "counties_deenergized"):
            item = fields.get(name) or {}
            row[name] = item.get("value")
            row[f"{name}_source"] = f"PDF page {item.get('page')}" if item.get("page") else ""
        row["counties_deenergized_names"] = "; ".join((fields.get("counties_deenergized") or {}).get("names") or [])
        book = books.get(rid) or {}
        for name, book_key, row_key in (("first_deenergization", "first_deenergization", "first_row"), ("last_restoration", "last_restoration", "last_row")):
            item = fields.get(name) or {}
            pdf_value, pdf_page = item.get("value"), item.get("page")
            book_value = book.get(book_key)
            if book_value:
                row[name] = book_value
                row[f"{name}_source"] = f"Excel sheet {book['sheet']} row {book[row_key]} ({book['circuit_rows']} circuits)"
            else:
                row[name] = pdf_value
                row[f"{name}_source"] = f"PDF page {pdf_page}" if pdf_page else ""
            row[f"{name}_pdf_value"] = pdf_value
            gap = minutes_apart(book_value, pdf_value)
            if book_value and pdf_value and gap is not None and gap > CONFLICT_MINUTES:
                flags.append(f"{name}:conflicting_sources")
                detail = f"workbook {book_value} (sheet {book['sheet']} row {book[row_key]}) vs PDF {pdf_value} (page {pdf_page})"
                queue.append({
                    "reason": "conflicting_sources", "report_id": rid, "utility": event["utility"], "event_year": event["event_year"],
                    "field": name, "question": f"Which {name.replace('_', ' ')} is right?", "options": "", "answer": book_value,
                    "confidence": "", "page_refs": str(pdf_page or ""), "document_url": event["document_url"], "detail": detail,
                })
                auto_contradictions.append({"set": "automatic", "report_id": rid, "field": name, "value_a": book_value,
                                            "source_a": f"workbook {book['sheet']}", "value_b": pdf_value,
                                            "source_b": f"PDF page {pdf_page}", "note": "Workbook circuit table vs PDF statement."})
        row["event_duration_hours"] = hours(row["first_deenergization"], row["last_restoration"])

        # Version-level conflicts
        if event["partial_corrections"]:
            flags.append("event:conflicting_sources")
            queue.append({
                "reason": "conflicting_sources", "report_id": rid, "utility": event["utility"], "event_year": event["event_year"],
                "field": "all fields", "question": "A partial correction was filed; check whether it changes any extracted value.",
                "options": "", "answer": "", "confidence": "", "page_refs": "", "document_url": event["document_url"],
                "detail": f"correction: {event['partial_corrections']}",
            })
        if event["redlined"] == "True":
            flags.append("numbers:conflicting_sources")
            queue.append({
                "reason": "conflicting_sources", "report_id": rid, "utility": event["utility"], "event_year": event["event_year"],
                "field": "numeric fields", "question": "The extracted version is a redline; check that no struck (deleted) value was used.",
                "options": "", "answer": "", "confidence": "", "page_refs": "", "document_url": event["document_url"], "detail": "",
            })

        truncated = (luna.get(rid) or {}).get("truncated_after_page")
        row["luna_text_truncated_after_page"] = truncated
        if truncated:
            flags.append("numbers:unreadable_page")
            queue.append({
                "reason": "unreadable_page", "report_id": rid, "utility": event["utility"], "event_year": event["event_year"],
                "field": "numeric fields", "question": "The report was too long for the model; pages after the cut were not read for the numbers.",
                "options": "", "answer": "", "confidence": "", "page_refs": f"after {truncated}", "document_url": event["document_url"], "detail": "",
            })

        # Image pages
        mine = [r for r in images if r["report_id"] == rid]
        bad = [r for r in mine if r["status"] in ("unreadable", "not_transcribed_cap")]
        row["image_pages_transcribed"] = sum(r["status"] == "transcribed" for r in mine)
        row["image_pages_unread"] = ";".join(f"{r['page']}({r['status']})" for r in bad)
        if bad:
            flags.append("event:unreadable_page")
            queue.append({
                "reason": "unreadable_page", "report_id": rid, "utility": event["utility"], "event_year": event["event_year"],
                "field": "table pages", "question": "Relevant table page(s) could not be read; check the fields that depend on them.",
                "options": "", "answer": "", "confidence": "", "page_refs": ";".join(str(r["page"]) for r in bad),
                "document_url": event["document_url"], "detail": "; ".join(f"p{r['page']} {r['caption']} ({r['status']})" for r in bad),
            })

        # Automatic contradiction candidates: several different stated totals of customers de-energized.
        pages_path = p3.PAGES / f"{rid}.jsonl"
        if pages_path.exists():
            totals = {}
            for page in jsonl(pages_path):
                text = " ".join(page["text"].split())
                for m in re.finditer(r"(?:total of |de-?\s?energized |ultimately de-?\s?energized )(?:approximately )?(\d{1,3}(?:,\d{3})+|\d{2,6}) customers|(\d{1,3}(?:,\d{3})+|\d{2,6}) customers (?:were )?(?:ultimately )?de-?\s?energized", text, re.I):
                    value = int((m.group(1) or m.group(2)).replace(",", ""))
                    totals.setdefault(value, page["page"])
            if len(totals) > 1:
                values = sorted(totals.items(), key=lambda kv: -kv[0])[:3]
                auto_contradictions.append({"set": "automatic", "report_id": rid, "field": "customers_deenergized (stated totals)",
                                            "value_a": values[0][0], "source_a": f"PDF page {values[0][1]}",
                                            "value_b": "; ".join(str(v) for v, _ in values[1:]),
                                            "source_b": "; ".join(f"PDF page {p}" for _, p in values[1:]),
                                            "note": "Candidate: several different totals stated as customers de-energized. Some are partial counts (for example per phase); not hand-checked."})
        row["flags"] = " ".join(flags)
        dataset.append(row)

    queue.sort(key=lambda q: (REASON_ORDER[q["reason"]], q["confidence"] if isinstance(q["confidence"], float) else 1.0, q["report_id"], q["field"]))
    for i, item in enumerate(queue, start=1):
        item["item"] = i
    queue = [{"item": q.pop("item"), **q} for q in queue]
    p3.write_csv(HERE / "dataset.csv", dataset)
    p3.write_csv(HERE / "review_queue.csv", queue)

    hand = p3.read_csv(HERE.parent / "round2" / "contradictions.csv")
    extra = p3.read_csv(HERE / "contradictions_sample10.csv") if (HERE / "contradictions_sample10.csv").exists() else []
    rows = [{**r, "set": r["set"] + " (hand-checked)"} for r in hand + extra] + auto_contradictions
    fields = ["set", "report_id", "field", "value_a", "source_a", "value_b", "source_b", "note"]
    with open(HERE / "contradictions.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})

    counts = {}
    for q in queue:
        counts[q["reason"]] = counts.get(q["reason"], 0) + 1
    print(f"events {len(dataset)}; review queue {len(queue)} items {counts}; "
          f"at 2-3 min per item: {len(queue) * 2 / 60:.1f} to {len(queue) * 3 / 60:.1f} hours")
    print(f"contradictions: hand-checked {len(hand) + len(extra)}, automatic candidates {len(auto_contradictions)}")
    print("spend", p3.spend())


if __name__ == "__main__":
    main()
