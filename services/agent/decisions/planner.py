"""Build an ordered tool plan from Jev facts and router slots."""

from __future__ import annotations

from typing import Any

MAX_PLAN_CALLS = 6

_VIZ = {
    "cpuc_ignitions": "ignitions",
    "epss_outages": "epss",
    "psps_events": "psps",
    "calfire_incidents": "calfire",
    "us_ignitions": "us_ignitions",
    "hftd": "hftd",
}
_COUNTY_DATASETS = {
    "calfire_incidents",
    "cpuc_ignitions",
    "epss_outages",
    "psps_events",
}


def _entity_scopes(
    utilities: list[str],
    counties: list[str],
    dataset: str | None,
) -> list[dict[str, Any]] | None:
    if len(utilities) > 1 and len(counties) > 1:
        return None
    if len(counties) > 1:
        if dataset not in _COUNTY_DATASETS:
            return None
        return [{"county": name} for name in counties]
    if len(utilities) > 1:
        return [{"utility": name} for name in utilities]
    extra: dict[str, Any] = {}
    if len(utilities) == 1:
        extra["utility"] = utilities[0]
    if len(counties) == 1 and dataset in _COUNTY_DATASETS:
        extra["county"] = counties[0]
    return [extra]


def _plan_covers(
    calls: list[tuple[str, dict[str, Any]]],
    *,
    years: list[int],
    utilities: list[str],
    counties: list[str],
    wants_count: bool,
    wants_list: bool,
    wants_series: bool,
    wants_map: bool,
    wants_rank: bool,
    wants_compare: bool,
) -> bool:
    def counts():
        return [
            args
            for name, args in calls
            if name == "data_query_records" and args.get("result_mode") != "records"
        ]

    has_count = bool(counts()) or any(name == "comparison_run" for name, _ in calls)
    has_count = has_count or any(name == "data_query_rank" for name, _ in calls)
    has_list = any(
        name == "data_query_records" and args.get("result_mode") == "records"
        for name, args in calls
    )
    has_series = any(
        name == "visualization_create" and args.get("kind") == "time_series"
        for name, args in calls
    )
    has_map = any(
        name == "visualization_create" and args.get("kind") == "map"
        for name, args in calls
    )
    has_rank = any(name == "data_query_rank" for name, _ in calls)
    has_compare = any(name == "comparison_run" for name, _ in calls) or len(counts()) >= 2
    if wants_count and not has_count:
        return False
    if wants_list and not has_list:
        return False
    if wants_series and not has_series:
        return False
    if wants_map and not has_map:
        return False
    if wants_rank and not has_rank:
        return False
    if wants_compare and not has_compare:
        return False
    if len(utilities) > 1:
        covered = set()
        for _, args in calls:
            if args.get("utility"):
                covered.add(args["utility"])
            covered.update(args.get("utilities") or [])
        if not set(utilities) <= covered:
            return False
    if len(counties) > 1:
        covered = {args.get("county") for _, args in calls}
        if not has_rank and not set(counties) <= covered:
            return False
    if len(years) > 1:
        covered_years = set()
        for _, args in calls:
            if args.get("year") is not None:
                covered_years.add(int(args["year"]))
            for key in ("period_a_start", "period_b_start"):
                if args.get(key):
                    covered_years.add(int(str(args[key])[:4]))
        if not set(years) <= covered_years:
            return False
    return True


# Facts each plan type gates on. Entity counts come from router slots.
PLAN_DEPENDENCIES = {
    "per_year_counts": ("intent", "dataset", "breakdown", "also_chart"),
    "per_utility_counts": ("intent", "dataset", "breakdown", "also_chart"),
    "per_county_counts": ("intent", "dataset", "breakdown", "also_chart"),
    "monthly_series": ("intent", "dataset", "breakdown"),
    "weekly_series": ("intent", "dataset", "breakdown"),
    "rank": ("intent", "dataset", "breakdown", "rank_dimension"),
    "count_plus_chart": ("intent", "dataset", "breakdown", "also_chart"),
    "list": ("intent", "dataset", "breakdown"),
    "comparison": ("intent", "dataset", "breakdown", "also_chart"),
    "single_count": ("intent", "dataset", "breakdown", "also_chart"),
    "map": ("intent", "dataset", "breakdown"),
}
_INTENT_FORM = {
    "count": "single_number",
    "records_list": "record_list",
    "map": "map",
    "trend": "time_series",
    "map_plus_trend": "map_plus_trend",
    "compare": "comparison",
    "rank": "ranking",
}
_AMBIGUOUS_INTENTS = {"multi_intent", "other", "exploratory_overview"}
_RANK_GROUP = {"county": "county", "utility": "utility", "circuit": "circuit"}


