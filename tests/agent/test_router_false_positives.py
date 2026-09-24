"""Reviewer probes for PR #22: backstops must not swallow in-scope questions.

Every question here once routed to a refusal or clarification that main did
not produce. Each expected route is the one platform/main gives, or the
intended fix where main was also wrong (two counties, a change ranking).
"""

import json
from pathlib import Path

import pytest

from services.agent.decisions.decide_mode import BACKSTOP_RULES
from services.agent.routing import _counties, _county, route_question
from services.shared.dataset_registry import UTILITY_CLARIFY_LABELS


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
        "For PG&E, chart monthly EPSS fast-trip outage events during 2022 and include the annual total.",
        "Chart weekly PG&E ignitions in 2023 and how many there were overall.",
        "Plot CAL FIRE incidents by month for 2021 plus the total count.",
    ],
)
def test_a_series_plus_a_total_defers_instead_of_one_series_panel(question):
    """Issue 44: with the dataset and window known, the deterministic pair.

    The issue's SCE wording (hv2_002) is the EPSS PG&E-only clarification
    instead; see test_review_82_a_non_pge_utility_on_epss_never_returns_zero.
    """
    decision = route_question(question)
    names = [name for name, _ in decision.tool_calls]
    assert decision.path == "deterministic", question
    assert decision.rule == "multi_intent_count_and_trend", question
    assert names == ["data_query_records", "visualization_create"], question


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


def test_issue_32_service_area_phrasing_without_territory_is_the_boundary_map():
    # Label rule G: a utility service-area outline, boundary, polygon, or
    # footprint with no count and no dataset word is the territory map.
    for question in (
        "Draw the Southern California Edison service-area outline.",
        "What is the SDG&E service area?",
        "Show the PG&E service-area polygon.",
    ):
        decision = route_question(question)
        assert decision.path == "deterministic", question
        assert decision.rule == "utility_territory", question
        assert decision.tool_calls[0][0] == "visualization_inspect", question


def test_issue_32_service_area_with_a_count_or_a_dataset_is_not_the_boundary():
    for question in (
        "How many ignitions were there in the PG&E service area in 2023?",
        "Show CAL FIRE incidents inside the SCE service area in 2020.",
        "Compare EPSS outages across the PG&E service area in 2022 and 2023.",
    ):
        assert route_question(question).rule != "utility_territory", question


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


# Every eval set: dev, paraphrases, and holdouts v1, v2, and v3 (issue 41).
_EVAL_SETS = (
    "cases.json",
    "jev_paraphrases.json",
    "jev_holdout.json",
    "jev_holdout_v2.json",
    "jev_holdout_v3_questions.json",
)


def _eval_questions():
    rows = []
    for name in _EVAL_SETS:
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
        # The four backstops PR 58 left without a probe.
        ("unsupported_air_quality", "How many PSPS events in 2021 had fair quality wind readings?"),
        ("unsupported_cpz", "How many EPSS outages in 2023 were on circuits with protection settings enabled?"),
        ("unsupported_evacuation", "How many PSPS events in 2021 were preemptive de-energizations?"),
        ("unsupported_leadership", "Which utility led the ignition counts in 2023?"),
    ],
)
def test_issue_41_every_backstop_has_an_in_scope_question_it_must_not_catch(rule, question):
    decision = route_question(question)
    assert decision.rule != rule, (rule, question, decision.rule)
    assert decision.path != "unsupported", (rule, question, decision.rule)


def test_issue_41_every_backstop_rule_has_a_negative_probe():
    """A new backstop fails here until it gets an in-scope question it must not catch."""
    probed = {
        rule
        for marker in test_issue_41_every_backstop_has_an_in_scope_question_it_must_not_catch.pytestmark
        for rule, _question in marker.args[1]
    }
    assert BACKSTOP_RULES <= probed, sorted(BACKSTOP_RULES - probed)


