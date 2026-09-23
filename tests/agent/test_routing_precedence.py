"""Keyword collisions and relative-date slot fill for the deterministic router."""

from __future__ import annotations

from datetime import date

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


def test_around_a_year_or_close_to_a_number_is_not_a_place():
    around = route_question("Around 2023, how many CPUC ignitions were there?")
    assert around.rule != "undefined_spatial_scope"
    assert around.slots["dataset"] == "cpuc_ignitions"
    close = route_question("Were CPUC ignitions close to 500 in 2024?")
    assert close.rule != "undefined_spatial_scope"
    assert close.slots["dataset"] == "cpuc_ignitions"