def plan_calls(
    facts: Any,
    slots: dict[str, Any],
    *,
    min_confidence: float = 0.8,
) -> tuple[list[tuple[str, dict[str, Any]]] | None, str]:
    """Return calls, or (None, reason) when the whole question must fall back."""
    year = slots.get("year")
    years = [int(item) for item in (slots.get("years") or [])]
    if year is not None and int(year) not in years:
        years = [int(year), *years]
    utilities = list(slots.get("utilities") or [])
    counties = [str(item) for item in (slots.get("counties") or [])]
    if not counties and slots.get("county"):
        counties = [str(slots["county"])]

    # Several named years are one count per year for both count and trend, and for
    # breakdown none and by_year. Those readings are the same plan, so they are
    # not gated against each other.
    per_year_shape = len(years) > 1 and len(utilities) <= 1 and len(counties) <= 1
    intent_value = getattr(facts, "intent", None)
    breakdown_value = getattr(facts, "breakdown", None) or "none"
    if per_year_shape and intent_value in {"count", "trend"}:
        intent, intent_reason = intent_value, None
    else:
        intent, intent_reason = _gated_choice(facts, "intent", min_confidence)
    if intent_reason:
        return None, intent_reason
    if not intent:
        return None, "cannot_express"
    if per_year_shape and breakdown_value in {"none", "by_year"}:
        breakdown, breakdown_reason = breakdown_value, None
    else:
        breakdown, breakdown_reason = _gated_choice(facts, "breakdown", min_confidence)
    if breakdown_reason:
        return None, breakdown_reason
    breakdown = breakdown or "none"
    dataset, dataset_reason = _resolve_dataset(facts, slots, min_confidence)
    if dataset_reason:
        return None, dataset_reason

    form = _INTENT_FORM.get(intent)
    if intent in _AMBIGUOUS_INTENTS:
        form, form_reason = _gated_choice(facts, "output_form", min_confidence)
        if form_reason:
            return None, form_reason
        if not form:
            return None, "cannot_express"
    if form is None:
        return None, "cannot_express"
    if per_year_shape and form in {"single_number", "time_series"} and breakdown in {"none", "by_year"}:
        # A yearly chart is the per-year counts. An extra chart does not add a call.
        form = "single_number"
        chart, chart_reason = False, None
    else:
        chart, chart_reason = _extra_chart(facts, form, min_confidence)
    if chart_reason:
        return None, chart_reason

    viz = _VIZ.get(dataset or "", dataset)
    calls: list[tuple[str, dict[str, Any]]] = []

    def one_scope() -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if len(utilities) == 1:
            extra["utility"] = utilities[0]
        if len(counties) == 1 and dataset in _COUNTY_DATASETS:
            extra["county"] = counties[0]
        return extra

    def count_call(item_year: int, *, mode: str = "count", **extra: Any):
        args: dict[str, Any] = {
            "dataset": dataset,
            "result_mode": mode,
            "year": int(item_year),
            **extra,
        }
        return ("data_query_records", args)

    def series_call(item_year: int, interval: str):
        return (
            "visualization_create",
            {
                "kind": "time_series",
                "dataset": viz,
                "interval": interval,
                "year": int(item_year),
                **one_scope(),
            },
        )

    interval = "weekly" if breakdown == "by_week" else "monthly"
    want_count = form == "single_number"
    want_series = form == "time_series" or breakdown in {"by_month", "by_week"}
    want_list = form == "record_list"
    want_map = form in {"map", "map_plus_trend"}
    want_rank = form == "ranking"
    want_compare = form == "comparison"
    if form == "map_plus_trend":
        want_series = True

    if not dataset and form not in {"comparison"}:
        return None, "missing_slot"

    if want_series and breakdown in {"by_month", "by_week"} and len(years) <= 1:
        if year is None:
            return None, "missing_slot"
        if len(utilities) > 1 or len(counties) > 1:
            return None, "cannot_express"
        if want_count or intent == "count":
            calls.append(count_call(int(year), **one_scope()))
        calls.append(series_call(int(year), interval))
    elif len(years) == 2 and want_compare and len(utilities) <= 1 and len(counties) <= 1 and breakdown != "by_year":
        if len(utilities) != 1:
            return None, "missing_slot"
        calls.append(
            (
                "comparison_run",
                {
                    "kind": "periods",
                    "scope_type": "utility",
                    "scope": utilities[0],
                    "metric": "ignition_count",
                    "period_a_start": f"{years[0]}-01-01",
                    "period_a_end": f"{years[0]}-12-31",
                    "period_b_start": f"{years[1]}-01-01",
                    "period_b_end": f"{years[1]}-12-31",
                },
            )
        )
    elif len(years) > 1 and (want_count or want_series or breakdown == "by_year" or (want_compare and len(utilities) <= 1)):
        scopes = _entity_scopes(utilities, counties, dataset)
        if scopes is None:
            return None, "cannot_express"
        if len(years) * len(scopes) > MAX_PLAN_CALLS:
            return None, "over_limit"
        for item in years:
            for extra in scopes:
                calls.append(count_call(item, **extra))
    elif len(utilities) > 1 and want_compare and year is not None:
        calls.append(
            (
                "comparison_run",
                {
                    "kind": "utilities",
                    "utilities": utilities,
                    "metric": "ignition_count",
                    "start_date": f"{int(year)}-01-01",
                    "end_date": f"{int(year)}-12-31",
                },
            )
        )
    elif len(utilities) > 1 and want_count:
        if year is None:
            return None, "missing_slot"
        if len(utilities) > MAX_PLAN_CALLS:
            return None, "over_limit"
        for utility in utilities:
            calls.append(count_call(int(year), utility=utility))
    elif len(counties) > 1 and want_count:
        if year is None:
            return None, "missing_slot"
        scopes = _entity_scopes([], counties, dataset)
        if scopes is None:
            return None, "cannot_express"
        if len(scopes) > MAX_PLAN_CALLS:
            return None, "over_limit"
        for extra in scopes:
            calls.append(count_call(int(year), **extra))
    elif want_rank:
        dimension = getattr(facts, "rank_dimension", None)
        if dimension in (None, "", "none"):
            dimension = breakdown.removeprefix("by_") if str(breakdown).startswith("by_") else None
        group = _RANK_GROUP.get(dimension or "")
        rank_conf = _choice_confidence(facts, "rank_dimension_confidence")
        if getattr(facts, "rank_dimension", None) not in (None, "none", "") and rank_conf < min_confidence:
            return None, "gate"
        if group not in _RANK_GROUP.values():
            return None, "cannot_express"
        if year is None:
            return None, "missing_slot"
        calls.append(
            (
                "data_query_rank",
                {
                    "dataset": dataset,
                    "group_by": group,
                    "metric": "count",
                    "year": int(year),
                },
            )
        )
    elif want_list:
        if year is None or len(utilities) > 1 or len(counties) > 1:
            return None, "missing_slot" if year is None else "cannot_express"
        calls.append(count_call(int(year), mode="records", **one_scope()))
    elif want_map and not want_series:
        calls.append(("visualization_create", {"kind": "map", "dataset": viz, **one_scope()}))
    elif want_count:
        if year is None:
            return None, "missing_slot"
        calls.append(count_call(int(year), **one_scope()))
    elif want_series:
        if year is None or len(utilities) > 1 or len(counties) > 1:
            return None, "missing_slot" if year is None else "cannot_express"
        calls.append(series_call(int(year), interval))
    else:
        return None, "cannot_express"

    if chart and not any(name == "visualization_create" and args.get("kind") == "time_series" for name, args in calls):
        if year is None or len(utilities) > 1 or len(counties) > 1 or len(years) > 1:
            return None, "cannot_express"
        calls.append(series_call(int(year), interval))
    if want_map and want_series and not any(args.get("kind") == "map" for _, args in calls):
        calls.append(("visualization_create", {"kind": "map", "dataset": viz, **one_scope()}))

    if len(calls) > MAX_PLAN_CALLS:
        return None, "over_limit"
    if not calls:
        return None, "cannot_express"
    if not _plan_covers(
        calls,
        years=years,
        utilities=utilities,
        counties=counties,
        wants_count=want_count or bool(any(name == "data_query_records" and args.get("result_mode") != "records" for name, args in calls)),
        wants_list=want_list,
        wants_series=want_series or bool(chart),
        wants_map=want_map,
        wants_rank=want_rank,
        wants_compare=want_compare and len(years) == 2 and len(utilities) <= 1,
    ):
        return None, "partial_plan"
    return calls, "planned"

