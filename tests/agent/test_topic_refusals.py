"""Issue #97: topic keyword refusals are Jev's off_topic judgment in decide mode.

Live or real-time data and future prediction stay router backstops. Every other
unsupported-topic keyword (routing.TOPIC_JUDGMENT_RULES, plus web-search wording)
is decided by Jev's off_topic fact at or above the decline gate. The advice rule
stays with the router: off_topic has no advice option. Below the gate,
on an error, or with decide off, the keyword rule stands. No Jev calls: every
answer here is synthetic.
"""

from __future__ import annotations

from datetime import date

import pytest

from services.agent.decisions.backend import Answer, DecisionResult
from services.agent.decisions.decide_mode import (
    BACKSTOP_RULES,
    decide_from_answers,
    decide_live,
    exemption,
    is_topic_judgment,
    jev_calls,
)
from services.agent.decisions.provenance import decision_source
from services.agent.routing import TOPIC_JUDGMENT_RULES, route_question

TODAY = date(2026, 9, 24)


def _noul(value):
    return Answer(kind="noul", value=value, confidence=None, probabilities=None)


def _choice(value, confidence=0.95):
    return Answer(kind="choice", value=value, confidence=confidence, probabilities={value: confidence})


def _facts(off_topic="on_topic", confidence=0.95, **overrides):
    """A clean in-scope data question in Jev's answer space, with one off_topic reading."""
    base = {
        "has_time_scope": _noul(0.97),
        "vague_time": _noul(0.03),
        "future_time": _noul(0.03),
        "names_specific_place": _noul(0.8),
        "vague_proximity": _noul(0.02),
        "broad_region": _noul(0.03),
        "asks_risk": _noul(0.03),
        "names_risk_metric": _noul(0.1),
        "prompt_injection": _noul(0.02),
        "is_multi_intent": _noul(0.05),
        "mentions_multiple_datasets": _noul(0.05),
        "off_topic": _choice(off_topic, confidence),
        "intent": _choice("count"),
        "dataset": _choice("cpuc_ignitions"),
        "measure": _choice("count"),
        "county": _choice("none"),
        "rank_dimension": _choice("none"),
    }
    base.update(overrides)
    return base


class FakeBackend:
    def __init__(self, answers=None, error=None, raises=None):
        self.answers = answers or {}
        self.error = error
        self.raises = raises
        self.calls = 0

    def evaluate(self, state, questions, *, request_id, question_hash, capture=None):
        self.calls += 1
        if self.raises:
            raise self.raises
        if self.error:
            return DecisionResult({}, None, 1.0, None, {}, request_id, question_hash, error=self.error)
        subset = {name: value for name, value in self.answers.items() if name in questions}
        return DecisionResult(subset, "jev-test", 1.0, 10, {}, request_id, question_hash)


# The five passing mentions named in #97, as in-scope questions, with the route
# each takes once the keyword is set aside.
PASSING_MENTIONS = [
    ("After the budget meeting, how many PG&E ignitions were there in 2022?", "unsupported_cost", "filtered_records"),
    ("The CEO testified last week. How many SCE ignitions were in Tier 3 HFTD in 2022?", "unsupported_leadership", "filtered_records"),
    ("Our schedule is tight: how many CAL FIRE incidents were there in Butte County in 2020?", "unsupported_optimization", "filtered_records"),
    ("For a cost report, show a map of PG&E ignitions in 2022.", "unsupported_cost", "map"),
    ("Ahead of the price cap hearing, how many PSPS events were there in 2019?", "unsupported_cost", "filtered_records"),
]

# Questions whose subject is the unsupported topic, with the off_topic option
# that names it and the keyword rule the router refuses with.
GENUINE_TOPICS = [
    ("Who is the CEO of PG&E?", "other_off_topic", "unsupported_leadership"),
    ("What was PG&E's wildfire mitigation budget in 2022?", "cost_or_budget", "unsupported_cost"),
    ("Optimize the vegetation crew schedule for Butte County.", "optimization_or_scheduling", "unsupported_optimization"),
    ("What was the property damage from CAL FIRE incidents in 2018?", "damage_or_loss", "unsupported_damage"),
    ("Which circuit protection zones had outages in 2023?", "cpz", "unsupported_cpz"),
    ("What air quality readings were there during the 2020 fires?", "other_off_topic", "unsupported_air_quality"),
    ("Do a web search for PG&E wildfire news.", "live_or_web", "unsupported_live_web"),
]


