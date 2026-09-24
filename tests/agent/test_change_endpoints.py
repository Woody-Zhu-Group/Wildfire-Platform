"""The harness never rewrites distinct periods the model chose, and change
arithmetic comes from call structure, never from the question's words.

Production answered "By what percentage did SCE's CPUC ignitions change
between 2020 and 2023?" with the same 2020-2023 count twice: the hold-window
rule widened the model's per-year calls to the span, and the fallback printed
the same line twice. A second report asked for July 2024 and August 2024 as
two calls and got the August count twice. Earlier versions of this fix read
change intent from a word list; it missed "by how much" and "gap" and misfired
on "bigger than 2000 acres" and "circuits went down". There is no such list
now: a written range is one span, listed years or months are separate
periods, and whatever windows the model chooses in one turn are kept.

The reviewer's questions (tests/agent/fixtures/pr95_change_questions.json)
were written without looking at the fix. Counts here are fixture values, not
warehouse figures.
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
from services.agent.derived import DERIVED_TOOL
from services.agent.orchestrator import AgentOrchestrator, _render_deterministic
from services.agent.provider import ModelReply
from services.agent.routing import route_question
from services.agent.time_resolve import apply_harness_years, call_window, resolve_time
from services.agent.tools import ToolExecution, ToolExecutor

TODAY = date(2026, 9, 24)
QUESTION = "By what percentage did SCE's CPUC ignitions change between 2020 and 2023?"
COUNT_CONTROL = "How many CPUC ignitions did SCE have between 2020 and 2023?"
MONTHS_QUESTION = "How many CPUC ignitions did PG&E have in July 2024 and August 2024?"
REVIEWER = json.loads(
    (Path(__file__).parent / "fixtures" / "pr95_change_questions.json").read_text(encoding="utf-8")
)
# Questions where a change word list misfired (a size, an outage verb) or
# missed (gap, by how much, better or worse). None may resolve to only two
# endpoint years: a written range stays the whole span.
WRITTEN_RANGES = [
    "How many CAL FIRE incidents bigger than 2000 acres from 2018 to 2022?",
    "How many EPSS circuits went down between 2022 and 2024?",
    "What was the gap in SCE ignitions between 2019 and 2022?",
    "By how much did SCE ignitions change between 2019 and 2022?",
    "Was PG&E better or worse than SCE for ignitions from 2019 to 2022?",
    QUESTION,
    REVIEWER["user_case"],
]
SCE = {"2020": 75, "2023": 90}
COUNTY_COUNTS = {("Butte", 2019): 40, ("Butte", 2022): 30, ("Shasta", 2019): 12, ("Shasta", 2022): 18}
MONTH_COUNTS = {"07": 61, "08": 98}


def _years_in(question: str) -> list[int]:
    return sorted({int(item) for item in __import__("re").findall(r"\b20\d{2}\b", question) if int(item) >= 2014})


# --- time resolution -------------------------------------------------------


@pytest.mark.parametrize("question", WRITTEN_RANGES + REVIEWER["total_span"])
def test_a_written_range_is_the_whole_span_whatever_the_wording(question):
    resolved = resolve_time(question, today=TODAY)
    first, last = _years_in(question)[0], _years_in(question)[-1]
    assert resolved.years == tuple(range(first, last + 1))
    assert resolved.start_date == f"{first}-01-01"
    assert resolved.end_date == f"{last}-12-31"
    assert "endpoints" not in resolved.as_slot()


@pytest.mark.parametrize("question", REVIEWER["change"])
def test_reviewer_change_questions_resolve_by_form_not_by_wording(question):
    resolved = resolve_time(question, today=TODAY)
    years = _years_in(question)
    if resolved.start_date is None:
        # Years listed separately ("2019 versus 2023", "in 2021 than in 2020")
        # are separate periods, exactly as on main.
        assert sorted(resolved.years) == years
        assert resolved.per_year is True
    else:
        assert resolved.years == tuple(range(years[0], years[-1] + 1))
        assert resolved.end_date == f"{years[-1]}-12-31"


def test_two_named_months_keep_both_months():
    resolved = resolve_time(MONTHS_QUESTION, today=TODAY)
    assert resolved.years == (2024,)
    assert (resolved.start_date, resolved.end_date) == ("2024-07-01", "2024-08-31")
    assert resolved.per_year is True
    assert resolved.phrase == "july, august 2024"
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


@pytest.mark.parametrize("question", WRITTEN_RANGES[:5] + ["Show SCE ignitions from 2019 to 2022"])
def test_distinct_periods_in_one_turn_are_never_widened_whatever_the_wording(question):
    first, last = _years_in(question)[0], _years_in(question)[-1]
    windows = [call_window(_count_args(year=first)), call_window(_count_args(year=last))]
    for year in (first, last):
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


def test_the_span_control_is_still_held_to_the_whole_range():
    filled, error, corrections = _hold(_count_args(year=2020), COUNT_CONTROL)
    assert error is None
    assert (filled["start_date"], filled["end_date"]) == ("2020-01-01", "2023-12-31")
    assert corrections and corrections[0]["rule"] == "hold_resolved_window"


# --- routing ---------------------------------------------------------------


@pytest.mark.parametrize("question", WRITTEN_RANGES + REVIEWER["total_span"])
def test_no_written_range_answers_with_only_two_endpoint_years(question):
    decision = route_question(question)
    assert decision.rule != "period_comparison"
    for _tool, args in decision.tool_calls:
        assert args.get("kind") != "periods"
        window = call_window(args)
        if window is not None:
            first, last = _years_in(question)[0], _years_in(question)[-1]
            assert window == (f"{first}-01-01", f"{last}-12-31")


def test_mains_compare_and_versus_routing_is_unchanged():
    decision = route_question("Compare SCE ignitions in 2023 versus 2024")
    assert decision.rule == "period_comparison"
    assert decision.tool_calls[0][1]["scope_type"] == "utility"
    county = route_question("Compare Butte County ignitions in 2019 with 2022.")
    assert county.path == "model" and county.rule == "open_comparison"


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


# --- the answer ------------------------------------------------------------


def _handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
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


def _ask(provider: ScriptedProvider, question: str) -> dict:
    settings = AgentSettings(max_tool_steps=3)
    executor = ToolExecutor(settings, ArtifactStore(60), transport=httpx.MockTransport(_handler))

    async def run():
        try:
            orchestrator = AgentOrchestrator(settings, provider, executor)
            return (await orchestrator.ask(question)).response
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


def test_the_production_question_keeps_both_yearly_calls_and_derives_the_percent_change():
    assert route_question(QUESTION).path == "model"
    calls = [_count_call(1, utility="SCE", year=2020), _count_call(2, utility="SCE", year=2023)]
    response = _ask(ScriptedProvider([calls]), QUESTION)
    assert response["status"] == "answer"
    counts = {e["arguments"]["year"]: e["summary"]["total"] for e in _primary_counts(response)}
    assert counts == {2020: 75, 2023: 90}
    assert not [e for e in response["trajectory"] if e.get("type") == "duplicate_tool_call_suppressed"]

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

    # The fallback answer carries both yearly counts and the percentage, once each.
    text = response["answer_text"]
    assert text.count("count: 75 ") == 1
    assert text.count("count: 90 ") == 1
    assert text.count("percent change +20%") == 1


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
    # Both calls narrow to 2020 in different spellings, both are held to the
    # 2020-2023 span, and the second is suppressed before it runs.
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
