"""AGENT_JEV_MODE=decide: router backstops first, then Jev's derived disposition.

Order for one question:
1. Router hard backstops (BACKSTOP_RULES) decide. Jev is not called.
2. Routes Jev cannot express (router_only) are decided by the router. Jev is not called.
3. Otherwise Jev's v3_hybrid facts go through derive_outcome (the same policy,
   including the measure gate, that the offline combined decider scored).
   - Jev clarify or refuse at or above the decline gate (default 0.8) returns
     that disposition. Jev owns the disposition; the router owns the wording.
     When the router also declined the same way (both clarify, or both refuse),
     the router's text, rule, and reason stand and Jev's rule goes to the log only,
     unless the router's rule is a generic one (GENERIC_ROUTER_RULES): then Jev's
     more specific clarification is shown, composed with the router's slots.
     When Jev changes the disposition, its clarification goes through the same
     clarify-all-missing composition as the router's, using the router's slots.
   - A Jev clarification about an item the router already resolved (the time,
     or the place: county, utility, coordinates, or a geocoded city) is ignored
     (why contradicts_slot). So is one asking for a time or place that the
     router's chosen deterministic calls do not take, for example a year on a
     point-context, territory, or circuit lookup (why slot_unused).
   - Jev answer where the router declined wins only at or above the separate,
     higher answer gate (default 0.9) on the facts behind the router's rule, since
     a wrong answer is worse than a clarifying question. The question then takes
     the model path, since a declined route has no deterministic call.
   - A Jev answer never overrides a decline the router's resolver proved in code:
     a missing or ambiguous time, or a missing place.
   - Below the gate, on a timeout, on any Jev error, or when the daily API call
     cap (AGENT_JEV_DAILY_CALL_CAP, counted per call) is reached, the router
     stands. The cap case is recorded as why "daily_cap".
4. When the final disposition is answer, the question proceeds as today: the
   router's deterministic call if it has one, otherwise the model path.

decide_from_answers is pure so the runtime and the offline replay share it.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import threading
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from services.agent.clarify_missing import (
    complete_clarification,
    rank_slots_question,
    series_dataset_question,
)
from services.agent.measure_clarify import measure_clarification, measure_group
from services.agent.decisions.backend import Answer
from services.agent.decisions.call_budget import DailyCallBudget
from services.agent.decisions.jev_policy import (
    OFF_TOPIC_RULES,
    REGEX_ONLY,
    MEASURE_CLARIFY_INTENTS,
    DerivedOutcome,
    JevFacts,
    derive_outcome,
    facts_from_answers,
)
from services.agent.routing import (
    _RISK_COVERAGE_LIMIT,
    UNSUPPORTED,
    UNSUPPORTED_ANSWERS,
    RouteDecision,
)
from services.agent.schemas import TOOL_MODELS
from services.shared.dataset_registry import MEASURE_DATASETS, RANK_MEASURES

logger = logging.getLogger("services.agent.decisions")

# Router hard backstops. These fire before Jev is asked anything.
BACKSTOP_RULES: frozenset[str] = frozenset(
    {
        "unsupported_live_web",
        "risk_future_date",
        "unsupported_future_prediction",
        "city_needs_place",
        "hftd_constraint_unavailable",
        # Explicit unsupported topics: every routing.UNSUPPORTED key.
        *(f"unsupported_{key}" for key in UNSUPPORTED),
    }
)

# Tools the router calls that are not in Jev's tool vocabulary (schemas.TOOL_MODELS),
# for example risk_surface. A route that uses one is decided by the router, so Jev
# never asks for a place on a statewide surface question.
def router_only_tools(decision: RouteDecision) -> list[str]:
    return sorted({tool for tool, _args in decision.tool_calls if tool not in TOOL_MODELS})


def exemption(decision: RouteDecision) -> str | None:
    """Why the router decides this question alone, or None when Jev is asked."""
    if decision.rule in BACKSTOP_RULES:
        return "backstop"
    if decision.rule in REGEX_ONLY:
        return "regex_only"
    if router_only_tools(decision):
        return "router_only_tool"
    return None


# Router clarifications that say only that something is missing, without naming
# what is wrong with the question. When Jev also clarifies, with a different and
# more specific reason, Jev's reason is shown instead of the router's.
GENERIC_ROUTER_RULES: frozenset[str] = frozenset({"ranking_missing_slots"})

ROUTER_DISPOSITION = {
    "deterministic": "answer",
    "model": "answer",
    "clarification": "clarify",
    "unsupported": "unsupported",
}

# Facts behind each rule. Used for the confidence of a Jev decline and for how
# sure Jev must be that a router decline does not hold before an answer wins.
# Rules decided in code from the question text (time_out_of_coverage and the
# riskiest regex) have no Jev fact, so Jev never overrides them.
_RULE_FACTS: dict[str, tuple[str, ...]] = {
    "prompt_injection": ("prompt_injection",),
    "missing_location": ("vague_proximity", "names_specific_place"),
    "undefined_spatial_scope": ("vague_proximity",),
    "undefined_region": ("broad_region",),
    "ambiguous_relative_time": ("vague_time", "has_time_scope"),
    "risk_future_date": ("asks_risk", "future_time"),
    "risk_missing_place": ("asks_risk", "names_specific_place"),
    "forecast_missing_date": ("asks_risk", "has_time_scope"),
    "ambiguous_risk_place": ("asks_risk", "county"),
    "map_plus_trend_missing_year": ("intent", "has_time_scope"),
    "map_missing_year": ("intent", "has_time_scope"),
    "trend_missing_year": ("intent", "has_time_scope"),
    "spatial_missing_year": ("intent", "has_time_scope"),
    "records_missing_year": ("intent", "has_time_scope"),
    "ranking_missing_year": ("intent", "rank_dimension", "has_time_scope"),
    "ranking_missing_slots": ("intent", "dataset", "rank_dimension"),
    "ranking_county_contradiction": ("intent", "rank_dimension", "county"),
    "unsupported_rank_cross_dataset": ("intent", "rank_dimension", "mentions_multiple_datasets"),
    "unsupported_rank_us_state": ("intent", "rank_dimension", "dataset"),
    "unsupported_rank_epss_utility": ("intent", "rank_dimension", "dataset"),
    "unsupported_ranking": ("intent", "dataset", "rank_dimension"),
    "unexpressable_county_filter": ("intent", "dataset", "county"),
    "unsupported_other_measure": ("intent", "measure"),
    "other_off_topic": ("off_topic",),
    **{rule: ("off_topic",) for rule in OFF_TOPIC_RULES.values()},
}

_REASON_TEXT: dict[str, str] = {
    "missing_location": "What latitude/longitude or bounding box should I use?",
    "undefined_spatial_scope": (
        "How should that nearby area be defined? Provide a radius (for example 25 km) "
        "or a county/utility polygon to use."
    ),
    "undefined_region": (
        "How should that region be defined? The warehouse has no such region polygons."
    ),
    "ambiguous_relative_time": "Which calendar year or exact date range should I use?",
    "time_out_of_coverage": "That time period is outside the years available in the warehouse.",
    "risk_missing_place": "Which cell, county, utility, or coordinates should I score?",
    "forecast_missing_date": "Which past date should I score?",
    "ambiguous_risk_metric": (
        "Which risk measure and time period should I use, for example ignition count, "
        "CAL FIRE incidents, EPSS outages, or fitted cell risk?"
    ),
    "ambiguous_risk_place": "Should I score the county or the utility territory?",
    "map_plus_trend_missing_year": "What year or date range should I use?",
    "map_missing_year": "What year or date range should I use?",
    "trend_missing_year": "What year or date range should I use?",
    "spatial_missing_year": "What year or date range should I use?",
    "records_missing_year": "What year or date range should I use?",
    "ranking_missing_year": "What year or date range should the ranking cover?",
    # The router's wording, with the registry's rankings.
    "ranking_missing_slots": rank_slots_question(),
    # The router's wording, with the registry's chartable datasets.
    "series_mode_missing_dataset": series_dataset_question(),
    "ranking_county_contradiction": "Should I rank all counties, or report the one county you named?",
    # Reuses routing.py wording for the same rules.
    "risk_future_date": f"{_RISK_COVERAGE_LIMIT}. Which past date should I score?",
    "unexpressable_county_filter": (
        "County filtering needs a dataset that stores county. "
        "Ask for a CAL FIRE county incident count, a CPUC county ignition "
        "count, or drop the county constraint. I will not answer with a "
        "broader statewide count that ignores county."
    ),
}
_GENERIC_UNSUPPORTED = (
    "This system cannot answer that question with its available read-only wildfire services."
)
# The router has no prompt-injection rule; its generic unsupported answer is reused.
_REASON_TEXT["prompt_injection"] = _GENERIC_UNSUPPORTED

# Which item each Jev clarification asks for. A clarification about an item the
# router already resolved is ignored, and a router decline for an item its own
# resolver proved missing cannot be answered over.
_TIME_RULES = frozenset(
    {
        "records_missing_year",
        "map_missing_year",
        "trend_missing_year",
        "spatial_missing_year",
        "map_plus_trend_missing_year",
        "ranking_missing_year",
        "ambiguous_relative_time",
        "forecast_missing_date",
    }
)
_PLACE_RULES = frozenset({"missing_location", "risk_missing_place"})
_KNOWN_TIME = {"explicit", "relative_year", "relative_range"}


def _time_status(slots: dict[str, Any]) -> str | None:
    return (slots.get("time_resolution") or {}).get("status")


def router_resolved(item: str, slots: dict[str, Any]) -> bool:
    """True when the router's own resolver filled this item for the question."""
    if item == "time":
        return _time_status(slots) in _KNOWN_TIME
    if item == "place":
        # city_point is a city the router geocoded to a Census place point (PR #28).
        return bool(
            slots.get("county")
            or slots.get("counties")
            or slots.get("utilities")
            or slots.get("coords")
            or slots.get("city_point")
        )
    return False


