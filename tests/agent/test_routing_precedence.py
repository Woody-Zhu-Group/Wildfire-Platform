"""Keyword collisions and relative-date slot fill for the deterministic router."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from services.agent.argument_normalize import prepare_tool_arguments
from services.agent.routing import route_question, _year


def test_quantity_outranks_territory_keyword():
    decision = route_question(
        "How many ignitions happened inside SCE's territory in 2023?"
    )
    assert decision.path == "deterministic"
    assert decision.rule == "spatial_utility_count"
    tool, args = decision.tool_calls[0]
    assert tool == "data_query_spatial"
    assert args["kind"] == "summary"
    assert args["utility"] == "SCE"
    assert args["start_date"] == "2023-01-01"


def test_territory_boundary_still_matches_without_quantity():
    decision = route_question("Show the SCE utility territory boundary")
    assert decision.path == "deterministic"
    assert decision.rule == "utility_territory"
    assert decision.tool_calls[0][0] == "visualization_inspect"


def test_map_outages_without_year_clarifies_not_open_ended():
    decision = route_question("Show me the map of PG&E outages")
    assert decision.path == "clarification"
    assert decision.rule == "map_missing_year"


def test_map_month_to_month_range_is_deterministic():
    decision = route_question(
        "map cpuc ignitions from august 2023 to september 2024"
    )
    assert decision.path == "deterministic"
    assert decision.rule == "map"
    args = decision.tool_calls[0][1]
    assert args["kind"] == "map"
    assert args["dataset"] == "ignitions"
    assert args["start_date"] == "2023-08-01"
    assert args["end_date"] == "2024-09-30"


def test_trend_year_to_year_range_is_deterministic():
    decision = route_question("trend of SCE ignitions 2021 to 2025")
    assert decision.path == "deterministic"
    assert decision.rule == "time_series"
    args = decision.tool_calls[0][1]
    assert args["kind"] == "time_series"
    assert args["dataset"] == "ignitions"
    assert args["utility"] == "SCE"
    assert args["start_date"] == "2021-01-01"
    assert args["end_date"] == "2025-12-31"
    assert args["interval"] == "monthly"


def test_bare_year_map_still_uses_year():
    decision = route_question(
        "I'd like to see where PG&E's CPUC ignitions happened in 2024"
    )
    assert decision.path == "deterministic"
    assert decision.rule == "map"
    assert decision.tool_calls[0][1]["year"] == 2024


def test_compare_last_year_resolves_and_uses_utilities_kind():
    decision = route_question(
        "Compare wildfire activity between PG&E and SCE territories last year"
    )
    assert decision.path == "deterministic"
    assert decision.rule == "utility_comparison"
    tool, args = decision.tool_calls[0]
    assert tool == "comparison_run"
    assert args["kind"] == "utilities"
    assert args["utilities"] == ["PGE", "SCE"]
    assert args["ignition_definition"] == "spatial"
    assert decision.slots["year"] == date.today().year - 1


def test_period_comparison_still_requires_two_explicit_years():
    decision = route_question("Compare PGE ignitions in 2023 versus 2024")
    assert decision.rule == "period_comparison"
    assert decision.tool_calls[0][1]["kind"] == "periods"


def test_recent_clarifies_but_past_two_years_resolves():
    recent = route_question("What were recent ignitions for SCE?")
    assert recent.path == "clarification"
    assert recent.rule == "ambiguous_relative_time"

    span = route_question("How many ignitions in the past two years for PGE?")
    assert span.path == "deterministic"
    assert span.rule == "filtered_records"
    args = span.tool_calls[0][1]
    assert args["start_date"] == f"{date.today().year - 1}-01-01"
    assert args["end_date"] == f"{date.today().year}-12-31"


def test_relative_year_helpers():
    today = date(2026, 8, 10)
    assert _year("last year", today=today) == 2025
    assert _year("this year", today=today) == 2026
    assert _year("2 years ago", today=today) == 2024
    assert _year("two years ago", today=today) == 2024
    assert _year("recent fires", today=today) is None


def test_close_to_a_number_is_a_count_not_a_place():
    decision = route_question("Were CPUC ignitions close to 500 in 2024?")
    assert decision.path == "deterministic"
    assert decision.rule == "filtered_records"
    assert decision.tool_calls[0][0] == "data_query_records"
    assert decision.tool_calls[0][1]["year"] == 2024


def test_apostrophe_year_counts_as_that_year():
    decision = route_question("Tally PG and E utility-attributed ignitions in '24.")
    assert decision.path == "deterministic"
    assert decision.rule == "filtered_records"
    assert decision.tool_calls[0][1]["year"] == 2024


def test_near_place_without_radius_clarifies():
    decision = route_question("Show me fires near Sacramento in 2024")
    assert decision.path == "clarification"
    assert decision.rule == "undefined_spatial_scope"


def test_two_years_ago_compare_resolves_to_harness_year():
    today = date(2026, 8, 10)
    from services.agent.time_resolve import resolve_time

    resolution = resolve_time(
        "Compare wildfire activity between PG&E and SCE territories 2 years ago",
        today=today,
    )
    assert resolution.status == "relative_year"
    assert resolution.year == 2024
    decision = route_question(
        "Compare wildfire activity between PG&E and SCE territories 2 years ago"
    )
    assert decision.path == "deterministic"
    assert decision.slots["year"] == date.today().year - 2


def test_comparison_kind_repair_periods_to_utilities():
    repaired = prepare_tool_arguments(
        "comparison_run",
        {
            "kind": "periods",
            "metric": "ignition_count",
            "scope_type": "utility",
            "scope": "PGE",
        },
        year=2025,
        years=[2025],
        utilities=["PGE", "SCE"],
        fill_aliases=True,
        fill_year=True,
        repair_comparison=True,
    )
    assert repaired["kind"] == "utilities"
    assert repaired["utilities"] == ["PGE", "SCE"]
    assert repaired["start_date"] == "2025-01-01"
    assert "period_a_start" not in repaired


def test_trend_with_territory_keyword_is_not_boundary_lookup():
    decision = route_question(
        "Weekly CAL FIRE trend in SCE territory for 2023"
    )
    assert decision.rule == "time_series"
    assert decision.tool_calls[0][0] == "visualization_create"


def test_map_territory_alone_is_boundary_not_multi_intent():
    decision = route_question("Map SCE territory")
    assert decision.rule == "utility_territory"


def test_map_plus_monthly_trend_fires_both_visualization_calls():
    decision = route_question(
        "Map PG&E CPUC ignition events for 2024 and show the monthly trend."
    )
    assert decision.path == "deterministic"
    assert decision.rule == "map_plus_trend"
    assert [tool for tool, _args in decision.tool_calls] == [
        "visualization_create",
        "visualization_create",
    ]
    map_args, series_args = decision.tool_calls[0][1], decision.tool_calls[1][1]
    assert map_args["kind"] == "map"
    assert series_args["kind"] == "time_series"
    assert series_args["interval"] == "monthly"
    assert map_args["dataset"] == series_args["dataset"] == "ignitions"
    assert map_args["utility"] == series_args["utility"] == "PGE"
    assert map_args["year"] == series_args["year"] == 2024


def test_map_monthly_without_trend_word_stays_map_only():
    decision = route_question("Map monthly CAL FIRE incidents for 2024.")
    assert decision.rule == "map"
    assert len(decision.tool_calls) == 1
    assert decision.tool_calls[0][1]["kind"] == "map"


def test_see_where_outranks_count_and_maps():
    decision = route_question(
        "I'd like to see where PG&E's CPUC ignitions happened in 2024"
    )
    assert decision.path == "deterministic"
    assert decision.rule == "map"
    tool, args = decision.tool_calls[0]
    assert tool == "visualization_create"
    assert args["kind"] == "map"
    assert args["dataset"] == "ignitions"
    assert args["utility"] == "PGE"
    assert args["year"] == 2024


def test_locations_of_maps_even_with_how_many():
    decision = route_question(
        "How many and where are PG&E CPUC ignitions in 2024?"
    )
    assert decision.rule == "map"
    assert decision.tool_calls[0][1]["kind"] == "map"


def test_show_me_where_maps():
    decision = route_question(
        "Show me where SCE CAL FIRE incidents were in 2024"
    )
    assert decision.rule == "map"
    assert decision.tool_calls[0][1]["dataset"] == "calfire"
    assert decision.tool_calls[0][1]["utility"] == "SCE"


def test_several_utilities_and_a_chart_is_not_a_partial_count():
    decision = route_question(
        "Give me the ignition count for PGE, SCE, and SDGE in 2024 and chart it"
    )
    assert decision.path == "model"
    assert decision.tool_calls == []


def test_annual_counts_across_a_year_range_are_not_one_sum():
    decision = route_question(
        "Chart the annual ignition counts for SCE from 2016 through 2022"
    )
    assert decision.path in {"model", "clarification"}
    assert not any(
        call[0] == "data_query_records" and "start_date" in call[1]
        for call in decision.tool_calls
    )


def test_each_month_of_one_year_is_not_an_annual_total():
    decision = route_question("How many PGE outages were there in each month of 2023?")
    assert decision.path == "model"
    assert decision.tool_calls == []


def test_several_named_years_are_not_one_collapsed_count():
    decision = route_question(
        "How many SCE ignitions were there in 2018, and how many in 2020?"
    )
    assert decision.path in {"model", "clarification"}
    totals = [
        call for call in decision.tool_calls if call[0] == "data_query_records"
    ]
    assert len(totals) != 1


def test_single_utility_single_year_count_stays_deterministic():
    decision = route_question("How many PGE ignitions were there in 2024?")
    assert decision.path == "deterministic"
    assert decision.rule == "filtered_records"
    assert decision.tool_calls[0][1]["utility"] == "PGE"
    assert decision.tool_calls[0][1]["year"] == 2024


def test_single_dataset_monthly_trend_still_builds_a_monthly_series():
    decision = route_question("Show the monthly CAL FIRE incident trend for 2024")
    assert decision.path == "deterministic"
    assert decision.rule == "time_series"
    args = decision.tool_calls[0][1]
    assert args["kind"] == "time_series"
    assert args["interval"] == "monthly"
    assert args["year"] == 2024


def test_list_records_uses_preview_limit_25():
    decision = route_question(
        "Show me CAL FIRE incidents in Sacramento County in 2024"
    )
    assert decision.path == "deterministic"
    assert decision.rule == "filtered_records"
    args = decision.tool_calls[0][1]
    assert args["result_mode"] == "records"
    assert args["dataset"] == "calfire_incidents"
    assert args["county"] == "Sacramento"
    assert args["limit"] == 25


def test_bear_valley_fills_the_utility_slot():
    decision = route_question("How many Bear Valley ignitions were there in 2021?")
    assert decision.slots["utilities"] == ["BVES"]
    assert decision.slots["dataset"] == "cpuc_ignitions"


def test_live_phrasing_is_unsupported_and_history_is_not():
    live = route_question("Are there any PSPS outages right now?")
    assert live.path == "unsupported"
    assert live.rule == "unsupported_live_web"
    weather = route_question("What is today's weather in Sonoma County?")
    assert weather.rule == "unsupported_live_web"
    assert route_question("What is the current wildfire risk near San Jose?").rule == (
        "unsupported_live_web"
    )
    assert route_question("Are any fires live in Sonoma County?").rule == (
        "unsupported_live_web"
    )

    recent = route_question("What were recent ignitions for SCE?")
    assert recent.rule == "ambiguous_relative_time"
    last_year = route_question("How many PGE ignitions were there last year?")
    assert last_year.rule != "unsupported_live_web"
    assert last_year.path == "deterministic"
    this_year = route_question("How many PGE ignitions were there this year?")
    assert this_year.rule != "unsupported_live_web"
    assert this_year.rule != "risk_future_date"


def test_future_modal_and_a_year_past_coverage_are_unsupported_predictions():
    for question in (
        "Will PG&E have another PSPS event this fall?",
        "How many PSPS events are expected during the 2026 fire season?",
        "How many ignitions in 2030?",
        "Predict next summer's SCE ignition count.",
        "Predict which utility will have the most wildfire ignitions in 2027.",
    ):
        decision = route_question(question)
        assert decision.path == "unsupported", question
        assert decision.rule == "unsupported_future_prediction", question
        assert "does not predict" in (decision.answer or ""), question


def test_future_risk_question_keeps_the_risk_clarification():
    for question in (
        "What's the fire risk in Sacramento County tomorrow?",
        "What will the ignition risk be in Butte County next summer?",
        "Can you forecast daily ignition risk for every California grid cell "
        "for the next 30 days?",
        "Can the model forecast tomorrow's ignition hotspots?",
    ):
        decision = route_question(question)
        assert decision.path == "clarification", question
        assert decision.rule == "risk_future_date", question


def test_historical_expectation_is_not_a_future_refusal():
    value = route_question("What was the expected value of CPUC ignitions in 2021?")
    assert value.rule != "risk_future_date"
    forecast = route_question("What was forecast for SCE ignitions in 2021?")
    assert forecast.rule != "risk_future_date"


def test_advice_about_a_utility_or_the_cpuc_is_unsupported():
    for question in (
        "Which utility should the CPUC penalize based on its wildfire record?",
        "Can you recommend which utility should change its wildfire mitigation strategy?",
        "What is the best strategy for PG&E in Tier 3?",
    ):
        decision = route_question(question)
        assert decision.path == "unsupported", question
        assert decision.rule == "unsupported_optimization", question


def test_predict_historical_risk_on_a_past_date_stays_a_score():
    decision = route_question(
        "Predict historical ignition risk for cell 400 on 2024-08-15."
    )
    assert decision.rule == "cell_risk"


def test_quoted_should_is_not_advice():
    decision = route_question(
        'The note says "the utility should inspect lines", but how many '
        "PG&E ignitions were there in 2023?"
    )
    assert decision.rule != "unsupported_optimization"


def test_future_event_counts_are_unsupported_predictions():
    for question in (
        "How many ignitions will there be tomorrow?",
        "How many CAL FIRE incidents next summer?",
        "How many ignitions next year?",
        "How many ignitions in future years?",
    ):
        decision = route_question(question)
        assert decision.path == "unsupported", question
        assert decision.rule == "unsupported_future_prediction", question


def test_early_returns_keep_the_dataset_slot():
    near = route_question("How many CAL FIRE incidents happened near San Jose?")
    assert near.path == "clarification"
    assert near.slots["dataset"] == "calfire_incidents"
    damage = route_question(
        "What property damage should we expect from ignitions next year?"
    )
    assert damage.path == "unsupported"
    assert damage.slots["dataset"] == "cpuc_ignitions"
    tomorrow = route_question("What's the fire risk in Sacramento County tomorrow?")
    assert tomorrow.rule == "risk_future_date"
    assert "dataset" in tomorrow.slots


def test_a_city_that_is_not_a_county_asks_for_a_real_place():
    # One city with a territory or tier question is answered at its center
    # point (test_city_points.py). Several places, or counts in a city, still ask.
    for question in (
        "For Sacramento, Stockton, and Fresno, identify the utility territory and HFTD tier.",
        "How many CAL FIRE incidents were there in Chico in 2023?",
        "Is part of Modesto in a Tier 2 or Tier 3 High Fire Threat District?",
    ):
        decision = route_question(question)
        assert decision.path == "clarification"
        assert decision.rule == "city_needs_place"
        assert decision.tool_calls == []
        assert "county" in decision.answer.lower()


def test_county_questions_still_answer():
    decision = route_question(
        "How many CAL FIRE incidents were there in Sacramento County in 2023?"
    )
    assert decision.path == "deterministic"
    assert decision.rule == "filtered_records"
    assert decision.slots["county"] == "Sacramento"
    orange = route_question(
        "How many CAL FIRE incidents were there in Orange County in 2023?"
    )
    assert orange.rule == "filtered_records"
    assert orange.slots["county"] == "Orange"
    assert orange.rule != "city_needs_place"


def test_common_word_cities_are_not_places_without_a_cue():
    not_places = (
        "What is the state of the utility industry in 2023?",
        "How many CPUC ignitions involved the commerce sector in 2024?",
        "Is weed abatement tracked as an EPSS outage in 2023?",
        "Do pine needles change the fm100 fuel moisture?",
        "This warehouse is a paradise of ignition records. How many CPUC ignitions were there in 2023?",
        "Describe the orange glow of the 2024 fire season.",
        "Admiral Coronado counted CPUC ignitions in 2023.",
        "What is the best evacuation route out of Paradise?",
    )
    for question in not_places:
        decision = route_question(question)
        assert decision.rule != "city_needs_place", question
    glow = route_question("Describe the orange glow of the 2024 fire season.")
    assert glow.slots.get("county") is None


def test_common_word_cities_are_places_when_cued():
    # Cued, these are cities. A tier or territory question is answered at the
    # city center point; an address or a count in the city still asks.
    for question in (
        "Is the city of Weed inside a Tier 3 HFTD area?",
        "What utility territory contains Needles?",
        "Is the city of Industry inside PG&E territory?",
    ):
        decision = route_question(question)
        assert decision.rule == "city_point_context", question
    for question in (
        "For 2020, what was the historical ignition risk at an address in Paradise, California?",
        "How many PSPS shutoffs affected Coronado, California?",
    ):
        decision = route_question(question)
        assert decision.rule == "city_needs_place", question


def test_a_city_name_followed_by_county_is_not_the_city():
    for question in (
        "How many CAL FIRE incidents were there in Weed County in 2023?",
        "How many ignitions were there in Industry County in 2019?",
        "How many fires were there in Paradise County in 2018?",
        "How many outages were there in Commerce County in 2020?",
        "How many incidents were there in Needles County in 2021?",
        "How many ignitions were there in Coronado County in 2022?",
    ):
        decision = route_question(question)
        assert decision.rule == "unknown_county", question
        assert decision.rule != "city_needs_place"


def test_saved_questions_do_not_take_a_common_word_as_a_city():
    root = Path(__file__).resolve().parents[2] / "services" / "agent" / "eval"
    for name in ("cases.json", "jev_paraphrases.json"):
        rows = json.loads((root / name).read_text(encoding="utf-8"))
        for row in rows:
            decision = route_question(row["question"])
            assert decision.rule != "city_needs_place", row["question"]
            assert not decision.rule.startswith("city_point"), row["question"]


def test_circuits_with_an_hftd_tier_or_hftd_acreage_clarify():
    circuits = route_question(
        "Give me the distribution circuits in SCE territory that intersect Tier 3 HFTD areas."
    )
    assert circuits.path == "clarification"
    assert circuits.rule == "hftd_constraint_unavailable"
    mapped = route_question(
        "Map PG&E distribution circuits in Nevada County that are inside HFTD tier 2 or 3."
    )
    assert mapped.rule == "hftd_constraint_unavailable"
    acreage = route_question(
        "Compare HFTD Tier 2 and Tier 3 acreage within PG&E and SCE service territories."
    )
    assert acreage.rule == "hftd_constraint_unavailable"


def test_an_epss_count_inside_one_tier_is_not_a_circuit_intersection():
    decision = route_question(
        "How many EPSS events occurred on PG&E circuits in Tier 3 HFTD areas in 2023?"
    )
    assert decision.rule != "hftd_constraint_unavailable"


def test_a_single_tier_hftd_map_still_answers():
    decision = route_question("Show me a map of Tier 2 High Fire Threat District areas.")
    assert decision.path == "deterministic"
    assert decision.rule == "map"
    assert decision.tool_calls[0][1]["dataset"] == "hftd"


def test_rank_utilities_by_epss_is_refused_including_fast_trip_and_a_year_span():
    # _asks_ranking used to require which/most/top, so "rank utilities" never
    # reached unsupported_rank_epss_utility and fell through to the model.
    for question in (
        "Rank utilities by EPSS events in 2022.",
        "Rank utilities by total EPSS fast-trip events from 2021 to 2023.",
    ):
        decision = route_question(question)
        assert decision.path == "unsupported"
        assert decision.rule == "unsupported_rank_epss_utility"


def test_cpuc_utility_rankings_still_answer():
    decision = route_question("Which utility had the most CPUC ignitions in 2023?")
    assert decision.path == "deterministic"
    assert decision.rule == "ranked_records"
    args = decision.tool_calls[0][1]
    assert args["dataset"] == "cpuc_ignitions"
    assert args["group_by"] == "utility"


def test_around_a_year_or_close_to_a_number_is_not_a_place():
    around = route_question("Around 2023, how many CPUC ignitions were there?")
    assert around.rule != "undefined_spatial_scope"
    assert around.slots["dataset"] == "cpuc_ignitions"
    close = route_question("Were CPUC ignitions close to 500 in 2024?")
    assert close.rule != "undefined_spatial_scope"
    assert close.slots["dataset"] == "cpuc_ignitions"


def test_summary_phrase_opens_summary_panel_and_bare_counts_do_not():
    summary = route_question("summary of EPSS outages in 2024")
    assert summary.rule == "summary_stats"
    assert summary.slots["stat_mode"] == "summary"
    assert summary.slots["dataset"] == "epss_outages"
    assert summary.tool_calls[0][1]["result_mode"] == "count"
    assert summary.tool_calls[0][1]["year"] == 2024
    overview = route_question("overview of CPUC ignitions 2024")
    assert overview.rule == "summary_stats"
    assert overview.slots["dataset"] == "cpuc_ignitions"
    bare = route_question("How many PG&E ignitions in 2024?")
    assert bare.rule == "filtered_records"
    assert bare.slots.get("stat_mode") is None
    medical = route_question("show me medical baseline data 2024")
    assert medical.rule == "medical_exposure"
    assert medical.slots["stat_mode"] == "medical_exposure"
    missing = route_question("summary of EPSS outages")
    assert missing.rule == "records_missing_year"
    assert missing.tool_calls == []


def test_series_modes_resolve_dataset_and_year():
    cases = [
        ("Show year over year CPUC ignition totals for 2024.", "series_yearly", "ignitions", "cpuc_ignitions"),
        ("Show the seasonal profile of CAL FIRE incidents for 2024.", "series_seasonal", "calfire", "calfire_incidents"),
        ("Show cumulative acres burned over the year for 2024.", "series_cumulative_acres", "calfire", "calfire_incidents"),
        ("Show customers affected over time in 2024.", "series_customer_events", "psps", "psps_events"),
        ("Show EPSS outages by division in 2024.", "series_regional", "epss", "epss_outages"),
    ]
    for question, rule, viz, warehouse in cases:
        decision = route_question(question)
        assert decision.path == "deterministic", question
        assert decision.rule == rule
        assert decision.slots["series_mode"] == rule.removeprefix("series_")
        assert decision.slots["dataset"] == warehouse
        assert decision.slots["year"] == 2024
        tool, args = decision.tool_calls[0]
        assert tool == "visualization_create"
        assert args["kind"] == "time_series"
        assert args["dataset"] == viz
        assert args["year"] == 2024


def test_series_modes_clarify_when_the_year_or_dataset_is_missing():
    missing_year = route_question("Show cumulative acres burned over the year.")
    assert missing_year.path == "clarification"
    assert missing_year.rule == "series_mode_missing_year"
    assert missing_year.tool_calls == []
    missing_dataset = route_question("Show annual totals for 2024.")
    assert missing_dataset.path == "clarification"
    assert missing_dataset.rule == "series_mode_missing_dataset"
    assert missing_dataset.tool_calls == []


def test_series_mode_guards_keep_existing_routes():
    trend = route_question("Show the monthly CAL FIRE incident trend for 2024.")
    assert trend.rule == "time_series"
    assert "series_mode" not in trend.slots
    count = route_question("How many EPSS outages were there in 2024?")
    assert count.rule == "filtered_records"
    assert count.tool_calls[0][1]["result_mode"] == "count"
    incidents = route_question("How many CAL FIRE incidents were there in 2024?")
    assert incidents.rule == "filtered_records"
    assert incidents.tool_calls[0][1]["dataset"] == "calfire_incidents"
    assert incidents.slots.get("series_mode") is None


def test_medical_baseline_opens_epss_exposure_panel():
    decision = route_question("show me medical baseline data 2024")
    assert decision.path == "deterministic"
    assert decision.rule == "medical_exposure"
    assert decision.slots["dataset"] == "epss"
    assert decision.slots["stat_mode"] == "medical_exposure"
    assert decision.slots["view_id"] == "medical-exposure"
    assert decision.slots["year"] == 2024
    tool, args = decision.tool_calls[0]
    assert tool == "data_query_records"
    assert args == {"dataset": "epss_outages", "result_mode": "count", "year": 2024}


def test_life_support_and_medically_vulnerable_use_the_same_panel():
    for question in (
        "life support customers in 2024",
        "life-support customers in 2024",
        "medically vulnerable customers 2024",
    ):
        decision = route_question(question)
        assert decision.rule == "medical_exposure", question
        assert decision.slots["dataset"] == "epss"
        assert decision.tool_calls[0][1]["dataset"] == "epss_outages"
        assert decision.tool_calls[0][1]["year"] == 2024


def test_medical_exposure_without_a_year_clarifies():
    decision = route_question("show me medical baseline data")
    assert decision.path == "clarification"
    assert decision.rule == "medical_exposure_missing_year"
    assert decision.tool_calls == []


def test_nearby_epss_questions_keep_their_routes():
    count = route_question("How many EPSS outages were there in 2024?")
    assert count.rule == "filtered_records"
    assert count.tool_calls[0][1]["dataset"] == "epss_outages"
    assert count.tool_calls[0][1]["result_mode"] == "count"

    mapped = route_question("Map EPSS outages for 2024.")
    assert mapped.rule == "map"
    assert mapped.tool_calls[0][0] == "visualization_create"
    assert mapped.tool_calls[0][1]["dataset"] == "epss"

    ranked = route_question("Which EPSS circuits had the most outages in 2024?")
    assert ranked.rule == "ranked_records"
    assert ranked.tool_calls[0][0] == "data_query_rank"

    listed = route_question("Show me PG&E outages in 2024")
    assert listed.rule == "filtered_records"
    assert listed.tool_calls[0][1]["result_mode"] == "records"
    assert listed.tool_calls[0][1]["utility"] == "PGE"


def test_event_map_guard_stays_an_event_map():
    decision = route_question("Map PG&E CPUC ignition events for 2024")
    assert decision.rule == "map"
    assert decision.tool_calls == [
        ("visualization_create", {"kind": "map", "dataset": "ignitions", "year": 2024, "utility": "PGE"})
    ]
    for key in ("map_mode", "show_hdw", "series_mode"):
        assert key not in decision.slots


def test_risk_surface_and_residual_keep_the_risk_route_and_add_a_grid_slot():
    surface = route_question("Show the modeled ignition risk surface for Butte County on 2024-08-15")
    assert surface.rule == "county_risk"
    assert surface.tool_calls == [("risk_forecast", {"county": "Butte", "date": "2024-08-15"})]
    assert surface.slots["map_mode"] == "risk"
    mapped = route_question("Map the ignition risk for PG&E on 2024-08-15")
    assert mapped.rule == "utility_risk"
    assert mapped.slots["map_mode"] == "risk"
    residual = route_question("Show the residual map for Butte County on 2024-08-15")
    assert residual.rule == "county_risk"
    assert residual.slots["map_mode"] == "residual"
    plain = route_question("What was the ignition risk for Butte County on 2024-08-15?")
    assert plain.rule == "county_risk"
    assert "map_mode" not in plain.slots


def test_risk_grid_clarifies_instead_of_inventing_a_date_or_place():
    no_date = route_question("Show the risk surface for Butte County")
    assert no_date.path == "clarification"
    assert no_date.rule == "forecast_missing_date"
    assert no_date.tool_calls == []
    month = route_question("Show the residual map for Butte County in August 2024")
    assert month.path == "clarification"
    assert month.tool_calls == []
    future = route_question("Show the risk surface for Butte County on 2026-08-15")
    assert future.rule == "risk_future_date"
    no_place = route_question("Show the risk surface for 2024-08-15")
    assert no_place.rule == "risk_surface"
    assert no_place.tool_calls == [("risk_surface", {"date": "2024-08-15"})]


def test_where_advice_does_not_become_a_risk_map():
    decision = route_question("Where should I buy a house if I want to minimize future wildfire risk?")
    assert "map_mode" not in decision.slots


def test_hdw_map_needs_a_named_layer_and_a_covered_year():
    decision = route_question("Map CPUC ignitions with HDW for 2024")
    assert decision.path == "deterministic"
    assert decision.rule == "hdw_map"
    assert decision.slots["show_hdw"] is True
    assert decision.tool_calls == [("visualization_create", {"kind": "map", "dataset": "ignitions", "year": 2024})]
    weather = route_question("Show fire weather with EPSS outages in 2023")
    assert weather.rule == "hdw_map"
    assert weather.tool_calls[0][1]["dataset"] == "epss"
    no_year = route_question("Map CAL FIRE incidents with HDW")
    assert no_year.rule == "map_missing_year"
    assert no_year.tool_calls == []
    early = route_question("Map CPUC ignitions with HDW for 2019")
    assert early.rule == "time_out_of_coverage"
    assert early.tool_calls == []
    no_layer = route_question("Show HDW for 2024")
    assert no_layer.rule != "hdw_map"
    assert "show_hdw" not in no_layer.slots


def test_hdw_years_match_the_playback_files():
    from pathlib import Path

    from services.shared.dataset_registry import HDW_YEARS

    root = Path(__file__).resolve().parents[2] / "docs/assets/data/weather_anim"
    files = {int(path.stem.rsplit("_", 1)[1]) for path in root.glob("weather_anim_*.json")}
    assert files == set(HDW_YEARS)


def test_timeline_needs_several_chartable_datasets():
    decision = route_question("Show CPUC ignitions, EPSS outages, and CAL FIRE incidents over time in 2024")
    assert decision.rule == "series_timeline"
    assert decision.slots["series_mode"] == "timeline"
    assert sorted(decision.slots["timeline_datasets"]) == ["calfire", "epss", "ignitions"]
    assert sorted(args["dataset"] for _, args in decision.tool_calls) == ["calfire", "epss", "ignitions"]
    assert all(
        tool == "visualization_create" and args["kind"] == "time_series" and args["year"] == 2024
        for tool, args in decision.tool_calls
    )
    missing = route_question("Trend of CPUC ignitions and EPSS outages")
    assert missing.rule == "trend_missing_year"
    assert missing.tool_calls == []
    single = route_question("Show the monthly CAL FIRE incident trend for 2024.")
    assert single.rule == "time_series"
    assert len(single.tool_calls) == 1
    assert "timeline_datasets" not in single.slots
    psps = route_question("Show PSPS and EPSS trend for 2024")
    assert psps.rule != "series_timeline"


def test_bare_ignitions_beside_another_dataset_are_cpuc():
    from services.agent.routing import _datasets

    assert _datasets("ignitions and EPSS outages") == ["epss_outages", "cpuc_ignitions"]
    assert _datasets("ignitions and CAL FIRE incidents") == ["calfire_incidents", "cpuc_ignitions"]
    assert _datasets("PSPS events and ignitions") == ["psps_events", "cpuc_ignitions"]
    timeline = route_question("Show ignitions and EPSS outages over time in 2024")
    assert timeline.rule == "series_timeline"
    assert sorted(timeline.slots["timeline_datasets"]) == ["epss", "ignitions"]
    # Two datasets in a count no longer answer with EPSS alone.
    both = route_question("How many ignitions and EPSS outages in 2024?")
    assert both.slots["dataset"] is None
    assert not any(args.get("dataset") == "epss_outages" for _, args in both.tool_calls)


def test_qualified_or_single_dataset_ignitions_are_unchanged():
    from services.agent.routing import _datasets

    assert _datasets("How many CAL FIRE ignitions in 2024?") == ["calfire_incidents"]
    assert _datasets("How many US ignitions in 2024?") == ["us_ignitions"]
    assert _datasets("How many national ignitions in 2024?") == ["us_ignitions"]
    assert _datasets("How many ignitions in 2024?") == ["cpuc_ignitions"]
    assert _datasets("How many PG&E ignitions in 2024?") == ["cpuc_ignitions"]
    assert _datasets("CPUC ignitions and EPSS outages") == ["epss_outages", "cpuc_ignitions"]
    assert _datasets("How many ignitions in HFTD Tier 3 in 2024?") == ["hftd"]
    assert _datasets("Map EPSS outages for 2024") == ["epss_outages"]
    for question, rule, dataset in (
        ("How many CAL FIRE ignitions in 2024?", "filtered_records", "calfire_incidents"),
        ("How many ignitions in 2024?", "filtered_records", "cpuc_ignitions"),
        ("Map EPSS outages for 2024.", "map", "epss"),
    ):
        decision = route_question(question)
        assert decision.rule == rule, question
        assert decision.tool_calls[0][1]["dataset"] == dataset