@pytest.mark.parametrize("question,keyword_rule,answer_rule", PASSING_MENTIONS)
def test_off_mode_still_refuses_a_passing_mention(question, keyword_rule, answer_rule):
    decision = route_question(question)
    assert (decision.path, decision.rule) == ("unsupported", keyword_rule)
    # Without the question, as in any caller that does not pass it, the keyword
    # refusal keeps its old backstop exemption.
    assert exemption(decision) == "backstop"


@pytest.mark.parametrize("question,keyword_rule,answer_rule", PASSING_MENTIONS)
def test_passing_mention_is_answered_when_jev_reads_on_topic(question, keyword_rule, answer_rule):
    decision = route_question(question)
    assert is_topic_judgment(decision, question) and exemption(decision, question) is None
    result = decide_from_answers(question, decision, _facts("on_topic", 0.9), gate=0.8, today=TODAY)
    assert (result.decision.path, result.decision.rule) == ("deterministic", answer_rule)
    assert result.decision.tool_calls
    assert (result.winner, result.why) == ("jev", "on_topic")
    assert result.topic_keyword_rule == keyword_rule
    assert (result.router_path, result.router_rule) == ("unsupported", keyword_rule)
    assert result.jev_confidence == pytest.approx(0.9)
    source = decision_source(
        path=result.decision.path,
        rule=result.decision.rule,
        slots={"jev_decide": {"winner": result.winner, "why": result.why, "router_rule": result.router_rule}},
        jev_mode="decide",
    )
    assert source["source"] == "jev"


def test_on_topic_at_exactly_the_gate_decides():
    question, _rule, answer_rule = PASSING_MENTIONS[0]
    result = decide_from_answers(question, route_question(question), _facts("on_topic", 0.8), gate=0.8, today=TODAY)
    assert result.decision.rule == answer_rule


@pytest.mark.parametrize("question,option,keyword_rule", GENUINE_TOPICS)
def test_genuine_topic_is_refused_when_jev_reads_off_topic(question, option, keyword_rule):
    decision = route_question(question)
    assert (decision.path, decision.rule) == ("unsupported", keyword_rule)
    assert is_topic_judgment(decision, question)
    result = decide_from_answers(question, decision, _facts(option, 0.9), gate=0.8, today=TODAY)
    assert result.decision.path == "unsupported"
    assert result.winner in {"router", "jev"} and result.why in {"agree", "gate"}
    # The router's refusal wording stands (both refused).
    assert result.decision.answer == decision.answer


@pytest.mark.parametrize("question,option,keyword_rule", GENUINE_TOPICS)
@pytest.mark.parametrize("reading", ["on_topic", "off_topic"])
def test_below_the_gate_the_keyword_refusal_stands(question, option, keyword_rule, reading):
    decision = route_question(question)
    choice = "on_topic" if reading == "on_topic" else option
    result = decide_from_answers(question, decision, _facts(choice, 0.6), gate=0.8, today=TODAY)
    assert result.decision is decision
    assert (result.winner, result.why) == ("router", "below_gate")
    assert result.jev_confidence == pytest.approx(0.6)


@pytest.mark.parametrize("question,keyword_rule,answer_rule", PASSING_MENTIONS)
def test_passing_mention_below_the_gate_falls_back_to_the_keyword(question, keyword_rule, answer_rule):
    decision = route_question(question)
    result = decide_from_answers(question, decision, _facts("on_topic", 0.79), gate=0.8, today=TODAY)
    assert (result.decision.path, result.decision.rule) == ("unsupported", keyword_rule)
    assert (result.winner, result.why) == ("router", "below_gate")


