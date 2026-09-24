"""Enum filter values the question never asked for are dropped: utility=untagged and incident_type_mode.

recover_503's wording ("Show a weekly CPUC ignition time series for 2024.") is the
case where Luna sent utility=untagged on its own; a question that asks about
untagged or unattributed records must still be able to use it.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.grounding import (
    audit_executed_filters,
    ground_model_filters,
    question_allows_untagged,
    question_incident_type_modes,
    score_executed_filters,
)
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import ModelReply
from services.agent.tools import ToolExecutor, _strip_ungrounded_utilities

RECOVER_503 = "Show a weekly CPUC ignition time series for 2024."
UNTAGGED_QUESTIONS = [
    "How many untagged CAL FIRE incidents were there in 2024?",
    "CAL FIRE incidents with no utility attribution in 2023",
    "How many non-utility CAL FIRE incidents in 2022?",
    "Which CAL FIRE incidents were not attributed to a utility in 2024?",
    "Count the unattributed CAL FIRE incidents in 2021",
]


def _fields(drops):
    return sorted((d["field"], d["reason"]) for d in drops)


# ------------------------------------------------------------- grounding


def test_recover_503_wording_does_not_ground_untagged():
    assert question_allows_untagged(RECOVER_503) is False
    grounded, drops = ground_model_filters(
        {"kind": "time_series", "dataset": "ignitions", "utility": "untagged", "year": 2024},
        question=RECOVER_503,
    )
    assert grounded == {"kind": "time_series", "dataset": "ignitions", "year": 2024}
    assert _fields(drops) == [("utility", "not_in_question")]
    audit = audit_executed_filters(
        "visualization_create",
        {"kind": "time_series", "dataset": "ignitions", "utility": "untagged"},
        question=RECOVER_503,
        utilities=[],
        county=None,
    )
    assert _fields(audit) == [("utility", "not_in_slots")]
    stripped_args, stripped = _strip_ungrounded_utilities(
        {"kind": "time_series", "dataset": "ignitions", "utility": "untagged"}, utilities=[]
    )
    assert stripped == ["untagged"] and "utility" not in stripped_args


@pytest.mark.parametrize("question", UNTAGGED_QUESTIONS)
def test_a_question_about_untagged_records_keeps_the_untagged_filter(question):
    assert question_allows_untagged(question) is True
    args = {"dataset": "calfire_incidents", "result_mode": "count", "utility": "untagged"}
    grounded, drops = ground_model_filters(args, question=question)
    assert grounded == args and drops == []
    assert audit_executed_filters("data_query_records", args, question=question, utilities=[], county=None) == []
    kept, stripped = _strip_ungrounded_utilities(args, utilities=[], allow_untagged=True)
    assert kept == args and stripped == []
    assert score_executed_filters(
        question, [{"tool": "data_query_records", "arguments": args, "ok": True}], utilities=[], county=None
    ) == {"invented": [], "missing": [], "pass": True}


def test_a_named_iou_is_still_stripped_even_when_untagged_is_allowed():
    kept, stripped = _strip_ungrounded_utilities(
        {"dataset": "calfire_incidents", "utility": "SCE"}, utilities=[], allow_untagged=True
    )
    assert stripped == ["SCE"] and "utility" not in kept


@pytest.mark.parametrize(
    "question,modes",
    [
        ("Tell me about wildfire incidents in Sacramento County during 2024", {"wildfire_default"}),
        (RECOVER_503, {"wildfire_default"}),
        ("Count CAL FIRE records of all incident types in 2024", {"wildfire_default", "all"}),
        ("CAL FIRE incidents in 2024 including non-wildfire types", {"wildfire_default", "all"}),
        ("How many untyped CAL FIRE records are there?", {"wildfire_default", "untyped"}),
        ("CAL FIRE records with no incident type in 2023", {"wildfire_default", "untyped"}),
    ],
)
def test_incident_type_mode_is_grounded_by_the_question(question, modes):
    assert question_incident_type_modes(question) == modes
    for mode in ("wildfire_default", "all", "untyped"):
        args = {"dataset": "calfire_incidents", "result_mode": "count", "incident_type_mode": mode, "year": 2024}
        grounded, drops = ground_model_filters(args, question=question)
        if mode in modes:
            assert grounded == args and drops == [], (question, mode)
        else:
            assert "incident_type_mode" not in grounded, (question, mode)
            assert _fields(drops) == [("incident_type_mode", "not_in_question")], (question, mode)


def test_tier_dataset_and_other_enum_fields_are_unchanged():
    # Tiers were already grounded; dataset and result_mode are structural, not filters.
    args = {"dataset": "calfire_incidents", "result_mode": "records", "tier": "Tier 2"}
    grounded, drops = ground_model_filters(args, question="CAL FIRE incidents in 2024")
    assert grounded == {"dataset": "calfire_incidents", "result_mode": "records"}
    assert _fields(drops) == [("tier", "not_in_question")]


# -------------------------------------------------------------- executor


def _time_series(request: httpx.Request) -> httpx.Response:
    params = dict(request.url.params)
    total = 0 if params.get("utility") == "untagged" else 741
    return httpx.Response(
        200,
        json={
            "dataset": "ignitions",
            "interval": "weekly",
            "buckets": [{"start": "2024-01-01", "count": total}],
            "meta": {"total_events": total, "filters": params},
        },
    )


def test_an_injected_fault_records_the_arguments_that_would_have_run():
    executor = ToolExecutor(
        AgentSettings(), ArtifactStore(60), transport=httpx.MockTransport(_time_series), fault_scenario="service_503_once"
    )
    result = asyncio.run(
        executor.execute(
            "visualization_create",
            {"kind": "time_series", "dataset": "ignitions", "utility": "untagged", "year": 2024, "interval": "weekly"},
            request_id="t",
            attempt=1,
            utilities=[],
        )
    )
    assert not result.ok and result.error["code"] == "service_unavailable"
    assert "utility" not in result.arguments, "the audit must see the stripped call, not the raw model payload"


def test_the_executor_keeps_untagged_only_when_allowed():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return _time_series(request)

    executor = ToolExecutor(AgentSettings(), ArtifactStore(60), transport=httpx.MockTransport(handler))
    args = {"kind": "time_series", "dataset": "ignitions", "utility": "untagged", "year": 2024, "interval": "weekly"}
    blocked = asyncio.run(executor.execute("visualization_create", dict(args), request_id="t", attempt=1, utilities=[]))
    allowed = asyncio.run(
        executor.execute("visualization_create", dict(args), request_id="t", attempt=2, utilities=[], allow_untagged=True)
    )
    assert blocked.ok and "utility" not in seen[0] and blocked.stripped_utilities == ["untagged"]
    assert blocked.summary["total_events"] == 741
    assert allowed.ok and seen[1]["utility"] == "untagged" and allowed.stripped_utilities == []


# ---------------------------------------------------------- orchestrator


class _Provider:
    def __init__(self, tool: str, arguments: dict) -> None:
        self.tool = tool
        self.arguments = arguments

    async def complete(self, **kwargs):
        if kwargs.get("tools"):
            return ModelReply(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": self.tool, "arguments": json.dumps(self.arguments)},
                    }
                ],
                raw={"choices": [{"finish_reason": "tool_calls"}]},
                latency_ms=1.0,
                usage={},
            )
        return ModelReply(
            content=json.dumps(
                {"status": "answer", "answer": "Done.", "claims": [{"text": "Done.", "evidence_ids": []}]}
            ),
            tool_calls=[],
            raw={"choices": [{"finish_reason": "stop"}]},
            latency_ms=1.0,
            usage={},
        )


def test_recover_503_wording_never_runs_an_untagged_series():
    seen = []

    def handler(request):
        seen.append((request.url.path, dict(request.url.params)))
        return _time_series(request)

    provider = _Provider(
        "visualization_create",
        {"kind": "time_series", "dataset": "ignitions", "utility": "untagged", "year": 2024, "interval": "weekly"},
    )
    executor = ToolExecutor(AgentSettings(max_tool_steps=2), ArtifactStore(60), transport=httpx.MockTransport(handler))
    result = asyncio.run(AgentOrchestrator(AgentSettings(max_tool_steps=2), provider, executor).ask(RECOVER_503, force_model=True))
    response = result.response
    series = [params for path, params in seen if path.endswith("/time-series")]
    assert series and all("utility" not in params for params in series)
    calls = [e for e in response["trajectory"] if e.get("type") == "tool_call" and not e.get("qualification_call")]
    assert all("utility" not in (e.get("arguments") or {}) for e in calls)
    dropped = [e for e in response["trajectory"] if e.get("type") == "filter_dropped"]
    assert [(e["field"], e["value"]) for e in dropped] == [("utility", "untagged")]
    assert "0 " not in response["answer_text"]
    assert score_executed_filters(RECOVER_503, calls, utilities=[], county=None)["pass"] is True


def test_a_question_about_untagged_incidents_still_runs_with_untagged():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={"data": [], "meta": {"total": 282, "returned": 0, "filters": dict(request.url.params)}},
        )

    question = UNTAGGED_QUESTIONS[0]
    provider = _Provider(
        "data_query_records",
        {"dataset": "calfire_incidents", "result_mode": "count", "utility": "untagged", "year": 2024},
    )
    executor = ToolExecutor(AgentSettings(max_tool_steps=2), ArtifactStore(60), transport=httpx.MockTransport(handler))
    result = asyncio.run(AgentOrchestrator(AgentSettings(max_tool_steps=2), provider, executor).ask(question, force_model=True))
    calls = [e for e in result.response["trajectory"] if e.get("type") == "tool_call" and not e.get("qualification_call")]
    assert calls and calls[0]["arguments"].get("utility") == "untagged"
    assert any(params.get("utility") == "untagged" for params in seen)
    assert not [e for e in result.response["trajectory"] if e.get("type") == "filter_dropped"]
    assert score_executed_filters(question, calls, utilities=[], county=None)["pass"] is True


# ---------------------------------------------------------------- runner


def _sacramento_case():
    cases = json.load(open("services/agent/eval/cases.json", encoding="utf-8"))
    return next(c for c in cases if c["id"] == "model_sacramento_tell_me_about_2024")


def _response(tools: list[str]) -> dict:
    calls = [
        {
            "type": "tool_call",
            "tool": tool,
            "arguments": {"dataset": "calfire_incidents", "result_mode": "count" if i == 0 else "records", "county": "Sacramento", "year": 2024},
            "ok": True,
        }
        for i, tool in enumerate(tools)
    ]
    return {
        "status": "answer",
        "answer_text": "The CAL FIRE incidents dataset recorded 11 incidents in Sacramento County during 2024.",
        "route": {"path": "model", "answer_origin": "model", "slot_resolution": {"year": 2024}},
        "qualifications": [{"id": "calfire_missingness"}],
        "evidence": [{"id": "evidence_1"}],
        "trajectory": calls,
        "timings_ms": {"total": 5000},
        "views": [],
        "view_status": "none",
    }


def test_the_sacramento_case_accepts_a_count_call_plus_records_calls():
    from services.agent.eval.runner import score_case

    case = _sacramento_case()
    assert case["accepted_tool_sequences"] == [
        ["data_query_records", "data_query_records"],
        ["data_query_records", "data_query_records", "data_query_records"],
    ]
    assert "count call" in case["expectation_note"]
    for tools in (["data_query_records"], ["data_query_records"] * 2, ["data_query_records"] * 3):
        score = score_case(case, _response(tools))
        assert score["routing_pass"], (tools, score)
    wrong = score_case(case, _response(["data_query_rank"]))
    assert wrong["routing_pass"] is False
