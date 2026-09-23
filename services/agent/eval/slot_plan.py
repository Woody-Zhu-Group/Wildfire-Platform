"""Plans built only from router slots. Jev is not called."""

from __future__ import annotations

import re
from typing import Any

from services.agent.routing import RouteDecision, route_question

MAX_ENTITY_CALLS = 10
_VIZ = {
    "cpuc_ignitions": "ignitions",
    "epss_outages": "epss",
    "psps_events": "psps",
    "calfire_incidents": "calfire",
    "us_ignitions": "us_ignitions",
    "hftd": "hftd",
}
_RANK_DATASETS = {"cpuc_ignitions", "calfire_incidents"}

_PER_MONTH = re.compile(
    r"\b(?:each|every|per)\s+months?\b|\bby\s+months?\b|\bmonthly\b|"
    r"\bmonth by month\b|\bin each month\b",
    re.I,
)
_BY_COUNTY = re.compile(r"\b(?:by|per|each)\s+count(?:y|ies)\b", re.I)
# A plan built from slots can only count, chart one monthly series, or rank by
# county. Anything below means the plan would answer a narrower question than
# the one asked, so the rule falls back and the deferral stands.
_MAP_ASK = re.compile(r"\bmaps?\b|\bmapped\b|\bwhere\b|\blocations?\s+of\b", re.I)
_OTHER_METRIC = re.compile(
    r"\bacres?\b|\bacreage\b|\bcustomers?\b|\brate\b|\bratio\b|"
    r"\bper\s+(?:circuit|customer|mile|km|kilometer|square)\b",
    re.I,
)
_MONTH_WORD = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|"
    r"\b(?:q[1-4]|quarter|week of|first half|second half|spring|summer|fall|autumn|winter)\b",
    re.I,
)
_COUNTY_CAPABLE = {"calfire_incidents", "cpuc_ignitions", "epss_outages", "psps_events", "circuits"}


def _full_year_window(slots: dict[str, Any]) -> bool:
    start = slots.get("start_date")
    end = slots.get("end_date")
    if not start or not end:
        return True
    return start.endswith("-01-01") and end.endswith("-12-31")


def _would_lose_something(lower: str, slots: dict[str, Any]) -> bool:
    """True when every slot plan for this question drops part of it."""
    dataset = slots.get("dataset")
    years = list(slots.get("years") or [])
    utilities = list(slots.get("utilities") or [])
    counties = list(slots.get("counties") or [])
    if _MAP_ASK.search(lower):
        return True
    if _OTHER_METRIC.search(lower):
        return True
    if len(years) > 1 and (_MONTH_WORD.search(lower) or not _full_year_window(slots)):
        return True
    if _PER_MONTH.search(lower) and (len(utilities) > 1 or len(counties) > 1):
        return True
    if len(counties) == 1 and dataset not in _COUNTY_CAPABLE:
        return True
    return False
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
    if not dataset or _would_lose_something(lower, slots):
        return None
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


def _time_fields(slots: dict[str, Any], year: int) -> dict[str, Any]:
    start = slots.get("start_date")
    end = slots.get("end_date")
    full_year = (
        isinstance(start, str)
        and isinstance(end, str)
        and start.endswith("-01-01")
        and end.endswith("-12-31")
        and start[:4] == end[:4]
    )
    if start and end and not full_year:
        return {"start_date": start, "end_date": end, "year": year}
    return {"year": year}


def slot_tool_calls(question: str) -> list[tuple[str, dict[str, Any]]] | None:
    """Concrete calls for a slot plan. None means fall back to the model path."""
    names = slot_plan(question)
    if not names:
        return None
    if names.count("data_query_records") > MAX_ENTITY_CALLS:
        return None
    if len(names) > MAX_ENTITY_CALLS:
        return None
    slots = route_question(question).slots
    dataset = slots.get("dataset")
    if not dataset:
        return None
    years = [int(item) for item in (slots.get("years") or [])]
    year = slots.get("year")
    if year is None and len(years) == 1:
        year = years[0]
    utilities = list(slots.get("utilities") or [])
    counties = list(slots.get("counties") or [])
    viz = _VIZ.get(dataset, dataset)
    calls: list[tuple[str, dict[str, Any]]] = []
    record_index = 0
    for name in names:
        if name == "visualization_create":
            if year is None:
                return None
            calls.append(
                (
                    "visualization_create",
                    {
                        "kind": "time_series",
                        "dataset": viz,
                        "interval": "monthly",
                        "year": int(year),
                        **({"utility": utilities[0]} if len(utilities) == 1 else {}),
                    },
                )
            )
            continue
        if name == "data_query_rank":
            if dataset not in _RANK_DATASETS or year is None:
                return None
            calls.append(
                (
                    "data_query_rank",
                    {
                        "dataset": dataset,
                        "group_by": "county",
                        "metric": "count",
                        "year": int(year),
                    },
                )
            )
            continue
        if name != "data_query_records":
            return None
        if len(utilities) > 1 and len(counties) <= 1 and len(years) <= 1:
            utility = utilities[record_index]
            record_index += 1
            if year is None:
                return None
            calls.append(
                (
                    "data_query_records",
                    {
                        "dataset": dataset,
                        "result_mode": "count",
                        "utility": utility,
                        **({"county": counties[0]} if len(counties) == 1 else {}),
                        **_time_fields(slots, int(year)),
                    },
                )
            )
            continue
        if len(counties) > 1 and len(years) <= 1:
            county = counties[record_index]
            record_index += 1
            if year is None:
                return None
            calls.append(
                (
                    "data_query_records",
                    {
                        "dataset": dataset,
                        "result_mode": "count",
                        "county": county,
                        **_time_fields(slots, int(year)),
                    },
                )
            )
            continue
        if len(years) > 1:
            item_year = years[record_index]
            record_index += 1
            extra: dict[str, Any] = {"year": item_year}
            if len(utilities) == 1:
                extra["utility"] = utilities[0]
            if len(counties) == 1:
                extra["county"] = counties[0]
            calls.append(
                (
                    "data_query_records",
                    {"dataset": dataset, "result_mode": "count", **extra},
                )
            )
            continue
        if year is None:
            return None
        extra = _time_fields(slots, int(year))
        if len(utilities) == 1:
            extra["utility"] = utilities[0]
        if len(counties) == 1:
            extra["county"] = counties[0]
        calls.append(
            ("data_query_records", {"dataset": dataset, "result_mode": "count", **extra})
        )
    return calls or None


def apply_slot_plan(decision: RouteDecision, question: str) -> RouteDecision:
    """Replace a deferred multi-entity route when the slot rule can plan it."""
    if decision.path in {"clarification", "unsupported"}:
        return decision
    if decision.rule != "multi_entity_deferred":
        return decision
    calls = slot_tool_calls(question)
    if not calls:
        return decision
    return RouteDecision(
        "deterministic",
        "slot_plan",
        "Router slots name every entity, so the slot rule plans the calls",
        tool_calls=calls,
        slots=decision.slots,
    )
