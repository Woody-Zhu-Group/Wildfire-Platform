"""Ask for every missing item in one clarification, with an example rephrasing.

route_question and decide mode pick the clarification rule and its first
question. Which items are missing (a year or date, a dataset, a ranking
grouping, a place) is computed from the question and the router's slots alone,
never from the rule, so the text asks for the same items whichever rule's
wording is the base. The rule contributes only the item its own text asks for.
Items the base text does not already ask for are appended, then a concrete
rephrasing. The options offered come from the registry for the task: ranking
options from RANK_MEASURES for the grouping, comparison options from
COMPARE_MEASURES, yearly and seasonal chart options from SERIES_DATASETS. The
rule id and path never change.
"""

from __future__ import annotations

import re
from typing import Any

from services.shared.dataset_registry import (
    CALIFORNIA_COUNTIES,
    CLARIFY_DATASET_LABELS,
    COMPARE_MEASURES,
    MEASURE_DATASETS,
    MEASURE_UTILITIES,
    RANK_MEASURES,
    SERIES_DATASETS,
    UTILITY_CLARIFY_LABELS,
)

# The item each clarification rule's own text asks for. Rules that are not
# about a missing input (coverage, tool gaps, contradictions, forward dates)
# are not listed and keep their text. ranking_missing_slots asks for the
# dataset, the grouping, and the period in its own words (rank_slots_question).
# This is what the base text says, not what is missing: missing_items reads that
# from the question and the slots.
RULE_ITEM: dict[str, str | None] = {
    "records_missing_year": "year",
    "map_missing_year": "year",
    "trend_missing_year": "year",
    "map_plus_trend_missing_year": "year",
    "ranking_missing_year": "year",
    "spatial_missing_year": "year",
    "ambiguous_relative_time": "year",
    "series_mode_missing_year": "year",
    "series_mode_missing_dataset": "dataset",
    "ranking_missing_slots": None,
    "ambiguous_risk_metric": "measure",
    "risk_missing_place": "place",
    "forecast_missing_date": "date",
    "missing_location": "place",
    "undefined_spatial_scope": "place",
    "city_needs_place": "place",
    "unknown_county": "place",
    "undefined_region": "place",
}

# Whether a text already asks for an item, read from its words, so any base
# wording works (a rule's own text, Jev's, or a generic fallback).
ASKED: dict[str, re.Pattern[str]] = {
    "year": re.compile(r"\byear\b|\bdate range\b|\btime period\b", re.I),
    "date": re.compile(r"\bcalendar day\b|\bpast date\b", re.I),
    # A measure names its dataset.
    "dataset": re.compile(r"\bdatasets?\b|\bmeasure\b", re.I),
    "grouping": re.compile(r"\bgrouping\b", re.I),
    "place": re.compile(
        r"\bplace\b|\blatitude\b|\bcoordinates\b|\bbounding box\b|\bradius\b|\bpolygon\b"
        r"|\bregion\b|\bwhich (?:cell|county)\b",
        re.I,
    ),
    "measure": re.compile(r"\bmeasure\b", re.I),
}

# A question asks for a year and a dataset only when it reads event data, which
# the warehouse always serves for a year or date range.
_EVENT_DATASETS = {"cpuc_ignitions", "calfire_incidents", "epss_outages", "psps_events", "us_ignitions"}
_PLACE_RULES = {"missing_location", "undefined_spatial_scope", "city_needs_place", "unknown_county", "undefined_region"}

_LABELS = CLARIFY_DATASET_LABELS
_GROUP_PLURALS = {"county": "counties", "utility": "utilities", "circuit": "circuits"}
# A utility word that qualifies the records ("utility-caused ignitions") is not
# the grouping.
_UTILITY_QUALIFIER = re.compile(r"\butilit(?:y|ies)[- ](?:caused|attributed|tagged|related)\b", re.I)


def _join(items: list[str], word: str = "or") -> str:
    if len(items) <= 2:
        return f" {word} ".join(items)
    return ", ".join(items[:-1]) + f", {word} {items[-1]}"


def _ordered_datasets(datasets) -> list[str]:
    """Datasets in the registry's clarification order."""
    wanted = set(datasets)
    return [key for key in _LABELS if key in wanted]


# Registry options for each task.


def rank_datasets(group: str | None = None) -> list[str]:
    """Datasets a ranking can read, for one grouping or for any grouping."""
    groups = [group] if group in RANK_MEASURES else list(RANK_MEASURES)
    return _ordered_datasets(MEASURE_DATASETS[item] for g in groups for item in RANK_MEASURES[g])


def rank_groups(dataset: str | None = None) -> list[str]:
    """Groupings a ranking can use, for one dataset or for any dataset."""
    return [
        group
        for group in _GROUP_PLURALS
        if group in RANK_MEASURES
        and (dataset is None or any(MEASURE_DATASETS[item] == dataset for item in RANK_MEASURES[group]))
    ]


