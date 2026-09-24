"""Ask for every missing item in one clarification, with an example rephrasing.

route_question picks the clarification rule and its first question. When the
router's slots show that the question is missing more than that one thing (a
year, a place, a dataset), the text also asks for the others and ends with a
concrete rephrasing. The rule id and path never change. When only the rule's
own item is missing, the text is returned unchanged.
"""

from __future__ import annotations

from typing import Any

from services.shared.dataset_registry import (
    CALIFORNIA_COUNTIES,
    CLARIFY_DATASET_LABELS,
    UTILITY_CLARIFY_LABELS,
)

# Each clarification rule: what it already asks for, then what else it may need.
# Rules that are not about a missing input (coverage, tool gaps, contradictions,
# forward dates) are not listed and keep their text.
RULE_ITEMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "records_missing_year": ("year", ("dataset",)),
    "map_missing_year": ("year", ("dataset",)),
    "trend_missing_year": ("year", ("dataset",)),
    "map_plus_trend_missing_year": ("year", ("dataset",)),
    "ranking_missing_year": ("year", ("dataset",)),
    "spatial_missing_year": ("year", ("place",)),
    "ambiguous_relative_time": ("year", ("dataset",)),
    "series_mode_missing_year": ("year", ("dataset",)),
    "series_mode_missing_dataset": ("dataset", ("year",)),
    "ranking_missing_slots": ("dataset", ("year",)),
    "risk_missing_place": ("place", ("date",)),
    "forecast_missing_date": ("date", ("place",)),
    "missing_location": ("place", ("year", "dataset")),
    "undefined_spatial_scope": ("place", ("year", "dataset")),
    "city_needs_place": ("place", ("year", "dataset")),
    "unknown_county": ("place", ("year", "dataset")),
    "undefined_region": ("place", ("year", "dataset")),
}

# Items the rule's own text already asks for; they are not asked twice, but the
# example rephrasing is still added when they are missing.
ALREADY_ASKED: dict[str, set[str]] = {
    "risk_missing_place": {"date"},
    "ranking_missing_slots": {"year"},
}

# Place clarifications ask for a year or a dataset only when the question reads
# event data, which the warehouse always serves for a year or date range.
_EVENT_DATASETS = {"cpuc_ignitions", "calfire_incidents", "epss_outages", "psps_events", "us_ignitions"}
_PLACE_RULES = {"missing_location", "undefined_spatial_scope", "city_needs_place", "unknown_county", "undefined_region"}

_LABELS = CLARIFY_DATASET_LABELS
_ASK = {
    "year": "a year or date range",
    "date": "one past calendar day through 2025-12-31",
    "dataset": "a dataset (CPUC ignitions, CAL FIRE incidents, EPSS outages, or PSPS events)",
    "place": "a place (a county, a utility territory, or latitude/longitude)",
}


def _has_time(slots: dict[str, Any]) -> bool:
    status = (slots.get("time_resolution") or {}).get("status")
    return bool(slots.get("year") or slots.get("years") or slots.get("start_date")) and status not in {"ambiguous"}


def _has_place(slots: dict[str, Any]) -> bool:
    return bool(slots.get("county") or slots.get("counties") or slots.get("coords") or slots.get("utilities"))


def _statewide(text: str, slots: dict[str, Any]) -> bool:
    """A statewide surface or grid map has no place to ask for."""
    import re

    return bool(slots.get("map_mode")) or bool(
        re.search(r"\b(?:surface|statewide|grid map|all of california)\b", text, re.I)
    )


def _reads_events(text: str, datasets: list[str]) -> bool:
    """The question reads event data: a named event dataset or a generic event word."""
    import re

    if set(datasets) & _EVENT_DATASETS:
        return True
    return not datasets and bool(
        re.search(r"\b(?:fires?|wildfires?|incidents?|events?|outages?|shutoffs?)\b", text, re.I)
    )


def _asks_risk(text: str) -> bool:
    from services.agent.routing import _wants_risk

    return bool(_wants_risk(text.lower()))


def missing_items(rule: str, text: str, slots: dict[str, Any]) -> list[str]:
    """The rule's own item first, then every other item the slots show is missing."""
    from services.agent.routing import _datasets

    spec = RULE_ITEMS.get(rule)
    if spec is None:
        return []
    primary, extras = spec
    datasets = _datasets(text)
    missing = [primary]
    for item in extras:
        if item in {"year", "date"}:
            if rule in _PLACE_RULES and not _reads_events(text, datasets) and not _asks_risk(text):
                continue
            if _has_time(slots):
                continue
            # A risk question needs one scoreable day, not a year.
            missing.append("date" if item == "year" and _asks_risk(text) else item)
        elif item == "dataset":
            # Acres are CAL FIRE only, so an acres chart already names its dataset.
            if datasets or _asks_risk(text) or "acre" in text.lower():
                continue
            if rule in _PLACE_RULES and not _reads_events(text, datasets):
                continue
            missing.append("dataset")
        elif item == "place":
            if _has_place(slots) or _statewide(text, slots):
                continue
            missing.append("place")
    return list(dict.fromkeys(missing))