# Tool-call arguments that carry a time window or a place. A deterministic route
# whose calls carry none of the time arguments takes no time, so a Jev request
# for a year on it is ignored; likewise for a place.
_TIME_ARGS = frozenset(
    {
        "year",
        "date",
        "start_date",
        "end_date",
        "period_a_start",
        "period_a_end",
        "period_b_start",
        "period_b_end",
    }
)
_PLACE_ARGS = frozenset(
    {
        "lat",
        "lon",
        "bbox",
        "county",
        "utility",
        "utilities",
        "regions",
        "region_type",
        "scope",
        "scope_type",
        "circuit_id",
        "cell_id",
        "record_id",
        "tier",
        "hftd_tier",
    }
)


def route_uses(item: str, decision: RouteDecision) -> bool | None:
    """Whether the router's chosen deterministic calls take this item.

    None when the router chose no call (a model-path or declined route), so
    nothing can be said about what the eventual tool needs.
    """
    if decision.path != "deterministic" or not decision.tool_calls:
        return None
    names = _TIME_ARGS if item == "time" else _PLACE_ARGS
    return any(
        key in names and value is not None
        for _tool, args in decision.tool_calls
        for key, value in (args or {}).items()
    )


def _item_for(rule: str | None) -> str | None:
    if rule in _TIME_RULES:
        return "time"
    if rule in _PLACE_RULES:
        return "place"
    return None


