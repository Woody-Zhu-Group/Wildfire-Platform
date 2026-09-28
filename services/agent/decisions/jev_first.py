"""Jev owns decide-mode intent. Parsing and execution constraints stay in code."""

from __future__ import annotations

import math
from dataclasses import asdict
from datetime import date
from typing import Any

from services.agent.decisions.decide_mode import (
    ask_jev,
    _answer_confidence,
    _REASON_TEXT,
)
from services.agent.decisions.jev_policy import RISK_COVERAGE_END
from services.agent.decisions.v4 import SCHEMA_VERSION, calls_for, topic_questions
from services.agent.grounding import named_tiers
from services.agent.places import city_point
from services.agent.routing import (
    RouteDecision,
    UNSUPPORTED_ANSWERS,
    question_context,
    _city_matches,
    _city_named_as_county,
    _city_point_slot,
    _unresolved_county_place,
    _CITY_POINT_PARTIAL,
    _CITY_POINT_RADIUS,
)
from services.agent.schemas import TOOL_MODELS
from services.shared.dataset_registry import ALLOWED_RANK_PAIRS, DATASETS

_TOOLS = {
    "count": ["data_query_records", "data_query_spatial"],
    "records_list": ["data_query_records"],
    "map": ["visualization_create"],
    "trend": ["visualization_create", "data_query_records"],
    "map_plus_trend": ["visualization_create", "data_query_records"],
    "compare": ["comparison_run", "data_query_records", "data_query_spatial"],
    "rank": ["data_query_rank"],
    "risk": ["risk_forecast", "data_query_spatial"],
    "spatial_context": ["data_query_spatial"],
    "territory_boundary": ["visualization_inspect"],
    "circuit_detail": ["visualization_inspect"],
    "exploratory_overview": list(TOOL_MODELS),
    "multi_intent": list(TOOL_MODELS),
}
_EVENT_INTENTS = {
    "count",
    "records_list",
    "map",
    "trend",
    "map_plus_trend",
    "compare",
    "rank",
}
_INVENTORY = {"circuits", "hftd", "iou_territories"}
_TOPICS = {
    "cpz": "unsupported_cpz",
    "cost_or_budget": "unsupported_cost",
    "optimization_or_scheduling": "unsupported_optimization",
    "damage_or_loss": "unsupported_damage",
    "live_or_web": "unsupported_live_web",
    "advice_or_judgment": "unsupported_advice",
    "other_off_topic": "unsupported_topic",
}


def _value(answers: dict[str, Any], name: str) -> Any:
    answer = answers.get(name)
    return (
        answer.get("value")
        if isinstance(answer, dict)
        else getattr(answer, "value", None)
    )


def _confidence(answers: dict, name: str) -> float:
    value = _answer_confidence(answers.get(name))
    return (
        value if value is not None and math.isfinite(value) and 0 <= value <= 1 else 0.0
    )


def highest_choices(answers: dict) -> dict:
    """Select maximal-probability labels without changing reported confidence.

    Keep the provider's choice on a maximum-probability tie; otherwise break
    ties by label. Missing or malformed probabilities cannot supply a choice.
    """
    selected = {}
    for name, answer in answers.items():
        item = dict(answer) if isinstance(answer, dict) else asdict(answer)
        if item.get("kind") == "choice":
            probabilities = item.get("probabilities")
            if not probabilities or any(
                not isinstance(p, (int, float))
                or not math.isfinite(p)
                or not 0 <= p <= 1
                for p in probabilities.values()
            ):
                raise ValueError(f"No valid choice probabilities for {name}")
            maximum = max(probabilities.values())
            if maximum == 0:
                raise ValueError(f"No positive choice probability for {name}")
            winners = sorted(
                label
                for label, probability in probabilities.items()
                if probability == maximum
            )
            item["value"] = (
                item["value"] if item.get("value") in winners else winners[0]
            )
        selected[name] = item
    return selected


