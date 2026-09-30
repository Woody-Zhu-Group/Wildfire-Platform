"""Code checks on reviewer answers (DESIGN.md, "Code checks", plus the changes before the freeze).

An answer is valid only if:
1. `value` is an allowed option, a well-formed number or time, or well-formed
   correction syntax (`no change`, or `field=value; field=value`);
2. `quote` appears on a cited page, in the text layer or the stored transcription,
   after NFKC normalization (which unfolds ligatures), case folding, and removal of
   all whitespace. `not_stated` and `null` answers skip the quote check but must list
   the pages read and the search terms used. A quote found nowhere, when a cited page
   is image-only, is `unverified_visual`. For partial-correction items at least one
   cited page must be from a correction letter;
3. `unsure` is false.

Two answers agree when both are valid and their canonical values are identical.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

import packets as pk

TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
INT = re.compile(r"^\d+$")
CORRECTABLE = pk.FIELDS + ["counties_deenergized_names"]
EMPTY_QUOTE_VALUES = {"not_stated", "null"}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[\s​‌‍﻿]+", "", text)


def _time_ok(v: str) -> bool:
    if not TIME.match(v):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%d %H:%M")
        return True
    except ValueError:
        return False


def field_value(field: str, v: str) -> str | None:
    """Canonical form of a value for a dataset field, or None if malformed."""
    v = v.strip()
    if v.lower() in ("null", "none", ""):
        return "null"
    if field in ("customers_deenergized", "counties_deenergized"):
        v = v.replace(",", "")
        return str(int(v)) if INT.match(v) else None
    if field in ("first_deenergization", "last_restoration"):
        return v if _time_ok(v) else None
    if field == "counties_deenergized_names":
        return v
    return None


def canonical(item: dict, value) -> str | None:
    """Canonical value, or None when the value is not allowed for this item."""
    if value is None:
        return None
    v = str(value).strip()
    options = [o.strip() for o in item["options"].split(";") if o.strip()] if item["reason"] or item["field"] in pk.CAT else []
    if item["field"] in pk.CAT:
        return v if v in options else None
    if item["field"] in pk.NUM:
        return field_value(item["field"], v)
    # Special items: no change, or field=value corrections.
    if v.lower() == "no change":
        return "no change"
    parts = [p.strip() for p in v.split(";") if p.strip()]
    out = {}
    for part in parts:
        if "=" not in part:
            return None
        f, fv = (x.strip() for x in part.split("=", 1))
        if f in pk.CAT:
            allowed = {"mbl_advance_notice": ["all_notified", "some_not_notified", "not_stated", "not_applicable"],
                       "wind_threshold_cited": ["met", "not_met", "not_stated"],
                       "complaints_reported": ["one_or_more", "zero", "not_stated"],
                       "claims_reported": ["one_or_more", "zero", "not_stated"],
                       "canceled_after_notice": ["yes", "no", "not_stated"]}[f]
            cv = fv if fv in allowed else None
        elif f in CORRECTABLE:
            cv = field_value(f, fv)
        else:
            cv = None
        if cv is None or f in out:
            return None
        out[f] = cv
    return "; ".join(f"{k}={out[k]}" for k in sorted(out)) if out else None


def parse_pages(pages) -> list[tuple[str, int]] | None:
    if isinstance(pages, (str, int)):
        pages = [pages]
    out = []
    for p in pages or []:
        m = re.fullmatch(r"\s*(?:(report|correction\d?)\s*:\s*)?(\d+)\s*", str(p))
        if not m:
            return None
        out.append((m.group(1) or "report", int(m.group(2))))
    return out


def check(answer: dict | None, item: dict, docs: dict) -> dict:
    """Returns {valid, reason, canonical}."""
    if answer is None:
        return {"valid": False, "reason": "missing", "canonical": None}
    value = canonical(item, answer.get("value"))
    if value is None:
        return {"valid": False, "reason": "value_not_allowed", "canonical": None}
    if answer.get("unsure") is not False:
        return {"valid": False, "reason": "unsure", "canonical": value}
    pages = parse_pages(answer.get("pages"))
    if pages is None:
        return {"valid": False, "reason": "pages_malformed", "canonical": value}
    cited = [(d, n) for d, n in pages if d in docs and n in docs[d]]
    if len(cited) != len(pages):
        return {"valid": False, "reason": "page_not_in_document", "canonical": value}
    if item["field"] == "all fields" and not any(d.startswith("correction") for d, _ in cited):
        return {"valid": False, "reason": "no_correction_page_cited", "canonical": value}
    quote = norm(answer.get("quote") or "")
    if value in EMPTY_QUOTE_VALUES and not quote:
        if not cited:
            return {"valid": False, "reason": "no_pages_read_listed", "canonical": value}
        if not [t for t in answer.get("search_terms") or [] if str(t).strip()]:
            return {"valid": False, "reason": "no_search_terms", "canonical": value}
        return {"valid": True, "reason": "not_stated_with_search", "canonical": value}
    if not quote:
        return {"valid": False, "reason": "no_quote", "canonical": value}
    if not cited:
        return {"valid": False, "reason": "no_pages", "canonical": value}
    for d, n in cited:
        page = docs[d][n]
        if quote in norm(page["text"]) or quote in norm(page["transcription"]):
            return {"valid": True, "reason": "quote_found", "canonical": value}
    if any(docs[d][n]["image_only"] for d, n in cited):
        return {"valid": False, "reason": "unverified_visual", "canonical": value}
    return {"valid": False, "reason": "quote_not_found", "canonical": value}


def decide(r1: dict, r2: dict) -> str:
    if r1["valid"] and r2["valid"] and r1["canonical"] == r2["canonical"]:
        return "model_review_agreed"
    return "unresolved"