PLACE_PLACEHOLDER = "[a county]"
_UTILITY_LABELS = UTILITY_CLARIFY_LABELS
# A place phrase starts after one of these words and ends at the next stop word.
_PLACE_START = r"(?:near|around|close to|in|for|at)"
_PLACE_STOP = {
    "in", "for", "during", "from", "and", "or", "than", "since", "between", "last",
    "this", "next", "on", "at", "of", "to", "over", "with", "rn", "now", "today",
}


def _bare_county(text: str) -> str | None:
    """A county the question names by a place phrase that is exactly a county name.

    "near Sacramento in 2024" resolves to Sacramento; "around Lake Tahoe" does
    not resolve to Lake County, and a city such as San Jose resolves to nothing.
    """
    import re

    names = {name.lower(): name for name in CALIFORNIA_COUNTIES}
    for match in re.finditer(rf"\b{_PLACE_START}\s+(.+)", text, re.I):
        words = []
        for token in re.split(r"\s+", match.group(1)):
            word = re.sub(r"[^\w&'-]", "", token).lower()
            if not word or word in _PLACE_STOP or word.isdigit():
                break
            words.append(word)
            if re.search(r"[?.,;:!]$", token):
                break
        phrase = " ".join(words).removesuffix(" county").removesuffix(" counties")
        if phrase in names:
            return names[phrase]
    return None


def resolved_place(text: str, slots: dict[str, Any]) -> str | None:
    """The place the question already resolved, or None. Never a place it did not name."""
    if slots.get("county"):
        return f"{slots['county']} County"
    if slots.get("counties"):
        return f"{slots['counties'][0]} County"
    if slots.get("utilities"):
        utility = slots["utilities"][0]
        return f"{_UTILITY_LABELS.get(utility, utility)} territory"
    if slots.get("coords"):
        lat, lon = slots["coords"][:2]
        return f"{lat}, {lon}"
    county = _bare_county(text)
    return f"{county} County" if county else None


def _example(rule: str, text: str, slots: dict[str, Any], missing: list[str]) -> str:
    import re

    from services.agent.routing import _datasets

    datasets = _datasets(text)
    dataset = _LABELS.get(datasets[0] if datasets else "", "CPUC ignitions")
    place = resolved_place(text, slots) or PLACE_PLACEHOLDER
    year = str(slots.get("year") or "2023")
    needs_place = "place" in missing or rule in _PLACE_RULES or slots.get("county") or slots.get("utilities")
    where = f" in {place}" if needs_place else ""
    if _asks_risk(text) or "date" in missing:
        return f"What was the fitted ignition risk for {place} on 2023-08-15?"
    if rule.startswith("map"):
        return f"Map {dataset}{where} for {year}."
    if rule.startswith("series"):
        lower = text.lower()
        if "acre" in lower:
            return f"Show the cumulative acres burned by CAL FIRE incidents{where} for {year}."
        if "season" in lower:
            return f"Show a seasonal chart of {dataset}{where} for {year}."
        if "year" in lower:
            return f"Show a yearly chart of {dataset}{where} from 2019 through 2023."
    if rule.startswith("trend") or rule.startswith("series"):
        return f"Show the monthly trend of {dataset}{where} for {year}."
    if rule.startswith("ranking"):
        group = "counties"
        if not re.search(r"\bcount(?:y|ies)\b", text, re.I) and re.search(
            r"\butilit(?:y|ies)\b(?![- ](?:caused|attributed|tagged))", text, re.I
        ):
            group = "utilities"
        return f"Rank {group} by {dataset} for {year}."
    return f"How many {dataset} were there{where} in {year}?"


def complete_clarification(rule: str, text: str, slots: dict[str, Any], answer: str | None) -> str | None:
    """Return the clarification text asking for every missing item."""
    if not answer:
        return answer
    missing = missing_items(rule, text, slots)
    if len(missing) < 2:
        return answer
    example = _example(rule, text, slots, missing)
    skip = ALREADY_ASKED.get(rule, set())
    others = [_ASK[item] for item in missing[1:] if item not in skip]
    if not others:
        return f"{answer.rstrip()} For example: \"{example}\""
    also = others[0] if len(others) == 1 else ", ".join(others[:-1]) + f", and {others[-1]}"
    return f"{answer.rstrip()} I also need {also}. For example: \"{example}\""