def rank_options() -> str:
    """Every ranking the tools run, by dataset: counties or utilities in CPUC ignitions, and so on."""
    return _join(
        [
            f"{_join([_GROUP_PLURALS[g] for g in rank_groups(dataset)])} in {_LABELS[dataset]}"
            for dataset in rank_datasets()
        ]
    )


def rank_slots_question() -> str:
    """The ranking_missing_slots question, with the registry's rankings."""
    return (
        f"Which dataset and grouping should I rank? I can rank {rank_options()}, "
        "for one year or date range."
    )


def compare_datasets(group: str | None, utilities: list[str]) -> list[str]:
    """Datasets a comparison can read for the grouping and the named utilities."""
    groups = [group] if group in COMPARE_MEASURES else list(COMPARE_MEASURES)
    measures = [
        item
        for g in groups
        for item in COMPARE_MEASURES[g]
        if len(utilities) < 2 or set(utilities) & MEASURE_UTILITIES.get(item, set(utilities))
    ]
    return _ordered_datasets(MEASURE_DATASETS[item] for item in measures)


def series_datasets() -> list[str]:
    """Datasets a yearly or seasonal chart reads when the question fixes none."""
    return _ordered_datasets(SERIES_DATASETS)


def series_dataset_question() -> str:
    """The series_mode_missing_dataset question, with the registry's chartable datasets."""
    return f"Which dataset should I chart: {_join([_LABELS[key] for key in series_datasets()])}?"


def _series_without_dataset(text: str) -> bool:
    """A yearly or seasonal chart whose dataset the question must name."""
    from services.agent.routing import _series_mode_request

    request = _series_mode_request(text.lower())
    return request is not None and request[1] is None


def event_datasets() -> list[str]:
    """Datasets a count, map, list, or chart reads when the question names none."""
    return _ordered_datasets(MEASURE_DATASETS.values())


# Reading the question.


def _has_time(slots: dict[str, Any]) -> bool:
    status = (slots.get("time_resolution") or {}).get("status")
    return bool(slots.get("year") or slots.get("years") or slots.get("start_date")) and status not in {"ambiguous"}


def _has_place(slots: dict[str, Any]) -> bool:
    return bool(slots.get("county") or slots.get("counties") or slots.get("coords") or slots.get("utilities"))


def _statewide(text: str, slots: dict[str, Any]) -> bool:
    """A statewide surface or grid map has no place to ask for."""
    return bool(slots.get("map_mode")) or bool(
        re.search(r"\b(?:surface|statewide|grid map|all of california)\b", text, re.I)
    )


def _reads_events(text: str, datasets: list[str]) -> bool:
    """The question reads event data: a named event dataset or a generic event word."""
    if set(datasets) & _EVENT_DATASETS:
        return True
    return not datasets and bool(
        re.search(r"\b(?:fires?|wildfires?|incidents?|events?|outages?|shutoffs?)\b", text, re.I)
    )


def _asks_risk(text: str) -> bool:
    from services.agent.routing import _wants_risk

    return bool(_wants_risk(text.lower()))


def _named_datasets(text: str) -> list[str]:
    from services.agent.routing import _datasets

    return _datasets(text)


# Chart words: a chart always reads event data, even when it names no dataset.
_CHART = re.compile(r"\b(?:charts?|trends?|timelines?|seasonal|monthly|yearly|cumulative|series)\b", re.I)


def task_of(text: str, slots: dict[str, Any]) -> str:
    """What the question asks for: risk, rank, compare, data, or other.

    Read from the question and the slots only, never from the rule, so every
    rule's text asks for the same items on the same question.
    """
    from services.agent.routing import _asks_ranking

    lower = text.lower()
    if _asks_risk(text):
        return "risk"
    if _asks_ranking(lower):
        return "rank"
    if len(slots.get("utilities") or []) >= 2 or len(slots.get("counties") or []) >= 2:
        return "compare"
    datasets = _named_datasets(text)
    if _reads_events(text, datasets) or (not datasets and _CHART.search(lower)):
        return "data"
    # A place, tier, or inventory question: nothing beyond the rule's own item.
    return "other"


def task_group(text: str, slots: dict[str, Any]) -> str | None:
    """The grouping a ranking or comparison orders, or None when the question names none."""
    from services.agent.routing import _rank_dimension

    if len(slots.get("utilities") or []) >= 2:
        return "utility"
    if len(slots.get("counties") or []) >= 2:
        return "county"
    if slots.get("rank_group") in _GROUP_PLURALS:
        return slots["rank_group"]
    # The router's reading; when it sees two groupings, a utility word that
    # only qualifies the records does not count.
    lower = text.lower()
    group = _rank_dimension(lower) or _rank_dimension(_UTILITY_QUALIFIER.sub(" ", lower))
    return group if group in _GROUP_PLURALS else None