def code_verified_missing(decision: RouteDecision) -> bool:
    """A router decline whose missing time or place the resolver proved in code."""
    item = _item_for(decision.rule)
    if item == "time":
        return _time_status(decision.slots) in {None, "none", "ambiguous"}
    if item == "place":
        return not router_resolved("place", decision.slots)
    return decision.rule == "time_out_of_coverage"


def registry_verified_refusal(decision: RouteDecision, question: str) -> bool:
    """A router ranking refusal the registry proves: no ranking the tools run fits.

    The question names two or more datasets, or its one dataset and its grouping
    are not a pair in RANK_MEASURES (for example CAL FIRE acres by utility,
    EPSS by utility, or any dataset by state). Like a missing time or place the
    resolver proved in code, this is a verified fact, so a Jev clarification or
    answer cannot replace it. A ranking refused for another reason (a change
    over time) on a pair the registry has is not covered.
    """
    if decision.path != "unsupported" or not decision.rule.startswith("unsupported_rank"):
        return False
    from services.agent.clarify_missing import task_datasets
    from services.agent.routing import _rank_dimension

    datasets = task_datasets(question, "rank")
    if len(datasets) >= 2:
        return True
    dataset = datasets[0] if datasets else decision.slots.get("dataset")
    group = _rank_dimension(question.lower())
    if dataset is None or group is None:
        return False
    return group not in RANK_MEASURES or all(
        MEASURE_DATASETS[measure] != dataset for measure in RANK_MEASURES[group]
    )


