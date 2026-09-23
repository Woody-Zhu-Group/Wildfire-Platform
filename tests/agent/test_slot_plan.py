"""Slot plans do not call Jev."""

from services.agent.eval.slot_plan import apply_slot_plan, slot_plan, slot_tool_calls
from services.agent.orchestrator import _render_deterministic
from services.agent.routing import route_question


def test_a_monthly_trend_alone_is_one_series():
    plan = slot_plan("Show the monthly CAL FIRE incident trend for 2024")
    assert plan == ["visualization_create"]
    monthly_count = slot_plan(
        "For PGE, what was the monthly count of EPSS outages across 2022?"
    )
    assert monthly_count == ["visualization_create"]
    each_month = slot_plan("How many PGE EPSS outages were there in each month of 2023?")
    assert each_month == ["visualization_create"]


def test_a_monthly_breakdown_plus_a_total_adds_the_count():
    plan = slot_plan("Give me the PGE ignition count and its monthly trend for 2024")
    assert plan == ["data_query_records", "visualization_create"]
    yearly = slot_plan("PGE EPSS events by month for 2023 plus the yearly total")
    assert yearly == ["data_query_records", "visualization_create"]


def test_slot_plan_replaces_only_a_deferred_multi_entity_route():
    question = "How many PGE, SCE, and SDGE ignitions were there in 2022?"
    deferred = route_question(question)
    assert deferred.rule == "multi_entity_deferred"
    assert deferred.path == "model"
    planned = apply_slot_plan(deferred, question)
    assert planned.path == "deterministic"
    assert planned.rule == "slot_plan"
    assert [name for name, _ in planned.tool_calls] == ["data_query_records"] * 3
    assert [args["utility"] for _, args in planned.tool_calls] == ["PGE", "SCE", "SDGE"]
    clarify = route_question("Show ignitions near the town")
    assert apply_slot_plan(clarify, "Show ignitions near the town").path == "clarification"


def test_more_than_ten_entities_falls_back():
    question = (
        "How many PGE ignitions were there in 2014, 2015, 2016, 2017, 2018, "
        "2019, 2020, 2021, 2022, 2023, and 2024?"
    )
    assert slot_tool_calls(question) is None
    decision = apply_slot_plan(route_question(question), question)
    assert decision.path == "model"


def test_rendered_slot_numbers_trace_to_evidence():
    class _Exec:
        def __init__(self, total: int, utility: str):
            self.ok = True
            self.qualification_call = False
            self.tool = "data_query_records"
            self.arguments = {"year": 2022, "utility": utility}
            self.summary = {
                "dataset": "cpuc_ignitions",
                "result_mode": "count",
                "total": total,
                "filters": {"utility": utility, "year": 2022},
            }

    executions = [_Exec(4, "PGE"), _Exec(11, "SCE"), _Exec(2, "SDGE")]
    text = _render_deterministic(executions)
    evidence = " ".join(
        str(item.summary) + str(item.arguments) for item in executions
    )
    for number in ("4", "11", "2", "2022"):
        assert number in text
        assert number in evidence
    assert "999" not in text
