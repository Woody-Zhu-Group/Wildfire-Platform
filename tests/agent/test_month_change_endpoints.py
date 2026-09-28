"""A month-range comparison keeps each endpoint across model turns (#109)."""

import pytest

from services.agent.derived import DERIVED_TOOL
from services.agent.time_resolve import call_window
from tests.agent.test_change_endpoints import (
    _ask_with_jev,
    _count_call,
    _jev_facts,
    _primary_counts,
)


# Written before the implementation; fixture counts are not warehouse values.
CASES = [
    ("Did EPSS outages go up from September 2023 to September 2024?", "epss_outages", "2023-09-01", "2023-09-30", "2024-09-01", "2024-09-30"),
    ("How did PG&E's ignition count change from March 2021 to November 2022?", "cpuc_ignitions", "2021-03-01", "2021-03-31", "2022-11-01", "2022-11-30"),
    ("What was the shift in PG&E ignitions from February to June 2024?", "cpuc_ignitions", "2024-02-01", "2024-02-29", "2024-06-01", "2024-06-30"),
    ("Describe the movement in PG&E ignitions from December 2020 through February 2023.", "cpuc_ignitions", "2020-12-01", "2020-12-31", "2023-02-01", "2023-02-28"),
    ("By how much did PG&E ignitions differ from July to August 2024?", "cpuc_ignitions", "2024-07-01", "2024-07-31", "2024-08-01", "2024-08-31"),
]


def _calls(case):
    _, dataset, first, first_end, last, last_end = case
    return [
        _count_call(1, dataset=dataset, utility="PGE", start_date=first, end_date=first_end),
        _count_call(2, dataset=dataset, utility="PGE", start_date=last, end_date=last_end),
    ]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("intent,confidence", [("compare", 0.95), ("trend", 0.8)])
@pytest.mark.parametrize("separate_turns", [True, False])
def test_month_endpoints_are_kept_and_change_is_derived(case, intent, confidence, separate_turns):
    calls = _calls(case)
    response = _ask_with_jev(
        case[0], calls, _jev_facts(intent, confidence),
        turns=[[call] for call in calls] if separate_turns else None,
    )
    assert response["status"] == "answer", response["answer_text"]
    assert [call_window(e["arguments"]) for e in _primary_counts(response)] == [
        (case[2], case[3]), (case[4], case[5]),
    ]
    assert not [e for e in response["trajectory"] if e.get("type") == "harness_time_correction"]
    assert [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
    if separate_turns:
        continued = [e for e in response["trajectory"] if e.get("type") == "uncovered_entities_continue"]
        assert continued and f"month:{case[4][:7]}" in continued[0]["missing"]


def test_missing_endpoint_month_declines_instead_of_answering_a_span_total():
    case = CASES[0]
    response = _ask_with_jev(case[0], _calls(case)[:1], _jev_facts("compare", 0.95))
    assert response["status"] == "error"
    assert "month:2024-09" in str(response["trajectory"])
    assert not [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]


@pytest.mark.parametrize("jev", [None, _jev_facts("count", 0.95), _jev_facts("compare", 0.79)])
def test_without_change_intent_a_lone_month_still_reads_the_full_span(jev):
    case = CASES[0]
    response = _ask_with_jev(case[0], _calls(case)[:1], jev)
    assert [call_window(e["arguments"]) for e in _primary_counts(response)] == [(case[2], case[5])]
    assert not [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]


def test_whole_year_reads_do_not_answer_a_month_comparison():
    case = CASES[0]
    response = _ask_with_jev(case[0], [
        _count_call(1, dataset="epss_outages", utility="PGE", year=2023),
        _count_call(2, dataset="epss_outages", utility="PGE", year=2024),
    ], _jev_facts("compare", 0.95))
    assert response["status"] == "error"
    assert not [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
