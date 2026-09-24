"""Measured dataset coverage applied to one tool call.

Every path that plans or runs a call (router, Jev templates, slot planner,
executor) asks this module whether the call's dataset has rows for the named
utilities in the call's period. The coverage itself is measured by the loaders
and read through ``services.shared.dataset_registry``; nothing here declares it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from services.shared.dataset_registry import (
    COMPARISON_METRIC_DATASETS,
    DATASET_COVERAGE,
    covered_utilities,
    dataset_coverage_gap,
    to_canonical,
)

# The clarification rule for an uncovered read. A utility the dataset has no
# rows for is label rule I on EPSS and label rule J on the US sample; any other
# gap (a utility or a period outside measured coverage) is dataset_not_covered.
NOT_COVERED_RULES = {
    "epss_outages": "epss_non_pge_utility",
    "us_ignitions": "us_sample_utility_filter",
}
NOT_COVERED_RULE = "dataset_not_covered"

_READ_TOOLS = {
    "data_query_records",
    "data_query_rank",
    "data_query_spatial",
    "visualization_create",
}


def not_covered_rule(gap: dict[str, Any]) -> str:
    """Rule I or J when a named utility has no rows in the dataset at all."""
    dataset = gap["dataset"]
    known = set(covered_utilities(dataset))
    if dataset in NOT_COVERED_RULES and any(u not in known for u in gap.get("utilities") or []):
        return NOT_COVERED_RULES[dataset]
    return NOT_COVERED_RULE


def named_utilities(tool: str, arguments: dict[str, Any]) -> list[str]:
    """The utilities a call is scoped to, whichever argument names them."""
    if tool == "comparison_run":
        kind = arguments.get("kind")
        if kind == "utilities":
            return [str(item) for item in arguments.get("utilities") or []]
        if kind == "periods" and arguments.get("scope_type") == "utility":
            return [str(arguments["scope"])] if arguments.get("scope") else []
        return []
    utility = arguments.get("utility")
    return [str(utility)] if utility else []


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def call_periods(tool: str, arguments: dict[str, Any]) -> list[tuple[str | None, str | None]]:
    """The date windows a call counts over; one (None, None) window for all dates."""
    if tool == "comparison_run" and arguments.get("kind") == "periods":
        return [
            (_iso(arguments.get("period_a_start")), _iso(arguments.get("period_a_end"))),
            (_iso(arguments.get("period_b_start")), _iso(arguments.get("period_b_end"))),
        ]
    start, end = _iso(arguments.get("start_date")), _iso(arguments.get("end_date"))
    year = arguments.get("year")
    if year is not None and start is None and end is None:
        start, end = f"{int(year)}-01-01", f"{int(year)}-12-31"
    return [(start, end)]


def call_dataset(tool: str, arguments: dict[str, Any]) -> str | None:
    """The dataset whose rows a call counts, if it has measured coverage."""
    if tool == "comparison_run":
        # Validated arguments carry a known metric; an unmapped one is a
        # registry gap and must fail here rather than skip the check.
        return COMPARISON_METRIC_DATASETS[str(arguments.get("metric"))]
    if tool in _READ_TOOLS:
        dataset = arguments.get("dataset")
        return str(dataset) if dataset else None
    return None


def call_coverage_gap(tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """The coverage gap a call would hit, or None when some part of it is covered.

    A comparison with one covered side (a utility or one of two periods) runs;
    the service returns the other side null with its reason. Only a call with
    nothing covered is a gap.
    """
    dataset = call_dataset(tool, arguments)
    if not dataset:
        return None
    utilities = named_utilities(tool, arguments)
    return dataset_coverage_gap(dataset, utilities, periods=call_periods(tool, arguments))


def dataset_known(dataset: str) -> bool:
    """True when the name is a registry dataset (alias or key)."""
    try:
        to_canonical(dataset)
    except KeyError:
        return False
    return True


def count_coverage_gap(
    key: str, tool: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """The gap for one count key of a result (a spatial summary counts several datasets)."""
    try:
        dataset = to_canonical(key)
    except KeyError as exc:
        # A count that cannot be traced to a dataset is not rendered.
        raise ValueError(f"coverage: unknown count key {key!r}") from exc
    if dataset not in DATASET_COVERAGE:
        return None
    utilities = named_utilities(tool, arguments)
    (start, end), *_ = call_periods(tool, arguments)
    return dataset_coverage_gap(dataset, utilities, start, end)
