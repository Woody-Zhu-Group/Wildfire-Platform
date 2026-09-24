"""The harness sends canonical county names and surfaces an unknown county as a clarification."""

from __future__ import annotations

import asyncio
import json

import httpx

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import ModelReply
from services.agent.tools import ToolExecutor

UNKNOWN_DETAIL = (
    "unknown county 'Buttes'; it matches no California county. Did you mean Butte or Sutter?"
)


def _count_response(request: httpx.Request) -> httpx.Response:
    county = request.url.params.get("county")
    if county == "Buttes":
        return httpx.Response(400, json={"detail": UNKNOWN_DETAIL})
    total = 9 if county == "Butte" else 0
    return httpx.Response(
        200,
        json={"data": [], "meta": {"total": total, "returned": 0, "filters": {"county": county, "year": 2020}}},
    )


def _run(executor: ToolExecutor, county: str):
    return asyncio.run(
        executor.execute(
            "data_query_records",
            {"dataset": "calfire_incidents", "result_mode": "count", "year": 2020, "county": county},
            request_id="t",
            attempt=1,
        )
    )


def test_a_model_county_with_the_word_county_is_sent_as_the_canonical_name(capsys):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return _count_response(request)

    executor = ToolExecutor(AgentSettings(), ArtifactStore(60), transport=httpx.MockTransport(handler))
    result = _run(executor, "Butte County")
    assert result.ok
    assert seen[0]["county"] == "Butte"
    assert result.summary["total"] == 9
    assert result.arguments["county"] == "Butte"
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if "harness_county_correction" in line]
    assert events and events[0]["from"] == "Butte County" and events[0]["to"] == "Butte"


def test_an_unknown_county_is_a_recoverable_error_with_the_backend_suggestions():
    executor = ToolExecutor(AgentSettings(), ArtifactStore(60), transport=httpx.MockTransport(_count_response))
    result = _run(executor, "Buttes")
    assert not result.ok
    assert result.error["code"] == "unknown_county"
    assert result.error["recoverable"] is True
    assert "Did you mean Butte" in result.error["message"]
    assert result.summary == {}


def test_the_user_sees_the_suggestion_when_the_model_keeps_the_unknown_county():
    class StubbornProvider:
        async def complete(self, **kwargs):
            if kwargs.get("tools"):
                return ModelReply(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "data_query_records",
                                "arguments": json.dumps(
                                    {"dataset": "calfire_incidents", "result_mode": "count", "year": 2020, "county": "Buttes"}
                                ),
                            },
                        }
                    ],
                    raw={"choices": [{"finish_reason": "tool_calls"}]},
                    latency_ms=1.0,
                    usage={},
                )
            raise AssertionError("synthesis must not run without evidence")

    executor = ToolExecutor(
        AgentSettings(max_tool_steps=3), ArtifactStore(60), transport=httpx.MockTransport(_count_response)
    )
    orchestrator = AgentOrchestrator(AgentSettings(max_tool_steps=3), StubbornProvider(), executor)
    result = asyncio.run(
        orchestrator.ask("How many CAL FIRE incidents were there in Buttes County in 2020?", force_model=True)
    )
    response = result.response
    assert response["status"] == "error"
    assert "Did you mean Butte" in response["answer_text"]
    assert "0" not in response["answer_text"].replace("2020", "")