def _gated_choice(facts: Any, name: str, gate: float) -> tuple[Any, str | None]:
    """Choice confidence is the probability of the selected option."""
    value = getattr(facts, name, None)
    confidence = getattr(facts, f"{name}_confidence", None)
    if confidence is None:
        confidence = 1.0
    if float(confidence) < gate:
        return None, "gate"
    return value, None


def _resolve_dataset(facts: Any, slots: dict[str, Any], gate: float):
    """Use a confident Jev dataset. Otherwise keep the router slot instead of gating."""
    chosen = getattr(facts, "dataset", None)
    confidence = getattr(facts, "dataset_confidence", None)
    slot = slots.get("dataset")
    confident = confidence is None or float(confidence) >= gate
    if chosen == "multiple" and confident:
        return None, "cannot_express"
    if chosen not in (None, "", "none", "multiple") and confident:
        return chosen, None
    if slot:
        return slot, None
    if chosen not in (None, "", "none", "multiple"):
        return None, "gate"
    return None, "missing_slot"


def _extra_chart(facts: Any, form: str, gate: float) -> tuple[bool | None, str | None]:
    """Only count-like and comparison plans depend on the extra-chart Noul."""
    if form in {"time_series", "map", "map_plus_trend", "ranking", "record_list"}:
        return False, None
    decision = _noul_yes(getattr(facts, "also_chart", 0.0) or 0.0, gate)
    if decision is None:
        return None, "gate"
    return decision, None


