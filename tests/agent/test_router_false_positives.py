"""Reviewer probes for PR #22: backstops must not swallow in-scope questions.

Every question here once routed to a refusal or clarification that main did
not produce. Each expected route is the one platform/main gives, or the
intended fix where main was also wrong (two counties, a change ranking).
"""

import json
from pathlib import Path

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


@pytest.mark.parametrize(
    "question",
    [
        "How many SCE ignitions were there from 2018 to 2020 and in 2022?",
        "How many SCE ignitions were there in 2018 and from 2020 to 2022?",
    ],
)
def test_a_range_plus_another_year_defers_instead_of_dropping_it(question):
    decision = route_question(question)
    assert decision.path == "model", question
    assert decision.rule == "multi_entity_deferred", question
    assert decision.tool_calls == []


def test_a_plain_range_stays_one_windowed_call():
    decision = route_question("How many SCE ignitions were there from 2021 to 2023?")
    assert decision.rule == "filtered_records"
    name, args = decision.tool_calls[0]
    assert name == "data_query_records"
    assert args["start_date"] == "2021-01-01"
    assert args["end_date"] == "2023-12-31"


def test_a_change_word_elsewhere_does_not_make_a_change_ranking():
    decision = route_question(
        "Which circuits had the most outages in 2024 after the fast-trip changes?"
    )
    assert decision.path == "deterministic"
    assert decision.rule == "ranked_records"
    assert decision.tool_calls[0][0] == "data_query_rank"


def test_biggest_drop_is_a_change_ranking_and_is_refused():
    decision = route_question(
        "Which counties had the biggest drop in ignitions from 2022 to 2023?"
    )
    assert decision.path == "unsupported"
    assert decision.rule == "unsupported_ranking"


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


@pytest.mark.parametrize(
    "question",
    [
        "For SCE, chart monthly EPSS fast-trip outage events during 2022 and include the annual total.",
        "Chart weekly PG&E ignitions in 2023 and how many there were overall.",
        "Plot CAL FIRE incidents by month for 2021 plus the total count.",
    ],
)
def test_a_series_plus_a_total_defers_instead_of_one_series_panel(question):
    decision = route_question(question)
    names = [name for name, _ in decision.tool_calls]
    # Never one call that drops the total: either a deferral with no calls, or
    # the deterministic count-plus-series pair.
    if decision.path == "deterministic":
        assert decision.rule == "multi_intent_count_and_trend", question
        assert names == ["data_query_records", "visualization_create"], question
    else:
        assert decision.path == "model", question
        assert decision.rule in {"multi_entity_deferred", "multi_intent_count_and_trend"}, question
        assert names == [], question


def test_a_yearly_totals_series_alone_still_opens_the_series_panel():
    decision = route_question("Show year over year CPUC ignition totals for 2024.")
    assert decision.rule == "series_yearly"


# ---------------------------------------------------------------------------
# Router follow-ups: issues 30, 31, 32, 42, 47, and the router items of 41.
# ---------------------------------------------------------------------------


_EVAL = Path(__file__).resolve().parents[2] / "services" / "agent" / "eval"
_SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "route_snapshot.json"


@pytest.mark.parametrize(
    "question",
    [
        "Which PG&E circuits should I look at for 2023 outages?",
        "Which dataset would you recommend for SCE ignitions?",
        "Should I use the spatial or attribute definition for PG&E ignitions in 2024?",
        "How many SCE ignitions should I expect to see in the 2021 data?",
        "Which SCE county should I start with for 2022 ignitions?",
    ],
)
def test_issue_30_the_analyst_as_subject_of_should_is_a_data_question(question):
    decision = route_question(question)
    assert decision.rule != "unsupported_optimization", question


@pytest.mark.parametrize(
    "question",
    [
        "Which utility should the CPUC penalize based on its wildfire record?",
        "Can you recommend which utility should change its wildfire mitigation strategy?",
        "What is the best strategy for PG&E in Tier 3?",
        "Should PG&E expand EPSS to more circuits?",
        "What would you recommend the CPUC do about SCE ignitions?",
        "The commission should fine SCE for its 2021 ignitions, right?",
    ],
)
def test_issue_30_a_utility_or_the_cpuc_as_subject_of_should_is_advice(question):
    decision = route_question(question)
    assert decision.path == "unsupported", question
    assert decision.rule == "unsupported_optimization", question


def test_issue_31_a_tier_ranking_reaches_the_ranking_refusals_first():
    epss_by_utility = route_question("Rank utilities by EPSS outages on Tier 3 circuits in 2023.")
    assert epss_by_utility.rule == "unsupported_rank_epss_utility"
    cells = route_question("Rank grid cells in Tier 3 by ignition risk in 2024.")
    assert cells.rule == "unsupported_ranking"


def test_issue_31_an_allowed_ranking_with_a_tier_asks_instead_of_dropping_the_tier():
    for question in (
        "Rank EPSS circuits in Tier 3 by outages in 2023.",
        "Which counties had the most CAL FIRE incidents on Tier 2 circuits in 2022?",
    ):
        decision = route_question(question)
        assert decision.path == "clarification", question
        assert decision.rule == "hftd_constraint_unavailable", question
        assert decision.tool_calls == [], question
        assert "statewide" in (decision.answer or ""), question


def test_issue_31_a_tier_ranking_still_answers_without_the_tier():
    decision = route_question("Rank EPSS circuits by outages in 2023.")
    assert decision.path == "deterministic"
    assert decision.rule == "ranked_records"


