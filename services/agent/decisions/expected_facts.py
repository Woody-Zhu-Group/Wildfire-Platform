"""Expected fact labels derived from the question text, with a rationale."""

from __future__ import annotations

import re
from typing import Any

from services.agent.routing import UTILITY_PATTERNS, _CA_COUNTIES
from services.agent.time_resolve import resolve_time

_VAGUE_TIME = re.compile(r"\b(?:recent(?:ly)?|lately|currently|right now)\b", re.I)
_RELATIVE_YEAR = re.compile(
    r"\b(?:last year|this year|yesterday|today|tomorrow|next year|past two years|two years ago)\b",
    re.I,
)
_FUTURE = re.compile(r"\b(?:tomorrow|next year|next month|in the future)\b", re.I)
_PROXIMITY = re.compile(r"\b(?:near|around|close to)\b", re.I)
_BROAD = re.compile(r"\b(?:northern|southern)\s+california\b|\bup north\b", re.I)
_RISK = re.compile(r"\b(?:risk(?:iest|y)?|risk forecast|risk score)\b", re.I)
_RISK_METRIC = re.compile(
    r"\b(?:ignitions?|incidents?|outages?|fitted risk|acres)\b", re.I
)
_INJECTION = re.compile(
    r"\b(?:ignore (?:previous |all )?instructions|system prompt|api keys?)\b",
    re.I,
)
_DATASET_CUES = (
    ("cpuc_ignitions", re.compile(r"\b(?:cpuc|utility-caused ignitions?)\b", re.I)),
    ("calfire_incidents", re.compile(r"\b(?:cal\s*fire|calfire)\b", re.I)),
    ("epss_outages", re.compile(r"\bepss\b", re.I)),
    ("psps_events", re.compile(r"\bpsps\b", re.I)),
    ("us_ignitions", re.compile(r"\bus ignitions?\b", re.I)),
    ("circuits", re.compile(r"\bcircuits?\b", re.I)),
    ("hftd", re.compile(r"\bhftd\b", re.I)),
)


def _fact(value: bool, rationale: str, *, review: bool = False) -> dict[str, Any]:
    return {
        "value": value,
        "rationale": rationale,
        "needs_human_review": review,
    }


def expected_facts(question: str) -> dict[str, dict[str, Any]]:
    """One atomic label per fact Noul. Uncertain rows set needs_human_review."""
    text = " ".join(question.strip().split())
    lower = text.lower()
    resolved = resolve_time(text)
    has_clock = bool(re.search(r"\b20\d{2}\b", text)) or bool(_RELATIVE_YEAR.search(lower))
    has_clock = has_clock or resolved.status not in {"none", "ambiguous"}
    vague = bool(_VAGUE_TIME.search(lower))
    future = bool(_FUTURE.search(lower)) or (
        resolved.year is not None and resolved.year > 2025 and "risk" in lower
    )
    utility = any(re.search(pattern, text, re.I) for pattern in UTILITY_PATTERNS.values())
    county = any(re.search(rf"\b{re.escape(name)}\b", text, re.I) for name in _CA_COUNTIES)
    place = utility or county or bool(
        re.search(r"\b(?:grid cell|circuit\s+\d|[-+]?\d{1,3}\.\d+)\b", lower)
    )
    proximity = bool(_PROXIMITY.search(lower))
    proximity_is_place = proximity and not bool(re.search(r"\b(?:around|close to)\s+\d", lower))
    broad = bool(_BROAD.search(lower)) and not utility
    asks_risk = bool(_RISK.search(lower))
    names_metric = bool(_RISK_METRIC.search(lower))
    datasets = [name for name, pattern in _DATASET_CUES if pattern.search(text)]
    return {
        "has_time_scope": _fact(
            has_clock and not (vague and not has_clock),
            "A year, date, or simple relative year is present."
            if has_clock
            else "No year, date, or simple relative year.",
            review=vague and has_clock,
        ),
        "vague_time": _fact(vague, "Vague time words are present." if vague else "No vague time words."),
        "future_time": _fact(
            future,
            "The period is after the risk cutoff or uses a forward word."
            if future
            else "No forward date.",
        ),
        "names_specific_place": _fact(
            place,
            "A county, utility, circuit, cell, or coordinates are named."
            if place
            else "No specific place is named.",
        ),
        "vague_proximity": _fact(
            proximity_is_place and not place,
            "Near, around, or close to is used without a distance, and the object is not a year or number.",
            review=proximity and place,
        ),
        "broad_region": _fact(
            broad,
            "A broad region such as northern California is named." if broad else "No broad region.",
            review=broad and utility,
        ),
        "asks_risk": _fact(asks_risk, "The question asks about risk." if asks_risk else "Risk is not requested."),
        "names_risk_metric": _fact(
            names_metric and asks_risk,
            "A measure such as ignitions, incidents, outages, or acres is named."
            if names_metric
            else "No risk measure is named.",
            review=names_metric and not asks_risk,
        ),
        "prompt_injection": _fact(
            bool(_INJECTION.search(lower)),
            "The question tries to change the assistant's instructions."
            if _INJECTION.search(lower)
            else "No instruction override.",
        ),
        "mentions_multiple_datasets": _fact(
            len(datasets) >= 2,
            "Datasets named: " + (", ".join(datasets) if datasets else "none") + ".",
        ),
    }
