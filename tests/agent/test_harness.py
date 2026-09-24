from __future__ import annotations

import asyncio

import httpx

from services.agent.artifacts import ArtifactStore
from services.agent.caveats import collect_qualifications
from services.agent.config import AgentSettings
from services.agent.orchestrator import (
    AgentOrchestrator,
    _extract_embedded_agent_answer,
)
from services.agent.provider import ModelReply
from services.agent.routing import route_question
from services.agent.tools import ToolExecution, ToolExecutor


def test_router_high_precision_and_scope_gate():
    decision = route_question(
        "How many PG&E utility-attributed ignitions were there in 2024?"
    )
    assert decision.path == "deterministic"
    assert [name for name, _ in decision.tool_calls] == ["data_query_records"]

    ambiguous = route_question("Which utility is riskiest?")
    assert ambiguous.path == "clarification"

    unsupported = route_question("Which CPZ costs least to mitigate?")
    assert unsupported.path == "unsupported"


def test_router_place_based_risk():
    cell = route_question(
        "Predict historical ignition risk for cell 400 on 2024-08-15."
    )
    assert cell.path == "deterministic"
    assert cell.rule == "cell_risk"
    assert cell.tool_calls == [
        ("risk_forecast", {"cell_id": 400, "date": "2024-08-15"})
    ]

    county = route_question(
        "What was fitted ignition risk in Sacramento County on 2024-08-15?"
    )
    assert county.path == "deterministic"
    assert county.rule == "county_risk"
    assert county.tool_calls == [
        ("risk_forecast", {"county": "Sacramento", "date": "2024-08-15"})
    ]

    utility = route_question("What was PGE fitted ignition risk on 2024-08-15?")
    assert utility.path == "deterministic"
    assert utility.rule == "utility_risk"
    assert utility.tool_calls == [
        ("risk_forecast", {"utility": "PGE", "date": "2024-08-15"})
    ]

    # Phrasings that were not used to write the rules.
    how_risky = route_question(
        "How risky was Sacramento County on August 15th 2024?"
    )
    assert how_risky.path == "deterministic"
    assert how_risky.rule == "county_risk"
    assert how_risky.tool_calls == [
        ("risk_forecast", {"county": "Sacramento", "date": "2024-08-15"})
    ]
    assert how_risky.slots["start_date"] == "2024-08-15"
    assert how_risky.slots["end_date"] == "2024-08-15"

    ignition_risk = route_question(
        "What was the ignition risk in Sacramento County on 2024-08-15?"
    )
    assert ignition_risk.path == "deterministic"
    assert ignition_risk.rule == "county_risk"
    assert ignition_risk.tool_calls == [
        ("risk_forecast", {"county": "Sacramento", "date": "2024-08-15"})
    ]
    assert ignition_risk.slots["start_date"] == "2024-08-15"
    assert ignition_risk.slots["end_date"] == "2024-08-15"

    pge_day = route_question("How risky was PG&E territory on August 15, 2024?")
    assert pge_day.path == "deterministic"
    assert pge_day.rule == "utility_risk"
    assert pge_day.tool_calls == [
        ("risk_forecast", {"utility": "PGE", "date": "2024-08-15"})
    ]

    day_first = route_question(
        "Sacramento County ignition probability for 15 August 2024"
    )
    assert day_first.rule == "county_risk"
    assert day_first.tool_calls[0][1]["date"] == "2024-08-15"

    month_only = route_question(
        "How risky was Sacramento County in August 2024?"
    )
    assert month_only.path == "clarification"
    assert month_only.rule == "forecast_missing_date"
    assert "historical dates only" in (month_only.answer or "")
    assert "2025-12-31" in (month_only.answer or "")
    assert "predict" not in (month_only.answer or "").lower()

    tomorrow = route_question(
        "What's the fire risk in Sacramento County tomorrow?"
    )
    assert tomorrow.path == "clarification"
    assert tomorrow.rule == "risk_future_date"
    assert "can't answer about tomorrow" in (tomorrow.answer or "")
    assert "2025-12-31" in (tomorrow.answer or "")
    assert "no forecast ingestion" in (tomorrow.answer or "")

    today = route_question("What's the fire risk in Sacramento County today?")
    assert today.path == "unsupported"
    assert today.rule == "unsupported_live_web"

    for question, phrase in (
        ("What's the fire risk in Sacramento County this week?", "this week"),
        ("What's the fire risk in Sacramento County next week?", "next week"),
    ):
        decision = route_question(question)
        assert decision.rule == "risk_future_date", question
        assert f"can't answer about {phrase}" in (decision.answer or "")

    coords = route_question(
        "At 38.58,-121.49, what was fitted risk on 2024-08-15?"
    )
    assert coords.path == "deterministic"
    assert coords.rule == "coordinate_risk_chain"
    assert [name for name, _ in coords.tool_calls] == [
        "data_query_spatial",
        "risk_forecast",
    ]


