"""Reviewer questions for PR 46: the slot rule plans correctly or falls back.

It must never answer a narrower question than the one asked.
"""

from services.agent.eval.slot_plan import apply_slot_plan, fallback_reason, slot_tool_calls
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


# ---- The invariant: every resolved slot is represented in the planned calls ----


def test_invariant_refuses_a_plan_that_drops_a_resolved_slot():
    from services.agent.eval.slot_plan import _unrepresented

    question = "How many PGE ignitions were there in Butte County and Napa County in 2023?"
    slots = route_question(question).slots
    dropped_utility = [
        ("data_query_records", {"dataset": "cpuc_ignitions", "result_mode": "count", "county": c, "year": 2023})
        for c in ("Butte", "Napa")
    ]
    assert _unrepresented(question, slots, dropped_utility) == "each utility"
    one_county = [
        ("data_query_records", {"dataset": "cpuc_ignitions", "result_mode": "count", "utility": "PGE", "county": "Butte", "year": 2023})
    ]
    assert _unrepresented(question, slots, one_county) == "each county"
    wrong_dataset = [
        ("data_query_records", {"dataset": "calfire_incidents", "result_mode": "count", "utility": "PGE", "county": c, "year": 2023})
        for c in ("Butte", "Napa")
    ]
    assert _unrepresented(question, slots, wrong_dataset) == "dataset"


# ---- Reviewer questions (PR 46 second review) ----


def test_one_utility_in_two_counties_keeps_the_utility_on_every_call():
    question = "How many PGE ignitions were there in Butte County and Napa County in 2023?"
    decision, planned = _slot_calls(question)
    assert decision.rule == "multi_entity_deferred"
    assert planned.rule == "slot_plan"
    assert [(a["utility"], a["county"], a["year"]) for _, a in planned.tool_calls] == [
        ("PGE", "Butte", 2023),
        ("PGE", "Napa", 2023),
    ]


def test_two_utilities_in_two_counties_plan_every_pair():
    question = "How many PGE and SCE ignitions were there in Butte and Napa counties in 2023?"
    decision, planned = _slot_calls(question)
    assert planned.rule == "slot_plan"
    pairs = sorted((a["utility"], a["county"]) for _, a in planned.tool_calls)
    assert pairs == [("PGE", "Butte"), ("PGE", "Napa"), ("SCE", "Butte"), ("SCE", "Napa")]
    assert all(a["year"] == 2023 and a["dataset"] == "cpuc_ignitions" for _, a in planned.tool_calls)


def test_a_month_range_the_router_resolved_to_one_month_falls_back():
    # The router resolves "from March to June 2023" to March only; June is named
    # but outside the window, so a plan would answer a narrower question.
    question = "How many PGE EPSS outages were there each month from March to June 2023?"
    assert fallback_reason(question) == "the date window (a named month is outside it)"
    decision, planned = _slot_calls(question)
    assert planned.rule != "slot_plan"
    assert planned.path == decision.path


def test_list_wording_is_not_answered_with_counts():
    question = "List the PGE and SCE ignitions in 2023."
    assert fallback_reason(question) == "list or records wording"
    decision, planned = _slot_calls(question)
    assert planned.rule != "slot_plan"


def test_counties_on_a_dataset_with_no_county_column_fall_back():
    question = "How many US ignition sample events were there in Butte County and Napa County in 2020?"
    assert fallback_reason(question) == "county (the dataset cannot filter on it)"
    decision, planned = _slot_calls(question)
    assert planned.rule != "slot_plan"


# ---- New probes ----


def test_psps_by_county_falls_back_because_psps_has_no_county_filter():
    question = "How many PSPS events did PGE have in Butte County and Napa County in 2021?"
    assert fallback_reason(question) == "county (the dataset cannot filter on it)"
    assert slot_tool_calls(question) is None


def test_a_county_rank_keeps_the_named_utility():
    question = "How many PGE ignitions by county were there in 2023?"
    calls = slot_tool_calls(question)
    assert calls == [
        (
            "data_query_rank",
            {
                "dataset": "cpuc_ignitions",
                "group_by": "county",
                "metric": "count",
                "utility": "PGE",
                "year": 2023,
            },
        )
    ]


def test_a_sub_year_window_is_carried_on_every_call():
    question = "How many PGE and SCE ignitions were there in August 2023?"
    calls = slot_tool_calls(question)
    assert [a["utility"] for _, a in calls] == ["PGE", "SCE"]
    assert all(
        (a["start_date"], a["end_date"]) == ("2023-08-01", "2023-08-31") for _, a in calls
    )


def test_an_unresolved_part_of_a_year_falls_back():
    question = "How many PGE and SCE ignitions were there in the first half of 2023?"
    assert fallback_reason(question) == "a sub-year window the router did not resolve"
    assert slot_tool_calls(question) is None


def test_two_counties_across_two_named_years_plan_every_pair():
    question = "How many CAL FIRE incidents were there in Butte and Napa counties in 2021 and 2022?"
    calls = slot_tool_calls(question)
    assert sorted((a["county"], a["year"]) for _, a in calls) == [
        ("Butte", 2021),
        ("Butte", 2022),
        ("Napa", 2021),
        ("Napa", 2022),
    ]
    assert all(a["dataset"] == "calfire_incidents" for _, a in calls)


def test_a_measure_a_count_cannot_carry_falls_back():
    # Holdout v3 hv3_047: a county rank of ignition counts is not structures destroyed.
    question = "Give me the number of structures destroyed by PG&E-related ignitions in 2020 by county."
    assert fallback_reason(question) == "a measure other than a count"
    assert slot_tool_calls(question) is None
