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
from services.agent.decisions import decide_mode
from services.agent.decisions.decide_mode import _REASON_TEXT, decide_from_answers
from services.agent.decisions.jev_policy import DerivedOutcome
from services.agent.eval.jev_decide_replay import STORE, _answers
from services.agent.routing import route_question
from services.shared.dataset_registry import (
    CLARIFY_DATASET_LABELS,
    COMPARE_MEASURES,
    MEASURE_DATASETS,
    RANK_MEASURES,
    SERIES_DATASETS,
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
    "Show a year over year chart": {"year", "dataset"},
    "Show a seasonal chart for 2023": {"dataset"},
    "Show recent fires near me.": {"year", "dataset"},
    "How risky was it?": {"place", "date"},
    "Compare Butte and Shasta counties.": {"year", "dataset"},
    "How many CPUC ignitions were there in 2023?": set(),
    # hv3_013: a territory and HFTD lookup naming several cities reads no event data.
    "For Sacramento, Stockton, and Fresno, identify the utility territory and HFTD tier, if any.": set(),
    # hv2_049: a circuit and HFTD lookup in comparison wording reads no event data either.
    "Find the distribution circuits overlapping HFTD around Grass Valley and compare them with circuits near Auburn.": set(),
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


@pytest.mark.parametrize("rule", sorted(RULE_ITEM))
@pytest.mark.parametrize(
    "question", ["Show a seasonal chart", "Show a year over year chart", "Show a seasonal chart for 2023"]
)
def test_a_chart_without_a_dataset_offers_the_series_datasets(question, rule):
    decision = route_question(question)
    shown = complete_clarification(rule, question, decision.slots, _REASON_TEXT.get(rule, _FALLBACK))
    for text in (shown, decision.answer):
        if "dataset (" not in text and "should I chart:" not in text:
            continue  # this base text asks for the dataset in its own words
        for label in _labels(SERIES_DATASETS):
            assert label in text, (label, text)
        for label in _labels(MEASURE_DATASETS.values()) - _labels(SERIES_DATASETS):
            assert label not in text, (label, text)


def test_the_series_question_lists_the_registry_datasets():
    decision = route_question("Show a seasonal chart")
    assert decision.rule == "series_mode_missing_dataset"
    assert decision.answer.startswith(_REASON_TEXT["series_mode_missing_dataset"])
    for label in _labels(SERIES_DATASETS):
        assert label in decision.answer


def test_a_county_comparison_offers_the_compare_measures_datasets():
    question = "Compare Butte and Shasta counties."
    decision = route_question(question)
    shown = complete_clarification("records_missing_year", question, decision.slots, _REASON_TEXT["records_missing_year"])
    offered = _labels(MEASURE_DATASETS[m] for m in COMPARE_MEASURES["county"])
    for label in offered:
        assert label in shown, label
    # PSPS has no county.
    assert "PSPS" not in shown


def test_hv3_013_a_lookup_naming_several_cities_is_not_asked_for_a_year():
    rows = json.loads((STORE.parents[1] / "jev_holdout_v3_questions.json").read_text(encoding="utf-8"))
    question = next(row["question"] for row in rows if row.get("id") == "hv3_013")
    decision = route_question(question)
    assert decision.rule == "city_needs_place"
    assert "year" not in _asked(decision.answer) - _asked(_REASON_TEXT.get("city_needs_place", ""))
    assert "I also need" not in decision.answer and "For example" not in decision.answer, decision.answer


def _force_ranking_missing_year(monkeypatch):
    forced = DerivedOutcome("clarify", "ranking_missing_year", None, ["ranking_missing_year"], 0.97, {})
    monkeypatch.setattr(decide_mode, "derive_outcome", lambda *args, **kwargs: forced)
    return _answer_facts(intent=_choice("rank"), has_time_scope=_noul(0.03))


def test_a_registry_grounded_rank_refusal_stands_over_a_jev_clarification(monkeypatch):
    # CAL FIRE acres by utility is not a pair in RANK_MEASURES: a verified fact.
    question = "Which utility had the most CAL FIRE acres burned?"
    decision = route_question(question)
    assert (decision.path, decision.rule) == ("unsupported", "unsupported_ranking")
    assert not any(MEASURE_DATASETS[m] == "calfire_incidents" for m in RANK_MEASURES["utility"])
    result = decide_from_answers(question, decision, _force_ranking_missing_year(monkeypatch))
    assert result.jev_rule == "ranking_missing_year"
    assert (result.winner, result.why) == ("router", "code_verified")
    assert result.decision is decision


@pytest.mark.parametrize(
    "question",
    [
        "Which utility had the most EPSS outages in 2023?",
        "Which state had the most US ignitions in 2020?",
        "Rank counties by CPUC ignitions and CAL FIRE incidents in 2022.",
    ],
)
def test_every_unsupported_rank_pair_stands(monkeypatch, question):
    decision = route_question(question)
    assert decision.path == "unsupported" and decision.rule.startswith("unsupported_rank"), decision.rule
    result = decide_from_answers(question, decision, _force_ranking_missing_year(monkeypatch))
    assert (result.winner, result.why) == ("router", "code_verified")


def test_a_refusal_on_a_pair_the_registry_has_is_not_verified(monkeypatch):
    # A change over time on CPUC ignitions by county: the pair exists, so the
    # refusal is the router's reading, not a registry fact, and Jev may override it.
    question = "Which county had the largest increase in CPUC ignitions?"
    decision = route_question(question)
    assert (decision.path, decision.rule) == ("unsupported", "unsupported_ranking")
    result = decide_from_answers(question, decision, _force_ranking_missing_year(monkeypatch))
    assert result.why != "code_verified"