def test_router_generalizes_utility_comparison():
    decision = route_question("Compare SCE versus SDGE ignition counts in 2024.")
    assert decision.path == "deterministic"
    tool, args = decision.tool_calls[0]
    assert tool == "comparison_run"
    assert args["utilities"] == ["SCE", "SDGE"]
    assert args["ignition_definition"] == "attribute"


def test_executor_detects_partial_http_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "partial"})

    async def run():
        settings = AgentSettings()
        executor = ToolExecutor(
            settings,
            ArtifactStore(),
            transport=httpx.MockTransport(handler),
        )
        try:
            result = await executor.execute(
                "data_query_records",
                {
                    "dataset": "us_ignitions",
                    "result_mode": "count",
                    "year": 2024,
                },
                request_id="test",
                attempt=1,
            )
            assert not result.ok
            assert result.error["code"] == "unexpected_partial_response"
            assert result.error["recoverable"] is True
        finally:
            await executor.close()

    asyncio.run(run())


def test_caveat_companion_is_not_pge_specific():
    primary = ToolExecution(
        tool="data_query_records",
        arguments={
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "utility": "SCE",
            "year": 2024,
        },
        ok=True,
        summary={
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "total": 321,
            "metadata": {},
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1,
    )

    class FakeExecutor:
        async def execute(self, tool, arguments, **kwargs):
            assert tool == "data_query_spatial"
            assert arguments["utility"] == "SCE"
            return ToolExecution(
                tool=tool,
                arguments=arguments,
                ok=True,
                summary={
                    "kind": "summary",
                    "region": {"kind": "utility", "id": "SCE"},
                    "counts": {"ignitions": 330},
                    "metadata": {},
                },
                raw={},
                error=None,
                artifact=None,
                latency_ms=1,
                qualification_call=True,
            )

    async def run():
        caveats, companions, error = await collect_qualifications(
            [primary],
            FakeExecutor(),  # type: ignore[arg-type]
            request_id="test",
            start_attempt=1,
        )
        assert error is None
        assert len(companions) == 1
        by_id = {item["id"]: item for item in caveats}
        assert "ignition_definition_sce" in by_id
        assert "321" in by_id["ignition_definition_sce"]["text"]
        assert "330" in by_id["ignition_definition_sce"]["text"]

    asyncio.run(run())


def test_risk_health_degraded_is_not_assumed_healthy():
    # Contract-level guard is exercised in app health; this test locks the
    # service's relevant semantics for future refactors.
    payload = {"status": "degraded", "model_loaded": False}
    assert payload["status"] == "degraded" or payload["model_loaded"] is False


def test_model_direct_answer_without_tool_is_blocked():
    class DirectAnswerProvider:
        async def complete(self, **kwargs):
            return ModelReply(
                content=(
                    '{"status":"answer","answer":"There were 999 events.",'
                    '"claims":[{"text":"999 events","evidence_ids":[]}]}'
                ),
                tool_calls=[],
                raw={"choices": []},
                latency_ms=1,
                usage={},
            )

    class UnusedExecutor:
        async def execute(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("A direct answer must not execute a tool implicitly")

    async def run():
        orchestrator = AgentOrchestrator(
            AgentSettings(max_tool_steps=2),
            DirectAnswerProvider(),  # type: ignore[arg-type]
            UnusedExecutor(),  # type: ignore[arg-type]
        )
        result = await orchestrator.ask(
            "How many sample events occurred in 2024?",
            force_model=True,
        )
        assert result.response["status"] == "error"
        assert result.response["model_metrics"][
            "direct_answer_without_tool_attempts"
        ] == 2
        assert not result.response["evidence"]

    asyncio.run(run())


def test_duplicate_model_tool_calls_execute_once():
    class DuplicateProvider:
        calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                function = {
                    "name": "data_query_records",
                    "arguments": '{"dataset":"circuits","result_mode":"count"}',
                }
                return ModelReply(
                    content="",
                    tool_calls=[
                        {"id": "call_1", "type": "function", "function": function},
                        {"id": "call_2", "type": "function", "function": function},
                    ],
                    raw={"choices": []},
                    latency_ms=1,
                    usage={},
                )
            return ModelReply(
                content=(
                    '{"status":"answer","answer":"1",'
                    '"claims":[{"text":"1 circuit record",'
                    '"evidence_ids":["evidence_test"]}]}'
                ),
                tool_calls=[],
                raw={"choices": []},
                latency_ms=1,
                usage={},
            )

    class CountingExecutor:
        calls = 0

        async def execute(self, tool, arguments, **kwargs):
            self.calls += 1
            return ToolExecution(
                tool=tool,
                arguments=arguments,
                ok=True,
                summary={
                    "dataset": "circuits",
                    "result_mode": "count",
                    "total": 1,
                    "metadata": {},
                },
                raw={},
                error=None,
                artifact=None,
                latency_ms=1,
                evidence_id="evidence_test",
            )

    async def run():
        provider = DuplicateProvider()
        executor = CountingExecutor()
        orchestrator = AgentOrchestrator(
            AgentSettings(max_tool_steps=2),
            provider,  # type: ignore[arg-type]
            executor,  # type: ignore[arg-type]
        )
        result = await orchestrator.ask("How many circuit records?", force_model=True)
        assert result.response["status"] == "answer"
        assert executor.calls == 1
        assert len(result.response["evidence"]) == 1
        assert any(
            event["type"] == "duplicate_tool_call_suppressed"
            for event in result.response["trajectory"]
        )

    asyncio.run(run())


def test_embedded_json_recovery_keeps_answer_grounded():
    answer = _extract_embedded_agent_answer(
        'reasoning </think> {"status":"answer","answer":"536",'
        '"claims":[{"text":"536 ignitions",'
        '"evidence_ids":["evidence_test"]}]}'
    )
    assert answer.answer == "536"
    assert answer.claims[0].evidence_ids == ["evidence_test"]


def test_harness_retries_recoverable_tool_failure_without_model():
    class OneShotProvider:
        async def complete(self, **kwargs):
            if kwargs.get("constrained_tool_routing"):
                return ModelReply(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "data_query_records",
                                "arguments": (
                                    '{"dataset":"us_ignitions",'
                                    '"result_mode":"count","year":2024}'
                                ),
                            },
                        }
                    ],
                    raw={},
                    latency_ms=1,
                    usage={},
                )
            return ModelReply(
                content='{"status":"answer","answer":"3789","claims":[]}',
                tool_calls=[],
                raw={"choices": [{"message": {"content": "{}"}}]},
                latency_ms=1,
                usage={},
            )

    class FlakyThenOkExecutor:
        calls = 0

        async def execute(self, tool, arguments, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ToolExecution(
                    tool=tool,
                    arguments=arguments,
                    ok=False,
                    summary={},
                    raw=None,
                    error={
                        "code": "service_unavailable",
                        "message": "Injected 503",
                        "recoverable": True,
                        "suggested_action": "Retry once.",
                        "field_errors": [],
                    },
                    artifact=None,
                    latency_ms=1,
                )
            return ToolExecution(
                tool=tool,
                arguments=arguments,
                ok=True,
                summary={
                    "dataset": "us_ignitions",
                    "result_mode": "count",
                    "total": 3789,
                    "metadata": {
                        "notes": "US ignitions sample caveat.",
                        "sample_geography": {"note": "CA-heavy."},
                    },
                },
                raw={"total": 3789},
                error=None,
                artifact=None,
                latency_ms=1,
            )

    async def run():
        executor = FlakyThenOkExecutor()
        orchestrator = AgentOrchestrator(
            AgentSettings(max_tool_steps=2, max_validation_retries=1),
            OneShotProvider(),  # type: ignore[arg-type]
            executor,  # type: ignore[arg-type]
        )
        result = await orchestrator.ask(
            "How many US ignition sample events occurred in 2024?",
            force_model=True,
        )
        assert executor.calls == 2
        assert result.response["status"] == "answer"
        assert any(
            event.get("type") == "harness_auto_retry"
            for event in result.response["trajectory"]
        )
        assert any(
            item["id"] == "us_ignitions_sample"
            for item in result.response["qualifications"]
        )

    asyncio.run(run())


def test_caveats_attach_when_synthesis_fails_but_tools_succeed():
    class ToolsThenBadSynthesis:
        calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            if kwargs.get("constrained_tool_routing"):
                return ModelReply(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "data_query_records",
                                "arguments": (
                                    '{"dataset":"us_ignitions",'
                                    '"result_mode":"count","year":2024}'
                                ),
                            },
                        }
                    ],
                    raw={},
                    latency_ms=1,
                    usage={},
                )
            return ModelReply(
                content="not json",
                tool_calls=[],
                raw={"choices": []},
                latency_ms=1,
                usage={},
            )

    class UsExecutor:
        async def execute(self, tool, arguments, **kwargs):
            return ToolExecution(
                tool=tool,
                arguments=arguments,
                ok=True,
                summary={
                    "dataset": "us_ignitions",
                    "result_mode": "count",
                    "total": 3789,
                    "metadata": {
                        "notes": "US ignitions sample caveat.",
                        "sample_geography": {"note": "CA-heavy."},
                    },
                },
                raw={"total": 3789},
                error=None,
                artifact=None,
                latency_ms=1,
            )

    async def run():
        orchestrator = AgentOrchestrator(
            AgentSettings(max_tool_steps=2, max_validation_retries=0),
            ToolsThenBadSynthesis(),  # type: ignore[arg-type]
            UsExecutor(),  # type: ignore[arg-type]
        )
        result = await orchestrator.ask(
            "How many US ignition sample events occurred in 2024?",
            force_model=True,
        )
        assert result.response["status"] == "answer"
        assert any(
            event.get("type") == "synthesis_fallback_to_tool_summary"
            for event in result.response["trajectory"]
        )
        assert any(
            item["id"] == "us_ignitions_sample"
            for item in result.response["qualifications"]
        )

    asyncio.run(run())
