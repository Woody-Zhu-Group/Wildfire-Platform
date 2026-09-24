"""Invariant: a clarification asks for every item the question is missing, whichever rule's text is shown.

Decide mode can show Jev's clarification instead of the router's (the generic
ranking_missing_slots rule, GENERIC_ROUTER_RULES). The missing items (a year or
date, a dataset, a ranking grouping, a place) come from the question and the
router's slots in complete_clarification, never from the rule, so swapping the
base text cannot drop one. The options offered come from the registry for the
task: rankings from RANK_MEASURES, comparisons from COMPARE_MEASURES.

The expected items below are written by hand from each question, and "asks for"
is read from the shown text with this file's own patterns.
"""

from __future__ import annotations

import json
import re
from datetime import date

import pytest

from services.agent.clarify_missing import RULE_ITEM, complete_clarification, missing_items
from services.agent.decisions.decide_mode import _REASON_TEXT, decide_from_answers
from services.agent.eval.jev_decide_replay import STORE, _answers
from services.agent.routing import route_question
from services.shared.dataset_registry import (
    CLARIFY_DATASET_LABELS,
    COMPARE_MEASURES,
    MEASURE_DATASETS,
    RANK_MEASURES,
)
from tests.agent.test_jev_decide import _answer_facts, _choice, _noul

ASKS = {
    "year": re.compile(r"\byear\b|\bdate range\b|\btime period\b", re.I),
    "date": re.compile(r"\bcalendar day\b|\bpast date\b", re.I),
    "dataset": re.compile(r"\bdataset\b|\bmeasure\b", re.I),
    "grouping": re.compile(r"\bgrouping\b", re.I),
    "place": re.compile(
        r"\bplace\b|\blatitude\b|\bcoordinates\b|\bbounding box\b|\bradius\b|\bpolygon\b|\bregion\b"
        r"|\bwhich (?:cell|county)\b",
        re.I,
    ),
    "measure": re.compile(r"\bmeasure\b", re.I),
}

# Question -> the items it is missing, by hand, whatever rule fires.
QUESTIONS: dict[str, set[str]] = {
    "Which one had the most ignitions?": {"year", "grouping"},
    "Which had the most CAL FIRE incidents?": {"year", "grouping"},
    "Which had the most fires?": {"year", "dataset", "grouping"},
    "Which county had the most fires?": {"year", "dataset"},
    "Rank the counties by wildfire incidents.": {"year", "dataset"},
    "what counties had the most utility-caused ignitions?": {"year"},
    "Which had the most CAL FIRE incidents in 2023?": {"grouping"},
    "Which county had the most CPUC ignitions?": {"year"},
    "Which utility had the most dangerous fires?": {"year", "dataset"},
    "Show a seasonal chart": {"year", "dataset"},
    "Show recent fires near me.": {"year", "dataset"},
    "How risky was it?": {"place", "date"},
    "Compare Butte and Shasta counties.": {"year", "dataset"},
    "How many CPUC ignitions were there in 2023?": set(),
}

_FALLBACK = "Could you clarify the question?"


def _asked(text: str) -> set[str]:
    return {item for item, pattern in ASKS.items() if pattern.search(text)}


def _labels(datasets) -> set[str]:
    return {CLARIFY_DATASET_LABELS[key] for key in datasets}


@pytest.mark.parametrize("question", list(QUESTIONS))
def test_the_missing_items_do_not_depend_on_the_rule(question):
    slots = route_question(question).slots
    for rule, own in RULE_ITEM.items():
        found = set(missing_items(rule, question, slots)) - {own}
        assert found == QUESTIONS[question] - {own}, (rule, found)


@pytest.mark.parametrize("rule", sorted(RULE_ITEM))
@pytest.mark.parametrize("question", list(QUESTIONS))
def test_every_rule_text_asks_for_every_missing_item(question, rule):
    slots = route_question(question).slots
    base = _REASON_TEXT.get(rule, _FALLBACK)
    shown = complete_clarification(rule, question, slots, base)
    expected = QUESTIONS[question] | ({RULE_ITEM[rule]} - {None})
    assert expected <= _asked(shown), (expected - _asked(shown), shown)


