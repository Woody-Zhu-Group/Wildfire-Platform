"""Archived router_gate payload and policy for offline historical replay only.

This module is not imported by the agent runtime and has no live-call entry.
Keep its request wording and pure policy unchanged for captured-data verification.
"""

from __future__ import annotations

from datetime import date

from services.agent.decisions.backend import QuestionSpec
from services.agent.decisions.decide_mode import (
    DecideResult,
    _answer_confidence,
    code_verified_missing,
    exemption as legacy_exemption,
)
from services.agent.decisions.v3 import topic_questions
from services.agent.routing import ALL_MODEL_TOOLS, RouteDecision
from services.agent.schemas import EXECUTABLE_TOOL_MODELS, TOOL_DESCRIPTIONS

SCHEMA_VERSION = "router_gate_v1"


def exemption(decision: RouteDecision, question: str | None = None) -> str | None:
    reason = legacy_exemption(decision, question)
    if reason in {"backstop", "regex_only"}:
        return reason
    if code_verified_missing(decision):
        return "code_verified"
    return None


def calls_for(question: str, today: str, decision: RouteDecision) -> list[dict]:
    return [
        {
            "name": "router_gate",
            "state": {
                "question": question,
                "today": today,
                "schema_version": SCHEMA_VERSION,
                "router_proposal": {
                    "path": decision.path,
                    "rule": decision.rule,
                    "tool_calls": decision.tool_calls,
                    "answer": decision.answer,
                    "view": {
                        key: decision.slots[key]
                        for key in (
                            "map_mode",
                            "stat_mode",
                            "series_mode",
                            "city_point",
                        )
                        if key in decision.slots
                    },
                },
                "tool_contracts": {
                    name: TOOL_DESCRIPTIONS.get(name)
                    or EXECUTABLE_TOOL_MODELS[name].__doc__
                    for name, _ in decision.tool_calls
                },
            },
            "questions": {
                "route": QuestionSpec(
                    kind="choice",
                    instructions=(
                        "Can this exact router proposal completely answer the user's question? "
                        "Judge the proposed tool calls, not merely whether a keyword or rule matched. "
                        "All requested measures, entities, filters, periods and result types must be preserved. "
                        "A single total over a date span does not show how counts changed between periods."
                    ),
                    criteria={
                        "router": "The deterministic tool calls fully answer the question as asked, or the proposed clarification/refusal correctly handles it.",
                        "agent": "The proposal changes the meaning, omits a required scope/result, incorrectly declines, or only defers to a model without an exact answer plan.",
                    },
                ),
                "intent": topic_questions()["intent"],
            },
        }
    ]


def _value(answers: dict, key: str):
    item = answers.get(key)
    return item.get("value") if isinstance(item, dict) else getattr(item, "value", None)


def decide_from_answers(
    question: str,
    decision: RouteDecision,
    answers: dict | None,
    *,
    gate: float = 0.8,
    answer_gate: float = 0.9,
    error: str | None = None,
    today: date | None = None,
) -> DecideResult:
    base = {"router_path": decision.path, "router_rule": decision.rule}
    reason = exemption(decision, question)
    if reason:
        return DecideResult("router", reason, decision, **base)
    if error or not answers:
        return DecideResult(
            "router", "error", decision, error=error or "missing_answers", **base
        )
    route = _value(answers, "route")
    confidence = _answer_confidence(answers.get("route"))
    if (
        route not in {"router", "agent"}
        or confidence is None
        or not 0 <= confidence <= 1
    ):
        return DecideResult(
            "router", "error", decision, error="invalid_router_gate_answer", **base
        )
    intent = _value(answers, "intent")
    intent_confidence = _answer_confidence(answers.get("intent"))
    extra = {
        "intent": intent,
        "intent_confidence": intent_confidence,
        "schema_version": SCHEMA_VERSION,
    }
    # A trustworthy change reading cannot be executed as one scalar count,
    # even if the separate proposal-fit reading accidentally says yes.
    scalar_change = (
        intent in {"compare", "trend"}
        and intent_confidence is not None
        and intent_confidence >= gate
        and len(decision.tool_calls) == 1
        and decision.tool_calls[0][0] == "data_query_records"
        and decision.tool_calls[0][1].get("result_mode") == "count"
    )
    exact = (decision.path == "deterministic" and bool(decision.tool_calls)) or (
        decision.path in {"clarification", "unsupported"} and bool(decision.answer)
    )
    if route == "router" and confidence >= answer_gate and exact and not scalar_change:
        disposition = {
            "deterministic": "answer",
            "clarification": "clarify",
            "unsupported": "unsupported",
        }[decision.path]
        return DecideResult(
            "jev",
            "router_accepted",
            decision,
            jev_disposition=disposition,
            jev_rule="router_accepted",
            jev_confidence=confidence,
            extra=extra,
            **base,
        )
    why = (
        "below_gate"
        if confidence < (answer_gate if route == "router" else gate)
        else "agent_selected"
    )
    if scalar_change:
        why = "intent_plan_conflict"
    slots = dict(decision.slots)
    slots["candidate_tools"] = list(ALL_MODEL_TOOLS)
    slots["jev_request"] = (
        {"intent": intent}
        if intent in topic_questions()["intent"].criteria
        and intent_confidence is not None
        and intent_confidence >= gate
        else None
    )
    model = RouteDecision(
        "model", "jev_router_gate", "Router proposal needs agent planning", slots=slots
    )
    return DecideResult(
        "jev",
        why,
        model,
        jev_disposition="answer",
        jev_rule="jev_router_gate",
        jev_confidence=confidence,
        extra=extra,
        **base,
    )

