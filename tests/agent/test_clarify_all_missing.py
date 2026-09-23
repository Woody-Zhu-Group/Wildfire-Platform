"""A clarification asks for every missing item at once; rules and routes never change."""

from __future__ import annotations

import json
from pathlib import Path

from services.agent.routing import _route_question, route_question

EVAL = Path(__file__).resolve().parents[2] / "services" / "agent" / "eval"


def _pair(question: str):
    return _route_question(question), route_question(question)


def test_year_and_place_are_both_asked():
    before, after = _pair("Show PSPS events around Santa Rosa.")
    assert after.rule == before.rule == "undefined_spatial_scope"
    assert after.answer.startswith(before.answer)
    assert "I also need a year or date range." in after.answer
    assert 'For example: "How many PSPS events were there in Sonoma County in 2023?"' in after.answer


def test_year_and_dataset_are_both_asked():
    before, after = _pair("Show a seasonal chart")
    assert after.rule == before.rule == "series_mode_missing_dataset"
    assert after.answer.startswith(before.answer)
    assert "I also need a year or date range." in after.answer
    assert 'For example: "Show a seasonal chart of CPUC ignitions for 2023."' in after.answer


def test_an_item_the_rule_text_already_asks_for_is_not_asked_twice():
    before, after = _pair("Rank the counties by wildfire incidents.")
    assert after.rule == before.rule == "ranking_missing_slots"
    # The ranking text already asks for the dataset and a year range, so the year is
    # not asked twice; the example rephrasing names both.
    assert after.answer == before.answer + ' For example: "Rank counties by CPUC ignitions for 2023."'



def test_place_year_and_dataset_are_all_asked():
    before, after = _pair("Show recent fires near me.")
    assert after.rule == before.rule == "missing_location"
    assert "a year or date range, and a dataset" in after.answer


def test_risk_place_asks_for_a_scoreable_day():
    before, after = _pair("How risky was it?")
    assert after.rule == before.rule == "risk_missing_place"
    # "plus one historical calendar day" is already in the text; only the example is added.
    assert "I also need" not in after.answer
    assert after.answer.endswith('For example: "What was the fitted ignition risk for Sonoma County on 2023-08-15?"')



def test_acres_chart_is_not_asked_for_a_dataset():
    before, after = _pair("Show the cumulative acres chart")
    assert after.rule == before.rule == "series_mode_missing_year"
    assert after.answer == before.answer


def test_place_alone_reads_as_before():
    for question in (
        "How many CAL FIRE incidents happened near San Jose in 2023?",
        "What was the ignition risk on 2024-08-15?",
        "What utility service territory contains Modesto?",
        "Show the risk surface for August 2024",
    ):
        before, after = _pair(question)
        assert after.path == "clarification", question
        assert after.answer == before.answer, question
    assert route_question("How many CAL FIRE incidents happened near San Jose in 2023?").answer == (
        "How should that nearby area be defined? Provide a radius (for example 25 km) "
        "or a county/utility polygon to use."
    )


def test_rule_path_and_tools_never_change():
    questions = []
    for name in ("cases.json", "jev_paraphrases.json", "jev_holdout.json"):
        questions += [row["question"] for row in json.loads((EVAL / name).read_text(encoding="utf-8"))]
    for question in questions:
        before, after = _pair(question)
        assert (after.path, after.rule, after.tool_calls) == (before.path, before.rule, before.tool_calls), question
        if after.path != "clarification":
            assert after.answer == before.answer, question
