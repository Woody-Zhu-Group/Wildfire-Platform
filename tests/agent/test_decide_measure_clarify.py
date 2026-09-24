"""Decide mode: which measure a ranking or comparison orders by is Jev's judgment.

Production answered "Which utility had the most dangerous fires in 2023?" with
the generic ranking_missing_slots clarification. Two general rules cover it:

1. A rank or compare intent whose measure Jev reads as other_measure asks which
   of the registry's measures to use for that grouping (jev_policy). There is no
   word list: whether a phrase names a measure is Jev's call.
2. When the router and Jev both clarify and the router's rule is generic
   (GENERIC_ROUTER_RULES), Jev's more specific reason is shown, composed with the
   router's slots through complete_clarification (decide_mode).

The router itself routes these questions exactly as on main.
"""

from __future__ import annotations

import pytest

from services.agent.clarify_missing import complete_clarification
from services.agent.decisions.decide_mode import GENERIC_ROUTER_RULES, decide_from_answers
from services.agent.routing import route_question
from services.shared.dataset_registry import COMPARE_MEASURES, MEASURE_LABELS, RANK_MEASURES
from tests.agent.test_jev_decide import _answer_facts, _choice, _noul

REPORTED = "Which utility had the most dangerous fires in 2023?"


def _other_measure(intent: str, rank_dimension: str = "none", **overrides):
    facts = {
        "intent": _choice(intent),
        "measure": _choice("other_measure"),
        "dataset": _choice("none"),
        "rank_dimension": _choice(rank_dimension),
    }
    return _answer_facts(**{**facts, **overrides})


@pytest.mark.parametrize(
    "question",
    [
        REPORTED,
        # Paraphrases written before the fix; the router routes them three
        # different ways on main (generic clarification, model, deterministic).
        "Which utility had the scariest fires in 2023?",
        "In 2023, which utility's ignitions were the most concerning?",
        "What utility had the most alarming wildfire record in 2023?",
    ],
)
def test_a_ranking_by_no_measure_asks_which_registry_measure(question):
    decision = route_question(question)
    result = decide_from_answers(question, decision, _other_measure("rank", "utility"))
    assert result.winner == "jev" and result.wording == "jev", (question, result.why)
    final = result.decision
    assert (final.path, final.rule) == ("clarification", "ambiguous_risk_metric")
    assert final.tool_calls == []
    text = final.answer
    assert text.startswith("That question does not name a measure in the data.")
    assert f"To rank utilities for 2023, I can use {MEASURE_LABELS['ignition_count']}." in text
    for measure in COMPARE_MEASURES["utility"]:
        assert MEASURE_LABELS[measure] in text, measure
    assert "Damage, fatalities, and destroyed structures are not in the data." in text
    assert text.endswith("Which measure should I use?")
    assert "Which dataset and which grouping" not in text


def test_a_comparison_by_no_measure_lists_what_the_named_utilities_have():
    question = "Which is worse for fires, SCE or PacifiCorp?"
    result = decide_from_answers(question, route_question(question), _other_measure("compare"))
    text = result.decision.answer
    assert result.decision.rule == "ambiguous_risk_metric"
    assert "To compare SCE and PacifiCorp, I can use " in text
    # EPSS is PG&E only, and no period was named.
    assert "EPSS" not in text
    assert text.endswith("Which measure should I use, and for which year or date range?")


def test_without_a_grouping_every_scope_is_listed_with_its_measures():
    question = "Was 2024 better or worse?"
    result = decide_from_answers(question, route_question(question), _other_measure("compare"))
    text = result.decision.answer
    assert result.decision.rule == "ambiguous_risk_metric"
    assert "For 2024, I can compare utilities by " in text
    assert "; counties by " in text
    for measure in COMPARE_MEASURES["utility"]:
        assert MEASURE_LABELS[measure] in text, measure


def test_a_county_ranking_lists_the_county_measures():
    question = "Which county was hit hardest by wildfires in 2020?"
    result = decide_from_answers(question, route_question(question), _other_measure("rank", "county"))
    text = result.decision.answer
    for measure in RANK_MEASURES["county"]:
        assert MEASURE_LABELS[measure] in text, measure
    assert "PSPS" not in text


def test_a_named_measure_keeps_the_router_route():
    # The review's regression: a real measure in unusual words answers as on main.
    question = "Which utility had the highest tally of ignitions in 2023?"
    decision = route_question(question)
    assert (decision.path, decision.rule) == ("deterministic", "ranked_records")
    facts = _answer_facts(
        intent=_choice("rank"), rank_dimension=_choice("utility"), dataset=_choice("cpuc_ignitions")
    )
    result = decide_from_answers(question, decision, facts)
    assert result.winner == "router" and result.decision is decision


def test_below_the_gate_the_router_stands():
    decision = route_question(REPORTED)
    answers = _other_measure("rank", "utility", measure=_choice("other_measure", 0.6))
    result = decide_from_answers(REPORTED, decision, answers)
    assert result.winner == "router" and result.why == "below_gate"
    assert result.decision.rule == "ranking_missing_slots"


def test_a_generic_router_clarification_yields_to_jevs_specific_reason():
    """General rule: both clarify, router rule generic, Jev's reason is shown."""
    assert GENERIC_ROUTER_RULES == {"ranking_missing_slots"}
    question = "Which utility had the most dangerous fires?"
    decision = route_question(question)
    assert decision.rule == "ranking_missing_slots"
    # A different specific reason than the measure one: the ranking lacks a year.
    answers = _answer_facts(
        intent=_choice("rank"),
        rank_dimension=_choice("utility"),
        dataset=_choice("cpuc_ignitions"),
        has_time_scope=_noul(0.03),
    )
    result = decide_from_answers(question, decision, answers)
    assert (result.winner, result.why, result.wording) == ("jev", "gate", "jev")
    assert result.decision.rule == "ranking_missing_year"
    expected = complete_clarification(
        "ranking_missing_year",
        question,
        decision.slots,
        "What year or date range should the ranking cover?",
    )
    assert result.decision.answer == expected


def test_a_specific_router_clarification_keeps_its_wording():
    # Not generic: when both clarify, the router's text and rule stand as before.
    question = "Which county had the most CPUC ignitions?"
    decision = route_question(question)
    assert decision.path == "clarification" and decision.rule not in GENERIC_ROUTER_RULES
    answers = _other_measure("rank", "county")
    result = decide_from_answers(question, decision, answers)
    assert result.wording == "router"
    assert result.decision is decision
