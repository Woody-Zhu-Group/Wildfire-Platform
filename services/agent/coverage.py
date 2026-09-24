"""Measured dataset coverage applied to one tool call.

Every path that plans or runs a call (router, Jev templates, slot planner,
executor) asks this module whether the call's dataset has rows for the named
utilities in the call's period. The coverage itself is measured by the loaders
and read through ``services.shared.dataset_registry``; nothing here declares it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from services.agent.grounding import question_incident_type_modes
from services.agent.schemas import TOOL_MODELS
from services.shared.dataset_registry import (
    COMPARISON_METRIC_DATASETS,
    DATASET_COVERAGE,
    DATASETS,
    call_definition,
    covered_utilities,
    dataset_coverage_gap,
    default_definition,
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


def unmeasured_filters(tool: str, arguments: dict[str, Any]) -> list[str]:
    """The call's filters that measured coverage does not break down by.

    Coverage is measured per dataset, utility, and year only. An offer made
    for a call that also names a county, tier, circuit, place, or area is an
    offer for the utility and period without that filter, and must say so.
    """
    names: list[str] = []
    county = arguments.get("county")
    if county:
        text = str(county).strip()
        names.append(text if text.lower().endswith(" county") else f"{text} County")
    for key in ("tier", "hftd_tier"):
        tier = arguments.get(key)
        if tier:
            value = getattr(tier, "value", tier)
            names.append(f"HFTD {value}")
    if arguments.get("circuit_id"):
        names.append(f"circuit {arguments['circuit_id']}")
    if arguments.get("bbox"):
        names.append("map area")
    if arguments.get("lat") is not None and arguments.get("lon") is not None:
        names.append("location")
    if arguments.get("min_acres") is not None:
        names.append(f"minimum {arguments['min_acres']} acres")
    if tool == "comparison_run" and arguments.get("kind") == "regions":
        names.extend(str(item) for item in arguments.get("regions") or [])
    return list(dict.fromkeys(names))


def _with_filters(
    gap: dict[str, Any] | None, tool: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    if gap is not None:
        dropped = unmeasured_filters(tool, arguments)
        if dropped:
            gap["unmeasured_filters"] = dropped
    return gap


def call_coverage_gap(tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """The coverage gap a call would hit, or None when some part of it is covered.

    A comparison with one covered side (a utility or one of two periods) runs;
    the service returns the other side null with its reason. Only a call with
    nothing covered is a gap. The gap names the call's filters that coverage
    does not measure, so an offer says it drops them.
    """
    dataset = call_dataset(tool, arguments)
    if not dataset:
        return None
    utilities = named_utilities(tool, arguments)
    # Coverage of the rows this call counts: its dataset's query definition
    # (CAL FIRE incident_type_mode), the default when the call names none.
    gap = dataset_coverage_gap(
        dataset,
        utilities,
        periods=call_periods(tool, arguments),
        definition=call_definition(dataset, arguments),
    )
    return _with_filters(gap, tool, arguments)


def question_definitions(dataset: str, question: str) -> set[str]:
    """The dataset's non-default query definitions the question asks for.

    Only CAL FIRE has more than one (``incident_type_mode``); the wording that
    asks for all or untyped incident types is the registry's
    (``ALL_INCIDENT_TYPES_PATTERN``, ``UNTYPED_INCIDENT_PATTERN``), read
    through the same function model-path grounding uses.
    """
    default = default_definition(dataset)
    if default is None:
        return set()
    spec = DATASETS[to_canonical(dataset)]
    return {
        mode
        for mode in question_incident_type_modes(question)
        if mode != default and mode in spec.query_definitions
    }


def carry_question_definition(tool: str, arguments: dict[str, Any], question: str) -> bool:
    """Give a planned call the query definition its question asks for.

    A count reads, and its coverage is measured on, the rows of one
    definition; a question that asks for a non-default one must get it, not
    the default. Returns False when the call cannot carry it (its tool takes
    no definition argument, as a comparison does, or the question asks for
    two definitions at once): answering would drop an asked filter.
    """
    dataset = call_dataset(tool, arguments)
    if not dataset or not dataset_known(dataset):
        return True
    asked = question_definitions(dataset, question)
    if not asked:
        return True
    argument = DATASETS[to_canonical(dataset)].definition_argument
    if len(asked) > 1 or argument not in TOOL_MODELS[tool].model_fields:
        return False
    arguments[argument] = next(iter(asked))
    return True


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
    gap = dataset_coverage_gap(
        dataset, utilities, start, end, definition=call_definition(dataset, arguments)
    )
    return _with_filters(gap, tool, arguments)
