"""A change between two years is two endpoint periods, and the harness never
rewrites distinct periods the model chose.

Production answered "By what percentage did SCE's CPUC ignitions change
between 2020 and 2023?" with the same 2020-2023 count twice: the question
resolved to one span, the hold-window rule widened the model's per-year calls
to that span, and the fallback printed the same line twice. A second report
asked for July 2024 and August 2024 as two calls and got the August count
twice. The reviewer's twenty questions (tests/agent/fixtures/
pr95_change_questions.json) were written without looking at the fix. Counts
here are fixture values, not warehouse figures.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.derived import DERIVED_TOOL, requested_operations
from services.agent.orchestrator import AgentOrchestrator, _render_deterministic
from services.agent.provider import ModelReply
from services.agent.routing import route_question
from services.agent.time_resolve import (
    apply_harness_years,
    asks_change_between_years,
    call_window,
    resolve_time,
)
from services.agent.tools import ToolExecution, ToolExecutor

TODAY = date(2026, 9, 24)
QUESTION = "By what percentage did SCE's CPUC ignitions change between 2020 and 2023?"
COUNTY_QUESTION = "How did CPUC ignitions in Sonoma County change from 2020 to 2023?"
COUNT_CONTROL = "How many CPUC ignitions did SCE have between 2020 and 2023?"
MONTHS_QUESTION = "How many CPUC ignitions did PG&E have in July 2024 and August 2024?"
REVIEWER = json.loads(
    (Path(__file__).parent / "fixtures" / "pr95_change_questions.json").read_text(encoding="utf-8")
)
SCE = {"2020": 75, "2023": 90}
COUNTY_COUNTS = {("Butte", 2019): 40, ("Butte", 2022): 30, ("Shasta", 2019): 12, ("Shasta", 2022): 18}
MONTH_COUNTS = {"07": 61, "08": 98}


# --- time resolution -------------------------------------------------------


def test_the_production_question_resolves_to_two_endpoint_years():
    resolved = resolve_time(QUESTION, today=TODAY)
    assert resolved.status == "explicit"
    assert resolved.years == (2020, 2023)
    assert resolved.start_date is None and resolved.end_date is None
    assert resolved.per_year is True
    assert resolved.endpoints is True
    assert resolved.as_slot()["endpoints"] is True


@pytest.mark.parametrize("question", REVIEWER["change"] + [REVIEWER["user_case"]])
def test_every_reviewer_change_question_is_two_endpoint_years(question):
    resolved = resolve_time(question, today=TODAY)
    assert resolved.endpoints is True
    assert len(resolved.years) == 2
    assert resolved.start_date is None and resolved.end_date is None
    assert resolved.per_year is True
    assert asks_change_between_years(question, listed_years=True)
    # The same registry pattern tells the derived arithmetic to compute a difference.
    assert "difference" in requested_operations(question)


@pytest.mark.parametrize("question", REVIEWER["total_span"])
def test_every_reviewer_total_question_stays_one_span(question):
    resolved = resolve_time(question, today=TODAY)
    assert resolved.endpoints is False
    assert resolved.per_year is False
    assert resolved.years == tuple(range(resolved.years[0], resolved.years[-1] + 1))
    assert resolved.start_date == f"{resolved.years[0]}-01-01"
    assert resolved.end_date == f"{resolved.years[-1]}-12-31"
    assert not asks_change_between_years(question)
    assert requested_operations(question) == set()


def test_other_change_forms_are_endpoints_too():
    for question in (
        "Did PG&E EPSS outages increase between 2021 and 2023?",
        "Percent change in SCE ignitions 2020-2023?",
        "What is the ratio of SCE ignitions between 2020 and 2023?",
        "SCE ignitions in 2020 versus 2023",
        "Compare SCE ignitions in 2020 and 2023",
        "compare calfire wildfire totals in Ventura County for 2017 and 2022",
        # A named period is still a change question unless it asks for a total.
        "How did ignitions change over the period 2019 to 2022?",
        "What was the total change in SCE ignitions from 2019 to 2022?",
    ):
        resolved = resolve_time(question, today=TODAY)
        assert resolved.endpoints is True, question
        assert len(resolved.years) == 2 and resolved.start_date is None, question


def test_spans_that_are_not_change_questions_stay_spans():
    for question in (
        "SCE ignitions 2020-2023",
        "List SCE ignitions between 2020 and 2023",
        # A share, not a change.
        "What percent of SCE ignitions between 2020 and 2023 were in HFTD?",
        # A named metric ratio over the range, not a ratio between two years.
        "What is PG&E's EPSS to ignition ratio between 2021 and 2023?",
        # A size threshold, not a comparative between years.
        "How many CAL FIRE fires larger than 100 acres between 2019 and 2022?",
        # Two entities compared over one span: compare words alone do not split a range.
        "Compare Liberty and Bear Valley utility-caused ignition totals from 2019 through 2023.",
        "Take the 2019-2022 period and compare SDGE's ignition totals with SCE's.",
    ):
        resolved = resolve_time(question, today=TODAY)
        assert resolved.endpoints is False, question
        assert resolved.start_date == f"{resolved.years[0]}-01-01", question


def test_a_per_year_change_question_keeps_the_span_with_per_year_calls():
    question = "Between 2017 and 2023, how did SDGE's annual utility-caused ignition count change?"
    resolved = resolve_time(question, today=TODAY)
    assert resolved.endpoints is False
    assert resolved.per_year is True
    assert resolved.years == tuple(range(2017, 2024))
    assert resolved.start_date == "2017-01-01" and resolved.end_date == "2023-12-31"


def test_an_endpoint_outside_coverage_still_clarifies():
    resolved = resolve_time("How did SCE ignitions change between 2010 and 2023?", today=TODAY)
    assert resolved.status == "out_of_coverage"
    assert "2010" in (resolved.reason or "")


def test_two_named_months_keep_both_months():
    resolved = resolve_time(MONTHS_QUESTION, today=TODAY)
    assert resolved.years == (2024,)
    assert (resolved.start_date, resolved.end_date) == ("2024-07-01", "2024-08-31")
    assert resolved.per_year is True
    assert resolved.phrase == "july, august 2024"
    # One month, or a month range, is unchanged.
    single = resolve_time("How many PG&E ignitions in August 2024?", today=TODAY)
    assert (single.start_date, single.end_date) == ("2024-08-01", "2024-08-31")
    assert single.per_year is False
    ranged = resolve_time("How many PG&E ignitions from July to August 2024?", today=TODAY)
    assert (ranged.start_date, ranged.end_date) == ("2024-07-01", "2024-08-31")
    assert ranged.per_year is False


# --- harness window rules --------------------------------------------------


def _hold(arguments: dict, question: str, turn_windows: list | None = None):
    corrections: list[dict] = []
    filled, error = apply_harness_years(
        arguments,
        time_resolution=resolve_time(question, today=TODAY).as_slot(),
        today=TODAY,
        hold_window=True,
        corrections=corrections,
        turn_windows=turn_windows,
    )
    return filled, error, corrections


def _count_args(**args) -> dict:
    return {"dataset": "cpuc_ignitions", "result_mode": "count", "utility": "SCE", **args}


def test_distinct_periods_in_one_turn_are_never_widened_whatever_the_wording():
    # No change word at all: the model still chose 2019 and 2022 on purpose.
    question = "Show SCE ignitions from 2019 to 2022"
    assert resolve_time(question, today=TODAY).endpoints is False
    windows = [call_window(_count_args(year=2019)), call_window(_count_args(year=2022))]
    assert windows == [("2019-01-01", "2019-12-31"), ("2022-01-01", "2022-12-31")]
    for year in (2019, 2022):
        filled, error, corrections = _hold(_count_args(year=year), question, windows)
        assert error is None
        assert filled["year"] == year and "start_date" not in filled
        assert corrections == []


def test_a_single_narrowing_call_is_still_widened():
    question = "Show SCE ignitions from 2019 to 2022"
    filled, error, corrections = _hold(
        _count_args(year=2019), question, [call_window(_count_args(year=2019))]
    )
    assert error is None
    assert (filled["start_date"], filled["end_date"]) == ("2019-01-01", "2022-12-31")
    assert corrections and corrections[0]["rule"] == "hold_resolved_window"
    # Two calls that both narrow to the same window are one narrowing call.
    same = [
        call_window(_count_args(year=2019)),
        call_window(_count_args(start_date="2019-01-01", end_date="2019-12-31")),
    ]
    filled, error, _ = _hold(_count_args(year=2019), question, same)
    assert (filled["start_date"], filled["end_date"]) == ("2019-01-01", "2022-12-31")


def test_distinct_months_in_one_turn_are_never_rewritten():
    july = _count_args(utility="PGE", start_date="2024-07-01", end_date="2024-07-31")
    august = _count_args(utility="PGE", start_date="2024-08-01", end_date="2024-08-31")
    windows = [call_window(july), call_window(august)]
    for call in (july, august):
        filled, error, corrections = _hold(call, MONTHS_QUESTION, windows)
        assert error is None
        assert (filled["start_date"], filled["end_date"]) == (call["start_date"], call["end_date"])
        assert corrections == []
    # A July call for an August question is still corrected to August.
    filled, error, _ = _hold(july, "How many PG&E ignitions in August 2024?", [call_window(july)])
    assert error is None
    assert (filled["start_date"], filled["end_date"]) == ("2024-08-01", "2024-08-31")


def test_the_harness_does_not_widen_endpoint_calls():
    for year in (2020, 2023):
        filled, error, corrections = _hold(_count_args(year=year), QUESTION)
        assert error is None
        assert filled["year"] == year and "start_date" not in filled
        assert corrections == []
    # A year that is neither endpoint is rejected as unlisted.
    _filled, error, _ = _hold(_count_args(year=2021), QUESTION)
    assert error is not None and "2021" in error


def test_the_span_control_is_still_held_to_the_whole_range():
    filled, error, corrections = _hold(_count_args(year=2020), COUNT_CONTROL)
    assert error is None
    assert (filled["start_date"], filled["end_date"]) == ("2020-01-01", "2023-12-31")
    assert corrections and corrections[0]["rule"] == "hold_resolved_window"


# --- routing ---------------------------------------------------------------


def test_the_production_question_routes_to_a_utility_period_comparison():
    decision = route_question(QUESTION)
    assert decision.path == "deterministic"
    assert decision.rule == "period_comparison"
    tool, args = decision.tool_calls[0]
    assert tool == "comparison_run"
    assert args["kind"] == "periods"
    assert args["scope_type"] == "utility" and args["scope"] == "SCE"
    assert args["metric"] == "ignition_count"
    assert (args["period_a_start"], args["period_a_end"]) == ("2020-01-01", "2020-12-31")
    assert (args["period_b_start"], args["period_b_end"]) == ("2023-01-01", "2023-12-31")
    assert decision.slots["time_resolution"]["endpoints"] is True


def test_a_single_county_change_question_routes_to_a_county_period_comparison():
    decision = route_question(COUNTY_QUESTION)
    assert decision.rule == "period_comparison"
    tool, args = decision.tool_calls[0]
    assert tool == "comparison_run"
    assert args["scope_type"] == "county" and args["scope"] == "Sonoma"
    assert (args["period_a_start"], args["period_b_start"]) == ("2020-01-01", "2023-01-01")


def test_reviewer_single_scope_change_questions_route_to_period_comparison():
    single_scope = [
        q
        for q in REVIEWER["change"]
        if not any(word in q.lower() for word in ("psps", "tier", "epss outages drop"))
    ]
    assert len(single_scope) == 12
    for question in single_scope:
        decision = route_question(question)
        assert decision.rule == "period_comparison", question
        args = decision.tool_calls[0][1]
        years = sorted(resolve_time(question, today=TODAY).years)
        assert args["period_a_start"] == f"{years[0]}-01-01", question
        assert args["period_b_start"] == f"{years[1]}-01-01", question


def test_reviewer_total_questions_never_route_to_a_period_comparison():
    for question in REVIEWER["total_span"]:
        decision = route_question(question)
        assert decision.rule != "period_comparison", question
        for _tool, args in decision.tool_calls:
            assert args.get("kind") != "periods", question


def test_a_two_county_change_question_is_endpoints_but_not_one_period_comparison():
    decision = route_question(REVIEWER["user_case"])
    assert decision.rule != "period_comparison"
    assert decision.path == "model"
    slot = decision.slots["time_resolution"]
    assert slot["endpoints"] is True and slot["years"] == [2019, 2022]


def test_the_count_control_still_counts_the_whole_span():
    decision = route_question(COUNT_CONTROL)
    assert decision.rule == "filtered_records"
    tool, args = decision.tool_calls[0]
    assert tool == "data_query_records"
    assert (args["start_date"], args["end_date"]) == ("2020-01-01", "2023-12-31")


def test_two_named_months_are_not_counted_as_one_month():
    decision = route_question(MONTHS_QUESTION)
    assert decision.path == "model"
    assert decision.rule == "multi_entity_deferred"


def test_a_change_question_that_also_asks_for_a_chart_is_not_a_bare_period_comparison():
    decision = route_question(
        "How many utility-caused ignitions occurred in Sonoma County in 2018 and 2021, "
        "and can you chart the comparison?"
    )
    assert decision.rule != "period_comparison"


# --- the answer ------------------------------------------------------------


def _handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    if request.url.path.endswith("/compare-periods"):
        assert params.get("scope_type") == "utility" and params.get("scope") == "SCE"
        a = SCE[str(params.get("period_a_start"))[:4]]
        b = SCE[str(params.get("period_b_start"))[:4]]
        return httpx.Response(
            200,
            json={
                "metric": params.get("metric"),
                "scope_type": "utility",
                "scope": "SCE",
                "period_a": {"start": params.get("period_a_start"), "end": params.get("period_a_end"), "value": a},
                "period_b": {"start": params.get("period_b_start"), "end": params.get("period_b_end"), "value": b},
                "delta": {"value": b - a},
                "meta": {},
            },
        )
    start = str(params.get("start_date") or "")
    year = int(str(params.get("year") or start or "0")[:4] or 0)
    county = params.get("county")
    if county:
        total = COUNTY_COUNTS[(county, year)]
    elif start[:4] == "2024":
        total = MONTH_COUNTS[start[5:7]]
    else:
        total = SCE.get(str(year), 0)
    if "spatial" in request.url.path:
        return httpx.Response(
            200,
            json={
                "region": {"kind": "utility", "id": params.get("utility")},
                "start_date": params.get("start_date"),
                "end_date": params.get("end_date"),
                "counts": {"ignitions": total},
                "meta": {},
            },
        )
    return httpx.Response(200, json={"data": [], "meta": {"total": total, "returned": 0, "filters": {}}})


class ScriptedProvider:
    """Routes with the scripted calls, then fails synthesis so the fallback renders."""

    def __init__(self, routing_replies: list[list[dict]] | None = None) -> None:
        self.routing_replies = list(routing_replies or [])

    async def complete(self, **kwargs):
        if kwargs.get("tools"):
            calls = self.routing_replies.pop(0) if self.routing_replies else []
            return ModelReply(
                content="" if calls else "I have what I need.",
                tool_calls=calls,
                raw={"choices": [{"finish_reason": "tool_calls" if calls else "stop"}]},
                latency_ms=1.0,
                usage={},
            )
        return ModelReply(
            content="not json", tool_calls=[], raw={"choices": [{"finish_reason": "stop"}]}, latency_ms=1.0, usage={}
        )


def _ask(provider: ScriptedProvider, question: str, *, force_model: bool = False) -> dict:
    settings = AgentSettings(max_tool_steps=3)
    executor = ToolExecutor(settings, ArtifactStore(60), transport=httpx.MockTransport(_handler))

    async def run():
        try:
            orchestrator = AgentOrchestrator(settings, provider, executor)
            return (await orchestrator.ask(question, force_model=force_model)).response
        finally:
            await executor.close()

    return asyncio.run(run())


def _count_call(index: int, **args) -> dict:
    return {
        "id": f"call_{index}",
        "type": "function",
        "function": {
            "name": "data_query_records",
            "arguments": json.dumps({"dataset": "cpuc_ignitions", "result_mode": "count", **args}),
        },
    }


def _primary_counts(response: dict) -> list[dict]:
    return [
        e
        for e in response["evidence"]
        if e["tool"] == "data_query_records" and not e.get("qualification_call")
    ]


def test_the_production_question_gets_both_yearly_counts_and_the_percent_change():
    response = _ask(ScriptedProvider(), QUESTION)
    assert response["status"] == "answer"
    assert response["route"]["rule"] == "period_comparison"
    assert response["route"]["answer_origin"] == "deterministic"
    assert not [e for e in response["trajectory"] if e.get("type") == "grounding_error"]

    # One primary comparison; the spatial companion is a qualification call.
    comparisons = [
        e
        for e in response["evidence"]
        if e["tool"] == "comparison_run" and e["arguments"].get("ignition_definition") == "attribute"
    ]
    assert len(comparisons) == 1
    summary = comparisons[0]["summary"]
    assert summary["period_a"]["value"] == 75 and summary["period_b"]["value"] == 90

    derived = [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
    assert len(derived) == 1
    rows = derived[0]["summary"]["derivations"]
    assert len(rows) == 1
    row = rows[0]
    assert row["basis"] == "change_over_time"
    assert row["from"] == {"entity": "utility=SCE", "period": "2020", "value": 75}
    assert row["to"] == {"entity": "utility=SCE", "period": "2023", "value": 90}
    assert row["difference"] == 15
    assert row["percent_change"] == 20
    assert row["direction"] == "increase"
    assert row["source_evidence_ids"] == [comparisons[0]["id"]]

    # The rendered answer carries both yearly counts and the percentage, once each.
    text = response["answer_text"]
    assert text.count("period A=75, period B=90") == 1
    assert text.count("percent change +20%") == 1
    assert text.count("difference +15") == 1


def test_butte_and_shasta_end_to_end_keeps_four_calls_and_derives_each_county_change():
    calls = [
        _count_call(1, county="Butte", year=2019),
        _count_call(2, county="Butte", year=2022),
        _count_call(3, county="Shasta", year=2019),
        _count_call(4, county="Shasta", year=2022),
    ]
    response = _ask(ScriptedProvider([calls]), REVIEWER["user_case"])
    assert response["status"] == "answer"
    counts = {(e["arguments"]["county"], e["arguments"]["year"]): e["summary"]["total"] for e in _primary_counts(response)}
    assert counts == COUNTY_COUNTS
    assert not [e for e in response["trajectory"] if e.get("type") == "duplicate_tool_call_suppressed"]

    derived = [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
    assert len(derived) == 1
    rows = derived[0]["summary"]["derivations"]
    assert [row["basis"] for row in rows] == ["change_over_time", "change_over_time"]
    by_county = {row["to"]["entity"]: row for row in rows}
    assert by_county["county=Butte"]["difference"] == -10
    assert by_county["county=Butte"]["direction"] == "decrease"
    assert by_county["county=Shasta"]["difference"] == 6
    assert by_county["county=Shasta"]["direction"] == "increase"
    text = response["answer_text"]
    for value in ("40", "30", "12", "18"):
        assert text.count(f"count: {value} ") == 1, value


def test_july_and_august_end_to_end_return_two_different_counts():
    calls = [
        _count_call(1, utility="PGE", start_date="2024-07-01", end_date="2024-07-31"),
        _count_call(2, utility="PGE", start_date="2024-08-01", end_date="2024-08-31"),
    ]
    response = _ask(ScriptedProvider([calls]), MONTHS_QUESTION)
    assert response["status"] == "answer"
    counts = {e["arguments"]["start_date"][5:7]: e["summary"]["total"] for e in _primary_counts(response)}
    assert counts == {"07": 61, "08": 98}
    assert not [e for e in response["trajectory"] if e.get("type") == "duplicate_tool_call_suppressed"]
    assert response["answer_text"].count("count: 61 ") == 1
    assert response["answer_text"].count("count: 98 ") == 1


def test_two_model_calls_corrected_to_the_same_window_run_once_and_render_once():
    # A span question on the model path: both calls narrow to 2020 in
    # different spellings, both are held to the 2020-2023 span, and the second
    # is suppressed before it runs.
    question = "What happened with SCE CPUC ignitions between 2020 and 2023?"
    assert route_question(question).path == "model"
    calls = [
        _count_call(1, utility="SCE", year=2020),
        _count_call(2, utility="SCE", start_date="2020-01-01", end_date="2020-12-31"),
    ]
    response = _ask(ScriptedProvider([calls]), question)
    counts = _primary_counts(response)
    assert len(counts) == 1
    assert (counts[0]["arguments"]["start_date"], counts[0]["arguments"]["end_date"]) == (
        "2020-01-01",
        "2023-12-31",
    )
    suppressed = [e for e in response["trajectory"] if e.get("type") == "duplicate_tool_call_suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0]["reason"] == "identical after harness correction"
    assert suppressed[0]["executed_arguments"]["end_date"] == "2023-12-31"
    assert response["answer_text"].count("cpuc_ignitions count:") == 1


def test_the_production_question_on_the_model_path_keeps_one_call_per_endpoint_year():
    calls = [_count_call(1, utility="SCE", year=2020), _count_call(2, utility="SCE", year=2023)]
    response = _ask(ScriptedProvider([calls]), QUESTION, force_model=True)
    counts = {e["arguments"]["year"]: e["summary"]["total"] for e in _primary_counts(response)}
    assert counts == {2020: 75, 2023: 90}
    assert not [e for e in response["trajectory"] if e.get("type") == "duplicate_tool_call_suppressed"]
    derived = [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
    assert derived and derived[0]["summary"]["derivations"][0]["percent_change"] == 20


def test_identical_evidence_renders_one_fallback_line():
    execution = ToolExecution(
        tool="data_query_records",
        arguments={"dataset": "cpuc_ignitions", "result_mode": "count", "utility": "SCE", "start_date": "2020-01-01", "end_date": "2023-12-31"},
        ok=True,
        summary={"dataset": "cpuc_ignitions", "result_mode": "count", "total": 529},
        raw=None,
        error=None,
        artifact=None,
        latency_ms=0.0,
        evidence_id="evidence_a",
    )
    twin = ToolExecution(**{**execution.__dict__, "evidence_id": "evidence_b"})
    rendered = _render_deterministic([execution, twin])
    assert rendered.count("529") == 1
