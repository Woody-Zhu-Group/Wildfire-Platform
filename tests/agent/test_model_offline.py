from __future__ import annotations

import asyncio

from services.agent.config import AgentSettings
from services.agent.orchestrator import MODEL_OFFLINE_ANSWER, AgentOrchestrator


class _OfflineProvider:
    async def health(self):
        return {"available": False, "detail": "connection refused"}

    async def complete(self, **kwargs):  # pragma: no cover
        raise AssertionError("model path must not call complete when offline")


class _UnusedExecutor:
    async def execute(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("model-offline short-circuit must not execute tools")


def test_model_path_returns_error_not_500_when_the_provider_is_down():
    async def run():
        orchestrator = AgentOrchestrator(
            AgentSettings(max_tool_steps=2),
            _OfflineProvider(),  # type: ignore[arg-type]
            _UnusedExecutor(),  # type: ignore[arg-type]
        )
        result = await orchestrator.ask(
            "Tell me about CPUC ignitions versus the US sample in 2023.",
            force_model=True,
        )
        assert result.response["status"] == "error"
        assert result.response["answer_text"] == MODEL_OFFLINE_ANSWER

    asyncio.run(run())