def task_datasets(text: str, task: str) -> list[str]:
    """The datasets the question names."""
    named = _named_datasets(text)
    if task == "rank":
        # "circuit" is the grouping and a tier is a constraint, not a table.
        named = [item for item in named if item not in {"circuits", "hftd"}]
    if not named and "acre" in text.lower():
        # Acres are CAL FIRE only.
        return ["calfire_incidents"]
    return named


def task_dataset(text: str, task: str) -> str | None:
    """The one dataset the question names, or None."""
    named = task_datasets(text, task)
    return named[0] if len(named) == 1 else None


def missing_items(rule: str, text: str, slots: dict[str, Any]) -> list[str]:
    """Every item the question is missing, the rule's own item first.

    Only the rule's own item depends on the rule; every other item is read from
    the question and the router's slots.
    """
    if rule not in RULE_ITEM:
        return []
    own = RULE_ITEM[rule]
    task = task_of(text, slots)
    missing: list[str] = [own] if own else []
    needs = {
        "risk": ["place", "date"],
        "rank": ["year", "dataset", "grouping"],
        "compare": ["year", "dataset"],
        "data": ["year", "dataset"],
    }.get(task, [])
    for item in needs:
        if item in {"year", "date"}:
            if not _has_time(slots):
                missing.append(item)
        elif item == "dataset":
            # Any named dataset counts; two named datasets are the router's call.
            if not task_datasets(text, task):
                missing.append("dataset")
        elif item == "grouping":
            if task_group(text, slots) is None:
                missing.append("grouping")
        elif item == "place":
            if not _has_place(slots) and not _statewide(text, slots):
                missing.append("place")
    return list(dict.fromkeys(missing))


def ask_phrase(item: str, rule: str, text: str, slots: dict[str, Any]) -> str:
    """How to ask for one item, with the registry's options for the task."""
    if item == "year":
        return "a year or date range"
    if item == "date":
        return "one past calendar day through 2025-12-31"
    if item == "place":
        return "a place (a county, a utility territory, or latitude/longitude)"
    if item == "measure":
        return "a measure"
    task = task_of(text, slots)
    dataset = task_dataset(text, task)
    if item == "grouping":
        return f"a grouping ({_join([_GROUP_PLURALS[g] for g in rank_groups(dataset)])})"
    group = task_group(text, slots)
    if task == "rank":
        if group is None:
            return f"a dataset and grouping ({rank_options()})"
        options = rank_datasets(group)
    elif task == "compare":
        options = compare_datasets(group, list(slots.get("utilities") or []))
    elif _series_without_dataset(text):
        options = series_datasets()
    else:
        options = event_datasets()
    return f"a dataset ({_join([_LABELS[key] for key in options])})"


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
    task = task_of(text, slots)
    named = task_dataset(text, task)
    year = str(slots.get("year") or "2023")
    if task == "rank":
        # Only a ranking the tools run: a grouping the dataset has, a dataset the grouping has.
        group = task_group(text, slots)
        groups = rank_groups(named) or rank_groups()
        group = group if group in groups else groups[0]
        dataset = named if named in rank_datasets(group) else rank_datasets(group)[0]
        return f"Rank {_GROUP_PLURALS[group]} by {_LABELS[dataset]} for {year}."
    first = (task_datasets(text, task) or [""])[0]
    dataset = _LABELS.get(first, "CPUC ignitions")
    place = resolved_place(text, slots) or PLACE_PLACEHOLDER
    needs_place = "place" in missing or rule in _PLACE_RULES or slots.get("county") or slots.get("utilities")
    where = f" in {place}" if needs_place else ""
    if task == "risk" or "date" in missing:
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
    return f"How many {dataset} were there{where} in {year}?"


def complete_clarification(rule: str, text: str, slots: dict[str, Any], answer: str | None) -> str | None:
    """Return the clarification text asking for every missing item.

    Whichever rule's text is the base, every item missing_items reads from the
    question and slots is asked: the base text's own item, the items its words
    already ask for, and the rest appended with the registry's options.
    """
    if not answer:
        return answer
    missing = missing_items(rule, text, slots)
    own = RULE_ITEM.get(rule)
    to_ask = [item for item in missing if not ASKED[item].search(answer)]
    if not to_ask and (own == "measure" or not [item for item in missing if item != own]):
        # Nothing to add. A measure question lists its options instead of an example.
        return answer
    if "dataset" in to_ask and "grouping" in to_ask and task_of(text, slots) == "rank":
        # One phrase lists every dataset with its groupings.
        to_ask.remove("grouping")
    others = [ask_phrase(item, rule, text, slots) for item in to_ask]
    example = _example(rule, text, slots, missing)
    if not others:
        return f"{answer.rstrip()} For example: \"{example}\""
    also = others[0] if len(others) == 1 else ", ".join(others[:-1]) + f", and {others[-1]}"
    return f"{answer.rstrip()} I also need {also}. For example: \"{example}\""