@pytest.mark.parametrize("question", list(QUESTIONS))
def test_the_router_text_asks_for_every_missing_item(question):
    decision = route_question(question)
    if decision.path != "clarification":
        return
    expected = QUESTIONS[question] | ({RULE_ITEM.get(decision.rule)} - {None})
    assert expected <= _asked(decision.answer), (decision.rule, decision.answer)


def _ranking_missing_year(**overrides):
    # Jev reads a grouping the question never names, so its rule asks only for
    # the year; the router's slots still show the grouping missing.
    facts = {
        "intent": _choice("rank"),
        "has_time_scope": _noul(0.03),
        "dataset": _choice("cpuc_ignitions"),
        "rank_dimension": _choice("county"),
    }
    return _answer_facts(**{**facts, **overrides})


@pytest.mark.parametrize(
    "question, dataset",
    [
        ("Which one had the most ignitions?", "cpuc_ignitions"),
        ("Which had the most CAL FIRE incidents?", "calfire_incidents"),
    ],
)
def test_jevs_year_question_keeps_the_grouping_the_router_knew_was_missing(question, dataset):
    decision = route_question(question)
    assert decision.rule == "ranking_missing_slots"
    result = decide_from_answers(question, decision, _ranking_missing_year(dataset=_choice(dataset)))
    assert (result.winner, result.wording, result.decision.rule) == ("jev", "jev", "ranking_missing_year")
    text = result.decision.answer
    assert QUESTIONS[question] <= _asked(text), text
    # The groupings offered are the ones RANK_MEASURES has for the named dataset.
    groups = [group for group, measures in RANK_MEASURES.items() if any(MEASURE_DATASETS[m] == dataset for m in measures)]
    plurals = {"county": "counties", "utility": "utilities", "circuit": "circuits"}
    for group, plural in plurals.items():
        assert (plural in text.split("I also need", 1)[1].split("For example")[0]) == (group in groups), (group, text)


def _stored_decision(key: str, question: str):
    store = json.loads(STORE.read_text(encoding="utf-8"))
    stored = store["rows"][key]
    return decide_from_answers(
        question,
        route_question(question),
        _answers(stored["answers"]),
        error=stored.get("error"),
        today=date.fromisoformat(store["today"]),
    )


def test_stored_ho_094_asks_for_the_year_only():
    question = "what counties had the most utility-caused ignitions?"
    result = _stored_decision("v1|ho_094", question)
    assert result.decision.rule == "ranking_missing_year"
    # The grouping (counties) and dataset (CPUC ignitions) are named.
    assert QUESTIONS[question] <= _asked(result.decision.answer)


def test_stored_hv3_077_offers_only_the_datasets_a_county_ranking_reads():
    question = "Rank the counties by wildfire incidents."
    result = _stored_decision("v3|hv3_077", question)
    text = result.decision.answer
    assert result.decision.rule == "ranking_missing_year"
    assert QUESTIONS[question] <= _asked(text), text
    offered = _labels(MEASURE_DATASETS[m] for m in RANK_MEASURES["county"])
    for label in offered:
        assert label in text, label
    for label in _labels(MEASURE_DATASETS.values()) - offered:
        assert label not in text, (label, text)


def test_a_county_comparison_offers_the_compare_measures_datasets():
    question = "Compare Butte and Shasta counties."
    decision = route_question(question)
    shown = complete_clarification("records_missing_year", question, decision.slots, _REASON_TEXT["records_missing_year"])
    offered = _labels(MEASURE_DATASETS[m] for m in COMPARE_MEASURES["county"])
    for label in offered:
        assert label in shown, label
    # PSPS has no county.
    assert "PSPS" not in shown
