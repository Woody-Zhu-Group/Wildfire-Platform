"""Numeric fields from PSPS post-event reports, extracted by GPT-6 Luna on OpenRouter.

Luna reads the whole report with [Page N] markers and returns each number with
the page it came from and a short verbatim quote. Code then checks that the
quote really is on the cited page and that the number appears in the quote.
Event duration is computed in code from the two timestamps, never by the model.

Skips with a message if OPENROUTER_API_KEY is not set.

    python research/psps_reports/llm_extract.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

from common import (
    HERE,
    LUNA_MODEL,
    LUNA_PRICE_IN,
    LUNA_PRICE_OUT,
    SPEND_CAP_USD,
    format_pages,
    load_env,
    load_pages,
    load_sources,
    normalize,
)

SYSTEM = (
    "You extract numbers from California utility Public Safety Power Shutoff (PSPS) "
    "post-event reports. Every page of the report starts with a [Page N] marker. "
    "Use only facts stated in the report. For every value, give the page number where "
    "it is stated and a short quote copied exactly, character for character, from that "
    "page that contains the value. If the report does not state a value, use null for "
    "value, page, and quote. Return JSON only."
)

TASK = """Extract these fields for the de-energization event this report covers.

1. customers_deenergized: the total number of customers whose power was shut off
   (de-energized) in this event, as the report states it. Integer.
2. first_deenergization: the date and time the first customers were de-energized,
   formatted "YYYY-MM-DD HH:MM" in 24-hour local time.
3. last_restoration: the date and time power was restored to the last de-energized
   customers, formatted "YYYY-MM-DD HH:MM" in 24-hour local time.
4. counties_deenergized: the number of counties where customers were de-energized.
   Integer. Also list the county names in "names".

Return exactly this JSON shape:
{
  "customers_deenergized": {"value": int|null, "page": int|null, "quote": str|null},
  "first_deenergization": {"value": str|null, "page": int|null, "quote": str|null},
  "last_restoration": {"value": str|null, "page": int|null, "quote": str|null},
  "counties_deenergized": {"value": int|null, "names": [str], "page": int|null, "quote": str|null}
}
"""


def call_luna(report_text: str) -> tuple[dict, dict]:
    body = {
        "model": LUNA_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{TASK}\n\nREPORT:\n{report_text}"},
        ],
        "usage": {"include": True},
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        payload = json.load(response)
    content = payload["choices"][0]["message"]["content"]
    content = re.sub(r"^```(json)?|```$", "", content.strip()).strip()
    return json.loads(content), payload.get("usage") or {}


def quote_check(item: dict, pages_by_number: dict[int, str]) -> dict:
    """Is the quote on the cited page, and does the value appear in the quote?"""
    page = item.get("page")
    quote = item.get("quote")
    value = item.get("value")
    if value is None:
        return {"quote_on_page": None, "value_in_quote": None}
    on_page = bool(
        quote and isinstance(page, int) and page in pages_by_number
        and normalize(quote) in normalize(pages_by_number[page])
    )
    value_in_quote = None
    if quote and isinstance(value, int):
        digits = re.sub(r"\D", "", quote)
        value_in_quote = str(value) in digits or f"{value:,}" in quote
    return {"quote_on_page": on_page, "value_in_quote": value_in_quote}


def main() -> int:
    load_env()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set; numeric fields skipped.")
        return 0
    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    spend = 0.0
    records = []
    for source in load_sources():
        report_id = source["report_id"]
        pages = load_pages(report_id)
        pages_by_number = {p["page"]: p["text"] for p in pages}
        started = time.perf_counter()
        fields, usage = call_luna(format_pages(pages))
        cost = usage.get("cost")
        if cost is None:
            cost = (usage.get("prompt_tokens", 0) * LUNA_PRICE_IN
                    + usage.get("completion_tokens", 0) * LUNA_PRICE_OUT)
        spend += float(cost)
        checks = {name: quote_check(item, pages_by_number) for name, item in fields.items()}
        record = {
            "report_id": report_id,
            "model": LUNA_MODEL,
            "fields": fields,
            "checks": checks,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cost_usd": round(float(cost), 5),
            "latency_s": round(time.perf_counter() - started, 1),
        }
        records.append(record)
        print(report_id, json.dumps(fields), json.dumps(checks), f"${cost:.4f}", flush=True)
        if spend >= SPEND_CAP_USD:
            print(f"STOP Luna spend ${spend:.3f}")
            break
    with open(runs / "llm_raw.jsonl", "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    print(f"Luna calls {len(records)} spend ${spend:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
