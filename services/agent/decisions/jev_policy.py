"""Turn Jev's atomic facts into a routing outcome. Jev does not apply policy."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

import re

from services.agent.routing import _coords, _rank_metric, _wants_risk
from services.agent.time_resolve import resolve_time

# The router writes this pattern inside _wants_risk. Compile that source
# instead of keeping a second copy here.
_RISKIEST_METRIC = re.compile(
    re.search(r'r"(.*?riskiest.*?)"', inspect.getsource(_wants_risk)).group(1)
)
_KNOWN_TIME = {"explicit", "relative_year", "relative_range"}

COUNTY_CAPABLE = {
    "calfire_incidents",
    "cpuc_ignitions",
    "epss_outages",
    "psps_events",
    "circuits",
}
RISK_COVERAGE_END = date(2025, 12, 31)

# Same allowed (dataset, group_by, metric) triples as routing._route_ranking.
ALLOWED_RANK_TRIPLES = {
    ("cpuc_ignitions", "county", "count"),
    ("cpuc_ignitions", "utility", "count"),
    ("calfire_incidents", "county", "count"),
    ("calfire_incidents", "county", "acres_burned"),
    ("epss_outages", "circuit", "count"),
}

# A dropped filter is a property of the tool call the router would build, not
# of the question wording. Jev would have to know which read fires and which
# argument that read cannot express. That stays in routing.py.
REGEX_ONLY = {
    "unexpressed_filter_constraints": (
        "Depends on whether the matched read can express a named county or "
        "month. That is tool-schema arithmetic, not an atomic fact about the wording."
    ),
    "city_needs_place": (
        "A city gazetteer, not an atomic fact. The router asks for coordinates, "
        "a county, or a utility territory."
    ),
    "unknown_county": (
        "A name-plus-County check against the county list, not an atomic fact."
    ),
    "hftd_constraint_unavailable": (
        "Tool-schema gap: no read intersects circuits with an HFTD tier or "
        "measures HFTD polygon area."
    ),
    "unsupported_air_quality": "Router keyword. v3 off_topic groups this under other_off_topic.",
    "unsupported_evacuation": "Router keyword. v3 off_topic groups this under other_off_topic.",
    "unsupported_translation": "Router keyword. v3 off_topic groups this under other_off_topic.",
    "unsupported_personnel": "Router keyword. v3 off_topic groups this under other_off_topic.",
    "unsupported_satellite": "Router keyword. v3 off_topic groups this under other_off_topic.",
    "unsupported_leadership": "Router keyword. v3 off_topic groups this under other_off_topic.",
    "unsupported_future_prediction": (
        "Router phrasing and year arithmetic: a forward modal, expectation, or "
        "forecast of events or counts, with no risk object. Kept out of the Jev "
        "context for now because a new policy sentence changes the payload."
    ),
    "medical_exposure_missing_year": (
        "The medical-baseline, life-support, and medically-vulnerable phrases "
        "are matched in routing.py. There is no separate Jev fact for that phrase set yet."
    ),
    "series_mode_missing_year": (
        "Yearly, seasonal, cumulative-acres, customer-event, and regional "
        "phrases are matched in routing.py. There is no separate Jev fact for that phrase set yet."
    ),
    "series_mode_missing_dataset": (
        "Yearly and seasonal charts require a named CPUC, EPSS, or CAL FIRE "
        "dataset. That check stays in routing.py."
    ),
}

OFF_TOPIC_RULES = {
    "cpz": "unsupported_cpz",
    "cost_or_budget": "unsupported_cost",
    "optimization_or_scheduling": "unsupported_optimization",
    "damage_or_loss": "unsupported_damage",
    "live_or_web": "unsupported_live_web",
}


# Intents where an other_measure answer means the warehouse cannot return
# what was asked. Map, territory_boundary, and spatial_context are never gated.
MEASURE_GATED_INTENTS = frozenset({"count", "rank", "compare", "trend", "records_list"})


@dataclass
class JevFacts:
    has_time_scope: float = 0.0
    vague_time: float = 0.0
    future_time: float = 0.0
    names_specific_place: float = 0.0
    vague_proximity: float = 0.0
    broad_region: float = 0.0
    asks_risk: float = 0.0
    names_risk_metric: float = 0.0
    prompt_injection: float = 0.0
    off_topic: str | None = "on_topic"
    intent: str | None = None
    dataset: str | None = None
    is_multi_intent: float = 0.0
    county: str | None = "none"
    utilities: dict[str, float] = field(default_factory=dict)
    rank_dimension: str | None = "none"
    mentions_multiple_datasets: float = 0.0
    measure: str | None = None
    clarify_reason: str | None = None
    clarify_reason_confidence: float = 0.0
    live_web_probability: float = 0.0
    threshold: float = 0.5


@dataclass
class DerivedOutcome:
    disposition: str
    clarify_reason: str | None
    unsupported_topic: str | None
    trace: list[str]
    confidence: float | None
    deciding_margins: dict[str, float]


def facts_from_answers(answers: dict[str, Any], *, threshold: float = 0.5) -> JevFacts:
    """Build JevFacts from Answer objects or JSON answer dicts."""

    def raw(name: str) -> Any:
        answer = answers.get(name)
        if answer is None:
            return None
        if isinstance(answer, dict):
            return answer.get("value")
        return getattr(answer, "value", None)

    def noul(name: str) -> float:
        value = raw(name)
        return float(value) if isinstance(value, (int, float)) else 0.0

    def choice(name: str) -> str | None:
        value = raw(name)
        return value if isinstance(value, str) else None

    def probabilities(name: str) -> dict[str, float]:
        answer = answers.get(name)
        if answer is None:
            return {}
        if isinstance(answer, dict):
            found = answer.get("probabilities") or {}
        else:
            found = getattr(answer, "probabilities", None) or {}
        return {str(key): float(value) for key, value in found.items()}

    def confidence(name: str) -> float:
        answer = answers.get(name)
        if answer is None:
            return 0.0
        if isinstance(answer, dict):
            value = answer.get("confidence")
        else:
            value = getattr(answer, "confidence", None)
        return float(value) if isinstance(value, (int, float)) else 0.0

    utilities = {
        key.removeprefix("utility_"): noul(key)
        for key in answers
        if str(key).startswith("utility_")
    }
    return JevFacts(
        has_time_scope=noul("has_time_scope"),
        vague_time=noul("vague_time"),
        future_time=noul("future_time"),
        names_specific_place=noul("names_specific_place"),
        vague_proximity=noul("vague_proximity"),
        broad_region=noul("broad_region"),
        asks_risk=noul("asks_risk"),
        names_risk_metric=noul("names_risk_metric"),
        prompt_injection=noul("prompt_injection"),
        off_topic=choice("off_topic") or "on_topic",
        intent=choice("intent"),
        dataset=choice("dataset"),
        is_multi_intent=noul("is_multi_intent"),
        county=choice("county") or "none",
        utilities=utilities,
        rank_dimension=choice("rank_dimension") or "none",
        mentions_multiple_datasets=noul("mentions_multiple_datasets"),
        measure=choice("measure"),
        clarify_reason=choice("clarify_reason"),
        clarify_reason_confidence=confidence("clarify_reason"),
        live_web_probability=probabilities("off_topic").get("live_or_web", 0.0),
        threshold=threshold,
    )


def _yes(value: float, threshold: float) -> bool:
    return value >= threshold


def _margin(value: float, threshold: float) -> float:
    return abs(value - threshold)


def derive_outcome(
    facts: JevFacts,
    *,
    question: str = "",
    today: date | None = None,
) -> DerivedOutcome:
    """Apply policies in route_question order where the order changes the answer."""
    threshold = facts.threshold
    margins: dict[str, float] = {}
    trace: list[str] = []

    def hit(rule_id: str, *fact_names: str) -> DerivedOutcome:
        trace.append(rule_id)
        for name in fact_names:
            raw = getattr(facts, name, None)
            if isinstance(raw, float):
                margins[name] = _margin(raw, threshold)
        disposition = "clarify"
        clarify = rule_id
        topic = None
        if rule_id.startswith("unsupported") or rule_id == "prompt_injection":
            disposition = "unsupported"
            clarify = None
            topic = None if rule_id == "prompt_injection" else rule_id
        confidence = min(margins.values()) if margins else None
        return DerivedOutcome(disposition, clarify, topic, trace, confidence, margins)

    if _yes(facts.prompt_injection, threshold):
        return hit("prompt_injection", "prompt_injection")
    if facts.off_topic == "live_or_web" and _live_web_is_missing_location(facts, threshold):
        return hit("missing_location", "vague_proximity", "names_specific_place")
    topic_rule = OFF_TOPIC_RULES.get(facts.off_topic or "")
    if topic_rule:
        trace.append(topic_rule)
        return DerivedOutcome("unsupported", None, topic_rule, trace, None, {})
    if facts.off_topic == "other_off_topic":
        trace.append("other_off_topic")
        return DerivedOutcome("unsupported", None, None, trace, None, {})

    if facts.measure == "other_measure" and (facts.intent or "") in MEASURE_GATED_INTENTS:
        # A map, a territory boundary, or spatial context names no warehouse
        # measure, so other_measure never declines those intents.
        if re.search(
            r"\b(?:worst|most dangerous|safest|riskiest|most risky|highest risk)\b",
            question or "",
            re.I,
        ):
            trace.append("measure_is_judgment")
            return hit("ambiguous_risk_metric")
        trace.append("unsupported_other_measure")
        return DerivedOutcome(
            "unsupported", None, "unsupported_other_measure", trace, None, {}
        )
    if question and _RISKIEST_METRIC.search(question.lower()):
        trace.append("ambiguous_risk_metric")
        return DerivedOutcome("clarify", "ambiguous_risk_metric", None, trace, None, {})
    if _yes(facts.vague_proximity, threshold) and not _yes(facts.names_specific_place, threshold):
        return hit("missing_location", "vague_proximity", "names_specific_place")
    if _yes(facts.vague_proximity, threshold):
        return hit("undefined_spatial_scope", "vague_proximity")
    if _yes(facts.broad_region, threshold):
        return hit("undefined_region", "broad_region")

    resolved = resolve_time(question, today=today) if question else None
    if (
        not _time_known(resolved)
        and _yes(facts.vague_time, threshold)
        and not _yes(facts.has_time_scope, threshold)
    ):
        return hit("ambiguous_relative_time", "vague_time", "has_time_scope")

    if resolved is not None and getattr(resolved, "status", None) == "out_of_coverage":
        trace.append("time_out_of_coverage")
        return DerivedOutcome("clarify", "time_out_of_coverage", None, trace, None, {})
    if _yes(facts.asks_risk, threshold) and (
        _yes(facts.future_time, threshold)
        or _risk_date_after_coverage(question)
    ):
        return hit("risk_future_date", "asks_risk", "future_time")
    if _yes(facts.asks_risk, threshold) and not _named_place(facts, threshold):
        return hit("risk_missing_place", "asks_risk", "names_specific_place")
    if _yes(facts.asks_risk, threshold) and _lacks_year(facts, resolved, threshold):
        return hit("forecast_missing_date", "asks_risk", "has_time_scope")

    ranking_rule = _ranking_rule(facts, question, threshold, resolved)
    if ranking_rule:
        if ranking_rule.startswith("unsupported"):
            trace.append(ranking_rule)
            return DerivedOutcome("unsupported", None, ranking_rule, trace, None, {})
        return hit(ranking_rule, "has_time_scope", "mentions_multiple_datasets")

    if (
        facts.county
        and facts.county != "none"
        and facts.dataset not in COUNTY_CAPABLE
        and facts.intent == "count"
    ):
        trace.append("unexpressable_county_filter")
        return DerivedOutcome("unsupported", None, "unexpressable_county_filter", trace, None, {})
    if (
        _yes(facts.asks_risk, threshold)
        and facts.county not in (None, "none")
        and any(_yes(value, threshold) for value in facts.utilities.values())
    ):
        return hit("ambiguous_risk_place", "asks_risk")

    intent = facts.intent or ""
    missing_year = {
        "map_plus_trend": "map_plus_trend_missing_year",
        "map": "map_missing_year",
        "trend": "trend_missing_year",
        "spatial_context": "spatial_missing_year",
        "count": "records_missing_year",
        "records_list": "records_missing_year",
        "compare": "records_missing_year",
    }
    coordinate_lookup = intent == "spatial_context" and bool(question) and _coords(question) is not None
    if (
        intent in missing_year
        and not coordinate_lookup
        and _lacks_year(facts, resolved, threshold)
        and not (intent == "map" and facts.dataset == "hftd")
    ):
        return hit(missing_year[intent], "has_time_scope")

    if _yes(facts.is_multi_intent, threshold):
        trace.append("multi_intent_count_and_trend")
        margins["is_multi_intent"] = _margin(facts.is_multi_intent, threshold)
        return DerivedOutcome("answer", None, None, trace, margins["is_multi_intent"], margins)

    trace.append("answer")
    return DerivedOutcome("answer", None, None, trace, None, {})


def _risk_date_after_coverage(question: str) -> bool:
    if not question:
        return False
    resolved = resolve_time(question)
    end = resolved.end_date or (
        f"{resolved.year}-12-31" if resolved.year else None
    )
    if not end:
        return False
    try:
        return date.fromisoformat(end) > RISK_COVERAGE_END
    except ValueError:
        return False


def _live_web_is_missing_location(facts: JevFacts, threshold: float) -> bool:
    """A near-me question, or a weak live-web label contradicted by the clarify Choice."""
    near_unplaced = _yes(facts.vague_proximity, threshold) and not _yes(
        facts.names_specific_place, threshold
    )
    choice_says_missing = (
        facts.clarify_reason == "missing_location"
        and facts.clarify_reason_confidence >= 0.7
        and facts.live_web_probability < 0.85
    )
    return near_unplaced or choice_says_missing


def _time_known(resolved: Any) -> bool:
    return resolved is not None and getattr(resolved, "status", None) in _KNOWN_TIME


def _lacks_year(facts: JevFacts, resolved: Any, threshold: float) -> bool:
    """True when the year gates should clarify.

    A year resolve_time actually parsed wins over the Noul. A weak Noul yes
    does not override a question the parser finds no time in. A strong Noul
    yes still counts.
    """
    if _time_known(resolved):
        return False
    if not _yes(facts.has_time_scope, threshold):
        return True
    if resolved is not None and resolved.status == "none" and facts.has_time_scope < 0.8:
        return True
    return False


def _named_place(facts: JevFacts, threshold: float) -> bool:
    if _yes(facts.names_specific_place, threshold):
        return True
    return any(_yes(value, threshold) for value in facts.utilities.values())


def _ranking_rule(
    facts: JevFacts,
    question: str,
    threshold: float,
    resolved: Any = None,
) -> str | None:
    """Mirror _route_ranking using dataset, rank_dimension, and a code-side metric."""
    dimension = facts.rank_dimension if facts.rank_dimension not in (None, "none") else None
    if (
        dimension is not None
        and _yes(facts.mentions_multiple_datasets, threshold)
        and facts.intent in {"rank", "multi_intent"}
    ):
        return "unsupported_rank_cross_dataset"
    if facts.intent != "rank":
        return None
    dataset = facts.dataset if facts.dataset not in (None, "none", "multiple") else None
    if _yes(facts.mentions_multiple_datasets, threshold) or facts.dataset == "multiple":
        return "unsupported_rank_cross_dataset"
    if dimension == "state" or facts.dataset == "us_ignitions":
        return "unsupported_rank_us_state"
    if dimension == "utility" and dataset == "epss_outages":
        return "unsupported_rank_epss_utility"
    if dimension in {"cell", "division"}:
        return "unsupported_ranking"
    if dataset is None or dimension is None:
        return "ranking_missing_slots"
    metric = _rank_metric((question or "").lower(), dataset)
    if (dataset, dimension, metric) not in ALLOWED_RANK_TRIPLES:
        return "unsupported_ranking"
    if _lacks_year(facts, resolved, threshold):
        return "ranking_missing_year"
    if dimension == "county" and facts.county not in (None, "none"):
        return "ranking_county_contradiction"
    return None


def covered_rule_ids() -> set[str]:
    """Rule ids this module can emit, excluding the extra prompt_injection fact."""
    return {
        "ambiguous_risk_metric",
        "missing_location",
        "undefined_spatial_scope",
        "undefined_region",
        "ambiguous_relative_time",
        "time_out_of_coverage",
        "risk_future_date",
        "risk_missing_place",
        "forecast_missing_date",
        "unsupported_rank_cross_dataset",
        "unsupported_rank_us_state",
        "unsupported_rank_epss_utility",
        "unsupported_ranking",
        "unsupported_other_measure",
        "unexpressable_county_filter",
        "ambiguous_risk_place",
        "map_plus_trend_missing_year",
        "map_missing_year",
        "trend_missing_year",
        "spatial_missing_year",
        "records_missing_year",
        "ranking_missing_year",
        "ranking_missing_slots",
        "ranking_county_contradiction",
        *OFF_TOPIC_RULES.values(),
    }


PolicyFn = Callable[[JevFacts], Any]