def decide_from_answers(
    question: str,
    answers: dict | None,
    *,
    gate: float = 0.8,
    answer_gate: float = 0.9,
    error: str | None = None,
    today: date | None = None,
    use_confidence: bool = True,
) -> RouteDecision:
    """Pure v4 policy, shared by live execution, scripted tests and replay.

    No call to route_question, no keyword candidate catalog, and no router
    fallback on uncertainty, missing facts, backend failure or budget exhaustion.
    """
    today = today or date.today()
    slots, time = question_context(question, today=today)
    answers = answers or {}
    if not use_confidence and answers and not error:
        answers = highest_choices(answers)
    intent = _value(answers, "intent")
    intent_confidence = _confidence(answers, "intent")

    def certain(name: str, threshold: float) -> bool:
        return not use_confidence or _confidence(answers, name) >= threshold

    truth_threshold = gate if use_confidence else 0.5

    def finish(
        path: str, rule: str, text: str = "", *, confidence=None, calls=None, why="gate"
    ):
        slots["jev_decide"] = {
            "schema_version": SCHEMA_VERSION,
            "winner": "jev",
            "why": why,
            "jev_disposition": {
                "model": "answer",
                "deterministic": "answer",
                "clarification": "clarify",
            }.get(path, path),
            "jev_rule": rule,
            "jev_confidence": confidence,
            "jev_intent": intent,
            "jev_intent_confidence": intent_confidence,
        }
        if not use_confidence:
            slots["jev_decide"]["selection"] = "argmax_without_confidence_gate"
            if why == "gate":
                slots["jev_decide"]["why"] = "argmax"
        return RouteDecision(
            path,
            rule,
            f"Jev-first decide: {rule}",
            tool_calls=calls or [],
            answer=text or None,
            slots=slots,
        )

    def clarify(
        rule: str, text: str | None = None, *, why="constraint", confidence=None
    ):
        text = text or _REASON_TEXT.get(
            rule, "Please specify the result, dataset, place and time period you need."
        )
        if (
            rule in {"undefined_region", "undefined_spatial_scope", "city_needs_place"}
            and intent in _EVENT_INTENTS
            and time.status == "none"
            and _value(answers, "dataset") not in _INVENTORY
        ):
            text += " I also need a year or date range."
        return finish("clarification", rule, text, why=why, confidence=confidence)

    def uncertain():
        return clarify(
            "jev_uncertain",
            "I could not confidently determine the requested result. Please clarify what you want to count, compare, map, or inspect.",
            why="below_gate",
        )

    if error or not answers:
        return finish(
            "error",
            "jev_unavailable",
            "The question-understanding service is unavailable. Please try again. No data query was run.",
            why=error or "missing_answers",
        )

    specs = topic_questions()
    # Invalid or missing labels are not interpreted as an on-topic answer.
    for name in ("intent", "off_topic", "dataset", "measure"):
        if _value(answers, name) not in specs[name].criteria:
            return uncertain()
    topic = _value(answers, "off_topic")
    if topic in _TOPICS and certain("off_topic", gate):
        rule = _TOPICS[topic]
        text = (
            "This service reports historical data; it does not give recommendations or judgments about blame, responsibility, or penalties."
            if topic == "advice_or_judgment"
            else UNSUPPORTED_ANSWERS.get(
                rule.removeprefix("unsupported_"),
                "The available wildfire services do not provide that information.",
            )
        )
        return finish(
            "unsupported", rule, text, confidence=_confidence(answers, "off_topic")
        )
    if topic != "on_topic" or not certain("off_topic", answer_gate):
        return uncertain()
    for name, rule in (
        ("prompt_injection", "prompt_injection"),
        ("future_time", "unsupported_future_prediction"),
    ):
        value = _value(answers, name)
        if (
            not isinstance(value, (float, int))
            or not math.isfinite(value)
            or not 0 <= value <= 1
            or not certain(name, gate)
        ):
            return uncertain()
        if value >= truth_threshold:
            return finish(
                "unsupported",
                rule,
                "This service can only answer supported historical wildfire data questions.",
                confidence=_confidence(answers, name),
            )
        if not certain(name, answer_gate):
            return uncertain()
    if intent == "other" or not certain("intent", gate):
        return uncertain()

    dataset = _value(answers, "dataset")
    measure = _value(answers, "measure")
    if not certain("dataset", gate) or not certain("measure", gate):
        return uncertain()
    if slots["dataset"] and dataset not in {slots["dataset"], "multiple", "none"}:
        return clarify(
            "jev_dataset_conflict",
            "Which dataset should I use? The dataset interpretation conflicts with the one named in your question.",
        )
    if not slots["dataset"] and dataset in DATASETS:
        slots["dataset"] = dataset
    if dataset == "none" and intent in _EVENT_INTENTS:
        return clarify("missing_dataset", "Which wildfire dataset should I use?")

    for fact, rule in (
        ("broad_region", "undefined_region"),
        ("vague_proximity", "undefined_spatial_scope"),
    ):
        value = _value(answers, fact)
        if (
            not isinstance(value, (float, int))
            or not math.isfinite(value)
            or not 0 <= value <= 1
            or not certain(fact, gate)
        ):
            return uncertain()
        if value >= truth_threshold:
            return clarify(rule, why="gate", confidence=_confidence(answers, fact))
        if not certain(fact, answer_gate):
            return uncertain()
    if time.status in {"ambiguous", "out_of_coverage"}:
        return clarify(
            "ambiguous_relative_time"
            if time.status == "ambiguous"
            else "time_out_of_coverage",
            time.reason,
        )
    if time.end_date and date.fromisoformat(time.end_date) > today:
        return finish(
            "unsupported",
            "unsupported_future_prediction",
            "Future event counts and forecasts are not available.",
            why="constraint",
        )
    if (
        slots["county"]
        and dataset in DATASETS
        and "county" not in DATASETS[dataset].allowed_filters
    ):
        return clarify("unexpressable_county_filter")
    if named_tiers(question) and dataset == "circuits":
        return clarify(
            "hftd_constraint_unavailable",
            "No available tool intersects circuits with an HFTD tier. Please choose a supported circuit or HFTD query.",
        )

    unknown_county = _city_named_as_county(question.lower())
    if unknown_county:
        return clarify(
            "unknown_county",
            f"{unknown_county.title()} County is not a stored California county. Which county should I use?",
        )
    unresolved_place = _unresolved_county_place(question)
    if unresolved_place:
        return clarify(
            "county_place_ambiguous",
            f"Please specify the county or coordinates for {unresolved_place}.",
        )
    cities = (
        {match.group(0) for match in _city_matches(question.lower())}
        if not slots["coords"]
        else set()
    )
    if len(cities) > 1:
        return clarify(
            "city_needs_place",
            "Please give one city-center point, or explicit coordinates for the places to inspect.",
        )
    city = next(iter(cities), None)
    point = city_point(city) if city else None
    if point and (
        _CITY_POINT_RADIUS.search(question.lower())
        or _CITY_POINT_PARTIAL.search(question.lower().replace(city, " "))
    ):
        return clarify(
            "city_needs_place",
            "A city center cannot represent an address, neighborhood, radius, or city boundary. Please give coordinates or a supported geographic scope.",
        )
    if city and (
        point is None
        or intent not in {"risk", "spatial_context"}
        or measure in {"event_count", "record_list"}
    ):
        return clarify(
            "city_needs_place",
            f"{city.title()} is a city, not a county or utility territory. Please supply a supported geographic scope.",
        )
    if point:
        slots["coords"] = (point.lat, point.lon)
        slots["county"] = None
        slots["city_point"] = _city_point_slot(point)

    if intent in _EVENT_INTENTS and dataset not in _INVENTORY and time.status == "none":
        return clarify("records_missing_year", "What year or date range should I use?")
    if intent in {"count", "rank", "compare", "trend"} and measure == "other_measure":
        return clarify(
            "ambiguous_risk_metric",
            "Which supported measure should I use: event count, CAL FIRE acres, PSPS customer-events, or a supported comparison rate?",
        )
    if intent == "rank":
        group = _value(answers, "rank_dimension")
        metric = "acres_burned" if measure == "acres_burned" else "count"
        if (
            not certain("rank_dimension", gate)
            or (dataset, group, metric) not in ALLOWED_RANK_PAIRS
        ):
            return clarify("unsupported_ranking")
        if (group == "county" and slots["county"]) or named_tiers(question):
            return clarify("unexpressed_filter_constraints")

    if intent in {"risk", "risk_surface"}:
        if not time.start_date or time.start_date != time.end_date:
            return clarify(
                "forecast_missing_date",
                "Which single historical calendar day should I score?",
            )
        if date.fromisoformat(time.start_date) > RISK_COVERAGE_END:
            return clarify(
                "risk_future_date",
                f"Fitted risk is available through {RISK_COVERAGE_END}. Which past day should I score?",
            )
    if intent == "risk" and (len(slots["utilities"]) + len(slots["counties"]) > 1):
        return clarify(
            "ambiguous_risk_place",
            "Which single county or utility territory should I score?",
        )
    if intent == "risk_surface":
        if (
            slots["utilities"]
            or slots["counties"]
            or slots["coords"]
            or named_tiers(question)
        ):
            return clarify(
                "unexpressed_filter_constraints",
                "The model grid is statewide. Please request the statewide grid or a supported point, county, or utility risk score.",
            )
        kind = _value(answers, "risk_map_kind")
        if kind not in {"risk", "residual"} or not certain("risk_map_kind", gate):
            return uncertain()
        slots["map_mode"] = kind
        return finish(
            "deterministic",
            "jev_risk_surface",
            confidence=intent_confidence,
            calls=[("risk_surface", {"date": time.start_date})],
        )
    if intent == "model_metrics":
        if (
            time.status != "none"
            or slots["utilities"]
            or slots["counties"]
            or slots["coords"]
            or named_tiers(question)
        ):
            return clarify(
                "unexpressed_filter_constraints",
                "Model evaluation is statewide for the saved evaluation period. Geographic or date filters are not supported.",
            )
        slots["stat_mode"] = "model_metrics"
        return finish(
            "deterministic",
            "jev_model_metrics",
            confidence=intent_confidence,
            calls=[("risk_metrics", {})],
        )
    if intent == "risk" and not (
        slots["coords"] or slots["county"] or slots["utilities"]
    ):
        return clarify("risk_missing_place")
    if point:
        calls = [
            (
                "data_query_spatial",
                {
                    "kind": "point",
                    "lat": point.lat,
                    "lon": point.lon,
                    "snap_shoreline": True,
                },
            )
        ]
        if intent == "risk":
            calls.append(
                ("risk_forecast", {"cell_id": "$grid_cell_id", "date": time.start_date})
            )
        return finish(
            "deterministic", "jev_city_point", confidence=intent_confidence, calls=calls
        )

    slots["candidate_tools"] = _TOOLS[intent]
    slots["jev_request"] = {"intent": intent, "dataset": dataset, "measure": measure}
    return finish("model", "jev_intent", confidence=intent_confidence)


def decide_live(
    question: str,
    *,
    backend,
    gate: float,
    answer_gate: float,
    timeout: float,
    budget=None,
):
    today = date.today()
    calls = calls_for(question, today.isoformat())
    if budget is not None and not budget.reserve(len(calls)):
        return decide_from_answers(question, None, error="daily_cap")
    answers, error, _ = ask_jev(
        backend, question, today.isoformat(), timeout=timeout, calls=calls
    )
    return decide_from_answers(
        question, answers, gate=gate, answer_gate=answer_gate, error=error, today=today
    )
