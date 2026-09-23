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
    if breakdown != "none" and _low(facts, "breakdown_confidence", min_confidence):
        return None, "low_confidence"

    def confident(name: str) -> bool:
        return float(getattr(facts, name, 0) or 0) >= min_confidence

    wants_count = confident("wants_count")
    wants_series = confident("wants_time_series")
    wants_rank = confident("wants_ranking")
    wants_compare = confident("wants_comparison")
    viz = _VIZ.get(dataset or "", dataset)
    calls: list[tuple[str, dict[str, Any]]] = []

    def one_utility() -> dict[str, Any]:
        if len(utilities) == 1:
            return {"utility": utilities[0]}
        return {}

    if breakdown == "by_month":
        if not dataset or year is None or len(utilities) > 1:
            return None, "missing_slot"
        calls.append(
            (
                "visualization_create",
                {
                    "kind": "time_series",
                    "dataset": viz,
                    "interval": "monthly",
                    "year": int(year),
                    **one_utility(),
                },
            )
        )
    elif breakdown == "by_county" and wants_rank:
        if dataset not in {"cpuc_ignitions", "calfire_incidents"} or year is None:
            return None, "cannot_express"
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
    elif len(years) > 1 and wants_count and len(utilities) <= 1:
        if not dataset:
            return None, "missing_slot"
        if len(years) == 2 and wants_compare and len(utilities) == 1:
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
        else:
            for item in years:
                args: dict[str, Any] = {
                    "dataset": dataset,
                    "result_mode": "count",
                    "year": item,
                    **one_utility(),
                }
                calls.append(("data_query_records", args))
    elif len(utilities) > 1 and wants_count:
        if not dataset or year is None:
            return None, "missing_slot"
        if wants_compare:
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
        else:
            for utility in utilities:
                calls.append(
                    (
                        "data_query_records",
                        {
                            "dataset": dataset,
                            "result_mode": "count",
                            "year": int(year),
                            "utility": utility,
                        },
                    )
                )
    elif wants_count and wants_series:
        if not dataset or year is None or len(utilities) > 1:
            return None, "missing_slot"
        calls.append(
            (
                "data_query_records",
                {
                    "dataset": dataset,
                    "result_mode": "count",
                    "year": int(year),
                    **one_utility(),
                },
            )
        )
        calls.append(
            (
                "visualization_create",
                {
                    "kind": "time_series",
                    "dataset": viz,
                    "interval": "monthly",
                    "year": int(year),
                    **one_utility(),
                },
            )
        )
    else:
        return None, "cannot_express"

    if len(calls) > MAX_PLAN_CALLS:
        return None, "over_limit"
    if not calls:
        return None, "cannot_express"
    return calls, "planned"


def load_plan_facts(question: str, settings: Any) -> Any | None:
    """Read planning facts. Tool arguments stay in code."""
    from datetime import date

    from services.agent.decisions.jev_policy import facts_from_answers
    from services.agent.decisions.typesafe_backend import TypeSafeBackend
    from services.agent.decisions.v3 import calls_for_config

    calls = calls_for_config(
        getattr(settings, "jev_ablation", "v3_hybrid"),
        question,
        date.today().isoformat(),
        None,
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


def _low(facts: Any, name: str, gate: float) -> bool:
    value = getattr(facts, name, None)
    if value is None:
        return False
    return float(value) < gate