def test_issue_41_route_snapshot_matches_the_committed_fixture():
    """Routes for every dev, paraphrase, and holdout v1, v2, and v3 question, pinned.

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


# ---------------------------------------------------------------------------
# Review of PR 58: plural counties, one county scan, county place words,
# passive advice, and any HFTD tier on a ranking.
# ---------------------------------------------------------------------------



@pytest.mark.parametrize(
    "question,expected",
    [
        ("How many CAL FIRE incidents were there in Lake and Napa counties in 2020?", {"Lake", "Napa"}),
        ("How many CPUC ignitions were there in Kings and Tulare counties in 2019?", {"Kings", "Tulare"}),
        ("How many PSPS events were there in Sonoma, Napa, and Lake counties in 2021?", {"Sonoma", "Napa", "Lake"}),
    ],
)
def test_review_42_a_plural_counties_list_qualifies_every_name(question, expected):
    decision = route_question(question)
    assert set(decision.slots["counties"]) == expected, question
    assert decision.slots["county"] is None, question
    assert decision.rule == "multi_entity_deferred", question


@pytest.mark.parametrize(
    "phrase",
    # Shasta Lake is an incorporated city, so it routes as that city instead
    # (tests/agent/test_city_points.py); a count in it asks for a place.
    ["Napa Valley", "Sonoma Valley", "Kern River", "Santa Clara Valley"],
)
def test_review_42_the_single_and_list_county_slots_always_agree(phrase):
    question = f"How many CAL FIRE incidents were there in {phrase} in 2020?"
    counties = _counties(question)
    single = _county(question)
    assert single == (counties[0] if len(counties) == 1 else None), phrase
    assert counties == [], phrase
    decision = route_question(question)
    assert decision.slots["county"] is None and decision.slots["counties"] == [], phrase
    assert decision.path == "clarification", phrase
    assert decision.rule == "county_place_ambiguous", phrase
    assert decision.tool_calls == [], phrase


def test_review_42_a_cue_required_county_word_without_county_clarifies():
    decision = route_question("How many CAL FIRE incidents were there in Trinity in 2020?")
    assert decision.path == "clarification"
    assert decision.rule == "county_place_ambiguous"
    assert "Trinity County" in (decision.answer or "")
    qualified = route_question("How many CAL FIRE incidents were there in Trinity County in 2020?")
    assert qualified.rule == "filtered_records"
    assert qualified.slots["county"] == "Trinity"


def test_review_42_a_qualified_county_resolves_a_place_word_beside_it():
    decision = route_question(
        "How many CAL FIRE incidents were there in Kings Canyon National Park in Fresno County in 2020?"
    )
    assert decision.rule == "filtered_records"
    assert decision.slots["county"] == "Fresno"
    assert decision.slots["counties"] == ["Fresno"]


@pytest.mark.parametrize(
    "question",
    [
        "Recommend a strategy to cut PG&E ignitions in 2024",
        "What penalties should apply to SCE for its 2021 ignitions?",
        "Should undergrounding be required of PG&E after 2021?",
        "Is it advisable for SCE to expand EPSS?",
    ],
)
def test_review_30_passive_and_object_advice_is_refused(question):
    decision = route_question(question)
    assert decision.path == "unsupported", question
    assert decision.rule == "unsupported_optimization", question


@pytest.mark.parametrize(
    "question",
    [
        "Which PG&E circuits should I look at for 2023 outages?",
        "Which dataset would you recommend for SCE ignitions?",
        "Should I use the spatial or attribute definition for PG&E ignitions in 2024?",
        "How many SCE ignitions should I expect to see in the 2021 data?",
        "How many PG&E ignitions were there in 2024 after EPSS was required on its circuits?",
    ],
)
def test_review_30_the_analyst_as_subject_still_answers(question):
    assert route_question(question).rule != "unsupported_optimization", question


@pytest.mark.parametrize(
    "question",
    [
        "Which utility had the most ignitions in Tier 3 in 2023?",
        "Which counties had the most CAL FIRE incidents in HFTD Tier 2 areas in 2022?",
        "Rank EPSS circuits in Tier 3 by outages in 2023.",
    ],
)
def test_review_31_any_tier_on_an_allowed_ranking_asks_instead_of_dropping_it(question):
    decision = route_question(question)
    assert decision.path == "clarification", question
    assert decision.rule == "hftd_constraint_unavailable", question
    assert decision.tool_calls == [], question
    assert "statewide" in (decision.answer or ""), question


def test_review_31_a_tier_on_an_unsupported_ranking_is_refused_first():
    assert route_question("Rank utilities by EPSS outages in Tier 3 in 2023.").rule == "unsupported_rank_epss_utility"
    assert route_question("Rank grid cells in Tier 3 by ignition risk in 2024.").rule == "unsupported_ranking"


def test_review_31_the_same_rankings_still_answer_without_a_tier():
    assert route_question("Which utility had the most ignitions in 2023?").rule == "ranked_records"
    assert route_question("Which counties had the most CAL FIRE incidents in 2022?").rule == "ranked_records"


# ---------------------------------------------------------------------------
# Router polish: issues 67, 44, and the remaining router items of 41.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        # The reviewer's question from issue 67.
        "Top 3 utilities by ignitions in HFTD Tier 2 in 2022",
        "Top 3 counties by CAL FIRE incidents in Tier 3 in 2022",
        "Rank utilities by ignitions in high fire threat districts tier 2 for 2022",
        "Top 5 counties by ignitions on Tier 3 circuits in 2022",
    ],
)
def test_issue_67_a_tier_ranking_with_a_bare_dataset_word_reaches_the_tier_clarification(question):
    decision = route_question(question)
    assert decision.path == "clarification", (question, decision.rule)
    assert decision.rule == "hftd_constraint_unavailable", (question, decision.rule)
    assert decision.tool_calls == [], question
    assert "statewide" in (decision.answer or ""), question


def test_issue_67_bare_outages_with_a_tier_resolve_to_epss_and_reach_its_refusal():
    # Utilities cannot be ranked in EPSS; the tier must not hide that behind the generic refusal.
    decision = route_question("Top 3 utilities by outages in HFTD Tier 2 in 2022")
    assert decision.rule == "unsupported_rank_epss_utility"


def test_issue_67_a_tier_ranking_with_no_dataset_asks_for_one_instead_of_refusing():
    decision = route_question("Top 3 utilities in HFTD Tier 2 in 2022")
    assert decision.path == "clarification"
    assert decision.rule == "ranking_missing_slots"


def test_issue_67_the_same_rankings_still_answer_without_a_tier():
    decision = route_question("Top 3 utilities by ignitions in 2022")
    assert decision.rule == "ranked_records"
    assert decision.tool_calls[0][1]["dataset"] == "cpuc_ignitions"
    assert decision.tool_calls[0][1]["group_by"] == "utility"


def test_issue_67_unsupported_tier_rankings_are_still_refused_first():
    assert route_question("Rank utilities by EPSS outages on Tier 3 circuits in 2023.").rule == "unsupported_rank_epss_utility"
    assert route_question("Rank grid cells in Tier 3 by ignition risk in 2024.").rule == "unsupported_ranking"
    assert route_question("Which counties had the most US ignitions in HFTD Tier 2 in 2022?").rule == "unsupported_rank_us_state"


def _pair(question):
    decision = route_question(question)
    assert decision.path == "deterministic", (question, decision.rule)
    assert decision.rule == "multi_intent_count_and_trend", (question, decision.rule)
    (records_name, records), (series_name, series) = decision.tool_calls
    assert (records_name, series_name) == ("data_query_records", "visualization_create")
    return records, series


def test_issue_44_the_pair_carries_the_series_dataset_utility_and_window():
    records, series = _pair(
        "For PG&E, chart monthly EPSS fast-trip outage events during 2022 and include the annual total."
    )
    assert records == {"dataset": "epss_outages", "result_mode": "count", "year": 2022, "utility": "PGE"}
    assert series == {"kind": "time_series", "dataset": "epss", "interval": "monthly", "year": 2022, "utility": "PGE"}


def test_issue_44_the_pair_carries_the_county_and_reads_the_interval_from_by_month():
    records, series = _pair("Plot CAL FIRE incidents in Sonoma County by month for 2021 plus the total count.")
    assert records == {"dataset": "calfire_incidents", "result_mode": "count", "year": 2021, "county": "Sonoma"}
    assert series == {"kind": "time_series", "dataset": "calfire", "interval": "monthly", "year": 2021, "county": "Sonoma"}
    _records, weekly = _pair("Plot CAL FIRE incidents by week for 2021 plus the total count.")
    assert weekly["interval"] == "weekly"


def test_issue_44_a_psps_chart_plus_a_total_also_takes_the_pair():
    records, series = _pair("Chart monthly PSPS events in 2021 plus the total count.")
    assert records["dataset"] == "psps_events" and series["dataset"] == "psps"


@pytest.mark.parametrize(
    "question",
    [
        # No window: nothing to count or chart yet.
        "Chart monthly PG&E ignitions plus the total",
        # Enumerated years: one pair would collapse the per-year totals.
        "Chart PG&E ignitions by month in 2021 and 2022 plus the annual totals",
        # Two counties: one pair would drop one of them.
        "Chart monthly PG&E ignitions in Napa and Sonoma counties in 2022 plus the total",
    ],
)
def test_issue_44_a_series_plus_a_total_still_defers_without_one_dataset_and_window(question):
    decision = route_question(question)
    assert decision.path == "model", (question, decision.rule)
    assert decision.rule == "multi_entity_deferred", (question, decision.rule)
    assert decision.tool_calls == [], question


def test_issue_44_a_yearly_totals_series_alone_is_still_the_series_panel():
    assert route_question("Show year over year CPUC ignition totals for 2024.").rule == "series_yearly"


# ---------------------------------------------------------------------------
# Review of PR 82: breakdown and per-year charts plus a total, the explicit
# pair with several utilities, a map plus a count, EPSS for a non-PG&E utility,
# and the dataset named in the tier clarification.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Chart monthly CPUC ignitions by county in 2023 plus the total",
        "Plot 2022 ignitions by utility by month and the overall count",
        "Chart monthly ignitions for each year from 2021 to 2024 plus the annual total",
        "Chart annual CPUC ignitions 2015 to 2023 plus the overall count",
        "Plot PG&E ignitions per year from 2019 to 2023 plus the total",
    ],
)
def test_review_82_a_breakdown_or_per_year_chart_plus_a_total_defers(question):
    decision = route_question(question)
    assert decision.path == "model", (question, decision.rule)
    assert decision.tool_calls == [], question


def test_review_82_the_total_ask_and_interval_words_are_not_breakdowns():
    # "annual total" and "by month" describe the total and the step, not a breakdown.
    records, series = _pair(
        "For PG&E, chart monthly EPSS fast-trip outage events during 2022 and include the annual total."
    )
    assert series["interval"] == "monthly" and records["result_mode"] == "count"
    _pair("Plot CAL FIRE incidents by month for 2021 plus the total count.")


@pytest.mark.parametrize(
    "question",
    [
        "Show the monthly trend of PG&E and SCE ignitions in 2023 and the count",
        "How many PG&E and SCE ignitions were there in 2023 and their monthly trend?",
        "Show the monthly CAL FIRE incident trend and count for Napa and Sonoma counties in 2022",
    ],
)
def test_review_82_the_explicit_pair_defers_instead_of_dropping_a_utility_or_county(question):
    decision = route_question(question)
    assert decision.path == "model", (question, decision.rule)
    assert decision.rule == "multi_entity_deferred", (question, decision.rule)
    assert decision.tool_calls == [], question


def test_review_82_the_explicit_pair_still_runs_for_one_utility():
    records, series = _pair("Show the monthly trend of PG&E ignitions in 2023 and the count")
    assert records["utility"] == "PGE" and series["utility"] == "PGE"


@pytest.mark.parametrize(
    "question,dataset,viz,extra",
    [
        ("Map PG&E ignitions in 2023 and how many there were", "cpuc_ignitions", "ignitions", {"utility": "PGE", "year": 2023}),
        ("Show a map of CAL FIRE incidents in 2021 and how many there were", "calfire_incidents", "calfire", {"year": 2021}),
        ("Map 2022 EPSS outages and the total count", "epss_outages", "epss", {"year": 2022}),
        ("How many and where are PG&E CPUC ignitions in 2024?", "cpuc_ignitions", "ignitions", {"utility": "PGE", "year": 2024}),
    ],
)
def test_review_82_a_map_plus_a_count_runs_both_never_the_map_alone(question, dataset, viz, extra):
    decision = route_question(question)
    assert decision.path == "deterministic", (question, decision.rule)
    assert decision.rule == "multi_intent_count_and_map", (question, decision.rule)
    (count_name, count_args), (map_name, map_args) = decision.tool_calls
    assert (count_name, map_name) == ("data_query_records", "visualization_create")
    assert count_args == {"dataset": dataset, "result_mode": "count", **extra}
    assert map_args == {"kind": "map", "dataset": viz, **extra}


@pytest.mark.parametrize(
    "question,dataset,extra",
    [
        # The US sample maps to itself, so a dataset-rename check dropped its count.
        ("Map US ignitions in 2023 and how many were there", "us_ignitions", {"year": 2023}),
        ("Map PG&E ignitions in 2023 and how many were there", "cpuc_ignitions", {"utility": "PGE", "year": 2023}),
    ],
)
def test_review_82_a_us_sample_map_plus_a_count_runs_both(question, dataset, extra):
    decision = route_question(question)
    assert decision.rule == "multi_intent_count_and_map", (question, decision.rule)
    (count_name, count_args), (map_name, _map_args) = decision.tool_calls
    assert (count_name, map_name) == ("data_query_records", "visualization_create")
    assert count_args == {"dataset": dataset, "result_mode": "count", **extra}


@pytest.mark.parametrize(
    "question",
    [
        "Map circuits in 2023 and how many were there",
        "Map HFTD tier 3 and how many ignitions in 2023",
        # The US sample has no utility column: never a PG&E-labeled sample count.
        "Map PG&E US sample ignitions in 2023 and how many were there",
    ],
)
def test_review_82_no_count_is_added_for_a_non_event_map_or_a_utility_scoped_us_sample(question):
    decision = route_question(question)
    assert "data_query_records" not in [name for name, _ in decision.tool_calls or []], (
        question,
        decision.tool_calls,
    )


def test_review_82_a_map_without_a_count_is_still_the_map_alone():
    decision = route_question("Map PG&E ignitions in 2023")
    assert decision.rule == "map"
    assert [name for name, _ in decision.tool_calls] == ["visualization_create"]


@pytest.mark.parametrize(
    "question",
    [
        # hv2_002 and ho_080, both labeled with the note that EPSS is PG&E only.
        "For SCE, chart monthly EPSS fast-trip outage events during 2022 and include the annual total.",
        "show monthly epss outage counts for sce in 2022",
        "How many EPSS outages did SCE have in 2022?",
        "Map SCE EPSS outages in 2022",
        "Map SDG&E EPSS outages in 2023 and how many there were",
    ],
)
def test_review_82_a_non_pge_utility_on_epss_never_returns_zero(question):
    decision = route_question(question)
    assert decision.path == "clarification", (question, decision.rule)
    assert decision.rule == "epss_non_pge_utility", (question, decision.rule)
    assert decision.tool_calls == [], question
    assert "PG&E-only" in decision.answer and "absent, not zero" in decision.answer
    # Named as the reader knows it (SDG&E, not the code SDGE).
    utility = decision.slots["utilities"][0]
    assert UTILITY_CLARIFY_LABELS.get(utility, utility) in decision.answer


@pytest.mark.parametrize(
    "question,rule",
    [
        ("How many EPSS outages did PG&E have in 2022?", "filtered_records"),
        ("Chart monthly EPSS outages for PG&E in 2022", "time_series"),
        ("How many EPSS outages were there in 2022?", "filtered_records"),
        ("How many PSPS events did SCE have in 2022?", "filtered_records"),
    ],
)
def test_review_82_pge_epss_and_other_datasets_are_unchanged(question, rule):
    decision = route_question(question)
    assert decision.rule == rule, (question, decision.rule)


def test_review_82_the_epss_utility_rule_is_router_only():
    from services.agent.decisions.jev_policy import REGEX_ONLY
    from services.agent.decisions.mapping import RULE_TO_INTENT
    from services.agent.decisions.schemas import CONTEXT_DEFERRED_RULES

    assert "epss_non_pge_utility" in REGEX_ONLY
    assert "epss_non_pge_utility" in RULE_TO_INTENT
    assert "epss_non_pge_utility" in CONTEXT_DEFERRED_RULES


# ---------------------------------------------------------------------------
# Label rule J (Michael, 2026-09-24): a US-sample question restricted to a
# utility clarifies, since the sample has no utility column.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,name",
    [
        ("Map PG&E US sample ignitions in 2023", "PG&E"),
        ("Map PG&E US sample ignitions in 2023 and how many were there", "PG&E"),
        ("How many US sample ignitions did SCE have in 2022?", "SCE"),
        ("Chart the monthly trend of US ignitions for SDG&E in 2021", "SDG&E"),
        ("Rank counties by US sample ignitions for PG&E in 2020", "PG&E"),
        ("Compare PG&E CPUC ignitions with the US sample in 2023", "PG&E"),
    ],
)
def test_rule_j_a_us_sample_question_with_a_utility_clarifies(question, name):
    decision = route_question(question)
    assert decision.path == "clarification", (question, decision.rule)
    assert decision.rule == "us_sample_utility_filter", (question, decision.rule)
    assert decision.tool_calls == [], question
    assert "no utility column" in decision.answer
    assert "national sample" in decision.answer
    assert f"{name}'s CPUC utility ignitions" in decision.answer


@pytest.mark.parametrize(
    "question,rule",
    [
        ("Map US ignitions in 2023 and how many were there", "multi_intent_count_and_map"),
        ("How many US sample ignitions in 2022?", "filtered_records"),
        ("Map PG&E ignitions in 2023", "map"),
        ("How many sampled ignitions in California in 2022?", "unexpressable_county_filter"),
    ],
)
def test_rule_j_leaves_the_sample_without_a_utility_and_cpuc_with_one_unchanged(question, rule):
    decision = route_question(question)
    assert decision.rule == rule, (question, decision.rule)


def test_rule_j_the_us_sample_utility_rule_is_router_only():
    from services.agent.decisions.jev_policy import REGEX_ONLY
    from services.agent.decisions.mapping import RULE_TO_INTENT
    from services.agent.decisions.schemas import CONTEXT_DEFERRED_RULES

    assert "us_sample_utility_filter" in REGEX_ONLY
    assert "us_sample_utility_filter" in RULE_TO_INTENT
    assert "us_sample_utility_filter" in CONTEXT_DEFERRED_RULES


@pytest.mark.parametrize(
    "question,label,group",
    [
        ("Top 3 utilities by ignitions in HFTD Tier 2 in 2022", "CPUC ignitions", "utility"),
        ("Top 3 counties by CAL FIRE incidents in Tier 3 in 2022", "CAL FIRE incidents", "county"),
        ("Rank EPSS circuits in Tier 3 by outages in 2023.", "EPSS outages", "circuit"),
    ],
)
def test_review_82_the_tier_clarification_names_the_resolved_dataset(question, label, group):
    decision = route_question(question)
    assert decision.rule == "hftd_constraint_unavailable"
    assert decision.answer.startswith(f"I can rank {label} by {group} statewide"), decision.answer


def test_the_registry_measures_match_the_tools():
    from typing import get_args

    from services.agent.schemas import Metric
    from services.comparison.metrics import METRICS
    from services.shared.dataset_registry import (
        ALLOWED_RANK_PAIRS,
        COMPARE_MEASURES,
        MEASURE_DATASETS,
        MEASURE_LABELS,
        RANK_MEASURES,
    )

    assert set(MEASURE_LABELS) == set(MEASURE_DATASETS) == set(METRICS) == set(get_args(Metric))
    for measures in COMPARE_MEASURES.values():
        assert set(measures) <= set(METRICS)
    assert {group for _dataset, group, _metric in ALLOWED_RANK_PAIRS} == set(RANK_MEASURES)
    assert sum(len(measures) for measures in RANK_MEASURES.values()) == len(ALLOWED_RANK_PAIRS)