@dataclass
class DecideResult:
    winner: str  # "router" or "jev"
    why: str  # backstop, regex_only, router_only_tool, agree, gate, below_gate, contradicts_slot, slot_unused, code_verified, error, ...
    decision: RouteDecision
    router_path: str
    router_rule: str
    jev_disposition: str | None = None
    jev_rule: str | None = None
    jev_confidence: float | None = None
    error: str | None = None
    # "router" when Jev won a decline the router made the same way and the
    # router's wording was kept; "jev" when Jev's own text is shown.
    wording: str | None = None
    latency_ms: float | None = None
    input_tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def disposition(self) -> str:
        return ROUTER_DISPOSITION.get(self.decision.path, "answer")

    @property
    def disagrees(self) -> bool:
        return self.jev_disposition is not None and self.jev_disposition != ROUTER_DISPOSITION.get(
            self.router_path
        )

    @property
    def reason_differs(self) -> bool:
        """Jev declined for a different reason than the router's final rule."""
        return self.jev_disposition in {"clarify", "unsupported"} and self.jev_rule != self.decision.rule

    def log_record(self, question: str, request_id: str) -> dict[str, Any]:
        return {
            "event": "jev_decide",
            "request_id": request_id,
            "question_sha": hashlib.sha256(question.encode("utf-8")).hexdigest()[:16],
            "question": question,
            "router": {"path": self.router_path, "rule": self.router_rule},
            "jev": {
                "disposition": self.jev_disposition,
                "rule": self.jev_rule,
                "confidence": self.jev_confidence,
            },
            "winner": self.winner,
            "why": self.why,
            "wording": self.wording,
            "final": {"path": self.decision.path, "rule": self.decision.rule},
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


def _answer_confidence(answer: Any) -> float | None:
    if answer is None:
        return None
    kind = getattr(answer, "kind", None) if not isinstance(answer, dict) else answer.get("kind")
    value = getattr(answer, "value", None) if not isinstance(answer, dict) else answer.get("value")
    if kind == "noul":
        if not isinstance(value, (int, float)):
            return None
        return max(float(value), 1.0 - float(value))
    conf = getattr(answer, "confidence", None) if not isinstance(answer, dict) else answer.get("confidence")
    return float(conf) if isinstance(conf, (int, float)) else None


def rule_confidence(rule: str | None, answers: dict[str, Any]) -> float | None:
    """Lowest confidence among the facts behind a rule, or None if it has no Jev fact."""
    names = _RULE_FACTS.get(rule or "")
    if not names:
        return None
    values = [_answer_confidence(answers.get(name)) for name in names]
    values = [value for value in values if value is not None]
    return min(values) if values else None


def _jev_rule(outcome: DerivedOutcome) -> str:
    if outcome.disposition == "clarify":
        return outcome.clarify_reason or (outcome.trace[-1] if outcome.trace else "clarify")
    if outcome.disposition == "unsupported":
        return outcome.unsupported_topic or (outcome.trace[-1] if outcome.trace else "unsupported")
    return "answer"


# The facts that would have declined the question. Jev's confidence in an answer
# is the lowest confidence among them (each one on the "no" side).
_ANSWER_FACTS: tuple[str, ...] = (
    "prompt_injection",
    "off_topic",
    "vague_proximity",
    "broad_region",
    "vague_time",
)


def answer_confidence(answers: dict[str, Any]) -> float | None:
    """Jev's confidence that no decline fact holds, or None without those facts."""
    values = [_answer_confidence(answers.get(name)) for name in _ANSWER_FACTS]
    values = [value for value in values if value is not None]
    return min(values) if values else None


def _decline_text(rule: str, slots: dict[str, Any], question: str, facts: JevFacts | None) -> str:
    """Jev's clarification text before clarify-all-missing composition."""
    if (
        rule == "ambiguous_risk_metric"
        and facts is not None
        and facts.intent in MEASURE_CLARIFY_INTENTS
        and facts.measure == "other_measure"
    ):
        # A ranking or comparison by no measure in the data: list the registry's
        # measures for the grouping, keeping the router's grouping and period.
        return measure_clarification(
            facts.intent, slots, text=question, rank_dimension=facts.rank_dimension
        )
    return _REASON_TEXT.get(rule, "Could you clarify the question?")


def _decline_decision(
    rule: str,
    disposition: str,
    slots: dict[str, Any],
    question: str = "",
    facts: JevFacts | None = None,
) -> RouteDecision:
    if disposition == "clarify":
        text = _decline_text(rule, slots, question, facts)
        # Same composition the router applies: ask for every missing item, with an
        # example, from the router's slots. The measure text states the grouping it
        # lists measures for, so that grouping is not asked again.
        compose_slots = slots
        if (
            rule == "ambiguous_risk_metric"
            and facts is not None
            and facts.intent in MEASURE_CLARIFY_INTENTS
            and facts.measure == "other_measure"
        ):
            group = measure_group(slots, facts.rank_dimension, question)
            if group:
                compose_slots = {**slots, "rank_group": group}
        return RouteDecision(
            "clarification",
            rule,
            f"Jev decide: {rule}",
            slots=slots,
            answer=complete_clarification(rule, " ".join(question.strip().split()), compose_slots, text),
        )
    key = rule.removeprefix("unsupported_")
    return RouteDecision(
        "unsupported",
        rule,
        f"Jev decide: {rule}",
        slots=slots,
        answer=_REASON_TEXT.get(rule) or UNSUPPORTED_ANSWERS.get(key, _GENERIC_UNSUPPORTED),
    )


def decide_from_answers(
    question: str,
    decision: RouteDecision,
    answers: dict[str, Any] | None,
    *,
    gate: float = 0.8,
    answer_gate: float = 0.9,
    error: str | None = None,
    today: date | None = None,
) -> DecideResult:
    """Pure decide policy over one question's merged Jev answers.

    gate: a Jev clarify or refuse needs this confidence to win.
    answer_gate: a Jev answer over a router decline needs this (higher) confidence.
    """
    base = {"router_path": decision.path, "router_rule": decision.rule}
    exempt = exemption(decision)
    if exempt:
        return DecideResult("router", exempt, decision, **base)
    if error or not answers:
        return DecideResult("router", "error", decision, error=error or "no answers", **base)

    facts = facts_from_answers(answers)
    outcome = derive_outcome(facts, question=question, today=today)
    jev_disposition = outcome.disposition
    jev_rule = _jev_rule(outcome)
    router_disposition = ROUTER_DISPOSITION.get(decision.path, "answer")
    info = {"jev_disposition": jev_disposition, "jev_rule": jev_rule}

    if jev_disposition in {"clarify", "unsupported"}:
        if "measure_is_judgment" in outcome.trace:
            # ambiguous_risk_metric reached through Jev's measure answer.
            confidence = rule_confidence("unsupported_other_measure", answers)
        else:
            confidence = rule_confidence(jev_rule, answers)
        # A rule decided in code from the question text (no Jev fact) is certain.
        if confidence is None and jev_rule not in _RULE_FACTS and "measure_is_judgment" not in outcome.trace:
            confidence = 1.0
        info["jev_confidence"] = confidence
        if jev_disposition == "clarify" and registry_verified_refusal(decision, question):
            # The registry proves no ranking fits; a clarification cannot replace that.
            return DecideResult("router", "code_verified", decision, **info, **base)
        if router_disposition == jev_disposition and decision.rule == jev_rule:
            return DecideResult("router", "agree", decision, **info, **base)
        item = _item_for(jev_rule)
        if jev_disposition == "clarify" and item and router_resolved(item, decision.slots):
            # Jev asks for something the router already resolved from the question.
            return DecideResult("router", "contradicts_slot", decision, **info, **base)
        if jev_disposition == "clarify" and item and route_uses(item, decision) is False:
            # Jev asks for a time or place the router's chosen calls do not take,
            # for example a year on a point-context or territory lookup.
            return DecideResult("router", "slot_unused", decision, **info, **base)
        if confidence is not None and confidence >= gate:
            if router_disposition == jev_disposition and not (
                jev_disposition == "clarify" and decision.rule in GENERIC_ROUTER_RULES
            ):
                # Both declined the same way: the router's wording stands.
                return DecideResult("jev", "gate", decision, wording="router", **info, **base)
            # Jev changed the disposition, or both clarify and the router's rule
            # is generic: Jev's reason, composed with the router's slots.
            declined = _decline_decision(jev_rule, jev_disposition, decision.slots, question, facts)
            return DecideResult("jev", "gate", declined, wording="jev", **info, **base)
        return DecideResult("router", "below_gate", decision, **info, **base)

    # Jev says answer.
    if router_disposition == "answer":
        info["jev_confidence"] = answer_confidence(answers)
        return DecideResult("router", "agree", decision, **info, **base)
    # The router declined. A missing time or place its resolver proved in code stands.
    confidence = rule_confidence(decision.rule, answers)
    info["jev_confidence"] = confidence
    if code_verified_missing(decision) or registry_verified_refusal(decision, question):
        return DecideResult("router", "code_verified", decision, **info, **base)
    # Otherwise Jev must be sure the facts behind that rule do not hold.
    if confidence is not None and confidence >= answer_gate:
        answered = RouteDecision(
            "model",
            "jev_decide_answer",
            f"Jev decide: answer over router {decision.rule}",
            slots=dict(decision.slots),
        )
        return DecideResult("jev", "gate", answered, **info, **base)
    return DecideResult("router", "below_gate", decision, **info, **base)


def jev_calls(question: str, today: str) -> list[dict[str, Any]]:
    """The v3_hybrid disposition calls (facts, topic, places); no tool_pick call."""
    from services.agent.decisions.v3 import calls_for_config

    return calls_for_config(question, today, None, "v3_hybrid")


# One bounded pool for every decide request. A call that outlives its timeout
# keeps a worker until the backend's own timeout ends it, but the pool never
# grows past MAX_WORKERS, so timed-out requests cannot pile up threads.
MAX_WORKERS = 8
_EXECUTOR_LOCK = threading.Lock()
_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def shared_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=MAX_WORKERS, thread_name_prefix="jev-decide"
            )
        return _EXECUTOR