def test_missing_off_topic_answer_falls_back_to_the_keyword():
    question, keyword_rule, _ = PASSING_MENTIONS[0]
    answers = _facts("on_topic")
    del answers["off_topic"]
    result = decide_from_answers(question, route_question(question), answers, gate=0.8, today=TODAY)
    assert (result.decision.rule, result.why) == (keyword_rule, "below_gate")


@pytest.mark.parametrize("question,keyword_rule,answer_rule", PASSING_MENTIONS)
def test_jev_error_falls_back_to_the_keyword(question, keyword_rule, answer_rule):
    decision = route_question(question)
    result = decide_from_answers(question, decision, None, gate=0.8, error="HTTP 402", today=TODAY)
    assert result.decision is decision and (result.winner, result.why) == ("router", "error")


@pytest.mark.parametrize(
    "backend,why",
    [
        (FakeBackend(error="HTTP 402"), "error"),
        (FakeBackend(raises=RuntimeError("boom")), "error"),
        (FakeBackend(_facts("on_topic", 0.5)), "below_gate"),
    ],
)
def test_runtime_fallbacks_keep_the_keyword_refusal(backend, why):
    question, keyword_rule, _ = PASSING_MENTIONS[1]
    decision = route_question(question)
    result = decide_live(question, decision, backend=backend, gate=0.8, today=TODAY)
    assert backend.calls >= 1  # a topic keyword no longer skips Jev
    assert (result.decision.path, result.decision.rule) == ("unsupported", keyword_rule)
    assert result.why == why


def test_runtime_asks_jev_the_unchanged_calls_and_answers_on_topic():
    question, keyword_rule, answer_rule = PASSING_MENTIONS[2]
    backend = FakeBackend(_facts("on_topic", 0.92))
    result = decide_live(question, route_question(question), backend=backend, gate=0.8, today=TODAY)
    # The same v3_hybrid disposition calls as any other question: the payload is unchanged.
    assert backend.calls == len(jev_calls(question, TODAY.isoformat()))
    assert result.decision.rule == answer_rule and result.why == "on_topic"
    assert result.log_record(question, "r1")["topic_keyword_rule"] == keyword_rule


@pytest.mark.parametrize(
    "question,keyword_rule,backstop",
    [
        ("For the budget memo, which fires are burning right now?", "unsupported_cost", "unsupported_live_web"),
        ("How many PG&E ignitions will there be next year? It's for the budget.", "unsupported_cost", "unsupported_future_prediction"),
    ],
)
def test_on_topic_never_lifts_a_hard_backstop_behind_the_keyword(question, keyword_rule, backstop):
    decision = route_question(question)
    assert decision.rule == keyword_rule
    result = decide_from_answers(question, decision, _facts("on_topic", 0.99), gate=0.8, today=TODAY)
    assert (result.decision.path, result.decision.rule) == ("unsupported", backstop)
    assert (result.winner, result.why) == ("router", "backstop")
    assert result.topic_keyword_rule == keyword_rule
    source = decision_source(
        path=result.decision.path,
        rule=result.decision.rule,
        slots={"jev_decide": {"winner": result.winner, "why": result.why, "router_rule": result.router_rule}},
        jev_mode="decide",
    )
    assert source == {"source": "backstop", "rule": backstop, "mode": "decide"}


@pytest.mark.parametrize(
    "question",
    [
        "What wildfires are burning right now?",
        "Show me today's fires on a map.",
        "Search the web for live fires in Butte County.",
    ],
)
def test_live_wording_is_a_hard_backstop_even_with_web_wording(question):
    decision = route_question(question)
    assert decision.rule == "unsupported_live_web"
    assert not is_topic_judgment(decision, question)
    assert exemption(decision, question) == "backstop"
    result = decide_from_answers(question, decision, _facts("on_topic", 0.99), gate=0.8, today=TODAY)
    assert result.decision is decision and result.why == "backstop"


