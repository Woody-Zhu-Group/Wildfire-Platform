"""Acceptable gold alternatives. Original labels stay. These only widen a match."""

from __future__ import annotations

import re
from collections import Counter

from services.agent.time_resolve import resolve_time

_TIME_REASONS = {
    "records_missing_year",
    "map_missing_year",
    "trend_missing_year",
    "ranking_missing_year",
    "spatial_missing_year",
    "map_plus_trend_missing_year",
    "forecast_missing_date",
}
_PLACE_REASONS = {
    "undefined_spatial_scope",
    "missing_location",
    "risk_missing_place",
}
_REGION_REASONS = {"undefined_region"}
_VERB = re.compile(
    r"\b(?:how|what|which|show|map|list|compare|chart|give|tell|plot|find|"
    r"display|were|was|did|is|are|can|put|order|rank|break)\b",
    re.I,
)


def _gaps(question: str) -> set[str]:
    lower = " ".join(question.lower().split())
    gaps: set[str] = set()
    resolved = resolve_time(question)
    if resolved.status not in {"explicit", "relative_year", "relative_range"}:
        gaps.add("time")
    if re.search(r"\b(?:near|around|close to)\b", lower) and not re.search(
        r"\b\d+(?:\.\d+)?\s*(?:km|mi|miles?|kilometers?)\b|\bradius\b", lower
    ):
        gaps.add("place")
    if re.search(r"\b(?:northern|southern)\s+california\b|\bbay area\b", lower):
        gaps.add("region")
    return gaps


def clarify_alternatives(question: str, gold: str | None) -> list[str] | None:
    """Rule A. Two missing requirements accept either matching clarify reason."""
    gaps = _gaps(question)
    if gold is None or isinstance(gold, list) or len(gaps) < 2:
        return None
    allowed: set[str] = set()
    if "time" in gaps:
        allowed |= _TIME_REASONS
    if "place" in gaps:
        allowed |= _PLACE_REASONS
    if "region" in gaps:
        allowed |= _REGION_REASONS
    if gold not in allowed:
        return None
    return sorted(allowed)


def separate_count_question(question: str) -> bool:
    """Rule B. The question asks for separate counts, not one combined total."""
    lower = " ".join(question.lower().split())
    if "respectively" in lower:
        return True
    if re.search(r"\bhow many\b.+\band how many\b", lower):
        return True
    years = set(re.findall(r"\b20\d{2}\b", lower))
    if len(years) >= 2 and re.search(r"\bhow many\b", lower):
        return True
    return False


def tool_alternatives(question: str, gold) -> list | None:
    if not separate_count_question(question) or gold is None:
        return None
    if isinstance(gold, list):
        extra = list(gold)
    else:
        extra = [gold]
    if "data_query_records" not in extra:
        extra.append("data_query_records")
    return extra


def fragment_without_verb(question: str) -> bool:
    """Rule C. A dataset and a time range, and no verb."""
    lower = " ".join(question.lower().split())
    if _VERB.search(lower):
        return False
    if not re.search(r"\b20\d{2}\b", lower):
        return False
    return bool(
        re.search(
            r"\b(?:cal\s*fire|calfire|cpuc|epss|psps|ignitions?|incidents?|outages?)\b",
            lower,
        )
    )


def plans_equivalent(gold: list[str] | None, actual: list[str] | None) -> bool:
    """Rule D. A comparison and the matching per-entity counts are the same numbers."""
    if not gold or not actual:
        return False
    if Counter(gold) == Counter(actual):
        return True

    def counts(plan: list[str]) -> bool:
        return len(plan) >= 2 and all(name == "data_query_records" for name in plan)

    def comparison(plan: list[str]) -> bool:
        return plan == ["comparison_run"]

    return (comparison(gold) and counts(actual)) or (counts(gold) and comparison(actual))


def intent_alternatives(question: str, gold) -> list[str] | None:
    if isinstance(gold, list) or gold not in {"count", "records_list"}:
        return None
    if not fragment_without_verb(question):
        return None
    return ["count", "records_list"]
