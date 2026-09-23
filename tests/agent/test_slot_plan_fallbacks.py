"""Reviewer questions for PR 46: the slot rule plans correctly or falls back.

It must never answer a narrower question than the one asked.
"""

from services.agent.eval.slot_plan import apply_slot_plan, slot_tool_calls
from services.agent.routing import route_question


def _slot_calls(question):
    decision = route_question(question)
    planned = apply_slot_plan(decision, question)
    return decision, planned


def test_two_utilities_in_one_county_keep_the_county_on_every_call():
    question = "How many PGE and SCE ignitions were there in Sonoma County in 2023?"
    decision, planned = _slot_calls(question)
    assert decision.rule == "multi_entity_deferred"
    assert planned.rule == "slot_plan"
    assert [args["utility"] for _, args in planned.tool_calls] == ["PGE", "SCE"]
    assert all(args["county"] == "Sonoma" for _, args in planned.tool_calls)
    assert all(args["year"] == 2023 for _, args in planned.tool_calls)


def test_a_month_window_across_two_years_falls_back():
    question = "How many PGE ignitions were there in August 2022 and August 2023?"
    assert slot_tool_calls(question) is None
    decision, planned = _slot_calls(question)
    assert planned.rule != "slot_plan"
    assert planned.path == decision.path


def test_a_monthly_breakdown_for_two_utilities_falls_back():
    question = "How many PGE and SCE EPSS outages were there in each month of 2023?"
    assert slot_tool_calls(question) is None
    decision, planned = _slot_calls(question)
    assert planned.rule != "slot_plan"


def test_a_two_period_comparison_keeps_both_periods_and_the_utility():
    question = "Compare PGE EPSS outages in 2022 vs 2023."
    decision, planned = _slot_calls(question)
    if planned.rule == "slot_plan":
        years = sorted(args["year"] for _, args in planned.tool_calls)
        assert years == [2022, 2023]
        assert all(args.get("utility") == "PGE" for _, args in planned.tool_calls)
        assert all(args["dataset"] == "epss_outages" for _, args in planned.tool_calls)
    else:
        assert planned.path == decision.path and planned.rule == decision.rule


def test_a_two_utility_comparison_keeps_both_utilities_and_the_dataset():
    question = "Compare PSPS events for PGE vs SCE in 2021."
    decision, planned = _slot_calls(question)
    if planned.rule == "slot_plan":
        assert sorted(args["utility"] for _, args in planned.tool_calls) == ["PGE", "SCE"]
        assert all(args["dataset"] == "psps_events" for _, args in planned.tool_calls)
        assert all(args["year"] == 2021 for _, args in planned.tool_calls)
    else:
        assert planned.path == decision.path and planned.rule == decision.rule


def test_a_count_plus_a_where_never_becomes_counts_alone():
    question = "How many PGE ignitions were there in 2023 and where were they?"
    assert slot_tool_calls(question) is None
    decision, planned = _slot_calls(question)
    assert planned.rule != "slot_plan"


def test_other_metrics_and_maps_fall_back():
    for question in (
        "How many acres burned in CAL FIRE incidents in Butte County and Napa County in 2020?",
        "Map PGE and SCE ignitions in 2022.",
        "How many customers were affected by PGE and SCE PSPS events in 2021?",
    ):
        assert slot_tool_calls(question) is None, question