def ask_jev(
    backend: Any,
    question: str,
    today: str,
    *,
    timeout: float | None = None,
) -> tuple[dict[str, Answer], str | None, int]:
    """Run the three calls on the shared pool. Any failed call is an error for the question.

    With a timeout, the question gives up after that many seconds; calls that have
    not started are cancelled, and the answer is a timeout error.
    """
    from services.agent.decisions.integrity import question_hash

    calls = jev_calls(question, today)
    digest = question_hash(question)

    def one(call: dict[str, Any]):
        return backend.evaluate(call["state"], call["questions"], request_id=call["name"], question_hash=digest)

    pool = shared_executor()
    futures = [pool.submit(one, call) for call in calls]
    done, pending = concurrent.futures.wait(futures, timeout=timeout)
    if pending:
        for future in pending:
            future.cancel()
        return {}, f"timeout after {timeout}s", 0
    results = [future.result() for future in futures]
    answers: dict[str, Answer] = {}
    tokens = 0
    for call, result in zip(calls, results):
        if result is None:
            return {}, f"{call['name']}: backend disabled", tokens
        if result.error:
            return {}, f"{call['name']}: {result.error}", tokens
        answers.update(result.answers)
        tokens += int(result.input_tokens or 0)
    return answers, None, tokens


def decide_live(
    question: str,
    decision: RouteDecision,
    *,
    backend: Any,
    gate: float,
    answer_gate: float = 0.9,
    today: date | None = None,
    timeout: float | None = None,
    budget: DailyCallBudget | None = None,
) -> DecideResult:
    """Runtime decide: skip Jev for exempt routes, otherwise ask it and apply the policy.

    budget: today's API call budget. The question's calls are reserved before any
    is sent; when they do not fit, Jev is not asked and the router stands with
    why "daily_cap".
    """
    if exemption(decision):
        return decide_from_answers(question, decision, None, gate=gate, answer_gate=answer_gate)
    day = today or date.today()
    if budget is not None and not budget.reserve(len(jev_calls(question, day.isoformat()))):
        result = decide_from_answers(
            question, decision, None, gate=gate, answer_gate=answer_gate, error="daily_cap"
        )
        result.why = "daily_cap"
        return result
    started = time.perf_counter()
    try:
        answers, error, tokens = ask_jev(backend, question, day.isoformat(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        answers, error, tokens = {}, f"{type(exc).__name__}: {exc}", 0
    result = decide_from_answers(
        question, decision, answers, gate=gate, answer_gate=answer_gate, error=error, today=day
    )
    if error and error.startswith("timeout"):
        result.why = "timeout"
    result.latency_ms = (time.perf_counter() - started) * 1000
    result.input_tokens = tokens
    return result
