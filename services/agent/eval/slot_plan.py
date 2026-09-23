"""Plans built only from router slots. Jev is not called."""

from __future__ import annotations

import re

from services.agent.routing import route_question

_PER_MONTH = re.compile(
    r"\b(?:each|every|per)\s+months?\b|\bby\s+months?\b|\bmonthly\b|"
    r"\bmonth by month\b|\bin each month\b",
    re.I,
)
_BY_COUNTY = re.compile(r"\b(?:by|per|each)\s+count(?:y|ies)\b", re.I)
_ALSO_TOTAL = re.compile(
    r"\byearly\s+total\b|\bplus\s+(?:the\s+)?(?:yearly\s+)?total\b|"
    r"\bcounts?\b.+\b(?:monthly|by month|each month)\b|"
    r"\b(?:monthly|by month|each month)\b.+\b(?:and|plus)\b.+\btotal\b",
    re.I,
)


def slot_plan(question: str) -> list[str] | None:
    """One count per named entity, a monthly series, or a county rank."""
    slots = route_question(question).slots
    dataset = slots.get("dataset")
    years = [int(item) for item in (slots.get("years") or [])]
    utilities = list(slots.get("utilities") or [])
    counties = list(slots.get("counties") or [])
    lower = " ".join(question.lower().split())
    explicit = set(re.findall(r"\b20\d{2}\b", lower))
    if len(counties) > 1 and dataset and (slots.get("year") or len(years) == 1):
        return ["data_query_records"] * len(counties)
    if len(utilities) > 1 and dataset and slots.get("year"):
        return ["data_query_records"] * len(utilities)
    if (
        len(explicit) > 1
        and dataset
        and len(years) > 1
        and len(utilities) <= 1
        and len(counties) <= 1
    ):
        return ["data_query_records"] * len(years)
    if _PER_MONTH.search(lower) and dataset and slots.get("year"):
        if _ALSO_TOTAL.search(lower):
            return ["data_query_records", "visualization_create"]
        return ["visualization_create"]
    if _BY_COUNTY.search(lower) and dataset and slots.get("year") and len(counties) <= 1:
        return ["data_query_rank"]
    return None
