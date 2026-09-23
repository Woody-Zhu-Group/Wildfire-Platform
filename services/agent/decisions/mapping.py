"""Project the current router and eval cases onto Jev's label space."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from services.agent.decisions.backend import Answer
from services.agent.decisions.schemas import county_option_id
from services.agent.routing import (
    UNSUPPORTED,
    UTILITY_PATTERNS,
    RouteDecision,
    route_question,
)

# Every rule id route_question can emit maps to exactly one coarse intent.
# filtered_records is the count/list rule; regex_labels upgrades it to
# records_list when the compiled tool asks for records rather than a count.
RULE_TO_INTENT: dict[str, str] = {
    "filtered_records": "count",
    "ranked_records": "rank",
    "map": "map",
    "hdw_map": "map",
    "time_series": "trend",
    "series_yearly": "trend",
    "series_seasonal": "trend",
    "series_cumulative_acres": "trend",
    "series_customer_events": "trend",
    "series_regional": "trend",
    "series_timeline": "trend",
    "series_mode_missing_year": "trend",
    "series_mode_missing_dataset": "trend",
    "map_plus_trend": "map_plus_trend",
    "spatial_utility_count": "spatial_context",
    "coordinate_context": "spatial_context",
    "coordinate_risk_chain": "risk",
    "city_point_context": "spatial_context",
    "city_point_risk_chain": "risk",
    "cell_risk": "risk",
    "county_risk": "risk",
    "risk_surface": "risk",
    "utility_risk": "risk",
    "utility_territory": "territory_boundary",
    "circuit_detail": "circuit_detail",
    "period_comparison": "compare",
    "utility_comparison": "compare",
    "hftd_comparison": "compare",
    "open_comparison": "compare",
    "open_ended": "exploratory_overview",
    "forced_eval": "other",
    # Orchestrator rewrite, not a routing.py rule. Excluded from intent scoring.
    "deterministic_router_disabled": None,
    "multi_intent_count_and_trend": "multi_intent",
    "multi_entity_deferred": "multi_intent",
    "multi_intent_territory_and_map": "multi_intent",
    "ambiguous_risk_metric": "risk",
    "ambiguous_risk_place": "risk",
    "risk_missing_place": "risk",
    "risk_future_date": "risk",
    "forecast_missing_date": "risk",
    "missing_location": "spatial_context",
    "undefined_spatial_scope": "spatial_context",
    "city_needs_place": "spatial_context",
    "unknown_county": "spatial_context",
    "county_place_ambiguous": "spatial_context",
    "hftd_constraint_unavailable": "other",
    "undefined_region": "other",
    "ambiguous_relative_time": "other",
    "time_out_of_coverage": "other",
    "map_missing_year": "map",
    "map_plus_trend_missing_year": "map_plus_trend",
    "trend_missing_year": "trend",
    "spatial_missing_year": "spatial_context",
    "records_missing_year": "count",
    "medical_exposure": "count",
    "summary_stats": "count",
    "medical_exposure_missing_year": "count",
    "ranking_missing_slots": "rank",
    "ranking_missing_year": "rank",
    "ranking_county_contradiction": "rank",
    "unexpressed_filter_constraints": "other",
    "unexpressable_county_filter": "count",
    "unsupported_ranking": "rank",
    "unsupported_rank_cross_dataset": "rank",
    "unsupported_rank_us_state": "rank",
    "unsupported_rank_epss_utility": "rank",
    "unsupported_future_prediction": "other",
    **{f"unsupported_{key}": "other" for key in UNSUPPORTED},
}


def policy_rule_ids() -> set[str]:
    """Clarification and unsupported rule ids that routing.py can emit."""
    source = Path(__file__).resolve().parents[1] / "routing.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "RouteDecision" or len(node.args) < 2:
            continue
        path = node.args[0]
        rule = node.args[1]
        if (
            isinstance(path, ast.Constant)
            and path.value in {"clarification", "unsupported"}
            and isinstance(rule, ast.Constant)
            and isinstance(rule.value, str)
        ):
            found.add(rule.value)
    found.update(f"unsupported_{key}" for key in UNSUPPORTED)
    return found


def routing_rule_ids() -> set[str]:
    """Rule ids statically present in routing.py, plus unsupported_{table key}."""
    source = Path(__file__).resolve().parents[1] / "routing.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name == "RouteDecision" and len(node.args) >= 2:
            rule = node.args[1]
            if isinstance(rule, ast.Constant) and isinstance(rule.value, str):
                found.add(rule.value)
        for keyword in node.keywords:
            if keyword.arg == "rule" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    found.add(keyword.value.value)
    found.update(f"unsupported_{key}" for key in UNSUPPORTED)
    return found


def _disposition(path: str) -> str:
    if path in {"deterministic", "model"}:
        return "answer"
    if path == "clarification":
        return "clarify"
    if path == "unsupported":
        return "unsupported"
    return "other"


def regex_labels(decision: RouteDecision) -> dict[str, Any]:
    """Labels for agreement. Does not change the decision.

    An unknown rule is null and unmapped_rule, never the intent "other".
    A rule explicitly mapped to None (deterministic_router_disabled) is also
    excluded from intent scoring, without the unmapped flag.
    """
    if decision.rule not in RULE_TO_INTENT:
        intent: str | None = None
        unmapped_rule = True
    else:
        intent = RULE_TO_INTENT[decision.rule]
        unmapped_rule = False
    if decision.rule == "filtered_records":
        modes = [
            args.get("result_mode")
            for name, args in decision.tool_calls
            if name == "data_query_records"
        ]
        if modes and modes[0] == "records":
            intent = "records_list"
    dataset = decision.slots.get("dataset") or "none"
    utilities = list(decision.slots.get("utilities") or [])
    county = decision.slots.get("county")
    rule = decision.rule
    clarify = rule if decision.path == "clarification" else "not_applicable"
    unsupported = rule if decision.path == "unsupported" else "not_applicable"
    risk_rules = {
        "cell_risk",
        "county_risk",
        "utility_risk",
        "coordinate_risk_chain",
        "city_point_risk_chain",
        "ambiguous_risk_metric",
        "ambiguous_risk_place",
        "risk_missing_place",
        "risk_future_date",
        "forecast_missing_date",
    }
    return {
        "disposition": _disposition(decision.path),
        "clarify_reason": clarify,
        "unsupported_topic": unsupported,
        "intent": intent,
        "unmapped_rule": unmapped_rule,
        "dataset": dataset,
        "utilities": utilities,
        "county": county_option_id(county) if county else "none",
        "wants_risk": rule in risk_rules or rule.startswith("risk_"),
        "wants_map": intent in {"map", "map_plus_trend"},
        "is_exploratory": rule == "open_ended",
    }


def model_tool_labels(trajectory: list[dict[str, Any]], status: str) -> dict[str, Any]:
    """Tools qwen3 emitted on its first routing turn, and the last successful primary tool."""
    phase = "before"
    first: list[str] = []
    for event in trajectory:
        if event.get("type") == "model_turn" and event.get("phase") == "routing":
            phase = "first" if phase == "before" else "later"
            continue
        if (
            phase == "first"
            and event.get("type") == "tool_call"
            and not event.get("qualification_call")
        ):
            tool = event.get("tool")
            if isinstance(tool, str):
                first.append(tool)
    successful = [
        event.get("tool")
        for event in trajectory
        if event.get("type") == "tool_call"
        and event.get("ok")
        and not event.get("qualification_call")
        and isinstance(event.get("tool"), str)
    ]
    return {
        "model_first_tools": first,
        "model_final_tools": successful[-1:],
        "returned_clarification": status == "clarification",
        "returned_unsupported": status == "unsupported",
    }


def _choice_value(answers: dict[str, Answer], name: str) -> str | None:
    item = answers.get(name)
    if item is None or item.kind != "choice" or item.value is None:
        return None
    return str(item.value)


def _noul_ids(answers: dict[str, Answer], prefix: str, threshold: float = 0.5) -> list[str]:
    found: list[str] = []
    for name, item in answers.items():
        if not name.startswith(prefix) or item.kind != "noul" or item.value is None:
            continue
        if float(item.value) >= threshold:
            found.append(name[len(prefix) :])
    return found


def agreement(regex: dict[str, Any], answers: dict[str, Answer]) -> dict[str, bool]:
    """Field-level agreement. Not accuracy. Missing Jev answers are not compared here."""
    jev_utilities = _noul_ids(answers, "utility_")
    regex_utilities = list(regex.get("utilities") or [])
    return {
        "disposition": _choice_value(answers, "disposition") == regex.get("disposition"),
        "intent": (
            None
            if regex.get("intent") is None or regex.get("unmapped_rule")
            else _choice_value(answers, "intent") == regex.get("intent")
        ),
        "dataset": _choice_value(answers, "dataset") == regex.get("dataset"),
        "utilities": sorted(jev_utilities) == sorted(regex_utilities),
        "county": _choice_value(answers, "county") == regex.get("county"),
    }


def _fault_or_bounded_case(case: dict[str, Any]) -> bool:
    """Harness fault or a success-or-error bound. Not a real coverage refusal.

    A lone expected_status of error with no fault_scenario is the question's
    own outcome (for example a date past covariate coverage), so it is routed
    from the question text instead of from the tool the harness attempted.
    """
    status = case.get("expected_status")
    if case.get("fault_scenario"):
        return True
    return isinstance(status, list) and "error" in status


def derive_case_labels(case: dict[str, Any]) -> dict[str, Any]:
    """Map a case onto Jev labels for the question itself.

    Fault injection and a success-or-error bound describe the harness, not the
    question. Those cases use expected_route and expected_tools. A lone error
    status is routed from the question text. Intent, dataset, and tool_pick are
    filled only when disposition is answer; clarify and unsupported cases keep
    intent null. clarify_reason and unsupported_topic come from route_question.
    """
    status = case.get("expected_status")
    route = case.get("expected_route")
    question = str(case.get("question") or "")
    tools = [str(item) for item in (case.get("expected_tools") or [])]
    views = [str(item) for item in (case.get("expected_view_types") or [])]
    review = False
    notes: list[str] = []
    natural = route_question(question)
    if _fault_or_bounded_case(case):
        disposition = _disposition(str(route or ""))
        notes.append(
            "fault or bounded error ignored; labels follow the question's listed route and tools"
        )
        if disposition == "other":
            disposition = None
            review = True
    elif status == "error":
        disposition = {
            "clarification": "clarify",
            "unsupported": "unsupported",
            "deterministic": "answer",
            "model": "answer",
        }.get(natural.path)
        notes.append(
            f"error status follows the question; route_question returned {natural.path}/{natural.rule}"
        )
        if disposition is None:
            review = True
    elif status == "clarification" or route == "clarification":
        disposition = "clarify"
    elif status == "unsupported" or route == "unsupported":
        disposition = "unsupported"
    elif status == "answer" or route in {"deterministic", "model"}:
        disposition = "answer"
    else:
        disposition = None
        review = True

    clarify_reason = None
    unsupported_topic = None
    if disposition == "clarify":
        if natural.path == "clarification":
            clarify_reason = natural.rule
        else:
            review = True
            notes.append(
                f"expected clarify but route_question returned {natural.path}/{natural.rule}"
            )
    elif disposition == "unsupported":
        if natural.path == "unsupported":
            unsupported_topic = natural.rule
        else:
            review = True
            notes.append(
                f"expected unsupported but route_question returned {natural.path}/{natural.rule}"
            )

    if disposition == "answer":
        intent, intent_clear = _intent_from_case(tools, views, question)
        tool_pick, tool_clear = _tool_pick_from_case(tools)
        if not intent_clear or not tool_clear:
            review = True
        if not intent_clear:
            notes.append("intent cannot be derived from expected tools and the question")
        if not tool_clear:
            notes.append("tool_pick is not a single expected tool")
    else:
        intent, intent_clear = None, False
        tool_pick, tool_clear = None, False

    row = {
        "case_id": case.get("id"),
        "question": question,
        "expected_route": route,
        "expected_status": status,
        "expected_tools": tools,
        "expected_view_types": views,
        "disposition": disposition,
        "intent": intent if intent_clear else None,
        "dataset": None,
        "tool_pick": tool_pick if tool_clear else None,
        "clarify_reason": clarify_reason,
        "unsupported_topic": unsupported_topic,
        "comparison_kind": None,
        "needs_human_review": review,
        "intent_excluded": disposition != "answer" or not intent_clear,
        "notes": "; ".join(notes),
    }
    from services.agent.decisions.expected_facts import expected_facts

    row["expected_facts"] = expected_facts(question)
    return _apply_label_overrides(str(case.get("id") or ""), row)


def _apply_label_overrides(case_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """Hand-set labels for questions the tool list does not disambiguate."""
    override = _LABEL_OVERRIDES.get(case_id)
    if override is None:
        return row
    extra = override.get("notes_extra")
    for key, value in override.items():
        if key == "notes_extra":
            continue
        row[key] = value
    if row.get("disposition") == "answer" and row.get("intent") is not None:
        row["intent_excluded"] = False
    if extra:
        note = row.get("notes") or ""
        row["notes"] = f"{note}; {extra}" if note else extra
    return row


_LABEL_OVERRIDES: dict[str, dict[str, Any]] = {
    "model_cpuc_tell_me_about_2023": {
        "intent": "exploratory_overview",
        "needs_human_review": False,
    },
    "model_sacramento_tell_me_about_2024": {
        "intent": "exploratory_overview",
        "needs_human_review": False,
    },
    "count_plus_trend": {
        "intent": "multi_intent",
        "tool_pick": ["data_query_records", "visualization_create"],
        "needs_human_review": False,
    },
    "holdout_count_trend_sce_2023": {
        "intent": "multi_intent",
        "tool_pick": ["data_query_records", "visualization_create"],
        "needs_human_review": False,
    },
    "coordinate_to_risk": {
        "intent": "risk",
        "tool_pick": ["data_query_spatial", "risk_forecast"],
        "needs_human_review": False,
    },
    "cpuc_vs_us": {
        "intent": ["compare", "multi_intent"],
        "tool_pick": ["data_query_records"],
        "needs_human_review": False,
    },
    "holdout_unsupported_budget": {
        "unsupported_topic": ["unsupported_cost", "unsupported_optimization"],
    },
    "utility_not_invented_from_place": {
        "acceptable_outcomes": [
            {
                "disposition": "unsupported",
                "unsupported_topic": "unexpressable_county_filter",
            },
            {
                "disposition": "answer",
                "dataset": "calfire_incidents",
                "intent": "count",
            },
        ],
        "notes_extra": (
            "Router policy under review: either refuse because no county-capable "
            "dataset was named, or answer a CAL FIRE county count."
        ),
    },
}


def _intent_from_case(
    tools: list[str],
    views: list[str],
    question: str = "",
) -> tuple[str | None, bool]:
    unique = list(dict.fromkeys(tools))
    if unique == ["data_query_records"]:
        lower = question.lower()
        listing = bool(re.search(r"\b(?:list|records?)\b", lower))
        counting = bool(re.search(r"\b(?:how many|count|number of|tally|total)\b", lower))
        if listing and not counting:
            return "records_list", True
        if counting and not listing:
            return "count", True
        return None, False
    if unique == ["visualization_inspect"]:
        lower = question.lower()
        if re.search(r"\bterritor", lower) and not re.search(r"\b\d{9}\b", question):
            return "territory_boundary", True
        if re.search(r"\b(?:circuit|\d{9})\b", lower):
            return "circuit_detail", True
        return None, False
    if unique == ["data_query_rank"]:
        return "rank", True
    if unique == ["data_query_spatial"]:
        return "spatial_context", True
    if unique == ["risk_forecast"]:
        return "risk", True
    if unique == ["comparison_run"]:
        return "compare", True
    if unique == ["visualization_create"]:
        if views == ["map"]:
            return "map", True
        if views == ["time_series"]:
            return "trend", True
        if set(views) == {"map", "time_series"}:
            return "map_plus_trend", True
        lower = question.lower()
        has_map = bool(re.search(r"\bmap\b", lower))
        has_trend = bool(
            re.search(r"\b(?:trend|time series|weekly|monthly|daily)\b", lower)
        )
        if has_map and has_trend:
            return "map_plus_trend", True
        if has_map and not has_trend:
            return "map", True
        if has_trend and not has_map:
            return "trend", True
        return None, False
    if set(unique) == {"data_query_records", "visualization_create"}:
        return "multi_intent", True
    if "data_query_spatial" in unique and "risk_forecast" in unique:
        return "risk", True
    if not unique:
        return "exploratory_overview", False
    return None, False


def _tool_pick_from_case(tools: list[str]) -> tuple[str | None, bool]:
    unique = list(dict.fromkeys(tools))
    if len(unique) == 1:
        return unique[0], True
    if not unique:
        return None, False
    return None, False


def _case_notes(intent_clear: bool, tool_clear: bool, tools: list[str]) -> str:
    notes: list[str] = []
    if not intent_clear:
        notes.append("intent cannot be derived from expected tools and views alone")
    if not tool_clear:
        notes.append("tool_pick is not a single expected tool")
    if "comparison_run" in tools:
        notes.append("comparison_kind is not in cases.json and is not scored")
    notes.append("dataset is not in cases.json and is not scored")
    return "; ".join(notes)


def contaminated_case(case: dict[str, Any]) -> bool:
    """domain.py worked examples use SCE 2023, so those holdouts may be memorized."""
    question = str(case.get("question") or "")
    lowered = question.lower()
    return "sce" in lowered and "2023" in lowered


def utility_ids() -> list[str]:
    return list(UTILITY_PATTERNS)
