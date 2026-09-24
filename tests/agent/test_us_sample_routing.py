"""US-sample wording routes to us_ignitions; a state restriction clarifies (rules F and H)."""

from __future__ import annotations

import pytest

from services.agent.routing import route_question


@pytest.mark.parametrize(
    "question",
    [
        "How many sampled ignitions were there in 2021?",
        "How many ignitions of all causes were in the sample in 2020?",
        "How many all-cause ignitions were in the national sample in 2020?",
        "How many ignitions were in the national sample in 2019?",
        "How many US ignitions were there in 2019?",
        "How many sample wildfire ignitions were there in 2018?",
    ],
)
def test_us_sample_wording_resolves_to_us_ignitions(question):
    decision = route_question(question)
    assert decision.slots["dataset"] == "us_ignitions"
    assert decision.path == "deterministic"
    assert [args["dataset"] for _, args in decision.tool_calls] == ["us_ignitions"]


def test_ho_030_wording_clarifies_instead_of_counting_cpuc():
    # On main this answered with a CPUC utility-ignition count.
    decision = route_question(
        "How many sampled wildfire ignitions of all causes occurred in California in 2016?"
    )
    assert decision.slots["dataset"] == "us_ignitions"
    assert (decision.path, decision.rule) == ("clarification", "unexpressable_county_filter")
    assert decision.tool_calls == []
    assert "no state or county column" in decision.answer


@pytest.mark.parametrize(
    "question",
    [
        # hv2_011, hv3_005, hv3_036, ho_021 (rules F and H)
        "Take 2021 and 2022 separately: how many sampled wildfire ignitions of all causes occurred in California in each year?",
        "What percentage of the sample US wildfire ignitions in California occurred in 2022?",
        "How many sample wildfire ignitions occurred in California from all causes in 2018, 2019, 2020, 2021, and 2022?",
        "Out of the sampled California wildfire ignitions in 2020, what fraction fell inside HFTD Tier 2 or Tier 3?",
        "How many US ignitions were there in Oregon in 2021?",
    ],
)
def test_a_us_sample_question_restricted_to_a_state_clarifies(question):
    decision = route_question(question)
    assert (decision.path, decision.rule) == ("clarification", "unexpressable_county_filter")


def test_a_us_sample_county_count_stays_unsupported():
    # ho_078 is labeled unsupported / unexpressable_county_filter.
    decision = route_question(
        "how many sampled ignitions from all causes were in Sonoma County in 2021?"
    )
    assert (decision.path, decision.rule) == ("unsupported", "unexpressable_county_filter")


@pytest.mark.parametrize(
    "question",
    [
        "How many PGE ignitions were there in 2023?",
        "How many utility-caused ignitions were there in California in 2022?",
        "How many CPUC ignitions were there in Sonoma County in 2023?",
        "Show me a sample of PGE ignitions in 2023",
        "List a sample of SCE ignitions from 2022",
        "How many ignitions were there in 2024?",
    ],
)
def test_ordinary_cpuc_ignition_questions_are_unchanged(question):
    decision = route_question(question)
    assert decision.slots["dataset"] == "cpuc_ignitions"
    assert decision.rule != "unexpressable_county_filter"
    assert all(args["dataset"] == "cpuc_ignitions" for _, args in decision.tool_calls)