def load_plan_facts(question: str, settings: Any) -> Any | None:
    """Read planning facts. Tool arguments stay in code."""
    from datetime import date

    from services.agent.decisions.jev_policy import facts_from_answers
    from services.agent.decisions.typesafe_backend import TypeSafeBackend
    from services.agent.decisions.v3 import calls_for_config

    calls = calls_for_config(
        question,
        date.today().isoformat(),
        None,
        getattr(settings, "jev_ablation", "v3_hybrid"),
    )
    backend = TypeSafeBackend(
        model=getattr(settings, "jev_model", "jev-latest"),
        timeout_seconds=float(getattr(settings, "jev_timeout_seconds", 8.0)),
    )
    answers: dict[str, Any] = {}
    for name in ("facts", "topic"):
        call = next(item for item in calls if item["name"] == name)
        result = backend.evaluate(
            call["state"],
            call["questions"],
            request_id="plan",
            question_hash="plan",
        )
        if result is None:
            return None
        answers.update(result.answers)
    return facts_from_answers(answers)


def _noul_yes(probability: float, gate: float) -> bool | None:
    """Confidence is in the chosen side. 0.08 is a confident no, not a weak yes."""
    p = min(1.0, max(0.0, float(probability)))
    confidence = max(p, 1.0 - p)
    if confidence < gate:
        return None
    return p >= 0.5


def _choice_confidence(facts: Any, name: str) -> float:
    value = getattr(facts, name, None)
    if value is None:
        return 1.0
    return float(value)
