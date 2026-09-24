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
warehouse figures. End-to-end reads use years inside measured CPUC coverage
(rows from 2020-01-01, PR #93): a read outside it is not covered and the
answer clarifies (test_a_change_with_an_uncovered_endpoint_clarifies), so the
reviewer's end-to-end questions are asked with their years moved to 2020 on,
their structure unchanged.
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
from services.agent.time_resolve import (
    CallWindows,
    apply_harness_years,
    call_window,
    named_month_periods,
    resolve_time,
)
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
# The reviewer's user case with its range moved inside measured CPUC coverage.
USER_CASE = REVIEWER["user_case"].replace("from 2019 to 2022", "from 2020 to 2023")
COUNTY_COUNTS = {("Butte", 2020): 40, ("Butte", 2023): 30, ("Shasta", 2020): 12, ("Shasta", 2023): 18}
MONTH_COUNTS = {"07": 61, "08": 98}
PGE = {"2020": 185, "2021": 110, "2023": 42, "2024": 58}
PGE_SPAN = 777


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


def _hold(
    arguments: dict,
    question: str,
    seen: list | None = None,
    endpoints: list[int] | None = None,
):
    corrections: list[dict] = []
    windows = CallWindows(
        endpoints=frozenset((f"{item}-01-01", f"{item}-12-31") for item in endpoints or [])
    ).with_calls(list(seen or []))
    filled, error = apply_harness_years(
        arguments,
        time_resolution=resolve_time(question, today=TODAY).as_slot(),
        today=TODAY,
        hold_window=True,
        corrections=corrections,
        windows=windows,
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


def test_windows_from_an_earlier_turn_count_like_windows_in_this_turn():
    # One call per turn: turn 1 read 2016, turn 2 reads 2020. The hold rule
    # sees both, so the second call is a split, not a narrowing.
    question = "Show the shift in PG&E ignitions from 2016 to 2020."
    earlier = [call_window(_count_args(year=2016))]
    filled, error, corrections = _hold(
        _count_args(year=2020), question, earlier + [call_window(_count_args(year=2020))]
    )
    assert error is None
    assert filled["year"] == 2020 and "start_date" not in filled
    assert corrections == []


def test_a_lone_endpoint_call_is_kept_when_coverage_asks_for_the_endpoints():
    # Jev's compare or trend reading makes the endpoints the coverage
    # entities, so a first-turn call on one of them is a planned read.
    question = "Show the shift in PG&E ignitions from 2016 to 2020."
    for year in (2016, 2020):
        filled, error, corrections = _hold(
            _count_args(year=year), question, [call_window(_count_args(year=year))], endpoints=[2016, 2020]
        )
        assert error is None
        assert filled["year"] == year and "start_date" not in filled
        assert corrections == []
    # A lone call on a year that is not an endpoint is still widened.
    filled, _error, corrections = _hold(
        _count_args(year=2018), question, [call_window(_count_args(year=2018))], endpoints=[2016, 2020]
    )
    assert (filled["start_date"], filled["end_date"]) == ("2016-01-01", "2020-12-31")
    assert corrections and corrections[0]["rule"] == "hold_resolved_window"
    # Without endpoints (a total, Jev off, below the gate) it is widened, as on main.
    filled, _error, _ = _hold(_count_args(year=2016), question, [call_window(_count_args(year=2016))])
    assert (filled["start_date"], filled["end_date"]) == ("2016-01-01", "2020-12-31")


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
        total = MONTH_COUNTS.get(start[5:7], 500)
    elif params.get("utility") == "PGE":
        end = str(params.get("end_date") or "")
        # A window across several years is the span total.
        total = PGE_SPAN if end and end[:4] != str(year) else PGE.get(str(year), 0)
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


def test_the_production_question_with_jev_off_keeps_both_calls_and_declines_as_on_main():
    # Both endpoint calls run unwidened, but with Jev off nothing says the
    # range names only its endpoints, so the full-range coverage rule
    # declines, as main did, and no change figure is derived.
    assert route_question(QUESTION).path == "model"
    calls = [_count_call(1, utility="SCE", year=2020), _count_call(2, utility="SCE", year=2023)]
    response = _ask(ScriptedProvider([calls]), QUESTION)
    assert response["status"] == "error"
    counts = {e["arguments"]["year"]: e["summary"]["total"] for e in _primary_counts(response)}
    assert counts == {2020: 75, 2023: 90}
    assert not [e for e in response["trajectory"] if e.get("type") == "duplicate_tool_call_suppressed"]
    assert not [e for e in response["trajectory"] if e.get("type") == "harness_time_correction"]
    assert not [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]


def test_the_production_question_with_jev_compare_intent_derives_the_percent_change():
    calls = [_count_call(1, utility="SCE", year=2020), _count_call(2, utility="SCE", year=2023)]
    response = _ask_with_jev(QUESTION, calls, _jev_facts("compare", 0.95))
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
        _count_call(1, county="Butte", year=2020),
        _count_call(2, county="Butte", year=2023),
        _count_call(3, county="Shasta", year=2020),
        _count_call(4, county="Shasta", year=2023),
    ]
    # "Up or down from 2020 to 2023" is a comparison over a written range: Jev's
    # compare intent lets the two endpoint years cover it.
    assert USER_CASE != REVIEWER["user_case"]
    response = _ask_with_jev(USER_CASE, calls, _jev_facts("compare", 0.95))
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


# --- coverage of a written range needs Jev's intent -------------------------


def _ask_with_jev(
    question: str,
    calls: list[dict],
    jev_answers: dict | None,
    *,
    turns: list[list[dict]] | None = None,
) -> dict:
    """Decide mode with a scripted Jev backend; None means Jev off.

    ``calls`` are one model turn; ``turns`` gives one list of calls per turn.
    """
    from dataclasses import replace

    from tests.agent.test_jev_decide import FakeBackend

    settings = AgentSettings(max_tool_steps=4)
    if jev_answers is not None:
        settings = replace(settings, jev_mode="decide", jev_backend="typesafe", jev_decide_min_confidence=0.8)
    executor = ToolExecutor(settings, ArtifactStore(60), transport=httpx.MockTransport(_handler))
    backend = FakeBackend(jev_answers) if jev_answers is not None else None

    async def run():
        try:
            orchestrator = AgentOrchestrator(
                settings, ScriptedProvider(turns or [calls]), executor, decide_backend=backend
            )
            return (await orchestrator.ask(question)).response
        finally:
            await executor.close()

    return asyncio.run(run())


def _jev_facts(intent: str, confidence: float) -> dict:
    from tests.agent.test_jev_decide import _answer_facts, _choice

    return _answer_facts(intent=_choice(intent, confidence))


ENDPOINT_CALLS = [_count_call(1, utility="SCE", year=2020), _count_call(2, utility="SCE", year=2023)]
TOTAL_QUESTION = "What happened with SCE CPUC ignitions between 2020 and 2023?"


def test_a_total_over_a_range_with_endpoint_only_calls_declines():
    for jev in (None, _jev_facts("count", 0.95)):
        response = _ask_with_jev(TOTAL_QUESTION, ENDPOINT_CALLS, jev)
        assert response["status"] == "error", jev
        assert "2021" in response["answer_text"] and "2022" in response["answer_text"]
        assert [e for e in response["trajectory"] if e.get("type") == "uncovered_entities_stop"]


def test_a_change_question_with_jev_compare_intent_answers_with_both_years():
    response = _ask_with_jev(QUESTION, ENDPOINT_CALLS, _jev_facts("compare", 0.95))
    assert response["status"] == "answer"
    assert response["route"]["rule"] == "open_ended"
    counts = {e["arguments"]["year"]: e["summary"]["total"] for e in _primary_counts(response)}
    assert counts == {2020: 75, 2023: 90}
    assert not [e for e in response["trajectory"] if e.get("type", "").startswith("uncovered_entities")]
    derived = [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
    assert derived and derived[0]["summary"]["derivations"][0]["percent_change"] == 20
    assert response["answer_text"].count("percent change +20%") == 1


def test_the_same_change_question_with_jev_below_the_gate_declines():
    for jev in (_jev_facts("compare", 0.6), _jev_facts("trend", 0.79), None):
        response = _ask_with_jev(QUESTION, ENDPOINT_CALLS, jev)
        assert response["status"] == "error", jev
        assert "2021" in response["answer_text"] and "2022" in response["answer_text"]


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


# --- one call per turn -------------------------------------------------------

# The C8 review question ("from 2016 to 2020") with its range moved inside
# measured CPUC coverage; the harness-only tests above keep 2016 to 2020.
C8 = "Show the shift in PG&E ignitions from 2021 to 2024."


def _c8_turns() -> list[list[dict]]:
    return [[_count_call(1, utility="PGE", year=2021)], [_count_call(2, utility="PGE", year=2024)]]


def test_c8_one_call_per_turn_with_jev_compare_keeps_both_years_and_derives_the_change():
    # Hosted models often send one call per turn. The lone 2021 call is an
    # endpoint coverage will ask for, so it is not widened to 2021-2024, and
    # the 2024 call in the next turn is kept beside it.
    response = _ask_with_jev(C8, [], _jev_facts("compare", 0.95), turns=_c8_turns())
    assert response["status"] == "answer"
    windows = [call_window(e["arguments"]) for e in _primary_counts(response)]
    assert windows == [("2021-01-01", "2021-12-31"), ("2024-01-01", "2024-12-31")]
    assert not [e for e in response["trajectory"] if e.get("type") == "harness_time_correction"]
    continued = [e for e in response["trajectory"] if e.get("type") == "uncovered_entities_continue"]
    assert continued and continued[0]["missing"] == ["year:2024"]
    derived = [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
    assert len(derived) == 1
    row = derived[0]["summary"]["derivations"][0]
    assert (row["from"]["period"], row["to"]["period"]) == ("2021", "2024")
    assert (row["from"]["value"], row["to"]["value"], row["difference"]) == (110, 58, -52)


def test_c8_one_call_per_turn_with_trend_intent_behaves_the_same():
    response = _ask_with_jev(C8, [], _jev_facts("trend", 0.9), turns=_c8_turns())
    assert response["status"] == "answer"
    windows = [call_window(e["arguments"]) for e in _primary_counts(response)]
    assert windows == [("2021-01-01", "2021-12-31"), ("2024-01-01", "2024-12-31")]


def test_c8_one_call_per_turn_without_a_change_reading_is_held_to_the_span_as_on_main():
    # Jev off, a count reading, or compare below the gate: nothing says the
    # range names its endpoints, so the lone first call is widened to the
    # span, which covers every year, and no change figure is derived.
    for jev in (None, _jev_facts("count", 0.95), _jev_facts("compare", 0.6)):
        response = _ask_with_jev(C8, [], jev, turns=_c8_turns())
        windows = [call_window(e["arguments"]) for e in _primary_counts(response)]
        assert windows == [("2021-01-01", "2024-12-31")], jev
        assert not [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL], jev


def test_a_lone_endpoint_the_model_never_follows_up_declines():
    response = _ask_with_jev(
        C8, [], _jev_facts("compare", 0.95), turns=[[_count_call(1, utility="PGE", year=2021)]]
    )
    assert response["status"] == "error"
    assert "2024" in response["answer_text"]
    assert [e for e in response["trajectory"] if e.get("type") == "uncovered_entities_stop"]


ORIGINAL_UNCOVERED = [
    # The reviewer's user case and C8 as written: 2019 and 2016 are before
    # measured CPUC coverage (rows from 2020-01-01).
    (
        REVIEWER["user_case"],
        [
            [
                _count_call(1, county="Butte", year=2019),
                _count_call(2, county="Butte", year=2022),
                _count_call(3, county="Shasta", year=2019),
                _count_call(4, county="Shasta", year=2022),
            ]
        ],
        "2019",
    ),
    (
        "Show the shift in PG&E ignitions from 2016 to 2020.",
        [[_count_call(1, utility="PGE", year=2016)], [_count_call(2, utility="PGE", year=2020)]],
        "2016",
    ),
]


@pytest.mark.parametrize("question,turns,uncovered", ORIGINAL_UNCOVERED)
def test_a_change_with_an_uncovered_endpoint_clarifies(question, turns, uncovered):
    # A change needs both endpoints. One outside measured coverage has no
    # count (absent, not zero), so the answer clarifies, derives nothing, and
    # never shows a count for the uncovered year.
    response = _ask_with_jev(question, [], _jev_facts("compare", 0.95), turns=turns)
    assert response["status"] == "clarification", response["answer_text"]
    assert "absent, not zero" in response["answer_text"]
    assert not [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
    assert not [
        e for e in _primary_counts(response) if str(e["arguments"].get("year")) == uncovered
    ]


# --- named months are coverage entities ---------------------------------------

N5 = "Which circuits had EPSS outages in July 2023 and August 2023?"


def test_named_months_are_separate_periods_only_when_a_year_names_them():
    assert named_month_periods(N5) == ["2023-07", "2023-08"]
    assert named_month_periods(MONTHS_QUESTION) == ["2024-07", "2024-08"]
    assert named_month_periods("How many outages in July and August 2023?") == ["2023-07", "2023-08"]
    assert named_month_periods(
        "Were there fewer EPSS outages in October 2023 than in October 2022, and by how many?"
    ) == ["2023-10", "2022-10"]
    # A month range is one span; one month, or "may" the verb, is no list.
    assert named_month_periods("How many EPSS outages from March to June 2023?") == []
    assert named_month_periods("EPSS outages from August 2023 to September 2024") == []
    assert named_month_periods("How many outages in July 2023?") == []
    assert named_month_periods("Which circuits may have had outages in 2023?") == []


def _july(index: int = 1) -> dict:
    return _count_call(index, utility="PGE", start_date="2024-07-01", end_date="2024-07-31")


def _august(index: int = 2) -> dict:
    return _count_call(index, utility="PGE", start_date="2024-08-01", end_date="2024-08-31")


def test_july_then_august_in_separate_turns_answers_both_months():
    response = _ask_with_jev(MONTHS_QUESTION, [], None, turns=[[_july()], [_august()]])
    assert response["status"] == "answer"
    counts = {e["arguments"]["start_date"][5:7]: e["summary"]["total"] for e in _primary_counts(response)}
    assert counts == {"07": 61, "08": 98}
    continued = [e for e in response["trajectory"] if e.get("type") == "uncovered_entities_continue"]
    assert continued and continued[0]["missing"] == ["month:2024-08"]


def test_july_alone_never_answers_a_july_and_august_question():
    # The regression the review found: July fetched, the loop stopped, and
    # the answer gave July only. August is now a coverage entity.
    response = _ask_with_jev(MONTHS_QUESTION, [], None, turns=[[_july()]])
    assert response["status"] == "error"
    assert "2024-08" in response["answer_text"]
    assert [e for e in response["trajectory"] if e.get("type") == "uncovered_entities_stop"]
    assert "61" not in response["answer_text"]


def test_a_call_that_reads_months_the_question_never_named_covers_none():
    # Named months are separate periods, so the per-year flag keeps a
    # whole-year window as written; it reads ten months nobody asked about.
    # (A bare year=2024 is rewritten to the named months by the harness.)
    whole_year = _count_call(1, utility="PGE", start_date="2024-01-01", end_date="2024-12-31")
    response = _ask_with_jev(MONTHS_QUESTION, [], None, turns=[[whole_year]])
    assert response["status"] == "error"
    stop = [e for e in response["trajectory"] if e.get("type") == "uncovered_entities_stop"]
    assert stop and stop[0]["missing"] == ["month:2024-07", "month:2024-08"]


def test_one_call_over_exactly_the_named_months_covers_both():
    # "Which circuits had outages in July and August" is answered by one read
    # of July through August, as a written span covers each of its years.
    both = _count_call(1, utility="PGE", start_date="2024-07-01", end_date="2024-08-31")
    response = _ask_with_jev(MONTHS_QUESTION, [], None, turns=[[both]])
    assert not [e for e in response["trajectory"] if e.get("type", "").startswith("uncovered_entities")]


# --- derived figures need Jev's change reading ---------------------------------

N1 = "List PG&E ignitions in 2020 and in 2023."
N1_CALLS = [_count_call(1, utility="PGE", year=2020), _count_call(2, utility="PGE", year=2023)]


def test_a_listing_of_two_years_carries_no_change_figures():
    for jev in (None, _jev_facts("records", 0.95), _jev_facts("compare", 0.6)):
        response = _ask_with_jev(N1, N1_CALLS, jev)
        assert response["status"] == "answer", jev
        assert not [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL], jev
        text = response["answer_text"]
        assert "count: 185 " in text and "count: 42 " in text, jev
        for word in ("difference", "percent change", "ratio"):
            assert word not in text, (jev, word)
        withheld = [e for e in response["trajectory"] if e.get("type") == "derived_evidence_withheld"]
        assert len(withheld) == 1, jev


def test_the_same_calls_with_jev_compare_carry_the_change():
    response = _ask_with_jev(N1, N1_CALLS, _jev_facts("compare", 0.95))
    derived = [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
    assert len(derived) == 1
    assert derived[0]["summary"]["derivations"][0]["difference"] == -143
    assert "percent change" in response["answer_text"]
