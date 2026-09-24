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
    # Santa Rosa is a city, not a county: the example uses a placeholder, not a real county.
    assert 'For example: "How many PSPS events were there in [a county] in 2023?"' in after.answer


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
    assert after.answer.endswith('For example: "What was the fitted ignition risk for [a county] on 2023-08-15?"')



def test_acres_chart_is_not_asked_for_a_dataset():
    before, after = _pair("Show the cumulative acres chart")
    assert after.rule == before.rule == "series_mode_missing_year"
    assert after.answer == before.answer


def test_place_alone_reads_as_before():
    for question in (
        "How many CAL FIRE incidents happened near San Jose in 2023?",
        "What was the ignition risk on 2024-08-15?",
        # A territory question about one city is answered at its center point
        # (city_point_context); a count inside a city still needs a place.
        "How many CAL FIRE incidents were there in Modesto in 2023?",
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


def test_example_reuses_what_the_question_resolved():
    # A place phrase that is exactly a county name becomes that county, with the year.
    near = route_question("Show me fires near Sacramento in 2024").answer
    assert 'For example: "How many CPUC ignitions were there in Sacramento County in 2024?"' in near
    # A named utility and dataset are reused.
    psps = route_question("What was the total number of PSPS events for PG&E?")
    if psps.answer != _route_question("What was the total number of PSPS events for PG&E?").answer:
        assert "PG&E territory" in psps.answer and "PSPS events" in psps.answer


def test_cities_and_near_me_get_a_placeholder_not_a_real_county():
    for question in (
        "What's the historical ignition risk for downtown Bakersfield?",
        "How many CAL FIRE incidents happened near San Jose?",
        "Show recent fires near me.",
        "List the CAL FIRE fires around Lake Tahoe.",
    ):
        answer = route_question(question).answer
        assert "[a county]" in answer, question
        assert "Sonoma" not in answer and "Lake County" not in answer, question


def test_no_example_names_a_county_the_question_did_not_mention():
    import re

    from services.agent.routing import _CA_COUNTIES

    questions = []
    for name in ("cases.json", "jev_paraphrases.json", "jev_holdout.json"):
        questions += [row["question"] for row in json.loads((EVAL / name).read_text(encoding="utf-8"))]
    for question in questions:
        decision = route_question(question)
        if decision.path != "clarification" or "For example:" not in (decision.answer or ""):
            continue
        example = decision.answer.split("For example:", 1)[1]
        for county in _CA_COUNTIES:
            if re.search(rf"\b{re.escape(county)} County\b", example):
                assert county.lower() in question.lower(), (question, example)
