"""Identical schema failures should block a tool after two attempts."""

from __future__ import annotations

import asyncio

from services.agent.config import AgentSettings
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import OpenAICompatibleProvider
from services.agent.tools import ToolExecution, ToolExecutor
from services.agent.artifacts import ArtifactStore


class _AlwaysBadSchemaProvider(OpenAICompatibleProvider):
    """Emit the same invalid tool call every routing turn."""

    def __init__(self) -> None:
        self.settings = AgentSettings.from_env()
        self.calls = 0

    async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
        from types import SimpleNamespace

        self.calls += 1
        return SimpleNamespace(
            content="",
            tool_calls=[
                {
                    "id": f"call_{self.calls}",
                    "type": "function",
                    "function": {
                        "name": "data_query_records",
                        "arguments": "{}",
                    },
                }
            ],
            latency_ms=1.0,
            usage={},
            raw={"choices": [{"finish_reason": "tool_calls"}]},
        )


def test_schema_retry_bound_stops_identical_failures():
    settings = AgentSettings.from_env()
    # Keep loop long enough that an unbounded harness would thrash.
    from dataclasses import replace

    settings = replace(settings, max_tool_steps=5)
    provider = _AlwaysBadSchemaProvider()
    executor = ToolExecutor(settings, ArtifactStore(60), fault_scenario=None)
    orchestrator = AgentOrchestrator(settings, provider, executor)

    async def _run():
        return await orchestrator._model_loop(
            "How many US ignition sample events occurred in 2024?",
            "test-request",
            ["data_query_records"],
            year=2024,
            years=[2024],
            utilities=[],
            time_resolution={
                "status": "explicit",
                "year": 2024,
                "years": [2024],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
        )

    status, answer, executions, trajectory, *_ = asyncio.run(_run())
    assert status == "error"
    bound_events = [
        event for event in trajectory if event.get("type") == "schema_retry_bound"
    ]
    assert bound_events, "expected schema_retry_bound after identical failures"
    # Two real schema failures, then blocked short-circuits — not six identical tries.
    schema_failures = [
        item
        for item in executions
        if not item.ok and (item.error or {}).get("code") == "invalid_arguments"
    ]
    assert len(schema_failures) == 2
    assert "could not" in answer.lower()
    assert "schema validation" not in answer.lower()


class _AlwaysInventedYearProvider(OpenAICompatibleProvider):
    """Emit the same invented year when the harness resolved none."""

    def __init__(self) -> None:
        self.settings = AgentSettings.from_env()
        self.calls = 0

    async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
        from types import SimpleNamespace

        self.calls += 1
        return SimpleNamespace(
            content="",
            tool_calls=[
                {
                    "id": f"call_{self.calls}",
                    "type": "function",
                    "function": {
                        "name": "data_query_records",
                        "arguments": (
                            '{"dataset":"cpuc_ignitions","result_mode":"count",'
                            '"year":2024}'
                        ),
                    },
                }
            ],
            latency_ms=1.0,
            usage={},
            raw={"choices": [{"finish_reason": "tool_calls"}]},
        )


def test_identical_non_schema_failures_are_bounded():
    """Invented-year failures must not loop until max_tool_steps."""
    from dataclasses import replace

    settings = replace(AgentSettings.from_env(), max_tool_steps=5)
    provider = _AlwaysInventedYearProvider()
    executor = ToolExecutor(settings, ArtifactStore(60), fault_scenario=None)
    orchestrator = AgentOrchestrator(settings, provider, executor)

    async def _run():
        return await orchestrator._model_loop(
            "How many CPUC ignitions are there?",
            "test-request",
            ["data_query_records"],
            year=None,
            years=[],
            utilities=[],
            time_resolution={"status": "none"},
        )

    status, answer, executions, trajectory, *_ = asyncio.run(_run())
    assert status == "error"
    bound_events = [
        event for event in trajectory if event.get("type") == "schema_retry_bound"
    ]
    assert bound_events
    year_failures = [
        item
        for item in executions
        if not item.ok and (item.error or {}).get("code") == "year_not_derived"
    ]
    assert len(year_failures) == 2
    assert "year" in answer.lower() or "could not" in answer.lower()


class _CorrectsInventedYearProvider(OpenAICompatibleProvider):
    """Drop the invented year after the targeted harness correction."""

    def __init__(self) -> None:
        self.settings = AgentSettings.from_env()
        self.calls = 0

    async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
        from types import SimpleNamespace

        self.calls += 1
        prompt = kwargs["messages"][-1]["content"]
        if self.calls == 1:
            assert "Harness-resolved time: none" in prompt
            arguments = (
                '{"dataset":"cpuc_ignitions","result_mode":"count","year":2023}'
            )
        else:
            assert "Remove year, start_date, and end_date" in prompt
            assert "Do not repeat the rejected arguments" in prompt
            arguments = '{"dataset":"cpuc_ignitions","result_mode":"count"}'
        return SimpleNamespace(
            content="",
            tool_calls=[
                {
                    "id": f"call_{self.calls}",
                    "type": "function",
                    "function": {
                        "name": "data_query_records",
                        "arguments": arguments,
                    },
                }
            ],
            latency_ms=1.0,
            usage={},
            raw={"choices": [{"finish_reason": "tool_calls"}]},
        )


class _YearGuardExecutor:
    async def execute(self, tool, arguments, **kwargs):  # type: ignore[no-untyped-def]
        if "year" in arguments:
            return ToolExecution(
                tool=tool,
                arguments=arguments,
                ok=False,
                summary={},
                raw=None,
                error={
                    "code": "year_not_derived",
                    "message": "year was not resolved from the question",
                    "recoverable": False,
                    "suggested_action": (
                        "Use only harness-resolved years from the question, "
                        "or ask for clarification."
                    ),
                },
                artifact=None,
                latency_ms=0.0,
            )
        return ToolExecution(
            tool=tool,
            arguments=arguments,
            ok=True,
            summary={
                "dataset": "cpuc_ignitions",
                "result_mode": "count",
                "total": 1,
            },
            raw={},
            error=None,
            artifact=None,
            latency_ms=0.0,
        )


def test_invented_year_retry_is_told_to_remove_time_filters():
    settings = AgentSettings.from_env()
    provider = _CorrectsInventedYearProvider()
    orchestrator = AgentOrchestrator(
        settings,
        provider,
        _YearGuardExecutor(),  # type: ignore[arg-type]
    )

    async def _run():
        return await orchestrator._model_loop(
            "Why might CPUC and CAL FIRE counts differ in the same year?",
            "test-request",
            ["data_query_records"],
            year=None,
            years=[],
            utilities=[],
            time_resolution={"status": "none"},
        )

    status, _, executions, trajectory, *_ = asyncio.run(_run())
    assert status == "tools_ready"
    assert provider.calls == 2
    assert [
        (item.error or {}).get("code")
        for item in executions
        if not item.ok
    ] == ["year_not_derived"]
    assert executions[-1].ok
    assert "year" not in executions[-1].arguments
    assert any(
        event.get("type") == "routing_history_reset_after_tool_failure"
        for event in trajectory
    )
