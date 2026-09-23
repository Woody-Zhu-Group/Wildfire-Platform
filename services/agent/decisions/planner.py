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


def plan_calls(
    facts: Any,
    slots: dict[str, Any],
    *,
    min_confidence: float = 0.8,
) -> tuple[list[tuple[str, dict[str, Any]]] | None, str]:
    """Return calls, or (None, reason) when the whole question must fall back."""
    dataset = slots.get("dataset")
    year = slots.get("year")
    years = [int(item) for item in (slots.get("years") or [])]
    if year is not None and int(year) not in years:
        years = [int(year), *years]
    utilities = list(slots.get("utilities") or [])
    breakdown = getattr(facts, "breakdown", None) or "none"
    breakdown_confidence = _choice_confidence(facts, "breakdown_confidence")
    if breakdown_confidence < min_confidence:
        return None, "gate"

    def confident(name: str) -> bool | None:
        """True or false when the chosen side is confident. None is unresolved."""
        return _noul_yes(getattr(facts, name, 0) or 0, min_confidence)

    unresolved = [
        name
        for name in (
            "wants_count",
            "wants_list",
            "wants_time_series",
            "wants_map",
            "wants_ranking",
            "wants_comparison",
        )
        if confident(name) is None
    ]
    if unresolved:
        return None, "gate"

    wants_count = confident("wants_count") is True
    wants_list = confident("wants_list") is True
    wants_series = confident("wants_time_series") is True
    wants_map = confident("wants_map") is True
    wants_rank = confident("wants_ranking") is True
    wants_compare = confident("wants_comparison") is True
    counties = [str(item) for item in (slots.get("counties") or [])]
    if not counties and slots.get("county"):
        counties = [str(slots["county"])]
    viz = _VIZ.get(dataset or "", dataset)
    calls: list[tuple[str, dict[str, Any]]] = []
    need_series = wants_series or breakdown in {"by_month", "by_week"}
    interval = "weekly" if breakdown == "by_week" else "monthly"
    use_periods = (
        len(years) == 2
        and wants_compare
        and not wants_list
        and not wants_rank
        and not need_series
        and not wants_map
        and len(utilities) <= 1
        and len(counties) <= 1
        and breakdown in {"none", "by_year"}
    )

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

    if not dataset and (wants_count or wants_list or need_series or wants_map or wants_rank):
        return None, "missing_slot"

    if use_periods:
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
    elif len(years) > 1 and (wants_count or breakdown == "by_year"):
        scopes = _entity_scopes(utilities, counties, dataset)
        if scopes is None:
            return None, "cannot_express"
        if len(years) * len(scopes) > MAX_PLAN_CALLS:
            return None, "over_limit"
        for item in years:
            for extra in scopes:
                calls.append(count_call(item, **extra))
    elif len(utilities) > 1 and wants_count and wants_compare and year is not None:
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
    elif (len(utilities) > 1 or len(counties) > 1) and wants_count:
        if year is None:
            return None, "missing_slot"
        scopes = _entity_scopes(utilities, counties, dataset)
        if scopes is None:
            return None, "cannot_express"
        if len(scopes) > MAX_PLAN_CALLS:
            return None, "over_limit"
        for extra in scopes:
            calls.append(count_call(int(year), **extra))
    elif wants_list and len(utilities) <= 1 and len(counties) <= 1:
        if year is None:
            return None, "missing_slot"
        calls.append(count_call(int(year), mode="records", **one_scope()))
    elif wants_count:
        if year is None:
            return None, "missing_slot"
        calls.append(count_call(int(year), **one_scope()))

    if need_series:
        if year is None or len(utilities) > 1 or len(counties) > 1:
            return None, "missing_slot" if year is None else "cannot_express"
        calls.append(
            (
                "visualization_create",
                {
                    "kind": "time_series",
                    "dataset": viz,
                    "interval": interval,
                    "year": int(year),
                    **one_scope(),
                },
            )
        )
    if wants_map:
        if len(utilities) > 1 or len(counties) > 1:
            return None, "cannot_express"
        calls.append(
            (
                "visualization_create",
                {"kind": "map", "dataset": viz, **one_scope()},
            )
        )
    if wants_rank and breakdown == "by_county":
        if dataset not in {"cpuc_ignitions", "calfire_incidents"} or year is None:
            return None, "cannot_express" if year is not None else "missing_slot"
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
    if wants_list and not any(
        name == "data_query_records" and args.get("result_mode") == "records"
        for name, args in calls
    ):
        if year is None or len(utilities) > 1 or len(counties) > 1:
            return None, "cannot_express"
        calls.append(count_call(int(year), mode="records", **one_scope()))

    if len(calls) > MAX_PLAN_CALLS:
        return None, "over_limit"
    if not calls:
        return None, "cannot_express"
    if not _plan_covers(
        calls,
        years=years,
        utilities=utilities,
        counties=counties,
        wants_count=wants_count,
        wants_list=wants_list,
        wants_series=need_series,
        wants_map=wants_map,
        wants_rank=wants_rank and breakdown == "by_county",
        wants_compare=wants_compare,
    ):
        return None, "partial_plan"
    return calls, "planned"


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
    fact_call = next(call for call in calls if call["name"] == "facts")
    backend = TypeSafeBackend(
        model=getattr(settings, "jev_model", "jev-latest"),
        timeout_seconds=float(getattr(settings, "jev_timeout_seconds", 8.0)),
    )
    result = backend.evaluate(
        fact_call["state"],
        fact_call["questions"],
        request_id="plan",
        question_hash="plan",
    )
    if result is None:
        return None
    return facts_from_answers(result.answers)


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