def test_web_search_wording_alone_is_a_topic_judgment():
    question = "Without doing a web search, what does the CPUC ignition data say about PG&E in 2019?"
    decision = route_question(question)
    assert decision.rule == "unsupported_live_web"
    assert is_topic_judgment(decision, question)
    result = decide_from_answers(question, decision, _facts("on_topic", 0.9), gate=0.8, today=TODAY)
    assert result.decision.path != "unsupported"
    refused = decide_from_answers(question, decision, _facts("live_or_web", 0.9), gate=0.8, today=TODAY)
    assert (refused.decision.path, refused.decision.rule) == ("unsupported", "unsupported_live_web")


def test_future_prediction_stays_a_backstop():
    question = "Predict which utility will have the most wildfire ignitions in 2027."
    decision = route_question(question)
    assert decision.rule in BACKSTOP_RULES
    assert exemption(decision, question) == "backstop"


def test_skip_topic_judgments_changes_only_topic_keyword_routes():
    for question, _rule, answer_rule in PASSING_MENTIONS:
        assert route_question(question, skip_topic_judgments=True).rule == answer_rule
    for question in ("How many PG&E utility-attributed ignitions were there in 2024?", "What wildfires are burning right now?"):
        plain, skipped = route_question(question), route_question(question, skip_topic_judgments=True)
        assert (plain.path, plain.rule) == (skipped.path, skipped.rule)


def test_topic_judgment_rules_cover_the_named_topics():
    for rule in (
        "unsupported_cost",
        "unsupported_leadership",
        "unsupported_optimization",
        "unsupported_damage",
        "unsupported_cpz",
    ):
        assert rule in TOPIC_JUDGMENT_RULES
    assert "unsupported_live_web" not in TOPIC_JUDGMENT_RULES
    assert "unsupported_future_prediction" not in TOPIC_JUDGMENT_RULES


@pytest.mark.parametrize(
    "question",
    [
        "Should PG&E de-energize more circuits in 2023?",
        "How should PG&E optimize its vegetation management schedule?",
        # Found in the v3 replay: Jev reads this on_topic at 0.99 because no
        # off_topic option names advice.
        "Which utility should the CPUC penalize based on its wildfire record?",
    ],
)
def test_advice_stays_with_the_router_even_when_jev_reads_on_topic(question):
    decision = route_question(question)
    assert decision.rule == "unsupported_optimization"
    assert not is_topic_judgment(decision, question)
    assert exemption(decision, question) == "regex_only"
    result = decide_from_answers(question, decision, _facts("on_topic", 0.99), gate=0.8, today=TODAY)
    assert result.decision is decision and (result.winner, result.why) == ("router", "regex_only")


def test_a_keyword_in_front_of_advice_still_refuses_as_advice():
    question = "After the budget meeting, should PG&E de-energize more circuits?"
    decision = route_question(question)
    assert decision.rule == "unsupported_cost" and is_topic_judgment(decision, question)
    result = decide_from_answers(question, decision, _facts("on_topic", 0.99), gate=0.8, today=TODAY)
    assert (result.decision.path, result.decision.rule) == ("unsupported", "unsupported_optimization")
    assert result.why == "regex_only" and result.topic_keyword_rule == "unsupported_cost"


def test_off_mode_provenance_of_a_topic_refusal_is_still_backstop():
    question = PASSING_MENTIONS[0][0]
    decision = route_question(question)
    source = decision_source(path=decision.path, rule=decision.rule, slots={}, jev_mode="off")
    assert source == {"source": "backstop", "rule": "unsupported_cost", "mode": "off"}


def test_decide_mode_fallback_provenance_says_below_gate():
    question = PASSING_MENTIONS[0][0]
    decision = route_question(question)
    result = decide_from_answers(question, decision, _facts("on_topic", 0.6), gate=0.8, today=TODAY)
    slots = {"jev_decide": {"winner": result.winner, "why": result.why, "router_rule": result.router_rule,
                            "jev_disposition": result.jev_disposition, "jev_confidence": result.jev_confidence}}
    source = decision_source(path=decision.path, rule=decision.rule, slots=slots, jev_mode="decide")
    assert source["source"] == "router" and source["why"] == "jev_below_gate"