@pytest.mark.parametrize(
    "question,rule",
    [
        ("Show the SCE utility territory boundary", "utility_territory"),
        ("Map SCE territory", "utility_territory"),
        ("Show the PG&E territory polygon", "utility_territory"),
        ("Show me the SCE territory footprint", "utility_territory"),
        ("What is the SCE territory?", "utility_territory"),
    ],
)
def test_issue_32_territory_boundary_phrasings_still_route_to_the_boundary(question, rule):
    assert route_question(question).rule == rule, question


def test_issue_32_service_area_phrasing_without_territory_keeps_its_route():
    # The removed regex never matched these: they carry no "territory" word,
    # which _asks_territory_boundary requires first. This pins that behavior.
    for question in (
        "Draw the Southern California Edison service-area outline.",
        "What is the SDG&E service area?",
    ):
        decision = route_question(question)
        assert decision.path == "model", question
        assert decision.rule == "open_ended", question


@pytest.mark.parametrize(
    "question,county",
    [
        ("How many CAL FIRE incidents were there in Kings Canyon National Park in Fresno County in 2020?", "Fresno"),
        ("How many PSPS events hit the Lake Tahoe basin in El Dorado County in 2021?", "El Dorado"),
        ("How many CAL FIRE incidents were there in the Trinity Alps in Shasta County in 2021?", "Shasta"),
        ("How many CPUC ignitions were there along the Kings River in Tulare County in 2019?", "Tulare"),
        ("How many CAL FIRE incidents were there in Mono Basin in Inyo County in 2020?", "Inyo"),
        ("How many CAL FIRE incidents were there near Glenn Ranch in Butte County in 2019?", "Butte"),
    ],
)
def test_issue_42_a_place_name_beside_a_qualified_county_is_not_a_second_county(question, county):
    decision = route_question(question)
    if decision.path == "deterministic":
        assert decision.slots["county"] == county, question
    assert decision.slots["counties"] == [county], question
    assert decision.rule != "multi_entity_deferred", question


def test_issue_42_short_county_names_still_count_with_the_word_county():
    alone = route_question("How many CAL FIRE incidents were there in Kings County in 2020?")
    assert alone.slots["county"] == "Kings"
    two = route_question("How many CAL FIRE incidents were there in Lake County and Napa County in 2020?")
    assert set(two.slots["counties"]) == {"Lake", "Napa"}
    assert two.slots["county"] is None
    bare = route_question("How many CAL FIRE incidents were there by the lake in 2020?")
    assert bare.slots["counties"] == []


def _eval_questions():
    rows = []
    for name in ("cases.json", "jev_paraphrases.json", "jev_holdout.json"):
        for row in json.loads((_EVAL / name).read_text(encoding="utf-8")):
            if "question" in row:
                rows.append((name, str(row.get("id")), row["question"]))
    return rows


def test_issue_47_every_route_decision_carries_the_slots_dict():
    keys = {"utilities", "year", "years", "dataset", "coords", "county", "counties", "time_resolution", "start_date", "end_date"}
    for name, case_id, question in _eval_questions():
        decision = route_question(question)
        assert isinstance(decision.slots, dict), (name, case_id)
        assert keys <= set(decision.slots), (name, case_id, keys - set(decision.slots))


def test_issue_41_several_counties_never_collapse_to_one_county_slot():
    for question in (
        "How many CAL FIRE incidents were there in Napa and Sonoma County in 2020?",
        "Map CPUC ignitions in Butte County and Napa County for 2021.",
        "Show the monthly CAL FIRE incident trend for Sonoma County and Napa County in 2022.",
        "List EPSS outages in Kern County and Fresno County in 2023.",
    ):
        decision = route_question(question)
        assert len(decision.slots["counties"]) == 2, question
        assert decision.slots["county"] is None, question
        assert not any("county" in args for _, args in decision.tool_calls), question


@pytest.mark.parametrize(
    "rule,question",
    [
        ("unsupported_live_web", "How many CPUC ignitions are in the current dataset for 2024?"),
        ("unsupported_future_prediction", "Will you show me a map of 2024 CPUC ignitions?"),
        ("risk_future_date", "What is the risk forecast for cell 100 on 2024-08-01?"),
        ("unsupported_optimization", "Which PG&E circuits should I look at for 2023 outages?"),
        ("city_needs_place", "What was the fitted risk at 38.55, -121.74 (Davis) on 2024-08-01?"),
        ("hftd_constraint_unavailable", "How many EPSS events occurred on PG&E circuits in Tier 3 HFTD areas in 2023?"),
        ("unsupported_translation", "Which grid cell does 38.5, -121.5 translate to?"),
        ("unsupported_personnel", "How many CAL FIRE incidents involved firefighters in 2020?"),
        ("unsupported_satellite", "Show the satellite basemap with CAL FIRE incidents in 2020."),
        ("unsupported_cost", "How many EPSS outages were there in 2023 on circuits with costly repairs?"),
        ("unsupported_damage", "How many CAL FIRE incidents damaged more than 100 acres in 2021?"),
    ],
)
def test_issue_41_every_backstop_has_an_in_scope_question_it_must_not_catch(rule, question):
    decision = route_question(question)
    assert decision.rule != rule, (rule, question, decision.rule)


def test_issue_41_route_snapshot_matches_the_committed_fixture():
    """Routes for every dev and holdout v1 question, pinned.

    A router change that moves a route fails here until the fixture is
    regenerated, which is the route report CLAUDE.md requires:
    python -m tests.agent.route_snapshot > tests/agent/fixtures/route_snapshot.json
    """
    expected = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    actual = {}
    for name, case_id, question in _eval_questions():
        decision = route_question(question)
        actual[f"{name}:{case_id}"] = [decision.path, decision.rule]
    moved = {k: (expected.get(k), v) for k, v in actual.items() if expected.get(k) != v}
    missing = sorted(set(expected) - set(actual))
    assert not moved and not missing, {"moved": moved, "missing": missing}
