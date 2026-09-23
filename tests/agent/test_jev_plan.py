"""Planner maps facts and slots to tool calls. It does not call Jev."""

from types import SimpleNamespace

from services.agent.decisions.planner import plan_calls
from services.agent.orchestrator import _render_deterministic


def _facts(**overrides):
    base = dict(
        wants_count=0.0,
        wants_list=0.0,
        wants_time_series=0.0,
        wants_map=0.0,
        wants_ranking=0.0,
        wants_comparison=0.0,
        is_multi_part=0.0,
        breakdown="none",
        breakdown_confidence=0.95,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_per_month_plans_a_monthly_series():
    calls, reason = plan_calls(
        _facts(breakdown="by_month"),
        {"dataset": "epss_outages", "year": 2023, "utilities": ["SCE"]},
    )
    assert reason == "planned"
    assert calls == [
        (
            "visualization_create",
            {
                "kind": "time_series",
                "dataset": "epss",
                "interval": "monthly",
                "year": 2023,
                "utility": "SCE",
            },
        )
    ]


def test_several_named_years_plan_one_count_each():
    calls, reason = plan_calls(
        _facts(wants_count=0.9),
        {
            "dataset": "cpuc_ignitions",
            "years": [2018, 2019, 2020],
            "utilities": ["Liberty"],
        },
    )
    assert reason == "planned"
    assert [name for name, _ in calls] == ["data_query_records"] * 3
    assert [args["year"] for _, args in calls] == [2018, 2019, 2020]


def test_two_years_with_a_comparison_use_periods():
    calls, reason = plan_calls(
        _facts(wants_count=0.9, wants_comparison=0.9),
        {"dataset": "cpuc_ignitions", "years": [2017, 2021], "utilities": ["PGE"]},
    )
    assert reason == "planned"
    assert calls[0][0] == "comparison_run"
    assert calls[0][1]["kind"] == "periods"


def test_several_utilities_plan_one_count_each():
    calls, reason = plan_calls(
        _facts(wants_count=0.92),
        {
            "dataset": "cpuc_ignitions",
            "year": 2022,
            "utilities": ["PGE", "SCE", "SDGE"],
        },
    )
    assert reason == "planned"
    assert [args["utility"] for _, args in calls] == ["PGE", "SCE", "SDGE"]


def test_by_county_plans_a_rank():
    calls, reason = plan_calls(
        _facts(breakdown="by_county", wants_ranking=0.9),
        {"dataset": "cpuc_ignitions", "year": 2023, "utilities": []},
    )
    assert reason == "planned"
    assert calls[0][0] == "data_query_rank"
    assert calls[0][1]["group_by"] == "county"


def test_count_plus_chart_plans_two_tools():
    calls, reason = plan_calls(
        _facts(wants_count=0.9, wants_time_series=0.9),
        {"dataset": "calfire_incidents", "year": 2024, "utilities": []},
    )
    assert reason == "planned"
    assert [name for name, _ in calls] == [
        "data_query_records",
        "visualization_create",
    ]


def test_missing_year_falls_back():
    calls, reason = plan_calls(
        _facts(wants_count=0.95),
        {"dataset": "cpuc_ignitions", "utilities": ["PGE", "SCE"]},
    )
    assert calls is None
    assert reason == "missing_slot"


def test_plan_over_the_call_limit_falls_back():
    calls, reason = plan_calls(
        _facts(wants_count=0.95),
        {
            "dataset": "cpuc_ignitions",
            "years": [2014, 2015, 2016, 2017, 2018, 2019, 2020],
            "utilities": [],
        },
    )
    assert calls is None
    assert reason == "over_limit"


def test_low_confidence_breakdown_falls_back():
    calls, reason = plan_calls(
        _facts(breakdown="by_month", breakdown_confidence=0.4),
        {"dataset": "epss_outages", "year": 2023, "utilities": []},
    )
    assert calls is None
    assert reason == "low_confidence"


def test_plan_facts_use_the_facts_call():
    from services.agent.decisions import planner
    from services.agent.decisions import typesafe_backend

    seen = {}

    class _Backend:
        def __init__(self, **kwargs):
            pass

        def evaluate(self, state, questions, **kwargs):
            seen["questions"] = set(questions)
            return None

    original = typesafe_backend.TypeSafeBackend
    typesafe_backend.TypeSafeBackend = _Backend
    try:
        assert planner.load_plan_facts("How many in 2024?", object()) is None
    finally:
        typesafe_backend.TypeSafeBackend = original
    assert "wants_count" in seen["questions"]
    assert "tool_pick" not in seen["questions"]


def test_every_rendered_number_traces_to_evidence():
    class _Exec:
        ok = True
        qualification_call = False
        tool = "data_query_records"
        arguments = {"year": 2024, "utility": "PGE"}
        summary = {
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "total": 17,
            "filters": {"utility": "PGE", "year": 2024},
        }

    text = _render_deterministic([_Exec()])
    assert "17" in text
    evidence = str(_Exec.summary) + str(_Exec.arguments)
    for number in ("17", "2024"):
        assert number in text
        assert number in evidence
    assert "999" not in text
