"""A change between two years is two endpoint periods, not one span.

Production answered "By what percentage did SCE's CPUC ignitions change
between 2020 and 2023?" with the same 2020-2023 count twice: the question
resolved to one span, the hold-window rule widened the model's per-year calls
to that span, and the fallback printed the same line twice. Counts here are
fixture values, not warehouse figures.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.derived import DERIVED_TOOL
from services.agent.orchestrator import AgentOrchestrator, _render_deterministic
from services.agent.provider import ModelReply
from services.agent.routing import route_question
from services.agent.time_resolve import (
    apply_harness_years,
    asks_change_between_years,
    resolve_time,
)
from services.agent.tools import ToolExecution, ToolExecutor

TODAY = date(2026, 9, 24)
QUESTION = "By what percentage did SCE's CPUC ignitions change between 2020 and 2023?"
COUNTY_QUESTION = "How did CPUC ignitions in Sonoma County change from 2020 to 2023?"
TWO_COUNTY_QUESTION = (
    "What was the difference in CAL FIRE incidents between Sonoma and Napa "
    "counties, 2020 vs 2023?"
)
COUNT_CONTROL = "How many CPUC ignitions did SCE have between 2020 and 2023?"
SCE = {"2020": 75, "2023": 90}


# --- time resolution -------------------------------------------------------


def test_the_production_question_resolves_to_two_endpoint_years():
    resolved = resolve_time(QUESTION, today=TODAY)
    assert resolved.status == "explicit"
    assert resolved.years == (2020, 2023)
    assert resolved.start_date is None and resolved.end_date is None
    assert resolved.per_year is True
    assert resolved.endpoints is True
    assert resolved.as_slot()["endpoints"] is True


def test_every_change_form_between_two_years_is_endpoints():
    for question in (
        "How did SCE ignitions change from 2020 to 2023?",
        "What was the difference in SCE ignitions between 2020 and 2023?",
        "Did PG&E EPSS outages increase between 2021 and 2023?",
        "How much did CAL FIRE incidents in Napa County decrease from 2020 through 2023?",
        "Percent change in SCE ignitions 2020-2023?",
        "What is the ratio of SCE ignitions between 2020 and 2023?",
        "SCE ignitions 2020 vs 2023",
        "SCE ignitions in 2020 versus 2023",
        "Compare SCE ignitions in 2020 and 2023",
        "compare calfire wildfire totals in Ventura County for 2017 and 2022",
    ):
        resolved = resolve_time(question, today=TODAY)
        assert resolved.endpoints is True, question
        assert len(resolved.years) == 2, question
        assert resolved.start_date is None, question
        assert resolved.per_year is True, question


def test_a_total_or_count_over_the_range_stays_one_span():
    for question in (
        COUNT_CONTROL,
        "Total CAL FIRE incidents in Sonoma County from 2020 to 2023",
        "SCE ignitions 2020-2023",
        "List SCE ignitions between 2020 and 2023",
        # A share, not a change.
        "What percent of SCE ignitions between 2020 and 2023 were in HFTD?",
        # A named metric ratio over the range, not a ratio between two years.
        "What is PG&E's EPSS to ignition ratio between 2021 and 2023?",
        "PG&E EPSS-to-ignition ratio from 2021 to 2023",
        # Two entities compared over one span.
        "Compare Liberty and Bear Valley utility-caused ignition totals from 2019 through 2023.",
        "Take the 2019-2022 period and compare SDGE's ignition totals with SCE's.",
    ):
        resolved = resolve_time(question, today=TODAY)
        assert resolved.endpoints is False, question
        assert resolved.years == tuple(range(resolved.years[0], resolved.years[-1] + 1)), question
        assert resolved.start_date == f"{resolved.years[0]}-01-01", question
        assert resolved.end_date == f"{resolved.years[-1]}-12-31", question
        assert not asks_change_between_years(question), question


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


# --- harness window rules --------------------------------------------------


def test_the_harness_does_not_widen_endpoint_calls_to_the_span():
    slot = resolve_time(QUESTION, today=TODAY).as_slot()
    corrections: list[dict] = []
    for year in (2020, 2023):
        filled, error = apply_harness_years(
            {"dataset": "cpuc_ignitions", "result_mode": "count", "utility": "SCE", "year": year},
            time_resolution=slot,
            today=TODAY,
            hold_window=True,
            corrections=corrections,
        )
        assert error is None
        assert filled["year"] == year
        assert "start_date" not in filled
    assert corrections == []


def test_the_harness_rejects_one_call_over_both_endpoint_years():
    slot = resolve_time(QUESTION, today=TODAY).as_slot()
    _filled, error = apply_harness_years(
        {
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "utility": "SCE",
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
        },
        time_resolution=slot,
        today=TODAY,
        hold_window=True,
    )
    assert error is not None
    assert "2020 and 2023" in error
    assert "one of those years" in error
    # A year that is neither endpoint is still rejected as unlisted.
    _filled, error = apply_harness_years(
        {"dataset": "cpuc_ignitions", "result_mode": "count", "utility": "SCE", "year": 2021},
        time_resolution=slot,
        today=TODAY,
        hold_window=True,
    )
    assert error is not None and "2021" in error


def test_the_span_control_is_still_held_to_the_whole_range():
    slot = resolve_time(COUNT_CONTROL, today=TODAY).as_slot()
    assert slot["endpoints"] is False
    corrections: list[dict] = []
    filled, error = apply_harness_years(
        {"dataset": "cpuc_ignitions", "result_mode": "count", "utility": "SCE", "year": 2020},
        time_resolution=slot,
        today=TODAY,
        hold_window=True,
        corrections=corrections,
    )
    assert error is None
    assert filled["start_date"] == "2020-01-01" and filled["end_date"] == "2023-12-31"
    assert corrections and corrections[0]["rule"] == "hold_resolved_window"


def test_period_comparison_arguments_pass_the_endpoint_guard():
    slot = resolve_time(QUESTION, today=TODAY).as_slot()
    args = {
        "kind": "periods",
        "scope_type": "utility",
        "scope": "SCE",
        "metric": "ignition_count",
        "period_a_start": "2020-01-01",
        "period_a_end": "2020-12-31",
        "period_b_start": "2023-01-01",
        "period_b_end": "2023-12-31",
    }
    filled, error = apply_harness_years(args, time_resolution=slot, today=TODAY, hold_window=True)
    assert error is None and filled == args


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
    assert args["metric"] == "ignition_count"
    assert (args["period_a_start"], args["period_b_start"]) == ("2020-01-01", "2023-01-01")

    calfire = route_question("How much did CAL FIRE incidents in Napa County drop from 2020 to 2023?")
    assert calfire.rule == "period_comparison"
    assert calfire.tool_calls[0][1]["metric"] == "calfire_incident_count"
    assert calfire.tool_calls[0][1]["scope"] == "Napa"


def test_a_two_county_change_question_is_endpoints_but_not_one_period_comparison():
    decision = route_question(TWO_COUNTY_QUESTION)
    assert decision.rule != "period_comparison"
    assert decision.path == "model"
    slot = decision.slots["time_resolution"]
    assert slot["endpoints"] is True
    assert slot["years"] == [2020, 2023]
    assert slot["start_date"] is None
    # The model's per-county, per-year calls are neither widened nor rejected.
    for county in ("Sonoma", "Napa"):
        for year in (2020, 2023):
            filled, error = apply_harness_years(
                {"dataset": "calfire_incidents", "result_mode": "count", "county": county, "year": year},
                time_resolution=slot,
                today=TODAY,
                hold_window=True,
            )
            assert error is None and filled["year"] == year


def test_the_count_control_still_counts_the_whole_span():
    decision = route_question(COUNT_CONTROL)
    assert decision.rule == "filtered_records"
    tool, args = decision.tool_calls[0]
    assert tool == "data_query_records"
    assert args["result_mode"] == "count"
    assert (args["start_date"], args["end_date"]) == ("2020-01-01", "2023-12-31")


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
    year = str(params.get("year") or params.get("start_date") or "")[:4]
    end_year = str(params.get("end_date") or "")[:4]
    if "spatial" in request.url.path:
        return httpx.Response(
            200,
            json={
                "region": {"kind": "utility", "id": params.get("utility")},
                "start_date": params.get("start_date"),
                "end_date": params.get("end_date"),
                "counts": {"ignitions": SCE.get(year, 999)},
                "meta": {},
            },
        )
    total = SCE.get(year, 0) if not end_year or end_year == year else sum(SCE.values())
    return httpx.Response(
        200,
        json={"data": [], "meta": {"total": total, "returned": 0, "filters": {"utility": "SCE"}}},
    )


class SynthesisProvider:
    """Never routes; writes a brief when asked, or fails synthesis on request."""

    def __init__(self, *, brief: str | None = None, fail: bool = False) -> None:
        self.brief = brief
        self.fail = fail
        self.routing_replies: list[list[dict]] = []

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
        if self.fail:
            return ModelReply(
                content="not json", tool_calls=[], raw={"choices": [{"finish_reason": "stop"}]}, latency_ms=1.0, usage={}
            )
        content = kwargs["messages"][-1]["content"]
        payload = json.loads(content.split("Evidence and caveats (JSON):\n", 1)[1])
        derived = next(item for item in payload["evidence"] if item["summary"].get("kind") == "derived_arithmetic")
        return ModelReply(
            content=json.dumps(
                {
                    "status": "answer",
                    "answer": self.brief,
                    "claims": [{"text": self.brief, "evidence_ids": [derived["evidence_id"]]}],
                }
            ),
            tool_calls=[],
            raw={"choices": [{"finish_reason": "stop"}]},
            latency_ms=1.0,
            usage={},
        )


def _ask(provider: SynthesisProvider, question: str, *, force_model: bool = False) -> dict:
    settings = AgentSettings(max_tool_steps=3)
    executor = ToolExecutor(settings, ArtifactStore(60), transport=httpx.MockTransport(_handler))

    async def run():
        try:
            orchestrator = AgentOrchestrator(settings, provider, executor)
            return (await orchestrator.ask(question, force_model=force_model)).response
        finally:
            await executor.close()

    return asyncio.run(run())


def test_the_production_question_gets_both_yearly_counts_and_the_percent_change():
    response = _ask(SynthesisProvider(), QUESTION)
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
    assert [e for e in response["trajectory"] if e.get("type") == "derived_evidence"]

    # The rendered answer carries both yearly counts and the percentage, once each.
    text = response["answer_text"]
    assert text.count("period A=75, period B=90") == 1
    assert text.count("percent change +20%") == 1
    assert text.count("difference +15") == 1


def _count_call(index: int, **args) -> dict:
    return {
        "id": f"call_{index}",
        "type": "function",
        "function": {
            "name": "data_query_records",
            "arguments": json.dumps({"dataset": "cpuc_ignitions", "result_mode": "count", "utility": "SCE", **args}),
        },
    }


def test_two_model_calls_held_to_the_same_span_run_once_and_render_once():
    # A span question on the model path: the model asks for 2020 and 2023
    # separately, the hold-window rule makes both the 2020-2023 span, and the
    # second is suppressed before it runs.
    question = "What happened with SCE CPUC ignitions between 2020 and 2023?"
    assert route_question(question).path == "model"
    provider = SynthesisProvider(fail=True)
    provider.routing_replies = [[_count_call(1, year=2020), _count_call(2, year=2023)]]
    response = _ask(provider, question)
    counts = [e for e in response["evidence"] if e["tool"] == "data_query_records"]
    assert len(counts) == 1
    assert (counts[0]["arguments"]["start_date"], counts[0]["arguments"]["end_date"]) == (
        "2020-01-01",
        "2023-12-31",
    )
    suppressed = [e for e in response["trajectory"] if e.get("type") == "duplicate_tool_call_suppressed"]
    assert len(suppressed) == 1
    assert suppressed[0]["reason"] == "identical after harness correction"
    assert suppressed[0]["arguments"]["year"] == 2023
    assert suppressed[0]["executed_arguments"]["start_date"] == "2020-01-01"
    assert response["answer_text"].count("cpuc_ignitions count: 165") == 1


def test_the_production_question_on_the_model_path_keeps_one_call_per_endpoint_year():
    provider = SynthesisProvider(fail=True)
    provider.routing_replies = [[_count_call(1, year=2020), _count_call(2, year=2023)]]
    response = _ask(provider, QUESTION, force_model=True)
    counts = {e["arguments"]["year"]: e["summary"]["total"] for e in response["evidence"] if e["tool"] == "data_query_records"}
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
