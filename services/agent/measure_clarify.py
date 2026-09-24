"""Clarification for a ranking or comparison whose measure names none in the data.

Jev judges whether a ranking or comparison names a real measure (its
other_measure answer, jev_policy.derive_outcome). This module only writes the
question back: it keeps the grouping and period the router already resolved and
lists the registry's measures for that grouping (RANK_MEASURES,
COMPARE_MEASURES), so the offered measures cannot drift from the tools.
"""

from __future__ import annotations

from typing import Any

from services.shared.dataset_registry import (
    COMPARE_MEASURES,
    MEASURE_DATASETS,
    MEASURE_LABELS,
    MEASURE_UTILITIES,
    MEASURES_NOT_IN_DATA,
    RANK_MEASURES,
    UTILITY_CLARIFY_LABELS,
)

_GROUP_PLURALS = {"utility": "utilities", "county": "counties", "circuit": "circuits"}


def _time_phrase(time_resolution: dict[str, Any]) -> str | None:
    """The resolved period in words, or None when there is none."""
    if time_resolution.get("status") not in {"explicit", "relative_year", "relative_range"}:
        return None
    years = list(time_resolution.get("years") or [])
    start, end = time_resolution.get("start_date"), time_resolution.get("end_date")
    whole_years = bool(start and end and start[5:] == "01-01" and end[5:] == "12-31")
    if len(years) == 1 and (whole_years or not start):
        return str(years[0])
    if len(years) >= 2 and time_resolution.get("per_year"):
        return ", ".join(str(item) for item in years[:-1]) + f" and {years[-1]}"
    if whole_years:
        return f"{start[:4]} through {end[:4]}"
    if start and end:
        return f"{start} to {end}"
    return None


def _join(items: list[str], word: str) -> str:
    if len(items) <= 2:
        return f" {word} ".join(items)
    return ", ".join(items[:-1]) + f", {word} {items[-1]}"


def _labels(measures, dataset: str | None) -> list[str]:
    """Registry labels, narrowed to the named dataset when it has any of them."""
    named = [item for item in measures if MEASURE_DATASETS.get(item) == dataset]
    return [MEASURE_LABELS[item] for item in (named or measures)]


def measure_group(slots: dict[str, Any], rank_dimension: str | None, text: str) -> str | None:
    """The grouping being ranked or compared.

    Two or more named utilities or counties decide it; otherwise Jev's
    rank_dimension, then the router's reading of the question.
    """
    from services.agent.routing import _rank_dimension

    if len(slots.get("utilities") or []) >= 2:
        return "utility"
    if len(slots.get("counties") or []) >= 2:
        return "county"
    if rank_dimension in _GROUP_PLURALS:
        return rank_dimension
    return _rank_dimension(text.lower())


def measure_clarification(
    kind: str, slots: dict[str, Any], *, text: str = "", rank_dimension: str | None = None
) -> str:
    """Ask which measure, keeping the grouping and period, listing the registry's measures.

    kind: "rank" or "compare" (Jev's intent).
    """
    utilities = list(slots.get("utilities") or [])
    counties = list(slots.get("counties") or [])
    dataset = slots.get("dataset")
    group = measure_group(slots, rank_dimension, text)
    period = _time_phrase(slots.get("time_resolution") or {})
    when = f" for {period}" if period else ""
    plural = _GROUP_PLURALS.get(group or "")
    parts = ["That question does not name a measure in the data."]
    if kind == "rank" and group in RANK_MEASURES:
        ranked = [item for item in RANK_MEASURES[group] if MEASURE_DATASETS[item] == dataset]
        ranked = ranked or list(RANK_MEASURES[group])
        parts.append(
            f"To rank {plural}{when}, I can use {_join([MEASURE_LABELS[item] for item in ranked], 'or')}."
        )
        extra = [item for item in COMPARE_MEASURES.get(group, ()) if item not in ranked]
        if extra:
            parts.append(
                f"To compare named {plural}, I can also use {_join(_labels(extra, dataset), 'or')}."
            )
    elif kind == "compare" and group in COMPARE_MEASURES:
        if len(utilities) >= 2:
            subject = _join([UTILITY_CLARIFY_LABELS.get(item, item) for item in utilities], "and")
        elif len(counties) >= 2:
            subject = _join(counties, "and")
        else:
            subject = plural
        # EPSS is PG&E only: offered only when PG&E is one of the named utilities.
        measures = [
            item
            for item in COMPARE_MEASURES[group]
            if len(utilities) < 2 or set(utilities) & MEASURE_UTILITIES.get(item, set(utilities))
        ]
        parts.append(f"To compare {subject}{when}, I can use {_join(_labels(measures, dataset), 'or')}.")
    else:
        # No grouping resolved: every grouping the tool takes, with its measures.
        verb, by_scope = ("rank", RANK_MEASURES) if kind == "rank" else ("compare", COMPARE_MEASURES)
        by_group = [
            f"{_GROUP_PLURALS[group_by]} by {_join([MEASURE_LABELS[item] for item in measures], 'or')}"
            for group_by, measures in by_scope.items()
            if group_by in _GROUP_PLURALS
        ]
        prefix = f"For {period}, I can" if period else "I can"
        parts.append(f"{prefix} {verb} {'; '.join(by_group)}.")
    not_in_data = _join(list(MEASURES_NOT_IN_DATA), "and")
    parts.append(f"{not_in_data[:1].upper()}{not_in_data[1:]} are not in the data.")
    parts.append(
        "Which measure should I use?"
        if period
        else "Which measure should I use, and for which year or date range?"
    )
    return " ".join(parts)
