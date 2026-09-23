"""Reviewer probes for PR #22: backstops must not swallow in-scope questions.

Every question here once routed to a refusal or clarification that main did
not produce. Each expected route is the one platform/main gives, or the
intended fix where main was also wrong (two counties, a change ranking).
"""

import pytest

from services.agent.routing import route_question


FORECAST_AND_PREDICT = [
    ("What is the risk forecast for cell 100 on 2024-08-01?", "deterministic", "cell_risk"),
    ("Predict the fitted risk for 38.5, -121.5 on 2024-08-01", "deterministic", "coordinate_risk_chain"),
    ("Which counties had the highest predicted risk on 2024-08-01?", "clarification", "ranking_missing_slots"),
    ("Will you show me a map of 2024 CPUC ignitions?", "deterministic", "map"),
    ("What will the 2024 PGE ignition count be if I use the spatial definition?", "deterministic", "filtered_records"),
]

LIVE_WORDING_WITH_A_PAST_PERIOD = [
    ("How many PG&E ignitions were there from January 2024 up to today?", "deterministic", "filtered_records"),
    ("How many CPUC ignitions are in the current dataset for 2024?", "deterministic", "filtered_records"),
    ("Show me the current HFTD tier 3 map.", "clarification", "ambiguous_relative_time"),
]

CITY_NAMES_THAT_ARE_NOT_PLACES = [
    ("How many PSPS events were driven by Santa Ana wind events in 2020?", "deterministic", "filtered_records"),
    ("How many CAL FIRE incidents were there in Marina del Rey in 2021?", "deterministic", "filtered_records"),
    ("How many CPUC ignitions happened in the winters of 2021 to 2023?", "deterministic", "filtered_records"),
    ("What was the fitted risk at 38.55, -121.74 (Davis) on 2024-08-01?", "deterministic", "coordinate_risk_chain"),
]

SINGLE_STEM_TOPICS_WITH_CONTEXT = [
    ("Which grid cell does 38.5, -121.5 translate to?", "deterministic", "coordinate_context"),
    ("How many CAL FIRE incidents involved firefighters in 2020?", "deterministic", "filtered_records"),
    ("Show the satellite basemap with CAL FIRE incidents in 2020.", "model", "open_ended"),
]

YEAR_RANGES_ARE_ONE_CALL = [
    "How many SCE ignitions were there from 2018 to 2020?",
    "How many SCE ignitions were there between 2018 and 2020?",
    "How many SCE ignitions were there in 2018-2020?",
]


@pytest.mark.parametrize(
    "question,path,rule",
    FORECAST_AND_PREDICT
    + LIVE_WORDING_WITH_A_PAST_PERIOD
    + CITY_NAMES_THAT_ARE_NOT_PLACES
    + SINGLE_STEM_TOPICS_WITH_CONTEXT,
)
def test_probe_routes_as_on_main(question, path, rule):
    decision = route_question(question)
    assert decision.path == path, (question, decision.path, decision.rule)
    assert decision.rule == rule, (question, decision.rule)


def test_live_oak_is_a_city_not_live_wording():
    decision = route_question("How many CAL FIRE incidents were there in Live Oak in 2024?")
    assert decision.rule != "unsupported_live_web"
    assert decision.rule == "city_needs_place"


def test_live_wording_still_refuses_without_a_past_period():
    for question in (
        "Are there any PSPS outages right now?",
        "What is the current wildfire risk near San Jose?",
        "Are any fires live in Sonoma County?",
        "What is today's weather in Sonoma County?",
    ):
        assert route_question(question).rule == "unsupported_live_web", question


def test_two_counties_defer_and_never_hint_one_county():
    decision = route_question(
        "How many CAL FIRE incidents were there in Napa and Sonoma County in 2020?"
    )
    assert decision.path == "model"
    assert decision.rule == "multi_entity_deferred"
    assert decision.slots["county"] is None
    assert set(decision.slots["counties"]) == {"Napa", "Sonoma"}


def test_close_to_a_number_is_the_deterministic_count():
    decision = route_question("Were CPUC ignitions close to 500 in 2024?")
    assert decision.path == "deterministic"
    assert decision.rule == "filtered_records"
    assert decision.tool_calls[0][0] == "data_query_records"


def test_a_change_over_time_ranking_is_refused_not_counted():
    decision = route_question(
        "Which counties had the largest increase in CAL FIRE wildfire incidents "
        "between 2019 and 2020?"
    )
    assert decision.path == "unsupported"
    assert decision.rule == "unsupported_ranking"
    assert decision.tool_calls == []


@pytest.mark.parametrize("question", YEAR_RANGES_ARE_ONE_CALL)
def test_a_year_range_without_a_breakdown_is_one_windowed_call(question):
    decision = route_question(question)
    assert decision.path == "deterministic", question
    assert decision.rule == "filtered_records", question
    name, args = decision.tool_calls[0]
    assert name == "data_query_records"
    assert args["start_date"] == "2018-01-01"
    assert args["end_date"] == "2020-12-31"
    assert args["utility"] == "SCE"


def test_enumerated_years_and_breakdowns_still_defer():
    enumerated = route_question("How many SCE ignitions were there in 2018, 2019, and 2020?")
    assert enumerated.rule == "multi_entity_deferred"
    annual = route_question("Chart the annual ignition counts for SCE from 2016 through 2022.")
    assert not any(
        call[0] == "data_query_records" and "start_date" in call[1]
        for call in annual.tool_calls
    )


def test_forward_tokens_and_the_current_year_still_refuse_predictions():
    for question in (
        "Will PG&E have another PSPS event this fall?",
        "How many PSPS events are expected during the 2026 fire season?",
        "How many ignitions in 2030?",
    ):
        assert route_question(question).rule == "unsupported_future_prediction", question


def test_single_stem_topics_still_refuse_with_their_context():
    assert route_question("Translate how many fires into Spanish.").rule == "unsupported_translation"
    assert route_question("How many firefighters were deployed to the 2024 Park Fire?").rule == "unsupported_personnel"
    assert route_question("Show me the satellite infrared image of the 2024 fire.").rule == "unsupported_satellite"
